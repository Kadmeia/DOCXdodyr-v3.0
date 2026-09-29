#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Совместимый entry point для ``python tools/build_pullenti_candidates.py``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from corpus_annotation import main


if __name__ == "__main__":
    raise SystemExit(main())
