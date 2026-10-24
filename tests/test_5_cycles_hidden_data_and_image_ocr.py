# -*- coding: utf-8 -*-
"""5 циклов код-ревью и валидации:
Цикл 1: Проверка значений политики скрытых данных по умолчанию (анонимизация vs удаление).
Цикл 2: Обезличивание скрытых данных в DOCX (маскирование комментариев и сносок, удаление hidden_text и свойств).
Цикл 3: Обезличивание скрытых данных в PDF (маскирование аннотаций и AcroForm, удаление метаданных).
Цикл 4: Распознавание PDF и всех вариаций изображений (JPG, PNG, TIFF, BMP, WEBP).
Цикл 5: Проверка элементов UI (Распознавание PDF / JPG, янтарная кнопка «Сообщить о баге»).
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

import app_paths
import ocr_backend
from backend_api import BackendApi
import hidden_data


class Test5CyclesHiddenDataAndImageOCR:

    # -------------------------------------------------------------------------
    # ЦИКЛ 1: Валидация политики скрытых данных по умолчанию
    # -------------------------------------------------------------------------
    def test_cycle_01_default_hidden_data_policy_classification(self):
        """Цикл 1: Проверка разделения сущностей на маскируемые плейсхолдерами и удаляемые."""
        defaults = BackendApi._default_hidden_data_policy()

        # Группа 1: Маскируются по плейсхолдерам при обычном обезличивании
        anonymize_group = ["comments", "footnotes", "endnotes", "acroform", "annotations"]
        for kind in anonymize_group:
            assert defaults.get(kind) == "anonymize", f"{kind} должен иметь действие 'anonymize'"

        # Группа 2: Всегда удаляются при обычном обезличивании
        remove_group = [
            "hidden_text", "tracked_changes", "embedded_objects", "embedded_files",
            "javascript", "macros", "custom_xml", "external_links",
            "document_properties", "image_exif_xmp", "pdf_info", "pdf_xmp"
        ]
        for kind in remove_group:
            assert defaults.get(kind) == "remove", f"{kind} должен иметь действие 'remove'"

        # Проверка включения acroform и annotations в TEXTUAL_HIDDEN_KINDS
        assert "acroform" in hidden_data.TEXTUAL_HIDDEN_KINDS
        assert "annotations" in hidden_data.TEXTUAL_HIDDEN_KINDS

    # -------------------------------------------------------------------------
    # ЦИКЛ 2: Обезличивание DOCX (маскирование комментариев/сносок, удаление hidden_text)
    # -------------------------------------------------------------------------
    def test_cycle_02_docx_hidden_data_transformations(self, tmp_path: Path):
        """Цикл 2: Комментарии и автор маскируются, hidden_text физически удаляется."""
        xml_comments = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:comment w:id="1" w:author="\xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd \xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xbe\xd0\xb2" w:initials="\xd0\x98\xd0\x98">'
            b'<w:p><w:r><w:t>\xd0\x9f\xd1\x80\xd0\xbe\xd0\xb2\xd0\xb5\xd1\x80\xd0\xb8\xd1\x82\xd1\x8c \xd0\xb4\xd0\xbe\xd0\xb3\xd0\xbe\xd0\xb2\xd0\xbe\xd1\x80 \xd0\xa1\xd0\xb8\xd0\xb4\xd0\xbe\xd1\x80\xd0\xbe\xd0\xb2\xd0\xb0 \xd0\x9f.\xd0\xa1.</w:t></w:r></w:p>'
            b'</w:comment>'
            b'</w:comments>'
        )

        def mock_transform(text, kind):
            t = text.replace("Иван Иванов", "[ФИО-1]").replace("ИИ", "[ИНИЦИАЛЫ]")
            t = t.replace("Сидорова П.С.", "[ФИО-2]")
            return t

        transformed_xml, removed = hidden_data._transform_word_xml(
            xml_comments, comments=False, notes=False, revisions=False,
            hidden_text=False, text_transform=mock_transform, text_kind="comments"
        )

        assert "[ФИО-1]".encode("utf-8") in transformed_xml
        assert "[ИНИЦИАЛЫ]".encode("utf-8") in transformed_xml
        assert "[ФИО-2]".encode("utf-8") in transformed_xml

        # Проверка удаления скрытого текста (w:vanish)
        xml_doc = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:body>'
            b'<w:p>'
            b'<w:r><w:t>\xd0\x92\xd0\xb8\xd0\xb4\xd0\xb8\xd0\xbc\xd1\x8b\xd0\xb9 \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82</w:t></w:r>'
            b'<w:r><w:rPr><w:vanish/></w:rPr><w:t>\xd0\xa1\xd0\xba\xd1\x80\xd1\x8b\xd1\x82\xd1\x8b\xd0\xb9 \xd1\x82\xd0\xb5\xd0\xba\xd1\x81\xd1\x82</w:t></w:r>'
            b'</w:p>'
            b'</w:body>'
            b'</w:document>'
        )
        cleaned_doc, doc_removed = hidden_data._transform_word_xml(
            xml_doc, comments=False, notes=False, revisions=False,
            hidden_text=True, text_transform=None, text_kind="hidden_text"
        )
        assert "hidden_text" in doc_removed
        assert b"\xd0\xa1\xd0\xba\xd1\x80\xd1\x8b\xd1\x82\xd1\x8b\xd0\xb9" not in cleaned_doc
        assert b"\xd0\x92\xd0\xb8\xd0\xb4\xd0\xb8\xd0\xbc\xd1\x8b\xd0\xb9" in cleaned_doc

    # -------------------------------------------------------------------------
    # ЦИКЛ 3: Обезличивание PDF (аннотации и AcroForm маскируются)
    # -------------------------------------------------------------------------
    def test_cycle_03_pdf_annotations_and_forms_anonymization(self, tmp_path: Path):
        """Цикл 3: Аннотации и поля форм в PDF маскируются через text_transform."""
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)

        # Добавляем тестовую текстовую аннотацию
        annot = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Contents"): TextStringObject("Заметка: паспорт 4509 123456 выдан МВД"),
            NameObject("/T"): TextStringObject("Инспектор Сидоров"),
        })
        page[NameObject("/Annots")] = ArrayObject([annot])

        # Добавляем AcroForm с полем
        field = DictionaryObject({
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject("ClientName"),
            NameObject("/V"): TextStringObject("Петров Петр Петрович"),
            NameObject("/TU"): TextStringObject("Подсказка: Петров"),
        })
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
            NameObject("/Fields"): ArrayObject([field])
        })

        src_pdf = tmp_path / "test_form.pdf"
        dst_pdf = tmp_path / "test_form_clean.pdf"
        with open(src_pdf, "wb") as f:
            writer.write(f)

        def pdf_transform(text, kind):
            return text.replace("4509 123456", "[ПАСПОРТ]").replace("Сидоров", "[ФИО-1]").replace("Петров Петр Петрович", "[ФИО-2]")

        report = hidden_data.HiddenDataReport(source_type="pdf", source_path=str(src_pdf))
        hidden_data._sanitize_pdf(
            src_pdf, dst_pdf, report,
            annotations=False, attachments=False, xmp_exif=True, forms=False,
            text_transform=pdf_transform, text_kinds={"annotations", "acroform"}
        )

        assert dst_pdf.exists()
        from pypdf import PdfReader
        r = PdfReader(str(dst_pdf))
        p0 = r.pages[0]
        annots = p0.get("/Annots")
        assert len(annots) > 0
        cleaned_annot = annots[0].get_object()
        assert "[ПАСПОРТ]" in str(cleaned_annot.get("/Contents"))
        assert "[ФИО-1]" in str(cleaned_annot.get("/T"))

        # Проверка AcroForm поля
        root_acro = r.trailer["/Root"].get("/AcroForm")
        fields = root_acro.get("/Fields")
        cleaned_fld = fields[0].get_object()
        assert "[ФИО-2]" in str(cleaned_fld.get("/V"))

    # -------------------------------------------------------------------------
    # ЦИКЛ 4: Распознавание PDF и всех вариаций картинок (JPG, PNG, TIFF, BMP, WEBP)
    # -------------------------------------------------------------------------
    def test_cycle_04_image_and_pdf_ocr_worker_all_formats(self, tmp_path: Path, monkeypatch):
        """Цикл 4: _run_pdf_ocr_worker успешно обрабатывает PDF, JPG, JPEG, PNG, BMP, TIFF, WEBP."""
        api = BackendApi()
        api.ocr_lang = "rus+eng"

        formats = [
            ("scan1.jpg", "JPEG"),
            ("scan2.jpeg", "JPEG"),
            ("scan3.png", "PNG"),
            ("scan4.bmp", "BMP"),
            ("scan5.tiff", "TIFF"),
            ("scan6.webp", "WEBP"),
        ]

        test_files = []
        for filename, fmt in formats:
            img_path = tmp_path / filename
            im = Image.new("RGB", (200, 100), color=(255, 255, 255))
            im.save(img_path, format=fmt)
            test_files.append(str(img_path))

        class FakeDocResult:
            text = "Распознанный текст на скане: ООО Ромашка ИНН 7701234567"

        monkeypatch.setattr(
            "ocr_backend.ocr_image_to_result",
            lambda path, **kwargs: FakeDocResult()
        )
        monkeypatch.setattr(api, "_ensure_ocr_backend", lambda: True)

        api._run_pdf_ocr_worker(test_files)

        # Проверяем, что для каждого формата создан выходной .txt файл с результатом
        for file_path in test_files:
            out_txt = Path(file_path).with_suffix(".txt")
            assert out_txt.exists(), f"Выходной файл для {file_path} не создан"
            content = out_txt.read_text(encoding="utf-8")
            assert "ООО Ромашка" in content

    # -------------------------------------------------------------------------
    # ЦИКЛ 5: Проверка элементов UI и янтарной подсветки кнопки «Сообщить о баге»
    # -------------------------------------------------------------------------
    def test_cycle_05_ui_elements_and_amber_bug_report_button(self):
        """Цикл 5: Проверка кнопки 'Распознавание PDF / JPG' и янтарной кнопки 'Сообщить о баге'."""
        repo_root = Path(__file__).resolve().parent.parent
        index_html = (repo_root / "web" / "index.html").read_text(encoding="utf-8")

        # 1. Проверка кнопки Распознавание PDF / JPG
        assert "Распознавание PDF / JPG" in index_html

        # 2. Проверка кнопки Сообщить о баге с янтарной подсветкой
        assert 'id="btn-report-bug"' in index_html
        assert 'bg-amber-50' in index_html or 'border-amber-100' in index_html
        assert 'Сообщить о баге' in index_html

        # 3. Кнопка "Проверить скрытые данные" удалена из интерфейса
        assert "btn-hidden-data" not in index_html
        assert ">Проверить скрытые данные<" not in index_html

        # 4. Проверка стилей янтарной подсветки в CSS
        assert "html:not(.dark) #btn-report-bug" in index_html
        assert "#fcd34d" in index_html  # янтарная рамка
        assert "#fffbeb" in index_html  # янтарный фон
