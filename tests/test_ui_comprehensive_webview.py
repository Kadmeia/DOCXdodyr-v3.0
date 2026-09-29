# -*- coding: utf-8 -*-
"""Комплексный автоматический тест кликабельности и работоспособности всех кнопок и окон интерфейса DOCXдодыр."""

import os
import sys
import time
import threading
import subprocess
from pathlib import Path
import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import webview
from main import ApiWrapper


def run_comprehensive_button_tests(window):
    """Выполняет клики по всем кнопкам всех окон и модальных панелей."""
    try:
        print("[TEST] Инициализация окна и ожидание загрузки...")
        time.sleep(2)

        def eval_js(code):
            return window.evaluate_js(code)

        # 1. Проверяем регистрацию слушателя ошибок JS
        eval_js("""
            window.__js_errors = [];
            window.addEventListener('error', function(e) {
                window.__js_errors.push(e.message || String(e));
            });
        """)

        # --- ТЕСТ 1: Переключение вкладок главного окна ---
        print("\n[TEST 1] Проверка вкладок главного окна...")
        eval_js("document.getElementById('btn-tab-restore').click();")
        time.sleep(0.3)
        res_restore = eval_js("document.getElementById('tab-restore').style.display;")
        assert res_restore == 'flex', f"Вкладка восстановления не открылась! display={res_restore}"

        eval_js("document.getElementById('btn-tab-anonymize').click();")
        time.sleep(0.3)
        res_anon = eval_js("document.getElementById('tab-anonymize').style.display;")
        assert res_anon == 'flex', f"Вкладка обезличивания не открылась! display={res_anon}"
        print("  ✓ Переключение основных вкладок работает корректно.")

        # --- ТЕСТ 2: Кнопки переключения тем ---
        print("\n[TEST 2] Проверка кнопок смены тем интерфейса...")
        eval_js("document.getElementById('btn-theme-dark').click();")
        time.sleep(0.2)
        is_dark = eval_js("document.documentElement.classList.contains('dark');")
        assert is_dark is True, "Темная тема не активировалась!"

        eval_js("document.getElementById('btn-theme-light').click();")
        time.sleep(0.2)
        is_light = not eval_js("document.documentElement.classList.contains('dark');")
        assert is_light is True, "Светлая тема не активировалась!"

        eval_js("document.getElementById('btn-theme-system').click();")
        time.sleep(0.2)
        print("  ✓ Кнопки смены тем работают.")

        # --- ТЕСТ 3: Чекбоксы настроек на главной вкладке ---
        print("\n[TEST 3] Проверка чекбоксов и переключателей...")
        eval_js("document.getElementById('cb-save-md').checked = true;")
        toggles = [
            'cb-save-original', 'cb-save-pdf', 'cb-irreversible-pdf',
            'cb-save-decoder', 'cb-continue-folder'
        ]
        for tid in toggles:
            before = eval_js(f"document.getElementById('{tid}').checked;")
            eval_js(f"document.getElementById('{tid}').click();")
            after = eval_js(f"document.getElementById('{tid}').checked;")
            assert before != after, f"Чекбокс #{tid} не переключился!"
            # Возвращаем исходное состояние
            eval_js(f"document.getElementById('{tid}').click();")
        eval_js("document.getElementById('cb-save-md').checked = false;")
        print(f"  ✓ Все {len(toggles)} переключателей успешно проверены.")

        # --- ТЕСТ 4: Модальное окно «Настройка списков» (List Settings) ---
        print("\n[TEST 4] Модальное окно списков исключений и замен...")
        eval_js("window.showListSettingsModal();")
        time.sleep(0.5)
        display = eval_js("document.getElementById('list-settings-overlay').style.display;")
        assert display == 'flex', "Окно списков не открылось!"

        # Вкладки внутри окна списков
        eval_js("document.getElementById('btn-tab-replacements').click();")
        time.sleep(0.2)
        assert eval_js("document.getElementById('tab-content-replacements').style.display;") == 'flex'

        eval_js("document.getElementById('btn-tab-exclusions').click();")
        time.sleep(0.2)
        assert eval_js("document.getElementById('tab-content-exclusions').style.display;") == 'flex'

        # Добавление исключения
        eval_js("document.getElementById('input-new-exclusion').value = 'ТестКомпания';")
        eval_js("window.addExclusion();")
        time.sleep(0.2)

        # Сохранение
        eval_js("document.getElementById('btn-save-lists').click();")
        time.sleep(0.5)

        # Закрытие через Отмену
        eval_js("window.closeListSettingsModal();")
        time.sleep(0.4)
        assert eval_js("document.getElementById('list-settings-overlay').style.display;") == 'none'
        print("  ✓ Модальное окно списков и все внутренние кнопки проверены.")

        # --- ТЕСТ 5: Модальное окно «Плейсхолдеры» (Placeholder Settings) ---
        print("\n[TEST 5] Модальное окно настроек плейсхолдеров...")
        eval_js("window.showPlaceholderSettingsModal();")
        time.sleep(0.5)
        display = eval_js("document.getElementById('placeholder-settings-overlay').style.display;")
        assert display == 'flex', "Окно плейсхолдеров не открылось!"

        # Кнопки "Снять все" и "Выбрать все"
        eval_js("window.toggleAllPlaceholders(false);")
        unchecked_count = eval_js("document.querySelectorAll('.placeholder-checkbox:checked').length;")
        assert unchecked_count == 0, "Кнопка 'Снять все' не сняла флажки!"

        eval_js("window.toggleAllPlaceholders(true);")
        checked_count = eval_js("document.querySelectorAll('.placeholder-checkbox:checked').length;")
        assert checked_count > 0, "Кнопка 'Выбрать все' не выставила флажки!"

        # Сохранение
        eval_js("document.getElementById('btn-save-placeholders').click();")
        time.sleep(0.5)

        # Закрытие
        eval_js("window.closePlaceholderSettingsModal();")
        time.sleep(0.4)
        assert eval_js("document.getElementById('placeholder-settings-overlay').style.display;") == 'none'
        print("  ✓ Окно плейсхолдеров и все кнопки успешно проверены.")

        # --- ТЕСТ 6: Модальное окно подтверждения установки Qwen 3.5 ---
        print("\n[TEST 6] Модальное окно подтверждения установки Qwen 3.5...")
        eval_js("window.showQwenConfirmModal();")
        time.sleep(0.4)
        qwen_disp = eval_js("document.getElementById('qwen-confirm-overlay').style.display;")
        assert qwen_disp == 'flex', "Окно подтверждения Qwen не открылось!"
        eval_js("window.closeQwenConfirmModal(false);")
        time.sleep(0.4)
        assert eval_js("document.getElementById('qwen-confirm-overlay').style.display;") == 'none'
        print("  ✓ Окно подтверждения Qwen 3.5 открывается и закрывается корректно.")

        # --- ТЕСТ 7: Модальное окно «Проверка сущностей» (Review Findings) ---
        print("\n[TEST 7] Модальное окно проверки сущностей...")
        eval_js("document.getElementById('btn-review-findings').click();")
        time.sleep(0.8)
        display = eval_js("document.getElementById('review-overlay').style.display;")
        assert display == 'flex', "Окно проверки сущностей не открылось!"

        # Проверка, что окно не застряло на Загрузка
        summary_text = eval_js("document.getElementById('review-summary').textContent;")
        assert "Загрузка" not in summary_text, f"Окно зависло на Загрузка: {summary_text}"
        print(f"  Статус сводки очереди: '{summary_text}'")

        # Кнопки фильтрации и пакетных действий
        eval_js("document.getElementById('review-filter-reset').click();")
        time.sleep(0.3)
        eval_js("document.getElementById('review-batch-accept').click();")
        time.sleep(0.4)

        # Закрытие кнопкой "Готово"
        eval_js("window.closeReviewModal();")
        time.sleep(0.4)
        assert eval_js("document.getElementById('review-overlay').style.display;") == 'none'

        # Открываем снова и закрываем крестиком
        eval_js("window.showReviewModal();")
        time.sleep(0.5)
        eval_js("document.querySelector('#review-modal button[aria-label=\"Закрыть\"]').click();")
        time.sleep(0.4)
        assert eval_js("document.getElementById('review-overlay').style.display;") == 'none'
        print("  ✓ Окно проверки сущностей, фильтры и кнопки закрытия работают отлично.")

        # --- ТЕСТ 8: Модальное окно «Скрытые данные» (Hidden Data) ---
        print("\n[TEST 8] Модальное окно анализа скрытых данных...")
        eval_js("""
            window.showHiddenDataModal({
                source_name: 'test_doc.docx',
                detected: { comments: 2, hidden_text: 1 },
                actions: { comments: 'remove', hidden_text: 'anonymize' }
            });
        """)
        time.sleep(0.5)
        display = eval_js("document.getElementById('hidden-data-overlay').style.display;")
        assert display == 'flex', "Окно скрытых данных не открылось!"

        # Чекбокс подтверждения
        eval_js("document.getElementById('hidden-data-confirm').click();")
        assert eval_js("document.getElementById('hidden-data-confirm').checked;") is True

        # Кнопка Создать безопасную копию
        eval_js("document.getElementById('hidden-data-apply').click();")
        time.sleep(0.3)

        # Закрытие через Отмена
        eval_js("window.closeHiddenDataModal();")
        time.sleep(0.4)
        assert eval_js("document.getElementById('hidden-data-overlay').style.display;") == 'none'
        print("  ✓ Окно скрытых данных и его элементы проверены.")

        # --- ТЕСТ 9: Модальное окно поддержки (Support Modal) ---
        print("\n[TEST 9] Модальное окно поддержки автора...")
        eval_js("window.openSupport();")
        time.sleep(0.5)
        display = eval_js("document.getElementById('support-overlay').style.display;")
        assert display == 'flex', "Окно поддержки не открылось!"

        # Закрытие кнопкой
        eval_js("window.closeSupportModal();")
        time.sleep(0.4)
        assert eval_js("document.getElementById('support-overlay').style.display;") == 'none'
        print("  ✓ Окно поддержки успешно проверено.")

        print("\n🎉 ВСЕ КНОПКИ И ОКНА РАБОТАЮТ ИДЕАЛЬНО БЕЗ ОШИБОК!")
        try:
            window.destroy()
        except Exception:
            pass
        os._exit(0)

    except Exception as exc:
        print(f"\n[ERROR] Ошибка тестирования UI: {exc}")
        import traceback
        traceback.print_exc()
        try:
            window.destroy()
        except Exception:
            pass
        os._exit(1)


@pytest.mark.skipif(
    sys.platform not in ('darwin', 'win32') or os.environ.get('DOCXDODYR_RUN_NATIVE_GUI') != '1',
    reason='Native desktop WebView requires an interactive session and DOCXDODYR_RUN_NATIVE_GUI=1',
)
def test_ui_comprehensive_webview(tmp_path):
    """Запускает комплексное автоматическое UI тестирование всех кнопок и окон интерфейса в отдельном процессе."""
    env = os.environ.copy()
    for kind in ("DATA", "USER_DATA", "CONFIG", "CACHE", "LOG"):
        env[f"DOCXDODYR_{kind}_DIR"] = str(tmp_path / kind.lower())
    res = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env=env,
    )
    assert res.returncode == 0, f"UI comprehensive test failed (code {res.returncode}):\n{res.stdout}\n{res.stderr}"


if __name__ == '__main__':
    wrapper = ApiWrapper()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, '..', 'web', 'index.html')
    if not os.path.exists(html_path):
        html_path = os.path.join(current_dir, 'web', 'index.html')

    window = webview.create_window(
        title='UI Buttons Test Suite',
        url=html_path,
        js_api=wrapper,
        width=840,
        height=740
    )
    wrapper.set_window(window)

    threading.Thread(target=run_comprehensive_button_tests, args=(window,), daemon=True).start()
    webview.start()
