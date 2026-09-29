# -*- coding: utf-8 -*-
"""Модуль санитизации и маскирования чувствительных данных в логах.

Защищает системные логи и консольный вывод от непреднамеренной утечки персональных данных
(ФИО, паспорта, СНИЛС, ИНН, банковские счета, телефоны, email) и секретов (API-ключи, токены).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

# Регулярные выражения для обнаружения секретов и персональных данных
REDACTION_PATTERNS = [
    # API-ключи Google (AIzaSy...)
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,50}\b"), "[REDACTED_API_KEY]"),
    # Bearer токены
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.]+\b"), "Bearer [REDACTED_TOKEN]"),
    # Переменные с паролями и токенами: key=..., password=...
    (
        re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*['\"]?([^\s'\"]+)['\"]?"),
        r"\1=[REDACTED]",
    ),
    # Паспорта РФ: 4 цифры (серия) + 6 цифр (номер)
    (re.compile(r"\b\d{2}\s*\d{2}\s+\d{6}\b"), "[REDACTED_PASSPORT]"),
    # СНИЛС: 11 цифр (123-456-789 01 или 123 456 789 01)
    (re.compile(r"\b\d{3}[-\s]\d{3}[-\s]\d{3}[-\s]\d{2}\b"), "[REDACTED_SNILS]"),
    # Банковские счета РФ (20 цифр, обычно начинаются на 407, 408, 301, 423 и т.д.)
    (re.compile(r"\b[34]\d{19}\b"), "[REDACTED_ACCOUNT]"),
    # Номера банковских карт (16 цифр с разделителями)
    (re.compile(r"\b(?:\d{4}[-\s]){3}\d{4}\b"), "[REDACTED_CARD]"),
    # ИНН с ключевым словом
    (re.compile(r"(?i)\b(инн\s*[:#№]?\s*)\d{10,12}\b"), r"\1[REDACTED_INN]"),
    # Email адреса
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Номера телефонов РФ (+7 / 8)
    (re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b"), "[REDACTED_PHONE]"),
    # Пути пользователей Windows (C:\Users\<username>... или C:/Users/...)
    (re.compile(r"(?i)\b[a-z]:[\\/]users[\\/][^\s\\/:]+"), "[USER_PATH]"),
    # Пути пользователей macOS (/Users/<username>...)
    (re.compile(r"(?<!\w)/Users/[^\s\\/:]+"), "[USER_PATH]"),
    # Пути пользователей Linux (/home/<username>...)
    (re.compile(r"(?<!\w)/home/[^\s\\/:]+"), "[USER_PATH]"),
]


def sanitize_user_paths(text: str) -> str:
    """Очищает строку от системных путей пользователя и имени профиля ОС."""
    if not isinstance(text, str) or not text:
        return text

    sanitized = text
    try:
        home = str(Path.home())
        if home and home in sanitized:
            sanitized = sanitized.replace(home, "[USER_PATH]")
        # Также проверяем вариант с прямыми слешами на Windows
        home_fwd = home.replace("\\", "/")
        if home_fwd and home_fwd in sanitized:
            sanitized = sanitized.replace(home_fwd, "[USER_PATH]")
    except Exception:
        pass

    # Регулярные выражения для профилей пользователей
    sanitized = re.sub(r"(?i)\b[a-z]:[\\/]users[\\/][^\s\\/:]+", "[USER_PATH]", sanitized)
    sanitized = re.sub(r"(?<!\w)/Users/[^\s\\/:]+", "[USER_PATH]", sanitized)
    sanitized = re.sub(r"(?<!\w)/home/[^\s\\/:]+", "[USER_PATH]", sanitized)
    return sanitized


def sanitize_log_text(text: str) -> str:
    """Очищает строку от конфиденциальных данных, путей пользователей и секретов."""
    if not isinstance(text, str) or not text:
        return text

    sanitized = sanitize_user_paths(text)
    for pattern, replacement in REDACTION_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_error_message(exc_or_text: Any, max_length: int = 500) -> str:
    """Безопасно очищает сообщение об ошибке от путей пользователя, секретов и текста документов.

    Ограничивает длину сообщения для предотвращения непреднамеренной утечки
    больших фрагментов текста документов через текст исключения.
    """
    if isinstance(exc_or_text, BaseException):
        # Exception messages may contain arbitrary document text, which no
        # regex can reliably recognize as PII. Export only the error class.
        text = type(exc_or_text).__name__
    else:
        text = str(exc_or_text or "")

    sanitized = sanitize_log_text(text)
    if len(sanitized) > max_length:
        sanitized = sanitized[: max_length - 3] + "..."
    return sanitized


def get_safe_diagnostic_report() -> dict[str, Any]:
    """Возвращает полностью безопасный диагностический отчёт без PII, путей пользователей и текста документов."""
    import platform
    import version

    diag = {
        "app_name": "DOCXdodyr",
        "app_version": getattr(version, "__version__", "3.0.0"),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        },
        "privacy_status": {
            "local_only_core": True,
            "telemetry_enabled": False,
            "analytics_enabled": False,
            "cloud_consent_required": True,
        },
    }
    return diag


class RedactingFilter(logging.Filter):
    """Фильтр логирования, маскирующий чувствительные данные в объекте LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_log_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize_log_text(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize_log_text(str(arg)) if isinstance(arg, str) else arg for arg in record.args)
        return True


class RedactingFormatter(logging.Formatter):
    """Форматировщик логирования, гарантирующий очистку итогового вывода и трассировок стека."""

    def formatException(self, exc_info):
        return exc_info[0].__name__

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return sanitize_log_text(formatted)


def setup_application_logging(
    log_file: Optional[Path | str] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Централизованная настройка безопасного логирования приложения DOCXдодыр."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Очищаем существующие хэндлеры для предотвращения дублирования
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    filter_instance = RedactingFilter()
    formatter = RedactingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Консольный хэндлер
    # Keep stdout reserved for machine-readable CLI results.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(filter_instance)
    root_logger.addHandler(console_handler)

    # Файловый хэндлер (если указан путь к файлу логов)
    if log_file:
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(filter_instance)
            root_logger.addHandler(file_handler)
        except Exception as exc:
            root_logger.warning("Не удалось инициализировать файловый лог: %s", exc)

    return root_logger


__all__ = [
    "REDACTION_PATTERNS",
    "sanitize_log_text",
    "sanitize_user_paths",
    "sanitize_error_message",
    "get_safe_diagnostic_report",
    "RedactingFilter",
    "RedactingFormatter",
    "setup_application_logging",
]
