# -*- coding: utf-8 -*-

import pytest
from docx import Document

from backend_api import BackendApi


@pytest.fixture(scope="module")
def api():
    return BackendApi()


def _clean(api, text, mapping=None, seen=None):
    return api.anonymize_text_pullenti(
        text,
        set(),
        set(),
        mapping_dict={} if mapping is None else mapping,
        entity_seen={} if seen is None else seen,
    )[0]


def test_numeric_sections_and_legal_dates_are_not_websites(api):
    source = (
        "1.2. Общество создано по состоянию на 31.05.2003 г. "
        "Постановлением Госкомстата России от 05.01.2004 N 1."
    )

    assert _clean(api, source) == source
    assert _clean(api, "Сайт: www.example-realty.ru") == "Сайт: [Сайт_1]"


def test_historical_legal_chronology_is_preserved_but_private_contract_date_is_masked(api):
    history = (
        "29 декабря 2023 г. истекли полномочия исполнительного директора, "
        "в связи с чем Советом директоров принято решение о назначении нового руководителя; "
        "с 2 июля 2018 г. он находился на должности."
    )
    official = (
        "После обсуждения в связи с Директивой\n"
        "№ ДГИ-ДИ-672/23 от 1 декабря 2023 года Совет директоров принял решение."
    )

    assert _clean(api, history) == history
    assert _clean(api, official) == official
    assert _clean(api, "договор аренды от 7 апреля 2024 г. между сторонами") == (
        "договор аренды от [Дата_1] между сторонами"
    )


def test_org_forms_quotes_conjunctions_and_governance_context_are_preserved(api):
    assert _clean(api, 'Заказчик: ГАУК "МОСГОРТУР"') == (
        'Заказчик: ГАУК "[Наименование_1]"'
    )
    assert _clean(api, "ЕАО «СОК ВОСТОК»") == "ЕАО «[Наименование_1]»"
    source = (
        'между ЕАО "СОК "Восток" и ЕООО "ЛОГИКА И СИСТЕМЫ"'
    )
    assert _clean(api, source) == (
        'между ЕАО "[Наименование_1]" и ЕООО "[Наименование_2]"'
    )
    source = (
        "заседания Совета директоров Единоличного акционерного общества "
        "«Санаторно-оздоровительный комплекс «Восток»"
    )
    cleaned = _clean(api, source)
    assert cleaned.startswith(
        "заседания Совета директоров Единоличного акционерного общества «"
    )
    assert cleaned.endswith("[Наименование_1]»")


@pytest.mark.parametrize(
    "source,tail",
    [
        (
            "Иванов А.Н. – начальник Управления корпоративных отношений "
            "Департамента городского имущества города Москвы;",
            " – начальник Управления корпоративных отношений Департамента "
            "городского имущества города Москвы;",
        ),
        (
            "Григорьев Р.Г. – исполнительный директор ЕАО «СОК «Восток»;",
            " – исполнительный директор ЕАО «[Наименование_1]»;",
        ),
        (
            "Смирнов Д.В. – заместитель генерального директора ГАУК «МОСГОРТУР»;",
            " – заместитель генерального директора ГАУК «[Наименование_1]»;",
        ),
    ],
)
def test_person_span_does_not_swallow_job_or_public_organization(api, source, tail):
    assert _clean(api, source) == "[ФИО_1]" + tail


def test_foreign_and_initial_person_forms_are_complete_and_linked(api):
    assert _clean(api, "Димитрова Петра Димитрова") == "[ФИО_1]"
    assert _clean(api, "От имени Общества Н.Г.Новиков заключил сделки") == (
        "От имени Общества [ФИО_1] заключил сделки"
    )
    assert _clean(api, "Уважаемый [ФИО_1] Смирнов") == "Уважаемый [ФИО_1]"

    mapping = {}
    seen = {}
    assert _clean(
        api,
        "Ахметов Рустем Муратович / Ахметов.Р. М.",
        mapping,
        seen,
    ) == "[ФИО_1] / [ФИО_1]"


def test_address_boundary_and_public_geography_policy(api):
    assert _clean(
        api,
        "Адрес:107497, г. Москва, ул. Примерная, дом 44к1, кв 272",
    ) == "Адрес:[Адрес_1]"
    assert _clean(
        api,
        "129000, г Москва, пр-кт Мира, д. 10 К. 1, э 8 ком 6 оф а3с",
    ) == "[Адрес_1]"
    assert _clean(api, "Проживает: Москва, м. Юго-Западная") == (
        "Проживает: Москва, м. Юго-Западная"
    )
    territory = "в любую точку г. Москвы, Московской области и России"
    assert _clean(api, territory) == territory


def test_fio_field_instruction_is_not_a_person(api):
    source = "юр. адрес правообладателя, вкл. ФИО ген. директора"
    assert _clean(api, source) == source


def test_multiline_requisites_cell_is_processed_as_one_semantic_unit(api):
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    lines = [
        "ООО «Ромашка»",
        "Адрес:107497, г. Москва, ул. Примерная, дом 44к1, кв 272",
        "ИНН/КПП 9901234561 / 990101001",
        "ОГРН 1234567890127",
        "р/с: 4080 2810 9000 0000 1234",
        "к/с: 3010 1810 4000 0000 0225",
        "БИК 044525256",
        "Дата рождения: 12.07. 1989",
        "СНИЛС 123-456-789-01",
    ]
    cell.paragraphs[0].text = lines[0]
    for line in lines[1:]:
        cell.add_paragraph(line)

    api.clean_document(doc, set(), set(), mapping_dict={}, entity_seen={})
    cleaned = cell.text

    for secret in (
        "Ромашка", "107497", "7720855429", "772001001", "1217700485947",
        "4080 2810 9000 0000 1234", "3010 1810 4000 0000 0225",
        "044525256", "12.07. 1989", "123-456-789-01",
    ):
        assert secret not in cleaned
    for placeholder in (
        "[Наименование_1]", "[Адрес_1]", "[ИНН_1]", "[КПП_1]", "[ОГРН_1]",
        "[Р/с_1]", "[К/с_1]", "[БИК_1]", "[Дата_1]", "[СНИЛС_1]",
    ):
        assert placeholder in cleaned
