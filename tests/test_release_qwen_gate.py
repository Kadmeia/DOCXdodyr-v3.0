import pytest

import qwen_offline
from qwen_postprocessor import QwenPostprocessorSettings, _default_loader, _as_bool, runtime_preflight


def test_tampered_model_rejected_before_transformers_import(tmp_path, monkeypatch):
    calls = []
    def validate(path, *, verify_hash):
        calls.append(verify_hash)
        return False
    monkeypatch.setattr(qwen_offline, "validate_model", validate)
    with pytest.raises(ValueError, match="SHA-256"):
        _default_loader(QwenPostprocessorSettings(model_path=str(tmp_path)))
    assert calls == [True]


def test_remote_settings_cannot_trigger_inference_download(tmp_path, monkeypatch):
    monkeypatch.setattr(qwen_offline, "get_default_model_dir", lambda: tmp_path / "missing")
    settings = QwenPostprocessorSettings(model_id="arbitrary/remote", local_files_only=False,
                                         allow_network_download=True)
    with pytest.raises(ValueError, match="local model directory"):
        _default_loader(settings)


def test_mapping_rejects_remote_model_id():
    with pytest.raises(ValueError, match="remote Qwen model IDs"):
        QwenPostprocessorSettings.from_mapping({"model_id": "arbitrary/remote"})


def test_preflight_never_reports_missing_model_ready(tmp_path):
    result = __import__("qwen_postprocessor").runtime_preflight(
        QwenPostprocessorSettings(model_id="", model_path=str(tmp_path / "missing"), local_files_only=False)
    )
    assert result["ready"] is False


def test_resource_check_does_not_create_directories(tmp_path):
    target = tmp_path / "missing" / "model"
    qwen_offline.check_resources(target)
    assert not target.parent.exists()


def test_delete_model_fails_closed_when_allowed_root_unavailable(tmp_path, monkeypatch):
    import app_paths
    model = tmp_path / "keep"
    model.mkdir()
    def fail():
        raise OSError("Synthetic failure")
    monkeypatch.setattr(app_paths, "get_user_models_dir", fail)
    with pytest.raises(qwen_offline.QwenInstallError):
        qwen_offline.delete_model(model)
    assert model.is_dir()


@pytest.mark.parametrize("value", ["false", "False", "0", False])
def test_false_settings_remain_false(value):
    assert _as_bool(value) is False


def test_runtime_preflight_requires_pinned_hash(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(qwen_offline, "validate_model", lambda path, *, verify_hash: calls.append(verify_hash) or False)
    result = runtime_preflight(QwenPostprocessorSettings(model_path=str(tmp_path)))
    assert result["ready"] is False
    assert calls == [True]
