# -*- coding: utf-8 -*-
"""Управление путями приложения DOCXдодыр: разделение read-only ресурсов и writable state.

Архитектурный принцип:
- Read-only ресурсы (веб-интерфейс, шаблоны по умолчанию) берутся из каталога поставки/пакета (bundle).
- Writable state (настройки, очереди, зашифрованный контекст, списки, кэш, логи, модели)
  хранятся строго в системных пользовательских каталогах согласно спецификации ОС (platformdirs).
- Поддерживается переопределение через переменные окружения для тестирования и изолированных контуров:
  DOCXDODYR_DATA_DIR, DOCXDODYR_CONFIG_DIR, DOCXDODYR_CACHE_DIR, DOCXDODYR_LOG_DIR.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

try:
    import platformdirs
except ImportError:
    platformdirs = None

APP_NAME = "DOCXdodyr"


def get_bundle_dir() -> Path:
    """Возвращает базовый read-only каталог поставки приложения."""
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS).resolve()
        return Path(sys.executable).parent.resolve()
    return Path(__file__).resolve().parents[1]


def get_web_dir() -> Path:
    """Возвращает путь к read-only ресурсам веб-интерфейса."""
    bundle = get_bundle_dir()
    web_dir = bundle / "web"
    if web_dir.exists():
        return web_dir
    # Fallback для стандартного размещения ресурсов внутри macOS бандла
    if getattr(sys, "frozen", False):
        try:
            resources_web = Path(sys.executable).parent.parent / "Resources" / "web"
            if resources_web.exists():
                return resources_web
        except Exception:
            pass
    return web_dir


def get_user_data_dir(*, create: bool = True) -> Path:
    """Возвращает пользовательский каталог данных (state, review queue, vault, lists)."""
    override = os.environ.get("DOCXDODYR_DATA_DIR") or os.environ.get("DOCXDODYR_USER_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif platformdirs is not None:
        path = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)).resolve()
    else:
        # Fallback без platformdirs
        if sys.platform == "darwin":
            path = Path.home() / "Library" / "Application Support" / APP_NAME
        elif sys.platform == "win32":
            appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            path = Path(appdata) / APP_NAME
        else:
            path = Path.home() / ".local" / "share" / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_config_dir() -> Path:
    """Возвращает пользовательский каталог конфигураций (settings.json)."""
    override = os.environ.get("DOCXDODYR_CONFIG_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif platformdirs is not None:
        path = Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)).resolve()
    else:
        if sys.platform == "darwin":
            path = Path.home() / "Library" / "Application Support" / APP_NAME
        elif sys.platform == "win32":
            appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            path = Path(appdata) / APP_NAME
        else:
            path = Path.home() / ".config" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_cache_dir() -> Path:
    """Возвращает каталог кэша приложения (OCR временные файлы, превью)."""
    override = os.environ.get("DOCXDODYR_CACHE_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif platformdirs is not None:
        path = Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)).resolve()
    else:
        if sys.platform == "darwin":
            path = Path.home() / "Library" / "Caches" / APP_NAME
        elif sys.platform == "win32":
            appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            path = Path(appdata) / APP_NAME / "Cache"
        else:
            path = Path.home() / ".cache" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_log_dir() -> Path:
    """Возвращает каталог системных логов приложения."""
    override = os.environ.get("DOCXDODYR_LOG_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif platformdirs is not None:
        path = Path(platformdirs.user_log_dir(APP_NAME, appauthor=False)).resolve()
    else:
        if sys.platform == "darwin":
            path = Path.home() / "Library" / "Logs" / APP_NAME
        elif sys.platform == "win32":
            appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            path = Path(appdata) / APP_NAME / "Logs"
        else:
            path = Path.home() / ".local" / "state" / APP_NAME / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_models_dir(*, create: bool = True) -> Path:
    """Возвращает каталог для локальных нейросетевых моделей (Qwen)."""
    path = get_user_data_dir(create=create) / "models"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


# Конкретные пути к файлам состояния
def get_settings_path() -> Path:
    return get_user_config_dir() / "settings.json"


def get_review_queue_path() -> Path:
    return get_user_data_dir() / "review_queue.json"


def get_review_context_path() -> Path:
    return get_user_data_dir() / ".review_context.enc"


def get_exclusions_path() -> Path:
    return get_user_data_dir() / "Исключения.txt"


def get_replacements_path() -> Path:
    return get_user_data_dir() / "Замены.txt"


def get_app_log_path() -> Path:
    return get_user_log_dir() / "docxdodyr.log"


# Legacy-пути для проверки миграции
def get_legacy_settings_path() -> Path:
    return get_bundle_dir() / "settings.json"


# Атомарная запись файлов
def atomic_write_text(path: os.PathLike[str] | str, text: str, *, encoding: str = "utf-8", mode: int = 0o600) -> Path:
    """Атомарно записывает текстовый файл с защитой от сбоев и контролем прав доступа."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, mode)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, target)
        try:
            os.chmod(target, mode)
        except OSError:
            pass
        return target
    finally:
        tmp_path.unlink(missing_ok=True)


def atomic_write_json(path: os.PathLike[str] | str, data: Any, *, indent: int = 4, mode: int = 0o600) -> Path:
    """Атомарно сериализует данные в JSON с защитой от сбоев."""
    text = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
    return atomic_write_text(path, text, encoding="utf-8", mode=mode)


def is_folder_open_in_file_manager(folder_path: str | os.PathLike[str]) -> bool:
    """Проверяет, открыта ли уже данная папка в системном проводнике (Finder / Explorer)."""
    if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("DOCXDODYR_HEADLESS") == "1":
        return False
    try:
        import unicodedata
        target = Path(folder_path).expanduser().resolve()
        if target.is_file():
            target = target.parent
        if not target.exists():
            return False

        if sys.platform == "darwin":
            cmd = [
                "osascript", "-l", "JavaScript", "-e",
                'JSON.stringify(Application("Finder").windows().map(w => { '
                'try { return decodeURI(w.target().url()).replace(/^file:\\/\\//, "").replace(/\\/$/, ""); } '
                'catch(e) { return null; } '
                '}).filter(Boolean))'
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
            if res.returncode == 0 and res.stdout.strip():
                open_paths = json.loads(res.stdout.strip())
                norm_target = unicodedata.normalize("NFC", str(target).rstrip("/"))
                for p in open_paths:
                    try:
                        resolved_p = Path(p).resolve()
                        if unicodedata.normalize("NFC", str(resolved_p).rstrip("/")) == norm_target:
                            return True
                    except Exception:
                        continue
        elif sys.platform == "win32":
            cmd = [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "@((New-Object -ComObject Shell.Application).Windows()) | "
                "ForEach-Object { try { $_.Document.Folder.Self.Path } catch {} }"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)
            if res.returncode == 0 and res.stdout.strip():
                target_str = str(target).rstrip("\\").lower()
                for line in res.stdout.splitlines():
                    p = line.strip().rstrip("\\").lower()
                    if p and p == target_str:
                        return True
    except Exception as exc:
        logger.debug("Проверка открытых окон файлового менеджера пропущена: %s", exc)
    return False


def open_folder_in_file_manager(folder_path: str | os.PathLike[str], check_if_already_open: bool = True) -> bool:
    """Открывает указанную папку в системном файловом менеджере (Finder / Explorer / xdg-open).

    Если папка уже открыта в файловом менеджере, повторное открытие пропускается,
    предотвращая появление дублирующих окон.
    Автоматически подавляет реальное открытие окон в headless-режиме и во время тестов pytest,
    предотвращая появление всплывающих окон в CI и у разработчика.
    """
    try:
        target = Path(folder_path).expanduser().resolve()
        if target.is_file():
            target = target.parent
        if not target.exists():
            return False
        if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("DOCXDODYR_HEADLESS") == "1":
            return True

        if check_if_already_open and is_folder_open_in_file_manager(target):
            logger.info("Папка %s уже открыта в файловом менеджере, повторное открытие пропущено", target)
            return True

        if sys.platform == "darwin":
            subprocess.run(["open", str(target)], check=False)
        elif sys.platform == "win32":
            os.startfile(str(target))
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
        return True
    except Exception as exc:
        logger.warning("Не удалось открыть папку %s в файловом менеджере: %s", folder_path, exc)
        return False
