from __future__ import annotations

import json
import subprocess
import sys

from .runner import MANIFEST, load_manifest, select_scenarios


def test_manifest_is_machine_readable_and_unique():
    manifest = load_manifest(MANIFEST)
    ids = [item["id"] for item in manifest["scenarios"]]
    assert len(ids) == len(set(ids))
    assert manifest["offline_by_default"] is True
    assert all(item["pytest"] for item in manifest["scenarios"])


def test_scenario_selection_rejects_unknown_and_filters_kind():
    manifest = load_manifest(MANIFEST)
    assert all(item["kind"] == "backend" for item in select_scenarios(manifest, [], "backend"))
    try:
        select_scenarios(manifest, ["does.not.exist"], None)
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("unknown scenario was silently accepted")


def test_cli_list_is_fast_and_does_not_start_pytest():
    completed = subprocess.run([sys.executable, "-m", "tests.antigravity_regression.runner", "--list"], capture_output=True, text=True)
    assert completed.returncode == 0
    assert "ui.launch_theme_preflight" in completed.stdout
    assert "backend.public_contract_inventory" in completed.stdout

