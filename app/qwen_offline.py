# -*- coding: utf-8 -*-
"""Pinned, opt-in installer for the Qwen3.5-0.8B local model.

This module deliberately does not ship model weights.  Network download is an
explicit operation (``consent=True``), while bundle import is the preferred
way to deploy an air-gapped workstation.  Downloads are resumable and are
written to a temporary directory until every file has passed its checks.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import urllib.parse
import re
import zipfile
from typing import Any, Callable, Mapping, Optional

import version

MODEL_ID = "Qwen/Qwen3.5-0.8B"
# Pinned from the official Hugging Face model API.  Do not silently follow
# ``main``: an operator must update this manifest deliberately.
REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
LICENSE = "Apache-2.0"
LICENSE_URL = "https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/" + REVISION + "/LICENSE"
MODEL_API_URL = "https://huggingface.co/api/models/Qwen/Qwen3.5-0.8B"
REPOSITORY_URL = "https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/" + REVISION
TOTAL_BYTES = 1_759_750_582
WEIGHTS_BYTES = 1_746_942_600
WEIGHTS_SHA256 = "04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696"

_FILES = (
    {"name": "config.json", "size": 2907, "sha256": "b90b86f35c8e6925ef74ee04d0e758f0a845c83a42089ad82bbaa948de9b4204"},
    {"name": "preprocessor_config.json", "size": 390, "sha256": "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"},
    {"name": "tokenizer.json", "size": 12807982, "sha256": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"},
    {"name": "tokenizer_config.json", "size": 16709, "sha256": "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c"},
    {"name": "chat_template.jinja", "size": 7755, "sha256": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80"},
    {"name": "merges.txt", "size": 3353259, "sha256": "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"},
    {"name": "vocab.json", "size": 6722759, "sha256": "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"},
    {"name": "video_preprocessor_config.json", "size": 385, "sha256": "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"},
    {"name": "model.safetensors.index.json", "size": 50900, "sha256": "d8a08838a613b025eb7952ed9db11696213e57e76a375661ef5c12f9dd5dcf4e"},
    {"name": "model.safetensors-00001-of-00001.safetensors", "size": WEIGHTS_BYTES, "sha256": WEIGHTS_SHA256},
)


def model_manifest() -> dict[str, Any]:
    """Return the checked-in, JSON-serialisable upstream manifest."""
    return {
        "schema": 1,
        "model_id": MODEL_ID,
        "revision": REVISION,
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "model_api_url": MODEL_API_URL,
        "repository_url": REPOSITORY_URL,
        "total_bytes": TOTAL_BYTES,
        "weights_bytes": WEIGHTS_BYTES,
        "files": [dict(item) for item in _FILES],
        "integrity_note": "all files are pinned by SHA-256 and size at the immutable revision",
    }


MANIFEST = model_manifest()


class QwenInstallError(RuntimeError):
    """A safe installation or validation failure."""


@dataclass(frozen=True)
class ResourceEstimate:
    disk_bytes: int
    ram_bytes: int
    recommended_ram_bytes: int
    cpu_only: bool
    suitable: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["warnings"] = list(self.warnings)
        return result


def _available_memory() -> Optional[int]:
    try:
        import psutil  # type: ignore
        avail = int(psutil.virtual_memory().available)
        if sys.platform == "darwin":
            try:
                import subprocess
                out = subprocess.check_output(["sysctl", "-in", "sysctl.proc_translated"], stderr=subprocess.DEVNULL).decode().strip()
                if out == "1":
                    # Under Rosetta 2 on Apple Silicon, host_statistics reports page counts
                    # assuming 4096-byte pages while kernel pages are 16384 bytes, underreporting RAM by 4x.
                    avail *= 4
            except Exception:
                pass
        return avail
    except Exception:
        try:
            pages = os.sysconf("SC_AVPHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * page_size)
        except (AttributeError, OSError, ValueError):
            return None


def estimate_resources(*, available_memory: Optional[int] = None, cpu_only: bool = True) -> ResourceEstimate:
    """Estimate peak resources before an operator downloads the model.

    The current runtime uses float32 on CPU and float16 on MPS/CUDA.  The
    estimate intentionally includes a conservative runtime overhead rather
    than promising that the raw safetensor size equals usable RAM.
    """
    ram = int(WEIGHTS_BYTES * (2.15 if cpu_only else 1.35))
    recommended = 4 * 1024**3 if cpu_only else 3 * 1024**3
    available = _available_memory() if available_memory is None else available_memory
    warnings: list[str] = []
    if available is not None and available < recommended:
        warnings.append("Недостаточно доступной памяти для безопасного запуска Qwen; используйте CPU только с малым документом или отключите модель")
    if cpu_only:
        warnings.append("CPU-режим использует float32 и требует больше памяти; MPS/CUDA предпочтительнее")
    # ``recommended`` is a comfort target shown as a warning, not a hard
    # refusal.  The hard gate is the estimated peak itself; otherwise a machine
    # with enough RAM for the measured workload can be rejected merely because
    # it misses the rounded recommendation by a few megabytes.
    suitable = available is None or available >= ram
    return ResourceEstimate(TOTAL_BYTES, ram, recommended, cpu_only, suitable, tuple(warnings))


def check_resources(destination: os.PathLike[str] | str, *, available_memory: Optional[int] = None, cpu_only: bool = True) -> dict[str, Any]:
    """Check disk and RAM without creating files or downloading anything."""
    target = Path(destination).expanduser()
    parent = target if target.exists() else target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    free = shutil.disk_usage(parent).free
    estimate = estimate_resources(available_memory=available_memory, cpu_only=cpu_only)
    warnings = list(estimate.warnings)
    if free < TOTAL_BYTES:
        warnings.append(f"Недостаточно места на диске: нужно не менее {TOTAL_BYTES} байт")
    return {"disk_free_bytes": free, "disk_required_bytes": TOTAL_BYTES, "ram": estimate.to_dict(), "suitable": free >= TOTAL_BYTES and estimate.suitable, "warnings": warnings}


def get_default_model_dir() -> Path:
    """Return canonical path to Qwen model in user data."""
    try:
        import app_paths
        return app_paths.get_user_models_dir(create=False) / "Qwen3.5-0.8B"
    except Exception:
        return Path.home() / ".docxdodyr" / "models" / "Qwen3.5-0.8B"


def _safe_name(name: str) -> str:
    path = Path(name)
    if path.is_absolute() or len(path.parts) != 1 or path.name != name or name in {"", ".", ".."}:
        raise QwenInstallError(f"Недопустимое имя файла модели: {name!r}")
    return name


def _sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _validate_download_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    allowed = {"huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.hf.co", "cas-bridge.xethub.hf.co", "us.aws.cdn.hf.co"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise QwenInstallError("Недопустимый адрес загрузки модели")


class _ModelRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_model_url(request, timeout):
    _validate_download_url(request.full_url)
    return urllib.request.build_opener(_ModelRedirectHandler()).open(request, timeout=timeout)


def _download_file(
    url: str,
    destination: Path,
    *,
    expected_size: Optional[int],
    expected_hash: Optional[str],
    timeout: int,
    progress: Optional[Callable[..., None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> None:
    partial = destination.with_name(destination.name + ".part")
    if partial.is_symlink() or destination.is_symlink():
        raise QwenInstallError("Символические ссылки запрещены для файлов модели")
    limit = expected_size if expected_size is not None else 64 * 1024 * 1024
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > limit:
        partial.unlink()
        offset = 0
    headers = {"User-Agent": f"DOCXdodyr/{version.APP_VERSION} Qwen offline installer"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = _open_model_url(request, timeout=timeout)
    except Exception as exc:
        raise QwenInstallError(f"Не удалось загрузить {destination.name}: {type(exc).__name__}") from exc
    status = getattr(response, "status", 200)
    # A server may ignore Range.  Never append a complete response to a part.
    if offset and status != 206:
        offset = 0
        partial.unlink(missing_ok=True)
        response.close()
        response = _open_model_url(urllib.request.Request(url, headers={"User-Agent": headers["User-Agent"]}), timeout=timeout)
    if offset and status == 206:
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
        if not match or int(match[1]) != offset or (expected_size is not None and int(match[3]) != expected_size):
            response.close()
            partial.unlink(missing_ok=True)
            raise QwenInstallError("Сервер вернул неверный диапазон загрузки")
    mode = "ab" if offset else "wb"
    written = offset
    with response, partial.open(mode) as handle:
        while True:
            if cancel_check and cancel_check():
                handle.close()
                partial.unlink(missing_ok=True)
                raise QwenInstallError("Загрузка отменена пользователем")
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            if written + len(chunk) > limit:
                handle.close()
                partial.unlink(missing_ok=True)
                raise QwenInstallError("Превышен допустимый размер файла модели")
            handle.write(chunk)
            written += len(chunk)
            if progress:
                try:
                    progress(written, expected_size, destination.name)
                except TypeError:
                    try:
                        progress(written, expected_size)
                    except TypeError:
                        pass
    if expected_size is not None and written != expected_size:
        partial.unlink(missing_ok=True)
        raise QwenInstallError(f"Размер {destination.name} не совпал: {written} вместо {expected_size}")
    if expected_hash:
        actual = _sha256(partial)
        if actual.casefold() != expected_hash.casefold():
            partial.unlink(missing_ok=True)
            raise QwenInstallError(f"Хеш {destination.name} не совпал с pinned manifest")
    os.replace(partial, destination)


def is_installed(model_dir: os.PathLike[str] | str, *, manifest: Mapping[str, Any] = MANIFEST) -> bool:
    root = Path(model_dir).expanduser()
    return root.is_dir() and all((root / _safe_name(str(item["name"]))).is_file() for item in manifest.get("files", []))


def validate_model(model_dir: os.PathLike[str] | str, *, manifest: Mapping[str, Any] = MANIFEST, verify_hash: bool = True) -> bool:
    """Validate a local model, including all pinned size/hash fields."""
    root = Path(model_dir).expanduser()
    if not is_installed(root, manifest=manifest):
        return False
    marker = root / "DOCXdodyr-qwen-manifest.json"
    if marker.is_file():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            if metadata.get("model_id") != manifest.get("model_id") or metadata.get("revision") != manifest.get("revision"):
                return False
        except (OSError, ValueError, TypeError):
            return False
    for item in manifest.get("files", []):
        path = root / _safe_name(str(item["name"]))
        if path.is_symlink():
            return False
        expected_size = item.get("size")
        if expected_size is not None and path.stat().st_size != int(expected_size):
            return False
        expected_hash = item.get("sha256")
        if verify_hash and (not expected_hash or _sha256(path).casefold() != str(expected_hash).casefold()):
            return False
    return True


def verify_model_integrity(
    model_dir: os.PathLike[str] | str,
    *,
    manifest: Mapping[str, Any] = MANIFEST,
    progress: Optional[Callable[..., None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    """Verify all files against manifest with SHA-256 calculation, progress and cancellation."""
    root = Path(model_dir).expanduser().resolve()
    if not root.is_dir():
        return {
            "valid": False,
            "status": "not_installed",
            "files_checked": 0,
            "total_files": len(manifest.get("files", [])),
            "missing_files": [str(item["name"]) for item in manifest.get("files", [])],
            "corrupted_files": [],
            "message": "Каталог модели не существует",
        }

    files = manifest.get("files", [])
    total_files = len(files)
    missing: list[str] = []
    corrupted: list[str] = []
    checked = 0

    for item in files:
        if cancel_check and cancel_check():
            raise QwenInstallError("Проверка целостности отменена пользователем")
        name = _safe_name(str(item["name"]))
        path = root / name
        if not path.is_file():
            missing.append(name)
            continue
        expected_size = item.get("size")
        if expected_size is not None and path.stat().st_size != int(expected_size):
            corrupted.append(f"{name} (размер {path.stat().st_size} != {expected_size})")
            continue
        expected_hash = item.get("sha256")
        if expected_hash:
            actual = _sha256(path)
            if actual.casefold() != str(expected_hash).casefold():
                corrupted.append(f"{name} (SHA-256 несоответствие)")
                continue
        checked += 1
        if progress:
            try:
                progress(checked, total_files, name)
            except TypeError:
                try:
                    progress(checked, total_files)
                except TypeError:
                    pass

    is_valid = not missing and not corrupted
    if is_valid:
        msg = f"Все файлы модели ({checked}/{total_files}) успешно прошли проверку SHA-256"
        status = "installed"
    elif missing and not corrupted:
        msg = f"Отсутствуют файлы модели: {', '.join(missing[:3])}"
        status = "incomplete"
    else:
        msg = f"Обнаружены повреждённые файлы модели: {', '.join(corrupted[:3])}"
        status = "corrupted"

    return {
        "valid": is_valid,
        "status": status,
        "files_checked": checked,
        "total_files": total_files,
        "missing_files": missing,
        "corrupted_files": corrupted,
        "message": msg,
    }


def delete_model(
    destination: os.PathLike[str] | str,
    *,
    allowed_parent: Optional[os.PathLike[str] | str] = None,
) -> bool:
    """Safely delete model directory from user data.

    Protects against path traversal or deleting system directories by verifying
    that destination is inside allowed_parent (or app_paths.get_user_models_dir()).
    """
    target = Path(destination).expanduser().resolve()
    if not target.exists():
        return False

    if allowed_parent is None:
        try:
            import app_paths
            parent = app_paths.get_user_models_dir().resolve()
        except Exception as exc:
            raise QwenInstallError("Не удалось определить разрешённый каталог моделей") from exc
    else:
        parent = Path(allowed_parent).expanduser().resolve()

    try:
        rel = target.relative_to(parent)
        if rel == Path(".") or len(rel.parts) == 0:
            raise QwenInstallError(f"Нельзя удалять сам корневой каталог моделей: {target}")
    except ValueError:
        raise QwenInstallError(f"Удаление разрешено только внутри каталога моделей: {target} не внутри {parent}")

    if target.is_dir():
        shutil.rmtree(target, ignore_errors=False)
    elif target.is_file():
        target.unlink()
    return True


def install_model(
    destination: os.PathLike[str] | str,
    *,
    consent: bool = False,
    timeout: int = 60,
    progress: Optional[Callable[..., None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    manifest: Mapping[str, Any] = MANIFEST,
) -> Path:
    """Download a pinned model after explicit consent; never called at startup."""
    if not consent:
        raise QwenInstallError("Загрузка Qwen требует явного согласия пользователя (consent=True)")
    target = Path(destination).expanduser().resolve()
    if target.exists() and target.is_file():
        raise QwenInstallError("Каталог модели не может быть обычным файлом")
    report = check_resources(target)
    if report["disk_free_bytes"] < int(manifest.get("total_bytes", TOTAL_BYTES)):
        raise QwenInstallError("Недостаточно свободного места для модели")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qwen-install-", dir=str(target.parent)))
    total_manifest_bytes = int(manifest.get("total_bytes", TOTAL_BYTES))
    cumulative_bytes = 0
    try:
        for item in manifest.get("files", []):
            if cancel_check and cancel_check():
                raise QwenInstallError("Загрузка отменена пользователем")
            name = _safe_name(str(item["name"]))
            expected_file_size = item.get("size")
            file_base_bytes = cumulative_bytes

            def _file_progress(file_written: int, file_total: Optional[int], *args: Any) -> None:
                if progress:
                    current_overall = file_base_bytes + file_written
                    try:
                        progress(current_overall, total_manifest_bytes, name)
                    except TypeError:
                        try:
                            progress(current_overall, total_manifest_bytes)
                        except TypeError:
                            pass

            url = f"https://huggingface.co/{manifest['model_id']}/resolve/{manifest['revision']}/{name}?download=true"
            _download_file(
                url,
                staging / name,
                expected_size=expected_file_size,
                expected_hash=item.get("sha256"),
                timeout=timeout,
                progress=_file_progress,
                cancel_check=cancel_check,
            )
            cumulative_bytes += (expected_file_size if expected_file_size is not None else (staging / name).stat().st_size)
        (staging / "DOCXdodyr-qwen-manifest.json").write_text(json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            if any(target.iterdir()):
                raise QwenInstallError("Каталог назначения уже содержит файлы; выберите новый каталог")
            target.rmdir()
        os.replace(staging, target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def export_bundle(model_dir: os.PathLike[str] | str, bundle_path: os.PathLike[str] | str, *, consent: bool = False, manifest: Mapping[str, Any] = MANIFEST) -> Path:
    if not consent:
        raise QwenInstallError("Экспорт offline bundle требует явного согласия пользователя (consent=True)")
    root = Path(model_dir).expanduser().resolve()
    if not is_installed(root, manifest=manifest):
        raise QwenInstallError("Каталог модели не прошёл проверку полноты")
    destination = Path(bundle_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for item in manifest["files"]:
                name = _safe_name(str(item["name"]))
                archive.write(root / name, arcname=name)
            metadata = root / "DOCXdodyr-qwen-manifest.json"
            archive.writestr(metadata.name, metadata.read_text(encoding="utf-8") if metadata.is_file() else json.dumps(dict(manifest), indent=2))
        os.replace(temporary, destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def import_bundle(
    bundle_path: os.PathLike[str] | str,
    destination: os.PathLike[str] | str,
    *,
    consent: bool = False,
    manifest: Mapping[str, Any] = MANIFEST,
    progress: Optional[Callable[..., None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Path:
    if not consent:
        raise QwenInstallError("Импорт offline bundle требует явного согласия пользователя (consent=True)")
    bundle = Path(bundle_path).expanduser().resolve()
    if not bundle.is_file():
        raise QwenInstallError("Offline bundle не найден")
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".qwen-import-", dir=str(target.parent)))
    try:
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            allowed = {_safe_name(str(item["name"])) for item in manifest["files"]} | {"DOCXdodyr-qwen-manifest.json"}
            if any(name not in allowed for name in names):
                raise QwenInstallError("Bundle содержит лишние или небезопасные пути")
            if len(names) != len(set(names)):
                raise QwenInstallError("Bundle содержит повторяющиеся имена")
            sizes = {str(item["name"]): item.get("size", 64 * 1024 * 1024) for item in manifest["files"]}
            sizes["DOCXdodyr-qwen-manifest.json"] = 1024 * 1024
            if any(info.file_size > sizes[info.filename] for info in archive.infolist()):
                raise QwenInstallError("Bundle превышает допустимый размер")
            total_items = len(names)
            for idx, name in enumerate(names):
                if cancel_check and cancel_check():
                    raise QwenInstallError("Импорт отменен пользователем")
                archive.extract(name, staging)
                if progress:
                    try:
                        progress(idx + 1, total_items, name)
                    except TypeError:
                        try:
                            progress(idx + 1, total_items)
                        except TypeError:
                            pass
        if not validate_model(staging, manifest=manifest):
            raise QwenInstallError("Bundle не содержит полного набора pinned-файлов")
        if target.exists() and any(target.iterdir()):
            raise QwenInstallError("Каталог назначения уже содержит файлы; выберите новый каталог")
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def runtime_settings(model_dir: os.PathLike[str] | str, *, manifest: Mapping[str, Any] = MANIFEST) -> dict[str, Any]:
    """Settings fragment for the application after a successful local install."""
    root = str(Path(model_dir).expanduser().resolve())
    if not validate_model(root, manifest=manifest, verify_hash=True):
        raise QwenInstallError("Нельзя включить runtime: локальная модель не установлена полностью")
    return {"model_id": root, "model_path": root, "local_files_only": True, "allow_network_download": False}


__all__ = [
    "MANIFEST", "MODEL_ID", "REVISION", "QwenInstallError", "ResourceEstimate",
    "check_resources", "delete_model", "estimate_resources", "export_bundle",
    "get_default_model_dir", "import_bundle", "install_model", "is_installed",
    "model_manifest", "runtime_settings", "validate_model", "verify_model_integrity",
]
