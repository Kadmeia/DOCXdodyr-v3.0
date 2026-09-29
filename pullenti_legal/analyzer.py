# -*- coding: utf-8 -*-
"""Pullenti cartridge for context-bound Russian legal identifiers.

Each grammar has a Russian field marker, a format grammar and (where one
exists) a checksum validator.  We deliberately do not search arbitrary
numbers: an unqualified ``№ 12`` is not enough evidence to redact.  Separators
may contain tabs/newlines so flattened two-column Word tables are supported.
"""

from __future__ import annotations

import re
import threading

from pullenti.ner.Analyzer import Analyzer
from pullenti.ner.ProcessorService import ProcessorService
from pullenti.ner.ReferentToken import ReferentToken

from .checksums import valid_iban, valid_inn, valid_luhn, valid_ogrn, valid_swift_bic
from .referent import LegalEntityReferent


def _digits(value):
    return "".join(character for character in value if character.isdigit())


def _upper(value):
    return value.upper().replace("–", "-").replace("—", "-")


def _field(words, value, flags=re.I):
    """Build a semantic ``field marker + value`` expression."""
    marker = r"(?:" + words + r")"
    separator = (
        r"[\s»”\"')\]]*"
        r"(?:(?::|№|#|N(?:O)?\.?|номер|[-–—,;|])\s*)?"
        r"(?:(?:сер(?:ия|\.)|составляет|равен(?:а|о)?|указан(?:а|о)?|значение)"
        r"\s*(?::|№|#|N(?:O)?\.?|[-–—,;|])?\s*)?"
    )
    return re.compile(
        r"(?<![\wА-Яа-яЁё])" + marker + separator + r"(?P<value>" + value + r")(?![\wА-Яа-яЁё])",
        # Multiline anchors are part of the field grammar (for example an
        # ``Образование:`` line), not a document-specific layout assumption.
        flags | re.MULTILINE,
    )


def _text_field(words, value, flags=re.I):
    """Build a semantic field marker + value expression for free-text fields.

    Unlike numeric or formatted identifiers, textual attributes (birth place,
    citizenship, religion, etc.) MUST NOT use comma (,) or semicolon (;) as a separator,
    because in Russian legal texts commas and semicolons separate listed items
    (e.g., 'дата и место рождения, пол, гражданство').
    Valid separators are colon, dash, equals, tab, newline, or 2+ spaces.
    """
    marker = r"(?:" + words + r")"
    separator = (
        r"[\s»”\"')\]]*"
        r"(?:"
        r"(?:(?:,?\s*)?(?:составляет|равен(?:а|о)?|указан(?:а|о)?|значение)\s*(?::|[-–—=|])?\s*)"
        r"|"
        r"(?:(?<=[)\]»”\"'])\s+|(?::|[-–—=|]|\t|\r?\n)\s*(?:(?:составляет|равен(?:а|о)?|указан(?:а|о)?|значение)\s*(?::|[-–—=|])?\s*)?|(?:\s{2,}))"
        r")"
    )
    return re.compile(
        r"(?<![\wА-Яа-яЁё])" + marker + separator + r"(?P<value>" + value + r")(?![\wА-Яа-яЁё])",
        flags | re.MULTILINE,
    )


def _certificate(words):
    return _field(words, r"[IVX]{1,4}\s*[-–—]\s*[А-ЯЁ]{2}\s*\d{6}")


def _number(words, value=r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{2,31}"):
    return _field(words, value)


def _valid_power_of_attorney(value: str) -> bool:
    upper = value.upper().strip()
    if not any(char.isdigit() for char in upper):
        return False
    if upper in {"ОТ", "МЕЖДУ", "БЕЗ", "ДЛЯ", "ГОДА", "Г."}:
        return False
    if re.search(r"\bФЗ\b|-ФЗ\b|/ФЗ\b|\bФКЗ\b|-ФКЗ\b|/ФКЗ\b", upper) or upper.endswith(("ФЗ", "ФКЗ")) or upper.startswith(("ФЗ-", "ФКЗ-")):
        return False
    return True


def _valid_visa(value: str) -> bool:
    return any(char.isdigit() for char in value) and not re.search(r"[А-Яа-яЁё]", value)


def _valid_contract_number(value: str) -> bool:
    upper = value.upper().strip()
    if not any(char.isdigit() for char in upper):
        return False
    if upper in {"ОТ", "МЕЖДУ", "БЕЗ", "ДЛЯ", "ГОДА", "Г."}:
        return False
    # Федеральные законы (152-ФЗ, 402-ФЗ и др.), ФКЗ и ГОСТы не являются номерами договоров/приказов
    if re.search(r"\bФЗ\b|-ФЗ\b|/ФЗ\b|\bФКЗ\b|-ФКЗ\b|/ФКЗ\b", upper) or upper.endswith(("ФЗ", "ФКЗ")) or upper.startswith(("ФЗ-", "ФКЗ-")):
        return False
    if re.search(r"\b(?:ГОСТ|СНИП|САНПИН)\b", upper):
        return False
    return True


def _valid_court_case(value: str) -> bool:
    return (
        "/" in value
        or bool(re.fullmatch(r"\d{1,3}-[А-ЯЁA-Z]{1,4}\d{1,4}-\d{1,8}", value, re.I))
    ) and any(char.isdigit() for char in value)


def _valid_address_field(value: str) -> bool:
    cleaned = value.strip()
    lowered = cleaned.casefold()
    if any(term in lowered for term in (
        "претензи", "возврат", "денежн", "средств", "сообщаем", "рассмотрен",
        "договор", "заявк", "туроператор", "в связи с чем",
    )):
        return False
    return bool(re.search(
        r"(?:\b\d{6}\b|(?:^|[\s,])(?:г\.|город\b|ул\.|улица\b|пер\.|переулок\b|просп\.|проспект\b|"
        r"дом\b|д\.|корпус\b|корп\.|кв\.|квартира\b|пос\.|поселок\b|посёлок\b|с\.\s*[А-ЯЁ][а-яё]+|село\b|"
        r"област\w*\b|республик\w*\b|муниципалитет\w*\b|помещение\b|набережн\w*\b|наб\.|бульвар\b|б-р\b|шоссе\b|ш\.))",
        value,
        re.I,
    ))


def _valid_job_title(value: str) -> bool:
    cleaned = re.sub(r"\([^)]*\)|_{2,}", "", value).strip()
    lowered = cleaned.casefold()
    if lowered in {
        "сотрудник", "сотрудника", "работник", "работника", "лицо", "лица",
        "новый сотрудник", "наименование должности",
    }:
        return False
    # A field heading such as ``Желаемая должность и зарплата`` is matched by
    # the broad ``должность`` marker with ``и зарплата`` as its value.  A
    # genuine title is not a coordinated heading and should not contain
    # salary/field-meta vocabulary.
    if lowered.startswith("и ") or re.search(
        r"\b(?:зарплат\w*|оклад\w*|доход\w*)\b", lowered
    ):
        return False
    return len(cleaned) >= 2 and not cleaned.startswith("(")


def _valid_person_field(value: str) -> bool:
    """A FIO field value must look like a name, not a field instruction.

    The grammar is intentionally case-sensitive at validation time even
    though the ``ФИО`` marker itself is case-insensitive.  This rejects
    fragments such as ``ФИО ген. директора`` while keeping a single given
    name in a semantically labelled table column.
    """

    words = re.findall(r"[А-ЯЁа-яё-]+", value)
    if not words or not all(word[0].isupper() for word in words):
        return False
    # A single title-cased common noun after the field marker is often a
    # category heading (``ФИО Туристов``), not a person's name.  Require
    # morphological name evidence for one-token values; multi-token FIOs keep
    # their strong structural evidence.
    if len(words) == 1:
        try:
            import pymorphy3

            morph = getattr(_valid_person_field, "_morph", None)
            if morph is None:
                morph = pymorphy3.MorphAnalyzer()
                setattr(_valid_person_field, "_morph", morph)
            parses = morph.parse(words[0])
            _ROLE_NORMAL_FORMS = {
                "турагент", "туроператор", "клиент", "турист", "пассажир", "заказчик",
                "исполнитель", "подрядчик", "покупатель", "продавец", "поставщик",
                "агент", "субагент", "принципал", "представитель", "заявитель",
                "истец", "ответчик", "работник", "сотрудник", "директор", "менеджер",
            }
            if any(p.normal_form in _ROLE_NORMAL_FORMS for p in parses):
                return False
            return any(
                any(marker in parse.tag for marker in ("Name", "Surn", "Patr"))
                for parse in parses
            )
        except Exception:
            # Fail conservatively when morphology is unavailable: a lone word
            # is insufficient evidence for a full-name field value.
            return False
    return True


def _valid_birth_place(value: str) -> bool:
    cleaned = value.strip().casefold()
    # Отклоняем перечисления категорий персональных данных
    if any(term in cleaned for term in {
        "пол", "гражданств", "паспорт", "сери", "номер", "адрес",
        "телефон", "данны", "справк", "выписк", "сведени", "документ",
        "место жительств", "регистраци", "проживани", "электронн"
    }):
        return False
    # Значение должно содержать топонимические признаки (г., пос., обл. и т.д.) или быть именем собственным
    return bool(re.search(
        r"(?:\b(?:г|гор|город|с|село|дер|деревня|пос|поселок|пгт|обл|область|край|р-н|район|респ|республика|рф|россия|ссср)\b|[А-ЯЁа-яё]{2,})",
        value,
        re.I,
    ))


_COUNTRIES_RU = {
    "россия", "российская федерация", "рф", "беларусь", "белоруссия", "республика беларусь",
    "казахстан", "узбекистан", "таджикистан", "кыргызстан", "киргизия", "армения", "азербайджан",
    "грузия", "молдова", "молдавия", "туркменистан", "туркмения", "украина", "абхазия", "южная осетия",
    "турция", "египет", "оаэ", "объединенные арабские эмираты", "таиланд", "тайланд", "китай", "кнр",
    "индия", "вьетнам", "индонезия", "мальдивы", "сейшелы", "шри-ланка", "куба", "доминикана",
    "мексика", "бразилия", "аргентина", "сша", "соединенные штаты америки", "соединенные штаты",
    "канада", "великобритания", "соединенное королевство", "англия", "германия", "фрг", "франция",
    "италия", "испания", "греция", "кипр", "израиль", "сербия", "черногория", "болгария", "венгрия",
    "чехия", "словакия", "польша", "финляндия", "швеция", "норвегия", "австрия", "швейцария",
    "нидерланды", "голландия", "бельгия", "португалия", "япония", "южная корея", "корея", "кндр",
    "австралия", "новая зеландия", "сингапур", "малайзия", "филиппины", "монголия", "иран", "ирак",
    "саудовская аравия", "катар", "бахрейн", "оман", "иордания", "ливан", "тунис", "марокко", "юар",
    "албания", "андорра", "босния и герцеговина", "ватикан", "дания", "ирландия", "исландия",
    "латвия", "литва", "лихтенштейн", "люксембург", "мальта", "монако", "северная македония",
    "румыния", "сан-марино", "словения", "хорватия", "эстония", "алжир", "колумбия", "чили", "перу"
}

_COUNTRIES_EN = {
    "russia", "russian federation", "rf", "rus", "belarus", "blr", "kazakhstan", "kaz",
    "uzbekistan", "uzb", "tajikistan", "tjk", "kyrgyzstan", "kgz", "armenia", "arm",
    "azerbaijan", "aze", "georgia", "geo", "moldova", "mda", "turkmenistan", "tkm",
    "ukraine", "ukr", "turkey", "turkiye", "tur", "egypt", "egy", "uae", "united arab emirates",
    "thailand", "tha", "china", "prc", "chn", "india", "ind", "vietnam", "vnm", "indonesia", "idn",
    "maldives", "mdv", "seychelles", "syc", "sri lanka", "lka", "cuba", "cub", "mexico", "mex",
    "brazil", "bra", "argentina", "arg", "usa", "us", "united states", "united states of america",
    "canada", "can", "uk", "united kingdom", "great britain", "england", "gbr",
    "germany", "deu", "france", "fra", "italy", "ita", "spain", "esp", "greece", "grc",
    "cyprus", "cyp", "israel", "isr", "serbia", "srb", "montenegro", "mne", "bulgaria", "bgr",
    "hungary", "hun", "czech republic", "czechia", "cze", "slovakia", "svk", "poland", "pol",
    "finland", "fin", "sweden", "swe", "norway", "nor", "austria", "aut", "switzerland", "che",
    "netherlands", "nld", "belgium", "bel", "portugal", "prt", "japan", "jpn", "south korea",
    "korea", "kor", "australia", "aus", "new zealand", "nzl", "singapore", "sgp"
}

_DEMONYMS = {
    "гражданин рф", "гражданка рф", "гражданин россии", "гражданка россии", "гражданин российской федерации",
    "российское", "российский", "российская", "белорусское", "белорусская", "казахстанское",
    "иностранный гражданин", "иностранная гражданка", "лицо без гражданства", "лбг", "апатрид", "бипатрид",
    "russian", "belarusian", "kazakh", "german", "french", "american", "british", "italian", "spanish"
}

_STATE_MARKERS = re.compile(
    r"\b(?:республик\w*|федераци\w*|королевств\w*|эмират\w*|соединенн\w*\s+штат\w*|княжеств\w*|герцогств\w*|султанат\w*|государств\w*|"
    r"republic|federation|kingdom|emirates|states|principality|state|sultanate)\b",
    re.I
)


def _valid_citizenship(value: str) -> bool:
    cleaned = value.strip().rstrip(".,;")
    val = cleaned.casefold()
    if val in {
        "туриста", "туристов", "клиента", "участников", "гражданина", "лица", "сторон",
        "и национальность", "в настоящее время", "при рождении", "по рождению",
        "при наличии", "при необходимости", "иного государства", "другое", "отсутствует",
        "не имеется", "не требуется", "не указано", "согласно договору"
    }:
        return False
    # Описательные фразы с предлогами не являются названием гражданства
    if re.match(r"^(?:в|при|на|по|до|для|без|с|со|от|из|о|об)\s+", val):
        return False
    if any(stop in val for stop in {"выезда", "начала тура", "паспорта", "страны", "рождени", "пол,", "паспорт"}):
        return False
    # Название гражданства/страны пишется с заглавной буквы (Россия, РФ, Belarus и т.д.) либо является демонимом
    if not (cleaned[0].isupper() or val in _DEMONYMS or cleaned.startswith("РФ")):
        return False
    if not (2 <= len(cleaned) <= 60 and not cleaned.startswith("(")):
        return False
    # Прямое совпадение со справочником стран, кодов или демонимов
    if val in _COUNTRIES_RU or val in _COUNTRIES_EN or val in _DEMONYMS:
        return True
    # Наличие признанной страны как отдельного слова (напр. 'Гражданин Республики Беларусь')
    words = set(re.findall(r"[a-zA-Zа-яёА-ЯЁ]+", val))
    if words & _COUNTRIES_RU or words & _COUNTRIES_EN:
        return True
    # Государственные формулы (Республика ..., Королевство ..., Синтетическая Федерация-0001)
    if _STATE_MARKERS.search(val):
        return True
    return False


def _valid_employer(value: str) -> bool:
    cleaned = re.sub(r"\([^)]*\)|_{2,}", "", value).strip()
    val = cleaned.casefold()
    if any(bad in val for bad in {"подпись", "фио", "ф.и.о.", "дата", "обязуется", "сохранение", "реестр", "результат", "договор"}):
        return False
    return 2 <= len(cleaned) <= 70


def _valid_insurance_policy(value: str) -> bool:
    cleaned = value.strip()
    if cleaned.casefold() in {"услуга", "услуги", "договор", "полис", "памятка", "памяткой", "является"}:
        return False
    digits = re.findall(r"\d", cleaned)
    return len(digits) >= 3


def _valid_tour_operator_registry_number(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    digits = re.sub(r"\D", "", v)
    if not (5 <= len(digits) <= 7):
        return False
    # Отсечение товарооборота, технических регламентов, единиц времени и измерений
    if re.search(r"(?i)(?:руб|коп|млн|тыс|раз|дн|час|месяц|кв\b|год|этап|шт)", v):
        return False
    return True


_TOUR_OPERATOR_INTERMEDIATE_STOP_WORDS = (
    r"закон\w*|фз\b|фкз\b|кодекс\w*|указ\w*|постановлен\w*|распоряжен\w*|приказ\w*|"
    r"договор\w*|контракт\w*|соглашен\w*|спецификаци\w*|акт\w*|сч[её]т\w*|заявк\w*|заказ\w*|протокол\w*|накладн\w*|приложен\w*|"
    r"закупк\w*|адвокат\w*|нотари\w*|патентн\w*|егрн|недвижим\w*|лицензи\w*|свидетельств\w*|сертификат\w*|гаранти\w*|полис\w*|"
    r"стать\w*|ст\b|пункт\w*|п\b|подпункт\w*|пп\b|част\w*|ч\b|раздел\w*|глав\w*|абзац\w*|"
    r"инн\b|кпп\b|огрн\w*|бик\b|паспорт\w*|снилс\b"
)


_CONTRACT_INTERMEDIATE_STOP_WORDS = (
    r"закон\w*|фз\b|фкз\b|кодекс\w*|указ\w*|постановлен\w*|распоряжен\w*|приказ\w*|"
    r"регламент\w*|инструкци\w*|указани\w*|положен\w*|правил\w*|стандарт\w*|гост\w*|снип\w*|санпин\w*|письм\w*|"
    r"решен\w*|определен\w*|приговор\w*|предписан\w*|представлен\w*|"
    r"соглашен\w*|спецификаци\w*|акт\w*|сч[её]т\w*|заявк\w*|заказ\w*|протокол\w*|накладн\w*|упд\b|ттн\b|приложен\w*|"
    r"закупк\w*|извещен\w*|лот\w*|реестр\w*|лицензи\w*|свидетельств\w*|сертификат\w*|гаранти\w*|полис\w*|"
    r"стать\w*|ст\b|пункт\w*|п\b|подпункт\w*|пп\b|част\w*|ч\b|раздел\w*|глав\w*|абзац\w*|"
    r"инн\b|кпп\b|огрн\w*|бик\b|паспорт\w*|снилс\b"
)

_ORDER_INTERMEDIATE_STOP_WORDS = (
    r"закон\w*|фз\b|фкз\b|кодекс\w*|указ\w*|постановлен\w*|распоряжен\w*|"
    r"регламент\w*|инструкци\w*|указани\w*|положен\w*|правил\w*|стандарт\w*|гост\w*|снип\w*|санпин\w*|письм\w*|"
    r"решен\w*|определен\w*|приговор\w*|предписан\w*|представлен\w*|"
    r"договор\w*|контракт\w*|соглашен\w*|спецификаци\w*|акт\w*|сч[её]т\w*|заявк\w*|заказ\w*|протокол\w*|накладн\w*|упд\b|ттн\b|приложен\w*|"
    r"закупк\w*|извещен\w*|лот\w*|реестр\w*|лицензи\w*|свидетельств\w*|сертификат\w*|гаранти\w*|полис\w*|"
    r"стать\w*|ст\b|пункт\w*|п\b|подпункт\w*|пп\b|част\w*|ч\b|раздел\w*|глав\w*|абзац\w*|"
    r"инн\b|кпп\b|огрн\w*|бик\b|паспорт\w*|снилс\b"
)

_POA_INTERMEDIATE_STOP_WORDS = (
    r"закон\w*|фз\b|фкз\b|кодекс\w*|указ\w*|постановлен\w*|распоряжен\w*|приказ\w*|"
    r"договор\w*|контракт\w*|соглашен\w*|спецификаци\w*|акт\w*|сч[её]т\w*|заявк\w*|заказ\w*|протокол\w*|накладн\w*|приложен\w*|"
    r"закупк\w*|лицензи\w*|свидетельств\w*|сертификат\w*|гаранти\w*|полис\w*|"
    r"стать\w*|ст\b|пункт\w*|п\b|подпункт\w*|пп\b|част\w*|ч\b|раздел\w*|глав\w*|абзац\w*|"
    r"инн\b|кпп\b|огрн\w*|бик\b|паспорт\w*|снилс\b"
)

_PATENT_APP_STOP_WORDS = (
    r"бронирован\w*|тур\w*|участи\w*|поездк\w*|возврат\w*|кредит\w*|заем|за[йм]\w*|"
    r"обслуживан\w*|перевод\w*|выдач\w*|подключен\w*|оказани\w*|закупк\w*|поставк\w*"
)


_BANK_ACCOUNT_VALUE = r"\d(?:[ \t\u00A0]*\d){19}"
_INN_VALUE = r"(?:\d[\s\u00a0]*){9}\d|(?:\d[\s\u00a0]*){11}\d"
_OGRN_VALUE = r"(?:\d[\s\u00a0]*){12}\d"
_OGRNIP_VALUE = r"(?:\d[\s\u00a0]*){14}\d"
_KPP_VALUE = r"(?:\d[\s\u00a0]*){8}\d"
_PHONE_VALUE = r"(?:(?:\+?7|8)[\s\u00a0().-]*)?\d(?:[\s\u00a0().-]*\d){9,10}"
_WEBSITE_VALUE = (
    r"(?:"
    r"(?:https?://|www\.)[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?:[/][^\s,;)]*)?"
    r"|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"(?:ru|com|org|net|io|biz|info|pro|edu|gov|рф)"
    r"(?:[/][^\s,;)]*)?"
    r")"
)
_EMAIL_VALUE = (
    r"[A-Za-zА-Яа-яЁё0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-zА-Яа-яЁё0-9](?:[A-Za-zА-Яа-яЁё0-9-]{0,61}[A-Za-zА-Яа-яЁё0-9])?"
    r"(?:\.[A-Za-zА-Яа-яЁё0-9](?:[A-Za-zА-Яа-яЁё0-9-]{0,61}[A-Za-zА-Яа-яЁё0-9])?)+"
)

_STRUCTURAL_HEADER_PATTERN = re.compile(
    r"(?im)^(?P<value>\s*(?:"
    r"желаемая\s+должность(?:\s+и\s+(?:зарплата|оклад))?|"
    r"дата|"
    r"навыки|знание\s+языков|опыт\s+вождения|"
    r"права(?:\s+категории\s+[A-ZА-ЯЁ0-9]+)?|"
    r"русский\s*[—-]\s*родной"
    r")\s*)$"
)
_STRUCTURAL_PUBLIC_LAW_PATTERN = re.compile(
    r"(?i)(?P<value>\b(?:"
    r"муниципальн\w+\s+образовани\w+|"
    r"субъект\s+Российской\s+Федерации"
    r")\b)"
)


_REQUISITES_TERMINATORS = (
    r"(?:\n\s*|"
    r"(?:^|[\s,;.\-–—])(?:"
    r"почтов(?:ый|ого)\s+адрес|фактическ(?:ий|ого)\s+адрес|юр\.?\s*адрес|факт\.?\s*адрес|"
    r"И\s*Н\s*Н|ИНН|К\s*П\s*П|КПП|О\s*Г\s*Р\s*Н(?:ИП)?|ОГРН(?:ИП)?|БИК|BIK|"
    r"[рpРP]\s*[/\\|.]\s*[сc]|р\.?\s*сч[её]т|расч[её]тн\w*\s+сч[её]т|"
    r"[кkКK]\s*[/\\|.]\s*[сc]|к\.?\s*сч[её]т|корр\w*\s+сч[её]т|"
    r"[лlЛL]\s*[/\\|.]\s*[сc]|лицев\w*\s+сч[её]т|"
    r"банк|банковские\s+реквизиты|"
    r"тел(?:ефон|\.|\:)?|т\.|факс|phone|tel|"
    r"email|e-mail|эл\.?\s*почт\w*|"
    r"сайт|site|web|www\.|"
    r"в\s+рамках|с\s+(?:одной|другой)\s+стороны"
    r")\b|"
    r"\s+\((?:далее)\b)"
)


# The value format is intentionally local to a labelled field.  This makes
# the analyzer useful in tables while avoiding global number false positives.
_CANDIDATES = (
    # An exact Cyrillic field label is strong evidence even in a teaching
    # template whose example checksum is intentionally invalid. OCR/Latin
    # aliases remain checksum-bound below.
    ("INN", _field(r"(?:И\s*Н\s*Н|ИНН)(?:\s*[/\\|]\s*(?:К\s*П\s*П|КПП|KPP)|\s+и\s+(?:К\s*П\s*П|КПП|KPP))?(?:\s+(?:покупателя|продавца|поставщика|исполнителя|заказчика|клиента|банка|организации|предприятия|плательщика|получателя))?", _INN_VALUE), None),
    ("INN", _field(r"(?:I\s*N\s*N|INN|UHH|IHH)(?:\s*[/\\|]\s*(?:К\s*П\s*П|КПП|KPP)|\s+и\s+(?:К\s*П\s*П|КПП|KPP))?(?:\s+(?:покупателя|продавца|поставщика|исполнителя|заказчика|клиента|банка|организации|предприятия|плательщика|получателя))?", _INN_VALUE), valid_inn),
    ("KPP", _field(r"(?:К\s*П\s*П|КПП|KPP|KITH|KTT|KTIП|КIП|(?:(?:И\s*Н\s*Н|ИНН)\s*[/\\|]\s*(?:К\s*П\s*П|КПП)|(?:И\s*Н\s*Н|ИНН)\s*и\s*(?:К\s*П\s*П|КПП))(?:\s+(?:покупателя|продавца|поставщика|исполнителя|заказчика|клиента|банка|организации|предприятия|плательщика|получателя))?[\s»”\"')\]]*(?::|№|#|N(?:O)?\.?|номер|[,;|])?\s*(?:" + _INN_VALUE + r")\s*[/\\|,\s]\s*)", _KPP_VALUE), None),
    ("OGRNIP", _field(r"(?:О\s*Г\s*Р\s*Н\s*И\s*П|ОГРНИП|OGRNIP|О\s*Г\s*Р\s*Н|ОГРН|OGRN|(?:номер\s+записи\s+в\s+|запись\s+в\s+|свидетельств(?:о|а)\s+|в\s+)?(?:ЕГРИП|EGRIP))", _OGRNIP_VALUE), valid_ogrn),

    ("OGRN", _field(r"(?:О\s*Г\s*Р\s*Н|ОГРН|OGRN|(?:номер\s+записи\s+в\s+|запись\s+в\s+|свидетельств(?:о|а)\s+|в\s+)?(?:ЕГРЮЛ|EGRUL))", _OGRN_VALUE), valid_ogrn),
    ("BIK", _field(r"(?:БИК|BIK|bHK|BVIK|БИK|ВIК)(?:\s+(?:банка|отделения|филиала|банка-получателя|банка\s+получателя|ТОФК|банка\s+клиента|банка\s+плательщика|банка\s+эмитента|[0OОoо][TtТtт][\w\s]{1,15}))?", r"\d{9}"), None),
    # An explicit SNILS label is strong evidence even for a teaching/example
    # value with an invalid checksum; checksum is useful for confidence, not
    # for deciding whether a labelled personal identifier is sensitive.
    ("SNILS", _field(r"(?:СНИЛС|SNILS|страхов(?:ый|ого|ом)\s+номер(?:а|е)?\s+индивидуального\s+лицевого\s+сч[её]та)", r"\d{3}[ -]?\d{3}[ -]?\d{3}[ -]?\d{2}"), None),
    ("PASSPORT", _field(r"(?:паспорт|серия\s+и\s+номер\s+паспорта|паспортные\s+данные)", r"\d{2}\s?\d{2}\s?\d{6}"), None),
    ("RU_CORR_ACCOUNT", _field(r"(?:[кkКK]\s*[/\\|.]\s*[сc](?:[qч][её]?[tт]?)?|[кkКK]\s*[сcчq][её]?[tт]?|корр(?:еспондентск(?:ий|ого|ому|им|ом)|\.)?\s*сч[её]т(?:а|у|ом|е)?(?:\s+банка)?|к\.?\s*сч[её]т(?:а|у|ом|е)?)", _BANK_ACCOUNT_VALUE), None),
    ("RU_ACCOUNT", _field(r"(?:[рpРP]\s*[/\\|.]\s*[сc](?:[qч][её]?[tт]?)?\.?|[рpРP]\s*[сcчq][её]?[tт]?\.?|(?:номер\s+)?расч[её]тн(?:ый|ого|ому|ым|ом)\s+сч[её]т(?:а|у|ом|е)?|(?:единый\s+)?казначейск(?:ий|ого|ому|им|ом)\s+сч[её]т(?:а|у|ом|е)?|сч[её]т(?:\s+банка)?\s+получателя|лицевой\s+сч[её]т(?:\s+получателя)?|р\.?\s*сч[её]т(?:а|у|ом|е)?)", _BANK_ACCOUNT_VALUE), None),
    ("IBAN", _field(r"(?:IBAN|сч[её]т\s+IBAN|номер\s+сч[её]та\s+IBAN|международн(?:ый|ого|ому|ым|ом)\s+сч[её]т(?:а|у|ом|е)?|IBAN\s+(?:account|number|acc)|account\s+number)", r"[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}"), valid_iban),
    ("SWIFT", _field(r"(?:SWIFT(?:\s*[/\\|-]\s*BIC)?|BIC(?:\s*[/\\|-]\s*SWIFT)|SWIFT[- ]?код\w*|код\w*\s+SWIFT|SWIFT\s+code|код\s+БИК(?:\s*[/\\|-]\s*SWIFT)|SWIFT)", r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?"), valid_swift_bic),

    ("BIRTH_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+о\s+рождени[ия]"), None),
    ("MARRIAGE_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+о\s+заключении\s+брака"), None),
    ("DIVORCE_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+о\s+расторжении\s+брака"), None),
    ("DEATH_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+о\s+смерти"), None),
    ("NAME_CHANGE_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+о\s+перемене\s+имени"), None),
    ("PATERNITY_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+об\s+установлении\s+отцовства"), None),
    ("ADOPTION_CERTIFICATE", _certificate(r"свидетельств(?:о|а)\s+об\s+усыновлении"), None),
    ("CIVIL_STATUS_ACT", _number(r"(?:номер\s+)?записи\s+акта\s+гражданского\s+состояния", r"\d{1,10}"), None),
    ("FOREIGN_PASSPORT", _field(r"(?:заграничный\s+паспорт|загранпаспорт|паспорт\s+гражданина\s+РФ\s+для\s+выезда)", r"\d{2}\s?\d{7}"), None),
    ("DRIVER_LICENSE", _field(r"(?:водительское\s+удостоверение|удостоверение\s+водителя)", r"\d{2}\s?\d{2}\s?\d{6}"), None),
    ("MILITARY_ID", _field(r"(?:военный\s+билет|удостоверение\s+военнослужащего)", r"[А-ЯЁ]{1,3}\s?\d{6,8}"), None),
    ("RESIDENCE_PERMIT", _field(r"(?:вид\s+на\s+жительство|ВНЖ)", r"\d{2}\s?\d{7}"), None),
    ("MIGRATION_CARD", _field(r"миграционная\s+карта", r"[А-ЯЁA-Z]{2}\s?\d{7,9}"), None),
    ("VISA_NUMBER", _field(r"(?:номер\s+визы|виз(?:а|ы)\s*(?:№|N|#|номер))", r"[A-Z0-9][A-Z0-9./-]{3,19}"), _valid_visa),
    ("WORK_PERMIT", _field(r"(?:разрешение\s+на\s+работу|патент\s+на\s+работу)", r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9./-]{4,19}"), None),

    ("CADASTRAL_NUMBER", _field(r"кадастров(?:ый|ого)\s+номер(?:а)?", r"\d{1,3}:\d{1,3}:\d{1,7}:\d{1,7}"), None),
    ("EGRN_RECORD_NUMBER", _field(r"(?:номер\s+)?записи\s+ЕГРН", r"[0-9]{2}-[0-9]{2}/[0-9]{3}-[0-9]{4,8}/[0-9]{2,4}"), None),
    ("PROPERTY_RIGHT_NUMBER", _field(r"(?:номер\s+)?государственной\s+регистрации\s+права", r"[0-9]{2}:[0-9]{2}:[0-9]{6,12}-[0-9]{2}/[0-9]{2,4}-[0-9]{1,6}"), None),
    ("PROPERTY_CONDITIONAL_NUMBER", _field(r"условный\s+номер\s+объекта", r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9:/_-]{3,30}"), None),
    ("VEHICLE_VIN", _field(r"VIN", r"[A-HJ-NPR-Z0-9]{17}"), None),
    ("VEHICLE_PLATE", _field(r"(?:государственный\s+регистрационный\s+знак|госномер|номер\s+автомобиля)", r"[А-ЯЁA-Z]\d{3}[А-ЯЁA-Z]{2}\s?\d{2,3}"), None),
    ("PTS_NUMBER", _field(r"(?:номер\s+)?ПТС", r"\d{2}\s?\d{2}\s?\d{6}"), None),
    ("STS_NUMBER", _field(r"(?:номер\s+)?СТС", r"\d{2}\s?\d{2}\s?\d{6}"), None),

    ("LAWYER_ID_NUMBER", _field(r"(?:удостоверение\s+адвоката|адвокатское\s+удостоверение)", r"(?:серия\s*)?[А-ЯЁA-Z0-9-]{1,8}\s*(?:№\s*)?\d{4,10}"), None),
    ("LAWYER_REGISTRY_NUMBER", _field(r"(?:реестровый\s+номер\s+адвоката|номер\s+адвоката\s+в\s+реестре)", r"\d{4,10}(?:/[А-ЯЁA-Z0-9-]{1,10})?"), None),
    ("PATENT_ATTORNEY_NUMBER", _field(r"(?:реестровый\s+номер\s+патентного\s+поверенного|номер\s+патентного\s+поверенного)", r"\d{3,8}"), None),
    ("TOUR_OPERATOR_REGISTRY_NUMBER", _field(
        r"(?!(?:реестров\w*\s+(?:№|N|#|номер\w*)?\s*(?:закупк\w*|адвокат\w*|нотари\w*|патентн\w*|егрн|недвижим\w*|программ\w*|баз\w*\s+данных|контракт\w*|договор\w*)))"
        r"(?:"
        r"реестров(?:ый|ого|ому|ом)\s+номер(?:а|у|ом|е)?(?:\s+(?!\b(?:" + _TOUR_OPERATOR_INTERMEDIATE_STOP_WORDS + r")\b)[^\n;.!?№N#]{0,80}?)?(?:\s+(?:в\s+)?(?:ефрт|едином\s+федеральном\s+реестре(?:\s+туроператоров)?|реестре(?:\s+туроператоров)?))"
        r"|реестров(?:ый|ого|ому|ом)\s+номер(?:а|у|ом|е)?\s+(?:в\s+)?ефрт"
        r"|реестров(?:ый|ого|ому|ом)\s+номер(?:а|у|ом|е)?\s+туроператора"
        r"|номер(?:а)?\s+туроператора\s+в\s+(?:ефрт|реестре)"
        r"|номер(?:а)?\s+в\s+ефрт"
        r"|сведения\s+(?:о\s+туроператоре\s+)?в\s+ефрт"
        r"|ефрт"
        r"|реестров(?:ый|ого|ому|ом)\s+номер(?:а|у|ом|е)?"
        r")",
        r"(?:(?:РТО|МВТ|ВНТ|МТ3)\s*[-–—№N#]?\s*)?\d{5,7}"
    ), _valid_tour_operator_registry_number),
    ("COURT_CASE_NUMBER", _field(r"(?:(?:номер\s+)?(?:судебного|гражданского|арбитражного|административного|уголовного)?\s*дел[аоуе]|по\s+делу)", r"[А-ЯЁA-Z]?\d{1,3}-\d{1,8}/\d{2,4}(?:-\d{1,4}(?:-\d{1,4})*)?"), _valid_court_case),
    ("COURT_CASE_NUMBER", _field(
        r"определени(?:е|я|ем|и)(?:\s+[^\n№N]{1,180}?)?",
        r"\d{1,3}-[А-ЯЁA-Z]{1,4}\d{1,4}-\d{1,8}"
    ), _valid_court_case),
    ("CRIMINAL_CASE_NUMBER", _field(r"номер\s+уголовного\s+дела", r"\d{1,5}-\d{1,8}/\d{2,4}"), None),
    ("KUSP_NUMBER", _field(r"(?:номер\s+)?КУСП", r"\d{4,12}"), None),
    ("ENFORCEMENT_PROCEEDING_NUMBER", _field(r"(?:номер\s+)?исполнительного\s+производства", r"\d{1,10}/\d{2}/\d{2,6}-ИП"), None),
    ("NOTARY_REGISTER_NUMBER", _field(r"(?:номер\s+)?нотариального\s+реестра", r"\d{1,12}"), None),
    ("POWER_OF_ATTORNEY_NUMBER", _field(
        r"(?:"
        r"(?<!без\s)доверенност(?:ь|и|ью|ей)"
        r"(?:\s+(?!\b(?:" + _POA_INTERMEDIATE_STOP_WORDS + r")\b)[^\n;.!?№N#]{1,100}?)?"
        r"(?:\s+от\s+(?:[«\"“„]?\d{1,2}[»\"”]?\s+[А-ЯЁа-яё]+\s+\d{4}(?:\s*(?:г\.|года?))?|\d{2}\.\d{2}\.\d{4}(?:\s*г\.)?))"
        r"\s*(?:№|N|#|номер)"
        r"|(?<!без\s)(?:номер\s+)?доверенности"
        r"|(?<!без\s)доверенност(?:ь|и)\s*(?:№|N|#|номер)"
        r")",
        r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{1,20}"
    ), _valid_power_of_attorney),

    ("PATENT_NUMBER", _field(r"номер\s+патента", r"\d{4,10}"), None),
    ("PATENT_APPLICATION_NUMBER", _field(
        r"(?:"
        r"(?:номер\s+)?(?:патентн\w*\s+)?заявк[аиеу]\s+на\s+(?:изобретение|полезную\s+модель|промышленный\s+образец|программу\s+для\s+ЭВМ|товарный\s+знак)|патентн\w*\s+заявк[аиеу]"
        r"|(?!заявк[аиеу]\s+(?:на\s+)?(?:" + _PATENT_APP_STOP_WORDS + r"))заявк[аиеу]"
        r")",
        r"\d{8,12}"
    ), None),
    ("TRADEMARK_NUMBER", _field(r"(?:номер\s+)?регистрации\s+товарного\s+знака", r"\d{4,10}"), None),
    ("TRADEMARK_APPLICATION_NUMBER", _field(r"номер\s+заявки\s+на\s+товарный\s+знак", r"\d{8,12}"), None),
    ("INDUSTRIAL_DESIGN_NUMBER", _field(r"номер\s+промышленного\s+образца", r"\d{4,10}"), None),
    ("UTILITY_MODEL_NUMBER", _field(r"номер\s+полезной\s+модели", r"\d{4,10}"), None),
    ("SOFTWARE_REGISTRATION_NUMBER", _field(r"(?:(?:номер\s+)?государственной\s+регистрации\s+программы\s+для\s+ЭВМ|в\s+Реестре\s+программ\s+для\s+ЭВМ|программ(?:ы)?\s+для\s+ЭВМ\s*(?:№|N|#|номер))", r"\d{6,12}"), None),
    ("DATABASE_REGISTRATION_NUMBER", _field(r"(?:номер\s+)?государственной\s+регистрации\s+базы\s+данных", r"\d{6,12}"), None),
    ("TOPOLOGY_REGISTRATION_NUMBER", _field(r"(?:номер\s+)?регистрации\s+топологии", r"\d{4,12}"), None),
    ("WIPO_APPLICATION_NUMBER", _field(r"(?:номер\s+)?международной\s+заявки", r"PCT/[A-Z]{2}\d{4}/\d{5}"), None),
    ("PRIORITY_APPLICATION_NUMBER", _field(r"(?:номер\s+)?приоритетной\s+заявки", r"[A-ZА-ЯЁ]{0,2}\d{6,12}(?:/[A-ZА-ЯЁ0-9-]{1,8})?"), None),

    ("OMS_POLICY", _field(r"(?:полис\s+ОМС|ОМС)", r"\d{16}"), None),
    ("DMS_POLICY", _field(r"(?:полис\s+ДМС|ДМС)", r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9-]{5,20}"), None),
    ("INSURANCE_POLICY", _field(r"(?:номер\s+)?страхово(?:й|го)\s+полис(?:а)?", r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9./-]{4,24}"), _valid_insurance_policy),
    ("MEDICAL_RECORD_NUMBER", _field(r"(?:номер\s+)?медицинской\s+карты", r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9./-]{2,20}"), None),
    ("DISABILITY_CERTIFICATE", _field(r"(?:номер\s+)?справки\s+об\s+инвалидности", r"[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9./-]{2,20}"), None),
    ("SICK_LEAVE_NUMBER", _field(r"(?:номер\s+)?листка\s+нетрудоспособности", r"\d{10,16}"), None),
    ("DIAGNOSIS", _text_field(r"диагноз", r"[^\n;,.]{2,80}"), None),
    ("HEALTH_INFORMATION", _text_field(r"(?:сведения\s+о\s+состоянии\s+здоровья|медицинские\s+сведения)", r"[^\n;]{2,120}"), None),

    ("BANK_CARD", _field(r"(?:номер\s+)?банковской\s+карты", r"(?:\d[ -]?){15,18}\d"), valid_luhn),
    ("PERSONAL_ACCOUNT", _field(r"(?:л\s*[/\\|.]\s*[сc]|лицевой\s+сч[её]т|номер\s+лицевого\s+сч[её]та)", r"\d{8,25}"), None),
    ("CONTRACT_NUMBER", _field(
        r"(?:"
        r"(?:гражданско-правов\w*\s+договор(?:а|у|ом|е)?|абонентск\w*\s+договор(?:а|у|ом|е)?|договор(?:а|у|ом|е)?|контракт(?:а|у|ом|е)?)"
        r"(?:\s+(?!\b(?:" + _CONTRACT_INTERMEDIATE_STOP_WORDS + r")\b)[^\n;.!?№N#]{1,120}?)?"
        r"(?:\s+от\s+(?:[«\"“„]?\d{1,2}[»\"”]?\s+[А-ЯЁа-яё]+\s+\d{4}(?:\s*(?:г\.|года?))?|\d{2}\.\d{2}\.\d{4}(?:\s*г\.)?))?"
        r"\s*(?:№|N|#|номер)"
        r"|ГК\s*(?:№|N|#)?|номер\s+договора"
        r")",
        r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{1,25}"
    ), _valid_contract_number),
    ("CLIENT_NUMBER", _field(r"(?:номер\s+клиента|клиентский\s+номер)", r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{2,25}"), None),
    ("ORDER_NUMBER", _field(
        r"(?:номер\s+(?:заказа|заявки|бронирования)|(?:заказ(?:[ауе]|ом|ы|ов)?|заявк[аиуе]|заявкой|бронировани[ея]|ваучер[ауе]?)\s*(?:№|N|#|номер|(?=\d)))",
        r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{2,25}"
    ), None),
    ("IP_ADDRESS", _field(r"IP[- ]?адрес", r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}"), None),
    ("MAC_ADDRESS", _field(r"MAC[- ]?адрес", r"(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}"), None),
    ("USER_ACCOUNT", _field(r"(?:логин|идентификатор\s+пользователя|учётная\s+запись)", r"[A-Za-zА-Яа-яЁё0-9_.@-]{3,40}"), None),
    ("TELEGRAM_NICK", _field(r"(?:Telegram|телеграм)[ -]?(?:аккаунт|логин|никнейм|username)?", r"@[A-Za-z0-9_]{4,32}"), None),
    ("SOCIAL_NETWORK_ID", _field(r"(?:ID|идентификатор)\s+(?:пользователя|социальной\s+сети)", r"\d{4,20}"), None),
    ("MESSENGER_ID", _field(r"(?:ID|идентификатор)\s+(?:мессенджера|чата)", r"[A-Za-z0-9_-]{4,32}"), None),
    ("PHONE_NUMBER", _field(r"(?:номер\s+телефона|телефон|тел\.|мобильный\s+телефон|телефон\s+для\s+связи)", _PHONE_VALUE), lambda value: len(value) in (10, 11)),

    # Context-only categories of personal data.  Their values do not have a
    # globally unique shape, therefore the field marker is mandatory and the
    # value is intentionally limited to one form line/field.
    ("ADDRESS", _field(
        r"(?:адрес\s+регистрации|юридический\s+адрес|почтовый\s+адрес|"
        r"адрес\s+места\s+(?:нахождения|жительства)|место\s+жительства|"
        r"место\s+нахождения\s+и\s+адрес(?:\s+(?:общества|организации|юридического\s+лица))?)",
        r"(?:(?!" + _REQUISITES_TERMINATORS + r")[^\n;]){5,500}"
    ), _valid_address_field),
    # Word tables commonly split one labelled address across paragraphs.
    # A strong field marker lets us consume the complete value up to the next
    # requisites field while preserving the marker itself.
    ("ADDRESS", re.compile(
        r"(?i)(?<![\wА-Яа-яЁё])(?:"
        r"(?:место\s+нахождения\s+и\s+адрес(?:\s+(?:общества|организации|юридического\s+лица))?|"
        r"адрес\s+регистрации|юридическ(?:ий|ого)\s+адрес|почтов(?:ый|ого)\s+адрес|"
        r"фактическ(?:ий|ого)\s+адрес|юр\.?\s*адрес|факт\.?\s*адрес|"
        r"адрес\s+местонахождения|адрес\s+места\s+нахождения|по\s+адресу)\s*(?:(?::|[-–—])\s*)?|"
        r"(?<!\bв\s)адрес\s*[:—–-]\s*"
        r")"
        r"(?P<value>"
        r"(?:(?!" + _REQUISITES_TERMINATORS + r")[\s\S]){5,700}?)"
        r"(?=" + _REQUISITES_TERMINATORS + r"|\Z)"
    ), _valid_address_field),
    ("BIRTH_DATE", _field(r"дата\s+рождения", r"(?:0?[1-9]|[12]\d|3[01])\s*[./-]\s*(?:0?[1-9]|1[0-2])\s*[./-]\s*(?:19|20)\d{2}"), None),
    ("BIRTH_PLACE", _text_field(r"место\s+рождения", r"(?:(?!,\s*паспорт\b)[^\n;]){2,120}"), _valid_birth_place),
    ("CITIZENSHIP", _text_field(r"гражданств(?:о|а)(?:\s*\([^)]*\))?", r"[А-ЯЁA-Z][A-Za-zА-Яа-яЁё0-9 .'-]{1,79}"), _valid_citizenship),
    ("JOB_TITLE", _text_field(r"должность", r"[^\n;]{2,100}"), _valid_job_title),
    ("EMPLOYER", _text_field(r"(?:место\s+работы|работодатель)", r"[^\n;]{2,120}"), _valid_employer),
    ("RELATIVE", _text_field(r"(?:родственная\s+связь|сведения\s+о\s+родственнике)", r"[^\n;]{2,120}"), None),
    # ``образование`` is a sensitive field only when it is an explicit field
    # marker.  In ``муниципальное образование`` it is a public-law noun.
    ("EDUCATION", _field(
        r"(?:(?<=\()образование\b(?=\))|(?<=«)образование\b(?=»)|"
        r"(?<=\")образование\b(?=\")|"
        r"(?<!муниципальное\s)(?<!адвокатское\s)образование\b"
        r"(?=\s*(?::|\t|составляет\b|равен(?:а|о)?\b|указан(?:а|о)?\b|значение\b))|"
        r"(?:^|\n|;|\|)\s*образование\b(?=\s*(?:\||[-–—]|$)))",
        r"[^\n;]{2,120}"
    ), None),
    ("INCOME", _field(r"(?:размер\s+дохода|доход)", r"\d[\d ]{0,14}(?:[.,]\d{2})?\s*(?:руб(?:\.|лей)?|₽)"), None),
    ("CRIMINAL_RECORD", _text_field(r"(?:сведения\s+о\s+судимости|судимость)", r"[^\n;]{2,120}"), None),
    ("NATIONALITY", _text_field(r"национальность", r"[^\n;]{2,80}"), None),
    ("RELIGION", _text_field(r"(?:вероисповедание|религия)", r"[^\n;]{2,100}"), None),
    ("POLITICAL_INFO", _text_field(r"(?:политические\s+взгляды|политическая\s+деятельность)", r"[^\n;]{2,120}"), None),
    ("BIOMETRIC_DATA", _text_field(r"(?:биометрические\s+данные|биометрия)", r"[^\n;]{2,120}"), None),
)

_EXTRA_CANDIDATES = (
    ("PASSPORT_DIVISION_CODE", _field(r"(?:код\s+подразделени(?:я|й|ем|ю)|код\s+подр\.?)", r"\d{3}[-\s]?\d{3}"), None),
    ("PASSPORT_SERIES", _field(r"(?:сер(?:и[яи]|ей|\.)\s*(?:паспорта)?|серия\s+паспорта)", r"\d{2}\s?\d{2}|\d{4}"), None),
    ("PASSPORT_NUMBER", _field(r"(?:номер\s+паспорта|№\s*паспорта|паспорт\s*№)", r"\d{6}"), None),
    ("NOTARY_REGISTER_NUMBER", re.compile(
        r"(?i)(?:зарегистрирован\w*\s+в\s+реестре|реестровый\s+номер)"
        r"\s*:\s*(?:№\s*)?(?P<value>\d{1,4}(?:/[А-ЯЁA-Z0-9-]{1,20}){1,5})"
    ), None),
    ("TOUR_OPERATOR_REGISTRY_NUMBER", re.compile(
        r"(?i)(?<![\wА-Яа-яЁё])(?P<value>(?:РТО|МВТ|ВНТ|МТ3)\s*[-–—№N#]?\s*\d{5,7})(?![\wА-Яа-яЁё])(?![\s]*(?:руб|коп|млн|тыс|раз|дн|час|месяц|кв\b|год|этап|шт))"
    ), _valid_tour_operator_registry_number),
    ("BIRTH_PLACE", re.compile(
        r"(?i)место\s+рождения\s*[:—–-]\s*(?P<value>[^\n;]{2,240}?)(?=,\s*паспорт\b)"
    ), _valid_birth_place),
    ("CONTRACT_NUMBER", re.compile(
        r"(?i)\bдоговор\w*(?:(?!\b(?:" + _CONTRACT_INTERMEDIATE_STOP_WORDS + r")\b)[^;\n!?]){0,120}?\s(?:№|N|#|номер)\s*(?P<value>[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{1,25})"
    ), _valid_contract_number),
    ("DOCUMENT_NUMBER", _field(
        r"приказ(?:а|ом|е|у)?(?:\s+(?!\b(?:" + _ORDER_INTERMEDIATE_STOP_WORDS + r")\b)[^\n;.!?№N#]{0,100}?)?",
        r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{0,25}"
    ), _valid_contract_number),
    ("DOCUMENT_NUMBER", _field(
        r"приказ(?:а|ом|е|у)?(?:\s+(?!\b(?:" + _ORDER_INTERMEDIATE_STOP_WORDS + r")\b)[^\n;.!?]{0,140}?)?\bот\s+\d{2}\.\d{2}\.\d{4}",
        r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{0,25}"
    ), _valid_contract_number),
    # In a semantically labelled table column even a single given name is a
    # person value. Outside that context single capitalized words are left to
    # the native person analyzer and its linguistic checks.
    ("PER", _field(r"ФИО", r"[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){0,2}"), _valid_person_field),
    ("PER", re.compile(
        r"(?i)\b(?:турагент|менеджер|куратор|администратор|гид|представитель|турист|заявитель|пассажир|клиент|специалист|бухгалтер|секретарь|юрист|адвокат)\s+(?P<value>[А-ЯЁ][а-яё]+)\b"
    ), _valid_person_field),
    # Synthetic table context supplied for a bare graduation year directly
    # below a merged education section heading.
    ("DATE", _field(r"(?:дата\s+образования|год\s+окончания)", r"(?:19|20)\d{2}"), None),
    ("EIK_BULSTAT", _field(r"(?:ЕИК|EIK|БУЛСТАТ|BULSTAT)", r"\d{9}|\d{10}|\d{13}"), None),
    # A valid checksum is sufficient semantic evidence even if a spreadsheet
    # separated the number from its neighbouring field label.
    ("INN", re.compile(r"(?<!\d)(?P<value>\d{10}|\d{12})(?!\d)"), valid_inn),
    # A 10/12-digit tax number followed by a slash and nine digits is the
    # conventional compound INN/KPP representation.  The structure itself is
    # strong evidence even when a merged Word cell has lost its nearby label.
    ("KPP", re.compile(
        r"(?<!\d)(?:\d{10}|\d{12})\s*[/\\|]\s*(?P<value>\d{9})(?!\d)"
    ), None),
    ("OGRN", re.compile(r"(?<!\d)(?P<value>\d{13})(?!\d)"), valid_ogrn),
    ("OGRNIP", re.compile(r"(?<!\d)(?P<value>\d{15})(?!\d)"), valid_ogrn),
    ("IBAN", re.compile(r"(?<![A-Za-z0-9])(?P<value>[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30})(?![A-Za-z0-9])"), valid_iban),
    ("PHONE_NUMBER", re.compile(
        r"(?<!\d)(?P<value>(?:\+7|8)[\s\u00a0().-]*\d(?:[\s\u00a0().-]*\d){9})(?!\d)"
    ), lambda value: len(value) == 11),
    # Unlabelled document numbers in compact passenger/person records are
    # identified by their semantic neighbours: birth date, sex and country.
    # Split into 4-digit series and 6-digit number.
    ("PASSPORT_SERIES", re.compile(
        r"(?:\d{2}\.\d{2}\.\d{4}\s+(?:муж|жен)\w*\s+"
        r"(?:РФ|Россия|Российская\s+Федерация)\s+)"
        r"(?P<value>\d{4})(?=\d{6}(?!\d))",
        re.I,
    ), None),
    ("PASSPORT_NUMBER", re.compile(
        r"(?:\d{2}\.\d{2}\.\d{4}\s+(?:муж|жен)\w*\s+"
        r"(?:РФ|Россия|Российская\s+Федерация)\s+\d{4})"
        r"(?P<value>\d{6})(?!\d)",
        re.I,
    ), None),
    ("PERSON_SALUTATION", _field(r"(?:Уважаем(?:ый|ая)|Дорог(?:ой|ая)|Господин|Госпожа)", r"[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){0,2}"), _valid_person_field),
    # Structural spans are embedded before native NER runs.  They have no
    # placeholder and therefore remain readable while preventing headings and
    # public-law terminology from becoming JOB_TITLE/EDUCATION entities.
    ("STRUCTURAL_HEADER", _STRUCTURAL_HEADER_PATTERN, None),
    ("STRUCTURAL_PUBLIC_LAW", _STRUCTURAL_PUBLIC_LAW_PATTERN, None),
    ("EMAIL", re.compile(
        # A sentence-ending dot is punctuation, not evidence that the email
        # continues.  Domain labels are already consumed greedily here.
        r"(?<![\w.+-])(?P<value>" + _EMAIL_VALUE + r")(?![\w-])", re.I
    ), None),
    ("WEBSITE", re.compile(r"(?<![\w@])(?P<value>" + _WEBSITE_VALUE + r")", re.I), None),
)

_PASSPORT_COMPOUND_PATTERN = re.compile(
    r"(?i)(?<![\wА-Яа-яЁё])"
    r"(?P<prefix>"
    r"(?:паспорт(?:\s+гражданина\s+(?:РФ|Российской\s+Федерации))?"
    r"|паспортные\s+данные"
    r"|пасп\.?"
    r"|документ"
    r"|удостоверение\s+личности"
    r"|сер(?:и[яи]|ей|\.)\s+паспорта"
    r"|сер(?:и[яи]|ей|\.))"
    r"[\s»”\"'\)\]]*"
    r"(?:(?::|[-–—,;|])\s*)?"
    r"(?:сер(?:и[яи]|ей|\.)[\s:]*)?"
    r")"
    r"(?P<series>\d{2}\s?\d{2}|\d{4})"
    r"(?P<sep>[\s»”\"'\)\]]*(?:(?:№|номер|N(?:O)?\.?|[-–—,;|])\s*|\s+))"
    r"(?P<number>\d{6})"
    r"(?![\wА-Яа-яЁё])"
)

_BOOKING_LIST_PATTERN = re.compile(
    r"(?i)\b(?:по\s+(?:трём|двум|четырём|нескольким|\d+)\s+)?(?:заявк(?:ам|ами|ах|и)|заказ(?:ам|ами|ах|ы))\s*[:\s]+(?P<numbers>\d{5,10}(?:[,\s]+\d{5,10})*)"
)


def _iter_candidate_details(text):
    """Yield validated candidates with evidence and value coordinates."""
    found = []
    opaque = {
        "BIRTH_CERTIFICATE", "MARRIAGE_CERTIFICATE", "DIVORCE_CERTIFICATE", "DEATH_CERTIFICATE",
        "NAME_CHANGE_CERTIFICATE", "PATERNITY_CERTIFICATE", "ADOPTION_CERTIFICATE",
        "MILITARY_ID", "MIGRATION_CARD", "VISA_NUMBER", "WORK_PERMIT",
        "CADASTRAL_NUMBER", "EGRN_RECORD_NUMBER", "PROPERTY_RIGHT_NUMBER", "PROPERTY_CONDITIONAL_NUMBER",
        "VEHICLE_VIN", "VEHICLE_PLATE", "LAWYER_ID_NUMBER", "LAWYER_REGISTRY_NUMBER", "TOUR_OPERATOR_REGISTRY_NUMBER",
        "COURT_CASE_NUMBER", "CRIMINAL_CASE_NUMBER", "ENFORCEMENT_PROCEEDING_NUMBER", "POWER_OF_ATTORNEY_NUMBER",
        "WIPO_APPLICATION_NUMBER", "PRIORITY_APPLICATION_NUMBER",
        "DMS_POLICY", "INSURANCE_POLICY", "MEDICAL_RECORD_NUMBER", "DISABILITY_CERTIFICATE",
        "DIAGNOSIS", "HEALTH_INFORMATION",
        "CONTRACT_NUMBER", "CLIENT_NUMBER", "ORDER_NUMBER", "DOCUMENT_NUMBER", "IP_ADDRESS", "MAC_ADDRESS",
        "IBAN", "SWIFT",
        "USER_ACCOUNT", "TELEGRAM_NICK", "MESSENGER_ID", "PERSON_SALUTATION", "PER",
        "ADDRESS",
        "STRUCTURAL_HEADER", "STRUCTURAL_PUBLIC_LAW", "EMAIL", "WEBSITE",
        "BIRTH_DATE", "BIRTH_PLACE", "CITIZENSHIP", "JOB_TITLE", "EMPLOYER", "RELATIVE",
        "EDUCATION", "INCOME", "CRIMINAL_RECORD", "NATIONALITY", "RELIGION",
        "POLITICAL_INFO", "BIOMETRIC_DATA",
    }
    # Связка серии и номера общегражданского паспорта: разделяем на [СерияПаспорта] и [НомерПаспорта]
    for match in _PASSPORT_COMPOUND_PATTERN.finditer(text):
        s_raw = match.group("series")
        n_raw = match.group("number")
        found.append((
            "PASSPORT_SERIES", _digits(s_raw),
            match.start("series"), match.end("series"),
            match.start("series"), match.end("series"),
        ))
        found.append((
            "PASSPORT_NUMBER", _digits(n_raw),
            match.start("number"), match.end("number"),
            match.start("number"), match.end("number"),
        ))
    for match in _BOOKING_LIST_PATTERN.finditer(text):
        raw_numbers = match.group("numbers")
        for num_m in re.finditer(r"\b\d{5,10}\b", raw_numbers):
            start = match.start("numbers") + num_m.start()
            end = match.start("numbers") + num_m.end()
            val = num_m.group(0)
            found.append((
                "ORDER_NUMBER", val,
                start, end,
                start, end,
            ))
    for kind, pattern, validator in tuple(_CANDIDATES) + tuple(_EXTRA_CANDIDATES):
        for match in pattern.finditer(text):
            raw_value = match.group("value")
            value_end = match.end("value")
            if kind in opaque:
                if kind == "JOB_TITLE":
                    sub_match = re.search(r"\s*(?:\([^)]*\)|_{2,})", raw_value)
                    if sub_match:
                        raw_value = raw_value[:sub_match.start()]
                        value_end = match.start("value") + len(raw_value)
                while raw_value and raw_value[-1] in " \t\r\n|.,;":
                    raw_value = raw_value[:-1]
                    value_end -= 1
            if kind == "PHONE_NUMBER":
                if re.search(r"\b(?:0?[1-9]|[12]\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:19|20)\d{2}\b", raw_value):
                    continue
                value = _digits(raw_value)
            elif kind in opaque or kind == "WEBSITE":
                value = _upper(raw_value).strip()
            else:
                value = _digits(raw_value)
                if kind == "RU_ACCOUNT" and value.startswith("30101"):
                    kind = "RU_CORR_ACCOUNT"
            if not value:
                continue
            validation_value = raw_value.strip() if kind in {"PER", "CITIZENSHIP", "BIRTH_PLACE", "JOB_TITLE", "EMPLOYER", "RELATIVE"} else value
            if validator is not None and not validator(validation_value):
                continue
            # The marker is semantic evidence, not personal data.  Pullenti
            # therefore publishes the exact value coordinates; the field
            # label remains readable in prose and in the neighbouring table
            # cell after anonymisation.
            found.append((
                kind, value,
                match.start(), value_end,
                match.start("value"), value_end,
            ))
    email_ranges = [
        (item[4], item[5]) for item in found if item[0] == "EMAIL"
    ]
    found = [
        item for item in found
        if not (
            item[0] == "WEBSITE"
            and any(start <= item[4] and item[5] <= end for start, end in email_ranges)
        )
    ]
    unique = {}
    for item in found:
        key = (item[0], item[4], item[5])
        unique.setdefault(key, item)
    yield from sorted(unique.values(), key=lambda item: (item[2], -(item[3] - item[2]), item[0]))


def iter_candidates(text):
    """Yield ``(kind, value, start, end)`` for the sensitive value only."""
    for kind, value, _evidence_start, _evidence_end, value_start, value_end in _iter_candidate_details(text):
        yield kind, value, value_start, value_end


def _tokens_for_span(first_token, start, end):
    begin = None
    finish = None
    token = first_token
    while token is not None:
        if begin is None and token.begin_char <= start <= token.end_char:
            begin = token
        if token.begin_char < end and token.end_char >= end - 1:
            finish = token
            break
        token = token.next0_
    return begin, finish


class LegalEntityAnalyzer(Analyzer):
    ANALYZER_NAME = "LEGALENTITY"
    _initialized = False
    _lock = threading.Lock()

    @property
    def name(self):
        return self.ANALYZER_NAME

    @property
    def caption(self):
        return "Юридические и персональные идентификаторы"

    @property
    def description(self):
        return "Контекстные документы, номера дел, имущество, ИС, медицина и реквизиты"

    @property
    def progress_weight(self):
        return 1

    def clone(self):
        return LegalEntityAnalyzer()

    def create_referent(self, type0_):
        if type0_ == LegalEntityReferent.OBJ_TYPENAME:
            return LegalEntityReferent()
        return None

    def process(self, kit):
        analyzer_data = kit.get_analyzer_data(self)
        matches = []
        all_candidates = list(_iter_candidate_details(kit.sofa.text))
        granular_passport_spans = [
            (c[4], c[5]) for c in all_candidates
            if c[0] in ("PASSPORT_SERIES", "PASSPORT_NUMBER")
        ]
        for kind, value, evidence_start, evidence_end, value_start, value_end in all_candidates:
            if kind == "PASSPORT" and any(
                max(value_start, s) < min(value_end, e) for s, e in granular_passport_spans
            ):
                continue
            begin, finish = _tokens_for_span(kit.first_token, evidence_start, evidence_end)
            if begin is not None and finish is not None:
                matches.append((kind, value, value_start, value_end, begin, finish))
        for kind, value, value_start, value_end, begin, finish in reversed(matches):
            referent = analyzer_data.register_referent(
                LegalEntityReferent(kind, value, value_start, value_end)
            )
            kit.embed_token(ReferentToken(referent, begin, finish))

    @classmethod
    def initialize(cls):
        with cls._lock:
            if cls._initialized:
                return
            ProcessorService.register_analyzer(cls())
            cls._initialized = True
