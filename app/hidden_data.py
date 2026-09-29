# -*- coding: utf-8 -*-
"""Проверка и безопасное удаление скрытых данных из Office/PDF.

Модуль не содержит правил Pullenti и не получает исходные значения сущностей.
Он работает только с контейнерной структурой документа.  Все операции пишут
в новый файл; при невозможности доказать целостность операция завершается
``HiddenDataError`` и целевой файл не создаётся.

Для PDF предусмотрен отдельный необратимый режим ``flatten_pdf_to_images``.
Он создаёт PDF, состоящий только из растровых страниц: извлекаемого текста,
слоёв аннотаций, вложений и метаданных в результате нет.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import hashlib
import posixpath
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from xml.etree import ElementTree as ET


HIDDEN_DATA_SCHEMA_VERSION = 1
HIDDEN_DATA_KINDS = (
    "comments", "footnotes", "endnotes", "hidden_text", "tracked_changes",
    "embedded_objects", "embedded_files", "annotations", "acroform",
    "javascript", "macros", "custom_xml", "external_links", "document_properties",
    "image_exif_xmp", "pdf_info", "pdf_xmp", "digital_signature", "encrypted_pdf",
)
TEXTUAL_HIDDEN_KINDS = frozenset({"comments", "footnotes", "endnotes", "hidden_text"})
DESTRUCTIVE_HIDDEN_KINDS = frozenset({
    "comments", "footnotes", "endnotes", "hidden_text", "tracked_changes",
    "embedded_objects", "embedded_files", "annotations", "acroform", "javascript",
    "macros", "custom_xml", "external_links", "document_properties", "image_exif_xmp",
    "pdf_info", "pdf_xmp",
})
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_W = "{" + _W_NS + "}"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


class HiddenDataError(RuntimeError):
    """Операция остановлена, чтобы не выдать повреждённый документ."""


@dataclass
class HiddenDataReport:
    source_type: str
    source_path: str
    detected: Dict[str, int] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    changed: bool = False

    def add_detected(self, name: str, count: int = 1) -> None:
        if count > 0:
            self.detected[name] = self.detected.get(name, 0) + int(count)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = HIDDEN_DATA_SCHEMA_VERSION
        value["safe_to_release"] = not bool(self.errors)
        return value

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return only aggregate, UI-safe risk data (never source path/text)."""
        return {
            "schema_version": HIDDEN_DATA_SCHEMA_VERSION,
            "source_type": self.source_type,
            "detected": {str(k): int(v) for k, v in self.detected.items()},
            "skipped": list(self.skipped),
            "errors": list(self.errors),
            "safe_to_release": not bool(self.errors),
        }


@dataclass
class FlattenReport:
    source_path: str
    output_path: str
    page_count: int
    dpi: int
    text_layer_removed: bool = True
    annotations_excluded: bool = True
    metadata_cleared: bool = True

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["schema_version"] = HIDDEN_DATA_SCHEMA_VERSION
        return value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_target(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _set_private_mode(path: Path) -> None:
    """Keep generated documents private on POSIX systems."""
    if os.name == "posix":
        os.chmod(path, 0o600)


def validate_safe_zip_archive(
    package: zipfile.ZipFile,
    max_files: int = 10000,
    max_total_size: int = 500 * 1024 * 1024,
    max_ratio: float = 100.0,
) -> None:
    """Validate archive against traversal, bombs, symlinks, and collisions."""
    infos = package.infolist()
    if len(infos) > max_files:
        raise HiddenDataError(f"Превышен лимит файлов в архиве: {len(infos)} > {max_files}")

    seen_names: set[str] = set()
    total_uncompressed = 0

    for info in infos:
        name = info.filename
        if ".." in name or name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
            raise HiddenDataError(f"Обнаружен недопустимый путь в архиве: {name}")

        norm_name = os.path.normpath(name).replace("\\", "/").lower()
        if norm_name in seen_names:
            raise HiddenDataError(f"Обнаружено повторяющееся имя в архиве: {name}")
        seen_names.add(norm_name)

        mode = (info.external_attr >> 16) & 0o177777
        if (mode & 0o170000) == 0o120000:
            raise HiddenDataError(f"Обнаружена символическая ссылка в архиве: {name}")

        total_uncompressed += info.file_size
        if total_uncompressed > max_total_size:
            raise HiddenDataError(f"Превышен суммарный распакованный размер архива: {total_uncompressed} байт")

        if info.file_size > 1024 * 1024:
            comp_size = max(1, info.compress_size)
            ratio = info.file_size / comp_size
            if ratio > max_ratio:
                raise HiddenDataError(f"Подозрительный коэффициент сжатия ({ratio:.1f}x) для {name}")


def _xml_parse(data: bytes, report: Optional[HiddenDataReport] = None, part: str = "") -> Optional[ET.Element]:
    if len(data) > 50 * 1024 * 1024:
        if report is not None:
            report.errors.append(f"oversized_xml:{part}")
        return None
    try:
        try:
            import defusedxml.ElementTree as DefusedET
            return DefusedET.fromstring(data)
        except ImportError:
            return ET.fromstring(data)
    except (ET.ParseError, Exception):
        if report is not None:
            report.errors.append(f"invalid_xml:{part}")
        return None


def _xml_parse_with_namespaces(data: bytes) -> tuple[ET.Element, dict[str, str]]:
    """Parse XML while retaining the source prefix mapping for OOXML MC."""
    namespaces: dict[str, str] = {}
    for _event, item in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        prefix, uri = item
        prefix = prefix or ""
        namespaces.setdefault(prefix, uri)
        if prefix not in {"xml", "xmlns"}:
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass
    return ET.fromstring(data), namespaces


def _repair_mc_ignorable(root: ET.Element, namespaces: Mapping[str, str]) -> None:
    """Drop only stale mc:Ignorable tokens whose xmlns declarations vanished.

    ElementTree does not serialize namespace declarations referenced solely
    from an attribute *value*.  Leaving such names in ``mc:Ignorable`` makes
    Microsoft Word report corrupt content.  Prefixes that are actually used
    by an element/attribute are retained and registered under their original
    names.
    """
    used_uris: set[str] = set()
    for node in root.iter():
        for qualified in (node.tag, *node.attrib.keys()):
            if isinstance(qualified, str) and qualified.startswith("{"):
                used_uris.add(qualified[1:].split("}", 1)[0])
    attr = "{" + _MC_NS + "}Ignorable"
    for node in root.iter():
        value = node.attrib.get(attr)
        if not value:
            continue
        retained = [
            prefix for prefix in value.split()
            if namespaces.get(prefix) in used_uris
        ]
        if retained:
            node.set(attr, " ".join(retained))
        else:
            del node.attrib[attr]


def _office_image_metadata(data: bytes, part: str) -> tuple[int, int]:
    """Returns (EXIF/XMP marker count, image count), without retaining pixels."""
    if not part.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp")):
        return 0, 0
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        exif_count = len(image.getexif())
        info = image.info
        xmp_count = 1 if any(key.lower() == "xmp" for key in info) else 0
        return exif_count + xmp_count, 1
    except Exception:
        return 0, 0


def _find_chart_embedding_parts(names: Any, read_bytes_fn) -> set[str]:
    """Find embedding files that are bound to charts as their underlying data source.

    In OOXML (Word, Excel, PowerPoint), charts store their tabular series data
    in embedded spreadsheets inside embeddings/ (e.g. word/embeddings/*.xlsx).
    Removing these files breaks the chart and corrupts the document in Office.
    """
    chart_targets: set[str] = set()
    for name in names:
        low = name.lower()
        if "/charts/_rels/" in low and low.endswith(".rels"):
            try:
                raw = read_bytes_fn(name)
                root = _xml_parse(raw)
                if root is None:
                    continue
                base_dir = posixpath.dirname(posixpath.dirname(name))
                for child in root:
                    target = child.attrib.get("Target", "")
                    if child.attrib.get("TargetMode") == "External" or not target:
                        continue
                    resolved = posixpath.normpath(posixpath.join(base_dir, target)).lstrip("/")
                    chart_targets.add(resolved.lower())
            except Exception:
                continue
    return chart_targets


def _scan_office(path: Path) -> HiddenDataReport:
    report = HiddenDataReport(path.suffix.lower().lstrip("."), str(path))
    try:
        with zipfile.ZipFile(path, "r") as package:
            validate_safe_zip_archive(package)
            infos = package.infolist()
            names = {info.filename for info in infos}
            chart_parts = _find_chart_embedding_parts(names, package.read)
            for name in names:
                low = name.lower()
                if low.startswith("_xmlsignatures/") or low.endswith("/sig1.xml"):
                    report.add_detected("digital_signature")
                if re.match(r"word/(comments|commentsextended|persons)(?:[^/]*)?\.xml$", low):
                    report.add_detected("comments")
                if low in {"word/footnotes.xml", "word/endnotes.xml"}:
                    report.add_detected("footnotes" if "footnotes" in low else "endnotes")
                if low.startswith(("word/embeddings/", "xl/embeddings/", "ppt/embeddings/")):
                    if low not in chart_parts:
                        report.add_detected("embedded_objects")
                if low.startswith("customxml/"):
                    report.add_detected("custom_xml")
                if low.endswith("vbaProject.bin".lower()):
                    report.add_detected("macros")
                if low in {"docprops/custom.xml", "docprops/thumbnail.jpeg", "docprops/thumbnail.png"}:
                    report.add_detected("document_properties")
                if "/media/" in low:
                    exif, image_count = _office_image_metadata(package.read(name), name)
                    if image_count:
                        report.add_detected("images")
                    if exif:
                        report.add_detected("image_exif_xmp", exif)
                if low.endswith(".xml") and low not in {"[content_types].xml"}:
                    data = package.read(name)
                    root = _xml_parse(data)
                    if root is not None:
                        revisions = 0
                        hidden = 0
                        external = 0
                        revision_tags = {
                            "ins", "del", "moveFrom", "moveTo",
                            "commentRangeStart", "commentRangeEnd", "commentReference",
                        }
                        for node in root.iter():
                            local = _local_name(node.tag)
                            revisions += local in revision_tags
                            hidden += local == "vanish"
                            external += node.attrib.get("TargetMode") == "External"
                        if revisions:
                            report.add_detected("tracked_changes", revisions)
                        if hidden:
                            report.add_detected("hidden_text", hidden)
                        if external:
                            report.add_detected("external_links", external)
    except (zipfile.BadZipFile, OSError) as exc:
        report.errors.append(f"office_read_failed:{type(exc).__name__}")
    return report


def _pdf_obj(value: Any) -> Any:
    try:
        return value.get_object()
    except Exception:
        return value


def _pdf_has_key(value: Any, key: str) -> bool:
    obj = _pdf_obj(value)
    return hasattr(obj, "get") and obj.get(key) is not None


def _scan_pdf(path: Path) -> HiddenDataReport:
    report = HiddenDataReport("pdf", str(path))
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            report.add_detected("encrypted_pdf")
            return report
        root = _pdf_obj(reader.trailer.get("/Root", {}))
        if reader.metadata:
            report.add_detected("pdf_info", len(reader.metadata))
        try:
            if reader.xmp_metadata is not None:
                report.add_detected("pdf_xmp")
        except Exception:
            report.add_detected("pdf_xmp")
        if _pdf_has_key(root, "/AcroForm"):
            report.add_detected("acroform")
        if _pdf_has_key(root, "/Perms"):
            report.add_detected("digital_signature")
        names = _pdf_obj(root.get("/Names")) if hasattr(root, "get") else None
        if names is not None and _pdf_has_key(names, "/EmbeddedFiles"):
            report.add_detected("embedded_files")
        if names is not None and _pdf_has_key(names, "/JavaScript"):
            report.add_detected("javascript")
        for page in reader.pages:
            page_obj = _pdf_obj(page)
            annots = _pdf_obj(page_obj.get("/Annots")) if hasattr(page_obj, "get") else None
            if annots:
                for item in annots:
                    annot = _pdf_obj(item)
                    subtype = str(annot.get("/Subtype", "")) if hasattr(annot, "get") else ""
                    report.add_detected("annotations")
                    if subtype in {"/Text", "/FileAttachment", "/Widget", "/Sound", "/Movie", "/RichMedia"}:
                        report.add_detected("interactive_annotations")
                    if subtype == "/FileAttachment":
                        report.add_detected("embedded_files")
        # pypdf exposes PDF image streams but not their EXIF payload.  Inspect
        # extracted bytes with Pillow when available; no pixels are persisted.
        try:
            import fitz
            from PIL import Image
            document = fitz.open(str(path))
            try:
                seen_xrefs: set[int] = set()
                for page in document:
                    for image_info in page.get_images(full=True):
                        xref = int(image_info[0])
                        if xref in seen_xrefs:
                            continue
                        seen_xrefs.add(xref)
                        image_data = document.extract_image(xref).get("image", b"")
                        if not image_data:
                            continue
                        image = Image.open(io.BytesIO(image_data))
                        if image.getexif() or any(str(key).lower() == "xmp" for key in image.info):
                            report.add_detected("image_exif_xmp")
            finally:
                document.close()
        except Exception:
            # Image inspection is advisory.  The PDF itself remains readable.
            report.skipped.append("pdf_image_metadata_inspection_unavailable")
    except Exception as exc:
        report.errors.append(f"pdf_read_failed:{type(exc).__name__}")
    return report


def inspect_hidden_data(path: os.PathLike[str] | str) -> HiddenDataReport:
    """Возвращает агрегированный отчёт без извлечения текста и PII."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() in {".docx", ".docm", ".xlsx", ".xlsm", ".xltx", ".xltm", ".pptx", ".pptm"}:
        return _scan_office(source)
    if source.suffix.lower() == ".pdf":
        return _scan_pdf(source)
    return HiddenDataReport(source.suffix.lower().lstrip("."), str(source), skipped=["unsupported_format"])


def inspection_fingerprint(path: os.PathLike[str] | str) -> str:
    """Stable file fingerprint used to bind a policy decision to one input."""
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_relations_and_content_types(
    data: bytes,
    removed_targets: set[str],
    is_content_types: bool = False,
    rel_path: Optional[str] = None,
) -> bytes:
    root, _namespaces = _xml_parse_with_namespaces(data)
    rel_base_dir = posixpath.dirname(posixpath.dirname(rel_path)) if (rel_path and "/_rels/" in rel_path.lower()) else ""
    for child in list(root):
        if is_content_types:
            part = _safe_target(child.attrib.get("PartName", ""))
            content_type = child.attrib.get("ContentType", "").lower()
            removed_kinds = {item.rsplit("/", 1)[-1].lower() for item in removed_targets}
            remove_related_type = (
                ("comments" in content_type and any("comments" in item for item in removed_kinds))
                or ("footnotes" in content_type and any("footnotes" in item for item in removed_kinds))
                or ("endnotes" in content_type and any("endnotes" in item for item in removed_kinds))
            )
            if part in removed_targets or remove_related_type:
                root.remove(child)
        else:
            raw_target = child.attrib.get("Target", "")
            target = _safe_target(raw_target)
            resolved_target = ""
            if rel_base_dir and target and not target.startswith("/"):
                resolved_target = posixpath.normpath(posixpath.join(rel_base_dir, target)).lstrip("/")
            
            target_low = target.lower()
            resolved_low = resolved_target.lower()
            removed_low = {t.lower() for t in removed_targets}
            matched = (
                target_low in removed_low
                or (resolved_low and resolved_low in removed_low)
                or any(
                    target_low.endswith("/" + item.lower())
                    or target_low == item.lower()
                    or target_low == item.rsplit("/", 1)[-1].lower()
                    or (resolved_low and (
                        resolved_low.endswith("/" + item.lower())
                        or resolved_low == item.lower()
                        or resolved_low == item.rsplit("/", 1)[-1].lower()
                    ))
                    for item in removed_targets
                )
            )
            if matched:
                root.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _external_relationship_ids(data: bytes) -> set[str]:
    root, _namespaces = _xml_parse_with_namespaces(data)
    return {
        child.attrib.get("Id", "")
        for child in root
        if child.attrib.get("TargetMode") == "External"
    } - {""}


def _remove_external_relationships(data: bytes) -> bytes:
    root, _namespaces = _xml_parse_with_namespaces(data)
    for child in list(root):
        if child.attrib.get("TargetMode") == "External":
            root.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _unwrap_external_hyperlinks(data: bytes, relation_ids: set[str]) -> bytes:
    """Preserve displayed runs while removing links whose targets were dropped."""
    root, namespaces = _xml_parse_with_namespaces(data)
    relationship_attr = "{" + _R_NS + "}id"

    def visit(parent: ET.Element) -> None:
        for child in list(parent):
            if _local_name(child.tag) == "hyperlink" and child.attrib.get(relationship_attr) in relation_ids:
                index = list(parent).index(child)
                parent.remove(child)
                for offset, grandchild in enumerate(list(child)):
                    parent.insert(index + offset, grandchild)
                continue
            visit(child)

    visit(root)
    _repair_mc_ignorable(root, namespaces)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _transform_word_xml(data: bytes, *, comments: bool, notes: bool, revisions: bool, hidden_text: bool,
                        text_transform=None, text_kind: str = "hidden_text") -> tuple[bytes, set[str]]:
    root, namespaces = _xml_parse_with_namespaces(data)
    removed: set[str] = set()
    tags_drop = set()
    tags_unwrap = set()
    if comments:
        tags_drop.update({"commentRangeStart", "commentRangeEnd", "commentReference"})
    if notes:
        tags_drop.update({"footnoteReference", "endnoteReference"})
    if revisions:
        tags_drop.update({"del", "moveFrom"})
        tags_unwrap.update({"ins", "moveTo"})
    if hidden_text:
        tags_drop.update({"vanish"})

    def visit(parent: ET.Element, inside_hidden_run: bool = False) -> None:
        for child in list(parent):
            local = _local_name(child.tag)
            child_is_hidden_run = (
                local == "r"
                and any(_local_name(node.tag) == "vanish" for node in child.iter())
            )
            if hidden_text and child_is_hidden_run:
                parent.remove(child)
                removed.add("hidden_text")
                continue
            if local in tags_drop:
                parent.remove(child)
                removed.add(local)
                continue
            if local in tags_unwrap:
                index = list(parent).index(child)
                parent.remove(child)
                for offset, grandchild in enumerate(list(child)):
                    parent.insert(index + offset, grandchild)
                removed.add(local)
                continue
            transform_this_text = text_kind != "hidden_text" or inside_hidden_run
            if local == "t" and text_transform is not None and child.text and transform_this_text:
                transformed = text_transform(child.text, text_kind)
                if transformed != child.text:
                    child.text = transformed
                    removed.add(f"anonymized_{text_kind}")
            visit(child, inside_hidden_run or child_is_hidden_run)

    visit(root)
    _repair_mc_ignorable(root, namespaces)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), removed


def _sanitize_office(source: Path, target: Path, report: HiddenDataReport, *, comments: bool, notes: bool,
                     revisions: bool, hidden_text: bool, attachments: bool, exif_xmp: bool,
                     external_links: bool = False,
                     text_transform=None, text_kinds: Optional[set[str]] = None) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".docxdodyr-hidden-", suffix=source.suffix, dir=str(target.parent))
    os.close(fd)
    temp = Path(tmp_name)
    removed_parts: set[str] = set()
    try:
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(temp, "w") as zout:
            validate_safe_zip_archive(zin)
            names = {info.filename for info in zin.infolist()}
            external_ids_by_owner: dict[str, set[str]] = {}
            if external_links:
                for rel_name in names:
                    if not rel_name.lower().endswith(".rels"):
                        continue
                    try:
                        relation_ids = _external_relationship_ids(zin.read(rel_name))
                    except ET.ParseError:
                        relation_ids = set()
                    rel_path = Path(rel_name)
                    if relation_ids and rel_path.parent.name == "_rels":
                        owner = str(rel_path.parent.parent / rel_path.name[:-5]).replace("\\", "/")
                        external_ids_by_owner[owner] = relation_ids
            chart_parts = _find_chart_embedding_parts(names, zin.read)
            # Compute all parts before writing relations.  ZIP member order is
            # not guaranteed, so a .rels part can precede the removed target.
            planned_removed_parts = {
                name for name in names
                if (comments and re.match(r"word/(comments|commentsextended|persons)(?:[^/]*)?\.xml$", name.lower()))
                or (notes and name.lower() in {"word/footnotes.xml", "word/endnotes.xml"})
                or (attachments and name.lower().startswith(("word/embeddings/", "xl/embeddings/", "ppt/embeddings/")) and name.lower() not in chart_parts)
                or (name.lower().startswith("customxml/") and getattr(report, "_remove_custom_xml", False))
                or (name.lower().startswith("docprops/") and getattr(report, "_remove_document_properties", False))
                or (name.lower().endswith("vbaproject.bin") and getattr(report, "_remove_macros", False))
            }
            removed_parts.update(planned_removed_parts)
            for info in zin.infolist():
                name = info.filename
                low = name.lower()
                if comments and re.match(r"word/(comments|commentsextended|persons)(?:[^/]*)?\.xml$", low):
                    removed_parts.add(name)
                    report.removed.append("comments")
                    report.changed = True
                    continue
                if notes and low in {"word/footnotes.xml", "word/endnotes.xml"}:
                    removed_parts.add(name)
                    report.removed.append("footnotes" if "footnotes" in low else "endnotes")
                    report.changed = True
                    continue
                if attachments and low.startswith(("word/embeddings/", "xl/embeddings/", "ppt/embeddings/")):
                    if low not in chart_parts:
                        removed_parts.add(name)
                        report.removed.append("embedded_objects")
                        report.changed = True
                        continue
                if getattr(report, "_remove_custom_xml", False) and low.startswith("customxml/"):
                    removed_parts.add(name)
                    report.removed.append("custom_xml")
                    report.changed = True
                    continue
                if getattr(report, "_remove_document_properties", False) and low.startswith("docprops/"):
                    removed_parts.add(name)
                    report.removed.append("document_properties")
                    report.changed = True
                    continue
                if getattr(report, "_remove_macros", False) and low.endswith("vbaproject.bin"):
                    removed_parts.add(name)
                    report.removed.append("macros")
                    report.changed = True
                    continue
                data = zin.read(name)
                if external_links and name in external_ids_by_owner and low.endswith(".xml"):
                    data = _unwrap_external_hyperlinks(data, external_ids_by_owner[name])
                    report.removed.append("external_links")
                    report.changed = True
                if low.endswith(".xml") and (comments or notes or revisions or hidden_text or text_transform):
                    try:
                        kind = "hidden_text"
                        if "comments" in low:
                            kind = "comments"
                        elif "footnotes" in low:
                            kind = "footnotes"
                        elif "endnotes" in low:
                            kind = "endnotes"
                        callback = text_transform if (text_transform is not None and (text_kinds is None or kind in text_kinds)) else None
                        data, removed_tags = _transform_word_xml(
                            data, comments=comments, notes=notes, revisions=revisions,
                            hidden_text=hidden_text, text_transform=callback, text_kind=kind,
                        )
                        if removed_tags:
                            report.removed.extend(sorted(removed_tags))
                            report.changed = True
                    except ET.ParseError:
                        # Non-Word XML is retained; document XML parse failures are fatal.
                        if low in {"word/document.xml", "word/footnotes.xml", "word/endnotes.xml"}:
                            raise HiddenDataError(f"Нельзя безопасно разобрать {name}")
                if exif_xmp and "/media/" in low:
                    data, did_clean = _strip_image_metadata(data, name)
                    if did_clean:
                        report.removed.append("image_exif_xmp")
                        report.changed = True
                if external_links and low.endswith(".rels"):
                    data = _remove_external_relationships(data)
                if low.endswith(".rels") and planned_removed_parts:
                    data = _remove_relations_and_content_types(
                        data, {_safe_target(item) for item in removed_parts}, rel_path=name
                    )
                elif low == "[content_types].xml" and planned_removed_parts:
                    data = _remove_relations_and_content_types(
                        data, {_safe_target(item) for item in removed_parts}, is_content_types=True
                    )
                zout.writestr(info, data)
        # A malformed package must never be handed to the caller.
        with zipfile.ZipFile(temp, "r") as check:
            if not check.namelist():
                raise HiddenDataError("После очистки Office-пакет пуст")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, target)
        _set_private_mode(target)
    finally:
        if temp.exists():
            temp.unlink()


def _strip_image_metadata(data: bytes, part: str) -> tuple[bytes, bool]:
    if not part.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp")):
        return data, False
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        has_metadata = bool(image.getexif()) or any(str(k).lower() == "xmp" for k in image.info)
        if not has_metadata:
            return data, False
        output = io.BytesIO()
        save_kwargs: Dict[str, Any] = {}
        if image.format in {"JPEG", "WEBP"}:
            save_kwargs["quality"] = "keep" if image.format == "JPEG" else 95
        image.save(output, format=image.format, **save_kwargs)
        return output.getvalue(), True
    except Exception:
        return data, False


def _sanitize_pdf(source: Path, target: Path, report: HiddenDataReport, *, annotations: bool, attachments: bool,
                  xmp_exif: bool, forms: bool = False, javascript: bool = False) -> None:
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(source), strict=False)
    if reader.is_encrypted:
        raise HiddenDataError("Зашифрованный PDF нельзя безопасно очистить без пароля")
    root = _pdf_obj(reader.trailer.get("/Root", {}))
    if _pdf_has_key(root, "/Perms"):
        raise HiddenDataError("PDF имеет цифровую подпись; очистка потребует её уничтожения")
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    if xmp_exif and report.detected.get("image_exif_xmp"):
        raise HiddenDataError("В PDF обнаружен EXIF/XMP внутри растрового объекта; используйте flatten_pdf_to_images")
    if annotations or attachments:
        for page in writer.pages:
            page_obj = _pdf_obj(page)
            if "/Annots" in page_obj:
                if annotations:
                    del page_obj["/Annots"]
                    report.removed.append("annotations")
                    report.changed = True
                elif attachments:
                    annots = _pdf_obj(page_obj.get("/Annots"))
                    retained = []
                    for annot_ref in annots or ():
                        annot = _pdf_obj(annot_ref)
                        if hasattr(annot, "get") and annot.get("/Subtype") == "/FileAttachment":
                            report.removed.append("embedded_files")
                            report.changed = True
                        else:
                            retained.append(annot_ref)
                    page_obj["/Annots"] = retained
    if attachments:
        root_obj = _pdf_obj(writer._root_object)
        names = _pdf_obj(root_obj.get("/Names")) if hasattr(root_obj, "get") else None
        if names is not None and "/EmbeddedFiles" in names:
            del names["/EmbeddedFiles"]
            report.removed.append("embedded_files")
            report.changed = True
    root_obj = _pdf_obj(writer._root_object)
    if forms and "/AcroForm" in root_obj:
        del root_obj["/AcroForm"]
        report.removed.append("acroform")
        report.changed = True
    if javascript:
        names_obj = _pdf_obj(root_obj.get("/Names")) if hasattr(root_obj, "get") else None
        if names_obj is not None and "/JavaScript" in names_obj:
            del names_obj["/JavaScript"]
            report.removed.append("javascript")
            report.changed = True
    if xmp_exif:
        root_obj = _pdf_obj(writer._root_object)
        if "/Metadata" in root_obj:
            del root_obj["/Metadata"]
            report.removed.append("pdf_xmp")
            report.changed = True
    try:
        writer._info = None
    except Exception:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as stream:
        writer.write(stream)
    _set_private_mode(target)


def sanitize_hidden_data(
    source_path: os.PathLike[str] | str,
    output_path: os.PathLike[str] | str,
    *,
    remove_comments: bool = True,
    remove_footnotes: bool = False,
    remove_tracked_changes: bool = True,
    remove_hidden_text: bool = False,
    remove_attachments: bool = True,
    remove_annotations: bool = True,
    remove_image_metadata: bool = True,
    strict: bool = True,
    preflight_report: HiddenDataReport | None = None,
) -> HiddenDataReport:
    """Пишет очищенную копию и возвращает отчёт.

    ``remove_footnotes`` выключен по умолчанию: сноски являются видимым
    содержимым договора и их удаление может изменить смысл. Включение этой
    опции удаляет и ссылки на сноски, сохраняя целостность пакета.
    """
    source, target = Path(source_path), Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == target.resolve():
        raise ValueError("output_path должен отличаться от исходного файла")
    if preflight_report is not None:
        expected_type = source.suffix.lower().lstrip(".")
        try:
            same_source = Path(preflight_report.source_path).resolve() == source.resolve()
        except (OSError, RuntimeError):
            same_source = False
        if not same_source or preflight_report.source_type != expected_type:
            raise HiddenDataError("Предварительный отчёт относится к другому файлу")
        report = preflight_report
    else:
        report = inspect_hidden_data(source)
    if report.errors and strict:
        raise HiddenDataError("Проверка исходного файла завершилась ошибкой: " + ", ".join(report.errors))
    if strict and report.detected.get("digital_signature"):
        raise HiddenDataError("Подписанный файл нельзя безопасно изменить: подпись будет недействительна")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name("." + target.name + ".hidden.tmp")
    if temp.exists():
        temp.unlink()
    try:
        if source.suffix.lower() in {".docx", ".docm", ".xlsx", ".xlsm", ".xltx", ".xltm", ".pptx", ".pptm"}:
            _sanitize_office(source, temp, report, comments=remove_comments, notes=remove_footnotes,
                             revisions=remove_tracked_changes, hidden_text=remove_hidden_text,
                             attachments=remove_attachments, exif_xmp=remove_image_metadata,
                             external_links=False)
        elif source.suffix.lower() == ".pdf":
            _sanitize_pdf(source, temp, report, annotations=remove_annotations, attachments=remove_attachments,
                          xmp_exif=remove_image_metadata)
        else:
            shutil.copy2(source, temp)
            report.skipped.append("unsupported_format")
        os.replace(temp, target)
        _set_private_mode(target)
    except Exception as exc:
        if temp.exists():
            temp.unlink()
        if isinstance(exc, HiddenDataError):
            raise
        if strict:
            raise HiddenDataError(f"Очистка остановлена: {type(exc).__name__}") from exc
        report.errors.append(type(exc).__name__)
        shutil.copy2(source, target)
        _set_private_mode(target)
    return report


def apply_hidden_data_policy(
    source_path: os.PathLike[str] | str,
    output_path: os.PathLike[str] | str,
    actions: Mapping[str, str],
    *,
    text_transform=None,
    strict: bool = True,
) -> HiddenDataReport:
    """Apply explicit per-risk actions to a new copy.

    Actions are ``keep``, ``anonymize`` or ``remove``.  Textual Office parts
    can be anonymized through ``text_transform(text, kind)``; the caller owns
    the Pullenti batch context.  Unknown kinds/actions and signed files fail
    closed, so a UI typo can never silently produce a partial result.
    """
    source, target = Path(source_path), Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == target.resolve():
        raise ValueError("output_path должен отличаться от исходного файла")
    normalized = {str(k): str(v).lower() for k, v in dict(actions or {}).items()}
    unknown = set(normalized) - set(HIDDEN_DATA_KINDS)
    invalid = {key: value for key, value in normalized.items() if value not in {"keep", "anonymize", "remove"}}
    if unknown or invalid:
        raise HiddenDataError("Неизвестное действие policy: " + ", ".join(sorted(unknown or invalid)))
    report = inspect_hidden_data(source)
    if report.errors and strict:
        raise HiddenDataError("Проверка исходного файла завершилась ошибкой: " + ", ".join(report.errors))
    if strict and report.detected.get("digital_signature"):
        raise HiddenDataError("Подписанный файл нельзя безопасно изменить")
    if any(action == "anonymize" for action in normalized.values()) and text_transform is None:
        raise HiddenDataError("Для действия «обезличить» нужен Pullenti-преобразователь")
    suffix = source.suffix.lower()
    remove = {key for key, action in normalized.items() if action == "remove"}
    anonymize = {key for key, action in normalized.items() if action == "anonymize"}
    if suffix == ".pdf" and anonymize & {"annotations", "embedded_files", "pdf_info", "pdf_xmp"}:
        raise HiddenDataError("Обезличивание этой PDF-структуры не поддерживается; выберите удаление")
    if suffix in {".docx", ".docm", ".xlsx", ".xlsm", ".xltx", ".xltm", ".pptx", ".pptm"}:
        temp = target.with_name("." + target.name + ".policy.tmp")
        if temp.exists():
            temp.unlink()
        try:
            # These switches are attached only to this in-memory report and
            # never serialized; they keep the low-level sanitizer API stable.
            report._remove_custom_xml = "custom_xml" in remove
            report._remove_document_properties = "document_properties" in remove
            report._remove_macros = "macros" in remove
            _sanitize_office(
                source, temp, report,
                comments="comments" in remove,
                notes=bool(remove & {"footnotes", "endnotes"}),
                revisions="tracked_changes" in remove,
                hidden_text="hidden_text" in remove,
                attachments=bool(remove & {"embedded_objects", "embedded_files"}),
                exif_xmp=bool(remove & {"image_exif_xmp"}),
                external_links="external_links" in remove,
                text_transform=text_transform if anonymize else None,
                text_kinds=anonymize & TEXTUAL_HIDDEN_KINDS,
            )
            # A keep/anonymize-only policy may still have no package mutation;
            # report.changed captures the actual result, not the choice.
            os.replace(temp, target)
            _set_private_mode(target)
        except Exception:
            if temp.exists():
                temp.unlink()
            raise
    elif suffix == ".pdf":
        _sanitize_pdf(
            source, target, report,
            annotations="annotations" in remove or "acroform" in remove,
            attachments=bool(remove & {"embedded_files", "embedded_objects"}),
            xmp_exif=bool(remove & {"pdf_xmp", "image_exif_xmp"}),
            forms="acroform" in remove,
            javascript="javascript" in remove,
        )
        if "pdf_info" in remove:
            report.removed.append("pdf_info")
            report.changed = True
    elif suffix in {".md", ".txt"}:
        shutil.copy2(source, target)
        _set_private_mode(target)
        report.skipped.append("plain_text_no_hidden_data")
    else:
        raise HiddenDataError("Формат не поддерживает управление скрытым содержимым")
    if target.is_file():
        _set_private_mode(target)
    return report


def flatten_pdf_to_images(
    source_path: os.PathLike[str] | str,
    output_path: os.PathLike[str] | str,
    *,
    dpi: int = 150,
) -> FlattenReport:
    """Создаёт необратимую PDF-копию без текста и интерактивных объектов.

    На выходе остаются только растровые изображения страниц. Исходный PDF
    не меняется. ``dpi`` должен быть от 72 до 600; большие значения заметно
    увеличивают размер результата.
    """
    source, target = Path(source_path), Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == target.resolve():
        raise ValueError("output_path должен отличаться от исходного файла")
    if not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise ValueError("dpi должен быть целым числом от 72 до 600")
    try:
        import fitz
        document = fitz.open(str(source))
        page_count = document.page_count
        if document.needs_pass:
            document.close()
            raise HiddenDataError("Зашифрованный PDF нельзя необратимо расплющить без пароля")
        if page_count == 0:
            document.close()
            raise HiddenDataError("PDF не содержит страниц")
        flattened = fitz.open()
        scale = dpi / 72.0
        matrix = fitz.Matrix(scale, scale)
        try:
            for page in document:
                pixmap = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
                out_page = flattened.new_page(width=page.rect.width, height=page.rect.height)
                out_page.insert_image(out_page.rect, stream=pixmap.tobytes("png"))
            metadata = {key: "" for key in ("format", "title", "author", "subject", "keywords", "creator", "producer")}
            flattened.set_metadata(metadata)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name("." + target.name + ".flatten.tmp")
            if temp.exists():
                temp.unlink()
            flattened.save(str(temp), garbage=4, deflate=True)
            os.replace(temp, target)
            _set_private_mode(target)
        finally:
            flattened.close()
            document.close()
    except HiddenDataError:
        raise
    except Exception as exc:
        raise HiddenDataError(f"Необратимая обработка PDF остановлена: {type(exc).__name__}") from exc
    return FlattenReport(str(source), str(target), page_count, dpi)


__all__ = [
    "HIDDEN_DATA_SCHEMA_VERSION", "HIDDEN_DATA_KINDS", "TEXTUAL_HIDDEN_KINDS",
    "DESTRUCTIVE_HIDDEN_KINDS", "HiddenDataError", "HiddenDataReport", "FlattenReport",
    "inspect_hidden_data", "inspection_fingerprint", "sanitize_hidden_data",
    "apply_hidden_data_policy", "flatten_pdf_to_images",
]
