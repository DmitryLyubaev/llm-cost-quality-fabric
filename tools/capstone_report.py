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
with either figure. `--freezes` is how many freezes there were in all.

Exit codes: 0 written; 1 the freeze is broken; 2 a usage or input error, or records that cannot be
reported (another freeze, a duplicate, a missing pass, a broken record).
"""
import argparse
import json
import sys
from pathlib import Path

import capstone_freeze
from capstone_freeze import InputError, add_freeze_arguments, load_verified_freeze
from lakehouse.capstone.freeze import DataAgentError, FreezeBroken
from lakehouse.capstone.report import ReportError, load_records, render


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
    add_freeze_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
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
