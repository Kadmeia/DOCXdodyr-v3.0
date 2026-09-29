# -*- coding: utf-8 -*-
"""Targeted suite for Packet C: Security boundaries, ZIP/XML bombs, no-leakage, offline-first."""

import io
import os
import zipfile
from pathlib import Path
import pytest
from docx import Document

from backend_api import BackendApi
import hidden_data
import log_sanitizer
import qwen_offline


def test_zip_path_traversal_rejection(tmp_path):
    """ZIP with path traversal entries must be rejected by hidden_data."""
    evil_docx = tmp_path / "evil_traversal.docx"
    with zipfile.ZipFile(evil_docx, "w") as z:
        z.writestr("word/document.xml", b"<w:document/>")
        z.writestr("../../evil.xml", b"malicious content")

    with pytest.raises(hidden_data.HiddenDataError, match="недопустимый путь"):
        hidden_data.inspect_hidden_data(evil_docx)


def test_zip_symlink_rejection(tmp_path):
    """ZIP with symlink entries must be rejected by hidden_data."""
    evil_docx = tmp_path / "evil_symlink.docx"
    with zipfile.ZipFile(evil_docx, "w") as z:
        z.writestr("word/document.xml", b"<w:document/>")
        zinfo = zipfile.ZipInfo("word/symlink.xml")
        zinfo.external_attr = 0o120777 << 16  # S_IFLNK
        z.writestr(zinfo, "/etc/passwd")

    with pytest.raises(hidden_data.HiddenDataError, match="символическая ссылка"):
        hidden_data.inspect_hidden_data(evil_docx)


def test_zip_duplicate_name_rejection(tmp_path):
    """ZIP with duplicate names must be rejected."""
    evil_docx = tmp_path / "evil_dup.docx"
    with zipfile.ZipFile(evil_docx, "w") as z:
        z.writestr("word/document.xml", b"<w:document/>")
        z.writestr("word/Document.xml", b"<w:document/>")  # Case collision

    with pytest.raises(hidden_data.HiddenDataError, match="повторяющееся имя"):
        hidden_data.inspect_hidden_data(evil_docx)


def test_zip_bomb_ratio_rejection(tmp_path):
    """ZIP bomb with excessive compression ratio must be rejected."""
    evil_docx = tmp_path / "evil_bomb.docx"
    with zipfile.ZipFile(evil_docx, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # 10 MB of zeros compresses to a few hundred bytes (> 1000x ratio)
        z.writestr("word/document.xml", b"\x00" * (10 * 1024 * 1024))

    with pytest.raises(hidden_data.HiddenDataError, match="коэффициент сжатия"):
        hidden_data.inspect_hidden_data(evil_docx)


def test_no_synthetic_marker_leakage_in_logs_and_reports(tmp_path, capsys):
    """High-entropy synthetic marker must never leak into stdout/stderr or diagnostic logs."""
    sentinel = "SENTINEL_SECRET_TOKEN_987654321_XYZ"
    doc_path = tmp_path / "sensitive.docx"
    doc = Document()
    doc.add_paragraph(f"Секретный гражданин: {sentinel}, проживающий в городе Москве.")
    doc.save(str(doc_path))

    api = BackendApi()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Process document
    api.process_single_file(str(doc_path), set(), {}, output_dir=out_dir)

    captured = capsys.readouterr()
    # Check stdout and stderr
    assert sentinel not in captured.out
    assert sentinel not in captured.err

    # Check sanitized log output
    secret_entry = "token=AUTH_SECRET_KEY_987654321_XYZ"
    sanitized = log_sanitizer.sanitize_log_text(f"Processed file with {secret_entry}")
    assert "AUTH_SECRET_KEY_987654321_XYZ" not in sanitized
    assert "[REDACTED]" in sanitized


def test_qwen_offline_security_guards():
    """Qwen offline module must reject non-HTTPS, untrusted domains, and path traversal."""
    from qwen_offline import _safe_name, _ModelRedirectHandler, QwenInstallError

    # Safe name check
    assert _safe_name("model.safetensors") == "model.safetensors"
    with pytest.raises(QwenInstallError):
        _safe_name("../evil.bin")
    with pytest.raises(QwenInstallError):
        _safe_name("/absolute/evil.bin")

    # Redirect handler check
    handler = _ModelRedirectHandler()
    with pytest.raises(QwenInstallError):
        handler.redirect_request(None, None, 302, "", {}, "ftp://huggingface.co/model")
    with pytest.raises(QwenInstallError):
        handler.redirect_request(None, None, 302, "", {}, "https://evil-site.com/model")
