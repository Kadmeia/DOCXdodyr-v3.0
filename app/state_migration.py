# -*- coding: utf-8 -*-
"""Безопасная миграция устаревших файлов настроек и секретов в системные каталоги.

Мигрирует:
1. settings.json из корня проекта/cwd в platformdirs user_config_dir.
2. Исключения.txt и Замены.txt из корня проекта — в platformdirs user_data_dir.
3. review_queue.json и .review_context.enc — в platformdirs user_data_dir.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil

import app_paths

logger = logging.getLogger(__name__)


def run_state_migration(
    base_dir: Path | None = None,
    *,
    migrate_review_state: bool = True,
) -> dict[str, bool]:
    """Выполняет однократную безопасную миграцию старого формата данных.

    Background document actions do not have a review UI and must not copy
    review queue/vault files into the user data directory just by starting.
    """
    results = {
        "settings_migrated": False,
        "exclusions_migrated": False,
        "replacements_migrated": False,
        "review_state_migrated": False,
    }

    source_dir = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
    target_config = app_paths.get_settings_path()
    # 1. Миграция settings.json
    legacy_settings = source_dir / "settings.json"
    if legacy_settings.is_file() and not target_config.exists():
        try:
            raw = legacy_settings.read_text(encoding="utf-8")
            data = json.loads(raw)
            app_paths.atomic_write_json(target_config, data)
            results["settings_migrated"] = True
            logger.info("Migrated legacy settings.json to %s", target_config)
        except Exception as exc:
            logger.warning("Failed to migrate legacy settings.json: %s", exc)

    # 3. Миграция Исключения.txt
    target_exclusions = app_paths.get_exclusions_path()
    legacy_exclusions = source_dir / "Исключения.txt"
    if legacy_exclusions.is_file() and not target_exclusions.exists():
        try:
            shutil.copy2(legacy_exclusions, target_exclusions)
            results["exclusions_migrated"] = True
            logger.info("Migrated Исключения.txt to %s", target_exclusions)
        except Exception as exc:
            logger.warning("Failed to migrate Исключения.txt: %s", exc)

    # 4. Миграция Замены.txt
    target_replacements = app_paths.get_replacements_path()
    legacy_replacements = source_dir / "Замены.txt"
    if legacy_replacements.is_file() and not target_replacements.exists():
        try:
            shutil.copy2(legacy_replacements, target_replacements)
            results["replacements_migrated"] = True
            logger.info("Migrated Замены.txt to %s", target_replacements)
        except Exception as exc:
            logger.warning("Failed to migrate Замены.txt: %s", exc)

    if migrate_review_state:
        # 5. Миграция review_queue.json и .review_context.enc
        target_queue = app_paths.get_review_queue_path()
        legacy_queue = source_dir / "review_queue.json"
        if legacy_queue.is_file() and not target_queue.exists():
            try:
                shutil.copy2(legacy_queue, target_queue)
                results["review_state_migrated"] = True
            except Exception:
                pass

        target_context = app_paths.get_review_context_path()
        legacy_context = source_dir / ".review_context.enc"
        if legacy_context.is_file() and not target_context.exists():
            try:
                shutil.copy2(legacy_context, target_context)
                results["review_state_migrated"] = True
            except Exception:
                pass

    return results
