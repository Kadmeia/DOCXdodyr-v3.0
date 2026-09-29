# -*- coding: utf-8 -*-
"""Скрипт пакетной обработки папки свежих тестовых документов Этапа 09 (Раунд 2)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from backend_api import BackendApi
from folder_pipeline import FolderAnonymizationPipeline

TARGET_DIR = ROOT / "tests" / "fresh_corpus_stage09_round2"

def progress(current, total, message):
    print(f"[{current}/{total}] {message}")

def main():
    print(f"Запуск пакетной обработки папки (Раунд 2): {TARGET_DIR}")
    api = BackendApi()
    pipeline = FolderAnonymizationPipeline(api)
    result = pipeline.process(
        TARGET_DIR,
        recursive=False,
        progress_callback=progress
    )
    print("\n--- ИТОГИ ОБРАБОТКИ ПАПКИ (РАУНД 2) ---")
    print(f"Обработано файлов: {result.processed_count}")
    print(f"Ошибок: {result.error_count}")
    print(f"Всего замен: {result.total_replacements}")
    print(f"Папка вывода: {result.output_dir}")
    print(f"Дешифратор: {result.decoder_path}")
    if result.errors:
        print("Ошибки:", result.errors)

if __name__ == "__main__":
    main()
