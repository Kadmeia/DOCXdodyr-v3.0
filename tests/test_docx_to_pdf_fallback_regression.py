# -*- coding: utf-8 -*-
from pathlib import Path
from docx import Document
from backend_api import BackendApi
import pdf_convert


def test_docx_pdf_conversion_uses_fitz_fallback_when_office_unavailable(tmp_path: Path, monkeypatch):
    """Regression test: verify DOCX can be exported to PDF via fitz when Word/LibreOffice are absent."""
    monkeypatch.setattr(pdf_convert, "check_libreoffice", lambda: None)
    monkeypatch.setattr(pdf_convert, "DOCX2PDF_AVAILABLE", False)
    monkeypatch.setattr(pdf_convert, "LIBREOFFICE_AVAILABLE", False)
    monkeypatch.setattr(pdf_convert, "PDF_CONVERSION_AVAILABLE", False)

    doc_path = tmp_path / "test_doc.docx"
    doc = Document()
    doc.add_paragraph("Тестовый документ для проверки экспорта в PDF без Office.")
    doc.save(str(doc_path))

    api = BackendApi()
    api.save_docx = True
    api.save_pdf = True
    api.save_decoder = False
    api.init_pullenti()

    count = api.process_single_file(
        str(doc_path),
        exclusions_list=[],
        custom_replacements_list=[],
        output_dir=tmp_path / "output",
    )

    out_docx = tmp_path / "output" / "test_doc_cleaned.docx"
    out_pdf = tmp_path / "output" / "test_doc_cleaned.pdf"

    assert out_docx.is_file(), "Очищенный DOCX должен существовать"
    assert out_pdf.is_file(), "Очищенный PDF должен существовать"
    assert out_pdf.stat().st_size > 0, "PDF не должен быть пустым"
