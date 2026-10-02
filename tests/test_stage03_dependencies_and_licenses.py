# -*- coding: utf-8 -*-
"""Автоматические тесты приёмки этапа 03: Воспроизводимые зависимости, лицензии и версия."""

import json
from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_version_single_source_of_truth():
    """Проверка единого источника версии и идентификаторов приложения."""
    import version

    assert version.__version__ == "3.0.2"
    assert version.APP_NAME == "DOCXdodyr"
    assert version.APP_DISPLAY_NAME == "DOCXдодыр"
    assert version.APP_TITLE == "DOCXдодыр v3.0.2"
    assert version.APP_BUNDLE_ID == "ru.docxdodyr.desktop"
    assert version.APP_ID_WINDOWS == "DOCXdodyr.Desktop.3.0"
    assert version.VERSION_TUPLE == (3, 0, 2)
    assert version.SUPPORTED_PYTHON_RECOMMENDED == (3, 11)

    info = version.get_version_info()
    assert info["version"] == "3.0.2"
    assert info["bundle_id"] == "ru.docxdodyr.desktop"
    assert info["app_id_windows"] == "DOCXdodyr.Desktop.3.0"
    assert "DOCXдодыр" in info["title"]


def test_backend_api_exposes_app_info():
    """Проверка доступности метода get_app_info через BackendApi."""
    from backend_api import BackendApi

    api = BackendApi()
    info = api.get_app_info()
    assert isinstance(info, dict)
    assert info["version"] == "3.0.2"
    assert info["app_name"] == "DOCXdodyr"
    assert info["bundle_id"] == "ru.docxdodyr.desktop"


def test_ui_branding_version_synchronized():
    """Проверка синхронизации версии в web/index.html и web/script.js."""
    index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "<title>DOCXдодыр v3.0</title>" in index_html
    assert 'id="app-version-badge"' in index_html
    assert "v3.0" in index_html

    script_js = (ROOT / "web" / "script.js").read_text(encoding="utf-8")
    assert "get_app_info" in script_js


def test_no_runtime_pip_install_in_codebase():
    """Проверка полного отсутствия runtime pip install вызовов в активном коде."""
    python_files = [
        ROOT / "main.py",
        ROOT / "app" / "backend_api.py",
        ROOT / "app" / "config.py",
        ROOT / "app" / "ocr_backend.py",
        ROOT / "app" / "folder_pipeline.py",
        ROOT / "app" / "claim_workflow.py",
        ROOT / "app" / "qwen_offline.py",
    ]
    for path in python_files:
        assert path.exists(), f"Отсутствует модуль {path}"
        content = path.read_text(encoding="utf-8")
        assert "pip install" not in content or "requirements.txt" in content, (
            f"Файл {path.name} содержит нежелательный вызов/упоминание pip install"
        )
        assert 'subprocess.run([sys.executable, "-m", "pip", "install"' not in content, (
            f"Файл {path.name} содержит опасный runtime pip install!"
        )


def test_requirements_structure_and_lockfiles():
    """Проверка разделения зависимостей на core, macos, windows, qwen, dev и наличие lock-файлов с SHA-256."""
    req_dir = ROOT / "requirements"
    assert req_dir.is_dir(), "Каталог requirements/ должен существовать"

    expected_files = [
        "requirements-core.in",
        "requirements-core.txt",
        "requirements-macos.in",
        "requirements-macos.txt",
        "requirements-windows.in",
        "requirements-windows.txt",
        "requirements-qwen.in",
        "requirements-qwen.txt",
        "requirements-dev.in",
        "requirements-dev.txt",
        "lock-core.txt",
        "lock-macos.txt",
        "lock-windows.txt",
    ]
    for name in expected_files:
        file_path = req_dir / name
        assert file_path.exists(), f"Файл {name} отсутствует в requirements/"
        assert file_path.stat().st_size > 0, f"Файл {name} пуст"

    # Проверка, что openpyxl присутствует в core
    core_txt = (req_dir / "requirements-core.txt").read_text(encoding="utf-8")
    assert "openpyxl" in core_txt
    macos_txt = (req_dir / "requirements-macos.txt").read_text(encoding="utf-8")
    assert "pytesseract==0.3.13" in macos_txt
    for macos_lock in ("lock-macos.txt", "lock-macos-intel.txt"):
        assert "pytesseract==0.3.13" in (req_dir / macos_lock).read_text(encoding="utf-8")

    # Проверка корневого requirements.txt
    root_req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    included = [line.split(maxsplit=1)[1] for line in root_req.splitlines()
                if line.startswith("-r ")]
    assert set(included) == {
        "requirements/requirements-core.txt",
        "requirements/requirements-macos.txt",
        "requirements/requirements-windows.txt",
    }
    included_text = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in included)
    assert "openpyxl==3.1.5" in included_text
    assert "setuptools==80.9.0" in included_text

    # Проверка lock-файлов на наличие хэшей
    for lock_name in ["lock-core.txt", "lock-macos.txt", "lock-windows.txt"]:
        content = (req_dir / lock_name).read_text(encoding="utf-8")
        assert "--hash=sha256:" in content, f"В {lock_name} отсутствуют SHA-256 хэши"


def test_legal_files_present_and_complete():
    """Проверка наличия и содержательности всех обязательных юридических документов."""
    eula_md = ROOT / "EULA.md"
    assert eula_md.exists() and eula_md.stat().st_size > 500
    eula_text = eula_md.read_text(encoding="utf-8")
    assert "Freeware" in eula_text
    assert "DOCXдодыр" in eula_text
    assert "AS IS" in eula_text

    eula_txt = ROOT / "EULA.txt"
    assert eula_txt.exists() and eula_txt.stat().st_size > 300

    privacy_md = ROOT / "PRIVACY_POLICY.md"
    assert privacy_md.exists() and privacy_md.stat().st_size > 500
    privacy_text = privacy_md.read_text(encoding="utf-8")
    assert "Local-First" in privacy_text
    assert "локаль" in privacy_text.lower()

    notices_md = ROOT / "THIRD_PARTY_NOTICES.md"
    assert notices_md.exists() and notices_md.stat().st_size > 1000
    notices_text = notices_md.read_text(encoding="utf-8")
    for component in ["pywebview", "python-docx", "openpyxl", "PullentiPython", "cryptography", "fpdf2", "pillow"]:
        assert component in notices_text, f"Компонент {component} отсутствует в THIRD_PARTY_NOTICES.md"

    model_rights_md = ROOT / "docs" / "MODEL_DISTRIBUTION_RIGHTS.md"
    assert model_rights_md.exists() and model_rights_md.stat().st_size > 500
    rights_text = model_rights_md.read_text(encoding="utf-8")
    assert "Qwen" in rights_text
    assert "Tesseract" in rights_text
    assert "Apple Vision" in rights_text

    python_policy_md = ROOT / "docs" / "PYTHON_RUNTIME_REQUIREMENTS.md"
    assert python_policy_md.exists() and python_policy_md.stat().st_size > 500
    policy_text = python_policy_md.read_text(encoding="utf-8")
    assert "3.11" in policy_text


def test_sbom_is_valid_cyclonedx_json():
    """Проверка валидности и полноты спецификации SBOM."""
    sbom_path = ROOT / "sbom.json"
    assert sbom_path.exists(), "sbom.json должен существовать"

    with sbom_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("bomFormat") == "CycloneDX"
    assert data.get("specVersion") == "1.5"
    metadata = data.get("metadata", {})
    assert metadata.get("component", {}).get("version") == "3.0.2"

    components = data.get("components", [])
    assert len(components) >= 14, "SBOM должен содержать все ключевые библиотеки ядра и платформы"

    names = {c["name"] for c in components}
    required_names = {
        "python-docx", "openpyxl", "pywebview",
        "PullentiPython", "pypdf", "cryptography", "keyring",
        "platformdirs", "pillow", "pytesseract", "fpdf2",
    }
    missing = required_names - names
    assert not missing, f"В SBOM отсутствуют компоненты: {missing}"


def test_openpyxl_is_importable_and_operational():
    """Проверка доступности и работоспособности openpyxl."""
    import openpyxl
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws["A1"] = "DOCXдодыр"
    ws["B1"] = "3.0.0"
    assert ws["A1"].value == "DOCXдодыр"
    assert ws["B1"].value == "3.0.0"
