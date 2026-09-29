# -*- coding: utf-8 -*-
"""Supported functional-test entry point for DOCXдодыр.

The previous script targeted the removed PyQt ``main_window`` application.
The current product uses pywebview, so functional behavior is exercised by
the maintained pytest scenarios with local models and temporary documents.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FUNCTIONAL_TESTS = (
    "tests/test_human_scenarios.py",
    "tests/test_batch_entity_ids.py",
    "tests/test_entity_restoration.py",
    "tests/test_table_entity_context.py",
    "tests/test_ocr_backend.py",
    "tests/test_qwen_postprocessor.py",
)


def run_tests() -> bool:
    command = [sys.executable, "-m", "pytest", "-q", *FUNCTIONAL_TESTS]
    return subprocess.call(command, cwd=PROJECT_ROOT) == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
