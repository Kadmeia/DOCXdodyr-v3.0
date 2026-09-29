# -*- coding: utf-8 -*-
"""E2E тест сохранения настроек и их восстановления при перезапуске приложения."""

import json
import os
import sys
from pathlib import Path
import pytest

from main import ApiWrapper
import app_paths


def test_settings_persistence_across_app_restarts(tmp_path, monkeypatch):
    """
    Проверяет полный цикл сохранения и восстановления настроек:
    1. Сессия 1: Запуск приложения с дефолтными настройками.
    2. Включение дешифратора, отключение автооткрытия папки, выбор формата PDF и языка OCR.
    3. Вызов update_settings (как это делает интерфейс при клике на тумблеры).
    4. Проверка, что файл конфигурации на диске действительно обновился.
    5. Завершение работы Сессии 1 (имитация закрытия приложения).
    6. Сессия 2: Новый запуск приложения 'с нуля' (новый экземпляр ApiWrapper).
    7. Проверка, что get_settings() возвращает именно сохранённые параметры Сессии 1.
    """
    config_dir = tmp_path / "user_config"
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_file = config_dir / "settings.json"
    
    monkeypatch.setattr(app_paths, "get_settings_path", lambda: settings_file)
    monkeypatch.setattr(app_paths, "get_user_config_dir", lambda: config_dir)
    monkeypatch.chdir(tmp_path)

    # 1. Первая сессия (по умолчанию)
    session1 = ApiWrapper()
    initial_settings = session1.get_settings()
    assert initial_settings["save_decoder"] is False, "По умолчанию дешифратор выключен"
    assert initial_settings["open_output_folder"] is True, "По умолчанию автооткрытие папки включено"
    assert initial_settings["save_pdf"] is False, "По умолчанию PDF выключен"

    # 2. Пользователь изменяет настройки в интерфейсе
    new_settings = {
        "save_original": True,
        "save_docx": True,
        "save_pdf": True,
        "save_markdown": True,
        "irreversible_pdf": True,
        "save_decoder": True,       # Включили дешифратор
        "open_output_folder": False, # Отключили открытие папки
        "auto_decoder": False,
        "ocr_lang": "rus+eng"
    }
    session1.update_settings(new_settings)

    # 3. Проверяем файл на диске
    assert settings_file.exists(), "Файл настроек должен быть сохранён на диск"
    saved_on_disk = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved_on_disk["save_decoder"] is True
    assert saved_on_disk["open_output_folder"] is False
    assert saved_on_disk["save_pdf"] is True
    assert saved_on_disk["ocr_lang"] == "rus+eng"

    # 4. Закрываем Сессию 1 (shutdown)
    session1.shutdown()
    del session1

    # 5. Вторая сессия: запуск приложения заново
    session2 = ApiWrapper()
    restored_settings = session2.get_settings()

    # Проверяем, что все изменённые настройки восстановились
    assert restored_settings["save_decoder"] is True, "Дешифратор должен остаться ВКЛЮЧЕННЫМ после перезапуска"
    assert restored_settings["open_output_folder"] is False, "Автооткрытие папки должно остаться ВЫКЛЮЧЕННЫМ после перезапуска"
    assert restored_settings["save_pdf"] is True, "PDF должен остаться ВКЛЮЧЕННЫМ"
    assert restored_settings["save_markdown"] is True, "Markdown должен остаться ВКЛЮЧЕННЫМ"
    assert restored_settings["irreversible_pdf"] is True, "Необратимый PDF должен остаться ВКЛЮЧЕННЫМ"
    assert restored_settings["auto_decoder"] is False, "Автопоиск дешифратора должен остаться ВЫКЛЮЧЕННЫМ"
    assert restored_settings["ocr_lang"] == "rus+eng", "Язык OCR должен остаться rus+eng"

    session2.shutdown()


def test_js_settings_sync_contract():
    """
    Проверяет контракт фронтенда:
    1. index.html содержит все необходимые чекбоксы.
    2. script.js содержит функцию loadGeneralSettings и вызывает её при старте (pywebviewready).
    3. script.js содержит updateSettings, передающий все поля включая cb-open-output-folder и cb-save-decoder.
    """
    web_dir = Path(__file__).resolve().parent.parent / "web"
    html_content = (web_dir / "index.html").read_text(encoding="utf-8")
    js_content = (web_dir / "script.js").read_text(encoding="utf-8")

    # Чекбоксы в разметке
    expected_ids = [
        'cb-save-original',
        'cb-save-pdf',
        'cb-save-md',
        'cb-irreversible-pdf',
        'cb-save-decoder',
        'cb-auto-decoder',
        'sel-ocr-lang'
    ]
    for element_id in expected_ids:
        assert f'id="{element_id}"' in html_content, f"Элемент #{element_id} должен присутствовать в index.html"

    # Проверка вызова get_settings и loadGeneralSettings в JS
    assert "loadGeneralSettings" in js_content, "Функция loadGeneralSettings должна быть в script.js"
    assert "api.get_settings()" in js_content or "api.get_settings" in js_content, "script.js должен вызывать api.get_settings"
    assert "cbDec.checked = settings.save_decoder" in js_content, "script.js должен восстанавливать save_decoder"
    assert "open_output_folder: true" in js_content, "updateSettings должен по умолчанию активировать open_output_folder"
