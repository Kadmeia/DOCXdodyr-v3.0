#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Сборочный скрипт полного цикла для macOS: компиляция .app, Hardened Runtime подпись, DMG и верификация."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"
APP_NAME = "DOCXdodyr"
# PyInstaller emits this stable intermediate name.  The published bundle is
# renamed per architecture after every Mach-O has been checked; keeping the
# intermediate name stable avoids coupling the .spec file to the release
# target and prevents accidental universal/architecture ambiguity.
APP_BUNDLE_NAME = f"{APP_NAME}.app"
SUPPORTED_ARCHES = ("arm64", "x86_64")
APP_BUNDLE_PATH = DIST_DIR / APP_BUNDLE_NAME
SPEC_FILE = REPO_ROOT / "DOCXdodyr.spec"
sys.path.insert(0, str(REPO_ROOT))
from version import __version__ as VERSION
from scripts.binary_arch import mach_files, verify_macos_arch


def architecture_bundle_name(arch: str) -> str:
    """Return the explicit thin-app name used in release artifacts."""
    if arch not in SUPPORTED_ARCHES:
        raise ValueError(f"Unsupported macOS architecture: {arch}")
    return f"{APP_NAME}-{arch}.app"


def architecture_bundle_path(dist_dir: Path, arch: str) -> Path:
    return Path(dist_dir) / architecture_bundle_name(arch)


def log(msg: str) -> None:
    print(f"\n[BUILD-MACOS] {msg}", flush=True)


def run_command(cmd: list[str], check: bool = True, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Выполняет команду с логированием."""
    safe = list(cmd)
    for flag in ("--password",):
        if flag in safe:
            safe[safe.index(flag) + 1] = "[REDACTED]"
    log_cmd = " ".join(str(x) for x in safe)
    print(f"  > {log_cmd}", flush=True)
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if "--password" in cmd:
        secret = cmd[cmd.index("--password") + 1]
        if secret:
            res.stdout = res.stdout.replace(secret, "[REDACTED]")
            res.stderr = res.stderr.replace(secret, "[REDACTED]")
    if res.stdout.strip():
        for line in res.stdout.strip().splitlines():
            print(f"    {line}")
    if res.stderr.strip():
        for line in res.stderr.strip().splitlines():
            print(f"    ! {line}")
    if check and res.returncode != 0:
        raise RuntimeError(f"Команда завершилась с ошибкой (code {res.returncode}): {log_cmd}\n{res.stderr}")
    return res


def ensure_assets() -> None:
    """Проверяет наличие иконки, Info.plist и entitlements.plist, при необходимости генерирует."""
    log("Проверка необходимых ассетов сборки...")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    icns_path = ASSETS_DIR / "DOCXdodyr.icns"
    if not icns_path.exists():
        log("Генерация иконки DOCXdodyr.icns...")
        gen_script = ASSETS_DIR / "generate_icons.py"
        run_command([sys.executable, str(gen_script)])

    plist_path = ASSETS_DIR / "Info.plist"
    if not plist_path.exists():
        raise FileNotFoundError(f"Отсутствует обязательный манифест: {plist_path}")

    entitlements_path = ASSETS_DIR / "entitlements.plist"
    if not entitlements_path.exists():
        raise FileNotFoundError(f"Отсутствует файл прав: {entitlements_path}")

    # Валидация plist файлов через plutil
    run_command(["plutil", "-lint", str(plist_path)])
    run_command(["plutil", "-lint", str(entitlements_path)])
    log("Все ассеты проверены и валидны.")


def _prepare_output_root(output_root: Path | None) -> tuple[Path, Path]:
    """Return isolated dist/build paths, refusing unsafe or non-fresh roots."""
    if output_root is None:
        return DIST_DIR, BUILD_DIR
    root = Path(output_root).expanduser()
    probe = root
    while probe != probe.parent:
        if probe.is_symlink():
            raise ValueError("Preview output root and its ancestors must not be symlinks")
        probe = probe.parent
    resolved = root.resolve(strict=False)
    protected = (REPO_ROOT.resolve(), DIST_DIR.resolve(), BUILD_DIR.resolve())
    private_root = (REPO_ROOT / ".release-audit").resolve(strict=False)
    inside_private = resolved != private_root and resolved.is_relative_to(private_root)
    if (not inside_private and any(candidate == base or candidate.is_relative_to(base) or base.is_relative_to(candidate)
           for base in protected for candidate in (resolved,))):
        raise ValueError("Preview output root overlaps the repository or legacy dist/build")
    if root.exists():
        if not root.is_dir():
            raise ValueError("Preview output root must be a directory")
        if any(root.iterdir()):
            raise ValueError("Preview output root must be fresh and empty")
    else:
        root.mkdir(parents=True)
    return root / "dist", root / "build"


def build_app(clean: bool = True, arch: str | None = None, output_root: Path | None = None) -> Path:
    """Build one native thin app and return its architecture-specific path."""
    arch = arch or platform.machine()
    if arch not in SUPPORTED_ARCHES or arch != platform.machine():
        raise ValueError("Build requires a native runner matching --arch")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Release builds require Python 3.11")
    dist_dir, build_dir = _prepare_output_root(output_root)
    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    intermediate_app_path = dist_dir / APP_BUNDLE_NAME
    app_bundle_path = architecture_bundle_path(dist_dir, arch)
    # PyInstaller otherwise persists its cache under ~/Library/Application
    # Support/pyinstaller.  That couples a release build to the user's home
    # and can fail in restricted CI/sandbox environments. Keep the cache
    # inside the isolated build root and restore the caller's environment
    # after PyInstaller exits.
    pyinstaller_config_dir = build_dir.with_name(f"{build_dir.name}-pyinstaller-config")
    os.environ["DOCXDODYR_TARGET_ARCH"] = arch
    os.environ["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
    log("Запуск сборки PyInstaller...")
    if clean:
        if output_root is None:
            if BUILD_DIR.exists():
                shutil.rmtree(BUILD_DIR)
            if APP_BUNDLE_PATH.exists():
                shutil.rmtree(APP_BUNDLE_PATH)
            if app_bundle_path.exists():
                shutil.rmtree(app_bundle_path)
            if pyinstaller_config_dir.exists():
                shutil.rmtree(pyinstaller_config_dir)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        *(["--clean"] if clean else []),
        str(SPEC_FILE),
    ]
    pyinstaller_config_dir.mkdir(parents=True, exist_ok=True)
    previous_pyinstaller_config = os.environ.get("PYINSTALLER_CONFIG_DIR")
    os.environ["PYINSTALLER_CONFIG_DIR"] = str(pyinstaller_config_dir)
    try:
        run_command(cmd)
    finally:
        if previous_pyinstaller_config is None:
            os.environ.pop("PYINSTALLER_CONFIG_DIR", None)
        else:
            os.environ["PYINSTALLER_CONFIG_DIR"] = previous_pyinstaller_config

    if not intermediate_app_path.exists():
        raise RuntimeError(f"Сборка не создала промежуточный бандл: {intermediate_app_path}")

    # Проверка структуры бандла
    contents_dir = intermediate_app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    main_exec = macos_dir / APP_NAME

    if not main_exec.exists() or not os.access(main_exec, os.X_OK):
        raise RuntimeError(f"Исполняемый файл приложения повреждён или недоступен: {main_exec}")

    # Гарантируем корректность Info.plist в бандле
    bundle_plist = contents_dir / "Info.plist"
    src_plist = ASSETS_DIR / "Info.plist"
    if src_plist.exists():
        shutil.copy2(src_plist, bundle_plist)

    # Проверяем наличие ресурсов UI
    web_dir_macos = macos_dir / "web"
    web_dir_resources = resources_dir / "web"
    if not web_dir_macos.exists() and not web_dir_resources.exists():
        log("Копирование веб-ресурсов в Contents/Resources/web...")
        shutil.copytree(REPO_ROOT / "web", web_dir_resources, dirs_exist_ok=True)

    # Embed the Quick Action launcher before any signing step. Package
    # wrappers only verify this file; they must not mutate a signed app.
    context_menu_source = REPO_ROOT / "macos_context_menu.py"
    if context_menu_source.exists():
        context_menu_target = resources_dir / context_menu_source.name
        shutil.copy2(context_menu_source, context_menu_target)
        context_menu_target.chmod(0o755)

    # Гарантируем наличие структуры каталогов docx для совместимости с POSIX traversal
    for sub in ("parts", "oxml", "opc", "templates"):
        sub_dir = resources_dir / "docx" / sub
        sub_dir.mkdir(parents=True, exist_ok=True)
        keep_file = sub_dir / ".keep"
        if not keep_file.exists():
            keep_file.touch()

    verify_macos_arch(intermediate_app_path, arch)
    if app_bundle_path.exists():
        shutil.rmtree(app_bundle_path)
    intermediate_app_path.rename(app_bundle_path)
    # Verify after the rename as well: the release path must itself be a thin
    # bundle and must never rely on the filename as an architecture claim.
    verify_macos_arch(app_bundle_path, arch)
    log(f"Бандл {app_bundle_path} успешно собран ({arch}, thin).")
    return app_bundle_path


def get_signing_identity(explicit_identity: str | None = None) -> str:
    """Определяет сертификат подписи кода из параметров или переменных окружения."""
    if explicit_identity:
        return explicit_identity

    env_identity = os.environ.get("APPLE_SIGNING_IDENTITY") or os.environ.get("DEVELOPER_ID_APPLICATION")
    if env_identity:
        return env_identity

    # Проверяем наличие доступных identities через security
    res = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        if "Developer ID Application:" in line:
            parts = line.split('"')
            if len(parts) >= 2:
                found_id = parts[1]
                log(f"Обнаружен системный сертификат Developer ID: {found_id}")
                return found_id

    log("Сертификат Developer ID не обнаружен. Используется локальная ad-hoc подпись ('-') согласно ADR 0006.")
    return "-"


def sign_app(app_path: Path, identity: str | None = None) -> None:
    """Выполняет рекурсивную подпись всех динамических библиотек и бандла приложения с Hardened Runtime."""
    identity = get_signing_identity(identity)
    entitlements_path = ASSETS_DIR / "entitlements.plist"
    is_adhoc = (identity == "-")

    log(f"Начало подписи кода (Identity: '{identity}', Hardened Runtime: да, Entitlements: {entitlements_path.name})...")

    # 1. Поиск всех вложенных исполняемых файлов, dylib, so и фреймворков
    nested = list(mach_files(app_path))
    nested += [p for p in app_path.rglob('*') if p.is_dir() and not p.is_symlink() and p.suffix in ('.framework', '.app', '.xpc', '.bundle')]
    for lib in sorted(nested, key=lambda p: len(p.parts), reverse=True):
        cmd = ["codesign", "-s", identity, "--force"]
        if not is_adhoc:
            cmd += ["--timestamp", "--options", "runtime"]
        cmd.append(str(lib))
        run_command(cmd)

    # 3. Подписываем главный исполняемый файл с entitlements
    main_exec = app_path / "Contents" / "MacOS" / APP_NAME
    main_cmd = [
        "codesign",
        "-s",
        identity,
        "--force",
        "--options",
        "runtime",
        "--entitlements",
        str(entitlements_path),
    ]
    if not is_adhoc:
        main_cmd += ["--timestamp"]
    main_cmd.append(str(main_exec))
    run_command(main_cmd)

    # 4. Подписываем весь бандл целиком
    bundle_cmd = [
        "codesign",
        "-s",
        identity,
        "--force",
        "--options",
        "runtime",
        "--entitlements",
        str(entitlements_path),
    ]
    if not is_adhoc:
        bundle_cmd += ["--timestamp"]
    bundle_cmd.append(str(app_path))
    run_command(bundle_cmd)

    log(f"Подпись бандла {app_path.name} успешно завершена.")


def verify_app(app_path: Path) -> dict[str, str]:
    """Проводит проверку подписи бандла через codesign и Gatekeeper assessment через spctl."""
    log(f"Верификация бандла {app_path.name}...")
    results = {}

    # codesign --verify --deep --strict
    verify_cmd = ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_path)]
    res_cs = run_command(verify_cmd)
    results["codesign"] = "PASS" if res_cs.returncode == 0 else "FAIL"
    log(f"Результат codesign --strict: {results['codesign']}")

    # spctl --assess
    spctl_cmd = ["spctl", "--assess", "--type", "execute", "--verbose", str(app_path)]
    res_sp = run_command(spctl_cmd, check=False)
    # Для ad-hoc spctl вернет rejected (source=Unnotarized Developer ID), что штатно
    if res_sp.returncode == 0:
        results["spctl"] = "ACCEPTED (Notarized Developer ID)"
    else:
        results["spctl"] = f"REJECTED/AD-HOC (Ожидаемо без платного Apple Developer ID: {res_sp.stderr.strip()})"
    log(f"Результат Gatekeeper (spctl): {results['spctl']}")

    return results


def build_dmg(app_path: Path, identity: str | None = None, arch: str = "arm64", production: bool = False, skip_sign: bool = False, dist_dir: Path | None = None) -> Path:
    """Создаёт дистрибутивный DMG с симлинком /Applications и проверяет его целостность."""
    if production and skip_sign:
        raise ValueError('Production DMG cannot skip signing')
    dist_dir = DIST_DIR if dist_dir is None else dist_dir
    arch = verify_macos_arch(app_path, arch)
    label = "" if production else "-development"
    dmg_name = f"{APP_NAME}-{VERSION}-macos-{arch}{label}.dmg"
    dmg_path = dist_dir / dmg_name
    staging_dir = dist_dir / "dmg_staging"

    log(f"Подготовка дистрибутива DMG: {dmg_path.name}...")

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Копируем приложение в staging под каноническим именем установки. Имя
    # архитектуры уже зафиксировано в dist/ и имени DMG; внутри образа нужен
    # обычный DOCXdodyr.app, чтобы drag-and-drop установка и Quick Actions,
    # использующие /Applications/DOCXdodyr.app, работали одинаково для arm64
    # и x86_64.
    staged_app = staging_dir / APP_BUNDLE_NAME
    shutil.copytree(app_path, staged_app, symlinks=True)

    # Создаём символическую ссылку на /Applications для Drag-and-Drop установки
    apps_symlink = staging_dir / "Applications"
    os.symlink("/Applications", apps_symlink)

    # Удаляем старый DMG если был
    if dmg_path.exists():
        dmg_path.unlink()

    # Сборка сжатого UDZO образа через hdiutil
    hdiutil_cmd = [
        "hdiutil",
        "create",
        "-volname",
        APP_NAME,
        "-srcfolder",
        str(staging_dir),
        "-ov",
        "-format",
        "UDZO",
        str(dmg_path),
    ]
    run_command(hdiutil_cmd)

    # Очистка staging
    shutil.rmtree(staging_dir)

    # Подпись DMG образа
    if not skip_sign:
        identity = get_signing_identity(identity)
        sign_cmd = ["codesign", "-s", identity, "--force"]
        if identity != "-":
            sign_cmd += ["--timestamp"]
        sign_cmd.append(str(dmg_path))
        run_command(sign_cmd)

    # Верификация целостности DMG через hdiutil verify
    verify_cmd = ["hdiutil", "verify", str(dmg_path)]
    run_command(verify_cmd)

    size_mb = dmg_path.stat().st_size / (1024 * 1024)
    log(f"DMG дистрибутив успешно создан: {dmg_path} ({size_mb:.2f} МБ)")
    return dmg_path


def notarize_dmg(dmg_path: Path, app_path: Path) -> bool:
    """Отправляет DMG на нотаризацию в Apple Notary Service при наличии учетных данных."""
    apple_id = os.environ.get("APPLE_ID")
    app_pwd = os.environ.get("APPLE_APP_SPECIFIC_PASSWORD")
    team_id = os.environ.get("APPLE_TEAM_ID")

    if not (apple_id and app_pwd and team_id):
        log("Переменные окружения для нотаризации (APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID) не заданы. Нотаризация пропущена (штатно для этапа 06).")
        return False

    log(f"Отправка {dmg_path.name} на нотаризацию в Apple Notary Service...")
    submit_cmd = [
        "xcrun",
        "notarytool",
        "submit",
        str(dmg_path),
        "--apple-id",
        apple_id,
        "--password",
        app_pwd,
        "--team-id",
        team_id,
        "--wait",
    ]
    run_command(submit_cmd)

    log("Прикрепление тикета нотаризации (stapler)...")
    run_command(["xcrun", "stapler", "staple", str(dmg_path)])
    run_command(["xcrun", "stapler", "staple", str(app_path)])
    log("Нотаризация и stapling успешно завершены!")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка macOS .app и DMG для DOCXдодыр")
    parser.add_argument("--skip-build", action="store_true", help="Пропустить шаг компиляции PyInstaller")
    parser.add_argument("--skip-dmg", action="store_true", help="Пропустить сборку DMG")
    parser.add_argument("--skip-sign", action="store_true", help="Пропустить подпись кода")
    parser.add_argument("--identity", type=str, default=None, help="Идентификатор сертификата подписи кода")
    parser.add_argument("--arch", type=str, default=platform.machine(), choices=SUPPORTED_ARCHES, help="Целевая архитектура")
    parser.add_argument("--production", action="store_true", help="Require Developer ID, notarization and Gatekeeper acceptance")
    parser.add_argument("--notarize", action="store_true", help="Explicitly submit to Apple Notary Service")
    parser.add_argument("--output-root", type=Path, default=None, help="Fresh empty root for isolated preview output (contains dist/ and build/)")
    parser.add_argument("--adhoc-preview", action="store_true", help="Explicitly ad-hoc sign an isolated preview after bundle mutations")
    args = parser.parse_args()

    if args.output_root is not None and args.skip_build:
        parser.error("--output-root requires a fresh preview build; do not combine it with --skip-build")
    if args.adhoc_preview and (args.output_root is None or args.skip_sign or args.identity or args.notarize or args.production):
        parser.error("--adhoc-preview requires --output-root and cannot combine with --skip-sign, --identity, --notarize or --production")

    if args.production:
        if args.skip_sign or args.skip_dmg:
            parser.error("Production cannot skip signing or DMG")
        identity = get_signing_identity(args.identity)
        if not identity.startswith("Developer ID Application:"):
            parser.error("Production requires Developer ID Application")
        if not all(os.environ.get(key) for key in ("APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID")):
            parser.error("Production requires notarization credentials")
        args.identity = identity
    ensure_assets()
    output_root = args.output_root
    preview_dist = (Path(output_root).expanduser() / "dist") if output_root is not None else DIST_DIR

    if not args.skip_build:
        app_path = build_app(clean=True, arch=args.arch, output_root=output_root)
    else:
        app_path = architecture_bundle_path(preview_dist, args.arch)

    if args.adhoc_preview:
        sign_app(app_path, identity="-")
    elif not args.skip_sign:
        sign_app(app_path, identity=args.identity)

    verify_macos_arch(app_path, args.arch)
    verify_app(app_path)

    if args.production:
        from scripts.audit_macos_deployment import audit
        if not audit(app_path)["passed"]:
            raise RuntimeError("Bundled binaries require macOS newer than 14")
        # Staple the app before placing it inside the final DMG/ZIP.
        submission = preview_dist / "notary-submission.zip"
        run_command(["ditto", "-c", "-k", "--keepParent", str(app_path), str(submission)])
        run_command(["xcrun", "notarytool", "submit", str(submission), "--apple-id", os.environ["APPLE_ID"], "--password", os.environ["APPLE_APP_SPECIFIC_PASSWORD"], "--team-id", os.environ["APPLE_TEAM_ID"], "--wait"])
        run_command(["xcrun", "stapler", "staple", str(app_path)])
        run_command(["xcrun", "stapler", "validate", str(app_path)])
        run_command(["spctl", "--assess", "--type", "execute", str(app_path)])
        submission.unlink()
    if not args.skip_dmg:
        dmg_path = build_dmg(app_path, identity=args.identity, arch=args.arch, production=args.production, skip_sign=(args.skip_sign or args.adhoc_preview), dist_dir=preview_dist)
        if args.notarize or args.production:
            if not notarize_dmg(dmg_path, app_path):
                raise RuntimeError("Notarization credentials are required")
            run_command(["xcrun", "stapler", "validate", str(dmg_path)])


if __name__ == "__main__":
    main()
