# -*- coding: utf-8 -*-
"""Тесты этапа 07: Windows приложение, манифест, иконка .ico, OCR и установщик Inno Setup."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
INSTALLER_DIR = REPO_ROOT / "installer"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def test_windows_icon_validity_and_multi_resolution():
    """Проверяет наличие, сигнатуру и полный набор слоёв разрешения в DOCXdodyr.ico."""
    ico_path = ASSETS_DIR / "DOCXdodyr.ico"
    assert ico_path.exists(), f"Иконка Windows отсутствует: {ico_path}"
    assert ico_path.stat().st_size > 25_000, f"Файл .ico слишком мал: {ico_path.stat().st_size} байт"

    # 1. Проверка бинарного заголовка ICO (Reserved=0, Type=1 для ICO)
    with open(ico_path, "rb") as f:
        header = f.read(6)
    assert len(header) == 6
    reserved, ico_type, count = struct.unpack("<HHH", header)
    assert reserved == 0, f"Некорректное поле reserved в заголовке ICO: {reserved}"
    assert ico_type == 1, f"Некорректный тип файла (ожидался 1 для ICO): {ico_type}"
    assert count >= 6, f"В иконке должно быть не менее 6 слоёв разрешения, найдено: {count}"

    # 2. Проверка через Pillow
    with Image.open(ico_path) as im:
        assert im.format == "ICO", f"Некорректный формат Pillow: {im.format}"
        # Проверяем размеры всех слоёв
        entry_sizes = []
        if hasattr(im, "ico") and hasattr(im.ico, "entry"):
            for entry in im.ico.entry:
                w = entry.dim[0]
                h = entry.dim[1]
                entry_sizes.append((w, h))

        # Обязательные стандартные размеры Windows Shell
        for expected in [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]:
            assert expected in entry_sizes, f"Отсутствует обязательный слой разрешения: {expected}"


def test_windows_manifest_validity_and_security():
    """Проверяет XML-манифест приложения на DPI awareness, UAC asInvoker и UTF-8."""
    manifest_path = ASSETS_DIR / "DOCXdodyr.manifest"
    assert manifest_path.exists(), f"Манифест отсутствует: {manifest_path}"

    content = manifest_path.read_text(encoding="utf-8")
    assert "<?xml" in content
    assert "<assembly" in content

    # Парсинг XML
    root = ET.fromstring(content)

    # 1. Проверка безопасности: UAC asInvoker (не требует повышения до администратора)
    assert 'level="asInvoker"' in content, "Манифест должен указывать level='asInvoker' для безопасного per-user запуска"
    assert 'uiAccess="false"' in content

    # 2. Проверка поддержки Per-Monitor V2 DPI awareness
    assert "PerMonitorV2" in content, "Манифест должен поддерживать PerMonitorV2 DPI awareness"
    assert "true/pm" in content

    # 3. Проверка кодовой страницы UTF-8 для корректных путей с кириллицей
    assert "<activeCodePage" in content and "UTF-8" in content, "Манифест должен задавать активную кодовую страницу UTF-8"

    # 4. Проверка GUID совместимости Windows 10/11
    win10_guid = "{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"
    assert win10_guid in content, f"Манифест должен содержать GUID совместимости Windows 10/11: {win10_guid}"


def test_inno_setup_script_structure_and_app_id():
    """Проверяет структуру скрипта Inno Setup, стабильный AppId, per-user режим и WebView2."""
    iss_path = INSTALLER_DIR / "DOCXdodyr.iss"
    assert iss_path.exists(), f"Скрипт Inno Setup отсутствует: {iss_path}"

    iss_content = iss_path.read_text(encoding="utf-8")

    # 1. Стабильный AppId для надежных обновлений и удаления
    expected_app_id = "{{B42E784D-6780-4D56-A83A-2895DC569941}}"
    assert expected_app_id in iss_content, f"Inno Setup должен содержать стабильный AppId: {expected_app_id}"

    # 2. Per-user установка без прав администратора
    assert "PrivilegesRequired=lowest" in iss_content, "Инсталлятор должен устанавливаться без прав администратора (lowest)"
    assert "{localappdata}\\Programs\\DOCXdodyr" in iss_content

    # 3. 64-битная архитектура
    assert "ArchitecturesAllowed=x64compatible" in iss_content
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in iss_content

    # 4. Ассоциация расширений файлов
    for ext in [".docx", ".xlsx", ".pdf"]:
        assert ext in iss_content, f"Инсталлятор должен регистрировать ассоциацию для {ext}"

    # 5. Регистрация AppUserModelId для панели задач
    assert "Software\\Classes\\AppUserModelId\\DOCXdodyr.Desktop.3.0" in iss_content

    # 6. Проверка и детекция WebView2 Evergreen Runtime в [Code]
    assert "function IsWebView2Installed" in iss_content, "Скрипт должен содержать функцию проверки WebView2"
    assert "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" in iss_content, "Проверка ключа реестра клиента Edge WebView2"
    assert "https://go.microsoft.com/fwlink/p/?LinkId=2124703" in iss_content, "Ссылка на установщик WebView2"


def test_windows_ocr_backend_uses_supported_local_engines_only():
    """Windows OCR перечисляет только поддерживаемые локальные движки."""
    import ocr_backend

    backends = ocr_backend.available_backend_names("win32")
    assert set(backends).issubset({"tesseract", "paddleocr"})

    # Проверка чистоты лицензии в THIRD_PARTY_NOTICES.md
    notices_path = REPO_ROOT / "THIRD_PARTY_NOTICES.md"
    assert notices_path.exists()
    notices_text = notices_path.read_text(encoding="utf-8")
    assert "pytesseract" in notices_text


def test_pdf_conversion_limitations_diagnostics():
    """Проверяет явную диагностику ограничений PDF-конвертации без Word/LibreOffice на Windows."""
    import pdf_convert

    status = pdf_convert.get_pdf_conversion_status()
    assert isinstance(status, dict)
    assert "conversion_available" in status
    assert "engine" in status
    assert "libreoffice_available" in status
    assert "docx2pdf_available" in status
    assert "message" in status
    assert isinstance(status["message"], str) and len(status["message"]) > 20

    # Проверяем русскоязычное сообщение при отсутствии обоих конвертеров
    # Создаем эмуляцию отсутствия LibreOffice и docx2pdf
    orig_lo = pdf_convert.check_libreoffice
    orig_docx2pdf = pdf_convert.DOCX2PDF_AVAILABLE
    try:
        pdf_convert.check_libreoffice = lambda: None
        pdf_convert.DOCX2PDF_AVAILABLE = False
        simulated = pdf_convert.get_pdf_conversion_status()
        assert simulated["conversion_available"] is False
        assert simulated["engine"] is None
        assert "установите LibreOffice" in simulated["message"]
        assert "LibreOffice" in simulated["message"]
        assert "OCR" in simulated["message"]
    finally:
        pdf_convert.check_libreoffice = orig_lo
        pdf_convert.DOCX2PDF_AVAILABLE = orig_docx2pdf


def test_spec_file_cross_platform_windows():
    """Проверяет, что спецификация PyInstaller DOCXdodyr.spec корректно конфигурирует сборку под Windows."""
    spec_path = REPO_ROOT / "DOCXdodyr.spec"
    assert spec_path.exists()

    spec_code = spec_path.read_text(encoding="utf-8")
    parsed = ast.parse(spec_code)
    assert parsed is not None

    # Проверка наличия веток win32
    assert 'sys.platform == "win32"' in spec_code
    assert "DOCXdodyr.ico" in spec_code
    assert "DOCXdodyr.manifest" in spec_code
    assert "webview.platforms.winforms" in spec_code
    assert "webview.platforms.edgechromium" in spec_code

    # Проверка, что BUNDLE ограничен macOS
    assert 'if sys.platform == "darwin":' in spec_code
    assert "BUNDLE(" in spec_code


def test_build_windows_script_cli_and_helpers():
    """Проверяет CLI сборочного скрипта scripts/build_windows.py и его вспомогательные функции."""
    build_script = SCRIPTS_DIR / "build_windows.py"
    assert build_script.exists()

    # Запуск --help
    res = subprocess.run(
        [sys.executable, str(build_script), "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0
    assert "--skip-build" in res.stdout
    assert "--skip-installer" in res.stdout
    assert "--skip-sign" in res.stdout

    # Проверка функции поиска signtool и iscc через импорт
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_windows", str(build_script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, "ensure_assets")
    assert hasattr(mod, "build_app")
    assert hasattr(mod, "sign_binaries")
    assert hasattr(mod, "build_installer")
    assert hasattr(mod, "verify_build")

    # Проверка функции ensure_assets без падений
    mod.ensure_assets()
