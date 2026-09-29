# -*- coding: utf-8 -*-
"""Модуль защиты от сбоев и восстановления сессий пакетной обработки.

Обеспечивает атомарную фиксацию контрольных точек (чекпоинтов) в пользовательском
каталоге данных, сохраняя сквозной дешифратор и список оставшихся файлов. При
аварийном завершении процесса (сбой питания, закрытие приложения) позволяет
бесшовно продолжить обработку без потери уже обезличенных документов.
"""

from __future__ import annotations

import json
import logging
import shutil
import hashlib
import os
import threading
from functools import wraps
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import app_paths

logger = logging.getLogger(__name__)

CHECKPOINT_FILENAME = "active_batch_checkpoint.json"
_lock = threading.RLock()
_lock_state = threading.local()


def _serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _lock:
            if getattr(_lock_state, "active", False):
                return function(*args, **kwargs)
            lock_path = get_checkpoint_path().with_suffix(".lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as stream:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    if not stream.read(1):
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                _lock_state.active = True
                try:
                    return function(*args, **kwargs)
                finally:
                    _lock_state.active = False
                    if os.name == "nt":
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return wrapped


def _digest(checkpoint):
    return hashlib.sha256(json.dumps(checkpoint, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_recovery_dir() -> Path:
    """Возвращает защищённый каталог контрольных точек в user_data_dir."""
    recovery_dir = app_paths.get_user_data_dir() / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    return recovery_dir


def get_checkpoint_path() -> Path:
    """Возвращает путь к файлу текущей активной контрольной точки."""
    return get_recovery_dir() / CHECKPOINT_FILENAME


@dataclass
class BatchCheckpoint:
    batch_id: str
    mode: str  # "files" | "folder"
    created_at: str
    updated_at: str
    files_total: List[str]
    processed_files: List[str] = field(default_factory=list)
    failed_files: List[Dict[str, str]] = field(default_factory=list)
    batch_mapping: Dict[str, str] = field(default_factory=dict)
    total_replacements: int = 0
    complete: bool = False
    source_folder: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BatchCheckpoint:
        return cls(
            batch_id=str(data.get("batch_id") or ""),
            mode=str(data.get("mode") or "files"),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            files_total=list(data.get("files_total") or []),
            processed_files=list(data.get("processed_files") or []),
            failed_files=list(data.get("failed_files") or []),
            batch_mapping=dict(data.get("batch_mapping") or {}),
            total_replacements=int(data.get("total_replacements") or 0),
            complete=bool(data.get("complete", False)),
            source_folder=str(data.get("source_folder") or ""),
        )


def get_vault_path() -> Path:
    """Возвращает путь к зашифрованному хранилищу дешифратора активной сессии."""
    return get_recovery_dir() / "active_batch_vault.bin"


def _get_recovery_vault():
    from review_context import SecureContextVault
    return SecureContextVault(get_vault_path(), account="local")


@_serialized
def start_batch_checkpoint(
    batch_id: str,
    files: List[str | Path],
    mode: str = "files",
    source_folder: str = "",
) -> BatchCheckpoint:
    """Создаёт новую контрольную точку перед началом пакетной обработки."""
    checkpoint = BatchCheckpoint(
        batch_id=str(batch_id),
        mode=mode,
        created_at=_utc_now(),
        updated_at=_utc_now(),
        files_total=[str(Path(f).resolve()) for f in files],
        processed_files=[],
        failed_files=[],
        batch_mapping={},
        total_replacements=0,
        complete=False,
        source_folder=str(source_folder),
    )
    checkpoint_path = get_checkpoint_path()
    app_paths.atomic_write_json(checkpoint_path, checkpoint.to_dict(), indent=2)
    vault_path = get_vault_path()
    if vault_path.exists():
        vault_path.unlink(missing_ok=True)
    logger.info("Контрольная точка сессии %s сохранена (%d файлов)", batch_id, len(files))
    return checkpoint


@_serialized
def update_batch_checkpoint(
    batch_id: str,
    processed_file: Optional[str | Path] = None,
    mapping_delta: Optional[Dict[str, str | dict]] = None,
    replacements: int = 0,
    error: Optional[str] = None,
) -> Optional[BatchCheckpoint]:
    """Атомарно обновляет прогресс активной контрольной точки после завершения файла."""
    checkpoint_path = get_checkpoint_path()
    if not checkpoint_path.exists():
        return None

    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint = BatchCheckpoint.from_dict(data)
    except Exception as exc:
        logger.warning("Не удалось прочитать чекпоинт: %s", exc)
        return None

    if checkpoint.batch_id != batch_id:
        return None

    previous_mapping = load_interrupted_batch_mapping(batch_id)
    previous_digest = _digest(data)
    previous_state = {"batch_id": batch_id, "mapping_state": previous_mapping}

    checkpoint.updated_at = _utc_now()
    if processed_file and not error:
        resolved = str(Path(processed_file).resolve())
        if resolved not in checkpoint.processed_files:
            checkpoint.processed_files.append(resolved)

    if mapping_delta:
        # Save real mapping encrypted in vault (no plaintext originals in checkpoint JSON)
        checkpoint.batch_mapping.update({str(key): "" for key in mapping_delta})
        previous_mapping = {**previous_mapping, **mapping_delta}

    checkpoint.total_replacements += max(0, int(replacements))

    if error and processed_file:
        checkpoint.failed_files.append({"file": str(processed_file), "error": str(error)})

    updated = checkpoint.to_dict()
    # Both generations are authenticated. If checkpoint publication fails or
    # the process stops between writes, the old checkpoint still selects its
    # old mapping; no mixed generation can be resumed.
    _get_recovery_vault().save({"states": {
        previous_digest: previous_state,
        _digest(updated): {"batch_id": batch_id, "mapping_state": previous_mapping},
    }})
    app_paths.atomic_write_json(checkpoint_path, updated, indent=2)
    return checkpoint


@_serialized
def load_interrupted_batch_mapping(batch_id: str = "", *, fail_closed: bool = True) -> Dict[str, Any]:
    """Загружает расшифрованный batch_mapping прерванной сессии из vault."""
    try:
        checkpoint = json.loads(get_checkpoint_path().read_text(encoding="utf-8"))
        if not batch_id or checkpoint.get("batch_id") != batch_id or checkpoint.get("complete") is not False:
            raise ValueError("Recovery batch mismatch")
        if not get_vault_path().exists():
            if checkpoint.get("batch_mapping") or checkpoint.get("processed_files"):
                raise ValueError("Recovery vault missing")
            return {}
        vault = _get_recovery_vault()
        loaded = vault.load()
        state = loaded.get("states", {}).get(_digest(checkpoint))
        if not isinstance(state, dict) or state.get("batch_id") != batch_id:
            raise ValueError("Recovery checkpoint authentication failed")
        mapping = state.get("mapping_state")
        if not isinstance(mapping, dict) or set(mapping) != set(checkpoint.get("batch_mapping", {})):
            raise ValueError("Recovery mapping mismatch")
        return mapping
    except Exception as exc:
        logger.error("Ошибка чтения зашифрованного дешифратора: %s", type(exc).__name__)
        raise RuntimeError("Не удалось расшифровать состояние дешифратора прерванной сессии: хранилище повреждено или недоступно") from exc


@_serialized
def complete_batch_checkpoint(batch_id: str) -> None:
    """Отмечает контрольную точку как успешно завершённую и удаляет её."""
    checkpoint_path = get_checkpoint_path()
    if checkpoint_path.exists():
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if BatchCheckpoint.from_dict(data).batch_id != str(batch_id):
                return
            checkpoint_path.unlink()
            get_vault_path().unlink(missing_ok=True)
            logger.info("Сессия %s успешно завершена, чекпоинт очищен", batch_id)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("Не удалось удалить чекпоинт %s: %s", checkpoint_path, exc)


@_serialized
def get_interrupted_batch() -> Optional[Dict[str, Any]]:
    """Возвращает информацию о незавершённой сессии, если приложение было прервано."""
    checkpoint_path = get_checkpoint_path()
    if not checkpoint_path.exists():
        return None

    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint = BatchCheckpoint.from_dict(data)
        if checkpoint.complete:
            return None

        total = len(checkpoint.files_total)
        processed = len(checkpoint.processed_files)
        remaining = [f for f in checkpoint.files_total if f not in checkpoint.processed_files]

        return {
            "batch_id": checkpoint.batch_id,
            "mode": checkpoint.mode,
            "created_at": checkpoint.created_at,
            "updated_at": checkpoint.updated_at,
            "files_total_count": total,
            "processed_count": processed,
            "failed_count": len(checkpoint.failed_files),
            "failed_files": checkpoint.failed_files,
            "remaining_count": len(remaining),
            "remaining_files": remaining,
            "entities_mapped": len(checkpoint.batch_mapping),
            "total_replacements": checkpoint.total_replacements,
            "source_folder": checkpoint.source_folder,
        }
    except Exception as exc:
        logger.warning("Ошибка чтения прерванной сессии: %s", exc)
        return None


@_serialized
def discard_interrupted_batch() -> bool:
    """Удаляет файл контрольной точки и зашифрованный vault прерванной сессии."""
    checkpoint_path = get_checkpoint_path()
    success = True
    if checkpoint_path.exists():
        try:
            checkpoint_path.unlink()
        except OSError:
            success = False
    vault_path = get_vault_path()
    if vault_path.exists():
        try:
            vault_path.unlink()
        except OSError:
            pass
    return success


def find_orphaned_folder_runs(folder: Path | str) -> List[Path]:
    """Находит незавершённые временные каталоги `.docxdodyr-incomplete-*` в папке."""
    root = Path(folder).resolve()
    if not root.is_dir():
        return []

    orphans = []
    try:
        for entry in root.iterdir():
            if entry.is_dir() and entry.name.startswith(".docxdodyr-incomplete-"):
                orphans.append(entry)
    except OSError:
        pass
    return orphans


def cleanup_orphaned_folder_runs(folder: Path | str) -> int:
    """Удаляет брошенные каталоги `.docxdodyr-incomplete-*` от прерванных запусков."""
    orphans = find_orphaned_folder_runs(folder)
    removed = 0
    for orphan in orphans:
        try:
            shutil.rmtree(orphan, ignore_errors=True)
            removed += 1
            logger.info("Удалён брошенный временный каталог: %s", orphan.name)
        except Exception as exc:
            logger.warning("Не удалось удалить %s: %s", orphan, exc)
    return removed


__all__ = [
    "BatchCheckpoint",
    "get_recovery_dir",
    "get_checkpoint_path",
    "start_batch_checkpoint",
    "update_batch_checkpoint",
    "complete_batch_checkpoint",
    "get_interrupted_batch",
    "discard_interrupted_batch",
    "find_orphaned_folder_runs",
    "cleanup_orphaned_folder_runs",
]
