"""The expected answers: an answer function plus an optional selector, never a typed value.

A question's `source` names a function (`lakehouse.answers.*`, `lakehouse.gateway.answers.*` or
`lakehouse.capstone.answers.*`) and its arguments. A selector then picks the rows and the cell:

- `select` holds column-to-value equality filters, compared as strings with no case folding;
- `column` names the single cell to return (exactly one row must be selected); without it the
  value is the set of the selected rows' first column, sorted and de-duplicated, as strings.

The result of each question is `{"value": number | list[str] | None, "currency": str | None,
"rows": int}`. `currency` is the selected row's `billing_currency` when the answer has one, else
its `actual_currency` (as in `cost_per_result`); for a set, the one currency its rows share;
otherwise None. A question with no source (the
unanswerable one) has no expected answer and is left out.
"""

import importlib
import inspect
import math
from dataclasses import dataclass, field
from decimal import Decimal

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse.paths import LakePaths

# Exact module names, checked before anything is imported: the question file names the function, so
# it must not be able to name an arbitrary callable.
ALLOWED_MODULES = ("lakehouse.answers", "lakehouse.gateway.answers", "lakehouse.capstone.answers")
MAX_ROWS = 25  # the data agent's answers are capped at 25 rows, so an expected answer is too
# The answer's currency column: billing_currency, else actual_currency (cost_per_result), else none.
CURRENCY_COLUMNS = ("billing_currency", "actual_currency")
_FIELDS = ("function", "args", "select", "column")


@dataclass(frozen=True)
class Source:
    """Where an expected answer comes from: `function(spark, paths, **args)`, then the selector."""

    function: str
    args: dict = field(default_factory=dict)
    select: dict | None = None
    column: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "Source":
        if not isinstance(data, dict):
            raise ValueError(f"a source is an object, got {type(data).__name__}")
        extra = sorted(set(data) - set(_FIELDS))
        if extra:
            raise ValueError(f"unknown source fields (extra): {extra}")
        function = data.get("function")
        if not isinstance(function, str) or not function:
            raise ValueError("a source needs a `function` name")
        args = data.get("args") or {}
        select = data.get("select") or None
        column = data.get("column")
        if not isinstance(args, dict) or not all(isinstance(k, str) for k in args):
            raise ValueError(f"`args` must be an object with string keys, got {args!r}")
        if select is not None and (not isinstance(select, dict) or not all(isinstance(k, str) for k in select)):
            raise ValueError(f"`select` must be an object with string keys, got {select!r}")
        if column is not None and not isinstance(column, str):
            raise ValueError(f"`column` must be a string, got {column!r}")
        return cls(function, args, select, column)


def resolve_function(name: str):
    """The public answer function called `name`, or ValueError.

    The module must be one of `ALLOWED_MODULES` exactly (checked before any import), the name must
    not start with an underscore, and it must be a function defined in that module (not an imported
    helper or a constant).
    """
    if not isinstance(name, str):
        raise ValueError(f"a function name is a string, got {name!r}")
    module, _, attr = name.rpartition(".")
    if module not in ALLOWED_MODULES:
        raise ValueError(f"function {name!r} is not in an allowed module {list(ALLOWED_MODULES)}")
    if not attr or attr.startswith("_"):
        raise ValueError(f"function {name!r} is not a public function")
    target = getattr(importlib.import_module(module), attr, None)
    if not inspect.isfunction(target) or target.__module__ != module:
        raise ValueError(f"{name!r} is not a function defined in {module}")
    return target


def _number(value, qid: str, column: str):
    """A cell as float or int; NaN and None are None; anything else that is not a number raises."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ValueError(f"question {qid}: column {column!r} holds {value!r}, not a number")
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            raise ValueError(f"question {qid}: column {column!r} holds {value!r}, not a finite number")
    return value


def _select_rows(frame: DataFrame, source: Source, qid: str) -> list:
    """The selected rows (at most MAX_ROWS), or ValueError when there are none or too many."""
    selected = frame
    for column, wanted in (source.select or {}).items():
        if column not in frame.columns:
            raise ValueError(f"question {qid}: selector column {column!r} is not in {frame.columns}")
        if not isinstance(wanted, str):
            raise ValueError(f"question {qid}: selector value for {column!r} must be a string, got {wanted!r}")
        selected = selected.where(frame[column].cast("string") == F.lit(wanted))
    rows = selected.limit(MAX_ROWS + 1).collect()
    if len(rows) > MAX_ROWS:
        raise ValueError(f"question {qid}: the selection yields more than {MAX_ROWS} rows")
    if not rows:
        raise ValueError(f"question {qid}: the selection matches no row")
    return rows


def _expected_answer(spark: SparkSession, paths: LakePaths, qid: str, source: Source) -> dict:
    function = resolve_function(source.function)
    try:
        frame = function(spark, paths, **source.args)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"question {qid}: {source.function} refused its arguments {source.args!r}: {exc}") from exc
    if not isinstance(frame, DataFrame):
        raise ValueError(f"question {qid}: {source.function} did not return a DataFrame")
    if source.column is not None and source.column not in frame.columns:
        raise ValueError(f"question {qid}: column {source.column!r} is not in {frame.columns}")

    rows = _select_rows(frame, source, qid)
    currency_column = next((c for c in CURRENCY_COLUMNS if c in frame.columns), None)
    if source.column is not None:
        if len(rows) > 1:
            raise ValueError(f"question {qid}: the selection matches more than one row ({len(rows)}) for a single cell")
        row = rows[0]
        currency = row[currency_column] if currency_column else None
        return {"value": _number(row[source.column], qid, source.column), "currency": currency, "rows": 1}

    first = frame.columns[0]
    value = sorted({str(row[first]) for row in rows if row[first] is not None})
    currencies = {row[currency_column] for row in rows if row[currency_column] is not None} if currency_column else set()
    return {"value": value, "currency": next(iter(currencies)) if len(currencies) == 1 else None, "rows": len(rows)}


def compute_expected(spark: SparkSession, paths: LakePaths, questions: list[dict]) -> dict[str, dict]:
    """`{question_id: {"value", "currency", "rows"}}` for every question that has a source.

    ValueError when a question id repeats or is missing, a function is not an allowed public one,
    a selector names a missing column, matches no row, or yields more than 25 rows, or a
    single-cell selector matches more than one row. Every message names the question.
    """
    ids = [q.get("id") if isinstance(q, dict) else None for q in questions]
    if any(not isinstance(i, str) or not i for i in ids):
        raise ValueError(f"every question needs a non-empty string id, got {ids!r}")
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise ValueError(f"duplicate question ids: {repeated}")

    expected: dict[str, dict] = {}
    for question in questions:
        if question.get("source") is None:
            continue
        qid = question["id"]
        try:
            source = Source.from_dict(question["source"])
        except ValueError as exc:
            raise ValueError(f"question {qid}: {exc}") from exc
        expected[qid] = _expected_answer(spark, paths, qid, source)
    return expected
