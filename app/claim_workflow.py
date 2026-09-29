# -*- coding: utf-8 -*-
"""Verified anonymised-batch -> claim DOCX -> Qwen -> decoder binding.

The module deliberately separates drafting from privacy enforcement.  A
caller-supplied drafter receives only anonymised corpus text and the set of
allowed placeholder IDs.  Decoder originals are used only for a final local
leak check and are never passed to the drafter or Qwen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from document_restorer import is_valid_decoder_structure
from folder_pipeline import PROVENANCE_SUFFIX
from qwen_postprocessor import extract_placeholders


class ClaimWorkflowError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class VerifiedBatch:
    root: Path
    manifest_path: Path
    manifest: dict
    decoder_path: Path
    decoder: dict

    @property
    def allowed_placeholders(self) -> frozenset[str]:
        return frozenset(self.decoder)


def load_verified_batch(manifest_path: str | os.PathLike) -> VerifiedBatch:
    path = Path(manifest_path).expanduser().resolve(strict=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "docxdodyr.batch-manifest/v1":
        raise ClaimWorkflowError("Неподдерживаемая схема batch manifest")
    if payload.get("complete") is not True:
        raise ClaimWorkflowError("Пачка не помечена как полностью завершённая")
    if payload.get("errors"):
        raise ClaimWorkflowError("Нельзя готовить иск по пачке с ошибками")
    documents = payload.get("documents")
    expected_count = payload.get("expected_document_count")
    if not isinstance(documents, list) or not documents:
        raise ClaimWorkflowError("В manifest отсутствуют обработанные документы")
    if not isinstance(expected_count, int) or expected_count != len(documents):
        raise ClaimWorkflowError("Состав пачки не совпадает с ожидаемым количеством документов")
    root = path.parent
    if root.name.startswith(".docxdodyr-incomplete-") or (root / "НЕПОЛНЫЙ_ПРОГОН.json").exists():
        raise ClaimWorkflowError("Нельзя готовить иск по незавершённому прогону")
    decoder_info = payload.get("decoder") or {}
    decoder_path = (root / str(decoder_info.get("path", ""))).resolve(strict=True)
    if decoder_path.parent != root or decoder_path.is_symlink():
        raise ClaimWorkflowError("Дешифратор находится вне корня задания")
    if _sha256(decoder_path) != str(decoder_info.get("sha256", "")):
        raise ClaimWorkflowError("Хеш дешифратора не совпадает с manifest")
    decoder_hash = _sha256(decoder_path)
    decoder = json.loads(decoder_path.read_text(encoding="utf-8"))
    if not is_valid_decoder_structure(decoder):
        raise ClaimWorkflowError("Некорректная структура дешифратора")
    for record in payload.get("documents", []):
        for output in record.get("outputs", []):
            output_path = (root / str(output["path"])).resolve(strict=True)
            try:
                output_path.relative_to(root)
            except ValueError as exc:
                raise ClaimWorkflowError("Результат находится вне корня задания") from exc
            output_hash = _sha256(output_path)
            if output_hash != str(output.get("sha256", "")):
                raise ClaimWorkflowError(f"Хеш результата изменён: {output_path.name}")
            provenance_path = (root / str(output.get("provenance", ""))).resolve(strict=True)
            try:
                provenance_path.relative_to(root)
            except ValueError as exc:
                raise ClaimWorkflowError("Provenance находится вне корня задания") from exc
            if provenance_path.is_symlink():
                raise ClaimWorkflowError("Provenance не должен быть символической ссылкой")
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if provenance.get("schema") != "docxdodyr.decoder-binding/v1":
                raise ClaimWorkflowError(f"Некорректная схема provenance: {output_path.name}")
            if str(provenance.get("run_id", "")) != str(payload.get("run_id", "")):
                raise ClaimWorkflowError(f"Run ID provenance не совпадает: {output_path.name}")
            if str(provenance.get("output_sha256", "")) != output_hash:
                raise ClaimWorkflowError(f"Хеш provenance не совпадает: {output_path.name}")
            if str(provenance.get("decoder_sha256", "")) != decoder_hash:
                raise ClaimWorkflowError(f"Provenance связан с другим дешифратором: {output_path.name}")
            bound_decoder = (provenance_path.parent / str(provenance.get("decoder_path", ""))).resolve(strict=True)
            if bound_decoder != decoder_path:
                raise ClaimWorkflowError(f"Путь дешифратора в provenance не совпадает: {output_path.name}")
    return VerifiedBatch(root, path, payload, decoder_path, decoder)


def _docx_text(path: Path) -> str:
    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells if cell.text.strip())
    return "\n".join(parts)


def anonymized_corpus(batch: VerifiedBatch) -> str:
    """Read one canonical DOCX per source; never read decoder values."""

    parts = []
    for record in batch.manifest.get("documents", []):
        candidates = [item for item in record.get("outputs", []) if str(item.get("path", "")).lower().endswith(".docx")]
        if not candidates:
            continue
        path = (batch.root / str(candidates[0]["path"])).resolve(strict=True)
        parts.append(f"\n=== {record.get('source', path.name)} ===\n{_docx_text(path)}")
    corpus = "\n".join(parts).strip()
    if not corpus:
        raise ClaimWorkflowError("В проверенной пачке нет DOCX-текста для подготовки иска")
    return corpus


def _original_value(record) -> str:
    return record if isinstance(record, str) else str(record.get("original", ""))


def validate_claim_text(text: str, batch: VerifiedBatch) -> None:
    # Discover tokens independently of the decoder.  Looking only for known
    # tokens makes the ``unknown`` check tautological and lets a drafter invent
    # IDs such as ``[ФИО_999]`` without being rejected.
    token_matches = list(re.finditer(
        r"(?:\{\{[^{}\n]{1,120}\}\}|\[\[[^\[\]\n]{1,120}\]\]|"
        r"/(?=[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:\-]{0,119}/)"
        r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:\-]{0,119}/|"
        r"\[(?=[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:/\-]{0,119}\])"
        r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:/\-]{0,119}\])",
        text or "",
    ))
    protected_ranges = [(match.start(), match.end()) for match in token_matches]
    tokens = frozenset(match.group(0) for match in token_matches)
    unknown = tokens - batch.allowed_placeholders
    if unknown:
        raise ClaimWorkflowError(f"В иске есть неизвестные плейсхолдеры: {', '.join(sorted(unknown))}")
    if not tokens:
        raise ClaimWorkflowError("Иск не содержит ни одного ID из проверенной пачки")
    leaks = []
    for token, record in batch.decoder.items():
        original = _original_value(record).strip()
        if len(original) < 4:
            continue
        for match in re.finditer(re.escape(original), text, re.IGNORECASE):
            if not any(start < match.end() and match.start() < end for start, end in protected_ranges):
                leaks.append(token)
                break
    if leaks:
        safe_tokens = ", ".join(sorted(set(leaks)))
        raise ClaimWorkflowError(
            f"В проекте иска обнаружены исходные значения из дешифратора: {safe_tokens}"
        )


def _set_run_font(run, *, size=12, bold=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold


def _build_docx(text: str, output_path: Path) -> None:
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(3)
    section.right_margin = Cm(1.5)
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for style_name, size, bold in (
        ("Claim Heading", 12, True),
        ("Claim Metadata", 12, False),
    ):
        if style_name not in document.styles:
            style = document.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
        else:
            style = document.styles[style_name]
        style.font.name = "Times New Roman"
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Times New Roman")
        style.font.size = Pt(size)
        style.font.bold = bold
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.line_spacing = 1.0 if style_name == "Claim Metadata" else 1.5

    lines = text.splitlines()
    for raw in lines:
        value = raw.rstrip()
        if not value:
            continue
        paragraph = document.add_paragraph()
        if value.startswith("@right "):
            paragraph.style = document.styles["Claim Metadata"]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            run = paragraph.add_run(value[7:].strip())
            _set_run_font(run)
        elif value.startswith("@center "):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(value[8:].strip())
            _set_run_font(run)
        elif value.startswith("# "):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(value[2:].strip())
            _set_run_font(run, size=14, bold=True)
        elif value.startswith("## "):
            paragraph.style = document.styles["Claim Heading"]
            paragraph.paragraph_format.keep_with_next = True
            run = paragraph.add_run(value[3:].strip())
            _set_run_font(run, size=12, bold=True)
        else:
            paragraph.paragraph_format.first_line_indent = Cm(1.25)
            run = paragraph.add_run(value)
            _set_run_font(run)
    document.save(str(output_path))


def create_claim_from_verified_batch(
    manifest_path: str | os.PathLike,
    output_path: str | os.PathLike,
    *,
    draft_builder: Callable[[str, frozenset[str]], str],
    qwen_processor=None,
) -> dict:
    """Create, optionally polish, verify and bind a claim to its decoder."""

    batch = load_verified_batch(manifest_path)
    corpus = anonymized_corpus(batch)
    draft = str(draft_builder(corpus, batch.allowed_placeholders) or "").strip()
    # A natural-language drafter can reuse a value already present in the
    # batch decoder (including a value first discovered in another file).
    # Reconcile the complete draft before either validation or model access.
    from batch_reconcile import reconcile_text
    draft, _claim_reconciliations = reconcile_text(draft, batch.decoder)
    validate_claim_text(draft, batch)
    final_text = draft
    qwen_status = "disabled"
    if qwen_processor is not None:
        final_text = qwen_processor.process(draft)
        qwen_status = str(getattr(qwen_processor, "last_status", "unknown"))
        validate_claim_text(final_text, batch)

    destination = Path(output_path).expanduser().resolve(strict=False)
    output_root = batch.root / "Обезличенные документы"
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        destination.relative_to(output_root.resolve())
    except ValueError as exc:
        raise ClaimWorkflowError("Иск должен сохраняться в папке «Обезличенные документы»") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    _build_docx(final_text, destination)

    provenance = {
        "schema": "docxdodyr.decoder-binding/v1",
        "run_id": str(batch.manifest.get("run_id", "")),
        "decoder_path": os.path.relpath(batch.decoder_path, destination.parent),
        "decoder_sha256": _sha256(batch.decoder_path),
        "output_sha256": _sha256(destination),
        "purpose": "claim",
        "qwen_status": qwen_status,
    }
    provenance_path = destination.with_name(destination.name + PROVENANCE_SUFFIX)
    _write_json_atomic(provenance_path, provenance)
    batch.manifest.setdefault("derived_documents", []).append({"outputs": [{
        "path": str(destination.relative_to(batch.root)),
        "sha256": _sha256(destination),
        "provenance": str(provenance_path.relative_to(batch.root)),
    }]})
    _write_json_atomic(batch.manifest_path, batch.manifest)
    return {
        "output_path": str(destination),
        "provenance_path": str(provenance_path),
        "qwen_status": qwen_status,
        "placeholder_count": len(extract_placeholders(final_text)),
    }


__all__ = [
    "ClaimWorkflowError", "VerifiedBatch", "anonymized_corpus",
    "create_claim_from_verified_batch", "load_verified_batch", "validate_claim_text",
]
