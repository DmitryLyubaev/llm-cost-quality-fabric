"""Freeze or verify everything that decides the capstone's score (see lakehouse.capstone.freeze).

    python tools/capstone_freeze.py freeze --analyst-repo <path> --analyst-commit <sha> \\
        --snapshot-time <iso> --model-direct <name> --model-foundry <name>
    python tools/capstone_freeze.py verify <same arguments>

`freeze` writes capstone/freeze.json; `verify` rebuilds it and exits 1, naming the component, if
anything drifted. The analyst's instructions are read with `git show <commit>:<path>` so they are
the instructions at that exact commit, not whatever is checked out; the blob's bytes are hashed as
they are. Exit 2 is a usage or input error: its message is fixed text that names the argument or the
component and never repeats git's own output.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from lakehouse.capstone.freeze import DataAgentError, FreezeBroken, build_freeze, verify_freeze

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_PATH = "src/Portfolio.Analyst/instructions.md"
COMPONENT = "analyst_instructions"

MSG_OPTION = f"{COMPONENT}: --analyst-commit must be a commit id, not an option"
MSG_NO_GIT = f"{COMPONENT}: git is not installed"
MSG_NO_COMMIT = f"{COMPONENT}: --analyst-commit is not a commit in --analyst-repo"
MSG_NO_FILE = f"{COMPONENT}: {INSTRUCTIONS_PATH} does not exist at --analyst-commit"


class InputError(Exception):
    """An input of the freeze could not be read; the message is fixed text."""


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True)
    except FileNotFoundError:
        raise InputError(MSG_NO_GIT) from None


def read_instructions(repo: str, commit: str) -> bytes:
    """The analyst's instructions at `commit`: the blob's raw bytes, never decoded."""
    if commit.startswith("-"):
        raise InputError(MSG_OPTION)
    resolved = _git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    sha = resolved.stdout.decode("ascii", "replace").strip()
    if resolved.returncode != 0 or not sha:
        raise InputError(MSG_NO_COMMIT)
    shown = _git(repo, "show", f"{sha}:{INSTRUCTIONS_PATH}")
    if shown.returncode != 0:
        raise InputError(MSG_NO_FILE)
    return shown.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("freeze", "verify"))
    parser.add_argument("--questions", type=Path, default=ROOT / "capstone" / "questions.json")
    parser.add_argument("--expected", type=Path, default=ROOT / "capstone" / "expected.json")
    parser.add_argument("--rules", type=Path, default=None, help="default: src/lakehouse/capstone/score.py")
    parser.add_argument(
        "--data-agent-dir", type=Path, default=ROOT / "fabric-capstone" / "da_portfolio.DataAgent"
    )
    parser.add_argument("--snapshot-time", required=True)
    parser.add_argument("--analyst-repo", required=True)
    parser.add_argument("--analyst-commit", required=True)
    parser.add_argument("--model-direct", required=True)
    parser.add_argument("--model-foundry", required=True)
    parser.add_argument("--freeze", type=Path, default=ROOT / "capstone" / "freeze.json")
    return parser


def _require_file(argument: str, path: Path | None) -> None:
    if path is not None and not path.is_file():
        raise InputError(f"{argument}: file not found")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _require_file("--questions", args.questions)
        _require_file("--expected", args.expected)
        _require_file("--rules", args.rules)
        if not args.data_agent_dir.is_dir():
            raise InputError("--data-agent-dir: folder not found")
        if args.command == "verify":
            _require_file("--freeze", args.freeze)
        inputs = {
            "questions": args.questions,
            "expected": args.expected,
            "rules": args.rules,
            "snapshot_time": args.snapshot_time,
            "data_agent_dir": args.data_agent_dir,
            "analyst_instructions": read_instructions(args.analyst_repo, args.analyst_commit),
            "models": {"direct": args.model_direct, "foundry": args.model_foundry},
        }
        if args.command == "freeze":
            freeze = build_freeze(**inputs)
            args.freeze.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"froze {freeze['short']} into {args.freeze}")
            return 0
        verify_freeze(json.loads(args.freeze.read_text(encoding="utf-8")), **inputs)
        print("freeze intact")
        return 0
    except FreezeBroken as broken:
        print(str(broken), file=sys.stderr)
        return 1
    except DataAgentError as error:
        print(f"--data-agent-dir: {error}", file=sys.stderr)
        return 2
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
