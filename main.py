# -*- coding: utf-8 -*-
"""Точка входа приложения DOCXдодыр (PyWebView)."""
import sys

if sys.version_info[:2] != (3, 11):
    raise SystemExit("DOCXдодыр требует Python 3.11. Создайте окружение с Python 3.11.")

import os
from pathlib import Path
import multiprocessing
APP_CODE_DIR = Path(__file__).resolve().parent / "app"
if str(APP_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(APP_CODE_DIR))
if __name__ == '__main__':
    multiprocessing.freeze_support()
import docx_compat
import webview
from backend_api import BackendApi
import logging
import threading
import inspect
import app_paths
import ui_bridge
import version
from log_sanitizer import setup_application_logging

# Настройка безопасного логирования с автоматической санитизацией PII и секретов
try:
    log_file = app_paths.get_app_log_path()
    setup_application_logging(log_file)
except Exception:
    setup_application_logging()

logger = logging.getLogger(__name__)

def extract_cli_paths(raw_args: list[str]) -> list[str]:
    """Извлекает и нормализует пути к файлам и папкам из аргументов командной строки.

    Надёжно обрабатывает:
      - Пути с пробелами и внешними кавычками
      - Артефакты экранирования обратных слэшей в Windows Explorer (например, C:\\dir\\" -> C:\\dir)
      - Относительные и абсолютные пути
    """
    cleaned_paths = []
    for raw in raw_args:
        if raw.startswith('-'):
            continue
        val = raw.strip(' \t\r\n')
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1].strip()
        val = val.rstrip('"\'').strip()

        candidates = [val, val.rstrip('\\/')]
        for cand in candidates:
            if cand and os.path.exists(cand):
                abs_path = os.path.abspath(cand)
                if abs_path not in cleaned_paths:
                    cleaned_paths.append(abs_path)
                break
    return cleaned_paths


class ApiWrapper:
    def __init__(self, api=None, startup_files=None, auto_start=False, restore_mode=False):
        if api is not None:
            self._api = api
        else:
            parameters = inspect.signature(BackendApi).parameters
            backend_kwargs = {}
            if "lazy_pullenti" in parameters:
                backend_kwargs["lazy_pullenti"] = True
            if "background_mode" in parameters:
                # A visible compact window does not imply review/recovery UI.
                # Finder actions must remain independent from Keychain just as
                # the former headless path was.
                backend_kwargs["background_mode"] = bool(auto_start or restore_mode)
            self._api = BackendApi(**backend_kwargs)
        self._window = None
        self.startup_files = list(startup_files) if startup_files else []
        self.auto_start = bool(auto_start)
        self.restore_mode = bool(restore_mode)
        self._docs_waiting_for_decoder = []
        self._native_drop_handlers = []
        self._native_drop_bound = False

    def set_window(self, window):
        self._window = window
        self._api.set_window(window)

    # --- Явные прокси для всех методов, которые вызывает JavaScript ---
    # PyWebView инспектирует js_api при запуске и экспортирует только явно
    # определённые методы. __getattr__ не работает для этой цели.

    def init_ui(self):
        res = self._api.init_ui()
        self._bind_native_drop_handlers()
        if self.auto_start or self.restore_mode:
            self._api.is_quick_mode = True
            to_process = list(self.startup_files)
            self.startup_files = []
            threading.Thread(target=self._run_quick_mode, args=(to_process,), daemon=True).start()
        elif self.startup_files:
            to_process = list(self.startup_files)
            self.startup_files = []
            def _stage_startup():
                import time
                time.sleep(0.35)
                folders = [f for f in to_process if os.path.isdir(f)]
                files = [f for f in to_process if os.path.isfile(f)]
                if folders:
                    target_dir = folders[0]
                    self._api.staged_folder = target_dir
                    if self._window:
                        ui_bridge.ui_set_folder_status(self._window, f"Выбрана папка: {os.path.basename(target_dir)}", "info")
                elif files:
                    self._api.staged_files = files
                    if self._window:
                        names = ", ".join(Path(p).name for p in files[:3])
                        if len(files) > 3:
                            names += f" и ещё {len(files) - 3}"
                        prompt = f"Загружено: {names} (нажмите «Обезличить»)"
                        ui_bridge.ui_update_dropzone_text(self._window, 'lbl-anonymize-docs', prompt)
            threading.Thread(target=_stage_startup, daemon=True).start()
        return res

    def _run_quick_mode(self, to_process: list[str]):
        import time
        time.sleep(0.15)
        mode_title = "DOCXдодыр — Восстановление" if self.restore_mode else "DOCXдодыр — Обезличивание"

        if not to_process:
            if self._window:
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title,
                    "Ошибка: не указаны файлы или папки для обработки.", 0
                )
            time.sleep(2.5)
            self.close_window()
            return

        if self.restore_mode:
            self._execute_quick_restore(to_process, mode_title)
        else:
            self._execute_quick_anonymize(to_process, mode_title)

    def _execute_quick_anonymize(self, to_process: list[str], mode_title: str):
        import time
        from backend_api import Worker
        from log_sanitizer import sanitize_error_message

        targets = [str(path) for path in to_process if os.path.exists(path)]
        if not targets:
            self._finish_quick_with_error(
                mode_title, ValueError("Не найдено объектов для обработки"), delay=2.5
            )
            return

        processed_total = 0
        error_total = 0
        error_messages = []
        last_output_dir = None
        total_targets = len(targets)

        def progress_for(target_index):
            def _progress(current, maximum, status_text, percent=None, *args, **kwargs):
                if percent is not None:
                    overall_percent = int(((target_index + (percent / 100.0)) / total_targets) * 100)
                else:
                    fraction = current / max(maximum, 1)
                    overall_percent = int(((target_index + fraction) / total_targets) * 100)
                if self._window:
                    ui_bridge.ui_set_global_progress(
                        self._window, True, mode_title,
                        f"[{target_index + 1}/{total_targets}] {status_text}", overall_percent,
                    )
            return _progress

        for target_index, target in enumerate(targets):
            if getattr(self._api, 'is_cancelled', lambda: False)():
                if self._window:
                    ui_bridge.ui_set_global_progress(
                        self._window, True, mode_title,
                        "Обработка отменена пользователем.",
                        int((target_index / total_targets) * 100),
                    )
                time.sleep(1.2)
                self.close_window()
                return

            target_name = Path(target).name
            if self._window:
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title,
                    f"[{target_index + 1}/{total_targets}] Подготовка: {target_name}",
                    int((target_index / total_targets) * 100),
                )

            try:
                if os.path.isdir(target):
                    process_folder = getattr(self._api, 'process_folder', None)
                    if not callable(process_folder):
                        raise RuntimeError("Папочный режим недоступен")
                    parameters = inspect.signature(process_folder).parameters
                    kwargs = {
                        'output_dir_name': 'Обезличенные документы',
                        'continue_existing': False,
                    }
                    if (
                        'progress_callback' in parameters
                        or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
                    ):
                        kwargs['progress_callback'] = progress_for(target_index)
                    result = process_folder(target, **kwargs)
                    last_output_dir = (
                        str(getattr(result, 'output_dir', '') or '')
                        or getattr(self._api, '_last_output_dir', None)
                        or os.path.join(target, 'Обезличенные документы')
                    )
                else:
                    original_progress = getattr(self._api, 'update_progress', None)
                    self._api.update_progress = progress_for(target_index)
                    try:
                        result = Worker([target], self._api).run()
                    finally:
                        if original_progress is not None:
                            self._api.update_progress = original_progress
                        else:
                            delattr(self._api, 'update_progress')
                    last_output_dir = (
                        getattr(self._api, '_last_output_dir', None)
                        or os.path.join(os.path.dirname(target), 'Обезличенные документы')
                    )

                if isinstance(result, dict):
                    processed = int(result.get('processed_count', 0) or 0)
                    errors = int(result.get('error_count', 0) or 0)
                    details = result.get('errors', []) or []
                elif result is not None:
                    processed = int(getattr(result, 'processed_count', 0) or 0)
                    errors = int(getattr(result, 'error_count', 0) or 0)
                    details = getattr(result, 'errors', []) or []
                else:
                    processed, errors, details = 0, 1, ["операция не была запущена"]
                processed_total += processed
                error_total += errors
                for detail in details:
                    message = detail.get('error') if isinstance(detail, dict) else str(detail)
                    error_messages.append(f"{target_name}: {message}")
            except Exception as exc:
                error_total += 1
                error_messages.append(f"{target_name}: {sanitize_error_message(exc)}")
                logger.exception("Ошибка обработки объекта быстрого режима")

        if self._window:
            if error_total:
                prefix = (
                    f"Частично готово: успешно {processed_total}, ошибок {error_total}."
                    if processed_total
                    else f"Обработка не выполнена: ошибок {error_total}."
                )
                detail = f" {error_messages[0]}" if error_messages else ""
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title, f"{prefix}{detail}",
                    100 if processed_total else 0,
                )
            else:
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title,
                    f"Готово! Успешно обработано файлов: {processed_total}", 100,
                )
        if (
            getattr(self._api, 'open_output_folder', True)
            and last_output_dir
            and os.path.exists(last_output_dir)
        ):
            app_paths.open_folder_in_file_manager(last_output_dir)
        time.sleep(3.0 if error_total else 1.6)
        self.close_window()

    def _execute_quick_restore(self, to_process: list[str], mode_title: str):
        import time
        from document_restorer import find_decoder_near_document

        docs_to_restore = []
        supported = {'.docx', '.docm', '.xlsx', '.xlsm', '.md'}
        for target in to_process:
            if os.path.isfile(target) and Path(target).suffix.casefold() in supported:
                if target not in docs_to_restore:
                    docs_to_restore.append(target)
            elif os.path.isdir(target):
                for current_root, dirs, names in os.walk(target):
                    dirs[:] = [d for d in dirs if d != 'Обезличенные документы']
                    for name in sorted(names, key=str.casefold):
                        candidate = os.path.join(current_root, name)
                        if os.path.splitext(name)[1].lower() in supported and candidate not in docs_to_restore:
                            docs_to_restore.append(candidate)

        if not docs_to_restore:
            if self._window:
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title,
                    "В указанной папке нет документов для восстановления.", 0
                )
            time.sleep(2.5)
            self.close_window()
            return

        self._docs_waiting_for_decoder = docs_to_restore
        decoders = []
        for document in docs_to_restore:
            try:
                decoder = find_decoder_near_document(document)
            except Exception:
                # Retry discovery inside BackendApi for every document.  That
                # loop records the integrity error for the affected file and
                # still restores the remaining Finder selection.
                self._perform_restore_with_decoders(docs_to_restore, [], mode_title)
                return
            if decoder:
                decoders.append(str(decoder))
            else:
                decoders = []
                break

        if decoders:
            self._perform_restore_with_decoders(docs_to_restore, decoders, mode_title)
        elif len(docs_to_restore) == 1:
            if self._window:
                ui_bridge.safe_call_js(
                    self._window,
                    "showMissingDecoder",
                    f"Дешифратор (.json) не найден рядом с «{Path(docs_to_restore[0]).name}». Выберите дешифратор вручную:"
                )
        else:
            self._perform_restore_with_decoders(docs_to_restore, [], mode_title)

    def _perform_restore_with_decoder(self, docs_to_restore: list[str], decoder_path: str, mode_title: str = "DOCXдодыр — Восстановление"):
        return self._perform_restore_with_decoders(docs_to_restore, [decoder_path], mode_title)

    def _perform_restore_with_decoders(self, docs_to_restore: list[str], decoder_paths: list[str], mode_title: str = "DOCXдодыр — Восстановление"):
        import time
        self._api.restore_doc_paths = docs_to_restore
        self._api.restore_json_paths = list(decoder_paths)

        if self._window:
            decoder_label = (
                Path(decoder_paths[0]).name
                if len(decoder_paths) == 1
                else f"{len(decoder_paths)} дешифраторов"
            ) if decoder_paths else "автопоиск дешифраторов"
            ui_bridge.ui_set_global_progress(
                self._window, True, mode_title,
                f"{decoder_label}. Восстановление {len(docs_to_restore)} документов...", 10
            )

        try:
            res = self._api._run_restore()
        except Exception as exc:
            self._finish_quick_with_error(mode_title, exc, delay=2.5)
            return
        success_count = len(res.get("successes", [])) if isinstance(res, dict) else len(docs_to_restore)
        errors = list(res.get("errors", [])) if isinstance(res, dict) else []

        if self._window:
            if errors:
                first_error = str(errors[0])
                prefix = (
                    f"Частично готово: восстановлено {success_count}; ошибок {len(errors)}."
                    if success_count
                    else "Восстановление не выполнено."
                )
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title,
                    f"{prefix} {first_error}", 100 if success_count else 0
                )
            else:
                ui_bridge.ui_set_global_progress(
                    self._window, True, mode_title,
                    f"Готово! Восстановлено: {success_count} файлов.", 100
                )

        parent_dir = os.path.dirname(docs_to_restore[0])
        if getattr(self._api, 'open_output_folder', True) and os.path.exists(parent_dir):
            app_paths.open_folder_in_file_manager(parent_dir)
        time.sleep(3.0 if errors else 1.6)
        self.close_window()

    def _finish_quick_with_error(self, mode_title: str, exc: Exception, *, delay: float = 2.0):
        """Keep Quick Action failures visible instead of losing them in a worker thread."""
        import time
        from log_sanitizer import sanitize_error_message

        safe_error = sanitize_error_message(exc)
        logger.error("Ошибка быстрого режима %s: %s", mode_title, safe_error)
        if self._window:
            ui_bridge.ui_set_global_progress(
                self._window, True, mode_title, f"Ошибка: {safe_error}", 0
            )
        time.sleep(delay)
        self.close_window()

    def pick_decoder_for_quick_restore(self):
        """Вызов диалога выбора файла дешифратора (.json) при фоновом восстановлении."""
        if not self._window:
            return
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=('Дешифратор (*.json)', 'Все файлы (*.*)')
        )
        if result and len(result) > 0:
            picked_json = str(result[0])
            docs = getattr(self, '_docs_waiting_for_decoder', [])
            if docs:
                threading.Thread(
                    target=self._perform_restore_with_decoder,
                    args=(docs, picked_json),
                    daemon=True
                ).start()

    def cancel_operation(self):
        return self.cancel_processing()

    def get_window_geometry(self):
        if hasattr(self._api, "get_window_geometry"):
            return self._api.get_window_geometry()
        return {"width": 840, "height": 740}

    def save_window_geometry(self, width, height, x=None, y=None):
        if hasattr(self._api, "save_window_geometry"):
            return self._api.save_window_geometry(width, height, x, y)
        return {"success": False}

    def get_settings(self):
        return self._api.get_settings()

    def update_settings(self, settings):
        if hasattr(self._api, "update_settings") and callable(self._api.update_settings):
            self._api.update_settings(settings)
        else:
            if hasattr(self._api, "settings") and isinstance(self._api.settings, dict):
                self._api.settings.update(settings)
            if 'save_original' in settings:
                self._api.save_original = bool(settings['save_original'])
            if 'save_docx' in settings:
                self._api.save_docx = bool(settings['save_docx'])
            if 'save_pdf' in settings:
                self._api.save_pdf = bool(settings['save_pdf'])
            if 'save_markdown' in settings:
                self._api.save_markdown = bool(settings['save_markdown'])
            if 'irreversible_pdf' in settings:
                self._api.irreversible_pdf = bool(settings['irreversible_pdf'])
            if 'save_decoder' in settings:
                self._api.save_decoder = bool(settings['save_decoder'])
            if 'auto_decoder' in settings:
                self._api.auto_decoder = bool(settings['auto_decoder'])
            if 'open_output_folder' in settings:
                self._api.open_output_folder = bool(settings['open_output_folder'])
            if 'ocr_lang' in settings:
                self._api.ocr_lang = settings['ocr_lang']
            if 'qwen_enabled' in settings and hasattr(self._api, "save_qwen_settings"):
                self._api.save_qwen_settings({"enabled": bool(settings['qwen_enabled'])})
            if hasattr(self._api, "_save_settings"):
                self._api._save_settings()

    def open_file_dialog(self, dialog_type):
        if not self._window: return
        file_types = ('Все файлы (*.*)',)
        if dialog_type == 'anonymize_docs':
            file_types = (
                'Все поддерживаемые (*.docx;*.docm;*.xlsx;*.xlsm;*.pdf;*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.tif;*.webp)',
                'Документы Word/Excel/PDF (*.docx;*.docm;*.xlsx;*.xlsm;*.pdf)',
                'Изображения (*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.tif;*.webp)',
                'Все файлы (*.*)',
            )
        elif dialog_type == 'restore_doc':
            file_types = ('Документы (*.docx;*.docm;*.xlsx;*.xlsm)',)
        elif dialog_type == 'restore_json':
            file_types = ('Дешифратор (*.json)',)
        elif dialog_type == 'hidden_inspect':
            file_types = ('Документы (*.docx;*.docm;*.xlsx;*.xlsm;*.pptx;*.pdf)',)
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True, file_types=file_types
        )
        if result:
            self.files_dropped(dialog_type, list(result))

    def open_folder_dialog(self, dialog_type='anonymize_folder'):
        """Open a native folder picker for the batch anonymization flow.

        PyWebView returns a string on some backends and a one-item tuple on
        others, so both shapes are normalized before reaching the API.
        """
        if not self._window:
            return
        folder_dialog = getattr(webview, 'FOLDER_DIALOG', webview.OPEN_DIALOG)
        try:
            result = self._window.create_file_dialog(folder_dialog)
        except TypeError:
            # A few older pywebview versions do not expose a distinct folder
            # dialog.  Passing the open dialog still permits selecting a
            # directory on those platforms.
            result = self._window.create_file_dialog(webview.OPEN_DIALOG)
        if result:
            selected = result[0] if isinstance(result, (tuple, list)) else result
            self.folder_selected(dialog_type, str(selected))

    def process_folder_dialog(self, folder_path, continue_existing=False):
        """Public JS bridge entry point used by the folder drop zone."""
        mode = 'continue_folder' if continue_existing else 'anonymize_folder'
        self.folder_selected(mode, folder_path)

    @staticmethod
    def _paths_from_native_drop(event):
        """Extract absolute paths added by pywebview's native DnD bridge."""
        if not isinstance(event, dict):
            return []
        transfer = event.get('dataTransfer')
        if not isinstance(transfer, dict):
            return []
        paths = []
        for item in transfer.get('files', []):
            if not isinstance(item, dict):
                continue
            value = item.get('pywebviewFullPath') or item.get('path')
            if isinstance(value, str) and os.path.isabs(value) and value not in paths:
                paths.append(value)
        return paths

    def _folder_drop_mode(self):
        if not self._window:
            return 'anonymize_folder'
        try:
            should_continue = self._window.evaluate_js(
                "Boolean(document.getElementById('cb-continue-folder')?.checked)"
            )
        except Exception:
            should_continue = False
        return 'continue_folder' if should_continue else 'anonymize_folder'

    def _handle_native_drop(self, dialog_type, event):
        paths = self._paths_from_native_drop(event)
        if not paths:
            if self._window:
                ui_bridge.ui_set_folder_status(
                    self._window,
                    'Не удалось получить полный путь. Перетащите объект из Finder или выберите его кнопкой.',
                    'error',
                )
            return
        effective_type = self._folder_drop_mode() if dialog_type == 'anonymize_folder' else dialog_type
        self.files_dropped(effective_type, paths)

    def _bind_native_drop_handlers(self):
        """Use pywebview's native bridge so macOS supplies absolute folder paths."""
        if self._native_drop_bound or not self._window:
            return
        dom = getattr(self._window, 'dom', None)
        if dom is None or not hasattr(dom, 'get_element'):
            return
        bindings = (
            ('#dropzone-anonymize', 'dropzone-anonymize', 'anonymize_docs'),
            ('#folder-dropzone', 'folder-dropzone', 'anonymize_folder'),
            ('#dropzone-restore-doc', 'dropzone-restore-doc', 'restore_doc'),
            ('#dropzone-restore-json', 'dropzone-restore-json', 'restore_json'),
        )
        for selector, bridge_key, dialog_type in bindings:
            try:
                element = dom.get_element(selector)
                if element is None or not hasattr(element.events, 'drop'):
                    continue
                handler = lambda event, kind=dialog_type: self._handle_native_drop(kind, event)
                element.events.drop += handler
                self._native_drop_handlers.append((element, handler))
                self._window.evaluate_js(
                    "window.__docxdodyrNativeDropBridge = "
                    "window.__docxdodyrNativeDropBridge || {}; "
                    f"window.__docxdodyrNativeDropBridge['{bridge_key}'] = true"
                )
            except Exception as exc:
                logger.warning(
                    "Нативный drag-and-drop недоступен для %s: %s",
                    selector,
                    type(exc).__name__,
                )
        self._native_drop_bound = bool(self._native_drop_handlers)

    def folder_selected(self, dialog_type, folder_path):
        if not folder_path:
            return
        folder_path = str(folder_path)
        if dialog_type not in ('anonymize_folder', 'continue_folder', 'folder'):
            return
        continue_existing = dialog_type == 'continue_folder'
        output_dir = os.path.join(folder_path, 'Обезличенные документы')
        if self._window:
            status_prefix = 'Продолжение комплекта' if continue_existing else 'Выбрана папка'
            ui_bridge.ui_set_folder_status(self._window, f"{status_prefix}: {folder_path}", 'working')
            ui_bridge.ui_set_folder_output_hint(self._window, output_dir)
        process_folder = getattr(self._api, 'process_folder', None)
        if callable(process_folder):
            def _process_folder():
                try:
                    if continue_existing:
                        result = process_folder(
                            folder_path,
                            output_dir_name='Обезличенные документы',
                            continue_existing=True,
                        )
                    else:
                        try:
                            result = process_folder(
                                folder_path,
                                output_dir_name='Обезличенные документы',
                                continue_existing=False,
                            )
                        except TypeError:
                            # Backward-compatible with the first folder API,
                            # which accepted only the source directory.
                            result = process_folder(folder_path)

                    if isinstance(result, dict):
                        processed = int(result.get('processed_count', 0) or 0)
                        errors = int(result.get('error_count', 0) or 0)
                    else:
                        processed = int(getattr(result, 'processed_count', 0) or 0)
                        errors = int(getattr(result, 'error_count', 0) or 0)
                    if self._window:
                        if errors:
                            message = (
                                f'Обработка завершена частично: успешно {processed}, ошибок {errors}'
                                if processed
                                else f'Обработка не выполнена: ошибок {errors}'
                            )
                            ui_bridge.ui_set_folder_status(self._window, message, 'error')
                        else:
                            action = 'Комплект продолжен' if continue_existing else 'Обработка папки завершена'
                            ui_bridge.ui_set_folder_status(
                                self._window,
                                f'{action}: обработано файлов {processed}',
                                'ready',
                            )
                        ui_bridge.ui_set_global_progress(self._window, False)
                        if processed and os.path.exists(output_dir):
                            app_paths.open_folder_in_file_manager(output_dir)
                except Exception as exc:
                    logger.exception("Ошибка пакетной обработки папки %s", folder_path)
                    if self._window:
                        ui_bridge.ui_set_folder_status(self._window, f"Ошибка: {exc}", 'error')
                        ui_bridge.ui_set_global_progress(self._window, False)

            threading.Thread(target=_process_folder, daemon=True).start()
            return

        # Compatibility with older backend builds.  The current BackendApi
        # exposes process_folder; this branch keeps the UI usable while an
        # older binary is being upgraded and never includes our output folder.
        if continue_existing:
            if self._window:
                ui_bridge.ui_set_folder_status(self._window, 'Режим продолжения недоступен в этой версии приложения', 'error')
            return
        root = os.path.abspath(folder_path)
        supported = {'.docx', '.docm', '.xlsx', '.pdf'}
        files = []
        for current_root, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d != 'Обезличенные документы']
            for name in names:
                path = os.path.join(current_root, name)
                if os.path.splitext(name)[1].lower() in supported:
                    files.append(path)
        if files and hasattr(self._api, 'process_files'):
            def _process_legacy_files():
                try:
                    self._api.process_files(sorted(files))
                    if self._window:
                        ui_bridge.ui_set_folder_status(self._window, 'Обработка папки завершена', 'ready')
                        ui_bridge.ui_set_global_progress(self._window, False)
                except Exception as exc:
                    logger.exception("Ошибка пакетной обработки папки %s", folder_path)
                    if self._window:
                        ui_bridge.ui_set_folder_status(self._window, f"Ошибка: {exc}", 'error')
                        ui_bridge.ui_set_global_progress(self._window, False)

            threading.Thread(target=_process_legacy_files, daemon=True).start()
        elif self._window:
            ui_bridge.ui_set_folder_status(self._window, 'В папке нет поддерживаемых документов', 'error')

    def _resolve_dropped_path(self, path_str, related_paths=None):
        if not path_str:
            return path_str
        p = Path(path_str)
        if p.is_absolute() and p.exists():
            return str(p)
        name = p.name
        candidates_dirs = []
        if related_paths:
            for rel in related_paths:
                rel_p = Path(rel)
                if rel_p.is_absolute() and rel_p.exists():
                    parent = rel_p.parent if rel_p.is_file() else rel_p
                    candidates_dirs.append(parent)
                    curr = parent
                    for _ in range(4):
                        if curr.parent != curr:
                            curr = curr.parent
                            candidates_dirs.append(curr)
        if getattr(self._api, '_last_output_dir', None):
            candidates_dirs.append(Path(self._api._last_output_dir))
            candidates_dirs.append(Path(self._api._last_output_dir).parent)
        if getattr(self._api, 'staged_folder', None):
            candidates_dirs.append(Path(self._api.staged_folder))

        for c_dir in candidates_dirs:
            direct = c_dir / name
            if direct.is_file():
                return str(direct.resolve())
            try:
                for sub in c_dir.iterdir():
                    if sub.is_file() and sub.name.lower() == name.lower():
                        return str(sub.resolve())
            except OSError:
                pass
        return path_str

    def close_window(self):
        try:
            if self._window:
                self._window.destroy()
        except Exception:
            pass
        import os
        os._exit(0)

    def files_dropped(self, dialog_type, paths):
        if not paths: return
        if dialog_type == 'anonymize_docs':
            folders = [str(path) for path in paths if os.path.isdir(path)]
            files = [str(path) for path in paths if not os.path.isdir(path)]
            if folders:
                if len(folders) == 1 and not files:
                    self.folder_selected('anonymize_folder', folders[0])
                elif self._window:
                    ui_bridge.ui_alert(
                        self._window,
                        'Перетащите одну папку отдельно либо только файлы.'
                    )
                return
            self._api.process_files(files)
        elif dialog_type in ('hidden_inspect', 'hidden_data'):
            try:
                result = self._api.inspect_hidden_file(paths[0])
                if self._window:
                    ui_bridge.ui_show_hidden_data_modal(self._window, result)
            except Exception:
                if self._window:
                    ui_bridge.ui_alert(self._window, 'Не удалось безопасно проверить файл')
        elif dialog_type in ('anonymize_folder', 'continue_folder', 'folder'):
            folders = [str(path) for path in paths if os.path.isdir(path)]
            if len(paths) != 1 or len(folders) != 1:
                if self._window:
                    ui_bridge.ui_set_folder_status(
                        self._window,
                        'Для папочного режима перетащите одну папку.',
                        'error',
                    )
                return
            self.folder_selected(dialog_type, folders[0])
        elif dialog_type == 'restore_doc':
            resolved = [self._resolve_dropped_path(p, getattr(self._api, 'restore_json_paths', [])) for p in paths]
            pdf_docs = [p for p in resolved if Path(p).suffix.lower() == '.pdf']
            if pdf_docs and self._window:
                ui_bridge.ui_alert(
                    self._window,
                    'Формат PDF является необратимым форматом публикации и не подлежит восстановлению.\n\n'
                    'Для восстановления данных по дешифратору выберите исходно обработанный файл Word (.docx) или Excel (.xlsx).'
                )
                resolved = [p for p in resolved if Path(p).suffix.lower() != '.pdf']
                if not resolved:
                    self._api.restore_doc_paths = []
                    ui_bridge.ui_update_dropzone_text(self._window, 'lbl-restore-doc', 'Перетащите сюда или нажмите для выбора')
                    return
            self._api.restore_doc_paths = resolved
            if self._window:
                names = [os.path.basename(p) for p in resolved]
                lbl = f'Выбрано: {names[0]}' if len(names) == 1 else f'Выбрано: {len(names)} файлов'
                ui_bridge.ui_update_dropzone_text(self._window, 'lbl-restore-doc', lbl)
        elif dialog_type == 'restore_json':
            resolved = [self._resolve_dropped_path(p, getattr(self._api, 'restore_doc_paths', [])) for p in paths]
            self._api.restore_json_paths = resolved
            if self._window:
                names = [os.path.basename(p) for p in resolved]
                lbl = f'Дешифратор: {names[0]}' if len(names) == 1 else f'Выбрано: {len(names)} файлов'
                ui_bridge.ui_update_dropzone_text(self._window, 'lbl-restore-json', lbl)

    def run_action(self, action):
        if action == 'pdf_ocr':
            if hasattr(self._api, 'run_pdf_ocr_only'):
                self._api.run_pdf_ocr_only()

    def run_restore(self):
        if hasattr(self._api, '_run_restore'):
            threading.Thread(target=self._api._run_restore, daemon=True).start()

    def set_restore_auto_decoder(self, enabled):
        """Persist the UI choice for ancestor-folder decoder discovery."""
        enabled = bool(enabled)
        self._api.auto_decoder = enabled
        self._api.settings['auto_decoder'] = enabled
        if hasattr(self._api, '_save_settings'):
            self._api._save_settings()

    def clear_restore_decoder(self):
        """Forget a manually selected decoder when explicitly requested."""
        self._api.restore_json_paths = []

    # --- Настройки: списки ---
    def get_lists(self):
        return self._api.get_lists()

    def save_lists(self, lists_dict):
        return self._api.save_lists(lists_dict)

    # --- Настройки: обезличивание ---
    def get_placeholder_settings(self):
        return self._api.get_placeholder_settings()

    def save_placeholder_settings(self, data):
        return self._api.save_placeholder_settings(data)

    def get_qwen_settings(self):
        return self._api.get_qwen_settings()

    def save_qwen_settings(self, data):
        return self._api.save_qwen_settings(data)

    # --- Управление локальной моделью Qwen (Model Manager) ---
    def get_qwen_model_info(self):
        return self._api.get_qwen_model_info()

    def install_qwen_model(self, consent=False):
        return self._api.install_qwen_model(consent=consent)

    def import_qwen_model_bundle(self, bundle_path='', consent=False):
        return self._api.import_qwen_model_bundle(bundle_path=bundle_path, consent=consent)

    def cancel_qwen_model_operation(self):
        return self._api.cancel_qwen_model_operation()

    def delete_qwen_model(self):
        return self._api.delete_qwen_model()

    def verify_qwen_model_integrity(self):
        return self._api.verify_qwen_model_integrity()

    def open_output_folder_in_explorer(self, folder_path=''):
        return self._api.open_output_folder_in_explorer(folder_path=folder_path)

    def create_claim_document(self, manifest_path, draft_text, output_name='Исковое заявление.docx'):
        return self._api.create_claim_document(manifest_path, draft_text, output_name)

    def get_review_findings(self, entity_type='', document_ref='', status='', min_confidence=None, limit=100, offset=0):
        return self._api.get_review_findings(entity_type, document_ref, status, min_confidence, limit, offset)

    def decide_review_finding(self, finding_id, status, note=''):
        return self._api.decide_review_finding(finding_id, status, note)

    def batch_decide_review_findings(self, finding_ids=None, status='accepted', entity_type='', document_ref='', min_confidence=None):
        return self._api.batch_decide_review_findings(finding_ids, status, entity_type, document_ref, min_confidence)

    def get_review_finding_preview(self, finding_id):
        return self._api.get_review_finding_preview(finding_id)

    def rebuild_rejected_finding(self, finding_id):
        return self._api.rebuild_rejected_finding(finding_id)

    def clear_review_findings(self):
        return self._api.clear_review_findings()

    def open_review_window(self):
        """Открывает отдельное полноценное окно для проверки сущностей."""
        try:
            if hasattr(self, '_review_window') and self._review_window is not None:
                try:
                    self._review_window.show()
                    self._review_window.restore()
                    return {"success": True, "action": "focused"}
                except Exception:
                    self._review_window = None

            review_html_path = str(app_paths.get_web_dir() / 'review_window.html')
            self._review_window = webview.create_window(
                title='Проверка найденных сущностей — DOCXдодыр',
                url=review_html_path,
                js_api=self,
                width=1150,
                height=820,
                min_size=(800, 600),
                background_color='#020617'
            )
            def _on_review_closed():
                self._review_window = None
            self._review_window.events.closed += _on_review_closed
            return {"success": True, "action": "opened"}
        except Exception as e:
            logger.error(f"Не удалось открыть отдельное окно проверки: {e}", exc_info=True)
            import webbrowser
            review_html_path = str(app_paths.get_web_dir() / 'review_window.html')
            webbrowser.open(f"file://{review_html_path}")
            return {"success": True, "action": "browser"}

    # --- Управление скрытым содержимым ---
    def inspect_hidden_file(self, source_path):
        return self._api.inspect_hidden_file(source_path)

    def get_hidden_data_policy(self):
        return self._api.get_hidden_data_policy()

    def save_hidden_data_policy(self, actions, confirm_destructive=False):
        return self._api.save_hidden_data_policy(actions, confirm_destructive)

    def apply_hidden_data_policy(self, inspection_token, actions=None, confirm_destructive=False, batch_id=None):
        return self._api.apply_hidden_data_policy(inspection_token, actions, confirm_destructive, batch_id)

    def get_app_info(self):
        import platform
        info = self._api.get_app_info()
        info["architecture"] = platform.machine()
        info["user_data_dir"] = str(app_paths.get_user_data_dir())
        return info

    def get_capability_matrix(self):
        return self._api.get_capability_matrix()

    def get_safe_diagnostic_report(self):
        return self._api.get_safe_diagnostic_report()

    def get_interrupted_batch_info(self):
        return self._api.get_interrupted_batch_info()

    def discard_interrupted_batch(self):
        return self._api.discard_interrupted_batch()

    def resume_interrupted_batch(self):
        return self._api.resume_interrupted_batch()

    def cancel_processing(self):
        """Запрашивает отмену активной операции."""
        logger.info("Пользователь запросил отмену операции через UI.")
        if hasattr(self._api, 'cancel_processing'):
            self._api.cancel_processing()

    def shutdown(self):
        """Корректно останавливает фоновые задачи при завершении работы приложения."""
        logger.info("Завершение работы приложения...")
        if hasattr(self._api, 'shutdown'):
            self._api.shutdown()
        else:
            if hasattr(self._api, '_cancel_event') and self._api._cancel_event is not None:
                self._api._cancel_event.set()
            if hasattr(self._api, '_model_cancel_event') and self._api._model_cancel_event is not None:
                self._api._model_cancel_event.set()
            if hasattr(self._api, 'wait_for_tasks'):
                self._api.wait_for_tasks(timeout=0.2)

    # --- Прочее ---
    def show_settings(self):
        if self._window: ui_bridge.ui_show_list_settings_modal(self._window)

    def show_placeholder_settings(self):
        if self._window: ui_bridge.ui_show_placeholder_settings_modal(self._window)

    def open_telegram(self):
        import webbrowser
        webbrowser.open("https://t.me/pro_servitude")

    def open_author_telegram(self):
        import webbrowser
        webbrowser.open("https://t.me/aebeloglazov")

    def open_bug_email(self):
        import webbrowser
        webbrowser.open("mailto:pro.servitude@gmail.com")

    def show_support(self):
        if self._window:
            ui_bridge.ui_show_support_modal(self._window, "https://www.tinkoff.ru/rm/r_cNLDGIyQuz.TzrqnfAGGL/G4xqW19880")


def _safe_cli_print(text: str) -> None:
    """Безопасный вывод в консоль для десктопного приложения Windows/macOS."""
    if "--report-file" in sys.argv:
        from pathlib import Path
        import tempfile
        import os
        index = sys.argv.index("--report-file")
        if index + 1 >= len(sys.argv):
            raise SystemExit(2)
        target = Path(sys.argv[index + 1]).absolute()
        fd, temporary = tempfile.mkstemp(prefix=".diagnostic-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return
    if sys.stdout is not None:
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
            return
        except Exception:
            pass
    if sys.stderr is not None:
        try:
            sys.stderr.write(text + "\n")
            sys.stderr.flush()
            return
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            if kernel32.AttachConsole(-1):
                with open("CONOUT$", "w", encoding="utf-8") as conout:
                    conout.write(text + "\n")
                    conout.flush()
        except Exception:
            pass


def handle_cli_arguments() -> bool:
    """Обрабатывает аргументы командной строки (--version, --self-test, --capabilities, --diagnostics, --help).

    Возвращает True, если аргумент был обработан и приложение должно завершиться без запуска GUI.
    """
    if len(sys.argv) <= 1:
        return False

    args = sys.argv[1:]
    if "-v" in args or "--version" in args:
        _safe_cli_print(f"{version.APP_NAME} {version.APP_VERSION}")
        return True

    if "-h" in args or "--help" in args:
        help_text = (
            f"{version.APP_NAME} v{version.APP_VERSION}\n"
            "Использование: DOCXdodyr [ОПЦИИ] [ФАЙЛ_ИЛИ_ПАПКА]\n\n"
            "Опции:\n"
            "  -v, --version             Показать версию приложения\n"
            "  -h, --help                Показать справку по опциям\n"
            "  -a, --anonymize <путь>    Обезличить файл или папку с настройками по умолчанию\n"
            "  -r, --restore <путь>      Восстановить файл или папку по найденному дешифратору\n"
            "  --headless                Выполнить операцию без запуска графического интерфейса\n"
            "  --no-open-output         Не открывать папку результата (для Quick Action/Service)\n"
            "  --register-context-menu   Зарегистрировать пункт в контекстном меню Windows\n"
            "  --unregister-context-menu Удалить пункт из контекстного меню Windows\n"
            "  --context-menu-status     Проверить регистрацию контекстного меню\n"
            "  --self-test               Запустить самотестирование упакованных компонентов и путей\n"
            "  --capabilities            Вывести матрицу возможностей компонентов в формате JSON\n"
            "  --diagnostics             Вывести безопасный диагностический отчёт в формате JSON\n"
        )
        _safe_cli_print(help_text)
        return True

    if "--register-context-menu" in args:
        try:
            import json
            import windows_context_menu
            res = windows_context_menu.register_windows_context_menu()
            _safe_cli_print(json.dumps(res, ensure_ascii=False, indent=2))
        except Exception as exc:
            _safe_cli_print(json.dumps({"status": "error", "message": str(exc)}))
            raise SystemExit(1)
        return True

    if "--unregister-context-menu" in args:
        try:
            import json
            import windows_context_menu
            res = windows_context_menu.unregister_windows_context_menu()
            _safe_cli_print(json.dumps(res, ensure_ascii=False, indent=2))
        except Exception as exc:
            _safe_cli_print(json.dumps({"status": "error", "message": str(exc)}))
            raise SystemExit(1)
        return True

    if "--context-menu-status" in args:
        try:
            import json
            import windows_context_menu
            is_reg = windows_context_menu.is_windows_context_menu_registered()
            _safe_cli_print(json.dumps({"registered": is_reg}, indent=2))
        except Exception as exc:
            _safe_cli_print(json.dumps({"status": "error", "message": str(exc)}))
            raise SystemExit(1)
        return True

    if ("--anonymize" in args or "-a" in args) and ("--headless" in args or "--no-gui" in args):
        import json
        clean_paths = [
            arg.strip('"\'') for arg in args
            if not arg.startswith('-') and os.path.exists(arg.strip('"\''))
        ]
        if not clean_paths:
            _safe_cli_print(json.dumps({"status": "error", "message": "no_valid_path_specified"}))
            raise SystemExit(1)
        try:
            from backend_api import BackendApi, Worker
            # Keep the frozen CLI compatible with older test doubles and
            # upgraded external backends that predate ``background_mode``;
            # the bundled BackendApi receives the stronger no-Keychain mode.
            try:
                api = BackendApi(background_mode=True)
            except TypeError as exc:
                if "background_mode" not in str(exc):
                    raise
                api = BackendApi()
            api.save_decoder = True
            # Preserve the normal headless behaviour unless the caller asks
            # explicitly to suppress Explorer (the context-menu contract does).
            api.open_output_folder = "--no-open-output" not in args

            results = []
            for target_str in clean_paths:
                target = Path(target_str).resolve()
                if target.is_dir():
                    res = api.process_folder(str(target), recursive=True)
                    result = res.to_dict() if res else None
                    item_status = (
                        "succeeded" if res and res.error_count == 0 else
                        "partial" if res and res.processed_count > 0 else "failed"
                    )
                    results.append({"path": str(target), "type": "folder", "status": item_status, "result": result})
                else:
                    worker = Worker([str(target)], api)
                    worker_result = worker.run() or {"status": "failed", "error_count": 1}
                    results.append({"path": str(target), "type": "file", "result": worker_result,
                                    "status": worker_result.get("status", "failed")})
            successful = all(item.get("status") == "succeeded" for item in results)
            has_success = any(item.get("status") in {"succeeded", "partial"} for item in results)
            overall_status = "ok" if successful else ("partial" if has_success else "error")
            _safe_cli_print(json.dumps({"status": overall_status, "items": results}, ensure_ascii=False, indent=2))
            if not successful:
                raise SystemExit(1)
        except Exception as exc:
            if isinstance(exc, SystemExit):
                raise
            logger.exception("Ошибка при обезличивании в headless-режиме")
            _safe_cli_print(json.dumps({"status": "error", "message": str(exc)}))
            raise SystemExit(1)
        return True

    if ("--restore" in args or "-r" in args) and ("--headless" in args or "--no-gui" in args):
        import json
        clean_paths = [
            arg.strip('"\'') for arg in args
            if not arg.startswith('-') and os.path.exists(arg.strip('"\''))
        ]
        if not clean_paths:
            _safe_cli_print(json.dumps({"status": "error", "message": "no_valid_path_specified"}))
            raise SystemExit(1)
        try:
            from document_restorer import find_decoder_near_document, restore_document
            supported = {'.docx', '.docm', '.xlsx', '.xlsm', '.md'}
            results = []
            for target_str in clean_paths:
                target = Path(target_str).resolve()
                if target.is_dir():
                    files = [
                        Path(root) / f
                        for root, _, names in os.walk(target)
                        for f in names if Path(f).suffix.lower() in supported
                    ]
                else:
                    files = [target]

                restored = []
                errors = []
                for document in files:
                    decoder = find_decoder_near_document(document)
                    if not decoder:
                        errors.append({"path": str(document), "message": "decoder_not_found"})
                        continue
                    try:
                        output = restore_document(document, decoder_path=decoder)
                        restored.append({"path": str(document), "decoder": str(decoder), "output": str(output)})
                    except Exception as exc:
                        errors.append({"path": str(document), "message": str(exc)})

                item_status = (
                    "restored" if restored and not errors else
                    "partial" if restored else "error"
                )
                results.append({"path": str(target), "type": "folder" if target.is_dir() else "file",
                                "status": item_status, "files_count": len(files),
                                "restored": restored, "errors": errors})
            successful = all(item.get("status") == "restored" for item in results)
            has_success = any(item.get("status") in {"restored", "partial"} for item in results)
            overall_status = "ok" if successful else ("partial" if has_success else "error")
            _safe_cli_print(json.dumps({"status": overall_status, "items": results}, ensure_ascii=False, indent=2))
            if not successful:
                raise SystemExit(1)
        except Exception as exc:
            if isinstance(exc, SystemExit):
                raise
            logger.exception("Ошибка при восстановлении в headless-режиме")
            _safe_cli_print(json.dumps({"status": "error", "message": str(exc)}))
            raise SystemExit(1)
        return True

    if "--capabilities" in args:
        try:
            from capabilities import get_system_capability_matrix
            import json
            matrix = get_system_capability_matrix()
            _safe_cli_print(json.dumps(matrix, ensure_ascii=False, indent=2))
        except Exception as exc:
            _safe_cli_print('{"error": "diagnostic_failed"}')
            raise SystemExit(1)
        return True

    if "--diagnostics" in args:
        try:
            from log_sanitizer import get_safe_diagnostic_report
            import json
            diag = get_safe_diagnostic_report()
            _safe_cli_print(json.dumps(diag, ensure_ascii=False, indent=2))
        except Exception as exc:
            _safe_cli_print('{"error": "diagnostic_failed"}')
            raise SystemExit(1)
        return True

    if "--keyring-smoke-test" in args:
        try:
            import json
            from packaged_smoke import run_keyring
            _safe_cli_print(json.dumps(run_keyring()))
        except Exception as exc:
            import json
            _safe_cli_print(json.dumps({"status": "error", "message": "synthetic_keyring_smoke_failed", "error_type": type(exc).__name__}))
            raise SystemExit(1)
        return True

    if "--qwen-smoke-test" in args:
        try:
            import json
            from packaged_qwen_smoke import run
            _safe_cli_print(json.dumps(run()))
        except Exception as exc:
            import json
            _safe_cli_print(json.dumps({"status": "error", "message": "synthetic_qwen_smoke_failed", "error_type": type(exc).__name__}))
            raise SystemExit(1)
        return True

    if "--gui-smoke-test" in args:
        try:
            import json
            from packaged_gui_smoke import run
            _safe_cli_print(json.dumps(run()))
        except Exception as exc:
            import json
            _safe_cli_print(json.dumps({"status": "error", "message": "synthetic_gui_smoke_failed", "error_type": type(exc).__name__}))
            raise SystemExit(1)
        return True

    if "--smoke-test" in args:
        try:
            import json
            from packaged_smoke import run
            _safe_cli_print(json.dumps(run()))
        except Exception:
            _safe_cli_print('{"status": "error", "message": "synthetic_smoke_failed"}')
            raise SystemExit(1)
        return True

    if "--self-test" in args:
        try:
            import json
            from capabilities import get_system_capability_matrix
            from crash_recovery import get_interrupted_batch

            web_dir = app_paths.get_web_dir()
            index_exists = (web_dir / "index.html").exists() if web_dir else False

            if not index_exists:
                raise RuntimeError("Missing UI resources")
            test_res = {
                "status": "ok",
                "app": version.APP_NAME,
                "version": version.APP_VERSION,
                "platform": sys.platform,
                "paths": {
                    "user_data_dir": str(app_paths.get_user_data_dir()),
                    "logs_path": str(app_paths.get_app_log_path()),
                    "web_dir": str(web_dir),
                    "web_index_present": index_exists,
                },
                "interrupted_batch_present": bool(get_interrupted_batch()),
                "capabilities": get_system_capability_matrix(),
            }
            _safe_cli_print(json.dumps(test_res, ensure_ascii=False, indent=2))
        except Exception as exc:
            _safe_cli_print('{"status": "error", "message": "self_test_failed"}')
            raise SystemExit(1)
        return True

    return False


def startup_html_path(is_quick_mode: bool) -> str:
    """HTML стартового окна: компактный прогресс в CLI/контекстном меню, иначе основной UI."""
    filename = "quick_progress.html" if is_quick_mode else "index.html"
    return str(app_paths.get_web_dir() / filename)


def configure_visible_quick_mode(wrapper: ApiWrapper, raw_args: list[str]) -> None:
    """Apply CLI-only side-effect flags to the visible compact workflow."""
    if "--no-open-output" in raw_args:
        wrapper._api.open_output_folder = False


if __name__ == '__main__':
    if handle_cli_arguments():
        sys.exit(0)

    raw_args = sys.argv[1:]
    initial_args = extract_cli_paths(raw_args)
    auto_start = ("--anonymize" in raw_args or "-a" in raw_args)
    restore_mode = ("--restore" in raw_args or "-r" in raw_args)
    is_quick_mode = bool(auto_start or restore_mode)
    wrapper = ApiWrapper(startup_files=initial_args, auto_start=auto_start, restore_mode=restore_mode)
    # Visible Finder Quick Actions use the compact progress window but must
    # not steal focus again by opening the result folder.
    configure_visible_quick_mode(wrapper, raw_args)
    html_path = startup_html_path(is_quick_mode)

    if is_quick_mode:
        quick_title = "DOCXдодыр — Восстановление" if restore_mode else "DOCXдодыр — Обезличивание"
        window_kwargs = {
            "title": quick_title,
            "url": html_path,
            "js_api": wrapper,
            "width": 480,
            "height": 260,
            "resizable": False,
            "background_color": '#090d16',
        }
    else:
        # Считываем ранее сохранённые размеры и координаты окна
        saved_geom = wrapper.get_window_geometry()
        window_kwargs = {
            "title": version.APP_WINDOW_TITLE,
            "url": html_path,
            "js_api": wrapper,
            "width": saved_geom.get("width", 840),
            "height": saved_geom.get("height", 740),
            "min_size": (780, 700),
            "background_color": '#020617',
        }
        if "x" in saved_geom and "y" in saved_geom:
            window_kwargs["x"] = saved_geom["x"]
            window_kwargs["y"] = saved_geom["y"]

    # Create the webview window
    window = webview.create_window(**window_kwargs)
    
    wrapper.set_window(window)

    def _save_geometry():
        if is_quick_mode:
            return
        try:
            w = getattr(window, "width", None)
            h = getattr(window, "height", None)
            x = getattr(window, "x", None)
            y = getattr(window, "y", None)
            if w and h:
                wrapper.save_window_geometry(w, h, x, y)
        except Exception:
            pass

    def _on_closing():
        _save_geometry()
        wrapper.shutdown()

    def _on_closed():
        _save_geometry()
        wrapper.shutdown()
        import os
        os._exit(0)

    window.events.closing += _on_closing
    window.events.closed += _on_closed
    
    try:
        webview.start(debug=False)
    except Exception as e:
        print(f"Ошибка запуска: {e}")
    finally:
        _save_geometry()
        wrapper.shutdown()
        os._exit(0)
