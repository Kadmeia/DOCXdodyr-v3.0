# -*- coding: utf-8 -*-
import pytest
from types import SimpleNamespace

pytest.importorskip("webview")

from backend_api import BackendApi


class _Occurrence:
    def __init__(self, text, start, end):
        self.sofa = type("Source", (), {"text": text})()
        self.begin_char = start
        self.end_char = end

    def get_text(self):
        return self.sofa.text[self.begin_char:self.end_char + 1]


class _Referent:
    def __init__(self, type_name, occurrence):
        self.type_name = type_name
        self.occurrence = [occurrence]


class _Processor:
    def process(self, source, *_):
        text = source.text
        referents = []
        for type_name, surface in self.entities:
            start = text.index(surface)
            referents.append(_Referent(
                type_name,
                _Occurrence(text, start, start + len(surface) - 1),
            ))
        return type("Result", (), {"entities": referents})()


def _api_with_entities(entities):
    api = BackendApi.__new__(BackendApi)
    processor = _Processor()
    processor.entities = entities
    api._pullenti_processor = processor
    api.current_placeholders = {
        "PER": "[ФИО]",
        "ORG": "[Наименование]",
        "ADDRESS": "[Адрес]",
    }
    api.enabled_placeholders = set(api.current_placeholders)
    return api


def test_full_pipeline_uses_last_character_of_pullenti_entity():
    api = _api_with_entities([("PERSON", "Иванов Иван Иванович")])

    cleaned, count, _ = api.anonymize_text_pullenti(
        "Иванов Иван Иванович заключил договор.", set(), set()
    )

    assert cleaned == "[ФИО] заключил договор."
    assert count == 1


def test_full_pipeline_does_not_merge_two_people():
    first = "Иванов Иван Иванович"
    second = "Петров Пётр Петрович"
    api = _api_with_entities([("PERSON", first), ("PERSON", second)])

    cleaned, count, _ = api.anonymize_text_pullenti(
        f"{first}, {second} подписали акт.", set(), set()
    )

    assert cleaned == "[ФИО], [ФИО] подписали акт."
    assert count == 2


def test_exclusions_and_custom_rules_do_not_affect_pullenti_pipeline():
    person = "Иванов Иван Иванович"
    api = _api_with_entities([("PERSON", person)])

    cleaned, count, _ = api.anonymize_text_pullenti(
        f"{person} подписал секретный акт.",
        {person},
        {"секретный акт -> особый документ"},
    )

    assert cleaned == "Иванов Иван Иванович подписал особый документ."
    assert count == 1


def test_qwen_hook_runs_only_after_placeholder_replacement():
    person = "Иванов Иван Иванович"
    api = _api_with_entities([("PERSON", person)])
    api.qwen_settings = SimpleNamespace(enabled=True)
    api._qwen_postprocessor = SimpleNamespace(last_status="ok")
    received = []

    def polish(text):
        received.append(text)
        return text.replace("заключил договор", "заключило договор")

    api.postprocess_anonymized_text = polish
    cleaned, count, _ = api.anonymize_text_pullenti(
        f"{person} заключил договор.", set(), set()
    )

    assert received == ["[ФИО] заключил договор."]
    assert cleaned == "[ФИО] заключило договор."
    assert count == 1


def test_paragraph_text_preserves_edge_whitespace():
    api = BackendApi.__new__(BackendApi)
    paragraph = type("Paragraph", (), {"text": "  Иванов Иван Иванович  "})()

    assert api.get_paragraph_text_with_revisions(paragraph) == "  Иванов Иван Иванович  "
