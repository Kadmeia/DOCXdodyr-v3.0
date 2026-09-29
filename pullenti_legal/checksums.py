# -*- coding: utf-8 -*-
"""Checksum validation kept inside the Pullenti legal cartridge."""


def valid_inn(value: str) -> bool:
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) not in (10, 12):
        return False
    value = digits
    if len(value) == 10:
        weights = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        return sum(w * int(n) for w, n in zip(weights, value)) % 11 % 10 == int(value[-1])
    first = sum(
        w * int(n) for w, n in zip((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), value[:10])
    ) % 11 % 10
    second = sum(
        w * int(n)
        for w, n in zip((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8), value[:10] + str(first))
    ) % 11 % 10
    return value[-2:] == f"{first}{second}"


def valid_ogrn(value: str) -> bool:
    digits = "".join(character for character in str(value) if character.isdigit())
    if not digits:
        return False
    value = digits
    if len(value) == 13:
        return value[0] != "0" and int(value[:-1]) % 11 % 10 == int(value[-1])
    if len(value) == 15:
        return value[0] in "34" and int(value[:-1]) % 13 % 10 == int(value[-1])
    return False


def valid_snils(value: str) -> bool:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != 11:
        return False
    total = sum(int(digit) * weight for digit, weight in zip(digits[:9], range(9, 0, -1)))
    if total < 100:
        check = total
    elif total in (100, 101):
        check = 0
    else:
        check = total % 101
        if check == 100:
            check = 0
    return check == int(digits[9:])


def valid_luhn(value: str) -> bool:
    """Validate a decimal identifier using the ISO/IEC 7812 Luhn check."""

    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 12 or len(digits) > 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        number = int(digit)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def valid_iban(value: str) -> bool:
    """Validate an International Bank Account Number using ISO 7064 MOD 97-10."""
    import re
    cleaned = re.sub(r"[\s-]+", "", str(value)).upper()
    if not re.fullmatch(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$", cleaned):
        return False
    rearranged = cleaned[4:] + cleaned[:4]
    digits_str = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    try:
        return int(digits_str) % 97 == 1
    except (ValueError, TypeError):
        return False


def valid_swift_bic(value: str) -> bool:
    """Validate a SWIFT/BIC code using ISO 9362 format."""
    import re
    cleaned = re.sub(r"[\s-]+", "", str(value)).upper()
    return bool(re.fullmatch(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$", cleaned))

