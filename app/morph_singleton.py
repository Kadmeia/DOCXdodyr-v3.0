"""Единый ленивый синглтон pymorphy3.MorphAnalyzer для DOCXdodyr.

Три функции в ``legal_pullenti.py`` ранее создавали собственные
экземпляры через ``setattr``, удерживая ~75 МБ (3×25 МБ).
Этот модуль предоставляет один общий экземпляр, экономя ~50 МБ
и упрощая управление жизненным циклом.
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_ANALYZER = None


def get_morph_analyzer():
    """Возвращает единственный экземпляр MorphAnalyzer (~25 МБ).

    Потокобезопасен.  Если pymorphy3 не установлен, возвращает ``None``.
    """
    global _ANALYZER
    if _ANALYZER is not None:
        return _ANALYZER
    with _LOCK:
        if _ANALYZER is not None:
            return _ANALYZER
        try:
            import pymorphy3
            _ANALYZER = pymorphy3.MorphAnalyzer()
        except Exception:
            return None
    return _ANALYZER
