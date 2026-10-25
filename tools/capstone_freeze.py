"""Freeze or verify everything that decides the capstone's score (see lakehouse.capstone.freeze).

    python tools/capstone_freeze.py freeze --analyst-repo <path> --analyst-commit <sha> \\
        --snapshot-time <iso> --model-direct <name> --model-foundry <name>
    python tools/capstone_freeze.py verify <same arguments>

`freeze` writes capstone/freeze.json; `verify` rebuilds it and exits 1, naming the component, if
anything drifted. The analyst's instructions are read with `git show <commit>:<path>` so they are
the instructions at that exact commit, not whatever is checked out. Exit 2 is a usage or input error.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from lakehouse.capstone.freeze import FreezeBroken, build_freeze, verify_freeze

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_PATH = "src/Portfolio.Analyst/instructions.md"


class InputError(Exception):
    """An input of the freeze could not be read."""


def read_instructions(repo: str, commit: str) -> str:
    """The analyst's instructions at `commit`, from `git show` (bytes in, so CRLF survives to the hash)."""
    result = subprocess.run(
        ["git", "-C", repo, "show", f"{commit}:{INSTRUCTIONS_PATH}"],
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise InputError(f"cannot read the analyst's instructions at {commit}: {detail}")
    return result.stdout.decode("utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("freeze", "verify"))
    parser.add_argument("--questions", type=Path, default=ROOT / "capstone" / "questions.json")
    parser.add_argument("--expected", type=Path, default=ROOT / "capstone" / "expected.json")
    parser.add_argument("--rules", type=Path, default=None, help="default: src/lakehouse/capstone/score.py")
    parser.add_argument("--data-agent-dir", type=Path, default=ROOT / "fabric-capstone" / "data-agent")
    parser.add_argument("--snapshot-time", required=True)
    parser.add_argument("--analyst-repo", required=True)
    parser.add_argument("--analyst-commit", required=True)
    parser.add_argument("--model-direct", required=True)
    parser.add_argument("--model-foundry", required=True)
    parser.add_argument("--freeze", type=Path, default=ROOT / "capstone" / "freeze.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        for path in (args.questions, args.expected):
            if not path.is_file():
                raise InputError(f"missing file: {path}")
        if not args.data_agent_dir.is_dir():
            raise InputError(f"missing data agent folder: {args.data_agent_dir}")
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
        if not args.freeze.is_file():
            raise InputError(f"missing freeze file: {args.freeze}")
        verify_freeze(json.loads(args.freeze.read_text(encoding="utf-8")), **inputs)
        print("freeze intact")
        return 0
    except FreezeBroken as broken:
        print(str(broken), file=sys.stderr)
        return 1
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
