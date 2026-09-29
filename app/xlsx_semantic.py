"""Deterministic, header-aware privacy rules for spreadsheets.

Free-form NER is useful in prose, but columnar workbooks already declare the
meaning of a value in their headers.  These helpers keep inventory/business
registries stable and mask short numeric identifiers that an NER model cannot
classify reliably when it sees the value alone.
"""

from __future__ import annotations

import re
from pathlib import Path


_DATE = re.compile(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[.](?:0?[1-9]|1[0-2])[.](?:19|20)\d{2}(?!\d)")
_ACCOUNT = re.compile(r"(?<!\d)(?:\d[ \u00a0]*){20}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?7|8|9)[\s\u00a0()\-–—‒]*(?:\d[\s\u00a0()\-–—‒]*){9,11}(?!\d)")
_DOC_AFTER_NUMBER = re.compile(r"(?i)(?P<prefix>\b(?:сч[её]т(?:у|а)?|сч\.?|счету)\s*№\s*)(?P<value>[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9./\-]*)")


def _norm(value) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def sheet_profile(rows, title: str = "") -> str:
    first = [_norm(v) for row in rows[:3] for v in row]
    joined = " | ".join(first)
    top = " | ".join(_norm(v) for row in rows[:12] for v in row)
    if "инвентарный номер" in joined or _norm(title) in {"мц", "ос", "ано осв"}:
        return "inventory"
    if "полное наименование фсо" in joined:
        return "public_org_registry"
    if "полное наименование ип" in joined:
        return "sole_trader_registry"
    if "назначение платежа" in joined and ("вх.номер" in joined or "контрагент" in joined):
        return "payment_register"
    if "назначение платежа" in joined and "номер документа" in joined:
        return "bank_operations"
    if "владелец счета" in joined or "владелец счёта" in joined:
        return "simple_bank_statement"
    if _norm(title).startswith("выписка"):
        return "bank_statement"
    if "образец заполнения платежного поручения" in top:
        return "payment_order"
    if "организация" in joined and "телефон" in joined and "сайт" in joined:
        return "supplier_directory"
    return "generic"


def header_map(rows):
    """Return the most useful top-row header for each column."""
    result = {}
    for row in rows[:12]:
        for col, value in enumerate(row):
            n = _norm(value)
            if n and col not in result:
                result[col] = n
        if len(result) >= max((len(r) for r in rows[:12]), default=0):
            break
    return result


def mask_filename(name: str, placeholder) -> str:
    def safe(label, value):
        return placeholder(label, value).replace("/", "-")

    stem, suffix = Path(name).stem, Path(name).suffix
    stem = _ACCOUNT.sub(lambda m: safe("RU_ACCOUNT", m.group(0)), stem)
    stem = re.sub(r"(?i)(\b(?:бик\s*)?)(\d{9})(?!\d)",
                  lambda m: m.group(1) + safe("BIK", m.group(2)), stem)
    stem = _DATE.sub(lambda m: safe("DATE", m.group(0)), stem)
    stem = re.sub(r"(?i)(\bсч[её]т\s*№\s*)([\w./-]+)",
                  lambda m: m.group(1) + safe("DOCUMENT_NUMBER", m.group(2)), stem)
    return stem + suffix


def mask_sheet_title(title: str, placeholder) -> str:
    def safe(label, original):
        return placeholder(label, original).replace("[", "(").replace("]", ")").replace("/", "-")

    value = _ACCOUNT.sub(lambda m: safe("RU_ACCOUNT", m.group(0)), title)
    return value[:31]


def targeted_cell(text: str, profile: str, row: int, col: int, headers, placeholder):
    """Return ``(value, count, handled)`` for a semantically declared cell."""
    if not text.strip():
        return text, 0, profile != "generic"
    header = headers.get(col, "")
    header_rows = {
        "public_org_registry": {0}, "sole_trader_registry": {0},
        "payment_register": {0}, "supplier_directory": {0},
        "bank_operations": {1},
    }
    if row in header_rows.get(profile, set()):
        return text, 0, True

    if profile == "inventory":
        return text, 0, True

    if profile == "public_org_registry":
        if "контакт" not in header:
            return text, 0, True
        result, count = text, 0
        result, n = _EMAIL.subn(lambda m: placeholder("EMAIL", m.group(0)), result); count += n
        # Already-redacted/template telephone fragments contain X and stay intact.
        result, n = _PHONE.subn(lambda m: placeholder("PHONE_NUMBER", m.group(0)), result); count += n
        return result, count, True

    if profile == "sole_trader_registry":
        label = None
        if "полное наименование ип" in header or "сокращенное наименование ип" in header:
            prefix = re.match(r"(?i)^\s*(?:индивидуальный\s+предприниматель|ип)\s+", text)
            start = prefix.end() if prefix else 0
            return text[:start] + placeholder("PER", text[start:].strip()), 1, True
        if header == "инн": label = "INN"
        elif header in {"огрн", "огрнип"}: label = "OGRNIP"
        elif "адрес" in header: label = "ADDRESS"
        if label:
            return placeholder(label, text.strip()), 1, True
        if "контакт" in header:
            result, count = text, 0
            result, n = _EMAIL.subn(lambda m: placeholder("EMAIL", m.group(0)), result); count += n
            result, n = _PHONE.subn(lambda m: placeholder("PHONE_NUMBER", m.group(0)), result); count += n
            return result, count, True
        return text, 0, True

    if profile in {"payment_register", "bank_operations"}:
        if "дата" in header:
            result, n = _DATE.subn(lambda m: placeholder("DATE", m.group(0)), text)
            # ISO-looking spreadsheet dates are identifiers too.
            if not n and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:\s+00:00:00)?", text.strip()):
                return placeholder("DATE", text.strip()), 1, True
            return result, n, True
        if "номер" in header:
            return placeholder("DOCUMENT_NUMBER", text.strip()), 1, True
        if "назначение платежа" in header:
            result, count = _DOC_AFTER_NUMBER.subn(
                lambda m: m.group("prefix") + placeholder("DOCUMENT_NUMBER", m.group("value")), text
            )
            result, n = _DATE.subn(lambda m: placeholder("DATE", m.group(0)), result); count += n
            return result, count, True
        if profile == "payment_register" and "контрагент" in header:
            return placeholder("ORG", text.strip()), 1, True
        return text, 0, profile == "payment_register"

    if profile == "bank_statement":
        result, count = text, 0
        if col == 1 and row >= 17 and re.fullmatch(r"\d+", text.strip()):
            return placeholder("DOCUMENT_NUMBER", text.strip()), 1, True
        if col == 2 and row >= 17 and not text.casefold().startswith("контрагент"):
            return placeholder("ORG", text.strip()), 1, True
        if col == 3:
            rules = ((r"(?i)(БИК\s*:\s*)(\d{9})", "BIK"),
                     (r"(?i)(ИНН\s*:\s*)(\d{10,12})", "INN"),
                     (r"(?i)(Р\s*/\s*С\s*:\s*)((?:\d\s*){20})", "RU_ACCOUNT"))
            for pattern, label in rules:
                result, n = re.subn(pattern, lambda m: m.group(1)+placeholder(label,m.group(2)), result)
                count += n
            return result, count, True
        if col == 5:
            result, count = _DOC_AFTER_NUMBER.subn(lambda m: m.group("prefix")+placeholder("DOCUMENT_NUMBER",m.group("value")), result)
            result, n = _DATE.subn(lambda m: placeholder("DATE",m.group(0)), result); count += n
            return result, count, True
        result, n = _DATE.subn(lambda m: placeholder("DATE",m.group(0)), result); count += n
        result, n = _ACCOUNT.subn(lambda m: placeholder("RU_ACCOUNT",m.group(0)), result); count += n
        result, n = re.subn(r'(?i)(ИНН\s*:\s*)(\d{10,12})',lambda m:m.group(1)+placeholder('INN',m.group(2)),result); count += n
        if "клиент:" in result.casefold():
            prefix, value = result.split(":",1)
            result = prefix+': '+placeholder('ORG',value.strip()); count += 1
        return result, count, True

    if profile == "simple_bank_statement":
        if row == 0 and col == 1: return placeholder("RU_ACCOUNT", text.strip()), 1, True
        if row == 1 and col == 1:
            result,n=_DATE.subn(lambda m:placeholder('DATE',m.group(0)),text); return result,n,True
        if row == 2 and col == 1:
            suffix=' (ИП)' if text.strip().endswith('(ИП)') else ''
            core=text.strip()[:-4].strip() if suffix else text.strip()
            return placeholder('PER',core)+suffix,1,True
        if row == 3 and col == 1: return placeholder('INN',text.strip()),1,True
        if row == 4 and col == 1: return placeholder('BIK',text.strip()),1,True
        if row >= 12 and col == 0:
            result,n=_DATE.subn(lambda m:placeholder('DATE',m.group(0)),text); return result,n,True
        if row >= 12 and col == 1: return placeholder('DOCUMENT_NUMBER',text.strip()),1,True
        if row >= 12 and col == 3:
            prefix=re.match(r'(?i)^\s*(?:ООО|ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ|ИНДИВИДУАЛЬНЫЙ\s+ПРЕДПРИНИМАТЕЛЬ)\s+',text)
            if prefix and 'предприниматель' in prefix.group(0).casefold():
                return prefix.group(0)+placeholder('PER',text[prefix.end():].strip()),1,True
            return placeholder('ORG',text.strip()),1,True
        return text,0,True

    if profile == "payment_order":
        positions={(5,17):'BIK',(6,17):'RU_ACCOUNT',(8,0):'INN',(8,8):'KPP',(8,17):'RU_ACCOUNT'}
        if (row,col) in positions:
            return placeholder(positions[(row,col)],text.strip()),1,True
        return text,0,False

    if profile == "supplier_directory":
        if header == "организация":
            return placeholder("ORG", text.strip()), 1, True
        if header == "сайт":
            suffix = "*" if text.endswith("*") else ""
            core = text[:-1] if suffix else text
            return placeholder("WEBSITE", core) + suffix, 1, True
        if header == "телефон":
            if "x" in text.casefold() or "х" in text.casefold():
                return text, 0, True
            return placeholder("PHONE_NUMBER", text.strip()), 1, True
        return text, 0, True

    return text, 0, False


def generic_numeric_fallback(text: str, label: str | None, placeholder):
    """Mask short identifiers whose semantics comes from an adjacent label."""
    key = _norm(label)
    direct = {
        "инн": "INN", "кпп": "KPP", "бик": "BIK", "огрн": "OGRN",
        "огрнип": "OGRNIP", "расчетный счет": "RU_ACCOUNT",
        "лицевой счет": "RU_ACCOUNT", "номер счета": "RU_ACCOUNT",
        "номер карты счета": "RU_ACCOUNT", "номер карты / счета": "RU_ACCOUNT",
        "счет": "RU_ACCOUNT", "банковский счет": "RU_ACCOUNT",
        "номер документа": "DOCUMENT_NUMBER",
    }
    if key in direct and re.fullmatch(r"[\d \u00a0./-]+", text.strip()):
        return placeholder(direct[key], text.strip()), 1
    if re.fullmatch(r"\d{20}", text.strip()) and any(w in key for w in ("счет", "счёт", "карт", "account", "р/с")):
        return placeholder("RU_ACCOUNT", text.strip()), 1
    result, count = _DOC_AFTER_NUMBER.subn(
        lambda m: m.group("prefix") + placeholder("DOCUMENT_NUMBER", m.group("value")), text
    )
    return result, count


def close_workbook(workbook):
    """Release both the workbook reader and openpyxl's separate VBA archive."""
    try:
        workbook.close()
    finally:
        archive = getattr(workbook, "vba_archive", None)
        if archive is not None:
            archive.close()
