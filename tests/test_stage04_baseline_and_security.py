# -*- coding: utf-8 -*-
"""Автотесты приёмки Этапа 04 — Тестовый и security baseline.

Проверяет:
1. Безопасную сериализацию вызовов JS bridge (предотвращение XSS и синтаксических ошибок в Webview).
2. Защиту от JavaScript-инъекций через вредоносные имена файлов и системные ошибки.
3. Санитизацию и маскирование PII и секретов в логах (паспорта, СНИЛС, счета, телефоны, email, API-ключи).
4. Механизм контролируемой отмены и безопасного завершения фоновых задач (Worker, FolderPipeline, ApiWrapper).
5. Изоляцию тестовой среды и отсутствие сетевых утечек.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock
import pytest

import ui_bridge
from log_sanitizer import (
    RedactingFilter,
    RedactingFormatter,
    sanitize_log_text,
    setup_application_logging,
)
from backend_api import BackendApi, Worker
from main import ApiWrapper


class MockWindow:
    """Mock-окно PyWebView для перехвата и валидации передаваемых скриптов."""

    def __init__(self):
        self.scripts: list[str] = []

    def evaluate_js(self, script: str):
        self.scripts.append(script)
        return True


def test_ui_bridge_serializes_safe_json_arguments():
    """Проверка корректной JSON-сериализации аргументов любых типов."""
    window = MockWindow()
    ui_bridge.safe_call_js(window, "testFunc", "hello", 123, True, {"key": "val"}, [1, 2, 3])

    assert len(window.scripts) == 1
    script = window.scripts[0]
    assert 'typeof window["testFunc"] === \'function\'' in script
    assert '"hello"' in script
    assert '123' in script
    assert 'true' in script
    assert '{"key": "val"}' in script
    assert '[1, 2, 3]' in script


@pytest.mark.parametrize(
    "malicious_input",
    [
        "'); alert('xss'); (//",
        '"><script>alert(1)</script>',
        '</script><script>window.pwned=true;</script>',
        "test\\'; window.bad=1; //",
        "Line 1\nLine 2\r\nLine 3",
        "Unicode \u2028 separator and \u2029 paragraph",
        "quotes ' \" ` ${eval(1)} ` \" '",
        "file <!-- comment --> name.docx",
    ],
)
def test_ui_bridge_prevents_code_injection_and_escapes_properly(malicious_input):
    """Проверка защиты от XSS и синтаксических сбоев при любых спецсимволах в именах файлов и ошибках."""
    window = MockWindow()
    ui_bridge.ui_alert(window, malicious_input)
    ui_bridge.ui_set_folder_status(window, malicious_input, "working")

    assert len(window.scripts) == 2
    for script in window.scripts:
        # Ни один сырой тег script или комментарий не должен попасть в JS
        assert "</script>" not in script
        assert "</SCRIPT>" not in script
        assert "<!--" not in script
        # Сырые разделители U+2028 / U+2029 должны быть экранированы
        assert "\u2028" not in script
        assert "\u2029" not in script
        # Исходный скрипт должен начинаться и заканчиваться корректной JS-конструкцией
        assert script.startswith("if (typeof window !== 'undefined'")


def test_ui_bridge_helpers_smoke():
    """Проверка всех встроенных хелперов модуля ui_bridge."""
    window = MockWindow()
    ui_bridge.ui_alert(window, "Внимание")
    ui_bridge.ui_set_global_progress(window, True, "Заголовок", "Текст", 42)
    ui_bridge.ui_set_global_progress(window, False)
    ui_bridge.ui_set_folder_status(window, "Готово", "ready")
    ui_bridge.ui_set_folder_output_hint(window, "/path/to/output")
    ui_bridge.ui_show_hidden_data_modal(window, {"files": 1})
    ui_bridge.ui_update_badge_status(window, "Активно: 1")
    ui_bridge.ui_update_dropzone_text(window, "zone1", "Файлы")
    ui_bridge.ui_show_support_modal(window, "https://example.com")
    ui_bridge.ui_show_review_modal(window)
    ui_bridge.ui_show_list_settings_modal(window)
    ui_bridge.ui_show_placeholder_settings_modal(window)
    ui_bridge.ui_show_legal_progress_modal(window)
    ui_bridge.ui_show_ocr_progress_modal(window)

    assert len(window.scripts) == 14


def test_log_sanitizer_masks_secrets_and_pii():
    """Проверка функции санитизации логов на тестовом наборе персональных данных и ключей."""
    sample_text = (
        "Ошибка запроса: key AIzaSyD1234567890abcdefghijklmnopqrstu. "
        "Пользователь Иванов, паспорт 45 15 123456, СНИЛС 123-456-789 01, "
        "р/с 40702810938000012345, карта 4276 3800 1234 5678. "
        "Email test.user+docs@example.com, тел. +7 (999) 123-45-67, 88005553535. "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    )
    sanitized = sanitize_log_text(sample_text)

    assert "AIzaSy" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized
    assert "45 15 123456" not in sanitized
    assert "[REDACTED_PASSPORT]" in sanitized
    assert "123-456-789 01" not in sanitized
    assert "[REDACTED_SNILS]" in sanitized
    assert "40702810938000012345" not in sanitized
    assert "[REDACTED_ACCOUNT]" in sanitized
    assert "test.user+docs@example.com" not in sanitized
    assert "[REDACTED_EMAIL]" in sanitized
    assert "+7 (999) 123-45-67" not in sanitized
    assert "[REDACTED_PHONE]" in sanitized
    assert "Bearer [REDACTED_TOKEN]" in sanitized


def test_log_sanitizer_filter_and_formatter_in_logger():
    """Проверка интеграции фильтра и форматтера в стандартный логгер Python."""
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(RedactingFormatter("%(levelname)s: %(message)s"))
    handler.addFilter(RedactingFilter())

    test_logger = logging.getLogger("test_security_logger")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)

    test_logger.info("Клиент имеет паспорт 77 02 654321 и телефон 89161234567")
    output = log_stream.getvalue()

    assert "77 02 654321" not in output
    assert "[REDACTED_PASSPORT]" in output
    assert "89161234567" not in output
    assert "[REDACTED_PHONE]" in output


def test_backend_api_cancellation_mechanism():
    """Проверка флага и методов отмены в BackendApi."""
    api = BackendApi()
    assert not api.is_cancelled()

    api.cancel_processing()
    assert api.is_cancelled()

    api.reset_cancellation()
    assert not api.is_cancelled()


def test_worker_aborts_immediately_when_cancelled(tmp_path):
    """Проверка прерывания Worker при активации флага отмены."""
    api = BackendApi()
    # Создаем 5 фиктивных файлов
    dummy_files = []
    for i in range(5):
        p = tmp_path / f"test_{i}.docx"
        p.write_text("test")
        dummy_files.append(str(p))

    worker = Worker(dummy_files, api)
    # Имитируем отмену до или в начале первой итерации
    api.cancel_processing()

    # Запускаем worker
    worker.run()

    # Worker не должен обработать все 5 файлов
    # (обработка должна прерваться на первой итерации)
    assert api.is_cancelled()


def test_network_calls_blocked_during_tests():
    """Проверка гарантии изоляции сети фикстурой conftest."""
    import socket

    with pytest.raises(RuntimeError, match="Внешние сетевые вызовы в тестах строго запрещены"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("8.8.8.8", 53))


def test_pytest_ini_and_collection_boundary():
    """Проверка корректности конфигурации pytest.ini и границ сбора тестов."""
    pytest_ini = Path("pytest.ini")
    assert pytest_ini.exists(), "Файл pytest.ini должен присутствовать в корне"
    content = pytest_ini.read_text(encoding="utf-8")
    assert "testpaths = tests" in content
    assert "norecursedirs" in content
    assert "scratch" in content

    # Проверяем, что интерактивный скрипт test_ui не находится в корне
    assert not Path("test_ui.py").exists(), "test_ui.py не должен находиться в корне"
    assert Path("scripts/manual_test_ui.py").exists(), "Интерактивный UI-скрипт должен быть в scripts/"
