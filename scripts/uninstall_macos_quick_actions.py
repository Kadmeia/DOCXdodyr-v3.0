#!/usr/bin/env python3.11
"""Удалить только зарегистрированные DOCXdodyr Quick Actions."""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from macos_context_menu import uninstall_quick_actions, uninstall_system_quick_actions  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", action="store_true", help="удалить из /Library/Services (требуются права)")
    parser.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    removed = uninstall_system_quick_actions(root=args.root) if args.system else uninstall_quick_actions()
    for path in removed:
        print(path)
