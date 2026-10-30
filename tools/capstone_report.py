"""Render the capstone report from both routes' records (see lakehouse.capstone.report).

    python tools/capstone_report.py --runs capstone/runs/direct capstone/runs/foundry \\
        --out capstone/report.md --freezes 1 \\
        --cu-direct <n> --cu-foundry <n> --cu-unit "CU-seconds" \\
        --analyst-repo <path> --analyst-commit <sha> \\
        --snapshot-time <iso> --model-direct <name> --model-foundry <name>

It takes the same freeze options as tools/capstone_freeze.py and refuses (exit 1, naming the
component) unless verify_freeze passes on them, so the report scores against the frozen questions
and expected answers, and states the verified snapshot time. `--runs` takes `.jsonl` files or
folders (a folder's `*.jsonl`; an interrupted run's `.jsonl.partial` is not read). The capacity
units are read by hand from the Capacity Metrics app; `--cu-unit` names their unit and is required
with either figure. `--freezes` is how many freezes there were in all. Both routes need records:
`--allow-missing-route <route>` lets one have none, and the report then says it was not run.

It takes no `--rules`: it scores with the imported `lakehouse.capstone.score`, so the freeze is
verified against that file (`freeze.RULES_PATH`); a `--rules` is refused (exit 2). The report states
the Fabric commit it ran from (`git rev-parse --short HEAD`) and refuses to render (exit 2) while
src/lakehouse/capstone/report.py or score.py has uncommitted changes. `--allow-dirty` renders anyway,
for development only, and the report then says so.

Exit codes: 0 written; 1 the freeze is broken; 2 a usage or input error (a --rules, uncommitted
scoring code), or records that cannot be reported (another freeze, a duplicate, a missing pass, a
broken record).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import capstone_freeze
from capstone_freeze import InputError, add_freeze_arguments, load_verified_freeze, refuse_rules
from lakehouse.capstone.freeze import DataAgentError, FreezeBroken
from lakehouse.capstone.report import ReportError, load_records, render


SOURCE_PATHS = ("src/lakehouse/capstone/report.py", "src/lakehouse/capstone/score.py")
MSG_NO_COMMIT = "the Fabric commit cannot be read (git failed or is not installed)"
MSG_DIRTY = (
    "src/lakehouse/capstone/report.py or score.py has uncommitted changes: commit them first "
    "(--allow-dirty renders anyway, for development only)"
)


def source_state(root: Path = capstone_freeze.ROOT) -> tuple[str, bool]:
    """The short commit of `root`'s HEAD, and whether report.py or score.py differs from it
    (staged, unstaged or untracked). InputError (fixed text) when git cannot tell."""
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True)
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--", *SOURCE_PATHS],
            capture_output=True,
        )
    except FileNotFoundError:
        raise InputError(MSG_NO_COMMIT) from None
    commit = head.stdout.decode("ascii", "replace").strip()
    if head.returncode != 0 or status.returncode != 0 or not commit:
        raise InputError(MSG_NO_COMMIT)
    return commit, bool(status.stdout.strip())


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _cu(text: str) -> float:
    value = float(text)
    if not value >= 0:
        raise argparse.ArgumentTypeError("must be a number, 0 or more")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    runs = capstone_freeze.ROOT / "capstone" / "runs"
    parser.add_argument("--runs", type=Path, nargs="+", default=[runs / "direct", runs / "foundry"])
    parser.add_argument("--out", type=Path, default=capstone_freeze.ROOT / "capstone" / "report.md")
    parser.add_argument("--freezes", type=_positive, required=True, help="how many freezes there were in all")
    parser.add_argument("--cu-direct", type=_cu, default=None)
    parser.add_argument("--cu-foundry", type=_cu, default=None)
    parser.add_argument("--cu-unit", default=None, help='the CU figures\' unit, for example "CU-seconds"')
    parser.add_argument(
        "--allow-missing-route",
        action="append",
        choices=("direct", "foundry"),
        default=[],
        help="a route that may have no records; the report says it was not run (repeatable)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="development only: render although report.py or score.py has uncommitted changes (the report says so)",
    )
    add_freeze_arguments(parser, rules=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        refuse_rules(argv)
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2
    args = _parser().parse_args(argv)
    try:
        commit, dirty = source_state()
        if dirty and not args.allow_dirty:
            raise InputError(MSG_DIRTY)
        if (args.cu_direct is not None or args.cu_foundry is not None) and not (args.cu_unit or "").strip():
            raise InputError("--cu-unit: required with --cu-direct or --cu-foundry")
        freeze = load_verified_freeze(args)
        questions = json.loads(args.questions.read_text(encoding="utf-8"))
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        text = render(
            load_records(args.runs),
            questions,
            expected,
            freeze,
            snapshot_time=args.snapshot_time,
            freezes=args.freezes,
            cu={"direct": args.cu_direct, "foundry": args.cu_foundry},
            cu_unit=args.cu_unit,
            allow_missing=tuple(args.allow_missing_route),
            commit=commit,
            dirty=dirty,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
        return 0
    except FreezeBroken as broken:
        print(str(broken), file=sys.stderr)
        return 1
    except DataAgentError as error:
        print(f"--data-agent-dir: {error}", file=sys.stderr)
        return 2
    except (InputError, ReportError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
