# -*- coding: utf-8 -*-
"""Модуль анонимизации, извлечения текста и восстановления презентаций PowerPoint (.pptx, .pptm).

Поддерживает работу с форматом OpenXML:
- Текстовые блоки, фигуры и заголовки на слайдах (ppt/slides/slide*.xml)
- Заметки докладчика (ppt/notesSlides/notesSlide*.xml)
- Таблицы на слайдах
- Комментарии (ppt/comments/comment*.xml)
- Сохранение разметки, темы и стилей оформления
- Экспорт слайдов в чистый текст (.txt) и Markdown (.md)
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET

# Пространства имен OpenXML PresentationML & DrawingML
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Регистрируем стандартные префиксы пространств имен для корректной сериализации
ET.register_namespace("a", A_NS)
ET.register_namespace("p", P_NS)
ET.register_namespace("r", R_NS)

NAMESPACES = {"a": A_NS, "p": P_NS, "r": R_NS}


def _natural_slide_sort_key(filename: str) -> int:
    """Извлекает номер слайда для сортировки (например, slide2.xml -> 2)."""
    match = re.search(r"slide(\d+)\.xml", filename, re.IGNORECASE)
    return int(match.group(1)) if match else 999999


def extract_pptx_slides(pptx_path: Path | str) -> List[Dict[str, Any]]:
    """Извлекает структурированный текст слайдов из архива PPTX/PPTM."""
    pptx_p = Path(pptx_path)
    if not pptx_p.exists():
        raise FileNotFoundError(f"Файл презентации не найден: {pptx_p}")

    slides_data: List[Dict[str, Any]] = []

    with zipfile.ZipFile(pptx_p, "r") as zf:
        slide_files = [
            f for f in zf.namelist()
            if re.match(r"^ppt/slides/slide\d+\.xml$", f, re.IGNORECASE)
        ]
        slide_files.sort(key=_natural_slide_sort_key)

        for idx, slide_file in enumerate(slide_files, start=1):
            slide_xml_content = zf.read(slide_file)
            root = ET.fromstring(slide_xml_content)

            paragraphs: List[str] = []
            title: str = ""

            # Ищем абзацы на слайде
            for p_elem in root.findall(".//a:p", NAMESPACES):
                t_nodes = p_elem.findall(".//a:t", NAMESPACES)
                p_text = "".join(t.text or "" for t in t_nodes).strip()
                if p_text:
                    if not title and len(p_text) < 120 and not p_text.startswith(("-", "•", "*")):
                        title = p_text
                    paragraphs.append(p_text)

            # Проверяем наличие заметок докладчика
            notes_file = f"ppt/notesSlides/notesSlide{idx}.xml"
            notes_text: List[str] = []
            if notes_file in zf.namelist():
                try:
                    notes_root = ET.fromstring(zf.read(notes_file))
                    for p_elem in notes_root.findall(".//a:p", NAMESPACES):
                        t_nodes = p_elem.findall(".//a:t", NAMESPACES)
                        nt = "".join(t.text or "" for t in t_nodes).strip()
                        if nt and not re.match(r"^\d+$", nt):  # пропускаем номер слайда в колонтитуле
                            notes_text.append(nt)
                except Exception:
                    pass

            slides_data.append({
                "slide_number": idx,
                "title": title or f"Слайд {idx}",
                "paragraphs": paragraphs,
                "notes": notes_text,
            })

    return slides_data


def pptx_to_text(slides: Sequence[Dict[str, Any]]) -> str:
    """Форматирует слайды презентации в чистый структурированный текст."""
    lines: List[str] = []
    for s in slides:
        num = s.get("slide_number", 1)
        title = s.get("title", f"Слайд {num}")
        lines.append(f"=== Слайд {num}: {title} ===")
        for p in s.get("paragraphs", []):
            lines.append(p)
        if s.get("notes"):
            lines.append("[Заметки:]")
            for n in s.get("notes", []):
                lines.append(f"  * {n}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def pptx_to_markdown(slides: Sequence[Dict[str, Any]]) -> str:
    """Форматирует слайды презентации в стандартный Markdown."""
    lines: List[str] = []
    for s in slides:
        num = s.get("slide_number", 1)
        title = s.get("title", f"Слайд {num}")
        lines.append(f"## Слайд {num}: {title}\n")
        for p in s.get("paragraphs", []):
            if re.match(r"^[-•*]\s+", p):
                lines.append(p)
            else:
                lines.append(f"{p}\n")
        if s.get("notes"):
            lines.append("\n> **Заметки докладчика:**")
            for n in s.get("notes", []):
                lines.append(f"> - {n}")
            lines.append("")
        lines.append("---\n")
    return "\n".join(lines).strip() + "\n"


def anonymize_pptx(
    pptx_path: Path | str,
    output_path: Path | str,
    anonymize_fn: Callable[[str], Tuple[str, int, List[str]]],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[int, List[str], str]:
    """Анонимизирует PPTX/PPTM презентацию с сохранением всех стилей и структуры.

    Аргументы:
      pptx_path: путь к исходной презентации
      output_path: путь для сохранения обезличенной презентации
      anonymize_fn: функция (текст) -> (обезличенный_текст, число_замен, список_логов)
      progress_callback: коллбэк для индикатора прогресса (step, total, label)

    Возвращает:
      (total_replacements, log_data, cleaned_full_text)
    """
    src_p = Path(pptx_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    total_replacements = 0
    all_logs: List[str] = []
    cleaned_slides_for_export: List[Dict[str, Any]] = []

    # Читаем исходный архив во временную директорию
    with tempfile.TemporaryDirectory(prefix="docxdodyr-pptx-") as tmpdir:
        tmp_path = Path(tmpdir)
        with zipfile.ZipFile(src_p, "r") as zin:
            zin.extractall(tmp_path)

        # Собираем список целевых xml-файлов слайдов и заметок
        slide_xmls = sorted(
            [p for p in (tmp_path / "ppt" / "slides").glob("slide*.xml") if p.is_file()],
            key=lambda p: _natural_slide_sort_key(p.name),
        ) if (tmp_path / "ppt" / "slides").exists() else []

        notes_xmls = sorted(
            [p for p in (tmp_path / "ppt" / "notesSlides").glob("notesSlide*.xml") if p.is_file()],
            key=lambda p: _natural_slide_sort_key(p.name),
        ) if (tmp_path / "ppt" / "notesSlides").exists() else []

        comment_xmls = sorted(
            [p for p in (tmp_path / "ppt" / "comments").glob("comment*.xml") if p.is_file()]
        ) if (tmp_path / "ppt" / "comments").exists() else []

        total_steps = max(len(slide_xmls) + len(notes_xmls) + len(comment_xmls), 1)
        current_step = 0

        # 1. Обработка основных слайдов
        for idx, slide_file in enumerate(slide_xmls, start=1):
            current_step += 1
            if progress_callback:
                try:
                    progress_callback(current_step, total_steps, f"Слайд {idx} из {len(slide_xmls)}")
                except Exception:
                    pass

            xml_data = slide_file.read_bytes()
            root = ET.fromstring(xml_data)
            slide_modified = False
            slide_paragraphs: List[str] = []
            slide_title = ""

            for p_elem in root.findall(".//a:p", NAMESPACES):
                t_nodes = p_elem.findall(".//a:t", NAMESPACES)
                if not t_nodes:
                    continue

                full_p_text = "".join(t.text or "" for t in t_nodes)
                if not full_p_text.strip():
                    continue

                cleaned_p, repl_count, logs = anonymize_fn(full_p_text)
                if not slide_title and len(cleaned_p) < 120 and not cleaned_p.startswith(("-", "•", "*")):
                    slide_title = cleaned_p
                slide_paragraphs.append(cleaned_p)

                if repl_count > 0:
                    total_replacements += repl_count
                    all_logs.extend(f"[Слайд {idx}] {ln}" for ln in logs)
                    slide_modified = True

                    # Записываем результат в первый узел текста, остальные очищаем
                    t_nodes[0].text = cleaned_p
                    for extra_t in t_nodes[1:]:
                        extra_t.text = ""

            if slide_modified:
                new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                slide_file.write_bytes(new_xml)

            cleaned_slides_for_export.append({
                "slide_number": idx,
                "title": slide_title or f"Слайд {idx}",
                "paragraphs": slide_paragraphs,
                "notes": [],
            })

        # 2. Обработка заметок докладчика
        for n_idx, notes_file in enumerate(notes_xmls, start=1):
            current_step += 1
            if progress_callback:
                try:
                    progress_callback(current_step, total_steps, f"Заметки к слайду {n_idx}")
                except Exception:
                    pass

            xml_data = notes_file.read_bytes()
            root = ET.fromstring(xml_data)
            notes_modified = False

            for p_elem in root.findall(".//a:p", NAMESPACES):
                t_nodes = p_elem.findall(".//a:t", NAMESPACES)
                if not t_nodes:
                    continue
                full_text = "".join(t.text or "" for t in t_nodes)
                if not full_text.strip():
                    continue

                cleaned_n, repl_count, logs = anonymize_fn(full_text)
                if repl_count > 0:
                    total_replacements += repl_count
                    all_logs.extend(f"[Заметки {n_idx}] {ln}" for ln in logs)
                    notes_modified = True
                    t_nodes[0].text = cleaned_n
                    for extra_t in t_nodes[1:]:
                        extra_t.text = ""

                # Прикрепляем к слайду для экспорта
                if n_idx <= len(cleaned_slides_for_export):
                    cleaned_slides_for_export[n_idx - 1]["notes"].append(cleaned_n)

            if notes_modified:
                new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                notes_file.write_bytes(new_xml)

        # 3. Обработка комментариев
        for comment_file in comment_xmls:
            current_step += 1
            xml_data = comment_file.read_bytes()
            root = ET.fromstring(xml_data)
            comm_modified = False

            for text_elem in root.findall(".//p:text", NAMESPACES):
                raw = text_elem.text or ""
                if not raw.strip():
                    continue
                cleaned_c, repl_count, logs = anonymize_fn(raw)
                if repl_count > 0:
                    total_replacements += repl_count
                    all_logs.extend(f"[Комментарий] {ln}" for ln in logs)
                    comm_modified = True
                    text_elem.text = cleaned_c

            if comm_modified:
                new_xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                comment_file.write_bytes(new_xml)

        # Собираем модифицированный архив обратно
        temp_out = out_p.with_suffix(out_p.suffix + ".tmp")
        with zipfile.ZipFile(temp_out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for root_dir, _dirs, files in os.walk(tmp_path):
                for f in files:
                    full_path = Path(root_dir) / f
                    rel_path = full_path.relative_to(tmp_path)
                    zout.write(full_path, arcname=str(rel_path).replace("\\", "/"))

        temp_out.replace(out_p)

    cleaned_full_text = pptx_to_text(cleaned_slides_for_export)
    return total_replacements, all_logs, cleaned_full_text


def restore_pptx(
    pptx_path: Path | str,
    output_path: Path | str,
    mapping_dict: Dict[str, str],
) -> Tuple[bool, str]:
    """Восстанавливает исходные данные в PPTX/PPTM презентации по дешифратору."""
    src_p = Path(pptx_path)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if not mapping_dict:
        return False, "Дешифратор пуст или не содержит соответствий"

    keys = sorted(mapping_dict.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys))

    def _replace_match(m: re.Match) -> str:
        val = mapping_dict.get(m.group(0), m.group(0))
        if isinstance(val, dict):
            return str(val.get("original", ""))
        return str(val)

    try:
        with tempfile.TemporaryDirectory(prefix="docxdodyr-restore-pptx-") as tmpdir:
            tmp_path = Path(tmpdir)
            with zipfile.ZipFile(src_p, "r") as zin:
                zin.extractall(tmp_path)

            all_xml_files = list(tmp_path.rglob("*.xml"))
            for xml_file in all_xml_files:
                raw_text = xml_file.read_text(encoding="utf-8", errors="replace")
                if pattern.search(raw_text):
                    restored_text = pattern.sub(_replace_match, raw_text)
                    xml_file.write_text(restored_text, encoding="utf-8")

            temp_out = out_p.with_suffix(out_p.suffix + ".tmp")
            with zipfile.ZipFile(temp_out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for root_dir, _dirs, files in os.walk(tmp_path):
                    for f in files:
                        full_path = Path(root_dir) / f
                        rel_path = full_path.relative_to(tmp_path)
                        zout.write(full_path, arcname=str(rel_path).replace("\\", "/"))
            temp_out.replace(out_p)

        return True, "Презентация успешно восстановлена"
    except Exception as exc:
        return False, f"Ошибка восстановления PPTX: {exc}"
