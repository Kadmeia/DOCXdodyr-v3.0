# -*- coding: utf-8 -*-
"""Автотесты приёмки этапа 06: macOS .app бандл, иконка, Info.plist, entitlements, подпись и DMG."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import pytest

import version

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
DIST_DIR = REPO_ROOT / "dist"
APP_BUNDLE_PATH = DIST_DIR / "DOCXdodyr.app"
SPEC_PATH = REPO_ROOT / "DOCXdodyr.spec"


def test_assets_icon_validity():
    """Проверяет наличие и корректность заголовка и размеров иконки DOCXdodyr.icns."""
    icns_path = ASSETS_DIR / "DOCXdodyr.icns"
    assert icns_path.exists(), f"Иконка отсутствует: {icns_path}"
    assert icns_path.stat().st_size > 50_000, "Файл .icns слишком мал"

    with open(icns_path, "rb") as f:
        magic = f.read(4)
    assert magic == b"icns", f"Некорректный заголовок .icns: {magic}"

    # Проверка наличия iconset
    iconset_dir = ASSETS_DIR / "DOCXdodyr.iconset"
    assert iconset_dir.exists()
    for size_name in ["icon_16x16.png", "icon_32x32.png", "icon_128x128.png", "icon_256x256.png", "icon_512x512@2x.png"]:
        p = iconset_dir / size_name
        assert p.exists(), f"Отсутствует размер иконки: {size_name}"
        assert p.stat().st_size > 0


def test_info_plist_validity_and_synchronization():
    """Проверяет корректность структуры Info.plist и синхронизацию с version.py."""
    plist_path = ASSETS_DIR / "Info.plist"
    assert plist_path.exists(), f"Info.plist не найден: {plist_path}"

    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)

    assert plist.get("CFBundleIdentifier") == version.APP_BUNDLE_ID
    assert plist.get("CFBundleShortVersionString") == version.APP_VERSION
    assert plist.get("CFBundleVersion") == version.APP_VERSION
    assert plist.get("CFBundleName") == version.APP_NAME
    assert plist.get("CFBundleDisplayName") == version.APP_DISPLAY_NAME
    assert plist.get("CFBundleIconFile") == "DOCXdodyr.icns"
    assert plist.get("LSMinimumSystemVersion") == "14.0"
    assert plist.get("NSHighResolutionCapable") is True
    assert plist.get("NSRequiresAquaSystemAppearance") is False

    # Проверка зарегистрированных типов документов
    doc_types = plist.get("CFBundleDocumentTypes", [])
    assert len(doc_types) >= 3, "Должны быть зарегистрированы DOCX, XLSX и PDF"
    all_content_types = []
    for dt in doc_types:
        all_content_types.extend(dt.get("LSItemContentTypes", []))
    assert any("wordprocessingml" in ct for ct in all_content_types)
    assert any("spreadsheetml" in ct for ct in all_content_types)
    assert any("pdf" in ct for ct in all_content_types)


def test_entitlements_validity():
    """Проверяет корректность Hardened Runtime entitlements."""
    ent_path = ASSETS_DIR / "entitlements.plist"
    assert ent_path.exists(), f"entitlements.plist не найден: {ent_path}"

    with open(ent_path, "rb") as f:
        ent = plistlib.load(f)

    assert ent.get("com.apple.security.cs.allow-jit") is True
    assert ent.get("com.apple.security.cs.allow-unsigned-executable-memory") is True
    assert ent.get("com.apple.security.cs.disable-library-validation") is True
    assert ent.get("com.apple.security.network.client") is True
    assert ent.get("com.apple.security.files.user-selected.read-write") is True


def test_pyinstaller_spec_configuration():
    """Проверяет настройки PyInstaller спецификации DOCXdodyr.spec."""
    assert SPEC_PATH.exists(), f"DOCXdodyr.spec не найден: {SPEC_PATH}"
    spec_content = SPEC_PATH.read_text(encoding="utf-8")

    # Проверка включения ключевых данных и модулей
    assert "'web'" in spec_content
    assert "collect_data_files('pullenti')" in spec_content
    assert "collect_data_files('webview')" in spec_content
    assert "collect_submodules('pullenti')" in spec_content
    assert "'platformdirs'" in spec_content
    assert "'log_sanitizer'" in spec_content
    assert "'DOCXdodyr.app'" in spec_content
    for module in ("batch_reconcile", "claim_workflow", "config", "constants", "decoder_binding"):
        assert f"'{module}'" in spec_content

    # Проверка исключения тяжелых компонентов
    for excl in ["'torch'", "'transformers'", "'accelerate'", "'scipy'", "'matplotlib'"]:
        assert excl in spec_content, f"Исключение {excl} должно присутствовать в spec"


def test_build_script_api():
    """Проверяет функции и интерфейс сборочного скрипта scripts/build_macos.py."""
    build_script = REPO_ROOT / "scripts" / "build_macos.py"
    assert build_script.exists()
    assert os.access(build_script, os.X_OK)

    import scripts.build_macos as bmac

    # Проверка fallback на ad-hoc identity
    identity = bmac.get_signing_identity("-")
    assert identity == "-"


@pytest.mark.skipif(not APP_BUNDLE_PATH.exists(), reason="Бандл DOCXdodyr.app ещё не собран")
def test_app_bundle_structure_and_signature():
    """Проверяет структуру собранного приложения и валидность кода через codesign."""
    contents = APP_BUNDLE_PATH / "Contents"
    assert contents.exists()

    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"
    assert macos_dir.exists()
    assert resources_dir.exists()

    main_bin = macos_dir / "DOCXdodyr"
    assert main_bin.exists()
    assert os.access(main_bin, os.X_OK)

    # Проверка веб-ресурсов внутри бандла
    web_dir = macos_dir / "web"
    if not web_dir.exists():
        web_dir = resources_dir / "web"
    assert web_dir.exists()
    assert (web_dir / "index.html").exists()

    # Проверка иконки
    assert (resources_dir / "DOCXdodyr.icns").exists() or (resources_dir / "icon-windowed.icns").exists()

    # Строгая проверка codesign
    res = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP_BUNDLE_PATH)],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0, f"codesign --strict провален:\n{res.stderr}"


@pytest.mark.skipif(sys.platform != "darwin", reason="Требуется macOS для hdiutil")
def test_dmg_verification():
    """Проверяет созданный DMG-образ дистрибутива."""
    dmg_files = list(DIST_DIR.glob(f"DOCXdodyr-{version.APP_VERSION}-*.dmg"))
    if not dmg_files:
        pytest.skip("DMG файл ещё не собран")

    dmg_path = dmg_files[0]
    assert dmg_path.exists()
    size_mb = dmg_path.stat().st_size / (1024 * 1024)
    # Размер должен быть в пределах разумного для легковесного ядра (100–350 МБ)
    assert 50 < size_mb < 350, f"Размер DMG неожиданный: {size_mb:.2f} МБ"

    # hdiutil verify
    res = subprocess.run(["hdiutil", "verify", str(dmg_path)], capture_output=True, text=True)
    assert res.returncode == 0, f"hdiutil verify провален: {res.stderr}"
