# -*- coding: utf-8 -*-
"""Валидация ИНН, ОГРН/ОГРНИП, IBAN, SWIFT и адресов."""
from __future__ import annotations

import re

class ValidationError(Exception):
    """Базовый класс для ошибок валидации."""
    pass

class InvalidFormat(ValidationError):
    """Ошибка: Неверный формат."""
    pass

class InvalidLength(ValidationError):
    """Ошибка: Неверная длина."""
    pass

class InvalidChecksum(ValidationError):
    """Ошибка: Неверная контрольная сумма."""
    pass

class InvalidComponent(ValidationError):
    """Ошибка: Неверный компонент номера."""
    pass

def _clean_util(number, chars=None):
    number_str = str(number)
    if chars is None or chars == ' ':
        return re.sub(r"[\s\u00a0]+", "", number_str)
    trans_table = str.maketrans('', '', chars)
    return number_str.translate(trans_table)

def _isdigits_util(value):
    return str(value).isdigit()

def _calc_company_check_digit_inn(number):
    weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    return str(sum(w * int(n) for w, n in zip(weights, number)) % 11 % 10)

def _calc_personal_check_digits_inn(number):
    weights = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    d1 = str(sum(w * int(n) for w, n in zip(weights, number)) % 11 % 10)
    weights = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
    d2 = str(sum(w * int(n) for w, n in zip(weights, number[:10] + d1)) % 11 % 10)
    return d1 + d2

def validate_inn(number):
    number = _clean_util(number, ' ')
    if not _isdigits_util(number):
        raise InvalidFormat("ИНН должен содержать только цифры.")
    length = len(number)
    if length not in (10, 12):
        raise InvalidLength("ИНН должен состоять из 10 или 12 цифр.")
    checksum_valid = (_calc_company_check_digit_inn(number) == number[-1]) if length == 10 else (_calc_personal_check_digits_inn(number) == number[-2:])
    if not checksum_valid:
        raise InvalidChecksum(f"Неверная контрольная сумма ИНН-{length}.")
    return number

def is_valid_inn(number):
    try:
        validate_inn(number)
        return True
    except ValidationError:
        return False

def validate_ogrn_ogrnip(number):
    number = _clean_util(number, ' ')
    if not _isdigits_util(number):
        raise InvalidFormat("ОГРН/ОГРНИП должен содержать только цифры.")
    length = len(number)
    if length == 13:
        if number[0] == '0':
            raise InvalidComponent("Первая цифра ОГРН не может быть 0.")
        calculated_checksum = str(int(number[:-1]) % 11 % 10)
    elif length == 15:
        if number[0] not in '34':
            raise InvalidComponent("Первая цифра ОГРНИП должна быть 3 или 4.")
        calculated_checksum = str(int(number[:-1]) % 13 % 10)
    else:
        raise InvalidLength("ОГРН должен состоять из 13 цифр, ОГРНИП - из 15.")
    actual_checksum = number[-1]
    if actual_checksum != calculated_checksum:
        raise InvalidChecksum("Неверная контрольная сумма ОГРН/ОГРНИП.")
    return number

def is_valid_ogrn_ogrnip(number):
    try:
        validate_ogrn_ogrnip(number)
        return True
    except ValidationError:
        return False


IBAN_COUNTRY_LENGTHS = {
    "AL": 28, "AD": 24, "AT": 20, "AZ": 28, "BH": 22, "BY": 28, "BE": 16, "BA": 20, "BR": 29,
    "BG": 22, "CR": 22, "HR": 21, "CY": 28, "CZ": 24, "DK": 18, "DO": 28, "EE": 20, "FO": 18,
    "FI": 18, "FR": 27, "GE": 22, "DE": 22, "GI": 23, "GR": 27, "GL": 18, "GT": 28, "HU": 28,
    "IS": 26, "IE": 22, "IL": 23, "IT": 27, "JO": 30, "KZ": 20, "XK": 20, "KW": 30, "LV": 21,
    "LB": 28, "LI": 21, "LT": 20, "LU": 20, "MK": 19, "MT": 31, "MR": 27, "MU": 30, "MD": 24,
    "MC": 27, "ME": 22, "NL": 18, "NO": 15, "PK": 24, "PS": 29, "PL": 28, "PT": 25, "QA": 29,
    "RO": 24, "LC": 32, "SM": 27, "ST": 25, "SA": 24, "RS": 22, "SC": 31, "SK": 24, "SI": 19,
    "ES": 24, "SE": 24, "CH": 21, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "AE": 23, "GB": 22,
    "VA": 22, "VG": 24, "RU": 33
}

ISO_COUNTRY_CODES = frozenset({
    "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ",
    "BA", "BB", "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS",
    "BT", "BV", "BW", "BY", "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN",
    "CO", "CR", "CU", "CV", "CW", "CX", "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE",
    "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK", "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF",
    "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS", "GT", "GU", "GW", "GY", "HK", "HM",
    "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR", "IS", "IT", "JE", "JM",
    "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA", "LB", "LC",
    "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
    "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA",
    "NC", "NE", "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG",
    "PH", "PK", "PL", "PM", "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW",
    "SA", "SB", "SC", "SD", "SE", "SG", "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS",
    "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF", "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO",
    "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY", "UZ", "VA", "VC", "VE", "VG", "VI",
    "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW"
})


def validate_iban(number: str) -> str:
    """Проверяет IBAN по стандарту ISO 13616 и формуле ISO 7064 MOD 97-10."""
    import re
    cleaned = re.sub(r"[\s-]+", "", str(number)).upper()
    if not re.fullmatch(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$", cleaned):
        raise InvalidFormat("IBAN должен состоять из 2 букв страны, 2 цифр и BBAN.")
    country = cleaned[:2]
    if country not in ISO_COUNTRY_CODES:
        raise InvalidComponent(f"Неизвестный код страны IBAN: {country}")
    expected_len = IBAN_COUNTRY_LENGTHS.get(country)
    if expected_len and len(cleaned) != expected_len:
        raise InvalidLength(f"Для страны {country} длина IBAN должна составлять {expected_len}, получено {len(cleaned)}.")
    # Перенос первых 4 символов в конец: BBAN + Country + CheckDigits
    rearranged = cleaned[4:] + cleaned[:4]
    # Замена букв A-Z на цифры (A=10, B=11, ..., Z=35)
    digits_str = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    if int(digits_str) % 97 != 1:
        raise InvalidChecksum("Неверная контрольная сумма IBAN (MOD 97).")
    return cleaned


def is_valid_iban(number: str) -> bool:
    """Возвращает True, если IBAN валиден по стандарту ISO 13616."""
    try:
        validate_iban(number)
        return True
    except (ValidationError, AssertionError, ValueError):
        return False


def validate_swift_bic(code: str) -> str:
    """Проверяет банковский код SWIFT/BIC по стандарту ISO 9362."""
    import re
    cleaned = re.sub(r"[\s-]+", "", str(code)).upper()
    if not re.fullmatch(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$", cleaned):
        raise InvalidFormat("SWIFT/BIC должен содержать 8 или 11 буквенно-цифровых символов.")
    country = cleaned[4:6]
    if country not in ISO_COUNTRY_CODES:
        raise InvalidComponent(f"Некорректный код страны в SWIFT/BIC: {country}")
    return cleaned


def is_valid_swift_bic(code: str) -> bool:
    """Возвращает True, если код SWIFT/BIC валиден по стандарту ISO 9362."""
    try:
        validate_swift_bic(code)
        return True
    except (ValidationError, AssertionError):
        return False


_STREET_INDICATOR_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:ул\.|улиц[а-яё]*|"
    r"пр-кт\w*|пр-т\w*|проспект\w*|"
    r"пер\.|переул\w*|"
    r"б-р\w*|бульвар\w*|"
    r"ш\.|шоссе\b|"
    r"(?!(?:г\.|город\s+)?Набережные\s+Челн)набережн(?:ая|ой|ую|ое|ом|ые|ых|ым)?\b|наб\.|"
    r"пр-д\b|проезд\w*|"
    r"туп\.|тупик\w*|"
    r"алле[яеи]\b|"
    r"тракт\w*|"
    r"пл\.|площад\w*|"
    r"линия\b|лин\.|"
    r"взвоз\b|спуск\b|"
    r"street\b|st\.|"
    r"avenue\b|ave\.|"
    r"boulevard\b|blvd\.|"
    r"road\b|rd\.|"
    r"drive\b|dr\.|"
    r"lane\b|ln\.|"
    r"way\b|highway\b|hwy\.)"
    r")"
)

_DISTRICT_INDICATOR_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:р-н\b|район\w*|"
    r"мкр\b|мкр-н\b|микрорайон\w*|"
    r"кв-л\b|квартал\w*|"
    r"пос\.|посел[ое]к\w*|"
    r"пгт\b|п\.г\.т\.|"
    r"дер\.|деревн\w*|"
    r"село\b|с\.\s+[А-ЯЁ]|"
    r"станиц\w*|ст-ца\b|"
    r"аул\w*|"
    r"хутор\w*|хут\.|"
    r"поселени\w*|пос-е\b|"
    r"муниципальн\w*\s+округ\w*|"
    r"муниципалитет\w*|"
    r"вн\.тер\.г\.|"
    r"district\b)"
    r")"
)

_HOUSE_INDICATOR_PATTERN = re.compile(
    r"(?i)(?:"
    r"\b(?:дом|д\.|владение|вл\.|строение|стр\.|корпус|корп\.|к\.|"
    r"квартира|кв\.|офис|оф\.?|помещение|помещ\.|пом\.|"
    r"комната|комн\.|ком\.|кабинет|каб\.|этаж|эт\.|э\.|"
    r"участок|уч\.|бокс|павильон|пав\.|подъезд|под\.|"
    r"house\b|bldg\b|building\b|apt\b|apartment\b|suite\b|ste\b|room\b|unit\b|fl\.|floor\b)\s*[:№#\-–—]?\s*\d+"
    r"|"
    r"\b(?:пом[а-яё]*|помещ[а-яё]*|кв[а-яё]*|ком[а-яё]*|оф[а-яё]*)\.?\s*[/\\|.,]+\s*(?:оф[а-яё]*|ком[а-яё]*|пом[а-яё]*)\.?\s*[:№#\-–—]?\s*\d+"
    r"|"
    r"\b\d+[A-Za-zА-Яа-яЁё]?(?:/\d+)?(?=\s*(?:лит\.|литера\b|[A-Za-zА-Яа-яЁё]\b|кв\.|оф\.?|пом\.?|стр\.?|корп\.?))"
    r"|"
    r"(?:ул\.|улица|пр-кт|пр-т|проспект|пер\.|переулок|б-р|бульвар|ш\.|шоссе|наб\.|набережная|проезд|street|st\.|road|rd\.|avenue|ave\.)"
    r"[^,\n;]{1,60}?[,\s]+(?:\bд\.\s*)?(?P<num>\d{1,4}[A-Za-zА-Яа-яЁё]?(?:/\d+)?)\b"
    r")"
)


def is_sufficient_address(text: str) -> bool:
    """Определяет, является ли текст достаточным адресом для обезличивания ([Адрес]).

    Неполные географические обозначения (страна, город, атолл, остров, регион без улицы/дома,
    например: 'Мальдивы, Адду Атолл', 'г. Москва', 'Россия, Москва') адресом НЕ являются.

    Адресом признается текст, в котором указана:
    1) Улица (ул., проспект, переулок, бульвар, шоссе, проезд, street, avenue...)
       желательно с номером дома/квартиры/офиса или с районом; ИЛИ
    2) Район / микрорайон / поселение (р-н, микрорайон, поселок, деревня, село, квартал...)
       дополнительно с номером дома/строения/участка или улицей.
    """
    if not text:
        return False
    clean = text.strip()
    if len(clean) < 4:
        return False

    has_street = bool(_STREET_INDICATOR_PATTERN.search(clean))
    has_district = bool(_DISTRICT_INDICATOR_PATTERN.search(clean))
    has_house = bool(_HOUSE_INDICATOR_PATTERN.search(clean))

    # 1. Если указана улица
    if has_street:
        # Улица + номер дома/строения/квартиры -> полноценный адрес
        if has_house:
            return True
        # Улица + район допом -> полноценный адрес
        if has_district:
            return True
        # Улица + числовой номер
        if re.search(r"\b\d{1,4}[A-Za-zА-Яа-яЁё]?(?:/\d+)?\b", clean):
            return True
        # Улица с конкретным наименованием с заглавной буквы (например, 'ул. Тверская', 'Невский проспект')
        if re.search(r"(?:ул\.|улиц\w*|проспект\w*|пр-кт\w*|бульвар\w*|б-р\w*|пер\.|переул\w*|шоссе\b|street\b|avenue\b|road\b)\s+[А-ЯЁA-Z]", clean):
            return True

    # 2. Если указан район / микрорайон / поселок / село / квартал допом
    if has_district:
        # С явным номером дома / строения / участка
        if has_house:
            return True
        # Либо с числовым номером (например: 'пос. Барвиха 5', 'с. Усово 12')
        if re.search(r"\b\d{1,4}[A-Za-zА-Яа-яЁё]?(?:/\d+)?\b", clean):
            return True

    return False

