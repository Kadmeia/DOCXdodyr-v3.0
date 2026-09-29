# -*- coding: utf-8 -*-
"""Synthetic regression coverage for the expanded Pullenti legal cartridge.

Values are intentionally fictional.  These tests exercise field semantics,
format checks and exact source coordinates, including flattened table rows.
"""

from pullenti_legal.analyzer import iter_candidates


CASES = {
    "BIRTH_CERTIFICATE": "Свидетельство о рождении: IV-АБ 123456",
    "MARRIAGE_CERTIFICATE": "Свидетельство о заключении брака: II-МЮ 234567",
    "DIVORCE_CERTIFICATE": "Свидетельство о расторжении брака: III-АК 345678",
    "DEATH_CERTIFICATE": "Свидетельство о смерти: I-ВГ 456789",
    "NAME_CHANGE_CERTIFICATE": "Свидетельство о перемене имени: IV-ДЕ 567890",
    "PATERNITY_CERTIFICATE": "Свидетельство об установлении отцовства: II-ЖЗ 678901",
    "ADOPTION_CERTIFICATE": "Свидетельство об усыновлении: III-ИЙ 789012",
    "CIVIL_STATUS_ACT": "Номер записи акта гражданского состояния: 12345",
    "FOREIGN_PASSPORT": "Заграничный паспорт: 70 1234567",
    "DRIVER_LICENSE": "Водительское удостоверение № 9900 123456",
    "MILITARY_ID": "Военный билет: АБ 1234567",
    "RESIDENCE_PERMIT": "Вид на жительство: 12 1234567",
    "MIGRATION_CARD": "Миграционная карта: АБ 12345678",
    "VISA_NUMBER": "Номер визы: AB123456",
    "WORK_PERMIT": "Разрешение на работу: AB12345",
    "CADASTRAL_NUMBER": "Кадастровый номер: 77:01:0004012:123",
    "EGRN_RECORD_NUMBER": "Номер записи ЕГРН: 77-77/001-1234567/2024",
    "PROPERTY_RIGHT_NUMBER": "Номер государственной регистрации права: 77:01:1234567-01/2024-123",
    "PROPERTY_CONDITIONAL_NUMBER": "Условный номер объекта: 77:01:1234:567",
    "VEHICLE_VIN": "VIN: XTA210990Y1234567",
    "VEHICLE_PLATE": "Государственный регистрационный знак: А123ВС77",
    "PTS_NUMBER": "Номер ПТС: 77 77 123456",
    "STS_NUMBER": "Номер СТС: 77 77 123456",
    "LAWYER_ID_NUMBER": "Удостоверение адвоката № 77-АБ 123456",
    "LAWYER_REGISTRY_NUMBER": "Реестровый номер адвоката: 123456/77",
    "PATENT_ATTORNEY_NUMBER": "Реестровый номер патентного поверенного: 12345",
    "COURT_CASE_NUMBER": "Номер арбитражного дела: А40-12345/2024",
    "CRIMINAL_CASE_NUMBER": "Номер уголовного дела: 123-4567/2024",
    "KUSP_NUMBER": "Номер КУСП: 1234567",
    "ENFORCEMENT_PROCEEDING_NUMBER": "Номер исполнительного производства: 12345/24/77001-ИП",
    "NOTARY_REGISTER_NUMBER": "Номер нотариального реестра: 123456",
    "POWER_OF_ATTORNEY_NUMBER": "Номер доверенности: Д-123/24",
    "PATENT_NUMBER": "Номер патента: 2345678",
    "PATENT_APPLICATION_NUMBER": "Номер заявки на изобретение: 2023123456",
    "TRADEMARK_NUMBER": "Номер регистрации товарного знака: 123456",
    "TRADEMARK_APPLICATION_NUMBER": "Номер заявки на товарный знак: 2023123456",
    "INDUSTRIAL_DESIGN_NUMBER": "Номер промышленного образца: 345678",
    "UTILITY_MODEL_NUMBER": "Номер полезной модели: 456789",
    "SOFTWARE_REGISTRATION_NUMBER": "Номер государственной регистрации программы для ЭВМ: 2023661234",
    "DATABASE_REGISTRATION_NUMBER": "Номер государственной регистрации базы данных: 2023771234",
    "TOPOLOGY_REGISTRATION_NUMBER": "Номер регистрации топологии: 123456",
    "WIPO_APPLICATION_NUMBER": "Номер международной заявки: PCT/RU2023/01234",
    "OMS_POLICY": "Полис ОМС: 1234567890123456",
    "DMS_POLICY": "Полис ДМС: AB123456",
    "INSURANCE_POLICY": "Номер страхового полиса: ОСАГО-12345",
    "MEDICAL_RECORD_NUMBER": "Номер медицинской карты: МК-12345",
    "DISABILITY_CERTIFICATE": "Номер справки об инвалидности: ИНВ-12345",
    "SICK_LEAVE_NUMBER": "Номер листка нетрудоспособности: 123456789012",
    "DIAGNOSIS": "Диагноз: гипертония",
    "BANK_CARD": "Номер банковской карты: 4111 1111 1111 1111",
    "PERSONAL_ACCOUNT": "Лицевой счет: 123456789012",
    "CONTRACT_NUMBER": "Номер договора: Д-123/24",
    "CLIENT_NUMBER": "Номер клиента: CL-12345",
    "ORDER_NUMBER": "Номер заказа: ORD-12345",
    "IP_ADDRESS": "IP-адрес: 192.168.1.10",
    "MAC_ADDRESS": "MAC-адрес: 00:11:22:33:44:55",
    "USER_ACCOUNT": "Логин: ivan.petrov",
    "TELEGRAM_NICK": "Telegram логин: @ivan_petrov",
    "SOCIAL_NETWORK_ID": "ID пользователя: 12345678",
    "MESSENGER_ID": "ID мессенджера: chat_1234",
}


def test_each_expanded_kind_has_a_contextual_candidate_and_exact_offsets():
    for kind, text in CASES.items():
        found = [item for item in iter_candidates(text) if item[0] == kind]
        assert found, f"no candidate for {kind}: {text}"
        _kind, _value, start, end = found[0]
        assert start > 0
        assert text[start:end]
        assert text.endswith(text[start:end])


def test_unlabelled_numbers_and_short_contract_numbers_are_not_candidates():
    text = "№ 12345, 77:01:0004012:123, 2023123456, 4111111111111111"
    assert list(iter_candidates(text)) == []
    assert list(iter_candidates("Номер договора: 1")) == []


def test_tab_and_newline_separators_cover_flattened_table_rows():
    text = "Кадастровый номер\t77:01:0004012:123\nНомер дела\nА40-12345/2024"
    kinds = {item[0] for item in iter_candidates(text)}
    assert "CADASTRAL_NUMBER" in kinds
    # The court grammar needs the explicit semantic field phrase as well.
    text = "Номер арбитражного дела\nА40-12345/2024"
    assert any(item[0] == "COURT_CASE_NUMBER" for item in iter_candidates(text))


def test_checksum_and_card_validation_remain_enabled():
    assert not list(iter_candidates("7707083894"))
    assert any(item[0] == "INN" for item in iter_candidates("7702012345"))
    assert not list(iter_candidates("Номер банковской карты: 4111 1111 1111 1112"))


def test_context_only_sensitive_categories_require_their_field_marker():
    cases = {
        "BIRTH_DATE": "Дата рождения: 01.02.1980",
        "BIRTH_PLACE": "Место рождения: город Тестовый",
        "CITIZENSHIP": "Гражданство: Российская Федерация",
        "JOB_TITLE": "Должность: ведущий юрист",
        "EMPLOYER": "Место работы: ООО Ромашка",
        "RELATIVE": "Родственная связь: супруг",
        "EDUCATION": "Образование: высшее юридическое",
        "INCOME": "Размер дохода: 120 000,00 руб.",
        "CRIMINAL_RECORD": "Сведения о судимости: отсутствуют",
        "NATIONALITY": "Национальность: русская",
        "RELIGION": "Вероисповедание: не указано",
        "POLITICAL_INFO": "Политические взгляды: не указаны",
        "BIOMETRIC_DATA": "Биометрические данные: шаблон лица",
        "HEALTH_INFORMATION": "Сведения о состоянии здоровья: ограничений нет",
        "PRIORITY_APPLICATION_NUMBER": "Номер приоритетной заявки: RU2023123456",
    }
    for kind, text in cases.items():
        assert any(item[0] == kind for item in iter_candidates(text)), kind
    assert not list(iter_candidates("01.02.1980, ведущий юрист, 120 000 руб."))
