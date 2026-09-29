# -*- coding: utf-8 -*-
"""Определение падежа плейсхолдера и локальное склонение сущностей."""
from __future__ import annotations

import re

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:
    _MORPH = None


CASE_TO_GRAMMEME = {
    "NOMINATIVE": "nomn", "GENITIVE": "gent", "DATIVE": "datv",
    "ACCUSATIVE": "accs", "INSTRUMENTAL": "ablt", "PREPOSITIONAL": "loct",
}


class ContextCaseResolver:
    _PATTERNS = [
        (r"(?:согласно|вопреки)\s*$", "DATIVE"),
        (r"(?:по\s+отношению\s+к|обратиться\s+к|иск\s+к|к)\s*$", "DATIVE"),
        (r"(?:передал|передала|направил|направила|вручил|вручила|сообщил|сообщила)\s+[А-Яа-яЁё-]+\s*$", "DATIVE"),
        (r"(?:в\s+отношении|в\s+пользу|со\s+стороны|для|без|из|от|до|у)\s*$", "GENITIVE"),
        (r"(?:в\s+соответствии\s+с|вместе\s+с|заключ(?:ить|ен|ён|ила|ило)\s+с|совместно\s+с|с|со)\s*$", "INSTRUMENTAL"),
        (r"(?:о|об|обо|при)\s*$", "PREPOSITIONAL"),
        (r"(?:обязать|уведомить|назначить|вызвать)\s*$", "ACCUSATIVE"),
    ]

    def resolve(self, text, placeholder_start, fallback="NOMINATIVE"):
        before = text[max(0, placeholder_start - 100):placeholder_start].casefold()
        for pattern, case in self._PATTERNS:
            if re.search(pattern, before):
                return case
        return fallback


class EntityInflector:
    _TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*|[^A-Za-zА-Яа-яЁё]+")

    def inflect(self, record, target_case):
        if not isinstance(record, dict):
            return str(record)
        forms = record.get("forms") or {}
        if target_case in forms:
            return forms[target_case]
        original = record.get("original", "")
        if record.get("language") == "EN" or _MORPH is None:
            return original
        label = record.get("type")
        if label == "PER":
            value = self._inflect_person(original, target_case)
        elif label == "ORG":
            value = self._inflect_organization(original, target_case, record.get("head_word"))
        else:
            value = original
        forms[target_case] = value
        record["forms"] = forms
        return value

    def _inflect_word(self, word, target_case, require_name=False):
        if word.isupper() or len(word) <= 1:
            return word
        result = []
        for part in word.split("-"):
            parses = _MORPH.parse(part)
            if require_name:
                named = [p for p in parses if p.tag.grammemes & {"Name", "Surn", "Patr"}]
                parses = named or parses
            parse = max(parses, key=lambda p: p.score) if parses else None
            inflected = parse.inflect({CASE_TO_GRAMMEME[target_case]}) if parse else None
            value = inflected.word if inflected else part
            result.append(value.capitalize() if part[:1].isupper() else value)
        return "-".join(result)

    def _inflect_person(self, value, target_case):
        return "".join(
            self._inflect_word(token, target_case, True) if token[:1].isalpha() else token
            for token in self._TOKEN_RE.findall(value)
        )

    def _inflect_organization(self, value, target_case, head_word):
        # Бренд внутри кавычек и аббревиатуры остаются неизменными.
        quote_at = min([p for p in (value.find("«"), value.find('"')) if p >= 0] or [len(value)])
        prefix, suffix = value[:quote_at], value[quote_at:]
        tokens = self._TOKEN_RE.findall(prefix)
        head_index = None
        for index, token in enumerate(tokens):
            if not token[:1].isalpha() or token.isupper():
                continue
            parses = _MORPH.parse(token)
            if any(p.tag.POS == "NOUN" and (not head_word or p.normal_form == head_word) for p in parses):
                head_index = index
                break
        if head_index is None:
            return value
        for index in range(head_index + 1):
            token = tokens[index]
            if token[:1].isalpha() and not token.isupper():
                tokens[index] = self._inflect_word(token, target_case)
        return "".join(tokens) + suffix
