# -*- coding: utf-8 -*-
"""Безопасный JavaScript-мост для взаимодействия Python и PyWebView.

Гарантирует корректную сериализацию всех аргументов в формат JSON (через json.dumps),
полностью исключая инъекции кода (XSS), ошибки экранирования кавычек, переносов
строк и спецсимволов (включая Unicode Line/Paragraph Separator U+2028/U+2029 и теги <script>).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def serialize_js_arg(arg: Any) -> str:
    """Сериализует любой Python-объект в безопасный строковый литерал JavaScript.

    Все аргументы кодируются по строгой спецификации JSON с дополнительной
    нейтрализацией тегов HTML/XML и опасных спецсимволов Unicode.
    """
    raw = json.dumps(arg, ensure_ascii=False)
    # Защита от выхода из контекста тега script или комментария в WebView
    safe = (
        raw.replace("</script", "<\\/script")
        .replace("</SCRIPT", "<\\/SCRIPT")
        .replace("<!--", "<\\!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return safe


def safe_call_js(window: Any, func_name: str, *args: Any) -> bool:
    """Безопасно вызывает глобальную JavaScript-функцию на объекте window.

    Args:
        window: Экземпляр окна webview.Window или None.
        func_name: Имя глобальной JS-функции (например, 'showAlert').
        *args: Произвольное количество позиционных аргументов Python.

    Returns:
        bool: True если вызов успешно передан в WebView, иначе False.
    """
    if not window:
        return False

    # Защита от дедлока при закрытии окна (evaluate_js блокирует главный поток Cocoa)
    if hasattr(window, "events"):
        try:
            if hasattr(window.events, "closing") and window.events.closing.is_set():
                return False
            if hasattr(window.events, "closed") and window.events.closed.is_set():
                return False
        except Exception:
            pass

    try:
        serialized_args = [serialize_js_arg(arg) for arg in args]
        args_repr = ", ".join(serialized_args)
        # Проверяем наличие функции в window перед вызовом во избежание uncaught TypeError в JS
        script = (
            f"if (typeof window !== 'undefined' && typeof window[{json.dumps(func_name)}] === 'function') "
            f"{{ window[{json.dumps(func_name)}]({args_repr}); }}"
        )
        window.evaluate_js(script)
        return True
    except Exception as exc:
        logger.debug("Не удалось выполнить JS-вызов %s: %s", func_name, exc)
        return False


# Алиас для совместимости с кодом backend_api
call_js = safe_call_js


def ui_alert(window: Any, message: str) -> bool:
    """Отображает диалоговое окно предупреждения/ошибки в UI."""
    return safe_call_js(window, "showAlert", str(message))


def ui_set_global_progress(
    window: Any,
    visible: bool,
    title: str = "",
    text: str = "",
    percent: Optional[int] = None,
) -> bool:
    """Управляет глобальным модальным индикатором прогресса."""
    if not visible:
        return safe_call_js(window, "setGlobalProgress", False)
    if percent is not None:
        return safe_call_js(window, "setGlobalProgress", True, str(title), str(text), int(percent))
    return safe_call_js(window, "setGlobalProgress", True, str(title), str(text))


def ui_set_folder_status(window: Any, status_text: str, status_class: str = "ready") -> bool:
    """Обновляет статус пакетной обработки папки."""
    return safe_call_js(window, "setFolderStatus", str(status_text), str(status_class))


def ui_set_folder_output_hint(window: Any, output_path: str) -> bool:
    """Обновляет путь к выходной папке в интерфейсе."""
    return safe_call_js(window, "setFolderOutputHint", str(output_path))


def ui_show_hidden_data_modal(window: Any, payload: dict) -> bool:
    """Отображает модальное окно аудита скрытых метаданных."""
    return safe_call_js(window, "showHiddenDataModal", payload)


def ui_update_badge_status(window: Any, badge_text: str) -> bool:
    """Обновляет плашку со счетчиками исключений и замен."""
    return safe_call_js(window, "updateBadgeStatus", str(badge_text))


def ui_update_dropzone_text(window: Any, zone_id: str, label_text: str) -> bool:
    """Обновляет текстовую подпись дропзоны."""
    return safe_call_js(window, "updateDropzoneText", str(zone_id), str(label_text))


def ui_show_support_modal(window: Any, url: str) -> bool:
    """Открывает окно пожертвований/поддержки автора."""
    return safe_call_js(window, "showSupportModal", str(url))


def ui_show_review_modal(window: Any) -> bool:
    """Открывает модальное окно интерактивного ревью находок."""
    return safe_call_js(window, "showReviewModal")


def ui_show_list_settings_modal(window: Any) -> bool:
    """Открывает модальное окно настроек списков исключений/замен."""
    return safe_call_js(window, "showListSettingsModal")


def ui_show_placeholder_settings_modal(window: Any) -> bool:
    """Открывает модальное окно настроек типов плейсхолдеров."""
    return safe_call_js(window, "showPlaceholderSettingsModal")


def ui_show_legal_progress_modal(window: Any) -> bool:
    """Открывает прогресс правового анализа."""
    return safe_call_js(window, "showLegalProgressModal")


def ui_show_ocr_progress_modal(window: Any) -> bool:
    """Открывает прогресс OCR."""
    return safe_call_js(window, "showOcrProgressModal")
