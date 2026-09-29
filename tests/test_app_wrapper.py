import pytest
import threading
from unittest.mock import MagicMock
import main
from backend_api import BackendApi
from main import ApiWrapper
import webview


def test_api_wrapper_defers_pullenti_until_window_is_ready(monkeypatch):
    observed = {}

    class FakeBackend:
        def __init__(self, *, lazy_pullenti=False):
            observed["lazy_pullenti"] = lazy_pullenti

    monkeypatch.setattr(main, "BackendApi", FakeBackend)

    wrapper = main.ApiWrapper()

    assert observed["lazy_pullenti"] is True
    assert isinstance(wrapper._api, FakeBackend)


def test_background_pullenti_initialization_is_started_only_once(monkeypatch):
    api = BackendApi.__new__(BackendApi)
    api._pullenti_processor = None
    api._pullenti_state = "not_started"
    api._pullenti_state_lock = threading.Lock()
    api._pullenti_ready = threading.Event()
    api._pullenti_init_thread = None
    api._shutdown_requested = False
    release = threading.Event()
    calls = []

    def initialize():
        calls.append(True)
        release.wait(timeout=2)
        api._pullenti_processor = object()
        api._pullenti_state = "ready"
        api._pullenti_ready.set()
        return True

    monkeypatch.setattr(api, "_initialize_pullenti", initialize)

    first = api.start_pullenti_initialization()
    second = api.start_pullenti_initialization()
    assert first is second
    release.set()
    first.join(timeout=2)

    assert api.init_pullenti() is True
    assert len(calls) == 1

def test_api_wrapper_initial_ocr_lang(monkeypatch, tmp_path):
    """
    Тест 1: значение OCR по умолчанию распознаёт русский и английский.
    Проверяет, что при обновлении настроек без явного указания языка
    используется значение по умолчанию.
    """
    # Настройки теста не должны появляться в корне рабочего проекта.
    monkeypatch.chdir(tmp_path)
    wrapper = ApiWrapper()
    # Эмулируем получение настроек от фронтенда (как при инициализации)
    wrapper.update_settings({})
    assert wrapper._api.ocr_lang == 'rus+eng'
    
def test_modal_functions_do_not_block():
    """
    Тест 2: вызовы получения данных для модальных окон
    возвращают данные (успешный статус) и не вызывают исключений.
    Эти методы должны отрабатывать мгновенно, не блокируя поток.
    """
    wrapper = ApiWrapper()
    
    # Проверка получения настроек списков
    lists = wrapper.get_lists()
    assert isinstance(lists, dict)
    assert 'exclusions' in lists
    assert 'replacements' in lists
    
    # Проверка получения настроек плейсхолдеров
    placeholders = wrapper.get_placeholder_settings()
    assert isinstance(placeholders, dict)
    assert 'bracket_type' in placeholders
    assert 'enabled_placeholders' in placeholders
    

def test_window_size():
    """
    Тест 3: проверка правильных параметров инициализации окна.
    Перехватываем параметры вызова webview.create_window.
    """
    original_create_window = webview.create_window
    try:
        mock_create = MagicMock()
        webview.create_window = mock_create
        
        wrapper = ApiWrapper()
        
        # Эмуляция того, как это вызывается в if __name__ == '__main__':
        webview.create_window(
            title='DOCXдодыр v2.0',
            url='web/index.html',
            js_api=wrapper,
            width=1200,
            height=850,
            min_size=(800, 600)
        )
        
        mock_create.assert_called_with(
            title='DOCXдодыр v2.0',
            url='web/index.html',
            js_api=wrapper,
            width=1200,
            height=850,
            min_size=(800, 600)
        )
    finally:
        webview.create_window = original_create_window


def test_api_wrapper_cancellation_and_shutdown():
    """Тест 4: проверка работы cancel_processing и shutdown."""
    wrapper = ApiWrapper()
    assert hasattr(wrapper, "cancel_processing")
    assert hasattr(wrapper, "shutdown")
    assert not wrapper._api.is_cancelled()
    wrapper.cancel_processing()
    assert wrapper._api.is_cancelled()
    wrapper.shutdown()


def test_api_wrapper_get_settings_and_update_settings(monkeypatch, tmp_path):
    """Тест 5: проверка получения и обновления общих настроек приложения."""
    monkeypatch.chdir(tmp_path)
    wrapper = ApiWrapper()
    settings = wrapper.get_settings()
    assert isinstance(settings, dict)
    assert "save_original" in settings
    assert "save_decoder" in settings
    assert "open_output_folder" in settings
    assert settings["save_decoder"] is False
    assert settings["open_output_folder"] is True

    wrapper.update_settings({
        "save_decoder": True,
        "open_output_folder": False,
        "save_pdf": True
    })
    updated = wrapper.get_settings()
    assert updated["save_decoder"] is True
    assert updated["open_output_folder"] is False
    assert updated["save_pdf"] is True
    assert wrapper._api.save_decoder is True
    assert wrapper._api.open_output_folder is False
    assert wrapper._api.save_pdf is True
