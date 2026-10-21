"""Fail if tracked files hold notebook outputs or forbidden literal values.

Forbidden values (for example a tenant ID or storage account name) come from the
HYGIENE_FORBIDDEN environment variable, comma-separated, so they never live in the repo.
GUIDs are not checked for: Fabric workspace and item IDs may be committed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binary or unreadable: nothing to scan


def _notebook_has_outputs(text: str) -> bool:
    try:
        notebook = json.loads(text)
    except json.JSONDecodeError:
        return False
    cells = notebook.get("cells", []) if isinstance(notebook, dict) else []
    return any(cell.get("outputs") for cell in cells if isinstance(cell, dict))


def check_hygiene(paths: list[Path], forbidden: list[str]) -> list[str]:
    """Return one message per problem found. Messages never repeat a forbidden value."""
    needles = [v.lower() for v in forbidden if v]
    problems: list[str] = []
    for path in paths:
        text = _read_text(Path(path))
        if text is None:
            continue
        if path.suffix == ".ipynb" and _notebook_has_outputs(text):
            problems.append(f"{path}: notebook has saved outputs")
        lowered = text.lower()
        for index, needle in enumerate(needles, start=1):
            if needle in lowered:
                problems.append(f"{path}: contains forbidden value #{index}")
    return problems


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True, text=True
    ).stdout
    return [Path(p) for p in out.split("\0") if p]


def main() -> int:
    forbidden = [v.strip() for v in os.environ.get("HYGIENE_FORBIDDEN", "").split(",")]
    problems = check_hygiene(_tracked_files(), forbidden)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
