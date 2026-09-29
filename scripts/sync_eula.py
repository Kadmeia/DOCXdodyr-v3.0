"""Render the plain-text installer license from the Markdown source."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def render_plain(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        line = line.rstrip()
        if line == "---":
            continue
        line = re.sub(r"^#{1,6} ", "", line)
        line = re.sub(
            r"\[([^]]+)\]\(([^)]+)\)",
            lambda match: match[1] if match[1] == match[2] else f"{match[1]} ({match[2]})",
            line,
        )
        line = line.replace("**", "").replace("`", "")
        if line or not lines or lines[-1]:
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


if __name__ == "__main__":
    (ROOT / "EULA.txt").write_text(
        render_plain((ROOT / "EULA.md").read_text(encoding="utf-8")),
        encoding="utf-8",
    )
