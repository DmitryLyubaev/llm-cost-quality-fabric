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
MSG_FREEZE_NOT_UTF8 = "--freeze: not UTF-8 text"
MSG_FREEZE_NOT_JSON = "--freeze: not valid JSON"
MSG_FREEZE_NOT_OBJECT = "--freeze: not a freeze object (a JSON object)"


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


def add_freeze_arguments(parser: argparse.ArgumentParser, rules: bool = True) -> None:
    """The freeze's inputs as options; the runner and the report take the same ones, less `--rules`.

    `rules=False` leaves `--rules` out: the runner and the report score with the imported
    `lakehouse.capstone.score`, so they must verify the freeze against that same file
    (`freeze.RULES_PATH`), never one named on the command line.
    """
    parser.add_argument("--questions", type=Path, default=ROOT / "capstone" / "questions.json")
    parser.add_argument("--expected", type=Path, default=ROOT / "capstone" / "expected.json")
    if rules:
        parser.add_argument("--rules", type=Path, default=None, help="default: src/lakehouse/capstone/score.py")
    else:
        parser.set_defaults(rules=None)  # build_freeze then hashes RULES_PATH, the scoring code itself
    parser.add_argument(
        "--data-agent-dir", type=Path, default=ROOT / "fabric-capstone" / "da_portfolio.DataAgent"
    )
    parser.add_argument("--snapshot-time", required=True)
    parser.add_argument("--analyst-repo", required=True)
    parser.add_argument("--analyst-commit", required=True)
    parser.add_argument("--model-direct", required=True)
    parser.add_argument("--model-foundry", required=True)
    parser.add_argument("--freeze", type=Path, default=ROOT / "capstone" / "freeze.json")


MSG_NO_RULES = (
    "--rules: not taken here; the freeze is verified against src/lakehouse/capstone/score.py, "
    "the code that scores (only the freeze command takes --rules)"
)


def refuse_rules(argv: list[str] | None) -> None:
    """InputError (fixed text) when `argv` names --rules: for the runner and the report, which drop it."""
    given = sys.argv[1:] if argv is None else argv
    if any(arg == "--rules" or arg.startswith("--rules=") for arg in given):
        raise InputError(MSG_NO_RULES)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("freeze", "verify"))
    add_freeze_arguments(parser)
    return parser


def _require_file(argument: str, path: Path | None) -> None:
    if path is not None and not path.is_file():
        raise InputError(f"{argument}: file not found")


def gather_inputs(args: argparse.Namespace, need_freeze: bool) -> dict:
    """The keyword arguments of build_freeze / verify_freeze, from `add_freeze_arguments` options.

    InputError (fixed text) when a file or folder is missing or the instructions cannot be read;
    `need_freeze` also requires the --freeze file.
    """
    _require_file("--questions", args.questions)
    _require_file("--expected", args.expected)
    _require_file("--rules", getattr(args, "rules", None))
    if not args.data_agent_dir.is_dir():
        raise InputError("--data-agent-dir: folder not found")
    if need_freeze:
        _require_file("--freeze", args.freeze)
    return {
        "questions": args.questions,
        "expected": args.expected,
        "rules": getattr(args, "rules", None),
        "snapshot_time": args.snapshot_time,
        "data_agent_dir": args.data_agent_dir,
        "analyst_instructions": read_instructions(args.analyst_repo, args.analyst_commit),
        "models": {"direct": args.model_direct, "foundry": args.model_foundry},
    }


def load_verified_freeze(args: argparse.Namespace) -> dict:
    """The --freeze file, after verify_freeze passed on the inputs it names.

    FreezeBroken names the component that drifted; DataAgentError and InputError as for `verify`.
    """
    inputs = gather_inputs(args, need_freeze=True)
    try:
        text = args.freeze.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise InputError(MSG_FREEZE_NOT_UTF8) from None
    try:
        freeze = json.loads(text)
    except json.JSONDecodeError:
        raise InputError(MSG_FREEZE_NOT_JSON) from None
    if not isinstance(freeze, dict):
        raise InputError(MSG_FREEZE_NOT_OBJECT)
    verify_freeze(freeze, **inputs)
    return freeze


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze":
            freeze = build_freeze(**gather_inputs(args, need_freeze=False))
            args.freeze.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"froze {freeze['short']} into {args.freeze}")
            return 0
        load_verified_freeze(args)
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
