# -*- mode: python ; coding: utf-8 -*-
"""Спецификация PyInstaller для сборки macOS .app приложения DOCXдодыр."""

import os
from pathlib import Path
import plistlib
import sys

if sys.version_info[:2] != (3, 11):
    raise SystemExit("PyInstaller build requires Python 3.11")

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None
REPO_ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'app'))
from version import __version__ as APP_VERSION
target_arch = os.environ.get("DOCXDODYR_TARGET_ARCH")
if sys.platform == "darwin" and target_arch not in ("arm64", "x86_64"):
    raise ValueError("Set DOCXDODYR_TARGET_ARCH to arm64 or x86_64")
BUNDLE_QWEN = os.environ.get("DOCXDODYR_BUNDLE_QWEN", "0").strip().lower() in {"1", "true", "yes"}

# 1. Сбор статических данных
datas = [
    (str(REPO_ROOT / 'web'), 'web'),
    (str(REPO_ROOT / 'SECURITY.md'), '.'),
    (str(REPO_ROOT / 'DISCLAIMER.md'), '.'),
    (str(REPO_ROOT / 'NOTICE.txt'), '.'),
    (str(REPO_ROOT / 'EULA.txt'), '.'),
    (str(REPO_ROOT / 'PRIVACY_POLICY.md'), '.'),
    (str(REPO_ROOT / 'THIRD_PARTY_NOTICES.md'), '.'),
]

if (REPO_ROOT / 'resources' / 'tessdata').exists():
    datas.append((str(REPO_ROOT / 'resources' / 'tessdata'), 'resources/tessdata'))

SHIPPED_DOCS = [
    'USER_GUIDE.md', 'WORKFLOWS.md', 'UI_MAP.md', 'TROUBLESHOOTING.md',
    'TECHNICAL_OVERVIEW.md', 'DATA_RETENTION_AND_DELETION.md',
    'DOCUMENTATION_ACCEPTANCE.md', 'DEPENDENCY_READINESS.md',
    'RELEASE_NOTES_v3.0.0.md',
]
datas += [(str(REPO_ROOT / 'docs' / name), 'docs') for name in SHIPPED_DOCS]

# Сбор встроенных словарей и данных Pullenti, PyWebView и docx
datas += collect_data_files('pullenti')
datas += collect_data_files('webview')
datas += collect_data_files('docx')

# 2. Сбор скрытых импортов
hiddenimports = [
    # Внутренние модули приложения
    'version',
    'app_paths',
    'docx_compat',
    'log_sanitizer',
    'state_migration',
    'ui_bridge',
    'backend_api',
    'batch_reconcile',
    'capabilities',
    'claim_workflow',
    'config',
    'constants',
    'crash_recovery',
    'decoder_binding',
    'folder_pipeline',
    'pdf_convert',
    'ocr_backend',
    'markdown_export',
    'validation',
    'packaged_smoke',
    'packaged_qwen_smoke',
    'packaged_gui_smoke',
    'document_restorer',
    'entity_registry',
    'entity_utils',
    'entity_inflection',
    'legal_pullenti',
    'placeholders',
    'hidden_data',
    'privacy_audit',
    'review_context',
    'review_rebuild',
    'table_entity_context',
    'xlsx_semantic',
    'qwen_offline',
    'qwen_postprocessor',
    'intel_torch_compat',
    'morph_singleton',
    'detection_engine',
    # Библиотеки документов и системы
    'platformdirs',
    'docx',
    'docx.oxml',
    'docx.opc',
    'openpyxl',
    'openpyxl.cell',
    'openpyxl.worksheet',
    'fitz',
    'webview',
]

if sys.platform == "win32":
    hiddenimports += [
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'docx2pdf',
        'windows_context_menu',
    ]
elif sys.platform == "darwin":
    hiddenimports += [
        'webview.platforms.cocoa',
    ]
    # Нативные macOS фреймворки (PyObjC)
    for objc_mod in ['objc', 'Foundation', 'AppKit', 'WebKit', 'Quartz', 'Vision', 'Security', 'UniformTypeIdentifiers']:
        try:
            hiddenimports += collect_submodules(objc_mod)
        except Exception:
            hiddenimports.append(objc_mod)

# Все подмодули Pullenti (для корректной работы NER и морфологии)
hiddenimports += collect_submodules('pullenti')
hiddenimports += collect_submodules('pullenti_legal')
hiddenimports.append('pullenti_legal')

# 2. Сбор бинарников
binaries = []

# 3. Исключения (тяжелые или dev-библиотеки, не входящие в базовое ядро)
excludes = [
    'scipy',
    'matplotlib',
    'pytest',
    'pytest_mock',
    'pip',
    'pip_tools',
    'tkinter',
    'test',
]

# Qwen is an optional frozen-app capability.  The base application deliberately
# excludes the large ML stack; an opt-in build must use the pinned local
# requirements-qwen environment and still keeps model weights outside the app
# bundle (installed/imported into the user model directory after consent).
if not BUNDLE_QWEN:
    excludes += ['torch', 'transformers', 'accelerate']
else:
    # PyTorch has substantial lazy Python and native-extension imports.  Source
    # files are required by torch's runtime introspection in a frozen app, while
    # collect_dynamic_libs captures the platform-specific *.so/*.dylib payload.
    hiddenimports += collect_submodules('torch')
    hiddenimports += collect_submodules('torchvision')
    hiddenimports += collect_submodules('safetensors')
    hiddenimports += collect_submodules('tokenizers')
    hiddenimports += collect_submodules('transformers')
    hiddenimports += [
        'transformers.models.qwen3_5',
        'transformers.models.qwen3_5.configuration_qwen3_5',
        'transformers.models.qwen3_5.modeling_qwen3_5',
        'transformers.models.qwen3_vl.processing_qwen3_vl',
        'transformers.models.qwen2.tokenization_qwen2',
        'transformers.models.qwen3_5.tokenization_qwen3_5',
        'transformers.models.qwen3_5_moe',
        'transformers.models.qwen3_5_moe.configuration_qwen3_5_moe',
        'transformers.models.qwen3_5_moe.modeling_qwen3_5_moe',
        'transformers.models.auto',
        'transformers.models.auto.processing_auto',
    ]
    datas += collect_data_files('torch', include_py_files=True)
    datas += collect_data_files('torchvision', include_py_files=True)
    datas += collect_data_files('transformers', include_py_files=True)
    datas += collect_data_files('safetensors', include_py_files=True)
    datas += collect_data_files('tokenizers', include_py_files=True)
    binaries += (
        collect_dynamic_libs('torch')
        + collect_dynamic_libs('torchvision')
        + collect_dynamic_libs('safetensors')
        + collect_dynamic_libs('tokenizers')
    )

os.makedirs(os.path.join(str(REPO_ROOT), 'build', 'DOCXdodyr'), exist_ok=True)

a = Analysis(
    ['main.py'],
    pathex=[str(REPO_ROOT), str(REPO_ROOT / 'app')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(REPO_ROOT / 'scripts' / 'pyinstaller_intel_hook.py')],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == "win32":
    app_icon = str(REPO_ROOT / 'assets' / 'DOCXdodyr.ico') if (REPO_ROOT / 'assets' / 'DOCXdodyr.ico').exists() else None
    app_manifest = str(REPO_ROOT / 'assets' / 'DOCXdodyr.manifest') if (REPO_ROOT / 'assets' / 'DOCXdodyr.manifest').exists() else None
else:
    app_icon = str(REPO_ROOT / 'assets' / 'DOCXdodyr.icns') if (REPO_ROOT / 'assets' / 'DOCXdodyr.icns').exists() else None
    app_manifest = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DOCXdodyr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
    manifest=app_manifest,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DOCXdodyr',
)

if sys.platform == "darwin":
    # Загрузка кастомного Info.plist для BUNDLE на macOS
    info_plist_path = REPO_ROOT / 'assets' / 'Info.plist'
    info_plist_dict = None
    if info_plist_path.exists():
        with open(info_plist_path, 'rb') as fp:
            info_plist_dict = plistlib.load(fp)

    app = BUNDLE(
        coll,
        name='DOCXdodyr.app',
        icon=app_icon,
        bundle_identifier='ru.docxdodyr.desktop',
        version=APP_VERSION,
        info_plist=info_plist_dict,
    )
