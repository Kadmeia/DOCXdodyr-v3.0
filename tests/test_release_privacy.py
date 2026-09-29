import logging

import backend_api
import log_sanitizer


def test_backend_never_exports_raw_document_diagnostics(capsys, caplog):
    secret = "SYNTHETIC_PRIVATE_SENTINEL_123"
    backend_api._private_status(secret)
    with caplog.at_level(logging.WARNING, logger="backend_api"):
        try:
            raise ValueError(secret)
        except ValueError:
            backend_api.logger.warning("Document %s failed", secret, exc_info=True)
    assert secret not in caplog.text
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert "Traceback" not in caplog.text


def test_error_export_omits_unknown_sensitive_text():
    assert log_sanitizer.sanitize_error_message(ValueError("SYNTHETIC_PRIVATE_SENTINEL")) == "ValueError"


def test_default_model_status_path_is_read_only(tmp_path, monkeypatch):
    import qwen_offline
    target = tmp_path / "uncreated"
    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(target))
    assert qwen_offline.get_default_model_dir() == target / "models" / "Qwen3.5-0.8B"
    assert not target.exists()
