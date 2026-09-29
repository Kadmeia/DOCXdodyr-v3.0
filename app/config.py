# -*- coding: utf-8 -*-
"""Конфигурация путей и опциональных локальных библиотек."""

import importlib.util

import app_paths

def get_base_path():
    """Возвращает базовый путь для доступа к ресурсам."""
    return str(app_paths.get_bundle_dir())

BASE_PATH = get_base_path()
USER_DATA_PATH = str(app_paths.get_user_data_dir())
USER_CONFIG_PATH = str(app_paths.get_user_config_dir())

EXCLUSIONS_FILENAME = str(app_paths.get_exclusions_path())
REPLACEMENTS_FILENAME = str(app_paths.get_replacements_path())
# Опциональные библиотеки
import logging
_cfg_logger = logging.getLogger("config")

try:
    import pymorphy3 as pm
    morph3 = pm.MorphAnalyzer()
    PYMORPHY3_AVAILABLE = True
except ImportError:
    PYMORPHY3_AVAILABLE = False
    morph3 = None
    _cfg_logger.info("Библиотека pymorphy3 не найдена. Морфологическая нормализация не будет использоваться.")

PULLENTI_AVAILABLE = importlib.util.find_spec("pullenti") is not None
if not PULLENTI_AVAILABLE:
    _cfg_logger.info("Библиотека Pullenti (PullentiPython) не найдена. Обезличивание на основе NER будет отключено.")

try:
    from ocr_backend import pdf_ocr_dependencies_available
    OCR_PDF_AVAILABLE = pdf_ocr_dependencies_available()
except Exception:
    OCR_PDF_AVAILABLE = False
    _cfg_logger.info("OCR для PDF недоступен. Установите PyMuPDF и один из поддерживаемых OCR backend-ов.")

PYPDF_AVAILABLE = any(
    importlib.util.find_spec(module_name) is not None
    for module_name in ("pypdf", "PyPDF2")
)
if not PYPDF_AVAILABLE:
    _cfg_logger.info("Для извлечения текста из PDF установите зависимости из requirements.txt.")
