# -*- coding: utf-8 -*-
import hashlib
import json
import zipfile

import pytest

from qwen_offline import (
    QwenInstallError,
    check_resources,
    estimate_resources,
    export_bundle,
    import_bundle,
    is_installed,
    runtime_settings,
    validate_model,
)


def _tiny_manifest(tmp_path):
    content = b"tiny model fixture"
    return {
        "schema": 1,
        "model_id": "local/test-qwen",
        "revision": "abc123",
        "license": "Apache-2.0",
        "total_bytes": len(content) * 2,
        "files": [
            {"name": "config.json", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()},
            {"name": "weights.bin", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()},
        ],
    }, content


def _make_model(path, manifest, content):
    path.mkdir()
    for item in manifest["files"]:
        (path / item["name"]).write_bytes(content)
    (path / "DOCXdodyr-qwen-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_download_requires_explicit_consent(tmp_path):
    with pytest.raises(QwenInstallError, match="явного согласия"):
        from qwen_offline import install_model
        install_model(tmp_path / "model")


def test_resource_check_does_not_download_and_warns_on_weak_memory(tmp_path):
    report = check_resources(tmp_path / "model", available_memory=128 * 1024**2)
    assert report["suitable"] is False
    assert report["disk_required_bytes"] > 0
    estimate = estimate_resources(available_memory=128 * 1024**2)
    assert estimate.suitable is False


def test_offline_bundle_roundtrip_and_local_only_runtime(tmp_path):
    manifest, content = _tiny_manifest(tmp_path)
    source = tmp_path / "source"
    _make_model(source, manifest, content)
    bundle = tmp_path / "qwen.bundle.zip"
    export_bundle(source, bundle, consent=True, manifest=manifest)
    restored = tmp_path / "restored"
    import_bundle(bundle, restored, consent=True, manifest=manifest)
    assert is_installed(restored, manifest=manifest)
    assert validate_model(restored, manifest=manifest)
    settings = runtime_settings(restored, manifest=manifest)
    assert settings["model_path"] == str(restored.resolve())
    assert settings["local_files_only"] is True
    assert settings["allow_network_download"] is False


def test_bundle_rejects_path_traversal(tmp_path):
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("../escape", b"no")
    with pytest.raises(QwenInstallError, match="лишние или небезопасные"):
        import_bundle(bundle, tmp_path / "restored", consent=True)
