# -*- coding: utf-8 -*-
"""Semantic context for values stored in document tables.

Pullenti normally sees a table cell in isolation.  In legal documents the
meaning of a short value is often carried by a neighbouring cell (``ИНН`` ->
``770...`` or ``ФИО`` -> ``Иванов И.И.``).  This module turns a rectangular
table into value-cell contexts without changing the source table.  Callers
send :attr:`TableCellContext.analysis_text` to Pullenti and apply the returned
spans only to :attr:`TableCellContext.value_text`.

The implementation deliberately keeps the label vocabulary small and
domain-oriented.  It is not an anonymisation rule list: labels only provide
semantic context; Pullenti remains the source of entity spans.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List, Optional, Sequence, Tuple


# A label may contain punctuation, a line break, or a short explanatory text.
# Long cells are not labels: this avoids treating a paragraph in a table as a
# field name and accidentally providing misleading context to Pullenti.
_LABEL_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("ФИО", re.compile(r"\b(?:ф\W*и\W*о|фамилия\s+имя(?:\s+отчество)?)\b", re.I)),
    ("Дата рождения", re.compile(r"\b(?:дата\s+рождения|родился?\s*[:№-]?)\b", re.I)),
    ("Место рождения", re.compile(r"\bместо\s+рождения\b", re.I)),
    ("Адрес", re.compile(r"\b(?:адрес|место\s+жительства|место\s+пребывания)\b", re.I)),
    ("Телефон", re.compile(r"\b(?:тел\.?|телефон|мобил(?:ьный|ьн)?\s+телефон)\b", re.I)),
    ("Электронная почта", re.compile(r"\b(?:e-?mail|электронн(?:ая|ой)\s+почт(?:а|ы))\b", re.I)),
    ("Наименование", re.compile(r"\b(?:наименование|название)\b", re.I)),
    ("ИНН", re.compile(r"\bинн\b", re.I)),
    ("КПП", re.compile(r"\bкпп\b", re.I)),
    ("ОГРНИП", re.compile(r"\bогрнип\b", re.I)),
    ("ОГРН", re.compile(r"\bогрн\b", re.I)),
    ("БИК", re.compile(r"\bбик\b", re.I)),
    ("СНИЛС", re.compile(r"\bснилс\b", re.I)),
    ("Паспорт", re.compile(r"\b(?:паспорт|серия\s+и\s+номер\s+паспорта)\b", re.I)),
    ("Кадастровый номер", re.compile(r"\bкадастров(?:ый|ого)\s+номер(?:а)?\b", re.I)),
    ("Свидетельство о рождении", re.compile(r"\bсвидетельств(?:о|а)\s+о\s+рождении\b", re.I)),
    ("Свидетельство о заключении брака", re.compile(r"\bсвидетельств(?:о|а)\s+о\s+(?:заключении\s+брака|браке)\b", re.I)),
    ("Запись акта гражданского состояния", re.compile(r"\b(?:номер\s+)?записи\s+акта(?:\s+гражданского\s+состояния)?\b", re.I)),
    ("Удостоверение адвоката", re.compile(r"\b(?:удостоверение|реестр)\s+адвокат(?:а|ов)\b", re.I)),
    ("Реестровый номер", re.compile(r"\b(?:реестров(?:ый|ого)\s+номер(?:а)?(?:\s+(?:в\s+)?ефрт|\s+туроператора)?|номер\s+в\s+ефрт|ефрт)\b", re.I)),
    ("Номер судебного дела", re.compile(r"\b(?:номер\s+(?:судебного\s+)?дела|дело\s*№|гражданское\s+дело)\b", re.I)),
    ("Номер договора", re.compile(r"\b(?:номер\s+договора|договор\s*№)\b", re.I)),
    ("Номер патента", re.compile(r"\b(?:номер\s+патента|патент)\b", re.I)),
    ("Лицевой счёт", re.compile(r"\bлицев(?:ой|ого)\s+сч[её]т\b", re.I)),
    ("Расчётный счёт", re.compile(r"\b(?:(?:номер\s+)?(?:расч[её]тн(?:ый|ого|ому|ым|ом)|банковск(?:ий|ого|ому|им|ом)|текущ(?:ий|его))\s+сч[её]т(?:а|у|ом|е)?|(?:номер\s+)?(?:карт(?:ы)?(?:\s*[/\\|]\s*)?)?сч[её]т(?:а|у|ом|е)?(?:\s*[/\\|]\s*карт(?:ы)?)?)\b", re.I)),
    ("Корреспондентский счёт", re.compile(r"\bкорреспондентск(?:ий|ого|ому|им|ом)\s+сч[её]т(?:а|у|ом|е)?\b", re.I)),
    ("VIN", re.compile(r"\b(?:vin|идентификационный\s+номер\s+транспортного\s+средства)\b", re.I)),
    ("Государственный регистрационный знак", re.compile(r"\b(?:государственный\s+(?:регистрационный\s+знак|номер)|номер\s+автомобиля|госномер)\b", re.I)),
    ("Заграничный паспорт", re.compile(r"\b(?:заграничный\s+паспорт|загранпаспорт)\b", re.I)),
    ("Водительское удостоверение", re.compile(r"\bводительское\s+удостоверение\b", re.I)),
    ("Военный билет", re.compile(r"\bвоенный\s+билет\b", re.I)),
    ("Вид на жительство", re.compile(r"\b(?:вид\s+на\s+жительство|внж)\b", re.I)),
    ("Миграционная карта", re.compile(r"\bмиграционная\s+карта\b", re.I)),
    ("Номер визы", re.compile(r"\b(?:номер\s+)?визы\b", re.I)),
    ("Разрешение на работу", re.compile(r"\bразрешение\s+на\s+работу\b", re.I)),
    ("Номер ПТС", re.compile(r"\b(?:номер\s+)?птс\b", re.I)),
    ("Номер СТС", re.compile(r"\b(?:номер\s+)?стс\b", re.I)),
    ("Номер исполнительного производства", re.compile(r"\b(?:номер\s+)?исполнительного\s+производства\b", re.I)),
    ("Номер доверенности", re.compile(r"\b(?:номер\s+)?доверенности\b", re.I)),
    ("Полис ОМС", re.compile(r"\b(?:полис\s+)?омс\b", re.I)),
    ("Полис ДМС", re.compile(r"\b(?:полис\s+)?дмс\b", re.I)),
    ("Номер медицинской карты", re.compile(r"\b(?:номер\s+)?медицинской\s+карты\b", re.I)),
    ("Диагноз", re.compile(r"\bдиагноз\b", re.I)),
    ("Номер банковской карты", re.compile(r"\b(?:номер\s+)?банковской\s+карты\b", re.I)),
    ("IP-адрес", re.compile(r"\bip[- ]?адрес\b", re.I)),
    ("MAC-адрес", re.compile(r"\bmac[- ]?адрес\b", re.I)),
    ("Логин", re.compile(r"\b(?:логин|уч[её]тная\s+запись)\b", re.I)),
    ("Дата рождения", re.compile(r"\bдата\s+рождения\b", re.I)),
    ("Место рождения", re.compile(r"\bместо\s+рождения\b", re.I)),
    ("Гражданство", re.compile(r"\bгражданство\b", re.I)),
    ("Должность", re.compile(r"\bдолжность\b", re.I)),
    ("Место работы", re.compile(r"\b(?:место\s+работы|работодатель)\b", re.I)),
    ("Образование", re.compile(r"\bобразование\b", re.I)),
    ("Размер дохода", re.compile(r"\b(?:размер\s+дохода|доход)\b", re.I)),
    ("Сведения о судимости", re.compile(r"\b(?:сведения\s+о\s+судимости|судимость)\b", re.I)),
    ("Национальность", re.compile(r"\bнациональность\b", re.I)),
    ("Вероисповедание", re.compile(r"\b(?:вероисповедание|религия)\b", re.I)),
    ("Политические взгляды", re.compile(r"\bполитические\s+взгляды\b", re.I)),
    ("Биометрические данные", re.compile(r"\bбиометрические\s+данные\b", re.I)),
    # Neutral structural header. It prevents a neighbouring sensitive header
    # (for example ``Ф.И.О``) from leaking into a clothing-size column.
    ("Размер", re.compile(r"\bразмер(?:ы)?\b", re.I)),
)


@dataclass(frozen=True)
class TableCellContext:
    """Coordinates and semantic context for one table cell."""

    row: int
    column: int
    value_text: str
    label: Optional[str] = None
    label_row: Optional[int] = None
    label_column: Optional[int] = None

    @property
    def has_context(self) -> bool:
        return bool(self.label and self.value_text.strip())

    @property
    def analysis_text(self) -> str:
        """Text passed to Pullenti, including context but not used for output."""

        if not self.has_context:
            return self.value_text
        # The legal Pullenti cartridge consumes the same representation that
        # occurs in flattened forms: ``field-label<TAB>value``.  The prefix is
        # analysis-only and coordinates are projected back to the cell.
        return f"{self.label}\t{self.value_text}"

    @property
    def value_offset(self) -> int:
        if not self.has_context:
            return 0
        return len(f"{self.label}\t")


def infer_field_label(text: object) -> Optional[str]:
    """Return a canonical semantic label when *text* looks like a field label."""

    if not isinstance(text, str):
        return None
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > 100:
        return None
    if re.search(r"\bинн\b", normalized, re.I) and re.search(
        r"\bкпп\b", normalized, re.I
    ):
        return "ИНН/КПП"
    # A label cell can carry ``ИНН:`` or ``ФИО (полностью)``.  If a large
    # amount of value text follows the label, it is a value cell instead.
    for canonical, pattern in _LABEL_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        tail = normalized[match.end():].strip(" \t:№-–—()[]")
        if len(tail) <= 36:
            return canonical
    return None


def is_pure_field_label(text: object) -> bool:
    """Return true only when a cell/paragraph is structural field text.

    ``infer_field_label`` deliberately accepts short inline tails so labels
    such as ``Наименование Банка полное`` can provide table context.  That is
    too permissive for deciding whether a paragraph may be skipped: ``ИНН
    770...`` and ``БИК 044...`` are values and must reach the analyzer.
    """

    if not isinstance(text, str):
        return False
    normalized = " ".join(text.split())
    if not normalized or "\n" in text or len(normalized) > 100:
        return False
    if re.fullmatch(
        r"ИНН\s*[/\\|]\s*КПП(?:\s+(?:покупателя|продавца|поставщика|"
        r"исполнителя|заказчика|клиента|банка|организации|предприятия|"
        r"плательщика|получателя))?\s*:?",
        normalized,
        re.I,
    ):
        return True
    for _canonical, pattern in _LABEL_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        before = normalized[:match.start()].strip(" \t:№-–—()[]")
        tail = normalized[match.end():].strip(" \t:№-–—()[]")
        if before:
            continue
        if re.search(r"\d|@|https?://|www\.|[«\"']", tail, re.I):
            return False
        if not tail:
            return True
        qualifiers = {
            "полностью", "полное", "полная", "полный", "сокращенное", "сокращённое",
            "банка", "банковские", "организации", "юридического лица", "сотрудника",
            "офиса", "регистрации", "фактический", "юридический", "почтовый",
            "работника", "правообладателя", "получателя", "плательщика",
        }
        return tail.casefold() in qualifiers
    return False


def _nonempty(value: object) -> bool:
    if value is None:
        return False
    return bool(str(value).strip())


def _candidate_labels(rows: Sequence[Sequence[object]]):
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            label = infer_field_label(cell)
            if label and is_pure_field_label(cell):
                yield row_index, col_index, label


def _distance(row: int, col: int, label_row: int, label_col: int) -> Tuple[int, int, int]:
    # Prefer same-row labels (horizontal forms), then same-column labels
    # (vertical forms), then the closest remaining cell.  The final tie-break
    # is deterministic and keeps merged/malformed tables safe.
    same_row = 0 if row == label_row else 1
    same_col = 0 if col == label_col else 1
    return same_row + same_col, abs(row - label_row) + abs(col - label_col), label_col


def build_table_contexts(rows: Sequence[Sequence[object]], *, max_label_distance: int = 2) -> List[TableCellContext]:
    """Build contexts for all non-empty cells in a rectangular table or spreadsheet.

    Labels are associated with the nearest non-empty value to their right or
    below.  In columnar tables and spreadsheets, a column header in the top
    rows is also propagated down the entire column as semantic context.
    """

    labels = list(_candidate_labels(rows))

    # Resume/CV layouts commonly use one merged ``Образование`` section row
    # followed by a bare graduation year.  The repeated merged cells are not
    # column headers (and must not make every later cell EDUCATION), but their
    # immediate year value still has an unambiguous date meaning.
    education_year_cells = set()
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            value_text = value if isinstance(value, str) else "" if value is None else str(value)
            if not re.fullmatch(r"\s*(?:19|20)\d{2}\s*", value_text):
                continue
            for previous_row in rows[max(0, row_index - 2):row_index]:
                if any(
                    infer_field_label(cell) == "Образование"
                    and is_pure_field_label(cell)
                    for cell in previous_row
                ):
                    education_year_cells.add((row_index, col_index))
                    break

    # Identify column header labels in the top header section (e.g. row < 10)
    # A merged section heading is repeated by python-docx in several columns;
    # it describes the following block, not every later row in those columns.
    repeated_in_row = {}
    for label_row, _label_col, label in labels:
        key = (label_row, label)
        repeated_in_row[key] = repeated_in_row.get(key, 0) + 1
    col_headers: dict[int, tuple[int, str]] = {}
    for label_row, label_col, label in labels:
        if label_row < 10 and repeated_in_row[(label_row, label)] == 1:
            if label_col not in col_headers or label_row > col_headers[label_col][0]:
                col_headers[label_col] = (label_row, label)

    result: List[TableCellContext] = []
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            value_text = value if isinstance(value, str) else "" if value is None else str(value)
            if not value_text.strip():
                continue
            # A cell that is itself a recognized field header is semantic
            # structure, never the value of a neighbouring header.
            own_label = infer_field_label(value_text)
            if own_label and is_pure_field_label(value_text):
                result.append(TableCellContext(
                    row=row_index,
                    column=col_index,
                    value_text=value_text,
                ))
                continue
            if (row_index, col_index) in education_year_cells:
                result.append(TableCellContext(
                    row=row_index,
                    column=col_index,
                    value_text=value_text,
                    label="Дата образования",
                    label_row=row_index - 1,
                    label_column=col_index,
                ))
                continue
            label_match = None
            possibilities = []
            for label_row, label_col, label in labels:
                if label_row == row_index and label_col == col_index:
                    continue
                if label_row != row_index and label_col != col_index:
                    # Diagonal proximity carries no field semantics and made
                    # section headings leak into the preceding table row.
                    continue
                distance = abs(row_index - label_row) + abs(col_index - label_col)
                if distance == 0 or distance > max_label_distance:
                    continue
                # A label points right in horizontal tables and down in
                # vertical tables.  Permit the opposite direction only when
                # there is no intervening value; this handles transposed
                # forms and right-to-left legal templates.
                if label_row == row_index and label_col < col_index:
                    between = row[label_col + 1:col_index]
                    if any(_nonempty(v) for v in between):
                        continue
                elif label_col == col_index and label_row < row_index:
                    between_rows = rows[label_row + 1:row_index]
                    if any(label_col < len(r) and _nonempty(r[label_col]) for r in between_rows):
                        continue
                elif label_row == row_index and label_col > col_index:
                    continue
                elif label_col == col_index and label_row > row_index:
                    continue
                possibilities.append(( _distance(row_index, col_index, label_row, label_col), label_row, label_col, label))
            if possibilities:
                _, label_row, label_col, label = min(possibilities)
                label_match = (label, label_row, label_col)
            elif col_index in col_headers and row_index > col_headers[col_index][0]:
                h_row, h_label = col_headers[col_index]
                label_match = (h_label, h_row, col_index)

            result.append(TableCellContext(
                row=row_index,
                column=col_index,
                value_text=value_text,
                label=label_match[0] if label_match else None,
                label_row=label_match[1] if label_match else None,
                label_column=label_match[2] if label_match else None,
            ))
    return result



def project_span_to_value(span_start: int, span_end: int, context: TableCellContext) -> Optional[Tuple[int, int]]:
    """Project a Pullenti span from ``analysis_text`` into cell coordinates.

    Spans touching the synthetic label/prefix are rejected, preventing a field
    label from leaking into a replacement.  Returned coordinates are local to
    ``value_text`` and use Python's half-open convention.
    """

    value_start = context.value_offset
    value_end = value_start + len(context.value_text)
    if span_start < value_start or span_end > value_end or span_start >= span_end:
        return None
    return span_start - value_start, span_end - value_start


def contexts_from_extracted_table(table: Iterable[Iterable[object]], **kwargs) -> List[TableCellContext]:
    """Alias for PDF/table extractors that expose rows as iterables."""

    return build_table_contexts([list(row) for row in table], **kwargs)
