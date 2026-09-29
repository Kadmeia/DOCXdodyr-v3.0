# -*- coding: utf-8 -*-
"""Извлечение сопоставительных блоков (абзацы / ячейки) до и после обезличивания (Раунд 3)."""

import json
from pathlib import Path
import docx
import openpyxl
import fitz

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "tests" / "fresh_corpus_stage09_round3"
CLEANED_DIR = SOURCE_DIR / "Обезличенные документы"
OUTPUT_JSON = SOURCE_DIR / "extracted_comparison_blocks.json"

blocks = []

# 1. DOCX
for docx_path in sorted(SOURCE_DIR.glob("*.docx")):
    cleaned_path = CLEANED_DIR / f"{docx_path.stem}_cleaned.docx"
    if not cleaned_path.exists():
        continue
    orig_doc = docx.Document(str(docx_path))
    clean_doc = docx.Document(str(cleaned_path))

    for idx, (p_orig, p_clean) in enumerate(zip(orig_doc.paragraphs, clean_doc.paragraphs), 1):
        t_orig = p_orig.text.strip()
        t_clean = p_clean.text.strip()
        if not t_orig:
            continue
        blocks.append({
            "file": docx_path.name,
            "type": "Абзац DOCX",
            "index": idx,
            "original": t_orig,
            "docxdodyr": t_clean
        })

# 2. XLSX
for xlsx_path in sorted(SOURCE_DIR.glob("*.xlsx")):
    cleaned_path = CLEANED_DIR / f"{xlsx_path.stem}_cleaned.xlsx"
    if not cleaned_path.exists():
        continue
    orig_wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    clean_wb = openpyxl.load_workbook(str(cleaned_path), data_only=True)

    orig_ws = orig_wb.active
    clean_ws = clean_wb.active

    for row_idx in range(1, orig_ws.max_row + 1):
        for col_idx in range(1, orig_ws.max_column + 1):
            val_orig = orig_ws.cell(row=row_idx, column=col_idx).value
            val_clean = clean_ws.cell(row=row_idx, column=col_idx).value
            if val_orig is None:
                continue
            t_orig = str(val_orig).strip()
            t_clean = str(val_clean).strip() if val_clean is not None else ""
            if not t_orig:
                continue
            blocks.append({
                "file": xlsx_path.name,
                "type": f"Ячейка R{row_idx}C{col_idx}",
                "index": f"R{row_idx}C{col_idx}",
                "original": t_orig,
                "docxdodyr": t_clean
            })

# 3. PDF
for pdf_path in sorted(SOURCE_DIR.glob("*.pdf")):
    cleaned_pdf_path = CLEANED_DIR / f"{pdf_path.stem}_cleaned.pdf"
    cleaned_docx_path = CLEANED_DIR / f"{pdf_path.stem}_cleaned.docx"

    # Извлекаем текст абзацев из оригинала через PyMuPDF
    orig_doc = fitz.open(str(pdf_path))
    orig_text = ""
    for page in orig_doc:
        orig_text += page.get_text() + "\n"
    orig_paras = [p.strip() for p in orig_text.split("\n") if p.strip()]

    # Извлекаем текст из очищенного docx или pdf
    clean_paras = []
    if cleaned_docx_path.exists():
        cdoc = docx.Document(str(cleaned_docx_path))
        clean_paras = [p.text.strip() for p in cdoc.paragraphs if p.text.strip()]
    elif cleaned_pdf_path.exists():
        cdoc = fitz.open(str(cleaned_pdf_path))
        ctext = ""
        for page in cdoc:
            ctext += page.get_text() + "\n"
        clean_paras = [p.strip() for p in ctext.split("\n") if p.strip()]

    for idx, (p_orig, p_clean) in enumerate(zip(orig_paras, clean_paras), 1):
        blocks.append({
            "file": pdf_path.name,
            "type": "Абзац PDF",
            "index": idx,
            "original": p_orig,
            "docxdodyr": p_clean
        })

print(f"Всего сопоставлено {len(blocks)} блоков раунда 3.")
with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(blocks, f, ensure_ascii=False, indent=2)
print(f"Результат сохранен: {OUTPUT_JSON}")
