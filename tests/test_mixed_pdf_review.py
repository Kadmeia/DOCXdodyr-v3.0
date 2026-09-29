"""Synthetic regressions for text headings above scanned PDF content."""

import fitz
import pytest

import ocr_backend
import pdf_convert


HEADING = "Contract attachment with confidential scanned details"
SCAN_TEXT = "Synthetic account 1234567890 belongs to Example Person"


def _mixed_page(path, *, unused_image=False):
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((20, 30), HEADING, fontsize=10)
        heading_stream = page.get_contents()[0]
        # The body is rasterized so its words are absent from the text layer.
        with fitz.open() as scan:
            body = scan.new_page(width=400, height=400)
            body.insert_text((20, 100), SCAN_TEXT, fontsize=10)
            image = body.get_pixmap().tobytes("png")
        page.insert_image(fitz.Rect(0, 80, 400, 480), stream=image)
        if unused_image:
            page.set_contents(heading_stream)
        document.save(path)


def test_native_heading_does_not_hide_raster_body(tmp_path, monkeypatch):
    path = tmp_path / "heading and scan.pdf"
    _mixed_page(path)
    assert SCAN_TEXT not in pdf_convert.extract_text_from_pdf(str(path))
    calls = []

    class Backend:
        def recognize_regions(self, image_bytes):
            calls.append(image_bytes)
            return [ocr_backend.OCRTextRegion(HEADING),
                    ocr_backend.OCRTextRegion(SCAN_TEXT)]

    monkeypatch.setattr(
        ocr_backend, "create_ocr_backend",
        lambda **kwargs: (ocr_backend.OCRBackendInfo("mock", "Mock", "test"), Backend()),
    )
    monkeypatch.setattr(pdf_convert, "_OCR_BACKEND_CACHE", {})
    text, method, details = pdf_convert.pdf_to_text_auto_detailed(str(path))
    assert len(calls) == 1
    assert SCAN_TEXT in text
    assert HEADING in text
    assert method == "mixed"
    assert details.pages[0].source == "ocr"


def test_empty_ocr_cannot_fall_back_to_native_heading(tmp_path, monkeypatch):
    path = tmp_path / "unreadable scan.pdf"
    _mixed_page(path)
    monkeypatch.setattr(
        pdf_convert, "ocr_pdf_to_result",
        lambda *args, **kwargs: ocr_backend.OCRDocumentResult(
            text="", pages=(ocr_backend.OCRPageResult(page=1, width=800, height=1000,
                                                      regions=()),), backend="mock",
        ),
    )
    with pytest.raises(pdf_convert.PDFTextQualityError, match="стр.1: пустой текст"):
        pdf_convert.pdf_to_text_auto_detailed(str(path))


def test_unused_image_resource_does_not_require_ocr(tmp_path, monkeypatch):
    path = tmp_path / "unused image resource.pdf"
    _mixed_page(path, unused_image=True)
    with fitz.open(path) as document:
        assert document[0].get_images(full=True)
        assert not document[0].get_image_info()
    def unexpected_ocr(*args, **kwargs):
        pytest.fail("Unused image resource must not trigger OCR")
    monkeypatch.setattr(pdf_convert, "ocr_pdf_to_result", unexpected_ocr)
    text, method, details = pdf_convert.pdf_to_text_auto_detailed(str(path))
    assert HEADING in text
    assert method == "text"
    assert details is None
