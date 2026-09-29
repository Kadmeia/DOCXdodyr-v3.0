import json

import crash_recovery
import privacy_audit


def _recovery_paths(monkeypatch, tmp_path):
    recovery_dir = tmp_path / "recovery"
    checkpoint_path = recovery_dir / "active_batch_checkpoint.json"
    monkeypatch.setattr(crash_recovery, "get_recovery_dir", lambda: recovery_dir)
    monkeypatch.setattr(crash_recovery, "get_checkpoint_path", lambda: checkpoint_path)
    return checkpoint_path


def test_checkpoint_never_persists_decoder_originals(monkeypatch, tmp_path):
    checkpoint_path = _recovery_paths(monkeypatch, tmp_path)
    source = tmp_path / "source.docx"
    crash_recovery.start_batch_checkpoint("batch", [source])
    crash_recovery.update_batch_checkpoint(
        "batch",
        processed_file=source,
        mapping_delta={"[ФИО_1]": "Иванов Иван Иванович"},
        replacements=1,
    )

    raw = checkpoint_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "Иванов Иван Иванович" not in raw
    assert data["batch_mapping"] == {"[ФИО_1]": ""}


def test_old_batch_cannot_delete_new_checkpoint(monkeypatch, tmp_path):
    checkpoint_path = _recovery_paths(monkeypatch, tmp_path)
    crash_recovery.start_batch_checkpoint("new-batch", [tmp_path / "source.docx"])

    crash_recovery.complete_batch_checkpoint("old-batch")

    assert checkpoint_path.exists()
    assert crash_recovery.get_interrupted_batch()["batch_id"] == "new-batch"


def test_broken_pdf_cleanup_does_not_publish_source_copy(tmp_path):
    source = tmp_path / "broken.pdf"
    output = tmp_path / "cleaned.pdf"
    source.write_bytes(b"%PDF-1.4\n%broken")

    report = privacy_audit.clean_file_metadata(source, output)

    assert report.error
    assert not output.exists()
