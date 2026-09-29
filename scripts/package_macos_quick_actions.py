#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Упаковать существующий macOS .app и Quick Actions в DMG.

Скрипт независим от основного build_macos.py: он не пересобирает PyInstaller
приложение и предназначен для отдельного этапа интеграции контекстного меню.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from macos_context_menu import materialize_quick_actions  # noqa: E402
from version import APP_VERSION  # noqa: E402


def package_quick_actions(
    app_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    arch: str,
    identity: str | None = None,
    skip_sign: bool = True,
) -> Path:
    """Create a self-contained DMG staging tree and return its path.

    ``build_macos.py`` embeds the launcher before signing the app. This
    packager deliberately verifies the copied payload and never mutates a
    signed bundle. ``identity`` is a Developer ID Application identity used
    only for the optional DMG signature.
    """
    if arch not in {"arm64", "x86_64"}:
        raise ValueError("arch must be arm64 or x86_64")
    app = Path(app_path).expanduser().resolve()
    if app.suffix != ".app" or not (app / "Contents" / "MacOS" / "DOCXdodyr").is_file():
        raise ValueError(f"Invalid DOCXdodyr app bundle: {app}")
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    dmgs = output / f"DOCXdodyr-{APP_VERSION}-macos-{arch}-quick-actions.dmg"
    staging = Path(tempfile.mkdtemp(prefix="docxdodyr-quick-actions-", dir=str(output)))
    try:
        # Build agents may label the source bundle with its target arch. The
        # installed product name must remain stable for LaunchServices and
        # the workflow's /Applications path.
        staged_app = staging / "DOCXdodyr.app"
        shutil.copytree(app, staged_app, symlinks=True)
        resources = staged_app / "Contents" / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        launcher_source = REPO_ROOT / "macos_context_menu.py"
        launcher_target = resources / launcher_source.name
        if not launcher_target.is_file() or launcher_target.read_bytes() != launcher_source.read_bytes():
            raise ValueError("Signed app is missing the pre-embedded Quick Actions launcher")
        # A DMG copy must not add resources after signing. Verify the exact
        # staged payload before hdiutil packages it.
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(staged_app)],
            check=True,
        )

        # The workflows are materialized for the final installation path. The
        # app is copied to /Applications by the standard DMG shortcut.
        quick_actions_dir = staging / "DOCXdodyr Quick Actions"
        materialize_quick_actions(quick_actions_dir, app_path=Path("/Applications/DOCXdodyr.app"))
        applications = staging / "Applications"
        applications.symlink_to("/Applications")

        install_cmd = staging / "Install DOCXdodyr Quick Actions.command"
        install_cmd.write_text(
            '#!/bin/zsh\nset -euo pipefail\n'
            'SELF_DIR="${0:A:h}"\n'
            'SERVICE_DIR="${HOME}/Library/Services"\n'
            'mkdir -p "$SERVICE_DIR"\n'
            'for workflow in "$SELF_DIR/DOCXdodyr Quick Actions/"*.workflow; do\n'
            '  /usr/bin/ditto "$workflow" "$SERVICE_DIR/${workflow:t}"\n'
            'done\n'
            '/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true\n',
            encoding="utf-8",
        )
        install_cmd.chmod(0o755)
        uninstall_cmd = staging / "Uninstall DOCXdodyr Quick Actions.command"
        uninstall_cmd.write_text(
            '#!/bin/zsh\nset -euo pipefail\n'
            'SERVICE_DIR="${HOME}/Library/Services"\n'
            'for name in "Обезличить DOCXdodyr.workflow" "Восстановить DOCXdodyr.workflow"; do\n'
            '  target="$SERVICE_DIR/$name"\n'
            '  info="$target/Contents/Info.plist"\n'
            '  identifier=$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$info" 2>/dev/null || true)\n'
            '  if [[ "$identifier" == ru.docxdodyr.quick-action.* ]]; then\n'
            '    rm -rf -- "$target"\n'
            '  fi\n'
            'done\n'
            '/System/Library/CoreServices/pbs -update >/dev/null 2>&1 || true\n',
            encoding="utf-8",
        )
        uninstall_cmd.chmod(0o755)

        if dmgs.exists():
            dmgs.unlink()
        subprocess.run(
            ["hdiutil", "create", "-volname", "DOCXdodyr", "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(dmgs)],
            check=True,
        )
        if not skip_sign and identity:
            subprocess.run(["codesign", "-s", identity, "--force", "--timestamp", str(dmgs)], check=True)
        subprocess.run(["hdiutil", "verify", str(dmgs)], check=True)
        return dmgs
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package DOCXdodyr Quick Actions DMG")
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--identity", default=None)
    parser.add_argument("--skip-sign", action="store_true")
    args = parser.parse_args(argv)
    result = package_quick_actions(args.app, args.output_dir, arch=args.arch, identity=args.identity, skip_sign=args.skip_sign)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
