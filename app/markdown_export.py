# -*- coding: utf-8 -*-
"""Экспорт обезличенных документов в формат Markdown (.md).

Поддерживает преобразование docx.Document, текста и Excel-таблиц
в стандартный GitHub Flavored Markdown с таблицами и списками.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Sequence


def _escape_md_table_cell(cell_text: str) -> str:
    """Экранирует символы вертикальной черты и переносов строк в ячейке таблицы."""
    cleaned = cell_text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    cleaned = cleaned.replace("|", "\\|")
    return re.sub(r"\s+", " ", cleaned).strip()


def docx_to_markdown(doc: Any) -> str:
    """Преобразует python-docx Document в чистый Markdown."""
    lines: list[str] = []

    try:
        from docx.text.paragraph import Paragraph
        from docx.table import Table
    except ImportError:
        Paragraph = None
        Table = None

    body_elem = getattr(getattr(doc, "element", None), "body", None)
    if body_elem is None:
        # Фоллбэк: обход через paragraphs
        for p in getattr(doc, "paragraphs", []):
            txt = p.text.strip()
            if txt:
                lines.append(f"{txt}\n")
        return "\n".join(lines).strip() + "\n"

    for child in body_elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            p = Paragraph(child, doc) if Paragraph is not None else None
            text = p.text.strip() if p is not None else "".join(child.itertext()).strip()
            if not text:
                continue

            style_name = getattr(getattr(p, "style", None), "name", "")
            heading_match = re.search(r"heading\s*([1-6])|заголовок\s*([1-6])", style_name, re.I)
            if heading_match:
                level = int(heading_match.group(1) or heading_match.group(2))
                lines.append(f"{'#' * level} {text}\n")
            elif re.match(r"^(?:ДОГОВОР|СОГЛАШЕНИЕ|АКТ|ПРИКАЗ|РЕШЕНИЕ|ОПРЕДЕЛЕНИЕ|ПРЕТЕНЗИЯ|ОТВЕТ НА ПРЕТЕНЗИЮ)\b", text):
                lines.append(f"## {text}\n")
            elif re.match(r"^(?:[0-9]+(?:\.[0-9]+)*\.?|[а-яёa-z]\)|\-|\•|\*)\s+", text):
                lines.append(f"- {text}")
            else:
                lines.append(f"{text}\n")

        elif tag == "tbl":
            table = Table(child, doc) if Table is not None else None
            rows_data: list[list[str]] = []
            if table is not None:
                for row in table.rows:
                    cells = [_escape_md_table_cell(cell.text) for cell in row.cells]
                    if any(cells):
                        rows_data.append(cells)
            else:
                for row in child.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr"):
                    cells = []
                    for tc in row.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc"):
                        cells.append(_escape_md_table_cell("".join(tc.itertext()).strip()))
                    if any(cells):
                        rows_data.append(cells)

            if rows_data:
                max_cols = max(len(r) for r in rows_data)
                if max_cols > 0:
                    norm_rows = [r + [""] * (max_cols - len(r)) for r in rows_data]
                    header = norm_rows[0]
                    lines.append("| " + " | ".join(header) + " |")
                    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
                    for r in norm_rows[1:]:
                        lines.append("| " + " | ".join(r) + " |")
                    lines.append("")

    return "\n".join(lines).strip() + "\n"


def text_to_markdown(text: str) -> str:
    """Преобразует структурированный текст (из OCR / PDF) в Markdown."""
    if not text:
        return ""

    lines: list[str] = []
    blocks = re.split(r"\n\s*\n+", text.strip())

    for block in blocks:
        raw_lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not raw_lines:
            continue

        # Проверяем, не является ли блок псевдо-таблицей (колонки через | или табуляцию)
        if any("|" in ln for ln in raw_lines):
            for ln in raw_lines:
                lines.append(ln)
            lines.append("")
            continue

        combined = " ".join(raw_lines).strip()
        if re.match(r"^(?:ДОГОВОР|СОГЛАШЕНИЕ|АКТ|ПРИКАЗ|РЕШЕНИЕ|ОПРЕДЕЛЕНИЕ|ПРЕТЕНЗИЯ|ОТВЕТ НА ПРЕТЕНЗИЮ)\b", combined):
            lines.append(f"## {combined}\n")
        elif re.match(r"^(?:[0-9]+(?:\.[0-9]+)*\.?|[а-яёa-z]\)|\-|\•|\*)\s+", combined):
            lines.append(f"- {combined}")
        else:
            lines.append(f"{combined}\n")

    return "\n".join(lines).strip() + "\n"


def excel_to_markdown(wb: Any) -> str:
    """Преобразует openpyxl Workbook в Markdown таблицы."""
    lines: list[str] = []
    for ws in getattr(wb, "worksheets", []):
        lines.append(f"## {ws.title}\n")
        rows_data: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() for c in row):
                cells = [_escape_md_table_cell(str(c) if c is not None else "") for c in row]
                rows_data.append(cells)
        if rows_data:
            max_cols = max(len(r) for r in rows_data)
            if max_cols > 0:
                norm_rows = [r + [""] * (max_cols - len(r)) for r in rows_data]
                header = norm_rows[0]
                lines.append("| " + " | ".join(header) + " |")
                lines.append("| " + " | ".join(["---"] * max_cols) + " |")
                for r in norm_rows[1:]:
                    lines.append("| " + " | ".join(r) + " |")
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_as_markdown(source: Any, out_path: str | os.PathLike) -> Path:
    """Сохраняет документ python-docx, openpyxl Workbook или строку в файл Markdown."""
    target_path = Path(out_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(source, "element") and hasattr(source, "paragraphs"):
        content = docx_to_markdown(source)
    elif hasattr(source, "worksheets"):
        content = excel_to_markdown(source)
    elif isinstance(source, str):
        content = text_to_markdown(source)
    else:
        content = str(source)

    target_path.write_text(content, encoding="utf-8")
    return target_path
