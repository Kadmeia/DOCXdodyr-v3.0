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

        # Подготовка ресурсов установщика (EULA, Приветствие, Завершение)
        resources_dir = stage.parent / f".DOCXdodyr-{arch}-resources"
        resources_dir.mkdir(parents=True, exist_ok=True)
        eula_path = REPO_ROOT / "EULA.txt"
        eula_content = eula_path.read_text(encoding="utf-8") if eula_path.exists() else "DOCXdodyr EULA"
        (resources_dir / "license.txt").write_text(eula_content, encoding="utf-8")
        (resources_dir / "welcome.html").write_text(
            f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 13px; line-height: 1.5; color: #1d1d1f; padding: 10px; }}
  h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 8px; color: #000; }}
  p {{ margin: 8px 0; }}
  ul {{ margin: 8px 0 12px 18px; }}
  li {{ margin-bottom: 4px; }}
</style>
</head>
<body>
  <h1>Добро пожаловать в установщик DOCXдодыр v{APP_VERSION}</h1>
  <p>Мастер установит <b>DOCXдодыр</b> в папку «Программы» (<code>/Applications</code>) и настроит системные быстрые действия Finder для мгновенного обезличивания файлов.</p>
  <ul>
    <li><b>Локальная обработка (Local-First)</b>: Ваши документы не передаются на сторонние серверы.</li>
    <li><b>Поддержка форматов</b>: DOCX, XLSX, PDF (включая сканы Apple Vision / Indra OCR).</li>
    <li><b>Восстановление</b>: быстрое демаскирование по зашифрованному дешифратору.</li>
  </ul>
  <p>Нажмите <b>«Продолжить»</b>, чтобы перейти к Лицензионному соглашению.</p>
</body>
</html>""",
            encoding="utf-8",
        )
        (resources_dir / "conclusion.html").write_text(
            f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 13px; line-height: 1.5; color: #1d1d1f; padding: 10px; }}
  h1 {{ font-size: 18px; font-weight: 600; margin-bottom: 8px; color: #1e7e34; }}
  p {{ margin: 8px 0; }}
  .box {{ background: #f5f5f7; border-left: 4px solid #0071e3; padding: 10px; border-radius: 4px; margin: 12px 0; }}
</style>
</head>
<body>
  <h1>Установка успешно завершена</h1>
  <p>Программа <b>DOCXдодыр v{APP_VERSION}</b> установлена и готова к работе.</p>
  <div class="box">
    <b>Как запустить:</b><br>
    - Запустите приложение из Launchpad или папки «Программы» (<code>/Applications/DOCXdodyr.app</code>).<br>
    - Для быстрой обработки кликните правой кнопкой мыши по файлу в Finder: <i>Быстрые действия → «Обезличить в DOCXдодыр»</i>.
  </div>
  <p>Нажмите <b>«Закрыть»</b> для завершения работы мастера установки.</p>
</body>
</html>""",
            encoding="utf-8",
        )

        dist_xml = stage.parent / f".DOCXdodyr-{arch}-distribution.xml"
        dist_content = f"""<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>DOCXдодыр {APP_VERSION}</title>
    <options customize="never" require-scripts="false" hostArchitectures="{arch}"/>
    <domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
    <welcome file="welcome.html" mime-type="text/html"/>
    <license file="license.txt" mime-type="text/plain"/>
    <conclusion file="conclusion.html" mime-type="text/html"/>
    <pkg-ref id="ru.docxdodyr.desktop.quick-actions.{arch}"/>
    <choices-outline>
        <line choice="default">
            <line choice="ru.docxdodyr.desktop.quick-actions.{arch}"/>
        </line>
    </choices-outline>
    <choice id="default"/>
    <choice id="ru.docxdodyr.desktop.quick-actions.{arch}" visible="false">
        <pkg-ref id="ru.docxdodyr.desktop.quick-actions.{arch}"/>
    </choice>
    <pkg-ref id="ru.docxdodyr.desktop.quick-actions.{arch}" version="{APP_VERSION}" onConclusion="none">{component_pkg.name}</pkg-ref>
</installer-gui-script>"""
        dist_xml.write_text(dist_content, encoding="utf-8")

        productbuild = ["productbuild"]
        if sign_identity:
            productbuild.extend(["--sign", sign_identity])
        productbuild.extend([
            "--distribution", str(dist_xml),
            "--package-path", str(stage.parent),
            "--resources", str(resources_dir),
            str(final_pkg),
        ])
        subprocess.run(productbuild, check=True)

        # Канонические ссылки инсталлятора
        canonical_target = output / f"DOCXdodyr-{APP_VERSION}-macos-{arch}{label}.pkg"
        if canonical_target != final_pkg:
            if canonical_target.exists() or canonical_target.is_symlink():
                canonical_target.unlink()
            try:
                os.symlink(final_pkg.name, canonical_target)
            except OSError:
                shutil.copyfile(final_pkg, canonical_target)
        if development:
            symlink_name = output / f"DOCXdodyr-{APP_VERSION}-macos-{arch}.pkg"
            if symlink_name.exists() or symlink_name.is_symlink():
                symlink_name.unlink()
            try:
                os.symlink(canonical_target.name, symlink_name)
            except OSError:
                pass

        return final_pkg
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if 'resources_dir' in locals() and resources_dir.exists():
            shutil.rmtree(resources_dir, ignore_errors=True)
        if 'dist_xml' in locals() and dist_xml.exists():
            dist_xml.unlink()
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
