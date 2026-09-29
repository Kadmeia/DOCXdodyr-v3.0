# -*- coding: utf-8 -*-
"""Тесты для detection_engine: нормализация, правила, контрольные суммы и Conflict Resolver."""
import pytest
from detection_engine import (
    EntityCandidate,
    normalize_with_offsets,
    map_span_to_original,
    is_valid_snils,
    is_valid_card_luhn,
    is_valid_bik,
    is_valid_oms,
    is_valid_court_case,
    is_valid_passport_rf,
    build_deterministic_rules,
    ConflictResolver,
    UnifiedDetectionPipeline,
)


def test_normalization_with_offset_map():
    orig = "ООО   «Ромашка»\u00a0\u00a0–\u00a0ИНН  7701234567"
    norm, offset_map = normalize_with_offsets(orig)
    assert "  " not in norm
    assert "\u00a0" not in norm
    assert "-" in norm

    # Проверяем проекцию смещения слова 'ИНН'
    idx_norm = norm.find("ИНН")
    orig_start, orig_end = map_span_to_original(idx_norm, idx_norm + 3, offset_map, len(orig))
    assert orig[orig_start:orig_end] == "ИНН"

    # Проверяем проекцию ИНН
    inn_idx = norm.find("7701234567")
    o_s, o_e = map_span_to_original(inn_idx, inn_idx + 10, offset_map, len(orig))
    assert orig[o_s:o_e] == "7701234567"


def test_validators():
    # СНИЛС (реальный валидный СНИЛС)
    assert is_valid_snils("112-233-445 95")
    assert not is_valid_snils("112-233-445 00")

    # Банковская карта (Luhn)
    # Тестовый валидный номер Visa
    assert is_valid_card_luhn("4111 1111 1111 1111")
    # Невалидный
    assert not is_valid_card_luhn("4111 1111 1111 1112")
    assert not is_valid_card_luhn("0000 0000 0000 0000")

    # БИК РФ
    assert is_valid_bik("044525225")  # Сбербанк Москва
    assert not is_valid_bik("014525225")
    assert not is_valid_bik("040025225")

    # Судебные дела
    assert is_valid_court_case("А40-12345/2025")
    assert is_valid_court_case("2-1234/2024")
    assert not is_valid_court_case("152-ФЗ")
    assert not is_valid_court_case("ГОСТ 1234-2020")
    assert not is_valid_court_case("152/2006")
    assert not is_valid_court_case("44/2013")

    # Паспорт РФ
    assert is_valid_passport_rf("4510", "123456")
    assert not is_valid_passport_rf("0010", "123456")


def test_passport_negative_veto():
    rules = {r.name: r for r in build_deterministic_rules()}
    series_rule = rules["passport_series_rf"]
    number_rule = rules["passport_number_rf"]

    # 1. Положительный контекст
    valid_text = "Паспорт гражданина РФ серия 45 10 номер 123456 выдан ОВД"
    cands_series = series_rule.evaluate(valid_text)
    cands_number = number_rule.evaluate(valid_text)
    assert len(cands_series) == 1
    assert cands_series[0].entity_type == "PASSPORT_SERIES"
    assert cands_series[0].text == "45 10"
    assert cands_series[0].confidence >= 0.70

    assert len(cands_number) == 1
    assert cands_number[0].entity_type == "PASSPORT_NUMBER"
    assert cands_number[0].text == "123456"
    assert cands_number[0].confidence >= 0.70

    # 2. Отрицательный контекст ("паспорт проекта") -> должен быть заблокирован
    veto_text = "Паспорт проекта № 4510 123456 утверждён приказом"
    assert len(series_rule.evaluate(veto_text)) == 0
    assert len(number_rule.evaluate(veto_text)) == 0

    # 3. Отрицательный контекст ("технический паспорт") -> заблокирован
    veto_text2 = "Технический паспорт 4510 123456 на здание"
    assert len(series_rule.evaluate(veto_text2)) == 0
    assert len(number_rule.evaluate(veto_text2)) == 0


def test_iban_and_swift_rules():
    rules = {r.name: r for r in build_deterministic_rules()}
    assert "iban_international" in rules
    assert "swift_bic" in rules


def test_conflict_resolver_exact_match():
    resolver = ConflictResolver()
    c1 = EntityCandidate(10, 20, "7701234567", "INN", "pullenti", confidence=0.90, priority=70)
    c2 = EntityCandidate(10, 20, "7701234567", "INN", "rule", confidence=0.95, priority=95)

    resolved = resolver.resolve([c1, c2])
    assert len(resolved) == 1
    res = resolved[0]
    assert res.source == "pullenti+rule"
    assert res.priority == 95
    assert res.confidence > 0.95


def test_conflict_resolver_nested_checksum_priority():
    resolver = ConflictResolver()
    # Широкий спан (например, Pullenti ошибочно захватил организацию вместе с ИНН)
    wide_org = EntityCandidate(0, 35, "ООО «Технологии» ИНН 7701234567", "ORG", "pullenti", confidence=0.75, priority=75)
    # Точный ИНН с валидной контрольной суммой
    inn = EntityCandidate(21, 31, "7701234567", "INN", "rule", confidence=0.98, priority=95)

    resolved = resolver.resolve([wide_org, inn])
    assert len(resolved) == 1
    assert resolved[0].entity_type == "INN"
    assert resolved[0].text == "7701234567"


def test_conflict_resolver_nested_validated_checksum():
    """Валидированный ИНН внутри широкого ADDRESS должен побеждать."""
    resolver = ConflictResolver()
    address = EntityCandidate(start=0, end=80, text="г. Москва, ул. Пушкина, д. 10, ИНН 7707049388, КПП 770701001",
                              entity_type="ADDRESS", source="pullenti", confidence=0.9, priority=70)
    inn = EntityCandidate(start=40, end=50, text="7707049388",
                          entity_type="INN", source="rule", confidence=0.95, priority=95,
                          metadata={"validator_passed": True})
    result = resolver.resolve([address, inn])
    types = {c.entity_type for c in result}
    assert "INN" in types, "ИНН должен быть извлечён из адреса"


def test_no_duplicate_confidence_inflation():
    """rule и rule_norm не должны удваивать confidence."""
    pipeline = UnifiedDetectionPipeline(enable_pullenti=False)
    text = "ИНН\u00a07707049388"  # неразрывный пробел
    results = pipeline.detect_candidates(text)
    inn_results = [c for c in results if c.entity_type == "INN"]
    assert len(inn_results) <= 1, f"Дубликат: {inn_results}"

