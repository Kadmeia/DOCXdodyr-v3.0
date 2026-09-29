# -*- coding: utf-8 -*-
"""Тесты доступности (a11y), эргономики и статической структуры фронтенда DOCXдодыр.

Использует только стандартную библиотеку Python (html.parser).

Проверяет:
1. Доступность кнопок (наличие текста, aria-label или title).
2. Доступность полей ввода (placeholder, label или aria-label).
3. Поддержку клавиатурной навигации и закрытия всех модалок по клавише Escape.
4. Наличие кнопок закрытия во всех модальных окнах.
5. Корректность и полноту классов тёмной темы (dark mode).
6. Автономность и порядок подключения локальных скриптов (qrcode.min.js перед script.js).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path


def get_web_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


def get_index_html_content() -> str:
    html_path = get_web_dir() / "index.html"
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


def get_script_js_content() -> str:
    script_path = get_web_dir() / "script.js"
    with open(script_path, "r", encoding="utf-8") as f:
        return f.read()


class ElementCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.inputs = []
        self.scripts = []
        self.labels = []
        self._current_tag = None
        self._current_btn = None

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        self._current_tag = tag
        if tag == "button":
            self._current_btn = {
                "attrs": attr_dict,
                "text": ""
            }
            self.buttons.append(self._current_btn)
        elif tag == "input":
            self.inputs.append(attr_dict)
        elif tag == "script" and "src" in attr_dict:
            self.scripts.append(attr_dict["src"])
        elif tag == "label" and "for" in attr_dict:
            self.labels.append(attr_dict["for"])

    def handle_endtag(self, tag):
        if tag == "button":
            self._current_btn = None
        self._current_tag = None

    def handle_data(self, data):
        if self._current_btn is not None:
            self._current_btn["text"] += data.strip()


def parse_elements():
    parser = ElementCollector()
    parser.feed(get_index_html_content())
    return parser


def test_all_buttons_have_accessible_names_or_labels():
    """Все кнопки в HTML должны быть понятны пользователю (текст, aria-label или title)."""
    parser = parse_elements()
    assert len(parser.buttons) > 0, "Не найдены кнопки в index.html"

    for btn in parser.buttons:
        attrs = btn["attrs"]
        text = btn["text"].strip()
        aria_label = attrs.get("aria-label", "").strip()
        title = attrs.get("title", "").strip()
        has_accessible_name = bool(text or aria_label or title)

        btn_id = attrs.get("id", attrs.get("class", "unknown-btn"))
        assert has_accessible_name, f"Кнопка {btn_id} не имеет текстового описания, aria-label или title"


def test_all_inputs_have_accessible_descriptions():
    """Все поля ввода должны иметь placeholder, связанный label или aria-label."""
    parser = parse_elements()
    assert len(parser.inputs) > 0, "Не найдены поля ввода в index.html"

    for attrs in parser.inputs:
        inp_type = attrs.get("type", "text")
        if inp_type in ("hidden", "checkbox", "radio"):
            continue

        placeholder = attrs.get("placeholder", "").strip()
        aria_label = attrs.get("aria-label", "").strip()
        inp_id = attrs.get("id", "")
        has_label = (inp_id in parser.labels) if inp_id else False

        has_accessible_desc = bool(placeholder or aria_label or has_label)
        assert has_accessible_desc, f"Поле ввода id='{inp_id}' (type={inp_type}) не имеет доступного описания или placeholder"


def test_all_modals_have_escape_listener():
    """Каждое модальное окно должно поддерживать закрытие по клавише Escape."""
    script = get_script_js_content()
    assert "Escape" in script, "В script.js отсутствует обработка клавиши Escape"

    expected_modals = [
        "placeholder-settings-overlay",
        "list-settings-overlay",
        "support-overlay",
        "review-overlay",
        "hidden-data-overlay"
    ]

    for modal_id in expected_modals:
        assert modal_id in script, f"Модальное окно {modal_id} не найдено в обработчиках script.js"


def test_all_modals_have_close_buttons():
    """Каждое модальное окно в HTML имеет кнопку закрытия с вызовом close*Modal()."""
    html = get_index_html_content()
    modal_overlays = [
        ("placeholder-settings-overlay", "closePlaceholderSettingsModal"),
        ("list-settings-overlay", "closeListSettingsModal"),
        ("support-overlay", "closeSupportModal"),
        ("review-overlay", "closeReviewModal"),
        ("hidden-data-overlay", "closeHiddenDataModal")
    ]

    for overlay_id, close_fn in modal_overlays:
        assert f'id="{overlay_id}"' in html, f"Оверлей #{overlay_id} не найден в index.html"
        assert close_fn in html, f"Функция закрытия {close_fn} не найдена в разметке #{overlay_id}"


def test_dark_mode_classes_presence():
    """Основные панели и модальные окна обязаны поддерживать стили тёмной темы (dark:)."""
    html = get_index_html_content()
    modals = [
        "placeholder-settings-modal",
        "list-settings-modal",
        "support-modal",
        "review-modal"
    ]

    for modal_id in modals:
        assert f'id="{modal_id}"' in html, f"Контейнер #{modal_id} не найден в index.html"

    # Проверяем общую насыщенность dark-классами в index.html
    dark_bg_count = len(re.findall(r"dark:bg-", html))
    dark_text_count = len(re.findall(r"dark:text-", html))
    dark_border_count = len(re.findall(r"dark:border-", html))

    assert dark_bg_count >= 20, f"Недостаточно стилей тёмной темы dark:bg- ({dark_bg_count})"
    assert dark_text_count >= 20, f"Недостаточно стилей тёмной темы dark:text- ({dark_text_count})"
    assert dark_border_count >= 10, f"Недостаточно стилей тёмной темы dark:border- ({dark_border_count})"
    assert "html.dark #placeholder-settings-modal" in html, "Отсутствуют селекторы тёмной темы для #placeholder-settings-modal"
    assert "#placeholder-settings-modal {" in html, "Отсутствуют базовые стили для #placeholder-settings-modal"


def test_offline_qrcode_library_and_script_order():
    """Библиотека qrcode.min.js должна присутствовать локально и подключаться строго перед script.js."""
    web_dir = get_web_dir()
    qr_file = web_dir / "qrcode.min.js"
    assert qr_file.exists(), "Файл web/qrcode.min.js отсутствует! Генерация QR-кода не будет автономной."
    assert qr_file.stat().st_size > 5000, "Файл web/qrcode.min.js повреждён или пуст."

    parser = parse_elements()
    scripts = parser.scripts

    assert "qrcode.min.js" in scripts, "qrcode.min.js не подключен в index.html"
    assert "script.js" in scripts, "script.js не подключен в index.html"

    qr_idx = scripts.index("qrcode.min.js")
    main_idx = scripts.index("script.js")
    assert qr_idx < main_idx, f"qrcode.min.js (индекс {qr_idx}) должен загружаться ДО script.js (индекс {main_idx})"


def test_folder_batch_block_resilience_to_flex_shrink():
    """Блок выбора папки не должен сжиматься flexbox'ом или скрывать содержимое."""
    html = get_index_html_content()
    
    # Ищем контейнер блока выбора папки
    folder_block_match = re.search(r'<div[^>]*aria-label="Пакетная обработка папки"[^>]*>', html)
    assert folder_block_match is not None, "Блок с aria-label='Пакетная обработка папки' не найден"
    
    folder_block_tag = folder_block_match.group(0)
    assert "overflow-hidden" not in folder_block_tag, (
        "Блок выбора папки не должен иметь overflow-hidden, иначе во flex-контейнере его min-height "
        "становится равным 0 и блок сплющивается при нехватке высоты окна."
    )
    assert "flex-shrink-0" in folder_block_tag, (
        "Блок выбора папки должен иметь класс flex-shrink-0 для предотвращения сжатия по вертикали."
    )
    
    # Родительский контейнер tab-anonymize должен иметь overflow-y-auto и flex-col
    tab_anon_match = re.search(r'<main[^>]*id="tab-anonymize"[^>]*>', html)
    assert tab_anon_match is not None, "Элемент #tab-anonymize не найден"
    tab_anon_tag = tab_anon_match.group(0)
    assert "overflow-y-auto" in tab_anon_tag, "#tab-anonymize должен иметь overflow-y-auto для скролла контента"
    assert "flex-col" in tab_anon_tag, "#tab-anonymize должен иметь flex-col для вертикальной раскладки"

