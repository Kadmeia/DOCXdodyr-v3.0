# -*- coding: utf-8 -*-
"""Приёмочные автотесты этапа 10: Release candidate, упаковка, целостность и публикация."""

from __future__ import annotations

import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
ASSETS_DIR = REPO_ROOT / "assets"
INSTALLER_DIR = REPO_ROOT / "installer"
DOCS_DIR = REPO_ROOT / "docs"

import version
from scripts import package_release


def test_stage10_version_synchronization():
    """Проверяет синхронизацию канонической версии (3.0.2) по всем манифестам и конфигурациям."""
    audit = package_release.check_version_consistency("3.0.2")
    assert audit["all_matched"] is True, f"Несоответствие версий: {audit['mismatches']}"
    assert version.__version__ == "3.0.2"
    assert version.VERSION_TUPLE == (3, 0, 2)
    assert version.APP_BUNDLE_ID == "ru.docxdodyr.desktop"
    assert version.APP_ID_WINDOWS == "DOCXdodyr.Desktop.3.0"


def test_stage10_legal_documents_completeness():
    """Проверяет наличие и соответствие обязательных юридических документов требованиям релиза."""
    legal_audit = package_release.check_legal_documents()
    assert legal_audit["all_present"] is True, f"Отсутствуют юридические документы: {legal_audit['missing']}"

    # Проверка EULA.txt
    eula_txt = (REPO_ROOT / "EULA.txt").read_text(encoding="utf-8")
    assert "Freeware" in eula_txt
    assert "DOCXдодыр" in eula_txt
    assert "3.0" in eula_txt
    assert "docxdodyr-pullenti-legal" in eula_txt
    assert "MIT" in eula_txt

    # Проверка PRIVACY_POLICY.md
    privacy = (REPO_ROOT / "PRIVACY_POLICY.md").read_text(encoding="utf-8")
    assert "LOCAL-FIRST" in privacy.upper()
    assert "platformdirs" in privacy
    assert "дешифратор.json" in privacy

    # Проверка THIRD_PARTY_NOTICES.md
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "pywebview" in notices
    assert "PullentiPython" in notices
    assert "pytesseract" in notices


def test_stage10_sbom_cyclonedx_contract():
    """Проверяет соответствие SBOM стандарту CycloneDX 1.5 и синхронизацию с THIRD_PARTY_NOTICES."""
    sbom_path = REPO_ROOT / "sbom.json"
    assert sbom_path.exists()

    with open(sbom_path, "r", encoding="utf-8") as f:
        sbom = json.load(f)

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"]["name"] == "DOCXdodyr"
    assert sbom["metadata"]["component"]["version"] == "3.0.2"

    components = sbom["components"]
    assert len(components) >= 15, f"Ожидалось не менее 15 компонентов в SBOM, получено {len(components)}"

    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for comp in components:
        comp_name = comp["name"]
        assert comp_name.lower() in notices.lower(), f"Компонент SBOM '{comp_name}' отсутствует в THIRD_PARTY_NOTICES.md"


def test_stage10_rollback_procedure_documentation():
    """Проверяет наличие и полноту регламента экстренного отката (docs/ROLLBACK_PROCEDURE.md)."""
    rollback_path = DOCS_DIR / "ROLLBACK_PROCEDURE.md"
    assert rollback_path.exists()
    content = rollback_path.read_text(encoding="utf-8")

    assert "Сценарий A" in content
    assert "Сценарий B" in content
    assert "Сценарий C" in content
    assert "Сценарий D" in content
    assert "platformdirs" in content
    assert "macOS" in content
    assert "Windows" in content
    assert "дешифратор.json" in content


def test_stage10_release_notes_completeness():
    """Проверяет наличие и полноту Release Notes версии v3.0."""
    notes_path = DOCS_DIR / "RELEASE_NOTES_v3.0.md"
    root_notes_path = REPO_ROOT / "RELEASE_NOTES.md"
    assert notes_path.exists()
    assert root_notes_path.exists()

    notes = notes_path.read_text(encoding="utf-8")
    assert "3.0" in notes
    assert "Local-First" in notes
    assert "[СерияПаспорта]" in notes
    assert "[НомерПаспорта]" in notes
    assert "[КодПодразделения]" in notes
    assert "[НомерРТО]" in notes
    assert "Apple Vision" in notes
    assert "Tesseract" in notes
    assert "SmartScreen" in notes
    assert "Gatekeeper" in notes
    assert "SHA256SUMS.txt" in notes
    assert "ROLLBACK_PROCEDURE.md" in notes


def test_stage10_package_release_audit_and_checksums(tmp_path, monkeypatch):
    """Проверяет работу скрипта package_release и валидность вычисленных контрольных сумм."""
    monkeypatch.setattr(package_release, "DIST_DIR", tmp_path)
    (tmp_path / "synthetic.zip").write_bytes(b"synthetic artifact")
    package_release.generate_sha256sums(tmp_path)
    audit = package_release.run_full_release_candidate_audit()
    assert audit["version"] == "3.0.2"
    assert audit["version_check"]["all_matched"] is True
    assert audit["legal_check"]["all_present"] is True
    assert audit["docs_check"]["all_present"] is True
    assert len(audit["artifacts"]) > 0

    # Проверка верификации SHA256SUMS.txt
    sums_path = tmp_path / "SHA256SUMS.txt"
    assert sums_path.exists()
    verify_res = package_release.verify_sha256sums(sums_path, tmp_path)
    assert verify_res["valid"] is True, f"Ошибки верификации SHA-256: {verify_res['mismatches']}"
    assert len(verify_res["verified_files"]) >= 1


def test_stage10_github_release_workflow():
    """Проверяет синтаксис и конфигурацию GitHub Actions release workflow."""
    wf_path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    assert wf_path.exists()
    wf_text = wf_path.read_text(encoding="utf-8")

    assert "macos-arm64" in wf_text
    assert "windows-x64" in wf_text
    assert "publish-release" not in wf_text
    assert "native_evidence_run_id" in wf_text
    assert "package_release.py" in wf_text
    assert "merge_release_evidence.py" in wf_text


def test_stage10_cli_version_contract():
    """Проверяет, что флаг CLI --version возвращает каноническую версию 3.0.2."""
    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "main.py"), "--version"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0
    assert "DOCXdodyr 3.0.2" in res.stdout


def test_stage10_clean_working_tree_against_secrets():
    """Проверяет отсутствие открытых секретов, токенов и приватных ключей в конфигурациях и документах релиза."""
    import os
    sensitive_regexes = [
        (re.compile(r"AIzaSy[A-Za-z0-9_-]{33}"), "Обнаружен боевой Google API Key"),
        (re.compile(r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----"), "Обнаружен закрытый ключ"),
        (re.compile(r"ghp_[A-Za-z0-9]{36}"), "Обнаружен реальный GitHub Personal Access Token"),
    ]

    scanned_extensions = {".py", ".json", ".iss", ".manifest", ".md", ".txt"}
    target_dirs = [REPO_ROOT / "scripts", REPO_ROOT / "installer", REPO_ROOT / "docs", REPO_ROOT / ".github"]

    for tdir in target_dirs:
        if not tdir.exists():
            continue
        for root, _, files in os.walk(tdir):
            for file in files:
                p = Path(root) / file
                if p.suffix in scanned_extensions:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    for rx, label in sensitive_regexes:
                        assert not rx.search(content), f"В файле {p.relative_to(REPO_ROOT)}: {label}"

    root_files = ["main.py", "app/backend_api.py", "version.py", "EULA.txt", "PRIVACY_POLICY.md", "THIRD_PARTY_NOTICES.md"]
    for rf in root_files:
        p = REPO_ROOT / rf
        assert p.exists(), f"Отсутствует исходный файл {rf}"
        content = p.read_text(encoding="utf-8", errors="ignore")
        for rx, label in sensitive_regexes:
            assert not rx.search(content), f"В файле {rf}: {label}"
