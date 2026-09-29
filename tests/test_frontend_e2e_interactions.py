# -*- coding: utf-8 -*-
"""E2E-тесты фронтенда DOCXдодыр: проверка интерактивности и DOM-мутаций в реальном WebView.

Выполняет сквозную проверку через реальный движок WebKit (macOS):
1. Фильтрация списка категорий через поисковую строку.
2. Интерактивное переключение выбранного элемента в Master-Detail Split-View (обновление правой панели).
3. Переключение формата скобок ([ФИО] <-> /ФИО/) с реактивным обновлением DOM.
4. Пакетные действия «Выбрать все» / «Снять все» и синхронизация счётчиков/бейджей.
5. Фильтрация по правовым чипсам-разделам (LEGAL_SECTIONS).
6. Автономная генерация QR-кода и отображение реквизитов карты в модальном окне поддержки.
7. Закрытие модальных окон по нажатию клавиши Escape.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("DOCXDODYR_RUN_NATIVE_GUI") != "1",
    reason="Требует интерактивной macOS-сессии и DOCXDODYR_RUN_NATIVE_GUI=1",
)
def test_e2e_frontend_dom_and_user_interactions():
    """Запускает изолированный процесс с WebKit и проверяет все сценарии фронтенда."""
    python_bin = sys.executable
    code = '''
import os
import sys
import time
import threading
import webview
from PyObjCTools import AppHelper

import main

wrapper = main.ApiWrapper()
html_path = str(main.app_paths.get_web_dir() / "index.html")

window = webview.create_window(
    title="Frontend E2E Test Suite",
    url=html_path,
    js_api=wrapper,
    width=900,
    height=750
)
wrapper.set_window(window)

def _on_closing():
    wrapper.shutdown()

def _on_closed():
    wrapper.shutdown()
    os._exit(0)

window.events.closing += _on_closing
window.events.closed += _on_closed

def run_tests():
    time.sleep(1.0)  # Даем DOM полностью инициализироваться
    try:
        # 1. Открытие модалки категорий
        window.evaluate_js("window.showPlaceholderSettingsModal()")
        time.sleep(0.3)
        modal_open = window.evaluate_js("!document.getElementById('placeholder-settings-overlay').classList.contains('hidden')")
        assert modal_open, "Модалка категорий не открылась"

        # 2. Проверка начального счетчика (37 из 37)
        badge_text = window.evaluate_js("document.getElementById('placeholder-counter-badge').textContent")
        assert "37 из 37" in badge_text, f"Неверный счетчик: {badge_text}"

        # 3. Фильтрация поиском: ищем 'паспорт'
        window.evaluate_js("window.filterPlaceholderList('паспорт')")
        visible_passport = window.evaluate_js("document.querySelector('.split-list-item[data-cat-id=passport]').style.display !== 'none'")
        hidden_snils = window.evaluate_js("document.querySelector('.split-list-item[data-cat-id=snils]').style.display === 'none'")
        assert visible_passport and hidden_snils, "Поиск не отфильтровал категории"

        # Сброс поиска
        window.evaluate_js("window.filterPlaceholderList('')")

        # 4. Master-Detail Split-View: клик по 'snils'
        window.evaluate_js("window.selectPlaceholderCategory('snils')")
        pane_title = window.evaluate_js("document.getElementById('pane-title').textContent")
        pane_token = window.evaluate_js("document.getElementById('pane-token').textContent")
        assert "СНИЛС" in pane_title, f"Неверный заголовок: {pane_title}"
        assert "[СНИЛС]" in pane_token, f"Неверный токен: {pane_token}"

        # 5. Переключение формата скобок на косые /ФИО/
        window.evaluate_js("document.querySelector('input[name=bracket_type][value=slash]').checked = true; window.onBracketTypeChange();")
        pane_token_slash = window.evaluate_js("document.getElementById('pane-token').textContent")
        assert "/СНИЛС/" in pane_token_slash, f"Скобки не обновились: {pane_token_slash}"

        # Возвращаем квадратные скобки
        window.evaluate_js("document.querySelector('input[name=bracket_type][value=square]').checked = true; window.onBracketTypeChange();")
        pane_token_sq = window.evaluate_js("document.getElementById('pane-token').textContent")
        assert "[СНИЛС]" in pane_token_sq, f"Скобки не вернулись: {pane_token_sq}"

        # 6. Пакетные кнопки «Снять все» и «Выбрать все»
        window.evaluate_js("window.toggleAllPlaceholders(false)")
        zero_badge = window.evaluate_js("document.getElementById('placeholder-counter-badge').textContent")
        assert "0 из 37" in zero_badge, f"Снять все не сработало: {zero_badge}"

        window.evaluate_js("window.toggleAllPlaceholders(true)")
        all_badge = window.evaluate_js("document.getElementById('placeholder-counter-badge').textContent")
        assert "37 из 37" in all_badge, f"Выбрать все не сработало: {all_badge}"

        # 7. Фильтрация по разделам (чипсы)
        window.evaluate_js("window.filterBySectionChip('sec_banking')")
        visible_bank = window.evaluate_js("document.querySelector('.split-list-item[data-cat-id=ru_account]').style.display !== 'none'")
        hidden_personal = window.evaluate_js("document.querySelector('.split-list-item[data-cat-id=fio]').style.display === 'none'")
        assert visible_bank and hidden_personal, "Фильтрация по разделу не сработала"

        window.evaluate_js("window.filterBySectionChip('all')")

        # 8. Закрытие по Escape
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))")
        time.sleep(0.3)
        modal_closed = window.evaluate_js("document.getElementById('placeholder-settings-overlay').classList.contains('hidden')")
        assert modal_closed, "Модалка категорий не закрылась по Escape"

        # 9. Модальное окно поддержки: QR-код и номер карты
        window.evaluate_js("window.showSupportModal('https://www.tinkoff.ru/rm/r_cNLDGIyQuz.TzrqnfAGGL/G4xqW19880')")
        time.sleep(0.3)
        support_open = window.evaluate_js("!document.getElementById('support-overlay').classList.contains('hidden')")
        assert support_open, "Модалка поддержки не открылась"

        qr_rendered = window.evaluate_js("document.getElementById('qrcode-container').children.length > 0")
        assert qr_rendered, "QR-код не был сгенерирован библиотекой qrcode.min.js"

        card_text = window.evaluate_js("document.getElementById('card-number').textContent")
        assert "5536 9137 6905 7999" in card_text, f"Неверный номер карты: {card_text}"

        # Закрытие модалки поддержки по Escape
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))")
        time.sleep(0.3)
        support_closed = window.evaluate_js("document.getElementById('support-overlay').classList.contains('hidden')")
        assert support_closed, "Модалка поддержки не закрылась по Escape"

        print("ALL_FRONTEND_E2E_CHECKS_PASSED")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"TEST_FAILED: {e}")
    finally:
        window.destroy()

threading.Thread(target=run_tests, daemon=True).start()

try:
    webview.start(debug=False)
finally:
    wrapper.shutdown()
    os._exit(0)
'''

    start_time = time.time()
    res = subprocess.run(
        [python_bin, "-c", code],
        capture_output=True,
        text=True,
        timeout=15
    )
    elapsed = time.time() - start_time

    assert "ALL_FRONTEND_E2E_CHECKS_PASSED" in res.stdout, f"E2E тест упал:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    assert res.returncode == 0, f"Код ошибки {res.returncode}"
    print(f"\nE2E тестирование завершено успешно за {elapsed:.2f} сек.")
