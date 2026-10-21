"""`run_load`: silver, gold and the quality checks for one load date.

Bronze ingest is not called here: plan 2's pipeline runs `fetch_manifest` as its own activity,
and this reads what it landed for `load_date`. Nothing is written before the cost export has
been found and read, so a missing or empty export can never replace a full month with nothing.

The re-run check proves the claim on every run: the silver and gold build runs twice in a row on
the same inputs, and the two gold fingerprints must be equal.
"""

from dataclasses import dataclass
from datetime import date

from pyspark.errors import AnalysisException
from pyspark.sql import DataFrame, SparkSession

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
    """Silver (cost month replace, attempts MERGE, deployments MERGE), then gold; the row counts."""
    costs = normalise_costs(
        spark,
        export,
        load_rg_rules(f"{seeds_dir}/rg_rules.csv"),
        load_session_windows(f"{seeds_dir}/session_windows.csv"),
    )
    replace_month(spark, costs, paths.table("silver", "daily_cost"), f"{load_date:%Y-%m}")

    attempts = build_eval_attempts(spark, paths.bronze, private_root, seeds_dir, load_date)
    merge_eval_attempts(spark, attempts, paths.table("silver", "eval_attempts"))

    changes = read_deployment_changes(spark, f"{seeds_dir}/deployment_changes.csv")
    apply_scd2(spark, changes, paths.table("silver", "model_deployment"), seeds_dir)

    return build_gold(spark, paths, seeds_dir)


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
    run, if any check fails; the reject table and gold stay in place as the record of the run.
    """
    export = _read_export(spark, cost_export_path)

    _build(spark, paths, seeds_dir, private_root, export, load_date)
    before = gold_fingerprint(spark, paths)
    row_counts = _build(spark, paths, seeds_dir, private_root, export, load_date)
    after = gold_fingerprint(spark, paths)

    checks = [
        check_deployment_resolution(spark, paths),
        check_cost_totals(spark, paths, export),
        check_published_figures(spark, paths, f"{seeds_dir}/published_figures.csv"),
        check_rerun_unchanged(before, after),
    ]
    failed = [c for c in checks if not c.passed]
    if failed:
        raise QualityCheckFailed(failed)
    return RunSummary(load_date=load_date, row_counts=row_counts, checks=checks, fingerprint=after)
