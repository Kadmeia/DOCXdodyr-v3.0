# -*- coding: utf-8 -*-
import json

from corpus_coverage import (
    DEFAULT_HARD_CASES_PER_TYPE,
    DEFAULT_SAMPLES_PER_TYPE,
    OFFICIAL_SOURCE_CATALOG,
    TYPE_SPECS,
    build_coverage_report,
    iter_negative_records,
    iter_positive_records,
    write_coverage,
)


def test_every_supported_type_has_200_unique_synthetic_positives():
    rows = list(iter_positive_records())
    by_label = {}
    for row in rows:
        by_label.setdefault(row["label"], []).append(row)
        assert row["synthetic"] is True
        assert row["start"] < row["end"]
        assert row["text"][row["start"]:row["end"]] == row["surface"]
        assert row["expected_relation"] == "cue_precedes_value"
    assert set(by_label) == {spec.label for spec in TYPE_SPECS}
    assert all(len(items) == DEFAULT_SAMPLES_PER_TYPE for items in by_label.values())
    assert all(len({item["surface"] for item in items}) == DEFAULT_SAMPLES_PER_TYPE for items in by_label.values())


def test_split_templates_are_disjoint_and_tables_are_covered():
    rows = list(iter_positive_records())
    templates = {}
    layouts = {}
    for row in rows:
        templates.setdefault(row["split"], set()).add(row["template_id"])
        layouts.setdefault(row["label"], set()).add(row["layout"])
    assert not (templates["train"] & templates["dev"])
    assert not (templates["train"] & templates["test"])
    assert not (templates["dev"] & templates["test"])
    assert all({"prose", "field", "table"}.issubset(value) for value in layouts.values())


def test_hard_negatives_keep_context_but_are_marked_negative():
    rows = list(iter_negative_records())
    assert len(rows) == len(TYPE_SPECS) * DEFAULT_HARD_CASES_PER_TYPE
    assert all(row["negative"] is True for row in rows)
    assert all(row["expected_relation"] == "hard_negative_invalid_or_missing_value" for row in rows)
    assert all(row["synthetic"] is True for row in rows)


def test_report_proves_minimum_and_no_template_leakage():
    report = build_coverage_report(list(iter_positive_records()) + list(iter_negative_records()))
    assert report["types"] == len(TYPE_SPECS)
    assert report["minimum_positive_per_type"] >= DEFAULT_SAMPLES_PER_TYPE
    assert report["template_split_leak_free"] is True
    assert report["template_split_leaks"] == []


def test_write_coverage_creates_reproducible_split_files(tmp_path):
    report = write_coverage(tmp_path, hard_cases_per_type=4)
    assert report["minimum_positive_per_type"] == DEFAULT_SAMPLES_PER_TYPE
    for filename in ("manifest.json", "source_manifest.json", "coverage_report.json", "synthetic.jsonl", "train.jsonl", "dev.jsonl", "test.jsonl"):
        assert (tmp_path / filename).exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["positive_per_type"] == DEFAULT_SAMPLES_PER_TYPE
    source_manifest = json.loads((tmp_path / "source_manifest.json").read_text(encoding="utf-8"))
    assert {source["host"] for source in source_manifest["sources"]} == {str(source["host"]) for source in OFFICIAL_SOURCE_CATALOG}


def test_every_legal_cartridge_positive_is_recognized_at_its_exact_surface():
    from pullenti_legal.analyzer import _CANDIDATES, iter_candidates

    legal_labels = {item[0] for item in _CANDIDATES}
    counts = {label: 0 for label in legal_labels}
    for row in iter_positive_records():
        if row["label"] not in legal_labels:
            continue
        matches = [item for item in iter_candidates(row["text"]) if item[0] == row["label"]]
        assert matches, (row["label"], row["text"])
        assert any(row["text"][start:end] == row["surface"] for _kind, _value, start, end in matches)
        counts[row["label"]] += 1
    assert counts and min(counts.values()) >= DEFAULT_SAMPLES_PER_TYPE
