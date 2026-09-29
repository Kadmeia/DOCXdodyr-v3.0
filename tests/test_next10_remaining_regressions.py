# -*- coding: utf-8 -*-

import re

import pytest

from backend_api import BackendApi
from pullenti_legal.analyzer import iter_candidates
from table_entity_context import build_table_contexts, infer_field_label, is_pure_field_label


@pytest.fixture(scope="module")
def api():
    return BackendApi()


def _clean(api, text):
    return api.anonymize_text_pullenti(
        text, set(), set(), mapping_dict={}, entity_seen={}
    )[0]


def test_official_treaty_and_refusal_dates_are_preserved(api):
    treaty = (
        '"Договор о Евразийском экономическом союзе" '
        '(Подписан в г. Астане 29.05.2014).'
    )
    refusal = "Отказ Агентства по регистрации Республики Болгария от 7 февраля 2024 г.;"

    assert _clean(api, treaty) == treaty
    assert _clean(api, refusal) == refusal
    assert _clean(api, "договор аренды, подписанный в Москве 29.05.2014") == (
        "договор аренды, подписанный в Москве [Дата_1]"
    )


def test_public_authority_inflections_are_preserved_but_private_names_are_not(api):
    source = "Приказом Росстандарта; редактор Роспатента"
    assert _clean(api, source) == source
    assert _clean(api, 'ООО «Росстандарт»') == 'ООО «[Наименование_1]»'


def test_compound_inn_kpp_table_label_keeps_both_semantics(api):
    rows = [["ИНН/КПП покупателя:", "7704747169 / 770401001"]]
    assert infer_field_label(rows[0][0]) == "ИНН/КПП"
    assert is_pure_field_label(rows[0][0])
    context = next(
        item for item in build_table_contexts(rows)
        if (item.row, item.column) == (0, 1)
    )
    assert context.label == "ИНН/КПП"
    assert api.anonymize_text_pullenti(
        rows[0][1], mapping_dict={}, entity_seen={}, semantic_context=context
    )[0] == "[ИНН_1] / [КПП_1]"


def test_explicit_invalid_inn_and_unlabelled_checksum_policy(api):
    assert _clean(api, "ИНН: 770123456789") == "ИНН: [ИНН_1]"
    assert list(iter_candidates("770123456789")) == []
    assert _clean(api, "7702012345") == "[ИНН_1]"
    assert _clean(api, "1197746012342") == "[ОГРН_1]"


def test_unlabelled_russian_phone_format_is_sensitive(api):
    source = "+7\u00a0(999)\u00a01234567 — предпочитаемый способ связи"
    assert _clean(api, source) == "[Телефон_1] — предпочитаемый способ связи"
    assert _clean(api, "40702810500000015075") == "40702810500000015075"


def test_institution_epithet_is_not_a_person_but_real_signature_is(api):
    faculty = "Юридический факультет имени М.М. Сперанского"
    school = "частной средней школы имени «Юрия Гагарина»"
    assert _clean(api, faculty) == faculty
    assert _clean(api, school) == "частной средней школы имени «[Наименование_1]»"
    assert _clean(api, "директор М.М. Сперанский") == "директор [ФИО_1]"


def test_initials_first_person_wins_over_preceding_geography(api):
    assert _clean(api, "города Москвы А.И. Смирнов") == "города Москвы [ФИО_1]"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('Название банка: ООО "Банк Точка"', 'Название банка: ООО "[Наименование_1]"'),
        ("Банк ПАО РОСБАНК", "Банк ПАО [Наименование_1]"),
        ("Банк АО «ТИНЬКОФФ БАНК»", "Банк АО «[Наименование_1]»"),
    ],
)
def test_bank_name_keeps_field_caption_opf_and_quotes(api, source, expected):
    assert _clean(api, source) == expected


def test_multiline_bank_field_caption_is_not_redacted(api):
    source = 'Наименование учреждения банка\nАО "АЛЬФА-БАНК"'
    assert _clean(api, source) == 'Наименование учреждения банка\nАО "[Наименование_1]"'


def test_descriptive_contract_number_is_masked_without_bare_number_false_positive(api):
    source = "Абонентский договор на юридическое обслуживание № 1106"
    assert _clean(api, source) == (
        "Абонентский договор на юридическое обслуживание № [НомерДоговора_1]"
    )
    assert _clean(api, "Приложение № 1106") == "Приложение № 1106"


def test_bare_domain_inside_system_name_is_still_a_website(api):
    source = "задача в системе «Kaiten.ru» (далее Система)"
    assert _clean(api, source) == "задача в системе «[Сайт_1]» (далее Система)"


def test_section_header_does_not_leak_education_into_unrelated_rows():
    rows = [
        ["Сентябрь 2024 — настоящее время", "", "АБСОЛЮТ, Группа"],
        ["Образование", "Образование", "Образование"],
        ["Неоконченное высшее", "Неоконченное высшее", "Неоконченное высшее"],
        ["2028", "2028", "Государственная академия"],
        ["Навыки", "Навыки", "Навыки"],
    ]
    contexts = {(item.row, item.column): item for item in build_table_contexts(rows)}
    assert contexts[(0, 0)].label is None
    assert contexts[(0, 2)].label is None
    assert contexts[(2, 0)].label == "Образование"
    assert contexts[(4, 0)].label is None


def test_unlabelled_postal_address_in_multiline_cell_keeps_index_and_tail(api):
    source = "Пример.\n123123, г. Москва, ул. Иванова, 12 кв. 123\nКомментарий"
    cleaned = _clean(api, source)
    assert cleaned == "Пример.\n[Адрес_1]\nКомментарий"


def test_explicit_full_location_and_address_is_one_span(api):
    source = (
        "Место нахождения и адрес Общества – Республика Болгария, область Варна, "
        "муниципалитет Аврен, с. Близнаци, Курортный комплекс Восток, "
        "Санаторно-оздоровительный комплекс «Восток», отель «Маяк»."
    )
    cleaned = _clean(api, source)
    assert cleaned == "Место нахождения и адрес Общества – [Адрес_1]."
    assert _clean(api, "Место нахождения: Москва") == "Место нахождения: Москва"


def test_malformed_quoted_organization_heading_is_redacted(api):
    source = "«САНАТОРНО-ОЗДОРОВИТЕЛЬНЫЙ КОМПЛЕКС «ВОСТОК»"
    cleaned = _clean(api, source)
    assert cleaned.startswith("«[Наименование_1]")
    assert "ВОСТОК" not in cleaned
    assert _clean(api, "гостиничный комплекс услуг") == "гостиничный комплекс услуг"


def test_institution_epithet_can_wrap_after_word_imeni(api):
    source = "частной средней школы имени \n«Юрия Гагарина»"
    assert _clean(api, source) == "частной средней школы имени \n«[Наименование_1]»"
