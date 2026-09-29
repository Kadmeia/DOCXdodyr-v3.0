# -*- coding: utf-8 -*-

from legal_pullenti import (
    EntitySpan,
    _DATE_TEXT_REGEX,
    _is_npa_date,
    _is_protected_legal_term,
    _is_public_or_excluded_org,
)


def _span(source, value, label="ORG"):
    start = source.index(value)
    return EntitySpan(value, start, start + len(value), label)


def test_date_pattern_does_not_consume_following_space():
    match = _DATE_TEXT_REGEX.search("Акт от 16.03.2009 N 1")
    assert match is not None
    assert match.group(0) == "16.03.2009"

    match = _DATE_TEXT_REGEX.search("Акт от 10 января 2025 года N 1")
    assert match is not None
    assert match.group(0) == "10 января 2025 года"


def test_legal_and_reference_dates_are_excluded_from_generic_date_masking():
    preserved = (
        "Решение Комиссии Таможенного союза от 16.08.2011 N 999",
        "Директивой органа от 1 декабря 2023 года",
        "Договор о Евразийском экономическом союзе (Подписан 29.05.2014)",
        "Протокол консервации: 31 декабря 2023 года",
        "по состоянию на 31.05.2003 г.",
        "КонсультантПлюс | Готовое решение | Актуально на 03.06.2025",
    )
    for source in preserved:
        date = _DATE_TEXT_REGEX.search(source)
        assert date is not None, source
        assert _is_npa_date(source, date.start()), source

    private = "Договор поставки от 17.07.2024"
    date = _DATE_TEXT_REGEX.search(private)
    assert date is not None
    assert not _is_npa_date(private, date.start())

    private_agreement = "Соглашение об оказании услуг от 17.07.2024"
    date = _DATE_TEXT_REGEX.search(private_agreement)
    assert date is not None
    assert not _is_npa_date(private_agreement, date.start())

    personal_in_protocol = "Протокол заседания; дата рождения: 17.07.1990"
    date = _DATE_TEXT_REGEX.search(personal_in_protocol)
    assert date is not None
    assert not _is_npa_date(personal_in_protocol, date.start())


def test_public_authorities_and_public_academy_are_not_private_orgs():
    public_names = (
        "Госстандарт СССР",
        "Росстандарт",
        "Госкомстат России",
        "Роспатент",
        "ОВД района",
        "Российская академия народного хозяйства и государственной службы при Президенте Российской Федерации",
    )
    for source in public_names:
        assert _is_public_or_excluded_org(source, source, 0, len(source), "ORG"), source


def test_public_law_formulae_and_structural_markers_are_protected():
    cases = (
        ("Российская Федерация, субъект Российской Федерации, муниципальное образование", "ORG"),
        ("Договор о Евразийском экономическом союзе", "ORG"),
        ('фонд «Резервный»', "ORG"),
        ("по адресу(-ам), указанному(-ым) в Приложении 1", "ADDRESS"),
    )
    for source, label in cases:
        assert _is_protected_legal_term(source, _span(source, source, label)), source


def test_private_date_remains_maskable_while_public_legal_date_is_protected():
    private = "Договор оказания услуг от 17 июля 2024 г."
    public = "Постановлением Госкомстата России от 05.01.2004 N 1"
    private_date = _DATE_TEXT_REGEX.search(private)
    public_date = _DATE_TEXT_REGEX.search(public)
    assert private_date and public_date
    assert not _is_npa_date(private, private_date.start())
    assert _is_npa_date(public, public_date.start())
