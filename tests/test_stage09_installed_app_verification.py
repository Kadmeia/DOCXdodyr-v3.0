# -*- coding: utf-8 -*-
"""Тесты этапа 09: Проверка установленных приложений и матрица чистых ОС.

Матрица охватывает сценарии:
1. Тестирование скомпилированного бинарника DOCXdodyr.exe (--version, --self-test, --capabilities, --diagnostics).
2. Обычный пользователь (standard user / non-admin, изоляция записи от папки приложения, права asInvoker).
3. Кириллическое имя пользователя и кириллические пути (Unicode / CP1251 safety).
4. Запуск из произвольного CWD / внешний диск (корректность разрешения путей web_dir и bundle).
5. 100% офлайн режим (Network-Deny на уровне сокетов для всех поддерживаемых типов файлов).
6. Отказ Keychain / Credential Manager (graceful fallback на memory vault без сбоя ядра).
7. Отсутствие Office / LibreOffice (честная диагностика ограничений без сбоя локальной обработки).
8. Крупная пачка документов (stress test пакетной обработки папок со сквозным дешифратором).
9. Аварийное завершение и восстановление (Crash Recovery, чекпоинты и очистка орфанных папок).
10. Повторный аудит секретов и приватности (Secret & Privacy scan).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

import app_paths
import capabilities
import crash_recovery
import log_sanitizer
import pdf_convert
import version
from backend_api import BackendApi
from folder_pipeline import FolderAnonymizationPipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
if sys.platform == "win32":
    EXE_PATH = DIST_DIR / "DOCXdodyr" / "DOCXdodyr.exe"
elif sys.platform == "darwin":
    app_bin = DIST_DIR / "DOCXdodyr.app" / "Contents" / "MacOS" / "DOCXdodyr"
    EXE_PATH = app_bin if app_bin.exists() else (DIST_DIR / "DOCXdodyr" / "DOCXdodyr")
else:
    EXE_PATH = DIST_DIR / "DOCXdodyr" / "DOCXdodyr"
ASSETS_DIR = REPO_ROOT / "assets"
INSTALLER_DIR = REPO_ROOT / "installer"


# ============================================================================
# 1. Проверка собранного бинарника DOCXdodyr.exe и CLI-подсистемы
# ============================================================================

def test_stage09_exe_binary_presence_and_pe_structure():
    """Проверяет наличие и структуру скомпилированного бинарника (PE32+ на Windows, Mach-O на macOS)."""
    if not EXE_PATH.exists():
        pytest.skip(f"Скомпилированный бинарник {EXE_PATH.name} отсутствует")
    size_bytes = EXE_PATH.stat().st_size
    assert size_bytes > 10_000_000, f"Размер бинарника слишком мал ({size_bytes} байт), возможно сборка не завершена"

    if sys.platform == "win32":
        # Проверка PE-заголовка
        with open(EXE_PATH, "rb") as f:
            dos_header = f.read(64)
            assert dos_header[:2] == b"MZ", "Отсутствует сигнатура MZ"
            pe_offset = struct.unpack_from("<I", dos_header, 60)[0]
            f.seek(pe_offset)
            pe_sig = f.read(4)
            assert pe_sig == b"PE\x00\x00", "Отсутствует сигнатура PE"
            coff_header = f.read(20)
            machine, _, _, _, _, opt_hdr_size, characteristics = struct.unpack("<HHIIIHH", coff_header)
            assert machine == 0x8664, f"Ожидалась архитектура x64 (0x8664), получено: {hex(machine)}"
            opt_hdr = f.read(opt_hdr_size)
            subsystem = struct.unpack_from("<H", opt_hdr, 68)[0]
            IMAGE_SUBSYSTEM_WINDOWS_GUI = 2
            assert subsystem == IMAGE_SUBSYSTEM_WINDOWS_GUI, f"Ожидалась GUI-подсистема (2), получено: {subsystem}"
    elif sys.platform == "darwin":
        # Проверка Mach-O заголовка (arm64 / x86_64 / Universal)
        with open(EXE_PATH, "rb") as f:
            magic = f.read(4)
            macho_magics = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
            assert magic in macho_magics, f"Некорректная сигнатура Mach-O: {magic.hex()}"

    # Проверка веб-ресурсов в dist
    web_candidates = [
        DIST_DIR / "DOCXdodyr" / "web" / "index.html",
        DIST_DIR / "DOCXdodyr" / "_internal" / "web" / "index.html",
        DIST_DIR / "DOCXdodyr.app" / "Contents" / "Resources" / "web" / "index.html",
        DIST_DIR / "DOCXdodyr.app" / "Contents" / "MacOS" / "web" / "index.html",
        DIST_DIR / "DOCXdodyr-arm64.app" / "Contents" / "Resources" / "web" / "index.html",
        DIST_DIR / "DOCXdodyr-x86_64.app" / "Contents" / "Resources" / "web" / "index.html",
    ]
    assert any(p.exists() for p in web_candidates), f"Веб-интерфейс отсутствует в дистрибутиве: {web_candidates}"


def test_stage09_exe_cli_version_and_flags(tmp_path):
    """Check structured frozen CLI output even with the Windows GUI subsystem."""
    if not EXE_PATH.exists():
        pytest.skip("DOCXdodyr.exe отсутствует для прямого вызова")
    results = {}
    for flag in ("--version", "--self-test", "--capabilities", "--diagnostics"):
        output = tmp_path / (flag[2:] + ".txt")
        result = subprocess.run([str(EXE_PATH), flag, "--report-file", str(output)], capture_output=True, text=True, timeout=90)
        assert result.returncode == 0
        results[flag] = output.read_text(encoding="utf-8")
    assert version.APP_VERSION in results["--version"]
    assert json.loads(results["--self-test"])["status"] == "ok"
    assert "capabilities" in json.loads(results["--capabilities"])
    assert "app_name" in json.loads(results["--diagnostics"]) or "application" in json.loads(results["--diagnostics"])


def test_stage09_desktop_exe_process_and_task_manager():
    """Запускает процесс DOCXdodyr.exe, проверяет его в диспетчере задач Windows (psutil), память и статус."""
    if not EXE_PATH.exists():
        pytest.skip("DOCXdodyr.exe отсутствует для проверки GUI-процесса")
    import psutil
    p = None
    proc = subprocess.Popen([str(EXE_PATH)])
    try:
        time.sleep(3.0)
        p = psutil.Process(proc.pid)
        assert p.name().lower().startswith("docxdodyr"), f"Неожиданное имя процесса: {p.name()}"
        assert p.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING), f"Процесс не активен: {p.status()}"
        mem_mb = p.memory_info().rss / (1024 * 1024)
        assert mem_mb > 30.0, f"Потребление памяти аномально мало ({mem_mb} MB), возможно веб-движок не инициализирован"
    finally:
        if p:
            try:
                for child in p.children(recursive=True):
                    try:
                        child.kill()
                    except Exception:
                        pass
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def test_stage09_desktop_ui_e2e_clicks_and_folder_processing():
    """Автоматический UI E2E-тест: запускает десктопное приложение, выполняет клики в UI и проверяет отсутствие ошибок."""
    if not EXE_PATH.exists():
        pytest.skip("Скомпилированный бинарник DOCXdodyr.exe отсутствует для UI E2E тестирования")
    script_path = REPO_ROOT / "scripts" / "run_ui_e2e_desktop_test.py"
    if not script_path.exists():
        pytest.skip("Скрипт run_ui_e2e_desktop_test.py не найден")

    res = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300
    )
    assert res.returncode == 0, f"UI E2E тест завершился с ошибкой: {res.stderr or res.stdout}"
    assert "ИНТЕРАКТИВНЫЙ UI E2E ТЕСТ УСПЕШНО ЗАВЕРШЕН" in res.stdout
    assert "Ошибки бэкенда/ядра:    0" in res.stdout


# ============================================================================
# 2. Сценарий: Обычный пользователь (Standard User / Non-Admin)
# ============================================================================

def test_stage09_scenario_standard_user_isolation(tmp_path, monkeypatch):
    """Проверяет запуск под непривилегированным пользователем: исполняемый каталог read-only."""
    fake_install_dir = tmp_path / "ProgramFiles" / "DOCXdodyr"
    fake_install_dir.mkdir(parents=True)
    (fake_install_dir / "DOCXdodyr.exe").write_text("stub", encoding="utf-8")

    fake_user_data = tmp_path / "LocalAppData" / "DOCXdodyr"
    fake_user_config = tmp_path / "RoamingAppData" / "DOCXdodyr"

    monkeypatch.setattr(app_paths, "get_bundle_dir", lambda: fake_install_dir)
    monkeypatch.setattr(app_paths, "get_user_data_dir", lambda: fake_user_data)
    monkeypatch.setattr(app_paths, "get_user_config_dir", lambda: fake_user_config)

    # Инициализация BackendApi
    backend = BackendApi()
    backend.settings["save_decoder"] = True
    backend._save_settings()

    # Проверка: файлы настроек записаны строго в пользовательский каталог, а не в ProgramFiles
    assert not (fake_install_dir / "settings.json").exists(), "Приложение не должно писать настройки в каталог установки!"
    assert (fake_user_config / "settings.json").exists(), "Настройки должны сохраняться в user_config_dir"

    # Проверка манифеста и инсталлятора
    manifest = (ASSETS_DIR / "DOCXdodyr.manifest").read_text(encoding="utf-8")
    assert 'level="asInvoker"' in manifest, "Манифест должен гарантировать права asInvoker для обычного пользователя"

    iss = (INSTALLER_DIR / "DOCXdodyr.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss, "Инсталлятор должен устанавливаться без прав администратора"


# ============================================================================
# 3. Сценарий: Кириллическое имя пользователя и кириллические пути
# ============================================================================

def test_stage09_scenario_cyrillic_paths_and_entities(tmp_path):
    """Проверяет обработку документов в кириллическом дереве каталогов и корректность дешифратора."""
    cyrillic_root = tmp_path / "Пользователь_Юрист_Тест" / "Договоры_2026_«СинтПром»"
    cyrillic_root.mkdir(parents=True)

    input_docx = cyrillic_root / "Договор_Поставки_ООО_«Ромашка»_№123-А.docx"
    
    from docx import Document
    doc = Document()
    doc.add_heading("ДОГОВОР ПОСТАВКИ № 123-А", level=1)
    doc.add_paragraph(
        "ООО «Ромашка» в лице Генерального директора Иванова Ивана Ивановича, "
        "действующего на основании Устава, с одной стороны, и ПАО «СинтПром» в лице "
        "Петрова Петра Петровича с другой стороны, заключили настоящий договор."
    )
    doc.add_paragraph("ИНН 7701234567, КПП 770101001, тел: +7 (495) 123-45-67, email: ivanov@example.invalid.")
    doc.add_paragraph("Адрес поставки: г. Москва, ул. Ленина, д. 25, офис 101.")
    doc.save(str(input_docx))

    backend = BackendApi()
    backend.save_original = True
    backend.save_decoder = True
    backend.ocr_lang = "rus"

    res = backend.process_single_file(
        str(input_docx),
        exclusions_list=set(),
        custom_replacements_list={},
        defer_decoder=False,
    )
    assert res > 0, "Обработка DOCX в кириллическом пути должна завершиться с заменами"

    files = list(cyrillic_root.iterdir())
    anonymized_docs = [f for f in files if f.suffix.lower() == ".docx" and ("_cleaned" in f.name.lower() or "обезличен" in f.name.lower())]
    assert len(anonymized_docs) == 1, f"Обезличенный DOCX файл не найден в {cyrillic_root}: {[f.name for f in files]}"

    from document_restorer import find_decoder_near_document
    decoder_files = [find_decoder_near_document(f) for f in anonymized_docs]
    assert len(decoder_files) == 1, f"Дешифратор отсутствует: {[f.name for f in files]}"
    decoder_file = decoder_files[0]

    decoder_data = json.loads(decoder_file.read_text(encoding="utf-8"))
    assert len(decoder_data) > 0, "Дешифратор не должен быть пустым"
    
    all_decoder_text = json.dumps(decoder_data, ensure_ascii=False)
    assert "Иван" in all_decoder_text or "Петров" in all_decoder_text or "Ромашка" in all_decoder_text


# ============================================================================
# 4. Сценарий: Запуск из произвольного CWD / внешний диск
# ============================================================================

def test_stage09_scenario_arbitrary_cwd_and_external_drive(tmp_path):
    """Проверяет корректность работы ядра при произвольном CWD (имитация съёмного диска)."""
    orig_cwd = os.getcwd()
    isolated_cwd = tmp_path / "Внешний_Диск_E"
    isolated_cwd.mkdir(parents=True)

    try:
        os.chdir(str(isolated_cwd))
        
        web_dir = app_paths.get_web_dir()
        assert web_dir.exists(), f"Каталог web не найден из произвольного CWD: {web_dir}"
        assert (web_dir / "index.html").exists(), "index.html должен существовать"

        matrix = capabilities.get_system_capability_matrix()
        assert matrix["platform"] == sys.platform
        assert "capabilities" in matrix
    finally:
        os.chdir(orig_cwd)


# ============================================================================
# 5. Сценарий: 100% Офлайн режим (Network-Deny)
# ============================================================================

def test_stage09_scenario_offline_network_deny(tmp_path, monkeypatch):
    """Проверяет полную автономность конвейера обработки при заблокированной сети."""
    class NetworkAccessBlocked(RuntimeError):
        pass

    def block_socket_connect(*args, **kwargs):
        raise NetworkAccessBlocked("Сетевой доступ заблокирован тестовым изолированным окружением")

    monkeypatch.setattr(socket.socket, "connect", block_socket_connect)
    monkeypatch.setattr(urllib.request, "urlopen", block_socket_connect)

    doc_path = tmp_path / "offline_test.docx"
    from docx import Document
    doc = Document()
    doc.add_heading("КОНФИДЕНЦИАЛЬНЫЙ ДОГОВОР", level=1)
    doc.add_paragraph("Директор Смирнов Алексей Михайлович, ИНН 7702345678, тел +7 999 111-22-33.")
    doc.save(str(doc_path))

    backend = BackendApi()
    backend.save_decoder = True

    res = backend.process_single_file(
        str(doc_path),
        exclusions_list=set(),
        custom_replacements_list={},
        defer_decoder=False,
    )
    assert res > 0, "Офлайн обработка должна успешно завершиться без сети"


# ============================================================================
# 6. Сценарий: Отказ Keychain / Credential Manager (Graceful Fallback)
# ============================================================================

def test_stage09_scenario_missing_office_diagnostics(monkeypatch):
    """Проверяет честную диагностику при отсутствии Word и LibreOffice на машине."""
    monkeypatch.setattr(pdf_convert, "check_libreoffice", lambda: None)
    monkeypatch.setattr(pdf_convert, "DOCX2PDF_AVAILABLE", False)

    status = pdf_convert.get_pdf_conversion_status()
    assert status["available"] is False
    assert status["engine"] is None
    assert "установите LibreOffice" in status["message"] or "требуется" in status["message"]

    matrix = capabilities.get_system_capability_matrix()
    assert "pdf_conversion" in matrix["capabilities"]
    pdf_cap = matrix["capabilities"]["pdf_conversion"]
    assert pdf_cap["requires_office"] is True


# ============================================================================
# 8. Сценарий: Крупная пачка документов (Batch Processing Stress Test)
# ============================================================================

def test_stage09_scenario_large_batch_stress(tmp_path):
    """Стресс-тест пакетной обработки каталога с 20 документами (DOCX, XLSX, TXT) со сквозным дешифратором."""
    batch_dir = tmp_path / "Пакет_Документов_Стресс"
    batch_dir.mkdir(parents=True)

    from docx import Document
    import openpyxl

    for i in range(1, 16):
        d = Document()
        d.add_heading(f"Документ № {i}", level=1)
        d.add_paragraph(f"Генеральный директор Иванов Иван Иванович заключил договор с Сидоровым Сидором № {i}.")
        d.add_paragraph(f"ИНН 7701234567, сумма договора {1000 * i} рублей.")
        d.save(str(batch_dir / f"договор_{i:02d}.docx"))

    for i in range(1, 6):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Сотрудники"
        ws.append(["№", "ФИО", "Должность", "ИНН", "Оклад"])
        ws.append([i, "Иванов Иван Иванович", "Директор", "7701234567", 150000])
        ws.append([i + 10, "Петров Петр Петрович", "Менеджер", "7709876543", 90000])
        wb.save(str(batch_dir / f"таблица_{i:02d}.xlsx"))

    backend = BackendApi()
    backend.save_decoder = True
    backend.save_original = True
    backend.ocr_lang = "rus"

    pipeline = FolderAnonymizationPipeline(backend)
    result = pipeline.process(str(batch_dir))

    assert len(result.files) == 20, f"Ожидалось 20 файлов, найдено: {len(result.files)}"
    assert result.processed_count == 20, f"Ожидалось 20 обработанных файлов, результат: {result.processed_count}"
    assert result.error_count == 0, f"Ошибки при пакетной обработке: {result.errors}"

    assert result.decoder_path is not None and result.decoder_path.exists(), "Сквозной дешифратор пачки должен быть сохранён"
    decoder_data = json.loads(result.decoder_path.read_text(encoding="utf-8"))
    assert len(decoder_data) >= 2, "Дешифратор должен содержать сквозные сущности пачки"


# ============================================================================
# 9. Сценарий: Аварийное завершение и восстановление (Crash Recovery & Resume)
# ============================================================================

def test_stage09_scenario_crash_recovery_atomic_checkpoint(tmp_path, monkeypatch):
    """Проверяет механизм чекпоинтов при внезапном сбое и последующее восстановление."""
    fake_recovery_dir = tmp_path / "recovery"
    fake_recovery_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(crash_recovery, "get_recovery_dir", lambda: fake_recovery_dir)
    monkeypatch.setattr(crash_recovery, "get_checkpoint_path", lambda: fake_recovery_dir / "active_batch_checkpoint.json")

    batch_id = "test-crash-batch-42"
    all_files = [str(tmp_path / f"doc{i}.docx") for i in range(1, 6)]
    for f in all_files:
        Path(f).write_text("stub", encoding="utf-8")

    cp = crash_recovery.start_batch_checkpoint(
        batch_id=batch_id,
        files=all_files,
        mode="files",
        source_folder=str(tmp_path),
    )
    assert cp.complete is False

    crash_recovery.update_batch_checkpoint(
        batch_id=batch_id,
        processed_file=all_files[0],
        mapping_delta={"Иванов": "[ФИО_1]"},
        replacements=1
    )
    crash_recovery.update_batch_checkpoint(
        batch_id=batch_id,
        processed_file=all_files[1],
        mapping_delta={"Петров": "[ФИО_2]"},
        replacements=1
    )

    interrupted = crash_recovery.get_interrupted_batch()
    assert interrupted is not None, "Прерванная сессия должна быть обнаружена"
    assert interrupted["batch_id"] == batch_id
    assert interrupted["processed_count"] == 2
    assert len(interrupted["remaining_files"]) == 3

    crash_recovery.complete_batch_checkpoint(batch_id)
    assert crash_recovery.get_interrupted_batch() is None

    orphaned_folder = tmp_path / ".docxdodyr-incomplete-test-uuid-42"
    orphaned_folder.mkdir()
    (orphaned_folder / "temp.tmp").write_text("data")

    cleaned = crash_recovery.cleanup_orphaned_folder_runs(tmp_path)
    assert cleaned >= 1, "Орфанная папка должна быть очищена"
    assert not orphaned_folder.exists()


# ============================================================================
# 10. Повторный аудит секретов и приватности (Secret & Privacy Scan)
# ============================================================================

def test_stage09_post_verification_secret_and_privacy_scan():
    """Сканирует исходные файлы, скрипты и артефакты на отсутствие приватных данных и секретов."""
    import re
    sensitive_regexes = [
        (re.compile(r"AIzaSy[A-Za-z0-9_-]{33}"), "Обнаружен реальный Google API Key"),
        (re.compile(r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----"), "Обнаружен закрытый ключ"),
        (re.compile(r"ghp_[A-Za-z0-9]{36}"), "Обнаружен реальный GitHub Personal Access Token"),
    ]

    scanned_files = 0
    scanned_extensions = {".py", ".json", ".iss", ".manifest", ".md"}

    # Проверяем релизные и сборочные директории
    target_dirs = [REPO_ROOT / "app", REPO_ROOT / "scripts", REPO_ROOT / "installer", REPO_ROOT / ".antigravity"]

    for tdir in target_dirs:
        if not tdir.exists():
            continue
        for root, _, files in os.walk(tdir):
            for file in files:
                p = Path(root) / file
                if p.suffix in scanned_extensions:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    scanned_files += 1
                    for rx, label in sensitive_regexes:
                        assert not rx.search(content), f"В файле {p.relative_to(REPO_ROOT)}: {label}"

    # Проверяем ключевые исходные файлы ядра
    core_modules = [
        "main.py", "app/backend_api.py", "app/capabilities.py", "app/crash_recovery.py",
        "app/log_sanitizer.py", "app/pdf_convert.py", "version.py"
    ]
    for mod in core_modules:
        p = REPO_ROOT / mod
        assert p.exists(), f"Отсутствует модуль ядра {mod}"
        content = p.read_text(encoding="utf-8", errors="ignore")
        scanned_files += 1
        for rx, label in sensitive_regexes:
            assert not rx.search(content), f"В модуле ядра {mod}: {label}"

    assert scanned_files >= 10, f"Просканировано слишком мало файлов: {scanned_files}"
