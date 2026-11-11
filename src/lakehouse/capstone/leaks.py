"""The value-based leak check: does any few-shot example's result show a frozen question's answer?

The data agent sees its examples' queries, and a model could copy a figure from an example's result.
`tests/test_capstone_agent.py` rules that out structurally on the fixture (no example on a frozen
question's function with a selector as wide as its own). This check runs on the real snapshot
(`nb_capstone_expected` writes its findings to `leaks.json`), and compares values, so it also
catches an example on another function, or with other arguments, whose result happens to hold a
frozen answer.

`value_leaks(spark, paths, questions, fewshots_sources)` evaluates every example's Source (its
function and arguments, then its selector's equality filters, never its `column`) and keeps the
example's listed output columns. For each single-cell frozen question (money, count or rate, with a
`column`), an example leaks when its output columns include the question's selector keys and its
figure column (the question's `column`), and one of its rows has the selector's values (compared as
strings, as `compute_expected` does) and a figure equal to the expected value: within the scorer's
money tolerance (1% or 0.01, whichever is wider), within its rate tolerance (0.005), or exactly for a
count. Findings are `{"example_id", "question_id"}` only, sorted, never a value.
"""

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from lakehouse.capstone.expected import Source, compute_expected, resolve_function
from lakehouse.capstone.score import MONEY_ABSOLUTE, MONEY_RELATIVE, RATE_ABSOLUTE
from lakehouse.paths import LakePaths

SINGLE_CELL_KINDS = ("money", "count", "rate")
_EPSILON = 1e-9  # the scorer's float slack


def _is_number(value) -> bool:
    """A figure cell: not None, a bool or a string (a label column is never a figure)."""
    return value is not None and not isinstance(value, bool | str)


def figure_matches(kind: str, value, want) -> bool:
    """Whether `value` would score as `want` for a question of `kind` (money, count or rate)."""
    if kind not in SINGLE_CELL_KINDS or not _is_number(value) or not _is_number(want):
        return False
    try:
        value, want = float(value), float(want)  # a Decimal cell reads as its float
    except (TypeError, ValueError):
        return False
    if kind == "money":
        return abs(value - want) <= max(MONEY_ABSOLUTE, MONEY_RELATIVE * abs(want)) + _EPSILON
    if kind == "rate":
        return abs(value - want) <= RATE_ABSOLUTE + _EPSILON
    return value == want


def _example_rows(spark: SparkSession, paths: LakePaths, entry: dict) -> tuple[list[str], list[dict]]:
    """An example's listed output columns and its rows, as `{column: cell}`."""
    example_id = entry.get("id")
    columns = entry.get("columns")
    if not isinstance(columns, list) or not columns or not all(isinstance(c, str) for c in columns):
        raise ValueError(f"example {example_id}: `columns` must be a non-empty list of names")
    try:
        source = Source.from_dict(entry.get("source"))
    except ValueError as exc:
        raise ValueError(f"example {example_id}: {exc}") from exc
    frame = resolve_function(source.function)(spark, paths, **source.args)
    missing = [c for c in columns + list(source.select or {}) if c not in frame.columns]
    if missing:
        raise ValueError(f"example {example_id}: columns {missing} are not in {source.function}'s {frame.columns}")
    for column, wanted in (source.select or {}).items():
        frame = frame.where(frame[column].cast("string") == F.lit(wanted))
    return columns, [row.asDict() for row in frame.select(*columns).collect()]


def value_leaks(
    spark: SparkSession,
    paths: LakePaths,
    questions: list[dict],
    fewshots_sources: list[dict],
    expected: dict | None = None,
) -> list[dict]:
    """`[{"example_id", "question_id"}]` for every example whose result shows a frozen answer.

    `expected` is `compute_expected`'s result for these questions when the caller has it already;
    without it the single-cell questions' answers are computed here. ValueError when an example's
    Source is invalid or lists a column its function does not return.
    """
    single = [
        q for q in questions
        if q.get("kind") in SINGLE_CELL_KINDS and (q.get("source") or {}).get("column")
    ]  # fmt: skip
    if expected is None:
        expected = compute_expected(spark, paths, single)
    examples = [(entry.get("id"), *_example_rows(spark, paths, entry)) for entry in fewshots_sources]

    findings = set()
    for question in single:
        want = (expected.get(question["id"]) or {}).get("value")
        if not _is_number(want):
            continue
        select = question["source"].get("select") or {}
        figure = question["source"]["column"]
        for example_id, columns, rows in examples:
            if figure not in columns or not set(select) <= set(columns):
                continue
            for row in rows:
                if all(row[k] is not None and str(row[k]) == v for k, v in select.items()) and figure_matches(
                    question["kind"], row[figure], want
                ):
                    findings.add((example_id, question["id"]))
                    break
    return [{"example_id": e, "question_id": q} for e, q in sorted(findings)]
