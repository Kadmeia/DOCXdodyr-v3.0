#!/usr/bin/env python3.11
"""Prepare local human-review rows with source context for spreadsheet export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from corpus_annotation import extract_xml_plain_text
from corpus_coverage import TYPE_SPECS


CORPUS = ROOT / ".cache" / "legal-corpus"
QUEUE = CORPUS / "gold" / "luna_queue.jsonl"
CANDIDATES = CORPUS / "derived" / "candidates.jsonl"
SOURCES = CORPUS / "source-plain-document" / "xml_test"
OUTPUT = ROOT / "outputs" / "gold_annotation_942" / "review_rows.json"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def compact(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def main() -> None:
    queue = read_jsonl(QUEUE)
    documents = {row["document_id"]: row for row in read_jsonl(CANDIDATES)}
    text_cache: dict[str, str] = {}
    rows = []
    integrity_errors = []
    for index, task in enumerate(queue, start=1):
        document_id = task["document_id"]
        document = documents[document_id]
        source_name = document["source"]["path"]
        if document_id not in text_cache:
            text_cache[document_id] = extract_xml_plain_text(SOURCES / source_name)
        text = text_cache[document_id]
        span = task["span"]
        start, end = int(span["start"]), int(span["end"])
        surface = text[start:end]
        digest = hashlib.sha256(surface.encode("utf-8")).hexdigest()
        if digest != span["surface_sha256"]:
            integrity_errors.append(task["task_id"])
        before = compact(text[max(0, start - 120):start])
        after = compact(text[end:min(len(text), end + 120)])
        fragment = compact(surface)
        context = f"{before} ⟦{fragment}⟧ {after}".strip()
        rows.append({
            "number": index,
            "decision": "",
            "suggested_type": span["label"],
            "correct_type": "",
            "boundaries": "",
            "fragment": fragment,
            "context": context,
            "layout": span.get("layout", "text"),
            "comment": "",
            "task_id": task["task_id"],
            "document_id": document_id,
            "source_file": source_name,
            "start": start,
            "end": end,
            "luna_confidence": task.get("model_review", {}).get("confidence"),
        })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "rows": rows,
        "entity_types": [
            {"label": item.label, "description": item.cue, "group": item.group}
            for item in TYPE_SPECS
        ],
        "stats": {
            "tasks": len(rows),
            "documents": len(text_cache),
            "integrity_errors": integrity_errors,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "tasks": len(rows), "documents": len(text_cache), "integrity_errors": len(integrity_errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
