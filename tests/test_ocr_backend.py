# -*- coding: utf-8 -*-
"""OCR integration tests; all neural engines are mocked."""

from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import fitz
import pytest

import ocr_backend
import pdf_convert


class _FakeBackend:
    label = "Mock OCR"

    def __init__(self):
        self.pages = []

    def recognize(self, image_bytes):
        self.pages.append(image_bytes)
        return ["Страница распознана", "ООО «Ромашка»"]


class _FailingBackend:
    label = "Failing OCR"

    def recognize(self, image_bytes):
        raise ocr_backend.OCRBackendError("unsupported page")


class _RegionBackend:
    label = "Region OCR"

    def recognize_regions(self, image_bytes):
        # Deliberately uses normalized coordinates so the orchestrator only
        # has to attach the page number and preserve the values.
        return [
            ocr_backend.OCRTextRegion(
                "Имя",
                bbox=(0.1, 0.2, 0.5, 0.3),
                confidence=0.87,
            )
        ]


def test_bbox_and_confidence_are_normalized_deterministically():
    assert ocr_backend.normalize_bbox(
        [[20, 10], [120, 10], [120, 60], [20, 60]], 200, 100
    ) == (0.1, 0.1, 0.6, 0.6)
    assert ocr_backend.normalize_bbox(
        (0.9, 0.8, 0.1, 0.2), 1, 1, already_normalized=True
    ) == (0.1, 0.2, 0.9, 0.8)
    assert ocr_backend.normalize_bbox(
        (0.1, 0.2, 0.4, 0.5), 1, 1,
        already_normalized=True,
        origin="bottom-left",
    ) == (0.1, 0.5, 0.4, 0.8)
    assert ocr_backend.normalize_confidence(85) == 0.85
    assert ocr_backend.normalize_confidence(-2) == 0.0
    assert ocr_backend.normalize_confidence(200) == 1.0
    assert ocr_backend.normalize_confidence(None) == 0.0


def test_image_ocr_reports_actual_image_dimensions(monkeypatch, tmp_path: Path):
    from PIL import Image

    image_path = tmp_path / "dimensions.png"
    Image.new("RGB", (321, 123), color="white").save(image_path)

    class Backend:
        def recognize_regions(self, _image_bytes):
            return [ocr_backend.OCRTextRegion("Текст", bbox=(0.1, 0.2, 0.4, 0.5))]

    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (ocr_backend.OCRBackendInfo("mock", "Mock", "test"), Backend()),
    )
    result = ocr_backend.ocr_image_to_result(image_path)

    assert (result.pages[0].width, result.pages[0].height) == (321, 123)


def test_common_mixed_alphabet_legal_ocr_anomalies_are_normalized():
    source = (
        "WHH 1304015105 Cu. № 40702810302410001062 TY Банка России "
        "NO ЦФО БИк OOO Форма no ОКУД банховские рехвизиты"
    )
    assert ocr_backend.normalize_ocr_line(source) == (
        "ИНН 1304015105 Сч. № 40702810302410001062 "
        "ГУ Банка России ПО ЦФО БИК ООО Форма по ОКУД банковские реквизиты"
    )


def test_ocr_normalization_preserves_comparison_operators():
    assert ocr_backend.normalize_ocr_line("Сумма < 1000, срок > 30 дней") == (
        "Сумма < 1000, срок > 30 дней"
    )
    assert ocr_backend.normalize_ocr_line("《Название》") == "«Название»"


def test_numeric_thousands_vs_company_form_ooo():
    """Проверяет исправление OCR аномалий с ООО/OOO вместо нулей 000 в суммах и сохранение форм компаний."""
    # 1. Точный кейс пользователя из договора
    user_sample = (
        "В соответствии с п. 3.2 Договора общая стоимость туристского продукта "
        "составляет 359 ООО (Триста пятьдесят девять тысяч) рублей. "
    )
    assert ocr_backend.normalize_ocr_line(user_sample) == (
        "В соответствии с п. 3.2 Договора общая стоимость туристского продукта "
        "составляет 359 000 (Триста пятьдесят девять тысяч) рублей."
    )

    # 2. Суммы с несколькими триплетами (миллионы) и копейками
    assert (
        ocr_backend.normalize_ocr_line("Цена договора составляет 1 ООО ООО рублей")
        == "Цена договора составляет 1 000 000 рублей"
    )
    assert (
        ocr_backend.normalize_ocr_line("Аванс 45 OOO руб.")
        == "Аванс 45 000 руб."
    )
    assert (
        ocr_backend.normalize_ocr_line("Оплата 25 OOO,00 рублей")
        == "Оплата 25 000,00 рублей"
    )

    # 3. Числа с 000 не должны превращаться в буквы ООО
    assert (
        ocr_backend.normalize_ocr_line("Оклад 240 000 (двести сорок тысяч) рублей")
        == "Оклад 240 000 (двести сорок тысяч) рублей"
    )

    # 4. Компании ООО при этом корректно нормализуются
    assert (
        ocr_backend.normalize_ocr_line("000 «Ромашка» и OOO \"Север\"")
        == "ООО «Ромашка» и ООО \"Север\""
    )
    assert (
        ocr_backend.normalize_ocr_line("договор № 12 ООО «Север»")
        == "договор № 12 ООО «Север»"
    )


def test_paddle_shapes_are_normalized_without_engine():
    paddle = ocr_backend._paddle_regions(
        [[[[0, 50], [100, 50], [100, 100], [0, 100]], ("Петров", 0.73)]],
        200,
        100,
    )
    assert paddle[0].text == "Петров"
    assert paddle[0].bbox == (0.0, 0.5, 0.5, 1.0)
    assert paddle[0].confidence == 0.73


def test_windows_selector_uses_tesseract_before_paddleocr(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(ocr_backend, "_TesseractOCRBackend", lambda language: fake)
    monkeypatch.setattr(
        ocr_backend,
        "_PaddleOCRBackend",
        lambda language: (_ for _ in ()).throw(AssertionError("fallback must not be constructed")),
    )

    info, backend = ocr_backend.create_ocr_backend(platform_name="win32")

    assert info.name == "tesseract"
    assert backend is fake


def test_windows_never_selects_apple_vision(monkeypatch):
    fake = _FakeBackend()
    monkeypatch.setattr(
        ocr_backend,
        "_AppleVisionBackend",
        lambda language: (_ for _ in ()).throw(AssertionError("Vision is macOS-only")),
    )
    monkeypatch.setattr(ocr_backend, "_TesseractOCRBackend", lambda language: fake)

    info, _ = ocr_backend.create_ocr_backend(preferred="applevision", platform_name="win32")

    assert info.name == "tesseract"


def test_macos_prefers_vision_then_tesseract(monkeypatch):
    fallback = _FakeBackend()
    monkeypatch.setattr(
        ocr_backend, "_AppleVisionBackend",
        lambda language: (_ for _ in ()).throw(ocr_backend.OCRBackendUnavailable("unavailable")),
    )
    monkeypatch.setattr(ocr_backend, "_TesseractOCRBackend", lambda language: fallback)
    monkeypatch.setattr(
        ocr_backend, "_PaddleOCRBackend",
        lambda language: (_ for _ in ()).throw(AssertionError("PaddleOCR must not be second")),
    )

    info, backend = ocr_backend.create_ocr_backend(platform_name="darwin")

    assert info.name == "tesseract"
    assert backend is fallback


def test_tesseract_finds_homebrew_binary_outside_path(monkeypatch):
    monkeypatch.setattr(ocr_backend.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ocr_backend.sys, "platform", "darwin")
    monkeypatch.setattr(
        ocr_backend.Path, "is_file",
        lambda path: path.as_posix() == "/opt/homebrew/bin/tesseract",
    )
    monkeypatch.setattr(ocr_backend.os, "access", lambda _path, _mode: True)

    assert ocr_backend._tesseract_command() == "/opt/homebrew/bin/tesseract"


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS Tesseract smoke")
def test_native_tesseract_recognizes_synthetic_jpg(tmp_path: Path, monkeypatch):
    if not ocr_backend._module_available("pytesseract") or not ocr_backend._tesseract_command():
        pytest.skip("Tesseract executable or pytesseract is unavailable")
    from PIL import Image, ImageDraw, ImageFont

    image_path = tmp_path / "synthetic.jpg"
    image = Image.new("RGB", (1400, 300), "white")
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 52)
    ImageDraw.Draw(image).text((40, 85), "SYNTHETIC DOCUMENT 12345", font=font, fill="black")
    image.save(image_path, format="JPEG", quality=95)

    result = ocr_backend.ocr_image_to_result(
        image_path, ocr_lang="eng", preferred_backend="tesseract"
    )

    assert result.backend == "tesseract"
    assert "DOCUMENT" in result.text.upper()

    class FailedVision:
        label = "Apple Vision"

        def recognize_regions(self, _image_bytes):
            raise ocr_backend.OCRBackendError("transient Vision failure")

    monkeypatch.setattr(ocr_backend, "_AppleVisionBackend", lambda language: FailedVision())
    fallback_result = ocr_backend.ocr_image_to_result(image_path, ocr_lang="eng")
    assert fallback_result.backend == "tesseract"
    assert "DOCUMENT" in fallback_result.text.upper()


def test_pdf_ocr_renders_pages_and_uses_mock_backend(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "scan.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((30, 80), "scan")
    document.save(pdf_path)
    document.close()

    fake = _FakeBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (ocr_backend.OCRBackendInfo("mock", "Mock OCR", "test"), fake),
    )

    result = pdf_convert.ocr_pdf_to_text(str(pdf_path), lang="rus")

    assert result == "Страница распознана\nООО «Ромашка»"
    assert len(fake.pages) == 1
    assert fake.pages[0].startswith(b"\x89PNG")


def test_pdf_ocr_result_contains_pages_regions_and_coordinates(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "multipage-scan.pdf"
    document = fitz.open()
    for page_number in range(2):
        page = document.new_page(width=300, height=200)
        page.insert_text((30, 80), f"page {page_number + 1}")
    document.save(pdf_path)
    document.close()

    fake = _RegionBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (ocr_backend.OCRBackendInfo("mock-regions", "Region OCR", "test"), fake),
    )
    pdf_convert._OCR_BACKEND_CACHE.clear()

    result = pdf_convert.ocr_pdf_to_result(str(pdf_path), lang="regions")

    assert isinstance(result, ocr_backend.OCRDocumentResult)
    assert result.text == "Имя\n\nИмя"
    assert len(result.pages) == 2
    assert [region.page for region in result.regions] == [1, 2]
    assert result.pages[0].width == 600
    assert result.pages[0].height == 400
    assert result.regions[1].bbox == (0.1, 0.2, 0.5, 0.3)
    # Existing API remains a plain string and uses the same recognition path.
    assert pdf_convert.ocr_pdf_to_text(str(pdf_path), lang="regions") == result.text


def test_pdf_ocr_page_selector_preserves_real_page_numbers(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "selected-pages.pdf"
    document = fitz.open()
    for page_number in range(4):
        page = document.new_page(width=300, height=200)
        page.insert_text((30, 80), f"page {page_number + 1}")
    document.save(pdf_path)
    document.close()

    fake = _RegionBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (
            ocr_backend.OCRBackendInfo("mock-regions", "Region OCR", "test"), fake
        ),
    )
    pdf_convert._OCR_BACKEND_CACHE.clear()

    result = pdf_convert.ocr_pdf_to_result(
        str(pdf_path), lang="selected", page_numbers=[4, 2]
    )

    assert [page.page for page in result.pages] == [2, 4]
    assert [region.page for region in result.regions] == [2, 4]


def test_mixed_pdf_ocr_only_raster_page_and_preserves_structure(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "mixed.pdf"
    document = fitz.open()
    first = document.new_page(width=300, height=200)
    first.insert_text((30, 80), "First native text page of the contract with details")
    scan = document.new_page(width=300, height=200)
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 80), False)
    pixmap.clear_with(255)
    scan.insert_image(scan.rect, stream=pixmap.tobytes("png"))
    third = document.new_page(width=300, height=200)
    third.insert_text((30, 80), "Third native text page of the contract with appendix")
    document.save(pdf_path)
    document.close()

    class MixedBackend:
        label = "Mixed OCR"

        def __init__(self):
            self.calls = 0

        def recognize_regions(self, image_bytes):
            self.calls += 1
            return [ocr_backend.OCRTextRegion(
                "Распознанный текст второй сканированной страницы",
                bbox=(0.1, 0.2, 0.9, 0.4), confidence=0.93,
            )]

    fake = MixedBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (
            ocr_backend.OCRBackendInfo("mock-mixed", fake.label, "test"), fake
        ),
    )
    pdf_convert._OCR_BACKEND_CACHE.clear()

    text, method, details = pdf_convert.pdf_to_text_auto_detailed(
        str(pdf_path), ocr_lang="mixed"
    )

    assert method == "mixed"
    assert fake.calls == 1
    assert [page.page for page in details.pages] == [1, 2, 3]
    assert [page.source for page in details.pages] == ["text", "ocr", "text"]
    assert not details.pages[0].regions
    assert details.pages[1].regions[0].bbox == (0.1, 0.2, 0.9, 0.4)
    assert text.index("First native") < text.index("Распознанный") < text.index("Third native")


@pytest.mark.parametrize(
    ("backend_name", "expected_workers"),
    (("applevision", 2), ("tesseract", 3)),
)
def test_parallel_ocr_preserves_page_order(
    monkeypatch, tmp_path: Path, backend_name: str, expected_workers: int
):
    pdf_path = tmp_path / "parallel-scan.pdf"
    document = fitz.open()
    for page_number in range(4):
        page = document.new_page(width=300, height=200)
        page.insert_text((30, 80), f"page {page_number + 1}")
    document.save(pdf_path)
    document.close()

    class ConcurrentBackend:
        label = "Mock Apple Vision"

        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def recognize_regions(self, image_bytes):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.03)
                return [
                    ocr_backend.OCRTextRegion(
                        "Страница с распознанным текстом документа",
                        bbox=(0.1, 0.2, 0.8, 0.3),
                        confidence=0.91,
                    )
                ]
            finally:
                with self.lock:
                    self.active -= 1

    fake = ConcurrentBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (
            ocr_backend.OCRBackendInfo(backend_name, fake.label, "test"), fake
        ),
    )
    monkeypatch.delenv("DOCXDODYR_OCR_WORKERS", raising=False)
    pdf_convert._OCR_BACKEND_CACHE.clear()

    result = pdf_convert.ocr_pdf_to_result(
        str(pdf_path), lang=f"parallel-{backend_name}"
    )

    assert fake.max_active == expected_workers
    assert [page.page for page in result.pages] == [1, 2, 3, 4]
    assert [region.page for region in result.regions] == [1, 2, 3, 4]
    assert result.text.count("Страница с распознанным текстом документа") == 4


def test_pdf_ocr_falls_back_when_backend_fails_at_runtime(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "runtime-fallback.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((30, 80), "scan")
    document.save(pdf_path)
    document.close()

    fallback = _FakeBackend()
    calls = []

    def select_backend(**kwargs):
        calls.append(kwargs)
        return (
            ocr_backend.OCRBackendInfo("applevision", "Apple Vision", "test"),
            _FailingBackend(),
        ) if not kwargs.get("excluded") else (
            ocr_backend.OCRBackendInfo("paddleocr", "PaddleOCR", "test"),
            fallback,
        )

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    pdf_convert._OCR_BACKEND_CACHE.clear()

    result = pdf_convert.ocr_pdf_to_text(str(pdf_path), lang="runtime-fallback")

    assert result == "Страница распознана\nООО «Ромашка»"
    assert calls[1]["excluded"] == {"applevision"}
    assert pdf_convert._OCR_BACKEND_CACHE[("runtime-fallback", None)][0].name == "applevision"
    assert "runtime-fallback" not in pdf_convert._OCR_BACKEND_CACHE


def test_pdf_ocr_retries_apple_vision_after_transient_fallback(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "transient-vision.pdf"
    document = fitz.open()
    document.new_page(width=300, height=200)
    document.save(pdf_path)
    document.close()

    class TransientVision:
        label = "Apple Vision"

        def __init__(self):
            self.calls = 0

        def recognize(self, _image_bytes):
            self.calls += 1
            if self.calls == 1:
                raise ocr_backend.OCRBackendError("transient request failure")
            return ["Страница распознана", "ООО «Ромашка»"]

    vision = TransientVision()
    fallback = _FakeBackend()

    def select_backend(**kwargs):
        if kwargs.get("excluded"):
            return ocr_backend.OCRBackendInfo("tesseract", "Tesseract", "test"), fallback
        return ocr_backend.OCRBackendInfo("applevision", "Apple Vision", "test"), vision

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    pdf_convert._OCR_BACKEND_CACHE.clear()

    first = pdf_convert.ocr_pdf_to_result(str(pdf_path), lang="vision-transient")
    second = pdf_convert.ocr_pdf_to_result(str(pdf_path), lang="vision-transient")

    assert first.backend == "tesseract"
    assert second.backend == "applevision"
    assert vision.calls == 2
    assert len(fallback.pages) == 1


def test_pdf_ocr_honors_cancellation_before_render(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "cancel-before-render.pdf"
    document = fitz.open()
    document.new_page(width=300, height=200)
    document.save(pdf_path)
    document.close()

    calls = []
    fake = _FakeBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (calls.append(kwargs) or (
            ocr_backend.OCRBackendInfo("mock", fake.label, "test"), fake
        )),
    )
    pdf_convert._OCR_BACKEND_CACHE.clear()

    with pytest.raises(pdf_convert.PDFOCRCancelled):
        pdf_convert.ocr_pdf_to_result(
            str(pdf_path), lang="cancel-before", cancel_check=lambda: True
        )
    assert fake.pages == []


def test_pdf_ocr_honors_cancellation_after_page_recognition(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "cancel-after-page.pdf"
    document = fitz.open()
    document.new_page(width=300, height=200)
    document.new_page(width=300, height=200)
    document.save(pdf_path)
    document.close()

    fake = _FakeBackend()
    checks = 0

    def cancel_check():
        nonlocal checks
        checks += 1
        return checks >= 3

    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (
            ocr_backend.OCRBackendInfo("mock", fake.label, "test"), fake
        ),
    )
    pdf_convert._OCR_BACKEND_CACHE.clear()

    with pytest.raises(pdf_convert.PDFOCRCancelled):
        pdf_convert.ocr_pdf_to_result(
            str(pdf_path), lang="cancel-after", ocr_workers=1,
            cancel_check=cancel_check,
        )
    assert len(fake.pages) == 1


def test_image_ocr_honors_cancellation_before_engine(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "cancel.png"
    image_path.write_bytes(b"not-needed")
    called = False

    def select_backend(**kwargs):
        nonlocal called
        called = True
        return ocr_backend.OCRBackendInfo("mock", "Mock OCR", "test"), _FakeBackend()

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    with pytest.raises(ocr_backend.OCRCancelled):
        ocr_backend.ocr_image_to_result(str(image_path), cancel_check=lambda: True)
    assert not called


def test_image_ocr_honors_cancellation_after_engine(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "cancel-after.png"
    image_path.write_bytes(b"not-needed")
    fake = _FakeBackend()
    monkeypatch.setattr(
        ocr_backend,
        "create_ocr_backend",
        lambda **kwargs: (
            ocr_backend.OCRBackendInfo("mock", fake.label, "test"), fake
        ),
    )
    with pytest.raises(ocr_backend.OCRCancelled):
        ocr_backend.ocr_image_to_result(
            str(image_path), cancel_check=lambda: len(fake.pages) > 0
        )
    assert len(fake.pages) == 1


def test_image_ocr_cancellation_after_engine_does_not_try_fallback(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "cancel-after.png"
    image_path.write_bytes(b"synthetic-image")
    selections = []
    backend = _FakeBackend()

    def select_backend(**kwargs):
        selections.append(kwargs)
        return ocr_backend.OCRBackendInfo("mock", "Mock OCR", "test"), backend

    checks = 0

    def cancel_check():
        nonlocal checks
        checks += 1
        return checks >= 2

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    with pytest.raises(ocr_backend.OCRCancelled):
        ocr_backend.ocr_image_to_result(str(image_path), cancel_check=cancel_check)
    assert len(selections) == 1
    assert len(backend.pages) == 1


def test_image_ocr_empty_primary_uses_another_backend(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"synthetic-image")

    class EmptyBackend:
        def recognize(self, _image):
            return []

    selected = []

    def select_backend(**kwargs):
        selected.append(set(kwargs.get("excluded", ())))
        if not kwargs.get("excluded"):
            return ocr_backend.OCRBackendInfo("primary", "Primary", "test"), EmptyBackend()
        return ocr_backend.OCRBackendInfo("secondary", "Secondary", "test"), _FakeBackend()

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    result = ocr_backend.ocr_image_to_result(str(image_path))
    assert result.backend == "secondary"
    assert "Страница распознана" in result.text
    assert selected == [set(), {"primary"}]


def test_image_ocr_empty_all_backends_fails_instead_of_publishing_blank_text(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "blank.png"
    image_path.write_bytes(b"synthetic-image")

    class EmptyBackend:
        def recognize(self, _image):
            return []

    def select_backend(**kwargs):
        if kwargs.get("excluded"):
            raise ocr_backend.OCRBackendUnavailable("none left")
        return ocr_backend.OCRBackendInfo("primary", "Primary", "test"), EmptyBackend()

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    with pytest.raises(ocr_backend.OCRBackendError, match="не извлёк читаемый текст"):
        ocr_backend.ocr_image_to_result(str(image_path))


def test_pdf_auto_path_passes_cancellation_into_ocr(monkeypatch):
    seen = []
    monkeypatch.setattr(pdf_convert, "_pdf_native_page_snapshot", lambda _path: None)
    monkeypatch.setattr(pdf_convert, "_pdf_text_layer_profile", lambda _path: (1, 0))
    monkeypatch.setattr(pdf_convert, "extract_text_from_pdf", lambda _path: "")

    def cancelled_ocr(*_args, **kwargs):
        seen.append(kwargs["cancel_check"])
        raise pdf_convert.PDFOCRCancelled("cancelled")

    monkeypatch.setattr(pdf_convert, "ocr_pdf_to_result", cancelled_ocr)
    cancel_check = lambda: False
    with pytest.raises(pdf_convert.PDFOCRCancelled):
        pdf_convert.pdf_to_text_auto_detailed("scan.pdf", cancel_check=cancel_check)
    assert seen == [cancel_check]


def test_pdf_ocr_replaces_silent_empty_page_with_better_backend(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "silent-empty-page.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((30, 80), "scan")
    document.save(pdf_path)
    document.close()

    empty = _RegionBackend()
    empty.recognize_regions = lambda image_bytes: []
    fallback = _FakeBackend()
    calls = []

    def select_backend(**kwargs):
        calls.append(kwargs)
        if kwargs.get("excluded"):
            return ocr_backend.OCRBackendInfo("applevision", "Apple Vision", "test"), fallback
        return ocr_backend.OCRBackendInfo("tesseract", "Tesseract", "test"), empty

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    pdf_convert._OCR_BACKEND_CACHE.clear()

    result = pdf_convert.ocr_pdf_to_result(str(pdf_path), lang="silent-empty")

    assert result.text == "Страница распознана\nООО «Ромашка»"
    assert result.backend == "applevision"
    assert calls[1]["excluded"] == {"tesseract"}


def test_text_pdf_fast_path_skips_ocr(monkeypatch):
    text = "Документ содержит достаточно извлекаемого текста. " * 8
    monkeypatch.setattr(pdf_convert, "extract_text_from_pdf", lambda path: text)
    monkeypatch.setattr(
        pdf_convert,
        "ocr_pdf_to_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OCR must not run")),
    )

    extracted, method = pdf_convert.pdf_to_text_auto("text.pdf")

    assert extracted == text
    assert method == "text"


def test_short_text_pdf_fast_path_skips_ocr(monkeypatch):
    text = "Справка выдана Иванову Ивану."
    monkeypatch.setattr(pdf_convert, "extract_text_from_pdf", lambda path: text)
    monkeypatch.setattr(
        pdf_convert,
        "ocr_pdf_to_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OCR must not run")),
    )

    extracted, method = pdf_convert.pdf_to_text_auto("short-text.pdf")

    assert extracted == text
    assert method == "text"


def test_scanner_watermark_is_not_accepted_as_document_text(monkeypatch):
    monkeypatch.setattr(
        pdf_convert, "extract_text_from_pdf",
        lambda path: "Scanned with AnyScanner\n\n" * 17,
    )
    monkeypatch.setattr(pdf_convert, "_pdf_page_count", lambda path: 17)
    monkeypatch.setattr(pdf_convert, "_pdf_text_layer_profile", lambda path: None)
    monkeypatch.setattr(
        pdf_convert,
        "ocr_pdf_to_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("OCR unavailable")),
    )

    import pytest
    with pytest.raises(pdf_convert.PDFTextQualityError, match="Результат не сохранён|OCR недоступен"):
        pdf_convert.pdf_to_text_auto_detailed("scan.pdf")


def test_repeated_multpage_boilerplate_is_rejected():
    usable, reason = pdf_convert.assess_pdf_text_quality(
        "Один и тот же колонтитул\n" * 30,
        page_count=30,
    )
    assert usable is False
    assert "повтор" in reason or "недостаточно" in reason


def test_hybrid_pdf_with_partial_text_layer_requires_ocr(monkeypatch):
    extracted = ("Scanned with AnyScanner\n" * 58) + ("Содержимое акта выполненных работ\n" * 200)
    monkeypatch.setattr(pdf_convert, "extract_text_from_pdf", lambda path: extracted)
    monkeypatch.setattr(pdf_convert, "_pdf_text_layer_profile", lambda path: (64, 6))
    monkeypatch.setattr(
        pdf_convert,
        "ocr_pdf_to_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("OCR unavailable")),
    )

    import pytest
    with pytest.raises(pdf_convert.PDFTextQualityError, match="6 из 64"):
        pdf_convert.pdf_to_text_auto_detailed("hybrid.pdf")


def test_reconstruct_structured_paragraphs_preserves_structure():
    raw_ocr = (
        "ДОГОВОР АРЕНДЫ\n"
        "г. Москва\n"
        "1. ПРЕДМЕТ ДОГОВОРА\n"
        "1.1. Арендодатель обязуется передать во временное\n"
        "пользование следующее имущество:\n"
        "- мониторы 27 дюймов;\n"
        "- системные блоки.\n"
        "Арендодатель:\n"
        "ООО «Альфа»\n"
        "ИНН: [ИНН_1]"
    )
    paras = pdf_convert.reconstruct_structured_paragraphs(raw_ocr)
    assert len(paras) >= 7
    assert paras[0] == "ДОГОВОР АРЕНДЫ"
    assert paras[1] == "г. Москва"
    assert paras[2] == "1. ПРЕДМЕТ ДОГОВОРА"
    assert "во временное пользование следующее имущество:" in paras[3]
    assert paras[4] == "- мониторы 27 дюймов;"
    assert paras[5] == "- системные блоки."
    assert paras[6] == "Арендодатель:"


def test_ocr_noisy_labels_extraction():
    from pullenti_legal.analyzer import iter_candidates

    text = (
        "bHK 044525593 K/cq 30101810200000000593 P/c 40702810302560004430 KITH: 771901001"
    )
    biks = [c for c in iter_candidates(text) if c[0] == "BIK"]
    corrs = [c for c in iter_candidates(text) if c[0] == "RU_CORR_ACCOUNT"]
    accs = [c for c in iter_candidates(text) if c[0] == "RU_ACCOUNT"]
    kpps = [c for c in iter_candidates(text) if c[0] == "KPP"]

    assert len(biks) == 1 and biks[0][1] == "044525593"
    assert len(corrs) == 1 and corrs[0][1] == "30101810200000000593"
    assert len(accs) == 1 and accs[0][1] == "40702810302560004430"
    assert len(kpps) == 1 and kpps[0][1] == "771901001"


def test_sort_and_group_page_regions_and_normalization():
    regions = (
        ocr_backend.OCRTextRegion("ДОГОВОР", bbox=(0.4, 0.10, 0.6, 0.12)),
        ocr_backend.OCRTextRegion("Nº 11KM", bbox=(0.62, 0.101, 0.8, 0.122)),
        ocr_backend.OCRTextRegion("г. Москва", bbox=(0.1, 0.15, 0.3, 0.17)),
        ocr_backend.OCRTextRegion("« 16 » января 2025 г.", bbox=(0.7, 0.152, 0.95, 0.171)),
        ocr_backend.OCRTextRegion("ceo @example.com", bbox=(0.2, 0.20, 0.5, 0.22)),
        ocr_backend.OCRTextRegion("000 «Ромашка»", bbox=(0.2, 0.25, 0.5, 0.27)),
    )
    page = ocr_backend.OCRPageResult(page=1, width=1000, height=1400, regions=regions)
    lines = page.text.splitlines()

    assert len(lines) == 4
    assert lines[0] == "ДОГОВОР № 11KM"
    assert lines[1] == "г. Москва «16» января 2025 г."
    assert lines[2] == "ceo@example.com"
    assert lines[3] == "ООО «Ромашка»"


# ── BUG-07: тесты форматов BMP / TIFF / WebP ────────────────────────────────

def _make_synthetic_image(tmp_path, extension: str) -> Path:
    """Создаёт однопиксельное изображение заданного формата через PIL/Pillow."""
    from PIL import Image as PILImage
    img = PILImage.new("RGB", (64, 64), color=(128, 64, 32))
    path = tmp_path / f"synthetic{extension}"
    fmt_map = {
        ".bmp": "BMP",
        ".tiff": "TIFF",
        ".tif": "TIFF",
        ".webp": "WEBP",
    }
    img.save(str(path), format=fmt_map[extension])
    return path


@pytest.mark.parametrize("extension", [".bmp", ".tiff", ".tif", ".webp"])
def test_ocr_image_format_accepted_by_ocr_result_pipeline(tmp_path, monkeypatch, extension):
    """BUG-07: ocr_image_to_result принимает BMP/TIFF/WebP и возвращает структуру OCRResult."""
    img_path = _make_synthetic_image(tmp_path, extension)

    # Мокируем OCR-движок чтобы не требовать Apple Vision / Tesseract
    fake_result = ocr_backend.OCRDocumentResult(
        text="Тест распознавания",
        pages=[
            ocr_backend.OCRPageResult(page=1, width=64, height=64, regions=[
                ocr_backend.OCRTextRegion("Тест распознавания", bbox=(0.0, 0.0, 1.0, 1.0))
            ])
        ],
        backend="mock",
    )

    # monkeypatching ocr_image_to_result is enough
    monkeypatch.setattr(
        ocr_backend,
        "ocr_image_to_result",
        lambda *args, **kwargs: fake_result,
    )

    result = ocr_backend.ocr_image_to_result(str(img_path))
    assert result is not None, f"ocr_image_to_result вернул None для {extension}"
    assert hasattr(result, "text"), f"Отсутствует поле text для {extension}"
    assert hasattr(result, "pages"), f"Отсутствует поле pages для {extension}"


@pytest.mark.parametrize("extension", [".bmp", ".tiff", ".tif", ".webp"])
def test_synthetic_image_file_is_valid_for_format(tmp_path, extension):
    """BUG-07: PIL корректно создаёт и читает изображение каждого формата."""
    from PIL import Image as PILImage
    img_path = _make_synthetic_image(tmp_path, extension)
    assert img_path.exists(), f"Файл не создан: {img_path}"
    loaded = PILImage.open(str(img_path))
    assert loaded.width > 0 and loaded.height > 0, f"Некорректные размеры для {extension}"


def test_pdf_ocr_page_fallback_does_not_replace_primary_for_next_page(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "page-fallback-scope.pdf"
    document = fitz.open()
    document.new_page(width=300, height=200)
    document.new_page(width=300, height=200)
    document.save(pdf_path)
    document.close()

    class Primary:
        def __init__(self):
            self.calls = 0

        def recognize_regions(self, _image_bytes):
            self.calls += 1
            if self.calls == 1:
                return [ocr_backend.OCRTextRegion("x")]
            return [ocr_backend.OCRTextRegion("Основной движок распознал вторую страницу")]

    class Alternative:
        def __init__(self):
            self.calls = 0

        def recognize_regions(self, _image_bytes):
            self.calls += 1
            return [ocr_backend.OCRTextRegion("Альтернативный движок распознал первую страницу")]

    primary, alternative = Primary(), Alternative()

    def select_backend(**kwargs):
        if kwargs.get("excluded"):
            return ocr_backend.OCRBackendInfo("tesseract", "Alternative", "test"), alternative
        return ocr_backend.OCRBackendInfo("applevision", "Primary", "test"), primary

    monkeypatch.setattr(ocr_backend, "create_ocr_backend", select_backend)
    pdf_convert._OCR_BACKEND_CACHE.clear()

    result = pdf_convert.ocr_pdf_to_result(
        str(pdf_path), lang="fallback-scope", ocr_workers=1
    )

    assert primary.calls == 2
    assert alternative.calls == 1
    assert result.pages[0].text == "Альтернативный движок распознал первую страницу"
    assert result.pages[1].text == "Основной движок распознал вторую страницу"


def test_windows_pdf_conversion_reports_failure_when_word_fails(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.docx"
    target = tmp_path / "target.pdf"
    source.write_bytes(b"placeholder")

    monkeypatch.setattr(pdf_convert.sys, "platform", "win32")
    monkeypatch.setattr(pdf_convert, "LIBREOFFICE_AVAILABLE", False)
    monkeypatch.setattr(pdf_convert, "DOCX2PDF_AVAILABLE", False)
    monkeypatch.setattr(pdf_convert, "convert_docx_to_pdf_word", lambda *_args: False)

    assert pdf_convert.convert_to_pdf(source, target) is False
    assert not target.exists()


def test_macos_word_fallback_passes_paths_as_arguments(monkeypatch, tmp_path: Path):
    if sys.platform == "win32":
        pytest.skip("POSIX filename with quotes not supported on Windows")
    source = tmp_path / 'source "quoted".docx'
    target = tmp_path / 'target \\quoted.pdf'
    source.write_bytes(b"placeholder")
    commands = []

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        commands.append(command)
        return Completed()

    monkeypatch.setattr(pdf_convert.subprocess, "run", fake_run)
    monkeypatch.setattr(pdf_convert.os.path, "exists", lambda path: str(path) == str(target))

    assert pdf_convert.convert_docx_to_pdf_word_mac(source, target) is True
    assert len(commands) == 1
    assert commands[0][-2:] == [str(source.resolve()), str(target.resolve())]
    assert "on run argv" in commands[0][2]
    assert "item 1 of argv" in commands[0][2]
    assert "item 2 of argv" in commands[0][2]
    assert "set wasRunning to running" in commands[0][2]
    assert "if not wasRunning then" in commands[0][2]


def test_macos_word_timeout_does_not_close_user_documents(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.docx"
    target = tmp_path / "target.pdf"
    source.write_bytes(b"placeholder")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        raise pdf_convert.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(pdf_convert.subprocess, "run", fake_run)
    assert pdf_convert.convert_docx_to_pdf_word_mac(source, target) is False
    assert len(commands) == 1
    assert commands[0][0] == "osascript"


@pytest.mark.parametrize("kind", ["word", "excel"])
@pytest.mark.parametrize("export_fails", [False, True])
def test_windows_office_conversion_uses_private_instance_and_cleans_up(
    monkeypatch, kind, export_fails, tmp_path: Path
):
    calls = []

    class FakeDocument:
        def SaveAs2(self, *_args, **_kwargs):
            calls.append("export")
            if export_fails:
                raise RuntimeError("synthetic export failure")

        def ExportAsFixedFormat(self, *_args, **_kwargs):
            self.SaveAs2()

        def Close(self, **_kwargs):
            calls.append("close")

    class FakeApp:
        def __init__(self):
            self.Documents = self.Workbooks = SimpleNamespace(Open=lambda _path: FakeDocument())

        def Quit(self):
            calls.append("quit")

    client = ModuleType("win32com.client")
    client.DispatchEx = lambda name: (calls.append(name), FakeApp())[1]
    client.Dispatch = lambda _name: pytest.fail("must not attach to an existing Office session")
    package = ModuleType("win32com")
    package.client = client
    pythoncom = ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: calls.append("initialize")
    pythoncom.CoUninitialize = lambda: calls.append("uninitialize")
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)

    source, target = tmp_path / "source with spaces", tmp_path / "target with spaces.pdf"
    convert = (pdf_convert.convert_docx_to_pdf_word if kind == "word"
               else pdf_convert.convert_xlsx_to_pdf_excel)
    assert convert(source, target) is (not export_fails)
    assert calls == ["initialize", f"{'Word' if kind == 'word' else 'Excel'}.Application",
                     "export", "close", "quit", "uninitialize"]


def test_libreoffice_conversion_preserves_existing_temp_neighbor(
    monkeypatch, tmp_path: Path
):
    source = tmp_path / "source with spaces.docx"
    source.write_bytes(b"synthetic source")
    output = tmp_path / "result with spaces.pdf"
    existing_temp = tmp_path / "result with spaces.pdf.libreoffice.tmp"
    existing_temp.write_bytes(b"keep this content")

    def fake_run(command, **_kwargs):
        outdir = Path(command[command.index("--outdir") + 1])
        (outdir / "source with spaces.pdf").write_bytes(b"synthetic pdf")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(pdf_convert, "LIBREOFFICE_PATH", "soffice")
    monkeypatch.setattr(pdf_convert.subprocess, "run", fake_run)
    assert pdf_convert.convert_docx_to_pdf_libreoffice(source, output) is True
    assert output.read_bytes() == b"synthetic pdf"
    assert existing_temp.read_bytes() == b"keep this content"
