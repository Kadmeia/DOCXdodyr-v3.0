# -*- coding: utf-8 -*-
from legal_pullenti import iter_legal_identifier_spans, refine_composite_spans


def _found(text):
    return [(span.label, span.text, span.start, span.end) for span in iter_legal_identifier_spans(text)]


def test_valid_identifiers_keep_exact_positions():
    text = "ИНН 7707083893; СНИЛС 112-233-445 95; паспорт 45 08 123456"
    found = _found(text)

    assert [item[0] for item in found] == ["INN", "SNILS", "PASSPORT_SERIES", "PASSPORT_NUMBER"]
    assert all(text[start:end] == surface for _, surface, start, end in found)


def test_explicit_inn_label_is_strong_but_unlabelled_invalid_number_is_rejected():
    assert _found("ИНН 7707083894")[0][0] == "INN"
    assert _found("7707083894") == []


def test_accounts_require_explicit_pullenti_legal_marker():
    assert _found("Номер 40702810900000000001") == []
    assert _found("р/с 40702810900000000001")[0][0] == "RU_ACCOUNT"


def test_field_number_markers_seen_in_corpus_are_supported():
    assert _found("ИНН N 7707083893")[0][0] == "INN"
    assert _found("расчетный счет № 40702810900000000001")[0][0] == "RU_ACCOUNT"


def test_inflected_bank_account_markers_and_grouped_digits_are_supported():
    assert _found("Номер расчетного счета: 40702810500000015075")[0][0] == "RU_ACCOUNT"
    assert _found("По расчетному счету 40702 810 500 000 015 075")[0][0] == "RU_ACCOUNT"
    assert _found("Корреспондентского счета: 30101810200000000823")[0][0] == "RU_CORR_ACCOUNT"


def test_native_pullenti_cartridge_emits_exact_legal_referents():
    from legal_pullenti import initialize_ner, iter_pullenti_spans
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()
    text = "ООО «Ромашка», ИНН 7707083893, паспорт 45 08 123456."
    result = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(refine_composite_spans(text, iter_pullenti_spans(result, text)))

    # The adapter exposes only the sensitive name.  The legal form and quotes
    # remain outside the replacement span and therefore survive redaction.
    assert any(span.label == "ORG" and span.text == "Ромашка" for span in spans)
    assert any(span.label == "INN" and span.text == "7707083893" for span in spans)
    assert any(span.label == "PASSPORT_SERIES" and span.text == "45 08" for span in spans)
    assert any(span.label == "PASSPORT_NUMBER" and span.text == "123456" for span in spans)
    assert all(text[span.start:span.end] == span.text for span in spans)


def test_postal_index_not_masked_as_passport_number():
    """Почтовый индекс на странице паспорта не должен стать [НомерПаспорта]."""
    from legal_pullenti import iter_pullenti_spans
    text = "Паспорт выдан УФМС. Почтовый индекс: 125009. Место жительства: г. Москва"
    # Без Pullenti result, проверяем regex-часть
    spans = list(iter_pullenti_spans(None, text))
    passport_numbers = [s for s in spans if s.label == "PASSPORT_NUMBER"]
    assert not any("125009" in s.text for s in passport_numbers), \
        "Почтовый индекс ошибочно маскирован как PASSPORT_NUMBER"

