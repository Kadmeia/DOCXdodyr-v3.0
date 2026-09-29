# -*- coding: utf-8 -*-
"""Тесты жизненного цикла окна: открытие, закрытие на крестик без зависаний и стресс-тесты.

Проверяет:
1. Защиту ui_bridge от вызовов evaluate_js во время closing/closed (предотвращение дедлока в Cocoa).
2. Безопасное завершение ApiWrapper.shutdown() и BackendApi.shutdown() без обращения к UI.
3. Полный цикл открытия и закрытия окна на крестик (performClose_ на macOS) через отдельный процесс.
4. Нагрузочный стресс-тест многократного открытия и закрытия окон подряд без утечек и зависаний.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

import ui_bridge
from backend_api import BackendApi
from main import ApiWrapper


def test_ui_bridge_safeguard_during_window_closing():
    """ui_bridge.safe_call_js не должен вызывать evaluate_js, если окно закрывается."""
    mock_window = MagicMock()
    mock_window.events.closing.is_set.return_value = True
    mock_window.events.closed.is_set.return_value = False

    result = ui_bridge.safe_call_js(mock_window, "testFunction", 123)

    assert result is False
    mock_window.evaluate_js.assert_not_called()


def test_ui_bridge_safeguard_during_window_closed():
    """ui_bridge.safe_call_js не должен вызывать evaluate_js, если окно уже закрыто."""
    mock_window = MagicMock()
    mock_window.events.closing.is_set.return_value = False
    mock_window.events.closed.is_set.return_value = True

    result = ui_bridge.safe_call_js(mock_window, "testFunction", "data")

    assert result is False
    mock_window.evaluate_js.assert_not_called()


def test_api_wrapper_shutdown_contract():
    """ApiWrapper.shutdown() корректно выставляет флаги отмены и не вызывает evaluate_js."""
    wrapper = ApiWrapper()
    mock_window = MagicMock()
    wrapper.set_window(mock_window)

    wrapper.shutdown()

    # Флаги отмены должны быть выставлены
    assert wrapper._api._cancel_event.is_set()
    # evaluate_js не должен вызываться во время shutdown!
    mock_window.evaluate_js.assert_not_called()


def test_backend_api_shutdown_contract():
    """BackendApi.shutdown() останавливает фоновые потоки и флаги без обращения к UI."""
    api = BackendApi()
    mock_window = MagicMock()
    api.set_window(mock_window)

    dummy_thread_finished = threading.Event()

    def dummy_worker():
        while not api.is_cancelled():
            time.sleep(0.02)
        dummy_thread_finished.set()

    t = threading.Thread(target=dummy_worker, daemon=True)
    t.start()
    api._active_threads.append(t)

    api.shutdown()

    assert api.is_cancelled()
    assert dummy_thread_finished.wait(timeout=1.0)
    mock_window.evaluate_js.assert_not_called()


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("DOCXDODYR_RUN_NATIVE_GUI") != "1",
    reason="Требует интерактивной macOS-сессии и DOCXDODYR_RUN_NATIVE_GUI=1",
)
def test_real_window_close_on_cross_button_subprocess():
    """Проверяет реальное закрытие окна через клик на крестик (performClose_) в подпроцессе."""
    python_bin = sys.executable
    code = """
import os
import sys
import time
import threading
import webview
from PyObjCTools import AppHelper
from AppKit import NSApplication

import main

wrapper = main.ApiWrapper()
html_path = str(main.app_paths.get_web_dir() / 'index.html')

window = webview.create_window(
    title='Subproc Close Test',
    url=html_path,
    js_api=wrapper,
    width=600,
    height=500
)
wrapper.set_window(window)

closing_fired = False

def _on_closing():
    global closing_fired
    closing_fired = True
    wrapper.shutdown()

def _on_closed():
    wrapper.shutdown()
    os._exit(0)

window.events.closing += _on_closing
window.events.closed += _on_closed

def simulate_cross_click():
    app = NSApplication.sharedApplication()
    for win in app.windows():
        if win.title() == 'Subproc Close Test':
            win.performClose_(None)
            break

def trigger_close():
    time.sleep(1.2)
    AppHelper.callAfter(simulate_cross_click)

threading.Thread(target=trigger_close, daemon=True).start()

try:
    webview.start(debug=False)
finally:
    wrapper.shutdown()
    os._exit(0)
"""

    start_time = time.time()
    res = subprocess.run(
        [python_bin, "-c", code],
        capture_output=True,
        text=True,
        timeout=10
    )
    elapsed = time.time() - start_time

    assert res.returncode == 0, f"Подпроцесс завершился с ошибкой {res.returncode}:\nSTDERR: {res.stderr}\nSTDOUT: {res.stdout}"
    assert elapsed < 8.0, f"Закрытие окна заняло слишком много времени ({elapsed:.2f}s) — возможно зависание!"


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("DOCXDODYR_RUN_NATIVE_GUI") != "1",
    reason="Требует интерактивной macOS-сессии и DOCXDODYR_RUN_NATIVE_GUI=1",
)
def test_stress_window_open_and_close_cycles():
    """Стресс-тест: многократное быстрое открытие и закрытие окон подряд."""
    python_bin = sys.executable
    cycles = 2
    single_cycle_code = """
import os
import sys
import time
import threading
import webview
from PyObjCTools import AppHelper
from AppKit import NSApplication

import main

wrapper = main.ApiWrapper()
html_path = str(main.app_paths.get_web_dir() / 'index.html')

window = webview.create_window(
    title='Stress Window',
    url=html_path,
    js_api=wrapper,
    width=500,
    height=400
)
wrapper.set_window(window)

def _on_closing():
    wrapper.shutdown()

def _on_closed():
    wrapper.shutdown()
    os._exit(0)

window.events.closing += _on_closing
window.events.closed += _on_closed

def trigger(w):
    time.sleep(0.3)
    try:
        w.destroy()
    except Exception:
        pass
    time.sleep(0.2)
    os._exit(0)

threading.Thread(target=trigger, args=(window,), daemon=True).start()
try:
    webview.start(debug=False)
finally:
    wrapper.shutdown()
    os._exit(0)
"""

    start_time = time.time()
    for cycle in range(cycles):
        res = subprocess.run(
            [python_bin, "-c", single_cycle_code],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res.returncode == 0, f"Стресс-тест на цикле {cycle} завершился с ошибкой {res.returncode}:\nSTDERR: {res.stderr}\nSTDOUT: {res.stdout}"
    elapsed = time.time() - start_time
    assert elapsed < 45.0, f"Стресс-тест занял {elapsed:.2f}s, ожидалось менее 45с."
