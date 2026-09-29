# -*- coding: utf-8 -*-
"""Synthetic coverage for rare legal fields missing from the public corpus."""

from legal_pullenti import initialize_ner, iter_pullenti_spans
from pullenti.ner.ProcessorService import ProcessorService
from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis


def test_all_legal_kinds_are_native_pullenti_referents_with_exact_offsets():
    initialize_ner()
    processor = ProcessorService.create_processor()
    samples = {
        "INN": ("ИНН № 9901234561", "9901234561"),
        "KPP": ("КПП 990101001", "990101001"),
        "OGRN": ("ОГРН 1234567890127", "1234567890127"),
        "OGRNIP": ("ОГРНИП 304000000000007", "304000000000007"),
        "BIK": ("БИК 049999999", "049999999"),
        "SNILS": ("СНИЛС 112-233-445 95", "112-233-445 95"),
        "PASSPORT_SERIES": ("серия паспорта 45 08", "45 08"),
        "PASSPORT_NUMBER": ("номер паспорта 123456", "123456"),
        "RU_ACCOUNT": ("расчетный счет N 40702810900000000001", "40702810900000000001"),
        "RU_CORR_ACCOUNT": ("корреспондентский счет 30101810400000000000", "30101810400000000000"),
    }
    text = "; ".join(marker for marker, _value in samples.values()) + "."
    result = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(iter_pullenti_spans(result, text))

    legal = {span.label: span for span in spans if span.label in samples}
    assert set(legal) == set(samples)
    for label, (_marker, surface) in samples.items():
        span = legal[label]
        assert span.text == surface
        assert text[span.start:span.end] == surface


def test_strong_labels_allow_example_inn_and_snils_but_ogrn_stays_checksum_bound():
    initialize_ner()
    processor = ProcessorService.create_processor()
    text = "ИНН 9901234568; ОГРН 1234567890124; СНИЛС 112-233-445 96"
    result = processor.process(SourceOfAnalysis(text), None, None)
    labels = {span.label for span in iter_pullenti_spans(result, text)}

    assert "INN" in labels
    assert "OGRN" not in labels
    assert "SNILS" in labels
