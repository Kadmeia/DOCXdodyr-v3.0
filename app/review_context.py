# -*- coding: utf-8 -*-
"""Encrypted, restart-safe private context for manual review decisions.

The review queue and audit artifacts intentionally contain no source values.
This module stores the small private decoder records separately, encrypted with
an OS-managed key.  There is no plaintext fallback: missing keychain access,
missing cryptography support, malformed ciphertext, and permission failures all
raise ``SecureContextError``.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


class SecureContextError(RuntimeError):
    """The private context cannot be safely read or written."""


def _keychain_key(
    service: str,
    account: str,
    *,
    create_if_missing: bool = True,
) -> Optional[bytes]:
    """Read a 32-byte key, creating it only for a brand-new vault.

    Rotating the key when an encrypted vault already exists makes that vault
    permanently unreadable.  Callers loading or replacing an existing vault
    therefore pass ``create_if_missing=False`` and fail closed when Keychain
    access is missing or denied.
    """
    try:
        import keyring  # type: ignore
        value = keyring.get_password(service, account)
        if value is None:
            if not create_if_missing:
                raise SecureContextError(
                    "Ключ приватного контекста отсутствует в системном хранилище; "
                    "существующие данные не изменены"
                )
            value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
            keyring.set_password(service, account, value)
        key = base64.urlsafe_b64decode(value.encode("ascii"))
        if len(key) != 32:
            raise SecureContextError("Системное хранилище вернуло ключ неверного размера")
        return key
    except ImportError:
        pass
    except SecureContextError:
        raise
    except Exception as exc:
        raise SecureContextError("Не удалось получить ключ из системного хранилища") from exc

    # macOS is the primary supported desktop target.  ``security`` never
    # receives document text; only a stable service/account label and key.
    if os.name == "posix" and Path("/usr/bin/security").exists():
        try:
            found = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
                check=False, capture_output=True, text=True, timeout=3,
            )
            value = found.stdout.strip() if found.returncode == 0 else ""
            if not value:
                if not create_if_missing:
                    raise SecureContextError(
                        "Ключ приватного контекста отсутствует в macOS Keychain; "
                        "существующие данные не изменены"
                    )
                value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
                created = subprocess.run(
                    ["/usr/bin/security", "add-generic-password", "-U", "-s", service,
                     "-a", account, "-w", value],
                    check=False, capture_output=True, text=True, timeout=3,
                )
                if created.returncode != 0:
                    raise SecureContextError("Не удалось сохранить ключ в macOS Keychain")
            key = base64.urlsafe_b64decode(value.encode("ascii"))
            if len(key) != 32:
                raise SecureContextError("Системное хранилище вернуло ключ неверного размера")
            return key
        except SecureContextError:
            raise
        except Exception as exc:
            raise SecureContextError("Не удалось обратиться к macOS Keychain") from exc
    raise SecureContextError("OS keyring недоступен; приватный контекст не сохранён")


class SecureContextVault:
    """Small encrypted JSON vault with atomic writes and strict permissions."""

    schema_version = 1

    def __init__(self, path: os.PathLike[str] | str, *, service: str = "DOCXdodyr.review",
                 account: str = "default", key_provider: Optional[Callable[[], bytes]] = None):
        self.path = Path(path)
        self.service = str(service)
        self.account = str(account)
        self._key_provider = key_provider

    def _key(self, *, create_if_missing: bool) -> bytes:
        value = (
            self._key_provider()
            if self._key_provider
            else _keychain_key(
                self.service,
                self.account,
                create_if_missing=create_if_missing,
            )
        )
        if not isinstance(value, bytes) or len(value) != 32:
            raise SecureContextError("Ключ шифрования отсутствует или имеет неверный размер")
        return value

    def _fernet(self, *, create_if_missing: bool):
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise SecureContextError("Криптографический модуль недоступен; plaintext fallback запрещён") from exc
        return Fernet(
            base64.urlsafe_b64encode(
                self._key(create_if_missing=create_if_missing)
            )
        )

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_bytes()
            envelope = json.loads(raw.decode("utf-8"))
            if envelope.get("schema_version") != self.schema_version or envelope.get("algorithm") != "fernet":
                raise SecureContextError("Неизвестная схема приватного контекста")
            token = str(envelope.get("ciphertext", ""))
            if not token:
                raise SecureContextError("Пустой приватный контекст")
            decoded = json.loads(
                self._fernet(create_if_missing=False)
                .decrypt(token.encode("ascii"))
                .decode("utf-8")
            )
            if not isinstance(decoded, dict):
                raise SecureContextError("Повреждённый приватный контекст")
            return {str(key): value for key, value in decoded.items() if isinstance(value, dict)}
        except SecureContextError:
            raise
        except Exception as exc:
            raise SecureContextError("Приватный контекст повреждён или ключ не совпадает") from exc

    def save(self, values: Mapping[str, Mapping[str, Any]]) -> Path:
        # Serialization happens only after a valid key is acquired.
        token = self._fernet(create_if_missing=not self.path.exists()).encrypt(
            json.dumps(
                dict(values), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
        envelope = {"schema_version": self.schema_version, "algorithm": "fernet",
                    "ciphertext": token.decode("ascii")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent))
        temporary = Path(name)
        try:
            if hasattr(os, "fchmod"):
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            return self.path
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, finding_id: str, context: Mapping[str, Any]) -> Path:
        values = self.load()
        values[str(finding_id)] = dict(context)
        return self.save(values)

    def remove(self, finding_id: str) -> Path:
        values = self.load()
        values.pop(str(finding_id), None)
        return self.save(values)


__all__ = ["SecureContextError", "SecureContextVault"]
