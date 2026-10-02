# -*- coding: utf-8 -*-
"""Главное окно приложения DOCXдодыр."""
from __future__ import annotations

import os
import re
import json
import logging
import traceback
import shutil
import secrets
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional
import webview
import threading

logger = logging.getLogger(__name__)


class _BackendPrivacyFilter(logging.Filter):
    """Удаляет потенциальные ПДн и конфиденциальные данные из аргументов,
    сохраняя текст сообщений для диагностики.
    """
    _PII_PATTERN = re.compile(
        r"\b\d{10,12}\b|\b\d{3}-\d{3}\b|\b\d{2}\s?\d{2}\s?\d{6}\b"
    )

    def filter(self, record):
        # Не допускаем утечки сырых фрагментов документов через трассировки стека
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None

        if isinstance(record.args, (tuple, list)):
            new_args = []
            for a in record.args:
                if isinstance(a, str):
                    if "SENTINEL" in a or "PRIVATE" in a:
                        new_args.append("[REDACTED]")
                    else:
                        new_args.append(self._PII_PATTERN.sub("[REDACTED]", a))
                else:
                    new_args.append(a)
            record.args = tuple(new_args)
        elif isinstance(record.args, dict):
            new_args = {}
            for k, v in record.args.items():
                if isinstance(v, str):
                    if "SENTINEL" in v or "PRIVATE" in v:
                        new_args[k] = "[REDACTED]"
                    else:
                        new_args[k] = self._PII_PATTERN.sub("[REDACTED]", v)
                else:
                    new_args[k] = v
            record.args = new_args
        return True


logger.addFilter(_BackendPrivacyFilter())


def _private_status(*_args, **_kwargs):
    """Legacy console diagnostics are intentionally not exported with PII."""
    return None
import config
import app_paths
import state_migration
import version
import ui_bridge
import docx_compat
from docx import Document
from validation import is_sufficient_address
from placeholders import (
    generate_placeholders, PLACEHOLDER_TEMPLATES, UNIFIED_EXCLUSIONS,
)
import pdf_convert
from pdf_convert import (
    convert_docx_text_to_pdf,
    convert_to_pdf,
    pdf_to_text_auto_detailed,
)
from entity_utils import get_unique_filename
from legal_pullenti import (
    EntitySpan,
    apply_replacements,
    initialize_ner,
    iter_pullenti_spans,
    refine_composite_spans,
    select_non_overlapping,
)
from detection_engine import UnifiedDetectionPipeline
from entity_registry import EntityResolver
from qwen_postprocessor import QwenPlaceholderPostprocessor, QwenPostprocessorSettings
from table_entity_context import (
    TableCellContext,
    build_table_contexts,
    is_pure_field_label,
    project_span_to_value,
)
from pdf_convert import reconstruct_structured_paragraphs


def build_ocr_span_map(text, ocr_result):
    """Map OCR regions to offsets without storing their text in review state."""
    mapped = []
    cursor = 0
    for page in getattr(ocr_result, "pages", ()):
        for region in getattr(page, "regions", ()):
            region_text = str(getattr(region, "text", "") or "")
            if not region_text:
                continue
            start = text.find(region_text, cursor)
            if start < 0:
                start = text.find(region_text)
            if start < 0:
                continue
            end = start + len(region_text)
            x1, y1, x2, y2 = tuple(getattr(region, "bbox", (0, 0, 1, 1)))
            mapped.append({
                "start": start, "end": end,
                "position": {
                    "scope": "pdf", "kind": "ocr",
                    # OCR uses one-based pages; the review/PDF APIs use zero-based.
                    "page": max(0, int(getattr(page, "page", 1)) - 1),
                    "bbox": [float(x1), float(y1), max(0.0, float(x2) - float(x1)), max(0.0, float(y2) - float(y1))],
                    "width": int(getattr(page, "width", 0)),
                    "height": int(getattr(page, "height", 0)),
                },
                "confidence": max(0.0, min(1.0, float(getattr(region, "confidence", 0.0)))),
            })
            cursor = end
    return mapped


def ocr_position_for_span(span_map, start, end, fallback):
    """Return geometry/confidence of the OCR line intersecting a NER span."""
    for item in span_map or ():
        if int(item["start"]) < int(end) and int(start) < int(item["end"]):
            return dict(item["position"]), float(item["confidence"])
    return dict(fallback), None


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = "{" + _W_NS + "}"


_PROTECTED_PARENTS = {
    _W + "hyperlink",
    _W + "fldSimple",
    _W + "sdt",
    _W + "ins",
    _W + "del",
    _W + "smartTag",
}


def _is_inside_protected(node, p_elem):
    """Проверяет, находится ли w:t внутри защищённого элемента."""
    getparent = getattr(node, "getparent", None)
    if not callable(getparent):
        return False
    parent = node.getparent()
    while parent is not None and parent is not p_elem:
        if getattr(parent, "tag", None) in _PROTECTED_PARENTS:
            return True
        parent = getattr(parent, "getparent", lambda: None)()
    return False


def replace_spans_in_paragraph_xml(para_obj, replacements):
    """Применяет список замен (start, end, placeholder) непосредственно к w:t узлам параграфа,
    полностью сохраняя все runs, стили (жирный, курсив, шрифт, размер, цвет), списки,
    гиперссылки и свойства абзаца без разрушения разметки.
    """
    if not replacements:
        return False
    p_elem = getattr(para_obj, "_element", None)
    if p_elem is None:
        return False
    nodes = [
        node for node in p_elem.iter(_W + "t")
        if node.text is not None and not _is_inside_protected(node, p_elem)
    ]
    if not nodes:
        return False
    full_text = "".join(node.text for node in nodes)
    offsets = []
    cursor = 0
    for node in nodes:
        node_len = len(node.text)
        offsets.append((node, cursor, cursor + node_len))
        cursor += node_len

    sorted_repls = sorted(replacements, key=lambda r: (r[0], r[1]), reverse=True)
    for r_start, r_end, placeholder in sorted_repls:
        if not (0 <= r_start <= r_end <= len(full_text)):
            continue
        touched = []
        for idx, (node, n_start, n_end) in enumerate(offsets):
            if n_start <= r_end and n_end >= r_start and (n_start < r_end or (n_start == n_end == r_start)):
                touched.append((idx, node, n_start, n_end))
        if not touched:
            if offsets:
                touched.append((len(offsets)-1, offsets[-1][0], offsets[-1][1], offsets[-1][2]))
            else:
                continue
        if len(touched) == 1:
            idx, node, n_start, n_end = touched[0]
            local_start = max(0, min(r_start - n_start, len(node.text)))
            local_end = max(0, min(r_end - n_start, len(node.text)))
            old_node_text = node.text
            new_node_text = old_node_text[:local_start] + placeholder + old_node_text[local_end:]
            node.text = new_node_text
            if new_node_text.startswith(" ") or new_node_text.endswith(" "):
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            length_delta = len(new_node_text) - len(old_node_text)
            offsets[idx] = (node, n_start, n_start + len(new_node_text))
            for next_idx in range(idx + 1, len(offsets)):
                n, s, e = offsets[next_idx]
                offsets[next_idx] = (n, s + length_delta, e + length_delta)
        else:
            first_idx, first_node, f_start, f_end = touched[0]
            last_idx, last_node, l_start, l_end = touched[-1]
            f_local_start = max(0, min(r_start - f_start, len(first_node.text)))
            l_local_end = max(0, min(r_end - l_start, len(last_node.text)))
            old_first_text = first_node.text
            old_last_text = last_node.text
            new_first_text = old_first_text[:f_local_start] + placeholder
            new_last_text = old_last_text[l_local_end:]
            first_node.text = new_first_text
            if new_first_text.startswith(" ") or new_first_text.endswith(" "):
                first_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            last_node.text = new_last_text
            if new_last_text.startswith(" ") or new_last_text.endswith(" "):
                last_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            for mid_idx, mid_node, _, _ in touched[1:-1]:
                mid_node.text = ""
            new_offsets = []
            cur = 0
            for n, _, _ in offsets:
                n_l = len(n.text or "")
                new_offsets.append((n, cur, cur + n_l))
                cur += n_l
            offsets = new_offsets
    return True


def replace_diff_in_paragraph_xml(para_obj, original_text, cleaned_text):
    """Сравнивает original_text и cleaned_text и выполняет точечную замену в w:t узлах."""
    if original_text == cleaned_text:
        return True
    p_elem = getattr(para_obj, "_element", None)
    nodes = [node for node in p_elem.iter(_W + "t") if node.text is not None] if p_elem is not None else []
    if len(nodes) == 1 and not getattr(para_obj, "runs", None):
        nodes[0].text = cleaned_text
        return True
    if not nodes:
        para_obj.text = cleaned_text
        return True
    prefix_len = 0
    while prefix_len < len(original_text) and prefix_len < len(cleaned_text) and original_text[prefix_len] == cleaned_text[prefix_len]:
        prefix_len += 1
    suffix_len = 0
    while suffix_len < (len(original_text) - prefix_len) and suffix_len < (len(cleaned_text) - prefix_len) and original_text[len(original_text) - 1 - suffix_len] == cleaned_text[len(cleaned_text) - 1 - suffix_len]:
        suffix_len += 1
    orig_mid_end = len(original_text) - suffix_len
    clean_mid_end = len(cleaned_text) - suffix_len
    replacements = [(prefix_len, orig_mid_end, cleaned_text[prefix_len:clean_mid_end])]
    return replace_spans_in_paragraph_xml(para_obj, replacements)


EXCLUSIONS_FILENAME = config.EXCLUSIONS_FILENAME
REPLACEMENTS_FILENAME = config.REPLACEMENTS_FILENAME
PYMORPHY3_AVAILABLE = getattr(config, "PYMORPHY3_AVAILABLE", False)
morph3 = getattr(config, "morph3", None)
PDF_CONVERSION_AVAILABLE = pdf_convert.PDF_CONVERSION_AVAILABLE
# Проверка телефонов (исправляет текущую ошибку)

PHONENUMBERS_AVAILABLE = bool(getattr(pdf_convert, "PHONENUMBERS_AVAILABLE", False))

DOCX2PDF_AVAILABLE = bool(getattr(pdf_convert, "DOCX2PDF_AVAILABLE", False))
_PULLENTI_INITIALIZED = False
_PULLENTI_INIT_LOCK = threading.Lock()
LIBREOFFICE_AVAILABLE = shutil.which('libreoffice') is not None or shutil.which('soffice') is not None
try:
    from pullenti.ner.ProcessorService import ProcessorService
    from pullenti.ner.SourceOfAnalysis import SourceOfAnalysis
    PULLENTI_AVAILABLE = True
except ImportError:
    ProcessorService = None
    SourceOfAnalysis = None
    PULLENTI_AVAILABLE = False


def parse_custom_replacement_rule(rule: Any) -> tuple[str, str] | None:
    """Парсит правило пользовательской замены в кортеж (source, replacement)."""
    if isinstance(rule, (tuple, list)) and len(rule) == 2:
        src, dst = str(rule[0]).strip(), str(rule[1]).strip()
        return (src, dst) if src else None
    if isinstance(rule, dict):
        for k, v in rule.items():
            src, dst = str(k).strip(), str(v).strip()
            if src:
                return (src, dst)
        return None
    if isinstance(rule, str):
        rule_str = rule.strip()
        if not rule_str or rule_str.startswith("#"):
            return None
        for delim in ("=>", "->", "=", ":"):
            if delim in rule_str:
                parts = rule_str.split(delim, 1)
                src, dst = parts[0].strip(), parts[1].strip()
                if src:
                    return (src, dst)
        return None
    return None


class UnifiedOperationManager:
    """Менеджер состояний и фоновых задач для операций DOCXдодыр."""

    def __init__(self, backend_api=None):
        self._backend = backend_api
        self._lock = threading.Lock()
        self._state: str = "idle"  # idle | running | cancelling | succeeded | partial | failed
        self._current_op: str | None = None
        self._op_description: str = ""
        self._active_threads: list[threading.Thread] = []
        self._active_token: str | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def current_operation(self) -> str | None:
        with self._lock:
            return self._current_op

    def is_busy(self) -> bool:
        with self._lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            if self._state in ("running", "cancelling") and not self._active_threads and self._active_token is None:
                self._state = "idle"
                self._current_op = None
            return self._state in ("running", "cancelling")

    def start_operation_token(self, op_type: str, description: str = "") -> str | None:
        """Атомарно начинает операцию с уникальным токеном владения."""
        with self._lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            if self._state in ("running", "cancelling") and (self._active_threads or self._active_token is not None):
                msg = f"Операция '{self._current_op or 'обработка'}' уже выполняется. Дождитесь её завершения."
                logger.warning("Попытка запуска операции %s при активной %s", op_type, self._current_op)
                if self._backend and getattr(self._backend, "_window", None):
                    ui_bridge.ui_alert(self._backend._window, f"Внимание: {msg}")
                return None
            token = uuid.uuid4().hex
            self._state = "running"
            self._current_op = op_type
            self._op_description = description
            self._active_token = token
            return token

    def start_operation(self, op_type: str, thread: threading.Thread | None = None, description: str = "") -> bool:
        with self._lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            if self._state in ("running", "cancelling") and (self._active_threads or self._active_token is not None):
                msg = f"Операция '{self._current_op or 'обработка'}' уже выполняется. Дождитесь её завершения."
                logger.warning("Попытка запуска операции %s при активной %s", op_type, self._current_op)
                if self._backend and getattr(self._backend, "_window", None):
                    ui_bridge.ui_alert(self._backend._window, f"Внимание: {msg}")
                return False
            self._state = "running"
            self._current_op = op_type
            self._op_description = description
            self._active_token = uuid.uuid4().hex
            if thread is not None:
                self._active_threads.append(thread)
            return True

    def register_thread(self, thread: threading.Thread):
        with self._lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            self._active_threads.append(thread)

    def cancel_operation(self):
        with self._lock:
            if self._state == "running":
                self._state = "cancelling"
            logger.info("Запрошена отмена операции: %s", self._current_op)

    def finish_operation_token(self, token: str | None, status: str = "succeeded") -> bool:
        with self._lock:
            if token is not None and self._active_token is not None and token != self._active_token:
                logger.warning("Попытка завершить операцию чужим токеном")
                return False
            self._active_token = None
            self._current_op = None
            self._op_description = ""
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            self._state = status
            return True

    def finish_operation(self, status: str = "succeeded"):
        with self._lock:
            self._active_token = None
            self._current_op = None
            self._op_description = ""
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            self._state = "idle"


def save_decoder_atomic(decoder_path: Path | str, mapping_dict: dict) -> Path:
    """Атомарно сохраняет файл-дешифратор с правами 0600 и collision-safe записью."""
    path = Path(decoder_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_file = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(mapping_dict, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.chmod(temp_file, 0o600)
        os.replace(temp_file, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
        verified = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(verified, dict):
            raise ValueError("Дешифратор повреждён при сохранении")
        return path
    except Exception:
        try:
            os.unlink(temp_file)
        except OSError:
            pass
        raise


def excel_to_docx(wb, docx_path):
    """Преобразует openpyxl Workbook в документ Word (.docx)."""
    from docx import Document
    doc = Document()
    first_sheet = True
    for ws in getattr(wb, "worksheets", []):
        if not first_sheet:
            doc.add_page_break()
        first_sheet = False
        doc.add_heading(ws.title, level=2)
        rows_data = []
        for row in ws.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() for c in row):
                rows_data.append([str(c) if c is not None else "" for c in row])
        if rows_data:
            max_cols = max(len(r) for r in rows_data)
            if max_cols > 0:
                norm_rows = [r + [""] * (max_cols - len(r)) for r in rows_data]
                table = doc.add_table(rows=len(norm_rows), cols=max_cols)
                table.style = 'Table Grid'
                for r_idx, row in enumerate(norm_rows):
                    for c_idx, val in enumerate(row):
                        table.cell(r_idx, c_idx).text = val
    doc.save(str(docx_path))
    return Path(docx_path)


class BackendApi:

    def __init__(self, *, lazy_pullenti: bool = False, background_mode: bool = False):
        self._window = None
        # Context-menu/Quick Action runs have no review UI.  They must not
        # touch the encrypted review vault because that would invoke the OS
        # keyring/Keychain and can block a supposedly headless operation.
        self.background_mode = bool(background_mode)
        # Crash-recovery checkpoints contain the in-progress mapping and use
        # the encrypted recovery vault.  Background actions have no recovery
        # UI and must remain completely independent of the OS keyring.
        self.enable_crash_recovery = not self.background_mode
        # Выполняем безопасную миграцию устаревших настроек/секретов
        try:
            state_migration.run_state_migration(migrate_review_state=not self.background_mode)
        except Exception as _mig_err:
            logger.warning("State migration warning: %s", _mig_err)

        self.settings_file = Path(app_paths.get_settings_path())
        self.settings = self._load_settings()

        # Pullenti является единственным источником сущностей. Старые списки
        # оставлены на диске для совместимости данных пользователя, но больше не
        # загружаются и не участвуют в обезличивании.
        self.exclusions_file_path = Path(app_paths.get_exclusions_path())
        self.replacements_file_path = Path(app_paths.get_replacements_path())
        self.user_exclusions = set()
        self._normalized_exclusions_cache: frozenset[str] | None = None
        self.custom_replacements = set()

        # Настройки плейсхолдеров
        self.bracket_type = self.settings.get("bracket_type", "square")
        self.enabled_placeholders = set(self.settings.get("enabled_placeholders", list(PLACEHOLDER_TEMPLATES.keys())))
        if "PER" in self.enabled_placeholders:
            self.enabled_placeholders.add("FOREIGN_PER")
        if "ORG" in self.enabled_placeholders:
            self.enabled_placeholders.add("FOREIGN_ORG")
        if "RU_ACCOUNT" in self.enabled_placeholders or "ACCOUNT_GENERIC" in self.enabled_placeholders:
            self.enabled_placeholders.update({"IBAN", "SWIFT"})
        if "BIK" in self.enabled_placeholders:
            self.enabled_placeholders.add("SWIFT")
        if "PASSPORT" in self.enabled_placeholders:
            self.enabled_placeholders.update({"PASSPORT_SERIES", "PASSPORT_NUMBER", "PASSPORT_DIVISION_CODE", "PASSPORT_CODE"})
        if "DOCUMENT_NUMBER" in self.enabled_placeholders or "TOUR_OPERATOR_REGISTRY_NUMBER" not in self.settings.get("enabled_placeholders", []):
            self.enabled_placeholders.add("TOUR_OPERATOR_REGISTRY_NUMBER")
        self.current_placeholders = generate_placeholders(self.bracket_type, self.enabled_placeholders)

        # Optional local grammar pass over already anonymised text.  The
        # object is cheap; torch/transformers/model weights remain lazy.
        self.qwen_settings = QwenPostprocessorSettings.from_mapping(
            self.settings.get("qwen_postprocessor", {})
        )
        self._qwen_postprocessor = QwenPlaceholderPostprocessor(self.qwen_settings)
        self._model_operation_lock = threading.Lock()
        self._model_cancel_event = threading.Event()
        self._model_operation_status = None
        self._model_operation_thread = None

        # Язык OCR для PDF-сканов (rus, eng или rus+eng).
        self.ocr_lang = str(self.settings.get("ocr_lang", "rus+eng"))
        # Явно включаемый необратимый PDF: страницы становятся изображениями
        # без извлекаемого текста. Обычный режим остаётся обратимым.
        self.irreversible_pdf = bool(self.settings.get("irreversible_pdf", False))
        self.hidden_data_policy = {**self._default_hidden_data_policy(), **dict(self.settings.get("hidden_data_policy", {}))}
        self._hidden_inspections = {}

        # Управление чистотой рабочей папки пользователя:
        # По умолчанию логирование в отдельные файлы _log.txt, файлы аудита .audit.json
        # и sidecars .provenance.json отключены, чтобы не захламлять папку пользователя.
        # Вся системная диагностика и журнал безопасно пишутся в единый системный лог.
        self.save_original = bool(self.settings.get("save_original", False))
        self.save_docx = bool(self.settings.get("save_docx", False))
        self.save_pdf = bool(self.settings.get("save_pdf", False))
        self.save_markdown = bool(self.settings.get("save_markdown", False))
        self.save_decoder = bool(self.settings.get("save_decoder", False))
        self.auto_decoder = bool(self.settings.get("auto_decoder", True))
        self.save_log_files = bool(self.settings.get("save_log_files", False))
        self.save_audit_files = bool(self.settings.get("save_audit_files", False))
        self.emit_audit_sidecars = bool(self.settings.get("emit_audit_sidecars", False))
        self.open_output_folder = bool(self.settings.get("open_output_folder", True))
        self._last_output_dir: str | None = None

        # Pullenti NLP
        self._pullenti_processor = None
        self._pullenti_state = "not_started"
        self._pullenti_state_lock = threading.Lock()
        self._pullenti_ready = threading.Event()
        self._pullenti_init_thread = None
        self._shutdown_requested = False
        if not lazy_pullenti:
            self.init_pullenti()

        # Инициализация потока (добавляется)
        self.thread = None
        self.worker = None

        # Исходные значения для ручного отклонения живут только в памяти
        # backend-а. Они никогда не попадают в ReviewQueue, JS, логи или
        # сертификаты; после завершения процесса их можно удалить.
        from privacy_audit import ReviewQueue
        from review_context import SecureContextError, SecureContextVault
        self._review_queue_path = Path(app_paths.get_review_queue_path())
        self._review_context_path = Path(app_paths.get_review_context_path())
        self._review_vault = None
        self.review_queue = ReviewQueue()
        self._review_private = {}
        if not self.background_mode:
            self._review_vault = SecureContextVault(self._review_context_path, account="local")
            try:
                if self._review_queue_path.exists():
                    payload = json.loads(self._review_queue_path.read_text(encoding="utf-8"))
                    self.review_queue = ReviewQueue(payload.get("items", []))
            except Exception as exc:
                logger.warning("Очередь ручной проверки повреждена; начат новый сеанс: %s", type(exc).__name__)
            try:
                self._review_private = self._review_vault.load()
            except SecureContextError as exc:
                # Fail closed: no private context is reconstructed from plaintext.
                logger.warning("Приватный контекст ручной проверки недоступен: %s", type(exc).__name__)
                self._review_private = {}
        self._review_rebuilt = set()
        self._cancel_event = threading.Event()
        self._active_threads: list[threading.Thread] = []
        self._op_manager = UnifiedOperationManager(self)

        self.restore_doc_paths: list[str] = []
        self.restore_json_paths: list[str] = []
        self.is_quick_mode: bool = False
        self._active_batch_id: str | None = None
        self._active_document_ref: str = ""
        self._active_source_path: str = ""
        self._active_review_location: str = "text"
        self._active_review_coordinate: dict = {"scope": "body", "kind": "paragraph", "index": 0}
        self._active_ocr_span_map: list = []

    def _ensure_op_manager(self) -> UnifiedOperationManager:
        if not hasattr(self, "_op_manager") or self._op_manager is None:
            self._op_manager = UnifiedOperationManager(self)
        return self._op_manager

    def is_busy(self) -> bool:
        """Возвращает True, если сейчас выполняется фоновая операция."""
        return self._ensure_op_manager().is_busy()

    def start_operation(self, op_type: str, thread: threading.Thread | None = None, description: str = "") -> bool:
        """Запускает операцию через менеджер состояний с проверкой занятости."""
        return self._ensure_op_manager().start_operation(op_type, thread=thread, description=description)

    def finish_operation(self, status: str = "succeeded"):
        """Завершает активную операцию в менеджере состояний."""
        self._ensure_op_manager().finish_operation(status)


    def _save_decoder_atomic(self, decoder_path: Path | str, mapping_dict: dict) -> Path:
        """Атомарно сохраняет файл-дешифратор с правами 0600 и collision-safe записью."""
        return save_decoder_atomic(decoder_path, mapping_dict)

    def _get_unique_ocr_output_path(self, source_path: Path | str) -> Path:
        """Генерирует уникальный путь для OCR-результата без перезаписи существующих файлов."""
        src = Path(source_path)
        candidate = src.with_suffix(".txt")
        if not candidate.exists():
            return candidate
        counter = 1
        while candidate.exists():
            candidate = src.parent / f"{src.stem}_ocr_{counter}.txt"
            counter += 1
        return candidate

    def update_settings(self, settings: dict):
        """Патч-обновление настроек с валидацией типов."""
        if not isinstance(settings, dict):
            return
        self.settings.update(settings)
        if 'save_original' in settings:
            self.save_original = bool(settings['save_original'])
        if 'save_docx' in settings:
            self.save_docx = bool(settings['save_docx'])
        if 'save_pdf' in settings:
            self.save_pdf = bool(settings['save_pdf'])
        if 'save_markdown' in settings:
            self.save_markdown = bool(settings['save_markdown'])
            self.settings['save_markdown'] = self.save_markdown
        if 'irreversible_pdf' in settings:
            self.irreversible_pdf = bool(settings['irreversible_pdf'])
            self.settings['irreversible_pdf'] = self.irreversible_pdf
        if 'save_decoder' in settings:
            self.save_decoder = bool(settings['save_decoder'])
            self.settings['save_decoder'] = self.save_decoder
        if 'auto_decoder' in settings:
            self.auto_decoder = bool(settings['auto_decoder'])
            self.settings['auto_decoder'] = self.auto_decoder
        if 'open_output_folder' in settings:
            self.open_output_folder = bool(settings['open_output_folder'])
            self.settings['open_output_folder'] = self.open_output_folder
        if 'ocr_lang' in settings:
            ocr_lang_val = str(settings['ocr_lang'])
            if ocr_lang_val in ('rus', 'eng', 'rus+eng'):
                if getattr(self, 'ocr_lang', None) != ocr_lang_val:
                    import pdf_convert
                    pdf_convert.clear_ocr_cache()
                self.ocr_lang = ocr_lang_val
                self.settings['ocr_lang'] = ocr_lang_val
        if 'qwen_enabled' in settings:
            from qwen_postprocessor import _as_bool
            qwen_enabled = _as_bool(settings['qwen_enabled'])
            if hasattr(self, 'qwen_settings') and qwen_enabled != self.qwen_settings.enabled:
                self.save_qwen_settings({"enabled": qwen_enabled})
        self._save_settings()

    def cancel_processing(self):
        """Запрашивает отмену активных операций."""
        logger.info("Пользователь запросил отмену операции.")
        self._op_manager.cancel_operation()
        if not hasattr(self, "_cancel_event") or self._cancel_event is None:
            self._cancel_event = threading.Event()
        self._cancel_event.set()
        if hasattr(self, "_model_cancel_event") and self._model_cancel_event is not None:
            self._model_cancel_event.set()
        if getattr(self, "_window", None):
            ui_bridge.ui_set_global_progress(self._window, True, "Отмена", "Остановка обработки...", 0)
            def _close_cancel_overlay():
                import time
                time.sleep(0.8)
                if getattr(self, "_window", None):
                    ui_bridge.ui_set_global_progress(self._window, False)
            threading.Thread(target=_close_cancel_overlay, daemon=True).start()

    def cancel_operation(self):
        """Псевдоним для отмены операции через менеджер."""
        self.cancel_processing()

    def is_cancelled(self) -> bool:
        """Возвращает True, если была запрошена отмена."""
        cancel_event = getattr(self, "_cancel_event", None)
        return cancel_event.is_set() if cancel_event is not None else False

    def reset_cancellation(self):
        """Сбрасывает флаг отмены перед запуском новой операции."""
        if not hasattr(self, "_cancel_event") or self._cancel_event is None:
            self._cancel_event = threading.Event()
        else:
            self._cancel_event.clear()

    def wait_for_tasks(self, timeout: float = 2.0):
        """Ожидает завершения активных фоновых потоков."""
        for t in list(getattr(self, "_active_threads", [])):
            if t.is_alive():
                t.join(timeout=timeout)

    def shutdown(self):
        """Корректно останавливает все фоновые процессы без обращения к UI."""
        logger.info("Завершение работы BackendApi...")
        if hasattr(self, "_cancel_event") and self._cancel_event is not None:
            self._cancel_event.set()
        if hasattr(self, "_model_cancel_event") and self._model_cancel_event is not None:
            self._model_cancel_event.set()
        self._shutdown_requested = True
        pullenti_thread = getattr(self, "_pullenti_init_thread", None)
        if pullenti_thread is not None and pullenti_thread.is_alive():
            pullenti_thread.join(timeout=0.2)
        self.wait_for_tasks(timeout=0.2)

    def _persist_review_state(self) -> None:
        """Persist only the PII-free queue and encrypted private context."""
        if getattr(self, "background_mode", False):
            return
        try:
            self.review_queue.save(self._review_queue_path)
        except Exception as exc:
            logger.warning("Очередь ручной проверки не сохранена: %s", type(exc).__name__)
        try:
            if self._review_vault is not None:
                self._review_vault.save(getattr(self, "_review_private", {}))
        except Exception as exc:
            # Never create a plaintext substitute when OS keyring/crypto fails.
            logger.warning("Приватный контекст не сохранён безопасно: %s", type(exc).__name__)

    def set_window(self, window):
        self._window = window
        self.start_pullenti_initialization()

    def init_ui(self):
        if self._window:
            ui_bridge.ui_update_badge_status(self._window, f"Активно: {len(self.user_exclusions)} искл., {len(self.custom_replacements)} замен")



    def _on_ocr_lang_changed(self, text: str):
        mapping = {"Русский": "rus", "Английский": "eng", "Русский + Английский": "rus+eng"}
        lang_code = mapping.get(text, "rus")
        if getattr(self, "ocr_lang", None) != lang_code:
            import pdf_convert
            pdf_convert.clear_ocr_cache()
        self.ocr_lang = lang_code
        try:
            self.settings["ocr_lang"] = lang_code
        except Exception:
            pass



    def _run_restore(self):
        from document_restorer import DocumentRestorer, is_valid_decoder_structure, find_decoder_near_document
        from pathlib import Path
        import json
        if not hasattr(self, 'restore_doc_paths') or not self.restore_doc_paths:
            if self._window: ui_bridge.ui_alert(self._window, "Ошибка: Сначала загрузите обработанный документ.")
            return

        if not self.start_operation("restore_documents", description="Восстановление документов"):
            return

        try:
            document_paths = [Path(value) for value in self.restore_doc_paths]
            selected_decoders = [Path(value) for value in getattr(self, "restore_json_paths", [])]
            auto_decoder = getattr(self, "auto_decoder", self.settings.get("auto_decoder", True))
            successes, errors = [], []

            total_docs = len(document_paths)
            for index, doc_path_obj in enumerate(document_paths):
                try:
                    if self._window:
                        pct = int((index / max(1, total_docs)) * 90)
                        ui_bridge.ui_set_global_progress(
                            self._window, True, "Восстановление документов",
                            f"Обработка {doc_path_obj.name} ({index + 1}/{total_docs})...", pct
                        )
                    if selected_decoders:
                        # One explicitly selected shared decoder applies to all
                        # documents; equal-sized selections are paired by index.
                        decoder = selected_decoders[index] if len(selected_decoders) == len(document_paths) else selected_decoders[0]
                    elif auto_decoder:
                        decoder = find_decoder_near_document(doc_path_obj)
                    else:
                        decoder = None
                    if decoder is None or not Path(decoder).exists():
                        raise ValueError("не найден файл-дешифратор")
                    with Path(decoder).open("r", encoding="utf-8") as stream:
                        mapping = json.load(stream)
                    if not is_valid_decoder_structure(mapping):
                        raise ValueError("некорректная структура дешифратора")

                    restorer = DocumentRestorer(mapping)
                    suffix = doc_path_obj.suffix.lower()
                    if suffix in (".docx", ".docm"):
                        ok, msg = restorer.restore_docx(str(doc_path_obj))
                    elif suffix in (".xlsx", ".xls", ".xlsm"):
                        ok, msg = restorer.restore_excel(str(doc_path_obj))
                    elif suffix == ".md":
                        ok, msg = restorer.restore_markdown(str(doc_path_obj))
                    elif suffix == ".pdf":
                        raise ValueError(
                            "Восстановление PDF невозможно: PDF является финальным (необратимым) форматом публикации. "
                            "Для восстановления документа по дешифратору выберите исходный файл Word (.docx) или Excel (.xlsx)."
                        )
                    else:
                        raise ValueError(f"неподдерживаемый формат: {suffix}")
                    if not ok:
                        raise ValueError(msg)
                    successes.append(f"{doc_path_obj.name}: {msg}")
                except Exception as exc:
                    errors.append(f"{doc_path_obj.name}: {exc}")

            if self._window:
                if not getattr(self, "is_quick_mode", False):
                    ui_bridge.ui_set_global_progress(self._window, False)
                    summary = f"Восстановлено: {len(successes)}; ошибок: {len(errors)}"
                    if errors:
                        summary += ". " + "; ".join(errors[:5])
                    ui_bridge.ui_alert(self._window, summary)
            self.finish_operation("succeeded" if not errors else "partial")
            return {"successes": successes, "errors": errors}
        except Exception:
            self.finish_operation("failed")
            raise

    def get_current_status_text(self):
        parts_r = f"{len(self.custom_replacements)} зам." if self.custom_replacements else ""
        parts_e = f"{len(self.user_exclusions)} искл." if self.user_exclusions else ""

        # Информация о плейсхолдерах
        bracket_symbol = "[]" if self.bracket_type == "square" else "//"
        parts_p = f"{len(self.enabled_placeholders)} типов {bracket_symbol}"

        status_parts = [p for p in [parts_r, parts_e, parts_p] if p]
        if status_parts: return f"Активно: {', '.join(status_parts)}."
        else: return "Ожидание файла..."

    def _initialize_pullenti(self):
        """Инициализация Pullenti SDK и процессора NER."""

        if not PULLENTI_AVAILABLE:
            self._pullenti_processor = None
            self._pullenti_state = "unavailable"
            self._pullenti_ready.set()
            if not self._shutdown_requested:
                self.show_error("Библиотека Pullenti (PullentiPython) не установлена. Обезличивание невозможно.")
            return False

        try:
            global _PULLENTI_INITIALIZED
            with _PULLENTI_INIT_LOCK:
                if not _PULLENTI_INITIALIZED:
                    _private_status("Инициализация Pullenti SDK...")
                    initialize_ner()
                    _PULLENTI_INITIALIZED = True
                    _private_status(f"Pullenti инициализирован, версия {ProcessorService.get_version()} от {ProcessorService.get_version_date()}")

            # Создаем общий процессор для всех анализаторов
            self._pullenti_processor = ProcessorService.create_processor()
            self._pullenti_state = "ready"
            _private_status("Pullenti NER-процессор создан успешно.")
            return True
        except Exception as e:
            self._pullenti_processor = None
            self._pullenti_state = "failed"
            logger.exception("Ошибка инициализации Pullenti: %s", e)
            if not self._shutdown_requested:
                self.show_error("Ошибка инициализации Pullenti")
            return False
        finally:
            self._pullenti_ready.set()

    def init_pullenti(self):
        """Synchronously initialize Pullenti for CLI/tests and direct users."""

        state_lock = getattr(self, "_pullenti_state_lock", None)
        if state_lock is None:
            self._pullenti_state_lock = threading.Lock()
            self._pullenti_ready = threading.Event()
            self._pullenti_state = "not_started"
            self._shutdown_requested = False
            state_lock = self._pullenti_state_lock
        with state_lock:
            if self._pullenti_state == "ready":
                return True
            if self._pullenti_state == "initializing":
                wait_for_existing = True
            else:
                self._pullenti_state = "initializing"
                self._pullenti_ready.clear()
                wait_for_existing = False
        if wait_for_existing:
            self._pullenti_ready.wait()
            return self._pullenti_state == "ready"
        return self._initialize_pullenti()

    def start_pullenti_initialization(self):
        """Start Pullenti after the window exists, without blocking startup."""

        with self._pullenti_state_lock:
            if self._pullenti_state != "not_started":
                return self._pullenti_init_thread
            self._pullenti_state = "initializing"
            self._pullenti_ready.clear()
            thread = threading.Thread(
                target=self._initialize_pullenti,
                name="pullenti-initializer",
                daemon=True,
            )
            self._pullenti_init_thread = thread
        thread.start()
        return thread

    def _load_list_from_startup(self, file_path, target_set, item_type_name="элементов"):
        initial_count = len(target_set) # Считаем до загрузки из файла
        try:
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        word = line.strip()
                        if word and not word.startswith('#'):
                            target_set.add(word)
                loaded_count = len(target_set) - initial_count # Сколько было добавлено из файла
                _private_status(f"Загружено {loaded_count} дополнительных {item_type_name} из файла {file_path}. Всего (встроенные + файл): {len(target_set)}")
            else:
                _private_status(f"Файл '{file_path}' не найден при запуске. Используются только встроенные {item_type_name} ({initial_count} шт.).")
                # Только при первом запуске создаём файл с дефолтным списком
                self._save_list_to_file_startup(file_path, target_set, item_type_name, is_default_list=True)
        except Exception as e:
            _private_status(f"Ошибка загрузки файла '{file_path}' при запуске: {e}")

    def _save_list_to_file_startup(self, file_path, item_set, item_type_name="элементов", is_default_list=False):
        if file_path.exists() and not is_default_list:
            # Не перезаписываем файл, если он уже существует и это не первый запуск
            return
        try:
            header_comment = f"# Список {item_type_name} для DOCXдодыр\n# Каждое слово/фраза на новой строке.\n# Строки с '#' игнорируются.\n"
            if is_default_list:
                header_comment += "# Этот файл был создан автоматически с настройками по умолчанию.\n\n"
            else:
                header_comment += "\n"
            content = header_comment + "".join(f"{word}\n" for word in sorted(list(item_set)))
            app_paths.atomic_write_text(file_path, content)
            _private_status(f"Создан файл '{file_path}' с {len(item_set)} {item_type_name}.")
        except Exception as e:
            _private_status(f"Ошибка создания файла '{file_path}' при запуске: {e}")

    def load_exclusions_from_startup(self):
        # Загружаем пользовательские исключения из файла
        user_file_exclusions = set()
        file_path = self.exclusions_file_path
        try:
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        word = line.strip()
                        if word and not word.startswith('#'):
                            user_file_exclusions.add(word)
                _private_status(f"Загружено {len(user_file_exclusions)} пользовательских исключений из файла {file_path}.")
            else:
                _private_status(f"Файл '{file_path}' не найден при запуске. Используются только встроенные исключения.")
        except Exception as e:
            _private_status(f"Ошибка загрузки файла '{file_path}' при запуске: {e}")
        # Объединяем встроенные и пользовательские исключения
        all_exclusions = set(UNIFIED_EXCLUSIONS) | user_file_exclusions
        self.user_exclusions = all_exclusions
        self._normalized_exclusions_cache = frozenset(s.strip().casefold() for s in self.user_exclusions if s.strip() and not s.startswith('#'))
        _private_status(f"Итоговое количество исключений (встроенные + пользовательские): {len(self.user_exclusions)}")
        # Автоматически обновляем файл, если в UNIFIED_EXCLUSIONS появились новые слова
        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                file_words = set(line.strip() for line in f if line.strip() and not line.startswith('#'))
            if not all_exclusions.issubset(file_words) or not file_words.issubset(all_exclusions):
                try:
                    text = "# Список слов-исключений для DOCXдодыр\n# Каждое слово или фраза на новой строке.\n# Строки, начинающиеся с '#', игнорируются при загрузке.\n\n" + "".join(f"{word}\n" for word in sorted(all_exclusions))
                    app_paths.atomic_write_text(file_path, text)
                    _private_status(f"Файл '{file_path}' автоматически обновлён: теперь {len(all_exclusions)} исключений.")
                except Exception as e:
                    _private_status(f"Ошибка автообновления файла исключений: {e}")
        else:
            # Если файла не было, создаём его
            try:
                text = "# Список слов-исключений для DOCXдодыр\n# Каждое слово или фраза на новой строке.\n# Строки, начинающиеся с '#', игнорируются при загрузке.\n\n" + "".join(f"{word}\n" for word in sorted(all_exclusions))
                app_paths.atomic_write_text(file_path, text)
                _private_status(f"Файл '{file_path}' создан с {len(all_exclusions)} исключениями.")
            except Exception as e:
                _private_status(f"Ошибка создания файла исключений: {e}")

    def load_replacements_from_startup(self):
        self._load_list_from_startup(self.replacements_file_path, self.custom_replacements, "замен")

    def show_settings(self):
        if self._window: ui_bridge.ui_show_list_settings_modal(self._window)

    def get_lists(self):
        return {
            "exclusions": sorted(list(self.user_exclusions)),
            "replacements": sorted(list(self.custom_replacements))
        }

    def _write_list_to_file(self, file_path, item_set, item_type_name="элементов"):
        """Атомарно сохраняет список пользовательских исключений или замен на диск."""
        try:
            header_comment = f"# Список {item_type_name} для DOCXдодыр\n# Каждое слово/фраза на новой строке.\n# Строки с '#' игнорируются.\n\n"
            content = header_comment + "".join(f"{word}\n" for word in sorted(list(item_set)))
            app_paths.atomic_write_text(file_path, content)
            logger.info("Сохранён файл '%s' с %d %s", file_path, len(item_set), item_type_name)
        except Exception as e:
            logger.error("Ошибка сохранения файла '%s': %s", file_path, e)
            raise

    def save_lists(self, lists_dict):
        try:
            self.user_exclusions = set(lists_dict.get('exclusions', []))
            self.custom_replacements = set(lists_dict.get('replacements', []))
            self._write_list_to_file(self.exclusions_file_path, self.user_exclusions, "исключений")
            self._write_list_to_file(self.replacements_file_path, self.custom_replacements, "замен")
            if self._window:
                ui_bridge.ui_update_badge_status(self._window, f"Активно: {len(self.user_exclusions)} искл., {len(self.custom_replacements)} замен")
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def show_placeholder_settings(self):
        if self._window: ui_bridge.ui_show_placeholder_settings_modal(self._window)

    def _load_settings(self):
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                _private_status(f"Error loading settings: {e}")
        return {}

    def _save_settings(self):
        try:
            app_paths.atomic_write_json(self.settings_file, self.settings, indent=4)
        except Exception as e:
            _private_status(f"Error saving settings: {e}")

    @staticmethod
    def _default_hidden_data_policy():
        # Text that is not visible in the main document can contain the same
        # personal data as body text.  Anonymise it by default; never copy it
        # unchanged into a supposedly anonymised result.
        return {
            "comments": "remove", "footnotes": "anonymize", "endnotes": "anonymize",
            "hidden_text": "anonymize", "tracked_changes": "remove",
            "embedded_objects": "remove", "embedded_files": "remove",
            "annotations": "remove", "acroform": "remove", "javascript": "remove",
            "macros": "remove", "custom_xml": "remove", "external_links": "remove",
            "document_properties": "remove", "image_exif_xmp": "remove",
            "pdf_info": "remove", "pdf_xmp": "remove", "digital_signature": "keep",
            "encrypted_pdf": "keep",
        }

    def get_hidden_data_policy(self):
        """Return policy metadata and current actions, without document data."""
        from hidden_data import DESTRUCTIVE_HIDDEN_KINDS, HIDDEN_DATA_KINDS, TEXTUAL_HIDDEN_KINDS
        defaults = self._default_hidden_data_policy()
        current = {**defaults, **getattr(self, "hidden_data_policy", {})}
        return {
            "schema_version": 1,
            "actions": current,
            "kinds": [
                {"kind": kind, "textual": kind in TEXTUAL_HIDDEN_KINDS,
                 "destructive": kind in DESTRUCTIVE_HIDDEN_KINDS,
                 "actions": ["keep", "anonymize", "remove"] if kind in TEXTUAL_HIDDEN_KINDS else ["keep", "remove"]}
                for kind in HIDDEN_DATA_KINDS
            ],
            "safe_defaults": True,
            "destructive_confirmation_required": True,
        }

    def save_hidden_data_policy(self, actions, confirm_destructive=False):
        from hidden_data import DESTRUCTIVE_HIDDEN_KINDS, HIDDEN_DATA_KINDS, TEXTUAL_HIDDEN_KINDS
        if not isinstance(actions, dict):
            return {"success": False, "error": "Политика должна быть объектом"}
        merged = {**self._default_hidden_data_policy(), **actions}
        unknown = set(merged) - set(HIDDEN_DATA_KINDS)
        invalid = {key: value for key, value in merged.items() if value not in {"keep", "anonymize", "remove"}}
        invalid_nontext = {key for key, value in merged.items() if value == "anonymize" and key not in TEXTUAL_HIDDEN_KINDS}
        if unknown or invalid or invalid_nontext:
            return {"success": False, "error": "Недопустимое действие политики"}
        destructive = {key for key, value in merged.items() if value == "remove" and key in DESTRUCTIVE_HIDDEN_KINDS}
        if destructive and not bool(confirm_destructive):
            return {"success": False, "requires_confirmation": True, "destructive_kinds": sorted(destructive)}
        self.hidden_data_policy = merged
        self.settings["hidden_data_policy"] = dict(merged)
        self._save_settings()
        return {"success": True, "policy": self.get_hidden_data_policy()}

    def inspect_hidden_file(self, source_path):
        """Inspect one file and return only aggregate risks plus a one-use token."""
        from hidden_data import HiddenDataError, inspection_fingerprint, inspect_hidden_data
        source = Path(str(source_path))
        try:
            report = inspect_hidden_data(source)
            fingerprint = inspection_fingerprint(source)
        except Exception as exc:
            raise HiddenDataError("Не удалось безопасно проверить файл") from exc
        token = secrets.token_urlsafe(24)
        inspections = getattr(self, "_hidden_inspections", None)
        if inspections is None:
            inspections = self._hidden_inspections = {}
        inspections[token] = {"path": str(source.resolve()), "fingerprint": fingerprint, "report": report}
        safe = report.to_safe_dict()
        safe["inspection_token"] = token
        safe["actions"] = self.get_hidden_data_policy()["actions"]
        return safe

    def apply_hidden_data_policy(self, inspection_token, actions=None, confirm_destructive=False, batch_id=None):
        """Apply a previously inspected policy to a new output file.

        The token/fingerprint binding prevents applying a stale UI decision to
        another file. Textual hidden parts use the same Pullenti mapping and
        batch identifier throughout this operation; signed files fail closed.
        """
        from hidden_data import DESTRUCTIVE_HIDDEN_KINDS, HiddenDataError, apply_hidden_data_policy as apply_policy, inspection_fingerprint
        inspection = getattr(self, "_hidden_inspections", {}).get(str(inspection_token))
        if not inspection:
            return {"success": False, "error": "Проверка устарела; сначала проверьте файл заново"}
        source = Path(inspection["path"])
        if not source.is_file() or inspection_fingerprint(source) != inspection["fingerprint"]:
            return {"success": False, "error": "Исходный файл изменился; очистка остановлена"}
        policy = self.get_hidden_data_policy()["actions"]
        if actions is not None:
            if not isinstance(actions, dict):
                return {"success": False, "error": "Политика должна быть объектом"}
            policy.update(actions)
        destructive = sorted(key for key, value in policy.items() if value == "remove" and key in DESTRUCTIVE_HIDDEN_KINDS and inspection["report"].detected.get(key, 0))
        if destructive and not bool(confirm_destructive):
            return {"success": False, "requires_confirmation": True, "destructive_kinds": destructive}
        batch = str(batch_id or uuid.uuid4())
        if any(value == "anonymize" for value in policy.values()) and getattr(self, "_pullenti_processor", None) is None:
            return {"success": False, "error": "Pullenti недоступен; обезличивание скрытого текста остановлено"}
        target = source.with_name(f"{source.stem}_hidden_cleaned{source.suffix}")
        index = 1
        while target.exists():
            target = source.with_name(f"{source.stem}_hidden_cleaned_{index}{source.suffix}")
            index += 1
        mapping, seen = {}, {}
        old_batch = getattr(self, "_active_batch_id", None)
        old_ref, old_source, old_loc, old_coord = (getattr(self, key, None) for key in ("_active_document_ref", "_active_source_path", "_active_review_location", "_active_review_coordinate"))
        self._active_batch_id = batch
        self._active_document_ref = source.name
        self._active_source_path = str(source.resolve())
        self._active_review_location = "hidden"
        self._active_review_coordinate = {"scope": "hidden", "kind": "part"}
        def transform(text, kind):
            result, _count, _logs = self.anonymize_text_pullenti(
                text, getattr(self, "user_exclusions", set()), getattr(self, "custom_replacements", set()),
                prev_paragraphs=None, mapping_dict=mapping, entity_seen=seen,
            )
            return result
        try:
            report = apply_policy(source, target, policy, text_transform=transform, strict=True)
            from privacy_audit import build_audit_certificate, write_audit_certificate
            certificate = build_audit_certificate(source, target, counts={"files": 1}, review_queue=getattr(self, "review_queue", None))
            certificate["hidden_data"] = {"batch_id": batch, **report.to_safe_dict(), "removed": list(report.removed), "changed": bool(report.changed)}
            write_audit_certificate(certificate, target.with_name(target.name + ".audit.json"))
            self._hidden_inspections.pop(str(inspection_token), None)
            return {"success": True, "output_path": str(target), "report": report.to_safe_dict(), "batch_id": batch}
        except Exception as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, HiddenDataError):
                return {"success": False, "error": str(exc)}
            logger.warning("Скрытая policy-очистка остановлена: %s", exc)
            return {"success": False, "error": "Безопасная обработка скрытых данных остановлена"}
        finally:
            self._active_batch_id = old_batch
            for key, value in (("_active_document_ref", old_ref), ("_active_source_path", old_source), ("_active_review_location", old_loc), ("_active_review_coordinate", old_coord)):
                if value is None:
                    self.__dict__.pop(key, None)
                else:
                    setattr(self, key, value)

    def get_placeholder_settings(self):
        return {
            "bracket_type": self.bracket_type,
            "enabled_placeholders": list(self.enabled_placeholders),
            "all_placeholders": PLACEHOLDER_TEMPLATES
        }

    def save_placeholder_settings(self, data):
        try:
            self.bracket_type = data.get("bracket_type", "square")
            self.enabled_placeholders = set(data.get("enabled_placeholders", list(PLACEHOLDER_TEMPLATES.keys())))
            if "PER" in self.enabled_placeholders:
                self.enabled_placeholders.add("FOREIGN_PER")
            if "ORG" in self.enabled_placeholders:
                self.enabled_placeholders.add("FOREIGN_ORG")
            if "RU_ACCOUNT" in self.enabled_placeholders or "ACCOUNT_GENERIC" in self.enabled_placeholders:
                self.enabled_placeholders.update({"IBAN", "SWIFT"})
            if "BIK" in self.enabled_placeholders:
                self.enabled_placeholders.add("SWIFT")
            if "PASSPORT" in self.enabled_placeholders:
                self.enabled_placeholders.update({"PASSPORT_SERIES", "PASSPORT_NUMBER", "PASSPORT_DIVISION_CODE", "PASSPORT_CODE"})
            if "DOCUMENT_NUMBER" in self.enabled_placeholders or "TOUR_OPERATOR_REGISTRY_NUMBER" not in data.get("enabled_placeholders", []):
                self.enabled_placeholders.add("TOUR_OPERATOR_REGISTRY_NUMBER")
            
            # Save to persistent settings dict and file
            self.settings["bracket_type"] = self.bracket_type
            self.settings["enabled_placeholders"] = list(self.enabled_placeholders)
            self._save_settings()
            
            # Regenerate placeholders
            self.current_placeholders = generate_placeholders(self.bracket_type, self.enabled_placeholders)
            
            # Update UI Badge (Wait, placeholder badge isn't separate, it's combined or maybe just "Active: ...")
            # Let's send a generic success response
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_settings(self):
        """Возвращает текущие настройки форматов сохранения и общих параметров приложения."""
        return {
            "save_original": bool(getattr(self, "save_original", False)),
            "save_docx": bool(getattr(self, "save_docx", False)),
            "save_pdf": bool(getattr(self, "save_pdf", False)),
            "save_markdown": bool(getattr(self, "save_markdown", False)),
            "irreversible_pdf": bool(getattr(self, "irreversible_pdf", False)),
            "save_decoder": bool(getattr(self, "save_decoder", False)),
            "auto_decoder": bool(getattr(self, "auto_decoder", True)),
            "open_output_folder": bool(getattr(self, "open_output_folder", True)),
            "ocr_lang": getattr(self, "ocr_lang", "rus+eng"),
            "qwen_enabled": bool(getattr(self.qwen_settings, "enabled", False)),
        }

    def get_window_geometry(self) -> dict:
        """Возвращает сохранённый размер и координаты окна."""
        geom = self.settings.get("window_geometry", {})
        if not isinstance(geom, dict):
            geom = {}
        width = int(geom.get("width", 840))
        height = int(geom.get("height", 740))
        width = max(780, min(width, 3840))
        height = max(700, min(height, 2160))
        res = {"width": width, "height": height}
        x = geom.get("x")
        y = geom.get("y")
        if x is not None and y is not None:
            try:
                res["x"] = max(0, int(x))
                res["y"] = max(0, int(y))
            except (ValueError, TypeError):
                pass
        return res

    def save_window_geometry(self, width: int, height: int, x: int | None = None, y: int | None = None):
        """Сохраняет геометрию окна приложения в settings.json."""
        try:
            w = max(780, min(int(width), 3840))
            h = max(700, min(int(height), 2160))
            geom = {"width": w, "height": h}
            if x is not None and y is not None:
                try:
                    ix, iy = int(x), int(y)
                    if ix >= -1000 and iy >= -1000:
                        geom["x"] = ix
                        geom["y"] = iy
                except (ValueError, TypeError):
                    pass
            self.settings["window_geometry"] = geom
            self._save_settings()
            return {"success": True}
        except Exception as exc:
            logger.warning("Не удалось сохранить геометрию окна: %s", exc)
            return {"success": False, "error": str(exc)}


    def get_qwen_settings(self):
        from qwen_postprocessor import runtime_preflight

        payload = self.qwen_settings.to_dict()
        payload["status"] = self._qwen_postprocessor.last_status
        payload["loaded"] = self._qwen_postprocessor.loaded
        payload["preflight"] = runtime_preflight(self.qwen_settings)
        return payload

    def get_review_findings(self, entity_type="", document_ref="", status="", min_confidence=None, limit=100, offset=0):
        """Возвращает обезличенную очередь и поддерживает фильтры экрана.

        Фильтрация выполняется локально и не меняет очередь.  ``document_ref``
        является подстрокой (удобно для длинных имён), confidence задаётся
        числом 0..1 или процентом 0..100.
        """
        from privacy_audit import ReviewQueue, AUDIT_SCHEMA_VERSION

        queue = getattr(self, "review_queue", None)
        if queue is None:
            queue = self.review_queue = ReviewQueue()
        threshold = min_confidence
        try:
            if threshold not in (None, "") and float(threshold) > 1:
                threshold = float(threshold) / 100.0
        except (TypeError, ValueError):
            threshold = None
        items = queue.filter(
            entity_type=entity_type, document_ref=document_ref,
            status=status, min_confidence=threshold,
        )
        total_filtered = len(items)
        if limit is not None and str(limit).strip() != "":
            try:
                l_val = max(1, int(limit))
                o_val = max(0, int(offset or 0))
                sliced = items[o_val : o_val + l_val]
            except (ValueError, TypeError):
                l_val = 100
                o_val = 0
                sliced = items[:100]
        else:
            l_val = total_filtered
            o_val = 0
            sliced = items

        result = {
            "schema_version": getattr(queue, "schema_version", AUDIT_SCHEMA_VERSION),
            "summary": queue.summary(),
            "filtered_summary": {
                key: sum(1 for item in items if item.status == key)
                for key in ("pending", "accepted", "rejected", "skipped")
            },
            "items": [item.to_dict() for item in sliced],
            "entity_types": sorted(list(queue.by_entity_type().keys())),
            "total_filtered": total_filtered,
            "limit": l_val,
            "offset": o_val,
            "filters": {
                "entity_type": str(entity_type or ""), "document_ref": str(document_ref or ""),
                "status": str(status or ""), "min_confidence": threshold,
            }
        }
        result["filtered_summary"]["total"] = total_filtered
        return result

    def batch_decide_review_findings(self, finding_ids=None, status="accepted", entity_type="", document_ref="", min_confidence=None):
        """Apply one decision to the selected/filtered findings atomically enough for UI use.

        Only pending findings are changed; already decided items remain auditable.
        The method returns per-item results without exposing private source data.
        """
        from privacy_audit import ReviewQueue
        queue = getattr(self, "review_queue", None)
        if queue is None:
            queue = self.review_queue = ReviewQueue()
        if status not in {"accepted", "rejected", "skipped"}:
            raise ValueError("status должен быть accepted, rejected или skipped")
        ids = {str(value) for value in (finding_ids or []) if value}
        if ids:
            selected = [item for item in queue.items() if item.finding_id in ids]
        else:
            threshold = min_confidence
            try:
                if threshold not in (None, "") and float(threshold) > 1:
                    threshold = float(threshold) / 100.0
            except (TypeError, ValueError):
                threshold = None
            selected = queue.filter(entity_type=entity_type, document_ref=document_ref, status="pending", min_confidence=threshold)
        changed = []
        for item in selected:
            if item.status != "pending":
                continue
            queue.decide(item.finding_id, status, "массовое решение по фильтру")
            if len(changed) < 100:
                changed.append(item.to_dict())
        self._persist_review_state()
        return {"success": True, "changed": changed, "count": len(selected), "summary": queue.summary()}

    def get_review_finding_preview(self, finding_id):
        """Return navigation geometry only; never returns source/PDF pixels or text."""
        queue = getattr(self, "review_queue", None)
        item = queue.get(str(finding_id)) if queue is not None else None
        if item is None:
            return {"success": False, "error": "Находка не найдена"}
        position = dict(item.position or {})
        allowed = {key: position[key] for key in ("page", "bbox", "width", "height", "scope", "kind", "sheet", "cell", "row", "column", "index") if key in position}
        if "bbox" in allowed and (not isinstance(allowed["bbox"], (list, tuple)) or len(allowed["bbox"]) != 4):
            allowed.pop("bbox", None)
        return {"success": True, "finding_id": item.finding_id, "document_ref": item.document_ref, "position": allowed}

    def decide_review_finding(self, finding_id, status, note=""):
        from privacy_audit import ReviewQueue

        queue = getattr(self, "review_queue", None)
        if queue is None:
            queue = self.review_queue = ReviewQueue()
        item = queue.decide(str(finding_id), str(status), str(note or ""))
        response = {"success": True, "item": item.to_dict(), "summary": queue.summary()}
        if str(status) == "rejected":
            # Решение «отклонить» сразу применяет точечный откат. UI может
            # повторно вызвать rebuild_rejected_finding для совместимости —
            # операция идемпотентна и не изменит документ второй раз.
            response["rebuild"] = self.rebuild_rejected_finding(str(finding_id))
        self._persist_review_state()
        return response

    def rebuild_rejected_finding(self, finding_id):
        """Атомарно возвращает только конкретную отклонённую замену.

        Исходное значение берётся исключительно из приватного реестра,
        собранного во время текущего запуска. Если приложение перезапущено,
        операция безопасно завершится сообщением о недоступности контекста.
        """
        from privacy_audit import build_audit_certificate, clean_file_metadata, write_audit_certificate

        queue = getattr(self, "review_queue", None)
        item = queue.get(str(finding_id)) if queue is not None else None
        if item is None:
            return {"success": False, "error": "Находка не найдена"}
        if item.status != "rejected":
            return {"success": False, "error": "Сначала отклоните находку в проверке"}
        private = getattr(self, "_review_private", {}).get(str(finding_id))
        if not private:
            return {"success": False, "error": "Приватный контекст находки недоступен"}
        output_path = Path(private.get("output_path", ""))
        if not output_path.is_file() or output_path.is_symlink():
            return {"success": False, "error": "Результат документа не найден"}
        if str(finding_id) in getattr(self, "_review_rebuilt", set()):
            return {"success": True, "changed": False, "message": "Находка уже пересобрана"}

        try:
            from review_rebuild import rebuild_docx, rebuild_xlsx, rebuild_pdf

            suffix = output_path.suffix.casefold()
            if suffix in {".docx", ".docm"}:
                changed = rebuild_docx(
                    output_path, private["coordinate"], private["placeholder"],
                    private["original"], private.get("occurrence", 0),
                )
            elif suffix == ".xlsx":
                changed = rebuild_xlsx(
                    output_path, private["coordinate"], private["placeholder"],
                    private["original"], private.get("occurrence", 0),
                )
            elif suffix == ".pdf":
                changed = rebuild_pdf(
                    output_path, private["coordinate"], private["placeholder"],
                    private["original"], private.get("occurrence", 0),
                )
            else:
                return {"success": False, "error": "Координатная пересборка поддерживает DOCX, XLSX и обратимый PDF"}
            if not changed:
                return {"success": False, "error": "Плейсхолдер в указанной координате не найден"}

            # Сохраняем безопасные метаданные и обновляем сертификат: старый
            # hash результата после атомарной замены больше недействителен.
            metadata_temp = output_path.with_name(f".{output_path.stem}.review-metadata{output_path.suffix}")
            cleanup_report = clean_file_metadata(output_path, metadata_temp)
            if cleanup_report.error:
                raise RuntimeError(f"Очистка метаданных не выполнена: {cleanup_report.error}")
            os.replace(metadata_temp, output_path)
            source_path = Path(private.get("source_path", output_path))
            certificate = build_audit_certificate(
                source_path if source_path.is_file() else output_path,
                output_path,
                counts={"files": 1, "replacements_rebuilt": 1},
                review_queue=queue,
                cleanup_report=cleanup_report,
            )
            certificate_path = output_path.with_name(output_path.name + ".audit.json")
            write_audit_certificate(certificate, certificate_path)
            rebuilt_ids = getattr(self, "_review_rebuilt", None)
            if rebuilt_ids is None:
                rebuilt_ids = self._review_rebuilt = set()
            rebuilt_ids.add(str(finding_id))
            return {"success": True, "changed": True, "summary": queue.summary()}
        except Exception as exc:
            logger.warning("Координатная пересборка не выполнена: %s", exc)
            return {"success": False, "error": "Не удалось безопасно пересобрать документ"}

    def clear_review_findings(self):
        from privacy_audit import ReviewQueue

        self.review_queue = ReviewQueue()
        if getattr(self, "background_mode", False):
            self._review_private = {}
            return {"success": True}
        try:
            self.review_queue.save(self._review_queue_path)
        except Exception:
            pass
        # Empty only after obtaining the same OS-managed key.  If key access
        # is unavailable, leave the encrypted vault intact rather than writing
        # or replacing it with plaintext.
        try:
            self._review_vault.save({})
        except Exception:
            pass
        return {"success": True}

    def open_review_window(self):
        """Информирует о доступности интерфейса проверки сущностей."""
        return {"success": True}

    def save_qwen_settings(self, data):
        try:
            merged = {**self.qwen_settings.to_dict(), **(data or {})}
            self.qwen_settings = QwenPostprocessorSettings.from_mapping(merged)
            self._qwen_postprocessor.reset(self.qwen_settings)
            self.settings["qwen_postprocessor"] = self.qwen_settings.to_dict()
            self._save_settings()
            return {"success": True, **self.get_qwen_settings()}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def postprocess_anonymized_text(self, text):
        """Run Qwen only after Pullenti replacement, with a safe no-op fallback."""
        return self._qwen_postprocessor.process(
            text, cancel_check=getattr(self, "is_cancelled", None)
        )

    def get_qwen_model_info(self):
        """Возвращает детальную информацию о локальной модели Qwen, ресурсах и статусе."""
        from qwen_offline import (
            MANIFEST, TOTAL_BYTES, check_resources,
            get_default_model_dir, is_installed, validate_model
        )
        from qwen_postprocessor import choose_device, runtime_preflight

        model_dir = Path(self.qwen_settings.model_path) if self.qwen_settings.model_path else get_default_model_dir()
        installed = is_installed(model_dir)
        valid = validate_model(model_dir, verify_hash=False) if installed else False

        resources = check_resources(model_dir)
        preflight = runtime_preflight(self.qwen_settings)

        if self._model_operation_status:
            status = self._model_operation_status
            status_text = "Выполняется операция с моделью..."
        elif valid:
            status = "installed"
            status_text = "Установлена и готова к работе"
        elif installed and not valid:
            status = "corrupted"
            status_text = "Файлы модели повреждены или неполны"
        else:
            status = "not_installed"
            status_text = "Не установлена"

        return {
            "installed": installed,
            "valid": valid,
            "status": status,
            "status_text": status_text,
            "model_dir": str(model_dir),
            "enabled": self.qwen_settings.enabled,
            "device": choose_device(self.qwen_settings.device),
            "resources": resources,
            "preflight": preflight,
            "manifest": {
                "model_id": MANIFEST.get("model_id"),
                "revision": MANIFEST.get("revision"),
                "license": MANIFEST.get("license"),
                "license_url": MANIFEST.get("license_url"),
                "total_bytes": MANIFEST.get("total_bytes", TOTAL_BYTES),
                "weights_bytes": MANIFEST.get("weights_bytes"),
                "file_count": len(MANIFEST.get("files", [])),
            },
        }

    def install_qwen_model(self, consent=False):
        """Запускает фоновую загрузку модели Qwen с Hugging Face по явному согласию."""
        if not consent:
            return {"success": False, "error": "Загрузка Qwen требует явного согласия пользователя (consent=True)"}

        with self._model_operation_lock:
            if self._model_operation_status is not None:
                return {"success": False, "error": f"Операция с моделью уже выполняется ({self._model_operation_status})"}
            self._model_operation_status = "downloading"
            self._model_cancel_event.clear()

        def _worker():
            from qwen_offline import install_model, get_default_model_dir, TOTAL_BYTES
            target_dir = get_default_model_dir()
            logger.info("Запуск загрузки модели Qwen в %s", target_dir)
            try:
                def _on_progress(written, total, filename=""):
                    total = total or TOTAL_BYTES
                    pct = int(min(written / max(total, 1), 1.0) * 100)
                    msg = f"Загрузка {filename}: {written // (1024 * 1024)} МБ / {total // (1024 * 1024)} МБ ({pct}%)"
                    if getattr(self, "_window", None):
                        ui_bridge.call_js(self._window, "onModelInstallProgress", pct, msg, written, total)

                install_model(
                    target_dir,
                    consent=True,
                    progress=_on_progress,
                    cancel_check=self._model_cancel_event.is_set,
                )
                self.save_qwen_settings({
                    "model_path": str(target_dir),
                    "local_files_only": True,
                    "enabled": True,
                })
                logger.info("Модель Qwen успешно установлена в %s", target_dir)
                if getattr(self, "_window", None):
                    ui_bridge.call_js(self._window, "onModelInstallCompleted", True, "Модель успешно загружена и установлена")
            except Exception as exc:
                err_msg = str(exc)
                logger.warning("Ошибка установки модели Qwen: %s", err_msg)
                if getattr(self, "_window", None):
                    ui_bridge.call_js(self._window, "onModelInstallCompleted", False, err_msg)
            finally:
                with self._model_operation_lock:
                    self._model_operation_status = None

        thread = threading.Thread(target=_worker, name="QwenModelInstaller", daemon=True)
        self._model_operation_thread = thread
        thread.start()
        return {"success": True, "status": "started"}

    def import_qwen_model_bundle(self, bundle_path="", consent=False):
        """Импортирует готовый zip-бандл модели для изолированных офлайн-систем."""
        if not consent:
            return {"success": False, "error": "Импорт bundle требует явного согласия пользователя"}

        if not bundle_path and getattr(self, "_window", None):
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=('Архив модели Qwen (*.zip;*.bundle.zip)', 'Все файлы (*.*)')
            )
            if not result:
                return {"success": False, "cancelled": True, "error": "Файл архива не выбран"}
            bundle_path = result[0] if isinstance(result, (list, tuple)) else str(result)

        if not bundle_path or not os.path.isfile(bundle_path):
            return {"success": False, "error": f"Указанный файл архива не существует: {bundle_path}"}

        with self._model_operation_lock:
            if self._model_operation_status is not None:
                return {"success": False, "error": f"Операция с моделью уже выполняется ({self._model_operation_status})"}
            self._model_operation_status = "importing"
            self._model_cancel_event.clear()

        def _worker():
            from qwen_offline import import_bundle, get_default_model_dir
            target_dir = get_default_model_dir()
            logger.info("Запуск импорта zip-бандла Qwen из %s в %s", bundle_path, target_dir)
            try:
                def _on_progress(current, total, filename=""):
                    pct = int(min(current / max(total, 1), 1.0) * 100)
                    msg = f"Распаковка {filename}: файл {current} из {total} ({pct}%)"
                    if getattr(self, "_window", None):
                        ui_bridge.call_js(self._window, "onModelInstallProgress", pct, msg, current, total)

                import_bundle(
                    bundle_path,
                    target_dir,
                    consent=True,
                    progress=_on_progress,
                    cancel_check=self._model_cancel_event.is_set,
                )
                self.save_qwen_settings({
                    "model_path": str(target_dir),
                    "local_files_only": True,
                    "enabled": True,
                })
                logger.info("Модель Qwen успешно импортирована из архива в %s", target_dir)
                if getattr(self, "_window", None):
                    ui_bridge.call_js(self._window, "onModelInstallCompleted", True, "Модель успешно импортирована из архива")
            except Exception as exc:
                err_msg = str(exc)
                logger.warning("Ошибка импорта бандла Qwen: %s", err_msg)
                if getattr(self, "_window", None):
                    ui_bridge.call_js(self._window, "onModelInstallCompleted", False, err_msg)
            finally:
                with self._model_operation_lock:
                    self._model_operation_status = None

        thread = threading.Thread(target=_worker, name="QwenModelImporter", daemon=True)
        self._model_operation_thread = thread
        thread.start()
        return {"success": True, "status": "started"}

    def cancel_qwen_model_operation(self):
        """Отменяет активную загрузку или импорт модели."""
        logger.info("Запрос отмены операции с моделью Qwen")
        self._model_cancel_event.set()
        return {"success": True}

    def delete_qwen_model(self):
        """Безопасно удаляет файлы локальной модели из пользовательского каталога."""
        from qwen_offline import delete_model, get_default_model_dir
        target_dir = Path(self.qwen_settings.model_path) if self.qwen_settings.model_path else get_default_model_dir()
        logger.info("Удаление модели Qwen из %s", target_dir)
        try:
            success = delete_model(target_dir)
            self.save_qwen_settings({
                "model_path": "",
                "enabled": False,
            })
            self._qwen_postprocessor.reset(self.qwen_settings)
            if getattr(self, "_window", None):
                ui_bridge.call_js(self._window, "onModelDeleted", True)
            return {"success": True, "deleted": success, **self.get_qwen_model_info()}
        except Exception as exc:
            logger.error("Ошибка при удалении модели: %s", exc)
            return {"success": False, "error": str(exc)}

    def verify_qwen_model_integrity(self):
        """Выполняет проверку всех файлов модели по контрольным суммам SHA-256."""
        from qwen_offline import verify_model_integrity, get_default_model_dir
        target_dir = Path(self.qwen_settings.model_path) if self.qwen_settings.model_path else get_default_model_dir()
        try:
            report = verify_model_integrity(target_dir)
            return {"success": True, **report}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


    def create_claim_document(self, manifest_path, draft_text, output_name="Исковое заявление.docx"):
        """Publish a verified anonymised claim and bind it to the batch decoder.

        Drafting is intentionally outside this method: an agent receives only
        anonymised corpus.  This boundary validates IDs, applies the optional
        local Qwen pass and writes provenance for automatic restoration.
        """

        from claim_workflow import create_claim_from_verified_batch, load_verified_batch

        batch = load_verified_batch(manifest_path)
        safe_name = Path(str(output_name)).name
        if not safe_name.lower().endswith(".docx"):
            safe_name += ".docx"
        destination = batch.root / "Обезличенные документы" / safe_name
        qwen = self._qwen_postprocessor if getattr(self.qwen_settings, "enabled", False) else None
        return create_claim_from_verified_batch(
            manifest_path,
            destination,
            draft_builder=lambda _corpus, _tokens: str(draft_text),
            qwen_processor=qwen,
        )

    def get_app_info(self):
        """Возвращает информацию о версии и системных метаданных приложения."""
        return version.get_version_info()

    def get_capability_matrix(self):
        """Возвращает честную матрицу возможностей системы."""
        import capabilities
        return capabilities.get_system_capability_matrix(self)

    def get_safe_diagnostic_report(self):
        """Возвращает безопасный диагностический отчёт без путей, текста и PII."""
        import log_sanitizer
        return log_sanitizer.get_safe_diagnostic_report()

    def get_interrupted_batch_info(self):
        """Проверяет наличие незавершённой пакетной сессии после сбоя."""
        import crash_recovery
        return crash_recovery.get_interrupted_batch()

    def discard_interrupted_batch(self):
        """Сбрасывает незавершённую сессию."""
        import crash_recovery
        return crash_recovery.discard_interrupted_batch()

    def resume_interrupted_batch(self):
        """Возобновляет обработку прерванного пакета файлов с сохранением batch_id и дешифратора."""
        import crash_recovery
        info = crash_recovery.get_interrupted_batch()
        if not info or not info.get("remaining_files"):
            return {"success": False, "error": "Нет прерванной сессии для продолжения"}

        remaining = info["remaining_files"]
        batch_id = info.get("batch_id")
        decrypted_mapping = crash_recovery.load_interrupted_batch_mapping(batch_id)
        logger.info("Возобновление прерванной сессии %s: осталось файлов %d, восстановлено %d сущностей", batch_id, len(remaining), len(decrypted_mapping))
        self.process_files(remaining, batch_id=batch_id, initial_mapping=decrypted_mapping)
        return {"success": True, "resumed_files_count": len(remaining), "batch_id": batch_id}

    def show_support_dialog(self):
        if self._window: ui_bridge.ui_show_support_modal(self._window, "https://pay.cloudtips.ru/p/3bde1c71")

    def open_telegram_channel(self):
        import webbrowser
        webbrowser.open('https://t.me/pro_servitude')

    def open_author_telegram(self):
        import webbrowser
        webbrowser.open('https://t.me/aebeloglazov')

    def open_bug_email(self):
        import webbrowser
        webbrowser.open('mailto:pro.servitude@gmail.com')

    def sync_exclusions_file_with_unified(self):
        """Синхронизирует файл Исключения.txt с UNIFIED_EXCLUSIONS (обновляет, если отличается)."""
        try:
            exclusions_set = set(UNIFIED_EXCLUSIONS)
            file_path = self.exclusions_file_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            current_file_set = set()
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        word = line.strip()
                        if word and not word.startswith('#'):
                            current_file_set.add(word)
            if exclusions_set != current_file_set:
                header_comment = (
                    "# Список слов-исключений для DOCXдодыр\n"
                    "# Каждое слово или фраза на новой строке.\n"
                    "# Строки, начинающиеся с '#', игнорируются при загрузке.\n\n"
                )
                content = header_comment + "".join(f"{word}\n" for word in sorted(list(exclusions_set)))
                app_paths.atomic_write_text(file_path, content)
                _private_status(f"Файл '{file_path}' синхронизирован с UNIFIED_EXCLUSIONS ({len(exclusions_set)} слов).")
            else:
                _private_status(f"Файл '{file_path}' уже актуален ({len(exclusions_set)} слов).")
        except Exception as e:
            _private_status(f"Ошибка синхронизации файла исключений: {e}")



    def open_file_dialog(self):
        pass # handled by main.py wrapper

    def show_error(self, title, message=None):
        if message is None:
            message = title
            title = "Ошибка"
        if getattr(self, "is_quick_mode", False) and self._window:
            ui_bridge.ui_set_global_progress(self._window, True, f"DOCXдодыр — {title}", str(message), 0)
            return
        if self._window:
            ui_bridge.ui_alert(self._window, f"{title}: {message}")

    def process_files(self, files, batch_id=None, initial_mapping=None):
        if not self._pullenti_processor:
            self.init_pullenti()
        if not self._pullenti_processor:
            self.show_error("Pullenti не инициализирован. Обработка невозможна.")
            return

        if not self.start_operation("anonymize_files", description="Обезличивание файлов"):
            return

        self.reset_cancellation()
        if self._window:
            ui_bridge.ui_set_global_progress(self._window, True, 'Обезличивание', 'Подготовка файлов...', 0)
        
        t = threading.Thread(target=self._run_process_files, args=(files, batch_id, initial_mapping), daemon=True)
        self._active_threads.append(t)
        self._op_manager.register_thread(t)
        t.start()

    def process_folder(
        self, folder_path, recursive=True, output_dir_name="Обезличенные документы",
        continue_existing=False, progress_callback=None,
    ):
        """Обезличивает все поддерживаемые документы в выбранной папке."""
        from folder_pipeline import FolderAnonymizationPipeline

        if not self._pullenti_processor:
            self.init_pullenti()
        if not self._pullenti_processor:
            self.show_error("Pullenti не инициализирован. Обработка невозможна.")
            return None

        if not self.start_operation("anonymize_folder", description="Обезличивание папки"):
            return None

        self.reset_cancellation()
        pipeline = FolderAnonymizationPipeline(self, output_dir_name=output_dir_name)
        try:
            result = pipeline.process(
                folder_path, recursive=bool(recursive),
                progress_callback=progress_callback or self.update_progress,
                continue_existing=bool(continue_existing),
            )
            if result and result.output_dir:
                self._last_output_dir = str(result.output_dir)
            cancelled = bool(result and self.is_cancelled())
            status = (
                "succeeded"
                if result and result.error_count == 0 and not cancelled
                else ("partial" if result and result.processed_count > 0 else "failed")
            )
            self.finish_operation(status)
            return result
        except Exception:
            self.finish_operation("failed")
            raise
        finally:
            if self._window:
                ui_bridge.ui_set_global_progress(self._window, False)

    def open_output_folder_in_explorer(self, folder_path=""):
        """Открывает указанную папку или последнюю папку с результатами в проводнике."""
        target = folder_path or getattr(self, "_last_output_dir", None)
        if target:
            success = app_paths.open_folder_in_file_manager(target)
            return {"success": success, "path": str(target)}
        return {"success": False, "error": "Папка для открытия не указана"}

    def _run_process_files(self, files, batch_id=None, initial_mapping=None):
        worker = Worker(files, self, batch_id=batch_id, initial_mapping=initial_mapping)
        worker.run()

    def update_progress(self, current_step, max_steps, status_text, percent=None):
        if self._window:
            if percent is None:
                percent = int((current_step / max(max_steps, 1)) * 100)
            ui_bridge.ui_set_global_progress(self._window, True, 'Обработка', status_text, percent)

    def _on_ai_analysis_started(self):
        if self._window:
            ui_bridge.ui_set_global_progress(self._window, True, 'ИИ-Анализ', 'Подготовка к анализу...', 0)

    def _on_ai_analysis_progress(self, stage, message):
        if self._window:
            percent = int((min(stage, 8) / 8) * 100)
            ui_bridge.ui_set_global_progress(self._window, True, 'ИИ-Анализ', message, percent)

    def _on_ai_analysis_finished(self, analyzed_count):
        if self._window:
            ui_bridge.ui_set_global_progress(self._window, True, 'ИИ-Анализ', f'✅ Проанализировано файлов: {analyzed_count}', 100)

    def on_worker_finished(self, processed_count, error_count, total_changes_all_files):
        if getattr(self, "is_quick_mode", False):
            return
        if self._window:
            msg = f"Обработка завершена. Успешно: {processed_count}. Ошибок: {error_count}. Замен: {total_changes_all_files}"
            ui_bridge.ui_set_global_progress(self._window, False)
            ui_bridge.ui_alert(self._window, msg)

    def get_paragraph_text_with_revisions(self, para_obj):
        """Извлекает весь видимый текст, включая runs внутри hyperlink, ins, и переносы строк br.

        В используемой версии python-docx ``paragraph.text`` не включает
        текст гиперссылок и вставок рецензирования (w:ins), а также теги w:br.
        """
        try:
            p_elem = getattr(para_obj, "_element", None)
            if p_elem is None:
                return (para_obj.text or "") if hasattr(para_obj, "text") else ""
            chunks = []
            skip_container_tags = {
                _W + "drawing", _W + "pict", _W + "txbxContent",
                "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent",
            }

            def _collect(element):
                for child in element:
                    tag = child.tag
                    if (
                        tag in skip_container_tags
                        or tag.endswith("AlternateContent")
                        or tag.endswith("drawing")
                        or tag.endswith("pict")
                        or tag.endswith("txbxContent")
                    ):
                        continue
                    if tag == _W + "t":
                        chunks.append(child.text or "")
                    elif tag == _W + "br":
                        chunks.append("\n")
                    else:
                        _collect(child)

            _collect(p_elem)
            return "".join(chunks)
        except Exception:
            return ""

    def postprocess_placeholder_tails(self, text):
        """Совместимость со старым вызовом; позиционные замены не требуют очистки.

        Раньше здесь вслепую удалялись 1–2 буквы после ``]``. Это скрывало
        ошибку inclusive/exclusive в координатах Pullenti и могло повреждать
        нормальный текст после плейсхолдера.
        """
        return text




    def replace_text_in_paragraph_xml(self, para_obj, original_text, cleaned_text, log_data, context_prefix, replacements_list=None):
        """Замена текста в параграфе с полным сохранением структуры, стилей runs и оформления."""
        try:
            success = False
            if replacements_list:
                success = replace_spans_in_paragraph_xml(para_obj, replacements_list)
            if not success:
                success = replace_diff_in_paragraph_xml(para_obj, original_text, cleaned_text)

            if success:
                log_data.append(f"{context_prefix}DEBUG: Параграф обновлен in-place с сохранением всех стилей runs.")
            else:
                para_obj.text = cleaned_text
                log_data.append(f"{context_prefix}DEBUG: Fallback замена текста параграфа.")
            if self.get_paragraph_text_with_revisions(para_obj) != cleaned_text:
                raise RuntimeError("записанный текст не совпадает с обезличенным результатом")
        except Exception as e:
            log_data.append(f"{context_prefix}DEBUG: Ошибка in-place замены: {e}, fallback")
            try:
                para_obj.text = cleaned_text
            except Exception as fallback_error:
                raise RuntimeError("не удалось записать обезличенный текст в DOCX") from fallback_error
            if self.get_paragraph_text_with_revisions(para_obj) != cleaned_text:
                raise RuntimeError("fallback не записал обезличенный текст в DOCX") from e

    def process_single_file(
        self, file_path_str, exclusions_list, custom_replacements_list,
        batch_mapping=None, batch_entity_seen=None, defer_decoder=False,
        output_dir=None, decoder_path=None, progress_callback=None,
    ):
        # The workbook must survive every requested export, but be released
        # on processing errors as well as after the final successful save.
        from contextlib import ExitStack
        with ExitStack() as resources:
            return self._process_single_file(
                file_path_str, exclusions_list, custom_replacements_list,
                batch_mapping, batch_entity_seen, defer_decoder,
                output_dir, decoder_path, resources=resources,
                progress_callback=progress_callback,
            )

    def _process_single_file(
        self, file_path_str, exclusions_list, custom_replacements_list,
        batch_mapping=None, batch_entity_seen=None, defer_decoder=False,
        output_dir=None, decoder_path=None, *, resources,
        progress_callback=None,
    ):
        file_p = Path(file_path_str)
        if not file_p.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path_str}")

        # The GUI initializes Pullenti in the background after the window is
        # shown.  If the user starts immediately, wait for that one existing
        # initialization instead of rejecting a valid document.
        if self._pullenti_processor is None and not self.init_pullenti():
            raise RuntimeError("Pullenti не инициализирован. Анонимизация невозможна.")

        _private_status("Начало обработки документа")
        _private_status(f"DEBUG: Переданы пользовательские замены: {len(custom_replacements_list)} шт.")

        suffix = file_p.suffix.lower()
        if suffix == ".doc":
            raise ValueError("Устаревший бинарный формат .doc не поддерживается. Сохраните документ как .docx в Word перед обработкой.")
        if suffix == ".xls":
            raise ValueError("Устаревший бинарный формат .xls не поддерживается. Сохраните документ как .xlsx в Excel перед обработкой.")
        is_word = suffix in (".docx", ".docm")
        is_excel = suffix in (".xlsx", ".xlsm")
        is_pdf = suffix == ".pdf"
        is_image = suffix in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp")

        total_replacements_in_file = 0
        log_data = []
        generated_outputs = []
        current_target_file = None
        temp_source_for_pdf = None
        if not getattr(self, "_active_batch_id", None):
            self._active_batch_id = str(uuid.uuid4())
        self._active_document_ref = file_p.name
        self._active_source_path = str(file_p.resolve())
        self._active_review_location = "text"
        self._active_review_coordinate = {"scope": "body", "kind": "paragraph", "index": 0}
        self._active_ocr_span_map = []
        create_decoder = getattr(self, 'save_decoder', False)
        # Numbered identities are always enabled.  A Worker passes the same
        # dictionaries to every file selected in one batch, so one person or
        # organisation keeps one ID across DOCX/XLSX/PDF files.  Decoder
        # persistence remains optional and does not control identity quality.
        mapping_dict = batch_mapping if batch_mapping is not None else {}
        entity_seen = batch_entity_seen if batch_entity_seen is not None else {}

        if is_word:
            # DOCX / DOCM
            # python-docx omits text inside tracked insertions from
            # ``paragraph.text``.  Accept revisions in a private temporary
            # copy before analysis: inserted text becomes ordinary visible
            # content, deleted text is discarded, and the source is untouched.
            prepared_word_path = file_path_str
            revision_temp_path = None
            docm_read_path = None
            try:
                from hidden_data import inspect_hidden_data, sanitize_hidden_data

                revision_report = inspect_hidden_data(file_p)
                if revision_report.detected.get("tracked_changes"):
                    fd, temp_name = tempfile.mkstemp(prefix=".docxdodyr-revisions-", suffix=file_p.suffix)
                    os.close(fd)
                    revision_temp_path = Path(temp_name)
                    sanitize_hidden_data(
                        file_p, revision_temp_path,
                        remove_comments=False, remove_footnotes=False,
                        remove_tracked_changes=True, remove_hidden_text=False,
                        remove_attachments=False, remove_annotations=False,
                        remove_image_metadata=False, strict=True,
                        preflight_report=revision_report,
                    )
                    prepared_word_path = str(revision_temp_path)
                if suffix == ".docm":
                    from document_restorer import _docm_as_docx_for_reading
                    docm_read_path = _docm_as_docx_for_reading(prepared_word_path)
                    prepared_word_path = str(docm_read_path)
                doc = Document(prepared_word_path)
            finally:
                if docm_read_path is not None:
                    docm_read_path.unlink(missing_ok=True)
                if revision_temp_path is not None:
                    revision_temp_path.unlink(missing_ok=True)
            total_replacements_in_file, log_data = self.clean_document(
                doc, exclusions_list, custom_replacements_list,
                mapping_dict=mapping_dict, entity_seen=entity_seen,
                progress_callback=progress_callback,
            )
        elif is_excel:
            # Excel-файлы: обезличиваем поячейчно
            try:
                from openpyxl import load_workbook
            except ImportError as exc:
                raise RuntimeError(
                    "Для обработки Excel требуется библиотека openpyxl. "
                    "Установите зависимости из requirements.txt."
                ) from exc
            from xlsx_semantic import (
                generic_numeric_fallback, header_map, mask_filename,
                mask_sheet_title, sheet_profile, targeted_cell,
            )
            from xlsx_semantic import close_workbook
            if progress_callback:
                try:
                    progress_callback(0, 100, "Загрузка таблицы Excel...")
                except Exception:
                    pass
            wb = load_workbook(file_path_str, keep_vba=(suffix == ".xlsm"))
            resources.callback(close_workbook, wb)
            xlsx_resolver = EntityResolver(entity_seen, mapping_dict)

            def xlsx_placeholder(label, original):
                base = self.current_placeholders.get(label, f"[{label}]")
                return xlsx_resolver.resolve(str(original), label, base)

            # Sheet titles and defined names are part of the workbook privacy
            # surface.  Rename first, then repair defined-name formula text.
            title_changes = {}
            for ws_index, ws in enumerate(wb.worksheets, 1):
                safe_title = mask_sheet_title(ws.title, xlsx_placeholder)
                if safe_title != ws.title:
                    original_safe = safe_title
                    counter = 1
                    # Sheet titles are case-insensitive in Excel, openpyxl treats them as such in dictionary.
                    # We compare against all other sheet titles.
                    existing_titles = {s.title.lower() for s in wb.worksheets if s != ws}
                    while safe_title.lower() in existing_titles:
                        suffix = f"_{counter}"
                        # Ensure the total length does not exceed 31
                        safe_title = original_safe[:31 - len(suffix)] + suffix
                        counter += 1

                    title_changes[ws.title] = safe_title
                    ws.title = safe_title
                    total_replacements_in_file += 1
            if title_changes:
                for defined in wb.defined_names.values():
                    if not getattr(defined, "attr_text", None):
                        continue
                    for old, new in title_changes.items():
                        defined.attr_text = defined.attr_text.replace(
                            "'" + old.replace("'", "''") + "'!",
                            "'" + new.replace("'", "''") + "'!",
                        )
            total_excel_steps = sum(max(getattr(ws, 'max_row', 1) or 1, 1) for ws in wb.worksheets)
            excel_step = 0
            for ws_index, ws in enumerate(wb.worksheets, 1):
                # Build the semantic grid before changing any cell.  A label
                # in a neighbouring cell (e.g. ``ИНН``) is sent to Pullenti
                # as context, while the replacement remains local to the
                # original value cell.
                rows = [[cell.value for cell in row] for row in ws.iter_rows()]
                contexts = {
                    (item.row, item.column): item
                    for item in build_table_contexts(rows)
                }
                profile = sheet_profile(rows, ws.title)
                headers = header_map(rows)
                for row_index, row in enumerate(ws.iter_rows()):
                    excel_step += 1
                    if progress_callback and (excel_step % 20 == 0 or excel_step >= total_excel_steps or excel_step == 1):
                        try:
                            progress_callback(excel_step, max(total_excel_steps, 1), f"Лист '{ws.title}', стр. {row_index + 1}")
                        except Exception:
                            pass
                    for col_index, cell in enumerate(row):
                        try:
                            if cell.value is not None:
                                if isinstance(cell.value, float) and cell.value.is_integer():
                                    raw_cell_str = str(int(cell.value))
                                elif isinstance(cell.value, (int, float)):
                                    raw_cell_str = str(cell.value)
                                elif isinstance(cell.value, str):
                                    raw_cell_str = cell.value
                                else:
                                    raw_cell_str = str(cell.value)

                                if raw_cell_str.strip():
                                    cell_context = contexts.get((row_index, col_index))
                                    self._active_review_location = f"Excel:{ws.title}!{cell.coordinate}"
                                    self._active_review_coordinate = {
                                        "scope": "sheet", "kind": "cell",
                                        "sheet": ws.title, "cell": cell.coordinate,
                                    }
                                    cleaned_text, repl_count, handled = targeted_cell(
                                        raw_cell_str, profile, row_index, col_index,
                                        headers, xlsx_placeholder,
                                    )
                                    logs = []
                                    if not handled:
                                        cleaned_text, repl_count, logs = self.anonymize_text_pullenti(
                                            raw_cell_str, exclusions_list, custom_replacements_list, prev_paragraphs=None,
                                            mapping_dict=mapping_dict, entity_seen=entity_seen,
                                            semantic_context=cell_context,
                                        )
                                        fallback, fallback_count = generic_numeric_fallback(
                                            cleaned_text,
                                            getattr(cell_context, "label", None),
                                            xlsx_placeholder,
                                        )
                                        cleaned_text = fallback
                                        repl_count += fallback_count
                                    if repl_count > 0:
                                        cell.value = cleaned_text
                                        total_replacements_in_file += repl_count
                                        log_data.extend(
                                            f"[Excel:{ws.title}!{cell.coordinate}] {ln}" for ln in logs
                                        )
                        except Exception as e:
                            # Do not publish a workbook when one cell could not
                            # be analysed: the untouched value may contain PII.
                            # Keep diagnostics coordinate-only and fail closed.
                            coordinate = f"Sheet#{ws_index}!{getattr(cell, 'coordinate', '?')}"
                            logger.warning("Ошибка обработки ячейки Excel %s", coordinate)
                            raise RuntimeError(
                                f"Не удалось безопасно обработать ячейку Excel: {coordinate}"
                            ) from e
            # Raster drawings can contain names/logos that are invisible to
            # cell scanners.  In strict anonymisation mode they cannot be
            # certified safely, so remove them while preserving native charts.
            for ws in wb.worksheets:
                embedded_images = len(getattr(ws, "_images", ()))
                if embedded_images:
                    ws._images = []
                    total_replacements_in_file += embedded_images
                    log_data.append(
                        f"[Excel:{ws.title}] Удалено встроенных растровых изображений: {embedded_images}"
                    )
            if progress_callback:
                try:
                    progress_callback(total_excel_steps, max(total_excel_steps, 1), "Обработка Excel завершена")
                except Exception:
                    pass
        elif is_pdf:
            # PDF:
            # - Word->PDF (текстовый): извлекаем текст без OCR
            # - скан: OCR
            raw_text, method, ocr_details = pdf_to_text_auto_detailed(
                str(file_p),
                ocr_lang=getattr(self, "ocr_lang", "rus+eng"),
                preferred_backend=getattr(self, "preferred_ocr", None),
                cancel_check=self.is_cancelled,
            )
            _private_status(f"[PDF] Сценарий обработки: {method}")
            if ocr_details is not None:
                _private_status(f"[OCR] Фактический движок: {ocr_details.backend}")
            cleaned_parts = []
            if ocr_details is not None:
                # Pullenti has practical limits on a single analysis source.
                # Process OCR page-by-page so a long scan cannot be silently
                # anonymised only at the beginning while preserving one shared
                # batch entity registry and page coordinates.
                for page in ocr_details.pages:
                    page_text = page.text
                    page_source = getattr(page, "source", "ocr")
                    page_result = type(ocr_details)(
                        text=page_text,
                        pages=(page,),
                        backend=ocr_details.backend,
                    )
                    self._active_ocr_span_map = (
                        build_ocr_span_map(page_text, page_result)
                        if page_source == "ocr" else []
                    )
                    page_kind = "ocr" if page_source == "ocr" else "text"
                    self._active_review_location = f"PDF:{page_kind.upper()}:стр.{page.page}"
                    self._active_review_coordinate = {
                        "scope": "pdf", "kind": page_kind,
                        "page": max(0, page.page - 1),
                    }
                    cleaned_page, page_count, page_logs = self.anonymize_text_pullenti(
                        page_text, exclusions_list, custom_replacements_list,
                        prev_paragraphs=None, mapping_dict=mapping_dict,
                        entity_seen=entity_seen,
                    )
                    cleaned_parts.append(cleaned_page)
                    total_replacements_in_file += page_count
                    log_data.extend(f"[PDF стр.{page.page}] {line}" for line in page_logs)
                cleaned_text = "\n\n".join(cleaned_parts)
            else:
                # Native PDF extraction already separates pages/blocks with
                # blank lines.  Keep each analysis request bounded.
                pieces = re.split(r"(\n\s*\n+)", raw_text)
                for piece in pieces:
                    if not piece.strip() or re.fullmatch(r"\n\s*\n+", piece):
                        cleaned_parts.append(piece)
                        continue
                    # Разбивка по границам предложений с максимумом ~8000 символов
                    sentences = re.split(r'(?<=[.!?;])\s+', piece)
                    fragments = []
                    curr_frag = []
                    curr_len = 0
                    for sent in sentences:
                        sent_len = len(sent)
                        if curr_frag and (curr_len + 1 + sent_len > 8000):
                            fragments.append(" ".join(curr_frag))
                            curr_frag = [sent]
                            curr_len = sent_len
                        else:
                            curr_frag.append(sent)
                            curr_len += (sent_len + 1 if len(curr_frag) > 1 else sent_len)
                    if curr_frag:
                        fragments.append(" ".join(curr_frag))
                    if not fragments:
                        fragments = [piece]

                    for fragment in fragments:
                        cleaned_fragment, fragment_count, fragment_logs = self.anonymize_text_pullenti(
                            fragment, exclusions_list, custom_replacements_list,
                            prev_paragraphs=None, mapping_dict=mapping_dict,
                            entity_seen=entity_seen,
                        )
                        cleaned_parts.append(cleaned_fragment)
                        total_replacements_in_file += fragment_count
                        log_data.extend(fragment_logs)
                cleaned_text = "".join(cleaned_parts)
            # Сконструируем DOCX из обезличенного текста с сохранением структуры
            doc = Document()
            for paragraph_text in reconstruct_structured_paragraphs(cleaned_text):
                doc.add_paragraph(paragraph_text)

            # Для PDF-источника итоговый целевой формат — обезличенный PDF.
            # DOCX остаётся как промежуточный/дополнительный результат (если включено сохранение).
        elif is_image:
            # Изображения (JPG, PNG, TIFF, BMP, WEBP): распознаем через OCR
            from ocr_backend import ocr_image_to_result
            ocr_details = ocr_image_to_result(
                str(file_p),
                ocr_lang=getattr(self, "ocr_lang", "rus+eng"),
                preferred_backend=getattr(self, "preferred_ocr", None),
                cancel_check=self.is_cancelled,
            )
            _private_status(f"[OCR] Фактический движок: {ocr_details.backend}")
            self._active_ocr_span_map = (
                build_ocr_span_map(ocr_details.text, ocr_details)
                if ocr_details.text else []
            )
            self._active_review_location = "Image:OCR"
            self._active_review_coordinate = {"scope": "image", "kind": "ocr", "page": 0}

            cleaned_parts = []
            pieces = re.split(r"(\n\s*\n+)", ocr_details.text)
            for piece in pieces:
                if not piece.strip() or re.fullmatch(r"\n\s*\n+", piece):
                    cleaned_parts.append(piece)
                    continue
                for offset in range(0, len(piece), 8000):
                    fragment = piece[offset:offset + 8000]
                    cleaned_fragment, fragment_count, fragment_logs = self.anonymize_text_pullenti(
                        fragment, exclusions_list, custom_replacements_list,
                        prev_paragraphs=None, mapping_dict=mapping_dict,
                        entity_seen=entity_seen,
                    )
                    cleaned_parts.append(cleaned_fragment)
                    total_replacements_in_file += fragment_count
                    log_data.extend(fragment_logs)
            cleaned_text = "".join(cleaned_parts)

            doc = Document()
            for paragraph_text in reconstruct_structured_paragraphs(cleaned_text):
                doc.add_paragraph(paragraph_text)
        else:
            raise ValueError(f"Неподдерживаемый тип файла: {file_p.suffix}")

        # Пути сохранения
        # ``output_dir`` is used by the folder pipeline.  The source is always
        # read from its original location, while all generated artefacts are
        # written below the caller-provided directory.  Keeping this decision
        # here (instead of copying files first) also preserves the existing
        # single-file API and prevents accidental writes next to user files.
        if output_dir is not None:
            output_root = Path(output_dir)
            output_root.mkdir(parents=True, exist_ok=True)
            test_probe = output_root / f".docxdodyr_write_test_{uuid.uuid4().hex[:8]}"
            test_probe.touch()
            test_probe.unlink(missing_ok=True)
        else:
            output_root = file_p.parent
            try:
                output_root.mkdir(parents=True, exist_ok=True)
                test_probe = output_root / f".docxdodyr_write_test_{uuid.uuid4().hex[:8]}"
                try:
                    test_probe.touch()
                    test_probe.unlink(missing_ok=True)
                except (OSError, PermissionError):
                    raise PermissionError("Directory not writable")
            except (OSError, PermissionError):
                fallback_dir = Path.home() / "Downloads" / "DOCXdodyr_Output"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                logger.warning(
                    "Папка %s недоступна для записи (образ диска DMG или защищенная папка). Сохраняем результат в: %s",
                    output_root, fallback_dir
                )
                output_root = fallback_dir
        self._last_output_dir = str(output_root)

        if is_excel:
            safe_name = mask_filename(file_p.name, xlsx_placeholder)
            output_base = output_root / safe_name
        else:
            output_base = output_root / file_p.name
        log_path = get_unique_filename(output_base, "_cleaned_log", ".txt")

        current_target_file = None
        try:
            opt_word = bool(getattr(self, 'save_docx', False))
            opt_pdf = bool(getattr(self, 'save_pdf', False))
            opt_markdown = bool(getattr(self, 'save_markdown', False))

            if not opt_word and not opt_pdf and not opt_markdown:
                # По умолчанию (когда ни один тумблер форматов не включен):
                # сохраняем в том же формате, что и был загружен:
                # DOCX→DOCX, DOCM→DOCM, XLSX→XLSX, XLSM→XLSM, PDF→PDF, изображения→PDF.
                # Ровно один результат на входной файл!
                save_word = is_word
                save_excel = is_excel
                save_pdf = is_pdf or is_image
                save_markdown = False
            else:
                # Включены явные тумблеры форматов:
                # Сохраняем только выбранные форматы, не добавляя исходный формат автоматически.
                # Word для Excel дает DOCX, а не XLSX!
                save_word = opt_word
                save_excel = False
                save_pdf = opt_pdf
                save_markdown = opt_markdown

            # 1. Сохранение в формате Word (.docx / .docm)
            if save_word:
                word_ext = ".docx" if (is_pdf or is_image or is_excel) else (".docm" if suffix == ".docm" else ".docx")
                word_path = get_unique_filename(output_base, "_cleaned", word_ext)
                current_target_file = word_path
                _private_status(f"DEBUG: Начинаем сохранение документа Word в: {word_path}")
                _private_status(f"DEBUG: Общее количество замен в документе: {total_replacements_in_file}")
                if is_excel:
                    excel_to_docx(wb, word_path)
                else:
                    if suffix == ".docm":
                        from document_restorer import save_docm_preserving_vba
                        save_docm_preserving_vba(file_p, doc, word_path)
                    else:
                        doc.save(str(word_path))
                _private_status("DEBUG: Файл Word успешно сохранён")
                _private_status(f"Сохранен обезличенный файл: {word_path}")
                if word_path.exists():
                    generated_outputs.append(word_path)
                current_target_file = None

            # 2. Сохранение в формате Excel (.xlsx / .xlsm)
            if save_excel and is_excel:
                excel_ext = ".xlsm" if suffix == ".xlsm" else ".xlsx"
                excel_path = get_unique_filename(output_base, "_cleaned", excel_ext)
                current_target_file = excel_path
                _private_status(f"DEBUG: Начинаем сохранение документа Excel в: {excel_path}")
                _private_status(f"DEBUG: Общее количество замен в документе: {total_replacements_in_file}")
                wb.save(str(excel_path))
                _private_status(f"DEBUG: Файл Excel успешно сохранён ({excel_path.suffix.upper()}): {excel_path}")
                _private_status(f"Сохранен обезличенный файл: {excel_path}")
                if excel_path.exists():
                    generated_outputs.append(excel_path)
                current_target_file = None

            # 3. Сохранение в формате Markdown (.md)
            if save_markdown:
                from markdown_export import save_as_markdown
                md_path = get_unique_filename(output_base, "_cleaned", ".md")
                current_target_file = md_path
                _private_status(f"DEBUG: Начинаем сохранение в Markdown: {md_path}")
                if is_excel:
                    save_as_markdown(wb, md_path)
                else:
                    save_as_markdown(doc, md_path)
                if md_path.exists():
                    _private_status(f"Сохранен Markdown файл: {md_path}")
                    _private_status(f"DEBUG: Markdown создан, размер: {md_path.stat().st_size} байт")
                    generated_outputs.append(md_path)
                current_target_file = None

            # 4. Сохранение в формате PDF (.pdf)
            source_for_pdf = None
            temp_source_for_pdf = None
            temp_pdf_dir = None
            if save_pdf:
                temp_pdf_dir = tempfile.mkdtemp(prefix="docxdodyr-pdf-")
                if is_excel:
                    temp_source_for_pdf = Path(temp_pdf_dir) / f"{file_p.stem}_temp_for_pdf.xlsx"
                    wb.save(str(temp_source_for_pdf))
                    source_for_pdf = temp_source_for_pdf
                    _private_status(f"DEBUG: Создан временный XLSX для PDF: {temp_source_for_pdf}")
                else:
                    temp_source_for_pdf = Path(temp_pdf_dir) / f"{file_p.stem}_temp_for_pdf.docx"
                    doc.save(str(temp_source_for_pdf))
                    source_for_pdf = temp_source_for_pdf
                    _private_status(f"DEBUG: Создан временный DOCX для PDF: {temp_source_for_pdf}")

                # pdf_converter_ready проверяет, есть ли хотя бы один рабочий путь к PDF:
                # - LibreOffice/Word (полноценная конвертация с форматированием)
                # - PyMuPDF fallback (только для PDF/Image/DOCX источников)
                # Для XLSX без LibreOffice fallback работает, но качество PDF низкое
                # (нет пересчёта формул, упрощённый layout), поэтому предупреждаем.
                has_full_converter = PDF_CONVERSION_AVAILABLE or (
                    callable(convert_to_pdf) and convert_to_pdf is not getattr(pdf_convert, 'convert_to_pdf', None)
                )
                has_pymupdf_fallback = is_pdf or is_image or is_word
                pdf_converter_ready = has_full_converter or has_pymupdf_fallback or is_excel

                # Для XLSX без LibreOffice/Word показываем предупреждение о fallback
                if is_excel and not has_full_converter and getattr(self, '_window', None):
                    ui_bridge.ui_alert(
                        self._window,
                        "PDF создан в режиме совместимости (без LibreOffice).\n"
                        "Форматирование таблицы и формулы могут не отображаться.\n"
                        "Для полноценного PDF установите LibreOffice (бесплатно: https://www.libreoffice.org)."
                    )

                if not pdf_converter_ready or not source_for_pdf:
                    if temp_source_for_pdf and temp_source_for_pdf.exists():
                        temp_source_for_pdf.unlink(missing_ok=True)
                    if temp_pdf_dir and Path(temp_pdf_dir).exists():
                        shutil.rmtree(temp_pdf_dir, ignore_errors=True)
                    for partial_output in generated_outputs:
                        try:
                            partial_output.unlink(missing_ok=True)
                        except OSError:
                            pass
                    raise IOError(
                        "Обязательный PDF-результат не создан: конвертер PDF недоступен"
                    )

                try:
                    pdf_path = get_unique_filename(output_base, "_cleaned", ".pdf")
                    current_target_file = pdf_path
                    _private_status(f"DEBUG: Начинаем конвертацию в PDF: {pdf_path}")
                    converted = (
                        convert_docx_text_to_pdf(source_for_pdf, pdf_path)
                        if (is_pdf or is_image) else convert_to_pdf(source_for_pdf, pdf_path)
                    )
                    if converted and pdf_path.exists():
                        _private_status(f"Сохранен PDF файл: {pdf_path}")
                        _private_status(f"DEBUG: PDF создан, размер: {pdf_path.stat().st_size} байт")
                        generated_outputs.append(pdf_path)
                        current_target_file = None
                    else:
                        raise Exception("Все методы конвертации PDF не сработали")
                except Exception as pdf_error:
                    error_msg = str(pdf_error)
                    _private_status(f"Ошибка конвертации в PDF: {error_msg}")
                    if getattr(self, '_window', None):
                        ui_bridge.ui_alert(self._window, f"Для сохранения в PDF на вашем компьютере должен быть установлен Microsoft Word (или LibreOffice).\n\nТехническая ошибка: {error_msg}")
                    if current_target_file is not None:
                        try:
                            current_target_file.unlink(missing_ok=True)
                        except OSError:
                            pass
                    for partial_output in generated_outputs:
                        try:
                            partial_output.unlink(missing_ok=True)
                        except OSError:
                            pass
                    raise IOError(
                        f"Обязательный PDF-результат не создан: {error_msg}"
                    ) from pdf_error
                finally:
                    if temp_source_for_pdf and temp_source_for_pdf.exists():
                        try:
                            temp_source_for_pdf.unlink(missing_ok=True)
                            _private_status(f"DEBUG: Временный файл удален: {temp_source_for_pdf}")
                        except Exception:
                            pass
                    if temp_pdf_dir and Path(temp_pdf_dir).exists():
                        try:
                            shutil.rmtree(temp_pdf_dir, ignore_errors=True)
                        except Exception:
                            pass

            # 5. Промежуточный рабочий файл для пакетной сверки папок (при defer_decoder=True)
            if defer_decoder:
                has_word_or_excel = any(p.suffix.lower() in {".docx", ".docm", ".xlsx", ".xlsm"} for p in generated_outputs)
                if not has_word_or_excel:
                    rec_ext = ".xlsm" if suffix == ".xlsm" else (".xlsx" if is_excel else ".docx")
                    reconcile_temp_path = output_base.parent / f".{output_base.name}.reconcile_temp{rec_ext}"
                    if is_excel:
                        wb.save(str(reconcile_temp_path))
                    else:
                        doc.save(str(reconcile_temp_path))
                    self._last_reconcile_temp = reconcile_temp_path
                else:
                    self._last_reconcile_temp = None

            if generated_outputs:
                primary_out = generated_outputs[0]
                for private in getattr(self, "_review_private", {}).values():
                    if private.get("source_path") == self._active_source_path:
                        private["output_path"] = str(primary_out)
                self._persist_review_state()

        except PermissionError:
            _private_status(f"Ошибка прав доступа при сохранении файла: {file_p.name}")
            if current_target_file is not None:
                try:
                    current_target_file.unlink(missing_ok=True)
                except OSError:
                    pass
            for partial_output in generated_outputs:
                try:
                    partial_output.unlink(missing_ok=True)
                except OSError:
                    pass
            if temp_source_for_pdf and temp_source_for_pdf.exists():
                try:
                    temp_source_for_pdf.unlink(missing_ok=True)
                except OSError:
                    pass
            if hasattr(self, "_last_reconcile_temp") and self._last_reconcile_temp and self._last_reconcile_temp.exists():
                try:
                    self._last_reconcile_temp.unlink(missing_ok=True)
                except OSError:
                    pass
                self._last_reconcile_temp = None
            raise PermissionError(f"Нет прав на запись файла: {file_p.name}. Проверьте, не открыт ли файл.")
        except Exception as e:
            _private_status(f"Не удалось сохранить файл {file_p.name}: {e}")
            import traceback
            _private_status(f"DEBUG: Полная ошибка: {traceback.format_exc()}")
            if current_target_file is not None:
                try:
                    current_target_file.unlink(missing_ok=True)
                except OSError:
                    pass
            for partial_output in generated_outputs:
                try:
                    partial_output.unlink(missing_ok=True)
                except OSError:
                    pass
            if temp_source_for_pdf and temp_source_for_pdf.exists():
                try:
                    temp_source_for_pdf.unlink(missing_ok=True)
                except OSError:
                    pass
            if hasattr(self, "_last_reconcile_temp") and self._last_reconcile_temp and self._last_reconcile_temp.exists():
                try:
                    self._last_reconcile_temp.unlink(missing_ok=True)
                except OSError:
                    pass
                self._last_reconcile_temp = None
            raise IOError(f"Не удалось сохранить файл {file_p.name}: {e}")

        # Удаляем скрытые части и безопасно очищаемые метаданные, затем создаём
        # проверяемый сертификат. Строгая очистка не выдаёт сомнительный файл:
        # при ошибке созданный результат удаляется, исходник остаётся цел.
        # сертификат для каждого фактического результата. Исходный документ
        # не изменяется, исходные сущности и дешифратор в сертификат не входят.
        for generated_path in dict.fromkeys(generated_outputs):
            try:
                from hidden_data import apply_hidden_data_policy as apply_hidden_policy, flatten_pdf_to_images
                from privacy_audit import (
                    build_audit_certificate,
                    clean_file_metadata,
                    write_audit_certificate,
                )
                hidden_temp = generated_path.with_name(
                    f".{generated_path.stem}.hidden{generated_path.suffix}"
                )
                configured_policy = dict(getattr(self, "hidden_data_policy", self._default_hidden_data_policy()))
                if generated_path.suffix.casefold() in {".docm", ".xlsm"}:
                    custom_policy = getattr(self, "hidden_data_policy", None)
                    if custom_policy is None or "macros" not in custom_policy:
                        configured_policy["macros"] = "keep"
                hidden_transform = None
                if any(value == "anonymize" for value in configured_policy.values()):
                    def hidden_transform(text, _kind):
                        transformed, _count, _logs = self.anonymize_text_pullenti(
                            text, exclusions_list, custom_replacements_list,
                            prev_paragraphs=None, mapping_dict=mapping_dict, entity_seen=entity_seen,
                        )
                        return transformed

                hidden_report = None
                if generated_path.suffix.casefold() in {".docx", ".docm", ".xlsx", ".xlsm", ".pdf"}:
                    hidden_temp = generated_path.with_name(
                        f".{generated_path.stem}.hidden{generated_path.suffix}"
                    )
                    hidden_report = apply_hidden_policy(
                        generated_path, hidden_temp, configured_policy,
                        text_transform=hidden_transform, strict=True,
                    )
                    os.replace(hidden_temp, generated_path)

                flatten_report = None
                if generated_path.suffix.casefold() == ".pdf" and getattr(self, "irreversible_pdf", False):
                    flat_temp = generated_path.with_name(f".{generated_path.stem}.flat.pdf")
                    flatten_report = flatten_pdf_to_images(generated_path, flat_temp)
                    os.replace(flat_temp, generated_path)

                metadata_temp = generated_path.with_name(
                    f".{generated_path.stem}.metadata{generated_path.suffix}"
                )
                cleanup_report = clean_file_metadata(generated_path, metadata_temp)
                if cleanup_report.error:
                    raise RuntimeError(f"Очистка метаданных не выполнена: {cleanup_report.error}")
                os.replace(metadata_temp, generated_path)
                review_queue = getattr(self, "review_queue", None)
                certificate = build_audit_certificate(
                    file_p,
                    generated_path,
                    counts={
                        "files": 1,
                        "replacements": total_replacements_in_file,
                        "entities_by_type": (
                            review_queue.by_entity_type() if review_queue is not None else {}
                        ),
                    },
                    review_queue=review_queue,
                    cleanup_report=cleanup_report,
                )
                if hidden_report is not None:
                    certificate["hidden_data"] = {
                        "batch_id": str(getattr(self, "_active_batch_id", "")),
                        "detected": dict(hidden_report.detected),
                        "removed": list(hidden_report.removed),
                        "skipped": list(hidden_report.skipped),
                        "safe_to_release": not bool(hidden_report.errors),
                        "irreversible_pdf": bool(flatten_report),
                        "text_layer_removed": bool(
                            flatten_report and flatten_report.text_layer_removed
                        ),
                    }
                if getattr(self, "save_audit_files", False):
                    certificate_path = generated_path.with_name(
                        generated_path.name + ".audit.json"
                    )
                    write_audit_certificate(certificate, certificate_path)
                    log_data.append(
                        f"Создан сертификат обработки: {certificate_path.name}"
                    )
            except Exception as audit_error:
                logger.warning(
                    "Не удалось очистить метаданные/создать сертификат для %s: %s",
                    generated_path, audit_error,
                )
                try:
                    generated_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise IOError(
                    "Безопасная очистка результата остановлена; выходной файл не выдан"
                ) from audit_error

        if getattr(self, "save_log_files", False):
            try:
                with open(log_path, 'w', encoding='utf-8') as log_f:
                    json.dump({"schema": "docxdodyr.processing-log/v1", "status": "completed", "replacements": total_replacements_in_file}, log_f, ensure_ascii=False, indent=2)
                _private_status(f"Лог сохранен: {log_path}")
            except Exception as e:
                _private_status(f"Ошибка сохранения лог-файла {log_path}: {e}")
                # Не прерываем выполнение из-за ошибки лога, но сообщаем
                if self._window:
                    ui_bridge.ui_alert(self._window, f"Не удалось сохранить лог-файл для '{file_p.name}'")

        self._last_generated_outputs = list(generated_outputs)
        actual_create_decoder = bool(create_decoder if create_decoder is not None else getattr(self, "save_decoder", False))
        if actual_create_decoder and not defer_decoder and mapping_dict and len(mapping_dict) > 0 and total_replacements_in_file > 0:
            target_dec_path = Path(decoder_path) if decoder_path else output_root / f"{file_p.stem}_Дешифратор.json"
            if target_dec_path.exists():
                target_dec_path = target_dec_path.with_name(f"{target_dec_path.stem}_{uuid.uuid4()}.json")
            try:
                self._save_decoder_atomic(target_dec_path, mapping_dict)
                emit_sidecars = bool(getattr(self, "emit_audit_sidecars", False))
                from decoder_binding import publish_binding
                publish_binding(target_dec_path, generated_outputs, str(uuid.uuid4()), emit_sidecars=emit_sidecars)
                _private_status(f"Создан файл-дешифратор: {target_dec_path}")
                log_data.append(f"Создан файл-дешифратор: {target_dec_path}")
            except Exception as e:
                logger.error("Ошибка сохранения дешифратора %s: %s", target_dec_path, e)
                raise IOError(f"Не удалось сохранить файл-дешифратор: {e}") from e
        _private_status(f"Обработка документа завершена. Замен: {total_replacements_in_file}")
        return total_replacements_in_file

    def anonymize_text_pullenti(self, text, current_exclusions_original=None, current_replacements=None, prev_paragraphs=None, mapping_dict=None, entity_seen=None, semantic_context=None, protected_ranges=None, captured_replacements=None, apply_qwen=True):
        """Обезличивает сущности Pullenti с учётом активных исключений и пользовательских замен."""
        log_entries = ["\n--- Обработка фрагмента: чистый Pullenti ---"]
        if text is None:
            return "", 0, log_entries
        if not isinstance(text, str) or not text.strip():
            return text, 0, log_entries
        if self._pullenti_processor is None:
            # This public method must never turn an unavailable analyser into
            # an apparently anonymised result.  The regular file pipeline
            # initializes Pullenti before reaching this branch; direct
            # callers fail closed instead of publishing source text.
            raise RuntimeError("Pullenti не инициализирован; обезличивание остановлено")

        # Парсим пользовательские замены
        raw_replacements = current_replacements if current_replacements is not None else getattr(self, "custom_replacements", set())
        parsed_custom: list[tuple[str, str]] = []
        if isinstance(raw_replacements, dict):
            for k, v in raw_replacements.items():
                parsed = parse_custom_replacement_rule({k: v})
                if parsed:
                    parsed_custom.append(parsed)
        elif isinstance(raw_replacements, (list, set, tuple)):
            for item in raw_replacements:
                parsed = parse_custom_replacement_rule(item)
                if parsed:
                    parsed_custom.append(parsed)

        # Сортируем пользовательские замены: сначала более длинные источники
        parsed_custom.sort(key=lambda item: len(item[0]), reverse=True)

        # Парсим список исключений с кэшированием нормализованных форм
        if (current_exclusions_original is None or current_exclusions_original is getattr(self, "user_exclusions", None)) and getattr(self, "_normalized_exclusions_cache", None) is not None:
            normalized_exclusions = self._normalized_exclusions_cache
        elif current_exclusions_original is None or current_exclusions_original is getattr(self, "user_exclusions", None):
            raw_exclusions = getattr(self, "user_exclusions", set())
            self._normalized_exclusions_cache = frozenset(
                str(s).strip().casefold() for s in raw_exclusions
                if str(s).strip() and not str(s).strip().startswith("#")
            ) if isinstance(raw_exclusions, (list, set, tuple)) else frozenset()
            normalized_exclusions = self._normalized_exclusions_cache
        else:
            raw_exclusions = current_exclusions_original
            normalized_exclusions = set()
            if isinstance(raw_exclusions, (list, set, tuple)):
                for ex in raw_exclusions:
                    s = str(ex).strip().casefold()
                    if s and not s.startswith("#"):
                        normalized_exclusions.add(s)

        custom_replacements_list = []
        custom_ranges = []
        if parsed_custom:
            for src, dst in parsed_custom:
                pattern = re.escape(src)
                for match in re.finditer(pattern, text):
                    m_start, m_end = match.start(), match.end()
                    if not any(s < m_end and m_start < e for s, e in custom_ranges):
                        custom_ranges.append((m_start, m_end))
                        custom_replacements_list.append((m_start, m_end, dst, src))

        # A table label is analysis-only context.  It is never part of the
        # source text passed to ``apply_replacements``.  Spans touching the
        # synthetic prefix are discarded by ``project_span_to_value``.
        context = semantic_context if isinstance(semantic_context, TableCellContext) else None
        analysis_text = context.analysis_text if context is not None else text

        try:
            analysis = self._pullenti_processor.process(
                SourceOfAnalysis(analysis_text), None, None
            )
        except Exception as exc:
            # Fail closed.  Returning the original text here used to let a
            # source document be saved as if it had been anonymised.
            raise RuntimeError("Pullenti не смог безопасно обработать фрагмент") from exc

        pullenti_spans = list(iter_pullenti_spans(analysis, analysis_text))
        if context is not None:
            # Resolve composite boundaries while the synthetic semantic
            # prefix is still present.  This lets a multiline table value
            # such as «ТРОМЕДИА» inherit the preceding OPF, while projection
            # still guarantees that only characters from the value paragraph
            # can be replaced.
            pullenti_spans = list(refine_composite_spans(analysis_text, pullenti_spans))
            projected = []
            for span in pullenti_spans:
                local = project_span_to_value(span.start, span.end, context)
                if (
                    local is None
                    and context.label == "Адрес"
                    and span.label == "ADDRESS"
                    and span.start <= context.value_offset
                    and span.end == context.value_offset + len(context.value_text)
                ):
                    # Pullenti can join the analysis-only field marker and
                    # the complete postal value into one composite address.
                    # Keep the proven value part instead of discarding the
                    # entity merely because it overlaps the synthetic label.
                    local = (0, len(context.value_text))
                if local is None:
                    continue
                local_start, local_end = local
                projected.append(type(span)(
                    text=text[local_start:local_end],
                    start=local_start,
                    end=local_end,
                    label=span.label,
                ))
            pullenti_spans = projected
            log_entries.append(
                f"  Контекст таблицы: {context.label or 'нет метки'} "
                f"(строка {context.row + 1}, ячейка {context.column + 1})"
            )
        # Интеграция детерминированных правил (ИНН, СНИЛС, БИК, паспорта, карты, IBAN, SWIFT)
        # через UnifiedDetectionPipeline с разрешением конфликтов с Pullenti-спанами.
        det_pipeline = getattr(self, '_detection_pipeline', None)
        if det_pipeline is None:
            det_pipeline = UnifiedDetectionPipeline(enable_pullenti=False, enable_rules=True)
            self._detection_pipeline = det_pipeline
        rule_candidates = det_pipeline.detect_candidates(text, pullenti_spans=pullenti_spans)
        # Конвертируем кандидатов правил (не-pullenti) в EntitySpan для объединения
        rule_only_spans = [
            EntitySpan(text=c.text, start=c.start, end=c.end, label=c.entity_type)
            for c in rule_candidates
            if c.source != "pullenti"
        ]
        merged_spans = list(pullenti_spans) + rule_only_spans
        spans = select_non_overlapping(refine_composite_spans(text, merged_spans))
        all_protected_ranges = tuple(protected_ranges or ()) + tuple(custom_ranges)
        if all_protected_ranges:
            spans = [
                span for span in spans
                if not any(start < span.end and span.start < end for start, end in all_protected_ranges)
            ]
        replacements = []
        review_spans = []
        review_replacements = []
        resolver = EntityResolver(entity_seen, mapping_dict) if mapping_dict is not None and entity_seen is not None else None
        for span in spans:
            # A redaction entity must contain a real lexical value. Pullenti
            # and context-only grammars can occasionally return punctuation
            # or the tail of an existing placeholder from template fields.
            # Such fragments must never enter the shared batch decoder.
            surface = span.text.strip()
            if surface.casefold() in normalized_exclusions or span.text.casefold() in normalized_exclusions:
                log_entries.append(
                    f"  - {span.label} [{span.start}:{span.end}] сохранён: "
                    f"совпадает со списком исключений"
                )
                continue
            if (
                len(surface) < 2
                or not re.search(r"[A-Za-zА-Яа-яЁё0-9]", surface)
                or re.fullmatch(r"_?\d*\]", surface)
                or re.fullmatch(r"\[[^\]]*", surface)
            ):
                log_entries.append(
                    f"  - {span.label} [{span.start}:{span.end}] пропущен: "
                    "нет полноценного лексического значения"
                )
                continue
            if span.label in {"JOB_TITLE", "EDUCATION"}:
                log_entries.append(
                    f"  - {span.label} [{span.start}:{span.end}] сохранён: "
                    "категориальное значение является описательным контекстом, а не идентификатором"
                )
                continue
            if span.label == "ADDRESS":
                prefix_window = text[max(0, span.start - 80):span.start]
                has_address_marker = bool(re.search(
                    r"(?i)(?:юридическ(?:ий|ого)\s+адрес|почтов(?:ый|ого)\s+адрес|фактическ(?:ий|ого)\s+адрес|"
                    r"адрес\s+(?:регистрации|места\s+(?:нахождения|жительства))|"
                    r"место\s+нахождения\s+и\s+адрес(?:\s+(?:общества|организации|юридического\s+лица|клиента|гражданина))?|"
                    r"место\s+(?:нахождения|жительства|регистрации)|по\s+адресу|адрес(?:у)?)\s*[:\-–—]?\s*$",
                    prefix_window
                ))
                if not has_address_marker and not is_sufficient_address(surface):
                    log_entries.append(
                        f"  - {span.label} [{span.start}:{span.end}] сохранён: "
                        "неполное географическое обозначение без улицы/района/дома"
                    )
                    continue
            placeholder = self.current_placeholders.get(span.label)
            if not placeholder:
                if span.label == "FOREIGN_PER":
                    placeholder = "[FIO]"
                elif span.label == "FOREIGN_ORG":
                    placeholder = "[Name]"
                elif span.label == "IBAN":
                    placeholder = "[IBAN]"
                elif span.label == "SWIFT":
                    placeholder = "[SWIFT]"
            if not placeholder:
                log_entries.append(
                    f"  - {span.label} [{span.start}:{span.end}] пропущен: "
                    "тип отключён или для него нет плейсхолдера"
                )
                continue

            existing_fio = None
            if span.label in ("PER", "FOREIGN_PER"):
                existing_fio = re.match(r"^\[(?:ФИО|FIO)(?:_\d+)?\]", span.text)
            if existing_fio is not None:
                # A surname left immediately after an already anonymised FIO
                # belongs to that placeholder. Replace the combined fragment
                # with the existing token instead of allocating a second ID.
                placeholder = existing_fio.group(0)
            elif resolver is not None:
                placeholder = resolver.resolve(span.text, span.label, placeholder)

            if span.label == "ORG":
                m_branch_bank = re.match(
                    r"^(\s*филиал\s+[«\"'\u201c\u201d\u201e].+?[»\"'\u201c\u201d\u201e]\s+банка?\s+)([«\"'\u201c\u201d\u201e]?)(.+?)([»\"'\u201c\u201d\u201e]?)(\s*\([А-ЯЁA-Z]+\))?$",
                    span.text,
                    re.I
                )
                m_bank = re.match(
                    r"^(\s*(?:банка|банк|акционерного\s+банка)\s+)([«\"'\u201c\u201d\u201e]?)(.+?)([»\"'\u201c\u201d\u201e]?)(\s*\([А-ЯЁA-Z]+\))?$",
                    span.text,
                    re.I
                )
                m_opf = re.match(
                    r"^(\s*(?:общество\s+с\s+ограниченной\s+ответственностью|публичное\s+акционерное\s+общество|непубличное\s+акционерное\s+общество|акционерное\s+общество|единоличное\s+акционерное\s+общество|закрытое\s+акционерное\s+общество|открытое\s+акционерное\s+общество|"
                    r"(?:краевое|государственное|федеральное|муниципальное)\s+(?:бюджетное|автономное|казенное|казённое)\s+учреждение(?:\s+(?:здравоохранения|культуры|образования|социального\s+обслуживания))?(?:\s+города\s+\w+)?|"
                    r"ооо|еооо|пао|ао|зао|оао|нао|еао|"
                    r"кгбуз|гбуз|фгбуз|мбуз|гауз|мауз|гкуз|кгкуз|буз|фбуз|"
                    r"кгбу|гбу|фгбу|мбу|гау|мау|гку|кгку|фгку|мку|гаук|маук|гбук|фгбук|"
                    r"фгбоу|фгаоу|гбоу|мбоу|нмиц|нии|нпц|муп|гуп|фгуп|фгбу|ано|нко|тсж|тсн|снт|спао|оп|филиал(?:\s+[а-яё\w]+)?)\s*)([«\"'\u201c\u201d\u201e].*[»\"'\u201c\u201d\u201e])",
                    span.text,
                    re.I
                )
                if m_branch_bank:
                    branch_masked = re.sub(r'[«\"\'\u201c\u201d\u201e].+?[»\"\'\u201c\u201d\u201e]', f'"{placeholder}"', m_branch_bank.group(1))
                    suffix = m_branch_bank.group(5) or ""
                    placeholder = f'{branch_masked.strip()} {placeholder}{suffix}'
                elif m_bank and not (
                    span.start > 0
                    and text[span.start - 1] in '«\"\'“„'
                ):
                    bank_prefix = m_bank.group(1).strip()
                    bank_suffix = m_bank.group(5) or ""
                    placeholder = f'{bank_prefix} {placeholder}{bank_suffix}'
                elif m_opf:
                    opf_prefix = m_opf.group(1).strip()
                    quoted_part = m_opf.group(2).strip()
                    q_open = quoted_part[0] if quoted_part else '"'
                    q_close = '»' if q_open == '«' else ('”' if q_open in '“"' else ('\'' if q_open in '‘\'' else '"'))
                    placeholder = f'{opf_prefix} {q_open}{placeholder}{q_close}'

            replacement = (span.start, span.end, placeholder)
            replacements.append(replacement)
            review_spans.append(span)
            review_replacements.append((span, replacement))
            log_entries.append(
                f"  + Pullenti: [{span.start}:{span.end}] "
                f"{span.label} -> {placeholder} (исходное значение не журналируется)"
            )

        for m_start, m_end, dst, src in custom_replacements_list:
            replacements.append((m_start, m_end, dst))
            log_entries.append(
                f"  + Пользовательская замена: [{m_start}:{m_end}] '{src}' -> '{dst}'"
            )
            if mapping_dict is not None and dst.startswith("[") and dst.endswith("]"):
                mapping_dict[dst] = src

        replacements.sort(key=lambda r: r[0])

        if captured_replacements is not None:
            captured_replacements.extend(replacements)
        result, count = apply_replacements(text, replacements)
        if count:
            try:
                from privacy_audit import ReviewFinding, ReviewQueue

                queue = getattr(self, "review_queue", None)
                if queue is None:
                    queue = self.review_queue = ReviewQueue()
                search_from = 0
                for span, replacement in review_replacements:
                    placeholder = replacement[2]
                    review_coordinate, ocr_confidence = ocr_position_for_span(
                        getattr(self, "_active_ocr_span_map", ()),
                        span.start, span.end,
                        getattr(
                            self, "_active_review_coordinate",
                            {"scope": "body", "kind": "paragraph", "index": 0},
                        ),
                    )
                    position = result.find(placeholder, search_from)
                    if position < 0:
                        position = result.find(placeholder)
                    if position >= 0:
                        search_from = position + len(placeholder)
                        context_start = max(0, position - 60)
                        context_end = min(len(result), position + len(placeholder) + 60)
                        redacted_context = result[context_start:context_end]
                    else:
                        redacted_context = placeholder
                    finding = queue.add(ReviewFinding(
                        document_ref=getattr(self, "_active_document_ref", "document"),
                        location=(
                            f"{getattr(self, '_active_review_location', 'text')}:"
                            f"text:{span.start}-{span.end}"
                        ),
                        entity_type=span.label,
                        placeholder=placeholder,
                        confidence=(ocr_confidence if ocr_confidence is not None else (0.9 if context is not None else 1.0)),
                        source=("pullenti-ocr" if ocr_confidence is not None else ("pullenti-table" if context is not None else "pullenti")),
                        redacted_context=redacted_context,
                        position=review_coordinate,
                    ))
                    # Keep the original only in the process-local backend.
                    # ``occurrence`` makes a decision affect one placeholder
                    # in one paragraph/cell even when the same ID is repeated.
                    private = getattr(self, "_review_private", None)
                    if private is None:
                        private = self._review_private = {}
                    private[finding.finding_id] = {
                        "source_path": getattr(self, "_active_source_path", ""),
                        "output_path": "",
                        "coordinate": review_coordinate,
                        "placeholder": placeholder,
                        "original": span.text,
                        "occurrence": result[:position].count(placeholder) if position >= 0 else 0,
                    }
            except Exception as review_error:
                logger.warning("Не удалось добавить находки в очередь проверки: %s", review_error)
        if apply_qwen and count > 0 and getattr(getattr(self, "qwen_settings", None), "enabled", False):
            polished = self.postprocess_anonymized_text(result)
            if polished != result:
                result = polished
                log_entries.append("  + Qwen 3.5: согласование вокруг плейсхолдеров применено")
            else:
                log_entries.append(
                    f"  = Qwen 3.5: текст сохранён ({self._qwen_postprocessor.last_status})"
                )
        log_entries.append(f"  Заменено сущностей Pullenti: {count}")
        return result, count, log_entries
    def clean_document(self, doc, current_exclusions, current_replacements, mapping_dict=None, entity_seen=None, progress_callback=None):
        """ Очищает docx-документ от персональных данных, сохраняя форматирование. """
        from docx.text.paragraph import Paragraph

        total_replacements_doc = 0
        full_log_data = []
        replacement_failures = []
        prev_texts = [None, None]
        tables_to_replace = []

        def process_paragraph_obj(para_obj: Paragraph, context_prefix="", coordinate=None):
            nonlocal total_replacements_doc, prev_texts
            # Извлекаем текст включая исправления (track changes)
            original_para_text = self.get_paragraph_text_with_revisions(para_obj)
            if original_para_text is None or not isinstance(original_para_text, str) or not original_para_text.strip():
                prev_texts = [prev_texts[-1], original_para_text]
                return
            self._active_review_location = context_prefix.strip(" []") or "docx"
            self._active_review_coordinate = coordinate or {"scope": "body", "kind": "paragraph", "index": 0}
            cleaned_text, replacements_in_para, para_fragment_logs = self.anonymize_text_pullenti(
                original_para_text, current_exclusions, current_replacements, prev_paragraphs=prev_texts,
                mapping_dict=mapping_dict, entity_seen=entity_seen, apply_qwen=False
            )
            for log_line in para_fragment_logs:
                full_log_data.append(f"{context_prefix}{log_line}")
            if replacements_in_para > 0:
                total_replacements_doc += replacements_in_para
                try:
                    # Постобработка: удаление хвостов (окончания падежей, висячие знаки)
                    cleaned_text = self.postprocess_placeholder_tails(cleaned_text)
                    full_log_data.append(
                        f"{context_prefix}DEBUG: Начинаем позиционную замену; "
                        f"длина до={len(original_para_text)}, после={len(cleaned_text)}."
                    )

                    self.replace_text_in_paragraph_xml(para_obj, original_para_text, cleaned_text, full_log_data, context_prefix)

                    # Проверяем, действительно ли текст изменился
                    actual_text_after = para_obj.text
                    full_log_data.append(f"{context_prefix}DEBUG: Текст после замены: '{actual_text_after[:50]}...'")

                    full_log_data.append(f"{context_prefix}Текст параграфа заменен. Замен: {replacements_in_para}.")
                except Exception as e:
                    replacement_failures.append(f"{context_prefix}{e}")
                    full_log_data.append(f"{context_prefix}ОШИБКА при замене текста в параграфе: {e}. Оригинальный текст мог остаться.")
                    import traceback
                    full_log_data.append(f"{context_prefix}TRACEBACK: {traceback.format_exc()}")
            elif cleaned_text != original_para_text:
                 cleaned_text = self.postprocess_placeholder_tails(cleaned_text)
                 para_obj.clear()
                 para_obj.add_run(cleaned_text)
                 full_log_data.append(f"{context_prefix}Текст параграфа обновлен (без формальных замен, но текст изменился).")
            prev_texts = [prev_texts[-1], original_para_text]

        def process_block(block, block_label, process_tables=True, section_index=0, block_type="footer"):
            """Универсальная обработка блока (body, header, footer): параграфы и таблицы."""
            for i, para in enumerate(getattr(block, 'paragraphs', [])):
                try:
                    process_paragraph_obj(
                        para, context_prefix=f"[{block_label} Пар.{i}] ",
                        coordinate={"scope": "header_footer", "kind": "paragraph", "index": i,
                                    "section": section_index, "block": block_type},
                    )
                except Exception as e:
                    replacement_failures.append(f"{block_label} Пар.{i}: {e}")
                    logger.warning("Ошибка параграфа %s Пар.%s: %s", block_label, i, e)
                    full_log_data.append(f"[{block_label} Пар.{i}] Пропущен (ошибка): {e}")
            if process_tables:
                for t_idx, tbl in enumerate(getattr(block, 'tables', [])):
                    _process_one_table(tbl, t_idx, block_label)

        def _process_one_table(table_obj, t_idx, block_label):
            nonlocal total_replacements_doc
            table_needs_replacement = False
            table_data_backup = []
            try:
                for r_idx, row in enumerate(table_obj.rows):
                    try:
                        row_data = []
                        for c_idx, cell in enumerate(row.cells):
                            try:
                                row_data.append(cell.text)
                            except Exception as e:
                                logger.warning("Ошибка чтения ячейки: %s", e)
                                row_data.append("")
                        table_data_backup.append(row_data)
                    except Exception as e:
                        logger.warning("Ошибка строки таблицы: %s", e)
                        table_data_backup.append([])
            except Exception as e:
                replacement_failures.append(f"{block_label} Таб.{t_idx}: {e}")
                logger.warning("Ошибка таблицы: %s", e)
                return
            table_contexts = {
                (item.row, item.column): item
                for item in build_table_contexts(table_data_backup)
            }
            processed_cells = set()
            for r_idx, row in enumerate(table_obj.rows):
                try:
                    for c_idx, cell in enumerate(row.cells):
                        try:
                            # Merged Word cells can appear more than once in
                            # ``row.cells``. Process their XML container once.
                            # Keep the XML element itself as the stable key.
                            # ``id(cell._tc)`` is unsafe here because python-docx
                            # may create short-lived proxy objects and CPython can
                            # reuse their integer ids while the table is scanned.
                            cell_identity = cell._tc
                            if cell_identity in processed_cells:
                                continue
                            processed_cells.add(cell_identity)
                            cell_context = table_contexts.get((r_idx, c_idx))
                            paragraph_texts = [
                                self.get_paragraph_text_with_revisions(para)
                                for para in cell.paragraphs
                            ]
                            cell_text = "\n".join(paragraph_texts)
                            if not cell_text.strip() or is_pure_field_label(cell_text):
                                continue
                            self._active_review_location = f"{block_label} Таб.{t_idx} Р.{r_idx} Я.{c_idx}"
                            self._active_review_coordinate = {
                                "scope": "body" if block_label == "Док" else "header_footer",
                                "kind": "table_cell", "table": t_idx,
                                "row": r_idx, "column": c_idx,
                                "section": getattr(self, "_active_section_index", 0),
                                "block": getattr(self, "_active_block_type", "footer"),
                            }
                            effective_context = None
                            if cell_context is not None and cell_context.label:
                                effective_context = TableCellContext(
                                    row=r_idx,
                                    column=c_idx,
                                    value_text=cell_text,
                                    label=cell_context.label,
                                    label_row=cell_context.label_row,
                                    label_column=cell_context.label_column,
                                )
                            captured = []
                            cleaned, repl, cell_logs = self.anonymize_text_pullenti(
                                cell_text, current_exclusions, current_replacements,
                                prev_paragraphs=None, mapping_dict=mapping_dict,
                                entity_seen=entity_seen,
                                semantic_context=effective_context,
                                captured_replacements=captured,
                                apply_qwen=False,
                            )
                            for log_line in cell_logs:
                                full_log_data.append(
                                    f"[{block_label} Таб.{t_idx} Р.{r_idx} Я.{c_idx}] {log_line}"
                                )
                            if repl > 0 and cleaned != cell_text:
                                # Project each cell-level replacement back to
                                # the original paragraphs. A span crossing a
                                # paragraph boundary inserts its placeholder
                                # once and removes only the covered tails from
                                # subsequent paragraphs, preserving Word runs.
                                bounds = []
                                cursor = 0
                                for para_text in paragraph_texts:
                                    bounds.append((cursor, cursor + len(para_text)))
                                    cursor += len(para_text) + 1
                                local_by_paragraph = {index: [] for index in range(len(paragraph_texts))}
                                for start, end, placeholder in captured:
                                    inserted = False
                                    for p_idx, (p_start, p_end) in enumerate(bounds):
                                        overlap_start = max(start, p_start)
                                        overlap_end = min(end, p_end)
                                        if overlap_start >= overlap_end:
                                            continue
                                        local_by_paragraph[p_idx].append((
                                            overlap_start - p_start,
                                            overlap_end - p_start,
                                            placeholder if not inserted else "",
                                        ))
                                        inserted = True
                                for p_idx, para in enumerate(cell.paragraphs):
                                    local_replacements = local_by_paragraph[p_idx]
                                    if not local_replacements:
                                        continue
                                    original_para_text = paragraph_texts[p_idx]
                                    cleaned_para, _ = apply_replacements(
                                        original_para_text, local_replacements
                                    )
                                    self.replace_text_in_paragraph_xml(
                                        para, original_para_text, cleaned_para,
                                        full_log_data,
                                        f"[{block_label} Таб.{t_idx} Р.{r_idx} Я.{c_idx} Пар.{p_idx}] ",
                                        local_replacements,
                                    )
                                total_replacements_doc += repl
                                full_log_data.append(
                                    f"[{block_label} Таб.{t_idx} Р.{r_idx} Я.{c_idx}] ✅ Изменена"
                                )
                        except Exception as e:
                            replacement_failures.append(
                                f"{block_label} Таб.{t_idx} Р.{r_idx} Я.{c_idx}: {e}"
                            )
                            logger.warning("Ошибка ячейки: %s", e)
                            full_log_data.append(f"[{block_label} Таб.{t_idx} Р.{r_idx} Я.{c_idx}] Пропущена: {e}")
                except Exception as e:
                    logger.warning("Ошибка строки таблицы: %s", e)
            if table_needs_replacement:
                tables_to_replace.append((t_idx, table_obj, table_data_backup))

        full_log_data.append("--- Начало очистки документа ---")
        full_log_data.append(f"Параграфов в документе: {len(doc.paragraphs)}, Таблиц: {len(doc.tables)}")
        full_log_data.append(f"Активные исключения (ориг. список): {len(current_exclusions)}")
        full_log_data.append(f"Активные пользовательские замены: {len(current_replacements)}")

        full_log_data.append("\n--- Обработка параграфов основного текста ---")
        body_paragraphs = list(doc.paragraphs)
        tables_list = list(doc.tables)
        sections_list = list(doc.sections)
        total_steps = len(body_paragraphs) + len(tables_list) * 4 + len(sections_list) * 2
        if progress_callback:
            try:
                progress_callback(0, max(total_steps, 1), "Подготовка...")
            except Exception:
                pass
        step_count = 0

        def _tick(detail=""):
            nonlocal step_count
            step_count += 1
            if progress_callback and (step_count % 15 == 0 or step_count >= total_steps or step_count == 1):
                try:
                    progress_callback(step_count, max(total_steps, 1), detail)
                except Exception:
                    pass

        i = 0
        while i < len(body_paragraphs):
            paragraph = body_paragraphs[i]
            _tick(f"Абзац {i + 1}/{len(body_paragraphs)}")
            try:
                # Legal names are frequently wrapped into several Word
                # paragraphs.  Join only a bounded, syntactically unfinished
                # OPF/quoted construction, analyse it once and project exact
                # replacement coordinates back to the original paragraphs.
                first_text = self.get_paragraph_text_with_revisions(paragraph)
                group = [paragraph]
                group_texts = [first_text]
                looks_like_opf_start = bool(re.search(
                    r"(?:общество\s+с\s+ограниченной|автономная\s+некоммерческая|"
                    r"государственн\w*\s+(?:автономн\w*|бюджетн\w*|казенн\w*|казённ\w*)|"
                    r"краев\w*\s+государственн\w*|"
                    r"ООО|АО|ПАО|ГАУК|АНО|КГБУЗ|ГБУЗ|ФГБУЗ|МБУЗ|ГАУЗ|МАУЗ|ГКУЗ|КГКУЗ|БУЗ|ФБУЗ|КГБУ|ГБУ|ФГБУ|МБУ|ГАУ|МАУ|ГКУ|КГКУ|ФГКУ|МКУ|ФГБОУ|ГБОУ|МБОУ|НМИЦ|НИИ)\b",
                    first_text, re.I,
                ))
                joined = "\n".join(group_texts)
                j = i + 1
                while looks_like_opf_start and j < len(body_paragraphs) and len(group) < 5:
                    quote_count = len(re.findall(r"[«»\"“”„]", joined))
                    if quote_count >= 2 and quote_count % 2 == 0:
                        break
                    next_text = self.get_paragraph_text_with_revisions(body_paragraphs[j])
                    if not next_text.strip() and '"' not in joined and '«' not in joined:
                        break
                    group.append(body_paragraphs[j])
                    group_texts.append(next_text)
                    joined = "\n".join(group_texts)
                    j += 1
                if len(group) > 1 and len(re.findall(r"[«»\"“”„]", joined)) >= 2:
                    captured = []
                    cleaned, repl, logs = self.anonymize_text_pullenti(
                        joined, current_exclusions, current_replacements,
                        prev_paragraphs=prev_texts,
                        mapping_dict=mapping_dict, entity_seen=entity_seen,
                        captured_replacements=captured,
                        apply_qwen=False,
                    )
                    full_log_data.extend(f"[Док.Пар.{i}-{j - 1}] {line}" for line in logs)
                    if repl and cleaned != joined:
                        bounds = []
                        cursor = 0
                        for value in group_texts:
                            bounds.append((cursor, cursor + len(value)))
                            cursor += len(value) + 1
                        local_by_paragraph = {index: [] for index in range(len(group))}
                        for start, end, placeholder in captured:
                            inserted = False
                            for p_idx, (p_start, p_end) in enumerate(bounds):
                                overlap_start = max(start, p_start)
                                overlap_end = min(end, p_end)
                                if overlap_start >= overlap_end:
                                    continue
                                local_by_paragraph[p_idx].append((
                                    overlap_start - p_start, overlap_end - p_start,
                                    placeholder if not inserted else "",
                                ))
                                inserted = True
                        for p_idx, para in enumerate(group):
                            replacements = local_by_paragraph[p_idx]
                            if not replacements:
                                continue
                            cleaned_para, _ = apply_replacements(group_texts[p_idx], replacements)
                            self.replace_text_in_paragraph_xml(
                                para, group_texts[p_idx], cleaned_para, full_log_data,
                                f"[Док.Пар.{i + p_idx}] ", replacements,
                            )
                        total_replacements_doc += repl
                        for value in group_texts:
                            prev_texts = [prev_texts[-1], value]
                        i = j
                        continue
                process_paragraph_obj(
                    paragraph, context_prefix=f"[Док.Пар.{i}] ",
                    coordinate={"scope": "body", "kind": "paragraph", "index": i},
                )
            except Exception as e:
                replacement_failures.append(f"Док.Пар.{i}: {e}")
                logger.warning("Ошибка параграфа Док.Пар.%s: %s", i, e)
                full_log_data.append(f"[Док.Пар.{i}] Пропущен (ошибка): {e}")
            i += 1

        full_log_data.append("\n--- Обработка таблиц ---")
        for t_idx, table_obj in enumerate(doc.tables):
            _tick(f"Таблица {t_idx + 1}/{len(tables_list)}")
            full_log_data.append(f"\n[Таблица {t_idx}]")
            _process_one_table(table_obj, t_idx, "Док")

        # Обработка колонтитулов (headers & footers)
        full_log_data.append("\n--- Обработка колонтитулов ---")
        processed_header_footer_parts = set()
        for s_idx, section in enumerate(doc.sections):
            _tick(f"Колонтитулы {s_idx + 1}/{len(sections_list)}")
            try:
                self._active_section_index = s_idx
                blocks = (
                    ("header", "Header", section.header),
                    ("first_page_header", "FirstPageHeader", section.first_page_header),
                    ("even_page_header", "EvenPageHeader", section.even_page_header),
                    ("footer", "Footer", section.footer),
                    ("first_page_footer", "FirstPageFooter", section.first_page_footer),
                    ("even_page_footer", "EvenPageFooter", section.even_page_footer),
                )
                for block_type, label, block in blocks:
                    # Linked sections and disabled first/even variants can
                    # expose the same underlying XML part more than once.
                    # Process every distinct header/footer part exactly once.
                    identity = block._element
                    if identity in processed_header_footer_parts:
                        continue
                    processed_header_footer_parts.add(identity)
                    self._active_block_type = block_type
                    process_block(
                        block, f"Секция{s_idx}.{label}", process_tables=True,
                        section_index=s_idx, block_type=block_type,
                    )
            except Exception as e:
                replacement_failures.append(f"Секция{s_idx}: {e}")
                logger.warning("Ошибка секции %s: %s", s_idx, e)
                full_log_data.append(f"[Секция{s_idx}] Пропущена (ошибка): {e}")

        # Обработка сносок и концевых сносок (footnotes / endnotes) — BUG-02
        # python-docx не предоставляет высокоуровневый API для сносок, поэтому
        # работаем напрямую через XML-часть документа.
        for note_kind, note_label in (
            ("footnotes", "Сноска"),
            ("endnotes", "КонцеваяСноска"),
        ):
            try:
                part = getattr(doc.part, note_kind, None)
                if part is None:
                    continue
                notes_dict = getattr(part, note_kind, None) or {}
                if not notes_dict:
                    continue
                full_log_data.append(f"\n--- Обработка {note_label}ок ({len(notes_dict)}) ---")
                for fn_id, fn in notes_dict.items():
                    # Сноски с id=0 и id=-1 — системные (разделитель), пропускаем
                    try:
                        fn_id_int = int(fn_id)
                        if fn_id_int <= 0:
                            continue
                    except (TypeError, ValueError):
                        pass
                    try:
                        fn_paragraphs = list(fn.paragraphs)
                    except AttributeError:
                        continue
                    for fn_para_idx, fn_para in enumerate(fn_paragraphs):
                        try:
                            process_paragraph_obj(
                                fn_para,
                                context_prefix=f"[{note_label}.{fn_id}.{fn_para_idx}] ",
                                coordinate={
                                    "scope": note_kind,
                                    "kind": "paragraph",
                                    "note_id": fn_id,
                                    "index": fn_para_idx,
                                },
                            )
                        except Exception as e:
                            replacement_failures.append(
                                f"{note_label}.{fn_id}.{fn_para_idx}: {e}"
                            )
                            full_log_data.append(
                                f"[{note_label}.{fn_id}.{fn_para_idx}] Пропущен (ошибка): {e}"
                            )
            except Exception as e:
                logger.warning("Ошибка обработки %s: %s", note_label, e)
                full_log_data.append(f"[{note_label}] Пропущена обработка (ошибка): {e}")

        # Обработка вложенных текстовых врезок (Text Boxes) и элементов управления (SDT)
        try:
            processed_p_elements = {p._p for p in body_paragraphs}

            txbx_paragraphs = []
            for p_elm in doc.element.xpath(".//w:txbxContent//w:p"):
                if p_elm not in processed_p_elements:
                    processed_p_elements.add(p_elm)
                    txbx_paragraphs.append(Paragraph(p_elm, doc))

            if txbx_paragraphs:
                full_log_data.append(f"\n--- Обработка текстовых врезок (Text Boxes: {len(txbx_paragraphs)}) ---")
                for tb_idx, tb_para in enumerate(txbx_paragraphs):
                    try:
                        process_paragraph_obj(
                            tb_para, context_prefix=f"[ТекстоваяВрезка.{tb_idx}] ",
                            coordinate={"scope": "textbox", "kind": "paragraph", "index": tb_idx}
                        )
                    except Exception as e:
                        replacement_failures.append(f"ТекстоваяВрезка.{tb_idx}: {e}")
                        full_log_data.append(f"[ТекстоваяВрезка.{tb_idx}] Пропущен (ошибка): {e}")

            sdt_paragraphs = []
            for p_elm in doc.element.xpath(".//w:sdt//w:sdtContent//w:p"):
                if p_elm not in processed_p_elements:
                    processed_p_elements.add(p_elm)
                    sdt_paragraphs.append(Paragraph(p_elm, doc))

            if sdt_paragraphs:
                full_log_data.append(f"\n--- Обработка элементов управления (SDT: {len(sdt_paragraphs)}) ---")
                for sdt_idx, sdt_para in enumerate(sdt_paragraphs):
                    try:
                        process_paragraph_obj(
                            sdt_para, context_prefix=f"[SDT.{sdt_idx}] ",
                            coordinate={"scope": "sdt", "kind": "paragraph", "index": sdt_idx}
                        )
                    except Exception as e:
                        replacement_failures.append(f"SDT.{sdt_idx}: {e}")
                        full_log_data.append(f"[SDT.{sdt_idx}] Пропущен (ошибка): {e}")
        except Exception as e:
            logger.warning("Ошибка обработки TextBoxes / SDT: %s", e)

        # Заменяем проблемные таблицы целиком
        for t_idx, old_table, original_data in reversed(tables_to_replace):
            full_log_data.append(f"\n[Таблица {t_idx}] 🔄 СОЗДАНИЕ НОВОЙ ТАБЛИЦЫ...")
            try:
                # Получаем родительский элемент (обычно body документа)
                parent = old_table._element.getparent()
                table_position = list(parent).index(old_table._element)

        # Создаем обезличенные данные для новой таблицы
                cleaned_data = []
                for r_idx, row_data in enumerate(original_data):
                    cleaned_row = []
                    for c_idx, cell_text in enumerate(row_data):
                        if cell_text.strip():
                            cleaned_text, replacements, logs = self.anonymize_text_pullenti(
                                cell_text, current_exclusions, current_replacements, prev_paragraphs=None,
                                mapping_dict=mapping_dict, entity_seen=entity_seen
                            )
                            cleaned_text = self.postprocess_placeholder_tails(cleaned_text)
                            cleaned_row.append(cleaned_text)
                            if replacements > 0:
                                total_replacements_doc += replacements
                                full_log_data.append(
                                    f"[НоваяТаб.{t_idx} Р.{r_idx} Я.{c_idx}] "
                                    f"Заменено сущностей: {replacements}; исходное значение не журналируется"
                                )
                        else:
                            cleaned_row.append(cell_text)
                    cleaned_data.append(cleaned_row)

                # Создаем новую таблицу с теми же размерами
                new_table = doc.add_table(rows=len(cleaned_data), cols=len(cleaned_data[0]) if cleaned_data else 1)

                # Заполняем новую таблицу обезличенными данными
                for r_idx, row_data in enumerate(cleaned_data):
                    for c_idx, cell_text in enumerate(row_data):
                        if r_idx < len(new_table.rows) and c_idx < len(new_table.rows[r_idx].cells):
                            new_table.rows[r_idx].cells[c_idx].text = cell_text

                # Пытаемся скопировать стиль из старой таблицы
                try:
                    if old_table.style:
                        new_table.style = old_table.style
                except:
                    pass

                # Удаляем старую таблицу и вставляем новую на то же место
                parent.remove(old_table._element)
                parent.insert(table_position, new_table._element)

                full_log_data.append(f"[Таблица {t_idx}] ✅ ТАБЛИЦА УСПЕШНО ЗАМЕНЕНА")

            except Exception as e:
                full_log_data.append(f"[Таблица {t_idx}] ❌ ОШИБКА ПРИ ЗАМЕНЕ ТАБЛИЦЫ: {e}")
                import traceback
                full_log_data.append(f"[Таблица {t_idx}] TRACEBACK: {traceback.format_exc()}")

        full_log_data.append("\n--- Очистка документа завершена ---")
        full_log_data.append(f"Общее количество сделанных замен в документе: {total_replacements_doc}")
        _private_status(f"Очистка документа завершена. Общее количество сделанных замен: {total_replacements_doc}")

        if replacement_failures:
            raise RuntimeError(
                "DOCX не выдан: не удалось гарантированно записать обезличенный текст; "
                + "; ".join(replacement_failures[:5])
            )

        if progress_callback:
            try:
                progress_callback(total_steps, total_steps, "Анализ завершен")
            except Exception:
                pass

        self._write_back_qwen_document(doc, full_log_data)
        return total_replacements_doc, full_log_data

    def _write_back_qwen_document(self, doc, log_data):
        """One post-anonymization write-back for every DOCX paragraph structure."""
        if not getattr(getattr(self, "qwen_settings", None), "enabled", False):
            return
        from docx.text.paragraph import Paragraph
        from qwen_postprocessor import extract_placeholders
        roots = [doc.element]
        for section in doc.sections:
            for name in ("header", "footer", "first_page_header", "first_page_footer",
                         "even_page_header", "even_page_footer"):
                roots.append(getattr(section, name)._element)
        visited = set()
        for root in roots:
            for element in root.xpath(".//w:p"):
                if element in visited or element.xpath(".//w:p"):
                    continue
                visited.add(element)
                paragraph = Paragraph(element, doc)
                before = self.get_paragraph_text_with_revisions(paragraph)
                if not extract_placeholders(before):
                    continue
                after = self.postprocess_anonymized_text(before)
                if after != before:
                    self.replace_text_in_paragraph_xml(paragraph, before, after, log_data, "[Qwen] ")

    def run_pdf_ocr_only(self):
        """PDF → OCR (TSV временно) → DOCX (без обезличивания)."""
        if not getattr(self, '_window', None):
            return

        if not self.start_operation("ocr_pdf", description="Распознавание PDF"):
            return

        # Запрашиваем у пользователя PDF файлы через pywebview
        selected_files = self._window.create_file_dialog(1, allow_multiple=True, file_types=('PDF files (*.pdf)',))

        if not selected_files:
            self.finish_operation("idle")
            return

        # Показываем модальное окно прогресса OCR
        ui_bridge.ui_show_ocr_progress_modal(self._window)

        # Запуск OCR в фоновом потоке
        self.reset_cancellation()
        t = threading.Thread(target=self._run_pdf_ocr_worker, args=(selected_files,), daemon=True)
        self._active_threads.append(t)
        self._op_manager.register_thread(t)
        t.start()

    def _ensure_ocr_backend(self):
        """Проверяет наличие OCR адаптера текущей ОС без загрузки моделей."""
        try:
            from ocr_backend import available_backend_names, pdf_ocr_dependencies_available
            if not pdf_ocr_dependencies_available():
                names = ", ".join(available_backend_names()) or "нет"
                raise RuntimeError(
                    "Для OCR нужен PyMuPDF и OCR backend (Apple Vision на macOS "
                    f"или Tesseract/PaddleOCR). Найдено: {names}."
                )
            return True
        except Exception as exc:
            self.show_error("OCR недоступен", str(exc))
            return False

    def _run_pdf_ocr_worker(self, files):
        """Фоновый поток для распознавания PDF с защитой от коллизий и атомарным сохранением."""
        tmp_files_to_cleanup = []
        try:
            if not self._ensure_ocr_backend():
                self.finish_operation("failed")
                return
                
            from pdf_convert import ocr_pdf_to_text
            from pdf_convert import PDFOCRCancelled
            processed = 0
            ocr_lang = getattr(self, "ocr_lang", self.settings.get("ocr_lang", "rus+eng"))
            preferred_backend = getattr(self, "ocr_backend", None)

            for i, file_path in enumerate(files):
                if self.is_cancelled():
                    logger.info("Распознавание PDF отменено пользователем.")
                    break
                filename = os.path.basename(file_path)
                if self._window:
                    percent = int((i / len(files)) * 100)
                    ui_bridge.ui_set_global_progress(self._window, True, 'Распознавание PDF', f'Распознавание {i+1}/{len(files)}: {filename}', percent)
                
                target_output_path = self._get_unique_ocr_output_path(file_path)
                tmp_output_path = target_output_path.with_name(f".tmp_{target_output_path.stem}_{uuid.uuid4().hex[:8]}.txt")
                tmp_files_to_cleanup.append(tmp_output_path)
                try:
                    text = ocr_pdf_to_text(
                        file_path,
                        ocr_lang=ocr_lang,
                        preferred_backend=preferred_backend,
                        cancel_check=self.is_cancelled,
                    )
                    if self.is_cancelled():
                        logger.info("Распознавание PDF отменено перед сохранением %s", filename)
                        break

                    with open(tmp_output_path, 'w', encoding='utf-8') as f:
                        f.write(text)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_output_path, target_output_path)
                    if tmp_output_path in tmp_files_to_cleanup:
                        tmp_files_to_cleanup.remove(tmp_output_path)
                    processed += 1
                except PDFOCRCancelled:
                    logger.info("Распознавание PDF отменено пользователем во время обработки %s", filename)
                    break
                except Exception as e:
                    self.show_error("Ошибка OCR", f"Не удалось распознать {filename}: {e}")
                    if tmp_output_path.exists():
                        try:
                            tmp_output_path.unlink(missing_ok=True)
                        except OSError:
                            pass
            
            if self._window:
                ui_bridge.ui_set_global_progress(self._window, False)
                ui_bridge.ui_alert(self._window, f"Распознавание завершено!\nУспешно обработано: {processed}/{len(files)}")
            self.finish_operation("succeeded" if processed == len(files) else "partial")

        except Exception as e:
            if self._window: ui_bridge.ui_set_global_progress(self._window, False)
            self.show_error("Ошибка OCR", f"Критическая ошибка: {e}")
            self.finish_operation("failed")
        finally:
            for tmp_f in tmp_files_to_cleanup:
                try:
                    if tmp_f.exists():
                        tmp_f.unlink(missing_ok=True)
                except OSError:
                    pass


    def closeEvent(self, event):
        try:
            # Существующий код закрытия
            _private_status("Закрытие приложения...")

            # Если есть сохранение настроек, добавьте таймаут
            if hasattr(self, 'user_exclusions') and hasattr(self, 'custom_replacements'):
                _private_status("Сохранение настроек перед закрытием...")
                # Ваш код сохранения

            event.accept()  # Разрешить закрытие

        except KeyboardInterrupt:
            _private_status("Принудительное закрытие приложения (Ctrl+C)")
            event.accept()  # Принудительно закрыть

        except Exception as e:
            _private_status(f"Ошибка при закрытии приложения: {e}")
            event.accept()  # Закрыть несмотря на ошибку

class Worker:
    def __init__(self, files, cleaner_instance, batch_id=None, initial_mapping=None):
        self.files = files
        self.cleaner = cleaner_instance # Передаем экземпляр главного окна
        self.batch_id = batch_id
        self.initial_mapping = initial_mapping or {}
        self.last_result = None

    def run(self):
        """Запускает тяжелую обработку файлов."""
        num_files = len(self.files)
        if num_files == 0:
            if getattr(self.cleaner, '_window', None): self.cleaner.on_worker_finished(0, 0, 0)
            self.last_result = {
                "status": "failed",
                "processed_count": 0,
                "error_count": 1,
                "total_changes": 0,
                "message": "no_files",
            }
            return self.last_result

        processed_count, error_count, total_changes_all_files = 0, 0, 0
        try:
            processed_files = []
            completed_outputs = []
            batch_mapping = dict(self.initial_mapping)
            from entity_registry import entity_seen_from_mapping
            batch_entity_seen = entity_seen_from_mapping(batch_mapping) if batch_mapping else {}

            enable_crash_recovery = bool(
                getattr(
                    self.cleaner,
                    "enable_crash_recovery",
                    not bool(getattr(self.cleaner, "background_mode", False)),
                )
            )
            crash_recovery = None
            if enable_crash_recovery:
                import crash_recovery
            if self.batch_id:
                self.cleaner._active_batch_id = str(self.batch_id)
            else:
                self.cleaner._active_batch_id = str(uuid.uuid4())
                if enable_crash_recovery:
                    crash_recovery.start_batch_checkpoint(
                        self.cleaner._active_batch_id,
                        self.files,
                        mode="files"
                    )

            # Получаем актуальные списки из главного окна
            exclusions = self.cleaner.user_exclusions
            replacements = self.cleaner.custom_replacements

            for i, file_path_str in enumerate(self.files):
                if hasattr(self.cleaner, 'is_cancelled') and self.cleaner.is_cancelled():
                    logger.info("Пакетная обработка файлов отменена пользователем.")
                    break

                file_path = Path(file_path_str)

                if file_path.suffix.lower() == '.pdf' and getattr(self.cleaner, 'ocr_pdf', False):
                    if not self.cleaner._ensure_ocr_backend():
                        error_count += 1
                        continue

                self.cleaner.update_progress(i, num_files, f"Обработка: {file_path.name}")

                def _file_progress(step, total, detail=""):
                    if num_files <= 1:
                        p = int((step / max(total, 1)) * 100)
                    else:
                        base = (i / num_files) * 100
                        span = 100.0 / num_files
                        p = int(base + (step / max(total, 1)) * span)
                    msg = f"Обработка: {file_path.name}"
                    if detail:
                        msg += f" ({detail})"
                    self.cleaner.update_progress(i, num_files, msg, percent=p)

                kwargs = {
                    "batch_mapping": batch_mapping,
                    "batch_entity_seen": batch_entity_seen,
                    "defer_decoder": True,
                }
                import inspect
                try:
                    sig = inspect.signature(self.cleaner.process_single_file)
                    if "progress_callback" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    ):
                        kwargs["progress_callback"] = _file_progress
                except (ValueError, TypeError):
                    pass

                try:
                    changes_in_file = self.cleaner.process_single_file(
                        file_path_str, exclusions, replacements,
                        **kwargs,
                    )
                    processed_count += 1
                    total_changes_all_files += changes_in_file
                    processed_files.append(file_path_str)
                    completed_outputs.append(list(getattr(self.cleaner, "_last_generated_outputs", [])))

                    # Списочный режим Worker не использует пакетную сверку,
                    # удаляем временный файл .reconcile_temp, чтобы в папке не оставался лишний DOCX
                    last_reconcile = getattr(self.cleaner, "_last_reconcile_temp", None)
                    if last_reconcile and hasattr(last_reconcile, "exists") and last_reconcile.exists():
                        try:
                            last_reconcile.unlink(missing_ok=True)
                        except OSError:
                            pass
                        self.cleaner._last_reconcile_temp = None

                    if enable_crash_recovery:
                        crash_recovery.update_batch_checkpoint(
                            self.cleaner._active_batch_id,
                            processed_file=file_path_str,
                            mapping_delta=batch_mapping,
                            replacements=changes_in_file,
                        )
                except Exception as e:
                    from log_sanitizer import sanitize_error_message
                    error_count += 1
                    safe_err = sanitize_error_message(e)
                    error_title = "Ошибка обработки файла"
                    error_message = f"Критическая ошибка при обработке файла '{file_path.name}':\n{safe_err}"
                    if enable_crash_recovery:
                        crash_recovery.update_batch_checkpoint(
                            self.cleaner._active_batch_id,
                            processed_file=file_path_str,
                            error=safe_err,
                        )
                    display_err = str(e) if isinstance(e, (ValueError, PermissionError, FileNotFoundError)) else safe_err
                    from log_sanitizer import sanitize_user_paths
                    display_err = sanitize_user_paths(display_err)
                    _cleaner_win = getattr(self.cleaner, '_window', None)
                    if _cleaner_win: ui_bridge.ui_alert(_cleaner_win, f"Ошибка при обработке {file_path.name}:\n{display_err}")
                    _private_status(f"{error_title}: {error_message}\n{traceback.format_exc()}")

                self.cleaner.update_progress(i + 1, num_files, f"Завершено: {file_path.name}")

            if processed_count > 0 and getattr(self.cleaner, "save_decoder", False) and batch_mapping and total_changes_all_files > 0:
                emit_sidecars = bool(getattr(self.cleaner, "emit_audit_sidecars", False))
                # Every adjacent decoder receives the final batch-wide map, not a
                # partial snapshot from the moment its file happened to finish.
                all_batch_outputs = [out for sublist in completed_outputs for out in sublist]
                for source_path, outputs in zip(processed_files, completed_outputs):
                    path = Path(source_path)
                    output_parent = Path(outputs[0]).parent if outputs else path.parent
                    decoder_path = output_parent / f"{path.stem}_Дешифратор.json"
                    if decoder_path.exists() or (output_parent / f"{path.stem}_дешифратор.json").exists():
                        decoder_path = output_parent / f"{path.stem}_{self.cleaner._active_batch_id}_Дешифратор.json"
                    try:
                        save_decoder_atomic(decoder_path, batch_mapping)
                        target_outputs = all_batch_outputs if all_batch_outputs else outputs
                        if target_outputs and error_count == 0 and not (hasattr(self.cleaner, 'is_cancelled') and self.cleaner.is_cancelled()):
                            from decoder_binding import publish_binding
                            publish_binding(decoder_path, target_outputs, self.cleaner._active_batch_id, emit_sidecars=emit_sidecars)
                    except Exception as exc:
                        error_count += 1
                        logger.warning("Не удалось зафиксировать пакетный дешифратор: %s", type(exc).__name__)

            is_canc = bool(hasattr(self.cleaner, 'is_cancelled') and self.cleaner.is_cancelled())
            if enable_crash_recovery and not is_canc and error_count == 0:
                crash_recovery.complete_batch_checkpoint(self.cleaner._active_batch_id)
            elif enable_crash_recovery:
                logger.info("Сессия %s завершена с ошибками (%d) или отменена; чекпоинт сохранён для возобновления", self.cleaner._active_batch_id, error_count)

            if processed_count > 0 and processed_files and getattr(self.cleaner, "open_output_folder", True):
                try:
                    first_parent = Path(processed_files[0]).resolve().parent
                    self.cleaner._last_output_dir = str(first_parent)
                    if first_parent.exists():
                        app_paths.open_folder_in_file_manager(first_parent)
                except Exception as exc:
                    logger.debug("Не удалось автоматически открыть папку с результатами: %s", exc)

            is_canc = bool(hasattr(self.cleaner, 'is_cancelled') and self.cleaner.is_cancelled())
            if hasattr(self.cleaner, 'finish_operation') and callable(self.cleaner.finish_operation):
                self.cleaner.finish_operation("succeeded" if error_count == 0 and not is_canc else ("partial" if processed_count > 0 else "failed"))

        except Exception as e:
            logger.exception("Критическая ошибка в потоке обработки")
            from log_sanitizer import sanitize_error_message
            _cleaner_win = getattr(self.cleaner, '_window', None)
            if _cleaner_win:
                if getattr(self.cleaner, "is_quick_mode", False):
                    ui_bridge.ui_set_global_progress(_cleaner_win, True, "DOCXдодыр — Ошибка", f"Критическая ошибка обработки:\n{sanitize_error_message(e)}", 0)
                else:
                    ui_bridge.ui_alert(_cleaner_win, f"Критическая ошибка обработки пакета:\n{sanitize_error_message(e)}")
            if hasattr(self.cleaner, 'finish_operation') and callable(self.cleaner.finish_operation):
                self.cleaner.finish_operation("failed")
        finally:
            if getattr(self.cleaner, '_window', None):
                self.cleaner.on_worker_finished(processed_count, error_count, total_changes_all_files)
            is_cancelled = bool(hasattr(self.cleaner, 'is_cancelled') and self.cleaner.is_cancelled())
            self.last_result = {
                "status": (
                    "failed" if processed_count == 0 and (error_count or is_cancelled) else
                    "partial" if error_count or is_cancelled else "succeeded"
                ),
                "processed_count": processed_count,
                "error_count": error_count,
                "total_changes": total_changes_all_files,
                "cancelled": is_cancelled,
            }
        return self.last_result
