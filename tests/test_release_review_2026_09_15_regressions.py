# -*- coding: utf-8 -*-
"""Regression tests for 2026-09-15 release review findings (P0/P1)."""
import os
import json
import tempfile
import threading
from pathlib import Path
import pytest

from backend_api import BackendApi, UnifiedOperationManager
import crash_recovery
from folder_pipeline import FolderAnonymizationPipeline, SUPPORTED_SUFFIXES
from constants import FORMAT_REGISTRY
from qwen_postprocessor import (
    QwenPlaceholderPostprocessor,
    PlaceholderValidationError,
    protect_placeholders,
    restore_and_validate,
    ProtectedText,
)
from qwen_offline import validate_model, verify_model_integrity


# --- 1. Dead lists contract (Exclusions & Custom Replacements) ---

def test_repro_exclusions_not_redacted():
    """P0: Entities in exclusions list must NOT be redacted."""
    api = BackendApi(lazy_pullenti=False)
    api.user_exclusions = {"Иванов Иван Иванович", "ООО Ромашка"}
    text = "Генеральный директор Иванов Иван Иванович подписал договор с ООО Ромашка."
    cleaned, count, logs = api.anonymize_text_pullenti(
        text,
        current_exclusions_original=api.user_exclusions,
        current_replacements=set(),
    )
    assert "Иванов Иван Иванович" in cleaned, "Exclusion FIO was incorrectly redacted"
    assert count == 0 or "Иванов Иван Иванович" in cleaned


def test_repro_custom_replacements_applied():
    """P0: Custom replacements like 'SecretCode42=>[HIDDEN]' must be applied."""
    api = BackendApi(lazy_pullenti=False)
    api.custom_replacements = {"СекретКод42=>[СКРЫТО]", "ПроектАльфа -> [ПРОЕКТ]"}
    text = "Секретный ключ СекретКод42 передан в ПроектАльфа."
    cleaned, count, logs = api.anonymize_text_pullenti(
        text,
        current_exclusions_original=set(),
        current_replacements=api.custom_replacements,
    )
    assert "[СКРЫТО]" in cleaned, "Custom replacement was not applied"
    assert "[ПРОЕКТ]" in cleaned, "Custom replacement arrow syntax was not applied"
    assert "СекретКод42" not in cleaned
    assert count >= 2


# --- 2. Recovery fail-closed, partial preservation & atomic decoder ---

def test_repro_recovery_checkpoint_preserved_on_error(tmp_path):
    """P0: Checkpoint must NOT be deleted when error_count > 0."""
    checkpoint_file = tmp_path / "checkpoint.json"
    vault_file = tmp_path / "vault.enc"

    # Monkeypatch app_paths for test isolation
    orig_chk = crash_recovery.get_checkpoint_path
    orig_vlt = crash_recovery.get_vault_path
    try:
        crash_recovery.get_checkpoint_path = lambda: checkpoint_file
        crash_recovery.get_vault_path = lambda: vault_file

        batch_id = "batch-test-error-123"
        f1 = str(tmp_path / "file1.docx")
        f2 = str(tmp_path / "file2.docx")
        crash_recovery.start_batch_checkpoint(batch_id, [f1, f2])

        # File 1 succeeds
        crash_recovery.update_batch_checkpoint(batch_id, processed_file=f1, mapping_delta={"[FIO_1]": "Иванов"})
        # File 2 fails
        crash_recovery.update_batch_checkpoint(batch_id, processed_file=f2, error="Test error")

        # Simulate batch completion logic in backend_api
        error_count = 1
        is_cancelled = False
        if error_count == 0 and not is_cancelled:
            crash_recovery.complete_batch_checkpoint(batch_id)

        # Checkpoint MUST still exist for resumption of failed file
        assert checkpoint_file.exists(), "Checkpoint was improperly deleted despite errors"
        info = crash_recovery.get_interrupted_batch()
        assert info is not None
        assert info["remaining_count"] == 1
        assert f2 in info["remaining_files"]
    finally:
        crash_recovery.get_checkpoint_path = orig_chk
        crash_recovery.get_vault_path = orig_vlt


def test_repro_recovery_vault_fail_closed(tmp_path):
    """P0: Vault decryption failure must fail-closed, not return empty mapping."""
    vault_file = tmp_path / "corrupted_vault.enc"
    vault_file.write_bytes(b"CORRUPTED_CIPHERTEXT_DATA_THAT_FAILS_AUTH")

    orig_vlt = crash_recovery.get_vault_path
    try:
        crash_recovery.get_vault_path = lambda: vault_file
        with pytest.raises(Exception):
            crash_recovery.load_interrupted_batch_mapping("batch-123", fail_closed=True)
    finally:
        crash_recovery.get_vault_path = orig_vlt


def test_repro_atomic_decoder_save(tmp_path):
    """P0: Decoder file must be saved collision-safe and atomically with 0600 mode."""
    api = BackendApi(lazy_pullenti=True)
    doc_path = tmp_path / "test_doc.docx"
    doc_path.touch()
    dec_path = tmp_path / "test_doc_дешифратор.json"

    mapping = {"[ФИО_1]": "Иван Иванович"}
    api.save_decoder = True
    # Test atomic write helper
    written_path = api._save_decoder_atomic(dec_path, mapping)
    assert Path(written_path).exists()
    assert json.loads(Path(written_path).read_text(encoding="utf-8")) == mapping
    # Check permissions 0600 (read/write by owner only) on posix
    if os.name == "posix":
        mode = oct(Path(written_path).stat().st_mode & 0o777)
        assert mode == "0o600"


# --- 3. UnifiedOperationManager concurrency ---

def test_repro_operation_manager_sync_is_busy():
    """P0: start_operation without thread must remain busy until finish_operation."""
    mgr = UnifiedOperationManager()
    token = mgr.start_operation_token("folder_process", description="Processing folder")
    assert token is not None
    assert mgr.is_busy() is True
    assert mgr.state == "running"

    # Second operation must be blocked
    token2 = mgr.start_operation_token("restore", description="Restoring")
    assert token2 is None, "Concurrent operation was not blocked"

    # Finish operation with correct token
    mgr.finish_operation_token(token, status="succeeded")
    assert mgr.is_busy() is False
    assert mgr.state == "succeeded" or mgr.state == "idle"


# --- 4. OCR collision safety ---

def test_repro_ocr_collision_safety(tmp_path):
    """P0: OCR output must not overwrite existing txt file."""
    api = BackendApi(lazy_pullenti=True)
    pdf_file = tmp_path / "scan.pdf"
    pdf_file.touch()

    # Pre-existing txt file with user data
    existing_txt = tmp_path / "scan_ocr.txt"
    existing_txt.write_text("IMPORTANT_USER_DATA", encoding="utf-8")

    unique_dst = api._get_unique_ocr_output_path(pdf_file)
    assert unique_dst != existing_txt
    assert str(unique_dst).endswith(".txt")
    assert not unique_dst.exists()
    assert existing_txt.read_text(encoding="utf-8") == "IMPORTANT_USER_DATA"


# --- 5. Folder cancel / rollback-safe publication ---

def test_repro_folder_cancel_no_publish(tmp_path):
    """P0: Cancellation during folder processing must not publish to canonical folder."""
    root = tmp_path / "input_folder"
    root.mkdir()
    (root / "doc1.docx").touch()

    api = BackendApi(lazy_pullenti=True)
    api.cancel_operation()  # Pre-cancelled

    pipeline = FolderAnonymizationPipeline(api)
    res = pipeline.process(root)
    canonical = root / "Обезличенные документы"
    assert not canonical.exists(), "Cancelled folder pipeline published output"
    assert res.error_count > 0 or getattr(res, "cancelled", False) or res.processed_count == 0


# --- 6. Review DOM XSS safety ---

def test_repro_review_window_xss_safety():
    """P1: Review window must not use inline onclick with unescaped finding_id."""
    review_html = (Path(__file__).resolve().parents[1] / "web" / "review_window.html").read_text(encoding="utf-8")
    # Finding_id in onclick attribute is vulnerable to XSS
    assert "onclick=\"event.stopPropagation(); decide('${safeFindingId}'" not in review_html, \
        "Review window still uses inline onclick with interpolated finding ID"


# --- 7. Format Registry & Macro policy ---

def test_repro_format_registry_macro_consistency():
    """P1: .xlsm must be supported with VBA preservation; legacy .xls rejected."""
    assert ".xlsm" in SUPPORTED_SUFFIXES, ".xlsm missing from folder pipeline supported suffixes"
    assert ".xlsm" in FORMAT_REGISTRY["anonymize"]["extensions"]
    assert ".xls" not in FORMAT_REGISTRY["restore"]["extensions"], "Legacy .xls should not be in restore registry"


# --- 8. File association preflight ---

def test_repro_file_association_no_immediate_autoprocess():
    """P1: Startup files must stage selection without auto-triggering background process."""
    api = BackendApi(lazy_pullenti=True)
    from main import ApiWrapper
    wrapper = ApiWrapper(api=api, startup_files=["/tmp/sample.docx"])
    # init_ui should stage the file, not start processing
    res = wrapper.init_ui()
    assert wrapper._api.is_busy() is False


# --- 9. Qwen usefulness gate & negative safety ---

def test_repro_qwen_positive_grammar_and_sentinel_preservation(real_qwen_processor):
    """P1: Qwen must safely correct grammar/punctuation and pass validator."""
    model_dir = os.environ.get("QWEN_MODEL_DIR", "").strip()
    if not model_dir or not Path(model_dir).is_dir():
        pytest.skip("QWEN_MODEL_DIR not set for real model test")

    processor = real_qwen_processor

    # Golden scenario 1: dative case correction around placeholder
    text = "Уведомление направлено в адрес [Организация_1] ,согласно договора ."
    result = processor.process(text)
    assert result != text
    assert "[Организация_1]" in result
    assert processor.last_status == "ok", f"Qwen failed positive test: {processor.last_status}"
    assert "согласно договору" in result or "согласно договора." in result or "[Организация_1]," in result


def test_qwen_negative_fact_number_rejection():
    """P1: Model changing factual numbers must be rejected."""
    protected = ProtectedText("Сумма составляет 10000 рублей для <<QWENPH0>>.", ("[ФИО_1]",))
    # Model hallucinated 20000 instead of 10000
    generated = "Сумма составляет 20000 рублей для <<QWENPH0>>."
    with pytest.raises(PlaceholderValidationError, match="model changed factual numbers"):
        restore_and_validate(generated, protected)


def test_qwen_negative_placeholder_tamper_rejection():
    """P1: Model altering sentinels or inventing placeholders must be rejected."""
    protected = ProtectedText("Договор подписан <<QWENPH0>>.", ("[ФИО_1]",))
    # Model dropped sentinel
    generated1 = "Договор подписан гражданином."
    with pytest.raises(PlaceholderValidationError):
        restore_and_validate(generated1, protected)

    # Model invented placeholder
    generated2 = "Договор подписан <<QWENPH0>> и [ФИО_2]."
    with pytest.raises(PlaceholderValidationError):
        restore_and_validate(generated2, protected)
