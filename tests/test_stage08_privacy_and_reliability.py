# -*- coding: utf-8 -*-
"""Автоматические тесты приёмки Этапа 08: Privacy и пользовательская надёжность.

Проверяет:
1. Сетевую изоляцию (network-deny): ядро приложения работает на 100% локально и офлайн.
2. Политику приватности: отсутствие скрытых сетевых обращений.
3. Санитизацию логов и ошибок: удаление путей пользователей (C:\\Users\\..., /Users/...), секретов и PII.
4. Безопасный диагностический отчёт: отсутствие путей, PII и текста документов.
5. Честную матрицу возможностей платформы (DOCX, XLSX, Text PDF, OCR, PDF conversion, Qwen).
6. Защиту от сбоев (Crash Recovery): чекпоинты сессии, обнаружение незавершённых сессий и очистка.
7. Набор тестов на вредоносные и аномальные документы (Malicious Document Suite): XSS в метаданных,
   попытки path traversal, повреждённые zip/pdf контейнеры.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app_paths
import capabilities
import crash_recovery
import log_sanitizer
import privacy_audit
import ui_bridge
from backend_api import BackendApi, Worker


# =====================================================================
# 1. NETWORK-DENY: ПОЛНАЯ СЕТЕВАЯ ИЗОЛЯЦИЯ ЛОКАЛЬНОГО ЯДРА
# =====================================================================

class NetworkAccessBlocked(RuntimeError):
    pass


@pytest.fixture
def enforce_network_deny(monkeypatch):
    """Жёсткий перехватчик любых сетевых попыток на уровне сокетов, urllib и requests."""
    def block_socket_connect(*args, **kwargs):
        raise NetworkAccessBlocked(f"Попытка сетевого соединения через socket: {args}")

    def block_urllib_open(*args, **kwargs):
        raise NetworkAccessBlocked(f"Попытка сетевого запроса через urllib: {args}")

    def block_requests_send(*args, **kwargs):
        raise NetworkAccessBlocked(f"Попытка сетевого запроса через requests: {args}")

    monkeypatch.setattr(socket.socket, "connect", block_socket_connect)
    monkeypatch.setattr(urllib.request, "urlopen", block_urllib_open)
    try:
        import requests
        monkeypatch.setattr(requests.sessions.Session, "request", block_requests_send)
    except ImportError:
        pass


def test_network_deny_local_pipeline(enforce_network_deny, tmp_path):
    """Подтверждает, что локальная обработка DOCX, XLSX и PDF не обращается к сети."""
    # Создаём синтетический DOCX
    from docx import Document
    docx_file = tmp_path / "test_offline.docx"
    doc = Document()
    doc.add_heading("Договор поставки", level=1)
    doc.add_paragraph("Поставщик: Иванов Иван Иванович, Генеральный директор ООО «Ромашка».")
    doc.add_paragraph("ИНН 7701234567, телефон +7 (495) 123-45-67.")
    doc.save(docx_file)

    # Создаём синтетический XLSX
    from openpyxl import Workbook
    xlsx_file = tmp_path / "test_offline.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "ФИО"
    ws["B1"] = "Петров Петр Петрович"
    ws["A2"] = "Счет"
    ws["B2"] = "40702810938000012345"
    wb.save(xlsx_file)

    backend = BackendApi()
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Обработка DOCX в режиме network-deny
    changes_docx = backend.process_single_file(
        str(docx_file),
        exclusions_list=set(),
        custom_replacements_list={},
        defer_decoder=True,
        output_dir=output_dir,
    )
    assert changes_docx > 0

    # 2. Обработка XLSX в режиме network-deny
    changes_xlsx = backend.process_single_file(
        str(xlsx_file),
        exclusions_list=set(),
        custom_replacements_list={},
        defer_decoder=True,
        output_dir=output_dir,
    )
    assert changes_xlsx > 0

    # 3. Очистка метаданных
    report = privacy_audit.clean_file_metadata(docx_file, tmp_path / "docx_clean.docx")
    assert report.changed is True

    # 4. Сертификат аудита
    cert = privacy_audit.build_audit_certificate(docx_file, tmp_path / "docx_clean.docx")
    assert cert["schema_version"] == 1


# =====================================================================
# 2. ПОЛИТИКА ПРИВАТНОСТИ: ЗАЩИТА ОТ SILENT NETWORK И УТЕЧЕК СЕКРЕТОВ
# =====================================================================

def test_user_path_and_pii_sanitization():
    """Проверяет маскирование путей пользователей ОС (Windows/macOS/Linux) и PII."""
    home_dir = str(Path.home())
    test_message = (
        f"Ошибка чтения файла: {home_dir}\\Documents\\СекретныйДоговор.docx. "
        f"Путь на Windows: C:\\Users\\IvanPetrov\\Desktop\\Secret.docx. "
        f"Путь на macOS: /Users/johndoe/Work/Invoice.pdf. "
        f"Путь на Linux: /home/alex/contract.docx. "
        f"API ключ: AIzaSyD3x4mPl3K3y1234567890abcdef. "
        f"Паспорт: 45 12 345678, телефон: +7 (999) 111-22-33."
    )

    sanitized = log_sanitizer.sanitize_log_text(test_message)

    # Проверяем маскирование путей
    assert "IvanPetrov" not in sanitized
    assert "johndoe" not in sanitized
    assert "alex" not in sanitized
    assert "[USER_PATH]" in sanitized

    # Проверяем маскирование секретов и PII
    assert "AIzaSyD3x4mPl3K3y1234567890abcdef" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized
    assert "[REDACTED_PASSPORT]" in sanitized
    assert "[REDACTED_PHONE]" in sanitized


def test_safe_error_message_truncation():
    """Проверяет, что безопасное сообщение об ошибке обрезает текст документа и маскирует пути."""
    huge_document_leak = "Конфиденциальный текст договора " * 100
    long_error = Exception(f"Failed at C:\\Users\\Lawyer\\doc.docx: {huge_document_leak}")

    safe_msg = log_sanitizer.sanitize_error_message(long_error, max_length=150)
    assert len(safe_msg) <= 150
    assert "Lawyer" not in safe_msg
    assert safe_msg == "Exception"
    assert "Конфиденциальный" not in safe_msg


def test_safe_diagnostic_report():
    """Проверяет формирование безопасного отчёта диагностики без PII и путей."""
    diag = log_sanitizer.get_safe_diagnostic_report()
    assert diag["app_name"] == "DOCXdodyr"
    assert "app_version" in diag
    assert "platform" in diag
    assert diag["privacy_status"]["local_only_core"] is True
    assert diag["privacy_status"]["telemetry_enabled"] is False

    diag_json = json.dumps(diag)
    # Гарантируем отсутствие имени текущего пользователя в диагностике
    import getpass
    username = getpass.getuser()
    if username and len(username) > 3:
        assert username not in diag_json


# =====================================================================
# 4. ЧЕСТНАЯ МАТРИЦА ВОЗМОЖНОСТЕЙ ПЛАТФОРМЫ (CAPABILITY MATRIX)
# =====================================================================

def test_system_capability_matrix():
    """Проверяет честную матрицу возможностей компонентов системы."""
    backend = BackendApi()
    matrix = capabilities.get_system_capability_matrix(backend)
    caps = matrix["capabilities"]

    # Локальные компоненты должны быть строго offline: True
    assert caps["docx"]["offline"] is True
    assert caps["xlsx"]["offline"] is True
    assert caps["text_pdf"]["offline"] is True
    assert caps["scan_pdf_ocr"]["offline"] is True
    assert caps["pdf_conversion"]["offline"] is True
    assert caps["irreversible_pdf"]["offline"] is True
    assert caps["qwen_offline"]["offline"] is True


# =====================================================================
# 5. ЗАЩИТА ОТ СБОЕВ И ВОССТАНОВЛЕНИЕ СЕССИЙ (CRASH RECOVERY)
# =====================================================================

def test_crash_recovery_checkpoint_lifecycle(tmp_path, monkeypatch):
    """Проверяет жизненный цикл контрольной точки: старт, прогресс, прерывание и очистка."""
    recovery_dir = tmp_path / "recovery"
    monkeypatch.setattr(crash_recovery, "get_recovery_dir", lambda: recovery_dir)
    monkeypatch.setattr(crash_recovery, "get_checkpoint_path", lambda: recovery_dir / "active_batch_checkpoint.json")

    batch_id = "test-session-123"
    files = [str(tmp_path / f"doc_{i}.docx") for i in range(5)]

    # 1. Старт сессии
    checkpoint = crash_recovery.start_batch_checkpoint(batch_id, files)
    assert len(checkpoint.files_total) == 5
    assert len(checkpoint.processed_files) == 0

    # 2. Обработка двух файлов
    crash_recovery.update_batch_checkpoint(
        batch_id,
        processed_file=files[0],
        mapping_delta={"[ФИО_1]": "Иванов И.И."},
        replacements=3,
    )
    crash_recovery.update_batch_checkpoint(
        batch_id,
        processed_file=files[1],
        mapping_delta={"[ФИО_2]": "Петров П.П."},
        replacements=2,
    )

    # 3. Проверка обнаружения незавершённой сессии (имитация падения приложения)
    interrupted = crash_recovery.get_interrupted_batch()
    assert interrupted is not None
    assert interrupted["batch_id"] == batch_id
    assert interrupted["processed_count"] == 2
    assert interrupted["remaining_count"] == 3
    assert interrupted["entities_mapped"] == 2
    assert interrupted["total_replacements"] == 5
    checkpoint_text = crash_recovery.get_checkpoint_path().read_text(encoding="utf-8")
    assert "Иванов И.И." not in checkpoint_text
    assert "Петров П.П." not in checkpoint_text

    # 4. Завершение сессии
    crash_recovery.complete_batch_checkpoint(batch_id)
    assert crash_recovery.get_interrupted_batch() is None


def test_crash_recovery_does_not_delete_another_batch(tmp_path, monkeypatch):
    recovery_dir = tmp_path / "recovery"
    monkeypatch.setattr(crash_recovery, "get_recovery_dir", lambda: recovery_dir)
    monkeypatch.setattr(crash_recovery, "get_checkpoint_path", lambda: recovery_dir / "active_batch_checkpoint.json")

    crash_recovery.start_batch_checkpoint("new-batch", [tmp_path / "doc.docx"])
    crash_recovery.complete_batch_checkpoint("old-batch")

    assert crash_recovery.get_interrupted_batch()["batch_id"] == "new-batch"


def test_orphaned_folder_runs_cleanup(tmp_path):
    """Проверяет обнаружение и очистку брошенных временных каталогов .docxdodyr-incomplete-*."""
    folder = tmp_path / "docs"
    folder.mkdir()

    # Создаём обычные файлы и брошенные папки
    (folder / "file.docx").write_text("dummy", encoding="utf-8")
    orphan_1 = folder / ".docxdodyr-incomplete-aaa111"
    orphan_1.mkdir()
    orphan_2 = folder / ".docxdodyr-incomplete-bbb222"
    orphan_2.mkdir()

    orphans = crash_recovery.find_orphaned_folder_runs(folder)
    assert len(orphans) == 2

    cleaned = crash_recovery.cleanup_orphaned_folder_runs(folder)
    assert cleaned == 2
    assert len(crash_recovery.find_orphaned_folder_runs(folder)) == 0
    assert (folder / "file.docx").exists()


# =====================================================================
# 6. MALICIOUS DOCUMENT SUITE: АНОМАЛЬНЫЕ И ВРЕДОНОСНЫЕ ДОКУМЕНТЫ
# =====================================================================

def test_malicious_xss_in_docx_metadata_and_ui_bridge(tmp_path):
    """Проверяет нейтрализацию XSS инъекций в метаданных DOCX при передаче в UI мост."""
    import zipfile
    from xml.etree import ElementTree as ET

    docx_xss = tmp_path / "malicious_xss.docx"
    
    # Создаём DOCX с XSS пейлоадом в метаданных и тексте
    xss_payload = "<script>alert('XSS_BREACH')</script><img src=x onerror=alert(1)>"
    
    from docx import Document
    doc = Document()
    doc.add_paragraph(f"Текст с инъекцией: {xss_payload}")
    doc.core_properties.title = xss_payload
    doc.core_properties.comments = xss_payload
    doc.save(docx_xss)

    # 1. Очистка метаданных должна безопасно удалить XSS из core.xml
    cleaned_docx = tmp_path / "cleaned_xss.docx"
    report = privacy_audit.clean_file_metadata(docx_xss, cleaned_docx)
    assert report.changed is True

    with zipfile.ZipFile(cleaned_docx, "r") as z:
        core_xml = z.read("docProps/core.xml").decode("utf-8")
        assert "alert('XSS_BREACH')" not in core_xml

    # 2. Сериализация в UI-мост должна экранировать опасные теги
    safe_js = ui_bridge.serialize_js_arg({"title": xss_payload, "snippet": f"</script>{xss_payload}"})
    assert "</script" not in safe_js
    assert "<\\/script" in safe_js


def test_malicious_corrupted_containers_safe_handling(tmp_path):
    """Проверяет безопасную обработку обрезанных и битых файлов без падения процесса."""
    backend = BackendApi()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # 1. Битый DOCX (обрезанный zip)
    corrupted_docx = tmp_path / "broken.docx"
    corrupted_docx.write_bytes(b"PK\x03\x04corrupted_header_data_truncated")

    with pytest.raises(Exception):
        backend.process_single_file(
            str(corrupted_docx),
            set(),
            {},
            output_dir=out_dir,
        )

    # 2. Нулевой файл
    empty_docx = tmp_path / "empty.docx"
    empty_docx.write_bytes(b"")

    with pytest.raises(Exception):
        backend.process_single_file(
            str(empty_docx),
            set(),
            {},
            output_dir=out_dir,
        )

    # 3. Битый PDF
    broken_pdf = tmp_path / "broken.pdf"
    broken_pdf.write_bytes(b"%PDF-1.4\n%corrupted trailer and missing xref")

    clean_pdf_report = privacy_audit.clean_file_metadata(broken_pdf, tmp_path / "out_pdf.pdf")
    # При ошибке чтения PDF небезопасная исходная копия не публикуется.
    assert clean_pdf_report.output_path is not None
    assert clean_pdf_report.error
    assert not (tmp_path / "out_pdf.pdf").exists()


def test_path_traversal_prevention_in_output():
    """Проверяет защиту от выхода за пределы выходного каталога (Path Traversal)."""
    from folder_pipeline import _is_inside

    root = Path("/safe/root/folder")
    evil_path = Path("/safe/root/folder/../../etc/passwd")

    assert _is_inside(root / "subfolder" / "doc.docx", root) is True
    assert _is_inside(evil_path, root) is False
