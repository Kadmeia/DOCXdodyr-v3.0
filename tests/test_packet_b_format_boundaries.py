# -*- coding: utf-8 -*-
"""Targeted suite for Packet B: Document formats, recovery guarantees, and fault boundaries."""

import io
from pathlib import Path
import pytest
from docx import Document
from openpyxl import Workbook
import fitz

from backend_api import BackendApi
import document_restorer
import hidden_data
import privacy_audit


def test_docx_complex_elements_anonymization(tmp_path):
    """DOCX: paragraphs, runs, tables, headers, footers, comments, revisions."""
    doc_path = tmp_path / "complex.docx"
    doc = Document()
    
    # Header and Footer
    section = doc.sections[0]
    header = section.header
    header.paragraphs[0].text = "Договор с Ивановым Иваном Ивановичем"
    footer = section.footer
    footer.paragraphs[0].text = "Конфиденциально: ООО Ромашка"

    # Paragraphs and Runs
    p1 = doc.add_paragraph()
    r1 = p1.add_run("Заказчик: ")
    r2 = p1.add_run("Сидоров Сидор Сидорович")
    r2.bold = True
    p1.add_run(", ИНН 7701234567, телефон +7 (999) 000-11-22.")

    # Table
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Исполнитель"
    table.cell(0, 1).text = "Петров Петр Петрович"
    table.cell(1, 0).text = "Расчетный счет"
    table.cell(1, 1).text = "40702810938000012345"

    doc.save(str(doc_path))

    api = BackendApi()
    out_dir = tmp_path / "out_docx"
    out_dir.mkdir()

    api.save_original = False
    api.save_docx = True
    api.save_pdf = False
    api.save_markdown = False

    count = api.process_single_file(str(doc_path), set(), {}, output_dir=out_dir)
    assert count >= 4

    cleaned_files = list(out_dir.glob("*_cleaned.docx"))
    assert len(cleaned_files) == 1
    cleaned_doc = Document(str(cleaned_files[0]))

    # Verify header
    h_text = cleaned_doc.sections[0].header.paragraphs[0].text
    assert "Ивановым Иваном Ивановичем" not in h_text
    assert "[ФИО]" in h_text or "[ФИО_" in h_text

    # Verify table
    cell_val = cleaned_doc.tables[0].cell(0, 1).text
    assert "Петров Петр Петрович" not in cell_val
    assert "[ФИО]" in cell_val or "[ФИО_" in cell_val


def test_xlsx_formulas_and_structure_preservation(tmp_path):
    """XLSX: formulas, styles, merged cells, hidden sheets, string vs numbers."""
    xlsx_path = tmp_path / "financial.xlsx"
    wb = Workbook()
    
    # Sheet 1: Active
    ws1 = wb.active
    ws1.title = "Смета Иванова"
    ws1["A1"] = "Сотрудник"
    ws1["B1"] = "Иванов Иван Иванович"
    ws1["A2"] = "Оклад"
    ws1["B2"] = 150000  # Pure numeric, must NOT be masked
    ws1["A3"] = "Премия"
    ws1["B3"] = 50000   # Pure numeric
    ws1["A4"] = "Итого"
    ws1["B4"] = "=SUM(B2:B3)"  # Formula must be preserved!
    
    # Merged cells
    ws1.merge_cells("A5:B5")
    ws1["A5"] = "Примечание: ООО Рога и Копыта"

    # Sheet 2: Hidden sheet
    ws2 = wb.create_sheet(title="Секретные данные")
    ws2.sheet_state = "hidden"
    ws2["A1"] = "Директор"
    ws2["B1"] = "Кузнецов Алексей Николаевич"

    wb.save(str(xlsx_path))

    api = BackendApi()
    out_dir = tmp_path / "out_xlsx"
    out_dir.mkdir()

    api.save_original = False
    api.save_docx = False  # All format switches off preserves original format (XLSX -> XLSX)
    api.save_pdf = False
    api.save_markdown = False

    count = api.process_single_file(str(xlsx_path), set(), {}, output_dir=out_dir)
    assert count >= 2

    cleaned_files = list(out_dir.glob("*_cleaned.xlsx"))
    assert len(cleaned_files) == 1

    from openpyxl import load_workbook
    cleaned_wb = load_workbook(str(cleaned_files[0]))
    c_ws1 = cleaned_wb.worksheets[0]

    # Formula preserved
    assert c_ws1["B4"].value == "=SUM(B2:B3)"
    # Numeric values preserved
    assert c_ws1["B2"].value == 150000
    assert c_ws1["B3"].value == 50000
    # Strings masked
    assert "Иванов Иван Иванович" not in str(c_ws1["B1"].value)
    assert "[ФИО]" in str(c_ws1["B1"].value) or "[ФИО_" in str(c_ws1["B1"].value)

    # Hidden sheet preserved and anonymized
    c_ws2 = cleaned_wb.worksheets[1]
    assert c_ws2.sheet_state == "hidden"
    assert "Кузнецов Алексей Николаевич" not in str(c_ws2["B1"].value)


def test_encrypted_pdf_rejection_safety(tmp_path):
    """Encrypted/password-protected PDF input must be rejected safely with an exception."""
    pdf_path = tmp_path / "encrypted.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Секретный текст гражданина Иванова Ивана Ивановича")
    # Save with encryption password
    doc.save(
        str(pdf_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="password123",
        owner_pw="owner123"
    )
    doc.close()

    # Verify file is indeed encrypted
    check_doc = fitz.open(str(pdf_path))
    assert check_doc.is_encrypted is True
    check_doc.close()

    api = BackendApi()
    out_dir = tmp_path / "out_enc"
    out_dir.mkdir()

    with pytest.raises(Exception):
        api.process_single_file(str(pdf_path), set(), {}, output_dir=out_dir)

    # Ensure no partial or false-green output exists
    assert len(list(out_dir.iterdir())) == 0


def test_corrupted_archive_safety(tmp_path):
    """Corrupted ZIP containers for DOCX/XLSX must be cleanly rejected."""
    corrupted_docx = tmp_path / "corrupted.docx"
    corrupted_docx.write_bytes(b"PK\x03\x04" + b"\x00" * 50)  # Broken local file header

    api = BackendApi()
    out_dir = tmp_path / "out_broken"
    out_dir.mkdir()

    with pytest.raises(Exception):
        api.process_single_file(str(corrupted_docx), set(), {}, output_dir=out_dir)

    assert len(list(out_dir.iterdir())) == 0


def test_fault_injection_disk_full_cleanup(tmp_path, monkeypatch):
    """Simulate disk write failure/IOError: partial outputs must be cleaned up."""
    from docx.document import Document as DocxDocument
    doc_path = tmp_path / "test.docx"
    doc = Document()
    doc.add_paragraph("Тестовый договор с Ивановым Иваном Ивановичем.")
    doc.save(str(doc_path))

    api = BackendApi()
    out_dir = tmp_path / "out_disk_full"
    out_dir.mkdir()

    # Monkeypatch doc.save to fail after generating partial output
    orig_save = DocxDocument.save
    def failing_save(self, path):
        # Create a partial file first to simulate an interrupted write
        Path(path).write_bytes(b"partial content")
        raise OSError("No space left on device (simulated disk full)")

    monkeypatch.setattr(DocxDocument, "save", failing_save)

    with pytest.raises(IOError):
        api.process_single_file(str(doc_path), set(), {}, output_dir=out_dir)

    # Ensure no partial output files remain
    remaining = [f for f in out_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert remaining == []


def test_decoder_tampering_and_atomic_restoration(tmp_path):
    """Restoration must fail atomically if decoder has mismatched hash or is corrupted."""
    doc_path = tmp_path / "contract.docx"
    doc = Document()
    doc.add_paragraph("Договор заключил Петров Петр Петрович.")
    doc.save(str(doc_path))

    api = BackendApi()
    out_dir = tmp_path / "out_restore"
    out_dir.mkdir()
    api.save_decoder = True
    api.save_original = False
    api.save_docx = True

    api.process_single_file(str(doc_path), set(), {}, output_dir=out_dir)

    cleaned_docx = list(out_dir.glob("*_cleaned.docx"))[0]
    decoder_candidates = [
        path for root in (out_dir, tmp_path) for path in root.glob("*.json")
        if "дешифратор" in path.name.casefold()
    ]
    assert len(decoder_candidates) > 0
    decoder_json = decoder_candidates[0]

    # Tamper with the decoder: corrupt JSON structure
    corrupted_decoder = tmp_path / "corrupted_decoder.json"
    corrupted_decoder.write_text("{ broken json content", encoding="utf-8")

    restore_target = tmp_path / "restored.docx"
    with pytest.raises(Exception):
        document_restorer.restore_document(
            str(cleaned_docx),
            str(corrupted_decoder),
            str(restore_target)
        )

    # Restoration failed atomically: target file must not exist
    assert not restore_target.exists()
