# -*- coding: utf-8 -*-
"""Безопасная пакетная обработка папок.

Модуль намеренно не знает о UI. Он принимает готовый ``BackendApi``-подобный
объект и использует его ``process_single_file``. Это позволяет запускать
папочный режим из PyWebView, тестов и командных сценариев одинаково.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import app_paths

logger = logging.getLogger(__name__)

# Legacy binary .xls is intentionally excluded: openpyxl cannot read it and
# silently collecting it would turn a supported-folder run into a partial one.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"})
SUPPORTED_SUFFIXES = frozenset({".docx", ".docm", ".xlsx", ".xlsm", ".pdf"}) | IMAGE_SUFFIXES
DEFAULT_OUTPUT_DIR_NAME = "Обезличенные документы"
DEFAULT_DECODER_FILENAME = "Дешифратор.json"
DEFAULT_MANIFEST_SUFFIX = ".manifest.json"
PROVENANCE_SUFFIX = ".provenance.json"
MAX_DECODER_BYTES = 10 * 1024 * 1024
MAX_INPUT_BYTES = 500 * 1024 * 1024


@dataclass
class FolderProcessResult:
    """Результат пакетной обработки, удобный для UI и автоматических тестов."""

    source_dir: Path
    output_dir: Path
    decoder_path: Optional[Path] = None
    manifest_path: Optional[Path] = None
    files: list[Path] = field(default_factory=list)
    output_files: list[Path] = field(default_factory=list)
    processed_count: int = 0
    error_count: int = 0
    total_replacements: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    mapping: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_dir": str(self.source_dir),
            "output_dir": str(self.output_dir),
            "decoder_path": str(self.decoder_path) if self.decoder_path else None,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "files": [str(p) for p in self.files],
            "output_files": [str(p) for p in self.output_files],
            "processed_count": self.processed_count,
            "error_count": self.error_count,
            "total_replacements": self.total_replacements,
            "errors": list(self.errors),
        }


def _resolved(path: Path) -> Path:
    """Resolve without requiring the path to exist (Windows-friendly)."""

    return path.expanduser().resolve(strict=False)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(root))
        return True
    except ValueError:
        return False


def _validate_output_dir_name(value: str) -> str:
    """Require the output directory to be one safe child name of the root."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Имя выходной папки должно быть непустой строкой")
    # ``pathlib.Path`` uses the host platform's separator.  On POSIX a
    # backslash is therefore treated as an ordinary character even though it
    # is a path separator on Windows.  Validate both separators explicitly so
    # a setting accepted on macOS cannot turn into a path escape after moving
    # the same configuration to Windows.  Control characters and Windows'
    # trailing-dot/space aliases are rejected for the same portability reason.
    if any(ord(char) < 32 for char in value) or "/" in value or "\\" in value:
        raise ValueError("Имя выходной папки должно быть простым именем внутри выбранной папки")
    if value != value.rstrip(" ."):
        raise ValueError("Имя выходной папки не должно заканчиваться точкой или пробелом")
    candidate = Path(value)
    if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.name in {"", ".", ".."}:
        raise ValueError("Имя выходной папки должно быть простым именем внутри выбранной папки")
    return candidate.name


def _secure_input_snapshot(source: Path, snapshot_root: Path, *, root: Path | None = None) -> Path:
    """Copy one input through an open no-follow descriptor before processing."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    can_dir_fd = (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "supports_dir_fd")
        and os.open in os.supports_dir_fd
    )
    if root is None or not can_dir_fd:
        if source.is_symlink():
            raise OSError(f"Символические ссылки запрещены: {source}")
        if root is not None:
            p = source
            while p != root and p.parent != p:
                if p.is_symlink():
                    raise OSError(f"Символические ссылки запрещены: {p}")
                p = p.parent
        fd = os.open(source, flags)
    else:
        relative = source.relative_to(root)
        directory_flags = flags | os.O_DIRECTORY
        directory_fd = os.open(root, directory_flags)
        try:
            for part in relative.parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            fd = os.open(relative.name, flags, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
    try:
        stat = os.fstat(fd)
        if not stat or not __import__("stat").S_ISREG(stat.st_mode):
            raise ValueError("Входной документ не является обычным файлом")
        if stat.st_size > MAX_INPUT_BYTES:
            raise ValueError("Входной документ превышает допустимый размер")
        destination = snapshot_root / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd_out, temp_name = tempfile.mkstemp(prefix=f".{source.stem}.", suffix=source.suffix, dir=str(destination.parent))
        try:
            with os.fdopen(fd, "rb") as source_stream, os.fdopen(fd_out, "wb") as output_stream:
                copied = 0
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    copied += len(chunk)
                    if copied > MAX_INPUT_BYTES:
                        raise ValueError("Входной документ превышает допустимый размер")
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(temp_name, destination)
            return destination
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def collect_supported_files(
    folder: str | os.PathLike,
    *,
    output_dir_name: str = DEFAULT_OUTPUT_DIR_NAME,
    recursive: bool = True,
) -> list[Path]:
    """Collect supported documents below ``folder`` without following links.

    The output directory is excluded at any depth. Symlinks and junction-like
    entries are skipped so a user cannot accidentally make the application
    traverse outside the selected directory. Results are deterministic.
    """

    # Keep this public helper subject to the same path contract as the
    # pipeline itself.  Previously callers could pass ``..`` or a nested
    # path here: collection would then walk the source tree while the
    # pipeline later used a different output location, making the helper's
    # result unsafe to reuse by integrations.
    output_dir_name = _validate_output_dir_name(output_dir_name)
    root = _resolved(Path(folder))
    if not root.is_dir():
        raise NotADirectoryError(f"Папка не найдена: {folder}")
    output_name = output_dir_name.casefold()
    found: list[Path] = []
    visited: set[tuple[int, int] | str] = set()

    def visit(directory: Path) -> None:
        try:
            stat = os.stat(str(directory), follow_symlinks=False)
            identity: tuple[int, int] | str = (stat.st_dev, stat.st_ino)
        except OSError:
            identity = str(directory)
        if identity in visited:
            return
        visited.add(identity)
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name.casefold())
        except OSError as exc:
            logger.warning("Не удалось прочитать папку %s: %s", directory, exc)
            return
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.name.startswith((".", "~$")):
                    continue
                candidate = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    candidate_name = candidate.name.casefold()
                    if candidate_name == output_name or candidate_name.startswith(".docxdodyr-"):
                        continue
                    if recursive:
                        visit(candidate)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if candidate.suffix.casefold() in SUPPORTED_SUFFIXES:
                    found.append(_resolved(candidate))
            except OSError:
                continue

    visit(root)
    return sorted(found, key=lambda p: str(p.relative_to(root)).casefold())


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decoder_path_for_run(root: Path, mapping: dict) -> Path:
    """Return a collision-safe root decoder path.

    Every successful run gets its own immutable decoder.  Reusing a decoder
    and overwriting its manifest would invalidate provenance files archived
    from the preceding run, even when the mapping itself did not change.
    """

    candidate = root / DEFAULT_DECODER_FILENAME
    if not candidate.exists() and not (root / "дешифратор.json").exists():
        return candidate
    index = 1
    while True:
        versioned = root / f"Дешифратор_{index}.json"
        if not versioned.exists() and not (root / f"дешифратор_{index}.json").exists():
            return versioned
        index += 1


def _load_continuation_state(root: Path) -> tuple[dict, dict, Path, dict]:
    """Load the newest cryptographically verified completed folder run."""
    from document_restorer import is_valid_decoder_structure, extract_decoder_mapping
    from entity_registry import entity_seen_from_mapping

    manifests = []
    for path in root.glob(f"*{DEFAULT_MANIFEST_SUFFIX}"):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DECODER_BYTES:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("schema") == "docxdodyr.batch-manifest/v1":
            manifests.append((str(payload.get("created_at", "")), path.name.casefold(), path, payload))
    if manifests:
        _created, _name, manifest_path, manifest = max(manifests)
        if manifest.get("complete") is not True or manifest.get("errors"):
            raise ValueError("Нельзя продолжить комплект: последний запуск не был успешно завершён")
        if not isinstance(manifest.get("run_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", manifest["run_id"]):
            raise ValueError("Нельзя продолжить комплект: отсутствует корректный run ID")
        decoder_info = manifest.get("decoder")
        if not isinstance(decoder_info, dict):
            raise ValueError("Нельзя продолжить комплект: manifest не содержит дешифратор")
        decoder_name = decoder_info.get("path")
        if not isinstance(decoder_name, str) or Path(decoder_name).name != decoder_name:
            raise ValueError("Нельзя продолжить комплект: небезопасный путь к дешифратору")
        decoder_path = root / decoder_name
        if decoder_path.is_symlink() or not decoder_path.is_file():
            raise ValueError("Нельзя продолжить комплект: дешифратор не найден")
        if decoder_path.stat().st_size > MAX_DECODER_BYTES:
            raise ValueError("Нельзя продолжить комплект: дешифратор слишком большой")
        if _sha256(decoder_path) != str(decoder_info.get("sha256", "")):
            raise ValueError("Нельзя продолжить комплект: контрольная сумма дешифратора не совпадает")
        try:
            mapping = json.loads(decoder_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("Нельзя продолжить комплект: дешифратор повреждён") from exc
        if not is_valid_decoder_structure(mapping):
            raise ValueError("Нельзя продолжить комплект: неверная структура дешифратора")
        from document_restorer import find_decoder_near_document
        documents = manifest.get("documents")
        if not isinstance(documents, list) or not documents:
            raise ValueError("Нельзя продолжить комплект: отсутствует список результатов")
        for record in documents:
            outputs = record.get("outputs") if isinstance(record, dict) else None
            if not isinstance(outputs, list) or not outputs:
                raise ValueError("Нельзя продолжить комплект: отсутствует список результатов")
            for output in outputs:
                relative = output.get("path") if isinstance(output, dict) else None
                if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    raise ValueError("Нельзя продолжить комплект: небезопасный путь результата")
                document = root / relative
                if document.is_symlink() or not document.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Нельзя продолжить комплект: небезопасный путь результата")
                if not document.is_file() or _sha256(document) != output.get("sha256"):
                    raise ValueError("Нельзя продолжить комплект: результат изменён или отсутствует")
                if find_decoder_near_document(document) != decoder_path.resolve():
                    raise ValueError("Нельзя продолжить комплект: чужой дешифратор результата")
        clean_mapping = extract_decoder_mapping(mapping)
        entity_seen = entity_seen_from_mapping(clean_mapping)
        return clean_mapping, entity_seen, decoder_path, manifest

    # Если манифестов нет, проверяем дешифратор со встроенными метаданными v2
    candidate_decoders = [root / DEFAULT_DECODER_FILENAME, root / "дешифратор.json"]
    candidate_decoders.extend(sorted(root.glob("Дешифратор_*.json"), reverse=True))
    candidate_decoders.extend(sorted(root.glob("дешифратор_*.json"), reverse=True))
    for dec_candidate in candidate_decoders:
        if dec_candidate.is_file() and not dec_candidate.is_symlink() and dec_candidate.stat().st_size <= MAX_DECODER_BYTES:
            try:
                dec_data = json.loads(dec_candidate.read_text(encoding="utf-8"))
                meta = dec_data.get("__docxdodyr_meta__")
                if isinstance(meta, dict) and meta.get("schema") == "docxdodyr.decoder-binding/v2":
                    if meta.get("complete") is not True or not isinstance(meta.get("run_id"), str):
                        continue
                    docs = meta.get("documents", [])
                    if docs and isinstance(docs, list):
                        all_matched = True
                        for doc_entry in docs:
                            if not isinstance(doc_entry, dict):
                                all_matched = False
                                break
                            rel_path_str = doc_entry.get("path")
                            if not isinstance(rel_path_str, str) or not rel_path_str.strip():
                                all_matched = False
                                break
                            rel_p = Path(rel_path_str)
                            if rel_p.is_absolute() or ".." in rel_p.parts:
                                all_matched = False
                                break
                            doc_p = root / rel_p
                            if not _is_inside(doc_p, root) or doc_p.is_symlink() or not doc_p.is_file():
                                all_matched = False
                                break
                            expected_sha = doc_entry.get("sha256")
                            if not expected_sha or _sha256(doc_p) != expected_sha:
                                all_matched = False
                                break
                        if all_matched:
                            clean_mapping = extract_decoder_mapping(dec_data)
                            return clean_mapping, entity_seen_from_mapping(clean_mapping), dec_candidate, meta
            except Exception:
                continue

    raise ValueError(
        "Нельзя продолжить комплект: в корне папки нет manifest завершённого запуска или валидной привязки v2"
    )


class FolderAnonymizationPipeline:
    """Run one Pullenti batch while placing every result below one folder."""

    def __init__(
        self,
        backend,
        *,
        output_dir_name: str = DEFAULT_OUTPUT_DIR_NAME,
        emit_sidecars: Optional[bool] = None,
    ):
        self.backend = backend
        self.output_dir_name = _validate_output_dir_name(output_dir_name)
        self.emit_sidecars = (
            emit_sidecars
            if emit_sidecars is not None
            else getattr(backend, "emit_audit_sidecars", False)
        )

    def process(
        self,
        folder: str | os.PathLike,
        *,
        recursive: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        continue_existing: bool = False,
    ) -> FolderProcessResult:
        root = _resolved(Path(folder))
        if not root.is_dir():
            raise NotADirectoryError(f"Папка не найдена: {folder}")
        files = collect_supported_files(root, output_dir_name=self.output_dir_name, recursive=recursive)
        published_output_root = root / self.output_dir_name
        if not files:
            # Publishing an empty canonical output directory looks like a
            # successful run to the UI and leaves users with no indication
            # that the selected folder contained no supported documents.
            # Return a structured failure instead; callers can show the
            # message and keep the source folder untouched.
            return FolderProcessResult(
                source_dir=root,
                output_dir=published_output_root,
                files=[],
                error_count=1,
                errors=[{
                    "file": str(root),
                    "error": "В папке нет поддерживаемых документов",
                }],
            )
        run_id = str(uuid.uuid4())
        continuation_decoder = None
        continuation_manifest = None
        if continue_existing:
            mapping, entity_seen, continuation_decoder, continuation_manifest = _load_continuation_state(root)
        else:
            mapping, entity_seen = {}, {}
        # A run is built in an explicitly incomplete directory.  It becomes
        # visible under the canonical name only after every file, final batch
        # reconciliation and provenance generation have succeeded.
        output_root = root / f".docxdodyr-incomplete-{run_id}"
        result = FolderProcessResult(source_dir=root, output_dir=published_output_root, files=files)
        output_root.mkdir(parents=True, exist_ok=False)
        self.backend._active_batch_id = run_id
        batch_entries: list[dict] = []
        source_outputs: list[tuple[Path, list[Path]]] = []
        exclusions = getattr(self.backend, "user_exclusions", set())
        replacements = getattr(self.backend, "custom_replacements", set())

        for index, source in enumerate(files, 1):
            if getattr(self.backend, "is_cancelled", None) and self.backend.is_cancelled():
                logger.info("Обработка папки отменена пользователем.")
                result.error_count += 1
                result.errors.append({"file": f"{source.name} (Документ #{index})", "error": "Операция отменена пользователем"})
                break

            if progress_callback:
                progress_callback(index - 1, len(files), f"Обработка: {source.name}")
            snapshot = None
            snapshot_dir = None
            try:
                relative_parent = source.parent.relative_to(root)
                destination = output_root / relative_parent
                before = set(destination.glob("*")) if destination.exists() else set()
                snapshot_dir = Path(tempfile.mkdtemp(prefix="docxdodyr-inputs-"))
                snapshot = _secure_input_snapshot(source, snapshot_dir / relative_parent, root=root)
                source_hash = _sha256(snapshot)
                count = self.backend.process_single_file(
                    str(snapshot), exclusions, replacements,
                    batch_mapping=mapping,
                    batch_entity_seen=entity_seen,
                    defer_decoder=True,
                    output_dir=destination,
                )
                result.processed_count += 1
                result.total_replacements += int(count or 0)
                if destination.exists():
                    created = sorted(
                        (
                            p for p in destination.iterdir()
                            if p not in before
                            and p.is_file()
                            and not p.name.startswith((".", "~$", "~"))
                        ),
                        key=lambda p: p.name.casefold(),
                    )
                    result.output_files.extend(created)
                    document_outputs = [
                        p for p in created
                        if p.suffix.casefold() in {".docx", ".docm", ".xlsx", ".xlsm", ".pdf", ".md"}
                        and not p.name.startswith((".", "~$", "~"))
                    ]
                    source_outputs.append((source, document_outputs))
                    primary_docs = [p for p in document_outputs if p.suffix.casefold() in {".docx", ".docm", ".xlsx", ".xlsm"}]
                    if primary_docs:
                        target_doc = primary_docs[0]
                        is_temp = False
                    else:
                        target_doc = getattr(self.backend, "_last_reconcile_temp", None)
                        is_temp = True
                    batch_entries.append({
                        "source": source,
                        "source_sha256": source_hash,
                        "outputs": document_outputs,
                        "target_doc": target_doc,
                        "is_temp": is_temp,
                    })
            except Exception as exc:
                from log_sanitizer import sanitize_error_message

                result.error_count += 1
                safe_error = sanitize_error_message(exc)
                result.errors.append({"file": f"{source.name} (Документ #{index})", "error": safe_error})
                logger.warning("Ошибка папочного режима, документ #%d: %s", index, safe_error)
            finally:
                if snapshot_dir is not None:
                    shutil.rmtree(snapshot_dir)
            if progress_callback:
                progress_callback(index, len(files), f"Завершено: {source.name}")

        result.mapping = dict(mapping)
        if result.error_count:
            for entry in batch_entries:
                if entry.get("is_temp") and entry.get("target_doc"):
                    try:
                        entry["target_doc"].unlink(missing_ok=True)
                    except OSError:
                        pass
            error_report_lines = [
                f"DOCXдодыр: Отчет об ошибках обработки папки ({datetime.now(timezone.utc).isoformat()})",
                f"Всего файлов: {len(files)}",
                f"Успешно обработано: {result.processed_count}",
                f"Ошибок: {result.error_count}",
                "=" * 60,
                "Список ошибок по файлам:",
            ]
            for err_item in result.errors:
                error_report_lines.append(f"- Файл: {err_item.get('file')}\n  Ошибка: {err_item.get('error')}\n")

            error_txt = output_root / "_BATCH_ERROR.txt"
            app_paths.atomic_write_text(error_txt, "\n".join(error_report_lines))

            marker = output_root / ("НЕПОЛНЫЙ_ПРОГОН.json" if not result.processed_count else "ОШИБКИ_ОБРАБОТКИ.json")
            _write_json_atomic(marker, {
                "schema": "docxdodyr.incomplete-run/v1",
                "run_id": run_id,
                "errors": list(result.errors),
            })
            result.output_dir = output_root
            return result

        if mapping:
            from batch_reconcile import reconcile_document, transform_docx_paragraphs
            from pdf_convert import (
                convert_docx_text_to_pdf,
                convert_docx_to_pdf_libreoffice,
                convert_to_pdf,
            )
            from privacy_audit import clean_file_metadata

            max_sweeps = 4
            document_changes: dict[Path, int] = {}
            for sweep in range(max_sweeps):
                if getattr(self.backend, "is_cancelled", None) and self.backend.is_cancelled():
                    logger.info("Сверка папки отменена пользователем.")
                    result.errors.append({"file": "batch", "error": "Сверка папки отменена пользователем"})
                    result.error_count += 1
                    for entry in batch_entries:
                        if entry.get("is_temp") and entry.get("target_doc"):
                            entry["target_doc"].unlink(missing_ok=True)
                    incomplete_marker = output_root / f".docxdodyr-incomplete-{run_id}"
                    incomplete_marker.touch()
                    return result
                for entry in batch_entries:
                    target_doc = entry.get("target_doc")
                    if (
                        target_doc
                        and target_doc.is_file()
                        and (entry.get("is_temp") or not target_doc.name.startswith((".", "~$", "~")))
                        and target_doc.suffix.casefold() in {".docx", ".docm", ".xlsx", ".xlsm"}
                    ):
                        exact_changes = reconcile_document(target_doc, mapping)
                        result.total_replacements += exact_changes
                        document_changes[target_doc] = document_changes.get(target_doc, 0) + exact_changes
                sweep_changes = 0
                for entry in batch_entries:
                    target_doc = entry.get("target_doc")
                    source = entry["source"]
                    if (
                        target_doc
                        and target_doc.is_file()
                        and (entry.get("is_temp") or not target_doc.name.startswith((".", "~$", "~")))
                        and target_doc.suffix.casefold() in {".docx", ".docm"}
                    ):
                        self.backend._active_source_path = str(source.resolve())
                        self.backend._active_review_location = "batch-final-sweep"

                        def transform(text):
                            token_pattern = re.compile(
                                "|".join(
                                    re.escape(token)
                                    for token in sorted(mapping, key=len, reverse=True)
                                )
                            )
                            protected_ranges = [
                                (match.start(), match.end())
                                for match in token_pattern.finditer(text)
                            ]
                            cleaned, count, _logs = self.backend.anonymize_text_pullenti(
                                text, exclusions, replacements, prev_paragraphs=None,
                                mapping_dict=mapping, entity_seen=entity_seen,
                                protected_ranges=protected_ranges,
                            )
                            return cleaned, count

                        transformed = transform_docx_paragraphs(target_doc, transform)
                        sweep_changes += transformed
                        document_changes[target_doc] = document_changes.get(target_doc, 0) + transformed
                result.total_replacements += sweep_changes
                if not sweep_changes:
                    break
            else:
                for entry in batch_entries:
                    if entry.get("is_temp") and entry.get("target_doc"):
                        entry["target_doc"].unlink(missing_ok=True)
                raise RuntimeError(
                    "Финальный контроль продолжает находить новые сущности; пачка не опубликована"
                )

            # Финальная точная сверка перед регенерацией
            for entry in batch_entries:
                target_doc = entry.get("target_doc")
                if (
                    target_doc
                    and target_doc.is_file()
                    and (entry.get("is_temp") or not target_doc.name.startswith((".", "~$", "~")))
                    and target_doc.suffix.casefold() in {".docx", ".docm", ".xlsx", ".xlsm"}
                ):
                    added = reconcile_document(target_doc, mapping)
                    result.total_replacements += added
                    document_changes[target_doc] = document_changes.get(target_doc, 0) + added

            # Регенерация зависимых форматов (PDF, Markdown) и безопасное удаление временных файлов
            try:
                for entry in batch_entries:
                    target_doc = entry.get("target_doc")
                    outputs = entry["outputs"]
                    source = entry["source"]

                    pdf_outputs = [p for p in outputs if p.suffix.casefold() == ".pdf"]
                    for pdf in pdf_outputs:
                        temp_pdf = pdf.with_name(f".{pdf.stem}.reconciled.pdf")
                        cleaned_pdf = pdf.with_name(f".{pdf.stem}.cleaned.pdf")
                        try:
                            if source.suffix.casefold() == ".pdf" or source.suffix.casefold() in IMAGE_SUFFIXES:
                                converted = convert_docx_text_to_pdf(target_doc, temp_pdf)
                                if not converted:
                                    converted = convert_docx_to_pdf_libreoffice(target_doc, temp_pdf) or convert_to_pdf(target_doc, temp_pdf)
                            else:
                                converted = convert_to_pdf(target_doc, temp_pdf)
                            if not converted or not temp_pdf.is_file():
                                raise RuntimeError(f"Не удалось пересобрать PDF после финальной сверки: {pdf.name}")
                            cleanup_report = clean_file_metadata(temp_pdf, cleaned_pdf)
                            if cleanup_report.error:
                                raise RuntimeError(
                                    f"Не удалось очистить метаданные PDF: {cleanup_report.error}"
                                )
                            os.replace(cleaned_pdf, pdf)
                        finally:
                            temp_pdf.unlink(missing_ok=True)
                            cleaned_pdf.unlink(missing_ok=True)

                    md_outputs = [p for p in outputs if p.suffix.casefold() == ".md"]
                    for md in md_outputs:
                        from markdown_export import save_as_markdown
                        temp_md = md.with_name(f".{md.stem}.reconciled.md")
                        try:
                            if target_doc.suffix.casefold() in {".xlsx", ".xlsm"}:
                                import openpyxl
                                from xlsx_semantic import close_workbook
                                # Markdown does not need a copy of the VBA ZIP.
                                wb = openpyxl.load_workbook(str(target_doc))
                                try:
                                    save_as_markdown(wb, temp_md)
                                finally:
                                    close_workbook(wb)
                            else:
                                from docx import Document
                                doc = Document(str(target_doc))
                                save_as_markdown(doc, temp_md)
                            if not temp_md.is_file():
                                raise RuntimeError(f"Не удалось пересобрать Markdown после финальной сверки: {md.name}")
                            os.replace(temp_md, md)
                        finally:
                            temp_md.unlink(missing_ok=True)

                    # Если target_doc был временным файлом сверки, удаляем его
                    if entry.get("is_temp") and target_doc and target_doc.is_file():
                        target_doc.unlink(missing_ok=True)

                    # Обновляем хеши в сертификатах аудита, если аудит был включен (диагностический режим)
                    for out_doc in outputs:
                        audit_file = out_doc.with_name(out_doc.name + ".audit.json")
                        if audit_file.is_file():
                            try:
                                audit_payload = json.loads(audit_file.read_text(encoding="utf-8"))
                                audit_payload["output"] = {
                                    "sha256": _sha256(out_doc),
                                    "size_bytes": out_doc.stat().st_size,
                                }
                                _write_json_atomic(audit_file, audit_payload)
                            except Exception:
                                pass
            except Exception as conv_err:
                logger.warning("Ошибка конвертации при финальной сверке папки: %s", type(conv_err).__name__)
                for entry in batch_entries:
                    if entry.get("is_temp") and entry.get("target_doc"):
                        entry["target_doc"].unlink(missing_ok=True)
                raise conv_err
        else:
            for entry in batch_entries:
                if entry.get("is_temp") and entry.get("target_doc"):
                    entry["target_doc"].unlink(missing_ok=True)

        has_replacements = bool(mapping) and any(
            isinstance(v, (str, dict)) for k, v in mapping.items() if not str(k).startswith("__")
        )
        if has_replacements:
            decoder_path = _decoder_path_for_run(root, mapping)
            _write_json_atomic(decoder_path, mapping)
            result.decoder_path = decoder_path
            decoder_hash = _sha256(decoder_path)

            if self.emit_sidecars:
                manifest_path = decoder_path.with_name(
                    decoder_path.stem + DEFAULT_MANIFEST_SUFFIX
                )
                manifest = {
                    "schema": "docxdodyr.batch-manifest/v1",
                    "run_id": run_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "complete": True,
                    "expected_document_count": len(files),
                    "decoder": {
                        "path": decoder_path.name,
                        "sha256": decoder_hash,
                    },
                    "placeholder_grammar": {
                        "bracket_type": getattr(self.backend, "bracket_type", "square"),
                        "pattern": r"configured-brackets + label + optional numeric ID",
                    },
                    "documents": [],
                    "errors": list(result.errors),
                }
                if continuation_decoder is not None and continuation_manifest is not None:
                    manifest["continuation"] = {
                        "run_id": str(continuation_manifest.get("run_id", "")),
                        "decoder_path": continuation_decoder.name,
                        "decoder_sha256": _sha256(continuation_decoder),
                    }
                for entry in batch_entries:
                    source = entry["source"]
                    record = {
                        "source": str(source.relative_to(root)),
                        "source_sha256": entry["source_sha256"],
                        "outputs": [],
                    }
                    for output in entry["outputs"]:
                        provenance_path = output.with_name(output.name + PROVENANCE_SUFFIX)
                        published_output = published_output_root / output.relative_to(output_root)
                        published_provenance = published_output_root / provenance_path.relative_to(output_root)
                        relative_decoder = os.path.relpath(decoder_path, output.parent)
                        provenance = {
                            "schema": "docxdodyr.decoder-binding/v1",
                            "run_id": run_id,
                            "decoder_path": relative_decoder,
                            "decoder_sha256": decoder_hash,
                            "output_sha256": _sha256(output),
                        }
                        _write_json_atomic(provenance_path, provenance)
                        record["outputs"].append({
                            "path": str(published_output.relative_to(root)),
                            "sha256": provenance["output_sha256"],
                            "provenance": str(published_provenance.relative_to(root)),
                        })
                    manifest["documents"].append(record)
                _write_json_atomic(manifest_path, manifest)
                result.manifest_path = manifest_path
            else:
                # Чистый режим: привязка встроена прямо в дешифратор без sidecars
                clean_mapping = dict(mapping)
                doc_records = []
                for entry in batch_entries:
                    for output in entry["outputs"]:
                        published_output = published_output_root / output.relative_to(output_root)
                        doc_records.append({
                            "path": str(published_output.relative_to(root)),
                            "filename": output.name,
                            "sha256": _sha256(output),
                        })
                from document_restorer import META_KEY, BINDING_SCHEMA_V2
                clean_mapping[META_KEY] = {
                    "schema": BINDING_SCHEMA_V2,
                    "run_id": str(run_id),
                    "complete": True,
                    "documents": doc_records,
                }
                _write_json_atomic(decoder_path, clean_mapping)
                result.manifest_path = None
        else:
            result.decoder_path = None
            result.manifest_path = None

        def discard_unpublished_binding() -> None:
            # These paths were selected collision-safe for this run. A cancelled
            # or failed publication must not leave a decoder referring to an
            # unpublished document set in the user's source directory.
            for path in (result.manifest_path, result.decoder_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Не удалось удалить привязку неопубликованного запуска: %s", type(path).__name__)
            result.manifest_path = None
            result.decoder_path = None

        if getattr(self.backend, "is_cancelled", None) and self.backend.is_cancelled():
            result.errors.append({"file": "batch", "error": "Пакетная обработка отменена перед публикацией"})
            result.error_count += 1
            discard_unpublished_binding()
            incomplete_marker = output_root / f".docxdodyr-incomplete-{run_id}"
            incomplete_marker.touch()
            return result

        # Publish atomically.  Preserve a previous complete batch in a hidden
        # archive instead of deleting or mixing it with the new run.
        archived = None
        try:
            if published_output_root.exists():
                archive_root = root / ".docxdodyr-previous-runs"
                archive_root.mkdir(parents=True, exist_ok=True)
                archived = archive_root / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{run_id}"
                os.replace(published_output_root, archived)
            os.replace(output_root, published_output_root)
        except Exception as pub_exc:
            if archived is not None and archived.exists() and not published_output_root.exists():
                try:
                    os.replace(archived, published_output_root)
                except OSError:
                    pass
            discard_unpublished_binding()
            raise pub_exc

        result.output_files = [
            published_output_root / p.relative_to(output_root) for p in result.output_files
        ]
        result.output_dir = published_output_root
        if getattr(self.backend, "open_output_folder", True) and published_output_root.exists():
            app_paths.open_folder_in_file_manager(published_output_root)
        return result


FolderPipeline = FolderAnonymizationPipeline

__all__ = [
    "SUPPORTED_SUFFIXES", "DEFAULT_OUTPUT_DIR_NAME", "DEFAULT_DECODER_FILENAME",
    "FolderProcessResult", "FolderAnonymizationPipeline", "FolderPipeline", "collect_supported_files",
    "PROVENANCE_SUFFIX",
]
