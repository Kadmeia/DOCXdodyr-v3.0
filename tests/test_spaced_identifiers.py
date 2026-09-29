# -*- coding: utf-8 -*-
"""Regression tests for spaced identifier recognition (INN, OGRN, OGRNIP, KPP)."""

import pytest
from pullenti_legal.analyzer import iter_candidates
from entity_registry import canonical_key
from backend_api import BackendApi


def test_spaced_inn_and_ogrn_candidates():
    # Synthetic EGRUL-format header (values are structurally valid but not real):
    sample_text = (
        "ОГРН 1 2 3 4 5 6 7 8 9 0 1 2 7\n"
        "ИНН 9 9 0 1 2 3 4 5 6 1\n"
        "КПП 9 9 0 1 0 1 0 0 1\n"
        "И Н Н 9 9 0 1 2 3 4 5 6 1\n"
        "О Г Р Н 1 2 3 4 5 6 7 8 9 0 1 2 7\n"
        "К П П 9 9 0 1 0 1 0 0 1\n"
    )

    candidates = list(iter_candidates(sample_text))
    kinds_and_values = [(kind, "".join(c for c in val if c.isdigit())) for kind, val, _, _ in candidates]

    assert ("OGRN", "1234567890127") in kinds_and_values
    assert ("INN", "9901234561") in kinds_and_values
    assert ("KPP", "990101001") in kinds_and_values


def test_canonical_key_normalizes_spaced_identifiers():
    k1 = canonical_key("1 2 3 4 5 6 7 8 9 0 1 2 7", "OGRN")
    k2 = canonical_key("1234567890127", "OGRN")
    assert k1 == k2 == ("OGRN", "1234567890127")

    inn1 = canonical_key("9 9 0 1 2 3 4 5 6 1", "INN")
    inn2 = canonical_key("9901234561", "INN")
    assert inn1 == inn2 == ("INN", "9901234561")

    kpp1 = canonical_key("9 9 0 1 0 1 0 0 1", "KPP")
    kpp2 = canonical_key("990101001", "KPP")
    assert kpp1 == kpp2 == ("KPP", "990101001")


def test_anonymize_spaced_egrul_header():
    api = BackendApi()
    header_text = (
        "ОГРН 1 2 3 4 5 6 7 8 9 0 1 2 7\n"
        "ИНН 9 9 0 1 2 3 4 5 6 1\n"
    )

    result, replacements, _ = api.anonymize_text_pullenti(header_text)
    assert "1 2 3 4 5 6 7 8 9 0 1 2 7" not in result
    assert "9 9 0 1 2 3 4 5 6 1" not in result
    assert "ОГРН [ОГРН" in result
    assert "ИНН [ИНН" in result


def test_consistent_placeholder_across_compact_and_spaced():
    api = BackendApi()
    text = (
        "В шапке документа: ОГРН 1 2 3 4 5 6 7 8 9 0 1 2 7, ИНН 9 9 0 1 2 3 4 5 6 1.\n"
        "В реквизитах: ОГРН: 1234567890127, ИНН: 9901234561.\n"
    )

    entity_seen = {}
    mapping_dict = {}
    anon_text, _, _ = api.anonymize_text_pullenti(
        text,
        entity_seen=entity_seen,
        mapping_dict=mapping_dict,
    )

    # Both occurrences should map to the exact same placeholder token
    # e.g., [ОГРН_1] and [ИНН_1]
    assert "[ОГРН_1]" in anon_text
    assert "[ОГРН_2]" not in anon_text
    assert "9901234561" not in anon_text


def test_anonymize_ocr_opf_and_compound_addresses():
    api = BackendApi()
    text = (
        "Заказчик Исполнитель\n"
        "OOU «Альфа групп» ООО «БетаСервис»\n"
        "199034, г. Санкт-Петербург, ул. Синтетическая, д. 5, корпус 1, квартира 3 Адрес: 620000, г. Екатеринбург, ул. Тестовая, д. 10, корп. 3, пом.100,101,102/этаж 2\n"
    )

    anon_text, _, _ = api.anonymize_text_pullenti(text)
    assert "Альфа групп" not in anon_text
    assert "БетаСервис" not in anon_text
    assert "Синтетическая" not in anon_text
    assert "Тестовая" not in anon_text
    assert "д. 5" not in anon_text
    assert "пом.100" not in anon_text
    assert "этаж 2" not in anon_text
    assert "OOU «[Наименование" in anon_text or "ООО «[Наименование" in anon_text
    assert "ООО «[Наименование" in anon_text


