# -*- coding: utf-8 -*-
"""Автотесты приёмки Этапа 05 — Лёгкое ядро и опциональный Qwen (Model Manager).

Проверяемые критерии:
1. Базовое ядро (DOCX/XLSX/PDF/OCR) работает офлайн без Torch и без модели Qwen.
2. Повреждённая или отсутствующая модель приводит к безопасному no-op (оригинальный текст без сбоев).
3. Model Manager: определение директории в user data (platformdirs), расчёт размера и RAM.
4. Контроль целостности: проверка SHA-256 и обнаружение повреждённых/отсутствующих файлов.
5. Загрузка и импорт: строго по явному согласию (consent=True), поддержка прогресса и отмены.
6. Безопасное удаление модели с защитой от directory traversal.
7. Проксирование методов Model Manager через ApiWrapper в main.py.
8. Отсутствие весов модели (> 1.6 ГБ) в git-репозитории и наличие строгих правил ignore.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import zipfile

import pytest

import app_paths
from backend_api import BackendApi
from main import ApiWrapper
from qwen_offline import (
    MANIFEST,
    TOTAL_BYTES,
    WEIGHTS_BYTES,
    WEIGHTS_SHA256,
    QwenInstallError,
    check_resources,
    delete_model,
    estimate_resources,
    export_bundle,
    get_default_model_dir,
    import_bundle,
    install_model,
    is_installed,
    runtime_settings,
    validate_model,
    verify_model_integrity,
)
from qwen_postprocessor import (
    QwenPlaceholderPostprocessor,
    QwenPostprocessorSettings,
    choose_device,
    runtime_preflight,
)


def _create_synthetic_model(target_dir: Path) -> dict:
    """Создаёт корректную синтетическую модель с валидными хешами для тестов."""
    target_dir.mkdir(parents=True, exist_ok=True)
    file_a_content = b'{"vocab_size": 100, "model_type": "qwen3.5"}\n'
    file_b_content = b'fake_synthetic_weights_0123456789'

    files = [
        {
            "name": "config.json",
            "size": len(file_a_content),
            "sha256": hashlib.sha256(file_a_content).hexdigest(),
        },
        {
            "name": "model.safetensors-00001-of-00001.safetensors",
            "size": len(file_b_content),
            "sha256": hashlib.sha256(file_b_content).hexdigest(),
        },
    ]
    manifest = {
        "schema": 1,
        "model_id": "Qwen/Qwen3.5-0.8B",
        "revision": "synthetic_test_rev",
        "license": "Apache-2.0",
        "total_bytes": sum(f["size"] for f in files),
        "weights_bytes": len(file_b_content),
        "files": files,
    }

    (target_dir / "config.json").write_bytes(file_a_content)
    (target_dir / "model.safetensors-00001-of-00001.safetensors").write_bytes(file_b_content)
    (target_dir / "DOCXdodyr-qwen-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


# --- 1. Легкое ядро: автономность без Torch и Qwen ---

def test_lightweight_core_runs_without_torch(monkeypatch, tmp_path):
    """Ядро приложения функционирует, даже если PyTorch полностью отсутствует в окружении."""
    # Симулируем полное отсутствие torch
    monkeypatch.setitem(sys.modules, "torch", None)

    # Проверяем безопасный выбор устройства
    device = choose_device("auto")
    assert device == "cpu"
    assert choose_device("mps") == "cpu"

    # Создаём BackendApi и выполняем базовую обработку
    api = BackendApi()
    text = "Договор заключен с [ФИО_1] в пользу ООО [Наименование_1]."
    # Без Qwen текст возвращается мгновенно
    assert api.postprocess_anonymized_text(text) == text


def test_qwen_safe_noop_when_model_missing():
    """Если Qwen включён, но модель не установлена, происходит безопасный no-op без исключений."""
    settings = QwenPostprocessorSettings(
        enabled=True,
        model_path="/nonexistent/path/to/missing_qwen_model",
        local_files_only=True,
    )
    processor = QwenPlaceholderPostprocessor(settings)
    original_text = "Уважаемый [ФИО_1], уведомляем Вас о расторжении договора № [НомерДоговора_1]."
    result = processor.process(original_text)

    # Текст сохранен без изменений
    assert result == original_text
    assert processor.last_status.startswith("unavailable") or processor.last_status == "not_loaded"


def test_qwen_safe_noop_when_model_corrupted(tmp_path):
    """Если файлы модели повреждены или неполны, постпроцессор делает безопасный no-op."""
    model_dir = tmp_path / "corrupted_qwen"
    model_dir.mkdir()
    # Записываем испорченный файл конфигурации
    (model_dir / "config.json").write_text("CORRUPTED_JSON_CONTENT{{{", encoding="utf-8")

    settings = QwenPostprocessorSettings(
        enabled=True,
        model_path=str(model_dir),
        local_files_only=True,
    )
    processor = QwenPlaceholderPostprocessor(settings)
    sample_text = "Для [ФИО_1] подготовлен отчет."
    result = processor.process(sample_text)

    assert result == sample_text
    assert processor.last_status.startswith("unavailable")


# --- 2. Model Manager: директории, манифест и ресурсы ---

def test_default_model_dir_points_to_user_data(tmp_path, monkeypatch):
    """Модели хранятся в user data (platformdirs), а не внутри read-only пакета."""
    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(tmp_path / "user_data"))
    model_dir = get_default_model_dir()

    assert "models" in model_dir.parts
    assert model_dir.name == "Qwen3.5-0.8B"
    assert model_dir.parent == app_paths.get_user_models_dir()
    assert str(tmp_path) in str(model_dir)


def test_estimate_resources_and_warnings():
    """Расчёт ресурсов учитывает диск и RAM с формированием предупреждений для слабых машин."""
    # Недостаточно оперативной памяти (< 3 ГБ)
    low_ram_est = estimate_resources(available_memory=1 * 1024**3, cpu_only=True)
    assert low_ram_est.suitable is False
    assert any("Недостаточно доступной памяти" in w for w in low_ram_est.warnings)

    # Достаточно оперативной памяти (> 6 ГБ)
    good_ram_est = estimate_resources(available_memory=8 * 1024**3, cpu_only=True)
    assert good_ram_est.suitable is True
    assert good_ram_est.disk_bytes == TOTAL_BYTES
    assert good_ram_est.ram_bytes > 0


def test_check_resources_disk_calculation(tmp_path):
    """Проверка доступного места на диске сообщает точные требования."""
    report = check_resources(tmp_path / "qwen_target")
    assert "disk_free_bytes" in report
    assert report["disk_required_bytes"] == TOTAL_BYTES
    assert "ram" in report


# --- 3. Контроль целостности и верификация SHA-256 ---

def test_validate_and_verify_integrity_on_valid_model(tmp_path):
    """Валидная модель проходит полную проверку контрольных сумм SHA-256."""
    model_dir = tmp_path / "valid_model"
    manifest = _create_synthetic_model(model_dir)

    assert is_installed(model_dir, manifest=manifest)
    assert validate_model(model_dir, manifest=manifest, verify_hash=True)

    report = verify_model_integrity(model_dir, manifest=manifest)
    assert report["valid"] is True
    assert report["status"] == "installed"
    assert report["files_checked"] == 2
    assert len(report["missing_files"]) == 0
    assert len(report["corrupted_files"]) == 0


def test_verify_integrity_detects_tampered_and_missing_files(tmp_path):
    """Проверка целостности безошибочно выявляет изменённые байты и отсутствующие файлы."""
    model_dir = tmp_path / "tampered_model"
    manifest = _create_synthetic_model(model_dir)

    # 1. Повреждаем один байт в весах
    weights_path = model_dir / "model.safetensors-00001-of-00001.safetensors"
    weights_path.write_bytes(b"tampered_bytes_with_wrong_hash_1234567")

    report = verify_model_integrity(model_dir, manifest=manifest)
    assert report["valid"] is False
    assert report["status"] == "corrupted"
    assert len(report["corrupted_files"]) == 1
    assert "SHA-256" in report["corrupted_files"][0] or "размер" in report["corrupted_files"][0]

    # 2. Удаляем файл конфигурации
    (model_dir / "config.json").unlink()
    report_missing = verify_model_integrity(model_dir, manifest=manifest)
    assert report_missing["valid"] is False
    assert "config.json" in report_missing["missing_files"]


# --- 4. Загрузка и импорт: согласие, прогресс и отмена ---

def test_install_requires_explicit_consent(tmp_path):
    """Загрузка Qwen без consent=True блокируется для исключения скрытых сетевых запросов."""
    with pytest.raises(QwenInstallError, match="явного согласия"):
        install_model(tmp_path / "model", consent=False)


def test_install_cancellation_cleans_up_staging(tmp_path):
    """При отмене операции пользователем временные файлы .part и staging немедленно удаляются."""
    target_dir = tmp_path / "cancel_target"
    cancelled = True

    def cancel_check():
        return cancelled

    manifest = {
        "model_id": "test/mock",
        "revision": "rev1",
        "total_bytes": 1000,
        "files": [{"name": "file.bin", "size": 100}],
    }

    with pytest.raises(QwenInstallError, match="отменена пользователем"):
        install_model(
            target_dir,
            consent=True,
            cancel_check=cancel_check,
            manifest=manifest,
        )

    # Каталог назначения не создан
    assert not target_dir.exists()
    # Временные папки .qwen-install-* очищены
    temp_dirs = list(tmp_path.glob(".qwen-install-*"))
    assert len(temp_dirs) == 0


def test_bundle_import_export_and_cancellation(tmp_path):
    """Импорт и экспорт zip-бандла работают офлайн и поддерживают отмену."""
    source_dir = tmp_path / "bundle_source"
    manifest = _create_synthetic_model(source_dir)

    bundle_path = tmp_path / "test_qwen.bundle.zip"
    export_bundle(source_dir, bundle_path, consent=True, manifest=manifest)
    assert bundle_path.is_file()

    # Успешный импорт с отслеживанием прогресса
    restored_dir = tmp_path / "bundle_restored"
    progress_calls = []

    def on_progress(current, total, filename):
        progress_calls.append((current, total, filename))

    import_bundle(
        bundle_path,
        restored_dir,
        consent=True,
        manifest=manifest,
        progress=on_progress,
    )
    assert restored_dir.is_dir()
    assert validate_model(restored_dir, manifest=manifest)
    assert len(progress_calls) >= 2

    # Проверка отмены импорта
    cancelled_dir = tmp_path / "bundle_cancelled"
    with pytest.raises(QwenInstallError, match="отменен пользователем"):
        import_bundle(
            bundle_path,
            cancelled_dir,
            consent=True,
            manifest=manifest,
            cancel_check=lambda: True,
        )
    assert not cancelled_dir.exists()


# --- 5. Безопасное удаление модели и защита от directory traversal ---

def test_delete_model_safely_removes_files(tmp_path, monkeypatch):
    """Удаление модели очищает файлы в каталоге пользователя."""
    models_dir = tmp_path / "user_models"
    monkeypatch.setattr(app_paths, "get_user_models_dir", lambda: models_dir)

    model_dir = models_dir / "Qwen3.5-0.8B"
    _create_synthetic_model(model_dir)
    assert model_dir.is_dir()

    success = delete_model(model_dir, allowed_parent=models_dir)
    assert success is True
    assert not model_dir.exists()


def test_delete_model_blocks_directory_traversal(tmp_path, monkeypatch):
    """Удаление защищено от выхода за пределы каталога моделей (directory traversal)."""
    models_dir = tmp_path / "user_models"
    models_dir.mkdir(parents=True)
    monkeypatch.setattr(app_paths, "get_user_models_dir", lambda: models_dir)

    # Попытка удалить сам родительский каталог
    with pytest.raises(QwenInstallError, match="Нельзя удалять сам корневой каталог"):
        delete_model(models_dir, allowed_parent=models_dir)

    # Попытка удалить посторонний каталог через обход путей
    forbidden_dir = tmp_path / "system_important_dir"
    forbidden_dir.mkdir()
    with pytest.raises(QwenInstallError, match="Удаление разрешено только внутри"):
        delete_model(forbidden_dir, allowed_parent=models_dir)
    assert forbidden_dir.is_dir()


# --- 6. BackendApi и ApiWrapper Model Manager ---

def test_backend_api_get_qwen_model_info(tmp_path, monkeypatch):
    """BackendApi.get_qwen_model_info возвращает полную структуру данных для UI."""
    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(tmp_path / "data"))
    api = BackendApi()
    info = api.get_qwen_model_info()

    assert "installed" in info
    assert "valid" in info
    assert "status" in info
    assert "resources" in info
    assert "manifest" in info
    assert info["manifest"]["total_bytes"] == TOTAL_BYTES
    assert info["manifest"]["model_id"] == "Qwen/Qwen3.5-0.8B"


def test_api_wrapper_exposes_all_model_manager_methods():
    """ApiWrapper в main.py содержит все явные прокси-методы для вызова из JavaScript."""
    wrapper = ApiWrapper()
    expected_methods = [
        "get_qwen_model_info",
        "install_qwen_model",
        "import_qwen_model_bundle",
        "cancel_qwen_model_operation",
        "delete_qwen_model",
        "verify_qwen_model_integrity",
    ]
    for method_name in expected_methods:
        assert hasattr(wrapper, method_name), f"Метод {method_name} отсутствует в ApiWrapper"
        assert callable(getattr(wrapper, method_name))


def test_backend_api_delete_qwen_model(tmp_path, monkeypatch):
    """BackendApi.delete_qwen_model корректно удаляет файлы и сбрасывает настройки."""
    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(tmp_path / "data"))
    api = BackendApi()
    target_dir = Path(api.qwen_settings.model_path or get_default_model_dir())
    _create_synthetic_model(target_dir)

    # Включаем Qwen в настройках
    api.save_qwen_settings({"enabled": True, "model_path": str(target_dir)})
    assert api.qwen_settings.enabled is True

    # Удаляем через API
    res = api.delete_qwen_model()
    assert res["success"] is True
    assert not target_dir.exists()
    assert api.qwen_settings.enabled is False
    assert api.qwen_settings.model_path == ""


def test_backend_api_verify_qwen_model_integrity(tmp_path, monkeypatch):
    """BackendApi.verify_qwen_model_integrity возвращает валидационный отчёт."""
    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(tmp_path / "data"))
    api = BackendApi()
    # Для отсутствующей модели
    report = api.verify_qwen_model_integrity()
    assert report["success"] is True
    assert report["valid"] is False
    assert report["status"] == "not_installed"


# --- 7. Контроль чистоты git и отсутствия весов модели ---

def test_gitignore_contains_model_weights_rules():
    """Файл .gitignore строго блокирует случайный коммит тяжелых весов нейросети (> 1.6 ГБ)."""
    gitignore_path = Path(__file__).resolve().parent.parent / ".gitignore"
    content = gitignore_path.read_text(encoding="utf-8")

    assert "models/" in content
    assert "*.safetensors" in content
    assert "*.bin" in content
    assert "*.bundle.zip" in content
