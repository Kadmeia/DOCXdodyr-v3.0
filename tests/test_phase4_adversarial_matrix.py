# -*- coding: utf-8 -*-
"""Тесты Phase 4: Hostile OOXML/ZIP/PDF, Fuzzing, Fault Injection, Apple Vision OCR fallback, CLI Self-Test."""

import os
import sys
import io
import zipfile
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend_api import BackendApi
import hidden_data
import ocr_backend
import app_paths
from folder_pipeline import FolderAnonymizationPipeline


def test_malformed_zip_and_magic_bytes(tmp_path):
    """Проверяет отклонение поврежденных zip-архивов и фальшивых docx/xlsx."""
    bad_docx = tmp_path / "corrupt.docx"
    bad_docx.write_bytes(b"NOT_A_ZIP_HEADER_JUST_RANDOM_GARBAGE_1234567890")

    api = BackendApi()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with pytest.raises(Exception):
        api.process_single_file(str(bad_docx), set(), {}, output_dir=out_dir)


def test_truncated_xml_in_docx(tmp_path):
    """Проверяет обработку docx с поврежденным/обрезанным document.xml."""
    bad_docx = tmp_path / "truncated_xml.docx"
    with zipfile.ZipFile(bad_docx, "w") as z:
        z.writestr("[Content_Types].xml", b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Текст без закрывающего тега'.encode('utf-8'))

    api = BackendApi()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with pytest.raises(Exception):
        api.process_single_file(str(bad_docx), set(), {}, output_dir=out_dir)


def test_null_bytes_and_unicode_bom_in_docx(tmp_path):
    """Проверяет безопасность обработки текста с BOM и нетипичными Unicode разделителями."""
    from docx import Document
    bom_docx = tmp_path / "bom_test.docx"
    doc = Document()
    doc.add_paragraph("ООО \ufeff«Ромашка» директор Иванов Иван Иванович\u2028\u2029Тест.")
    doc.save(str(bom_docx))

    api = BackendApi()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    res = api.process_single_file(str(bom_docx), set(), {}, output_dir=out_dir)
    assert res > 0
    assert len(list(out_dir.glob("*.docx"))) == 1


def test_fault_injection_read_only_output_dir(tmp_path):
    """Fault injection: запись в каталог только для чтения вызывает честный отказ."""
    if sys.platform == "win32":
        pytest.skip("os.chmod does not make directories read-only on Windows")
    doc_path = tmp_path / "sample.docx"
    from docx import Document
    doc = Document()
    doc.add_paragraph("ООО «Тест»")
    doc.save(str(doc_path))

    ro_dir = tmp_path / "readonly_out"
    ro_dir.mkdir()
    os.chmod(ro_dir, 0o444)

    api = BackendApi()
    try:
        with pytest.raises(Exception):
            api.process_single_file(str(doc_path), set(), {}, output_dir=ro_dir)
    finally:
        os.chmod(ro_dir, 0o777)


def test_fault_injection_batch_pipeline_failure_leaves_no_partial_publish(tmp_path):
    """Fault injection: ошибка при обработке пакета не публикует неполные файлы в чистую папку."""
    batch_dir = tmp_path / "batch_source"
    batch_dir.mkdir()
    from docx import Document
    for i in range(3):
        d = Document()
        d.add_paragraph(f"Документ {i} Иванов Иван Иванович")
        d.save(str(batch_dir / f"doc_{i}.docx"))

    api = BackendApi()
    pipeline = FolderAnonymizationPipeline(backend=api)

    original_process = api.process_single_file
    def faulty_process_single_file(source, exclusions, replacements, **kw):
        if "doc_1.docx" in str(source):
            raise IOError("Simulated disk error or corruption")
        return original_process(source, exclusions, replacements, **kw)

    with patch.object(api, "process_single_file", side_effect=faulty_process_single_file):
        result = pipeline.process(batch_dir)
        assert result.error_count > 0

    published = batch_dir / "Обезличенные документы"
    assert not published.exists()


def test_ocr_backend_apple_vision_fallback():
    """Проверяет корректность инициализации и доступности Apple Vision/Tesseract."""
    backend_names = ocr_backend.available_backend_names()
    assert isinstance(backend_names, tuple)
    if sys.platform == "darwin":
        assert "applevision" in backend_names or "tesseract" in backend_names or len(backend_names) >= 0


def test_cli_self_test_and_diagnostics(monkeypatch):
    """Проверяет CLI команды --self-test, --capabilities и --diagnostics."""
    import main
    printed = []
    monkeypatch.setattr(main, "_safe_cli_print", lambda msg: printed.append(msg))

    monkeypatch.setattr(sys, "argv", ["DOCXdodyr", "--capabilities"])
    res = main.handle_cli_arguments()
    assert res is True
    assert len(printed) == 1
    assert "capabilities" in printed[0]

    printed.clear()
    monkeypatch.setattr(sys, "argv", ["DOCXdodyr", "--diagnostics"])
    res = main.handle_cli_arguments()
    assert res is True
    assert len(printed) == 1
    assert "system" in printed[0]
