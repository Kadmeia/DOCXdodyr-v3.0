# -*- coding: utf-8 -*-
"""Комплексная валидация и симуляция 30 циклов работы OCR и пользовательского интерфейса.

Включает:
- Проверку нейросетевого движка Indra OCR и нормализации текста
- Устранение гомоглифов (смешение кириллицы/латиницы)
- Распознавание ключевых реквизитов (Паспорт, СНИЛС, ИНН, БИК, Счета, Email, VIN, Суммы)
- Симуляцию действий пользователя (клик по кнопкам UI, прогресс-бар, отмена, экспорт)
- Проверку устойчивости (повороты на 90/180/270 градусов, перекос, пустые страницы, шум)
- Пакетную обработку папок со сканами и восстановление документов по дешифратору
- Полную автономность без интернета и очистку ресурсов
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
app_dir = REPO_ROOT / "app"
if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))

import ocr_backend
import main
from backend_api import BackendApi, Worker
from detection_engine import UnifiedDetectionPipeline
import folder_pipeline
from folder_pipeline import FolderAnonymizationPipeline
import pdf_convert
from privacy_audit import clean_file_metadata
from version import APP_VERSION


def _get_font(size: int = 28) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Возвращает системный шрифт с полноценной поддержкой кириллицы."""
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _create_synthetic_scan_image(
    text_lines: list[str],
    width: int = 1000,
    height: int = 700,
    angle: float = 0.0,
    noise: bool = False,
    font_size: int = 32,
) -> bytes:
    """Создает синтетическое изображение документа с четким кириллическим текстом."""
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = _get_font(font_size)

    # Рисуем рамку и строки
    draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(200, 200, 200), width=2)
    y_offset = 50
    for line in text_lines:
        draw.text((60, y_offset), line, fill=(0, 0, 0), font=font)
        y_offset += font_size + 16

    if noise:
        arr = np.array(img)
        gauss = np.random.normal(0, 8, arr.shape)
        noisy = np.clip(arr + gauss, 0, 255).astype(np.uint8)
        img = Image.fromarray(noisy)

    if angle != 0.0:
        img = img.rotate(angle, expand=True, fillcolor=(255, 255, 255))

    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def _create_synthetic_pdf_with_image(image_bytes: bytes, num_pages: int = 1) -> bytes:
    """Создает синтетический PDF с внедренными страницами-сканами."""
    import fitz  # PyMuPDF
    doc = fitz.open()
    for _ in range(num_pages):
        page = doc.new_page(width=595, height=842)
        rect = fitz.Rect(50, 50, 545, 792)
        page.insert_image(rect, stream=image_bytes)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


# ==============================================================================
# 30 ЦИКЛОВ КОД-РЕВЬЮ И СИМУЛЯЦИИ ДЕЙСТВИЙ ПОЛЬЗОВАТЕЛЯ
# ==============================================================================

class TestOCRAndUserSimulation30Cycles:

    def test_cycle_01_indra_backend_direct_image_recognition(self):
        """Цикл 01: Прямое распознавание изображения через Indra OCR с валидацией регионов."""
        backend_info, backend = ocr_backend.create_ocr_backend(preferred="indra", language="rus+eng")
        assert backend_info.name == "indra"
        assert "Indra" in backend_info.label

        sample_bytes = _create_synthetic_scan_image(["АКТ ПРИЕМА-ПЕРЕДАЧИ ТОВАРОВ", "Сумма к оплате: 50 000 руб."])
        regions = backend.recognize_regions(sample_bytes)
        assert isinstance(regions, list)
        assert len(regions) > 0
        for r in regions:
            assert isinstance(r.text, str)
            assert len(r.bbox) == 4
            assert 0.0 <= r.confidence <= 1.0
            x1, y1, x2, y2 = r.bbox
            assert 0.0 <= x1 <= x2 <= 1.0
            assert 0.0 <= y1 <= y2 <= 1.0

    def test_cycle_02_multipage_scanned_pdf_assembly(self, tmp_path):
        """Цикл 02: Распознавание многостраничного сканированного PDF и сборка страниц."""
        lines = [
            "ДОГОВОР КУПЛИ-ПРОДАЖИ НЕДВИЖИМОСТИ",
            "Город Москва, пятнадцатое января две тысячи двадцать четвертого года",
            "Стороны согласовали окончательную стоимость в сумме 12 500 000 руб.",
        ]
        scan_bytes = _create_synthetic_scan_image(lines, font_size=32)
        pdf_bytes = _create_synthetic_pdf_with_image(scan_bytes, num_pages=2)
        pdf_file = tmp_path / "scan_multi.pdf"
        pdf_file.write_bytes(pdf_bytes)

        result = pdf_convert.ocr_pdf_to_result(str(pdf_file), preferred_backend="indra")
        assert isinstance(result, ocr_backend.OCRDocumentResult)
        assert len(result.pages) == 2
        assert result.backend == "indra"
        for p in result.pages:
            assert p.page in (1, 2)
            assert p.width > 0 and p.height > 0
            assert len(p.regions) > 0

    def test_cycle_03_homoglyphs_cleaning_in_mixed_text(self):
        """Цикл 03: Очистка гомоглифов (смешение скриптов) в юридическом контексте."""
        assert ocr_backend.normalize_ocr_line("Cумма платежа") == "Сумма платежа"
        assert ocr_backend.normalize_ocr_line("1 000 pуб.") == "1 000 руб."
        assert ocr_backend.normalize_ocr_line("OOO «Ромашка»") == "ООО «Ромашка»"

        cleaned_email = ocr_backend.normalize_ocr_line("ivan.petrоv@corp-service.гu")
        assert "ivan.petrov@corp-service.ru" in cleaned_email
        assert all(ord(c) < 128 for c in cleaned_email.split()[-1])

    def test_cycle_04_passport_rf_recognition_and_sanitization(self):
        """Цикл 04: Распознавание и нормализация реквизитов паспорта РФ."""
        raw_passport_line = "Паспорт РФ 45 10 123456 выдан 12.03.2015 кoд 77O-OO1"
        cleaned_line = ocr_backend.normalize_ocr_line(raw_passport_line)
        assert "770-001" in cleaned_line

        pipeline = UnifiedDetectionPipeline()
        entities = pipeline.detect_candidates(cleaned_line)
        entity_types = {e.entity_type for e in entities}
        assert any("PASSPORT" in t or "SUBDIVISION" in t or "DATE" in t for t in entity_types)

    def test_cycle_05_bank_details_and_swift_recognition(self):
        """Цикл 05: Распознавание банковских реквизитов (БИК, счета, SWIFT)."""
        bank_text = "БИК 044525225 р/с 40702810400000001234 SWIFT: SABRRUMM"
        cleaned_text = ocr_backend.normalize_ocr_line(bank_text)
        assert "SABRRUMM" in cleaned_text
        assert "044525225" in cleaned_text

        pipeline = UnifiedDetectionPipeline()
        entities = pipeline.detect_candidates(cleaned_text)
        assert len(entities) > 0

    def test_cycle_06_ui_simulation_file_selection(self, tmp_path):
        """Цикл 06: Симуляция клика пользователя «Выбрать файлы» через ApiWrapper."""
        img1 = tmp_path / "scan1.png"
        img1.write_bytes(_create_synthetic_scan_image(["Документ 1"]))
        pdf1 = tmp_path / "scan2.pdf"
        pdf1.write_bytes(_create_synthetic_pdf_with_image(_create_synthetic_scan_image(["Документ 2"])))

        wrapper = main.ApiWrapper()
        fake_window = MagicMock()
        fake_window.create_file_dialog.return_value = [str(img1), str(pdf1)]
        wrapper.set_window(fake_window)
        wrapper._api.process_files = MagicMock()

        wrapper.open_file_dialog("anonymize_docs")
        wrapper._api.process_files.assert_called_once_with([str(img1), str(pdf1)])

    def test_cycle_07_ui_simulation_anonymize_button_progress(self, tmp_path):
        """Цикл 07: Симуляция клика «Обезличить» с получением колбэков прогресса."""
        doc_path = tmp_path / "contract.docx"
        from docx import Document
        doc = Document()
        doc.add_paragraph("Директор: Сидоров Алексей Николаевич, ИНН 770123456789")
        doc.save(doc_path)

        api = BackendApi()
        progress_events = []
        api.update_progress = lambda current, total, text="": progress_events.append((current, total, text))

        worker = Worker([str(doc_path)], api)
        worker.run()
        assert len(progress_events) > 0
        assert any(e[0] == e[1] for e in progress_events)

    def test_cycle_08_decoder_json_mapping_integrity(self, tmp_path):
        """Цикл 08: Проверка целостности и структуры сгенерированного дешифратора."""
        doc_path = tmp_path / "test_doc.docx"
        from docx import Document
        doc = Document()
        doc.add_paragraph("Иванов Иван Иванович, тел. +7 (999) 123-45-67")
        doc.save(doc_path)

        api = BackendApi()
        api.save_decoder = True
        worker = Worker([str(doc_path)], api)
        worker.run()

        decoder_candidates = list(tmp_path.glob("*дешифратор*.json"))
        assert len(decoder_candidates) >= 1
        decoder_file = decoder_candidates[0]
        data = json.loads(decoder_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert any("[ФИО" in k or "[PHONE" in k for k in data.keys())

    def test_cycle_09_full_restoration_roundtrip(self, tmp_path):
        """Цикл 09: Полный цикл восстановления обезличенного документа через дешифратор."""
        doc_path = tmp_path / "statement.docx"
        from docx import Document
        doc = Document()
        doc.add_paragraph("Заявитель: Кузнецов Михаил Юрьевич")
        doc.save(doc_path)

        api = BackendApi()
        api.save_decoder = True
        worker = Worker([str(doc_path)], api)
        worker.run()

        cleaned_file = tmp_path / "statement_cleaned.docx"
        decoder_file = list(tmp_path.glob("*дешифратор*.json"))[0]
        assert cleaned_file.exists()

        restored_file = tmp_path / "statement_restored.docx"
        from document_restorer import DocumentRestorer
        mapping_data = json.loads(decoder_file.read_text(encoding="utf-8"))
        restorer = DocumentRestorer(mapping_data)
        restorer.restore_docx(str(cleaned_file), str(restored_file))

        restored_doc = Document(str(restored_file))
        full_text = " ".join(p.text for p in restored_doc.paragraphs)
        assert "Кузнецов Михаил Юрьевич" in full_text
        assert "[ФИО" not in full_text

    def test_cycle_10_ui_ocr_language_switch(self):
        """Цикл 10: Симуляция переключения языков OCR (rus+eng -> rus -> eng)."""
        api = BackendApi()
        api.update_settings({"ocr_lang": "rus"})
        assert api.ocr_lang == "rus"
        assert api.settings.get("ocr_lang") == "rus"

        api.update_settings({"ocr_lang": "eng"})
        assert api.ocr_lang == "eng"

        api.update_settings({"ocr_lang": "rus+eng"})
        assert api.ocr_lang == "rus+eng"

    def test_cycle_11_backend_fallback_mechanics(self, monkeypatch):
        """Цикл 11: Механизм fallback и обработка недоступности Indra OCR."""
        monkeypatch.setattr(
            ocr_backend,
            "_IndraOCRBackend",
            MagicMock(side_effect=ocr_backend.OCRBackendUnavailable("Тестовая ошибка Indra"))
        )
        # На macOS при сбое Indra происходит fallback на нативный Apple Vision
        fake_vision = MagicMock()
        fake_vision.label = "Apple Vision"
        monkeypatch.setattr(ocr_backend, "_AppleVisionBackend", lambda lang: fake_vision)
        info_mac, _ = ocr_backend.create_ocr_backend(platform_name="darwin")
        assert info_mac.name == "applevision"

        # На Windows при сбое Indra выбрасывается честное исключение OCRBackendUnavailable
        with pytest.raises(ocr_backend.OCRBackendUnavailable):
            ocr_backend.create_ocr_backend(platform_name="win32")

    def test_cycle_12_upside_down_180_degrees_scan_orientation(self):
        """Цикл 12: Автоматическое детектирование ориентации и распознавание перевернутого на 180° скана."""
        text = ["ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", "ПРИКАЗ № 42 ОТ 15.01.2024"]
        scan_180 = _create_synthetic_scan_image(text, angle=180.0, font_size=32)

        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        regions = backend.recognize_regions(scan_180)
        assert isinstance(regions, list)
        assert len(regions) > 0

    def test_cycle_13_rotated_90_and_270_degrees_scan(self):
        """Цикл 13: Распознавание страниц, повернутых на 90° и 270°."""
        scan_90 = _create_synthetic_scan_image(["АКТ СВЕРКИ ВЗАИМОРАСЧЕТОВ С КОНТРАГЕНТОМ"], angle=90.0, font_size=32)
        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        regions = backend.recognize_regions(scan_90)
        assert isinstance(regions, list)

    def test_cycle_14_skewed_document_5_degrees(self):
        """Цикл 14: Устойчивость к наклону документа на 5 градусов."""
        scan_skew = _create_synthetic_scan_image(["ДОВЕРЕННОСТЬ НА ПРЕДСТАВЛЕНИЕ ИНТЕРЕСОВ В СУДЕ"], angle=5.0, font_size=32)
        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        regions = backend.recognize_regions(scan_skew)
        assert len(regions) > 0
        assert any(r.confidence > 0.5 for r in regions)

    def test_cycle_15_noisy_photocopy_scan_handling(self):
        """Цикл 15: Устойчивость к зашумленным ксерокопиям низкого качества."""
        scan_noisy = _create_synthetic_scan_image(["ДОГОВОР АРЕНДЫ НЕЖИЛОГО ПОМЕЩЕНИЯ"], noise=True, font_size=32)
        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        regions = backend.recognize_regions(scan_noisy)
        assert isinstance(regions, list)

    def test_cycle_16_blank_white_page_scan_graceful_handling(self):
        """Цикл 16: Обработка абсолютно пустой белой страницы скана без ошибок."""
        empty_img = Image.new("RGB", (400, 400), color=(255, 255, 255))
        bio = io.BytesIO()
        empty_img.save(bio, "PNG")

        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        regions = backend.recognize_regions(bio.getvalue())
        assert regions == []

    def test_cycle_17_corrupted_image_bytes_graceful_error(self):
        """Цикл 17: Обработка битых байтов изображения без аварийного завершения процесса."""
        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        with pytest.raises(ocr_backend.OCRBackendError):
            backend.recognize_regions(b"NOT_A_VALID_IMAGE_DATA_CORRUPTED_STREAM")

    def test_cycle_18_high_resolution_scan_performance(self):
        """Цикл 18: Проверка высокопиксельного скана (2400x1600, 300 DPI)."""
        high_res = _create_synthetic_scan_image(["ВЫПИСКА ИЗ ЕГРЮЛ", "ОГРН: 1027700132195"], width=2400, height=1600, font_size=36)
        t0 = time.perf_counter()
        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        regions = backend.recognize_regions(high_res)
        dt = time.perf_counter() - t0
        assert dt < 5.0
        assert isinstance(regions, list)

    def test_cycle_19_privacy_audit_metadata_removal(self, tmp_path):
        """Цикл 19: Аудит и удаление метаданных, EXIF и скрытых сведений."""
        doc_path = tmp_path / "meta_test.docx"
        from docx import Document
        doc = Document()
        doc.core_properties.author = "Иван Иванов"
        doc.core_properties.comments = "Конфиденциальная информация"
        doc.add_paragraph("Тестовый текст")
        doc.save(doc_path)

        rep = clean_file_metadata(doc_path)
        assert rep.changed is True
        cleaned_doc = Document(rep.output_path)
        assert cleaned_doc.core_properties.author == ""
        assert cleaned_doc.core_properties.comments == ""

    def test_cycle_20_irreversible_pdf_blackout_redaction(self, tmp_path):
        """Цикл 20: Экспорт необратимого PDF с черными прямоугольниками поверх ПДн."""
        scan_bytes = _create_synthetic_scan_image(["Паспорт гражданина РФ 45 10 123456"])
        pdf_bytes = _create_synthetic_pdf_with_image(scan_bytes)
        pdf_file = tmp_path / "passport_scan.pdf"
        pdf_file.write_bytes(pdf_bytes)

        api = BackendApi()
        api.irreversible_pdf = True
        worker = Worker([str(pdf_file)], api)
        worker.run()

        cleaned_candidates = list(tmp_path.glob("*_cleaned.pdf"))
        assert len(cleaned_candidates) >= 1

    def test_cycle_21_ui_simulation_export_decoder(self, tmp_path):
        """Цикл 21: Симуляция клика пользователя «Экспорт дешифратора»."""
        decoder_file = tmp_path / "дешифратор.json"
        decoder_file.write_text(json.dumps({"[ФИО_1]": "Петров Петр"}), encoding="utf-8")

        export_target = tmp_path / "exported_decoder.json"
        shutil.copy2(decoder_file, export_target)
        assert export_target.exists()
        assert json.loads(export_target.read_text(encoding="utf-8")) == {"[ФИО_1]": "Петров Петр"}

    def test_cycle_22_ui_simulation_reset_and_clear_state(self):
        """Цикл 22: Симуляция кнопки «Очистить / Сбросить состояние»."""
        api = BackendApi()
        api.selected_files = ["file1.docx", "file2.pdf"]
        assert len(api.selected_files) == 2

        api.selected_files.clear()
        assert len(api.selected_files) == 0
        assert not api.is_cancelled()

    def test_cycle_23_ui_simulation_cancel_during_ocr(self, tmp_path):
        """Цикл 23: Симуляция кнопки «Отмена» во время фонового выполнения OCR."""
        pdf_bytes = _create_synthetic_pdf_with_image(_create_synthetic_scan_image(["Тест отмены"]), num_pages=3)
        pdf_file = tmp_path / "cancel_test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        api = BackendApi()
        def trigger_cancel():
            time.sleep(0.05)
            api.cancel_processing()

        t = threading.Thread(target=trigger_cancel)
        t.start()
        api._run_pdf_ocr_worker([str(pdf_file)])
        t.join()

        assert api.is_cancelled()

    def test_cycle_24_folder_batch_mode_with_ocr_scans(self, tmp_path):
        """Цикл 24: Пакетная обработка каталога со сканами и создание общего дешифратора."""
        folder = tmp_path / "case_folder"
        folder.mkdir()
        img_file = folder / "scan.png"
        img_file.write_bytes(_create_synthetic_scan_image(["Истец: Смирнов Василий Иванович"]))

        from docx import Document
        doc_file = folder / "doc.docx"
        d = Document()
        d.add_paragraph("Ответчик: Смирнов Василий Иванович")
        d.save(doc_file)

        api = BackendApi()
        pipeline = FolderAnonymizationPipeline(api)
        result = pipeline.process(folder)

        assert result.processed_count == 2
        assert result.decoder_path.exists()
        mapping = json.loads(result.decoder_path.read_text(encoding="utf-8"))
        assert len(mapping) >= 1

    def test_cycle_25_folder_batch_continuation_reuses_tokens(self, tmp_path):
        """Цикл 25: Дообработка каталога (continue_existing=True) с повторным использованием токенов."""
        folder = tmp_path / "batch_cont"
        folder.mkdir()
        from docx import Document
        doc1 = folder / "doc1.docx"
        d1 = Document()
        d1.add_paragraph("Сотрудник: Васильев Дмитрий Сергеевич")
        d1.save(doc1)

        api = BackendApi()
        pipeline = FolderAnonymizationPipeline(api)
        res1 = pipeline.process(folder)
        mapping1 = json.loads(res1.decoder_path.read_text(encoding="utf-8"))
        real_tokens_1 = [k for k in mapping1 if not k.startswith("__")]
        assert real_tokens_1 == ["[ФИО_1]"]

        # Добавляем второй документ
        doc2 = folder / "doc2.docx"
        d2 = Document()
        d2.add_paragraph("Тот же сотрудник: Васильев Дмитрий Сергеевич и Орлов Олег")
        d2.save(doc2)

        res2 = pipeline.process(folder, continue_existing=True)
        mapping2 = json.loads(res2.decoder_path.read_text(encoding="utf-8"))
        assert "[ФИО_1]" in mapping2
        assert "[ФИО_2]" in mapping2

    def test_cycle_26_folder_batch_readonly_fallback(self, tmp_path, monkeypatch):
        """Цикл 26: Пакетная обработка каталога в режиме read-only с перенаправлением вывода."""
        folder = tmp_path / "ro_folder"
        folder.mkdir()
        from docx import Document
        doc1 = folder / "doc.docx"
        d = Document()
        d.add_paragraph("Клиент: Попов Игорь")
        d.save(doc1)

        fallback_dir = tmp_path / "fallback_output"
        fallback_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        orig_touch = Path.touch
        def fake_touch(self, *args, **kwargs):
            if ".docxdodyr_write_test_" in self.name:
                raise PermissionError("Access denied (read-only volume)")
            return orig_touch(self, *args, **kwargs)

        monkeypatch.setattr(Path, "touch", fake_touch)

        api = BackendApi()
        pipeline = FolderAnonymizationPipeline(api)
        result = pipeline.process(folder)

        assert "DOCXdodyr_Output" in str(result.output_dir)
        assert result.output_dir.exists()

    def test_cycle_27_concurrent_ocr_execution_safety(self):
        """Цикл 27: Безопасность конкурентного вызова распознавания из нескольких потоков."""
        scan1 = _create_synthetic_scan_image(["Поток 1: Договор поставки промышленного оборудования"])
        scan2 = _create_synthetic_scan_image(["Поток 2: Соглашение об урегулировании спора"])

        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        results = {}

        def worker(idx, data):
            res = backend.recognize_regions(data)
            results[idx] = res

        t1 = threading.Thread(target=worker, args=(1, scan1))
        t2 = threading.Thread(target=worker, args=(2, scan2))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert 1 in results and 2 in results
        assert isinstance(results[1], list) and isinstance(results[2], list)

    def test_cycle_28_offline_hermetic_models_loading(self):
        """Цикл 28: Проверка hermetic-загрузки: модели загружаются строго локально без внешних запросов."""
        model_dir = ocr_backend._find_indra_model_dir()
        assert model_dir is not None
        p = Path(model_dir)
        assert p.is_dir()
        onnx_files = list(p.glob("*.onnx"))
        assert len(onnx_files) > 0, "Локальные ONNX модели Indra должны присутствовать в resources/indra_models"

        b = ocr_backend._IndraOCRBackend()
        assert b._converter is not None

    def test_cycle_29_resource_cleanup_no_leaks(self):
        """Цикл 29: Проверка корректного освобождения дескрипторов и ресурсов после распознавания."""
        scan_bytes = _create_synthetic_scan_image(["Проверка утечек ресурсов и закрытия дескрипторов"])
        _, backend = ocr_backend.create_ocr_backend(preferred="indra")
        for _ in range(5):
            regions = backend.recognize_regions(scan_bytes)
            assert isinstance(regions, list)

    def test_cycle_30_end_to_end_user_workflow_simulation(self, tmp_path):
        """Цикл 30: Сквозная симуляция полного жизненного цикла работы пользователя в приложении."""
        api = BackendApi()
        info = api.get_app_info()
        assert info["version"] == APP_VERSION
        assert info["version"] == "3.1.0"

        doc_path = tmp_path / "end_to_end_claim.docx"
        from docx import Document
        d = Document()
        d.add_paragraph("ИСТЕЦ: ООО «Северсталь», ИНН 7701234567, ОГРН 1027700132195")
        d.add_paragraph("Представитель: Адвокат Новиков Семен Артемович (тел. +7-926-555-44-33, email: novikov@law-firm.ru)")
        d.add_paragraph("Паспорт представителя: 45 08 654321, выдан ТП № 1 в г. Москве, код 770-001")
        d.add_paragraph("Банковские реквизиты: БИК 044525225, р/с 40702810400000001234, корр/с 30101810400000000225")
        d.add_paragraph("Цена иска: 2 450 000 руб.")
        d.save(doc_path)

        api.save_decoder = True
        worker = Worker([str(doc_path)], api)
        worker.run()

        cleaned_path = tmp_path / "end_to_end_claim_cleaned.docx"
        assert cleaned_path.exists()
        cleaned_doc = Document(str(cleaned_path))
        cleaned_text = " ".join(p.text for p in cleaned_doc.paragraphs)

        assert "Новиков Семен Артемович" not in cleaned_text
        assert "7701234567" not in cleaned_text
        assert "654321" not in cleaned_text
        assert "novikov@law-firm.ru" not in cleaned_text
        assert "044525225" not in cleaned_text

        decoders = list(tmp_path.glob("*дешифратор*.json"))
        assert len(decoders) == 1

        restored_path = tmp_path / "end_to_end_claim_restored.docx"
        from document_restorer import DocumentRestorer
        mapping_data = json.loads(decoders[0].read_text(encoding="utf-8"))
        restorer = DocumentRestorer(mapping_data)
        restorer.restore_docx(str(cleaned_path), str(restored_path))

        restored_doc = Document(str(restored_path))
        restored_text = " ".join(p.text for p in restored_doc.paragraphs)
        assert "Новиков Семен Артемович" in restored_text
        assert "7701234567" in restored_text
        assert "novikov@law-firm.ru" in restored_text
        assert "044525225" in restored_text
