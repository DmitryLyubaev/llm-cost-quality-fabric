"""The capstone's own answer functions, each the code behind one kind of question.

Each takes `(spark, paths, **args)` and returns a DataFrame read from gold, like the answers in
`lakehouse.answers`. The expected answer of a capstone question is one of these (or an existing
answer function) plus a selector, so no expected value is ever typed by hand. Costs stay in their
billing currency and are never converted or added across currencies.
"""

import re
from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse.paths import LakePaths

SCORED = "scored"
_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _gold(spark: SparkSession, paths: LakePaths, name: str) -> DataFrame:
    return spark.read.format("delta").load(paths.table("gold", name))


def _month(value: object) -> str:
    if not isinstance(value, str) or not _MONTH.fullmatch(value):
        raise ValueError(f"month must be a string like 2026-10 (yyyy-MM), got {value!r}")
    return value


def _day(name: str, value: object) -> str:
    if isinstance(value, str) and _DAY.fullmatch(value):
        try:
            date.fromisoformat(value)
            return value
        except ValueError:
            pass
    raise ValueError(f"{name} must be a calendar date like 2026-10-25 (yyyy-MM-dd), got {value!r}")


def spend_for_month(spark: SparkSession, paths: LakePaths, month: str) -> DataFrame:
    """Serves the question "How much did <project> spend in <month>?": the month's attributed spend.

    One row per project and billing currency that has cost lines in the billing month `month`
    (`yyyy-MM`): project, billing_currency, total (in that billing currency). A project with no
    lines that month has no row, and nothing is converted between currencies.
    """
    month = _month(month)
    return (
        _gold(spark, paths, "fact_daily_cost")
        .where(F.col("billing_month") == month)
        .groupBy("project", "billing_currency")
        .agg(F.sum("cost").alias("total"))
        .orderBy("project", "billing_currency")
    )


def live_check_counts(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Serves the question "How many live checks passed, failed or were not run for <project>?".

    One row per project and verdict in `fact_live_check`: project, verdict, n (the checks with
    that verdict, over every session).
    """
    return (
        _gold(spark, paths, "fact_live_check")
        .groupBy("project", "verdict")
        .agg(F.count("*").alias("n"))
        .orderBy("project", "verdict")
    )


def eval_count_by_arm(spark: SparkSession, paths: LakePaths, study: str) -> DataFrame:
    """Serves the question "How many scored results does each arm of <study> have?".

    One row per arm of the study: arm, n (its scored attempts). Determinism repeats and superseded
    attempts are not scored and are not counted.
    """
    if not isinstance(study, str) or not study:
        raise ValueError(f"study must be a non-empty string, got {study!r}")
    return (
        _gold(spark, paths, "fact_eval_attempt")
        .where((F.col("study") == study) & (F.col("attempt_kind") == SCORED))
        .groupBy("arm")
        .agg(F.count("*").alias("n"))
        .orderBy("arm")
    )


def cost_by_project_between(spark: SparkSession, paths: LakePaths, start: str, end: str) -> DataFrame:
    """Serves the question "What did <project> cost between <start> and <end>?": a closed range of days.

    One row per project and billing currency over the cost dates from `start` to `end` inclusive
    (both `yyyy-MM-dd`): project, billing_currency, total (in that billing currency). ValueError
    when `end` is before `start`.
    """
    start, end = _day("start", start), _day("end", end)
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return (
        _gold(spark, paths, "fact_daily_cost")
        .where((F.col("cost_date") >= F.lit(start).cast("date")) & (F.col("cost_date") <= F.lit(end).cast("date")))
        .groupBy("project", "billing_currency")
        .agg(F.sum("cost").alias("total"))
        .orderBy("project", "billing_currency")
    )
