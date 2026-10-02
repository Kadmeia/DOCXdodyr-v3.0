# -*- coding: utf-8 -*-
"""Тесты для нативных диалогов macOS, нормализации DnD и обработки ошибок."""
import os
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from main import ApiWrapper
import webview
from webview.util import parse_file_type
import docx
from backend_api import BackendApi, Worker


def test_all_file_dialog_filters_are_valid():
    """Все фильтры файлов в open_file_dialog должны соответствовать regex pywebview без слэшей."""
    dialog_types = ['anonymize_docs', 'restore_doc', 'restore_json', 'hidden_inspect']
    
    wrapper = ApiWrapper()
    mock_window = MagicMock()
    wrapper.set_window(mock_window)

    for dtype in dialog_types:
        wrapper.open_file_dialog(dtype)
        assert mock_window.create_file_dialog.called
        call_kwargs = mock_window.create_file_dialog.call_args[1]
        file_types = call_kwargs.get('file_types')
        assert file_types is not None, f"file_types отсутствует для {dtype}"
        for ft in file_types:
            # parse_file_type не должен вызывать ValueError
            desc, ext = parse_file_type(ft)
            assert desc
            assert ext
        mock_window.reset_mock()


def test_file_dialog_fallback_on_exception():
    """Если нативный диалог с фильтрами вызывает исключение, срабатывает fallback без фильтров."""
    wrapper = ApiWrapper()
    mock_window = MagicMock()
    # Первый вызов падает, второй успешен
    mock_window.create_file_dialog.side_effect = [ValueError("Invalid filter"), ["/path/to/doc.docx"]]
    wrapper.set_window(mock_window)
    wrapper.files_dropped = MagicMock()

    wrapper.open_file_dialog('anonymize_docs')
    assert mock_window.create_file_dialog.call_count == 2
    wrapper.files_dropped.assert_called_once_with('anonymize_docs', ["/path/to/doc.docx"])


def test_native_dnd_nfd_nfc_fallback():
    """Файлы с кириллицей в NFD (APFS) успешно сопоставляются и нормализуются в NFC."""
    filename_nfc = "отчет_май.docx"
    filename_nfd = unicodedata.normalize('NFD', filename_nfc)
    full_path_nfd = f"/Volumes/Data/{filename_nfd}"

    from webview.dom import _dnd_state
    _dnd_state['paths'] = [(filename_nfd, full_path_nfd)]

    # WebKit возвращает имя в NFC, но pywebviewFullPath не был проставлен
    event = {
        'dataTransfer': {
            'files': [
                {'name': filename_nfc}
            ]
        }
    }

    paths = ApiWrapper._paths_from_native_drop(event)
    assert len(paths) == 1
    # Путь должен быть в NFC
    expected_path = unicodedata.normalize('NFC', full_path_nfd)
    assert paths[0] == expected_path
    # Из _dnd_state объект должен быть удален
    assert len(_dnd_state['paths']) == 0


def test_legacy_doc_raises_informative_error(tmp_path):
    """Попытка обработать старый .doc файл возвращает понятную ошибку."""
    doc_path = tmp_path / "old.doc"
    doc_path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") # OLE2 signature

    api = BackendApi.__new__(BackendApi)
    api._pullenti_processor = True

    with pytest.raises(ValueError, match=r"Устаревший бинарный формат \.doc не поддерживается"):
        api.process_single_file(str(doc_path), set(), set())


def test_readonly_source_directory_fallbacks_to_downloads(tmp_path, monkeypatch):
    """Если исходная папка недоступна для записи, сохранение перенаправляется в Downloads."""
    doc_path = tmp_path / "test.docx"
    d = docx.Document()
    d.add_paragraph("Тест")
    d.save(str(doc_path))

    fake_downloads = tmp_path / "FakeDownloads"
    monkeypatch.setattr(Path, "home", lambda: fake_downloads)

    api = BackendApi.__new__(BackendApi)
    api._pullenti_processor = True
    api.save_docx = True
    api.save_pdf = False
    api.save_markdown = False
    api.save_decoder = False
    api.clean_document = lambda *args, **kwargs: (0, [])

    # Мокаем проверку записи: tmp_path недоступен для записи
    original_mkdir = Path.mkdir
    def mock_mkdir(self, *args, **kwargs):
        if str(tmp_path) in str(self) and "FakeDownloads" not in str(self):
            raise PermissionError("[Errno 30] Read-only file system")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    api.process_single_file(str(doc_path), set(), set())
    assert (fake_downloads / "Downloads" / "DOCXdodyr_Output").exists()
    assert (fake_downloads / "Downloads" / "DOCXdodyr_Output" / "test_cleaned.docx").exists()


def test_worker_run_alerts_safe_error_message(tmp_path):
    """Worker.run передает текст ошибки safe_err в ui_alert."""
    doc_path = tmp_path / "fail.docx"
    doc_path.write_text("corrupted", encoding="utf-8")

    alerts = []
    class MockCleaner:
        _window = object()
        user_exclusions = set()
        custom_replacements = set()
        batch_id = None
        enable_crash_recovery = False
        save_decoder = False
        emit_audit_sidecars = False
        open_output_folder = False

        def update_progress(self, *args, **kwargs):
            pass

        def process_single_file(self, *args, **kwargs):
            raise ValueError("Специальная ошибка парсинга")

        def is_cancelled(self):
            return False

        def finish_operation(self, _status):
            pass

        def on_worker_finished(self, *args):
            pass

    cleaner = MockCleaner()
    with patch("ui_bridge.ui_alert", side_effect=lambda win, msg: alerts.append(msg)):
        worker = Worker([str(doc_path)], cleaner)
        res = worker.run()
        assert res["status"] == "failed"
        assert len(alerts) == 1
        assert "Специальная ошибка парсинга" in alerts[0]
