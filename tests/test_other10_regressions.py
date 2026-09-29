# -*- coding: utf-8 -*-

import pytest

from backend_api import BackendApi


@pytest.fixture(scope="module")
def api():
    return BackendApi()


def _clean(api, text):
    return api.anonymize_text_pullenti(
        text, set(), set(), mapping_dict={}, entity_seen={}
    )[0]


def test_parenthetical_org_alias_keeps_structure(api):
    source = 'Общество с ограниченной ответственностью «Торговый ДомСервис» (ООО «ТДС»), далее'
    cleaned = _clean(api, source)
    assert cleaned == 'Общество с ограниченной ответственностью «[Наименование_1]» (ООО «[Наименование_2]»), далее'


def test_public_institution_parenthetical_alias_keeps_structure(api):
    source = 'Государственного автономного учреждения культуры города Москвы «МОСГОРТУР» (ГАУК «МОСГОРТУР»), в лице'
    cleaned = _clean(api, source)
    assert cleaned == 'Государственного автономного учреждения культуры города Москвы «[Наименование_1]» (ГАУК «[Наименование_1]»), в лице'


def test_multiline_labelled_address_and_bank_account(api):
    source = (
        'Юридический адрес: 107140, Российская Федерация,\n'
        'г. Москва, Комсомольская площадь,\n'
        'дом 4 А, строение 1, пом.3, ком.14,\n'
        'этаж 2\nПочтовый адрес: 107140, г. Москва, а/я 73\n'
        'Р/сч.:40702810402720009366'
    )
    cleaned = _clean(api, source)
    assert cleaned == 'Юридический адрес: [Адрес_1]\nПочтовый адрес: [Адрес_2]\nР/сч.:[Р/с_1]'


def test_address_suffix_office_apartment_and_workplace(api):
    source = 'Адрес: 125009, г. Москва, ул. Тверская, дом 10, оф кв. 15; ИНН: 7701234567'
    cleaned = _clean(api, source)
    assert cleaned == 'Адрес: [Адрес_1]; ИНН: [ИНН_1]'
    source = 'адрес: 143900, Московская область, г. Балашиха, ул. Советская, д. 1, помещение I, рабочее место А, ИНН: 5001012345'
    cleaned = _clean(api, source)
    assert cleaned == 'адрес: [Адрес_1], ИНН: [ИНН_1]'


def test_full_address_after_field_marker_is_atomic(api):
    source = 'по адресу: Российская Федерация, Московская область, Городской округ Красногорск, город Красногорск, бульвар Космонавтов, д. 5 (далее)'
    assert _clean(api, source) == 'по адресу: [Адрес_1] (далее)'


def test_compact_private_dates_and_contract_number(api):
    source = 'Договор от "2"октября 2024г. № ПД-291512'
    assert _clean(api, source) == 'Договор от [Дата_1] № [НомерДоговора_1]'
    assert _clean(api, '"  " октября 2024 г., Москва') == '[Дата_1], Москва'


def test_passport_division_code_is_masked(api):
    source = 'паспорт серии 4515 № 123456, код подразделения 770-001'
    cleaned = _clean(api, source)
    assert '770-001' not in cleaned
    assert '[СерияПаспорта_' in cleaned
    assert '[НомерПаспорта_' in cleaned
    assert '[КодПодразделения_' in cleaned


def test_common_court_words_are_not_organizations(api):
    assert _clean(api, 'интересов в судах') == 'интересов в судах'
    assert _clean(api, 'что судом ранее уже признаны требования') == 'что судом ранее уже признаны требования'


def test_bank_branch_structure_and_bare_template_opf_are_preserved(api):
    source = 'в Филиале «Центральный» банка ВТБ (ПАО)'
    assert _clean(api, source) == 'в Филиале «Центральный» банка [Наименование_1] (ПАО)'
    assert _clean(api, 'юридический адрес (для ООО) …') == 'юридический адрес (для ООО) …'
    template = 'Общество с ограниченной ответственностью «….»/Индивидуальный предприниматель (ФИО)'
    assert _clean(api, template) == template


def test_federal_chamber_is_preserved(api):
    source = "исследование проведено Федеральной палатой РФ"
    assert _clean(api, source) == source


def test_generic_bank_field_is_not_an_organization(api):
    source = "Банк\nГУ БАНКА РОССИИ ПО ЦФО//УФК ПО Г. МОСКВЕ"
    assert _clean(api, source) == source


def test_passport_ocr_fail_closed_for_mrz_names_and_residence(api):
    source = (
        "РОССИЙСКАЯ ФЕДЕРАЦИЯ\nПаспорт выдан ОТДЕЛОМ УФМС РОССИИ\n"
        "Код подразделення 770-001 16 длиный OS 123456\nЛичная подпись\n"
        "ИВАНОВ\nРФ\nИВАН\nОтчество ИВАНОВИЧ\nПол МУЖ. Дата рождения 01.01.1990\n"
        "PNRUSIVANOV<<IVAN<IVANOVICH<<<<<<<<<<<<<\nPO4515123456RUS9001015M<<<<<<<<<<<\n"
        "МЕСТО ЖИТЕЛЬСТВА\nул. Тверская, дом 10, квартира 15\n45 15 123456"
    )
    cleaned = _clean(api, source)
    for secret in ("770-001", "123456", "ИВАНОВ", "ИВАН", "ИВАНОВИЧ", "PNRUS", "Тверская", "45 15 123456"):
        assert secret not in cleaned
    back_only = "МЕСТО ЖИТЕЛЬСТВА\n22 октября 1990 г.\n770-001\n45 15 123456"
    back_cleaned = _clean(api, back_only)
    assert "770-001" not in back_cleaned
    assert "45 15 123456" not in back_cleaned


def test_flattened_pdf_identifiers_are_masked_without_intact_labels(api):
    source = (
        "Сч. № 30101810200000000593 БИК 044525593 ИНН 1304015105; "
        "кад. № 77:02:0004001:11852; Идентификатор: 8cc6e43d-df62-4601-97b9-9c41417ed858; "
        "Сертификат 02444399009BB3F29343474E0034AE4FB6; "
        "платежное поручение № 28511; №221/4-Р-50928-25/236469; 06 окт. 2025, 13:07"
    )
    cleaned = _clean(api, source)
    for secret in (
        "30101810200000000593", "044525593", "1304015105", "77:02:0004001:11852",
        "8cc6e43d-df62-4601-97b9-9c41417ed858", "02444399009BB3F29343474E0034AE4FB6",
        "28511", "221/4-Р-50928-25/236469", "06 окт. 2025, 13:07",
    ):
        assert secret not in cleaned


def test_private_reference_date_is_not_misclassified_as_law_date(api):
    source = (
        "В соответствии с Федеральным законом от 02.05.2006 № 59-ФЗ "
        "Ваше обращение от 03.12.2025 поступило через приемную"
    )
    cleaned = _clean(api, source)
    assert "02.05.2006" in cleaned
    assert "03.12.2025" not in cleaned


def test_flattened_bank_statement_masks_split_account_and_operation_ids(api):
    source = (
        "Выписка по счёту\n40802 810 7\n02620 За период c 01.01.2026\n"
        "ИНН владельца\n1304015105\n47БИК 044525593\n"
        "19.05.2026 899788 3 319,00 ООО Контрагент\n29.04.2026 19 31 000,00 ИП Иванов"
    )
    cleaned = _clean(api, source)
    for secret in ("40802 810 7", "02620", "1304015105", "044525593", "899788", " 19 31"):
        assert secret not in cleaned
