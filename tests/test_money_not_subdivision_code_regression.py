import pytest
from app.backend_api import BackendApi

def test_monetary_amounts_not_masked_as_passport_division_code():
    """
    Регрессионный тест: суммы, стоимости и числа с разделителями тысяч/копейками
    (например, 1 203 092,18 руб., 750 000 руб., 476 064,40 руб., 100 000 руб.)
    НЕ должны определяться как PASSPORT_DIVISION_CODE (КодПодразделения).
    """
    api = BackendApi(lazy_pullenti=False)

    text = (
        "Стоимость выполнения работ по Спецификации № 5 составляет 1 203 092,18 руб. "
        "Расчет остатка задолженности: 1 203 092,18 руб. – 727 027,78 руб. = 476 064,40 руб. "
        "В настоящем заявлении Взыскатель просит взыскать часть указанного остатка в размере 476 064,20 руб. "
        "Заявленная сумма 476 064,20 руб. не превышает предел 750 000 руб. "
        "Расчет: 10 000 руб. + 5% * (476 064,20 руб. - 100 000 руб.) = 14 401,61 руб. "
        "Основной долг в размере 476 064 (четыреста семьдесят шесть тысяч шестьдесят четыре) рубля 20 копеек."
    )

    cleaned, count, logs = api.anonymize_text_pullenti(
        text, current_exclusions_original=set(), current_replacements=set()
    )

    assert "КодПодразделения" not in cleaned, f"Обнаружена ложная замена на КодПодразделения: {cleaned}"
    assert "PASSPORT_DIVISION" not in str(logs)
    assert "1 203 092,18" in cleaned
    assert "727 027,78" in cleaned
    assert "476 064,40" in cleaned
    assert "750 000" in cleaned
    assert "100 000" in cleaned


def test_real_passport_division_code_is_masked():
    """Настоящий код подразделения в паспортном контексте должен маскироваться."""
    api = BackendApi(lazy_pullenti=False)

    text = "Паспорт выдан отделом УФМС России, код подразделения 770-001."
    cleaned, count, logs = api.anonymize_text_pullenti(
        text, current_exclusions_original=set(), current_replacements=set()
    )

    assert "770-001" not in cleaned
    assert "КодПодразделения" in cleaned or "[КодПодразделения" in cleaned
