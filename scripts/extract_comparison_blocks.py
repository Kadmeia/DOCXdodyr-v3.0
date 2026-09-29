# -*- coding: utf-8 -*-
"""Извлекает сопоставление исходных и обезличенных блоков текста для 9 тестовых документов."""

import json
import os
from pathlib import Path
import docx
import openpyxl
import fitz

BASE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fresh_corpus_stage09"
OUT_DIR = BASE_DIR / "Обезличенные документы"


def extract_docx_blocks(orig_path: Path, cleaned_path: Path):
    blocks = []
    d_orig = docx.Document(str(orig_path))
    d_clean = docx.Document(str(cleaned_path))

    # Сравнение по параграфам
    min_len = min(len(d_orig.paragraphs), len(d_clean.paragraphs))
    for idx in range(min_len):
        t_orig = d_orig.paragraphs[idx].text.strip()
        t_clean = d_clean.paragraphs[idx].text.strip()
        if t_orig:
            blocks.append({
                "file": orig_path.name,
                "type": "Абзац DOCX",
                "index": idx + 1,
                "original": t_orig,
                "docxdodyr": t_clean
            })
    return blocks


def extract_xlsx_blocks(orig_path: Path, cleaned_path: Path):
    blocks = []
    wb_orig = openpyxl.load_workbook(str(orig_path), data_only=True)
    wb_clean = openpyxl.load_workbook(str(cleaned_path), data_only=True)

    for sheet_name in wb_orig.sheetnames:
        if sheet_name not in wb_clean.sheetnames:
            continue
        ws_orig = wb_orig[sheet_name]
        ws_clean = wb_clean[sheet_name]

        for row_idx, row in enumerate(ws_orig.iter_rows(values_only=False), start=1):
            for col_idx, cell_orig in enumerate(row, start=1):
                val_orig = cell_orig.value
                val_clean = ws_clean.cell(row=row_idx, column=col_idx).value

                t_orig = str(val_orig).strip() if val_orig is not None else ""
                t_clean = str(val_clean).strip() if val_clean is not None else ""

                if t_orig and row_idx > 1:  # пропускаем шапку таблицы если она без ПДн
                    blocks.append({
                        "file": orig_path.name,
                        "type": f"Ячейка XLSX ({sheet_name}!R{row_idx}C{col_idx})",
                        "index": f"R{row_idx}C{col_idx}",
                        "original": t_orig,
                        "docxdodyr": t_clean
                    })
    return blocks


def extract_pdf_blocks(orig_path: Path, cleaned_doc_path: Path):
    blocks = []
    # Для PDF читаем текст из оригинального PDF и из сгенерированного DOCX/PDF
    doc_orig = fitz.open(str(orig_path))
    text_orig_pages = [page.get_text() for page in doc_orig]
    doc_orig.close()

    if cleaned_doc_path.suffix.lower() == ".docx":
        d_clean = docx.Document(str(cleaned_doc_path))
        clean_paragraphs = [p.text.strip() for p in d_clean.paragraphs if p.text.strip()]
    else:
        doc_clean = fitz.open(str(cleaned_doc_path))
        clean_paragraphs = [line.strip() for page in doc_clean for line in page.get_text().splitlines() if line.strip()]
        doc_clean.close()

    # Разобьём исходный текст PDF на непустые строки/абзацы
    orig_paragraphs = []
    for page_text in text_orig_pages:
        for line in page_text.splitlines():
            s = line.strip()
            if s:
                orig_paragraphs.append(s)

    min_len = min(len(orig_paragraphs), len(clean_paragraphs))
    for idx in range(min_len):
        blocks.append({
            "file": orig_path.name,
            "type": "Абзац PDF",
            "index": idx + 1,
            "original": orig_paragraphs[idx],
            "docxdodyr": clean_paragraphs[idx]
        })
    return blocks


def main():
    all_blocks = []

    # 1. DOCX
    docx_files = [
        "Договор_подряда_на_проектирование.docx",
        "Трудовой_договор_с_руководителем_отдела.docx",
        "Муниципальный_контракт_на_благоустройство.docx"
    ]
    for name in docx_files:
        orig = BASE_DIR / name
        cleaned = OUT_DIR / name.replace(".docx", "_cleaned.docx")
        if orig.exists() and cleaned.exists():
            b = extract_docx_blocks(orig, cleaned)
            all_blocks.extend(b)

    # 2. XLSX
    xlsx_files = [
        "Реестр_выплат_экспертам_и_подрядчикам.xlsx",
        "График_поставок_и_склады_контрагентов.xlsx",
        "Штатные_выплаты_и_уведомления.xlsx"
    ]
    for name in xlsx_files:
        orig = BASE_DIR / name
        cleaned = OUT_DIR / name.replace(".xlsx", "_cleaned.xlsx")
        if orig.exists() and cleaned.exists():
            b = extract_xlsx_blocks(orig, cleaned)
            all_blocks.extend(b)

    # 3. PDF
    pdf_files = [
        "Претензия_по_договору_поставки.pdf",
        "Акт_сдачи_приемки_оказанных_услуг.pdf",
        "Протокол_совещания_комиссии.pdf"
    ]
    for name in pdf_files:
        orig = BASE_DIR / name
        cleaned = OUT_DIR / name.replace(".pdf", "_cleaned.docx")
        if not cleaned.exists():
            cleaned = OUT_DIR / name.replace(".pdf", "_cleaned.pdf")
        if orig.exists() and cleaned.exists():
            b = extract_pdf_blocks(orig, cleaned)
            all_blocks.extend(b)

    output_json = BASE_DIR / "extracted_comparison_blocks.json"
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(all_blocks, f, ensure_ascii=False, indent=2)

    print(f"Всего извлечено {len(all_blocks)} блоков сравнения.")
    print(f"Сохранено в: {output_json}")


if __name__ == "__main__":
    main()
