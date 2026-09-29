# -*- coding: utf-8 -*-
"""Честная матрица возможностей платформы и компонентов DOCXдодыр.

Предоставляет прозрачную сводку доступности форматов (DOCX, XLSX, Text PDF, Scan PDF/OCR),
конвертации и локальных моделей.
"""

from __future__ import annotations

import sys
from typing import Any, Dict

import ocr_backend
import pdf_convert
import version


def get_system_capability_matrix(backend_instance: Any = None) -> Dict[str, Any]:
    """Возвращает структурированную матрицу возможностей системы."""
    # 1. OCR возможности
    ocr_backends = ocr_backend.available_backend_names()
    ocr_available = ocr_backend.pdf_ocr_dependencies_available()
    primary_ocr = ocr_backends[0] if ocr_backends else None

    if sys.platform == "darwin":
        ocr_desc = "Apple Vision Framework (нативно, русский/английский, оффлайн)"
    elif sys.platform == "win32":
        ocr_desc = "Tesseract OCR (локальная установка, русский/английский, оффлайн)"
    else:
        ocr_desc = "Tesseract OCR (оффлайн)"

    # 2. PDF конвертация
    pdf_conv = pdf_convert.get_pdf_conversion_status()

    # 3. Локальная модель Qwen
    try:
        import qwen_offline
        qwen_info = qwen_offline.get_installed_model_info()
        qwen_installed = bool(qwen_info.get("installed", False))
        qwen_size = qwen_info.get("total_size_formatted", "0 МБ")
    except Exception:
        qwen_installed = False
        qwen_size = "0 МБ"

    matrix = {
        "version": getattr(version, "__version__", "3.0.0"),
        "platform": sys.platform,
        "capabilities": {
            "docx": {
                "name": "Документы Word (.docx, .docm)",
                "status": "full",
                "engine": "python-docx",
                "offline": True,
                "notes": "Полное локальное чтение, разметка сущностей, очистка метаданных и сохранение.",
            },
            "xlsx": {
                "name": "Таблицы Excel (.xlsx, .xlsm)",
                "status": "full",
                "engine": "openpyxl",
                "offline": True,
                "notes": "Полное локальное чтение всех листов, формул и значений, маскирование и сохранение.",
            },
            "text_pdf": {
                "name": "Текстовые PDF-документы",
                "status": "full",
                "engine": "PyMuPDF (fitz) + pypdf",
                "offline": True,
                "notes": "Извлечение текстового слоя, удаление метаданных, аннотаций и скрытых объектов.",
            },
            "scan_pdf_ocr": {
                "name": "Сканированные PDF / Изображения (OCR)",
                "status": "available" if ocr_available else "limited",
                "engine": primary_ocr or "none",
                "offline": True,
                "description": ocr_desc,
                "notes": (
                    "Распознавание текста со сканов работает полностью локально без отправки изображений в сеть."
                    if ocr_available
                    else "Для распознавания сканов требуется локальный OCR-бэкенд."
                ),
            },
            "pdf_conversion": {
                "name": "Прямой экспорт DOCX/XLSX в PDF",
                "status": "available" if pdf_conv.get("conversion_available") else "limited",
                "engine": pdf_conv.get("engine") or "none",
                "offline": True,
                "requires_office": not pdf_conv.get("libreoffice_available", False),
                "notes": pdf_conv.get("message", ""),
            },
            "irreversible_pdf": {
                "name": "Необратимый графический PDF",
                "status": "full",
                "engine": "PyMuPDF (растеризация в растровые изображения)",
                "offline": True,
                "notes": "Удаляет текстовый слой и векторные объекты, предотвращая восстановление текста.",
            },
            "qwen_offline": {
                "name": "Локальная нейросеть Qwen (согласование падежей)",
                "status": "installed" if qwen_installed else "not_installed",
                "installed": qwen_installed,
                "size": qwen_size,
                "offline": True,
                "notes": (
                    "Установлена в каталог данных пользователя. Работает полностью локально."
                    if qwen_installed
                    else "Опциональный компонент (~1.75 ГБ). Базовое ядро работает без модели."
                ),
            },
        },
    }
    return matrix


__all__ = ["get_system_capability_matrix"]
