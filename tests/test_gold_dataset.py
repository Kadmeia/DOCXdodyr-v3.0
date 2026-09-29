from __future__ import annotations

import hashlib
import json
from pathlib import Path

from corpus_coverage import TYPE_SPECS
from gold_dataset import (
    GOLD_SCHEMA_VERSION,
    annotate_queue,
    add_manual_task,
    build_manifest,
    coverage_report,
    import_candidates,
    prepare_review_queue,
    deficit_report,
    metrics,
    validate_dataset,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(document_id: str = "doc_1", label: str = "PER"):
    return {
        "schema_version": "legalpullenti-span-1.0",
        "document_id": document_id,
        "source": {"path": "PII_NAME.txt", "sha256": _digest(document_id + " source"), "kind": "extracted_text"},
        "text_length": 40,
        "text_sha256": _digest(document_id + " text"),
        "annotations": [{
            "id": "candidate-1", "start": 0, "end": 6,
            "label": label, "detectors": ["pullenti"],
            "surface": "Иванов", "surface_sha256": _digest("Иванов"), "surface_length": 6,
            "status": "candidate", "grammar_profile": {"head_word": "Иванов"},
        }],
    }


def _gold(label: str, document_id: str, layout: str = "text", start: int = 0, end: int = 4):
    return {
        "schema_version": GOLD_SCHEMA_VERSION,
        "document_id": document_id,
        "source": {
            "source_sha256": _digest(document_id + " source"),
            "text_sha256": _digest(document_id + " text"),
            "provenance": {"real": True, "license": "test", "license_verified": True, "surface_policy": "hash_only"},
        },
        "span": {"start": start, "end": end, "label": label, "layout": layout, "surface_sha256": _digest("hidden"), "surface_length": end - start},
        "status": "accepted",
        "annotator": {"id": "reviewer-1", "status": "confirmed", "reviewed_at": "2026-01-01T00:00:00Z"},
    }


def test_import_is_pending_and_never_copies_pii(tmp_path: Path):
    source = tmp_path / "candidates.jsonl"
    source.write_text(json.dumps(_candidate(), ensure_ascii=False) + "\n", encoding="utf-8")
    queue = tmp_path / "queue.jsonl"
    manifest = tmp_path / "manifest.json"
    result = import_candidates(source, queue, manifest, license_name="public_pending_review")
    assert result["queue"]["candidate_annotations"] == 1
    raw = queue.read_text(encoding="utf-8")
    assert "Иванов" not in raw
    assert "PII_NAME" not in raw
    rows = [json.loads(raw)]
    assert rows[0]["status"] == "pending"
    assert rows[0]["candidate_origin"]["machine_generated"] is True
    task = annotate_queue(queue, rows[0]["task_id"], "accepted", "human-1", layout="ocr")
    assert task["gold_eligible"] is True
    assert validate_dataset(queue)["ok"] is True
    assert coverage_report([task], minimum=1)["labels"]["PER"]["complete"] is True


def test_coverage_excludes_pending_and_synthetic_and_reports_shortfall():
    pending = _gold("PER", "doc-pending")
    pending["status"] = "pending"
    synthetic = _gold("ORG", "doc-synthetic")
    synthetic["synthetic"] = True
    unconfirmed = _gold("EMAIL", "doc-unconfirmed")
    unconfirmed["annotator"]["status"] = "reviewed"
    report = coverage_report([pending, synthetic, unconfirmed], minimum=1)
    assert report["entities"] == len(TYPE_SPECS)
    assert report["real_confirmed_total"] == 0
    assert report["all_entities_complete"] is False
    assert "Insufficient" in report["warning"]


def test_metrics_are_separated_by_entity_and_layout_and_emit_confusion():
    gold = [_gold("PER", "doc-1", "text", 0, 4), _gold("ORG", "doc-2", "table", 2, 7)]
    predictions = [
        {"document_id": "doc-1", "span": {"start": 0, "end": 4, "end": 4, "label": "ORG", "layout": "text"}},
        {"document_id": "doc-2", "span": {"start": 2, "end": 7, "end": 7, "label": "ORG", "layout": "table"}},
    ]
    report = metrics(gold, predictions)
    assert report["gold_real_confirmed"] == 2
    assert report["micro"]["tp"] == 1
    assert report["by_layout"]["table"]["recall"] == 1.0
    assert {item["gold"] for item in report["errors"]["confusion"]} == {"PER"}
    assert report["by_entity"]["PER"]["fn"] == 1


def test_manual_add_task_supports_recall_and_prediction_candidates_are_flattened(tmp_path: Path):
    queue = tmp_path / "queue.jsonl"
    task = add_manual_task(queue, "doc-manual", _digest("source"), _digest("text"), 3, 8, "ORG", "table")
    assert task["candidate_origin"]["machine_generated"] is False
    assert validate_dataset(queue)["ok"] is True
    accepted = annotate_queue(queue, task["task_id"], "accepted", "reviewer")
    prediction_candidate = {
        "document_id": "doc-manual",
        "annotations": [{"start": 3, "end": 8, "label": "ORG", "layout": "table"}],
    }
    report = metrics([accepted], [prediction_candidate])
    assert report["micro"]["tp"] == 1
    assert "surface" not in json.dumps(report, ensure_ascii=False)


def test_manifest_hash_splits_are_disjoint_and_versioned():
    rows = [_gold("PER", f"doc-{index}") for index in range(30)]
    manifest = build_manifest(rows)
    assert manifest["schema_version"] == "legalpullenti-gold-manifest-1.0"
    split_sets = [set(manifest["splits"][name]) for name in ("train", "dev", "test")]
    assert not (split_sets[0] & split_sets[1] or split_sets[0] & split_sets[2] or split_sets[1] & split_sets[2])
    assert sum(len(item) for item in split_sets) == 30
    assert manifest["synthetic_excluded_from_gold"] is True


def test_luna_pre_review_is_separate_from_human_gold(tmp_path: Path):
    source = tmp_path / "candidates.jsonl"
    rows = [_candidate(f"doc_{index:08x}", "PER") for index in range(3)]
    rows.append(_candidate("doc_missing_type", "ORG"))
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    imported = tmp_path / "imported.jsonl"
    import_candidates(source, imported)
    curated = tmp_path / "luna.jsonl"
    result = prepare_review_queue(imported, curated, per_label=2)
    curated_rows = [json.loads(line) for line in curated.read_text(encoding="utf-8").splitlines()]
    assert result["queue"]["selected_annotations"] == 3
    assert {row["status"] for row in curated_rows} == {"model_reviewed"}
    assert all("human_confirmed" not in row for row in curated_rows)
    assert all(row["model_review"]["provider"] == "luna" for row in curated_rows)
    assert validate_dataset(curated)["ok"] is True
    assert deficit_report([json.loads(line) for line in imported.read_text(encoding="utf-8").splitlines()], minimum=2)["labels_with_200_candidates"] == 1
