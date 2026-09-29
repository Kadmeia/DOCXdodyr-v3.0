# -*- coding: utf-8 -*-
"""Модуль интеграции DOCXдодыр в контекстное меню Проводника Windows (Explorer).

Позволяет пользователю кликнуть правой кнопкой мыши (ПКМ) на любом файле
или папке и выбрать:
  1. «Обезличить DOCXdodyr» — быстрое обезличивание с настройками по умолчанию;
  2. «Восстановить DOCXdodyr» — восстановление документа по дешифратору (с автопоиском в папке/родителях или выбором .json).

Регистрация производится в ветке HKCU (HKEY_CURRENT_USER), что не требует
прав администратора и работает сразу в пользовательском окружении.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Ключи контекстного меню для обезличивания
REG_FOLDER_ANON_KEY = r"Software\Classes\Directory\shell\DOCXdodyr_anonymize"
REG_FOLDER_BG_ANON_KEY = r"Software\Classes\Directory\Background\shell\DOCXdodyr_anonymize"
REG_FILE_ANON_KEY = r"Software\Classes\*\shell\DOCXdodyr_anonymize"

# Ключи контекстного меню для восстановления
REG_FOLDER_RESTORE_KEY = r"Software\Classes\Directory\shell\DOCXdodyr_restore"
REG_FOLDER_BG_RESTORE_KEY = r"Software\Classes\Directory\Background\shell\DOCXdodyr_restore"
REG_FILE_RESTORE_KEY = r"Software\Classes\*\shell\DOCXdodyr_restore"

# Устаревшие ключи для очистки
LEGACY_KEYS = [
    r"Software\Classes\Directory\shell\DOCXdodyr",
    r"Software\Classes\Directory\Background\shell\DOCXdodyr",
    r"Software\Classes\*\shell\DOCXdodyr",
]

MENU_TEXT_ANONYMIZE = "Обезличить DOCXdodyr"
MENU_TEXT_ANONYMIZE_BG = "Обезличить эту папку DOCXdodyr"
MENU_TEXT_RESTORE = "Восстановить DOCXdodyr"
MENU_TEXT_RESTORE_BG = "Восстановить эту папку DOCXdodyr"


def _is_windows() -> bool:
    return sys.platform == "win32"


def get_default_executable_command() -> str:
    """Возвращает команду запуска DOCXдодыр для реестра Windows."""
    repo_root = Path(__file__).resolve().parent
    compiled_exe = repo_root / "dist" / "DOCXdodyr" / "DOCXdodyr.exe"
    local_app_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DOCXdodyr" / "DOCXdodyr.exe"

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        return f'"{exe_path}"'

    if local_app_exe.exists():
        return f'"{local_app_exe.resolve()}"'

    if compiled_exe.exists():
        return f'"{compiled_exe.resolve()}"'

    # При запуске из исходного кода используем pythonw.exe (без мигания консоли), если он есть
    python_exe = Path(sys.executable).resolve()
    pythonw = python_exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else python_exe
    main_py = (repo_root / "main.py").resolve()
    return f'"{runner}" "{main_py}"'


def get_default_icon_path() -> str:
    """Возвращает путь к иконке для контекстного меню Windows."""
    repo_root = Path(__file__).resolve().parent
    compiled_exe = repo_root / "dist" / "DOCXdodyr" / "DOCXdodyr.exe"
    local_app_exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DOCXdodyr" / "DOCXdodyr.exe"
    ico_file = repo_root / "assets" / "DOCXdodyr.ico"

    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}",0'

    if local_app_exe.exists():
        return f'"{local_app_exe.resolve()}",0'

    if compiled_exe.exists():
        return f'"{compiled_exe.resolve()}",0'

    if ico_file.exists():
        return f'"{ico_file.resolve()}"'

    return f'"{Path(sys.executable).resolve()}",0'


def register_windows_context_menu(
    command_prefix: Optional[str] = None,
    icon_path: Optional[str] = None,
) -> Dict[str, object]:
    """Регистрирует пункты контекстного меню в HKCU для папок и файлов.

    Создаёт две кнопки:
      - «Обезличить DOCXdodyr» (--anonymize)
      - «Восстановить DOCXdodyr» (--restore)
    """
    if not _is_windows():
        logger.info("Контекстное меню Windows поддерживается только на платформе win32")
        return {"status": "skipped", "platform": sys.platform, "message": "win32 only"}

    import winreg

    # Сначала удаляем старые одиночные ключи при обновлении
    for legacy_key in LEGACY_KEYS:
        _delete_reg_key_recursive(winreg.HKEY_CURRENT_USER, legacy_key)

    cmd = command_prefix or get_default_executable_command()
    icon = icon_path or get_default_icon_path()

    entries = [
        # --- 1. Обезличивание ---
        (
            REG_FOLDER_ANON_KEY,
            MENU_TEXT_ANONYMIZE,
            f'{cmd} --anonymize --headless --no-open-output "%1"',
            icon,
        ),
        (
            REG_FOLDER_BG_ANON_KEY,
            MENU_TEXT_ANONYMIZE_BG,
            f'{cmd} --anonymize --headless --no-open-output "%V"',
            icon,
        ),
        (
            REG_FILE_ANON_KEY,
            MENU_TEXT_ANONYMIZE,
            f'{cmd} --anonymize --headless --no-open-output "%1"',
            icon,
        ),
        # --- 2. Восстановление ---
        (
            REG_FOLDER_RESTORE_KEY,
            MENU_TEXT_RESTORE,
            f'{cmd} --restore --headless --no-open-output "%1"',
            icon,
        ),
        (
            REG_FOLDER_BG_RESTORE_KEY,
            MENU_TEXT_RESTORE_BG,
            f'{cmd} --restore --headless --no-open-output "%V"',
            icon,
        ),
        (
            REG_FILE_RESTORE_KEY,
            MENU_TEXT_RESTORE,
            f'{cmd} --restore --headless --no-open-output "%1"',
            icon,
        ),
    ]

    registered_keys = []
    try:
        for key_path, label, full_cmd, icon_val in entries:
            # 1. Основной ключ меню
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
                if icon_val:
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_val)

            # 2. Подключ command
            cmd_key_path = f"{key_path}\\command"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_key_path) as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, full_cmd)

            registered_keys.append(key_path)

        logger.info("Контекстное меню Windows успешно зарегистрировано: %s", registered_keys)
        return {
            "status": "ok",
            "registered": registered_keys,
            "command": cmd,
            "icon": icon,
        }
    except Exception as exc:
        logger.exception("Ошибка при записи в реестр Windows: %s", exc)
        return {"status": "error", "message": str(exc), "registered": registered_keys}


def _delete_reg_key_recursive(root_key, subkey_path: str) -> bool:
    """Рекурсивно удаляет подключ реестра Windows."""
    import winreg

    try:
        with winreg.OpenKey(root_key, subkey_path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child_name = winreg.EnumKey(key, 0)
                    _delete_reg_key_recursive(root_key, f"{subkey_path}\\{child_name}")
                except OSError:
                    break
        winreg.DeleteKey(root_key, subkey_path)
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.debug("Ошибка удаления ключа реестра %s: %s", subkey_path, exc)
        return False


def unregister_windows_context_menu() -> Dict[str, object]:
    """Удаляет все записи контекстного меню DOCXдодыр из HKCU."""
    if not _is_windows():
        return {"status": "skipped", "platform": sys.platform, "message": "win32 only"}

    import winreg

    targets = [
        REG_FOLDER_ANON_KEY,
        REG_FOLDER_BG_ANON_KEY,
        REG_FILE_ANON_KEY,
        REG_FOLDER_RESTORE_KEY,
        REG_FOLDER_BG_RESTORE_KEY,
        REG_FILE_RESTORE_KEY,
    ] + LEGACY_KEYS

    removed = []

    for key_path in targets:
        if _delete_reg_key_recursive(winreg.HKEY_CURRENT_USER, key_path):
            removed.append(key_path)

    logger.info("Контекстное меню Windows удалено: %s", removed)
    return {"status": "ok", "removed": removed}


def is_windows_context_menu_registered() -> bool:
    """Проверяет, зарегистрировано ли контекстное меню DOCXдодыр в текущей системе."""
    if not _is_windows():
        return False

    import winreg

    required_commands = (
        REG_FOLDER_ANON_KEY,
        REG_FOLDER_BG_ANON_KEY,
        REG_FILE_ANON_KEY,
        REG_FOLDER_RESTORE_KEY,
        REG_FOLDER_BG_RESTORE_KEY,
        REG_FILE_RESTORE_KEY,
    )
    try:
        for key_path in required_commands:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command", 0, winreg.KEY_READ) as command_key:
                command, _ = winreg.QueryValueEx(command_key, "")
                if not isinstance(command, str) or "--headless" not in command or "--no-open-output" not in command:
                    return False
        return True
    except (FileNotFoundError, OSError):
        return False
    except Exception:
        return False


if __name__ == "__main__":
    import json

    args = sys.argv[1:]
    if "--unregister" in args or "-u" in args:
        res = unregister_windows_context_menu()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif "--status" in args or "-s" in args:
        print(json.dumps({"registered": is_windows_context_menu_registered()}, indent=2))
    else:
        res = register_windows_context_menu()
        print(json.dumps(res, ensure_ascii=False, indent=2))
