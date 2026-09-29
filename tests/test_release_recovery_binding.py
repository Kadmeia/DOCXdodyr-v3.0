import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import crash_recovery as recovery


def test_vault_rejects_foreign_batch_and_checkpoint_tamper(tmp_path):
    recovery.start_batch_checkpoint("batch-a", [tmp_path / "a"])
    recovery.update_batch_checkpoint("batch-a", mapping_delta={"[NAME_1]": "Synthetic"})
    assert recovery.load_interrupted_batch_mapping("batch-a") == {"[NAME_1]": "Synthetic"}
    with pytest.raises(RuntimeError):
        recovery.load_interrupted_batch_mapping("batch-b", fail_closed=False)
    path = recovery.get_checkpoint_path()
    data = json.loads(path.read_text())
    data["total_replacements"] = 123
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        recovery.load_interrupted_batch_mapping("batch-a")


def test_checkpoint_commit_failure_preserves_prior_authenticated_state(tmp_path, monkeypatch):
    recovery.start_batch_checkpoint("batch-a", [tmp_path / "a"])
    recovery.update_batch_checkpoint("batch-a", mapping_delta={"[NAME_1]": "First"})
    def fail(*args, **kwargs):
        raise OSError("synthetic commit failure")
    monkeypatch.setattr(recovery.app_paths, "atomic_write_json", fail)
    with pytest.raises(OSError):
        recovery.update_batch_checkpoint("batch-a", mapping_delta={"[NAME_2]": "Second"})
    assert recovery.load_interrupted_batch_mapping("batch-a") == {"[NAME_1]": "First"}


def test_concurrent_updates_preserve_all_mapping_deltas(tmp_path):
    recovery.start_batch_checkpoint("batch-a", [tmp_path / str(i) for i in range(8)])
    def update(index):
        recovery.update_batch_checkpoint("batch-a", processed_file=tmp_path / str(index),
                                         mapping_delta={f"[NAME_{index}]": f"Synthetic {index}"}, replacements=1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(update, range(8)))
    assert len(recovery.load_interrupted_batch_mapping("batch-a")) == 8
    assert recovery.get_interrupted_batch()["total_replacements"] == 8


def test_vault_failure_does_not_advance_checkpoint(tmp_path, monkeypatch):
    recovery.start_batch_checkpoint("batch-a", [tmp_path / "a"])
    before = recovery.get_checkpoint_path().read_bytes()
    def fail():
        raise RuntimeError("Synthetic keyring failure")
    monkeypatch.setattr(recovery, "_get_recovery_vault", fail)
    with pytest.raises(RuntimeError):
        recovery.update_batch_checkpoint("batch-a", processed_file=tmp_path / "a",
                                         mapping_delta={"[NAME_1]": "Synthetic"})
    assert recovery.get_checkpoint_path().read_bytes() == before
