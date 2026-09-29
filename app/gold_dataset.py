# -*- coding: utf-8 -*-
"""Безопасный контур ручной gold-разметки и воспроизводимых метрик.

Кандидаты детектора никогда не считаются gold автоматически.  Команда
``import-candidates`` переносит только координаты, хеши и provenance в
очередь ручной проверки; подтверждённой разметкой запись становится только
после ``annotate --decision accepted`` с идентификатором аннотатора.

Файл намеренно hash-only: поверхности, исходный текст и абсолютные пути в него
не попадают. Это позволяет хранить очередь/отчёты рядом с кодом, не публикуя
PII из корпуса.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from corpus_coverage import TYPE_SPECS


GOLD_SCHEMA_VERSION = "legalpullenti-gold-1.0"
MANIFEST_SCHEMA_VERSION = "legalpullenti-gold-manifest-1.0"
QUEUE_SCHEMA_VERSION = "legalpullenti-gold-queue-1.0"
MIN_CONFIRMED_PER_ENTITY = 200
LAYOUTS = ("text", "table", "ocr")
# ``model_reviewed`` is deliberately not a gold state.  It records a separate
# pre-review pass (Luna/other model) and still requires an explicit human
# decision before it can contribute to coverage or metrics.
STATUSES = ("pending", "model_reviewed", "accepted", "rejected")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_document_id(value: str) -> str:
    """Оставляет машинный id, но хеширует случайно переданный путь/PII."""
    value = str(value or "")
    if re.fullmatch(r"(?:doc|document|file)[_-][a-f0-9]{8,64}", value, flags=re.IGNORECASE):
        return value
    return "docref_" + _sha(value)[:24]


def _jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            yield value


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _source_provenance(record: Mapping[str, Any], license_name: str, source_catalog_id: str) -> Dict[str, Any]:
    source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
    digest = str(source.get("sha256") or record.get("text_sha256") or "")
    if len(digest) != 64:
        # Invalid source hashes remain visible as an import error, rather than
        # being silently replaced with a hash of candidate metadata.
        digest = ""
    return {
        "real": True,
        "source_sha256": digest,
        "source_catalog_id": source_catalog_id,
        "license": license_name,
        "license_verified": False,
        "surface_policy": "hash_only",
    }


def _layout(annotation: Mapping[str, Any]) -> str:
    value = str(annotation.get("layout") or annotation.get("source_layout") or "text").lower()
    if value in {"table", "ocr", "text"}:
        return value
    if value in {"spreadsheet", "cell", "xlsx"}:
        return "table"
    if value in {"scan", "image", "vision"}:
        return "ocr"
    return "text"


def _candidate_id(document_id: str, annotation: Mapping[str, Any]) -> str:
    value = str(annotation.get("id") or "")
    if value:
        return value
    return f"{document_id}:candidate:{annotation.get('start')}:{annotation.get('end')}:{annotation.get('label')}"


def import_candidates(
    candidates_path: Path,
    queue_path: Path,
    manifest_path: Optional[Path] = None,
    license_name: str = "unknown_pending_verification",
    source_catalog_id: str = "local_corpus",
) -> Dict[str, Any]:
    """Импортирует 500/47k candidate records в pending-очередь.

    В выходе нет source.path, text, surface или grammar_profile. Даже если
    вход был создан с ``--include-surface``, эти поля никогда не копируются.
    """

    rows: List[Dict[str, Any]] = []
    documents: Dict[str, Dict[str, Any]] = {}
    skipped = Counter()
    labels = Counter()
    seen_tasks = set()
    candidate_input = Path(candidates_path)
    candidate_files = [candidate_input] if candidate_input.is_file() else sorted(candidate_input.rglob("*.jsonl"))
    if not candidate_files:
        raise FileNotFoundError(f"no candidate JSONL files found: {candidate_input}")
    for candidate_file in candidate_files:
      for record in _jsonl(candidate_file):
        raw_document_id = str(record.get("document_id") or "")
        if not raw_document_id:
            skipped["missing_document_or_source_hash"] += 1
            continue
        document_id = _safe_document_id(raw_document_id)
        source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
        source_hash = str(source.get("sha256") or "")
        text_hash = str(record.get("text_sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", source_hash) or not re.fullmatch(r"[a-f0-9]{64}", text_hash):
            skipped["missing_document_or_source_hash"] += 1
            continue
        provenance = _source_provenance(record, license_name, source_catalog_id)
        documents[document_id] = {
            "document_id": document_id,
            "source_sha256": source_hash,
            "text_sha256": text_hash,
            "provenance": provenance,
        }
        for annotation in record.get("annotations") or []:
            if not isinstance(annotation, Mapping):
                skipped["invalid_annotation"] += 1
                continue
            label = str(annotation.get("label") or "")
            try:
                start, end = int(annotation["start"]), int(annotation["end"])
            except (KeyError, TypeError, ValueError):
                skipped["invalid_coordinates"] += 1
                continue
            if label not in {spec.label for spec in TYPE_SPECS} or not (0 <= start < end):
                skipped["unsupported_label_or_coordinates"] += 1
                continue
            row = {
                "schema_version": QUEUE_SCHEMA_VERSION,
                "task_id": _sha(f"{document_id}|{start}|{end}|{label}")[:24],
                "document_id": document_id,
                "source": {
                    "source_sha256": source_hash,
                    "text_sha256": text_hash,
                    "provenance": provenance,
                },
                "span": {
                    "start": start,
                    "end": end,
                    "label": label,
                    "layout": _layout(annotation),
                    "surface_sha256": str(annotation.get("surface_sha256") or ""),
                    "surface_length": int(annotation.get("surface_length") or (end - start)),
                },
                "candidate_origin": {
                    "candidate_id": _sha(_candidate_id(document_id, annotation))[:24],
                    "detectors": sorted(str(x) for x in (annotation.get("detectors") or [])),
                    "machine_generated": True,
                },
                "status": "pending",
                "annotator": None,
                "created_at": _now(),
            }
            if row["task_id"] in seen_tasks:
                skipped["duplicate_task"] += 1
                continue
            seen_tasks.add(row["task_id"])
            rows.append(row)
            labels[label] += 1
    # Deterministic order is important for resumable annotation queues.
    rows.sort(key=lambda row: (row["document_id"], row["span"]["start"], row["span"]["end"], row["span"]["label"]))
    _write_jsonl(queue_path, rows)
    manifest = build_manifest(rows, documents=documents, source_kind="candidate_import")
    manifest["queue"] = {
        "candidate_documents": len(documents),
        "candidate_annotations": len(rows),
        "skipped": dict(skipped),
        "labels": dict(sorted(labels.items())),
        "gold_confirmed": 0,
        "warning": "Candidates are pending manual review and are not gold.",
    }
    if manifest_path:
        Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def queue_rows(queue_path: Path, label: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Возвращает неразмеченные задачи для UI/оператора, без поверхности.

    ``model_reviewed`` остаётся в ручной очереди: это лишь машинное
    предварительное мнение, а не подтверждение человека.
    """
    rows = [row for row in _jsonl(Path(queue_path)) if row.get("status") in {"pending", "model_reviewed"}]
    if label:
        rows = [row for row in rows if row.get("span", {}).get("label") == label]
    return rows[: max(0, limit)]


def annotate_queue(queue_path: Path, task_id: str, decision: str, annotator_id: str, layout: Optional[str] = None, note: str = "") -> Dict[str, Any]:
    """Подтверждает/отклоняет одну задачу, сохраняя audit trail."""
    if decision not in {"accepted", "rejected"}:
        raise ValueError("decision must be accepted or rejected")
    if not annotator_id.strip():
        raise ValueError("annotator_id is required")
    rows = list(_jsonl(Path(queue_path)))
    found = None
    for row in rows:
        if row.get("task_id") == task_id:
            if row.get("status") not in {"pending", "model_reviewed"}:
                raise ValueError("task is already reviewed")
            row["status"] = decision
            span = row.setdefault("span", {})
            if layout:
                span["layout"] = _layout({"layout": layout})
            row["annotator"] = {
                "id": annotator_id,
                "status": "confirmed" if decision == "accepted" else "reviewed",
                "reviewed_at": _now(),
                "note_sha256": _sha(note) if note else None,
            }
            # The source is retained only as immutable hashes/provenance.
            row["gold_eligible"] = decision == "accepted" and bool(row.get("source", {}).get("provenance", {}).get("real"))
            found = row
            break
    if found is None:
        raise KeyError(f"unknown task_id: {task_id}")
    _write_jsonl(queue_path, rows)
    return found


def _luna_review(row: Mapping[str, Any], model_name: str = "luna-pre-review-v1") -> Dict[str, Any]:
    """Build a PII-free, deterministic model pre-review record.

    No text or source path is inspected here.  The conservative score reflects
    only candidate provenance (currently Pullenti detector presence), so this
    record must never be interpreted as human confirmation.
    """
    origin = row.get("candidate_origin") if isinstance(row.get("candidate_origin"), Mapping) else {}
    detectors = sorted(str(item) for item in (origin.get("detectors") or []))
    confidence = 0.55 if detectors else 0.35
    return {
        "status": "model_reviewed",
        "model": model_name,
        "provider": "luna",
        "confidence": confidence,
        "reason": "Кандидат получен зарегистрированным детектором; требуется подтверждение человека.",
        "provenance": {
            "kind": "machine_pre_review",
            "detectors": detectors,
            "source_task_id_sha256": _sha(str(row.get("task_id") or "")),
            "surface_policy": "hash_only",
        },
        "reviewed_at": _now(),
    }


def prepare_review_queue(
    source_queue_path: Path,
    output_queue_path: Path,
    manifest_path: Optional[Path] = None,
    deficit_path: Optional[Path] = None,
    per_label: int = MIN_CONFIRMED_PER_ENTITY,
    model_name: str = "luna-pre-review-v1",
) -> Dict[str, Any]:
    """Create a bounded real-candidate review queue.

    At most ``per_label`` candidates are selected for each registered entity.
    The selected rows receive only ``model_reviewed`` and a hash-only Luna
    record; ``human_confirmed`` is intentionally never written.  Labels with
    no real candidates are represented in the deficit report and are not
    filled with synthetic examples.
    """
    if per_label < 1:
        raise ValueError("per_label must be positive")
    source_rows = list(_jsonl(Path(source_queue_path)))
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        span = row.get("span") if isinstance(row.get("span"), Mapping) else {}
        label = str(span.get("label") or "")
        provenance = row.get("source", {}).get("provenance", {}) if isinstance(row.get("source"), Mapping) else {}
        if label in {spec.label for spec in TYPE_SPECS} and provenance.get("real") is True and row.get("synthetic") is not True:
            groups[label].append(row)
    selected: List[Dict[str, Any]] = []
    selected_counts: Counter = Counter()
    for label in sorted(groups):
        candidates = sorted(
            groups[label],
            key=lambda item: (
                str(item.get("document_id") or ""),
                int(item.get("span", {}).get("start", -1)),
                int(item.get("span", {}).get("end", -1)),
                str(item.get("task_id") or ""),
            ),
        )[:per_label]
        for original in candidates:
            # Rows came from JSON, but copying the top level prevents this
            # operation from changing the full 47k pending queue in place.
            row = dict(original)
            row["status"] = "model_reviewed"
            row["model_review"] = _luna_review(row, model_name=model_name)
            # Do not add or infer a human annotation at this stage.
            row.pop("human_confirmed", None)
            selected.append(row)
            selected_counts[label] += 1
    selected.sort(key=lambda item: (str(item.get("document_id") or ""), int(item.get("span", {}).get("start", -1)), str(item.get("task_id") or "")))
    _write_jsonl(Path(output_queue_path), selected)
    report = deficit_report(source_rows, minimum=per_label, selected_counts=selected_counts)
    if deficit_path:
        Path(deficit_path).parent.mkdir(parents=True, exist_ok=True)
        Path(deficit_path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = build_manifest(selected, source_kind="luna_model_review_queue")
    manifest["queue"] = {
        "source_queue": "hash_only_local_queue",
        "selected_annotations": len(selected),
        "per_label_limit": per_label,
        "labels": dict(sorted(selected_counts.items())),
        "gold_confirmed": 0,
        "warning": "model_reviewed is not human gold; explicit annotator confirmation is required.",
    }
    if manifest_path:
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"queue": manifest["queue"], "deficit": report}


def deficit_report(
    rows: Iterable[Mapping[str, Any]],
    minimum: int = MIN_CONFIRMED_PER_ENTITY,
    selected_counts: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    """Report real availability and source gaps without exposing PII."""
    if minimum < 1:
        raise ValueError("minimum must be positive")
    by_label: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        span = row.get("span") if isinstance(row.get("span"), Mapping) else {}
        label = str(span.get("label") or "")
        provenance = row.get("source", {}).get("provenance", {}) if isinstance(row.get("source"), Mapping) else {}
        if label in {spec.label for spec in TYPE_SPECS} and provenance.get("real") is True and row.get("synthetic") is not True:
            by_label[label].append(row)
    entities: Dict[str, Any] = {}
    for spec in TYPE_SPECS:
        values = by_label.get(spec.label, [])
        available = len(values)
        docs = {_safe_document_id(str(row.get("document_id") or "")) for row in values}
        layouts = Counter(_layout(row.get("span", {})) for row in values)
        deficit = max(0, minimum - available)
        entities[spec.label] = {
            "available_real_candidates": available,
            "source_documents": len(docs),
            "layouts": dict(sorted(layouts.items())),
            "selected_for_review": int((selected_counts or {}).get(spec.label, min(available, minimum))),
            "required": minimum,
            "deficit": deficit,
            "complete_available": deficit == 0,
            "source_need": None if deficit == 0 else "Нужны дополнительные лицензированные реальные документы или ручные spans; синтетические примеры не засчитываются.",
        }
    return {
        "schema_version": GOLD_SCHEMA_VERSION,
        "report_kind": "real_candidate_deficit",
        "required_per_entity": minimum,
        "entities": len(TYPE_SPECS),
        "labels_with_any_candidates": sum(bool(by_label.get(spec.label)) for spec in TYPE_SPECS),
        "labels_with_200_candidates": sum(len(by_label.get(spec.label, [])) >= minimum for spec in TYPE_SPECS),
        "total_real_candidates": sum(len(values) for values in by_label.values()),
        "total_source_documents": len({_safe_document_id(str(row.get("document_id") or "")) for values in by_label.values() for row in values}),
        "all_entities_have_required_candidates": all(item["complete_available"] for item in entities.values()),
        "labels": entities,
        "pii_policy": "hash_only_no_surface_no_absolute_path",
        "warning": "Availability is not human gold; all rows remain pending/model_reviewed until manual confirmation.",
    }


def add_manual_task(
    queue_path: Path,
    document_id: str,
    source_sha256: str,
    text_sha256: str,
    start: int,
    end: int,
    label: str,
    layout: str = "text",
    license_name: str = "unknown_pending_verification",
    source_catalog_id: str = "local_corpus",
    surface_sha256: str = "",
) -> Dict[str, Any]:
    """Добавляет вручную найденный span, которого не было среди кандидатов.

    Это необходимо для измерения recall: очередь не ограничивает gold только
    тем, что уже смог найти детектор.
    """
    if label not in {spec.label for spec in TYPE_SPECS}:
        raise ValueError("unsupported entity label")
    if not (re.fullmatch(r"[a-f0-9]{64}", source_sha256) and re.fullmatch(r"[a-f0-9]{64}", text_sha256)):
        raise ValueError("source_sha256 and text_sha256 must be sha256")
    if not (0 <= start < end):
        raise ValueError("invalid span coordinates")
    layout = _layout({"layout": layout})
    task_id = _sha(f"{document_id}|{start}|{end}|{label}|{layout}|manual")[:24]
    row = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "task_id": task_id,
        "document_id": document_id,
        "source": {
            "source_sha256": source_sha256,
            "text_sha256": text_sha256,
            "provenance": {
                "real": True,
                "source_catalog_id": source_catalog_id,
                "license": license_name,
                "license_verified": False,
                "surface_policy": "hash_only",
            },
        },
        "span": {
            "start": start,
            "end": end,
            "label": label,
            "layout": layout,
            "surface_sha256": surface_sha256,
            "surface_length": end - start,
        },
        "candidate_origin": {"candidate_id": None, "detectors": [], "machine_generated": False},
        "status": "pending",
        "annotator": None,
        "created_at": _now(),
    }
    rows = list(_jsonl(Path(queue_path))) if Path(queue_path).exists() else []
    if any(existing.get("task_id") == task_id for existing in rows):
        raise ValueError("task already exists")
    rows.append(row)
    rows.sort(key=lambda item: (item.get("document_id", ""), item.get("span", {}).get("start", -1), item.get("task_id", "")))
    _write_jsonl(queue_path, rows)
    return row


def validate_gold_row(row: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if "human_confirmed" in row:
        errors.append("human_confirmed is reserved for explicit human workflow and must not be present")
    if row.get("schema_version") not in {GOLD_SCHEMA_VERSION, QUEUE_SCHEMA_VERSION}:
        errors.append("unsupported schema_version")
    if not row.get("document_id"):
        errors.append("document_id is required")
    if not row.get("task_id"):
        errors.append("task_id is required")
    source = row.get("source")
    if not isinstance(source, Mapping):
        errors.append("source is required")
    else:
        for field in ("source_sha256", "text_sha256"):
            if not isinstance(source.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", source[field]):
                errors.append(f"source.{field} must be a sha256")
        provenance = source.get("provenance")
        if not isinstance(provenance, Mapping):
            errors.append("source.provenance is required")
        else:
            if provenance.get("surface_policy") != "hash_only":
                errors.append("provenance.surface_policy must be hash_only")
            if provenance.get("real") is not True:
                errors.append("provenance.real must be true for real gold")
    span = row.get("span")
    if not isinstance(span, Mapping):
        errors.append("span is required")
    else:
        try:
            if not (0 <= int(span["start"]) < int(span["end"])):
                errors.append("invalid span coordinates")
        except (KeyError, TypeError, ValueError):
            errors.append("span coordinates are required")
        if str(span.get("label")) not in {spec.label for spec in TYPE_SPECS}:
            errors.append("unsupported entity label")
        if _layout(span) not in LAYOUTS:
            errors.append("unsupported layout")
        surface_hash = span.get("surface_sha256")
        if surface_hash is not None and surface_hash != "" and not re.fullmatch(r"[a-f0-9]{64}", str(surface_hash)):
            errors.append("span.surface_sha256 must be a sha256")
        if row.get("status") == "accepted" and not re.fullmatch(r"[a-f0-9]{64}", str(surface_hash or "")):
            errors.append("accepted gold requires surface_sha256")
        if "surface" in span:
            errors.append("surface must not be present in hash-only gold")
    status = row.get("status")
    if status not in STATUSES:
        errors.append("invalid status")
    if status == "model_reviewed":
        review = row.get("model_review")
        if not isinstance(review, Mapping):
            errors.append("model_reviewed requires model_review")
        else:
            if review.get("status") != "model_reviewed":
                errors.append("model_review.status must be model_reviewed")
            if review.get("provider") != "luna":
                errors.append("model_review.provider must be luna")
            try:
                confidence = float(review.get("confidence"))
                if not 0.0 <= confidence <= 1.0:
                    errors.append("model_review.confidence must be between 0 and 1")
            except (TypeError, ValueError):
                errors.append("model_review.confidence is required")
            if not str(review.get("model") or ""):
                errors.append("model_review.model is required")
            if not str(review.get("reason") or ""):
                errors.append("model_review.reason is required")
            provenance = review.get("provenance")
            if not isinstance(provenance, Mapping) or provenance.get("surface_policy") != "hash_only":
                errors.append("model_review.provenance must be hash_only")
    if status == "accepted":
        annotator = row.get("annotator")
        if not isinstance(annotator, Mapping) or annotator.get("status") != "confirmed" or not annotator.get("id"):
            errors.append("accepted gold requires confirmed annotator")
    return errors


def validate_dataset(path: Path, require_confirmed: bool = False) -> Dict[str, Any]:
    errors: List[str] = []
    counts = Counter()
    spans_by_document: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
    for number, row in enumerate(_jsonl(Path(path)), 1):
        row_errors = validate_gold_row(row)
        if row_errors:
            errors.extend([f"line {number}: {error}" for error in row_errors])
        counts[str(row.get("status"))] += 1
        span = row.get("span") if isinstance(row.get("span"), Mapping) else {}
        try:
            spans_by_document[str(row.get("document_id"))].append((int(span["start"]), int(span["end"]), number))
        except (KeyError, TypeError, ValueError):
            pass
        if require_confirmed and row.get("status") == "accepted" and row_errors:
            errors.append(f"line {number}: invalid accepted gold")
    for document_id, spans in spans_by_document.items():
        previous_end = -1
        previous_line = None
        for start, end, number in sorted(spans):
            if start < previous_end:
                errors.append(f"line {number}: overlapping gold span with line {previous_line} in {document_id}")
            if end > previous_end:
                previous_end = end
                previous_line = number
    return {"ok": not errors, "errors": errors, "rows": sum(counts.values()), "statuses": dict(counts)}


def _accepted_real(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    result = []
    for row in rows:
        provenance = row.get("source", {}).get("provenance", {}) if isinstance(row.get("source"), Mapping) else {}
        annotator = row.get("annotator") if isinstance(row.get("annotator"), Mapping) else {}
        if (
            row.get("status") == "accepted"
            and annotator.get("status") == "confirmed"
            and provenance.get("real") is True
            and row.get("synthetic") is not True
        ):
            result.append(row)
    return result


def coverage_report(rows: Iterable[Mapping[str, Any]], minimum: int = MIN_CONFIRMED_PER_ENTITY) -> Dict[str, Any]:
    accepted = _accepted_real(rows)
    by_label = Counter(str(row.get("span", {}).get("label")) for row in accepted)
    license_unverified = sum(
        1
        for row in accepted
        if not bool(row.get("source", {}).get("provenance", {}).get("license_verified"))
    )
    labels = {}
    for spec in TYPE_SPECS:
        count = by_label.get(spec.label, 0)
        labels[spec.label] = {"confirmed_real": count, "required": minimum, "complete": count >= minimum}
    return {
        "schema_version": GOLD_SCHEMA_VERSION,
        "real_confirmed_total": len(accepted),
        "required_per_entity": minimum,
        "entities": len(TYPE_SPECS),
        "complete_entities": sum(item["complete"] for item in labels.values()),
        "all_entities_complete": all(item["complete"] for item in labels.values()),
        "license_unverified_confirmed": license_unverified,
        "labels": labels,
        "warning": (
            "Insufficient confirmed real examples; synthetic/candidate rows are excluded."
            if not all(item["complete"] for item in labels.values())
            else "Some confirmed examples have unverified licence provenance."
            if license_unverified
            else None
        ),
    }


def _key(row: Mapping[str, Any], include_label: bool = True) -> Tuple[Any, ...]:
    span = row.get("span", {})
    return (
        row.get("document_id"),
        int(span.get("start", -1)),
        int(span.get("end", -1)),
        _layout(span),
        str(span.get("label")) if include_label else "*",
    )


def _expand_rows(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Принимает и gold-строки со ``span``, и candidate JSONL с annotations."""
    expanded: List[Mapping[str, Any]] = []
    for row in rows:
        annotations = row.get("annotations")
        if isinstance(annotations, list) and "span" not in row:
            for annotation in annotations:
                if not isinstance(annotation, Mapping):
                    continue
                expanded.append({
                    "document_id": row.get("document_id"),
                    "span": annotation,
                    "status": annotation.get("status", "candidate"),
                    "source": row.get("source"),
                    "synthetic": row.get("synthetic", False),
                })
        else:
            expanded.append(row)
    return expanded


def _safe_eval_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Ошибки метрик не должны случайно вернуть surface/text/path из предикта."""
    span = row.get("span", {}) if isinstance(row.get("span"), Mapping) else {}
    return {
        "document_id": _safe_document_id(str(row.get("document_id") or "")),
        "span": {
            "start": span.get("start"),
            "end": span.get("end"),
            "label": span.get("label"),
            "layout": _layout(span),
        },
    }


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _prf(tp: int, fp: int, fn: int) -> Dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": _f1(precision, recall), "support": tp + fn}


def metrics(gold_rows: Iterable[Mapping[str, Any]], prediction_rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Exact-span metrics by entity and layout, plus confusion/error lists."""
    gold = _accepted_real(_expand_rows(gold_rows))
    # Predictions are never allowed to turn synthetic rows into evaluation
    # evidence; they are compared only against accepted real gold.
    predictions = [row for row in _expand_rows(prediction_rows) if row.get("synthetic") is not True]
    gold_keys = Counter(_key(row) for row in gold)
    pred_keys = Counter(_key(row) for row in predictions)
    labels = [spec.label for spec in TYPE_SPECS]
    by_entity: Dict[str, Dict[str, Any]] = {}
    by_layout: Dict[str, Dict[str, Any]] = {}
    for label in labels:
        tp = sum(min(gold_keys[(doc, start, end, layout, label)], pred_keys[(doc, start, end, layout, label)]) for doc, start, end, layout, item_label in gold_keys if item_label == label)
        # Count each unique key in the union to avoid duplicate prediction inflation.
        gold_count = sum(count for key, count in gold_keys.items() if key[-1] == label)
        pred_count = sum(count for key, count in pred_keys.items() if key[-1] == label)
        by_entity[label] = _prf(tp, pred_count - tp, gold_count - tp)
    for layout in LAYOUTS:
        tp = sum(min(count, pred_keys.get(key, 0)) for key, count in gold_keys.items() if key[3] == layout)
        gold_count = sum(count for key, count in gold_keys.items() if key[3] == layout)
        pred_count = sum(count for key, count in pred_keys.items() if key[3] == layout)
        by_layout[layout] = _prf(tp, pred_count - tp, gold_count - tp)
    total_tp = sum(min(count, pred_keys.get(key, 0)) for key, count in gold_keys.items())
    total_gold = sum(gold_keys.values())
    total_pred = sum(pred_keys.values())
    present_entities = [item for item in by_entity.values() if item["support"] or item["tp"] + item["fp"]]
    confusion: Counter = Counter()
    gold_by_location: Dict[Tuple[Any, ...], str] = {}
    for row in gold:
        key = _key(row, include_label=False)
        gold_by_location[key] = str(row.get("span", {}).get("label"))
    for row in predictions:
        location = _key(row, include_label=False)
        pred_label = str(row.get("span", {}).get("label"))
        gold_label = gold_by_location.get(location)
        if gold_label and gold_label != pred_label:
            confusion[(gold_label, pred_label)] += 1
    errors = {
        "false_positives": [_safe_eval_row(row) for row in predictions if _key(row) not in gold_keys],
        "false_negatives": [_safe_eval_row(row) for row in gold if _key(row) not in pred_keys],
        "confusion": [{"gold": a, "predicted": b, "count": n} for (a, b), n in sorted(confusion.items())],
    }
    micro = _prf(total_tp, total_pred - total_tp, total_gold - total_tp)
    macro_supported = {
        "precision": sum(item["precision"] for item in present_entities) / len(present_entities) if present_entities else 0.0,
        "recall": sum(item["recall"] for item in present_entities) / len(present_entities) if present_entities else 0.0,
        "f1": sum(item["f1"] for item in present_entities) / len(present_entities) if present_entities else 0.0,
    }
    # Standard macro includes all registered labels, including those with no
    # confirmed support. The separate supported-only view prevents ambiguity
    # when the real gold collection is still incomplete.
    macro = {
        "precision": sum(item["precision"] for item in by_entity.values()) / len(labels),
        "recall": sum(item["recall"] for item in by_entity.values()) / len(labels),
        "f1": sum(item["f1"] for item in by_entity.values()) / len(labels),
        "entities_with_support": len(present_entities),
        "entities_total": len(labels),
        "supported_only": macro_supported,
    }
    return {
        "schema_version": GOLD_SCHEMA_VERSION,
        "evaluation": "exact_span_label_layout",
        "gold_real_confirmed": total_gold,
        "predictions": total_pred,
        "micro": micro,
        "macro": macro,
        "by_entity": by_entity,
        "by_layout": by_layout,
        "errors": errors,
        "warning": "Metrics are not representative for entities without confirmed real gold support." if not all(item["support"] for item in by_entity.values()) else None,
    }


def build_manifest(rows: Iterable[Mapping[str, Any]], documents: Optional[Mapping[str, Any]] = None, source_kind: str = "gold") -> Dict[str, Any]:
    docs: Dict[str, str] = {}
    for row in rows:
        if row.get("document_id"):
            source = row.get("source", {})
            docs[str(row["document_id"])] = str(source.get("text_sha256") or source.get("source_sha256") or "")
    if documents:
        docs.update({str(key): str(value.get("text_sha256") or value.get("source_sha256") or "") for key, value in documents.items()})
    ordered = sorted(docs.items(), key=lambda item: item[1] or item[0])
    split_docs = {"train": [], "dev": [], "test": []}
    for index, (doc_id, digest) in enumerate(ordered):
        bucket = (int((digest or _sha(doc_id))[:8], 16) % 100)
        split = "test" if bucket >= 80 else "dev" if bucket >= 70 else "train"
        split_docs[split].append(doc_id)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_schema": GOLD_SCHEMA_VERSION,
        "dataset_kind": source_kind,
        "created_at": _now(),
        "document_count": len(docs),
        "documents": len(docs),
        "splits": split_docs,
        "split_policy": "deterministic text_sha256 bucket; documents never cross splits",
        "synthetic_excluded_from_gold": True,
        "pii_policy": "hash_only_no_surface_no_absolute_path",
        "required_confirmed_per_entity": MIN_CONFIRMED_PER_ENTITY,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Hash-only ручная gold-разметка и метрики")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-candidates", help="поставить машинные кандидаты в очередь pending")
    imp.add_argument("candidates", type=Path)
    imp.add_argument("queue", type=Path)
    imp.add_argument("--manifest", type=Path)
    imp.add_argument("--license", default="unknown_pending_verification")
    imp.add_argument("--source-catalog-id", default="local_corpus")
    queue = sub.add_parser("queue", help="показать задачи ручной очереди")
    queue.add_argument("queue", type=Path)
    queue.add_argument("--label")
    queue.add_argument("--limit", type=int, default=50)
    prep = sub.add_parser("luna-pre-review", help="создать hash-only очередь model_reviewed, не gold")
    prep.add_argument("source_queue", type=Path)
    prep.add_argument("output_queue", type=Path)
    prep.add_argument("--manifest", type=Path)
    prep.add_argument("--deficit", type=Path)
    prep.add_argument("--per-label", type=int, default=MIN_CONFIRMED_PER_ENTITY)
    prep.add_argument("--model", default="luna-pre-review-v1")
    deficit = sub.add_parser("deficit", help="отчёт доступных реальных кандидатов по 90 типам")
    deficit.add_argument("dataset", type=Path)
    deficit.add_argument("--minimum", type=int, default=MIN_CONFIRMED_PER_ENTITY)
    deficit.add_argument("--output", type=Path)
    ann = sub.add_parser("annotate", help="принять/отклонить задачу")
    ann.add_argument("queue", type=Path)
    ann.add_argument("task_id")
    ann.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    ann.add_argument("--annotator", required=True)
    ann.add_argument("--layout", choices=LAYOUTS)
    ann.add_argument("--note", default="")
    add = sub.add_parser("add", help="добавить вручную найденный span для recall")
    add.add_argument("queue", type=Path)
    add.add_argument("document_id")
    add.add_argument("--source-sha256", required=True)
    add.add_argument("--text-sha256", required=True)
    add.add_argument("--start", type=int, required=True)
    add.add_argument("--end", type=int, required=True)
    add.add_argument("--label", required=True)
    add.add_argument("--layout", choices=LAYOUTS, default="text")
    add.add_argument("--surface-sha256", default="")
    add.add_argument("--license", default="unknown_pending_verification")
    add.add_argument("--source-catalog-id", default="local_corpus")
    val = sub.add_parser("validate", help="проверить очередь/gold")
    val.add_argument("dataset", type=Path)
    cov = sub.add_parser("coverage", help="покрытие подтверждёнными реальными примерами")
    cov.add_argument("dataset", type=Path)
    cov.add_argument("--minimum", type=int, default=MIN_CONFIRMED_PER_ENTITY)
    met = sub.add_parser("metrics", help="precision/recall/F1 и ошибки")
    met.add_argument("gold", type=Path)
    met.add_argument("predictions", type=Path)
    met.add_argument("--output", type=Path)
    manifest = sub.add_parser("manifest", help="версионированные split по документам")
    manifest.add_argument("dataset", type=Path)
    manifest.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "import-candidates":
        result = import_candidates(args.candidates, args.queue, args.manifest, args.license, args.source_catalog_id)
    elif args.command == "queue":
        result = {"pending": queue_rows(args.queue, args.label, args.limit)}
    elif args.command == "luna-pre-review":
        result = prepare_review_queue(args.source_queue, args.output_queue, args.manifest, args.deficit, args.per_label, args.model)
    elif args.command == "deficit":
        result = deficit_report(_jsonl(args.dataset), args.minimum)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "annotate":
        result = annotate_queue(args.queue, args.task_id, args.decision, args.annotator, args.layout, args.note)
    elif args.command == "add":
        result = add_manual_task(args.queue, args.document_id, args.source_sha256, args.text_sha256, args.start, args.end, args.label, args.layout, args.license, args.source_catalog_id, args.surface_sha256)
    elif args.command == "validate":
        result = validate_dataset(args.dataset)
    elif args.command == "coverage":
        result = coverage_report(_jsonl(args.dataset), args.minimum)
    elif args.command == "metrics":
        result = metrics(_jsonl(args.gold), _jsonl(args.predictions))
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        result = build_manifest(_jsonl(args.dataset))
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not (isinstance(result, Mapping) and result.get("ok") is False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
