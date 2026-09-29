"""Check that a source tree does not contain private test corpora or old paths."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {
    "1", "Тест", "тест 2", "тест 3", "manual_tests", "artifacts",
    "scratch", "outputs", "corpus", ".antigravity", ".work_contract_2026",
    "dist", "build", "venv", ".release-audit", ".agents",
}
DOCUMENT_EXTENSIONS = {
    ".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm", ".pdf",
    ".rtf", ".csv", ".ppt", ".pptx", ".zip", ".7z", ".rar",
    ".sqlite", ".db",
}
OLD_FOLDER = "DOCXdodyr2.2"


def check() -> list[str]:
    problems = []
    for name in sorted(PRIVATE_DIRS):
        if (ROOT / name).exists():
            problems.append(f"private directory present: {name}")
    for name in ("source_files", "test_data", "output_files"):
        if (ROOT / "tests" / name).exists():
            problems.append(f"generated tests directory present: tests/{name}")
    if list((ROOT / "tests").glob("fresh_corpus*")):
        problems.append("private test corpus present")
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(ROOT)
        if path.suffix.lower() in DOCUMENT_EXTENSIONS:
            problems.append(f"document present: {relative}")
        if path.suffix.lower() in {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".bat", ".command", ".spec"}:
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeError, OSError):
                continue
            if OLD_FOLDER in content and path != Path(__file__).resolve():
                problems.append(f"old absolute path present: {relative}")
    return problems


if __name__ == "__main__":
    failures = check()
    for failure in failures:
        print(failure)
    print(f"Public tree: {'FAILED' if failures else 'OK'}")
    sys.exit(bool(failures))
