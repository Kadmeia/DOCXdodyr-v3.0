# -*- coding: utf-8 -*-
"""Воспроизводимый корпус покрытия новых юридических сущностей.

Модуль намеренно не содержит NER и не изменяет Pullenti. Он создаёт безопасные
синтетические примеры, построенные по контекстам официальных форм, чтобы можно
было отдельно измерять распознавание сущности, контекстного cue и табличной
разметки. Реальные документы учитываются только через hash-only provenance.

Команды:

    python corpus_coverage.py generate corpus/coverage
    python corpus_coverage.py report corpus/coverage/synthetic.jsonl
    python corpus_coverage.py sources corpus/coverage/source_manifest.json
    python corpus_coverage.py fetch --url URL --cache-dir .cache/legal-corpus/provenance

По умолчанию создаётся 200 позитивных и 40 hard-negative примеров на каждый
тип. Генерация детерминирована и не требует сети.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


COVERAGE_SCHEMA_VERSION = "legalpullenti-coverage-1.0"
DEFAULT_SAMPLES_PER_TYPE = 200
DEFAULT_HARD_CASES_PER_TYPE = 40
SYNTHETIC_SEED = 20260824


@dataclass(frozen=True)
class TypeSpec:
    label: str
    group: str
    cue: str
    aliases: Tuple[str, ...]
    kind: str = "token"


def _spec(label: str, group: str, cue: str, *aliases: str, kind: str = "token") -> TypeSpec:
    return TypeSpec(label, group, cue, tuple(aliases), kind)


# Типы синхронизированы с расширенным словарём, предложенным в постановке.
# Алиасы — семантические подсказки для контекстного этапа, а не regex-правила.
TYPE_SPECS: Tuple[TypeSpec, ...] = (
    _spec("PER", "identity", "фамилия, имя, отчество", "ФИО", "гражданин"),
    _spec("ORG", "identity", "наименование организации", "юридическое лицо"),
    _spec("ADDRESS", "identity", "адрес места жительства", "адрес регистрации"),
    _spec("PHONE_NUMBER", "identity", "номер телефона", "телефон"),
    _spec("EMAIL", "identity", "адрес электронной почты", "e-mail"),
    _spec("WEBSITE", "identity", "адрес сайта", "веб-сайт"),
    _spec("INN", "tax", "ИНН", "идентификационный номер налогоплательщика", kind="digits"),
    _spec("KPP", "tax", "КПП", "код причины постановки на учёт", kind="digits"),
    _spec("OGRN", "tax", "ОГРН", "основной государственный регистрационный номер", kind="digits"),
    _spec("OGRNIP", "tax", "ОГРНИП", "государственный регистрационный номер индивидуального предпринимателя", kind="digits"),
    _spec("BIK", "bank", "БИК", "банковский идентификационный код", kind="digits"),
    _spec("SNILS", "identity", "СНИЛС", "страховой номер индивидуального лицевого счёта", kind="digits"),
    _spec("PASSPORT", "identity", "паспорт", "серия и номер паспорта", kind="document"),
    _spec("RU_ACCOUNT", "bank", "расчётный счёт", "р/с", kind="digits"),
    _spec("RU_CORR_ACCOUNT", "bank", "корреспондентский счёт", "к/с", kind="digits"),
    _spec("BIRTH_CERTIFICATE", "civil_status", "свидетельство о рождении", "номер свидетельства о рождении", kind="document"),
    _spec("MARRIAGE_CERTIFICATE", "civil_status", "свидетельство о заключении брака", "свидетельство о браке", kind="document"),
    _spec("DIVORCE_CERTIFICATE", "civil_status", "свидетельство о расторжении брака", "свидетельство о разводе", kind="document"),
    _spec("DEATH_CERTIFICATE", "civil_status", "свидетельство о смерти", "номер свидетельства о смерти", kind="document"),
    _spec("NAME_CHANGE_CERTIFICATE", "civil_status", "свидетельство о перемене имени", "перемена имени", kind="document"),
    _spec("PATERNITY_CERTIFICATE", "civil_status", "свидетельство об установлении отцовства", "установление отцовства", kind="document"),
    _spec("ADOPTION_CERTIFICATE", "civil_status", "свидетельство об усыновлении", "удочерение", kind="document"),
    _spec("CIVIL_STATUS_ACT", "civil_status", "номер записи акта гражданского состояния", "запись акта ЗАГС", kind="document"),
    _spec("FOREIGN_PASSPORT", "identity", "заграничный паспорт", "серия и номер заграничного паспорта", kind="document"),
    _spec("DRIVER_LICENSE", "identity", "водительское удостоверение", "номер водительского удостоверения", kind="document"),
    _spec("MILITARY_ID", "identity", "военный билет", "номер военного билета", kind="document"),
    _spec("RESIDENCE_PERMIT", "identity", "вид на жительство", "номер вида на жительство", kind="document"),
    _spec("MIGRATION_CARD", "identity", "миграционная карта", "номер миграционной карты", kind="document"),
    _spec("VISA_NUMBER", "identity", "номер визы", "виза", kind="document"),
    _spec("WORK_PERMIT", "employment", "разрешение на работу", "номер разрешения на работу", kind="document"),
    _spec("CADASTRAL_NUMBER", "property", "кадастровый номер", "кадастровый номер объекта недвижимости", kind="cadastral"),
    _spec("EGRN_RECORD_NUMBER", "property", "номер записи ЕГРН", "запись ЕГРН", kind="document"),
    _spec("PROPERTY_RIGHT_NUMBER", "property", "номер государственной регистрации права", "регистрация права", kind="document"),
    _spec("PROPERTY_CONDITIONAL_NUMBER", "property", "условный номер объекта", "условный номер недвижимости", kind="document"),
    _spec("VEHICLE_VIN", "vehicle", "VIN", "идентификационный номер транспортного средства", kind="vin"),
    _spec("VEHICLE_PLATE", "vehicle", "государственный регистрационный знак", "номер автомобиля", kind="plate"),
    _spec("PTS_NUMBER", "vehicle", "номер ПТС", "паспорт транспортного средства", kind="document"),
    _spec("STS_NUMBER", "vehicle", "номер СТС", "свидетельство о регистрации транспортного средства", kind="document"),
    _spec("LAWYER_ID_NUMBER", "legal_profession", "удостоверение адвоката", "адвокатское удостоверение", kind="document"),
    _spec("LAWYER_REGISTRY_NUMBER", "legal_profession", "реестровый номер адвоката", "номер адвоката в реестре", kind="document"),
    _spec("PATENT_ATTORNEY_NUMBER", "legal_profession", "реестровый номер патентного поверенного", "патентный поверенный", kind="document"),
    _spec("TOUR_OPERATOR_REGISTRY_NUMBER", "travel", "реестровый номер туроператора", "номер в ЕФРТ", kind="document"),
    _spec("COURT_CASE_NUMBER", "proceeding", "номер судебного дела", "дело №", kind="case"),
    _spec("CRIMINAL_CASE_NUMBER", "proceeding", "номер уголовного дела", "уголовное дело", kind="case"),
    _spec("KUSP_NUMBER", "proceeding", "номер КУСП", "регистрация сообщения о преступлении", kind="case"),
    _spec("ENFORCEMENT_PROCEEDING_NUMBER", "proceeding", "номер исполнительного производства", "исполнительное производство", kind="case"),
    _spec("NOTARY_REGISTER_NUMBER", "legal_profession", "номер нотариального реестра", "реестр нотариальных действий", kind="case"),
    _spec("POWER_OF_ATTORNEY_NUMBER", "legal_profession", "номер доверенности", "доверенность", kind="document"),
    _spec("PATENT_NUMBER", "ip", "номер патента", "патент на изобретение", kind="ip"),
    _spec("PATENT_APPLICATION_NUMBER", "ip", "номер заявки на изобретение", "заявка на патент", kind="ip"),
    _spec("TRADEMARK_NUMBER", "ip", "номер регистрации товарного знака", "товарный знак", kind="ip"),
    _spec("TRADEMARK_APPLICATION_NUMBER", "ip", "номер заявки на товарный знак", "заявка на товарный знак", kind="ip"),
    _spec("INDUSTRIAL_DESIGN_NUMBER", "ip", "номер промышленного образца", "промышленный образец", kind="ip"),
    _spec("UTILITY_MODEL_NUMBER", "ip", "номер полезной модели", "полезная модель", kind="ip"),
    _spec("SOFTWARE_REGISTRATION_NUMBER", "ip", "номер государственной регистрации программы для ЭВМ", "программа для ЭВМ", kind="ip"),
    _spec("DATABASE_REGISTRATION_NUMBER", "ip", "номер государственной регистрации базы данных", "база данных", kind="ip"),
    _spec("TOPOLOGY_REGISTRATION_NUMBER", "ip", "номер регистрации топологии", "топология интегральной микросхемы", kind="ip"),
    _spec("WIPO_APPLICATION_NUMBER", "ip", "номер международной заявки", "ВОИС", kind="ip"),
    _spec("PRIORITY_APPLICATION_NUMBER", "ip", "номер приоритетной заявки", "приоритет заявки", kind="ip"),
    _spec("OMS_POLICY", "medical", "полис ОМС", "номер полиса обязательного медицинского страхования", kind="digits"),
    _spec("DMS_POLICY", "medical", "полис ДМС", "номер полиса добровольного медицинского страхования", kind="document"),
    _spec("INSURANCE_POLICY", "medical", "номер страхового полиса", "страховой полис", kind="document"),
    _spec("MEDICAL_RECORD_NUMBER", "medical", "номер медицинской карты", "медицинская карта", kind="document"),
    _spec("DISABILITY_CERTIFICATE", "medical", "номер справки об инвалидности", "справка об инвалидности", kind="document"),
    _spec("SICK_LEAVE_NUMBER", "medical", "номер листка нетрудоспособности", "больничный лист", kind="document"),
    _spec("DIAGNOSIS", "health_data", "диагноз", "сведения о диагнозе", kind="phrase"),
    _spec("HEALTH_INFORMATION", "health_data", "сведения о состоянии здоровья", "медицинские сведения", kind="phrase"),
    _spec("BANK_CARD", "bank", "номер банковской карты", "банковская карта", kind="digits"),
    _spec("IBAN", "bank", "номер счёта IBAN", "IBAN", kind="digits"),
    _spec("SWIFT", "bank", "SWIFT-код", "SWIFT", kind="digits"),
    _spec("PERSONAL_ACCOUNT", "bank", "номер лицевого счёта", "лицевой счёт", kind="digits"),
    _spec("CONTRACT_NUMBER", "contract", "номер договора", "договор", kind="case"),
    _spec("CLIENT_NUMBER", "contract", "номер клиента", "идентификатор клиента", kind="case"),
    _spec("ORDER_NUMBER", "contract", "номер заказа", "заказ", kind="case"),
    _spec("IP_ADDRESS", "digital", "IP-адрес", "сетевой адрес", kind="ipaddr"),
    _spec("MAC_ADDRESS", "digital", "MAC-адрес", "физический адрес", kind="mac"),
    _spec("USER_ACCOUNT", "digital", "идентификатор пользователя", "логин", kind="account"),
    _spec("TELEGRAM_NICK", "digital", "Telegram логин", "Telegram", kind="account"),
    _spec("SOCIAL_NETWORK_ID", "digital", "идентификатор социальной сети", "профиль социальной сети", kind="account"),
    _spec("MESSENGER_ID", "digital", "идентификатор мессенджера", "мессенджер", kind="account"),
    _spec("BIRTH_DATE", "context", "дата рождения", "родился", kind="date"),
    _spec("BIRTH_PLACE", "context", "место рождения", "родилась", kind="place"),
    _spec("CITIZENSHIP", "context", "гражданство", "гражданин", kind="country"),
    _spec("JOB_TITLE", "context", "должность", "должность лица", kind="phrase"),
    _spec("EMPLOYER", "context", "место работы", "работодатель", kind="phrase"),
    _spec("RELATIVE", "context", "сведения о родственнике", "родственная связь", kind="phrase"),
    _spec("EDUCATION", "context", "образование", "сведения об образовании", kind="phrase"),
    _spec("INCOME", "context", "размер дохода", "доход", kind="money"),
    _spec("CRIMINAL_RECORD", "context", "сведения о судимости", "судимость", kind="phrase"),
    _spec("NATIONALITY", "context", "национальность", "национальность лица", kind="country"),
    _spec("RELIGION", "context", "вероисповедание", "религия", kind="phrase"),
    _spec("POLITICAL_INFO", "context", "политические взгляды", "политическая деятельность", kind="phrase"),
    _spec("BIOMETRIC_DATA", "context", "биометрические данные", "биометрия", kind="phrase"),
)

TYPE_SPEC_BY_LABEL: Mapping[str, TypeSpec] = {item.label: item for item in TYPE_SPECS}


# Только официальные/публичные точки для будущей provenance-проверки. Содержимое
# не загружается генератором и не включается в пакет приложения.
OFFICIAL_SOURCE_CATALOG: Tuple[Dict[str, object], ...] = (
    {"id": "pravo", "url": "https://publication.pravo.gov.ru/", "host": "publication.pravo.gov.ru", "topics": ["proceeding", "contract", "identity"], "license_status": "verify_before_redistribution"},
    {"id": "rospatent", "url": "https://rospatent.gov.ru/ru/documents", "host": "rospatent.gov.ru", "topics": ["ip", "legal_profession"], "license_status": "official_public_forms"},
    {"id": "rosreestr", "url": "https://rosreestr.gov.ru/", "host": "rosreestr.gov.ru", "topics": ["property"], "license_status": "verify_before_redistribution"},
    {"id": "nalog", "url": "https://www.nalog.gov.ru/rn77/related_activities/registration_ip_yl/", "host": "nalog.gov.ru", "topics": ["tax", "identity"], "license_status": "official_public_forms"},
    {"id": "cbr", "url": "https://cbr.ru/finmarkets/", "host": "cbr.ru", "topics": ["bank"], "license_status": "official_public_information"},
    {"id": "foms", "url": "https://ffoms.gov.ru/", "host": "ffoms.gov.ru", "topics": ["medical"], "license_status": "verify_before_redistribution"},
    {"id": "fss", "url": "https://sfr.gov.ru/", "host": "sfr.gov.ru", "topics": ["medical", "identity"], "license_status": "official_public_forms"},
    {"id": "sudrf", "url": "https://sudrf.ru/", "host": "sudrf.ru", "topics": ["proceeding", "identity"], "license_status": "anonymized_public_decisions"},
    {"id": "gibdd", "url": "https://гибдд.рф/", "host": "гибдд.рф", "topics": ["vehicle"], "license_status": "verify_before_redistribution"},
    {"id": "rospatent_form_example", "url": "https://rospatent.gov.ru/content/uploadfiles/primer1_610.pdf", "host": "rospatent.gov.ru", "topics": ["ip"], "license_status": "official_public_form_example"},
    {"id": "rospatent_patent_attorney_form", "url": "https://rospatent.gov.ru/content/uploadfiles/blank1_316.pdf", "host": "rospatent.gov.ru", "topics": ["ip", "legal_profession"], "license_status": "official_public_form"},
    {"id": "minjust_zags_forms", "url": "https://publication.pravo.gov.ru/Document/View/0001201810030012", "host": "publication.pravo.gov.ru", "topics": ["civil_status"], "license_status": "official_legal_publication"},
    {"id": "gosuslugi_document_fields", "url": "https://info.gosuslugi.ru/upload/medialibrary/1aa/l28yp0uf84lbk2t8b0ea4agt0sfptd9t/Avtokomplit-dokumenty_v2.pdf", "host": "info.gosuslugi.ru", "topics": ["civil_status", "identity"], "license_status": "official_public_documentation"},
    {"id": "fpa_advocate_form", "url": "https://apro.fparf.ru/upload/iblock/2f9/i2tlvlejb63sm8m30nsa3ksx0phvtfk4/98_2025_1.pdf", "host": "apro.fparf.ru", "topics": ["legal_profession", "proceeding"], "license_status": "official_professional_publication"},
)


SPLIT_RANGES = {"train": range(0, 140), "dev": range(140, 170), "test": range(170, 200)}
SPLIT_TEMPLATES = {
    "train": ("prose_a", "prose_b", "field_a", "table_row_a", "table_tab_a"),
    "dev": ("prose_c", "field_b", "table_row_b"),
    "test": ("prose_d", "field_c", "table_row_c"),
}


def _slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _digest_int(label: str, index: int, width: int = 10) -> int:
    raw = hashlib.sha256(f"{SYNTHETIC_SEED}:{label}:{index}".encode("ascii")).hexdigest()
    return int(raw[:16], 16) % (10 ** width)


def _digits(label: str, index: int, length: int) -> str:
    return f"{_digest_int(label, index, length):0{length}d}"[-length:]


def _inn(index: int) -> str:
    body = "77" + _digits("INN_BODY", index, 7)
    check = sum(w * int(n) for w, n in zip((2, 4, 10, 3, 5, 9, 4, 6, 8), body)) % 11 % 10
    return body + str(check)


def _ogrn(index: int, individual: bool = False) -> str:
    length = 15 if individual else 13
    prefix = "3" if individual else "1"
    body = prefix + _digits("OGRNIP_BODY" if individual else "OGRN_BODY", index, length - 2)
    divisor = 13 if individual else 11
    return body + str(int(body) % divisor % 10)


def _snils(index: int) -> str:
    body = f"{100000001 + index:09d}"[-9:]
    total = sum(int(digit) * weight for digit, weight in zip(body, range(9, 0, -1)))
    if total < 100:
        check = total
    elif total in (100, 101):
        check = 0
    else:
        check = total % 101
        if check == 100:
            check = 0
    return f"{body[:3]}-{body[3:6]}-{body[6:]} {check:02d}"


def _luhn_number(index: int) -> str:
    body = "411111" + _digits("CARD_BODY", index, 9)
    for check in range(10):
        candidate = body + str(check)
        total = 0
        parity = len(candidate) % 2
        for position, digit in enumerate(candidate):
            number = int(digit)
            if position % 2 == parity:
                number = number * 2 - 9 if number > 4 else number * 2
            total += number
        if total % 10 == 0:
            return candidate
    raise AssertionError("Luhn digit not found")


def synthetic_value(spec: TypeSpec, index: int) -> str:
    """Создаёт заведомо искусственное, но структурно правдоподобное значение."""
    n = _digest_int(spec.label, index)
    if spec.label == "PER":
        return f"Тестов{index:04d} Алексей{index:04d} Синтетикович"
    if spec.label == "ORG":
        return f"ООО «Синтетическая организация {index:04d}»"
    if spec.label == "ADDRESS":
        return f"г. Тестоград-{index:04d}, ул. Учебная, д. {1 + index}"
    if spec.label == "PHONE_NUMBER":
        return f"+7 (900) {100 + index % 900:03d}-{index % 100:02d}-{(index * 7) % 100:02d}"
    if spec.label == "EMAIL":
        return f"synthetic-{index:04d}@example.invalid"
    if spec.label == "WEBSITE":
        return f"https://example.invalid/entity/{index:04d}"
    if spec.label == "INN":
        return _inn(index)
    if spec.label == "OGRN":
        return _ogrn(index)
    if spec.label == "OGRNIP":
        return _ogrn(index, individual=True)
    if spec.label == "SNILS":
        return _snils(index)
    if spec.label == "BANK_CARD":
        number = _luhn_number(index)
        return " ".join(number[offset:offset + 4] for offset in range(0, 16, 4))
    if spec.label == "IBAN":
        bban = f"37040044053201{index:06d}"
        rearranged = bban + "131400"
        check = 98 - (int(rearranged) % 97)
        return f"DE{check:02d}{bban}"
    if spec.label == "SWIFT":
        return f"DEUTDEDD{index % 900 + 100:03d}"
    if spec.label in {"BIRTH_CERTIFICATE", "MARRIAGE_CERTIFICATE", "DIVORCE_CERTIFICATE", "DEATH_CERTIFICATE", "NAME_CHANGE_CERTIFICATE", "PATERNITY_CERTIFICATE", "ADOPTION_CERTIFICATE"}:
        return f"IV-АБ {100000 + index:06d}"
    if spec.label == "CIVIL_STATUS_ACT":
        return str(10000 + index)
    if spec.label in {"PASSPORT", "DRIVER_LICENSE", "PTS_NUMBER", "STS_NUMBER"}:
        return f"77 01 {100000 + index:06d}"
    if spec.label in {"FOREIGN_PASSPORT", "RESIDENCE_PERMIT"}:
        return f"70 {1000000 + index:07d}"
    if spec.label == "MILITARY_ID":
        return f"АБ {1000000 + index:07d}"
    if spec.label == "MIGRATION_CARD":
        return f"АБ {10000000 + index:08d}"
    if spec.label in {"VISA_NUMBER", "WORK_PERMIT"}:
        return f"AB{100000 + index:06d}"
    if spec.label == "EGRN_RECORD_NUMBER":
        return f"77-77/001-{1000000 + index:07d}/2024"
    if spec.label == "PROPERTY_RIGHT_NUMBER":
        return f"77:01:{1000000 + index:07d}-01/2024-{1 + index}"
    if spec.label == "PROPERTY_CONDITIONAL_NUMBER":
        return f"77:01:SYNTH-{1000 + index}"
    if spec.label == "VEHICLE_PLATE":
        return f"А{100 + index:03d}ВС{77 + index % 20}"
    if spec.label == "LAWYER_ID_NUMBER":
        return f"77-АБ {100000 + index}"
    if spec.label == "LAWYER_REGISTRY_NUMBER":
        return f"{100000 + index}/77"
    if spec.label == "TOUR_OPERATOR_REGISTRY_NUMBER":
        return f"РТО-{10000 + index:05d}"
    if spec.label in {"PATENT_ATTORNEY_NUMBER", "KUSP_NUMBER", "NOTARY_REGISTER_NUMBER", "PATENT_NUMBER", "TRADEMARK_NUMBER", "INDUSTRIAL_DESIGN_NUMBER", "UTILITY_MODEL_NUMBER", "TOPOLOGY_REGISTRATION_NUMBER"}:
        return str(100000 + index)
    if spec.label == "COURT_CASE_NUMBER":
        return f"А40-{10000 + index}/2024"
    if spec.label == "CRIMINAL_CASE_NUMBER":
        return f"123-{1000 + index}/2024"
    if spec.label == "ENFORCEMENT_PROCEEDING_NUMBER":
        return f"{10000 + index}/24/77001-ИП"
    if spec.label in {"POWER_OF_ATTORNEY_NUMBER", "CONTRACT_NUMBER", "CLIENT_NUMBER", "ORDER_NUMBER"}:
        return f"SYN-{1000 + index}/24"
    if spec.label in {"PATENT_APPLICATION_NUMBER", "TRADEMARK_APPLICATION_NUMBER"}:
        return str(2023000000 + index)
    if spec.label in {"SOFTWARE_REGISTRATION_NUMBER", "DATABASE_REGISTRATION_NUMBER"}:
        return str(2023000000 + index)
    if spec.label == "WIPO_APPLICATION_NUMBER":
        return f"PCT/RU2024/{10000 + index:05d}"
    if spec.label == "PRIORITY_APPLICATION_NUMBER":
        return f"RU{2023000000 + index}"
    if spec.label == "SICK_LEAVE_NUMBER":
        return str(100000000000 + index)
    if spec.label in {"DMS_POLICY", "INSURANCE_POLICY", "MEDICAL_RECORD_NUMBER", "DISABILITY_CERTIFICATE"}:
        return f"SYN-{100000 + index}"
    if spec.label == "IP_ADDRESS":
        return f"192.0.2.{1 + index % 200}"
    if spec.label == "TELEGRAM_NICK":
        return f"@synthetic_{index:04d}"
    if spec.label == "SOCIAL_NETWORK_ID":
        return str(10000000 + index)
    if spec.kind == "digits":
        lengths = {"INN": 10, "KPP": 9, "OGRN": 13, "OGRNIP": 15, "BIK": 9, "SNILS": 11, "RU_ACCOUNT": 20, "RU_CORR_ACCOUNT": 20, "OMS_POLICY": 16, "BANK_CARD": 16, "PERSONAL_ACCOUNT": 20}
        return _digits(spec.label, index, lengths.get(spec.label, 12))
    if spec.kind == "cadastral":
        return f"{10 + n % 89}:{n % 100:02d}:{100000 + n % 899999:06d}:{100000 + (n // 7) % 899999:06d}"
    if spec.kind == "vin":
        alphabet = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
        return "X" + "".join(alphabet[(n >> (i * 3)) % len(alphabet)] for i in range(16))
    if spec.kind == "plate":
        return f"Т{n % 10}{chr(ord('А') + n % 6)}{chr(ord('А') + (n // 6) % 6)}{n % 1000:03d}"
    if spec.kind == "ipaddr":
        return f"2001:db8::{1 + index:x}"  # RFC 3849 documentation prefix
    if spec.kind == "mac":
        return "02:00:%02X:%02X:%02X:%02X" % ((n >> 8) & 255, (n >> 16) & 255, (n >> 24) & 255, n & 255)
    if spec.kind == "date":
        return f"{1 + index % 28:02d}.{1 + (index // 28) % 12:02d}.{1970 + index // 336:04d}"
    if spec.kind == "money":
        return f"{10000 + n % 900000:06d},{n % 100:02d} руб"
    if spec.kind == "country":
        return ("Синтетическая Федерация" if n % 2 else "Тестовая Республика") + f"-{index:04d}"
    if spec.kind == "place":
        return f"город Тестовый-{index:04d}"
    if spec.kind == "phrase":
        return f"синтетическое значение {spec.label.lower()}-{index:04d}"
    if spec.kind == "account":
        return f"test_{_slug(spec.label).lower()}_{index:04d}"
    if spec.kind == "case":
        return f"А{1970 + index % 60:04d}-синт-{index:04d}"
    if spec.kind == "ip":
        return f"RU-{1970 + index % 60:02d}-{100000 + n % 899999:06d}"
    if spec.kind == "document":
        return f"С-{index % 99 + 1:02d} {100000 + n % 899999:06d}"
    return f"СИНТ-{_slug(spec.label)}-{index:04d}"


def _render(template: str, cue: str, value: str, index: int) -> Tuple[str, str]:
    if template.endswith("prose_a"):
        return f"В материалах дела указан {cue}: {value}.", "prose"
    if template.endswith("prose_b"):
        return f"Сведения представлены следующим образом — {cue} составляет {value};.", "prose"
    if template.endswith("prose_c"):
        return f"Для идентификации объекта необходимо указать ({cue}) {value}.", "prose"
    if template.endswith("prose_d"):
        return f"В приложении приведён реквизит «{cue}», значение: {value}.", "prose"
    if template.endswith("field_a"):
        return f"{cue}\t{value}", "field"
    if template.endswith("field_b"):
        return f"{cue}:\n{value}", "field"
    if template.endswith("field_c"):
        return f"Поле «{cue}» | {value}", "field"
    if template.endswith("table_row_a"):
        return f"| {cue} | {value} |", "table"
    if template.endswith("table_row_b"):
        return f"Строка {index + 1}\t{cue}\t{value}", "table"
    if template.endswith("table_row_c"):
        return f"Таблица: {cue}\t{value}", "table"
    if template.endswith("table_tab_a"):
        return f"{cue}\t{value}\n", "table"
    raise ValueError(f"unknown coverage template: {template}")


def _split_for_index(index: int) -> str:
    for split, indexes in SPLIT_RANGES.items():
        if index in indexes:
            return split
    raise ValueError(index)


def _template_for(split: str, index: int) -> str:
    templates = SPLIT_TEMPLATES[split]
    return f"{split}:{templates[index % len(templates)]}"


def _record_id(label: str, split: str, index: int, negative: bool = False) -> str:
    kind = "neg" if negative else "pos"
    return "cov_" + hashlib.sha256(f"{SYNTHETIC_SEED}:{label}:{kind}:{index}".encode()).hexdigest()[:16]


def iter_positive_records(samples_per_type: int = DEFAULT_SAMPLES_PER_TYPE) -> Iterator[Dict[str, object]]:
    if samples_per_type != DEFAULT_SAMPLES_PER_TYPE:
        raise ValueError("split layout is defined for exactly 200 positives per type")
    for spec in TYPE_SPECS:
        for index in range(samples_per_type):
            split = _split_for_index(index)
            template_id = _template_for(split, index)
            value = synthetic_value(spec, index)
            text, layout = _render(template_id, spec.cue, value, index)
            start = text.index(value)
            yield {
                "schema_version": COVERAGE_SCHEMA_VERSION,
                "id": _record_id(spec.label, split, index),
                "label": spec.label,
                "group": spec.group,
                "split": split,
                "template_id": template_id,
                "text": text,
                "surface": value,
                "start": start,
                "end": start + len(value),
                "layout": layout,
                "semantic_cues": [spec.cue, *spec.aliases],
                "expected_relation": "cue_precedes_value",
                "synthetic": True,
                "provenance": {"kind": "synthetic_from_official_form_context", "source_ids": ["rospatent" if spec.group == "ip" else "pravo"], "surface_policy": "included_synthetic_only"},
            }


def _negative_value(spec: TypeSpec, index: int) -> str:
    # Hard negatives сохраняют cue, но нарушают ожидаемый формат/семантику.
    variants = ("не указан", "нет данных", "ошибка", "N/A", f"{index:02d}-неполный")
    if spec.kind == "digits":
        variants = ("123", "000", "123456789012345678901", "не заполнено", "12-34")
    elif spec.kind == "cadastral":
        variants = ("77:0:1", "кадастровый номер отсутствует", "77-01-000000-000000", "не определён", "77:01:ABC")
    elif spec.kind == "vin":
        variants = ("VIN", "123", "OOOOOOOOOOOOOOOOO", "не указан", "X0")
    elif spec.kind == "ipaddr":
        variants = ("999.999.999.999", "192.0.2", "localhost", "не указан", "10.0.0.999")
    return variants[index % len(variants)]


def iter_negative_records(hard_cases_per_type: int = DEFAULT_HARD_CASES_PER_TYPE) -> Iterator[Dict[str, object]]:
    for spec in TYPE_SPECS:
        for index in range(hard_cases_per_type):
            split = ("train", "dev", "test")[index % 3]
            negative_template = ("field_a", "field_b", "field_c", "table_row_a")[index % 4]
            template_id = f"{split}:negative_{index % 4}"
            value = _negative_value(spec, index)
            text, layout = _render(f"{split}:{negative_template}", spec.cue, value, index)
            yield {
                "schema_version": COVERAGE_SCHEMA_VERSION,
                "id": _record_id(spec.label, split, index, negative=True),
                "label": spec.label,
                "group": spec.group,
                "split": split,
                "template_id": template_id,
                "text": text,
                "surface": value,
                "start": text.index(value),
                "end": text.index(value) + len(value),
                "layout": layout,
                "semantic_cues": [spec.cue, *spec.aliases],
                "expected_relation": "hard_negative_invalid_or_missing_value",
                "synthetic": True,
                "negative": True,
                "provenance": {"kind": "synthetic_hard_case", "source_ids": [], "surface_policy": "included_synthetic_only"},
            }


def build_coverage_report(records: Iterable[Mapping[str, object]]) -> Dict[str, object]:
    labels: Dict[str, Dict[str, object]] = {}
    template_splits: Dict[str, set] = {}
    total = positives = negatives = 0
    for row in records:
        total += 1
        label = str(row["label"])
        entry = labels.setdefault(label, {"positive": 0, "negative": 0, "splits": {"train": 0, "dev": 0, "test": 0}, "layouts": {}})
        if row.get("negative"):
            negatives += 1
            entry["negative"] = int(entry["negative"]) + 1
        else:
            positives += 1
            entry["positive"] = int(entry["positive"]) + 1
        split = str(row["split"])
        entry["splits"][split] += 1
        entry["layouts"][str(row["layout"])] = int(entry["layouts"].get(str(row["layout"]), 0)) + 1
        template_splits.setdefault(str(row["template_id"]), set()).add(split)
    leakage = [template for template, splits in template_splits.items() if len(splits) > 1]
    return {"schema_version": COVERAGE_SCHEMA_VERSION, "synthetic_seed": SYNTHETIC_SEED, "types": len(labels), "total_records": total, "positive_records": positives, "negative_records": negatives, "minimum_positive_per_type": min((int(v["positive"]) for v in labels.values()), default=0), "labels": labels, "template_split_leaks": sorted(leakage), "template_split_leak_free": not leakage}


def write_coverage(output_dir: Path, samples_per_type: int = DEFAULT_SAMPLES_PER_TYPE, hard_cases_per_type: int = DEFAULT_HARD_CASES_PER_TYPE) -> Dict[str, object]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(iter_positive_records(samples_per_type)) + list(iter_negative_records(hard_cases_per_type))
    rows.sort(key=lambda row: (str(row["split"]), str(row["label"]), bool(row.get("negative")), str(row["id"])))
    all_path = output_dir / "synthetic.jsonl"
    with all_path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    for split in ("train", "dev", "test"):
        split_rows = [row for row in rows if row["split"] == split]
        with (output_dir / f"{split}.jsonl").open("w", encoding="utf-8") as stream:
            for row in split_rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report = build_coverage_report(rows)
    (output_dir / "coverage_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_source_manifest(output_dir / "source_manifest.json")
    manifest = {"schema_version": COVERAGE_SCHEMA_VERSION, "generated_by": "corpus_coverage.py", "synthetic_seed": SYNTHETIC_SEED, "positive_per_type": samples_per_type, "hard_negative_per_type": hard_cases_per_type, "types": [spec.label for spec in TYPE_SPECS], "files": {"all": "synthetic.jsonl", "train": "train.jsonl", "dev": "dev.jsonl", "test": "test.jsonl", "report": "coverage_report.json", "source_manifest": "source_manifest.json"}}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def write_source_manifest(path: Path) -> None:
    payload = {"schema_version": "legalpullenti-source-provenance-1.0", "policy": "hash_only_for_real_documents", "sources": list(OFFICIAL_SOURCE_CATALOG)}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_provenance(url: str, cache_dir: Path, max_bytes: int = 5_000_000, timeout: int = 20) -> Dict[str, object]:
    parsed = urlparse(url)
    allowed = {str(item["host"]) for item in OFFICIAL_SOURCE_CATALOG}
    if parsed.scheme != "https" or parsed.hostname not in allowed:
        raise ValueError("URL is not an allow-listed HTTPS official source")
    request = urllib.request.Request(url, headers={"User-Agent": "DOCXdodyr-research-provenance/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("source exceeds max_bytes")
    digest = hashlib.sha256(data).hexdigest()
    destination = Path(cache_dir) / digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {"url": url, "host": parsed.hostname, "sha256": digest, "size_bytes": len(data), "cache_path": destination.name, "surface_policy": "hash_only_provenance"}


def _load_rows(path: Path) -> List[Dict[str, object]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Синтетическое покрытие юридических сущностей")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate")
    generate.add_argument("output_dir", type=Path)
    generate.add_argument("--hard-cases", type=int, default=DEFAULT_HARD_CASES_PER_TYPE)
    report = sub.add_parser("report")
    report.add_argument("jsonl", type=Path)
    sources = sub.add_parser("sources")
    sources.add_argument("output", type=Path)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "generate":
        result = write_coverage(args.output_dir, hard_cases_per_type=args.hard_cases)
        print(json.dumps({"types": result["types"], "positive_records": result["positive_records"], "negative_records": result["negative_records"], "minimum_positive_per_type": result["minimum_positive_per_type"]}, ensure_ascii=False))
        return 0
    if args.command == "report":
        print(json.dumps(build_coverage_report(_load_rows(args.jsonl)), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "sources":
        write_source_manifest(args.output)
        return 0
    print(json.dumps(fetch_provenance(args.url, args.cache_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
