# -*- coding: utf-8 -*-
from legal_pullenti import (
    apply_replacements,
    initialize_ner,
    iter_pullenti_spans,
    refine_composite_spans,
    select_non_overlapping,
    span_from_occurrence,
)


class FakeOccurrence:
    def __init__(self, source, begin, end, exclusive=False):
        self.source = source
        self.begin_char = begin
        self.end_char = end
        self.exclusive = exclusive

    def get_text(self):
        end = self.end_char if self.exclusive else self.end_char + 1
        return self.source[self.begin_char:end]


class FakeReferent:
    def __init__(self, type_name, occurrences):
        self.type_name = type_name
        self.occurrence = occurrences


class FakeResult:
    def __init__(self, entities):
        self.entities = entities


def test_pullenti_inclusive_end_is_converted_to_python_span():
    text = "С Ивановым Иваном Ивановичем заключен договор."
    surface = "Ивановым Иваном Ивановичем"
    start = text.index(surface)
    occurrence = FakeOccurrence(text, start, start + len(surface) - 1)

    span = span_from_occurrence(text, occurrence, "PER")

    assert span.text == surface
    assert text[span.start:span.end] == surface
    assert text[span.end] == " "


def test_adapter_tolerates_wrapper_with_exclusive_end():
    text = "ООО «Ромашка» заключило договор."
    surface = "ООО «Ромашка»"
    occurrence = FakeOccurrence(text, 0, len(surface), exclusive=True)

    span = span_from_occurrence(text, occurrence, "ORG")

    assert span.text == surface
    assert span.end == len(surface)


def test_supported_referents_keep_exact_source_positions():
    text = "Иванов работает в ООО «Ромашка»."
    person = "Иванов"
    org = "ООО «Ромашка»"
    result = FakeResult([
        FakeReferent("PERSON", [FakeOccurrence(text, 0, len(person) - 1)]),
        FakeReferent("ORGANIZATION", [FakeOccurrence(text, text.index(org), text.index(org) + len(org) - 1)]),
        FakeReferent("DATE", [FakeOccurrence(text, 0, 1)]),
    ])

    spans = list(iter_pullenti_spans(result, text))

    assert [(span.text, span.label) for span in spans] == [(person, "PER"), (org, "ORG")]


def test_position_replacement_does_not_delete_suffix_or_quote():
    text = "ООО «Ромашка» заключило договор."
    end = text.index(" заключило")

    result, count = apply_replacements(text, [(0, end, "[Наименование]")])

    assert result == "[Наименование] заключило договор."
    assert count == 1


def test_adjacent_people_are_not_collapsed_into_one_placeholder():
    text = "Иванов, Петров подписали акт."
    result, count = apply_replacements(text, [
        (0, len("Иванов"), "[ФИО_1]"),
        (text.index("Петров"), text.index("Петров") + len("Петров"), "[ФИО_2]"),
    ])

    assert result == "[ФИО_1], [ФИО_2] подписали акт."
    assert count == 2


def test_letters_after_placeholder_are_preserved():
    text = "Иванов написал заявление."
    result, _ = apply_replacements(text, [(0, len("Иванов"), "[ФИО]")])

    assert result == "[ФИО] написал заявление."


def test_overlap_resolution_uses_only_returned_coordinates():
    text = "ООО «Ромашка»"
    short = span_from_occurrence(text, FakeOccurrence(text, 5, 11), "ORG")
    full = span_from_occurrence(text, FakeOccurrence(text, 0, 12), "ORG")

    assert select_non_overlapping([short, full]) == [full]


def test_composite_person_is_trimmed_at_nested_pullenti_contact():
    text = "Иванов Иван Иванович, телефон +7 999 123-45-67"
    person = span_from_occurrence(
        text, FakeOccurrence(text, 0, len(text) - 1), "PER"
    )
    phone_start = text.index("телефон")
    phone = span_from_occurrence(
        text, FakeOccurrence(text, phone_start, len(text) - 1), "PHONE_NUMBER"
    )

    refined = refine_composite_spans(text, [person, phone])

    assert refined[0].text == "Иванов Иван Иванович"
    assert refined[1] == phone


def test_address_with_compound_office_and_parentheses_is_fully_masked():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    text = "Адрес: 191025, РФ, г. Санкт-Петербург, Невский проспект, дом 104 Литера А, пом/оф40-Н(11)/416-5."
    res = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(refine_composite_spans(text, iter_pullenti_spans(res, text)))

    addr_spans = [s for s in spans if s.label == "ADDRESS"]
    assert len(addr_spans) == 1
    assert addr_spans[0].text == "191025, РФ, г. Санкт-Петербург, Невский проспект, дом 104 Литера А, пом/оф40-Н(11)/416-5"


def test_public_authority_order_and_date_are_preserved():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    text = "Приказом Федерального архивного агентства от 20 декабря 2019 г. № 236 утвержден перечень."
    res = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(refine_composite_spans(text, iter_pullenti_spans(res, text)))

    # Ни госорган, ни дата НПА, ни номер приказа не должны быть в spans
    assert not any(s.label in {"ORG", "DATE", "DOCUMENT_NUMBER", "CONTRACT_NUMBER"} for s in spans)


def test_152_fz_number_and_date_are_preserved_in_contract_sentence():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    text = "В целях исполнения настоящего Договора в соответствии с Федеральным законом № 152-ФЗ «О персональных данных» от 27.07.2006 г. стороны действуют совместно."
    res = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(refine_composite_spans(text, iter_pullenti_spans(res, text)))

    # Номер 152-ФЗ и дата 27.07.2006 не должны быть распознаны как реквизиты договора
    assert not any(s.label in {"CONTRACT_NUMBER", "DOCUMENT_NUMBER", "DATE"} for s in spans)


def test_consent_pii_category_listing_is_not_falsely_redacted():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    text = (
        "и место рождения, пол, гражданство, серия, номер паспорта, иные данные, указанные в паспорте; "
        "гражданство в настоящее время (при необходимости – гражданство при рождении);"
    )
    res = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(refine_composite_spans(text, iter_pullenti_spans(res, text)))

    assert not any(s.label in {"BIRTH_PLACE", "CITIZENSHIP"} for s in spans)


def test_document_number_does_not_jump_over_law_or_contract():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    # 1. Приказ не должен прыгать через Закон № 152-ФЗ
    text1 = "Во исполнение приказа Руководителя в соответствии с Федеральным законом № 152-ФЗ стороны начали аудит."
    res1 = processor.process(SourceOfAnalysis(text1), None, None)
    spans1 = list(refine_composite_spans(text1, iter_pullenti_spans(res1, text1)))
    assert not any(s.label in {"DOCUMENT_NUMBER", "CONTRACT_NUMBER"} for s in spans1)

    # 2. Приказ не должен захватывать номер Договора как свой собственный
    text2 = "На основании приказа Генерального директора и Договора аренды № 789/2024 стороны подписали акт."
    res2 = processor.process(SourceOfAnalysis(text2), None, None)
    spans2 = list(refine_composite_spans(text2, iter_pullenti_spans(res2, text2)))
    assert any(s.label == "CONTRACT_NUMBER" and s.text == "789/2024" for s in spans2)
    assert not any(s.label == "DOCUMENT_NUMBER" for s in spans2)


def test_booking_application_is_not_falsely_classified_as_patent_application():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    text_booking = "Заявка на бронирование № 12345678 от 10.05.2024 принята к исполнению."
    res_b = processor.process(SourceOfAnalysis(text_booking), None, None)
    spans_b = list(refine_composite_spans(text_booking, iter_pullenti_spans(res_b, text_booking)))
    assert not any(s.label == "PATENT_APPLICATION_NUMBER" for s in spans_b)

    text_patent = "Патентная заявка на изобретение № 2023123456 находится на экспертизе."
    res_p = processor.process(SourceOfAnalysis(text_patent), None, None)
    spans_p = list(refine_composite_spans(text_patent, iter_pullenti_spans(res_p, text_patent)))
    assert any(s.label == "PATENT_APPLICATION_NUMBER" and s.text == "2023123456" for s in spans_p)


def test_power_of_attorney_with_date_and_stopping():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    # С датой: должно распознать доверенность
    text1 = "Действующий на основании доверенности от 15.01.2024 № 45/Д Иванов И.И."
    res1 = processor.process(SourceOfAnalysis(text1), None, None)
    spans1 = list(refine_composite_spans(text1, iter_pullenti_spans(res1, text1)))
    assert any(s.label == "POWER_OF_ATTORNEY_NUMBER" and s.text == "45/Д" for s in spans1)

    # При наличии промежуточного договора: доверенность не прыгает на номер договора
    text2 = "На основании доверенности и Договора подряда № 999 стороны выполнили работы."
    res2 = processor.process(SourceOfAnalysis(text2), None, None)
    spans2 = list(refine_composite_spans(text2, iter_pullenti_spans(res2, text2)))
    assert any(s.label == "CONTRACT_NUMBER" and s.text == "999" for s in spans2)
    assert not any(s.label == "POWER_OF_ATTORNEY_NUMBER" for s in spans2)


def test_official_acts_not_redacted_as_document_or_contract():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    initialize_ner()
    processor = ProcessorService.create_processor()

    text = "Согласно Распоряжению Правительства РФ от 12.03.2020 № 512-р и Основание: 152-ФЗ нормы соблюдены."
    res = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(refine_composite_spans(text, iter_pullenti_spans(res, text)))
    assert not any(s.label in {"DOCUMENT_NUMBER", "CONTRACT_NUMBER"} for s in spans)

