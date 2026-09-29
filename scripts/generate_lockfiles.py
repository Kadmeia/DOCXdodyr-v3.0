# -*- coding: utf-8 -*-
"""Скрипт генерации воспроизводимых lock-файлов с SHA-256 хэшами для релиза DOCXдодыр."""

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_DIR = ROOT / "requirements"

CORE_PACKAGES = [
    ("python-docx", "1.2.0"),
    ("openpyxl", "3.1.5"),
    ("pywebview", "6.2.1"),
    ("pymorphy3", "2.0.6"),
    ("PullentiPython", "0.1"),
    ("pypdf", "6.12.2"),
    ("cryptography", "50.0.0"),
    ("keyring", "25.7.0"),
    ("platformdirs", "4.4.0"),
    ("pillow", "11.3.0"),
    ("pymupdf", "1.26.5"),
    ("phonenumbers", "9.0.31"),
    ("fpdf2", "2.8.4"),
]

MACOS_PACKAGES = [
    ("pyobjc-core", "11.1"),
    ("pyobjc-framework-Cocoa", "11.1"),
    ("pyobjc-framework-Quartz", "11.1"),
    ("pyobjc-framework-Vision", "11.1"),
    ("pytesseract", "0.3.13"),
]

WINDOWS_PACKAGES = [
    ("docx2pdf", "0.1.8"),
]


def fetch_hashes(package: str, version: str) -> list[str]:
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "DOCXdodyr-Release-Builder"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            urls = data.get("urls", [])
            hashes = sorted({f"sha256:{u['digests']['sha256']}" for u in urls if "digests" in u and "sha256" in u["digests"]})
            return hashes
    except Exception as exc:
        print(f"Error fetching {package}=={version}: {exc}")
        return []


def generate_lock_content(package_list: list[tuple[str, str]], title: str) -> str:
    lines = [
        f"# DOCXдодыр — Воспроизводимый Lock-файл ({title})",
        "# Сгенерировано автоматически с официальными SHA-256 хэшами PyPI",
        "# Для проверки целостности при сборке пакета: pip install --require-hashes -r <file>",
        "",
    ]
    for pkg, ver in package_list:
        print(f"Fetching hashes for {pkg}=={ver}...")
        hashes = fetch_hashes(pkg, ver)
        if not hashes:
            lines.append(f"{pkg}=={ver}")
        else:
            lines.append(f"{pkg}=={ver} \\")
            for i, h in enumerate(hashes):
                sep = " \\" if i < len(hashes) - 1 else ""
                lines.append(f"    --hash={h}{sep}")
        lines.append("")
    return "\n".join(lines)


def main():
    REQ_DIR.mkdir(parents=True, exist_ok=True)

    print("=== Генерация lock-core.txt ===")
    core_content = generate_lock_content(CORE_PACKAGES, "Core — Базовое ядро")
    (REQ_DIR / "lock-core.txt").write_text(core_content, encoding="utf-8")
    print("Сохранён:", REQ_DIR / "lock-core.txt")

    print("=== Генерация lock-macos.txt ===")
    macos_content = generate_lock_content(CORE_PACKAGES + MACOS_PACKAGES, "macOS — Apple Silicon & Intel")
    (REQ_DIR / "lock-macos.txt").write_text(macos_content, encoding="utf-8")
    print("Сохранён:", REQ_DIR / "lock-macos.txt")

    print("=== Генерация lock-windows.txt ===")
    windows_content = generate_lock_content(CORE_PACKAGES + WINDOWS_PACKAGES, "Windows — x64")
    (REQ_DIR / "lock-windows.txt").write_text(windows_content, encoding="utf-8")
    print("Сохранён:", REQ_DIR / "lock-windows.txt")


if __name__ == "__main__":
    main()
