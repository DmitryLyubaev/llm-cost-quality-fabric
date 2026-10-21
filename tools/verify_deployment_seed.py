"""Check src/lakehouse/seeds/deployment_changes.csv against the Terraform at each row's commit.

Local only: it reads the two source repositories with `git show`, so it is not run in CI. The
comparison logic is pure (it takes a `git_show(sha, path) -> text` callable), and the tests
exercise it with a fake one.

    python tools/verify_deployment_seed.py --releaselens ../ReleaseLens --fwa ../foundry-workshop-agent

For every git-sourced row, the Terraform file named by `path` (and `variables.tf` in the same
folder) at the row's `sha` has to hold the row's model name, model version and capacity:
  - a version or capacity is a literal in the file, or the default of a `*_version` /
    `*_capacity` variable (the files set them through variables as often as not);
  - the version "unpinned" means a `*_model_version` variable with no default.
Exit status is 1 when any row does not match; each mismatch is named on stderr.
"""

import argparse
import csv
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

GitShow = Callable[[str, str], str]
DEFAULT_SEED = Path(__file__).resolve().parent.parent / "src" / "lakehouse" / "seeds" / "deployment_changes.csv"
REPO_OPTIONS = {"ReleaseLens": "releaselens", "foundry-workshop-agent": "fwa"}
UNPINNED = "unpinned"


def variable_blocks(text: str) -> dict[str, str]:
    """Body of each `variable "name" { ... }` block, found by brace matching.

    Braces inside quoted strings and `#` comments do not count.
    """
    blocks: dict[str, str] = {}
    for header in re.finditer(r'^\s*variable\s+"([^"]+)"\s*\{', text, re.MULTILINE):
        depth, i, in_string = 1, header.end(), False
        while i < len(text) and depth:
            ch = text[i]
            if in_string:
                if ch == "\\":
                    i += 1
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "#":
                end = text.find("\n", i)
                i = len(text) if end < 0 else end
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        blocks[header.group(1)] = text[header.end() : i - 1]
    return blocks


def _default(block: str) -> str | None:
    """The block's own `default = ...` value without quotes, or None if it has none."""
    match = re.search(r'^\s*default\s*=\s*"?([^"\n]*?)"?\s*$', block, re.MULTILINE)
    return match.group(1) if match else None


def _defaults(variables: str, name_suffix: str) -> list[str]:
    found = (_default(body) for name, body in variable_blocks(variables).items() if name.endswith(name_suffix))
    return [value for value in found if value is not None]


def _has_unpinned_version(variables: str) -> bool:
    return any(_default(body) is None for name, body in variable_blocks(variables).items() if name.endswith("model_version"))


def verify_row(row: dict, git_show: GitShow) -> list[str]:
    """Problems found for one git-sourced seed row (empty when it matches its commit)."""
    label = f"{row.get('account_alias')}/{row.get('deployment_name')} @ {(row.get('sha') or '')[:7]}"
    version, capacity, model = row.get("model_version"), row.get("capacity"), row.get("model_name")
    if not version or not capacity or not model:
        return [f"{label}: model_name, model_version and capacity must all be set"]

    path = row["path"]
    variables_path = posixpath.join(posixpath.dirname(path), "variables.tf")
    try:
        tf, variables = git_show(row["sha"], path), git_show(row["sha"], variables_path)
    except (RuntimeError, OSError) as error:
        return [f"{label}: cannot read the Terraform: {error}"]

    problems = []
    if f'"{model}"' not in tf:
        problems.append(f"{label}: model {model} not in {path}")

    if version == UNPINNED:
        if not _has_unpinned_version(variables):
            problems.append(f"{label}: version unpinned, but no *_model_version variable lacks a default")
    else:
        versions = re.findall(r'\bversion\s*=\s*"([^"]*)"', tf) + _defaults(variables, "_version")
        if version not in versions:
            problems.append(f"{label}: version {version} not in {path} or its variables.tf")

    capacities = re.findall(r"\bcapacity\s*=\s*(\d+)\b", tf) + _defaults(variables, "capacity")
    if capacity not in capacities:
        problems.append(f"{label}: capacity {capacity} not in {path} or its variables.tf")
    return problems


def verify_rows(rows: list[dict], git_shows: dict[str, GitShow]) -> tuple[list[str], int]:
    """Verify every git-sourced row. Returns (problems, number of rows verified clean)."""
    problems: list[str] = []
    verified = 0
    for row in rows:
        if row.get("source") == "manual":
            continue
        git_show = git_shows.get(row.get("repo") or "")
        if git_show is None:
            problems.append(f"{row.get('account_alias')}/{row.get('deployment_name')}: unknown repo {row.get('repo')}")
            continue
        found = verify_row(row, git_show)
        problems.extend(found)
        verified += not found
    return problems, verified


def _repo_git_show(repo: Path) -> GitShow:
    def git_show(sha: str, path: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"], capture_output=True, check=False)
        if done.returncode:
            raise RuntimeError(f"git show {sha}:{path} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
        return done.stdout.decode("utf-8")

    return git_show


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--releaselens", required=True, type=Path, help="local clone of ReleaseLens")
    parser.add_argument("--fwa", required=True, type=Path, help="local clone of foundry-workshop-agent")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    with args.seed.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    shows = {"ReleaseLens": _repo_git_show(args.releaselens), "foundry-workshop-agent": _repo_git_show(args.fwa)}
    problems, verified = verify_rows(rows, shows)
    manual = sum(row["source"] == "manual" for row in rows)
    for problem in problems:
        print(f"MISMATCH {problem}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s); {verified} git-sourced row(s) verified", file=sys.stderr)
        return 1
    print(f"verified {verified} git-sourced rows ({manual} manual rows skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
