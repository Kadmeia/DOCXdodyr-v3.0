# -*- coding: utf-8 -*-
"""Тесты имитации действий пользователя при выборе папки и проверке отказоустойчивости диалогов."""
from __future__ import annotations

import os
import sys
import tempfile
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx import Document

import main
from backend_api import BackendApi
from folder_pipeline import FolderAnonymizationPipeline, FolderProcessResult


class _SimulatedWebViewWindow:
    """Имитатор окна PyWebView для тестирования действий пользователя в UI."""

    def __init__(self, selected=()):
        self.selected = tuple(str(p) for p in selected)
        self.dialog_calls = []
        self.js_calls = []
        self.events = MagicMock()
        self.events.closing.is_set.return_value = False
        self.events.closed.is_set.return_value = False

    def create_file_dialog(self, *args, **kwargs):
        self.dialog_calls.append((args, kwargs))
        return self.selected

    def evaluate_js(self, script):
        self.js_calls.append(str(script))


class _FolderApiStub:
    """Заглушка бэкенда для изоляции тестов диалогов."""

    def __init__(self):
        self.processed_folders = []
        self._window = None
        self.open_output_folder = False

    def set_window(self, win):
        self._window = win

    def process_folder(self, folder, **kwargs):
        self.processed_folders.append((str(folder), dict(kwargs)))
        return {
            "processed_count": 1,
            "error_count": 0,
            "output_dir": os.path.join(str(folder), "Обезличенные документы"),
        }


def test_user_clicks_select_folder_opens_folder_dialog(monkeypatch, tmp_path):
    """Имитация: пользователь нажимает кнопку «Выбрать папку» и выбирает каталог."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()
    source_dir = tmp_path / "Юридические_Дела"
    source_dir.mkdir()

    window = _SimulatedWebViewWindow([source_dir])
    wrapper.set_window(window)

    threads = []
    class _TrackThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon
        def start(self):
            threads.append(self)

    monkeypatch.setattr(main.threading, "Thread", _TrackThread)

    # Пользователь нажал кнопку
    wrapper.open_folder_dialog("anonymize_folder")

    assert len(window.dialog_calls) >= 1
    assert len(threads) == 1
    threads[0].target()

    assert len(wrapper._api.processed_folders) == 1
    folder_path, opts = wrapper._api.processed_folders[0]
    assert folder_path == str(source_dir)
    assert opts.get("continue_existing") is False
    assert any("setFolderStatus" in call for call in window.js_calls)


def test_user_toggles_continue_checkbox_and_selects_folder(monkeypatch, tmp_path):
    """Имитация: пользователь включил чекбокс продолжения комплекта и нажал выбор папки."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()
    source_dir = tmp_path / "Существующий_Комплект"
    source_dir.mkdir()

    window = _SimulatedWebViewWindow([source_dir])
    wrapper.set_window(window)

    threads = []
    class _TrackThread:
        def __init__(self, *, target, daemon):
            self.target, self.daemon = target, daemon
        def start(self):
            threads.append(self)

    monkeypatch.setattr(main.threading, "Thread", _TrackThread)

    # Пользователь нажал кнопку в режиме continue_folder
    wrapper.open_folder_dialog("continue_folder")

    assert len(threads) == 1
    threads[0].target()

    folder_path, opts = wrapper._api.processed_folders[0]
    assert folder_path == str(source_dir)
    assert opts.get("continue_existing") is True
    assert any("Продолжение комплекта" in call for call in window.js_calls)


def test_user_clicks_folder_dialog_survives_native_exception_and_falls_back(monkeypatch, tmp_path):
    """Имитация: нативный FOLDER_DIALOG в ОС падает с ошибкой, срабатывает fallback OPEN_DIALOG."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()
    source_dir = tmp_path / "Рабочая_Папка"
    source_dir.mkdir()

    window = _SimulatedWebViewWindow()
    # Первый вызов FOLDER_DIALOG падает с ValueError (типично для багов pywebview/COM),
    # второй вызов (fallback OPEN_DIALOG) успешно возвращает папку
    window.create_file_dialog = MagicMock(side_effect=[
        ValueError("COM Exception in VistaDialog: Invalid Filter"),
        [str(source_dir)],
    ])
    wrapper.set_window(window)

    threads = []
    class _TrackThread:
        def __init__(self, *, target, daemon):
            self.target, self.daemon = target, daemon
        def start(self):
            threads.append(self)

    monkeypatch.setattr(main.threading, "Thread", _TrackThread)

    # Пользователь нажимает «Выбрать папку»
    wrapper.open_folder_dialog("anonymize_folder")

    # Должно быть 2 вызова create_file_dialog, без выброса исключения пользователю
    assert window.create_file_dialog.call_count == 2
    assert len(threads) == 1
    threads[0].target()
    assert wrapper._api.processed_folders[0][0] == str(source_dir)


def test_user_clicks_folder_dialog_falls_back_to_tkinter_when_pywebview_completely_fails(monkeypatch, tmp_path):
    """Имитация: все вызовы pywebview падают, срабатывает системный Tkinter fallback."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()
    source_dir = tmp_path / "Папка_Из_Ткинтера"
    source_dir.mkdir()

    window = _SimulatedWebViewWindow()
    # Оба вызова pywebview падают
    window.create_file_dialog = MagicMock(side_effect=[
        RuntimeError("WebView GUI not responding"),
        RuntimeError("Fallback OPEN_DIALOG failed"),
    ])
    wrapper.set_window(window)

    monkeypatch.setattr(main, "_choose_folder_native_fallback", lambda *a, **kw: str(source_dir))

    threads = []
    class _TrackThread:
        def __init__(self, *, target, daemon):
            self.target, self.daemon = target, daemon
        def start(self):
            threads.append(self)

    monkeypatch.setattr(main.threading, "Thread", _TrackThread)

    wrapper.open_folder_dialog("anonymize_folder")

    assert len(threads) == 1
    threads[0].target()
    assert wrapper._api.processed_folders[0][0] == str(source_dir)


def test_user_cancels_folder_dialog_gracefully(monkeypatch):
    """Имитация: пользователь нажал «Отмена» в системном диалоге выбора папки."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()

    window = _SimulatedWebViewWindow()
    window.create_file_dialog = MagicMock(return_value=None)
    wrapper.set_window(window)
    monkeypatch.setattr(main, "_choose_folder_native_fallback", lambda *a, **kw: None)

    # Не должно быть ни исключений, ни запущенных потоков
    wrapper.open_folder_dialog("anonymize_folder")
    assert len(wrapper._api.processed_folders) == 0


def test_user_accidentally_selects_file_inside_folder(monkeypatch, tmp_path):
    """Имитация: пользователь при выборе папки кликнул на файл внутри неё."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()
    source_dir = tmp_path / "Документы_Суда"
    source_dir.mkdir()
    file_inside = source_dir / "иск.docx"
    file_inside.write_text("договор", encoding="utf-8")

    window = _SimulatedWebViewWindow([file_inside])
    wrapper.set_window(window)

    threads = []
    class _TrackThread:
        def __init__(self, *, target, daemon):
            self.target, self.daemon = target, daemon
        def start(self):
            threads.append(self)

    monkeypatch.setattr(main.threading, "Thread", _TrackThread)

    # Передаем файл
    wrapper.open_folder_dialog("anonymize_folder")

    assert len(threads) == 1
    threads[0].target()

    # Система должна автоматически взять родительскую папку
    assert wrapper._api.processed_folders[0][0] == str(source_dir)


def test_user_selects_folder_with_no_supported_files_shows_informative_message(tmp_path):
    """Имитация: пользователь выбрал пустую папку или папку с неподдерживаемыми файлами."""
    empty_dir = tmp_path / "Пустая_Папка"
    empty_dir.mkdir()
    (empty_dir / "заметка.txt").write_text("тест", encoding="utf-8")

    window = _SimulatedWebViewWindow([empty_dir])
    
    class _MockPipelineApi:
        def __init__(self):
            self._window = window
            self.settings = {}

        def process_folder(self, folder, **kwargs):
            return FolderProcessResult(
                source_dir=Path(folder),
                output_dir=Path(folder) / "Обезличенные документы",
                files=[],
                error_count=1,
                errors=[{"file": str(folder), "error": "В папке нет поддерживаемых документов"}],
            )

    api = _MockPipelineApi()
    wrapper = main.ApiWrapper(api=api)
    wrapper._window = window

    threads = []
    class _TrackThread:
        def __init__(self, *, target, daemon):
            self.target, self.daemon = target, daemon
        def start(self):
            threads.append(self)

    with patch("main.threading.Thread", _TrackThread):
        wrapper.folder_selected("anonymize_folder", str(empty_dir))
        assert len(threads) == 1
        threads[0].target()

    # В js_calls должно быть понятное сообщение, а не просто "ошибок 1"
    status_calls = [c for c in window.js_calls if "setFolderStatus" in c]
    assert len(status_calls) >= 2
    last_status = status_calls[-1]
    assert "В папке нет поддерживаемых документов" in last_status
    assert "error" in last_status


def test_user_selects_readonly_folder_redirects_to_downloads(tmp_path, monkeypatch):
    """Имитация: пользователь выбрал папку на защищенном носителе или DMG (read-only)."""
    ro_dir = tmp_path / "ReadOnlyVolume"
    ro_dir.mkdir()
    sample_doc = ro_dir / "sample.docx"
    d = Document()
    d.add_paragraph("Тестовый документ Иванова Ивана Ивановича")
    d.save(str(sample_doc))

    fake_home = tmp_path / "UserHome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    api = BackendApi.__new__(BackendApi)
    api.user_exclusions = set()
    api.custom_replacements = set()
    api.emit_audit_sidecars = False
    api.open_output_folder = False
    api.process_single_file = MagicMock(return_value=1)
    api.is_cancelled = MagicMock(return_value=False)

    pipeline = FolderAnonymizationPipeline(api, output_dir_name="Обезличенные документы")

    # Имитируем ошибку записи в исходный каталог ro_dir
    original_mkdir = Path.mkdir
    def mock_mkdir(self, *args, **kwargs):
        if str(ro_dir) in str(self) and "Downloads" not in str(self):
            raise PermissionError("[Errno 30] Read-only file system")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    result = pipeline.process(ro_dir)

    # Пайплайн не должен падать с PermissionError!
    assert result.error_count == 0
    # Результат должен быть перенаправлен в Downloads
    assert "Downloads" in str(result.output_dir)
    assert "DOCXdodyr_Output" in str(result.output_dir)


def test_user_drags_and_drops_folder_into_dropzone(monkeypatch, tmp_path):
    """Имитация: пользователь перетащил папку мышью в область folder-dropzone."""
    monkeypatch.setattr(main, "BackendApi", _FolderApiStub)
    wrapper = main.ApiWrapper()
    folder = tmp_path / "Дропнутая_Папка"
    folder.mkdir()

    window = _SimulatedWebViewWindow()
    wrapper.set_window(window)

    wrapper.folder_selected = MagicMock()
    wrapper.files_dropped("anonymize_folder", [str(folder)])

    wrapper.folder_selected.assert_called_once_with("anonymize_folder", str(folder))


def test_frontend_open_folder_dialog_error_boundary():
    """Проверка скрипта фронтенда: openFolderDialog содержит обработчик ошибок."""
    web_dir = Path(__file__).resolve().parent.parent / "web"
    script_js = (web_dir / "script.js").read_text(encoding="utf-8")

    assert "function openFolderDialog" in script_js
    assert "res.catch" in script_js
    assert "setFolderStatus" in script_js
