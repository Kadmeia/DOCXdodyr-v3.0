# -*- coding: utf-8 -*-
import hashlib
import json

from corpus_annotation import (
    SCHEMA_VERSION,
    build_candidate_record,
    mask_text,
    sha256_text,
    validate_annotation_record,
    extract_xml_plain_text,
)
from legal_pullenti import EntitySpan


def test_candidate_record_has_exact_spans_and_hash_only_surface_by_default():
    text = "Иванов Иван подписал договор."
    start = text.index("Иванов Иван")
    record = build_candidate_record(
        text,
        {"path": "one.txt", "kind": "extracted_text", "encoding": "utf-8", "size_bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()},
        spans=[EntitySpan("Иванов Иван", start, start + len("Иванов Иван"), "PER")],
    )
    annotation = record["annotations"][0]
    assert record["schema_version"] == SCHEMA_VERSION
    assert annotation["start"] == start
    assert annotation["end"] == start + len("Иванов Иван")
    assert "surface" not in annotation
    assert annotation["surface_sha256"] == sha256_text("Иванов Иван")
    serialized = json.dumps(record, ensure_ascii=False)
    assert "Иванов" not in serialized
    assert "иванов" not in serialized.casefold()
    assert record["safety"]["masking_integrity_ok"] is True


def test_masking_is_positional_and_validator_catches_bad_coordinate():
    text = "ООО Ромашка, ИНН 7707083893"
    annotations = [{"start": 0, "end": 11, "label": "ORG"}]
    assert mask_text(text, annotations) == "[ORG], ИНН 7707083893"
    record = {
        "schema_version": SCHEMA_VERSION,
        "document_id": "doc_x",
        "text_length": len(text),
        "text_sha256": sha256_text(text),
        "annotations": [{
            "id": "doc_x:a0001", "start": 1, "end": 11, "label": "ORG",
            "detectors": ["pullenti"], "status": "candidate",
            "surface_sha256": sha256_text(text[1:11]), "surface_length": 10,
            "grammar_profile": {},
        }],
    }
    result = validate_annotation_record(record, source_text=text, masked_text="bad", check_leaks=True)
    assert result["ok"] is False
    assert any("masked_text" in error for error in result["errors"])


def test_validator_rejects_overlap_and_unknown_label():
    text = "Иванов"
    record = {
        "schema_version": SCHEMA_VERSION,
        "document_id": "doc_x",
        "text_length": len(text),
        "text_sha256": sha256_text(text),
        "annotations": [
            {"id": "a1", "start": 0, "end": 4, "label": "PER", "detectors": ["pullenti"], "status": "candidate", "surface_sha256": sha256_text("Иван"), "surface_length": 4, "grammar_profile": {}},
            {"id": "a2", "start": 3, "end": 6, "label": "SECRET", "detectors": ["pullenti"], "status": "candidate", "surface_sha256": sha256_text("нов"), "surface_length": 3, "grammar_profile": {}},
        ],
    }
    result = validate_annotation_record(record)
    assert result["ok"] is False
    assert any("overlapping" in error for error in result["errors"])
    assert any("unsupported annotation label" in error for error in result["errors"])


def test_xml_plain_extractor_keeps_title_punctuation_and_offsets():
    xml = """<doc><head><title>Решение N 1</title></head><body><p><s><w>Иванов</w><w>Иван</w><w>Иванович</w><w>,</w><w>ООО</w><w>«Ромашка»</w><w>.</w></s></p></body></doc>"""
    text = extract_xml_plain_text(xml)
    assert text == "Решение N 1\nИванов Иван Иванович, ООО «Ромашка»."
    assert text[text.index("ООО"):text.index("ООО") + 13] == "ООО «Ромашка»"
