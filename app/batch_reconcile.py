# -*- coding: utf-8 -*-
"""Final, decoder-driven reconciliation for anonymized batch artefacts.

Entities can first be discovered in a late document while an earlier document
contains the same value in a context that Pullenti did not classify.  A batch
therefore is not releasable until every produced document has been checked
against the *complete* decoder assembled for that run.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)


# Exact cross-document propagation is safe only for shape-bound identifiers.
# PERSON/ORG/ADDRESS/DATE values are context-dependent: the same surface can
# be a surname or a common word, a company or a public authority, a postal
# address or a jurisdiction. Those categories must be re-evaluated by NER in
# each local sentence instead of being replaced as global substrings.
_GLOBAL_EXACT_TYPES = frozenset({
    "INN", "KPP", "OGRN", "OGRNIP", "BIK", "SNILS", "PASSPORT",
    "FOREIGN_PASSPORT", "RU_ACCOUNT", "RU_CORR_ACCOUNT", "PERSONAL_ACCOUNT",
    "BANK_CARD", "PHONE_NUMBER", "EMAIL", "WEBSITE", "TELEGRAM_NICK",
    "PURCHASE_NUMBER", "CONTRACT_NUMBER", "COURT_CASE_NUMBER",
    "CRIMINAL_CASE_NUMBER", "ENFORCEMENT_PROCEEDING_NUMBER",
    "POWER_OF_ATTORNEY_NUMBER", "CADASTRAL_NUMBER", "EGRN_RECORD_NUMBER",
    "PROPERTY_RIGHT_NUMBER", "PROPERTY_CONDITIONAL_NUMBER", "VEHICLE_VIN",
    "VEHICLE_PLATE", "PTS_NUMBER", "STS_NUMBER", "LAWYER_ID_NUMBER",
    "LAWYER_REGISTRY_NUMBER", "OMS_POLICY", "DMS_POLICY", "INSURANCE_POLICY",
    "MEDICAL_RECORD_NUMBER", "SICK_LEAVE_NUMBER", "IP_ADDRESS", "MAC_ADDRESS",
})


def _original_value(record) -> str:
    if isinstance(record, str):
        return record
    if isinstance(record, dict) and isinstance(record.get("original"), str):
        return record["original"]
    return ""


def _replacement_pairs(mapping: dict) -> list[tuple[str, str]]:
    """Return deterministic value->token pairs, preferring the first token."""

    by_value: dict[str, tuple[str, str]] = {}
    for token, record in mapping.items():
        if isinstance(record, dict):
            entity_type = str(record.get("type") or "").upper()
            if entity_type not in _GLOBAL_EXACT_TYPES:
                continue
        value = _original_value(record).strip()
        if (
            len(value) < 2
            or not re.search(r"[A-Za-zА-Яа-яЁё0-9]", value)
            or re.fullmatch(r"_?\d*\]", value)
            or re.fullmatch(r"\[[^\]]*", value)
        ):
            continue
        by_value.setdefault(value.casefold(), (value, str(token)))
    return sorted(by_value.values(), key=lambda item: (-len(item[0]), item[0].casefold()))


def reconcile_text(text: str, mapping: dict) -> tuple[str, int]:
    """Replace every exact decoder value, without rewriting existing tokens."""

    if not text or not mapping:
        return text, 0
    tokens = sorted((str(token) for token in mapping), key=len, reverse=True)
    token_pattern = re.compile("|".join(re.escape(token) for token in tokens))
    protected = [(m.start(), m.end()) for m in token_pattern.finditer(text)]
    changes = 0
    result = text
    # Recompute protected ranges after each value.  This keeps newly inserted
    # placeholders protected from shorter decoder values.
    for original, token in _replacement_pairs(mapping):
        # Decoder values are semantic tokens, not arbitrary substrings. Word
        # boundaries on lexical edges keep names and acronyms out of longer
        # words while still supporting punctuation-rich identifiers.
        left = r"(?<![\wА-Яа-яЁё])" if re.match(r"[\wА-Яа-яЁё]", original) else ""
        right = r"(?![\wА-Яа-яЁё])" if re.search(r"[\wА-Яа-яЁё]$", original) else ""
        pattern = re.compile(left + re.escape(original) + right, re.IGNORECASE)
        matches = list(pattern.finditer(result))
        if not matches:
            continue
        protected = [(m.start(), m.end()) for m in token_pattern.finditer(result)]
        for match in reversed(matches):
            if any(start < match.end() and match.start() < end for start, end in protected):
                continue
            result = result[:match.start()] + token + result[match.end():]
            changes += 1
    return result, changes


def _replace_across_nodes(nodes, mapping: dict) -> int:
    if not nodes:
        return 0
    current = "".join(node.text or "" for node in nodes)
    reconciled, count = reconcile_text(current, mapping)
    if not count:
        return 0
    # Preserve the XML/run topology: distribute the new string over the
    # existing text nodes and put any length delta into the last node.
    cursor = 0
    for index, node in enumerate(nodes):
        old_len = len(node.text or "")
        if index == len(nodes) - 1:
            node.text = reconciled[cursor:]
        else:
            node.text = reconciled[cursor:cursor + old_len]
            cursor += old_len
    return count


def reconcile_docx(path: str | os.PathLike, mapping: dict) -> int:
    """Patch textual OOXML parts atomically while preserving run formatting."""

    source = Path(path)
    if not source.is_file() or not zipfile.is_zipfile(source):
        logger.warning("Пропуск файла для сверки DOCX (не является валидным ZIP/DOCX): %s", source)
        return 0
    fd, temp_name = tempfile.mkstemp(prefix=f".{source.stem}.", suffix=source.suffix, dir=source.parent)
    os.close(fd)
    total = 0
    try:
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(temp_name, "w") as zout:
            from hidden_data import validate_safe_zip_archive

            # Reconciliation republishes every OOXML member. Reject unsafe
            # members before copying any of them into the output archive.
            validate_safe_zip_archive(zin)
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".xml"):
                    try:
                        root = etree.fromstring(data)
                        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                        paragraphs = root.xpath(".//w:p", namespaces=ns)
                        part_total = 0
                        for paragraph in paragraphs:
                            part_total += _replace_across_nodes(
                                paragraph.xpath(".//w:t", namespaces=ns), mapping
                            )
                        if part_total:
                            total += part_total
                            data = etree.tostring(
                                root, xml_declaration=True, encoding="UTF-8"
                            )
                    except (etree.XMLSyntaxError, ValueError):
                        pass
                zout.writestr(item, data)
        os.replace(temp_name, source)
        return total
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def transform_docx_paragraphs(path: str | os.PathLike, transform) -> int:
    """Apply ``transform(text)->(text,count)`` to every OOXML paragraph."""

    source = Path(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{source.stem}.", suffix=source.suffix, dir=source.parent)
    os.close(fd)
    total = 0
    try:
        with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(temp_name, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith(".xml"):
                    try:
                        root = etree.fromstring(data)
                        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                        part_changed = False
                        for paragraph in root.xpath(".//w:p", namespaces=ns):
                            nodes = paragraph.xpath(".//w:t", namespaces=ns)
                            current = "".join(node.text or "" for node in nodes)
                            if not current.strip():
                                continue
                            replacement, count = transform(current)
                            if not count or replacement == current:
                                continue
                            cursor = 0
                            for index, node in enumerate(nodes):
                                old_len = len(node.text or "")
                                if index == len(nodes) - 1:
                                    node.text = replacement[cursor:]
                                else:
                                    node.text = replacement[cursor:cursor + old_len]
                                    cursor += old_len
                            total += int(count)
                            part_changed = True
                        if part_changed:
                            data = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
                    except (etree.XMLSyntaxError, ValueError):
                        pass
                zout.writestr(item, data)
        os.replace(temp_name, source)
        return total
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def reconcile_xlsx(path: str | os.PathLike, mapping: dict) -> int:
    """Reconcile textual workbook cells; formulas and non-text cells remain intact."""

    from openpyxl import load_workbook

    source = Path(path)
    if not source.is_file() or not zipfile.is_zipfile(source):
        logger.warning("Пропуск файла для сверки XLSX (не является валидным ZIP/XLSX): %s", source)
        return 0
    is_xlsm = source.suffix.casefold() == ".xlsm"
    workbook = load_workbook(source, keep_vba=is_xlsm)
    from xlsx_semantic import close_workbook
    try:
        total = 0
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and not cell.value.startswith("="):
                        value, count = reconcile_text(cell.value, mapping)
                        if count:
                            cell.value = value
                            total += count
        if total:
            fd, temp_name = tempfile.mkstemp(prefix=f".{source.stem}.", suffix=source.suffix, dir=source.parent)
            os.close(fd)
            try:
                workbook.save(temp_name)
                os.replace(temp_name, source)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
        return total
    finally:
        close_workbook(workbook)


def reconcile_markdown(path: str | os.PathLike, mapping: dict) -> int:
    """Reconcile Markdown text files against the batch mapping."""
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    reconciled, count = reconcile_text(text, mapping)
    if count:
        fd, temp_name = tempfile.mkstemp(prefix=f".{source.stem}.", suffix=source.suffix, dir=source.parent)
        os.close(fd)
        try:
            with open(temp_name, "w", encoding="utf-8") as f:
                f.write(reconciled)
            os.replace(temp_name, source)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    return count


def reconcile_document(path: str | os.PathLike, mapping: dict) -> int:
    suffix = Path(path).suffix.casefold()
    if suffix in {".docx", ".docm"}:
        return reconcile_docx(path, mapping)
    if suffix in {".xlsx", ".xlsm"}:
        return reconcile_xlsx(path, mapping)
    if suffix == ".md":
        return reconcile_markdown(path, mapping)
    return 0


__all__ = [
    "reconcile_text", "reconcile_docx", "reconcile_xlsx", "reconcile_markdown",
    "reconcile_document", "transform_docx_paragraphs",
]
