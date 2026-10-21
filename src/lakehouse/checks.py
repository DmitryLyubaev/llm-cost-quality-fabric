"""The four quality checks of spec 3.7, each guarding one claim.

A check returns a `CheckResult`; it does not raise when the data is wrong (`run_load` raises
`QualityCheckFailed` once every check has run). A broken configuration, such as a published-figures
row naming a query that does not exist, is an error, not a failed check.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse.paths import LakePaths
from lakehouse.seeding import read_seed_rows
from lakehouse.silver.costs import month_range, normalise_costs

COST_TOLERANCE = 0.01  # in the billing currency (spec 3.7)
SCORED = "scored"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


class QualityCheckFailed(Exception):
    """One or more quality checks failed; `failed` holds their results."""

    def __init__(self, failed: list[CheckResult]):
        self.failed = list(failed)
        super().__init__(
            "quality check(s) failed: " + "; ".join(f"{r.name}: {r.detail}" for r in self.failed)
        )


# --- 1. every result resolves to exactly one deployment version -------------------------------


def check_deployment_resolution(spark: SparkSession, paths: LakePaths) -> CheckResult:
    """Fails if `reject.eval_unresolved` holds any row; the detail counts them per study and arm."""
    rejected = spark.read.format("delta").load(paths.table("reject", "eval_unresolved"))
    rows = (
        rejected.groupBy("study", "arm", "reason").count().orderBy("study", "arm", "reason").collect()
    )
    total = sum(r["count"] for r in rows)
    if total == 0:
        return CheckResult("deployment_resolution", True, "every result resolves to one version")
    parts = ", ".join(f"{r['study']}/{r['arm']} {r['reason']} x{r['count']}" for r in rows)
    return CheckResult("deployment_resolution", False, f"{total} unresolved result(s): {parts}")


# --- 2. each month's silver cost total equals the export's total ------------------------------


def _month_totals(df: DataFrame) -> dict[str, float]:
    rows = df.groupBy("billing_month").agg(F.sum("cost").alias("total")).collect()
    return {r["billing_month"]: r["total"] or 0.0 for r in rows}


def check_cost_totals(
    spark: SparkSession,
    paths: LakePaths,
    export_df: DataFrame,
    expected_rows: dict[str, int],
) -> CheckResult:
    """Per billing month in the export, silver's total must equal the export's within 0.01; and
    per month folder, silver's row count must equal the manifest's `dataRowCount`.

    `expected_rows` is `{YYYYMMDD-YYYYMMDD folder: dataRowCount}` from `read_cost_export`; silver
    rows are counted by `cost_date` falling in the folder's range. Months silver holds from earlier
    loads but the export lacks are not compared. A month the export has and silver lacks fails
    (silver total 0, row count 0).
    """
    # no rules: only the date and cost parsing are used, attribution is irrelevant here
    exported = _month_totals(normalise_costs(spark, export_df, [], []))
    silver_df = spark.read.format("delta").load(paths.table("silver", "daily_cost"))
    silver = _month_totals(silver_df)
    differing = [
        f"{month}: export {total:.4f}, silver {silver.get(month, 0.0):.4f}"
        for month, total in sorted(exported.items(), key=lambda item: str(item[0]))
        if abs(total - silver.get(month, 0.0)) > COST_TOLERANCE
    ]
    for folder, manifest_rows in sorted(expected_rows.items()):
        first, last = month_range(folder)
        silver_rows = silver_df.where(F.col("cost_date").between(F.lit(first), F.lit(last))).count()
        if silver_rows != manifest_rows:
            differing.append(f"month folder {folder}: manifest {manifest_rows} row(s), silver {silver_rows}")
    if differing:
        return CheckResult("cost_totals", False, "; ".join(differing))
    return CheckResult(
        "cost_totals",
        True,
        f"{len(exported)} month(s) agree within {COST_TOLERANCE}; "
        f"{len(expected_rows)} manifest row count(s) match",
    )


# --- 3. gold reproduces the published figures -------------------------------------------------
# Each query takes (gold fact_eval_attempt, gold dim_arm, study) and returns one number or None.
# The figures CSV names a query from this fixed set; it never carries SQL.


def _scored(facts: DataFrame, study: str) -> DataFrame:
    return facts.where((F.col("study") == study) & (F.col("attempt_kind") == SCORED))


def _total_cost_usd(facts: DataFrame, dim_arm: DataFrame, study: str) -> float | None:
    return _scored(facts, study).agg(F.sum("cost_usd")).first()[0]


def _success_count(arm: str) -> Callable[[DataFrame, DataFrame, str], float | None]:
    def query(facts: DataFrame, dim_arm: DataFrame, study: str) -> float | None:
        rows = _scored(facts, study).where((F.col("arm") == arm) & F.col("success"))
        return float(rows.count())

    return query


def _n_per_arm(facts: DataFrame, dim_arm: DataFrame, study: str) -> float | None:
    """The scored-row count every arm of the study shares; None if the arms differ."""
    arms = dim_arm.where(F.col("study") == study).select("arm")
    counts = _scored(facts, study).groupBy("arm").count()
    per_arm = arms.join(counts, "arm", "left").select(F.coalesce("count", F.lit(0)).alias("n")).collect()
    sizes = {r["n"] for r in per_arm}
    return float(sizes.pop()) if len(sizes) == 1 else None


def _top1_e3_minus_e1(facts: DataFrame, dim_arm: DataFrame, study: str) -> float | None:
    rows = (
        _scored(facts, study)
        .where(F.col("arm").isin("E1", "E3"))
        .groupBy("arm")
        .agg(F.avg(F.col("top1").cast("double")).alias("top1"))
        .collect()
    )
    means = {r["arm"]: r["top1"] for r in rows}
    if means.get("E1") is None or means.get("E3") is None:
        return None
    return means["E3"] - means["E1"]


QUERIES: dict[str, Callable[[DataFrame, DataFrame, str], float | None]] = {
    "total_cost_usd": _total_cost_usd,
    "success_count_gpt": _success_count("gpt"),
    "success_count_claude": _success_count("claude"),
    "n_per_arm": _n_per_arm,
    "top1_e3_minus_e1": _top1_e3_minus_e1,
}


def check_published_figures(
    spark: SparkSession, paths: LakePaths, seeds_dir: str, name: str = "published_figures.csv"
) -> CheckResult:
    """Every figure in the seed must be reproduced by gold within its tolerance."""
    figures = read_seed_rows(seeds_dir, name)
    facts = spark.read.format("delta").load(paths.table("gold", "fact_eval_attempt"))
    dim_arm = spark.read.format("delta").load(paths.table("gold", "dim_arm"))

    mismatches = []
    for figure in figures:
        query = QUERIES.get(figure["query"])
        if query is None:
            raise ValueError(
                f"published figure {figure['figure_id']} names unknown query {figure['query']!r}; "
                f"known queries: {sorted(QUERIES)}"
            )
        expected = float(figure["expected"])
        tolerance = float(figure["tolerance"])
        actual = query(facts, dim_arm, figure["study"])
        if actual is None or abs(actual - expected) > tolerance:
            shown = "no value" if actual is None else f"{actual:.6g}"
            mismatches.append(f"{figure['figure_id']}: gold {shown}, published {expected:.6g}")
    if mismatches:
        return CheckResult("published_figures", False, "; ".join(mismatches))
    return CheckResult("published_figures", True, f"{len(figures)} figure(s) match")


# --- 4. a re-run leaves every gold table unchanged --------------------------------------------


def check_rerun_unchanged(before: dict[str, str], after: dict[str, str]) -> CheckResult:
    """Two `gold_fingerprint` results must name the same tables with the same content hashes."""
    changed = sorted(t for t in before.keys() & after.keys() if before[t] != after[t])
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    problems = []
    if changed:
        problems.append("content changed: " + ", ".join(changed))
    if added:
        problems.append("new: " + ", ".join(added))
    if removed:
        problems.append("missing: " + ", ".join(removed))
    if problems:
        return CheckResult("rerun_unchanged", False, "; ".join(problems))
    return CheckResult("rerun_unchanged", True, f"{len(after)} table(s) identical")
