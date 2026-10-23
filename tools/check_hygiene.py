"""Fail if tracked files hold notebook outputs, lakehouse shortcuts or forbidden literal values.

Fabric rules: a Fabric `notebook-content.py` must hold no cell-output marker, and a lakehouse
`shortcuts.metadata.json` must be an empty JSON array (a shortcut's target serialises the storage
account URL; the deploy creates the shortcut instead, so it never lands in git). No file may hold a
named Event Hubs namespace host (`<name>.servicebus.windows.net`) or Eventhouse query host
(`<name>.kusto.fabric.microsoft.com`): the deploy injects both.

Forbidden values (for example a tenant ID or storage account name) come from the
HYGIENE_FORBIDDEN environment variable, comma-separated, so they never live in the repo.
GUIDs are not checked for: Fabric workspace and item IDs may be committed.
"""
import json
import os
import re
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


# verify in plan 3: the marker a committed Fabric notebook would use for cell output. Fabric
# documents that outputs are not synced to git, so these are guesses that fail closed.
FABRIC_OUTPUT_MARKERS = ("# OUTPUT", "# CELL OUTPUT")


def _fabric_notebook_has_outputs(text: str) -> bool:
    return any(line.startswith(FABRIC_OUTPUT_MARKERS) for line in text.splitlines())


# A named Event Hubs namespace or Eventhouse (Kusto) host: identifiers that the deploy injects and git
# never holds. A bare suffix, or one after a template placeholder such as `${var.x}.` or `<namespace>.`,
# is not a host and passes.
FORBIDDEN_HOSTS = {
    "an Event Hubs namespace host": re.compile(
        r"(?<![\w.-])[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.servicebus\.windows\.net", re.IGNORECASE
    ),
    "a Kusto query host": re.compile(
        r"(?<![\w.-])[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.kusto\.fabric\.microsoft\.com", re.IGNORECASE
    ),
}


def _shortcuts_problem(text: str) -> str | None:
    try:
        shortcuts = json.loads(text)
    except json.JSONDecodeError:
        return "shortcuts file is not valid JSON"
    if shortcuts != []:
        return "lakehouse shortcuts are committed (expected an empty array)"
    return None


def check_hygiene(paths: list[Path], forbidden: list[str]) -> list[str]:
    """Return one message per problem found. Messages never repeat a forbidden value."""
    needles = [v.lower() for v in forbidden if v]
    problems: list[str] = []
    for path in paths:
        path = Path(path)
        text = _read_text(path)
        if text is None:
            continue
        if path.suffix == ".ipynb" and _notebook_has_outputs(text):
            problems.append(f"{path}: notebook has saved outputs")
        if path.name == "notebook-content.py" and _fabric_notebook_has_outputs(text):
            problems.append(f"{path}: Fabric notebook has an output marker")
        if path.name == "shortcuts.metadata.json":
            problem = _shortcuts_problem(text)
            if problem:
                problems.append(f"{path}: {problem}")
        for what, pattern in FORBIDDEN_HOSTS.items():
            if pattern.search(text):
                problems.append(f"{path}: contains {what}")
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
