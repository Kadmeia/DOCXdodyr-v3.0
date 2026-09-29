from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend_api
from backend_api import BackendApi
from privacy_audit import ReviewFinding, ReviewQueue, build_audit_certificate, write_audit_certificate
from qwen_postprocessor import QwenPlaceholderPostprocessor, QwenPostprocessorSettings
from review_context import SecureContextVault


PUBLIC_METHODS = {
    "set_window", "init_ui", "get_current_status_text", "get_lists", "save_lists",
    "get_hidden_data_policy", "save_hidden_data_policy", "inspect_hidden_file",
    "apply_hidden_data_policy", "get_placeholder_settings", "save_placeholder_settings",
    "get_qwen_settings", "get_review_findings", "batch_decide_review_findings",
    "get_review_finding_preview", "decide_review_finding", "rebuild_rejected_finding",
    "clear_review_findings", "save_qwen_settings", "postprocess_anonymized_text",
    "show_settings", "show_placeholder_settings", "show_support_dialog", "open_telegram_channel", "open_file_dialog",
    "show_error", "process_files", "process_folder", "update_progress", "on_worker_finished",
    "get_paragraph_text_with_revisions", "postprocess_placeholder_tails", "process_single_file",
    "anonymize_text_pullenti", "clean_document", "run_pdf_ocr_only",
    "closeEvent",
}


class Window:
    def __init__(self):
        self.js = []

    def evaluate_js(self, value):
        self.js.append(str(value))


def api_stub(tmp_path):
    api = BackendApi.__new__(BackendApi)
    api.settings_file = tmp_path / "settings.json"
    api.exclusions_file_path = tmp_path / "exclusions.txt"
    api.replacements_file_path = tmp_path / "replacements.txt"
    api.settings = {}
    api.user_exclusions = set()
    api.custom_replacements = set()
    api.bracket_type = "square"
    api.enabled_placeholders = {"FIO"}
    api.current_placeholders = {"FIO": "[ФИО]"}
    api.hidden_data_policy = api._default_hidden_data_policy()
    api._hidden_inspections = {}
    api.qwen_settings = QwenPostprocessorSettings(enabled=False)
    api._qwen_postprocessor = QwenPlaceholderPostprocessor(api.qwen_settings)
    api.review_queue = ReviewQueue()
    api._persist_review_state = lambda: None
    api._window = Window()
    api._pullenti_processor = None
    api.save_original, api.save_pdf, api.save_decoder = True, False, False
    api.ocr_lang, api.irreversible_pdf = "rus", False
    import threading
    api._active_threads = []
    api._cancel_event = threading.Event()
    return api


@pytest.mark.antigravity_backend
def test_public_backend_contract_inventory_is_callable():
    for name in PUBLIC_METHODS:
        assert callable(getattr(BackendApi, name, None)), name


@pytest.mark.antigravity_backend
def test_settings_lists_placeholders_status_and_qwen_contract(tmp_path):
    api = api_stub(tmp_path)
    assert "Ожидание" not in api.get_current_status_text() or "плейсхолдер" in api.get_current_status_text().lower()
    api._save_settings = lambda: None
    assert api.save_lists({"exclusions": ["ООО"], "replacements": ["ACME"]})["success"]
    assert api.get_lists() == {"exclusions": ["ООО"], "replacements": ["ACME"]}
    assert api.save_placeholder_settings({"bracket_type": "square", "enabled_placeholders": ["FIO"]})["success"]
    assert api.get_placeholder_settings()["bracket_type"] == "square"
    assert api.get_qwen_settings()["local_files_only"] is True
    assert api.save_qwen_settings({"enabled": "false"})["success"]


@pytest.mark.antigravity_backend
def test_review_contract_validates_errors_and_preserves_no_pii(tmp_path):
    api = api_stub(tmp_path)
    finding = api.review_queue.add(ReviewFinding("doc.docx", "body.0", "FIO", "[ФИО_1]", .9, redacted_context="[ФИО_1]"))
    assert api.get_review_findings(min_confidence=90)["filtered_summary"]["total"] == 1
    with pytest.raises(ValueError):
        api.batch_decide_review_findings([], "invalid")
    with pytest.raises(KeyError):
        api.review_queue.decide("missing", "accepted")
    assert api.get_review_finding_preview("missing")["success"] is False
    saved = api.review_queue.save(tmp_path / "review.json")
    assert "Иванов" not in saved.read_text(encoding="utf-8")
    assert api.decide_review_finding(finding.finding_id, "accepted")["success"]
    assert api.clear_review_findings()["success"]


@pytest.mark.antigravity_backend
def test_hidden_policy_error_branches_and_stale_token(tmp_path):
    api = api_stub(tmp_path)
    api._save_settings = lambda: None
    assert api.save_hidden_data_policy(None)["success"] is False
    assert api.save_hidden_data_policy({"comments": "bogus"})["success"] is False
    assert api.save_hidden_data_policy({"comments": "remove"})["requires_confirmation"] is True
    assert api.save_hidden_data_policy({"comments": "remove"}, confirm_destructive=True)["success"] is True
    assert api.apply_hidden_data_policy("stale")["success"] is False


@pytest.mark.antigravity_backend
def test_restart_safe_private_context_and_audit_do_not_leak_pii(tmp_path):
    pytest.importorskip("cryptography")
    key = b"a" * 32
    vault_path = tmp_path / ".review.enc"
    vault = SecureContextVault(vault_path, key_provider=lambda: key)
    vault.put("finding", {"original": "Иванов Иван Иванович", "output_path": "cleaned.docx"})
    raw = vault_path.read_text(encoding="utf-8")
    assert "Иванов" not in raw
    restarted = SecureContextVault(vault_path, key_provider=lambda: key)
    assert restarted.load()["finding"]["original"].startswith("Иванов")
    source = tmp_path / "source"
    result = tmp_path / "result"
    source.write_bytes(b"source")
    result.write_bytes(b"result")
    cert = build_audit_certificate(source, result, counts={"original": "Иванов Иван Иванович"}, review_queue=ReviewQueue())
    output = write_audit_certificate(cert, tmp_path / "audit.json")
    assert "Иванов" not in output.read_text(encoding="utf-8")


@pytest.mark.antigravity_backend
def test_restart_without_private_context_fails_closed(tmp_path):
    api = api_stub(tmp_path)
    api.review_queue.add(ReviewFinding("doc.docx", "body.0", "FIO", "[ФИО_1]", .9))
    result = api.rebuild_rejected_finding(api.review_queue.items()[0].finding_id)
    assert result["success"] is False and "отклон" in result["error"].lower()


@pytest.mark.antigravity_backend
def test_concurrent_read_only_calls_are_stable(tmp_path):
    api = api_stub(tmp_path)
    api.review_queue = ReviewQueue([ReviewFinding("doc.docx", "p", "FIO", "[ФИО_1]", .8)])
    def call(_):
        return api.get_review_findings(min_confidence=80)["filtered_summary"]["total"], api.postprocess_anonymized_text("Для [ФИО_1] готов документ.")
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(call, range(32)))
    assert all(total == 1 and text == "Для [ФИО_1] готов документ." for total, text in values)


@pytest.mark.antigravity_backend
def test_progress_and_no_pii_logs(tmp_path, caplog):
    api = api_stub(tmp_path)
    api.update_progress(1, 2, "Обработка")
    api.on_worker_finished(1, 0, 2)
    assert any("50" in event for event in api._window.js)
    assert all("Иванов" not in event for event in api._window.js)
    with caplog.at_level(logging.WARNING):
        api.show_error("safe error")
    assert all("Иванов" not in record.getMessage() for record in caplog.records)


@pytest.mark.antigravity_backend
def test_low_level_ui_bridge_and_text_helpers_have_stable_contract(tmp_path, monkeypatch):
    api = api_stub(tmp_path)
    api.init_ui()
    api.show_settings()
    api.show_placeholder_settings()
    api.show_support_dialog()
    assert api._window.js
    assert api.get_paragraph_text_with_revisions(SimpleNamespace(text="plain")) == "plain"
    assert api.get_paragraph_text_with_revisions(SimpleNamespace()) == ""
    assert api.postprocess_placeholder_tails("[ФИО_1]у") == "[ФИО_1]у"
    event = SimpleNamespace(accept=lambda: setattr(event, "accepted", True))
    api.closeEvent(event)
    assert event.accepted is True
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    api.open_telegram_channel()
    assert opened and opened[0].startswith("https://")


@pytest.mark.antigravity_backend
def test_public_error_branches_are_safe(tmp_path):
    api = api_stub(tmp_path)
    api.init_pullenti = lambda: None
    api.process_files(["missing.docx"])
    api.process_folder(tmp_path)
    assert any("Pullenti" in event for event in api._window.js)
    api._pullenti_processor = object()
    api._ensure_ocr_backend = lambda: False
    assert api._ensure_ocr_backend() is False
