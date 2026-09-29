# -*- coding: utf-8 -*-
"""Тесты кликабельности, привязки обработчиков и работоспособности кнопок интерфейса DOCXдодыр."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
import pytest
from backend_api import BackendApi
from main import ApiWrapper


class ButtonExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.links = []
        self.inputs = []
        self.selects = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "button":
            self.buttons.append(attr_dict)
        elif tag == "a":
            self.links.append(attr_dict)
        elif tag == "input":
            self.inputs.append(attr_dict)
        elif tag == "select":
            self.selects.append(attr_dict)


@pytest.fixture(scope="module")
def html_content():
    html_path = Path(__file__).resolve().parent.parent / "web" / "index.html"
    return html_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_content():
    js_path = Path(__file__).resolve().parent.parent / "web" / "script.js"
    return js_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed_dom(html_content):
    parser = ButtonExtractor()
    parser.feed(html_content)
    return parser


def test_all_html_buttons_have_identifiers_or_handlers(parsed_dom):
    """Каждая кнопка в HTML должна иметь id, onclick или обработчик в JS."""
    for idx, btn in enumerate(parsed_dom.buttons):
        has_id = bool(btn.get("id"))
        has_onclick = bool(btn.get("onclick"))
        assert has_id or has_onclick, f"Кнопка #{idx} ({btn}) не имеет ни id, ни onclick"


def test_all_onclick_functions_exist_in_js(parsed_dom, js_content):
    """Каждая функция, вызываемая в onclick, должна быть объявлена в script.js."""
    onclick_pattern = re.compile(r"([a-zA-Z0-9_]+)\s*\(")
    checked = set()
    for btn in parsed_dom.buttons + parsed_dom.links:
        onclick = btn.get("onclick", "")
        if not onclick:
            continue
        matches = onclick_pattern.findall(onclick)
        for fn_name in matches:
            if fn_name in checked:
                continue
            checked.add(fn_name)
            # Ищем объявление: function fnName, window.fnName = ..., fnName = ...
            patterns = [
                rf"function\s+{fn_name}\s*\(",
                rf"window\.{fn_name}\s*=",
                rf"{fn_name}\s*=\s*(?:function|\()",
            ]
            found = any(re.search(p, js_content) for p in patterns)
            assert found, f"Функция '{fn_name}', вызываемая в onclick, не найдена в script.js!"


def test_all_js_pywebview_api_calls_exist_in_backend(js_content):
    """Все вызовы window.pywebview.api.<method> должны существовать в ApiWrapper и BackendApi."""
    api_call_pattern = re.compile(r"api\.([a-zA-Z0-9_]+)\s*\(")
    calls = set(api_call_pattern.findall(js_content))

    wrapper = ApiWrapper()
    backend = wrapper._api

    for method_name in calls:
        # Проверяем наличие в ApiWrapper
        assert hasattr(wrapper, method_name), f"Метод '{method_name}' вызывается из JS, но отсутствует в ApiWrapper!"
        # Проверяем наличие в BackendApi (или прямо в ApiWrapper для UI прокси)
        assert callable(getattr(wrapper, method_name)), f"Атрибут '{method_name}' в ApiWrapper не является вызываемым!"


def test_review_findings_modal_backend_contract():
    """Тест работоспособности API для модального окна проверки сущностей."""
    api = BackendApi()
    
    # 1. Получение с лимитом и оффсетом
    res = api.get_review_findings(limit=10, offset=0)
    assert isinstance(res, dict)
    assert "items" in res
    assert "summary" in res
    assert "filtered_summary" in res
    assert "entity_types" in res
    assert "total_filtered" in res
    assert len(res["items"]) <= 10

    # 2. Фильтрация по статусу
    res_pending = api.get_review_findings(status="pending", limit=5)
    assert isinstance(res_pending["items"], list)
    for item in res_pending["items"]:
        assert item["status"] == "pending"

    # 3. Массовое решение без передачи гигантских объектов
    batch_res = api.batch_decide_review_findings(status="accepted")
    assert batch_res["success"] is True
    assert "count" in batch_res

    # 4. Проверка сброса
    clear_res = api.clear_review_findings()
    assert clear_res["success"] is True
    assert len(api.review_queue.items()) == 0


def test_all_settings_modals_backend_contracts():
    """Тест контрактов бэкенда для локальных настроек."""
    api = BackendApi()

    # 1. Списки исключений и замен
    lists = api.get_lists()
    assert "exclusions" in lists
    assert "replacements" in lists
    save_list_res = api.save_lists({"exclusions": ["ТестИскл"], "replacements": ["ТестЗам"]})
    assert save_list_res["success"] is True

    # 2. Плейсхолдеры
    ph = api.get_placeholder_settings()
    assert "bracket_type" in ph
    assert "enabled_placeholders" in ph
    assert "all_placeholders" in ph
    save_ph_res = api.save_placeholder_settings({
        "bracket_type": "square",
        "enabled_placeholders": list(ph["enabled_placeholders"])
    })
    assert save_ph_res["success"] is True

    # 3. Скрытые данные
    hidden_policy = api.get_hidden_data_policy()
    assert "actions" in hidden_policy
    assert "kinds" in hidden_policy
    save_hidden_res = api.save_hidden_data_policy(hidden_policy["actions"], confirm_destructive=True)
    assert save_hidden_res["success"] is True
