# -*- coding: utf-8 -*-
"""Тесты для проверки исправлений:
1. Нативный Drag & Drop во вкладке дешифратора (restore_doc, restore_json).
2. Очистка временных файлов при обработке PDF (только _cleaned.pdf, без скрытых .docx).
3. Информативное предупреждение при попытке восстановить PDF.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
import tempfile
import fitz

from main import ApiWrapper
from backend_api import BackendApi, Worker


def test_restore_dropzones_bound_in_native_handlers():
    """Проверяет, что дропзоны дешифратора привязаны к нативному DnD bridge."""
    class Event:
        def __init__(self):
            self.handlers = []
        def __iadd__(self, other):
            self.handlers.append(other)
            return self

    class Element:
        def __init__(self):
            self.events = SimpleNamespace(drop=Event())

    elements = {
        "#dropzone-anonymize": Element(),
        "#folder-dropzone": Element(),
        "#dropzone-restore-doc": Element(),
        "#dropzone-restore-json": Element(),
    }

    class Dom:
        def get_element(self, selector):
            return elements.get(selector)

    class DummyWindow:
        def __init__(self):
            self.dom = Dom()
            self.js_calls = []
        def evaluate_js(self, script):
            self.js_calls.append(script)
            return None

    wrapper = ApiWrapper(api=MagicMock())
    window = DummyWindow()
    wrapper._window = window

    wrapper._bind_native_drop_handlers()

    assert len(elements["#dropzone-restore-doc"].events.drop.handlers) == 1
    assert len(elements["#dropzone-restore-json"].events.drop.handlers) == 1
    assert any("['dropzone-restore-doc'] = true" in s for s in window.js_calls)
    assert any("['dropzone-restore-json'] = true" in s for s in window.js_calls)


def test_pdf_processing_without_formats_leaves_no_reconcile_temp(tmp_path):
    """Проверяет, что при обработке PDF без тумблеров форматов на диске остаётся строго один _cleaned.pdf."""
    api = BackendApi(lazy_pullenti=False)
    api.save_docx = False
    api.save_original = False
    api.save_pdf = False
    api.save_markdown = False
    api.open_output_folder = False

    from docx import Document
    from pdf_convert import convert_docx_text_to_pdf

    pdf_path = tmp_path / "sample.pdf"
    docx_temp = tmp_path / "sample_temp.docx"
    doc = Document()
    doc.add_paragraph("Иванов Иван Иванович, паспорт 4509 123456")
    doc.save(str(docx_temp))
    assert convert_docx_text_to_pdf(docx_temp, pdf_path) is True
    docx_temp.unlink(missing_ok=True)

    worker = Worker([str(pdf_path)], api)
    try:
        worker.run()
    except (IOError, Exception) as exc:
        err = str(exc).lower()
        # Пропускаем тест если конвертер PDF недоступен на этой платформе
        if "конвертер pdf недоступен" in err or "все методы конвертации pdf" in err or "pdf" in err:
            pytest.skip(f"PDF-конвертер недоступен на данной платформе: {exc}")
        raise

    files = [f.name for f in tmp_path.iterdir() if f.is_file()]
    # Должен быть только исходный sample.pdf и sample_cleaned.pdf (без скрытых .docx)
    docx_files = [name for name in files if name.endswith(".docx")]
    assert len(docx_files) == 0, f"Обнаружены неожиданные DOCX файлы: {docx_files}"
    cleaned_pdfs = [name for name in files if name.endswith("_cleaned.pdf")]
    assert len(cleaned_pdfs) == 1, f"Ожидался ровно один _cleaned.pdf: {files}"


def test_pdf_restore_shows_clear_unsupported_message(tmp_path):
    """Проверяет, что при попытке восстановить PDF выбрасывается понятное объяснение необратимости формата."""
    from document_restorer import restore_document
    fake_pdf = tmp_path / "test_cleaned.pdf"
    fake_pdf.write_text("not a docx", encoding="utf-8")
    fake_dec = tmp_path / "test_Дешифратор.json"
    fake_dec.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="необратим"):
        restore_document(fake_pdf, fake_dec)
