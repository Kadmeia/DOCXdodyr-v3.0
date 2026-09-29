"""PDF export must preserve unrelated files and publish only complete output."""
from pathlib import Path

import fitz
import pytest
from docx import Document

from pdf_convert import convert_docx_text_to_pdf


def test_invalid_docx_preserves_existing_pdf_and_temp_neighbor(tmp_path: Path):
    source = tmp_path / "invalid.docx"
    source.write_bytes(b"invalid synthetic document")
    target = tmp_path / "result.pdf"
    target.write_bytes(b"existing output")
    neighbor = tmp_path / "result.pdf.tmp"
    neighbor.write_bytes(b"unrelated file")

    assert convert_docx_text_to_pdf(source, target) is False
    assert target.read_bytes() == b"existing output"
    assert neighbor.read_bytes() == b"unrelated file"


@pytest.mark.parametrize("save_fails", [False, True])
def test_text_pdf_export_is_atomic_and_cleans_owned_temporary_file(tmp_path: Path, monkeypatch, save_fails):
    source = tmp_path / "source with spaces.docx"
    document = Document()
    document.add_paragraph("Synthetic PDF export")
    document.save(source)
    target = tmp_path / "result with spaces.pdf"
    target.write_bytes(b"existing output")
    neighbor = tmp_path / "result with spaces.pdf.tmp"
    neighbor.write_bytes(b"unrelated file")

    if save_fails:
        def broken_save(self, path, **kwargs):
            Path(path).write_bytes(b"partial PDF")
            raise OSError("synthetic write failure")
        monkeypatch.setattr(fitz.Document, "save", broken_save)

    assert convert_docx_text_to_pdf(source, target) is (not save_fails)
    assert neighbor.read_bytes() == b"unrelated file"
    assert not list(tmp_path.glob(".result with spaces.pdf.*.tmp"))
    if save_fails:
        assert target.read_bytes() == b"existing output"
    else:
        with fitz.open(target) as result:
            assert result[0].get_text().split() == ["Synthetic", "PDF", "export"]
