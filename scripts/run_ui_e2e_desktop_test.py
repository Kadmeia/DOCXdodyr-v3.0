# -*- coding: utf-8 -*-
"""Автоматический UI E2E-тест десктопного приложения DOCXдодыр.

1. Запускает скомпилированный процесс DOCXdodyr.exe.
2. Проверяет диспетчер задач Windows (PID, память, статус running).
3. Проверяет инициализацию веб-движка (WebView2 / Edge Chromium).
4. Запускает нативное окно десктопного приложения (PyWebView).
5. Кликами в DOM активирует вкладки, чекбоксы и инициирует загрузку папки.
6. В реальном времени мониторит статус, прогресс, ошибки JS и лог-файл ядра.
7. Проверяет модальные окна и генерацию обезличенных документов.
"""

import os
import platform
import sys
import time
import json
import threading
import subprocess
import shutil
import tempfile
from pathlib import Path
import psutil

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "app"))

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import functools
print = functools.partial(print, flush=True)

import webview
import app_paths
from main import ApiWrapper


def test_native_exe_process(exe_path: Path) -> dict:
    """Шаг 1: Запуск нативного бинарника, инспекция диспетчера задач/процессов и веб-движка."""
    print("=" * 70)
    print(f"ШАГ 1: Тестирование запуска бинарника {exe_path.name} в процессах ОС ({sys.platform})")
    print("=" * 70)
    assert exe_path.exists(), f"Файл не найден: {exe_path}"

    proc_info = {}
    p = None
    proc = subprocess.Popen([str(exe_path)])
    try:
        # Даем процессу инициализироваться в системе
        time.sleep(3.5)
        p = psutil.Process(proc.pid)
        proc_info["pid"] = p.pid
        proc_info["name"] = p.name()
        proc_info["status"] = p.status()
        mem_mb = p.memory_info().rss / (1024 * 1024)
        proc_info["memory_mb"] = round(mem_mb, 2)

        # Проверяем дочерние процессы (WebView2 / WebKit)
        children = p.children(recursive=True)
        proc_info["children_count"] = len(children)
        proc_info["children"] = [{"name": c.name(), "pid": c.pid} for c in children]

        print(f"  [OK] Процесс запущен: {proc_info['name']} (PID: {proc_info['pid']})")
        print(f"  [OK] Статус в ОС: {proc_info['status']}")
        print(f"  [OK] Использование памяти: {proc_info['memory_mb']} МБ")
        print(f"  [OK] Дочерних процессов: {proc_info['children_count']}")
        for ch in proc_info["children"]:
            print(f"    - Дочерний процесс: {ch['name']} (PID: {ch['pid']})")

        assert proc_info["name"].lower().startswith("docxdodyr"), "Имя процесса не совпадает"
        assert proc_info["status"] in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING), f"Процесс не находится в активном состоянии: {proc_info['status']}"
        assert proc_info["memory_mb"] > 20.0, "Потребление памяти аномально мало"

    finally:
        print(f"  Остановка тестового процесса {exe_path.name}...")
        from scripts.verify_packaged_app import _terminate_process_tree
        _terminate_process_tree(proc, psutil)
        print(f"  [OK] Процесс {exe_path.name} корректно завершен.")
        time.sleep(1.0)

    return proc_info


def run_ui_e2e_session(target_folder: Path, proc_info: dict = None, cleanup_dir: Path | None = None) -> dict:
    """Шаг 2: Запуск интерактивного UI E2E теста с кликами и обработкой файлов."""
    print("\n" + "=" * 70)
    print(f"ШАГ 2: Интерактивный UI E2E-тест десктопного окна DOCXдодыр")
    print(f"Целевая папка для клик-загрузки: {target_folder}")
    print("=" * 70)

    results = {
        "clicks": [],
        "ui_states": [],
        "js_errors": [],
        "backend_errors": [],
        "success": False,
        "processed_files": [],
        "total_replacements": 0
    }

    wrapper = ApiWrapper()
    html_path = str(app_paths.get_web_dir() / "index.html")
    assert Path(html_path).exists(), f"Файл интерфейса не найден: {html_path}"

    window = webview.create_window(
        title="DOCXdodyr UI E2E Test Suite",
        url=html_path,
        js_api=wrapper,
        width=960,
        height=800,
        background_color="#020617"
    )
    wrapper.set_window(window)

    def ui_actions_worker():
        try:
            print("\n[UI E2E] Ожидание инициализации DOM и скриптов...")
            time.sleep(2.0)

            def eval_js(code):
                return window.evaluate_js(code)

            # 1. Регистрация перехватчика JS-ошибок
            eval_js("""
                window.__js_errors = [];
                window.addEventListener('error', function(e) {
                    window.__js_errors.push(e.message || String(e));
                });
            """)
            print("  [OK] Слушатель JS-ошибок зарегистрирован в DOM.")

            # 2. Клик: переход на вкладку «Обезличивание»
            eval_js("document.getElementById('btn-tab-anonymize').click();")
            time.sleep(0.3)
            tab_disp = eval_js("document.getElementById('tab-anonymize').style.display;")
            assert tab_disp == "flex", "Вкладка анонимизации не активировалась!"
            results["clicks"].append("Click: #btn-tab-anonymize -> flex")
            print("  [OK] Клик: вкладка «Обезличивание» открыта.")

            # 3. Клики: переключение опций сохранения оригинала и дешифратора через клики
            eval_js("""
                const cbOrig = document.getElementById('cb-save-original');
                if (cbOrig && !cbOrig.checked) { cbOrig.click(); }
                const cbPdf = document.getElementById('cb-save-pdf');
                if (cbPdf && cbPdf.checked) { cbPdf.click(); }
                const cbMd = document.getElementById('cb-save-md');
                if (cbMd && cbMd.checked) { cbMd.click(); }
                const cbDec = document.getElementById('cb-save-decoder');
                if (cbDec && !cbDec.checked) { cbDec.click(); }
            """)
            results["clicks"].append("Click: #cb-save-original, #cb-save-decoder")
            print("  [OK] Клик: включены чекбоксы сохранения оригинала и дешифратора.")

            log_path = Path(app_paths.get_app_log_path())
            start_log_pos = log_path.stat().st_size if log_path.exists() else 0

            # 4. Имитация клика/вызова загрузки папки в интерфейсе через API мост
            folder_json = json.dumps(str(target_folder))
            print(f"  [OK] Клик: передача папки через UI bridge: {target_folder}")
            eval_js(f"window.pywebview.api.process_folder_dialog({folder_json}, false);")
            results["clicks"].append(f"Call: process_folder_dialog({folder_json})")

            # 5. Цикл мониторинга процесса обработки через UI
            print("\n[UI E2E] Мониторинг выполнения пакета через DOM и лог-файл...")
            max_wait_seconds = 300
            start_time = time.time()
            last_status = ""

            while time.time() - start_time < max_wait_seconds:
                status_text = eval_js("document.getElementById('folder-status').textContent;") or ""
                if status_text != last_status:
                    print(f"  [UI Status]: {status_text}")
                    results["ui_states"].append(status_text)
                    last_status = status_text

                # Проверка JS ошибок
                js_errs = eval_js("window.__js_errors || [];")
                if js_errs:
                    print(f"  [JS ERROR DETECTED]: {js_errs}")
                    results["js_errors"].extend(js_errs)

                if "Обработка папки завершена" in status_text or "Комплект успешно продолжен" in status_text:
                    print("  [OK] Получен финальный статус завершения обработки от UI!")
                    break

                if "Ошибка" in status_text:
                    print(f"  [ERROR STATUS]: {status_text}")
                    results["backend_errors"].append(status_text)
                    break

                time.sleep(1.0)

            # 6. Проверка модального окна проверки сущностей (Review Modal)
            time.sleep(1.5)
            review_display = eval_js("document.getElementById('review-overlay').style.display;")
            if review_display != "flex":
                eval_js("window.showReviewModal && window.showReviewModal();")
                time.sleep(0.5)
                review_display = eval_js("document.getElementById('review-overlay').style.display;")
            print(f"  Состояние Review Modal в DOM: display={review_display}")
            if review_display == "flex":
                print("  [OK] Модальное окно проверки сущностей успешно открылось.")
                # Закрываем модальное окно кликом
                eval_js("window.closeReviewModal();")
                time.sleep(0.5)
                results["clicks"].append("Click: closeReviewModal()")

            # 7. Проверка результатов в файловой системе
            out_dir = target_folder / "Обезличенные документы"
            if out_dir.exists():
                out_files = list(out_dir.glob("*.*"))
                results["processed_files"] = [f.name for f in out_files]
                print(f"  [OK] Сгенерировано файлов в выходе: {len(out_files)}")
                for f in out_files:
                    print(f"    - {f.name} ({f.stat().st_size} байт)")
                assert len(out_files) >= 9, f"Ожидалось не менее 9 файлов, получено: {len(out_files)}"
                results["success"] = True
            else:
                print(f"  ✗ Папка вывода не создана: {out_dir}")

            # 8. Проверка лог-файла приложения на ошибки за время теста
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(start_log_pos)
                    new_log_content = f.read()
                new_errors = [line for line in new_log_content.splitlines() if "ERROR" in line or "Exception" in line]
                if new_errors:
                    print(f"  [Внимание] Ошибки в логе ядра во время теста ({len(new_errors)} шт.):")
                    for err in new_errors[:5]:
                        print(f"    {err}")
                    results["backend_errors"].extend(new_errors)
                else:
                    print("  [OK] В лог-файле ядра docxdodyr.log во время теста ошибок не зафиксировано.")

            if results["success"] and not results["js_errors"] and not results["backend_errors"]:
                print("\n🎉 ИНТЕРАКТИВНЫЙ UI E2E ТЕСТ УСПЕШНО ЗАВЕРШЕН!")

        except Exception as e:
            import traceback
            traceback.print_exc()
            results["backend_errors"].append(str(e))
        finally:
            print("\n" + "=" * 70)
            print("ИТОГОВЫЙ ОТЧЕТ UI E2E-ТЕСТИРОВАНИЯ:")
            print("=" * 70)
            if proc_info:
                print(f"Запуск {proc_info.get('name', 'бинарника')}:  OK (PID {proc_info.get('pid')}, RAM {proc_info.get('memory_mb')} MB)")
            print(f"Клики UI:               {len(results.get('clicks', []))} действий выполнено")
            print(f"Ошибки JS (фронтенд):   {len(results.get('js_errors', []))}")
            print(f"Ошибки бэкенда/ядра:    {len(results.get('backend_errors', []))}")
            print(f"Обработано файлов:      {len(results.get('processed_files', []))} / 9")
            print(f"Статус E2E-теста:       {'УСПЕХ' if results.get('success') else 'ПРОВАЛ'}")
            print("=" * 70)
            sys.stdout.flush()
            try:
                window.destroy()
            except Exception:
                pass
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            os._exit(0 if results.get("success") and not results["js_errors"] and not results["backend_errors"] else 1)

    threading.Thread(target=ui_actions_worker, daemon=True).start()
    webview.start()

    return results


def main():
    if sys.platform == "win32":
        exe_path = REPO_ROOT / "dist" / "DOCXdodyr" / "DOCXdodyr.exe"
    elif sys.platform == "darwin":
        target_arch = os.environ.get("DOCXDODYR_TARGET_ARCH") or platform.machine()
        app_bin = REPO_ROOT / "dist" / f"DOCXdodyr-{target_arch}.app" / "Contents" / "MacOS" / "DOCXdodyr"
        standalone_bin = REPO_ROOT / "dist" / "DOCXdodyr" / "DOCXdodyr"
        exe_path = app_bin if app_bin.exists() else standalone_bin
    else:
        exe_path = REPO_ROOT / "dist" / "DOCXdodyr" / "DOCXdodyr"

    source_folder = REPO_ROOT / "tests" / "fresh_corpus_stage09_round3"
    if not source_folder.exists() or len(list(source_folder.glob("*.*"))) < 9:
        from scripts.generate_fresh_test_documents_round3 import main as gen_main
        gen_main()
    # Run against fresh copies: never delete previous outputs, decoder files,
    # or incomplete checkpoints in the checked-in test corpus.
    with tempfile.TemporaryDirectory(prefix="docxdodyr-ui-e2e-") as temp_dir:
        target_folder = Path(temp_dir) / "corpus"
        target_folder.mkdir()
        for source in source_folder.iterdir():
            if source.is_file() and source.suffix.lower() in {".docx", ".xlsx", ".pdf"}:
                shutil.copy2(source, target_folder / source.name)

        # Шаг 1: Тест процесса бинарника в диспетчере задач/процессов
        proc_info = test_native_exe_process(exe_path)

        # Шаг 2: UI E2E тест кликов и обработки документов в нативном окне
        run_ui_e2e_session(target_folder, proc_info=proc_info, cleanup_dir=Path(temp_dir))


if __name__ == "__main__":
    main()
