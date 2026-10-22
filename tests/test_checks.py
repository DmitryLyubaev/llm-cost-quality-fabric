import csv
import gzip
import shutil
from pathlib import Path
from datetime import UTC, date, datetime

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lakehouse import checks
from lakehouse.answers import cost_per_project, cost_per_result, quality_across_models
from lakehouse.checks import (
    QUERIES,
    CheckResult,
    QualityCheckFailed,
    check_cost_totals,
    check_deployment_resolution,
    check_published_figures,
    check_rerun_unchanged,
)
from lakehouse.gold.fingerprint import gold_fingerprint, table_fingerprint
from lakehouse.paths import LakePaths
from lakehouse.attribution import load_rg_rules, load_session_windows
from lakehouse.silver.costs import normalise_costs, read_cost_export, replace_month

from lake_helpers import COST_EXPORT_ROOT, FIGURES, FIGURES_DIR, SEEDS, copy_lake, cost_export_maker


@pytest.fixture
def good(loaded_lake):
    return loaded_lake


def _copy_lake(good, tmp_path) -> LakePaths:
    return copy_lake(good[0], tmp_path / "lake")


def _export(spark, root=COST_EXPORT_ROOT):
    """(export rows, manifest row counts per month folder) of an export tree."""
    return read_cost_export(spark, str(root))


def test_each_check_passes_on_good_data(spark, good):
    paths, _, summary = good
    export, expected = _export(spark)

    results = [
        check_deployment_resolution(spark, paths),
        check_cost_totals(spark, paths, export, expected),
        check_published_figures(spark, paths, FIGURES_DIR),
        check_rerun_unchanged(summary.fingerprint, gold_fingerprint(spark, paths)),
    ]

    assert all(isinstance(r, CheckResult) for r in results)
    assert [r.passed for r in results] == [True] * 4, [r for r in results if not r.passed]
    assert len({r.name for r in results}) == 4


def test_deployment_resolution_fails(spark, good, tmp_path):
    """A result timestamp before every version lands in the reject table and fails the check."""
    paths = _copy_lake(good, tmp_path)
    DeltaTable.forPath(spark, paths.table("silver", "eval_attempts")).update(
        (F.col("study") == "p1-three-arm") & (F.col("arm") == "Z") & (F.col("item_id") == "gq-001"),
        {"started_at": F.lit(datetime(2026, 1, 1, tzinfo=UTC)).cast("timestamp")},
    )
    from lakehouse.gold.build import build_gold

    build_gold(spark, paths, SEEDS)

    result = check_deployment_resolution(spark, paths)

    assert not result.passed
    assert "1" in result.detail and "p1-three-arm" in result.detail


def test_cost_totals_fails(spark, good, tmp_path):
    """One cost row removed from silver: the month's total no longer equals the export's."""
    paths = _copy_lake(good, tmp_path)
    DeltaTable.forPath(spark, paths.table("silver", "daily_cost")).delete(
        F.col("meter_id") == "meter-apim"
    )

    result = check_cost_totals(spark, paths, *_export(spark))

    assert not result.passed
    assert "2026-10" in result.detail
    assert "20261001-20261031" in result.detail  # the row count is checked too


def test_cost_totals_fails_when_month_missing_from_silver(spark, good, tmp_path):
    paths = _copy_lake(good, tmp_path)
    DeltaTable.forPath(spark, paths.table("silver", "daily_cost")).delete("billing_month = '2026-10'")

    assert not check_cost_totals(spark, paths, *_export(spark)).passed


def test_cost_totals_ignores_months_not_in_the_export(spark, good, tmp_path):
    """Silver keeps other months from earlier loads; only the export's months are compared."""
    paths = _copy_lake(good, tmp_path)
    silver = paths.table("silver", "daily_cost")
    other = (
        spark.read.format("delta").load(silver).limit(1)
        .withColumn("billing_month", F.lit("2026-08"))
        .withColumn("cost_date", F.lit(date(2026, 8, 5)))
    )
    other.write.format("delta").mode("append").save(silver)

    assert check_cost_totals(spark, paths, *_export(spark)).passed


def _silver_from(spark, paths, export):
    """Silver costs built from an export frame, month by month, as run_load does."""
    costs = normalise_costs(spark, export, load_rg_rules(SEEDS), load_session_windows(SEEDS))
    for month in sorted(r[0] for r in costs.select("billing_month").distinct().collect()):
        replace_month(spark, costs.where(F.col("billing_month") == month), paths.table("silver", "daily_cost"), month)


def test_manifest_row_count_mismatch_fails_check(spark, tmp_lake, tmp_path):
    """A part file that lost a row after the manifest counted it: the totals still agree between
    the export and silver, but silver holds fewer rows than the manifest says the run wrote."""
    root = tmp_path / "export"
    shutil.copytree(COST_EXPORT_ROOT, root)
    part = root / "portfolio-daily" / cost_export_maker.OCT / cost_export_maker.RUN_B / "part1.csv.gz"
    lines = gzip.decompress(part.read_bytes()).decode("utf-8").splitlines(keepends=True)
    part.write_bytes(gzip.compress("".join(lines[:-1]).encode("utf-8"), mtime=0))  # drop the last row
    export, expected = _export(spark, root)
    assert expected["20261001-20261031"] == 11 and export.count() == 14
    _silver_from(spark, tmp_lake, export)

    result = check_cost_totals(spark, tmp_lake, export, expected)

    assert not result.passed
    assert "20261001-20261031" in result.detail
    assert "manifest 11 row(s), silver 10" in result.detail
    assert "20260901-20260930" not in result.detail  # September agrees


def test_manifest_row_counts_agree_when_silver_is_complete(spark, tmp_lake):
    export, expected = _export(spark)
    _silver_from(spark, tmp_lake, export)

    result = check_cost_totals(spark, tmp_lake, export, expected)

    assert result.passed, result.detail
    assert "2 month(s)" in result.detail


def _figures(tmp_path, **changes):
    with open(FIGURES, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row.update(changes.get(row["figure_id"], {}))
    out = tmp_path / "figures" / "published_figures.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return str(out.parent)


def test_published_figures_fails(spark, good, tmp_path):
    """Expected P3 success one higher than gold has: the published-figures check fails."""
    paths = good[0]
    figures = _figures(tmp_path, p3_gpt_success={"expected": "2"})

    result = check_published_figures(spark, paths, figures)

    assert not result.passed
    assert "p3_gpt_success" in result.detail
    assert "p3_claude_success" not in result.detail


def test_published_figures_fails_when_gold_row_changes(spark, good, tmp_path):
    paths = _copy_lake(good, tmp_path)
    DeltaTable.forPath(spark, paths.table("gold", "fact_eval_attempt")).update(
        (F.col("study") == "p3-agent") & (F.col("arm") == "gpt") & F.col("success"),
        {"success": F.lit(False)},
    )

    result = check_published_figures(spark, paths, FIGURES_DIR)

    assert not result.passed
    assert "p3_gpt_success" in result.detail


def test_published_figures_p3_denominator_is_checked(spark, good, tmp_path):
    """The fixture's P3 arms each hold two scored rows; a published 3 fails the check."""
    paths = good[0]
    assert check_published_figures(spark, paths, FIGURES_DIR).passed

    result = check_published_figures(spark, paths, _figures(tmp_path, p3_n_per_arm={"expected": "3"}))

    assert not result.passed and "p3_n_per_arm" in result.detail


def test_published_figures_unknown_query_is_an_error(spark, good, tmp_path):
    figures = _figures(tmp_path, p3_gpt_success={"query": "select 1"})

    with pytest.raises(ValueError, match="select 1"):
        check_published_figures(spark, good[0], figures)


def test_published_figures_seed_is_well_formed():
    with open(Path(SEEDS) / "published_figures.csv", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = {r["figure_id"]: r for r in reader}
        assert reader.fieldnames == ["figure_id", "study", "query", "expected", "tolerance", "source"]
    assert set(rows) == {
        "p1_total_cost_usd", "p3_gpt_success", "p3_claude_success", "p3_n_per_arm", "p2_n_per_arm",
        "p2_e3_minus_e1_top1",
    }
    assert all(r["query"] in QUERIES for r in rows.values())
    assert all(r["source"] for r in rows.values())
    assert (float(rows["p1_total_cost_usd"]["expected"]), float(rows["p1_total_cost_usd"]["tolerance"])) == (2.7055, 0.0001)
    assert float(rows["p3_gpt_success"]["expected"]) == 59
    assert float(rows["p3_claude_success"]["expected"]) == 57
    assert float(rows["p2_n_per_arm"]["expected"]) == 229
    assert (rows["p3_n_per_arm"]["study"], float(rows["p3_n_per_arm"]["expected"])) == ("p3-agent", 60)
    assert float(rows["p3_n_per_arm"]["tolerance"]) == 0
    assert (float(rows["p2_e3_minus_e1_top1"]["expected"]), float(rows["p2_e3_minus_e1_top1"]["tolerance"])) == (0.271, 0.0005)
    # the fixture's figures use the same ids and queries
    with open(FIGURES, encoding="utf-8", newline="") as f:
        fixture = {r["figure_id"]: r for r in csv.DictReader(f)}
    assert {k: (v["study"], v["query"]) for k, v in fixture.items()} == {
        k: (v["study"], v["query"]) for k, v in rows.items()
    }


def test_rerun_unchanged_fails_when_a_gold_row_is_altered(spark, good, tmp_path):
    paths = _copy_lake(good, tmp_path)
    before = gold_fingerprint(spark, paths)
    DeltaTable.forPath(spark, paths.table("gold", "dim_project")).update(
        F.col("project") == "p1", {"name": F.lit("Renamed")}
    )
    after = gold_fingerprint(spark, paths)

    result = check_rerun_unchanged(before, after)

    assert not result.passed
    assert "dim_project" in result.detail
    assert "fact_eval_attempt" not in result.detail


def test_rerun_unchanged_fails_on_missing_or_extra_tables():
    assert not check_rerun_unchanged({"a": "1"}, {"a": "1", "b": "2"}).passed
    assert not check_rerun_unchanged({"a": "1", "b": "2"}, {"a": "1"}).passed
    assert check_rerun_unchanged({"a": "1"}, {"a": "1"}).passed


def test_quality_check_failed_names_the_checks():
    failed = [CheckResult("cost_totals", False, "2026-10 differs"), CheckResult("x", False, "y")]

    error = QualityCheckFailed(failed)

    assert error.failed == failed
    assert "cost_totals" in str(error) and "2026-10 differs" in str(error)


# --- R21: the fingerprint sees microseconds, in UTC -------------------------------------------


def _timestamp_table(spark, path, moment):
    spark.createDataFrame([(1, moment)], "id int, at timestamp").write.format("delta").mode(
        "overwrite"
    ).save(path)


def test_fingerprint_sees_one_microsecond(spark, tmp_path):
    path = str(tmp_path / "t")
    _timestamp_table(spark, path, datetime(2026, 10, 2, 3, 0, 9, 123456, tzinfo=UTC))
    first = table_fingerprint(spark, path)
    _timestamp_table(spark, path, datetime(2026, 10, 2, 3, 0, 9, 123457, tzinfo=UTC))

    assert table_fingerprint(spark, path) != first


def test_fingerprint_does_not_depend_on_session_time_zone(spark, tmp_path):
    path = str(tmp_path / "t")
    _timestamp_table(spark, path, datetime(2026, 10, 2, 3, 0, 9, 123456, tzinfo=UTC))
    utc = table_fingerprint(spark, path)
    spark.conf.set("spark.sql.session.timeZone", "Australia/Brisbane")
    try:
        brisbane = table_fingerprint(spark, path)
    finally:
        spark.conf.set("spark.sql.session.timeZone", "UTC")

    assert brisbane == utc


# --- answers ---------------------------------------------------------------------------------


def test_answers_include_untagged(spark, good):
    paths = good[0]

    rows = {(r["project"], r["billing_currency"]): r["total"] for r in cost_per_project(spark, paths).collect()}

    assert ("untagged", "AUD") in rows
    assert ("releaselens-shared", "AUD") in rows
    assert rows[("untagged", "AUD")] > 0


def test_answers_untagged_present_with_zero_when_empty(spark, good, tmp_path):
    paths = _copy_lake(good, tmp_path)
    DeltaTable.forPath(spark, paths.table("gold", "fact_daily_cost")).delete("1 = 1")

    rows = {r["project"]: (r["billing_currency"], r["total"]) for r in cost_per_project(spark, paths).collect()}

    assert rows == {"releaselens-shared": (None, 0.0), "untagged": (None, 0.0)}


def test_cost_per_project_totals_match_gold(spark, good):
    paths = good[0]
    total = sum(r["total"] for r in cost_per_project(spark, paths).collect())
    gold_total = (
        spark.read.format("delta").load(paths.table("gold", "fact_daily_cost")).agg(F.sum("cost")).first()[0]
    )

    assert total == pytest.approx(gold_total)


def test_cost_per_result_never_mixes_currencies(spark, good):
    paths = good[0]
    frame = cost_per_result(spark, paths)

    assert {
        "project", "results", "estimated_usd_per_result", "estimated_currency",
        "actual_per_result", "actual_currency",
    } <= set(frame.columns)
    rows = {r["project"]: r for r in frame.collect()}
    assert {"p1", "p2", "p3"} <= set(rows)
    assert all(r["estimated_currency"] == "USD" for r in rows.values())
    assert rows["p1"]["actual_currency"] == "AUD"
    assert rows["p1"]["results"] == 6
    # estimated: fixture P1 total 0.1 USD over 6 results; actual: attributed AUD spend over 6
    assert rows["p1"]["estimated_usd_per_result"] == pytest.approx(0.1 / 6)
    assert rows["p1"]["actual_per_result"] == pytest.approx(rows["p1"]["actual_total"] / 6)
    # nothing converts one into the other
    assert rows["p1"]["actual_per_result"] != pytest.approx(rows["p1"]["estimated_usd_per_result"])


def test_cost_per_result_counts_unpriced_results_and_withholds_the_estimate(spark, good, tmp_path):
    """A scored result with no price (rate_missing) makes the estimate unknown, not a partial sum."""
    before = {r["project"]: r for r in cost_per_result(spark, good[0]).collect()}
    assert all(r["unpriced_results"] == 0 for r in before.values())  # no_model rows are not unpriced
    assert before["p3"]["estimated_usd_total"] is not None

    paths = _copy_lake(good, tmp_path)
    facts = spark.read.format("delta").load(paths.table("gold", "fact_eval_attempt"))
    victim = facts.where((F.col("study") == "p3-agent") & (F.col("attempt_kind") == "scored")).first()
    DeltaTable.forPath(spark, paths.table("gold", "fact_eval_attempt")).update(
        (F.col("study") == "p3-agent") & (F.col("attempt_kind") == "scored")
        & (F.col("arm") == victim["arm"]) & (F.col("item_id") == victim["item_id"]),
        {"cost_usd": F.lit(None).cast("double"), "cost_basis": F.lit("rate_missing")},
    )

    rows = {r["project"]: r for r in cost_per_result(spark, paths).collect()}

    assert rows["p3"]["unpriced_results"] == 1
    assert rows["p3"]["estimated_usd_total"] is None and rows["p3"]["estimated_usd_per_result"] is None
    assert rows["p3"]["results"] == before["p3"]["results"]
    assert rows["p3"]["actual_total"] == before["p3"]["actual_total"]
    # other projects keep their estimates
    assert rows["p1"]["estimated_usd_total"] == before["p1"]["estimated_usd_total"]
    assert rows["p1"]["unpriced_results"] == 0


def test_quality_across_models_scored_rows_only(spark, good):
    paths = good[0]
    frame = quality_across_models(spark, paths)
    rows = {(r["study"], r["arm"]): r for r in frame.collect()}

    assert {"study", "arm", "model", "model_version", "n"} <= set(frame.columns)
    assert rows[("p2-retrieval", "E3")]["n"] == 2  # the determinism repeat is not counted
    assert rows[("p2-retrieval", "E3")]["top1_rate"] == 1.0
    assert rows[("p2-retrieval", "E1")]["top1_rate"] == 0.5
    assert rows[("p2-retrieval", "E1")]["model"] == "no-model"
    assert rows[("p3-agent", "gpt")]["n"] == 2
    assert rows[("p1-three-arm", "A")]["model"] == "claude-sonnet-5"
    assert rows[("p1-three-arm", "A")]["model_version"] == "claude-sonnet-5"
    assert rows[("p1-three-arm", "Z")]["model_version"] == "2025-04-14"


def test_checks_module_exports():
    assert {"CheckResult", "QualityCheckFailed", "QUERIES"} <= set(dir(checks))
