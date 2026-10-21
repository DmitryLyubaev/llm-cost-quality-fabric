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

from pyspark.errors import AnalysisException
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


def _read_export(spark: SparkSession, path: str) -> DataFrame:
    """The raw cost export; FileNotFoundError naming `path` if it is missing or has no data rows."""
    try:
        export = read_cost_export(spark, path)
    except AnalysisException as error:
        if "PATH_NOT_FOUND" in str(error) or "does not exist" in str(error):
            raise FileNotFoundError(f"cost export not found: {path}") from error
        raise
    if export.limit(1).count() == 0:
        raise FileNotFoundError(f"cost export has no data rows: {path}")
    return export


def _build(
    spark: SparkSession,
    paths: LakePaths,
    seeds_dir: str,
    private_root: str,
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
        load_rg_rules(f"{seeds_dir}/rg_rules.csv"),
        load_session_windows(f"{seeds_dir}/session_windows.csv"),
    )
    months = [r["billing_month"] for r in costs.select("billing_month").distinct().collect()]
    if None in months:
        raise ValueError("cost export has a row whose date could not be read; nothing was written")
    for month in sorted(months):
        replace_month(spark, costs.where(F.col("billing_month") == month), paths.table("silver", "daily_cost"), month)

    attempts = build_eval_attempts(spark, paths.bronze, private_root, seeds_dir, load_date)
    merge_eval_attempts(spark, attempts, paths.table("silver", "eval_attempts"))

    changes = read_deployment_changes(spark, f"{seeds_dir}/deployment_changes.csv")
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


def _append_log(spark: SparkSession, log_path: str, load_date: date, fingerprint: dict[str, str]) -> None:
    row = (load_date, json.dumps(fingerprint, sort_keys=True), datetime.now(UTC))
    spark.createDataFrame([row], _RUN_LOG_SCHEMA).write.format("delta").mode("append").save(log_path)


def run_load(
    spark: SparkSession,
    paths: LakePaths,
    seeds_dir: str,
    private_root: str,
    cost_export_path: str,
    load_date: date,
) -> RunSummary:
    """Build silver and gold for `load_date`, run the four checks and return the summary.

    Raises FileNotFoundError, before writing anything, if the cost export is missing or has no
    data rows. Raises QualityCheckFailed, after everything has been written and every check has
    run, if any check fails; the reject table and gold stay in place as the record of the run,
    but the run is not logged, so it is never the baseline for a later re-run.
    """
    export = _read_export(spark, cost_export_path)

    row_counts = _build(spark, paths, seeds_dir, private_root, export, load_date)
    fingerprint = gold_fingerprint(spark, paths)
    log_path = paths.table("silver", RUN_LOG)
    previous = _previous_fingerprint(spark, log_path, load_date)

    if previous is None:
        rerun = CheckResult("rerun_unchanged", True, "first run for this load date; nothing to compare")
    else:
        rerun = check_rerun_unchanged(previous, fingerprint)
    checks = [
        check_deployment_resolution(spark, paths),
        check_cost_totals(spark, paths, export),
        check_published_figures(spark, paths, f"{seeds_dir}/published_figures.csv"),
        rerun,
    ]
    failed = [c for c in checks if not c.passed]
    if failed:
        raise QualityCheckFailed(failed)
    _append_log(spark, log_path, load_date, fingerprint)
    return RunSummary(load_date=load_date, row_counts=row_counts, checks=checks, fingerprint=fingerprint)
