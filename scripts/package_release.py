#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Скрипт подготовки, валидации и упаковки релизных артефактов DOCXдодыр.

Автоматизирует:
1. Проверку синхронизации версий (version.py, Info.plist, DOCXdodyr.iss, DOCXdodyr.spec, sbom.json).
2. Проверку целостности юридических документов (EULA, PRIVACY_POLICY, THIRD_PARTY_NOTICES, SBOM).
3. Упаковку релизных дистрибутивов (DMG, ZIP, Setup.exe).
4. Вычисление и верификацию контрольных сумм SHA-256 (dist/SHA256SUMS.txt).
5. Валидацию релизной документации и регламента отката (RELEASE_NOTES, ROLLBACK_PROCEDURE).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DIST_DIR = REPO_ROOT / "dist"
ASSETS_DIR = REPO_ROOT / "assets"
INSTALLER_DIR = REPO_ROOT / "installer"
DOCS_DIR = REPO_ROOT / "docs"
MACOS_ARCHES = ("arm64", "x86_64")


def compute_sha256(file_path: Path) -> str:
    """Вычисляет хэш SHA-256 указанного файла."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_canonical_version() -> str:
    """Получает каноническую версию приложения из version.py."""
    import version
    return version.__version__


def check_version_consistency(target_version: Optional[str] = None) -> Dict[str, Any]:
    """Проверяет синхронизацию версий по всем манифестам и конфигурациям проекта."""
    expected_version = target_version or get_canonical_version()
    results: Dict[str, Any] = {
        "expected_version": expected_version,
        "files_checked": {},
        "all_matched": True,
        "mismatches": [],
    }

    # 1. version.py
    import version
    v_py = version.__version__
    results["files_checked"]["version.py"] = v_py
    if v_py != expected_version:
        results["all_matched"] = False
        results["mismatches"].append(f"version.py has {v_py}, expected {expected_version}")

    # 2. assets/Info.plist (macOS)
    plist_path = ASSETS_DIR / "Info.plist"
    if plist_path.exists():
        with open(plist_path, "rb") as f:
            plist_data = plistlib.load(f)
        short_ver = plist_data.get("CFBundleShortVersionString")
        bundle_ver = plist_data.get("CFBundleVersion")
        results["files_checked"]["assets/Info.plist"] = {
            "CFBundleShortVersionString": short_ver,
            "CFBundleVersion": bundle_ver,
        }
        if short_ver != expected_version or bundle_ver != expected_version:
            results["all_matched"] = False
            results["mismatches"].append(
                f"assets/Info.plist has {short_ver}/{bundle_ver}, expected {expected_version}"
            )

    # 3. installer/DOCXdodyr.iss (Windows)
    iss_path = INSTALLER_DIR / "DOCXdodyr.iss"
    if iss_path.exists():
        iss_text = iss_path.read_text(encoding="utf-8")
        match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', iss_text)
        iss_ver = match.group(1) if match else None
        results["files_checked"]["installer/DOCXdodyr.iss"] = iss_ver
        if iss_ver != expected_version:
            results["all_matched"] = False
            results["mismatches"].append(
                f"installer/DOCXdodyr.iss has {iss_ver}, expected {expected_version}"
            )

    # 4. sbom.json (Software Bill of Materials)
    sbom_path = REPO_ROOT / "sbom.json"
    if sbom_path.exists():
        with open(sbom_path, "r", encoding="utf-8") as f:
            sbom_data = json.load(f)
        sbom_comp_ver = sbom_data.get("metadata", {}).get("component", {}).get("version")
        results["files_checked"]["sbom.json"] = sbom_comp_ver
        if sbom_comp_ver != expected_version:
            results["all_matched"] = False
            results["mismatches"].append(
                f"sbom.json has {sbom_comp_ver}, expected {expected_version}"
            )

    # 5. DOCXdodyr.spec
    spec_path = REPO_ROOT / "DOCXdodyr.spec"
    if spec_path.exists():
        spec_text = spec_path.read_text(encoding="utf-8")
        match = re.search(r"version=['\"]([^'\"]+)['\"]", spec_text)
        spec_ver = match.group(1) if match else (get_canonical_version() if "version=APP_VERSION" in spec_text and "from version import __version__ as APP_VERSION" in spec_text else None)
        results["files_checked"]["DOCXdodyr.spec"] = spec_ver
        if spec_ver != expected_version:
            results["all_matched"] = False
            results["mismatches"].append(
                f"DOCXdodyr.spec has {spec_ver}, expected {expected_version}"
            )

    return results


def check_legal_documents() -> Dict[str, Any]:
    """Проверяет наличие и структуру обязательных правовых документов."""
    required_docs = [
        "EULA.txt",
        "EULA.md",
        "PRIVACY_POLICY.md",
        "THIRD_PARTY_NOTICES.md",
        "sbom.json",
        "SECURITY.md",
        "DISCLAIMER.md",
        "NOTICE.txt",
    ]
    results: Dict[str, Any] = {
        "documents": {},
        "all_present": True,
        "missing": [],
    }

    for doc_name in required_docs:
        p = REPO_ROOT / doc_name
        exists = p.exists()
        results["documents"][doc_name] = {
            "exists": exists,
            "size_bytes": p.stat().st_size if exists else 0,
        }
        if not exists:
            results["all_present"] = False
            results["missing"].append(doc_name)

    # Валидация CycloneDX SBOM
    sbom_path = REPO_ROOT / "sbom.json"
    if sbom_path.exists():
        try:
            with open(sbom_path, "r", encoding="utf-8") as f:
                sbom = json.load(f)
            components = sbom.get("components", [])
            results["sbom_valid"] = True
            results["sbom_components_count"] = len(components)
        except Exception as e:
            results["sbom_valid"] = False
            results["sbom_error"] = str(e)

    return results


def check_release_documentation() -> Dict[str, Any]:
    """Проверяет наличие Release Notes и регламента отката."""
    v_py = get_canonical_version()
    notes_candidate = REPO_ROOT / "docs" / f"RELEASE_NOTES_v{v_py}.md"
    if not notes_candidate.exists():
        short_ver = ".".join(v_py.split(".")[:2])
        short_candidate = REPO_ROOT / "docs" / f"RELEASE_NOTES_v{short_ver}.md"
        if short_candidate.exists():
            notes_candidate = short_candidate

    required_docs = {
        str(notes_candidate.relative_to(REPO_ROOT)): notes_candidate,
        "RELEASE_NOTES.md": REPO_ROOT / "RELEASE_NOTES.md",
        "docs/ROLLBACK_PROCEDURE.md": REPO_ROOT / "docs" / "ROLLBACK_PROCEDURE.md",
    }
    results: Dict[str, Any] = {
        "docs": {},
        "all_present": True,
        "missing": [],
    }

    for name, p in required_docs.items():
        exists = p.exists()
        results["docs"][name] = {
            "exists": exists,
            "size_bytes": p.stat().st_size if exists else 0,
        }
        if not exists:
            results["all_present"] = False
            results["missing"].append(name)

    return results


def find_dist_artifacts(dist_dir: Path = DIST_DIR) -> List[Path]:
    """Находит все дистрибутивные файлы (DMG, EXE, ZIP) в каталоге dist."""
    if not dist_dir.exists():
        return []
    artifacts: List[Path] = []
    for item in sorted(dist_dir.iterdir()):
        if item.is_file():
            ext = item.suffix.lower()
            if ext in [".dmg", ".exe", ".zip"] and not item.name.startswith("."):
                artifacts.append(item)
    return artifacts


def _macos_app_path(dist_dir: Path, arch: Optional[str]) -> tuple[Path, str]:
    """Resolve one explicitly thin, architecture-named app bundle.

    A legacy ``DOCXdodyr.app`` is intentionally not accepted: packaging must
    never infer a release architecture from an ambiguous universal/old path.
    When ``arch`` is omitted, exactly one architecture-named bundle must be
    present so local packaging remains convenient without weakening the gate.
    """
    dist_dir = Path(dist_dir)
    if arch is not None and arch not in MACOS_ARCHES:
        raise ValueError(f"Unsupported macOS architecture: {arch}")
    candidates = {
        candidate_arch: dist_dir / f"DOCXdodyr-{candidate_arch}.app"
        for candidate_arch in MACOS_ARCHES
        if (dist_dir / f"DOCXdodyr-{candidate_arch}.app").is_dir()
    }
    if arch is None:
        if len(candidates) != 1:
            raise ValueError("Expected exactly one architecture-specific DOCXdodyr app bundle")
        arch, app_path = next(iter(candidates.items()))
    else:
        app_path = candidates.get(arch, dist_dir / f"DOCXdodyr-{arch}.app")
    if not app_path.is_dir():
        raise FileNotFoundError(f"Missing architecture-specific app bundle: {app_path.name}")
    return app_path, arch


def package_macos_zip(
    dist_dir: Path = DIST_DIR,
    version: Optional[str] = None,
    production: bool = False,
    arch: Optional[str] = None,
) -> Optional[Path]:
    """Package exactly one architecture-named thin macOS app as a zip."""
    ver = version or get_canonical_version()
    app_path, arch = _macos_app_path(dist_dir, arch)

    from scripts.binary_arch import verify_macos_arch
    actual_arch = verify_macos_arch(app_path, arch)
    if actual_arch != arch:
        raise ValueError(f"App bundle architecture mismatch: expected {arch}, got {actual_arch}")
    if production:
        import subprocess
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app_path)], check=True)
        subprocess.run(["spctl", "--assess", "--type", "execute", str(app_path)], check=True)
        subprocess.run(["xcrun", "stapler", "validate", str(app_path)], check=True)
    label = "" if production else "-development"
    zip_filename = f"DOCXdodyr-{ver}-macos-{arch}{label}.zip"
    zip_path = dist_dir / zip_filename

    # Создаем zip с сохранением структуры бандла
    import subprocess
    cmd = ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_path), str(zip_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError("ditto failed; refusing an archive with damaged bundle symlinks")

    return zip_path if zip_path.exists() else None


def generate_sha256sums(dist_dir: Path = DIST_DIR, output_file: Optional[Path] = None) -> Path:
    """Генерирует официальный файл контрольных сумм SHA256SUMS.txt для всех артефактов в dist."""
    out_path = output_file or (dist_dir / "SHA256SUMS.txt")
    artifacts = find_dist_artifacts(dist_dir)
    # Include release metadata in the published inventory, excluding the
    # checksum file itself (and never hash the inventory recursively).
    artifacts = [p for p in sorted(dist_dir.iterdir()) if p.is_file() and p.name != out_path.name and not p.name.startswith('.')]
    if any(p.is_symlink() for p in artifacts):
        raise ValueError('Release inventory cannot contain symlinks')

    lines: List[str] = []
    for artifact in artifacts:
        # Не включаем сам файл контрольных сумм
        if artifact.name == out_path.name:
            continue
        sha = compute_sha256(artifact)
        lines.append(f"{sha}  {artifact.name}\n")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


    return out_path


def verify_sha256sums(sums_file: Path, dist_dir: Path = DIST_DIR) -> Dict[str, Any]:
    """Проверяет целостность артефактов по файлу SHA256SUMS.txt."""
    if not sums_file.exists():
        return {"valid": False, "error": f"Файл {sums_file} не найден"}

    results: Dict[str, Any] = {
        "valid": True,
        "verified_files": {},
        "mismatches": [],
        "missing_files": [],
    }

    with open(sums_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                results["valid"] = False
                continue
            expected_hash, filename = parts[0], parts[1].lstrip("* ")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) or Path(filename).name != filename or "\\" in filename or filename in results["verified_files"]:
                results["valid"] = False
                continue
            file_path = dist_dir / filename
            if file_path.is_symlink():
                results["valid"] = False
                continue
            if not file_path.exists():
                results["valid"] = False
                results["missing_files"].append(filename)
                results["verified_files"][filename] = {"status": "missing"}
                continue

            actual_hash = compute_sha256(file_path)
            matched = (actual_hash.lower() == expected_hash.lower())
            results["verified_files"][filename] = {
                "status": "ok" if matched else "hash_mismatch",
                "expected": expected_hash,
                "actual": actual_hash,
            }
            if not matched:
                results["valid"] = False
                results["mismatches"].append(filename)

    inventory = {p.name for p in dist_dir.iterdir() if p.is_file() and p.name != sums_file.name and not p.name.startswith('.')}
    if not results["verified_files"] or set(results["verified_files"]) != inventory:
        results["valid"] = False
    return results


def run_full_release_candidate_audit(dist_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Выполняет полную комплексную проверку готовности Release Candidate."""
    dist_dir = DIST_DIR if dist_dir is None else dist_dir
    ver = get_canonical_version()
    version_check = check_version_consistency(ver)
    legal_check = check_legal_documents()
    docs_check = check_release_documentation()
    artifacts = find_dist_artifacts(dist_dir)
    sums_file = dist_dir / "SHA256SUMS.txt"
    sums_verification = verify_sha256sums(sums_file, dist_dir) if sums_file.exists() else None

    ready = (
        version_check["all_matched"]
        and legal_check["all_present"]
        and docs_check["all_present"]
        and len(artifacts) > 0
        and (sums_verification is not None and sums_verification["valid"])
    )

    production_blockers = []
    try:
        from scripts.release_gate import verify
        import subprocess
        commit = os.environ.get("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        verify(dist_dir, commit)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        production_blockers.append("Missing or invalid signed/clean-machine evidence for all targets")
    return {
        "version": ver,
        "ready_for_release": ready and not production_blockers,
        "candidate_metadata_valid": ready,
        "production_blockers": production_blockers,
        "version_check": version_check,
        "legal_check": legal_check,
        "docs_check": docs_check,
        "artifacts": [a.name for a in artifacts],
        "sums_verification": sums_verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовка и проверка Release Candidate DOCXдодыр.")
    parser.add_argument("--verify", action="store_true", help="Проверить готовность артефактов без изменений")
    parser.add_argument("--package", action="store_true", help="Упаковать zip-архивы и обновить SHA256SUMS.txt")
    parser.add_argument("--checksums", action="store_true", help="Сгенерировать только файл SHA256SUMS.txt")
    parser.add_argument("--json", action="store_true", help="Вывести отчет в формате JSON")
    parser.add_argument("--development", action="store_true", help="Validate development metadata without asserting production readiness")
    parser.add_argument("--production", action="store_true", help="Require production signing/stapling checks while packaging")
    parser.add_argument("--arch", choices=MACOS_ARCHES, help="Architecture-specific macOS bundle to package")
    parser.add_argument("--dist-dir", type=Path, default=DIST_DIR, help="Explicit candidate directory (default: dist)")
    args = parser.parse_args()
    if args.production and args.development:
        parser.error("--production and --development are mutually exclusive")

    if args.package:
        print("[PACKAGE] Упаковка macOS zip архива...")
        package_kwargs = {"production": args.production}
        if args.arch is not None:
            package_kwargs["arch"] = args.arch
        pkg = package_macos_zip(args.dist_dir, **package_kwargs)
        if pkg is None:
            raise RuntimeError('No application bundle was available for packaging')
        print(f"  [OK] Создан архив: {pkg.name}")
        print("[PACKAGE] Генерация контрольных сумм SHA256SUMS.txt...")
        sums_p = generate_sha256sums(args.dist_dir)
        print(f"  [OK] Записаны контрольные суммы: {sums_p}")
        # Packaging is an artifact-producing phase, never an approval decision.
        return 0

    elif args.checksums:
        print("[CHECKSUMS] Генерация контрольных сумм SHA256SUMS.txt...")
        sums_p = generate_sha256sums(args.dist_dir)
        print(f"  [OK] Контрольные суммы сохранены в {sums_p}")
        return 0

    audit = run_full_release_candidate_audit(args.dist_dir)

    if args.development:
        print("Development metadata only; production release remains gated")
        return 0 if audit["candidate_metadata_valid"] else 1

    if args.json:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 0 if audit["ready_for_release"] else 1

    print(f"\n=======================================================")
    print(f" Аудит Release Candidate DOCXдодыр v{audit['version']}")
    print(f"=======================================================")
    print(f"Синхронизация версий: {'[OK]' if audit['version_check']['all_matched'] else '[FAIL]'}")
    for f, v in audit["version_check"]["files_checked"].items():
        print(f"  - {f}: {v}")
    if audit["version_check"]["mismatches"]:
        for m in audit["version_check"]["mismatches"]:
            print(f"    ! {m}")

    print(f"\nЮридические документы: {'[OK]' if audit['legal_check']['all_present'] else '[FAIL]'}")
    for doc, info in audit["legal_check"]["documents"].items():
        print(f"  - {doc}: {'присутствует' if info['exists'] else 'ОТСУТСТВУЕТ'} ({info['size_bytes']} байт)")

    print(f"\nРелизная документация: {'[OK]' if audit['docs_check']['all_present'] else '[FAIL]'}")
    for doc, info in audit["docs_check"]["docs"].items():
        print(f"  - {doc}: {'присутствует' if info['exists'] else 'ОТСУТСТВУЕТ'} ({info['size_bytes']} байт)")

    print(f"\nДистрибутивные артефакты в dist/ ({len(audit['artifacts'])} шт.):")
    for a in audit["artifacts"]:
        print(f"  - {a}")

    if audit["sums_verification"]:
        print(f"\nПроверка SHA256SUMS.txt: {'[OK]' if audit['sums_verification']['valid'] else '[FAIL]'}")
        for fn, res in audit["sums_verification"]["verified_files"].items():
            print(f"  - {fn}: {res['status']}")
    else:
        print(f"\nПроверка SHA256SUMS.txt: [ОТСУТСТВУЕТ]")

    print(f"\nИтоговый статус: {'[ГОТОВ К РЕЛИЗУ]' if audit['ready_for_release'] else '[ТРЕБУЮТСЯ ДЕЙСТВИЯ]'}\n")
    return 0 if audit["ready_for_release"] else 1


if __name__ == "__main__":
    sys.exit(main())
