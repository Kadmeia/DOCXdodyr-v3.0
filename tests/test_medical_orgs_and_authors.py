import pytest
from backend_api import BackendApi
from legal_pullenti import _is_initial_fio_candidate


def test_is_initial_fio_candidate_medic_and_surnames():
    # Surnames matching animate noun or specific endings
    assert _is_initial_fio_candidate("Медик В.А.") is True
    assert _is_initial_fio_candidate("В.А. Медик") is True
    assert _is_initial_fio_candidate("Коваль А.Б.") is True
    assert _is_initial_fio_candidate("Черных В.И.") is True
    assert _is_initial_fio_candidate("Иванов И.И.") is True
    assert _is_initial_fio_candidate("Шевченко Т.Г.") is True

    # Geographic contexts must NOT be treated as persons
    assert _is_initial_fio_candidate("Москвы А.Б.") is False
    assert _is_initial_fio_candidate("России В.Г.") is False


def test_bibliographic_reference_anonymizes_author():
    api = BackendApi()
    api.init_pullenti()

    text = "6.\tМедик В.А. Общественное здоровье и здравоохранение Москва: ГЭОТАР –Медиа, 2022"
    result, count, _ = api.anonymize_text_pullenti(text)

    assert count >= 1
    assert "Медик В.А." not in result
    assert "[ФИО]" in result or "[ФИО_1]" in result


def test_healthcare_org_preserves_kgbuz_masks_quoted_title():
    api = BackendApi()
    api.init_pullenti()

    # 1. Simple replacement
    text = "КГБУЗ «КМРД № 4»"
    result, count, _ = api.anonymize_text_pullenti(text)
    assert count == 1
    assert result == "КГБУЗ «[Наименование]»"

    # 2. In sentence context
    text_sentence = "Врач направлен в КГБУЗ «КМРД № 4» для прохождения практики."
    result_sent, count_sent, _ = api.anonymize_text_pullenti(text_sentence)
    assert count_sent >= 1
    assert "КГБУЗ «[Наименование]»" in result_sent

    # 3. With batch mapping
    mapping = {}
    seen = {}
    result_map, _, _ = api.anonymize_text_pullenti(text, mapping_dict=mapping, entity_seen=seen)
    assert result_map == "КГБУЗ «[Наименование_1]»"
    assert "[Наименование_1]" in mapping
    assert mapping["[Наименование_1]"]["original"] == "КМРД № 4"
