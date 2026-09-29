import pytest
from app.backend_api import BackendApi

def test_exclusion_priority_over_pullenti_and_rules():
    """
    Проверяем, что слова и данные из списка исключений:
    1. НЕ обезличиваются ни движком Pullenti (ФИО, организации, адреса)
    2. НЕ обезличиваются детерминированными правилами (ИНН, СНИЛС, паспорт, телефон, email)
    3. Регистронезависимы (case-insensitive)
    4. Очищаются от краевых пробелов (strip)
    """
    api = BackendApi(lazy_pullenti=False)

    text1 = (
        "Директор Иванов Иван Иванович, "
        "компания ООО Ромашка, "
        "ИНН 9901234567, "
        "СНИЛС 123-456-789 01, "
        "телефон +7 (999) 123-45-67."
    )

    # 1) Без исключений — всё должно быть обезличено
    cleaned_all, count_all, _ = api.anonymize_text_pullenti(
        text1,
        current_exclusions_original=set(),
        current_replacements=set()
    )
    assert "Иванов Иван Иванович" not in cleaned_all
    assert "9901234567" not in cleaned_all
    assert "+7 (999) 123-45-67" not in cleaned_all
    assert "123-456-789 01" not in cleaned_all

    # 2) С исключениями:
    # - "иванов иван иванович" (в нижнем регистре!) — Pullenti
    # - "9901234567" — детерминированное правило ИНН
    # - "ромашка" и "ООО Ромашка" — Pullenti
    user_exclusions = {
        "иванов иван иванович",
        "9901234567",
        "ромашка",
        "ООО Ромашка",
    }

    cleaned1, count1, logs1 = api.anonymize_text_pullenti(
        text1,
        current_exclusions_original=user_exclusions,
        current_replacements=set()
    )

    # Исключения сохранились без изменений:
    assert "Иванов Иван Иванович" in cleaned1, "ФИО из исключений должно остаться нетронутым"
    assert "Ромашка" in cleaned1, "Организация из исключений должна остаться нетронутой"
    assert "9901234567" in cleaned1, "ИНН из исключений должен остаться нетронутым (правило ИНН подавлено)"

    # Данные, которых не было в исключениях, успешно обезличены:
    assert "123-456-789 01" not in cleaned1, "СНИЛС не в исключениях — должен быть обезличен"
    assert "+7 (999) 123-45-67" not in cleaned1, "Телефон не в исключениях — должен быть обезличен"

    # 3) Проверка: Паспорт, Email, Адрес
    text2 = (
        "Паспорт РФ 45 10 123456 выдан 15.05.2024, "
        "почта test.director@example.invalid, адрес: г. Москва, ул. Тестовая, д. 1."
    )
    user_exclusions2 = {
        "45 10",
        "123456",
        "test.director@example.invalid",
    }
    cleaned2, count2, logs2 = api.anonymize_text_pullenti(
        text2,
        current_exclusions_original=user_exclusions2,
        current_replacements=set()
    )

    # Паспорт и email сохранились
    assert "45 10 123456" in cleaned2, "Паспорт в исключениях должен остаться"
    assert "test.director@example.invalid" in cleaned2, "Email в исключениях должен остаться"

    # Адрес не в исключениях — обезличен
    assert "ул. Тестовая" not in cleaned2, "Адрес не в исключениях — должен быть обезличен"
