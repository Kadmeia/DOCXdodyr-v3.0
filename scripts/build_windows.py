#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Сборочный скрипт полного цикла для Windows: компиляция onedir .exe, подпись Authenticode, Inno Setup инсталлятор."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
INSTALLER_DIR = REPO_ROOT / "installer"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"
APP_NAME = "DOCXdodyr"
APP_DIR = DIST_DIR / APP_NAME
MAIN_EXE = APP_DIR / f"{APP_NAME}.exe"
SPEC_FILE = REPO_ROOT / "DOCXdodyr.spec"
ISS_FILE = INSTALLER_DIR / "DOCXdodyr.iss"
sys.path.insert(0, str(REPO_ROOT))
from version import __version__ as VERSION
from scripts.binary_arch import verify_windows_pe


def log(msg: str) -> None:
    print(f"\n[BUILD-WINDOWS] {msg}", flush=True)


def run_command(cmd: list[str], check: bool = True, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Выполняет команду с подробным логированием."""
    safe = list(cmd)
    if "/p" in safe:
        safe[safe.index("/p") + 1] = "[REDACTED]"
    log_cmd = " ".join(str(x) for x in safe)
    print(f"  > {log_cmd}", flush=True)
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if "/p" in cmd:
        secret = cmd[cmd.index("/p") + 1]
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


def compute_sha256(file_path: Path) -> str:
    """Вычисляет хэш SHA-256 указанного файла."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def ensure_assets() -> None:
    """Проверяет наличие иконки, манифеста и юридических файлов."""
    log("Проверка необходимых ассетов сборки для Windows...")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    ico_path = ASSETS_DIR / "DOCXdodyr.ico"
    if not ico_path.exists():
        log("Генерация мультиразмерной иконки DOCXdodyr.ico...")
        gen_script = ASSETS_DIR / "generate_icons.py"
        run_command([sys.executable, str(gen_script)])

    manifest_path = ASSETS_DIR / "DOCXdodyr.manifest"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Отсутствует обязательный манифест Windows: {manifest_path}")

    # Проверка юридических файлов
    for req_file in ["EULA.txt", "PRIVACY_POLICY.md", "THIRD_PARTY_NOTICES.md"]:
        p = REPO_ROOT / req_file
        if not p.exists():
            raise FileNotFoundError(f"Отсутствует обязательный юридический документ: {p}")

    # Проверка скрипта установщика
    if not ISS_FILE.exists():
        raise FileNotFoundError(f"Отсутствует конфигурация Inno Setup: {ISS_FILE}")

    log("Все сборочные ассеты для Windows проверены и готовы.")


def build_app(clean: bool = True) -> Path:
    """Запускает PyInstaller для создания onedir дистрибутива Windows."""
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11):
        raise RuntimeError("Build requires native Windows and Python 3.11")
    log("Запуск компиляции PyInstaller для Windows x64...")
    if clean:
        if BUILD_DIR.exists():
            shutil.rmtree(BUILD_DIR)
        if APP_DIR.exists():
            shutil.rmtree(APP_DIR)
    (BUILD_DIR / "DOCXdodyr").mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(SPEC_FILE),
    ]
    run_command(cmd)

    if not MAIN_EXE.exists():
        raise RuntimeError(f"Сборка не создала исполняемый файл приложения: {MAIN_EXE}")

    # Проверяем наличие веб-ресурсов
    web_dir_dist = APP_DIR / "web"
    if not web_dir_dist.exists():
        log("Копирование веб-ресурсов в дистрибутив...")
        shutil.copytree(REPO_ROOT / "web", web_dir_dist, dirs_exist_ok=True)

    log(f"Дистрибутив приложения успешно сформирован в {APP_DIR}")
    return APP_DIR


def find_signtool() -> Path | None:
    """Ищет утилиту signtool.exe в системе (Windows SDK)."""
    # 1. Проверка PATH
    found = shutil.which("signtool.exe") or shutil.which("signtool")
    if found:
        return Path(found)

    # 2. Стандартные каталоги Windows SDK
    sdk_roots = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Windows Kits" / "10" / "bin",
        Path("C:/Program Files (x86)/Windows Kits/10/bin"),
        Path("C:/Program Files/Windows Kits/10/bin"),
    ]
    for root in sdk_roots:
        if root.exists():
            for ver_dir in sorted(root.glob("10.*"), reverse=True):
                candidate = ver_dir / "x64" / "signtool.exe"
                if candidate.exists():
                    return candidate

    return None


def sign_binaries(app_dir: Path, cert_path: str | None = None, cert_password: str | None = None) -> bool:
    """Выполняет цифровую подпись Authenticode с RFC 3161 timestamping согласно ADR 0006."""
    cert_path = cert_path or os.environ.get("WINDOWS_CODESIGN_CERT")
    cert_password = cert_password or os.environ.get("WINDOWS_CODESIGN_PASSWORD")
    timestamp_url = os.environ.get("RFC3161_TIMESTAMP_URL", "http://timestamp.digicert.com")

    if not cert_path:
        log("Сертификат Authenticode не задан. Приложение подготовлено в ad-hoc/unsigned режиме согласно ADR 0006.")
        return False

    signtool = find_signtool()
    if not signtool:
        log("ВНИМАНИЕ: signtool.exe не найден в системе. Пропуск шага цифровой подписи.")
        return False

    log(f"Подпись исполняемых файлов Authenticode через {signtool.name}...")
    target_files = [MAIN_EXE]
    for root, _, files in os.walk(app_dir):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in [".dll", ".pyd"] and p != MAIN_EXE:
                target_files.append(p)

    for target in target_files:
        sign_cmd = [
            str(signtool),
            "sign",
            "/f", str(cert_path),
            "/fd", "SHA256",
            "/tr", timestamp_url,
            "/td", "SHA256",
        ]
        if cert_password:
            sign_cmd.extend(["/p", cert_password])
        sign_cmd.append(str(target))
        run_command(sign_cmd)
        run_command([str(signtool), "verify", "/pa", "/all", "/tw", str(target)])

    log("Цифровая подпись компонентов Authenticode успешно завершена.")
    return True


def find_iscc() -> Path | None:
    """Ищет компилятор Inno Setup (ISCC.exe)."""
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return Path(found)

    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def build_installer(production: bool = False) -> Path | None:
    """Компилирует установочный пакет Inno Setup в dist/DOCXdodyr-Setup-3.0.0-win64.exe."""
    log("Компиляция инсталлятора Windows через Inno Setup...")
    iscc = find_iscc()
    if not iscc:
        log("Компилятор Inno Setup (ISCC.exe) не обнаружен в локальной системе.")
        log("Скрипт installer/DOCXdodyr.iss полностью подготовлен и готов для сборки в CI/CD или после установки Inno Setup.")
        return None

    label = "" if production else "-development"
    base_name = f"DOCXdodyr-{VERSION}-windows-x64{label}"
    cmd = [str(iscc), f"/DMyAppVersion={VERSION}", f"/F{base_name}", str(ISS_FILE)]
    run_command(cmd)

    installer_exe = DIST_DIR / f"{base_name}.exe"
    if not installer_exe.exists():
        raise RuntimeError(f"Инсталлятор не был создан: {installer_exe}")

    size_mb = installer_exe.stat().st_size / (1024 * 1024)
    sha256 = compute_sha256(installer_exe)
    log(f"Инсталлятор успешно создан: {installer_exe.name} ({size_mb:.2f} МБ)")
    log(f"SHA-256 инсталлятора: {sha256}")
    return installer_exe


def verify_build() -> dict[str, str]:
    """Проводит верификацию сформированных артефактов Windows."""
    log("Верификация собранного дистрибутива Windows...")
    results = {}

    if MAIN_EXE.exists():
        verify_windows_pe(MAIN_EXE, gui=True)
        results["executable"] = "PASS"
        results["executable_size"] = f"{MAIN_EXE.stat().st_size} bytes"
    else:
        results["executable"] = "FAIL"

    web_index = APP_DIR / "web" / "index.html"
    if web_index.exists():
        results["web_resources"] = "PASS"
    else:
        results["web_resources"] = "FAIL"

    iss_exists = ISS_FILE.exists()
    results["installer_script"] = "PASS" if iss_exists else "FAIL"

    ico_exists = (ASSETS_DIR / "DOCXdodyr.ico").exists()
    results["windows_icon"] = "PASS" if ico_exists else "FAIL"

    manifest_exists = (ASSETS_DIR / "DOCXdodyr.manifest").exists()
    results["manifest"] = "PASS" if manifest_exists else "FAIL"

    for k, v in results.items():
        log(f"Критерий {k}: {v}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Сборка Windows приложения и инсталлятора для DOCXдодыр")
    parser.add_argument("--skip-build", action="store_true", help="Пропустить компиляцию PyInstaller")
    parser.add_argument("--skip-installer", action="store_true", help="Пропустить компиляцию Inno Setup")
    parser.add_argument("--skip-sign", action="store_true", help="Пропустить шаг подписи Authenticode")
    parser.add_argument("--cert", type=str, default=None, help="Путь к файлу сертификата .pfx/.p12")
    parser.add_argument("--password", type=str, default=None, help="Пароль сертификата")
    parser.add_argument("--production", action="store_true", help="Require Authenticode and timestamp on app and installer")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11):
        parser.error("Build requires native Windows and Python 3.11")
    if args.production and (args.skip_sign or args.skip_installer or not (args.cert or os.environ.get("WINDOWS_CODESIGN_CERT")) or not find_signtool()):
        parser.error("Production requires certificate, signtool and installer")

    ensure_assets()

    if not args.skip_build:
        build_app(clean=True)

    if not args.skip_sign:
        signed = sign_binaries(APP_DIR, cert_path=args.cert, cert_password=args.password)
        if args.production and not signed:
            raise RuntimeError("Production signing failed")

    result = verify_build()
    if "FAIL" in result.values():
        raise RuntimeError("Windows build verification failed")

    if not args.skip_installer:
        installer = build_installer(production=args.production)
        if installer is None:
            raise RuntimeError("Installer is required; install Inno Setup")
        if args.production:
            signtool = find_signtool()
            cmd = [str(signtool), "sign", "/f", args.cert or os.environ["WINDOWS_CODESIGN_CERT"], "/fd", "SHA256", "/tr", os.environ.get("RFC3161_TIMESTAMP_URL", "http://timestamp.digicert.com"), "/td", "SHA256"]
            password = args.password or os.environ.get("WINDOWS_CODESIGN_PASSWORD")
            if password:
                cmd += ["/p", password]
            run_command(cmd + [str(installer)])
            run_command([str(signtool), "verify", "/pa", "/all", "/tw", str(installer)])
    label = "" if args.production else "-development"
    shutil.make_archive(str(DIST_DIR / f"DOCXdodyr-{VERSION}-windows-x64{label}"), "zip", DIST_DIR, APP_NAME)


if __name__ == "__main__":
    main()
