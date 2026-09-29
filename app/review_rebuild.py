# -*- coding: utf-8 -*-
"""Координатная пересборка документа после ручного отклонения находки.

Модуль получает исходное значение только из приватного in-memory реестра
backend-а. В публичную очередь проверки передаются лишь плейсхолдеры и
обезличенные координаты. Запись всегда идёт во временный файл в той же папке
с последующей атомарной заменой результата.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict
from xml.etree import ElementTree as ET


def replace_nth(text: str, token: str, replacement: str, occurrence: int = 0) -> tuple[str, bool]:
    """Заменяет только указанное вхождение token, не трогая остальные."""

    if not isinstance(text, str) or not token or not isinstance(replacement, str):
        return text, False
    occurrence = max(0, int(occurrence or 0))
    start = 0
    for _ in range(occurrence + 1):
        position = text.find(token, start)
        if position < 0:
            return text, False
        if _ == occurrence:
            return text[:position] + replacement + text[position + len(token):], True
        start = position + len(token)
    return text, False


def _atomic_save(path: Path, save_callback) -> None:
    """Сохраняет файл рядом с оригиналом и заменяет его через os.replace."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.review-", suffix=path.suffix, dir=str(path.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        callback_result = save_callback(temporary)
        if callback_result is False:
            return
        try:
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _docx_target(document, coordinate: Dict[str, Any]):
    scope = coordinate.get("scope", "body")
    kind = coordinate.get("kind")
    if scope == "header_footer":
        section = document.sections[int(coordinate.get("section", 0))]
        block_name = str(coordinate.get("block", coordinate.get("part", "footer"))).lower()
        if block_name not in {"header", "footer"}:
            raise ValueError("Некорректный тип колонтитула")
        block = getattr(section, block_name)
        if kind == "paragraph":
            return block.paragraphs[int(coordinate["index"])]
        if kind == "table_cell":
            table = block.tables[int(coordinate["table"])]
            return table.rows[int(coordinate["row"])].cells[int(coordinate["column"])]
        raise ValueError("Неизвестная координата колонтитула DOCX")
    if scope != "body":
        raise ValueError("Для этой находки координатная пересборка DOCX не поддерживается")
    if kind == "paragraph":
        index = int(coordinate["index"])
        return document.paragraphs[index]
    if kind == "table_cell":
        table = document.tables[int(coordinate["table"])]
        return table.rows[int(coordinate["row"])].cells[int(coordinate["column"])]
    raise ValueError("Неизвестная координата DOCX")


def rebuild_docx(path: Path, coordinate: Dict[str, Any], placeholder: str,
                 original: str, occurrence: int = 0) -> bool:
    """Возвращает конкретную отклонённую замену в paragraph или table cell."""

    from docx import Document

    # Notes/comments are separate package parts and python-docx deliberately
    # does not expose them.  Edit only the selected XML part and preserve all
    # other package members byte-for-byte.
    if coordinate.get("scope") in {"footnotes", "footnote", "endnotes", "endnote", "comments", "comment"}:
        return _rebuild_docx_note(path, coordinate, placeholder, original, occurrence)

    path = Path(path)
    document = Document(str(path))
    target = _docx_target(document, coordinate)
    current = target.text
    updated, changed = replace_nth(current, placeholder, original, occurrence)
    if not changed:
        return False
    # Both Paragraph and _Cell expose clear/add_run only indirectly; assigning
    # cell.text is intentional for table coordinates and preserves topology.
    if coordinate.get("kind") == "paragraph":
        target.clear()
        target.add_run(updated)
    else:
        target.text = updated
    _atomic_save(path, document.save)
    return True


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = "{" + _W_NS + "}"


def _replace_in_text_nodes(root, token: str, replacement: str, occurrence: int = 0) -> bool:
    """Replace one token even when Word split it across multiple ``w:t`` runs."""
    nodes = [node for node in root.iter(_W + "t") if node.text]
    if not nodes or not token:
        return False
    full = "".join(node.text or "" for node in nodes)
    start = 0
    for _ in range(max(0, int(occurrence or 0)) + 1):
        start = full.find(token, start)
        if start < 0:
            return False
        if _ < max(0, int(occurrence or 0)):
            start += len(token)
    end = start + len(token)
    offsets = []
    cursor = 0
    for node in nodes:
        node_start, node_end = cursor, cursor + len(node.text or "")
        offsets.append((node, node_start, node_end))
        cursor = node_end
    touched = [(node, max(start, a), min(end, b), a, b) for node, a, b in offsets if a < end and b > start]
    if not touched:
        return False
    first, *_ = touched[0]
    last = touched[-1][0]
    first_text = first.text or ""
    a0 = touched[0][1] - touched[0][3]
    if first is last:
        first.text = first_text[:a0] + replacement + first_text[touched[0][2] - touched[0][3]:]
        return True
    # The replacement belongs to the first run; preserve the suffix of the
    # final run and clear only the intervening text nodes.
    final_text = last.text or ""
    final_suffix = final_text[touched[-1][2] - touched[-1][3]:]
    first.text = first_text[:a0] + replacement
    for node, *_ in touched[1:-1]:
        node.text = ""
    last.text = final_suffix
    return True


def _rebuild_docx_note(path: Path, coordinate: Dict[str, Any], placeholder: str,
                       original: str, occurrence: int = 0) -> bool:
    path = Path(path)
    if _package_has_signature(path):
        raise ValueError("Подписанный DOCX нельзя изменить без явного снятия подписи")
    scope = str(coordinate.get("scope"))
    scope = {"footnote": "footnotes", "endnote": "endnotes", "comment": "comments"}.get(scope, scope)
    part = {
        "footnotes": "word/footnotes.xml",
        "endnotes": "word/endnotes.xml",
        "comments": "word/comments.xml",
    }[scope]
    wanted_id = coordinate.get("id", coordinate.get("note_id", coordinate.get("comment_id")))
    def mutate(source: Path, target: Path):
        changed = False
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.lower() == part and wanted_id is not None:
                    root = ET.fromstring(data)
                    tag_name = {"comments": "comment", "footnotes": "footnote", "endnotes": "endnote"}[scope]
                    tag = _W + tag_name
                    selected = [node for node in root if node.tag == tag and str(node.get(_W + "id", node.get("id", ""))) == str(wanted_id)]
                    if selected:
                        changed = _replace_in_text_nodes(selected[0], placeholder, original, occurrence)
                        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                elif info.filename.lower() == part:
                    root = ET.fromstring(data)
                    changed = _replace_in_text_nodes(root, placeholder, original, occurrence)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                zout.writestr(info, data)
        return changed
    # First pass determines whether a selected token exists; the callback
    # writes a complete package only once, and _atomic_save handles fsync.
    changed = False
    def save(target):
        nonlocal changed
        changed = mutate(path, target)
        return changed
    _atomic_save(path, save)
    return changed


def _package_has_signature(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as package:
            return any(name.lower().startswith("_xmlsignatures/") for name in package.namelist())
    except (OSError, zipfile.BadZipFile):
        return True


def rebuild_pdf(path: Path, coordinate: Dict[str, Any], placeholder: str,
                original: str, occurrence: int = 0) -> bool:
    """Restore one PDF text span using its page and exact bounding box.

    This is intentionally reversible (redaction + re-insertion), and refuses
    encrypted or signed PDFs.  OCR-only pages without a text layer are not
    accepted because a bbox alone cannot prove that the selected token is the
    target finding.
    """
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Для пересборки PDF требуется PyMuPDF") from exc
    path = Path(path)
    document = fitz.open(str(path))
    try:
        if getattr(document, "needs_pass", False):
            raise ValueError("Зашифрованный PDF нельзя изменить без пароля")
        raw_page = int(coordinate.get("page", coordinate.get("page_index", -1)))
        # OCRDocumentResult uses one-based page numbers.  Callers working
        # directly with PyMuPDF may opt into zero-based coordinates.
        page_number = raw_page if coordinate.get("page_base") == 0 or raw_page == 0 else raw_page - 1
        bbox = coordinate.get("bbox", coordinate.get("rect"))
        if page_number < 0 or page_number >= document.page_count or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return False
        page = document[page_number]
        values = [float(value) for value in bbox]
        normalized = coordinate.get("normalized") is True or coordinate.get("coordinate_space") == "normalized"
        if normalized or (max(values) <= 1.01 and page.rect.width > 2 and page.rect.height > 2):
            values = [values[0] * page.rect.width, values[1] * page.rect.height,
                      values[2] * page.rect.width, values[3] * page.rect.height]
        rect = fitz.Rect(*values)
        if rect.is_empty or not rect.is_valid:
            return False
        extracted = page.get_text("text") or ""
        if placeholder not in extracted:
            return False
        expected_text = str(coordinate.get("text", placeholder))
        if expected_text and expected_text not in extracted:
            return False
        # Require a text span intersecting the supplied bbox.  This prevents a
        # stale coordinate from restoring data into an unrelated page region.
        spans = []
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if fitz.Rect(span.get("bbox", ())).intersects(rect):
                        spans.append(span)
        if not spans or not any(placeholder in str(span.get("text", "")) for span in spans):
            return False
        # PDF signature fields are not safely preservable after a page edit.
        try:
            from pypdf import PdfReader
            fields = (PdfReader(str(path), strict=False).get_fields() or {}).values()
            if any(str(field.get("/FT", "")) == "/Sig" for field in fields):
                raise ValueError("Подписанный PDF нельзя изменить")
        except ImportError:
            pass
        style = spans[0]
        font_size = max(4.0, float(style.get("size", max(6.0, rect.height * 0.8))))
        color_int = int(style.get("color", 0))
        color = ((color_int >> 16 & 255) / 255.0, (color_int >> 8 & 255) / 255.0, (color_int & 255) / 255.0)
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        inserted = page.insert_textbox(rect, original, fontsize=font_size, fontname="helv", color=color, overlay=True)
        if inserted < 0:
            # Long names may not fit the anonymised token's box.  Scale down,
            # but never expand beyond the verified bbox.
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_textbox(rect, original, fontsize=max(4.0, font_size * 0.65), fontname="helv", color=color, overlay=True)
        temporary = path.with_name(f".{path.stem}.review-pdf-{os.getpid()}{path.suffix}")
        document.save(str(temporary), garbage=4, deflate=True)
        document.close()
        document = None
        try:
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
        except OSError:
            pass
        os.replace(temporary, path)
        return True
    finally:
        if document is not None:
            document.close()
        try:
            temporary.unlink(missing_ok=True)
        except (NameError, OSError):
            pass


def rebuild_xlsx(path: Path, coordinate: Dict[str, Any], placeholder: str,
                 original: str, occurrence: int = 0) -> bool:
    """Возвращает конкретную отклонённую замену в ячейке XLSX."""

    from openpyxl import load_workbook

    path = Path(path)
    workbook = load_workbook(str(path))
    sheet_name = str(coordinate.get("sheet", ""))
    cell_coordinate = str(coordinate.get("cell", ""))
    if sheet_name not in workbook.sheetnames or not cell_coordinate:
        return False
    cell = workbook[sheet_name][cell_coordinate]
    if not isinstance(cell.value, str):
        return False
    updated, changed = replace_nth(cell.value, placeholder, original, occurrence)
    if not changed:
        return False
    cell.value = updated
    _atomic_save(path, workbook.save)
    return True


__all__ = ["replace_nth", "rebuild_docx", "rebuild_xlsx", "rebuild_pdf"]
