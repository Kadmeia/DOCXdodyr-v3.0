# -*- coding: utf-8 -*-
"""Утилиты проверки адресов, организаций и персон (ФИО)."""
from __future__ import annotations

import re
import config
from constants import ORG_OPF_PATTERNS
from placeholders import (
    ADDRESS_MARKERS,
    VERIFIED_ORGANIZATIONS,
    KNOWN_OPF_ABBREVIATIONS,
    RUSSIAN_PATRONYMICS,
)

def is_address_context(text, entity_start):
    window = text[max(0, entity_start-25):entity_start].lower()
    return any(marker in window for marker in ADDRESS_MARKERS)

def get_unique_filename(base_path, suffix="_cleaned", extension=".docx"):
    """Генерирует уникальное имя файла с нумерацией, если файл уже существует"""
    counter = 1
    final_path = base_path.parent / f"{base_path.stem}{suffix}{extension}"
    if not final_path.exists():
        return final_path
    while True:
        final_path = base_path.parent / f"{base_path.stem}{suffix}_{counter}{extension}"
        if not final_path.exists():
            return final_path
        counter += 1
        if counter > 9999:
            final_path = base_path.parent / f"{base_path.stem}{suffix}_{counter}{extension}"
            break
    return final_path

def is_valid_address_pullenti(text):
    """Строгая проверка адреса - требует обязательные адресные маркеры"""
    if not text or len(text.strip()) < 2:
        return False
    text_clean = text.strip()
    invalid_patterns = [
        r'\b(?:случае|если|когда|где|там|здесь|тогда|потом|после|перед)\b',
        r'\b(?:оплат|платеж|стоимость|сумм|договор|услуг|работ)\b',
        r'\b(?:заказчик|исполнитель|подрядчик|клиент|стороны)\b',
        r'\b(?:уплачивает|получает|передает|выполняет|обязуется)\b',
        r'\b(?:с\s+обязательным|обязательным|соблюдением)\b',
        r'\b(?:этап|этапа|этапу|этапе|этапом|этапы|этапов)\b',
        r'\b(?:материал|материала|материалу|материале|материалом|материалы|материалов)\b',
        r'\b(?:условии|условие|условия|условий|условиям|условиями)\b',
        r'\b(?:завершении|завершение|завершения|завершению)\b',
        r'\b(?:работа|работы|работе|работу|работой|работ)\b',
        r'\b(?:проект|проекта|проекту|проекте|проектом|проекты|проектов)\b'
    ]
    for pattern in invalid_patterns:
        if re.search(pattern, text_clean.lower()):
            return False
    required_address_markers = [
        r'\b(?:г\.|город|гор\.)\s*[А-ЯЁ][а-яё]+',
        r'\b(?:ул\.|улица)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:пр\.|пр-т|проспект)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:пер\.|переулок)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:ш\.|шоссе)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:наб\.|набережная)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:пл\.|площадь)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:б-р|бульвар)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:тер\.|территория)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:обл\.|область)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:р[.-]н|район)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:пос\.|поселок|посёлок)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:с\.|село)\s*[А-ЯЁ][а-яё\s]+',
        r'\b(?:д\.|дом)\s*\d+[а-яёА-ЯЁ]?',
        r'\b(?:кв\.|квартира)\s*\d+',
        r'\b(?:стр\.|строение)\s*\d+',
        r'\b(?:корп\.|корпус)\s*\d+[а-яёА-ЯЁ]?',
        r'\b(?:оф\.|офис)\s*\d+[а-яёА-ЯЁ]?'
    ]
    has_required_marker = any(re.search(m, text_clean, re.IGNORECASE) for m in required_address_markers)
    if not has_required_marker and len(text_clean.split()) == 1:
        if re.match(r'^[А-ЯЁ][а-яё]{2,}(?:ск|рск|нск|цк|ия|ье|во|но|ты|ки|ев|ов|ин|ын|ург|град|город)$', text_clean):
            return True
        if re.match(r'^[А-ЯЁ][а-яё]{2,6}[ейыаи]?$', text_clean) and len(text_clean) >= 4:
            return True
    return has_required_marker

def has_opf_pattern_before(text, entity_start, max_distance=50, prev_paragraphs=None, max_empty=3, debug_log=None):
    """Проверяет наличие ОПФ паттерна непосредственно перед сущностью в кавычках"""
    context_window = text[max(0, entity_start-max_distance):entity_start]
    if debug_log is not None:
        debug_log.append(f"    [ОПФ Отладка] Контекст перед кавычками (позиция {entity_start}): '{context_window.replace(chr(10), '↵')}'")
    for opf_pattern in ORG_OPF_PATTERNS:
        direct_pattern = opf_pattern + r'[\s,:;-]*$'
        if re.search(direct_pattern, context_window, flags=re.IGNORECASE):
            if debug_log is not None:
                debug_log.append(f"    [ОПФ Отладка] ✓ Найден ОПФ '{opf_pattern}' в конце контекста")
            return True, opf_pattern
        flexible_pattern = opf_pattern + r'[\s,:;-]+[\w\s]{0,50}$'
        if re.search(flexible_pattern, context_window, flags=re.IGNORECASE):
            if debug_log is not None:
                debug_log.append(f"    [ОПФ Отладка] ✓ Найден ОПФ '{opf_pattern}' с дополнительными словами")
            return True, opf_pattern
    if debug_log is not None:
        debug_log.append("    [ОПФ Отладка] ОПФ в текущем контексте НЕ найден")
    if prev_paragraphs:
        if debug_log is not None:
            debug_log.append(f"    [ОПФ Отладка] Проверяем {len(prev_paragraphs)} предыдущих абзацев")
        empty_count = 0
        for i, prev_text in enumerate(reversed(prev_paragraphs)):
            if prev_text is None or not prev_text.strip():
                empty_count += 1
                if empty_count > max_empty:
                    break
                continue
            if debug_log is not None:
                debug_log.append(f"    [ОПФ Отладка] Проверяем абзац #{len(prev_paragraphs)-i}: '{prev_text.strip()[:100]}...'")
            for opf_pattern in ORG_OPF_PATTERNS:
                if re.search(opf_pattern + r'[\s,:;.-]*$', prev_text.strip(), flags=re.IGNORECASE):
                    if debug_log is not None:
                        debug_log.append(f"    [ОПФ Отладка] ✓ Найден ОПФ '{opf_pattern}' в конце предыдущего абзаца")
                    return True, f"{opf_pattern}(в предыдущем абзаце)"
            break
    if debug_log is not None:
        debug_log.append("    [ОПФ Отладка] ОПФ НЕ найден нигде")
    return False, None

def detect_surname(word, text, entity_start, log_entries):
    """Определяет, является ли слово фамилией по контексту и морфологии."""
    log_entries.append(f"┌─ Анализ потенциальной фамилии: '{word}'")
    if len(word) < 4:
        log_entries.append(f"├── ❌ Слишком короткое слово ({len(word)} символов)")
        log_entries.append("└── Результат: НЕ ФАМИЛИЯ")
        return False
    if not word.istitle():
        log_entries.append("├── ❌ Не начинается с заглавной буквы")
        log_entries.append("└── Результат: НЕ ФАМИЛИЯ")
        return False
    if not word.isalpha():
        log_entries.append("├── ❌ Содержит не только буквы")
        log_entries.append("└── Результат: НЕ ФАМИЛИЯ")
        return False
    log_entries.append(f"├── ✅ Базовые критерии пройдены (длина: {len(word)}, формат: заглавная+строчные, только буквы)")
    role_indicators = [
        'директор', 'генеральный', 'главный', 'руководитель', 'начальник', 'управляющий',
        'председатель', 'президент', 'вице', 'заместитель', 'зам', 'лице', 'представитель',
        'исполнительный', 'ответственный', 'уполномоченный'
    ]
    context_start = max(0, entity_start - 50)
    context_end = min(len(text), entity_start + len(word) + 50)
    context = text[context_start:context_end].lower()
    log_entries.append(f"├── Контекст: '{context.replace(chr(10), '↵').replace(chr(13), '↵')}'")
    found_roles = [r for r in role_indicators if r in context]
    if found_roles:
        log_entries.append(f"├── ✅ Найдены индикаторы ролей: {found_roles}")
    else:
        log_entries.append("├── ❌ Индикаторы ролей не найдены")
    is_surname_morph = False
    if config.PYMORPHY3_AVAILABLE and config.morph3:
        try:
            parsed = config.morph3.parse(word)[0]
            if parsed.tag.POS == 'NOUN':
                if 'Surn' in str(parsed.tag) or 'Name' in str(parsed.tag):
                    is_surname_morph = True
                    log_entries.append(f"├── ✅ Морфология: {parsed.tag} (фамилия)")
                else:
                    log_entries.append(f"├── 📝 Морфология: {parsed.tag} (существительное, но не фамилия)")
            else:
                log_entries.append(f"├── 📝 Морфология: {parsed.tag} (не существительное)")
        except Exception as e:
            log_entries.append(f"├── ⚠️ Ошибка морфологического анализа: {e}")
    else:
        log_entries.append("├── ⚠️ Морфологический анализ недоступен (pymorphy3 не найдена)")
    has_role_context = len(found_roles) > 0
    if has_role_context:
        log_entries.append("└── ✅ Результат: ФАМИЛИЯ (найден контекст роли)")
        return True
    if is_surname_morph:
        log_entries.append("└── ✅ Результат: ФАМИЛИЯ (морфология подтверждает)")
        return True
    log_entries.append("└── ❌ Результат: НЕ ФАМИЛИЯ (нет контекста роли и морфологических признаков)")
    return False

def is_valid_org_strict(ent_text, context, start_pos, prev_paragraphs=None):
    """Строгая проверка ORG - принимаем при наличии ОПФ, кавычек или белого списка."""
    # Нормализуем переносы/пробелы (OCR и PDF часто дают \n внутри сущности)
    normalized = re.sub(r"\s+", " ", (ent_text or "")).strip()
    words = normalized.split()
    clean_text = normalized.lower().strip('«»"\'')

    # 1) Если внутри самой сущности есть "сильный" ОПФ (полное или сокращённое) — принимаем.
    # Важно: в ORG_OPF_PATTERNS есть очень общие слова ("общество", "организация" и т.п.).
    # Их не используем как единственный критерий, чтобы не ловить ложные срабатывания.
    for opf_pattern in ORG_OPF_PATTERNS:
        try:
            # считаем паттерн "сильным", если он явно многословный/сокращение
            is_strong = bool(re.search(r"(\\s|\[\\s|\s|\-|\[)", opf_pattern))
            if is_strong and re.search(opf_pattern, normalized, flags=re.IGNORECASE):
                return True, f"содержит ОПФ '{opf_pattern}'"
        except re.error:
            continue

    for pattern in ORG_OPF_PATTERNS:
        try:
            if re.fullmatch(pattern, normalized, flags=re.IGNORECASE):
                return True, f"соответствует полному паттерну ОПФ '{pattern}'"
        except re.error:
            continue
    if clean_text in VERIFIED_ORGANIZATIONS:
        return True, "в белом списке организаций"
    if any(word.strip('«»"\'(),.;:').upper() in KNOWN_OPF_ABBREVIATIONS for word in words):
        return True, "содержит ОПФ"
    context_before = context[max(0, start_pos-10):start_pos]
    context_after = context[start_pos+len(ent_text):start_pos+len(ent_text)+10]
    # Кавычки могут быть внутри самой сущности (например, сущность начинается с «)
    starts_with_quote = normalized[:1] in ['«', '"', '„']
    ends_with_quote = normalized[-1:] in ['»', '"', '“']
    is_in_quotes = (
        (starts_with_quote and (ends_with_quote or '»' in normalized or '"' in normalized)) or
        (any(q in context_before for q in ['«', '"', '„']) and any(q in context_after for q in ['»', '"', '“']))
    )
    if is_in_quotes:
        has_opf_nearby, found_pattern = has_opf_pattern_before(context, start_pos, max_distance=100, prev_paragraphs=prev_paragraphs)
        if has_opf_nearby:
            return True, f"в кавычках + ОПФ '{found_pattern}'"
    if len(words) > 1:
        has_opf_nearby, found_pattern = has_opf_pattern_before(context, start_pos, max_distance=50, prev_paragraphs=prev_paragraphs)
        if has_opf_nearby:
            return True, f"многословное + ОПФ '{found_pattern}'"
    return False, "не соответствует строгим критериям ORG"

def is_valid_per_strict(ent_text, context, start_pos, current_exclusions_lower):
    """Строгая проверка PER - полное ФИО, инициалы, отчество или контекст роли."""
    normalized = re.sub(r"\s+", " ", (ent_text or "")).strip()
    if normalized.lower() in current_exclusions_lower:
        return False, "в списке исключений (юридический термин)"
    # Извлечение ФИО из контекста «Генеральный директор ___________А.А. Охабкин»
    role_fio_match = re.search(r'[А-ЯЁ]\.\s*[А-ЯЁ]\.?\s+[А-ЯЁ][а-яё]{2,}', normalized)
    if role_fio_match:
        fio_part = role_fio_match.group(0)
        if re.match(r'^[А-ЯЁ]\.\s*[А-ЯЁ]\.?\s+[А-ЯЁ][а-яё]{2,}$', fio_part):
            return True, "ФИО (инициалы+фамилия) в контексте роли/подчёркиваний"
    words = normalized.split()
    if len(words) == 3:
        name_pattern = r'^[А-ЯЁ][а-яё]{1,}$'
        if all(re.match(name_pattern, word) for word in words):
            if config.PYMORPHY3_AVAILABLE and config.morph3:
                try:
                    parses = [config.morph3.parse(w)[0] for w in words]
                    tags = [str(p.tag) for p in parses]
                    has_name_parts = any("Surn" in tag or "Name" in tag or "Patr" in tag for tag in tags)
                    if has_name_parts:
                        return True, "полное ФИО с морфологической поддержкой"
                    return False, "не прошло морфологическую проверку ФИО"
                except Exception:
                    return True, "полное ФИО (ошибка морфологии, принято по паттерну)"
            return True, "полное ФИО (3 капитализированных слова, морфология недоступна)"
    if 2 <= len(words) <= 4:
        full_text = normalized.strip()
        # Расширенные паттерны из DOCXdodyr 2.1 для различных форматов ФИО с инициалами
        fio_patterns = [
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?$',
            r'^[А-ЯЁ]\.\s*[А-ЯЁ]\.?\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ]\.\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?$',
            r'^[А-ЯЁ]\.\s*[А-ЯЁ]\.?\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ]\.\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ]\.$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ],\s*[А-ЯЁ][,.]?$',
            r'^[А-ЯЁ],\s*[А-ЯЁ][,.]?\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ],$',
            r'^[А-ЯЁ],\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ],\s*[А-ЯЁ][,.]?$',
            r'^[А-ЯЁ],\s*[А-ЯЁ][,.]?\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ],$',
            r'^[А-ЯЁ],\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\s+[А-ЯЁ]$',
            r'^[А-ЯЁ]\s+[А-ЯЁ]\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]$',
            r'^[А-ЯЁ]\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][А-ЯЁ]$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ]\s+[А-ЯЁ]$',
            r'^[А-ЯЁ]\s+[А-ЯЁ]\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ]$',
            r'^[А-ЯЁ]\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ][А-ЯЁ]$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s*[А-ЯЁ],$',
            r'^[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ],\s*[А-ЯЁ]\.$',
            r'^[А-ЯЁ]\.\s*[А-ЯЁ],\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ],\s*[А-ЯЁ]\.\s+[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]{2,}\.[А-ЯЁ]\.\s*[А-ЯЁ]\.?$',
            r'^[А-ЯЁ][а-яё]{2,}\.[А-ЯЁ],\s*[А-ЯЁ][,.]?$',
            r'^[А-ЯЁ][а-яё]{2,}\.[А-ЯЁ]\s+[А-ЯЁ]$',
            r'^[А-ЯЁ][а-яё]{2,}\.[А-ЯЁ]\.$',
            r'^[А-ЯЁ][а-яё]{2,}\.[А-ЯЁ],$',
            r'^[А-ЯЁ][а-яё]{2,}\.[А-ЯЁ]$',
            r'^[А-ЯЁ][а-яё]{2,}\.\s+[А-ЯЁ][\.,-]\s*[А-ЯЁ][\.,-]?$',
            r'^[А-ЯЁ][а-яё]{2,}[А-ЯЁ]\.[А-ЯЁ]\.?$',
            r'^[А-ЯЁ][а-яё]{2,}[А-ЯЁ],[А-ЯЁ][,.]?$',
            r'^[А-ЯЁ][а-яё]{2,}[А-ЯЁ][А-ЯЁ]\.?$',
            r'^[А-ЯЁ]\.[А-ЯЁ]\.?[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ],[А-ЯЁ][,.]?[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][А-ЯЁ][\.,-]?[А-ЯЁ][а-яё]{2,}$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*[А-ЯЁ]\.[А-ЯЁ]\.?$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*[А-ЯЁ],[А-ЯЁ][,.]?$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*[А-ЯЁ][А-ЯЁ]\.?$',
            r'^[А-ЯЁ]\.[А-ЯЁ]\.?[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ],[А-ЯЁ][,.]?[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][А-ЯЁ][\.,-]?[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*\s+[А-ЯЁ][\.,-]\s*[А-ЯЁ][\.,-]?$',
            r'^[А-ЯЁ][\.,-]\s*[А-ЯЁ][\.,-]?\s+[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*$',
            r'^[А-ЯЁ][а-яё]{2,}[\s\.,-]+[А-ЯЁ][\.\s,-]*[А-ЯЁ]?[\.\s,-]?$',
        ]
        for pattern in fio_patterns:
            if re.match(pattern, full_text):
                return True, "ФИО с инициалами"
        has_surname = False
        has_initials = False
        for w in words:
            clean_word = w.replace('.', '').replace(',', '')
            if len(clean_word) >= 3 and re.match(r'^[А-ЯЁ][а-яё-]{2,}$', clean_word):
                has_surname = True
            elif len(clean_word) <= 2 and re.match(r'^[А-ЯЁ]{1,2}$', clean_word):
                has_initials = True
        if has_surname and has_initials:
            return True, "ФИО с инициалами (эвристика)"
    if len(words) == 1:
        if detect_surname(ent_text, context, start_pos, []):
            return True, "фамилия с контекстом роли"
    for word in words:
        if word.lower() in RUSSIAN_PATRONYMICS:
            return True, f"содержит отчество '{word}'"
    return False, "не соответствует строгим критериям PER"
