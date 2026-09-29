# -*- coding: utf-8 -*-
"""Regression coverage for field-semantic identifiers found in next10 review."""

from pullenti_legal.analyzer import _iter_candidate_details


def _candidates(text):
    return [
        (kind, value, text[start:end])
        for kind, value, _evidence_start, _evidence_end, start, end
        in _iter_candidate_details(text)
    ]


def test_multiline_requisites_and_compound_inn_kpp_are_independent():
    text = (
        "ИНН/КПП 7702012345 / 771701001\n"
        "ОГРН 1197746012342\n"
        "БИК 044525256\n"
        "р/с: 40702810500000015075\n"
        "к/с: 30101810200000000823"
    )

    found = _candidates(text)
    assert [(kind, surface) for kind, _value, surface in found] == [
        ("KPP", "771701001"),
        ("INN", "7702012345"),
        ("OGRN", "1197746012342"),
        ("BIK", "044525256"),
        ("RU_ACCOUNT", "40702810500000015075"),
        ("RU_CORR_ACCOUNT", "30101810200000000823"),
    ]


def test_grouped_accounts_preserve_their_field_semantics():
    text = (
        "Номер расчетного счета: 40702 810 500 000 015 075\n"
        "Корреспондентский счет банка\t\n30101 810 2 0000 0000823"
    )
    found = _candidates(text)

    assert [(kind, value) for kind, value, _surface in found] == [
        ("RU_ACCOUNT", "40702810500000015075"),
        ("RU_CORR_ACCOUNT", "30101810200000000823"),
    ]


def test_birth_date_allows_spacing_after_date_separators():
    found = _candidates("Дата рождения: 12.07. 1989")

    assert [(kind, surface) for kind, _value, surface in found] == [
        ("BIRTH_DATE", "12.07. 1989"),
    ]


def test_explicit_snils_is_sensitive_even_when_teaching_checksum_is_invalid():
    found = _candidates("СНИЛС 123-456-789-01")

    assert [(kind, surface) for kind, _value, surface in found] == [
        ("SNILS", "123-456-789-01"),
    ]


def test_phone_accepts_nbsp_and_compact_digits_and_website():
    text = "Телефон: +7\u00a0(999)\u00a01234567; запасной 89991234567; www.example-realty.ru"
    found = _candidates(text)

    assert [(kind, surface) for kind, _value, surface in found] == [
        ("PHONE_NUMBER", "+7\u00a0(999)\u00a01234567"),
        ("PHONE_NUMBER", "89991234567"),
        ("WEBSITE", "www.example-realty.ru"),
    ]


def test_salary_heading_is_not_a_job_title_value():
    found = _candidates("Желаемая должность и зарплата")

    assert all(kind != "JOB_TITLE" for kind, _value, _surface in found)
    assert any(kind == "STRUCTURAL_HEADER" for kind, _value, _surface in found)


def test_public_law_education_term_is_structural_not_personal_education():
    text = "Российская Федерация, субъект Российской Федерации, муниципальное образование"
    found = _candidates(text)

    assert all(kind != "EDUCATION" for kind, _value, _surface in found)
    assert {kind for kind, _value, _surface in found} == {"STRUCTURAL_PUBLIC_LAW"}


def test_resume_service_headings_are_structural_and_not_sensitive_values():
    text = "Навыки\nЗнание языков\nОпыт вождения\nПрава категории B\nРусский — Родной"
    found = _candidates(text)

    assert all(kind not in {"JOB_TITLE", "EDUCATION"} for kind, _value, _surface in found)
    assert [kind for kind, _value, _surface in found] == [
        "STRUCTURAL_HEADER",
        "STRUCTURAL_HEADER",
        "STRUCTURAL_HEADER",
        "STRUCTURAL_HEADER",
        "STRUCTURAL_HEADER",
    ]
