from pathlib import Path
from types import SimpleNamespace

import backend_api
import fitz
from ocr_backend import OCRDocumentResult, OCRPageResult, OCRTextRegion
from tests.test_folder_pipeline import _backend


def test_pdf_ocr_is_anonymized_page_by_page_with_shared_ids(monkeypatch, tmp_path: Path):
    source = tmp_path / "scan.pdf"
    source_pdf = fitz.open()
    source_pdf.new_page().insert_text((30, 40), "scan")
    source_pdf.save(source)
    source_pdf.close()
    pages = tuple(
        OCRPageResult(
            page=index,
            width=100,
            height=100,
            regions=(OCRTextRegion("Иванов Иван Иванович", page=index),),
        )
        for index in (1, 2)
    )
    details = OCRDocumentResult(
        text="Иванов Иван Иванович\n\nИванов Иван Иванович",
        pages=pages,
        backend="mock",
    )
    monkeypatch.setattr(
        backend_api,
        "pdf_to_text_auto_detailed",
        lambda *args, **kwargs: (details.text, "ocr", details),
    )
    monkeypatch.setattr(backend_api, "PDF_CONVERSION_AVAILABLE", True)
    def convert(_source, output):
        pdf = fitz.open()
        pdf.new_page().insert_text((30, 40), "[ФИО_1]")
        pdf.save(output)
        pdf.close()
        return True

    monkeypatch.setattr(backend_api, "convert_to_pdf", convert)
    monkeypatch.setattr(backend_api, "convert_docx_text_to_pdf", convert)

    api = _backend()
    mapping, seen = {}, {}
    count = api.process_single_file(
        source,
        set(),
        set(),
        batch_mapping=mapping,
        batch_entity_seen=seen,
        defer_decoder=True,
        output_dir=tmp_path / "out",
    )
    assert count == 2
    assert list(mapping) == ["[ФИО_1]"]
