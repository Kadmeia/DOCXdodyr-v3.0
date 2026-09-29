# -*- coding: utf-8 -*-
"""Локальные артефакты контроля приватности DOCXдодыр.

Модуль намеренно не знает ничего о дешифраторе и не принимает исходные
значения сущностей.  Он предоставляет три маленьких независимых блока:

* безопасную очистку метаданных офисных пакетов и обычных PDF;
* локальную очередь решений ручной проверки с confidence;
* сертификат обработки с хешами файлов и агрегированными счётчиками.

Это backend API для будущего экрана проверки.  Сам по себе модуль не меняет
документ при импорте и не делает выводов о сущностях.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from xml.etree import ElementTree as ET


AUDIT_SCHEMA_VERSION = 1
RULES_VERSION = "2026.08.24"
_XML_NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: os.PathLike[str] | str) -> str:
    """Возвращает SHA-256 файла, не сохраняя его содержимое в памяти."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_fingerprint(path: Path) -> Dict[str, Any]:
    return {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _set_private_mode(path: Path) -> None:
    if os.name == "posix":
        os.chmod(path, 0o600)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


@dataclass
class ReviewFinding:
    """Найденный фрагмент без исходного персонального значения.

    ``redacted_context`` должен содержать только уже обезличенный фрагмент.
    Поля ``original``, ``value`` и ``surface`` намеренно отсутствуют в API.
    """

    document_ref: str
    location: str
    entity_type: str
    placeholder: str
    confidence: float
    source: str = "pullenti"
    redacted_context: str = ""
    finding_id: str = ""
    status: str = "pending"
    decision_note: str = ""
    # Machine-readable location for review navigation.  It is deliberately
    # structural only (no source text): DOCX/XLSX coordinates or a PDF page
    # and normalized OCR rectangle (x, y, width, height in 0..1).
    position: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.document_ref = str(self.document_ref or "")
        self.location = str(self.location or "")
        self.entity_type = str(self.entity_type or "UNKNOWN").upper()
        self.placeholder = str(self.placeholder or "")
        self.source = str(self.source or "unknown")
        self.confidence = _safe_float(self.confidence)
        self.status = self.status if self.status in {"pending", "accepted", "rejected", "skipped"} else "pending"
        self.redacted_context = str(self.redacted_context or "")
        self.decision_note = str(self.decision_note or "")
        if not isinstance(self.position, Mapping):
            self.position = {}
        else:
            self.position = dict(self.position)
        if not self.finding_id:
            identity = "\x1f".join(
                (self.document_ref, self.location, self.entity_type, self.placeholder)
            ).encode("utf-8")
            self.finding_id = hashlib.sha256(identity).hexdigest()[:20]

    def to_dict(self) -> Dict[str, Any]:
        """Публичная запись очереди, не содержащая исходного значения."""

        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReviewFinding":
        # Делiberately whitelist fields.  A caller cannot smuggle an ``original``
        # value into persisted review data by passing a decoder record.
        fields = {
            "document_ref", "location", "entity_type", "placeholder", "confidence",
            "source", "redacted_context", "finding_id", "status", "decision_note", "position",
        }
        return cls(**{key: value[key] for key in fields if key in value})


class ReviewQueue:
    """Потокобезопасно не требуется: очередь живёт в одном локальном запуске."""

    def __init__(self, findings: Optional[Iterable[ReviewFinding | Mapping[str, Any]]] = None):
        self._items: Dict[str, ReviewFinding] = {}
        for item in findings or ():
            self.add(item)

    def add(self, finding: ReviewFinding | Mapping[str, Any]) -> ReviewFinding:
        item = finding if isinstance(finding, ReviewFinding) else ReviewFinding.from_mapping(finding)
        self._items[item.finding_id] = item
        return item

    def get(self, finding_id: str) -> Optional[ReviewFinding]:
        return self._items.get(str(finding_id))

    def pending(self) -> list[ReviewFinding]:
        return [item for item in self._items.values() if item.status == "pending"]

    def items(self, status: Optional[str] = None) -> list[ReviewFinding]:
        values = list(self._items.values())
        return [item for item in values if status is None or item.status == status]

    def filter(
        self, *, entity_type: Optional[str] = None, document_ref: Optional[str] = None,
        status: Optional[str] = None, min_confidence: Optional[float] = None,
    ) -> list[ReviewFinding]:
        """Return a safe, deterministic subset for the manual-review screen."""
        entity = str(entity_type or "").strip().upper()
        document = str(document_ref or "").strip().casefold()
        selected = []
        threshold = None if min_confidence in (None, "") else _safe_float(min_confidence)
        for item in self._items.values():
            if entity and item.entity_type != entity:
                continue
            if document and document not in item.document_ref.casefold():
                continue
            if status and item.status != str(status):
                continue
            if threshold is not None and item.confidence < threshold:
                continue
            selected.append(item)
        return selected

    def decide(self, finding_id: str, status: str, note: str = "") -> ReviewFinding:
        if status not in {"accepted", "rejected", "skipped"}:
            raise ValueError("status должен быть accepted, rejected или skipped")
        item = self._items.get(str(finding_id))
        if item is None:
            raise KeyError(f"Находка не найдена: {finding_id}")
        item.status = status
        item.decision_note = str(note or "")
        return item

    def summary(self) -> Dict[str, int]:
        result = {status: 0 for status in ("pending", "accepted", "rejected", "skipped")}
        for item in self._items.values():
            result[item.status] = result.get(item.status, 0) + 1
        result["total"] = len(self._items)
        return result

    def by_entity_type(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for item in self._items.values():
            result[item.entity_type] = result.get(item.entity_type, 0) + 1
        return dict(sorted(result.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "items": [item.to_dict() for item in self._items.values()],
            "summary": self.summary(),
        }

    def save(self, path: os.PathLike[str] | str) -> Path:
        import app_paths
        target = Path(path)
        return app_paths.atomic_write_json(target, self.to_dict(), indent=2)


@dataclass
class MetadataCleanupReport:
    source_type: str
    output_path: str
    changed: bool = False
    removed_fields: list[str] = field(default_factory=list)
    skipped_features: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _xml_bytes(root: ET.Element) -> bytes:
    # OPC package relationship/content-type parts use a default namespace.
    # Some Office readers reject the semantically equivalent ``ns0:`` form
    # emitted by ElementTree, so retain the canonical default declaration.
    if isinstance(root.tag, str) and root.tag.startswith("{"):
        namespace = root.tag[1:].split("}", 1)[0]
        if namespace in {
            "http://schemas.openxmlformats.org/package/2006/relationships",
            "http://schemas.openxmlformats.org/package/2006/content-types",
        }:
            ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _strip_office_xml_metadata(data: bytes, part_name: str, report: MetadataCleanupReport) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        report.skipped_features.append(part_name + ":invalid_xml")
        return data
    removed = 0
    for child in list(root):
        root.remove(child)
        removed += 1
    if removed:
        report.removed_fields.append(part_name)
    return _xml_bytes(root)


def _strip_package_relationships(data: bytes, report: MetadataCleanupReport) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    removed = 0
    for child in list(root):
        target = child.attrib.get("Target", "").replace("\\", "/").lstrip("/")
        if target in {"docProps/custom.xml", "docProps/thumbnail.jpeg", "docProps/thumbnail.png"}:
            root.remove(child)
            removed += 1
    if removed:
        report.removed_fields.append("_rels/.rels")
    return _xml_bytes(root)


def _strip_content_types(data: bytes, report: MetadataCleanupReport) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    removed = 0
    for child in list(root):
        part_name = child.attrib.get("PartName", "").lstrip("/")
        if part_name in {"docProps/custom.xml", "docProps/thumbnail.jpeg", "docProps/thumbnail.png"}:
            root.remove(child)
            removed += 1
    if removed:
        report.removed_fields.append("[Content_Types].xml")
    return _xml_bytes(root)


def _copy_and_clean_office_package(source: Path, target: Path, report: MetadataCleanupReport) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".docxdodyr-metadata-", suffix=source.suffix, dir=str(target.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(temp_path, "w") as zout:
            for info in zin.infolist():
                name = info.filename
                if name in {"docProps/custom.xml", "docProps/thumbnail.jpeg", "docProps/thumbnail.png"}:
                    report.removed_fields.append(name)
                    report.changed = True
                    continue
                data = zin.read(name)
                if name in {"docProps/core.xml", "docProps/app.xml"}:
                    data = _strip_office_xml_metadata(data, name, report)
                    report.changed = True
                elif name == "_rels/.rels":
                    data = _strip_package_relationships(data, report)
                elif name == "[Content_Types].xml":
                    data = _strip_content_types(data, report)
                zout.writestr(info, data)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    with zipfile.ZipFile(target, "r") as package:
        names = set(package.namelist())
        for marker, candidates in {
            "comments": ("word/comments.xml", "word/commentsExtended.xml"),
            "tracked_changes": (),
            "embeddings": (),
        }.items():
            if candidates and any(name in names for name in candidates):
                report.skipped_features.append(marker)
            elif marker == "embeddings" and any(name.startswith("word/embeddings/") for name in names):
                report.skipped_features.append(marker)
    shutil.copymode(source, target)


def _pdf_has_risky_objects(reader: Any) -> list[str]:
    reasons: list[str] = []
    try:
        if reader.is_encrypted:
            reasons.append("encrypted_pdf")
        root = reader.trailer.get("/Root", {})
        if root.get("/AcroForm"):
            reasons.append("acroform")
        if root.get("/Perms"):
            reasons.append("digital_signature")
        names = root.get("/Names")
        if names and names.get("/EmbeddedFiles"):
            reasons.append("embedded_files")
    except Exception:
        reasons.append("unreadable_structure")
    return reasons


def _clean_pdf(source: Path, target: Path, report: MetadataCleanupReport) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(str(source), strict=False)
        risky = _pdf_has_risky_objects(reader)
        if risky:
            report.skipped_features.extend(risky)
            shutil.copy2(source, target)
            _set_private_mode(target)
            return
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        # Clear Info metadata and XMP packet from the document root/pages.  The
        # producer marker is intentionally omitted; pypdf may still add its
        # own non-personal producer field.
        try:
            writer._info = None
            writer._root_object.pop("/Metadata", None)
        except Exception:
            pass
        for page in writer.pages:
            try:
                page.pop("/Metadata", None)
            except Exception:
                pass
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            writer.write(stream)
        _set_private_mode(target)
        report.changed = True
        report.removed_fields.extend(["pdf_info", "pdf_xmp"])
    except Exception as exc:
        report.error = str(exc)
        report.skipped_features.append("pdf_rewrite_failed")
        target.unlink(missing_ok=True)


def clean_file_metadata(
    source_path: os.PathLike[str] | str,
    output_path: Optional[os.PathLike[str] | str] = None,
) -> MetadataCleanupReport:
    """Копирует файл в ``output_path`` и удаляет только безопасные метаданные.

    Исходник никогда не меняется. Для PDF с шифрованием, формами, вложениями
    или подписью создаётся byte-for-byte копия и в отчёте указывается причина
    пропуска. Комментарии/колонтитулы/сноски не удаляются автоматически:
    это содержимое документа, а не бесспорно безопасные метаданные.
    """

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if output_path is None:
        output = source.with_name(source.stem + "_metadata_cleaned" + source.suffix)
    else:
        output = Path(output_path)
    if output.resolve() == source.resolve():
        raise ValueError("output_path должен отличаться от исходного файла")
    report = MetadataCleanupReport(source_type=suffix.lstrip("."), output_path=str(output))
    if suffix in {".docx", ".docm", ".xlsx", ".xlsm", ".xltx", ".xltm"}:
        try:
            _copy_and_clean_office_package(source, output, report)
        except Exception as exc:
            report.error = str(exc)
            output.unlink(missing_ok=True)
    elif suffix == ".pdf":
        _clean_pdf(source, output, report)
    else:
        report.skipped_features.append("unsupported_format")
        shutil.copy2(source, output)
        _set_private_mode(output)
    if output.is_file():
        _set_private_mode(output)
    return report


def _clean_counts(counts: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Оставляет в сертификате только числовые агрегаты без PII."""

    counts = counts or {}
    result: Dict[str, Any] = {
        "files": _safe_int(counts.get("files", counts.get("file_count", 0))),
        "replacements": _safe_int(counts.get("replacements", counts.get("total_replacements", 0))),
        "entities_by_type": {},
    }
    raw_entities = counts.get("entities_by_type", counts.get("entity_counts", {}))
    if isinstance(raw_entities, Mapping):
        for key, value in raw_entities.items():
            if isinstance(key, str):
                result["entities_by_type"][key.upper()[:80]] = _safe_int(value)
    for key in ("documents_reviewed", "accepted", "rejected", "pending", "skipped"):
        if key in counts:
            result[key] = _safe_int(counts[key])
    return result


def build_audit_certificate(
    input_path: os.PathLike[str] | str,
    output_path: os.PathLike[str] | str,
    *,
    counts: Optional[Mapping[str, Any]] = None,
    review_queue: Optional[ReviewQueue] = None,
    cleanup_report: Optional[MetadataCleanupReport] = None,
    rules_version: str = RULES_VERSION,
) -> Dict[str, Any]:
    """Создаёт сериализуемый сертификат без исходных значений сущностей."""

    source = Path(input_path)
    output = Path(output_path)
    queue_summary = review_queue.summary() if review_queue is not None else {}
    safe_counts = _clean_counts(counts)
    if queue_summary:
        safe_counts["review"] = queue_summary
    certificate: Dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "rules_version": str(rules_version),
        "generated_at_utc": _utc_now(),
        "input": _file_fingerprint(source),
        "output": _file_fingerprint(output),
        "counts": safe_counts,
    }
    if cleanup_report is not None:
        certificate["metadata_cleanup"] = {
            "source_type": cleanup_report.source_type,
            "changed": bool(cleanup_report.changed),
            "removed_fields": list(cleanup_report.removed_fields),
            "skipped_features": list(cleanup_report.skipped_features),
            "error": bool(cleanup_report.error),
        }
    return certificate


def write_audit_certificate(certificate: Mapping[str, Any], path: os.PathLike[str] | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(certificate), ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        if os.name == "posix":
            os.chmod(target, 0o600)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return target


__all__ = [
    "AUDIT_SCHEMA_VERSION", "RULES_VERSION", "ReviewFinding", "ReviewQueue",
    "MetadataCleanupReport", "sha256_file", "clean_file_metadata",
    "build_audit_certificate", "write_audit_certificate",
]
