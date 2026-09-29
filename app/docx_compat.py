# -*- coding: utf-8 -*-
"""Совместимость python-docx в упакованных PyInstaller окружениях (macOS/Windows).

В скомпилированном бандле PyInstaller (onedir/app) Python-байткод упакован в архив
base_library.zip, а файлы данных извлекаются в каталоги бандла (Resources/Frameworks).
Модули docx.parts (hdrftr, styles, settings, comments) обращаются к своим встроенным
XML-шаблонам через относительные пути вида:
    os.path.join(os.path.split(__file__)[0], "..", "templates", "default-header.xml")
В файловых системах POSIX (macOS APFS) вызов open() падает с Errno 2 ENOENT, если
промежуточная директория docx/parts отсутствует на диске, даже если templates/
существует.

Данный модуль:
1. Гарантирует создание каталогов docx (parts, oxml, opc, templates) на диске.
2. Патчит функции загрузки шаблонов python-docx, чтобы они безопасно находили
   файлы шаблонов напрямую без уязвимости к отсутствию промежуточных каталогов.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_PATCHED = False


def _find_docx_template(filename: str) -> Optional[Path]:
    """Ищет файл шаблона docx во всех возможных расположениях бандла и среды."""
    candidates: list[Path] = []

    # 1. Расположение через импортированный пакет docx
    try:
        import docx
        docx_file = getattr(docx, "__file__", None)
        if docx_file:
            candidates.append(Path(docx_file).resolve().parent / "templates" / filename)
    except Exception:
        pass

    # 2. PyInstaller sys._MEIPASS
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "docx" / "templates" / filename)

        # 3. macOS .app bundle структуры: Contents/Resources и Contents/Frameworks
        try:
            exec_p = Path(sys.executable).resolve()
            app_contents = exec_p.parent.parent  # .../Contents
            candidates.append(app_contents / "Resources" / "docx" / "templates" / filename)
            candidates.append(app_contents / "Frameworks" / "docx" / "templates" / filename)
        except Exception:
            pass

    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            pass

    return None


def ensure_docx_directories() -> None:
    """Создает каталоги docx на диске в замороженном приложении, если их нет."""
    targets: list[Path] = []

    try:
        import docx
        docx_file = getattr(docx, "__file__", None)
        if docx_file:
            targets.append(Path(docx_file).resolve().parent)
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            targets.append(Path(meipass) / "docx")
        try:
            exec_p = Path(sys.executable).resolve()
            app_contents = exec_p.parent.parent
            targets.append(app_contents / "Resources" / "docx")
            targets.append(app_contents / "Frameworks" / "docx")
        except Exception:
            pass

    for docx_dir in targets:
        try:
            if docx_dir.exists() or docx_dir.parent.exists():
                for sub in ("parts", "oxml", "opc", "templates"):
                    sub_p = docx_dir / sub
                    sub_p.mkdir(parents=True, exist_ok=True)
                    keep_file = sub_p / ".keep"
                    if not keep_file.exists():
                        try:
                            keep_file.touch()
                        except OSError:
                            pass
        except Exception as e:
            logger.debug("Не удалось создать поддиректории docx в %s: %s", docx_dir, e)


def apply_docx_patches() -> None:
    """Применяет безопасные патчи к python-docx."""
    global _PATCHED
    if _PATCHED:
        return

    ensure_docx_directories()

    def _read_bytes(filename: str, original_func=None) -> bytes:
        tmpl_path = _find_docx_template(filename)
        if tmpl_path and tmpl_path.is_file():
            return tmpl_path.read_bytes()
        if original_func:
            return original_func()
        raise FileNotFoundError(f"Шаблон python-docx не найден: {filename}")

    try:
        import docx.parts.hdrftr as hdrftr
        orig_hdr = hdrftr.HeaderPart._default_header_xml
        orig_ftr = hdrftr.FooterPart._default_footer_xml
        hdrftr.HeaderPart._default_header_xml = classmethod(lambda cls: _read_bytes("default-header.xml", orig_hdr))
        hdrftr.FooterPart._default_footer_xml = classmethod(lambda cls: _read_bytes("default-footer.xml", orig_ftr))
    except Exception as e:
        logger.debug("Патч hdrftr не применен: %s", e)

    try:
        import docx.parts.styles as styles
        orig_styles = styles.StylesPart._default_styles_xml
        styles.StylesPart._default_styles_xml = classmethod(lambda cls: _read_bytes("default-styles.xml", orig_styles))
    except Exception as e:
        logger.debug("Патч styles не применен: %s", e)

    try:
        import docx.parts.settings as settings
        orig_settings = settings.SettingsPart._default_settings_xml
        settings.SettingsPart._default_settings_xml = classmethod(lambda cls: _read_bytes("default-settings.xml", orig_settings))
    except Exception as e:
        logger.debug("Патч settings не применен: %s", e)

    try:
        import docx.parts.comments as comments
        orig_comments = comments.CommentsPart._default_comments_xml
        comments.CommentsPart._default_comments_xml = classmethod(lambda cls: _read_bytes("default-comments.xml", orig_comments))
    except Exception as e:
        logger.debug("Патч comments не применен: %s", e)

    try:
        import docx.api as docx_api
        orig_docx_path = docx_api._default_docx_path

        def _safe_default_docx_path():
            tmpl = _find_docx_template("default.docx")
            if tmpl and tmpl.is_file():
                return str(tmpl)
            return orig_docx_path()

        docx_api._default_docx_path = _safe_default_docx_path
    except Exception as e:
        logger.debug("Патч docx.api._default_docx_path не применен: %s", e)

    _PATCHED = True
    logger.info("Патчи совместимости python-docx успешно активированы")


# Автоматическая активация при импорте модуля
apply_docx_patches()
