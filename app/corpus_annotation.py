# -*- coding: utf-8 -*-
"""Корпусная разметка кандидатов LegalPullenti.

Модуль намеренно не содержит собственного NER. Кандидаты строятся только из
``iter_pullenti_spans``; юридические реквизиты приходят в этот результат как
нативные ``LEGALENTITY`` от зарегистрированного Pullenti-картриджа.
JSONL по умолчанию не содержит исходных поверхностей сущностей: для ручной
проверки рядом можно сохранить замаскированные тексты.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from entity_registry import grammar_profile
from legal_pullenti import (
    EntitySpan,
    initialize_ner,
    iter_pullenti_spans,
    refine_composite_spans,
)


SCHEMA_VERSION = "legalpullenti-span-1.0"
ANNOTATION_LABELS = {
    "PER", "ORG", "ADDRESS", "PHONE_NUMBER", "EMAIL", "WEBSITE",
    "INN", "KPP", "OGRN", "OGRNIP", "BIK", "SNILS", "PASSPORT",
    "RU_ACCOUNT", "RU_CORR_ACCOUNT",
}
TEXT_SUFFIXES = {".txt", ".text", ".md", ".rst", ".xml"}
XML_SUFFIXES = {".xml"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _document_id(relative_path: str, text: str) -> str:
    digest = sha256_text(relative_path + "\0" + text)[:12]
    return "doc_" + digest


def source_metadata(path: Path, root: Optional[Path] = None, encoding: str = "utf-8") -> Dict[str, Any]:
    """Собирает воспроизводимую метаинформацию без абсолютного пути."""

    path = Path(path)
    root = Path(root) if root is not None else path.parent
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    raw = path.read_bytes()
    stat = path.stat()
    return {
        "path": relative,
        "kind": "extracted_text",
        "encoding": encoding,
        "size_bytes": stat.st_size,
        "sha256": sha256_bytes(raw),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


_NO_SPACE_BEFORE = set(",.;:!?%)]}»”\u201d")
_NO_SPACE_AFTER = set("([{«“\u201c")


def _join_xml_words(words: Sequence[str]) -> str:
    """Восстанавливает plain text из токенов ``w`` с детерминированными пробелами."""

    result = ""
    for token in words:
        token = token or ""
        if not token:
            continue
        if not result:
            result = token
            continue
        previous = result[-1]
        first = token[0]
        no_space = (
            first in _NO_SPACE_BEFORE
            or previous in _NO_SPACE_AFTER
            or first in "-–—"
            or previous in "-–—"
        )
        result += ("" if no_space else " ") + token
    return result


def extract_xml_plain_text(xml_value: Any) -> str:
    """Извлекает title и body из XML Plain Document Project.

    Важный контракт для offsets: текст строится только один раз из декодированных
    ``<title>`` и последовательности ``<w>``; тот же результат используется для
    Pullenti и для хеширования. Абзацы получают ``\n``, предложения внутри
    абзаца разделяются пробелом, пунктуация остаётся отдельными токенами.
    """

    if isinstance(xml_value, Path):
        xml_value = xml_value.read_bytes()
    if isinstance(xml_value, str):
        xml_value = xml_value.encode("utf-8")
    root = ET.fromstring(xml_value)
    title_node = root.find("./head/title")
    title = "".join(title_node.itertext()).strip() if title_node is not None else ""
    paragraphs: List[str] = []
    for paragraph in root.findall(".//p"):
        sentence_parts = []
        for sentence in paragraph.findall(".//s"):
            words = ["".join(word.itertext()) for word in sentence.findall(".//w")]
            sentence_text = _join_xml_words(words)
            if sentence_text:
                sentence_parts.append(sentence_text)
        if sentence_parts:
            paragraphs.append(" ".join(sentence_parts))
    body = "\n".join(paragraphs)
    if title and body:
        return title + "\n" + body
    return title or body


def load_source_text(path: Path) -> Tuple[str, str]:
    """Возвращает plain text и способ извлечения для метаданных."""

    if path.suffix.lower() in XML_SUFFIXES:
        return extract_xml_plain_text(path), "xml_title_and_w_tokens_v1"
    return path.read_text(encoding="utf-8-sig"), "utf8_text_v1"


def _pullenti_processor():
    """Создаёт один процессор; импорт Pullenti остаётся ленивым для тестов."""

    try:
        from pullenti.ner.ProcessorService import ProcessorService
    except ImportError as exc:
        raise RuntimeError("PullentiPython не установлен в текущем Python") from exc
    initialize_ner()
    return ProcessorService.create_processor()


def _text_chunks(text: str, max_chunk_chars: int = 30000) -> Iterable[Tuple[int, str]]:
    """Режет текст на абзацные chunks, сохраняя абсолютную позицию начала."""

    if len(text) <= max_chunk_chars:
        yield 0, text
        return
    start = 0
    while start < len(text):
        proposed = min(len(text), start + max_chunk_chars)
        if proposed < len(text):
            boundary = text.rfind("\n", start + 1, proposed)
            if boundary <= start:
                boundary = text.rfind(" ", start + 1, proposed)
            if boundary <= start:
                boundary = proposed
        else:
            boundary = proposed
        yield start, text[start:boundary]
        start = boundary


def _select_non_overlapping_fast(spans: Iterable[EntitySpan]) -> List[EntitySpan]:
    """Выбирает длиннейшие spans без квадратичной проверки всех пар."""

    selected: List[Tuple[int, int, EntitySpan]] = []
    selected_starts: List[int] = []
    for span in sorted(spans, key=lambda item: (-item.length, item.start, item.label)):
        position = bisect.bisect_left(selected_starts, span.start)
        overlaps = False
        if position and selected[position - 1][1] > span.start:
            overlaps = True
        if position < len(selected) and selected[position][0] < span.end:
            overlaps = True
        if not overlaps:
            selected.insert(position, (span.start, span.end, span))
            selected_starts.insert(position, span.start)
    return [item[2] for item in selected]


def detect_spans(text: str, processor=None, max_chunk_chars: int = 30000) -> Tuple[List[EntitySpan], Dict[Tuple[int, int, str], set]]:
    """Возвращает отобранные spans и источники детектора для каждого span.

    ``processor`` можно передать из CLI, чтобы не инициализировать Pullenti на
    каждый файл. В тестах допускается fake processor с методом ``process``.
    """

    if processor is None:
        processor = _pullenti_processor()
    try:
        from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    except ImportError as exc:
        raise RuntimeError("PullentiPython не установлен в текущем Python") from exc
    pullenti: List[EntitySpan] = []
    # Один гигантский документ из открытого корпуса имеет десятки мегабайт.
    # Pullenti работает по chunks, а offsets возвращаются обратно в координаты
    # исходного plain text. Второй regex-слой здесь намеренно не вызывается:
    # реквизиты должны приходить как нативные LEGALENTITY Pullenti.
    for offset, chunk in _text_chunks(text, max_chunk_chars):
        analysis = processor.process(SourceOfAnalysis(chunk), None, None)
        # Уточняем вложенные контакты внутри chunk до сдвига offsets. Глобальный
        # refine по миллионам символов имеет квадратичную стоимость на большом
        # нормативном документе; пересечения между chunks всё равно разрешаются
        # следующим select_non_overlapping.
        local_spans = list(iter_pullenti_spans(analysis, chunk))
        local_spans = refine_composite_spans(chunk, local_spans)
        pullenti.extend(
            EntitySpan(span.text, span.start + offset, span.end + offset, span.label)
            for span in local_spans
        )
    selected = _select_non_overlapping_fast(pullenti)
    detected: Dict[Tuple[int, int, str], set] = {}
    for span in selected:
        key = (span.start, span.end, span.label)
        methods = set()
        for candidate in pullenti:
            if candidate.label == span.label and candidate.start == span.start and candidate.end >= span.end:
                methods.add("pullenti")
        detected[key] = methods or {"pullenti"}
    return selected, detected


def _annotation(document_id: str, index: int, text: str, span: EntitySpan, methods: Iterable[str], include_surface: bool) -> Dict[str, Any]:
    profile = grammar_profile(span.text, span.label)
    if not include_surface:
        # Даже лемма head_word может быть именем собственным. В hash-only
        # корпусе она не должна попадать в JSONL в открытом виде.
        head_word = profile.pop("head_word", "")
        profile["head_word_sha256"] = sha256_text(head_word) if head_word else None
    result: Dict[str, Any] = {
        "id": "%s:a%04d" % (document_id, index),
        "start": span.start,
        "end": span.end,
        "label": span.label,
        "detectors": sorted(set(methods)),
        "status": "candidate",
        "surface_sha256": sha256_text(span.text),
        "surface_length": len(span.text),
        "grammar_profile": profile,
    }
    if include_surface:
        result["surface"] = span.text
    return result


def mask_text(text: str, annotations: Sequence[Dict[str, Any]]) -> str:
    """Заменяет spans справа налево и повторяющиеся поверхности.

    Повторное точное вхождение той же поверхности также маскируется в
    производной копии: иначе безопасный preview мог бы сохранить PII, даже
    если Pullenti отдал только одно из нескольких вхождений.
    """

    pieces = text
    surfaces = []
    for annotation in sorted(annotations, key=lambda item: (item["start"], item["end"]), reverse=True):
        start, end = annotation["start"], annotation["end"]
        if 0 <= start < end <= len(text):
            surfaces.append((text[start:end], "[" + annotation["label"] + "]"))
        pieces = pieces[:start] + "[" + annotation["label"] + "]" + pieces[end:]
    for surface, replacement in sorted(set(surfaces), key=lambda item: len(item[0]), reverse=True):
        if surface:
            pieces = pieces.replace(surface, replacement)
    return pieces


def build_candidate_record(
    text: str,
    source: Dict[str, Any],
    processor=None,
    include_surface: bool = False,
    spans: Optional[Sequence[EntitySpan]] = None,
    detected_methods: Optional[Dict[Tuple[int, int, str], set]] = None,
    max_chunk_chars: int = 30000,
) -> Dict[str, Any]:
    """Строит одну JSONL-запись документа."""

    document_id = _document_id(source.get("path", "document.txt"), text)
    if spans is None:
        spans, detected_methods = detect_spans(text, processor, max_chunk_chars=max_chunk_chars)
    detected_methods = detected_methods or {}
    annotations = [
        _annotation(document_id, index, text, span, detected_methods.get((span.start, span.end, span.label), {"pullenti"}), include_surface)
        for index, span in enumerate(spans, 1)
    ]
    masked = mask_text(text, annotations)
    validation = validate_annotation_record(
        {
            "schema_version": SCHEMA_VERSION,
            "document_id": document_id,
            "source": source,
            "text_length": len(text),
            "text_sha256": sha256_text(text),
            "annotations": annotations,
        },
        source_text=text,
        masked_text=masked,
        check_leaks=True,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "created_at": _utc_now(),
        "source": source,
        "text_length": len(text),
        "text_sha256": sha256_text(text),
        "annotation_count": len(annotations),
        "annotations": annotations,
        "safety": {
            "surface_policy": "included" if include_surface else "hash_only",
            "masking_integrity_checked": True,
            "masked_text_sha256": sha256_text(masked),
            "masking_integrity_ok": validation["ok"],
            "residual_candidate_scan": {
                "performed": False,
                "candidate_count": None,
                "is_evidence_of_absence": False,
            },
            "leaked_surface_ids": validation.get("leaked_surface_ids", []),
        },
    }


def validate_annotation_record(
    record: Dict[str, Any],
    source_text: Optional[str] = None,
    masked_text: Optional[str] = None,
    check_leaks: bool = False,
) -> Dict[str, Any]:
    """Проверяет JSONL-запись и, если передан исходник, точность координат."""

    errors: List[str] = []
    warnings: List[str] = []
    annotations = record.get("annotations")
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    if not isinstance(record.get("document_id"), str) or not record["document_id"]:
        errors.append("document_id must be a non-empty string")
    if not isinstance(annotations, list):
        errors.append("annotations must be a list")
        annotations = []
    text_length = record.get("text_length")
    if not isinstance(text_length, int) or text_length < 0:
        errors.append("text_length must be a non-negative integer")
        text_length = 0
    ordered = sorted(annotations, key=lambda item: (item.get("start", -1), item.get("end", -1)))
    previous_end = -1
    for item in ordered:
        if not isinstance(item, dict):
            errors.append("annotation must be an object")
            continue
        start, end, label = item.get("start"), item.get("end"), item.get("label")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= text_length):
            errors.append("invalid span coordinates")
        if isinstance(start, int) and start < previous_end:
            errors.append("overlapping annotations")
        if isinstance(end, int):
            previous_end = max(previous_end, end)
        if label not in ANNOTATION_LABELS:
            errors.append("unsupported annotation label: %s" % label)
        if not isinstance(item.get("surface_sha256"), str) or len(item["surface_sha256"]) != 64:
            errors.append("surface_sha256 is required")
        if item.get("status") not in {"candidate", "accepted", "rejected"}:
            errors.append("invalid annotation status")
    if source_text is not None:
        if record.get("text_length") != len(source_text):
            errors.append("text_length does not match source")
        if record.get("text_sha256") != sha256_text(source_text):
            errors.append("text_sha256 does not match source")
        for item in annotations:
            if not isinstance(item, dict) or not isinstance(item.get("start"), int) or not isinstance(item.get("end"), int):
                continue
            if 0 <= item["start"] < item["end"] <= len(source_text):
                surface = source_text[item["start"]:item["end"]]
                if sha256_text(surface) != item.get("surface_sha256"):
                    errors.append("surface hash does not match source at %s" % item.get("id", "unknown"))
                if "surface" in item and item["surface"] != surface:
                    errors.append("included surface does not match source")
    leaked: List[str] = []
    if check_leaks and source_text is not None:
        expected_masked = mask_text(source_text, annotations)
        if masked_text is None:
            errors.append("masked_text is required for masking check")
        elif masked_text != expected_masked:
            errors.append("masked_text differs from positional masking result")
        for item in annotations:
            if not isinstance(item, dict) or source_text is None:
                continue
            start, end = item.get("start"), item.get("end")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(source_text):
                surface = source_text[start:end]
            if surface and surface in (masked_text or ""):
                leaked.append(item.get("id", "unknown"))
        if leaked:
            errors.append("sensitive surface remains in masked text")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "leaked_surface_ids": leaked}


def iter_text_files(root: Path) -> Iterable[Path]:
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def build_candidate_file(
    input_dir: Path,
    output_jsonl: Path,
    masked_dir: Optional[Path] = None,
    include_surface: bool = False,
    stats_json: Optional[Path] = None,
    max_chunk_chars: int = 30000,
) -> int:
    """Обрабатывает локальную папку и пишет по одной записи на строку."""

    input_dir = Path(input_dir).resolve()
    output_jsonl = Path(output_jsonl)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if masked_dir is not None:
        Path(masked_dir).mkdir(parents=True, exist_ok=True)
    processor = _pullenti_processor()
    count = 0
    stats: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_dir": input_dir.name,
        "documents": 0,
        "documents_with_candidates": 0,
        "annotations": 0,
        "labels": {},
        "detectors": {"pullenti": 0, "legal_pullenti": 0},
        # This proves only that every selected span was masked correctly.  It
        # is not evidence that Pullenti found every possible PII occurrence.
        "masking_integrity": {"checked": 0, "passed": 0, "failed": 0},
    }
    with output_jsonl.open("w", encoding="utf-8") as stream:
        for path in iter_text_files(input_dir):
            if path.resolve() == output_jsonl.resolve():
                continue
            try:
                text, extractor = load_source_text(path)
            except UnicodeDecodeError as exc:
                print("Пропущен не-UTF-8 файл %s: %s" % (path, exc), file=sys.stderr)
                continue
            relative = path.relative_to(input_dir)
            source = source_metadata(path, input_dir)
            source["kind"] = "xml_plain_document" if path.suffix.lower() in XML_SUFFIXES else "extracted_text"
            source["extractor"] = extractor
            record = build_candidate_record(text, source, processor=processor, include_surface=include_surface, max_chunk_chars=max_chunk_chars)
            stats["documents"] += 1
            stats["annotations"] += record["annotation_count"]
            if record["annotation_count"]:
                stats["documents_with_candidates"] += 1
            for annotation in record["annotations"]:
                label = annotation["label"]
                stats["labels"][label] = stats["labels"].get(label, 0) + 1
                for detector in annotation["detectors"]:
                    stats["detectors"][detector] = stats["detectors"].get(detector, 0) + 1
            stats["masking_integrity"]["checked"] += 1
            if record["safety"]["masking_integrity_ok"]:
                stats["masking_integrity"]["passed"] += 1
            else:
                stats["masking_integrity"]["failed"] += 1
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            if masked_dir is not None:
                masked_path = Path(masked_dir) / relative
                masked_path.parent.mkdir(parents=True, exist_ok=True)
                masked_path.write_text(mask_text(text, record["annotations"]), encoding="utf-8")
            count += 1
    if stats_json is not None:
        stats["generated_at"] = _utc_now()
        stats["documents"] = count
        stats_path = Path(stats_json)
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return count


def _cli_validate(path: Path) -> int:
    errors = 0
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print("%s:%d: invalid JSON: %s" % (path, line_number, exc), file=sys.stderr)
                errors += 1
                continue
            result = validate_annotation_record(record)
            if not result["ok"]:
                print("%s:%d: %s" % (path, line_number, "; ".join(result["errors"])), file=sys.stderr)
                errors += 1
    return 1 if errors else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Кандидаты span-разметки на базе LegalPullenti")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="обработать папку UTF-8 текстов")
    build.add_argument("input_dir", type=Path)
    build.add_argument("output_jsonl", type=Path)
    build.add_argument("--masked-dir", type=Path, help="сохранить замаскированные тексты")
    build.add_argument("--stats", type=Path, help="сохранить агрегированную статистику JSON")
    build.add_argument("--max-chunk-chars", type=int, default=30000, help="верхняя граница plain-text chunk для Pullenti")
    build.add_argument("--include-surface", action="store_true", help="включить исходные поверхности (небезопасно для корпуса)")
    validate = subparsers.add_parser("validate", help="проверить JSONL-схему и координаты")
    validate.add_argument("jsonl", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        count = build_candidate_file(args.input_dir, args.output_jsonl, args.masked_dir, args.include_surface, args.stats, args.max_chunk_chars)
        print("Готово: %d документов" % count)
        return 0
    return _cli_validate(args.jsonl)


if __name__ == "__main__":
    raise SystemExit(main())
