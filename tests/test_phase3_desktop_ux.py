# -*- coding: utf-8 -*-
"""Тесты Phase 3: Desktop UX, события запуска, Drag-and-Drop, состояния операций и review window."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from main import ApiWrapper
import ui_bridge


class DummyWindow:
    def __init__(self):
        self.js_calls = []
        self.events = MagicMock()
        self.events.closing.is_set.return_value = False
        self.events.closed.is_set.return_value = False

    def evaluate_js(self, script):
        self.js_calls.append(script)
        return True


def test_api_wrapper_startup_files(tmp_path):
    """Проверяет обработку startup_files: файлы должны быть staged в UI без автостарта."""
    test_doc = tmp_path / "test.docx"
    test_doc.write_text("dummy", encoding="utf-8")

    wrapper = ApiWrapper(startup_files=[str(test_doc)])
    mock_process = MagicMock()
    wrapper._api.process_files = mock_process

    wrapper.init_ui()
    import time
    time.sleep(0.5)

    mock_process.assert_not_called()
    assert wrapper._api.is_busy() is False


def test_api_wrapper_startup_files_with_auto_start(tmp_path):
    """Проверяет запуск quick-mode с auto_start=True для файла."""
    test_doc = tmp_path / "test.docx"
    test_doc.write_text("dummy", encoding="utf-8")

    wrapper = ApiWrapper(startup_files=[str(test_doc)], auto_start=True)
    mock_quick_mode = MagicMock()
    wrapper._run_quick_mode = mock_quick_mode

    wrapper.init_ui()
    import time
    time.sleep(0.6)

    mock_quick_mode.assert_called_once_with([str(test_doc)])


def test_api_wrapper_startup_folder_with_auto_start(tmp_path):
    """Проверяет запуск quick-mode с auto_start=True для папки."""
    test_folder = tmp_path / "test_folder"
    test_folder.mkdir()

    wrapper = ApiWrapper(startup_files=[str(test_folder)], auto_start=True)
    mock_quick_mode = MagicMock()
    wrapper._run_quick_mode = mock_quick_mode

    wrapper.init_ui()
    import time
    time.sleep(0.6)

    mock_quick_mode.assert_called_once_with([str(test_folder)])


def test_api_wrapper_files_dropped():
    """Проверяет диспетчеризацию событий перетаскивания файлов (DnD)."""
    wrapper = ApiWrapper()
    window = DummyWindow()
    wrapper.set_window(window)

    # 1. anonymize_docs
    mock_process = MagicMock()
    wrapper._api.process_files = mock_process
    wrapper.files_dropped("anonymize_docs", ["/path/to/doc1.docx", "/path/to/doc2.pdf"])
    mock_process.assert_called_once_with(["/path/to/doc1.docx", "/path/to/doc2.pdf"])

    # 2. restore_doc
    wrapper.files_dropped("restore_doc", ["/path/to/anonymized.docx"])
    assert wrapper._api.restore_doc_paths == ["/path/to/anonymized.docx"]

    # 3. restore_json
    wrapper.files_dropped("restore_json", ["/path/to/дешифратор.json"])
    assert wrapper._api.restore_json_paths == ["/path/to/дешифратор.json"]

    # 4. hidden_inspect
    mock_inspect = MagicMock(return_value={"status": "ok", "source_name": "test.docx"})
    wrapper._api.inspect_hidden_file = mock_inspect
    wrapper.files_dropped("hidden_inspect", ["/path/to/secret.docx"])
    mock_inspect.assert_called_once_with("/path/to/secret.docx")


def test_api_wrapper_routes_dropped_directory_to_folder_pipeline(tmp_path):
    """Каталог из native DnD не должен попадать в обычный Worker файлов."""
    folder = tmp_path / "Папка целиком"
    folder.mkdir()
    wrapper = ApiWrapper()
    wrapper.folder_selected = MagicMock()
    wrapper._api.process_files = MagicMock()

    wrapper.files_dropped("anonymize_docs", [str(folder)])

    wrapper.folder_selected.assert_called_once_with("anonymize_folder", str(folder))
    wrapper._api.process_files.assert_not_called()


def test_native_folder_drop_uses_full_path_and_continue_toggle(tmp_path):
    """pywebviewFullPath обеспечивает абсолютный путь папки на WKWebView."""
    folder = tmp_path / "Комплект"
    folder.mkdir()
    wrapper = ApiWrapper()
    window = DummyWindow()
    wrapper.set_window(window)
    wrapper.folder_selected = MagicMock()
    event = {
        "dataTransfer": {
            "files": [{"name": folder.name, "pywebviewFullPath": str(folder)}]
        }
    }

    wrapper._handle_native_drop("anonymize_folder", event)

    wrapper.folder_selected.assert_called_once_with("continue_folder", str(folder))


def test_visible_quick_mode_honours_no_open_output():
    from main import configure_visible_quick_mode

    wrapper = ApiWrapper()
    wrapper._api.open_output_folder = True

    configure_visible_quick_mode(wrapper, ["--anonymize", "--no-open-output"])

    assert wrapper._api.open_output_folder is False


def test_quick_anonymize_processes_every_target_and_reports_partial_failure(tmp_path, monkeypatch):
    """Finder Quick Action обрабатывает смешанный набор целиком и не скрывает ошибки."""
    import backend_api
    import time

    folder = tmp_path / "Папка"
    folder.mkdir()
    document = tmp_path / "отдельный.docx"
    document.write_text("dummy", encoding="utf-8")
    progress = []

    class FakeApi:
        open_output_folder = False

        def process_folder(self, path, **kwargs):
            kwargs["progress_callback"](1, 2, "Обработка: первый.docx")
            kwargs["progress_callback"](2, 2, "Обработка: второй.docx")
            return SimpleNamespace(
                processed_count=1,
                error_count=1,
                errors=[{"error": "повреждённый документ"}],
                output_dir=folder / "Обезличенные документы",
            )

        def is_cancelled(self):
            return False

    class FakeWorker:
        def __init__(self, paths, api):
            self.paths = paths
            self.api = api

        def run(self):
            self.api.update_progress(1, 1, f"Обработка: {Path(self.paths[0]).name}")
            return {"processed_count": 1, "error_count": 0, "errors": []}

    wrapper = ApiWrapper(api=FakeApi())
    wrapper._window = DummyWindow()
    wrapper.close_window = MagicMock()
    monkeypatch.setattr(backend_api, "Worker", FakeWorker)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        ui_bridge,
        "ui_set_global_progress",
        lambda _window, _show, _title="", status="", percent=0: progress.append((status, percent)),
    )

    wrapper._execute_quick_anonymize([str(folder), str(document)], "Обезличивание")

    assert any("[1/2]" in status and "первый.docx" in status for status, _ in progress)
    assert any("[2/2]" in status and "отдельный.docx" in status for status, _ in progress)
    assert "Частично готово: успешно 2, ошибок 1" in progress[-1][0]
    assert [percent for _, percent in progress if percent not in (0, 100)] == sorted(
        percent for _, percent in progress if percent not in (0, 100)
    )


def test_quick_restore_pairs_every_document_with_its_decoder(tmp_path, monkeypatch):
    """Finder Quick Action восстанавливает весь выбранный набор, а не первый файл."""
    import document_restorer
    import time

    first = tmp_path / "первый.docx"
    second = tmp_path / "второй.xlsx"
    first.write_text("dummy", encoding="utf-8")
    second.write_text("dummy", encoding="utf-8")
    first_decoder = tmp_path / "первый.json"
    second_decoder = tmp_path / "второй.json"
    first_decoder.write_text("{}", encoding="utf-8")
    second_decoder.write_text("{}", encoding="utf-8")
    decoders = {str(first): str(first_decoder), str(second): str(second_decoder)}

    class FakeApi:
        open_output_folder = False

        def _run_restore(self):
            return {"successes": list(self.restore_doc_paths), "errors": []}

    wrapper = ApiWrapper(api=FakeApi())
    wrapper._window = DummyWindow()
    wrapper.close_window = MagicMock()
    monkeypatch.setattr(document_restorer, "find_decoder_near_document", lambda path: decoders[path])
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    wrapper._execute_quick_restore([str(first), str(second)], "Восстановление")

    assert wrapper._api.restore_doc_paths == [str(first), str(second)]
    assert wrapper._api.restore_json_paths == [str(first_decoder), str(second_decoder)]
    wrapper.close_window.assert_called_once()


def test_quick_restore_keeps_batch_running_when_one_binding_is_invalid(tmp_path, monkeypatch):
    """Ошибка привязки одного документа передаётся per-file restore loop всего набора."""
    import document_restorer

    first = tmp_path / "первый.docx"
    second = tmp_path / "второй.docx"
    first.write_text("dummy", encoding="utf-8")
    second.write_text("dummy", encoding="utf-8")
    wrapper = ApiWrapper(api=MagicMock())
    wrapper._window = DummyWindow()
    wrapper._perform_restore_with_decoders = MagicMock()
    monkeypatch.setattr(
        document_restorer,
        "find_decoder_near_document",
        MagicMock(side_effect=ValueError("Хеш документа не совпадает с дешифратором")),
    )

    wrapper._execute_quick_restore([str(first), str(second)], "Восстановление")

    wrapper._perform_restore_with_decoders.assert_called_once_with(
        [str(first), str(second)], [], "Восстановление"
    )


def test_folder_selected_reports_backend_errors_instead_of_success(tmp_path, monkeypatch):
    """Обычный папочный режим отражает partial failure из backend result."""
    import main

    folder = tmp_path / "Папка"
    output = folder / "Обезличенные документы"
    output.mkdir(parents=True)
    statuses = []

    class FakeApi:
        def process_folder(self, *_args, **_kwargs):
            return {"processed_count": 2, "error_count": 1}

    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    wrapper = ApiWrapper(api=FakeApi())
    wrapper._window = DummyWindow()
    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        ui_bridge,
        "ui_set_folder_status",
        lambda _window, message, state: statuses.append((message, state)),
    )
    opened = []
    monkeypatch.setattr(main.app_paths, "open_folder_in_file_manager", lambda path: opened.append(path))

    wrapper.folder_selected("anonymize_folder", str(folder))

    assert statuses[-1] == ("Обработка завершена частично: успешно 2, ошибок 1", "error")
    assert opened == [str(output)]


def test_native_drop_bridge_is_marked_per_bound_zone():
    """Сбой одной DOM-зоны не должен отключать browser fallback для другой."""

    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    class Element:
        def __init__(self):
            self.events = SimpleNamespace(drop=Event())

    class Dom:
        def __init__(self):
            self.element = Element()

        def get_element(self, selector):
            return self.element if selector == "#dropzone-anonymize" else None

    wrapper = ApiWrapper(api=MagicMock())
    window = DummyWindow()
    window.dom = Dom()
    wrapper._window = window

    wrapper._bind_native_drop_handlers()

    assert len(window.dom.element.events.drop.handlers) == 1
    assert any("['dropzone-anonymize'] = true" in script for script in window.js_calls)
    assert all("['folder-dropzone'] = true" not in script for script in window.js_calls)


def test_open_output_folder_setting_toggle(tmp_path, monkeypatch):
    """Проверяет соблюдение настройки open_output_folder при пакетной обработке."""
    from backend_api import BackendApi
    from folder_pipeline import FolderAnonymizationPipeline
    import app_paths

    opened_paths = []
    monkeypatch.setattr(app_paths, "open_folder_in_file_manager", lambda p, **kw: opened_paths.append(str(p)) or True)

    input_dir = tmp_path / "batch_in"
    input_dir.mkdir()
    (input_dir / "sample.docx").write_text("placeholder", encoding="utf-8")

    api = BackendApi()
    api.open_output_folder = False

    # When open_output_folder is False, folder pipeline does not open file manager
    pipeline = FolderAnonymizationPipeline(backend=api)
    with patch.object(api, "process_single_file", return_value=1):
        res = pipeline.process(input_dir)

    assert len(opened_paths) == 0


def test_review_window_fallback(monkeypatch):
    """Проверяет fallback при ошибке создания отдельного окна ревью."""
    import webview
    import webbrowser

    wrapper = ApiWrapper()
    opened_urls = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_urls.append(url) or True)
    monkeypatch.setattr(webview, "create_window", MagicMock(side_effect=RuntimeError("Webview unavailable")))

    result = wrapper.open_review_window()
    assert result["success"] is True
    assert result["action"] == "browser"
    assert len(opened_urls) == 1
    assert "review_window.html" in opened_urls[0]


def test_ui_bridge_safe_call_js():
    """Проверяет экранирование спецсимволов и безопасность в safe_call_js."""
    window = DummyWindow()
    res = ui_bridge.safe_call_js(window, "showAlert", "Привет</script><script>alert(1)</script>")
    assert res is True
    assert len(window.js_calls) == 1
    assert "</script" not in window.js_calls[0]
    assert "<\\/script" in window.js_calls[0]
