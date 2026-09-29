# -*- coding: utf-8 -*-
"""Класс DocumentRestorer для восстановления данных из обезличенных документов."""
from __future__ import annotations
import json
import logging
from pathlib import Path
import re
import hashlib
import os
import tempfile
import zipfile
from xml.etree import ElementTree as ET

from placeholders import BRACKET_TYPES

from entity_inflection import ContextCaseResolver, EntityInflector

logger = logging.getLogger(__name__)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


META_KEY = "__docxdodyr_meta__"
BINDING_SCHEMA_V2 = "docxdodyr.decoder-binding/v2"
DECODER_SCHEMA_V2 = "docxdodyr.decoder/v2"


def save_docm_preserving_vba(source_path, document, output_path):
    """Save a python-docx document while preserving the DOCM macro package.

    python-docx deliberately writes a DOCX package and drops vbaProject.bin.
    Build the edited package privately, transplant only the macro parts and
    their OOXML declarations, then validate before publishing atomically.
    """
    source_path = Path(source_path).resolve(strict=True)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".docm-base-", suffix=".docx", dir=output_path.parent)
    os.close(fd)
    temp_docx = Path(temp_name)
    fd, temp_name = tempfile.mkstemp(prefix=".docm-final-", suffix=".docm", dir=output_path.parent)
    os.close(fd)
    temp_output = Path(temp_name)
    try:
        document.save(str(temp_docx))
        with zipfile.ZipFile(source_path, "r") as source_zip, zipfile.ZipFile(temp_docx, "r") as generated_zip:
            from hidden_data import validate_safe_zip_archive
            validate_safe_zip_archive(source_zip)
            source_names = set(source_zip.namelist())
            macro_names = {
                name for name in source_names
                if name.casefold().endswith("vbaproject.bin") or name.casefold().startswith("customui/")
            }
            if not any(name.casefold().endswith("vbaproject.bin") for name in macro_names):
                # A macro-free file carrying a .docm suffix needs no macro
                # transplant; retain the historical DOCX-compatible output.
                document.save(str(output_path))
                return
            generated = {name: generated_zip.read(name) for name in generated_zip.namelist()}

        rels_name = "word/_rels/document.xml.rels"
        content_types_name = "[Content_Types].xml"
        with zipfile.ZipFile(source_path, "r") as source_zip:
            source_rels = source_zip.read(rels_name)
            source_types = source_zip.read(content_types_name)
            for name in macro_names:
                generated[name] = source_zip.read(name)

        rel_root = ET.fromstring(generated.get(rels_name, b""))
        src_rel_root = ET.fromstring(source_rels)
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        rel_tag = "{" + rel_ns + "}Relationship"
        have_vba = any(
            str(node.attrib.get("Type", "")).casefold().endswith("/vbaproject")
            for node in rel_root.findall(rel_tag)
        )
        if not have_vba:
            vba_rels = [
                node for node in src_rel_root.findall(rel_tag)
                if str(node.attrib.get("Type", "")).casefold().endswith("/vbaproject")
            ]
            if not vba_rels:
                raise ValueError("DOCM relationship для vbaProject.bin отсутствует")
            rel_root.append(vba_rels[0])
        generated[rels_name] = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)

        types_root = ET.fromstring(generated.get(content_types_name, b""))
        src_types_root = ET.fromstring(source_types)
        ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        override_tag = "{" + ct_ns + "}Override"
        src_main = next(
            (node for node in src_types_root.findall(override_tag)
             if node.attrib.get("PartName") == "/word/document.xml"), None
        )
        main = next(
            (node for node in types_root.findall(override_tag)
             if node.attrib.get("PartName") == "/word/document.xml"), None
        )
        if src_main is None or main is None or "macroEnabled" not in src_main.attrib.get("ContentType", ""):
            raise ValueError("DOCM macroEnabled content type отсутствует")
        main.set("ContentType", src_main.attrib["ContentType"])
        source_defaults = {node.attrib.get("Extension"): node for node in src_types_root}
        for node in src_types_root:
            ext = node.attrib.get("Extension", "").casefold()
            if ext == "bin" and not any(
                existing.attrib.get("Extension", "").casefold() == "bin"
                for existing in types_root
            ):
                types_root.append(node)
        generated[content_types_name] = ET.tostring(types_root, encoding="utf-8", xml_declaration=True)

        with zipfile.ZipFile(temp_output, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for name, data in generated.items():
                out_zip.writestr(name, data)
        with zipfile.ZipFile(temp_output, "r") as check_zip:
            check_names = set(check_zip.namelist())
            if not any(name.casefold().endswith("vbaproject.bin") for name in check_names):
                raise ValueError("Сохранённый DOCM не содержит vbaProject.bin")
            check_types = check_zip.read(content_types_name).decode("utf-8", "replace")
            check_rels = check_zip.read(rels_name).decode("utf-8", "replace")
            if "macroEnabled" not in check_types or "vbaProject" not in check_rels:
                raise ValueError("Проверка OOXML DOCM macro relationship/content type не пройдена")
        os.replace(temp_output, output_path)
    finally:
        temp_docx.unlink(missing_ok=True)
        temp_output.unlink(missing_ok=True)


def _docm_as_docx_for_reading(source_path):
    """Create a private DOCX-view of a DOCM so python-docx can read it."""
    from hidden_data import validate_safe_zip_archive

    fd, temp_name = tempfile.mkstemp(prefix=".docm-read-", suffix=".docx")
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(source_path, "r") as source_zip, zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as out_zip:
            validate_safe_zip_archive(source_zip)
            for item in source_zip.infolist():
                data = source_zip.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    data = data.replace(
                        b"application/vnd.ms-word.document.macroEnabled.main+xml",
                        b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                    )
                out_zip.writestr(item, data)
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def extract_decoder_mapping(data: dict) -> dict:
    """Возвращает очищенный словарь токенов из структуры дешифратора, исключая метаданные привязки."""
    if not isinstance(data, dict):
        return {}
    if data.get("schema") in (BINDING_SCHEMA_V2, DECODER_SCHEMA_V2) and isinstance(data.get("mapping"), dict):
        return dict(data["mapping"])
    return {
        k: v for k, v in data.items()
        if k not in (META_KEY, "__binding__", "schema", "run_id", "documents")
    }


def _bound_decoder_for_document(document):
    """Resolve the exact decoder cryptographically bound to an output file."""
    document = Path(document).resolve(strict=True)

    sidecar = document.with_name(document.name + ".provenance.json")
    if sidecar.is_symlink():
        raise ValueError("Нарушена безопасная привязка дешифратора: provenance является ссылкой")
    if sidecar.exists():
        if not sidecar.is_file():
            raise ValueError("Нарушена безопасная привязка дешифратора: provenance недоступен")
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if payload.get("schema") != "docxdodyr.decoder-binding/v1":
                raise ValueError("неверная схема provenance")
            expected_output = str(payload.get("output_sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", expected_output) or _sha256(document) != expected_output:
                raise ValueError("Хеш документа не совпадает с файлом привязки")
            if not isinstance(payload.get("run_id"), str) or not payload["run_id"].strip():
                raise ValueError("Идентификатор задания отсутствует")
            decoder = sidecar.parent / str(payload["decoder_path"])
            if decoder.is_symlink() or not decoder.is_file():
                raise ValueError("Привязанный дешифратор недоступен")
            decoder = decoder.resolve(strict=True)
            decoder_hash = _sha256(decoder)
            if decoder_hash != str(payload.get("decoder_sha256", "")):
                raise ValueError("Хеш дешифратора не совпадает с файлом привязки")
            # A decoder referenced by a sidecar is trusted only when a complete
            # batch manifest in the decoder's own directory binds the same run,
            # path and hash.  This prevents ``../../arbitrary.json`` bindings and
            # eliminates fallback to an unrelated newest decoder.
            if decoder.parent not in sidecar.parents:
                raise ValueError("Дешифратор находится вне корня задания")
            manifest_bound = False
            for manifest_path in decoder.parent.glob("*.manifest.json"):
                if manifest_path.is_symlink() or not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    info = manifest.get("decoder") or {}
                    manifest_decoder = (decoder.parent / str(info.get("path", ""))).resolve(strict=True)
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
                if (
                    manifest.get("schema") == "docxdodyr.batch-manifest/v1"
                    and manifest.get("complete") is True
                    and not manifest.get("errors")
                    and str(manifest.get("run_id", "")) == str(payload.get("run_id", ""))
                    and manifest_decoder == decoder
                    and str(info.get("sha256", "")) == decoder_hash
                    and any(
                        (decoder.parent / str(output.get("path", ""))).resolve() == document.resolve()
                        and output.get("sha256") == expected_output
                        for record in manifest.get("documents", []) + manifest.get("derived_documents", [])
                        for output in record.get("outputs", [])
                    )
                ):
                    manifest_bound = True
                    break
            if not manifest_bound:
                raise ValueError("Дешифратор не подтверждён завершённым manifest")
            data = json.loads(decoder.read_text(encoding="utf-8"))
            if not is_valid_decoder_structure(data):
                raise ValueError("Привязанный дешифратор имеет неверную структуру")
            return decoder
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Нарушена безопасная привязка дешифратора: {exc}") from exc

    # Если обнаружен legacy-манифест, привязывающий этот документ, но provenance отсутствует:
    check_dir = document.parent
    for _ in range(5):
        for m_path in check_dir.glob("*.manifest.json"):
            if m_path.is_file() and not m_path.is_symlink():
                try:
                    m_data = json.loads(m_path.read_text(encoding="utf-8"))
                    for rec in m_data.get("documents", []) + m_data.get("derived_documents", []):
                        for out in rec.get("outputs", []):
                            out_p = out.get("path", "")
                            if (m_path.parent / out_p).resolve() == document.resolve():
                                raise ValueError("Нарушена безопасная привязка дешифратора: provenance отсутствует")
                except ValueError:
                    raise
                except Exception:
                    pass
        if check_dir.parent == check_dir:
            break
        check_dir = check_dir.parent

    # Новый механизм: поиск дешифратора с криптографической привязкой __docxdodyr_meta__ без sidecar-файлов
    doc_hash = _sha256(document)
    base = re.sub(r"(?:_cleaned|_правки|_восстановлено)(?:_\d+)?$", "", document.stem, flags=re.IGNORECASE).strip("_")
    current_dir = document.parent
    max_parent_levels = 4
    candidates = []
    level = 0
    while current_dir and level <= max_parent_levels:
        level_candidates = [
            current_dir / f"{base}_Дешифратор.json",
            current_dir / f"{base}_дешифратор.json",
            current_dir / f"{document.stem}_Дешифратор.json",
            current_dir / f"{document.stem}_дешифратор.json",
            current_dir / "Дешифратор.json",
            current_dir / "дешифратор.json",
        ]
        try:
            for p in sorted(current_dir.glob("*[Дд]ешифратор*.json"), reverse=True)[:50]:
                if p not in level_candidates:
                    level_candidates.append(p)
            for p in sorted(current_dir.glob("*Дешифратор*.json"), reverse=True)[:50]:
                if p not in level_candidates:
                    level_candidates.append(p)
            for p in sorted(current_dir.glob("*дешифратор*.json"), reverse=True)[:50]:
                if p not in level_candidates:
                    level_candidates.append(p)
            for p in sorted(current_dir.glob("*decoder*.json"), reverse=True)[:50]:
                if p not in level_candidates:
                    level_candidates.append(p)
            for p in sorted(current_dir.glob("*.json"))[:50]:
                if p not in level_candidates:
                    level_candidates.append(p)
        except OSError:
            pass
        for c in level_candidates:
            if c not in candidates and c.is_file() and not c.is_symlink():
                candidates.append(c)
        if current_dir.parent == current_dir:
            break
        current_dir = current_dir.parent
        level += 1

    matched_decoders = []
    for candidate in candidates:
        if candidate.stat().st_size > 10 * 1024 * 1024:
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        meta = data.get(META_KEY) or (data if data.get("schema") in (BINDING_SCHEMA_V2, DECODER_SCHEMA_V2) else None)
        if not meta or not isinstance(meta, dict):
            continue
        if meta.get("schema") not in (BINDING_SCHEMA_V2, DECODER_SCHEMA_V2):
            continue
        if meta.get("complete") is not True:
            continue
        run_id = meta.get("run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            continue
        docs = meta.get("documents", [])
        if not isinstance(docs, list) or not docs:
            continue

        doc_matched = False
        for doc_entry in docs:
            if not isinstance(doc_entry, dict):
                continue
            entry_path = doc_entry.get("path")
            if not isinstance(entry_path, str) or not entry_path.strip():
                continue
            rel_p = Path(entry_path)
            if rel_p.is_absolute() or ".." in rel_p.parts:
                continue
            try:
                if (candidate.parent / rel_p).resolve() == document.resolve():
                    expected_sha = doc_entry.get("sha256")
                    if not expected_sha or expected_sha != doc_hash:
                        raise ValueError(f"Хеш документа не совпадает с дешифратором: файл был изменён ({document.name})")
                    if not is_valid_decoder_structure(data):
                        raise ValueError("Привязанный дешифратор имеет неверную структуру")
                    doc_matched = True
                    break
            except ValueError:
                raise
            except Exception:
                pass

        if doc_matched:
            cand_res = candidate.resolve()
            if cand_res not in matched_decoders:
                matched_decoders.append(cand_res)

    if len(matched_decoders) > 1:
        exact_doc_dec = (document.parent / f"{base}_Дешифратор.json").resolve()
        if exact_doc_dec not in matched_decoders:
            exact_doc_dec = (document.parent / f"{base}_дешифратор.json").resolve()
        if exact_doc_dec in matched_decoders:
            filtered = []
            for cand in matched_decoders:
                if cand == exact_doc_dec:
                    filtered.append(cand)
                else:
                    cand_stem = cand.stem
                    cand_stem_lower = cand_stem.lower()
                    if cand_stem_lower.endswith("_дешифратор"):
                        other_stem = cand_stem[:-11]
                        has_other_doc = any(
                            p.name.startswith(other_stem) and p != document
                            for p in document.parent.iterdir()
                            if p.is_file() and not p.name.endswith(".json")
                        )
                        if has_other_doc:
                            continue
                    filtered.append(cand)
            if len(filtered) == 1:
                return filtered[0]
            matched_decoders = filtered

        # A batch intentionally writes one adjacent decoder per source file,
        # and each of those decoders contains the same final batch-wide map and
        # binding.  A derived document can therefore be validly referenced by
        # several byte-for-byte equivalent decoders.  This is not an
        # ambiguity: restoring with any of them produces the same result.
        # Keep failing closed when the mappings differ.
        equivalent_mapping = None
        equivalent = True
        for decoder_path in matched_decoders:
            try:
                decoder_data = json.loads(decoder_path.read_text(encoding="utf-8"))
                decoder_mapping = extract_decoder_mapping(decoder_data)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                equivalent = False
                break
            if equivalent_mapping is None:
                equivalent_mapping = decoder_mapping
            elif decoder_mapping != equivalent_mapping:
                equivalent = False
                break
        if equivalent:
            return matched_decoders[0]

        raise ValueError(
            f"Неоднозначность: найдено {len(matched_decoders)} подходящих дешифраторов для документа {document.name}"
        )

    if matched_decoders:
        return matched_decoders[0]

    raise ValueError("Нарушена безопасная привязка дешифратора: отсутствует привязка к документу")


def is_valid_decoder_structure(data):
    """Проверяет, что JSON имеет правильную структуру словаря дешифратора."""
    if not isinstance(data, dict):
        return False
    # Enveloped V2 format
    if data.get("schema") in (BINDING_SCHEMA_V2, DECODER_SCHEMA_V2) and "mapping" in data:
        mapping = data.get("mapping")
        if not isinstance(mapping, dict):
            return False
        for k, v in mapping.items():
            if not isinstance(k, str) or not isinstance(v, (str, dict)):
                return False
            if isinstance(v, dict) and not isinstance(v.get("original"), str):
                return False
            if not _is_placeholder_token(k):
                return False
        return True

    # Flat mapping or mapping with __docxdodyr_meta__
    tokens_found = 0
    for k, v in data.items():
        if k in (META_KEY, "__binding__"):
            if not isinstance(v, dict):
                return False
            continue
        if not isinstance(k, str) or not isinstance(v, (str, dict)):
            return False
        if isinstance(v, dict) and not isinstance(v.get("original"), str):
            return False
        if not _is_placeholder_token(k):
            return False
        tokens_found += 1
    return True


def _is_placeholder_token(value):
    """Accept every configured placeholder bracket style, not arbitrary JSON."""

    if not isinstance(value, str) or "\n" in value or len(value) > 160:
        return False
    for left, right in BRACKET_TYPES.values():
        if value.startswith(left) and value.endswith(right):
            body = value[len(left):len(value) - len(right) if right else None]
            return bool(body and re.fullmatch(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:/\-]{0,119}", body))
    return False


def find_decoder_near_document(doc_path, *, max_parent_levels=4, max_decoder_bytes=10 * 1024 * 1024):
    """Безопасно ищет дешифратор рядом с документом и у его родителей.

    Поиск ограничен четырьмя уровнями вверх, не следует симлинкам и принимает
    только JSON с валидной структурой. Это покрывает папочный режим
    ``<корень>/обезличенные/...`` и не превращает восстановление в сканирование
    всего диска. Приоритет у точного имени документа, затем у общего
    ``дешифратор.json`` и у самого свежего файла.
    """
    raw_path = Path(doc_path).expanduser()
    if raw_path.is_symlink():
        return None
    p = raw_path.resolve(strict=False)
    if not p.exists() or not p.is_file():
        return None
    try:
        res = _bound_decoder_for_document(p)
        if res and res.is_file():
            return res
    except ValueError as exc:
        # Only a genuinely unbound legacy document may use the filename-based
        # fallback below.  Integrity failures and conflicting bound decoders
        # must stay visible instead of being replaced with an arbitrary JSON.
        if str(exc) != "Нарушена безопасная привязка дешифратора: отсутствует привязка к документу":
            raise
    except (OSError, json.JSONDecodeError):
        pass

    # Интеллектуальный fallback: поиск дешифратора в папке документа и в родительских папках
    base = re.sub(r"(?:_cleaned|_правки|_восстановлено)(?:_\d+)?$", "", p.stem, flags=re.IGNORECASE).strip("_")
    curr = p.parent
    for _ in range(max_parent_levels + 1):
        candidates = [
            curr / f"{base}_Дешифратор.json",
            curr / f"{base}_дешифратор.json",
            curr / f"{p.stem}_Дешифратор.json",
            curr / f"{p.stem}_дешифратор.json",
            curr / "Дешифратор.json",
            curr / "дешифратор.json",
        ]
        # Также проверяем файлы *[Дд]ешифратор*.json в этой папке
        try:
            for globbed in sorted(curr.glob("*[Дд]ешифратор*.json"))[:50]:
                if globbed not in candidates:
                    candidates.append(globbed)
        except OSError:
            pass

        for cand in candidates:
            if cand.is_file() and not cand.is_symlink():
                try:
                    if cand.stat().st_size <= max_decoder_bytes:
                        data = json.loads(cand.read_text(encoding="utf-8"))
                        if is_valid_decoder_structure(data):
                            return cand.resolve()
                except Exception:
                    pass
        if curr.parent == curr:
            break
        curr = curr.parent

    return None


class DocumentRestorer:
    """Восстанавливает исходные данные в обезличенном документе по JSON-дешифратору."""

    def __init__(self, mapping_dict, log_callback=None):
        """
        Args:
            mapping_dict: {"[ФИО_1]": "Иванов И.И.", "[Наименование_1]": "ООО Ромашка", ...}
            log_callback: optional callable(str) для логирования
        """
        self.mapping_dict = extract_decoder_mapping(mapping_dict or {})
        self.log = log_callback or (lambda s: None)
        self.case_resolver = ContextCaseResolver()
        self.inflector = EntityInflector()

    def _verify_document(self, path):
        decoder = _bound_decoder_for_document(Path(path).resolve(strict=True))
        raw_mapping = json.loads(decoder.read_text(encoding="utf-8"))
        mapping = extract_decoder_mapping(raw_mapping)
        if mapping != self.mapping_dict:
            raise ValueError("Выбранный дешифратор не принадлежит документу")

    def apply_replacements(self, text):
        """Применяет замены по маппингу. Сортирует ключи по длине (длинные сначала)."""
        if not text or not self.mapping_dict:
            return text
        keys = sorted(self.mapping_dict.keys(), key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(key) for key in keys))

        def replace(match):
            value = self.mapping_dict[match.group(0)]
            if isinstance(value, str):
                return value
            fallback = value.get("case", "NOMINATIVE")
            target_case = self.case_resolver.resolve(text, match.start(), fallback)
            return self.inflector.inflect(value, target_case)

        return pattern.sub(replace, text)

    def _restore_paragraph(self, paragraph):
        """Restore tokens in OOXML text nodes without flattening run styles.

        Placeholders may be split across Word runs.  Replacing from right to
        left lets us keep hyperlinks, fields, content-control wrappers and all
        unaffected run formatting; only the first text node of a token receives
        the restored value.
        """

        try:
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            nodes = list(paragraph._p.xpath(".//w:t | .//w:instrText", namespaces=ns))
        except Exception:
            from docx.oxml.ns import qn
            nodes = list(paragraph._p.iter(qn("w:t")))
        if not nodes:
            return 0
        keys = sorted(self.mapping_dict, key=len, reverse=True)
        if not keys:
            return 0
        pattern = re.compile("|".join(re.escape(key) for key in keys))
        changes = 0
        _MAX_RESTORE_PASSES = 500
        while True:
            if changes >= _MAX_RESTORE_PASSES:
                logger.error(
                    "Превышен лимит итераций (%d) при восстановлении параграфа; "
                    "возможна рекурсивная замена в маппинге дешифратора",
                    _MAX_RESTORE_PASSES,
                )
                break
            current = "".join(node.text or "" for node in nodes)
            matches = list(pattern.finditer(current))
            if not matches:
                return changes
            match = matches[-1]
            value = self.mapping_dict[match.group(0)]
            if isinstance(value, str):
                replacement = value
            else:
                fallback = value.get("case", "NOMINATIVE")
                target_case = self.case_resolver.resolve(current, match.start(), fallback)
                replacement = self.inflector.inflect(value, target_case)

            offsets = []
            cursor = 0
            for node in nodes:
                length = len(node.text or "")
                offsets.append((cursor, cursor + length, node))
                cursor += length
            touched = [item for item in offsets if item[0] < match.end() and match.start() < item[1]]
            if not touched:
                return changes
            first_start, _first_end, first_node = touched[0]
            last_start, _last_end, last_node = touched[-1]
            first_text = first_node.text or ""
            last_text = last_node.text or ""
            prefix = first_text[: match.start() - first_start]
            suffix = last_text[match.end() - last_start :]
            if first_node is last_node:
                first_node.text = prefix + replacement + suffix
            else:
                first_node.text = prefix + replacement
                for _start, _end, node in touched[1:-1]:
                    node.text = ""
                last_node.text = suffix
            changes += 1

    def restore_docx(self, doc_path, output_path=None):
        """
        Восстанавливает DOCX: обходит параграфы, таблицы, колонтитулы, врезки и SDT.
        Возвращает (success: bool, message: str).
        """
        self._verify_document(doc_path)
        try:
            from docx import Document
            from docx.text.paragraph import Paragraph
        except ImportError:
            return False, "Библиотека python-docx не установлена"

        doc_path = Path(doc_path)
        if output_path is None:
            output_path = doc_path.parent / f"{doc_path.stem}_восстановлено.docx"
        else:
            output_path = Path(output_path)

        read_path = None
        try:
            read_path = _docm_as_docx_for_reading(doc_path) if doc_path.suffix.casefold() == ".docm" else doc_path
            doc = Document(str(read_path))
        except Exception as e:
            logger.warning("Ошибка открытия DOCX: %s", e)
            return False, f"Не удалось открыть документ: {e}"
        finally:
            if read_path is not None and read_path != doc_path:
                read_path.unlink(missing_ok=True)

        total_replacements = 0
        processed_elements = set()

        # 1. Основной текст
        for i, para in enumerate(doc.paragraphs):
            try:
                processed_elements.add(para._p)
                if para.text and self.mapping_dict:
                    total_replacements += self._restore_paragraph(para)
            except Exception as e:
                logger.warning("Ошибка при обработке параграфа %s: %s", i, e)
                self.log(f"[Пар.{i}] Пропущен (ошибка): {e}")

        # 2. Таблицы
        for t_idx, table in enumerate(doc.tables):
            try:
                for r_idx, row in enumerate(table.rows):
                    try:
                        for c_idx, cell in enumerate(row.cells):
                            try:
                                for para in cell.paragraphs:
                                    try:
                                        processed_elements.add(para._p)
                                        if para.text and self.mapping_dict:
                                            total_replacements += self._restore_paragraph(para)
                                    except Exception as e:
                                        logger.warning("Ошибка параграфа в ячейке: %s", e)
                            except Exception as e:
                                logger.warning("Ошибка ячейки Таб.%s Р.%s Я.%s: %s", t_idx, r_idx, c_idx, e)
                                self.log(f"[Таб.{t_idx} Р.{r_idx} Я.{c_idx}] Пропущена: {e}")
                    except Exception as e:
                        logger.warning("Ошибка строки таблицы: %s", e)
            except Exception as e:
                logger.warning("Ошибка таблицы %s: %s", t_idx, e)

        # 3. Колонтитулы (параграфы и таблицы)
        for s_idx, section in enumerate(doc.sections):
            try:
                for block, name in [(section.header, "header"), (section.footer, "footer")]:
                    try:
                        for para in block.paragraphs:
                            try:
                                processed_elements.add(para._p)
                                if para.text and self.mapping_dict:
                                    total_replacements += self._restore_paragraph(para)
                            except Exception as e:
                                logger.warning("Ошибка параграфа в %s: %s", name, e)
                        for tbl in getattr(block, 'tables', []) or []:
                            try:
                                for row in tbl.rows:
                                    try:
                                        for cell in row.cells:
                                            try:
                                                for para in cell.paragraphs:
                                                    try:
                                                        processed_elements.add(para._p)
                                                        if para.text and self.mapping_dict:
                                                            total_replacements += self._restore_paragraph(para)
                                                    except Exception as e:
                                                        logger.warning("Ошибка параграфа в ячейке %s: %s", name, e)
                                            except Exception as e:
                                                logger.warning("Ошибка ячейки в %s: %s", name, e)
                                    except Exception as e:
                                        logger.warning("Ошибка строки в %s: %s", name, e)
                            except Exception as e:
                                logger.warning("Ошибка таблицы в %s: %s", name, e)
                    except Exception as e:
                        logger.warning("Ошибка блока %s секции %s: %s", name, s_idx, e)
            except Exception as e:
                logger.warning("Ошибка секции %s: %s", s_idx, e)

        # 4. Текстовые врезки (Text Boxes: w:txbxContent)
        try:
            for p_elm in doc.element.xpath(".//w:txbxContent//w:p"):
                if p_elm not in processed_elements:
                    processed_elements.add(p_elm)
                    try:
                        total_replacements += self._restore_paragraph(Paragraph(p_elm, doc))
                    except Exception as e:
                        logger.warning("Ошибка восстановления текстовой врезки: %s", e)
        except Exception as e:
            logger.warning("Ошибка обхода TextBoxes: %s", e)

        # 5. Элементы управления содержимым (SDT: Structured Document Tags)
        try:
            for p_elm in doc.element.xpath(".//w:sdt//w:sdtContent//w:p"):
                if p_elm not in processed_elements:
                    processed_elements.add(p_elm)
                    try:
                        total_replacements += self._restore_paragraph(Paragraph(p_elm, doc))
                    except Exception as e:
                        logger.warning("Ошибка восстановления SDT: %s", e)
        except Exception as e:
            logger.warning("Ошибка обхода SDT: %s", e)

        try:
            if doc_path.suffix.casefold() == ".docm":
                save_docm_preserving_vba(doc_path, doc, output_path)
            else:
                doc.save(str(output_path))
        except Exception as e:
            return False, f"Ошибка сохранения: {e}"

        self.log(f"Восстановлено замен: {total_replacements}")
        return True, f"Сохранено: {output_path}"

    def restore_excel(self, xlsx_path, output_path=None):
        """Восстанавливает Excel. Возвращает (success, message)."""
        self._verify_document(xlsx_path)
        try:
            from openpyxl import load_workbook
        except ImportError:
            return False, "Библиотека openpyxl не установлена"

        xlsx_path = Path(xlsx_path)
        if xlsx_path.suffix.lower() == ".xls":
            return False, "Устаревший бинарный формат .xls не поддерживается. Сохраните файл как .xlsx в Excel."
        if output_path is None:
            out_ext = ".xlsm" if xlsx_path.suffix.lower() == ".xlsm" else ".xlsx"
            output_path = xlsx_path.parent / f"{xlsx_path.stem}_восстановлено{out_ext}"
        else:
            output_path = Path(output_path)

        try:
            wb = load_workbook(str(xlsx_path), keep_vba=(xlsx_path.suffix.lower() == ".xlsm"))
        except Exception as e:
            return False, f"Не удалось открыть Excel: {e}"

        from xlsx_semantic import close_workbook
        try:
            total_replacements = 0
            for ws in wb.worksheets:
                try:
                    for row in ws.iter_rows():
                        for cell in row:
                            try:
                                if isinstance(cell.value, str) and cell.value and self.mapping_dict:
                                    new_val = self.apply_replacements(cell.value)
                                    if new_val != cell.value:
                                        cell.value = new_val
                                        total_replacements += 1
                            except Exception as e:
                                logger.warning("Ошибка ячейки Excel %s: %s", cell.coordinate, e)
                except Exception as e:
                    logger.warning("Ошибка листа %s: %s", ws.title, e)

            try:
                wb.save(str(output_path))
            except Exception as e:
                return False, f"Ошибка сохранения Excel: {e}"

            self.log(f"Восстановлено замен в Excel: {total_replacements}")
            return True, f"Сохранено: {output_path}"
        finally:
            close_workbook(wb)


    def restore_markdown(self, md_path, output_path=None):
        """Восстанавливает Markdown-документ (.md). Возвращает (success, message)."""
        self._verify_document(md_path)
        md_path = Path(md_path)
        if output_path is None:
            output_path = md_path.parent / f"{md_path.stem}_восстановлено.md"
        else:
            output_path = Path(output_path)

        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            return False, f"Не удалось прочитать Markdown: {e}"

        total_replacements = 0
        if self.mapping_dict and text:
            keys = sorted(self.mapping_dict.keys(), key=len, reverse=True)
            pattern = re.compile("|".join(re.escape(k) for k in keys))

            def replace(match):
                value = self.mapping_dict[match.group(0)]
                if isinstance(value, str):
                    return value
                fallback = value.get("case", "NOMINATIVE")
                target_case = self.case_resolver.resolve(text, match.start(), fallback)
                return self.inflector.inflect(value, target_case)

            restored, total_replacements = pattern.subn(replace, text)
        else:
            restored = text

        try:
            output_path.write_text(restored, encoding="utf-8")
        except Exception as e:
            return False, f"Ошибка сохранения Markdown: {e}"

        self.log(f"Восстановлено замен в Markdown: {total_replacements}")
        return True, f"Сохранено: {output_path}"


def restore_document(doc_path: str | Path, decoder_path: str | Path | None = None, output_path: str | Path | None = None) -> Path:
    """Восстанавливает обезличенный документ (.docx/.xlsx/.md) с криптографической проверкой привязки дешифратора."""
    doc_p = Path(doc_path).resolve(strict=True)
    if decoder_path is None:
        dec_p = find_decoder_near_document(doc_p)
    else:
        dec_raw = Path(decoder_path)
        if dec_raw.is_symlink():
            raise ValueError("Выбранный дешифратор является символической ссылкой")
        dec_p = dec_raw.resolve(strict=True)
        # An explicitly selected decoder is still required to be the exact
        # cryptographically bound decoder for this output.  Previously only
        # its mapping was read before _verify_document rediscovered a bound
        # decoder, so a second, unrelated file with the same mapping could be
        # supplied and silently accepted.
        bound = find_decoder_near_document(doc_p)
        if bound is None or bound != dec_p:
            raise ValueError("Выбранный дешифратор не привязан к документу")
    data = json.loads(dec_p.read_text(encoding="utf-8"))
    restorer = DocumentRestorer(data)
    if doc_p.suffix.lower() in {".docx", ".docm"}:
        target_out = Path(output_path) if output_path else doc_p.parent / f"{doc_p.stem}_восстановлено{doc_p.suffix}"
        ok, msg = restorer.restore_docx(doc_p, target_out)
    elif doc_p.suffix.lower() in {".xlsx", ".xlsm"}:
        target_out = Path(output_path) if output_path else doc_p.parent / f"{doc_p.stem}_восстановлено{doc_p.suffix}"
        ok, msg = restorer.restore_excel(doc_p, target_out)
    elif doc_p.suffix.lower() == ".md":
        target_out = Path(output_path) if output_path else doc_p.parent / f"{doc_p.stem}_восстановлено{doc_p.suffix}"
        ok, msg = restorer.restore_markdown(doc_p, target_out)
    elif doc_p.suffix.lower() == ".pdf":
        raise ValueError("Восстановление PDF не поддерживается: формат является необратимым")
    else:
        raise ValueError(f"Неподдерживаемый формат для восстановления: {doc_p.suffix}")
    if not ok:
        raise RuntimeError(f"Ошибка восстановления: {msg}")
    return target_out
