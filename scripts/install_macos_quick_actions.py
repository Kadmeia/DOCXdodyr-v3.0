#!/usr/bin/env python3.11
"""Установить DOCXdodyr Quick Actions в пользовательский Services каталог."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from macos_context_menu import install_quick_actions  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Install DOCXdodyr Quick Actions")
    parser.add_argument("--home", type=Path, default=None, help="test/user home override")
    parser.add_argument("--app-path", type=Path, default=Path("/Applications/DOCXdodyr.app"))
    args = parser.parse_args()
    installed = install_quick_actions(home=args.home, app_path=args.app_path)
    for path in installed:
        print(path)
