# -*- coding: utf-8 -*-
"""Единый источник версии и системных метаданных приложения DOCXдодыр."""

from __future__ import annotations

import sys
from typing import Any, Dict, Tuple

__version__: str = "3.0.2"
VERSION_TUPLE: Tuple[int, int, int] = (3, 0, 2)

APP_NAME: str = "DOCXdodyr"
APP_DISPLAY_NAME: str = "DOCXдодыр"
APP_TITLE: str = "DOCXдодыр v3.0.2"
APP_WINDOW_TITLE: str = APP_TITLE
APP_VERSION: str = __version__

# Идентификаторы приложения для платформ
APP_BUNDLE_ID: str = "ru.docxdodyr.desktop"           # macOS CFBundleIdentifier
APP_ID_WINDOWS: str = "DOCXdodyr.Desktop.3.0"         # Windows AppUserModelId / Inno Setup AppId

APP_AUTHOR: str = "DOCXdodyr Contributors"
APP_COPYRIGHT: str = "© 2024–2026 DOCXдодыр. Все права защищены."
APP_DESCRIPTION: str = (
    "Локальное десктопное приложение для интеллектуального обезличивания, "
    "маскирования персональных данных (ПДн), очистки метаданных и правового анализа документов."
)

# Матрица поддерживаемых версий Python
SUPPORTED_PYTHON_MIN: Tuple[int, int] = (3, 9)
SUPPORTED_PYTHON_RECOMMENDED: Tuple[int, int] = (3, 11)
SUPPORTED_PYTHON_MAX: Tuple[int, int] = (3, 12)  # < 3.12 для сборки релизов


def get_version_info() -> Dict[str, Any]:
    """Возвращает метаданные о версии и среде приложения."""
    return {
        "app_name": APP_NAME,
        "display_name": APP_DISPLAY_NAME,
        "version": APP_VERSION,
        "title": APP_TITLE,
        "bundle_id": APP_BUNDLE_ID,
        "app_id_windows": APP_ID_WINDOWS,
        "author": APP_AUTHOR,
        "copyright": APP_COPYRIGHT,
        "description": APP_DESCRIPTION,
        "platform": sys.platform,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "supported_python_min": f"{SUPPORTED_PYTHON_MIN[0]}.{SUPPORTED_PYTHON_MIN[1]}",
        "supported_python_recommended": f"{SUPPORTED_PYTHON_RECOMMENDED[0]}.{SUPPORTED_PYTHON_RECOMMENDED[1]}",
    }
