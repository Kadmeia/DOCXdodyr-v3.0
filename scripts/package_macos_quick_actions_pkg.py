#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Собрать unsigned/ad-hoc macOS PKG с приложением и системными Services."""

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


def package_quick_actions_pkg(
    app_path: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    arch: str,
    sign_identity: str | None = None,
    development: bool = False,
) -> Path:
    """Build a component pkg and wrap it with productbuild.

    ``build_macos.py`` embeds the launcher before signing the app. The staged
    bundle is verified after copying and before ``pkgbuild``. ``sign_identity``
    is deliberately reserved for a Developer ID Installer identity; it must
    never be used as an Application identity.
    """
    if arch not in {"arm64", "x86_64"}:
        raise ValueError("arch must be arm64 or x86_64")
    if sign_identity and not sign_identity.startswith("Developer ID Installer:"):
        raise ValueError("Quick Actions PKG requires a Developer ID Installer identity")
    source = Path(app_path).expanduser().resolve()
    executable = source / "Contents" / "MacOS" / "DOCXdodyr"
    if source.suffix != ".app" or not executable.is_file():
        raise ValueError(f"Invalid DOCXdodyr app bundle: {source}")

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    label = "-development" if development else ""
    final_pkg = output / f"DOCXdodyr-{APP_VERSION}-macos-{arch}{label}-quick-actions.pkg"
    component_pkg = output / f".DOCXdodyr-{APP_VERSION}-macos-{arch}-component.pkg"
    stage = Path(tempfile.mkdtemp(prefix="docxdodyr-pkg-", dir=str(output)))
    try:
        staged_app = stage / "Applications" / "DOCXdodyr.app"
        staged_app.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staged_app, symlinks=True)
        resources = staged_app / "Contents" / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        launcher_source = REPO_ROOT / "macos_context_menu.py"
        launcher_target = resources / launcher_source.name
        if not launcher_target.is_file() or launcher_target.read_bytes() != launcher_source.read_bytes():
            raise ValueError("Signed app is missing the pre-embedded Quick Actions launcher")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(staged_app)],
            check=True,
        )

        services = stage / "Library" / "Services"
        materialize_quick_actions(services, app_path=Path("/Applications/DOCXdodyr.app"))
        if component_pkg.exists():
            component_pkg.unlink()
        import plistlib

        components_data = [
            {
                "RootRelativeBundlePath": "Applications/DOCXdodyr.app",
                "BundleIsRelocatable": False,
                "BundleIsVersionChecked": False,
                "BundleHasStrictIdentifier": True,
                "BundleOverwriteAction": "upgrade",
            },
            {
                "RootRelativeBundlePath": "Library/Services/Обезличить DOCXdodyr.workflow",
                "BundleIsRelocatable": False,
                "BundleIsVersionChecked": False,
                "BundleOverwriteAction": "upgrade",
            },
            {
                "RootRelativeBundlePath": "Library/Services/Восстановить DOCXdodyr.workflow",
                "BundleIsRelocatable": False,
                "BundleIsVersionChecked": False,
                "BundleOverwriteAction": "upgrade",
            },
        ]
        component_plist = stage.parent / f".DOCXdodyr-{arch}-components.plist"
        with open(component_plist, "wb") as f:
            plistlib.dump(components_data, f)

        scripts_dir = stage.parent / f".DOCXdodyr-{arch}-scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        postinstall = scripts_dir / "postinstall"
        postinstall.write_text(
            "#!/bin/sh\n"
            "# Force LaunchServices and Applications folder refresh\n"
            "touch /Applications 2>/dev/null || true\n"
            "if [ -d \"/Applications/DOCXdodyr.app\" ]; then\n"
            "    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f /Applications/DOCXdodyr.app 2>/dev/null || true\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        postinstall.chmod(0o755)

        pkgbuild = [
            "pkgbuild",
            "--root",
            str(stage),
            "--component-plist",
            str(component_plist),
            "--scripts",
            str(scripts_dir),
            "--identifier",
            f"ru.docxdodyr.desktop.quick-actions.{arch}",
            "--version",
            APP_VERSION,
            "--install-location",
            "/",
        ]
        if sign_identity:
            pkgbuild.extend(["--sign", sign_identity])
        pkgbuild.append(str(component_pkg))
        subprocess.run(pkgbuild, check=True)

        productbuild = ["productbuild"]
        if sign_identity:
            productbuild.extend(["--sign", sign_identity])
        productbuild.extend(["--package", str(component_pkg), str(final_pkg)])
        subprocess.run(productbuild, check=True)
        return final_pkg
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if component_pkg.exists():
            component_pkg.unlink()
        if 'component_plist' in locals() and component_plist.exists():
            component_plist.unlink()
        if 'scripts_dir' in locals() and scripts_dir.exists():
            shutil.rmtree(scripts_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package DOCXdodyr app + Quick Actions as PKG")
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--sign-identity", default=None)
    parser.add_argument("--development", action="store_true", help="Mark the unsigned preview artifact")
    args = parser.parse_args(argv)
    print(package_quick_actions_pkg(
        args.app,
        args.output_dir,
        arch=args.arch,
        sign_identity=args.sign_identity,
        development=args.development,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
