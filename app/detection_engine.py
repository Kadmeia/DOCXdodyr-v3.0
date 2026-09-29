# -*- coding: utf-8 -*-
"""Система обнаружения ПДн: EntityCandidate, нормализация со смещениями,
детерминированные правила, валидация контрольных сумм и Conflict Resolver.

Разработано в рамках усиления DOCXdodyr независимыми детекторами по принципам
pii-guard с сохранением Pullenti и правил юридического обезличивания.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from pullenti_legal.checksums import (
    valid_iban,
    valid_inn,
    valid_luhn,
    valid_ogrn,
    valid_snils,
    valid_swift_bic,
)
from validation import is_sufficient_address


@dataclass
class EntityCandidate:
    """Унифицированный объект кандидата сущности от любого детектора."""

    start: int
    end: int
    text: str
    entity_type: str
    source: str  # "pullenti", "rule", "ner", "table_context"
    confidence: float = 1.0
    priority: int = 50
    metadata: Dict[str, Any] = field(default_factory=dict)
    normalized_value: Optional[str] = None
    gender: Optional[str] = None
    grammatical_info: Optional[Dict[str, Any]] = None

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps_with(self, other: EntityCandidate) -> bool:
        """Проверяет наличие любого пересечения интервалов [start, end)."""
        return max(self.start, other.start) < min(self.end, other.end)

    def contains(self, other: EntityCandidate) -> bool:
        """Проверяет, содержит ли текущий кандидат другой полностью."""
        return self.start <= other.start and other.end <= self.end

    def is_exact_match(self, other: EntityCandidate) -> bool:
        """Проверяет совпадение границ интервала."""
        return self.start == other.start and self.end == other.end

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "entity_type": self.entity_type,
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "priority": self.priority,
            "metadata": self.metadata,
            "normalized_value": self.normalized_value,
            "gender": self.gender,
            "grammatical_info": self.grammatical_info,
        }


# =====================================================================
# 1. Нормализация текста с сохранением отображения смещений (Offset Map)
# =====================================================================

def normalize_with_offsets(text: str) -> Tuple[str, List[int]]:
    """Нормализует текст для обнаружения ПДн, сохраняя точное отображение смещений.

    Возвращает (normalized_text, offset_map), где offset_map[i] указывает
    индекс соответствующего символа в исходном тексте.
    Это гарантирует, что координаты найденных сущностей всегда проецируются
    обратно на исходный документ/XML.
    """
    if not text:
        return "", [0]

    normalized_chars: List[str] = []
    offset_map: List[int] = []

    i = 0
    n = len(text)
    in_spaces = False

    while i < n:
        ch = text[i]

        # 1. Мягкие переносы (soft hyphen) и невидимые символы пропускаем
        if ch in ("\xad", "\u200b", "\u200c", "\u200d", "\ufeff"):
            i += 1
            continue

        # 2. Неразрывные и специальные пробелы нормализуем в ' '
        if ch in ("\u00a0", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006",
                  "\u2007", "\u2008", "\u2009", "\u200a", "\u202f", "\u205f", "\u3000", "\t"):
            ch = " "

        # 3. Разновидности тире нормализуем в дефис '-'
        elif ch in ("–", "—", "―", "‒", "−"):
            ch = "-"

        # 4. Разновидности кавычек нормализуем в стандартные
        elif ch in ("«", "»", "“", "”", "„", "‟"):
            ch = '"'
        elif ch in ("‘", "’", "‚", "‛"):
            ch = "'"

        # Схлопывание множественных пробелов
        if ch == " ":
            if in_spaces:
                i += 1
                continue
            in_spaces = True
        else:
            in_spaces = False

        normalized_chars.append(ch)
        offset_map.append(i)
        i += 1

    # Добавляем терминальное смещение для закрывающего диапазона
    offset_map.append(n)
    normalized_text = "".join(normalized_chars)
    return normalized_text, offset_map


def map_span_to_original(
    norm_start: int, norm_end: int, offset_map: List[int], original_len: int
) -> Tuple[int, int]:
    """Проецирует полуинтервал [norm_start, norm_end) из нормализованного текста в исходный."""
    if not offset_map:
        return norm_start, norm_end

    max_idx = len(offset_map) - 1
    safe_start = max(0, min(norm_start, max_idx))
    safe_end = max(0, min(norm_end, max_idx))

    orig_start = offset_map[safe_start]
    orig_end = offset_map[safe_end]

    orig_start = max(0, min(orig_start, original_len))
    orig_end = max(orig_start, min(orig_end, original_len))
    return orig_start, orig_end


# =====================================================================
# 2. Дополнительные валидаторы контрольных сумм и форматов
# =====================================================================

def is_valid_snils(number: str) -> bool:
    """Валидация СНИЛС по официальному алгоритму ПФР (mod 101)."""
    return valid_snils(str(number))


def is_valid_card_luhn(number: str) -> bool:
    """Валидация номера банковской карты по алгоритму Луна (13-19 цифр)."""
    digits = "".join(c for c in str(number) if c.isdigit())
    if len(digits) not in (13, 15, 16, 18, 19):
        return False
    # Отсечение заведомо фиктивных последовательностей
    if digits == "0" * len(digits) or digits == "1" * len(digits):
        return False
    # Проверка диапазона BIN (МИР, VISA, MasterCard, Maestro, Amex, UnionPay)
    first_two = digits[:2]
    first_four = digits[:4]
    if not (
        digits.startswith("4")  # Visa
        or (51 <= int(first_two) <= 55)  # MasterCard
        or (2221 <= int(first_four) <= 2720)  # MasterCard new
        or digits.startswith("220")  # МИР
        or (first_two in ("50", "56", "57", "58") or (60 <= int(first_two) <= 69))  # Maestro/UnionPay
        or (first_two in ("34", "37"))  # Amex
    ):
        return False
    return valid_luhn(digits)


def is_valid_bik(number: str) -> bool:
    """Валидация БИК (9 цифр, первые две '04', код региона РФ 01-99)."""
    digits = "".join(c for c in str(number) if c.isdigit())
    if len(digits) != 9:
        return False
    if not digits.startswith("04"):
        return False
    region = int(digits[2:4])
    return 1 <= region <= 99


def is_valid_oms(number: str) -> bool:
    """Валидация полиса ОМС единого образца (16 цифр, контрольная сумма Луна)."""
    digits = "".join(c for c in str(number) if c.isdigit())
    if len(digits) != 16:
        return False
    return valid_luhn(digits)


_COURT_CASE_ARBITR_RE = re.compile(
    r"\bА(?P<region>\d{1,2})-(?P<num>\d{1,8})/(?P<year>(?:19|20)\d{2}|\d{2})\b",
    re.I,
)
_COURT_CASE_SOJ_RE = re.compile(
    r"\b(?:\d{1,3}-)?(?P<num>\d{1,8})/(?P<year>(?:19|20)\d{2}|\d{2})\b"
)
_COURT_CASE_CAS_RE = re.compile(
    r"\b(?P<subcode>\d{1,2}[а-яёА-ЯЁ]?)-(?P<num>\d{1,8})/(?P<year>(?:19|20)\d{2}|\d{2})\b"
)


def is_valid_court_case(number: str) -> bool:
    """Проверяет формат российского судебного/арбитражного дела."""
    clean = number.strip().replace(" ", "")
    # Арбитражные дела: А40-12345/2025, А56-9999/2024
    if _COURT_CASE_ARBITR_RE.fullmatch(clean):
        return True
    # Суды общей юрисдикции / мировые судьи: 2-1234/2025, 1-45/2024, 05-12/2023
    if _COURT_CASE_CAS_RE.fullmatch(clean):
        return True
    if "/" in clean:
        parts = clean.split("/")
        if len(parts) == 2 and parts[1].isdigit():
            year = int(parts[1])
            if not ((1990 <= year <= 2035) or (0 <= year <= 35)):
                return False
            # Отсекаем чистые числа <= 4 цифр без дефиса (ссылки на ФЗ: 152/2006, 44/2013)
            if parts[0].isdigit() and len(parts[0]) <= 4:
                return False
            if any(c.isdigit() for c in parts[0]) and "-" in clean:
                return True
    return False


def is_valid_passport_rf(series: str, number: str) -> bool:
    """Проверяет серию (4 цифры) и номер (6 цифр) паспорта РФ."""
    s_digits = "".join(c for c in series if c.isdigit())
    n_digits = "".join(c for c in number if c.isdigit())
    if len(s_digits) != 4 or len(n_digits) != 6:
        return False
    region = int(s_digits[:2])
    year = int(s_digits[2:])
    # Регионы РФ: 01-99
    if not (1 <= region <= 99):
        return False
    # Год выпуска бланка: 97..30 (1997-2030)
    if not (97 <= year <= 99 or 0 <= year <= 30):
        return False
    return True


def is_valid_passport_division_code(code: str) -> bool:
    """Проверяет корректность структуры кода подразделения паспорта РФ (XXX-XXX или XXX XXX)."""
    digits = "".join(c for c in str(code) if c.isdigit())
    if len(digits) != 6:
        return False
    # Первые 2 цифры — код региона РФ (01..99). 00 не существует.
    region = int(digits[:2])
    if not (1 <= region <= 99):
        return False
    # Последние 3 цифры — порядковый номер подразделения (нумеруются от 001). 000 не существует!
    dept = int(digits[3:])
    if dept == 0:
        return False
    # Отсечение повторяющихся цифр (000000, 111111 и т.д.)
    if len(set(digits)) == 1:
        return False
    return True


# =====================================================================
# 3. EntityRule и контекстный скоринг с расстоянием и негативными вето
# =====================================================================

@dataclass
class EntityRule:
    """Правило обнаружения с учётом расстояния до ключевых слов и вето-контекстов."""

    name: str
    entity_type: str
    candidate_pattern: re.Pattern
    positive_keywords: Tuple[str, ...] = ()
    negative_keywords: Tuple[str, ...] = ()
    near_window: int = 40
    far_window: int = 140
    priority: int = 70
    base_confidence: float = 0.50
    validator: Optional[Callable[[str], bool]] = None
    keyword_weight: float = 0.40
    negative_weight: float = 0.80
    target_group: Optional[Union[str, int]] = None
    require_positive_keyword: bool = False

    def evaluate(self, text: str) -> List[EntityCandidate]:
        """Применяет правило к тексту и вычисляет confidence на основе контекста."""
        candidates: List[EntityCandidate] = []
        lowered_text = text.casefold()

        for match in self.candidate_pattern.finditer(text):
            if self.target_group is not None:
                try:
                    start = match.start(self.target_group)
                    end = match.end(self.target_group)
                    raw_val = match.group(self.target_group)
                except (IndexError, KeyError):
                    start = match.start()
                    end = match.end()
                    raw_val = match.group(0)
            else:
                start = match.start()
                end = match.end()
                raw_val = match.group(0)

            # Валидация структуры / контрольной суммы
            val_ok = True
            if self.validator is not None:
                try:
                    val_ok = bool(self.validator(raw_val))
                except Exception:
                    val_ok = False
                if not val_ok:
                    continue

            # Окна контекста до и после
            win_start = max(0, start - self.far_window)
            win_end = min(len(text), end + self.far_window)
            ctx_before = lowered_text[win_start:start]
            ctx_after = lowered_text[end:win_end]

            # 1. Проверка отрицательных ключевых слов (Negative Veto)
            is_vetoed = False
            for neg_kw in self.negative_keywords:
                neg_kw_low = neg_kw.casefold()
                # Ищем дистанцию до neg_kw
                pos_before = ctx_before.rfind(neg_kw_low)
                if pos_before != -1:
                    dist = len(ctx_before) - (pos_before + len(neg_kw_low))
                    if dist <= self.near_window:
                        is_vetoed = True
                        break
                pos_after = ctx_after.find(neg_kw_low)
                if pos_after != -1:
                    if pos_after <= self.near_window:
                        is_vetoed = True
                        break

            if is_vetoed:
                continue

            # 2. Расчет положительного контекстного скора по дистанции
            best_pos_score = 0.0
            best_kw = None
            min_dist = 9999

            for pos_kw in self.positive_keywords:
                pos_kw_low = pos_kw.casefold()
                # До кандидата
                p_before = ctx_before.rfind(pos_kw_low)
                if p_before != -1:
                    dist = len(ctx_before) - (p_before + len(pos_kw_low))
                    score = self._calc_distance_score(dist)
                    if score > best_pos_score:
                        best_pos_score = score
                        best_kw = pos_kw
                        min_dist = dist
                # После кандидата
                p_after = ctx_after.find(pos_kw_low)
                if p_after != -1:
                    dist = p_after
                    score = self._calc_distance_score(dist)
                    if score > best_pos_score:
                        best_pos_score = score
                        best_kw = pos_kw
                        min_dist = dist

            # Если правило строго требует наличия контекстных ключевых слов
            if self.require_positive_keyword and best_pos_score <= 0.0:
                continue

            # Итоговый confidence
            conf = self.base_confidence + (best_pos_score * self.keyword_weight)
            # Если валидатор контрольной суммы успешен, даем максимальный бонус
            if self.validator is not None and val_ok:
                conf = max(conf, 0.95 if best_pos_score > 0 else 0.85)

            conf = min(1.0, max(0.0, conf))

            meta = {
                "rule_name": self.name,
                "validator_passed": val_ok,
                "matched_keyword": best_kw,
                "keyword_distance": min_dist if best_kw else None,
                "context_score": round(best_pos_score, 3),
            }

            candidates.append(EntityCandidate(
                start=start,
                end=end,
                text=raw_val,
                entity_type=self.entity_type,
                source="rule",
                confidence=conf,
                priority=self.priority,
                metadata=meta,
                normalized_value=raw_val,
            ))

        return candidates

    def _calc_distance_score(self, distance: int) -> float:
        """Экспоненциально-линейное затухание веса ключевого слова по дистанции."""
        if distance <= self.near_window:
            return 1.0
        if distance >= self.far_window:
            return 0.0
        # Линейный спад от 1.0 до 0.0 между near и far
        return (self.far_window - distance) / float(self.far_window - self.near_window)


# =====================================================================
# 4. Реестр детерминированных правил (Deterministic Rules Registry)
# =====================================================================

def build_deterministic_rules() -> List[EntityRule]:
    """Формирует список строгих детерминированных правил для структурированных ПДн."""
    rules = [
        # --- ИНН (10 и 12 цифр с контрольной суммой и контекстом) ---
        EntityRule(
            name="inn_checksum",
            entity_type="INN",
            candidate_pattern=re.compile(r"(?<!\d)(?:\d{10}|\d{12})(?!\d)"),
            positive_keywords=("инн", "идентификационный номер", "inn", "инн/кпп", "инн/бик", "инн плательщика", "инн получателя"),
            negative_keywords=("счет", "счёт", "оквэд", "октмо", "гост", "код ошибки", "артикул", "заказ"),
            priority=95,
            base_confidence=0.40,
            validator=valid_inn,
        ),
        # --- СНИЛС (11 цифр с алгоритмом mod 101) ---
        EntityRule(
            name="snils_checksum",
            entity_type="SNILS",
            candidate_pattern=re.compile(r"(?<!\d)\d{3}[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{2}(?!\d)"),
            positive_keywords=("снилс", "snils", "страховой номер", "лицевой счет физлица", "страховое свидетельство"),
            negative_keywords=("телефон", "счет", "индекс", "паспорт"),
            priority=95,
            base_confidence=0.45,
            validator=is_valid_snils,
        ),
        # --- ОГРН (13 цифр) и ОГРНИП (15 цифр) ---
        EntityRule(
            name="ogrn_checksum",
            entity_type="OGRN",
            candidate_pattern=re.compile(r"(?<!\d)\d{13}(?!\d)"),
            positive_keywords=("огрн", "ogrn", "егрюл", "основной государственный регистрационный номер"),
            negative_keywords=("счет", "номер карты", "заказ", "штрихкод"),
            priority=95,
            base_confidence=0.50,
            validator=valid_ogrn,
        ),
        EntityRule(
            name="ogrnip_checksum",
            entity_type="OGRNIP",
            candidate_pattern=re.compile(r"(?<!\d)\d{15}(?!\d)"),
            positive_keywords=("огрнип", "ogrnip", "егрип", "индивидуальный предприниматель"),
            negative_keywords=("счет", "номер карты", "заказ"),
            priority=95,
            base_confidence=0.50,
            validator=valid_ogrn,
        ),
        # --- Банковская карта (Luhn + контекст) ---
        EntityRule(
            name="bank_card_luhn",
            entity_type="BANK_CARD",
            candidate_pattern=re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)|(?<!\d)\d{16,19}(?!\d)"),
            positive_keywords=("карта", "карты", "банковская карта", "visa", "mastercard", "мир", "mir", "maestro", "номер карты", "card number", "pan"),
            negative_keywords=("счет", "счёт", "лицевой", "инн", "огрн", "снилс", "телефон"),
            priority=90,
            base_confidence=0.30,
            validator=is_valid_card_luhn,
        ),
        # --- БИК (9 цифр, начинающихся с 04) ---
        EntityRule(
            name="bik_bank",
            entity_type="BIK",
            candidate_pattern=re.compile(r"(?<!\d)04\d{7}(?!\d)"),
            positive_keywords=("бик", "bik", "банк", "к/с", "р/с", "корреспондентский"),
            negative_keywords=("кпп", "инн", "телефон"),
            priority=85,
            base_confidence=0.55,
            validator=is_valid_bik,
        ),
        # --- ОМС (16 цифр единого образца) ---
        EntityRule(
            name="oms_policy",
            entity_type="OMS_POLICY",
            candidate_pattern=re.compile(r"(?<!\d)\d{16}(?!\d)"),
            positive_keywords=("омс", "полис омс", "полис", "страховой полис", "медстрах"),
            negative_keywords=("карта", "счет", "банковская карта"),
            priority=85,
            base_confidence=0.40,
            validator=is_valid_oms,
        ),
        # --- Паспорт РФ: серия (4 цифры) и номер (6 цифр) раздельно ---
        EntityRule(
            name="passport_series_rf",
            entity_type="PASSPORT_SERIES",
            candidate_pattern=re.compile(
                r"(?<!\d)(?P<series>\d{2}\s?\d{2})(?=\s*(?:№|#|номер|n\b)?\s*\d{6}(?!\d))",
                re.I,
            ),
            target_group="series",
            positive_keywords=("паспорт", "серия", "паспорта", "выдан", "кем выдан", "дата выдачи", "паспортные данные", "удостоверение личности"),
            negative_keywords=("паспорт сделки", "паспорт проекта", "паспорт безопасности", "технический паспорт", "паспорт объекта", "паспорт отходов", "техпаспорт"),
            near_window=60,
            far_window=180,
            priority=85,
            base_confidence=0.30,
        ),
        EntityRule(
            name="passport_number_rf",
            entity_type="PASSPORT_NUMBER",
            candidate_pattern=re.compile(
                r"(?<!\d)(?:\d{2}\s?\d{2})(?:[,\s]*(?:№|#|номер|n\b)?\s*)(?P<number>\d{6})(?!\d)",
                re.I,
            ),
            target_group="number",
            positive_keywords=("паспорт", "номер", "паспорта", "выдан", "кем выдан", "дата выдачи", "паспортные данные", "удостоверение личности"),
            negative_keywords=("паспорт сделки", "паспорт проекта", "паспорт безопасности", "технический паспорт", "паспорт объекта", "паспорт отходов", "техпаспорт"),
            near_window=60,
            far_window=180,
            priority=85,
            base_confidence=0.30,
        ),
        # --- Код подразделения паспорта РФ (\d{3}-\d{3}) ---
        EntityRule(
            name="passport_division_code",
            entity_type="PASSPORT_DIVISION_CODE",
            candidate_pattern=re.compile(
                r"(?<!\d[\s\u00a0.,])(?<!\d)\d{3}[-\s]\d{3}(?!\d)(?![,\.]\d{2})(?![\s\u00a0]\d{3})"
            ),
            positive_keywords=(
                "код подразделения", "код подр.", "код подр", "подразделение",
                "подразделения", "подразделением", "оуфмс", "уфмс", "мвд",
                "овд", "гувд", "рофмс", "тп", "паспорт", "паспорта", "выдан", "выдавший"
            ),
            negative_keywords=(
                "руб", "руб.", "рубля", "рублей", "коп", "коп.", "копеек", "копейки", "₽", "$", "€",
                "стоимость", "цена", "цене", "сумме", "сумма", "сумму", "суммой",
                "размер", "размере", "расчет", "расчету", "расчёт", "остаток", "остатка",
                "задолженность", "задолженности", "пошлина", "пошлины", "пошлину",
                "оплата", "оплате", "оплату", "выплата", "выплате", "платеж", "платежа",
                "предел", "превышает", "тысяч", "тысячи", "тыс", "тыс.", "млн", "млрд",
                "долг", "долга", "акт", "актом", "счет", "счету", "счёт", "счёту",
                "спецификация", "спецификации", "договор", "договору", "договора",
                "телефон", "тел.", "факс", "индекс", "заказ", "артикул", "версия", "статья", "пункт"
            ),
            near_window=60,
            far_window=140,
            priority=85,
            base_confidence=0.30,
            require_positive_keyword=True,
            validator=is_valid_passport_division_code,
        ),
        # --- Номера судебных дел РФ (А40-..., 2-.../2025) ---
        EntityRule(
            name="court_case_number",
            entity_type="COURT_CASE_NUMBER",
            candidate_pattern=re.compile(r"\bА\d{1,2}-\d{1,8}/\d{2,4}\b|\b(?:\d{1,3}-)?\d{1,8}/\d{2,4}\b|\b\d{1,2}[а-яёА-ЯЁ]?-\d{1,8}/\d{2,4}\b"),
            positive_keywords=("дело", "делу", "арбитражный суд", "суд", "иск", "исковое заявление", "определение", "постановление", "решение суда", "производство"),
            negative_keywords=("фз", "гост", "договор", "закон", "снип", "статья"),
            priority=80,
            base_confidence=0.45,
            validator=is_valid_court_case,
        ),
        # --- IP-адрес v4 ---
        EntityRule(
            name="ip_address",
            entity_type="IP_ADDRESS",
            candidate_pattern=re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b"),
            positive_keywords=("ip", "ip-адрес", "хост", "сеть", "адрес", "сервер", "шлюз"),
            negative_keywords=("пункт", "п.", "ст.", "версия"),
            priority=80,
            base_confidence=0.50,
        ),
        # --- Международный номер банковского счёта (IBAN) ---
        EntityRule(
            name="iban_international",
            entity_type="IBAN",
            candidate_pattern=re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
            positive_keywords=("iban", "счёт", "банк", "перевод", "swift"),
            negative_keywords=("паспорт", "снилс"),
            priority=85,
            base_confidence=0.40,
            validator=valid_iban,
        ),
        # --- Банковский идентификационный код SWIFT BIC ---
        EntityRule(
            name="swift_bic",
            entity_type="SWIFT",
            candidate_pattern=re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"),
            positive_keywords=("swift", "bic", "банк", "корреспондент", "перевод"),
            negative_keywords=("паспорт", "инн", "огрн"),
            priority=85,
            base_confidence=0.40,
            validator=valid_swift_bic,
        ),
    ]
    return rules


# =====================================================================
# 5. Продвинутый Conflict Resolver
# =====================================================================

class ConflictResolver:
    """Интеллектуальный арбитр конфликтов между несколькими детекторами
    (Pullenti, детерминированные правила, NER-модель, контекст).
    """

    def __init__(self, rules: Optional[List[EntityRule]] = None):
        self.rules = rules or build_deterministic_rules()

    def resolve(self, candidates: Sequence[EntityCandidate]) -> List[EntityCandidate]:
        """Разрешает наложения, вложенности и конфликты кандидатов."""
        if not candidates:
            return []

        # 1. Шаг 1: Объединение идентичных спанов (Exact duplicate merging)
        grouped_by_span: Dict[Tuple[int, int], List[EntityCandidate]] = {}
        for c in candidates:
            key = (c.start, c.end)
            grouped_by_span.setdefault(key, []).append(c)

        merged_exact: List[EntityCandidate] = []
        for (start, end), span_candidates in grouped_by_span.items():
            if len(span_candidates) == 1:
                merged_exact.append(span_candidates[0])
            else:
                merged = self._merge_exact_span(span_candidates)
                merged_exact.append(merged)

        # 2. Шаг 2: Сортировка по приоритету и уверенности
        # Высший приоритет: валидные контрольные суммы (priority >= 90), затем Pullenti PER/ORG, затем длина
        sorted_candidates = sorted(
            merged_exact,
            key=lambda c: (
                -c.priority,
                -round(c.confidence, 2),
                -c.length,
                c.start,
            )
        )

        # 3. Шаг 3: Разрешение вложенностей и частичных пересечений
        # Проверяем ВСЕ пересечения перед принятием решения (fix C-01).
        accepted: List[EntityCandidate] = []

        for candidate in sorted_candidates:
            dominated = False
            to_remove: List[EntityCandidate] = []

            for existing in list(accepted):
                if not candidate.overlaps_with(existing):
                    continue

                # Анализ вложенности:
                if existing.contains(candidate):
                    # existing полностью покрывает candidate.
                    # ИСКЛЮЧЕНИЕ: если candidate — это структурированный идентификатор с валидной контрольной суммой
                    # (например, ИНН, СНИЛС, БИК) внутри широкого ORG или ADDRESS, сохраняем оба.
                    if candidate.priority >= 90 and existing.priority < 90:
                        to_remove.append(existing)
                    else:
                        # shorter спан поглощается более широким
                        dominated = True
                        break

                elif candidate.contains(existing):
                    # candidate шире existing
                    if existing.priority >= 90 and candidate.priority < 90:
                        # Не позволяем широкому спану поглотить валидированный реквизит
                        dominated = True
                        break
                    # Иначе более приоритетный кандидат candidate поглощает existing
                    to_remove.append(existing)

                else:
                    # Частичное пересечение: выбирается тот, у кого выше (priority * confidence)
                    cand_score = candidate.priority * candidate.confidence
                    exist_score = existing.priority * existing.confidence
                    if cand_score > exist_score:
                        to_remove.append(existing)
                    else:
                        dominated = True
                        break

            if not dominated:
                for item in to_remove:
                    accepted.remove(item)
                accepted.append(candidate)

        # Сортируем итоговые сущности по координатам в тексте
        return sorted(accepted, key=lambda c: (c.start, c.end))

    def _merge_exact_span(self, candidates: List[EntityCandidate]) -> EntityCandidate:
        """Объединяет кандидатов с абсолютно одинаковыми границами [start, end)."""
        # Сбор всех источников
        sources = list(dict.fromkeys(c.source for c in candidates))
        combined_source = "+".join(sources)

        # Байесовское объединение уверенности: 1 - prod(1 - c_i)
        unconfidence = 1.0
        for c in candidates:
            unconfidence *= (1.0 - min(0.99, max(0.01, c.confidence)))
        combined_conf = min(1.0, max(0.0, 1.0 - unconfidence))

        # Выбираем наиболее приоритетный тип
        best = max(candidates, key=lambda c: (c.priority, c.confidence))

        meta = {"merged_sources": sources}
        for c in candidates:
            meta.update(c.metadata)

        return EntityCandidate(
            start=best.start,
            end=best.end,
            text=best.text,
            entity_type=best.entity_type,
            source=combined_source,
            confidence=combined_conf,
            priority=max(c.priority for c in candidates),
            metadata=meta,
            normalized_value=best.normalized_value,
            gender=best.gender,
            grammatical_info=best.grammatical_info,
        )


# =====================================================================
# 6. Комплексный конвейер обнаружения (DetectionPipeline)
# =====================================================================

class UnifiedDetectionPipeline:
    """Единый конвейер обнаружения: нормализация -> Rules -> Pullenti -> NER -> ConflictResolver."""

    def __init__(
        self,
        enable_pullenti: bool = True,
        enable_rules: bool = True,
    ):
        self.enable_pullenti = enable_pullenti
        self.enable_rules = enable_rules
        self.rules = build_deterministic_rules()
        self.resolver = ConflictResolver(self.rules)

    def detect_candidates(
        self, text: str, pullenti_spans: Optional[Iterable[Any]] = None
    ) -> List[EntityCandidate]:
        """Запускает детекторы (Pullenti + детерминированные правила) и возвращает непротиворечивый список кандидатов."""
        if not text:
            return []

        all_candidates: List[EntityCandidate] = []

        # 1. Запуск детерминированных правил на нормализованном тексте
        if self.enable_rules:
            # На исходном тексте
            for rule in self.rules:
                all_candidates.extend(rule.evaluate(text))

            # На нормализованном тексте с проекцией смещений
            norm_text, offset_map = normalize_with_offsets(text)
            if norm_text != text:
                for rule in self.rules:
                    norm_candidates = rule.evaluate(norm_text)
                    for nc in norm_candidates:
                        orig_start, orig_end = map_span_to_original(
                            nc.start, nc.end, offset_map, len(text)
                        )
                        orig_text = text[orig_start:orig_end]
                        if orig_text.strip():
                            all_candidates.append(EntityCandidate(
                                start=orig_start,
                                end=orig_end,
                                text=orig_text,
                                entity_type=nc.entity_type,
                                source="rule_norm",
                                confidence=nc.confidence * 0.98,
                                priority=nc.priority,
                                metadata=nc.metadata,
                                normalized_value=nc.text,
                            ))

            # Дедупликация: если rule и rule_norm кандидат имеют одинаковые
            # (start, end, entity_type), оставляем только один (с бо́льшим confidence)
            seen_spans: Dict[Tuple[int, int, str], EntityCandidate] = {}
            for c in all_candidates:
                key = (c.start, c.end, c.entity_type)
                existing = seen_spans.get(key)
                if existing is None or c.confidence > existing.confidence:
                    seen_spans[key] = c
            all_candidates = list(seen_spans.values())

        # 2. Интеграция кандидатов Pullenti
        if self.enable_pullenti and pullenti_spans is not None:
            for span in pullenti_spans:
                p_text = getattr(span, "text", "") or text[span.start:span.end]
                p_label = getattr(span, "label", "PER")
                # Приоритет Pullenti: PER=80, ORG=75, ADDRESS=70, контакты=75
                priority = 80 if p_label == "PER" else (75 if p_label == "ORG" else 70)
                all_candidates.append(EntityCandidate(
                    start=span.start,
                    end=span.end,
                    text=p_text,
                    entity_type=p_label,
                    source="pullenti",
                    confidence=0.92,
                    priority=priority,
                    metadata={"pullenti_label": p_label},
                ))

        # 3. Разрешение конфликтов
        return self.resolver.resolve(all_candidates)
