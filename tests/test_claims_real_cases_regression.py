# -*- coding: utf-8 -*-
"""Регрессионные сценарии на синтетических данных юридических документов."""

import pytest
from pullenti_legal.analyzer import iter_candidates
from ocr_backend import normalize_ocr_line
from legal_pullenti import _DATE_TEXT_REGEX


def test_two_digit_year_date():
    text = "22.05.26 г. Турагент Анастасия сообщила об увеличении срока"
    m = _DATE_TEXT_REGEX.search(text)
    assert m is not None
    assert m.group(0) == "22.05.26"


def test_role_based_single_name_person():
    text = "Турагент Анастасия установила срок возврата денежных средств"
    candidates = list(iter_candidates(text))
    persons = [c for c in candidates if c[0] == "PER"]
    assert any("АНАСТАСИЯ" in c[1] for c in persons), f"Expected АНАСТАСИЯ in persons, got {persons}"
    # Проверяем, что исходный текст среза именно 'Анастасия'
    match = next(c for c in persons if "АНАСТАСИЯ" in c[1])
    assert text[match[2]:match[3]] == "Анастасия"


def test_booking_numbers_list():
    text = "оформила возврат по трём заявкам: 300101, 300102, 300103; 08.04.2026 в сумме"
    candidates = list(iter_candidates(text))
    order_numbers = [c for c in candidates if c[0] == "ORDER_NUMBER"]
    nums = [c[1] for c in order_numbers]
    assert "300101" in nums
    assert "300102" in nums
    assert "300103" in nums


def test_ocr_email_space_and_double_at_normalization():
    line1 = "user 01@example.invalid/8 900 000 00 00"
    norm1 = normalize_ocr_line(line1)
    assert "user01@example.invalid" in norm1

    line2 = "sales @@example.invalid"
    norm2 = normalize_ocr_line(line2)
    assert "sales@example.invalid" in norm2


def test_corr_account_recognition():
    from legal_pullenti import initialize_ner, iter_pullenti_spans, select_non_overlapping
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "Назначение платежа: Корр. счет: 30101000000000000001"
    initialize_ner()
    processor = ProcessorService.create_processor()
    result = processor.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(result, text)))
    corr_spans = [s for s in spans if s.label == "RU_CORR_ACCOUNT"]
    assert len(corr_spans) == 1
    assert corr_spans[0].text == "30101000000000000001"


def test_v_adres_false_positive_rejected():
    text = (
        "В адрес ООО «Компания Пример» поступила претензия касательно возврата денежных "
        "средств по заявке № 400001. Туроператором данная претензия рассмотрена, в связи с чем "
        "сообщаем следующее: Адрес: 123456, г. Москва, улица Примерная, дом 1, корпус 1."
    )
    candidates = list(iter_candidates(text))
    addresses = [c for c in candidates if c[0] == "ADDRESS"]
    # Оборот "В адрес ООО..." не должен захватываться как адрес
    assert not any("ПОСТУПИЛА ПРЕТЕНЗИЯ" in c[1] for c in addresses)
    # Настоящий адрес должен быть найден
    assert any("УЛИЦА ПРИМЕРНАЯ" in c[1] for c in addresses)


def test_person_salutation_filters_common_nouns():
    text = "Уважаемый клиент! Туроператор сообщает Вам, что Уважаемый Иван Сергеевич рассмотрел вопрос."
    candidates = list(iter_candidates(text))
    salutations = [c for c in candidates if c[0] == "PERSON_SALUTATION"]
    # "клиент" не должен быть распознан как имя
    assert not any("КЛИЕНТ" in c[1] for c in salutations)
    # Настоящее имя должно быть распознано
    assert any("ИВАН СЕРГЕЕВИЧ" in c[1] for c in salutations)


def test_umvd_and_police_not_masked_as_org():
    from legal_pullenti import initialize_ner, iter_pullenti_spans, select_non_overlapping
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "Уведомление УМВД России по городу Новосибирску и отдел полиции № 3 «Заельцовский»."
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))
    org_spans = [s for s in spans if s.label == "ORG"]
    assert not any("УМВД" in s.text or "полиции" in s.text for s in org_spans)


def test_insurance_policy_rejects_common_words():
    from pullenti_legal.analyzer import iter_candidates
    text = "Страховой полис является официальным документом с памяткой."
    candidates = list(iter_candidates(text))
    policies = [c for c in candidates if c[0] == "INSURANCE_POLICY"]
    assert not any("является" in c[1].lower() or "памяткой" in c[1].lower() for c in policies)


def test_postal_code_not_phone_number():
    from legal_pullenti import initialize_ner, iter_pullenti_spans, select_non_overlapping
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "тел. (383) 200-00-01, факс (383)200-00-02 г. Новосибирск, 630000 №"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))
    phone_spans = [s for s in spans if s.label == "PHONE_NUMBER"]
    assert not any("630000" in s.text for s in phone_spans)


def test_person_span_excludes_trailing_address():
    from legal_pullenti import initialize_ner, iter_pullenti_spans, select_non_overlapping
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "Директору ООО «Компания Пример» Сидорову И.В.\nУл. Примерная, д.1, пом. 2"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))
    per_spans = [s for s in spans if s.label == "PER"]
    assert len(per_spans) == 1
    assert "Примерная" not in per_spans[0].text
    assert per_spans[0].text.strip() == "Сидорову И.В."


def test_slash_dates_are_date_not_document_number():
    from legal_pullenti import initialize_ner, iter_pullenti_spans, select_non_overlapping
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "Период действия: с 01/01/2026 по 31/12/2026."
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))
    doc_numbers = [s for s in spans if s.label == "DOCUMENT_NUMBER"]
    dates = [s for s in spans if s.label == "DATE"]
    assert not any("01/01/2026" in s.text or "31/12/2026" in s.text for s in doc_numbers)
    assert any("01/01/2026" in s.text for s in dates)
    assert any("31/12/2026" in s.text for s in dates)


def test_generic_hotel_word_excluded_from_org():
    from legal_pullenti import refine_org_span, EntitySpan, _is_public_or_excluded_org
    assert _is_public_or_excluded_org("отелях", "проживание в отелях", 13, 19, "ORG") is True
    span = EntitySpan("отелях Canareef Resort", 0, 22, "ORG")
    refined = refine_org_span(span, "отелях Canareef Resort")
    assert refined.text == "Canareef Resort"


def test_public_authority_cb_rf_and_uvd_ao():
    from legal_pullenti import _is_public_or_excluded_org
    # ЦБ РФ is a public authority per Rule 2.3
    assert _is_public_or_excluded_org("ЦБ РФ", "Лицензии ЦБ РФ СИ № 2496", 9, 14, "ORG") is True
    # УВД Центрального АО г. Тюмени is an interior ministry department, АО is okrug not company
    assert _is_public_or_excluded_org("УВД Центрального АО г. Тюмени", "ГОМ-6 УВД Центрального АО г. Тюмени", 6, 35, "ORG") is True


def test_insurance_table_numbers_not_phones():
    from legal_pullenti import initialize_ner, iter_pullenti_spans
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "Страховое покрытие: 1 000 1 000 1 000 рублей; лимит: 000 1 500 рублей; сумма 750 750 1 000 1"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(iter_pullenti_spans(res, text))
    phones = [s for s in spans if s.label == "PHONE_NUMBER"]
    assert len(phones) == 0, f"Expected 0 phones, got {phones}"


def test_common_nouns_and_destinations_not_org():
    from legal_pullenti import _is_public_or_excluded_org
    assert _is_public_or_excluded_org("авиакомпаниях", "во всех авиакомпаниях России", 8, 21, "ORG") is True
    assert _is_public_or_excluded_org("аэропорт", "доставить нас в аэропорт вовремя", 16, 24, "ORG") is True
    assert _is_public_or_excluded_org("аэропорт в г. Нечанге", "прибытия в аэропорт в г. Нечанге", 11, 32, "ORG") is True
    assert _is_public_or_excluded_org("Нечанг", "рейс Нечанг - Новосибирск", 5, 11, "ORG") is True
    assert _is_public_or_excluded_org("в п. Муйне", "отель в п. Муйне № vela", 6, 16, "ORG") is True


def test_hotel_inanimate_noun_not_person():
    from legal_pullenti import _is_plausible_person_surface
    assert _is_plausible_person_surface("Д.Т. Гостиница") is False
    assert _is_plausible_person_surface("д.Т. Гостиница") is False
    assert _is_plausible_person_surface("Сидоров Д.Ф.") is True


def test_person_job_title_trimmed():
    from legal_pullenti import initialize_ner, iter_pullenti_spans
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    text = "С уважением, Elena Sidorova \nСтарший менеджер по работе с ключевыми клиентами"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(iter_pullenti_spans(res, text))
    per_spans = [s for s in spans if s.label == "PER"]
    assert len(per_spans) == 1
    assert per_spans[0].text.strip() == "Elena Sidorova"
    assert "менеджер" not in per_spans[0].text


def test_role_words_not_single_token_person():
    from legal_pullenti import _GENERIC_PER_EXCLUSIONS
    from pullenti_legal.analyzer import _valid_person_field
    assert "турагента" in _GENERIC_PER_EXCLUSIONS
    assert "туроператора" in _GENERIC_PER_EXCLUSIONS
    assert "пассажира" in _GENERIC_PER_EXCLUSIONS
    assert _valid_person_field("Турагента") is False
    assert _valid_person_field("Туроператора") is False
    assert _valid_person_field("Пассажира") is False


def test_uvd_full_title_and_kkt_receipt_exclusion():
    from legal_pullenti import _is_public_or_excluded_org
    # Rule 2.3: do not mask public authorities including full police/UVD departments
    uvd_text = "ВЫДАН ОТДЕЛОМ ВНУТРЕННИХ ДЕЛ ЦЕНТРАЛЬНОГО ОКРУГА ГОРОДА ТЮМЕНИ"
    assert _is_public_or_excluded_org(
        "ОТДЕЛОМ ВНУТРЕННИХ ДЕЛ ЦЕНТРАЛЬНОГО ОКРУГА ГОРОДА ТЮМЕНИ",
        uvd_text,
        6,
        63,
        "ORG",
    ) is True

    # Cash register receipt artifacts must not be masked as ORG
    receipt_line = "д.Т. Гостиница ЗН ККТ 0000000000000001"
    assert _is_public_or_excluded_org(
        receipt_line,
        f"ЧЕК: {receipt_line}",
        5,
        5 + len(receipt_line),
        "ORG",
    ) is True


def test_english_terms_filtering_and_trimming():
    from legal_pullenti import (
        _is_plausible_person_surface,
        _is_public_or_excluded_org,
        refine_org_span,
        refine_person_span,
        EntitySpan,
    )
    # 1. Non-person Latin phrases rejected
    assert _is_plausible_person_surface("Deluxe Garden View") is False
    assert _is_plausible_person_surface("Key Account Manager") is False
    assert _is_plausible_person_surface("EURO Payments") is False
    assert _is_plausible_person_surface("USD Payments") is False

    # 2. Genuine Latin persons accepted
    assert _is_plausible_person_surface("Ivanov Maria") is True
    assert _is_plausible_person_surface("Petrov Artem") is True
    assert _is_plausible_person_surface("Elena Sidorova") is True

    # 3. Central Bank of Russia in English excluded as public authority
    assert _is_public_or_excluded_org(
        "Central Bank of the Russian Federation",
        "Licenses of the Central Bank of the Russian Federation",
        16,
        54,
        "ORG",
    ) is True

    # 4. Trailing location stripped from hotel
    span_hotel = EntitySpan("Soria Magestik в г. Нечанг", 0, 26, "ORG")
    refined_hotel = refine_org_span(span_hotel, "отель Soria Magestik в г. Нечанг")
    assert refined_hotel.text == "Soria Magestik"

    # 5. Salutation prefix stripped from foreign person
    span_person = EntitySpan("Ms, Sidorova Uliana", 0, 19, "FOREIGN_PER")
    refined_person = refine_person_span(span_person, "Ms, Sidorova Uliana")
    assert refined_person.text == "Sidorova Uliana"


def test_executor_initials_and_phone_number():
    from legal_pullenti import (
        _is_initial_fio_candidate,
        initialize_ner,
        iter_pullenti_spans,
        select_non_overlapping,
    )
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    # 1. Initials candidate with Slavic suffix accepted even if town homonym exists
    assert _is_initial_fio_candidate("Романова А.В.") is True
    assert _is_initial_fio_candidate("А.В. Романова") is True

    # 2. Complete line parsing
    text = "исп. Романова А.В. тел. +7 900 123-45-67"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))

    phones = [s for s in spans if s.label == "PHONE_NUMBER"]
    assert len(phones) == 1, f"Expected 1 phone, got {phones}"
    assert "123-45-67" in phones[0].text
    doc_nums = [s for s in spans if s.label == "DOCUMENT_NUMBER"]
    assert len(doc_nums) == 0, f"Expected 0 doc numbers, got {doc_nums}"


def test_bik_slash_after_inn_and_flight_numbers_not_org():
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        select_non_overlapping,
        _CARRIER_ORG_PATTERN,
    )
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    # 1. ИНН/БИК where BIK starts with 04 is recognized as BIK, not KPP
    text = "ИНН/БИК: 7700000000 / 040000000"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))

    biks = [s for s in spans if s.label == "BIK"]
    kpps = [s for s in spans if s.label == "KPP"]
    assert len(biks) == 1, f"Expected 1 BIK, got {biks}"
    assert biks[0].text == "040000000"
    assert len(kpps) == 0, f"Expected 0 KPP, got {kpps}"

    # 2. Flight numbers like U6737 after 'рейсами' are not matched as carrier org
    flight_text = "перелет Екатеринбург Дубай - Екатеринбург рейсами U6737/U6738."
    matches = list(_CARRIER_ORG_PATTERN.finditer(flight_text))
    assert len(matches) == 0, f"Expected 0 carrier matches, got {matches}"

def test_mixed_pdf_allows_short_ocr_page_in_multipage_doc(tmp_path, monkeypatch):
    import fitz
    import ocr_backend
    import pdf_convert

    pdf_file = tmp_path / "mixed_short.pdf"
    doc = fitz.open()
    # Стр 1: длинный нативный текст
    p1 = doc.new_page(width=300, height=200)
    p1.insert_text((30, 80), "First native page of the legal contract with all detailed terms and conditions")
    # Стр 2: растровая страница с коротким текстом логотипа (15 символов)
    p2 = doc.new_page(width=300, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 50), False)
    pix.clear_with(255)
    p2.insert_image(p2.rect, stream=pix.tobytes("png"))
    # Стр 3: длинный нативный текст
    p3 = doc.new_page(width=300, height=200)
    p3.insert_text((30, 80), "Third native page of the appendix to the contract with signatures and dates")
    doc.save(pdf_file)
    doc.close()

    class FakeShortBackend:
        label = "Fake Short OCR"
        def recognize_regions(self, image_bytes):
            return [ocr_backend.OCRTextRegion("GLOBAL TOUR 10", bbox=(0.1, 0.1, 0.5, 0.2), confidence=0.95)]

    fake = FakeShortBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (ocr_backend.OCRBackendInfo("mock-short", fake.label, "test"), fake),
    )
    pdf_convert._OCR_BACKEND_CACHE.clear()

    text, method, details = pdf_convert.pdf_to_text_auto_detailed(str(pdf_file), ocr_lang="mixed")
    assert method == "mixed"
    assert len(details.pages) == 3
    assert details.pages[1].source == "ocr"
    assert "GLOBAL TOUR 10" in details.pages[1].text


def test_dates_with_year_suffix_and_slash_and_generic_agency_not_org():
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        select_non_overlapping,
    )
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    text = """
у которого приобретался тур пакет 27.01.2026г.
В ОАЭ заявка № 300001 от 24.03.2026г.
Прошу Вас связаться с данным тур агенством ИП Сидорова А. В., для выяснение .
19/08/26
"""
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(iter_pullenti_spans(res, text)))

    dates = [s for s in spans if s.label == "DATE"]
    orgs = [s for s in spans if s.label in ("ORG", "FOREIGN_ORG")]
    pers = [s for s in spans if s.label == "PER"]

    date_texts = [d.text for d in dates]
    assert "27.01.2026" in date_texts
    assert "24.03.2026" in date_texts
    assert "19/08/26" in date_texts

    assert not any("агенств" in o.text.lower() for o in orgs), f"Unexpected orgs: {orgs}"
    assert any("Сидорова А. В." in p.text for p in pers), f"Missing Sidorova in pers: {pers}"


def test_order_number_without_num_sign_and_tourist_surname():
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        select_non_overlapping,
        refine_composite_spans,
    )
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    text = "перенести денежные средства с заявки 300001 за туристов Сидорова +1, на заявку 300002 Мальдивы за туристов Петрова +1"
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(refine_composite_spans(text, iter_pullenti_spans(res, text))))

    orders = [s for s in spans if s.label == "ORDER_NUMBER"]
    pers = [s for s in spans if s.label == "PER"]

    order_texts = [o.text for o in orders]
    assert "300001" in order_texts
    assert "300002" in order_texts

    per_texts = [p.text for p in pers]
    assert "Сидорова" in per_texts
    assert "Петрова" in per_texts


def test_scanned_claim_anomalies_filtered():
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        select_non_overlapping,
        refine_composite_spans,
    )
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis

    text = """
Мальдивы, Северный Ари Атолл, отель Sandies Bathala.
Вылет из г. Шарджа транспортной компанией Air Arabia.
Pax Name MR. NIKITA IVANOV External Payments PNR ABC123 ACCO_REFUND.
Customar Service Team Date: 05-07-2026-10:30.
"""
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(refine_composite_spans(text, iter_pullenti_spans(res, text))))

    labels = {s.text: s.label for s in spans}
    texts = [s.text for s in spans]

    # 1. Атолл не персона
    assert not any("атолл" in t.lower() and labels[t] == "PER" for t in texts)
    # 2. Шарджа не организация
    assert not any("шарджа" in t.lower() and labels[t] == "ORG" for t in texts)
    # 3. Pax Name, ACCO, Customar Service не попали в персоны / организации
    assert not any(t in {"Pax Name", "ACCO", "Customar Service", "G7"} for t in texts)
    # 4. Префикс 'транспортной компанией' отсечен, осталась Air Arabia
    assert "транспортной компанией Air Arabia" not in texts
    assert "Air Arabia" in texts
    # 5. Пассажир очищен от External Payments
    assert "NIKITA IVANOV" in texts
    assert "NIKITA IVANOV External" not in texts
    # 6. Дата 05-07-2026 не телефон
    phones = [s.text for s in spans if s.label == "PHONE_NUMBER"]
    assert not any("05-07-2026" in p for p in phones)


def test_scanned_claim_date_prefix_8_not_phone():
    text = "RL8 05-07-2026-10:30"
    candidates = list(iter_candidates(text))
    phones = [c for c in candidates if c[0] == "PHONE_NUMBER"]
    assert not phones, f"Expected no phone numbers, got {phones}"


def test_surname_ka_case_entities():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        refine_composite_spans,
        select_non_overlapping,
    )

    text = """•
ФиО
KOVALENKA ALEKSEI
KOVALENKA OLGA
KOVALENKA IULITA
KOVALENKA ANASTASIIA
- Код 2373989

Sunset Resort Cam Ranh 5* (Камрань) 24.06.2026 - 08.07.2026
Ocean travel не связались накануне вылета.

Коваленка А.А /
ФИО Заказчика
"""
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(refine_composite_spans(text, iter_pullenti_spans(res, text))))
    texts = [s.text for s in spans]
    labels = {s.text: s.label for s in spans}

    # 1. Все 4 пассажира распознаны отдельно
    assert "KOVALENKA ALEKSEI" in texts
    assert "KOVALENKA OLGA" in texts
    assert "KOVALENKA IULITA" in texts
    assert "KOVALENKA ANASTASIIA" in texts
    assert "KOVALENKA ALEKSEI\nKOVALENKA" not in texts

    # 2. Отель со звёздностью
    assert "Sunset Resort Cam Ranh" in texts
    assert "Камрань" not in texts[texts.index("Sunset Resort Cam Ranh")]

    # 3. Туристический оператор
    assert "Ocean travel" in texts

    # 4. ФИО с инициалами без последней точки и фамилией на -ка
    assert "Коваленка А.А" in texts
    assert labels["Коваленка А.А"] == "PER"


def test_power_of_attorney_case_entities():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        refine_composite_spans,
        select_non_overlapping,
    )
    from pullenti_legal.analyzer import iter_candidates

    text = """
Многофункциональном центре предоставления государственных и муниципальных услуг, в отношениях необходимых документов и справок, а также Акционерном обществе «Почта России».
Корр. счет: 30101000000000000001 и счет 30101000000000000002.
гр. Иванова Мария Сергеевна, зарегистрирована по адресу: г. Москва, ул. Примерная, дом 1, корп. 1, кв. 2,
настоящей доверенностью уполномочиваю гр. Петрова Петра Петровича, место рождения: гор. Москва.
заключила с ИП Сидорова Анной
Ивановной Туристической компанией «Aura Tour» (далее - Агент).
"""
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(text), None, None)
    spans = list(select_non_overlapping(refine_composite_spans(text, iter_pullenti_spans(res, text))))
    texts = [s.text for s in spans]
    labels = {s.text: s.label for s in spans}

    # 1. МФЦ и Почта России (включая АО) не замаскированы
    assert not any("предоставления государственных" in t.lower() for t in texts)
    assert not any("почта россии" in t.lower() for t in texts)

    # 2. Корр. счета 30101... распознаются как RU_CORR_ACCOUNT
    corr_spans = [s for s in spans if s.label == "RU_CORR_ACCOUNT"]
    corr_texts = [s.text for s in corr_spans]
    assert "30101000000000000001" in corr_texts
    assert "30101000000000000002" in corr_texts

    # 3. Префикс гр. отсечен
    assert "гр. Иванова Мария Сергеевна" not in texts
    assert "Иванова Мария Сергеевна" in texts

    # 4. Адрес не захватил уполномочиваю
    assert not any("уполномочиваю" in t for t in texts)

    # 5. Имя ИП и чистый Aura Tour, без 'Туристической компанией'
    assert not any("ивановной" in t.lower() and labels[t] == "ORG" for t in texts)
    assert not any("туристической компанией" in t.lower() for t in texts)
    assert "Aura Tour" in texts


def test_power_of_attorney_and_court_edge_cases():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        refine_composite_spans,
        select_non_overlapping,
    )

    sample_text = (
        "АО «АЛЬФА-БАНК»БИК: 044030786Расчетный счет: 40702810000000000001\n"
        "Обратилась в центре предоставления государственных и муниципальных услуг.\n"
        "Определение Судебной коллегии по гражданским делам Второго кассационного суда.\n"
        "Прибытие в Международный аэропорт Внуково.\n"
        "ИП Семенова Тамарой\n"
        "Юрьевной Туристической компанией Aura Tour заключен договор.\n"
        "г. Москва, ул. Примерная, д. 10, кв. 25,\n"
        "настоящей доверенностью уполномочиваю гор. Киров\n"
    )
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(sample_text), None, None)
    spans = list(select_non_overlapping(refine_composite_spans(sample_text, iter_pullenti_spans(res, sample_text))))
    texts = [s.text for s in spans]
    labels = {s.text: s.label for s in spans}

    # 1. АЛЬФА-БАНК выделен без БИК
    assert "АЛЬФА-БАНК" in texts
    assert not any("АЛЬФА-БАНК»БИК" in t for t in texts)

    # 2. центр предоставления госуслуг (без префикса МФЦ) не маскируется
    assert not any("предоставления государственных" in t.lower() for t in texts)

    # 3. Судебная коллегия не маскируется
    assert not any("судебной коллегии" in t.lower() for t in texts)

    # 4. Международный аэропорт не маскируется
    assert "Международный аэропорт" not in texts

    # 5. Aura Tour чистый, отчество Юрьевной не попало в ORG
    assert not any("юрьевной" in t.lower() and labels.get(t) == "ORG" for t in texts)
    assert not any("туристической компанией" in t.lower() for t in texts)
    assert "Aura Tour" in texts

    # 6. Адрес не склеился с 'уполномочиваю гор. Киров'
    assert not any("уполномочиваю" in t for t in texts)


def test_partner_travel_and_manager_edge_cases():
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    from legal_pullenti import (
        initialize_ner,
        iter_pullenti_spans,
        refine_composite_spans,
        select_non_overlapping,
    )

    sample_text = (
        "PARTNER - Альфа Тревел | 315094\n"
        "Анастасия Смирнова | Anastasiia Smirnova Менеджер по работе с ключевыми клиентами / Key account\n"
        "To: Delta Travel - Anastasiia Smirnova <smirnova.ai@example.invalid>; Delta Travel - Elena Popova\n"
        "Прибытие в Международный аэропорт Шарджа и в аэропорту Мале.\n"
        "Суммарная процентная комиссия\nMARANSHICA IAAAA\n"
    )
    initialize_ner()
    proc = ProcessorService.create_processor()
    res = proc.process(SourceOfAnalysis(sample_text), None, None)
    spans = list(select_non_overlapping(refine_composite_spans(sample_text, iter_pullenti_spans(res, sample_text))))
    texts = [s.text for s in spans]
    labels = {s.text: s.label for s in spans}

    # 1. Альфа Тревел классифицирован как ORG / FOREIGN_ORG, а не PER
    assert "Альфа Тревел" in texts
    assert labels["Альфа Тревел"] in ("ORG", "FOREIGN_ORG")

    # 2. Менеджер по работе с ключевыми клиентами отсечен от имени
    assert "Anastasiia Smirnova" in texts
    assert not any("менеджер по работе" in t.lower() for t in texts)

    # 3. Elena Popova очищена от Travel -
    assert "Elena Popova" in texts
    assert not any("travel - elena" in t.lower() for t in texts)

    # 4. Аэропорты не замаскированы
    assert not any("шарджа" in t.lower() and labels.get(t) == "ORG" for t in texts)
    assert not any("мале" in t.lower() and labels.get(t) == "ORG" for t in texts)

    # 5. Суммарная процентная комиссия не замаскирована
    assert not any("процентная комиссия" in t.lower() for t in texts)


