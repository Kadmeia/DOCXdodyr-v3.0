# -*- coding: utf-8 -*-
"""Стабильные записи сущностей и связывание падежных форм."""

from dataclasses import asdict, dataclass, field
import re

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:
    _MORPH = None


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")
_GRAMMAR_CASES = {
    "nomn": "NOMINATIVE", "gent": "GENITIVE", "datv": "DATIVE",
    "accs": "ACCUSATIVE", "ablt": "INSTRUMENTAL", "loct": "PREPOSITIONAL",
}


@dataclass
class EntityRecord:
    id: str
    type: str
    original: str
    normalized: str
    language: str = "RU"
    case: str = "NOMINATIVE"
    gender: str = "UNKNOWN"
    number: str = "SINGULAR"
    head_word: str = ""
    forms: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _best_parse(word, label):
    if _MORPH is None:
        return None
    parses = _MORPH.parse(word)
    if label == "PER":
        named = [p for p in parses if p.tag.grammemes & {"Name", "Surn", "Patr"}]
        if named:
            return max(named, key=lambda p: p.score)
    return max(parses, key=lambda p: p.score) if parses else None


def grammar_profile(surface, label):
    if label not in ("PER", "ORG", "FOREIGN_PER", "FOREIGN_ORG"):
        return {"language": "OTHER", "case": "NOMINATIVE", "gender": "UNKNOWN", "number": "SINGULAR", "head_word": ""}
    if label in ("FOREIGN_PER", "FOREIGN_ORG") or not re.search(r"[А-Яа-яЁё]", surface or ""):
        return {"language": "EN", "case": "NOMINATIVE", "gender": "UNKNOWN", "number": "SINGULAR", "head_word": ""}
    words = _WORD_RE.findall(surface)
    parses = [p for w in words if (p := _best_parse(w, label)) is not None]
    head = None
    if label == "ORG":
        head = next((p for p in parses if p.tag.POS == "NOUN"), None)
    elif label == "PER":
        head = next((p for p in parses if p.tag.grammemes & {"Surn", "Name"}), None)
    if head is None and parses:
        head = parses[0]
    tag = getattr(head, "tag", None)
    return {
        "language": "RU",
        "case": _GRAMMAR_CASES.get(getattr(tag, "case", None), "NOMINATIVE"),
        "gender": {"masc": "MASCULINE", "femn": "FEMININE", "neut": "NEUTER"}.get(getattr(tag, "gender", None), "UNKNOWN"),
        "number": "PLURAL" if getattr(tag, "number", None) == "plur" else "SINGULAR",
        "head_word": getattr(head, "normal_form", "") if head else "",
    }


def canonical_key(surface, label):
    """Нормализует форму без словарей имён и организаций приложения."""
    compact = re.sub(r"\s+", " ", (surface or "").strip())
    if label in (
        "INN", "OGRN", "OGRNIP", "KPP", "BIK", "SNILS",
        "RU_ACCOUNT", "RU_CORR_ACCOUNT", "PERSONAL_ACCOUNT",
        "PASSPORT", "PASSPORT_NUMBER", "PASSPORT_SERIES", "PASSPORT_DIVISION_CODE",
    ):
        digits = "".join(c for c in compact if c.isdigit())
        if digits:
            return label, digits
    if label == "FOREIGN_PER":
        words = [w.casefold() for w in _WORD_RE.findall(compact)]
        words.sort()
        return label, " ".join(words)
    if label == "FOREIGN_ORG":
        return label, compact.casefold()
    if label not in ("PER", "ORG") or _MORPH is None:
        return label, compact.casefold()
    words = []
    for match in _WORD_RE.finditer(compact):
        word = match.group(0)
        if word.isupper() or len(word) == 1:
            words.append(word.casefold())
            continue
        parts = []
        for part in word.split("-"):
            parse = _best_parse(part, label)
            parts.append(parse.normal_form if parse else part.casefold())
        words.append("-".join(parts))
    # ФИО разных порядков связываются по набору нормальных форм.
    if label == "PER":
        words.sort()
    return label, " ".join(words)


def _person_signature(surface):
    """Return a conservative surname+initials alias for cross-document FIO.

    It links ``Иванов Иван Иванович`` with ``Иванов И. И.`` and their case
    forms.  A surname with only one initial is deliberately not linked: two
    people with the same surname and first initial are common in legal files.
    """
    if _MORPH is None:
        return None
    words = _WORD_RE.findall(surface or "")
    if len(words) < 3:
        return None
    surname = name = patronymic = None
    initials = []
    lexical = []
    for word in words:
        if len(word) == 1:
            initials.append(word.casefold())
            continue
        lexical.append(word)
        parse = _best_parse(word, "PER")
        grammemes = getattr(getattr(parse, "tag", None), "grammemes", set())
        normal = getattr(parse, "normal_form", word.casefold())
        if "Surn" in grammemes and surname is None:
            surname = normal
        elif "Patr" in grammemes and patronymic is None:
            patronymic = normal
        elif "Name" in grammemes and name is None:
            name = normal
    if len(initials) >= 2 and len(lexical) == 1:
        # In ``И.И.Иванов`` / ``Иванов И.И.`` the two initials are stronger
        # evidence than an ambiguous dictionary parse of the surname.  Prefer
        # a surname parse when present, otherwise retain the lexical form.
        surname_parses = [
            parse for parse in _MORPH.parse(lexical[0])
            if "Surn" in parse.tag.grammemes
        ]
        surname = (
            max(surname_parses, key=lambda parse: parse.score).normal_form
            if surname_parses else lexical[0].casefold()
        )
        return "PER_SIGNATURE", surname, initials[0][0], initials[1][0]
    if len(lexical) == 3:
        # Resolve the common Surname–Name–Patronymic structure using all
        # morphological alternatives.  Choosing only the highest-scored parse
        # misclassifies ambiguous surnames such as ``Иванов`` as a name.
        analyses = [
            [parse for parse in _MORPH.parse(word) if parse.tag.grammemes & {"Name", "Surn", "Patr"}]
            for word in lexical
        ]
        patr_index = next((i for i, parses in enumerate(analyses) if any("Patr" in p.tag.grammemes for p in parses)), None)
        name_candidates = [
            i for i, parses in enumerate(analyses)
            if i != patr_index and any("Name" in p.tag.grammemes for p in parses)
        ]
        name_index = min(
            name_candidates,
            key=lambda i: any("Surn" in p.tag.grammemes for p in analyses[i]),
        ) if name_candidates else None
        surname_index = next((
            i for i in range(3) if i not in {name_index, patr_index}
        ), None)
        if name_index is not None and patr_index is not None and surname_index is not None:
            surname_parses = [p for p in analyses[surname_index] if "Surn" in p.tag.grammemes]
            name_parses = [p for p in analyses[name_index] if "Name" in p.tag.grammemes]
            patronymic_parses = [p for p in analyses[patr_index] if "Patr" in p.tag.grammemes]
            surname = (
                max(surname_parses, key=lambda p: p.score).normal_form
                if surname_parses else lexical[surname_index].casefold()
            )
            name = max(name_parses, key=lambda p: p.score).normal_form
            patronymic = max(patronymic_parses, key=lambda p: p.score).normal_form
    if surname is None:
        return None
    if len(initials) >= 2:
        return "PER_SIGNATURE", surname, initials[0][0], initials[1][0]
    if name and patronymic:
        return "PER_SIGNATURE", surname, name[0], patronymic[0]
    return None


def canonical_keys(surface, label):
    """All safe aliases under which one entity can be found."""
    keys = [canonical_key(surface, label)]
    if label == "PER":
        signature = _person_signature(surface)
        if signature is not None:
            keys.append(signature)
    return tuple(keys)


def entity_seen_from_mapping(mapping_dict):
    """Rebuild the alias registry stored only in memory during a batch.

    A decoder deliberately stores the original value and its observed forms,
    not Python tuple keys.  Continuing a folder run therefore has to recreate
    those keys before the first new document is processed.
    """
    seen = {}
    wrappers = {
        (placeholder[0], placeholder[-1])
        for placeholder in (mapping_dict or {})
        if len(placeholder) >= 2 and placeholder[0] in "[/" and placeholder[-1] in "]/"
    }
    if len(wrappers) > 1:
        raise ValueError("Дешифратор содержит несовместимые форматы плейсхолдеров")
    for placeholder, record in (mapping_dict or {}).items():
        if not isinstance(record, dict):
            raise ValueError(
                f"Плейсхолдер {placeholder} создан старой версией и не содержит тип сущности"
            )
        label = record.get("type")
        original = record.get("original")
        if not isinstance(label, str) or not label or not isinstance(original, str) or not original:
            raise ValueError(f"Неполная запись дешифратора для {placeholder}")
        surfaces = [original]
        forms = record.get("forms", {})
        if isinstance(forms, dict):
            surfaces.extend(value for value in forms.values() if isinstance(value, str) and value)
        keys = []
        normalized = record.get("normalized")
        if isinstance(normalized, str) and normalized:
            keys.append((label, normalized))
        for surface in surfaces:
            keys.extend(canonical_keys(surface, label))
        for key in keys:
            previous = seen.get(key)
            if previous is not None and previous != placeholder:
                raise ValueError(
                    f"Дешифратор неоднозначен: {previous} и {placeholder} описывают одну сущность"
                )
            seen[key] = placeholder
    return seen


class EntityResolver:
    def __init__(self, entity_seen=None, mapping_dict=None):
        self.entity_seen = entity_seen if entity_seen is not None else {}
        self.mapping_dict = mapping_dict if mapping_dict is not None else {}

    def resolve(self, surface, label, base_name):
        keys = canonical_keys(surface, label)
        key = keys[0]
        placeholder = next(
            (self.entity_seen[item] for item in keys if item in self.entity_seen),
            None,
        )
        profile = grammar_profile(surface, label)
        if placeholder is None:
            existing = {
                value for existing_key, value in self.entity_seen.items()
                if existing_key and existing_key[0] in {label, f"{label}_SIGNATURE"}
            }
            existing.update(
                token for token, record in self.mapping_dict.items()
                if isinstance(record, dict) and record.get("type") == label
            )
            indices = []
            for token in existing:
                match = re.search(r"_(\d+)(?:\]|/)$", token)
                if match:
                    indices.append(int(match.group(1)))
            index = max(indices, default=0) + 1
            if len(base_name) >= 2 and base_name[0] in "[/" and base_name[-1] in "]/":
                left, right, name = base_name[0], base_name[-1], base_name[1:-1]
            else:
                left, right, name = "[", "]", base_name
            # A continued batch inherits the previous decoder's wrapper even
            # if the UI setting has since changed.  Mixed wrappers would make
            # a single folder set needlessly inconsistent.
            wrappers = {
                (token[0], token[-1])
                for token in self.mapping_dict
                if len(token) >= 2 and token[0] in "[/" and token[-1] in "]/"
            }
            if len(wrappers) == 1:
                left, right = next(iter(wrappers))
            placeholder = f"{left}{name}_{index}{right}"
            while placeholder in self.mapping_dict:
                index += 1
                placeholder = f"{left}{name}_{index}{right}"
            record = EntityRecord(
                id=f"{label}_{index:06d}", type=label, original=surface,
                normalized=key[1], forms={profile["case"]: surface}, **profile,
            )
            self.mapping_dict[placeholder] = record.to_dict()
        else:
            record = self.mapping_dict.get(placeholder)
            if isinstance(record, dict):
                record.setdefault("forms", {})[profile["case"]] = surface
        for alias in keys:
            self.entity_seen[alias] = placeholder
        return placeholder
