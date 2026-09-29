# -*- coding: utf-8 -*-
"""Небольшой адаптер Pullenti для DOCXдодыр.

Внутри приложения все диапазоны имеют стандартный для Python вид ``[start, end)``.
PullentiPython, напротив, публикует ``end_char`` как индекс последнего символа
включительно.  Этот модуль является единственным местом, где координаты Pullenti
преобразуются в координаты приложения.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Optional

from validation import is_sufficient_address
from pullenti_legal.checksums import valid_ogrn

PULLENTI_TYPE_MAP = {
    "PERSON": "PER",
    "ORGANIZATION": "ORG",
    "GEO": "ADDRESS",
    "ADDRESS": "ADDRESS",
    "STREET": "ADDRESS",
    "PHONE": "PHONE_NUMBER",
}

_DATE_TEXT_REGEX = re.compile(
    r"(?:[«\"“„]?\d{1,2}[»\"”]?)\s*"
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
    r"\d{4}(?:\s*(?:год[а-яё]*|г\.))?"
    r"|[«\"“„]\s*[»\"”]\s*"
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+"
    r"\d{4}(?:\s*(?:год[а-яё]*|г\.))?"
    r"|\b(?:в\s+)?(?:январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)\s+"
    r"\d{4}(?:\s+(?:год[а-яё]*|г\.))?"
    r"|\b(?:январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)\s+"
    r"\d{4}(?:\s+(?:год[а-яё]*|г\.))?"
    r"|\b\d{2}\.\d{2}\.(?:\d{4}|\d{2})(?!\d)"
    r"|\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})(?!\d)",
    re.I,
)
_RANGE_START_DATE_REGEX = re.compile(
    r"(?<=\bс\s)\d{1,2}\s+"
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?=\s+по\s+)", re.I,
)
_TEMPLATE_YEAR_REGEX = re.compile(r"(?P<value>(?:19|20)\d{2}\s*г\.)", re.I)
_PURCHASE_REGEX = re.compile(
    r"(?:"
    r"(?:реестровый\s*(?:№|номер)\s*(?:закупки)?|номер\s+закупки|закупк[аи]\s*(?:№|N|#))"
    r"\s*[:\s]*(?P<labelled>\d{8,25})"
    r"|(?:№|N|#)\s*(?P<eis>\d{19})\b"
    r")",
    re.I,
)

URI_SCHEME_TO_LABEL = {
    "EMAIL": "EMAIL",
    "MAILTO": "EMAIL",
    "WWW": "WEBSITE",
    "HTTP": "WEBSITE",
    "HTTPS": "WEBSITE",
    "FTP": "WEBSITE",
}

_WEBSITE_VALUE_PATTERN = re.compile(
    r"(?:"
    r"(?:https?|ftp)://[^\s<>\[\]]+|"
    r"www\.[A-Za-zА-Яа-яЁё0-9-]+(?:\.[A-Za-zА-Яа-яЁё0-9-]+)+(?:[/?:#][^\s<>\[\]]*)?|"
    r"(?:[A-Za-zА-Яа-яЁё0-9](?:[A-Za-zА-Яа-яЁё0-9-]{0,62}[A-Za-zА-Яа-яЁё0-9])?\.)+"
    r"(?:ru|рф|com|org|net|io|biz|info|pro|edu|gov)(?:[/?:#][^\s<>\[\]]*)?"
    r")",
    re.I,
)


def _is_valid_website_surface(surface: str) -> bool:
    """Reject decimal numbers and dates misclassified as URI/WWW.

    Pullenti's URI analyzer deliberately accepts dotted numeric tokens.  For
    redaction, a website needs a protocol/``www`` marker or a real DNS suffix;
    section numbers (``1.2``) and dates (``31.05.2003``) are not web data.
    """

    return bool(_WEBSITE_VALUE_PATTERN.search(surface))

_LEGAL_URI_SCHEMES = {"ИНН", "КПП", "ОГРН", "ОГРНИП", "БИК", "Р/С", "К/С", "ОКПО", "ОКТМО", "ОКАТО", "ОКВЭД", "КБК", "Л/С", "Л/СЧ", "ЛС", "УПД"}

_GENERIC_PER_EXCLUSIONS = frozenset({
    "исполнитель", "исполнителя", "исполнителю", "исполнителем", "исполнителе", "исполнители", "исполнителей", "исполнителям", "исполнителями", "исполнителях",
    "заказчик", "заказчика", "заказчику", "заказчиком", "заказчике", "заказчики", "заказчиков", "заказчикам", "заказчиками", "заказчиках",
    "подрядчик", "подрядчика", "подрядчику", "подрядчиком", "подрядчике", "подрядчики", "подрядчиков", "подрядчикам", "подрядчиками", "подрядчиках",
    "генподрядчик", "генподрядчика", "субподрядчик", "субподрядчика",
    "арендатор", "арендатора", "арендатору", "арендатором", "арендаторе", "арендаторы", "арендаторов",
    "арендодатель", "арендодателя", "арендодателю", "арендодателем", "арендодателе", "арендодатели", "арендодателей",
    "покупатель", "покупателя", "покупателю", "покупателем", "покупателе", "покупатели", "покупателей",
    "продавец", "продавца", "продавцу", "продавцом", "продавце", "продавцы", "продавцов",
    "поставщик", "поставщика", "поставщику", "поставщиком", "поставщике", "поставщики", "поставщиков",
    "клиент", "клиента", "клиенту", "клиентом", "клиенте", "клиенты", "клиентов",
    "агент", "агента", "агенту", "агентом", "агенте", "агенты", "агентов",
    "принципал", "принципала", "принципалу", "принципалом", "принципале", "принципалы", "принципалов",
    "субагент", "субагента", "субагенту", "субагентом", "субагенте", "субагенты", "субагентов",
    "сторона", "стороны", "стороне", "сторону", "стороной", "сторонах", "сторонам", "сторонами",
    "гражданин", "гражданина", "гражданину", "гражданином", "гражданине", "граждане", "граждан", "гражданам", "гражданами", "гражданах",
    "индивидуальный предприниматель", "индивидуального предпринимателя", "индивидуальному предпринимателю", "индивидуальным предпринимателем",
    "директор", "директора", "директору", "директором", "директоре", "директоры", "директоров",
    "генеральный директор", "генерального директора", "генеральному директору", "генеральным директором",
    "руководитель", "руководителя", "руководителю", "руководителем", "руководителе", "руководители", "руководителей",
    "менеджер", "менеджера", "менеджеру", "менеджером", "менеджере", "менеджеры", "менеджеров",
    "работник", "работника", "работнику", "работником", "работнике", "работники", "работников",
    "сотрудник", "сотрудника", "сотруднику", "сотрудником", "сотруднике", "сотрудники", "сотрудников",
    "турагент", "турагента", "турагенту", "турагентом", "туроператор", "туроператора",
    "пассажир", "пассажира", "пассажиру", "пассажиром",
    "автор", "автора", "автору", "автором", "авторе", "авторы", "авторов",
    "турист", "туриста", "туристу", "туристом", "туристе", "туристы", "туристов",
    "заявитель", "заявителя", "заявителю", "заявителем", "заявители", "заявителей",
    "ответчик", "ответчика", "ответчику", "ответчиком", "ответчики", "ответчиков",
    "истец", "истца", "истцу", "истцом", "истцы", "истцов",
    "судья", "судьи", "судье", "судью", "судьей", "судьями", "судьях",
    "м.п.", "м. п.", "м.п", "мп", "т.е.", "и т.д.", "и т.п.", "и др.", "см.", "ср.", "напр.", "ст.", "ч.", "п.",
})

_GENERIC_GEO_EXCLUSIONS = frozenset({
    "рф", "россия", "россии", "россию", "россией",
    "российская федерация", "российской федерации", "российскую федерацию", "российской федерацией",
})


def is_semantic_acronym_or_term(source_text: str, start: int, end: int) -> bool:
    """Семантический и синтаксический анализ: определяет, является ли токен аббревиатурой или термином договора.
    
    Использует 3 структурных принципа:
    1. Расшифровка акронима в скобках: АББР (Расшифровка...) -> РИМ (Рекламно-информационные материалы).
    2. Структура словарной статьи/определения: '1.1. ТЕРМИН – определение'.
    3. Синтаксис регистра: короткое КАПСЛОК-слово в обычном тексте без предшествующего гео-классификатора.
    """
    token = source_text[start:end]
    # 1. Проверка на акроним со скобками
    after = source_text[end:].lstrip()
    if after.startswith("("):
        closing = after.find(")")
        if closing != -1:
            inside = after[1:closing].strip()
            words = re.findall(r'[А-ЯЁа-яёA-Za-z0-9]+', inside)
            if len(words) >= 2:
                initials = "".join(w[0].upper() for w in words)
                if token.upper() == initials or token.upper() in initials:
                    return True
                if not re.search(r'\b(?:город|г\.|страна|область|регион|district|city|state)\b', inside, re.I):
                    return True

    # 2. Определение термина (пункт договора с тире)
    before = source_text[max(0, start - 25):start]
    if re.search(r'(?:^\s*\d+\.\d+\.?|\bраздел\b|\bпункт\b|\bтермин\b)\s*$', before, re.I):
        if re.match(r'^\s*[\–\—\-]\s+[А-ЯЁа-яё]', after):
            return True

    # 3. Синтаксис регистра: 2-5 букв верхнего регистра в обычном тексте
    if token.isupper() and 2 <= len(token) <= 5:
        prefix = source_text[max(0, start - 30):start].casefold()
        has_geo_marker = bool(re.search(
            r'\b(?:г\.|город|поселение|пос\.|деревня|дер\.|село|с\.|район|р-н|ул\.|улица|пер\.|переулок|пр-кт|проспект|ш\.|шоссе|наб\.|набережная|адрес|нахождение|находящ\w*)\s*$',
            prefix
        ))
        if not has_geo_marker:
            alpha_chars = [c for c in source_text if c.isalpha()]
            upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / max(1, len(alpha_chars))
            if upper_ratio < 0.6:
                return True

    return False


@dataclass(frozen=True)
class EntitySpan:
    """Сущность с Python-совместимым полуинтервалом ``[start, end)``."""

    text: str
    start: int
    end: int
    label: str

    @property
    def length(self) -> int:
        return self.end - self.start


def _annotation_text(occurrence) -> Optional[str]:
    """Возвращает текст аннотации, если версия Pullenti это поддерживает."""

    getter = getattr(occurrence, "get_text", None)
    if callable(getter):
        try:
            value = getter()
            return value if isinstance(value, str) else None
        except Exception:
            return None
    return None


def span_from_occurrence(source_text: str, occurrence, label: str) -> Optional[EntitySpan]:
    """Безопасно преобразует координаты вхождения Pullenti.

    В штатном Pullenti ``end_char`` включителен. Проверка через ``get_text``
    оставлена для совместимости с возможными обёртками, которые уже перевели
    координаты в полуинтервал.
    """

    start = getattr(occurrence, "begin_char", None)
    raw_end = getattr(occurrence, "end_char", None)
    if not isinstance(start, int) or not isinstance(raw_end, int):
        return None

    reported_text = _annotation_text(occurrence)
    inclusive_end = raw_end + 1
    if reported_text is not None and source_text[start:raw_end] == reported_text:
        # Защита для сторонней обёртки с уже эксклюзивным концом.
        end = raw_end
    else:
        end = inclusive_end

    if not (0 <= start < end <= len(source_text)):
        return None

    surface = source_text[start:end]
    if reported_text is not None and surface != reported_text:
        return None
    return EntitySpan(surface, start, end, label)


def _label_for_referent(referent, source_text: str, occurrence) -> Optional[str]:
    import re
    type_name = str(getattr(referent, "type_name", "")).upper()
    if type_name == "LEGALENTITY":
        kind = getattr(referent, "kind", None)
        if kind:
            return str(kind).upper()
        finder = getattr(referent, "find_slot", None)
        if callable(finder):
            slot = finder("KIND", None, True)
            if slot is not None:
                return str(slot.value).upper()
        return None
    if type_name != "URI":
        return PULLENTI_TYPE_MAP.get(type_name)
    scheme = str(getattr(referent, "scheme", "") or "").upper()
    if scheme in URI_SCHEME_TO_LABEL:
        return URI_SCHEME_TO_LABEL[scheme]
    if scheme in _LEGAL_URI_SCHEMES:
        return None
    span = span_from_occurrence(source_text, occurrence, "URI")
    if span is not None:
        if "@" in span.text or span.text.startswith("mailto:"):
            return "EMAIL"
        if re.search(r"^(?:https?://|www\.)|(?:[a-zA-Z0-9-]+\.(?:ru|com|org|net|io|рф|biz|info|pro|edu|gov)(?:[/?#:]|$))", span.text, re.I):
            return "WEBSITE"
    return None


_PUBLIC_AUTHORITY_PATTERN = re.compile(
    r"\b(?:"
    r"арбитражн(?:ый|ого|ому|ым|ом|ые)\s+(?:апелляционн(?:ый|ого|ому|ым|ом)\s+|кассационн(?:ый|ого|ому|ым|ом)\s+)?суд(?:а|у|ом|е|ы|ов)?|"
    r"верховн(?:ый|ого|ому|ым|ом)\s+суд(?:а|у|ом|е)?|"
    r"конституционн(?:ый|ого|ому|ым|ом)\s+суд(?:а|у|ом|е)?|"
    r"районн(?:ый|ого|ому|ым|ом)\s+суд(?:а|у|ом|е)?|"
    r"городск(?:ой|ого|ому|им|ом)\s+суд(?:а|у|ом|е)?|"
    r"(?:[А-ЯЁ][а-яё-]+\s+)?(?:окружн(?:ой|ого|ому|ым|ом)|областн(?:ой|ого|ому|ым|ом)|республиканск(?:ий|ого|ому|им|ом))\s+суд(?:а|у|ом|е)?(?:\s+республик(?:а|и|е|у|ой)\s+[А-ЯЁа-яё-]+)?|"
    r"миров(?:ой|ого|ому|ым|ом)\s+судь(?:я|и|е|ю)|"
    r"судебн(?:ый|ого|ому|ым|ом)\s+участок|"
    r"судебн(?:ая|ой|ую|ое)\s+коллеги(?:я|и|ей|ю)(?:\s+по\s+[а-яёА-ЯЁ\s]+)?|"
    r"департамент(?:а|у|ом|е|ы|ов)?(?:\s+[а-яёА-ЯЁ\w\s]+)?|"
    r"министерств(?:о|а|у|ом|е)(?:\s+[а-яёА-ЯЁ\w\s]+)?|"
    r"федеральн(?:ая|ой|ую|ое|ый|ые|ых|ым|ого|ому)\s+(?:[а-яёА-ЯЁ\w-]+\s+){0,3}(?:служб\w*|агентств\w*|надзор\w*|палат\w*|комисси\w*|казначейств\w*)(?:\s+[а-яёА-ЯЁ\w\s]+)?|"
    r"правительств(?:о|а|у|ом|е)(?:\s+[а-яёА-ЯЁ\w\s]+)?|"
    r"парламент(?:а|у|ом|е)?(?:\s+республик(?:а|и|е|у|ой)\s+[а-яёА-ЯЁ\-\s]+)?|"
    r"государственн(?:ая|ой|ую)\s+дум(?:а|ы|е|у|ой)|госдум(?:а|ы|е|у|ой)|"
    r"федеральн(?:ая|ой|ую)\s+палат(?:а|ы|е|у|ой)(?:\s+рф)?|"
    r"агентств(?:о|а|у|ом|е)\s+по\s+регистрации(?:\s+[а-яёА-ЯЁ\-\s]+)?|"
    r"администраци(?:я|и|ей|ю)(?:\s+[а-яёА-ЯЁ\w\s]+)?|"
    r"комитет(?:а|у|ом|е|ы|ов)?(?:\s+[а-яёА-ЯЁ\w\s]+)?|"
    r"орган(?:а|у|ом|е|ы|ов)?\s+загс|отдел(?:а|у|ом|е|ы|ов)?\s+загс|"
    r"управлени(?:е|я|ем|и)\s+(?:загс|минюста|роспотребнадзора|фнс|фссп|мвд|гибдд|казначейства|федеральн\w*)|"
    r"главн(?:ое|ым|ого|ому)\s+управлени(?:е|ем|я|ю)\s+минюста|"
    r"банк(?:а|у|ом|е)?\s+россии|центральн(?:ый|ого|ому|ым|ом)\s+банк(?:а|у|ом|е)?|"
    r"росархив(?:а|у|ом|е)?|ростуризм(?:а|у|ом|е)?|росавиаци(?:я|и|ей|ю)?|росавтодор(?:а|у|ом|е)?|росимуществ(?:о|а|у|ом|е)?|росреестр(?:а|у|ом|е)?|рослесхоз(?:а|у|ом|е)?|росрыболовств(?:о|а|у|ом|е)?|роснедр(?:а|у|ом|е)?|росрезерв(?:а|у|ом|е)?|росжелдор(?:а|у|ом|е)?|росморречфлот(?:а|у|ом|е)?|рособрнадзор(?:а|у|ом|е)?|росздравнадзор(?:а|у|ом|е)?|россельхознадзор(?:а|у|ом|е)?|росфинмониторинг(?:а|у|ом|е)?|"
    r"минэкономразвити(?:я|и|ем|ю)?|минцифр(?:ы|е|у|ой)?|минтранс(?:а|у|ом|е)?|минздрав(?:а|у|ом|е)?|минпросвещени(?:я|и|ем|ю)?|минобрнаук(?:и|е|у|ой)?|минстрой(?:я|ю|ем|е)?|минсельхоз(?:а|у|ом|е)?|минприрод(?:ы|е|у|ой)?|минэнерго|минпромторг(?:а|у|ом|е)?|минтруд(?:а|у|ом|е)?|минкультур(?:ы|е|у|ой)?|минспорт(?:а|у|ом|е)?|мид\s+(?:россии|рф)|мчс\s+(?:россии|рф)|мвд\s+(?:россии|рф)|фас\s+(?:россии|рф)?|фмба\s+(?:россии|рф)?|фсин\s+(?:россии|рф)?|фсо\s+(?:россии|рф)?|фсб\s+(?:россии|рф)?|фтс\s+(?:россии|рф)?|"
    r"фссп|уфссп|гибдд|мвд|овд|уфмс|минюст(?:а|у|ом|е)?|минфин(?:а|у|ом|е)?|роспотребнадзор(?:а|у|ом|е)?|роскомнадзор(?:а|у|ом|е)?|"
    r"роспатент(?:а|у|ом|е)?|ростехнадзор(?:а|у|ом|е)?|росстандарт(?:а|у|ом|е)?|госстандарт(?:а|у|ом|е)?|госкомстат(?:а|у|ом|е)?|фнс|ифнс|уфк|вс\s+рф|казначейств(?:о|а|у|ом|е)|"
    r"российск(?:ая|ой|ую|ое|им|ом)\s+академи(?:я|и|ей|ю)\s+народн(?:ого|ому|ым|ом|ая|ой|ую)\s+хозяйств(?:а|у|ом|е)?\s+и\s+государственн(?:ой|ого|ому|ым|ом)\s+служб(?:ы|е|у|ой)?(?:\s+при\s+президент(?:е|а|у|ом)\s+российской\s+федерации)?|"
    r"аис(?:\s+[«\"'][^»\"']+[»\"'])?|"
    r"таможенн(?:ый|ого|ому|ым|ом)\s+союз(?:а|у|ом|е)?|евразийск(?:ая|ой|ую)\s+экономическ(?:ая|ой|ую)\s+комисси(?:я|и|ей|ю)|"
    r"тс|тр\s+тс"
    r")\b",
    re.I,
)

_NPA_PREFIX_REGEX = re.compile(
    r"(?:фз|федеральн\w+\s+закон\w*|закон\w*\s+(?:рф|российской\s+федерации|г\.\s*москвы|москвы|области|края)|кодекс\w*|постановлени\w*|указ\w*|распоряжени\w*|приказ\w*|гост(?=\s*(?:р\b|iso\b|iec\b|[-–—]|\d))|снип\w*|санпин\w*)\s*(?:от|\bN\b|№)?\s*$",
    re.I,
)

_NPA_DATE_CONTEXT = re.compile(
    r"(?:"
    r"федеральн\w*\s+закон\w*|закон\w*|кодекс\w*|"
    r"постановлени\w*|приказ\w*|указ\w*|распоряжени\w*|"
    r"определени\w*(?:\s+[^;]{0,180}?(?:суд\w*|вс\s+рф))|"
    r"гост(?=\s*(?:р\b|iso\b|iec\b|[-–—]|\d))|снип\w*|санпин\w*"
    r")[^;]{0,220}?\bот\s*$",
    re.I,
)

_LEGAL_DATE_CONTEXT = re.compile(
    r"(?:"
    r"решени\w*(?:\s+[^;]{0,180})?\s+(?:комисси\w*|орган\w*|суд\w*)|"
    r"директив\w*|распоряжени\w*|постановлени\w*|приказ\w*|"
    r"отказ\w*(?:\s+[^;]{0,160})?\s+(?:орган\w*|агентств\w*|ведомств\w*|служб\w*|суд\w*)|"
    r"судебн\w*\s+акт\w*|определени\w*(?:\s+[^;]{0,180})?\s+суд\w*|"
    r"договор\w*\s+(?:о\s+)?(?:евразийск\w*\s+экономическ\w*\s+союз\w*|"
    r"таможенн\w*\s+союз\w*|международн\w*)|"
    r"международн\w*\s+соглашени\w*|межправительственн\w*\s+соглашени\w*|"
    r"регламент\w*|конвенци\w*"
    r")[^;]{0,240}?\b(?:от|подписан\w*|утвержден\w*|принят\w*)\s*$",
    re.I,
)

_INTERNATIONAL_SIGNATURE_DATE_CONTEXT = re.compile(
    r"(?:"
    r"договор\w*\s+(?:о\s+)?(?:евразийск\w*\s+экономическ\w*\s+союз\w*|"
    r"таможенн\w*\s+союз\w*|международн\w*)|"
    r"международн\w*\s+соглашени\w*|межправительственн\w*\s+соглашени\w*|"
    r"конвенци\w*"
    r")[^;\n]{0,240}?\b(?:подписан\w*|принят\w*|заключ[её]н\w*|утвержден\w*)\b"
    r"[^;\n]{0,120}$",
    re.I,
)

_REFERENCE_DATE_CONTEXT = re.compile(
    r"(?:консультантплюс|готовое\s+решение|актуально\s+на|справочн\w*|библиограф\w*)[^;]{0,160}$",
    re.I,
)

_CHRONOLOGY_DATE_CONTEXT = re.compile(
    r"(?:историческ\w*\s+дат\w*|хронолог\w*|консерваци\w*|протокол\w*|"
    r"по\s+состоянию\s+на|истори\w*\s+событи\w*|решени\w*\s+и\s+отказ\w*)",
    re.I,
)

_PERSONAL_DATE_CONTEXT = re.compile(
    r"(?:дата\s+рождения|родил\w*|паспорт\w*|снилс|страхов\w*\s+номер|"
    r"срок\s+действия|дата\s+заключения\s+договор\w*)",
    re.I,
)

_HISTORICAL_EVENT_CONTEXT = re.compile(
    r"(?:"
    r"совет\w*\s+директор\w*[^;]{0,220}?(?:приня\w*\s+решени\w*|консерваци\w*|протокол\w*)|"
    r"истекл\w*\s+полномочи\w*|досрочн\w*\s+прекращени\w*\s+полномочи\w*|"
    r"агентств\w*\s+по\s+регистраци\w*[^;]{0,220}?отказ\w*|"
    r"торгов\w*\s+реестр\w*[^;]{0,260}?(?:указан\w*|зарегистрирован\w*)|"
    r"представлен\w*\s+информаци\w*[^;]{0,220}?(?:передач\w*|аренд\w*|сделк\w*)|"
    r"директив\w*[^;]{0,220}?совет\w*\s+директор\w*|"
    r"решени\w*\s+о\s+(?:консерваци\w*|назначени\w*|избрани\w*|прекращени\w*)"
    r")",
    re.I | re.S,
)

_PUBLIC_LEGAL_FORMULA_PATTERN = re.compile(
    r"(?:российск(?:ая|ой|ую|ое|ой)\s+федераци\w*|"
    r"субъект\w*\s+российск(?:ой|ую|ая|ое)\s+федераци\w*|"
    r"муниципальн(?:ое|ого|ому|ым|ом|ая|ой|ую)\s+образовани\w*)",
    re.I,
)

_INTERNATIONAL_LEGAL_TITLE_PATTERN = re.compile(
    r"(?:договор\w*\s+о\s+евразийск\w*\s+экономическ\w*\s+союз\w*|"
    r"решени\w*\s+комисси\w*\s+(?:таможенн\w*\s+союз\w*|евразийск\w*\s+экономическ\w*\s+комисси\w*)|"
    r"регламент\w*\s+(?:европейск\w*\s+союз\w*|ес)|директив\w*\s+(?:ес|европейск\w*\s+союз\w*))",
    re.I,
)

_INTERNAL_FUND_PATTERN = re.compile(
    r"\bфонд(?:а|у|ом|е)?\s+[«\"']\s*(?:резервн\w*|внутренн\w*)\s*[»\"']",
    re.I,
)

_LEGAL_FORMULA_OR_MARKER_PATTERN = re.compile(
    r"(?:адресу\s*\(-\s*ам\)|адрес\w*\s*\(-\s*ам\)|"
    r"фонд(?:а|у|ом|е)?\s+[«\"']\s*(?:резервн\w*|внутренн\w*)\s*[»\"']|"
    r"российск(?:ая|ой|ую|ое)\s+федераци\w*|субъект\w*\s+российск\w*\s+федераци\w*|"
    r"муниципальн\w*\s+образовани\w*|договор\w*\s+о\s+евразийск\w*\s+экономическ\w*\s+союз\w*)",
    re.I,
)

_PRIVATE_ORG_FORM_PATTERN = re.compile(
    r"\b(?:ооо|еооо|пао|ао|зао|оао|нао|ано|тсж|"
    r"гаук|гбу|фгбу|гку|фгку|муп|гуп|фгуп|"
    r"(?:федеральн\w*\s+)?государственн\w*\s+(?:автономн\w*|бюджетн\w*|каз[её]нн\w*)\s+учреждени\w*|"
    r"обществ\w*\s+с\s+ограниченн\w*\s+ответственност\w*|"
    r"акционерн\w*\s+обществ\w*)\b",
    re.I,
)

_ANY_ORG_FORM_PATTERN = re.compile(
    r"\b(?:ооо|еооо|пао|ао|зао|оао|нао|ано|тсж|муп|гуп|фгуп|"
    r"фгбу|гбу|гаук|гку|фгку)\b",
    re.I,
)

_GENERIC_QUOTED_UNIT_NAME = re.compile(
    r"^\s*(?:гостиничн(?:ый|ого|ому|ым|ом)\s+комплекс(?:а|у|ом|е)?|"
    r"структурн(?:ое|ого|ому|ым|ом)\s+подразделени(?:е|я|ю|ем|и))\s*$",
    re.I,
)


def _quoted_value(surface: str) -> Optional[str]:
    first = re.search(r"[«\"“„]", surface)
    if not first:
        return None
    last = max(surface.rfind(ch) for ch in '»\"”')
    if last <= first.start():
        return None
    return surface[first.end():last].strip()


def _is_npa_date(source_text: str, start: int) -> bool:
    """Return true for a date syntactically attached to a cited legal act."""

    prefix = source_text[max(0, start - 320):start]
    line_start = source_text.rfind("\n", 0, start) + 1
    line_prefix = source_text[max(line_start, start - 320):start]
    m_ot = re.search(r"\bот\s*$", prefix, re.I)
    if m_ot:
        ot_pos = m_ot.start()
        sub_prefix = prefix[:ot_pos]

        npa_matches = []
        for m in re.finditer(
            r"\b(?:"
            r"федеральн\w*\s+закон\w*|закон\w*(?:\s+(?:рф|российской\s+федерации|г\.\s*москвы|москвы|области|края))?|фз\b|"
            r"кодекс\w*|постановлени\w*|указ\w*|распоряжени\w*|"
            r"гост(?=\s*(?:р\b|iso\b|iec\b|[-–—]|\d))|снип\w*|санпин\w*|"
            r"регламент\w*|конвенци\w*|директив\w*|"
            r"определени\w*(?:\s+[^;]{0,180}?(?:суд\w*|вс\s+рф))|"
            r"приказ\w*"
            r")\b",
            sub_prefix,
            re.I,
        ):
            marker_str = m.group(0).casefold()
            m_start = m.start()
            if marker_str.startswith("приказ"):
                order_window = sub_prefix[m_start:]
                if not _PUBLIC_AUTHORITY_PATTERN.search(order_window):
                    continue
            npa_matches.append((m_start, m.end(), "NPA"))

        private_matches = []
        for m in re.finditer(
            r"\b(?:"
            r"обращени\w*|договор\w*|контракт\w*|сч[её]т\w*|"
            r"плат[её]жн\w*\s+поручени\w*|накладн\w*|акт\w*|заявлени\w*|"
            r"приказ\w*"
            r")\b",
            sub_prefix,
            re.I,
        ):
            marker_str = m.group(0).casefold()
            m_start = m.start()
            if marker_str.startswith("приказ"):
                order_window = sub_prefix[m_start:]
                if _PUBLIC_AUTHORITY_PATTERN.search(order_window):
                    continue
            private_matches.append((m_start, m.end(), "PRIVATE"))

        all_doc_markers = sorted(npa_matches + private_matches, key=lambda x: x[0])
        if all_doc_markers:
            last_marker = all_doc_markers[-1]
            if last_marker[2] == "NPA":
                return True
            if last_marker[2] == "PRIVATE":
                return False
    return bool(
        _NPA_PREFIX_REGEX.search(prefix)
        or _NPA_DATE_CONTEXT.search(prefix)
        or _LEGAL_DATE_CONTEXT.search(prefix)
        or _INTERNATIONAL_SIGNATURE_DATE_CONTEXT.search(prefix)
        # Reference captions are frequently split across Word paragraphs or
        # table-cell lines: ``КонсультантПлюс\nДата сохранения: <date>``.
        or _REFERENCE_DATE_CONTEXT.search(prefix)
        or re.search(
            r"зарегистрирован\w*\s+(?:в\s+)?(?:минюст\w*|министерств\w+\s+юстици\w+)(?:\s+россии)?\s*$",
            prefix,
            re.I,
        )
        or (
            _CHRONOLOGY_DATE_CONTEXT.search(line_prefix)
            and not _PERSONAL_DATE_CONTEXT.search(line_prefix)
        )
        or (
            _HISTORICAL_EVENT_CONTEXT.search(source_text)
            and not _PERSONAL_DATE_CONTEXT.search(source_text)
        )
        or re.search(r"по\s+состоянию\s+на\s*$", line_prefix, re.I)
    )


def _is_official_document_number(source_text: str, start: int, value: str = "") -> bool:
    if value:
        val_upper = value.upper().strip()
        if re.search(r"\bФЗ\b|-ФЗ\b|\bФКЗ\b|-ФКЗ\b", val_upper) or val_upper.endswith(("ФЗ", "ФКЗ")):
            return True
        if re.search(r"\b(?:ГОСТ|СНИП|САНПИН)\b", val_upper):
            return True

    prefix = source_text[max(0, start - 260):start]
    order = re.search(r"приказ\w*[^;\n]{0,180}$", prefix, re.I)
    if order and _PUBLIC_AUTHORITY_PATTERN.search(order.group(0)):
        return True
    return bool(re.search(
        r"(?:"
        r"фз\b|фкз\b|федеральн\w+\s+(?:конституционн\w+\s+)?закон\w*|закон\w*|"
        r"постановлени\w*|распоряжени\w*|указ\w*|регламент\w*|инструкци\w*|указани\w*|"
        r"положен\w*|правил\w*|стандарт\w*|гост\w*|снип\w*|санпин\w*|"
        r"ст\b|стать\w*|п\b|пункт\w*"
        r")[^;.!?]{0,180}$",
        prefix,
        re.I,
    ))


def _is_protected_legal_term(source_text: str, span: EntitySpan) -> bool:
    """Keep public-law formulae and internal structural labels out of NER."""

    # Only an overlapping match is evidence.  Looking at a broad surrounding
    # window would incorrectly exempt a private entity merely because the same
    # sentence mentions the Russian Federation or a public-law formula.
    local_start = max(0, span.start - 120)
    local_end = min(len(source_text), span.end + 120)
    for pattern in (
        _PUBLIC_LEGAL_FORMULA_PATTERN,
        _INTERNATIONAL_LEGAL_TITLE_PATTERN,
        _INTERNAL_FUND_PATTERN,
        _LEGAL_FORMULA_OR_MARKER_PATTERN,
    ):
        for match in pattern.finditer(source_text[local_start:local_end]):
            match_start = local_start + match.start()
            match_end = local_start + match.end()
            if span.start < match_end and match_start < span.end:
                return True
    return False


def _is_explicit_postal_address(source_text: str, span: EntitySpan) -> bool:
    """True when a legal-field marker and postal detail make GEO text private."""

    prefix = source_text[max(0, span.start - 48):span.start]
    return bool(
        _EXPLICIT_ADDRESS_CONTEXT.search(prefix)
        and _POSTAL_COMPONENT_PATTERN.search(span.text)
    )


_LEGAL_CITATION_AT_SPAN = re.compile(
    r"^\s*(?:ст\.|статья)\s*\d+(?:\.\d+)?\s+"
    r"(?:ГК|ГПК|АПК|УК|УПК|КоАП|КТМ|БК|НК|ТК|ЖК|СК)(?:\s+РФ)?\b",
    re.I,
)
_LEGAL_CODE_TAIL = re.compile(
    r"^\s*(?:ГК|ГПК|АПК|УК|УПК|КоАП|КТМ|БК|НК|ТК|ЖК|СК)(?:\s+РФ)?\s*[.,;:]",
    re.I,
)


def _is_legal_citation_span(surface: str) -> bool:
    """Reject legal citations that generic GEO/ORG analyzers over-extend."""

    return bool(
        _LEGAL_CITATION_AT_SPAN.match(surface)
        or _LEGAL_CODE_TAIL.match(surface)
    )


def _is_public_or_excluded_org(surface: str, source_text: str, start: int, end: int, label: str = "ORG") -> bool:
    """Универсальная проверка: является ли орган публичным институтом или термином."""
    cleaned = surface.strip(" \t\n\r:;,.()_\"'«»-").casefold()
    if "…" in surface or "..." in surface:
        # Ellipses inside legal forms denote an unfilled template value, not
        # an actual organization name that should be replaced.
        return True
    if _GENERIC_QUOTED_UNIT_NAME.fullmatch(cleaned):
        return True
    if cleaned in {"тс", "тр тс", "таможенный союз", "евразийская экономическая комиссия"}:
        return True
    if label == "ORG":
        if re.search(
            r"(?i)\b(?:(?:многофункциональн\w*\s+)?центр\w*\s+предоставления\s+государственных|почта\s+россии)\b",
            surface,
        ):
            return True
        if re.fullmatch(r"(?i)[A-Z]\d|Customar\s+Service", cleaned):
            return True
        if re.search(r"(?i)\bЗН\s+ККТ\s+\d{10,}\b", surface) and re.search(
            r"(?i)\bЧЕК\b", source_text[max(0, start - 20):start]
        ):
            return True
        if re.search(
            r"(?i)\b(?:УМВД|УВД|ОВД|ЦБ\s+РФ|отдел\w*\s+полиции|"
            r"отдел\w*\s+внутренних\s+дел|Central\s+Bank\s+of\s+the\s+Russian\s+Federation)\b",
            surface,
        ):
            return True
        if re.search(
            r"(?i)\b(?:международн\w*\s+)?аэропорт\w*(?:\s+[А-ЯЁA-Z][а-яёa-z-]+)?\b",
            surface,
        ):
            if not _OPF_PREFIX_PATTERN.match(surface) and not _OPF_QUOTED_CORE_PATTERN.match(surface):
                return True
        if re.search(
            r"(?i)\b(?:процентн\w*|суммарн\w*|банковск\w*|агентск\w*|брокерск\w*)\s+комисси\w*|"
            r"комисси(?:я|и|ей|ю)\s+(?:банка|покупателя|агента|за\s+бронирование)\b",
            surface,
        ):
            return True
        if re.fullmatch(r"(?i)(?:отел\w*|гостиниц\w*|авиакомпани\w*|аэропорт\w*|международный\s+аэропорт\w*|"
                        r"тур\s+агенств\w*|агенств\w*\s+ИП)", cleaned):
            return True
        if re.fullmatch(r"(?i)аэропорт\s+в\s+г\.\s*[А-ЯЁа-яё-]+|в\s+п\.\s*[А-ЯЁа-яё-]+", cleaned):
            return True
        if re.fullmatch(r"[А-ЯЁ][а-яё-]+", surface.strip()) and re.search(
            r"(?i)(?:рейс\s+|аэропорт\s+в\s+г\.\s*)$",
            source_text[max(0, start - 30):start],
        ):
            return True
        if cleaned in {"банк", "банка", "банку", "банком", "банке", "банки", "банков"}:
            return True
        if re.fullmatch(
            r"(?is)(?:отдел|управлени|служб|департамент|комитет)\w*"
            r"(?:\s+по\s+[а-яё\s]+)?(?:\s+(?:отдел|управлени|служб|департамент|комитет)\w*"
            r"(?:\s+по\s+[а-яё\s]+)?)?",
            surface.strip(),
        ):
            return True
        if _ANY_ORG_FORM_PATTERN.fullmatch(surface.strip()):
            return True
        # Quotes alone do not turn a public authority into a private company.
        # An explicit legal form is stronger evidence of a private entity:
        # ООО «Роскомнадзор» is masked, standalone «Роскомнадзор» is not.
        if _PUBLIC_AUTHORITY_PATTERN.search(surface):
            return not bool(_PRIVATE_ORG_FORM_PATTERN.search(surface))
        quoted = _quoted_value(surface)
        if quoted is not None and _GENERIC_QUOTED_UNIT_NAME.fullmatch(quoted):
            return True
        if re.search(r"[«\"'\u201c\u201d\u201e]", surface):
            return False
        # Structural units are roles inside an organisation, not independent
        # counterparties.  Pullenti may classify title-cased phrases such as
        # "Управления развития" as ORGANIZATION; the ontology of the head
        # noun is stronger evidence than capitalization.
        if _GENERIC_UNIT_ONLY.fullmatch(surface.strip()):
            return True
        return bool(_PUBLIC_AUTHORITY_PATTERN.search(surface))
    if label == "ADDRESS":
        # Проверка предшествующего юридического контекста (например, Арбитражный суд [города Москвы], ГУ Банка России [по ЦФО], УФК [по г. Москве])
        prefix_window = source_text[max(0, start - 80):start].casefold()
        if any(court in prefix_window for court in [
            "арбитражного суда", "арбитражный суд", "арбитражном суде", "верховного суда", "районного суда", "районный суд",
            "департамента", "департамент", "департаменте", "министерства", "министерство", "министерстве",
            "государственное автономное учреждение", "государственного автономного учреждения", "государственное бюджетное учреждение", "государственного бюджетного учреждения",
            "государственное казенное учреждение", "государственного казенного учреждения",
            "управления минюста", "управление минюста", "уфмс", "гибдд", "гу банка россии", "банка россии",
            "уфк по", "уфк", "казначейства", "казначейство", "следственного комитета", "прокуратуры",
            "нотариусом", "нотариуса", "нотариус", "мвд россии", "гу мвд",
        ]):
            return True
    return False


_TECHNICAL_MEASUREMENT_AFTER_ORG = re.compile(
    r"^\s+[а-яё-]+(?:ая|яя|ое|ее|ый|ий|ой|ого|ему|ыми?)\s*"
    r"[–—-]?\s*\d+(?:[.,]\d+)?\s*"
    r"(?:[КМГТ]?(?:б|байт)|Гц|кГц|МГц|ГГц|Вт|кВт|В|А|мм|см|м)\b",
    re.I,
)


def _is_technical_non_org(surface: str, source_text: str, start: int, end: int) -> bool:
    """Reject product codes and measured specification labels misread as ORG.

    The decision uses syntax around the span, not a vocabulary of product
    names: a Latin hyphenated code followed by a technical suffix and a
    manufacturer group, or a common specification head followed by an
    adjective and a measured value.
    """

    stripped = surface.strip()
    local = source_text[max(0, start - 100):min(len(source_text), end + 100)]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]{2,}", stripped) and re.search(
        r"(?i)(?:модель\s+процессора|процессор|яд[её]р|оперативн\w+\s+памят|интерфейс|порт(?:ов|а)?\s+USB|экран)",
        local,
    ):
        return True
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9.+-]{2,}", stripped)
        and re.search(r"(?i)наименование\s+товара|характеристики\s+позици", source_text)
        and re.search(r"\d|Core|USB|HDMI|Windows|Wi-?Fi", local, re.I)
    ):
        return True
    if stripped.casefold() in {"корпус", "корпуса", "корпусе", "корпусом"}:
        return True
    if re.fullmatch(r"(?is)компьютерн\w*\s+клуб", stripped) and re.search(r"(?i)ОКВЭД|деятельност\w*\s+на\s+объект", local):
        return True
    after = source_text[end:end + 80]
    if (
        re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+", stripped)
        and re.match(r"-[А-ЯЁA-Z]\b\s*\(\s*(?:ГК|группа\s+компаний)\b", after)
    ):
        return True
    if (
        re.fullmatch(r"[А-ЯЁ][а-яё-]{2,}", stripped)
        and _TECHNICAL_MEASUREMENT_AFTER_ORG.match(after)
        and not _ANY_ORG_FORM_PATTERN.search(source_text[max(0, start - 30):end])
    ):
        return True
    return False


@lru_cache(maxsize=4096)
def _is_unambiguous_inanimate_common_noun(token: str) -> bool:
    """Return true only for a high-confidence common-noun interpretation."""

    try:
        from morph_singleton import get_morph_analyzer

        analyzer = get_morph_analyzer()
        if analyzer is None:
            return False
        parses = analyzer.parse(token)
    except Exception:
        return False
    if not parses:
        return False
    if any(
        parse.score >= 0.05
        and any(marker in parse.tag for marker in ("Name", "Surn", "Patr"))
        for parse in parses
    ):
        return False
    common_noun_score = sum(
        parse.score
        for parse in parses
        if "NOUN" in parse.tag and "inan" in parse.tag
    )
    return common_noun_score >= 0.75


def _is_plausible_person_surface(surface: str) -> bool:
    """Reject Pullenti PERSON spans made solely from ordinary legal nouns.

    Unknown surnames remain accepted.  Rejection requires every lexical token
    to have a high-confidence inanimate common-noun reading, which filters
    false persons such as "Договор" and "Реестр Договоров" without keeping a
    document-specific blacklist.
    """

    if re.match(r"(?is)^\s*М\.?\s*П\.?\s*[\r\n]+", surface):
        return False
    if re.search(r"(?i)\bатолл\b", surface):
        return False
    tokens = re.findall(r"[А-ЯЁа-яё-]{2,}", surface)
    if tokens and all(_is_unambiguous_inanimate_common_noun(token) for token in tokens):
        return False
    if re.search(r"\b[А-ЯЁ]\.?\s*[А-ЯЁ]\.?(?:\b|$)", surface):
        return True
    # Legal forms are structural organization markers even when Pullenti's
    # person analyzer sees the all-caps token as an unknown surname.
    if _ANY_ORG_FORM_PATTERN.fullmatch(surface.strip()):
        return False
    if not tokens:
        latin = re.findall(r"[A-Za-z]{2,}", surface)
        if len(latin) not in (2, 3) or any(word[:1].islower() for word in latin):
            return False
        if any(word.casefold() in {"deluxe", "garden", "view", "account", "manager", "payments", "external", "euro", "usd", "service", "team", "pax", "name"} for word in latin):
            return False
        return True
    if tokens and not any(token[0].isupper() for token in tokens):
        return False
    return bool(tokens) and not all(_is_unambiguous_inanimate_common_noun(token) for token in tokens)


_CITY_ONLY_PATTERN = re.compile(
    r"^\s*(?:г\.?|город(?:а|е|у|ом)?)?\s*[А-ЯЁ][А-ЯЁа-яё-]+"
    r"(?:\s+[А-ЯЁ][А-ЯЁа-яё-]+){0,2}\s*$",
    re.I,
)
_EXPLICIT_ADDRESS_CONTEXT = re.compile(
    r"(?:адрес(?:у)?|место\s+(?:нахождения|оказания|жительства|регистрации)|"
    r"находящ\w*|расположенн\w*|проживающ\w*|зарегистрированн\w*)\s*[:\-]?\s*$",
    re.I,
)
_POSTAL_COMPONENT_PATTERN = re.compile(
    r"(?:\b\d{6}\b|(?:^|[\s,])(?:ул\.|улица\b|пер\.|переулок\b|"
    r"пр-кт\b|проспект\b|ш\.|шоссе\b|наб\.|набережная\b|"
    r"д\.|дом\b|владение\b|стр\.|строение\b|корп\.|корпус\b|"
    r"кв\.|квартира\b|оф\.|офис\b|пом\.|помещение\b))",
    re.I,
)
_PUBLIC_GEO_REFERENCE_PATTERN = re.compile(
    r"(?:^|[\s,])(?:г\.?\s*|город\w*\s+|м\.?\s+)?[А-ЯЁ][А-ЯЁа-яё-]+"
    r"|\b(?:област\w*|край|края|республик\w*|федераци\w*|росси\w*|"
    r"метро|муниципальн\w*\s+образовани\w*)\b",
    re.I,
)


def _is_embedded_geo_reference(source_text: str, span: EntitySpan) -> bool:
    """Distinguish a jurisdictional city mention from a postal address.

    A city by itself is public geographic context.  It becomes an address only
    when an explicit address marker or another postal component (index, street,
    house, building, apartment, office) is present in the same logical line.
    """

    line_start = source_text.rfind("\n", 0, span.start) + 1
    line_end = source_text.find("\n", span.end)
    if line_end < 0:
        line_end = len(source_text)
    # A Word paragraph can contain several sentences and route legs. Postal
    # components from a completed preceding sentence must not turn a later
    # standalone city plus landmark into a full address.  Abbreviation dots
    # (``г. Москва``) are harmless boundaries: the suffix still contains any
    # street/house components that belong to that city.
    before_line = source_text[line_start:span.start]
    sentence_breaks = list(re.finditer(r"[.!?]\s+(?=[А-ЯЁ])", before_line))
    segment_start = (
        line_start + sentence_breaks[-1].end()
        if sentence_breaks else line_start
    )
    after_line = source_text[span.end:line_end]
    next_break = re.search(r"[.!?](?:\s+|$)", after_line)
    segment_end = span.end + (next_break.start() if next_break else len(after_line))
    logical_segment = source_text[segment_start:segment_end]
    if _CITY_ONLY_PATTERN.fullmatch(span.text):
        between = source_text[span.end:segment_end]
        if re.search(r"\b(?:расположенн\w*|находящ\w*)\s+по\s+адресу\b", between, re.I):
            return True
    if _POSTAL_COMPONENT_PATTERN.search(logical_segment):
        return False
    # The user's policy treats locality-only data as public geographic
    # context, including a city plus a metro station and territorial scopes
    # such as "г. Москвы, Московской области и России".  An explicit field
    # caption does not change that when no street/building component exists.
    return bool(
        _CITY_ONLY_PATTERN.fullmatch(span.text)
        or _PUBLIC_GEO_REFERENCE_PATTERN.search(span.text)
    )


def iter_pullenti_spans(analysis_result, source_text: str) -> Iterable[EntitySpan]:
    """Выдаёт поддерживаемые сущности Pullenti с корректными позициями."""

    if analysis_result is None:
        return
    for referent in getattr(analysis_result, "entities", None) or ():
        if str(getattr(referent, "type_name", "")).upper() == "LEGALENTITY":
            finder = getattr(referent, "find_slot", None)
            if callable(finder):
                start_slot = finder("VALUE_START", None, True)
                end_slot = finder("VALUE_END", None, True)
                kind_slot = finder("KIND", None, True)
                try:
                    start = int(start_slot.value)
                    end = int(end_slot.value)
                    label = str(kind_slot.value).upper()
                except (AttributeError, TypeError, ValueError):
                    pass
                else:
                    if 0 <= start < end <= len(source_text):
                        if label == "PASSPORT":
                            raw_val = source_text[start:end]
                            m = re.search(r"(\d{2}\s?\d{2}|\d{4})\s*(\d{6})", raw_val)
                            if m:
                                s_start = start + m.start(1)
                                s_end = start + m.end(1)
                                n_start = start + m.start(2)
                                n_end = start + m.end(2)
                                yield EntitySpan(source_text[s_start:s_end], s_start, s_end, "PASSPORT_SERIES")
                                yield EntitySpan(source_text[n_start:n_end], n_start, n_end, "PASSPORT_NUMBER")
                                continue
                        candidate = EntitySpan(source_text[start:end], start, end, label)
                        if _is_protected_legal_term(source_text, candidate) and not (
                            label == "ADDRESS"
                            and _is_explicit_postal_address(source_text, candidate)
                        ):
                            continue
                        if label in {"DOCUMENT_NUMBER", "CONTRACT_NUMBER"} and _is_official_document_number(source_text, start, candidate.text):
                            continue
                        if label == "EDUCATION" and _PUBLIC_AUTHORITY_PATTERN.search(candidate.text):
                            continue
                        yield candidate
                    continue
        for occurrence in getattr(referent, "occurrence", None) or ():
            label = _label_for_referent(referent, source_text, occurrence)
            if label is None:
                continue
            span = span_from_occurrence(source_text, occurrence, label)
            if span is not None:
                if label in {"DOCUMENT_NUMBER", "CONTRACT_NUMBER"} and _is_official_document_number(source_text, span.start, span.text):
                    continue
                if label in {"PHONE_NUMBER", "EMAIL", "WEBSITE"}:
                    span = refine_contact_span(span, source_text)
                if label == "PHONE_NUMBER":
                    # Postal indexes and columns of monetary amounts are
                    # common false positives from Pullenti's phone analyzer.
                    if re.fullmatch(r"\d{6}", span.text) and not re.search(
                        r"(?i)(?:тел|факс|phone)\w*\s*[:.]?\s*$",
                        source_text[max(0, span.start - 20):span.start],
                    ):
                        continue
                    if re.search(r"(?i)\b(?:сумма|лимит|покрытие)\s*:?\s*$",
                                 source_text[max(0, span.start - 20):span.start]):
                        continue
                if label == "WEBSITE" and not _is_valid_website_surface(span.text):
                    continue
                cleaned_text = span.text.strip(" \t\n\r:;,.()_\"'«»-").casefold()
                if label == "PER":
                    for slot in getattr(referent, "slots", ()):
                        if slot.type_name in ("ATTRIBUTE", "ATTR") and hasattr(slot.value, "occurrence"):
                            prop = slot.value
                            for p_occ in getattr(prop, "occurrence", ()):
                                if p_occ.begin_char >= span.start and p_occ.end_char < span.end:
                                    new_start = p_occ.end_char + 1
                                    while new_start < span.end and source_text[new_start] in " \t\r\n,;:-":
                                        new_start += 1
                                    if new_start < span.end:
                                        span = EntitySpan(source_text[new_start:span.end], new_start, span.end, "PER")
                    cleaned_text = span.text.strip(" \t\n\r:;,.()_\"'«»-").casefold()
                    if cleaned_text in _GENERIC_PER_EXCLUSIONS:
                        continue
                    if not _is_plausible_person_surface(span.text):
                        continue
                    span = refine_person_span(span, source_text)
                    if not span.text.strip():
                        continue
                if label == "ADDRESS" and (cleaned_text in _GENERIC_GEO_EXCLUSIONS or is_semantic_acronym_or_term(source_text, span.start, span.end)):
                    continue
                if label == "ADDRESS" and re.search(r"(?i)занимаем\w*\s+площадь|\bкв\.?\s*$", span.text):
                    continue
                if label in {"ORG", "ADDRESS"} and _is_protected_legal_term(source_text, span) and not (
                    label == "ADDRESS" and _is_explicit_postal_address(source_text, span)
                ):
                    continue
                if label in {"ORG", "ADDRESS"} and _is_legal_citation_span(span.text):
                    continue
                if label == "ADDRESS" and _is_embedded_geo_reference(source_text, span):
                    continue
                if label == "ORG" and _is_technical_non_org(
                    span.text, source_text, span.start, span.end
                ):
                    continue
                if label in {"ORG", "ADDRESS"} and _is_public_or_excluded_org(span.text, source_text, span.start, span.end, label):
                    continue
                yield span

    for m in _DATE_TEXT_REGEX.finditer(source_text):
        if _is_npa_date(source_text, m.start()):
            continue
        yield EntitySpan(m.group(0), m.start(), m.end(), "DATE")

    for m in _RANGE_START_DATE_REGEX.finditer(source_text):
        yield EntitySpan(m.group(0), m.start(), m.end(), "DATE")

    for m in _TEMPLATE_YEAR_REGEX.finditer(source_text):
        prefix = source_text[max(0, m.start() - 40):m.start()]
        if "_" in prefix and not _is_npa_date(source_text, m.start()):
            yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "DATE")

    for m in _PURCHASE_REGEX.finditer(source_text):
        group = "labelled" if m.group("labelled") is not None else "eis"
        yield EntitySpan(m.group(group), m.start(group), m.end(group), "PURCHASE_NUMBER")

    # Fail-closed identifiers frequently found in flattened PDF text. Their
    # fixed formats are stronger evidence than a damaged/missing field label.
    for m in re.finditer(r"(?<!\d)\d{20}(?!\d)", source_text):
        prefix = source_text[max(0, m.start() - 80):m.start()]
        _is_corr = m.group(0).startswith("30101") or bool(re.search(
            r"(?i)(?:корр(?:еспондентск\w*)?\.?\s*сч[её]т\w*|к\s*[/\\]\s*с)\D*$", prefix
        ))
        if _is_corr:
            yield EntitySpan(m.group(0), m.start(), m.end(), "RU_CORR_ACCOUNT")
        elif (
            re.search(r"(?i)(?:сч\.?\s*№|р\s*[/\\]\s*с|к\s*[/\\]\s*с|сч[её]т\w*|БИК)\D*$", prefix)
            or (re.search(r"(?i)\bБИК\b", source_text) and re.search(r"(?i)сч\.?\s*№", source_text))
        ):
            yield EntitySpan(m.group(0), m.start(), m.end(), "RU_ACCOUNT")
    for m in re.finditer(r"(?i)(?:Б\s*И\s*К|БИК|BIK)\s*[:№]?\s*(?P<value>(?:\d[\s\u00a0]*){8}\d)\b", source_text):
        yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "BIK")
    for m in re.finditer(
        r"(?i)\bИНН\s*/\s*БИК\s*:\s*\d{10}(?:\d{2})?\s*/\s*(?P<value>\d{9})(?!\d)",
        source_text,
    ):
        yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "BIK")
    for m in re.finditer(r"(?i)\b(?:И\s*Н\s*Н|ИНН)(?:\s+владельца)?\s*[:№]?\s*(?P<value>(?:\d[\s\u00a0]*){9}\d|(?:\d[\s\u00a0]*){11}\d)\b", source_text):
        yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "INN")
    for m in re.finditer(r"(?i)\b(?:О\s*Г\s*Р\s*Н\s*И\s*П|ОГРНИП)\s*[:№]?\s*(?P<value>(?:\d[\s\u00a0]*){14}\d)\b", source_text):
        val = m.group("value")
        if valid_ogrn(val):
            yield EntitySpan(val, m.start("value"), m.end("value"), "OGRNIP")
    for m in re.finditer(r"(?i)\b(?:О\s*Г\s*Р\s*Н|ОГРН)\s*[:№]?\s*(?P<value>(?:\d[\s\u00a0]*){12}\d)\b", source_text):
        val = m.group("value")
        if valid_ogrn(val):
            yield EntitySpan(val, m.start("value"), m.end("value"), "OGRN")
    for m in re.finditer(r"(?i)\b(?:К\s*П\s*П|КПП)\s*[:№]?\s*(?P<value>(?:\d[\s\u00a0]*){8}\d)\b", source_text):
        yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "KPP")
    for m in re.finditer(r"(?<!\d)\d{1,3}:\d{1,3}:\d{6,7}:\d{1,7}(?!\d)", source_text):
        yield EntitySpan(m.group(0), m.start(), m.end(), "CADASTRAL_NUMBER")
    for m in re.finditer(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", source_text):
        yield EntitySpan(m.group(0), m.start(), m.end(), "DOCUMENT_NUMBER")
    for m in re.finditer(
        r"(?i)Сертификат\s*:?\s*(?P<value>[0-9A-F]{32,64}|(?:[0-9A-F]{2}\s+){15,31}[0-9A-F]{2})",
        source_text,
    ):
        yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "DOCUMENT_NUMBER")
    for m in re.finditer(r"(?im)^(?:ON_[A-Z0-9_-]{30,}|\d+_\d{8}_[A-Z0-9_-]{30,})$", source_text):
        yield EntitySpan(m.group(0), m.start(), m.end(), "DOCUMENT_NUMBER")
    for m in re.finditer(
        r"(?i)\b(?:сч[её]т\s+на\s+оплату|плат[её]жн\w*\s+поручени\w*|основание)\s*"
        r"(?:[:№N\s]*)(?P<value>\d+(?:[/.-][A-Za-zА-Яа-яЁё0-9]+)*)",
        source_text,
    ):
        val = m.group("value").upper().strip()
        if re.search(r"\bФЗ\b|-ФЗ\b|\bФКЗ\b|-ФКЗ\b", val) or val.endswith(("ФЗ", "ФКЗ")):
            continue
        yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "DOCUMENT_NUMBER")
    for m in re.finditer(r"(?<!\w)\d{2,6}/\d+(?:-[А-Яа-яA-Za-z]-[0-9/]+)+(?!\w)", source_text):
        yield EntitySpan(m.group(0), m.start(), m.end(), "DOCUMENT_NUMBER")
    for m in re.finditer(r"(?<!\w)\d{2,6}/[А-Яа-яA-Za-z0-9/-]{5,}(?!\w)", source_text):
        yield EntitySpan(m.group(0), m.start(), m.end(), "DOCUMENT_NUMBER")
    for m in re.finditer(r"(?<!\d)\d{1,2}\s+[а-яё]{3,4}\.\s+\d{4}(?:,\s*\d{2}:\d{2})?", source_text, re.I):
        yield EntitySpan(m.group(0), m.start(), m.end(), "DATE")
    if re.search(r"(?i)\bвыписка\s+по\s+сч[её]ту\b", source_text):
        account = re.search(
            r"(?is)\bпо\s+сч[её]ту\s*(?P<value>\d[\d \t\r\n]{8,30}?)(?=\s*За\s+период)",
            source_text,
        )
        if account:
            yield EntitySpan(account.group("value").strip(), account.start("value"), account.start("value") + len(account.group("value").strip()), "RU_ACCOUNT")
        for m in re.finditer(
            r"(?m)^\d{2}\.\d{2}\.\d{4}\s+(?P<value>\d{1,8})(?=\s+\d)", source_text
        ):
            yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "DOCUMENT_NUMBER")

    # Passport scans need a fail-closed pass because OCR commonly corrupts
    # field captions, vertical series/number glyphs and the MRZ.  These rules
    # activate only when the page itself identifies a Russian passport.
    if re.search(
        r"\b(?:паспорт\s+выдан|место\s+жительства)\b",
        source_text,
        re.I,
    ):
        for m in re.finditer(r"(?im)^(?:P[<O0]?RUS|PNRUS|PO\s*\d)[A-ZА-Я0-9<«\s]{18,}$", source_text):
            yield EntitySpan(m.group(0), m.start(), m.end(), "PASSPORT")
        for m in re.finditer(r"(?<!\d)(?P<series>\d{2}\s*\d{2})\s*(?P<number>\d{6})(?!\d)", source_text):
            yield EntitySpan(m.group("series"), m.start("series"), m.end("series"), "PASSPORT_SERIES")
            yield EntitySpan(m.group("number"), m.start("number"), m.end("number"), "PASSPORT_NUMBER")
        passport_marker_positions = [
            m.start() for m in re.finditer(
                r"(?i)\b(?:паспорт|серия|номер паспорта|выдан)\b", source_text
            )
        ]
        for m in re.finditer(r"(?<!\d)\d{6}(?!\d)", source_text):
            near_passport = any(
                abs(m.start() - pos) < 80 for pos in passport_marker_positions
            )
            if near_passport:
                # Исключаем почтовые индексы (6 цифр, если перед ними индекс/почтовый/postal)
                if re.search(
                    r"(?i)(?:индекс|почтовый|postal)\s*[:.]?\s*$",
                    source_text[max(0, m.start() - 30):m.start()],
                ):
                    continue
                yield EntitySpan(m.group(0), m.start(), m.end(), "PASSPORT_NUMBER")
        for m in re.finditer(
            r"(?im)(?:код\s+подраздел\w*)\s*(?P<value>\d{3}[-\s]?\d{3}|[^\n]{3,120})$", source_text
        ):
            yield EntitySpan(m.group("value"), m.start("value"), m.end("value"), "PASSPORT_DIVISION_CODE")
        signature_block = re.search(
            r"(?is)личная\s+подпись(?P<body>.{1,260}?)(?=\b(?:пол|дата|лата)\s+рождения\b)",
            source_text,
        )
        if signature_block:
            body = signature_block.group("body")
            for name in re.finditer(r"(?m)^\s*([А-ЯЁ]{4,})\s*$", body):
                value = name.group(1)
                if value not in {"МУЖ", "ЖЕН", "РОССИЯ", "ФЕДЕРАЦИЯ"}:
                    start = signature_block.start("body") + name.start(1)
                    yield EntitySpan(value, start, start + len(value), "PER")
            for name in re.finditer(
                r"(?i)\b(?:фамили[яи]|имя|отчеств[оа])\s*[:\-]?\s*(?P<value>[А-ЯЁ]{3,})\b",
                body,
            ):
                value = name.group("value")
                start = signature_block.start("body") + name.start("value")
                yield EntitySpan(value, start, start + len(value), "PER")
        residence = re.search(r"(?im)^\s*МЕСТО\s+ЖИТЕЛЬСТВА\s*$", source_text)
        if residence and residence.end() < len(source_text):
            start = residence.end()
            while start < len(source_text) and source_text[start] in " \t\r\n":
                start += 1
            if start < len(source_text):
                yield EntitySpan(source_text[start:], start, len(source_text), "PASSPORT")


def iter_legal_identifier_spans(source_text: str) -> Iterable[EntitySpan]:
    """Compatibility helper backed by the Pullenti legal cartridge grammar.

    Application code no longer calls this function: legal identifiers arrive
    as normal ``LEGALENTITY`` referents in the Pullenti analysis result.
    """

    from pullenti_legal.analyzer import iter_candidates

    candidates = list(iter_candidates(source_text))
    granular_ranges = [
        (start, end) for label, _value, start, end in candidates
        if label in ("PASSPORT_SERIES", "PASSPORT_NUMBER")
    ]
    for label, _value, start, end in candidates:
        if label == "PASSPORT" and any(
            max(start, s) < min(end, e) for s, e in granular_ranges
        ):
            continue
        yield EntitySpan(source_text[start:end], start, end, label)


_PREFIX_PATTERNS = [
    r"(?:в\s+лице\s+)?(?:исполняющего\s+обязанности\s+генерального\s+директора|исполняющий\s+обязанности\s+генерального\s+директора|и\.о\.\s+генерального\s+директора|врио\s+генерального\s+директора|заместителя\s+генерального\s+директора|заместитель\s+генерального\s+директора|генерального\s+директора|генеральный\s+директор|заместителя\s+директора|заместитель\s+директора|директора|директор|главного\s+бухгалтера|главный\s+бухгалтер|руководителя|руководитель|президента|президент|председателя|председатель|управляющего|управляющий|начальника\s+управления|начальник\s+управления|начальника\s+отдела|начальник\s+отдела|начальника|начальник|адвоката|адвокат|нотариуса|нотариус|судьи|судья|специалиста|специалист|эксперта|эксперт|врача-эксперта|врач-эксперт|ведущего\s+юрисконсульта|ведущий\s+юрисконсульт|юрисконсульта|юрисконсульт|юриста|юрист|менеджера|менеджер)\b",
    r"(?:(?:общество\s+с\s+ограниченной\s+ответственностью|государственное\s+автономное\s+учреждение(?:\s+культуры)?(?:\s+города\s+москвы)?|[ОO0Uu]{3}|[ОO0][ОO0Uu][ОO0Uu]|ooo|oou|ouo|uoo|ouu|0оо|о00|00о|0o0|o0o|000|00u|o0u|ооо|пао|ао|ao|a0|зао|3ao|3a0|гаук|гбу|муп|гуп|фгуп|фгбу|ано|тсж|спао|оп|филиал(?:\s+[а-яё\w]+)?)\s+[«\"'\u201c\u201d\u201e][^»\"'\u201c\u201d\u201e]+[»\"'\u201c\u201d\u201e])",
    r"(?:индивидуальный\s+предприниматель|индивидуального\s+предпринимателя|ип)\b",
    r"(?:гражданин(?:ин|ка|а|ки)?(?:\s+(?:рф|россии|российской\s+федерации))?)\b",
    r"(?:представител[ья](?:\s+по\s+доверенности)?)\b",
]
_PERSON_ROLE_PREFIX = re.compile(
    r"^(?:\s*(?:" + "|".join(_PREFIX_PATTERNS) + r")\s*)+(?:[:\-–—\s/_|\\]+)?",
    re.I,
)

_TRAILING_PER_ROLE_PATTERN = re.compile(
    r"(?:,\s*|\s+[–—-]\s+|\s*[\r\n]+\s*|\s+)(?:"
    r"(?:старш\w*|ведущ\w*|главн\w*|младш\w*)\s+менеджер\w*|менеджер\w*|"
    r"исполнительн\w*\s+директор|генеральн\w*\s+директор|директор|"
    r"исполняющ\w*\s+обязанност\w*|и\.о\.|врио|заместител\w*|"
    r"руководител\w*|председател\w*|главн\w*\s+бухгалтер\w*|"
    r"начальник\w*|представител\w*|юрист\w*|адвокат\w*|"
    r"действующ\w*|проживающ\w*|зарегистрированн\w*|паспорт\w*|телефон\w*|тел\.|e-mail|email|"
    r"оп\b|филиал\w*|отдел\w*"
    r")\b",
    re.I,
)


_PERSON_NAME_PATTERNS = (
    re.compile(r"\b([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.?\s*[А-ЯЁ]\.?)$"),
    re.compile(r"\b([А-ЯЁ]\.?\s*[А-ЯЁ]\.?\s*[А-ЯЁ][а-яё]+)$"),
    re.compile(r"\b([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)$"),
)

_FULL_FIO_CANDIDATE = re.compile(
    r"(?<![А-ЯЁа-яё-])(?P<name>"
    r"[А-ЯЁ][а-яё-]{1,40}\s+"
    r"[А-ЯЁ][а-яё-]{1,40}\s+"
    r"[А-ЯЁ][а-яё-]{1,40}"
    r")(?![А-ЯЁа-яё-])"
)

_INITIAL_FIO_CANDIDATE = re.compile(
    r"(?<![А-ЯЁа-яё-])(?P<name>(?:"
    r"[А-ЯЁ][а-яё-]{1,40}\.?\s*[А-ЯЁ]\.\s*[А-ЯЁ]\.?(?=\s|[/,;]|$)"
    r"|[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё-]{1,40}"
    r"))(?![А-ЯЁа-яё-])"
)

_PLACEHOLDER_SURNAME_PATTERN = re.compile(
    r"\[ФИО(?:_\d+)?\](?P<tail>\s+[А-ЯЁ][а-яё-]{2,40})\b"
)


@lru_cache(maxsize=4096)
def _person_morph_tags(token: str) -> frozenset[str]:
    try:
        from morph_singleton import get_morph_analyzer

        analyzer = get_morph_analyzer()
        if analyzer is None:
            return frozenset()
        return frozenset(
            marker
            for parse in analyzer.parse(token)
            if parse.score >= 0.05 or "Surn" in parse.tag
            for marker in ("Name", "Surn", "Patr")
            if marker in parse.tag
        )
    except Exception:
        return frozenset()


def _is_full_fio_candidate(surface: str) -> bool:
    """Validate a three-word name by the semantic Name/Patronymic relation."""

    words = surface.split()
    if len(words) != 3:
        return False
    # Polite-address markers are discourse context, not surnames.  Some of
    # them are morphologically ambiguous with adjectives/surnames, while the
    # dedicated salutation entity already carries the actual person's name.
    if words[0].casefold() in {
        "уважаемый", "уважаемая", "дорогой", "дорогая", "господин", "госпожа",
    }:
        return False
    tags = [_person_morph_tags(word) for word in words]
    # Surname may be morphologically ambiguous with an adjective (Белая, Красивая),
    # but a following dictionary name and patronymic make the triplet safe.
    return (
        "Name" in tags[1] and "Patr" in tags[2]
    ) or (
        "Name" in tags[0] and "Patr" in tags[1] and "Surn" in tags[2]
    ) or (
        # Bulgarian and other non-patronymic three-part names often repeat
        # a family form around the given name.
        "Surn" in tags[0] and "Name" in tags[1] and "Surn" in tags[2]
    )


_SLAVIC_SURNAME_SUFFIXES = re.compile(
    r"(?i)(?:"
    r"[оеё]в|[оеё]ва|[иы]н|[иы]на|"
    r"(?:ск|цк)(?:ий|ая|ое|их|ых|ого|ому|им|ом)|"
    r"енко|онко|ко|ук|юк|чук|щук|"
    r"ич|ыч|ович|евич|овна|евна|ична|"
    r"их|ых|"
    r"ец|ик|ак|як|"
    r"(?:н|в|т|л|р|д|м|б|п|г|к|х|ж|з|с|ш|щ)(?:ий|ый|ой)"
    r")$"
)

_STRUCTURAL_DOCUMENT_WORDS = frozenset({
    "статья", "статьи", "статью", "статьей", "статье",
    "пункт", "пункта", "пункту", "пунктом", "пункте",
    "раздел", "раздела", "разделу", "разделом", "разделе",
    "часть", "части", "частью",
    "глава", "главы", "главе", "главу", "главой",
    "том", "тома", "тому", "томом", "томе",
    "таблица", "таблицы", "таблице", "таблицу", "таблицей",
    "рисунок", "рисунка", "рисунку", "рисунком", "рисунке",
    "страница", "страницы", "странице", "страницу", "страницей",
    "приложение", "приложения", "приложению", "приложением", "приложении",
})


def _is_initial_fio_candidate(surface: str) -> bool:
    words = re.findall(r"[А-ЯЁ][а-яё-]{1,40}", surface)
    if len(words) != 1:
        return False
    w = words[0]
    w_cf = w.casefold()
    if w_cf in _STRUCTURAL_DOCUMENT_WORDS:
        return False

    tags = _person_morph_tags(w)

    try:
        from morph_singleton import get_morph_analyzer
        analyzer = get_morph_analyzer()
        parses = analyzer.parse(w) if analyzer else []
    except Exception:
        parses = []

    is_geox = any("Geox" in p.tag for p in parses)
    has_surn = "Surn" in tags or any("Surn" in p.tag for p in parses)
    has_anim = any("anim" in p.tag for p in parses)

    # In Russian, a purely geographic name (e.g. "Москвы А.Б.", "России В.Г.") is not a person
    if is_geox and not has_surn:
        return False

    initials_first = bool(re.match(r"^[А-ЯЁ]\.\s*[А-ЯЁ]\.", surface))
    if initials_first:
        # Two leading initials make an unknown following surname sufficiently
        # specific. This direction must win over an overlapping geographic
        # token followed by the same initials (``Москвы А.И. Смирнов``).
        return bool(tags.intersection({"Name", "Surn", "Patr"})) or len(w) >= 4

    # Reverse order: Word И.О. (e.g. "Медик В.А.", "Иванов И.И.")
    if has_surn:
        return True
    if _SLAVIC_SURNAME_SUFFIXES.search(w):
        return True
    if has_anim:
        return True
    if any("ADJF" in p.tag and "sing" in p.tag and "masc" in p.tag and "nomn" in p.tag for p in parses):
        return True
    return False


_INSTITUTION_EPITHET_PREFIX = re.compile(
    r"(?:факультет\w*|институт\w*|школ\w*|университет\w*|академи\w*|центр\w*)"
    r"[^\n;]{0,100}?\b(?:имени|им\.)\s*[«\"']?\s*$",
    re.I,
)


def _is_institution_epithet_context(source_text: str, start: int) -> bool:
    # Word exports often wrap exactly between ``имени`` and the epithet.
    # A bounded cross-line window keeps that relation without affecting a
    # person mentioned in a later sentence.
    prefix = source_text[max(0, start - 180):start]
    return bool(_INSTITUTION_EPITHET_PREFIX.search(prefix))

_GENERIC_ORG_UNIT_PATTERNS = re.compile(
    r"^(?:(?:\b(?:управление|управления|управлению|управлением|управлении|"
    r"отдел|отдела|отделу|отделом|отделе|отделы|отделов|"
    r"служба|службы|службе|службу|службой|"
    r"комиссия|комиссии|комиссию|комиссией|"
    r"инвентаризационная\s+комиссия|инвентаризационной\s+комиссии|инвентаризационную\s+комиссию|"
    r"постоянно\s+действующая\s+инвентаризационная\s+комиссия|постоянно\s+действующей\s+инвентаризационной\s+комиссии|"
    r"департамент|департамента|департаменту|департаментом|департаменте|"
    r"комитет|комитета|комитету|комитетом|комитете|"
    r"сектор|сектора|бюро|лаборатория|лаборатории|группа|группы|цех|цеха)\b[^\n«\"]*?)\s+)"
    r"(?=(?:(?:ГАУК|МАУК|КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ГБУК|ФГБОУ|ФГАОУ|ГБОУ|МБОУ|НМИЦ|НИИ|ООО|АО|ПАО|ЗАО|ОАО|НКО|МУП|ГУП|ИП|ОП)\s+|[«\"][А-ЯЁA-Z]))",
    re.I,
)

_GENERIC_UNIT_ONLY = re.compile(
    r"^(?:(?:\b(?:"
    r"управлени(?:е|я|ю|ем|и)|отдел(?:а|у|ом|е|ы|ов)?|"
    r"служб(?:а|ы|е|у|ой)|комисси(?:я|и|ю|ей)|"
    r"департамент(?:а|у|ом|е|ы|ов)?|комитет(?:а|у|ом|е|ы|ов)?|"
    r"сектор(?:а|у|ом|е|ы|ов)?|бюро|лаборатори(?:я|и|ю|ей)|"
    r"групп(?:а|ы|е|у|ой)|цех(?:а|у|ом|е|и|ов)?|бухгалтери(?:я|и|ю|ей)|"
    r"факультет(?:а|у|ом|е|ы|ов)?|кафедр(?:а|ы|е|у|ой)|курс(?:а|у|ом|е|ы|ов)?|"
    r"совет(?:а|у|ом|е|ы|ов)?\s+директор(?:а|ов|ам|ами|ах)?|"
    r"суд(?:а|у|ом|е|ы|ов|ам|ами|ах)?"
    r")\b[^\n«\"]*)|(?:[^\n«\"]*?\b(?:"
    r"отдел(?:а|у|ом|е|ы|ов)?|служб(?:а|ы|е|у|ой)|"
    r"управлени(?:е|я|ю|ем|и)|департамент(?:а|у|ом|е|ы|ов)?|"
    r"комисси(?:я|и|ю|ей)|бухгалтери(?:я|и|ю|ей)|"
    r"факультет(?:а|у|ом|е|ы|ов)?|кафедр(?:а|ы|е|у|ой)|курс(?:а|у|ом|е|ы|ов)?|"
    r"совет(?:а|у|ом|е|ы|ов)?\s+директор(?:а|ов|ам|ами|ах)?"
    r")\b[^\n«\"]*))$",
    re.I,
)

_GOVERNANCE_PREFIX_PATTERN = re.compile(
    r"^\s*(?:совет(?:а|у|ом|е|ы|ов)?\s+директор(?:а|ов|ам|ами|ах)?)\s+"
    r"(?=(?:единоличн\w*\s+акционерн\w*\s+обществ\w*|"
    r"акционерн\w*\s+обществ\w*|общество\s+с\s+ограниченной\s+ответственностью|"
    r"ООО|ЕООО|АО|ПАО|ЗАО|ОАО|ЕАО|КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ГАУК|МАУК|ГБУК|ФГБОУ|ГБОУ|МБОУ|НМИЦ|НИИ)\b)",
    re.I,
)

_ORG_FORM_AND_QUOTE = re.compile(
    r"^(?P<prefix>(?:КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ГАУК|МАУК|ГБУК|ФГБУК|ФГБОУ|ФГАОУ|ГБОУ|МБОУ|НМИЦ|НИИ|ООО|ОO0Uu|[ОO0Uu]{3}|[ОO0][ОO0Uu][ОO0Uu]|OOO|OOU|OUO|UOO|OUU|0ОО|О00|00О|0O0|O0O|000|00U|O0U|АО|AO|A0|ПАО|ЗАО|3AO|3A0|ОАО|НКО|МУП|ГУП|ИП|ОП)\s+)"
    r"(?P<quote>[«\"])(?P<name>[^«»\"\n]+)(?P<endquote>[»\"])$",
    re.I,
)

_OPF_QUOTED_CORE_PATTERN = re.compile(
    r"^(?P<prefix>.*?\b(?:"
    r"автономн(?:ая|ой|ую|ою)\s+некоммерческ(?:ая|ой|ую|ою)\s+организаци(?:я|и|ю|ей)|"
    r"обществ(?:о|а|у|ом|е)\s+с\s+ограниченн(?:ой|ую|ою)\s+ответственност(?:ью|и)|"
    r"публичн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"непубличн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"закрыт(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"(?:краев(?:ое|ого|ому|ым|ом)|государственн(?:ое|ого|ому|ым|ом)|федеральн(?:ое|ого|ому|ым|ом)|муниципальн(?:ое|ого|ому|ым|ом))\s+(?:бюджетн(?:ое|ого|ому|ым|ом)|автономн(?:ое|ого|ому|ым|ом)|казенн(?:ое|ого|ому|ым|ом)|казённ(?:ое|ого|ому|ым|ом))\s+учреждени(?:е|я|ю|ем|и)(?:\s+(?:здравоохранения|культуры|образования|социального\s+обслуживания))?(?:\s+города\s+[А-ЯЁа-яё]+)?|"
    r"индивидуальн(?:ый|ого|ому|ым|ом)\s+предпринимател(?:ь|я|ю|ем|и)|"
    r"[ОO0Uu]{3}|[ОO0][ОO0Uu][ОO0Uu]|OOO|OOU|OUO|UOO|OUU|0ОО|О00|00О|0O0|O0O|000|00U|O0U|ООО|ЕООО|ПАО|АО|AO|A0|ЗАО|3AO|3A0|ОАО|НАО|ЕАО|"
    r"КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|"
    r"КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ГАУК|МАУК|ГБУК|ФГБУК|"
    r"ФГБОУ|ФГАОУ|ГБОУ|МБОУ|МБДОУ|МАДОУ|МДОУ|ГБДОУ|ФГБНУ|"
    r"НМИЦ|НИИ|НПЦ|"
    r"МУП|ГУП|ФГУП|АНО|НКО|ТСЖ|ТСН|СНТ|СПАО|ОП|ИП|ИII|И11|ИTT|UП"
    r")\b(?:\s+(?:[A-ZА-ЯЁ]{2,10}|проектно-строительная\s+компания))*\s*)"
    r"(?P<open>[«\"“„])(?P<name>[^«»\"'“”„\r\n]{2,})(?P<close>[»\"”])"
    r"(?P<suffix>\s*(?:\(\s*далее\b.*)?$)",
    re.I | re.S,
)


def refine_person_span(span: EntitySpan, source_text: str) -> EntitySpan:
    """Оставляет должность или статус в тексте, маскируя исключительно ФИО."""
    if span.label not in {"PER", "FOREIGN_PER"}:
        return span
    text = span.text
    new_start = span.start
    new_end = span.end
    trimmed_text = text
    foreign_title = re.match(r"(?i)^\s*(?:Mr|Mrs|Ms)\.?\s*,?\s+", trimmed_text)
    if foreign_title:
        new_start += foreign_title.end()
        trimmed_text = trimmed_text[foreign_title.end():]
        new_end = new_start + len(trimmed_text)
    citizen_prefix = re.match(r"(?i)^\s*гр\.\s*", trimmed_text)
    if citizen_prefix:
        new_start += citizen_prefix.end()
        trimmed_text = trimmed_text[citizen_prefix.end():]
        new_end = new_start + len(trimmed_text)
    company_prefix = re.match(
        r"(?i)^\s*(?:[A-Za-zА-Яа-яЁё0-9_-]+\s+)*(?:Travel|Tour|Agency|Company|Group|Тур|Тревел|Агентство|Компания)\s*[–—-]\s*",
        trimmed_text,
    )
    if company_prefix:
        new_start += company_prefix.end()
        trimmed_text = trimmed_text[company_prefix.end():]
        new_end = new_start + len(trimmed_text)
    m = _PERSON_ROLE_PREFIX.match(text)
    if m:
        prefix_len = m.end()
        remainder = text[prefix_len:]
        if len(remainder.strip()) >= 2 and re.search(r"[А-ЯЁA-Z]", remainder):
            ws_match = re.match(r"^\s*", remainder)
            ws_len = ws_match.end() if ws_match else 0
            trimmed_text = remainder[ws_len:]
            new_start = span.start + prefix_len + ws_len
            new_end = span.end

    m_sig = re.match(r"^[\s_/\-\–—\(\).]+(?=[А-ЯЁа-яёA-Za-z])", trimmed_text)
    if m_sig:
        sig_len = m_sig.end()
        remainder = trimmed_text[sig_len:]
        if len(remainder.strip()) >= 2:
            new_start += sig_len
            trimmed_text = remainder

    m_trail = _TRAILING_PER_ROLE_PATTERN.search(trimmed_text)
    if m_trail:
        trail_pos = m_trail.start()
        trimmed_text = trimmed_text[:trail_pos].rstrip(" ,;:-")
        new_end = new_start + len(trimmed_text)

    uppercase_next_line = re.search(r"\n(?=[A-Z]{3,30}\b)", trimmed_text)
    if uppercase_next_line:
        trimmed_text = trimmed_text[:uppercase_next_line.start()].rstrip()
        new_end = new_start + len(trimmed_text)
    trailing_external = re.search(r"\s+External\b", trimmed_text)
    if trailing_external:
        trimmed_text = trimmed_text[:trailing_external.start()].rstrip()
        new_end = new_start + len(trimmed_text)

    next_block = re.search(
        r"\s*\n\s*(?=(?:ул\.|улица\b|адрес\b|старший\s+менеджер\b|"
        r"менеджер\s+по\b))",
        trimmed_text,
        re.I,
    )
    if next_block:
        trimmed_text = trimmed_text[:next_block.start()].rstrip()
        new_end = new_start + len(trimmed_text)

    if re.match(r"(?is)^\s*М\.?\s*П\.?\s*[\r\n]+", trimmed_text):
        return EntitySpan("", new_start, new_start, span.label)

    return EntitySpan(trimmed_text, new_start, new_end, span.label)


_OPF_PREFIX_PATTERN = re.compile(
    r"^(?:\s*(?:"
    r"обществ(?:о|а|у|ом|е)\s+с\s+ограниченной\s+ответственностью|"
    r"публичн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"непубличн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"единоличн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"закрыт(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"открыт(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"государственн(?:ое|ого|ому|ым|ом)\s+автономн(?:ое|ого|ому|ым|ом)\s+учреждени(?:е|я|ем|и)(?:\s+культуры)?(?:\s+города\s+москвы)?|"
    r"государственн(?:ое|ого|ому|ым|ом)\s+бюджетн(?:ое|ого|ому|ым|ом)\s+учреждени(?:е|я|ем|и)|"
    r"государственн(?:ое|ого|ому|ым|ом)\s+каз[её]нн(?:ое|ого|ому|ым|ом)\s+учреждени(?:е|я|ем|и)|"
    r"ооо|еооо|пао|ао|зао|оао|нао|еао|нко|муп|гуп|фгуп|фгбу|гбу|гаук|ано|тсж|спао|оп|филиал(?:\s+[а-яё\w]+)?|"
    r"санатори(?:й|я|ю|ем|и)|ресторан(?:а|у|ом|е)?|отел(?:ь|я|ю|ем|е)|"
    r"гостиничн(?:ый|ого|ому|ым|ом)\s+комплекс(?:а|у|ом|е)?"
    r")\s*)+(?:[:\-\s]*[«\"'\u201c\u201d\u201e])",
    re.I,
)

_BARE_OPF_PREFIX_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:"
    r"обществ(?:о|а|у|ом|е)\s+с\s+ограниченной\s+ответственностью|"
    r"публичн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"единоличн(?:ое|ого|ому|ым|ом)\s+акционерн(?:ое|ого|ому|ым|ом)\s+обществ(?:о|а|у|ом|е)|"
    r"государственн(?:ое|ого|ому|ым|ом)\s+автономн(?:ое|ого|ому|ым|ом)\s+учреждени(?:е|я|ем|и)(?:\s+культуры)?(?:\s+города\s+москвы)?|"
    r"ооо|еооо|пао|ао|зао|оао|нао|еао|нко|муп|гуп|фгуп|фгбу|гбу|гаук|ано|тсж|спао|"
    r"санатори(?:й|я|ю|ем|и)|ресторан(?:а|у|ом|е)?|отел(?:ь|я|ю|ем|е)|"
    r"гостиничн(?:ый|ого|ому|ым|ом)\s+комплекс(?:а|у|ом|е)?"
    r")\s+)(?P<name>[^\n,;:()]{2,})$",
    re.I,
)


def refine_org_span(span: EntitySpan, source_text: str) -> EntitySpan:
    """Оставляет ОПФ и кавычки в тексте, маскируя исключительно бренд/наименование."""
    if span.label != "ORG":
        return span
    text = span.text
    lead_patronymic = re.match(
        r"^([А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)(?:а|у|ем|е|ы|ой)?)\s+",
        text,
    )
    if lead_patronymic:
        p_len = lead_patronymic.end()
        span = EntitySpan(text[p_len:], span.start + p_len, span.end, "ORG")
        text = span.text

    travel_company = re.search(
        r"(?i)\bтуристическ\w*\s+компани\w*\s+(?:[«\"'“„](?P<quoted>[^»\"'”]{2,})[»\"'”]|(?P<bare>[A-Za-z0-9\s&-]+))",
        text,
    )
    if travel_company:
        group = "quoted" if travel_company.group("quoted") is not None else "bare"
        raw_name = travel_company.group(group)
        leading = len(raw_name) - len(raw_name.lstrip())
        name = raw_name.strip()
        start = span.start + travel_company.start(group) + leading
        return EntitySpan(name, start, start + len(name), "ORG")
    hotel_prefix = re.match(r"(?i)^(?:отел\w*|гостиниц\w*)\s+(?=[A-Z])", text)
    if hotel_prefix:
        start = span.start + hotel_prefix.end()
        return EntitySpan(source_text[start:span.end], start, span.end, "ORG")
    trailing_location = re.search(r"(?i)\s+в\s+(?:г|п)\.\s*[А-ЯЁ][а-яё-]+\s*$", text)
    if trailing_location and re.search(r"[A-Za-z]", text[:trailing_location.start()]):
        name = text[:trailing_location.start()]
        return EntitySpan(name, span.start, span.start + len(name), "ORG")
    carrier_prefix = re.match(r"(?i)^\s*транспортн\w*\s+компани\w*\s+(?=[A-Z])", text)
    if carrier_prefix:
        start = span.start + carrier_prefix.end()
        name = text[carrier_prefix.end():]
        return EntitySpan(name, start, start + len(name), "ORG")
    quote_chars = '«»"\'“”„'
    opf_quoted = _OPF_QUOTED_CORE_PATTERN.match(text)
    if opf_quoted:
        raw_name = opf_quoted.group("name")
        generic_prefix = re.match(
            r"^(гостиничн(?:ый|ого|ому|ым|ом)\s+комплекс(?:а|у|ом|е)?\s+)(?=\S)",
            raw_name,
            re.I,
        )
        offset = generic_prefix.end() if generic_prefix else 0
        name = raw_name[offset:].strip()
        leading = len(raw_name[offset:]) - len(raw_name[offset:].lstrip())
        new_start = span.start + opf_quoted.start("name") + offset + leading
        return EntitySpan(name, new_start, new_start + len(name), "ORG")
    field_caption = re.match(
        r"^(?:(?:наименовани\w*\s+)?учреждени\w*\s+банка|"
        r"наименовани\w*\s+банка)\s*[\t ]*[\r\n]+\s*",
        text,
        re.I,
    )
    if field_caption and field_caption.end() < len(text):
        inner_start = span.start + field_caption.end()
        return refine_org_span(
            EntitySpan(source_text[inner_start:span.end], inner_start, span.end, "ORG"),
            source_text,
        )
    branch_bank = re.match(
        r"^(?P<prefix>\s*филиал(?:а|е|у|ом)?\s+[«\"'“„][^»\"'”]+[»\"'”]\s+"
        r"банк(?:а|е|у|ом)?\s+)(?P<name>.+?)(?P<suffix>\s+\((?:АО|ПАО|ОАО|ЗАО|НАО)\)\s*)$",
        text, re.I,
    )
    if branch_bank:
        raw_name = branch_bank.group("name")
        leading = len(raw_name) - len(raw_name.lstrip())
        name = raw_name.strip()
        start = span.start + branch_bank.start("name") + leading
        return EntitySpan(name, start, start + len(name), "ORG")
    if text[:1] in '«"“„' and text[-1:] in '»"”' and len(text) > 2:
        # Quotation marks are syntax, not part of an organization value.
        return EntitySpan(text[1:-1], span.start + 1, span.end - 1, "ORG")
    if (
        text[:1] in '«"“„'
        and span.end < len(source_text)
        and source_text[span.end] in '»"”'
    ):
        # Pullenti sometimes includes only the opening quote in its ORG span.
        # Keep both quote marks stable and redact the text between them.
        return EntitySpan(text[1:], span.start + 1, span.end, "ORG")
    if (
        span.start > 0
        and source_text[span.start - 1] in quote_chars
        and text[-1:] in quote_chars
        and any(char in quote_chars for char in text[:-1])
    ):
        # Malformed nested quotes are common in legal exports:
        # ЕАО "СОК "Восток".  The first and last quotes delimit the alias;
        # keep them, mask the complete value between them.
        return EntitySpan(text[:-1], span.start, span.end - 1, "ORG")
    unit = _GENERIC_ORG_UNIT_PATTERNS.match(text) or _GOVERNANCE_PREFIX_PATTERN.match(text)
    if unit:
        inner_start = span.start + unit.end()
        inner = EntitySpan(source_text[inner_start:span.end], inner_start, span.end, "ORG")
        return refine_org_span(inner, source_text)
    bank_with_opf = re.match(
        r"^(?P<prefix>\s*банк(?:а|у|ом|е)?\s+"
        r"(?:ООО|ЕООО|ПАО|АО|ЗАО|ОАО|НАО)\s+)"
        r"(?:(?P<open>[«\"'“„])(?P<quoted>.+?)(?P<close>[»\"'”])|(?P<bare>[^\n,;()]+))\s*$",
        text,
        re.I,
    )
    if bank_with_opf:
        group = "quoted" if bank_with_opf.group("quoted") is not None else "bare"
        raw_name = bank_with_opf.group(group)
        leading = len(raw_name) - len(raw_name.lstrip())
        name = raw_name.strip()
        start = span.start + bank_with_opf.start(group) + leading
        return EntitySpan(name, start, start + len(name), "ORG")
    bank = re.match(
        r"^(?P<prefix>\s*банк(?:а|у|ом|е)?\s+)"
        r"(?P<name>.+?)"
        r"(?P<suffix>\s+\((?:АО|ПАО|ОАО|ЗАО|НАО)\)\s+г\.?\s*[А-ЯЁ][А-ЯЁа-яё-]+\s*)$",
        text,
        re.I,
    )
    if bank:
        raw_name = bank.group("name")
        leading = len(raw_name) - len(raw_name.lstrip())
        name = raw_name.strip()
        start = span.start + bank.start("name") + leading
        return EntitySpan(name, start, start + len(name), "ORG")
    m = _OPF_PREFIX_PATTERN.match(text)
    if m:
        prefix_len = m.end()
        remainder = text[prefix_len:]
        alias_boundary = re.search(r'[»"”]\s*\(\s*далее\b', remainder, re.I)
        closing_quote_idx = alias_boundary.start() if alias_boundary else -1
        if closing_quote_idx < 0:
            for idx in range(len(remainder) - 1, -1, -1):
                if remainder[idx] in '»"”"':
                    closing_quote_idx = idx
                    break
        if closing_quote_idx > 0:
            core_name = remainder[:closing_quote_idx]
            new_start = span.start + prefix_len
            new_end = new_start + closing_quote_idx
            return EntitySpan(core_name, new_start, new_end, "ORG")
    bare = _BARE_OPF_PREFIX_PATTERN.match(text)
    if bare:
        raw_name = bare.group("name")
        leading = len(raw_name) - len(raw_name.lstrip())
        name = raw_name.strip()
        if name:
            new_start = span.start + bare.start("name") + leading
            return EntitySpan(name, new_start, new_start + len(name), "ORG")
    return span


def refine_contact_span(span: EntitySpan, source_text: str) -> EntitySpan:
    """Удаляет лидирующие метки (Телефон:, E-mail:, Сайт:) из контактных данных."""
    if span.label not in {"PHONE_NUMBER", "EMAIL", "WEBSITE"}:
        return span
    text = span.text
    m = re.match(
        r"^(?:(?:контактный\s+)?телефон(?:\s+для\s+связи|\s+бухгалтерии|\s+отдела\s+кадров|\s+диспетчера|\s+юриста|\s+горячей\s+линии)?|"
        r"тел\.|факс|e-mail|email|почта(?:\s+технической\s+поддержки)?|электронная\s+почта(?:\s+для\s+направления\s+претензий|\s+заказчика)?|"
        r"сайт(?:\s+туроператора|\s+учреждения)?|официальный\s+сайт|интернет-ресурс|интернет-сайт|портал\s+государственных\s+услуг|web|url|адрес\s+сайта)[:\s\-–—]+",
        text,
        re.I,
    )
    if m:
        prefix_len = m.end()
        remainder = text[prefix_len:]
        if len(remainder.strip()) >= 3:
            ws_match = re.match(r"^\s*", remainder)
            ws_len = ws_match.end() if ws_match else 0
            trimmed = remainder[ws_len:]
            new_start = span.start + prefix_len + ws_len
            return EntitySpan(trimmed, new_start, span.end, span.label)
    return span


def select_non_overlapping(spans: Iterable[EntitySpan]):
    """Разрешает пересечения только по координатам: сохраняет самый длинный span."""

    selected = []
    for span in sorted(spans, key=lambda item: (-item.length, item.start, item.label)):
        if any(max(span.start, other.start) < min(span.end, other.end) for other in selected):
            continue
        selected.append(span)
    return sorted(selected, key=lambda item: (item.start, item.end))


_ADDRESS_KEYWORD_TOKENS = (
    r"д\.|дом|к\.|корп\.|корпус|кв\.|квартира|кв-л|квартал|стр\.|строение|"
    r"оф\.?|офис|ком\.?|комната|э\.?|эт\.|этаж|помещение|помещ\.|пом\.|пом[/\\.]*оф\w*|"
    r"литера|лит\.|внутригородск[а-яё]*|территория|поселение|поселок|пос\.|деревня|дер\.|село|с\.|"
    r"район|р-н|г\.|гор\.|город|пер\.|переулок|ул\.|улица|пр-кт|проспект|ш\.|шоссе|"
    r"street|st\.|avenue|ave\.|road|rd\.|blvd|bldg|building|apt|suite|ste|floor|fl\.|unit"
)
_ADDRESS_NUM_TOKEN = (
    r"(?:[№#]\s*)?[0-9IVXLCDM]+(?:[/\\.-][0-9IVXLCDM]+)*(?:[А-ЯЁа-яёA-Za-z])?|"
    r"\((?:один|два|три|четыре|пять|шесть|семь|восемь|девять|десять)\)|"
    r"лит(?:ера|\.)?\s*[А-ЯЁA-Z]"
)
_ADDRESS_CONNECTOR_PATTERN = re.compile(
    rf"^[\s,;./\-\–—]*(?:(?:{_ADDRESS_KEYWORD_TOKENS}|{_ADDRESS_NUM_TOKEN})\b[\s,;./\-\–—]*)*$",
    re.I,
)


_MONTH_NAMES = {
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
}


_ADDRESS_TERMINATORS = (
    r"(?:\b(?:Адрес\s*:?|И\s*Н\s*Н|ИНН|К\s*П\s*П|КПП|О\s*Г\s*Р\s*Н|ОГРН|БИК|р\s*[/\\]\s*с|к\s*[/\\]\s*с|л\s*[/\\]\s*с|сч\.?\s*№|Банк\s*:?|Заказчик|Исполнитель|Подрядчик|Генеральн\w*|Директор|тел|email|e-mail|сайт)\b)"
)

_ADDRESS_KEYWORDS = (
    r"(?:\b(?:дом|д|владение|вл|строение|стр|корпус|корп|к|литера|лит|секция|секц|квартира|кв|офис|оф|помещение|помещ|пом|комната|комн|ком|кабинет|каб|этаж|эт|э|бокс|павильон|пав|подъезд|под|подвал|цокол[ья]|антресол[ьи]|мансард[аы]|блок|цех|склад|гараж|уч|участок)\b)"
)

_ADDRESS_VAL = (
    r"(?:(?!" + _ADDRESS_TERMINATORS + r"|" + _ADDRESS_KEYWORDS + r")[0-9IVXLCDMА-ЯЁа-яёA-Za-z№#()]+)"
)

_ADDRESS_NUM = (
    r"(?:" + _ADDRESS_VAL + r"(?:\s*[/\\.,\-–—]+\s*" + _ADDRESS_VAL + r")*)"
)

_ADDRESS_SUFFIX_SEGMENT = re.compile(
    r"^[\s,;./\-\–—]*(?!" + _ADDRESS_TERMINATORS + r")(?:"
    r"(?:"
    r"(?:пом[а-яё]*|помещ[а-яё]*|кв[а-яё]*|ком[а-яё]*|оф[а-яё]*)\.?\s*[/\\|.,]+\s*(?:оф[а-яё]*|ком[а-яё]*|пом[а-яё]*)\.?"
    r"|(?:г\.\s*|гор\.\s*|город\s*)?(?:дом|д\.|владение|вл\.|строение|стр\.|корпус|корп\.|к\.|литера|лит\.|секция|секц\.|квартира|кв\.|офис|оф\.?|помещение|помещ\.|пом\.|комната|комн\.|ком\.?|кабинет|каб\.|этаж|эт\.|э\.?|бокс|павильон|пав\.|подъезд|под\.|подвал|цокол[ья]|антресол[ьи]|мансард[аы]|вн\.тер\.\s*г\.|муниципальный\s+округ|поселение|пос\.|кв-л|квартал|блок|цех|склад|гараж|уч\.|участок)"
    r"|building|bldg|floor|fl\.|suite|ste|office|off|apt|room"
    r")"
    r"[\s.:/\-–—]*"
    r"(?:" + _ADDRESS_NUM + r")"
    r"|"
    r"\d+[А-ЯЁа-яёA-Za-z]?(?=\s*(?:,|/|\s+(?:квартира|кв\.|офис|оф\.?|помещение|пом\.|комната|ком\.?|этаж|эт\.|э\.?|корпус|корп\.|к\.)))"
    r"|"
    r"\d+/\d+"
    r"|"
    r"\d{6}(?!\d)"
    r"|"
    r"(?:(?:пом[а-яё]*|помещ[а-яё]*|кв[а-яё]*|ком[а-яё]*|оф[а-яё]*)\.?\s*[/\\|.,]+\s*(?:оф[а-яё]*|ком[а-яё]*|пом[а-яё]*)\.?|пом\.|помещ\.|ком\.?|комн\.|оф\.?|кв\.|э\.?|эт\.|стр\.|корп\.|к\.|д\.|вл\.)"
    r")",
    re.I
)

_ADDRESS_FIELD_PREFIX = re.compile(
    r"^\s*(?:(?:юридическ\w*|фактическ\w*|почтов\w*)\s+)?"
    r"адрес(?:у)?(?:\s+(?:регистрации|места\s+(?:нахождения|жительства)))?\s*:\s*",
    re.I,
)


def refine_address_span(span: EntitySpan) -> EntitySpan:
    """Keep an address field caption while redacting its complete value."""

    if span.label != "ADDRESS":
        return span
    marker = _ADDRESS_FIELD_PREFIX.match(span.text)
    if marker and marker.end() < len(span.text):
        return EntitySpan(
            span.text[marker.end():], span.start + marker.end(), span.end, "ADDRESS"
        )
    return span


def extend_address_postal_prefix(source_text: str, span: EntitySpan) -> EntitySpan:
    """Include a six-digit postal index immediately preceding an address."""

    if span.label != "ADDRESS":
        return span
    line_start = source_text.rfind("\n", 0, span.start) + 1
    prefix = source_text[line_start:span.start]
    postal = re.search(r"(?<!\d)\d{6}\s*,\s*$", prefix)
    if postal is None:
        return span
    new_start = line_start + postal.start()
    return EntitySpan(source_text[new_start:span.end], new_start, span.end, "ADDRESS")


def extend_address_house_suffix(source_text: str, span: EntitySpan) -> EntitySpan:
    """Расширяет адресный спан на идущие следом номер дома/строения/офиса/квартала/помещения/комнаты."""
    if span.label != "ADDRESS":
        return span
    cur_end = span.end
    while cur_end < len(source_text):
        after = source_text[cur_end:]
        m = _ADDRESS_SUFFIX_SEGMENT.match(after)
        if not m:
            break
        matched_str = m.group(0)
        if "\n\n" in matched_str or "\r\n\r\n" in matched_str:
            break
        if ("\n" in matched_str or "\r" in matched_str) and not any(c.isdigit() for c in matched_str):
            break
        # Проверяем, не является ли следующий токен месяцем (например, 10 февраля)
        rest_after = after[m.end():].lstrip()
        first_word = rest_after.split()[0].rstrip(".,;").casefold() if rest_after.split() else ""
        if first_word in _MONTH_NAMES:
            break
        cur_end += m.end()

    if cur_end > span.end:
        clean_end = cur_end
        while clean_end > span.start and source_text[clean_end - 1] in " \t\r\n,;:-":
            clean_end -= 1
        return EntitySpan(source_text[span.start:clean_end], span.start, clean_end, "ADDRESS")
    return span


def _is_valid_address_span(source_text: str, span: EntitySpan) -> bool:
    if is_sufficient_address(span.text):
        return True
    prefix_window = source_text[max(0, span.start - 80):span.start]
    return bool(re.search(
        r"(?i)(?:юридическ(?:ий|ого)\s+адрес|почтов(?:ый|ого)\s+адрес|фактическ(?:ий|ого)\s+адрес|"
        r"адрес\s+(?:регистрации|места\s+(?:нахождения|жительства))|"
        r"место\s+нахождения\s+и\s+адрес(?:\s+(?:общества|организации|юридического\s+лица|клиента|гражданина))?|"
        r"место\s+(?:нахождения|жительства|регистрации)|по\s+адресу|адрес(?:у)?)\s*[:\-–—]?\s*$",
        prefix_window
    ))


def merge_contiguous_address_spans(source_text: str, spans: Iterable[EntitySpan]) -> list[EntitySpan]:
    """Объединяет смежные адресные сегменты (индекс, город, улицу, дом, квартал) в единый [Адрес]."""
    spans_list = list(spans)
    if not spans_list:
        return spans_list
    merged = []
    current = None
    for span in sorted(spans_list, key=lambda s: (s.start, s.end)):
        if span.label == "ADDRESS":
            span = extend_address_house_suffix(
                source_text,
                extend_address_postal_prefix(source_text, refine_address_span(span)),
            )
            if current is None:
                current = span
            else:
                if span.start <= current.end:
                    union_end = max(current.end, span.end)
                    current = EntitySpan(
                        source_text[current.start:union_end], current.start, union_end, "ADDRESS"
                    )
                else:
                    between = source_text[current.end:span.start]
                    has_stop_context = bool(
                        re.search(
                            r"(?i)\b(?:доверенност\w*|уполномоч\w*|зарегистрирован\w*|проживающ\w*|паспорт\w*|договор\w*|именуем\w*|действующ\w*|гражданин\w*)\b|[.!?]\s+[А-ЯЁ]",
                            between,
                        )
                    )
                    has_city_current = bool(re.search(r"(?i)\b(?:г\.|гор\.|город|пос\.|деревня|село|пгт)\b", current.text))
                    has_city_span = bool(re.search(r"(?i)\b(?:г\.|гор\.|город|пос\.|деревня|село|пгт)\b", span.text))
                    if (
                        not has_stop_context
                        and not (has_city_current and has_city_span)
                        and _ADDRESS_CONNECTOR_PATTERN.fullmatch(between)
                        and len(between) <= 120
                    ):
                        current = EntitySpan(
                            source_text[current.start:span.end], current.start, span.end, "ADDRESS"
                        )
                    else:
                        if _is_valid_address_span(source_text, current):
                            merged.append(current)
                        current = span
        else:
            if current is not None:
                if _is_valid_address_span(source_text, current):
                    merged.append(current)
                current = None
            merged.append(span)
    if current is not None:
        if _is_valid_address_span(source_text, current):
            merged.append(current)
    return merged


def split_parenthetical_org_spans(source_text: str, spans: list[EntitySpan]) -> list[EntitySpan]:
    """Разделяет публичный орган-получатель и организацию в скобках, например: Департамент финансов (ГАУК 'МОСГОРТУР')."""
    result = []
    for span in spans:
        if span.label == "ORG" and "(" in span.text:
            # Pullenti sometimes returns one ORG spanning two quoted aliases,
            # e.g. ``Напитки ТрансСервис» (ООО «НТС``. Replacing that union
            # destroys the legal form, parentheses and quotes. Split only the
            # two name cores and keep all structural punctuation verbatim.
            aliases = re.search(
                r"(?P<left>[^«»\"\n]{2,})[»\"]\s*\(\s*"
                r"(?:ООО|АО|ПАО|ЗАО|ОАО|НАО|ЕАО|КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ГАУК|МАУК|ГБУК|ФГБОУ|ФГАОУ|ГБОУ|МБОУ|НМИЦ|НИИ|АНО|НКО|МУП|ГУП|ИП|ОП)\s+"
                r"[«\"](?P<right>[^«»\"\n]{2,})[»\"]?\s*\)?$",
                span.text,
                re.I,
            )
            if aliases:
                for group in ("left", "right"):
                    item_start = span.start + aliases.start(group)
                    result.append(EntitySpan(
                        aliases.group(group), item_start,
                        span.start + aliases.end(group), "ORG"
                    ))
                continue
            m = re.search(r"\(([^)]+)\)?", span.text)
            if m:
                before_paren = span.text[:m.start()].strip()
                if _PUBLIC_AUTHORITY_PATTERN.search(before_paren):
                    inner_text = m.group(1).strip()
                    inner_start = span.start + m.start(1)
                    inner_end = inner_start + len(inner_text)
                    result.append(EntitySpan(inner_text, inner_start, inner_end, "ORG"))
                    continue
        result.append(span)
    return result


_QUOTED_ORG_PATTERN = re.compile(
    r"\b(?:(?:"
    r"автономная\s+некоммерческая\s+организация|"
    r"общество\s+с\s+ограниченной\s+ответственностью|"
    r"публичное\s+акционерное\s+общество|"
    r"непубличное\s+акционерное\s+общество|"
    r"акционерное\s+общество|"
    r"закрытое\s+акционерное\s+общество|"
    r"единоличное\s+акционерное\s+общество|"
    r"(?:краевое|государственное|федеральное|муниципальное)\s+(?:бюджетное|автономное|казенное|казённое)\s+учреждение(?:\s+(?:здравоохранения|культуры|образования|социального\s+обслуживания))?(?:\s+города\s+[А-ЯЁа-яё]+)?|"
    r"индивидуальный\s+предприниматель|"
    r"[ОO0Uu]{3}|[ОO0][ОO0Uu][ОO0Uu]|ooo|oou|ouo|uoo|ouu|0оо|о00|00о|0o0|o0o|000|00u|o0u|"
    r"ооо|еооо|пао|ао|ao|a0|зао|3ao|3a0|оао|нао|еао|"
    r"кгбуз|гбуз|фгбуз|мбуз|гауз|мауз|гкуз|кгкуз|буз|фбуз|"
    r"кгбу|гбу|фгбу|мбу|гау|мау|гку|кгку|фгку|мку|гаук|маук|гбук|фгбук|"
    r"фгбоу|фгаоу|гбоу|мбоу|мбдоу|мадоу|мдоу|гбдоу|фгбну|"
    r"нмиц|нии|нпц|"
    r"муп|гуп|фгуп|ано|нко|тсж|тсн|снт|спао|оп|ип|иii|и11|иtt|uп|"
    r"филиал(?:\s+[а-яё\w]+)?"
    r")"
    r"(?:\s+(?:[A-ZА-ЯЁ]{2,10}|проектно-строительная\s+компания))*\s+)"
    r"([«\"'\u201c\u201d\u201e][^«»\"'\u201c\u201d\u201e\r\n]{2,}[»\"'\u201c\u201d\u201e])",
    re.I,
)

_ORG_ALIAS_QUOTED_PATTERN = re.compile(
    r"\b(?:далее\s*[–—-]\s*)?(?:[ОO0Uu]{3}|[ОO0][ОO0Uu][ОO0Uu]|OOO|OOU|OUO|UOO|OUU|0ОО|О00|00О|0O0|O0O|000|00U|O0U|ООО|ЕООО|АО|AO|A0|ПАО|ЗАО|3AO|3A0|ОАО|НАО|ЕАО|КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ГАУК|МАУК|ГБУК|ФГБОУ|ФГАОУ|ГБОУ|МБОУ|НМИЦ|НИИ|МУП|ГУП|АНО|НКО|ИП)\s+"
    r"[«\"'\u201c\u201d\u201e](?P<name>[^«»\"'\u201c\u201d\u201e\r\n]{2,})[»\"'\u201c\u201d\u201e]"
    r"(?=\s*(?:[,;)]|$|[«\"'\u201c\u201d\u201e]|(?:и|или|либо)\s+(?:[ОO0Uu]{3}|OOO|OOU|ООО|ЕООО|АО|AO|ПАО|ЗАО|3AO|ОАО|НАО|ЕАО|КГБУЗ|ГБУЗ|ГАУК|ГБУ)\b))",
    re.I,
)

_DESCRIPTOR_QUOTED_ORG_PATTERN = re.compile(
    r"\b(?:санатори(?:й|я|ю|ем|и)|ресторан(?:а|у|ом|е)?|"
    r"отел(?:ь|я|ю|ем|е)|гостиничн(?:ый|ого|ому|ым|ом)\s+комплекс(?:а|у|ом|е)?)"
    r"\s+[«\"'\u201c\u201d\u201e][^«»\"'\u201c\u201d\u201e\n]+[»\"'\u201c\u201d\u201e]",
    re.I,
)

_DESCRIPTOR_INSIDE_QUOTE_PATTERN = re.compile(
    r"[«\"'\u201c\u201d\u201e](?:гостиничн(?:ый|ого|ому|ым|ом)\s+комплекс(?:а|у|ом|е)?\s+)"
    r"(?P<name>[^«»\"'\u201c\u201d\u201e\n]+)[»\"'\u201c\u201d\u201e]",
    re.I,
)

_CONSECUTIVE_QUOTED_NAME_PATTERN = re.compile(
    r"[»\"'\u201c\u201d\u201e]\s+[«\"'\u201c\u201d\u201e]"
    r"(?P<name>[^«»\"'\u201c\u201d\u201e\n]{2,})[»\"'\u201c\u201d\u201e]"
)

_QUOTED_DESCRIPTOR_TITLE_PATTERN = re.compile(
    r"[«\"'\u201c\u201d\u201e](?P<name>"
    r"(?:санаторно-оздоровительн\w*|гостиничн\w*|курортн\w*)\s+"
    r"комплекс\w*\s+[«\"'\u201c\u201d\u201e]"
    r"[^«»\"'\u201c\u201d\u201e\n]{2,}[»\"'\u201c\u201d\u201e])",
    re.I,
)

_BARE_COMPANY_GROUP_PATTERN = re.compile(
    r"\b(?:ГК|[Гг]руппа\s+компаний)\s+"
    r"(?P<name>[А-ЯЁ][A-Za-zА-Яа-яЁё0-9-]{2,}"
    r"(?:\s+[А-ЯЁ][A-Za-zА-Яа-яЁё0-9-]{1,}){0,4})"
    r"(?=\s*(?:[)\n,;.]|$))"
)

_TRAILING_CORPORATE_DESCRIPTOR_PATTERN = re.compile(
    r"(?m)^(?P<name>"
    r"[A-ZА-ЯЁ][A-ZА-ЯЁ0-9-]{2,}"
    r"(?:\s+[A-ZА-ЯЁ][A-ZА-ЯЁ0-9-]{1,}){0,3}"
    r",\s*(?:[Гг]руппа|[Кк]омпания))(?=\s*(?:\n|$))"
)

_EDUCATIONAL_LEGAL_ENTITY_PATTERN = re.compile(
    r"(?im)^(?P<name>"
    r"(?:[А-ЯЁ][А-ЯЁа-яё-]*\s+){0,4}"
    r"(?:академи(?:я|и|ю|ей)|университет(?:а|у|ом|е)?|институт(?:а|у|ом|е)?)"
    r"[^\n,;]{2,220})(?=,|\n|$)"
)

_INSTITUTION_QUOTED_EPONYM_PATTERN = re.compile(
    r"\b(?:школ(?:а|ы|е|у|ой)|университет(?:а|у|ом|е)?|"
    r"академи(?:я|и|ю|ей)|институт(?:а|у|ом|е)?|центр(?:а|у|ом|е)?)"
    r"[^;]{0,100}?\bимени\s*[\r\n\t ]*[«\"“„]"
    r"(?P<name>[^«»\"“„”\n]{2,})[»\"”]",
    re.I,
)

_CONTEXTUAL_PERSON_PATTERNS = (
    re.compile(r"\b(?:менеджер|представитель|исполнитель|заказчик)\s*:\s*(?P<name>[А-ЯЁ][а-яё-]{2,})\b", re.I),
    re.compile(r"[/|]\s*(?P<name>[А-ЯЁ][а-яё-]{2,})\b"),
    re.compile(r"\b(?:вернуть|выплатить|перечислить)\s+(?P<name>[А-ЯЁ][а-яё-]{2,})\b", re.I),
    re.compile(r"\b(?P<name>[А-ЯЁ][а-яё-]{2,})\s+(?:будет|был|была)\s+(?:вынужден|вынуждена|получен|получена)\b"),
    re.compile(r"(?i)\bза\s+туристов\s+(?P<name>[А-ЯЁ][а-яё-]{2,})\b"),
)

_UPPERCASE_FIO_LINE = re.compile(r"(?m)^(?P<name>[A-Z]{3,30}[ \t]+[A-Z]{3,30})[ \t]*$")
_STAR_HOTEL_NAME = re.compile(
    r"(?m)\b(?P<name>[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,}){1,5})\s+[1-5]\*(?=\s|$)"
)
_TRAVEL_BRAND_NAME = re.compile(r"\b(?P<name>[A-Z][a-z]{2,30}\s+[Tt]ravel)\b")
_IP_MULTILINE_FIO = re.compile(
    r"(?i)\bИП\s+(?P<name>[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s*\n\s*[А-ЯЁ][а-яё-]+)\b"
)

_CARRIER_ORG_PATTERN = re.compile(
    r"(?i)\b(?:перевозчик\w*|авиаперевозчик\w*|авиакомпани\w*|рейс\w*|авиалини\w*)"
    r"\s+([«\"'\u201c\u201d\u201e]?)(?P<name>[A-Z][A-Za-z][A-Za-z0-9\s&.-]{0,39}[A-Za-z])([»\"'\u201c\u201d\u201e]?)",
)

_HOTEL_ORG_PATTERN = re.compile(
    r"(?i)\b(?:отел\w*|гостиниц\w*|курорт\w*|пансионат\w*|санатори\w*)"
    r"\s+([«\"'\u201c\u201d\u201e]?)(?P<name>[A-Z][A-Za-z0-9\s&.-]{1,50}[A-Za-z0-9])(?=\s+\d+\*|\s*[»\"'\u201c\u201d\u201e]|\s*\(|\s*$)",
)

_FOREIGN_PERSON_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"турист\w*|пассажир\w*|клиент\w*|гост\w*|покупател\w*|заказчик\w*|представител\w*|"
    r"гр\.?|гражданин\w*|гражданк\w*|фио|fio|mr\.?|mrs\.?|ms\.?"
    r")\s*[:\s-]*"
    r"(?P<name>[A-Z][a-z]{1,25}(?:\s+[A-Z][a-z]{1,25}){1,2})\b"
)


@lru_cache(maxsize=2048)
def _is_contextual_person_token(token: str) -> bool:
    if _ANY_ORG_FORM_PATTERN.fullmatch(token.strip()):
        return False
    try:
        from morph_singleton import get_morph_analyzer

        analyzer = get_morph_analyzer()
        if analyzer is None:
            return False
        parses = analyzer.parse(token)
    except Exception:
        return False
    return any(
        parse.score >= 0.05 and any(marker in parse.tag for marker in ("Name", "Surn", "Patr"))
        for parse in parses
    )


def refine_composite_spans(source_text: str, spans: Iterable[EntitySpan]):
    """Отделяет ядро сущности от вложенных контактов и префиксов должностей."""

    spans_list = list(spans)
    for m in _QUOTED_ORG_PATTERN.finditer(source_text):
        full_org = m.group(0).strip()
        quoted = _quoted_value(full_org)
        if quoted is not None and _GENERIC_QUOTED_UNIT_NAME.fullmatch(quoted):
            continue
        s_start = m.start()
        s_end = s_start + len(full_org)
        if not any(s.label == "ORG" and s.start <= s_start and s_end <= s.end for s in spans_list):
            spans_list.append(EntitySpan(full_org, s_start, s_end, "ORG"))
        tail = source_text[m.end():]
        following = re.match(
            r"\s+[«\"'\u201c\u201d\u201e](?P<name>[^«»\"'\u201c\u201d\u201e\n]{2,})[»\"'\u201c\u201d\u201e]",
            tail,
        )
        if following:
            start = m.end() + following.start("name")
            name = following.group("name")
            spans_list.append(EntitySpan(name, start, start + len(name), "ORG"))

    for m in _DESCRIPTOR_QUOTED_ORG_PATTERN.finditer(source_text):
        quoted = _quoted_value(m.group(0))
        if quoted is None or not _GENERIC_QUOTED_UNIT_NAME.fullmatch(quoted):
            spans_list.append(EntitySpan(m.group(0), m.start(), m.end(), "ORG"))

    for m in _DESCRIPTOR_INSIDE_QUOTE_PATTERN.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "ORG"))

    for m in _CONSECUTIVE_QUOTED_NAME_PATTERN.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "ORG"))

    for m in _QUOTED_DESCRIPTOR_TITLE_PATTERN.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "ORG"))

    for pattern in (
        _BARE_COMPANY_GROUP_PATTERN,
        _TRAILING_CORPORATE_DESCRIPTOR_PATTERN,
        _EDUCATIONAL_LEGAL_ENTITY_PATTERN,
        _INSTITUTION_QUOTED_EPONYM_PATTERN,
    ):
        for m in pattern.finditer(source_text):
            spans_list.append(EntitySpan(
                m.group("name"), m.start("name"), m.end("name"), "ORG"
            ))

    for m in _CARRIER_ORG_PATTERN.finditer(source_text):
        name = m.group("name").strip()
        lbl = "FOREIGN_ORG" if not re.search(r"[А-Яа-яЁё]", name) else "ORG"
        spans_list.append(EntitySpan(name, m.start("name"), m.end("name"), lbl))

    for m in _HOTEL_ORG_PATTERN.finditer(source_text):
        name = m.group("name").strip()
        lbl = "FOREIGN_ORG" if not re.search(r"[А-Яа-яЁё]", name) else "ORG"
        spans_list.append(EntitySpan(name, m.start("name"), m.end("name"), lbl))

    for m in _STAR_HOTEL_NAME.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "FOREIGN_ORG"))

    for m in _TRAVEL_BRAND_NAME.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "FOREIGN_ORG"))

    for m in _IP_MULTILINE_FIO.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "PER"))

    for m in _FOREIGN_PERSON_PATTERN.finditer(source_text):
        spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "FOREIGN_PER"))

    for m in _FULL_FIO_CANDIDATE.finditer(source_text):
        covered_by_person = any(
            span.label in ("PER", "FOREIGN_PER") and span.start <= m.start("name") and m.end("name") <= span.end
            for span in spans_list
        )
        if not covered_by_person and _is_full_fio_candidate(m.group("name")):
            spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "PER"))

    for m in _INITIAL_FIO_CANDIDATE.finditer(source_text):
        prefix = source_text[max(0, m.start("name") - 12):m.start("name")]
        if re.search(r"\bим\.\s*$", prefix, re.I) or _is_institution_epithet_context(
            source_text, m.start("name")
        ):
            continue
        signature_context = bool(re.match(
            r"(?i)\s*/\s*\n?\s*ФИО\b", source_text[m.end("name"):m.end("name") + 30]
        ))
        if _is_initial_fio_candidate(m.group("name")) or (
            signature_context and re.match(r"(?i)^[А-ЯЁ][а-яё-]{3,}ка\s+[А-ЯЁ]\.\s*[А-ЯЁ]", m.group("name"))
        ):
            spans_list.append(EntitySpan(
                m.group("name"), m.start("name"), m.end("name"), "PER"
            ))

    for m in _PLACEHOLDER_SURNAME_PATTERN.finditer(source_text):
        surname = m.group("tail").strip()
        if "Surn" in _person_morph_tags(surname):
            spans_list.append(EntitySpan(
                m.group(0), m.start(), m.end(), "PER"
            ))

    for pattern in _CONTEXTUAL_PERSON_PATTERNS:
        for m in pattern.finditer(source_text):
            if "туристов" in m.group(0).casefold() or _is_contextual_person_token(m.group("name")):
                spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "PER"))

    for m in _UPPERCASE_FIO_LINE.finditer(source_text):
        preceding = source_text[max(0, m.start() - 100):m.start()]
        if re.search(r"(?i)\b(?:ФИО|Pax\s+Name)\b[^\n]*(?:\n[A-Z ]+){0,5}\n$", preceding):
            spans_list.append(EntitySpan(m.group("name"), m.start("name"), m.end("name"), "FOREIGN_PER"))

    spans_list = [
        span for span in spans_list
        if not (
            span.label == "PER"
            and _is_institution_epithet_context(source_text, span.start)
        )
        and not (
            span.label == "ORG"
            and ("…" in span.text or "..." in span.text)
        )
        and not (
            span.label in {"ORG", "FOREIGN_ORG"}
            and re.search(
                r"(?i)\b(?:(?:многофункциональн\w*\s+)?центр\w*\s+предоставления\s+государственных|"
                r"почта\s+россии)\b",
                span.text,
            )
        )
    ]
    converted_spans = []
    for span in spans_list:
        if span.label in {"PER", "FOREIGN_PER"} and re.search(
            r"(?i)\b(?:travel|tour|tours|voyage|resort|holding|тревел|трэвел|тур|туры|вояж|резорт|холдинг)\b",
            span.text,
        ):
            converted_spans.append(
                EntitySpan(
                    span.text, span.start, span.end,
                    "ORG" if any(ord(c) > 127 for c in span.text) else "FOREIGN_ORG"
                )
            )
        else:
            converted_spans.append(span)
    spans_list = converted_spans
    spans_list = split_parenthetical_org_spans(source_text, spans_list)
    spans_list = [refine_person_span(s, source_text) for s in spans_list]
    spans_list = [refine_org_span(s, source_text) for s in spans_list]
    refined = []
    for span in spans_list:
        # An explicit full address may legitimately contain names that the
        # generic analyzer also exposes as ORG (hotel, settlement, complex).
        # The strong ADDRESS field must stay atomic instead of being truncated
        # at the first nested organization.
        nested_starts = [
            other.start for other in spans_list
            if other is not span
            and other.label != span.label
            and span.start < other.start < span.end
            and other.end <= span.end
        ] if span.label in ("ORG", "PER") else []
        if nested_starts:
            end = min(nested_starts)
            while end > span.start and source_text[end - 1] in " \t\r\n,;:-":
                end -= 1
            if end > span.start:
                span = EntitySpan(source_text[span.start:end], span.start, end, span.label)
        refined.append(span)

    relabelled = []
    for s in refined:
        text_val = s.text
        has_latin = bool(re.search(r"[A-Za-z]", text_val))
        has_cyrillic = bool(re.search(r"[А-Яа-яЁё]", text_val))
        if s.label in ("PER", "FOREIGN_PER"):
            if has_latin and not has_cyrillic:
                relabelled.append(EntitySpan(s.text, s.start, s.end, "FOREIGN_PER"))
            else:
                relabelled.append(EntitySpan(s.text, s.start, s.end, "PER"))
        elif s.label in ("ORG", "FOREIGN_ORG"):
            if has_latin and not has_cyrillic:
                relabelled.append(EntitySpan(s.text, s.start, s.end, "FOREIGN_ORG"))
            else:
                relabelled.append(EntitySpan(s.text, s.start, s.end, "ORG"))
        else:
            relabelled.append(s)
    return merge_contiguous_address_spans(source_text, relabelled)


def initialize_ner():
    """Инициализирует только анализаторы, нужные для обезличивания.

    ``Sdk.initialize_all()`` в PullentiPython 0.1 загружает в том числе модуль
    нормативных актов. В опубликованной сборке этот модуль несовместим с её же
    базовым классом ``Termin`` и роняет весь NER. Для PERSON/ORG/ADDRESS он не
    нужен, поэтому используем штатную выборочную инициализацию Pullenti.
    """

    from pullenti.morph.MorphLang import MorphLang
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.address.AddressAnalyzer import AddressAnalyzer
    from pullenti.ner.bank.BankAnalyzer import BankAnalyzer
    from pullenti.ner.geo.GeoAnalyzer import GeoAnalyzer
    from pullenti.ner.org.OrganizationAnalyzer import OrganizationAnalyzer
    from pullenti.ner.person.PersonAnalyzer import PersonAnalyzer
    from pullenti.ner.phone.PhoneAnalyzer import PhoneAnalyzer
    from pullenti.ner.uri.UriAnalyzer import UriAnalyzer
    from pullenti_legal import initialize as initialize_legal_entities

    ProcessorService.initialize(MorphLang.RU | MorphLang.EN)
    # The legal cartridge must see original lexical tokens.  If it runs after
    # OrganizationAnalyzer, a company metatoken can swallow an adjacent INN and
    # Pullenti will correctly (but too broadly for redaction) attach that whole
    # company span to the legal referent.
    initialize_legal_entities()
    GeoAnalyzer.initialize()
    AddressAnalyzer.initialize()
    OrganizationAnalyzer.initialize()
    PersonAnalyzer.initialize()
    UriAnalyzer.initialize()
    PhoneAnalyzer.initialize()
    BankAnalyzer.initialize()


def apply_replacements(source_text: str, replacements):
    """Применяет непересекающиеся позиционные замены без изменения соседнего текста."""

    ordered = sorted(replacements, key=lambda item: (item[0], item[1]))
    parts = []
    cursor = 0
    applied = 0
    for start, end, placeholder in ordered:
        if not (0 <= start < end <= len(source_text)) or start < cursor:
            continue
        parts.append(source_text[cursor:start])
        parts.append(placeholder)
        cursor = end
        applied += 1
    parts.append(source_text[cursor:])
    return "".join(parts), applied
