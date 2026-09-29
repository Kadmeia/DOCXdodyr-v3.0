# -*- coding: utf-8 -*-
"""Root pytest fixtures ensuring full filesystem and keyring isolation during test runs."""

import os
import sys
if sys.version_info[:2] != (3, 11):
    raise RuntimeError("Tests require Python 3.11")

import shutil
import tempfile
from pathlib import Path
import pytest

# Базовая сессионная изоляция путей (защищает module-level фикстуры от чтения локальных пользовательских настроек)
_default_test_data = Path(tempfile.gettempdir()) / "docxdodyr_test_runtime"
_default_test_data.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DOCXDODYR_CONFIG_DIR", str(_default_test_data / "config"))
os.environ.setdefault("DOCXDODYR_DATA_DIR", str(_default_test_data / "data"))
os.environ.setdefault("DOCXDODYR_USER_DATA_DIR", str(_default_test_data / "data"))
os.environ.setdefault("DOCXDODYR_CACHE_DIR", str(_default_test_data / "cache"))
os.environ.setdefault("DOCXDODYR_LOG_DIR", str(_default_test_data / "logs"))

import app_paths


@pytest.fixture(scope="session")
def real_qwen_processor():
    from qwen_postprocessor import QwenPlaceholderPostprocessor
    model_dir = os.environ.get("QWEN_MODEL_DIR", "")
    if not model_dir:
        pytest.skip("QWEN_MODEL_DIR not set; native real-model evidence required separately")
    processor = QwenPlaceholderPostprocessor({
        "enabled": True, "model_path": model_dir, "local_files_only": True,
        "allow_network_download": False, "max_new_tokens": 128, "temperature": 0.0,
    })
    yield processor
    processor.reset()


@pytest.fixture(autouse=True)
def isolated_recovery_key(monkeypatch):
    # Unit/source tests must not read or create a user's OS keychain entries.
    # Native keyring acceptance is a separate packaged verification gate.
    import crash_recovery
    from review_context import SecureContextVault
    key = os.urandom(32)
    monkeypatch.setattr(crash_recovery, "_get_recovery_vault", lambda:
        SecureContextVault(crash_recovery.get_vault_path(), key_provider=lambda: key))


@pytest.fixture
def bind_decoder():
    """Explicitly bind synthetic restoration fixtures to their finalized bytes (legacy sidecar mode)."""
    def bind(document, mapping):
        from decoder_binding import publish_binding
        decoder = document.with_name(document.stem + "_дешифратор.json")
        app_paths.atomic_write_json(decoder, mapping)
        publish_binding(decoder, [document], "synthetic-test-run", emit_sidecars=True)
        return decoder
    return bind


@pytest.fixture
def bind_decoder_clean():
    """Explicitly bind synthetic restoration fixtures to their finalized bytes (clean embedded mode)."""
    def bind(document, mapping):
        from decoder_binding import publish_binding
        decoder = document.with_name(document.stem + "_дешифратор.json")
        app_paths.atomic_write_json(decoder, mapping)
        publish_binding(decoder, [document], "clean-test-run", emit_sidecars=False)
        return decoder
    return bind



@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    """Изолирует все пользовательские пути DOCXдодыр и хранилище секретов для каждого теста."""
    data_dir = tmp_path / "user_data"
    config_dir = tmp_path / "user_config"
    cache_dir = tmp_path / "user_cache"
    log_dir = tmp_path / "user_logs"

    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOCXDODYR_USER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOCXDODYR_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DOCXDODYR_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("DOCXDODYR_LOG_DIR", str(log_dir))

    yield {
        "data_dir": data_dir,
        "config_dir": config_dir,
        "cache_dir": cache_dir,
        "log_dir": log_dir,
    }


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch, request):
    """Блокирует внешние сетевые вызовы в тестах для гарантии Local-First и отсутствия утечек PII."""
    if "allow_network" in request.keywords:
        return

    import socket
    orig_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else ""
        # Разрешаем только локальные сокеты (для локальных тестов/мок-серверов)
        if host in ("127.0.0.1", "localhost", "::1"):
            return orig_connect(self, address)
        raise RuntimeError(f"Внешние сетевые вызовы в тестах строго запрещены (попытка обращения к {address}).")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
