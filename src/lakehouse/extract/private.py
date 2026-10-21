"""Sanitising extractor for the two private source reports (P1 and P2).

This is the only module that reads the private files. It keeps measurements and
identifiers of rows, and drops all free text, citations, retrieved hits and
endpoint details. Output goes to a git-ignored folder, never into the repo.
"""

import argparse
import json
import re
from pathlib import Path

P1_STUDY = "p1-three-arm"
P2_STUDY = "p2-retrieval"

FORBIDDEN_KEYS = frozenset(
    {"question", "answer", "groundedness_reason", "citations", "hits", "chunk_id",
     "search", "deployments", "questions"}
)
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_URL = re.compile(r"https?://", re.IGNORECASE)


def extract_p1(report: dict) -> list[dict]:
    """One row per scored outcome of the three-arm study."""
    return [
        {
            "study": P1_STUDY,
            "run_id": report["run_id"],
            "started_at": report["started_at"],
            "item_id": o["id"],
            "arm": o["arm"],
            "pass_index": o["pass_index"],
            "attempt_kind": "scored",
            "provider": o["provider"],
            "model": o["model"],
            "success": o["error"] is None and not o["no_reply"],
            "latency_ms": o["latency_ms"],
            "tokens_in": o["tokens_in"],
            "tokens_out": o["tokens_out"],
            "cached_tokens": o["cache_read_input_tokens"],
            "cost_usd": o["cost_usd"],
            "judge_cost_usd": o["judge_cost_usd"],
            "groundedness": o["groundedness"],
            "citation_recall": o["citation_recall"],
            "citation_precision": o["citation_precision"],
            "must_contain": o["must_contain_satisfied"],
        }
        for o in report["outcomes"]
    ]


def _target_rank(hits: list[dict], target: str) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if hit["artefact"] == target:
            return rank
    return None


def extract_p2(report: dict, questions: list[dict]) -> list[dict]:
    """One row per retrieval result, plus one per determinism repeat."""
    targets = {q["qid"]: q["target"] for q in questions}
    rows = []
    for kind, entries in (("scored", report["results"]), ("determinism_repeat", report["repeat"])):
        for entry in entries:
            qid = entry["qid"]
            if qid not in targets:
                raise ValueError(f"no target for question {qid!r} in the questions file")
            rank = _target_rank(entry["hits"], targets[qid])
            rows.append(
                {
                    "study": P2_STUDY,
                    "run_id": report["run_id"],
                    "started_at": report["started_at"],
                    "item_id": qid,
                    "arm": entry["arm"],
                    "pass_index": 1,
                    "attempt_kind": kind,
                    "success": entry["error"] is None,
                    "latency_ms": entry["ms"],
                    "tokens_in": entry["query_tokens"],
                    "target_rank": rank,
                    "top1": rank == 1,
                    "reciprocal_rank": 1 / rank if rank else 0.0,
                }
            )
    return rows


def assert_clean(rows: list[dict]) -> None:
    """Raise ValueError naming the field if a row holds text, GUIDs or URLs."""
    for row in rows:
        for key, value in row.items():
            if key in FORBIDDEN_KEYS:
                raise ValueError(f"forbidden field in output row: {key}")
            if isinstance(value, str) and (_GUID.search(value) or _URL.search(value)):
                raise ValueError(f"GUID or URL in value of field: {key}")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--p1", required=True, type=Path)
    parser.add_argument("--p2", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    p1 = json.loads(args.p1.read_text(encoding="utf-8"))
    p2 = json.loads(args.p2.read_text(encoding="utf-8"))
    questions = [
        json.loads(line)
        for line in args.questions.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    p1_rows = extract_p1(p1)
    p2_rows = extract_p2(p2, questions)
    assert_clean(p1_rows)
    assert_clean(p2_rows)

    args.out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.out / "p1_outcomes.jsonl", p1_rows)
    _write_jsonl(args.out / "p2_results.jsonl", p2_rows)
    print(f"p1_outcomes.jsonl: {len(p1_rows)} rows; p2_results.jsonl: {len(p2_rows)} rows")


if __name__ == "__main__":
    main()
