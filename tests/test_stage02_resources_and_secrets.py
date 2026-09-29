# -*- coding: utf-8 -*-
"""Тесты для этапа 02: Ресурсы, пользовательские данные и секреты."""

import json
import os
from pathlib import Path
import stat
import pytest

import app_paths
import state_migration
from backend_api import BackendApi
from privacy_audit import ReviewFinding, ReviewQueue


def test_app_paths_resolution(tmp_path, monkeypatch):
    """Проверяет корректное разрешение путей bundle, data, config, cache, logs."""
    data_dir = tmp_path / "custom_data"
    config_dir = tmp_path / "custom_config"
    cache_dir = tmp_path / "custom_cache"
    log_dir = tmp_path / "custom_log"

    monkeypatch.setenv("DOCXDODYR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOCXDODYR_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DOCXDODYR_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("DOCXDODYR_LOG_DIR", str(log_dir))

    assert app_paths.get_user_data_dir() == data_dir
    assert app_paths.get_user_config_dir() == config_dir
    assert app_paths.get_user_cache_dir() == cache_dir
    assert app_paths.get_user_log_dir() == log_dir
    assert app_paths.get_user_models_dir() == data_dir / "models"

    assert app_paths.get_settings_path() == config_dir / "settings.json"
    assert app_paths.get_review_queue_path() == data_dir / "review_queue.json"
    assert app_paths.get_review_context_path() == data_dir / ".review_context.enc"
    assert app_paths.get_exclusions_path() == data_dir / "Исключения.txt"
    assert app_paths.get_replacements_path() == data_dir / "Замены.txt"
    assert app_paths.get_app_log_path() == log_dir / "docxdodyr.log"

    # Bundle dir указывает на существующий каталог с web/
    bundle_dir = app_paths.get_bundle_dir()
    assert bundle_dir.exists()
    assert (bundle_dir / "web" / "index.html").exists()


def test_atomic_write_safety_and_permissions(tmp_path):
    """Проверяет атомарную запись и строгие права доступа (0o600)."""
    target = tmp_path / "secure_file.json"
    data = {"secret_level": "high", "val": 42}
    written = app_paths.atomic_write_json(target, data, mode=0o600)

    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8")) == data

    # На POSIX проверяем права 0o600
    if os.name == "posix":
        file_mode = stat.S_IMODE(target.stat().st_mode)
        assert file_mode == 0o600


def test_review_queue_and_vault_in_user_data_dir():
    """Проверяет, что очередь ревью и контекст хранятся в user_data_dir и пишутся атомарно."""
    api = BackendApi()
    assert api._review_queue_path.parent == app_paths.get_user_data_dir()
    assert api._review_context_path.parent == app_paths.get_user_data_dir()

    finding = ReviewFinding("test.docx", "body.0", "FIO", "[ФИО_1]", 0.95)
    api.review_queue.add(finding)
    api._persist_review_state()

    assert api._review_queue_path.exists()
    payload = json.loads(api._review_queue_path.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 1
    assert payload["items"][0]["finding_id"] == finding.finding_id


def test_state_migration_from_legacy_files(tmp_path):
    legacy_dir = tmp_path / "legacy_app"
    legacy_dir.mkdir()
    (legacy_dir / "settings.json").write_text(
        json.dumps({"bracket_type": "curly", "custom_option": 123}), encoding="utf-8"
    )
    (legacy_dir / "Исключения.txt").write_text("СловоИсключение1\n", encoding="utf-8")

    migration_res = state_migration.run_state_migration(base_dir=legacy_dir)

    assert migration_res["settings_migrated"] is True
    assert migration_res["exclusions_migrated"] is True
    migrated_settings = json.loads(app_paths.get_settings_path().read_text(encoding="utf-8"))
    assert migrated_settings["bracket_type"] == "curly"
    assert "СловоИсключение1" in app_paths.get_exclusions_path().read_text(encoding="utf-8")


def test_app_works_from_arbitrary_cwd_and_read_only_bundle(tmp_path, monkeypatch):
    """Проверяет работоспособность приложения при произвольном cwd."""
    from main import ApiWrapper

    arbitrary_cwd = tmp_path / "arbitrary_empty_cwd"
    arbitrary_cwd.mkdir()

    monkeypatch.chdir(arbitrary_cwd)

    wrapper = ApiWrapper()
    wrapper.update_settings({"save_original": True, "bracket_type": "square"})

    # settings.json записан в user_config_dir, а не в произвольный cwd
    assert not (arbitrary_cwd / "settings.json").exists()
    assert (app_paths.get_settings_path()).exists()


def test_open_folder_in_file_manager_safety(tmp_path, monkeypatch):
    """Проверяет безопасную работу open_folder_in_file_manager."""
    test_dir = tmp_path / "output_folder"
    test_dir.mkdir()
    test_file = test_dir / "sample.docx"
    test_file.write_text("dummy")

    # 1. При запуске тестов (PYTEST_CURRENT_TEST) возвращает True без запуска подпроцессов
    assert app_paths.open_folder_in_file_manager(test_dir) is True
    assert app_paths.open_folder_in_file_manager(test_file) is True

    # 2. Несуществующий путь возвращает False
    assert app_paths.open_folder_in_file_manager(tmp_path / "non_existent_folder") is False

    # 3. При эмуляции боевого режима проверяется обращение к системной утилите (open / startfile / xdg-open)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("DOCXDODYR_HEADLESS", raising=False)
    
    called_cmds = []
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: called_cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda p: called_cmds.append(["startfile", str(p)]))

    res = app_paths.open_folder_in_file_manager(test_dir)
    assert res is True
    open_cmds = [cmd for cmd in called_cmds if cmd[0] in ("open", "xdg-open", "startfile")]
    assert len(open_cmds) == 1
    assert str(test_dir) in open_cmds[0]

    # 4. Если папка уже открыта — повторное открытие пропускается
    called_cmds.clear()
    monkeypatch.setattr(app_paths, "is_folder_open_in_file_manager", lambda p: True)
    res_already_open = app_paths.open_folder_in_file_manager(test_dir)
    assert res_already_open is True
    open_cmds_already = [cmd for cmd in called_cmds if cmd[0] in ("open", "xdg-open", "startfile")]
    assert len(open_cmds_already) == 0, "Не должно вызываться открытие, если окно уже открыто"

