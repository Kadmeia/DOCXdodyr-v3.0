# -*- coding: utf-8 -*-
"""Regression tests for Phase 1: Security, UnifiedOperationManager, and OCR Isolation."""

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
import pytest

from backend_api import BackendApi, UnifiedOperationManager
import qwen_offline


def test_qwen_delete_model_blocks_path_traversal_and_parent_deletion(tmp_path, monkeypatch):
    """Проверяет строгую изоляцию удаления моделей от директории user_data и traversal-атак."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    user_data = tmp_path / "user_data"
    user_data.mkdir(parents=True, exist_ok=True)
    critical_file = user_data / "settings.json"
    critical_file.write_text("{}", encoding="utf-8")

    import app_paths
    monkeypatch.setattr(app_paths, "get_user_models_dir", lambda: models_dir)
    monkeypatch.setattr(app_paths, "get_user_data_dir", lambda: user_data)

    # 1. Попытка удалить через относительный выход за пределы models_dir
    with pytest.raises(qwen_offline.QwenInstallError, match="Удаление разрешено только внутри каталога моделей"):
        qwen_offline.delete_model(str(models_dir / ".." / "user_data" / "settings.json"))
    assert critical_file.exists()

    # 2. Попытка удалить сам корень models_dir
    with pytest.raises(qwen_offline.QwenInstallError, match="Нельзя удалять сам корневой каталог"):
        qwen_offline.delete_model(str(models_dir))

    # 3. Корректное удаление вложенной папки модели
    valid_model_dir = models_dir / "qwen-test-model"
    valid_model_dir.mkdir(parents=True, exist_ok=True)
    (valid_model_dir / "model.bin").write_text("data", encoding="utf-8")
    assert qwen_offline.delete_model(str(valid_model_dir)) is True
    assert not valid_model_dir.exists()


def test_unified_operation_manager_lifecycle_and_busy_lock():
    """Проверяет переходы состояний и отклонение параллельных операций в UnifiedOperationManager."""
    mgr = UnifiedOperationManager()
    assert mgr.state == "idle"
    assert mgr.is_busy() is False

    # Запуск операции с активным потоком
    long_event = threading.Event()
    long_thread = threading.Thread(target=long_event.wait)
    long_thread.start()
    try:
        assert mgr.start_operation("test_op", thread=long_thread, description="Test") is True
        assert mgr.state == "running"
        assert mgr.is_busy() is True
        assert mgr.current_operation == "test_op"

        # Попытка запустить вторую операцию пока первая активна
        assert mgr.start_operation("second_op") is False
        assert mgr.current_operation == "test_op"
    finally:
        long_event.set()
        long_thread.join(timeout=1.0)

    # Завершение
    mgr.finish_operation("succeeded")
    assert mgr.state == "idle"
    assert mgr.is_busy() is False
    assert mgr.current_operation is None


def test_ocr_worker_atomic_output_and_cancellation(tmp_path, monkeypatch):
    """Проверяет collision-safe временный файл и отсутствие мусорных .tmp файлов при OCR."""
    api = BackendApi.__new__(BackendApi)
    api._window = None
    api.settings = {"ocr_lang": "rus+eng"}
    api.ocr_lang = "rus+eng"
    api._ensure_op_manager()

    pdf_file = tmp_path / "scan.pdf"
    pdf_file.write_text("fake pdf", encoding="utf-8")
    txt_output = tmp_path / "scan.txt"

    monkeypatch.setattr(api, "_ensure_ocr_backend", lambda: True)
    
    def fake_ocr(path, **kwargs):
        assert kwargs.get("ocr_lang") == "rus+eng"
        return "Распознанный текст документа"

    import pdf_convert
    monkeypatch.setattr(pdf_convert, "ocr_pdf_to_text", fake_ocr)

    api.reset_cancellation()
    api._run_pdf_ocr_worker([str(pdf_file)])

    assert txt_output.exists()
    assert txt_output.read_text(encoding="utf-8") == "Распознанный текст документа"
    # Проверяем отсутствие временных .tmp_* файлов
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert len(tmp_files) == 0


def test_folder_pipeline_incomplete_protection_on_error(tmp_path, monkeypatch):
    """Проверяет, что при ошибке обработки папка .docxdodyr-incomplete-* НЕ публикуется как чистая."""
    from docx import Document
    from folder_pipeline import FolderAnonymizationPipeline, DEFAULT_OUTPUT_DIR_NAME

    doc_ok = Document()
    doc_ok.add_paragraph("Тестовый документ")
    doc_ok.save(str(tmp_path / "ok.docx"))

    doc_fail = tmp_path / "broken.docx"
    doc_fail.write_text("not a docx", encoding="utf-8")

    api = BackendApi.__new__(BackendApi)
    api.user_exclusions = set()
    api.custom_replacements = set()
    api.save_original = True
    api.save_docx = True
    api.emit_audit_sidecars = False
    api._ensure_op_manager()

    def fake_process_single(path, *args, **kwargs):
        if "broken.docx" in path:
            raise RuntimeError("Corrupted document")
        return 1

    api.process_single_file = fake_process_single
    api.is_cancelled = lambda: False

    pipeline = FolderAnonymizationPipeline(api)
    result = pipeline.process(tmp_path)

    assert result.error_count > 0
    # Чистая папка "Обезличенные документы" НЕ должна быть создана
    assert not (tmp_path / DEFAULT_OUTPUT_DIR_NAME).exists()

    # Временная папка с ошибками должна сохраниться
    incomplete_dirs = list(tmp_path.glob(".docxdodyr-incomplete-*"))
    assert len(incomplete_dirs) == 1
    error_txt = incomplete_dirs[0] / "_BATCH_ERROR.txt"
    assert error_txt.exists()
    error_text = error_txt.read_text(encoding="utf-8")
    assert "RuntimeError" in error_text
    assert "Corrupted document" not in error_text
    assert str(tmp_path) not in error_text
