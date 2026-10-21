"""`run_load`: silver, gold and the quality checks for one load date.

Bronze ingest is not called here: plan 2's pipeline runs `fetch_manifest` as its own activity,
and this reads what it landed for `load_date`. Nothing is written before the cost export has
been found and read, so a missing or empty export can never replace a full month with nothing.

Every run builds once. The re-run check compares this run's gold fingerprint with the one the
last successful run of the same load date recorded in `silver/run_log`; a first run for a date
has nothing to compare and passes. An entry is appended only after every check has passed, so a
failed run never becomes the baseline.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

from delta.tables import DeltaTable

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, StringType, StructField, StructType, TimestampType

from lakehouse.attribution import load_rg_rules, load_session_windows
from lakehouse.checks import (
    CheckResult,
    QualityCheckFailed,
    check_cost_totals,
    check_deployment_resolution,
    check_published_figures,
    check_rerun_unchanged,
)
from lakehouse.gold.build import build_gold
from lakehouse.gold.fingerprint import gold_fingerprint
from lakehouse.paths import LakePaths
from lakehouse.silver.costs import normalise_costs, read_cost_export, replace_month
from lakehouse.silver.deployments import apply_scd2, read_deployment_changes
from lakehouse.silver.eval_attempts import build_eval_attempts, merge_eval_attempts


RUN_LOG = "run_log"
_RUN_LOG_SCHEMA = StructType(
    [
        StructField("load_date", DateType()),
        StructField("fingerprint", StringType()),  # JSON object: gold table -> content hash
        StructField("recorded_at", TimestampType()),
    ]
)


@dataclass
class RunSummary:
    load_date: date
    row_counts: dict[str, int]
    checks: list[CheckResult]
    fingerprint: dict[str, str]


def _read_export(spark: SparkSession, root: str) -> tuple[DataFrame, dict[str, int]]:
    """The cost export's rows and manifest row counts per month folder.

    FileNotFoundError naming `root` if it holds no manifest, or its latest manifests list no rows.
    """
    export, expected_rows = read_cost_export(spark, root)
    if sum(expected_rows.values()) == 0 or export.limit(1).count() == 0:
        raise FileNotFoundError(f"cost export lists no data rows: {root}")
    return export, expected_rows


def _build(
    spark: SparkSession,
    paths: LakePaths,
    seeds_dir: str,
    export: DataFrame,
    load_date: date,
) -> dict[str, int]:
    """Silver (cost months replace, attempts MERGE, deployments MERGE), then gold; the row counts.

    Every billing month in the export is replaced, one `replace_month` each; months silver holds
    that the export lacks are untouched.
    """
    costs = normalise_costs(
        spark,
        export,
        load_rg_rules(seeds_dir),
        load_session_windows(seeds_dir),
    )
    months = [r["billing_month"] for r in costs.select("billing_month").distinct().collect()]
    if None in months:
        raise ValueError("cost export has a row whose date could not be read; nothing was written")
    for month in sorted(months):
        replace_month(spark, costs.where(F.col("billing_month") == month), paths.table("silver", "daily_cost"), month)

    attempts = build_eval_attempts(spark, paths.bronze, paths.private, seeds_dir, load_date)
    merge_eval_attempts(spark, attempts, paths.table("silver", "eval_attempts"))

    changes = read_deployment_changes(spark, seeds_dir)
    apply_scd2(spark, changes, paths.table("silver", "model_deployment"), seeds_dir)

    return build_gold(spark, paths, seeds_dir)


def _previous_fingerprint(spark: SparkSession, log_path: str, load_date: date) -> dict[str, str] | None:
    """The fingerprint the latest logged run of `load_date` recorded, or None for a first run."""
    if not DeltaTable.isDeltaTable(spark, log_path):
        return None
    latest = (
        spark.read.format("delta").load(log_path)
        .where(F.col("load_date") == F.lit(load_date))
        .orderBy(F.col("recorded_at").desc())
        .first()
    )
    return json.loads(latest["fingerprint"]) if latest else None


def _latest_logged_date(spark: SparkSession, log_path: str) -> date | None:
    """The latest `load_date` in the run log, or None when nothing has been logged."""
    if not DeltaTable.isDeltaTable(spark, log_path):
        return None
    return spark.read.format("delta").load(log_path).agg(F.max("load_date")).first()[0]


def _append_log(spark: SparkSession, log_path: str, load_date: date, fingerprint: dict[str, str]) -> None:
    row = (load_date, json.dumps(fingerprint, sort_keys=True), datetime.now(UTC))
    spark.createDataFrame([row], _RUN_LOG_SCHEMA).write.format("delta").mode("append").save(log_path)


def run_load(
    spark: SparkSession,
    paths: LakePaths,
    seeds_dir: str,
    cost_export_root: str,
    load_date: date,
    *,
    allow_earlier: bool = False,
) -> RunSummary:
    """Build silver and gold for `load_date`, run the four checks and return the summary.

    Sets the session to ANSI SQL and UTC first: Fabric's session is not built by `get_spark`, and
    both are part of the lake's contract (unreadable text fails, timestamps are UTC).

    Raises ValueError, before reading or writing anything, if `load_date` is earlier than the
    latest load date in the run log, because rebuilding an earlier export would roll the
    published costs back; pass `allow_earlier=True` to do it on purpose. Re-running the latest
    date is always allowed. Raises FileNotFoundError, before writing anything, if
    `cost_export_root` holds no manifest or its latest manifests list no rows. Raises
    QualityCheckFailed, after everything has been written and every check has
    run, if any check fails; the reject table and gold stay in place as the record of the run,
    but the run is not logged, so it is never the baseline for a later re-run.
    """
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    log_path = paths.table("silver", RUN_LOG)
    latest = _latest_logged_date(spark, log_path)
    if latest is not None and load_date < latest and not allow_earlier:
        raise ValueError(
            f"load date {load_date.isoformat()} is earlier than the latest logged load date "
            f"{latest.isoformat()}; rebuilding it would roll published costs back "
            "(pass allow_earlier=True to do so on purpose)"
        )
    export, expected_rows = _read_export(spark, cost_export_root)

    row_counts = _build(spark, paths, seeds_dir, export, load_date)
    fingerprint = gold_fingerprint(spark, paths)
    previous = _previous_fingerprint(spark, log_path, load_date)

    if previous is None:
        rerun = CheckResult("rerun_unchanged", True, "first run for this load date; nothing to compare")
    else:
        rerun = check_rerun_unchanged(previous, fingerprint)
    checks = [
        check_deployment_resolution(spark, paths),
        check_cost_totals(spark, paths, export, expected_rows),
        check_published_figures(spark, paths, seeds_dir),
        rerun,
    ]
    failed = [c for c in checks if not c.passed]
    if failed:
        raise QualityCheckFailed(failed)
    _append_log(spark, log_path, load_date, fingerprint)
    return RunSummary(load_date=load_date, row_counts=row_counts, checks=checks, fingerprint=fingerprint)
