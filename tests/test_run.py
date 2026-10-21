import csv
import json
import os
import shutil
from datetime import date
from pathlib import Path

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lake_helpers import COST_FIXTURE, copy_lake, make_lake, run
from lakehouse.checks import QualityCheckFailed
from lakehouse.gold.build import GOLD_TABLES, REJECT_NAME
from lakehouse.gold.fingerprint import gold_fingerprint
from lakehouse.run import RunSummary


@pytest.fixture
def lake(tmp_path):
    return make_lake(tmp_path)


def _listing(folder):
    return sorted(str(p) for p in Path(folder).rglob("*"))


def _log(spark, paths):
    rows = spark.read.format("delta").load(paths.table("silver", "run_log")).orderBy("recorded_at").collect()
    return [(r["load_date"], json.loads(r["fingerprint"])) for r in rows]


def _versions(spark, paths):
    tables = [paths.table("gold", name) for name in GOLD_TABLES] + [paths.table("reject", "eval_unresolved")]
    return {path: DeltaTable.forPath(spark, path).history(1).first()["version"] for path in tables}


def _copy(loaded_lake, tmp_path):
    """A copy of the loaded lake's tables (bronze included) to run against again."""
    paths, seeds, summary = loaded_lake
    return copy_lake(paths, tmp_path / "lake"), seeds, summary


def test_run_load_twice_is_identical(spark, loaded_lake, tmp_path):
    """The session's first run_load, then a second one on the same tree and date."""
    paths, seeds, first = _copy(loaded_lake, tmp_path)
    bronze_before = _listing(paths.bronze)
    private_before = sorted(os.listdir(paths.private))

    second = run(spark, paths, seeds)

    assert [c for c in second.checks if c.name == "rerun_unchanged"][0].passed
    assert "first run" in [c for c in first.checks if c.name == "rerun_unchanged"][0].detail
    assert "identical" in [c for c in second.checks if c.name == "rerun_unchanged"][0].detail
    assert isinstance(first, RunSummary)
    assert first.fingerprint == second.fingerprint == gold_fingerprint(spark, paths)
    assert first.row_counts == second.row_counts
    assert set(first.row_counts) == set(GOLD_TABLES) | {REJECT_NAME}
    assert first.row_counts[REJECT_NAME] == 0
    # 6 P1 + 13 P2 (12 scored, 1 repeat) + 5 P3 (4 scored, 1 superseded); 11 cost lines
    assert first.row_counts["fact_eval_attempt"] == 6 + 13 + 5
    assert first.row_counts["fact_daily_cost"] == 11
    # bronze and the private extracts are only read
    assert _listing(paths.bronze) == bronze_before and sorted(os.listdir(paths.private)) == private_before
    assert [c.passed for c in second.checks] == [True] * 4
    assert {c.name for c in second.checks} == {
        "deployment_resolution", "cost_totals", "published_figures", "rerun_unchanged",
    }
    assert second.load_date.isoformat() == "2026-10-21"
    assert _log(spark, paths) == [(date(2026, 10, 21), first.fingerprint)] * 2


def test_rerun_check_fails_when_the_rebuilt_gold_differs(spark, loaded_lake, tmp_path):
    """Between two runs of one date an extra row appears in silver (silver is only merged into,
    never pruned), so the rebuilt gold differs from the logged one: the re-run check fails."""
    paths, seeds, first = _copy(loaded_lake, tmp_path)
    silver = paths.table("silver", "eval_attempts")
    extra = (
        spark.read.format("delta").load(silver)
        .where("attempt_kind = 'determinism_repeat'").limit(1)
        .withColumn("pass_index", F.lit(7))
    )
    extra.write.format("delta").mode("append").save(silver)

    with pytest.raises(QualityCheckFailed) as error:
        run(spark, paths, seeds)

    assert [r.name for r in error.value.failed] == ["rerun_unchanged"]
    assert "fact_eval_attempt" in str(error.value)
    # the failed run did not become the baseline
    assert _log(spark, paths) == [(date(2026, 10, 21), first.fingerprint)]


def test_first_run_for_a_new_date_passes(spark, loaded_lake, tmp_path):
    paths, seeds, first = _copy(loaded_lake, tmp_path)
    bronze = Path(paths.bronze) / "public"
    shutil.copytree(bronze / "load_date=2026-10-21", bronze / "load_date=2026-10-22")

    second = run(spark, paths, seeds, load_date=date(2026, 10, 22))

    detail = [c for c in second.checks if c.name == "rerun_unchanged"][0]
    assert detail.passed and "first run" in detail.detail
    assert [d for d, _ in _log(spark, paths)] == [date(2026, 10, 21), date(2026, 10, 22)]


def test_earlier_load_date_is_refused_unless_allowed(spark, loaded_lake, tmp_path):
    """Re-running D1 after D2 would restate the published costs back to D1's export."""
    paths, seeds, _ = _copy(loaded_lake, tmp_path)
    bronze = Path(paths.bronze) / "public"
    shutil.copytree(bronze / "load_date=2026-10-21", bronze / "load_date=2026-10-22")
    run(spark, paths, seeds, load_date=date(2026, 10, 22))
    versions = _versions(spark, paths)
    logged = _log(spark, paths)

    with pytest.raises(ValueError, match=r"2026-10-21.*2026-10-22|2026-10-22.*2026-10-21"):
        run(spark, paths, seeds, load_date=date(2026, 10, 21))

    assert _versions(spark, paths) == versions and _log(spark, paths) == logged
    # the latest date may still be re-run
    run(spark, paths, seeds, load_date=date(2026, 10, 22))

    allowed = run(spark, paths, seeds, load_date=date(2026, 10, 21), allow_earlier=True)

    assert allowed.load_date == date(2026, 10, 21)
    assert [d for d, _ in _log(spark, paths)] == [
        date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 22), date(2026, 10, 21),
    ]


def test_run_load_is_the_same_whatever_the_session_time_zone(spark, loaded_lake, lake):
    """Fabric's session is not built by get_spark; run_load pins UTC itself."""
    paths, seeds = lake
    original = spark.conf.get("spark.sql.session.timeZone")
    spark.conf.set("spark.sql.session.timeZone", "Australia/Brisbane")
    try:
        summary = run(spark, paths, seeds)
    finally:
        spark.conf.set("spark.sql.session.timeZone", original)

    assert summary.fingerprint == loaded_lake[2].fingerprint


def test_run_load_turns_ansi_on(spark, lake):
    paths, seeds = lake
    original = spark.conf.get("spark.sql.ansi.enabled")
    spark.conf.set("spark.sql.ansi.enabled", "false")
    try:
        with pytest.raises(FileNotFoundError):  # the settings are made before the export is read
            run(spark, paths, seeds, export=paths.files_root + "/nope.csv")
        assert spark.conf.get("spark.sql.ansi.enabled") == "true"
    finally:
        spark.conf.set("spark.sql.ansi.enabled", original)


def test_export_spanning_two_months_loads_both(spark, lake, tmp_path):
    """Every month in the export is replaced, and each month's silver total matches the export's."""
    paths, seeds = lake
    text = COST_FIXTURE.read_text(encoding="utf-8")
    two_months = tmp_path / "two_months.csv"
    two_months.write_text(text.replace("2026-10-02,", "2026-09-28,").replace("2026-10-03,", "2026-09-30,"),
                          encoding="utf-8", newline="\n")
    expected: dict[str, float] = {}
    with open(two_months, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            expected[row["Date"][:7]] = expected.get(row["Date"][:7], 0.0) + float(row["Cost"])
    assert set(expected) == {"2026-09", "2026-10"}

    summary = run(spark, paths, seeds, export=two_months)

    totals = {
        r["billing_month"]: r["total"]
        for r in spark.read.format("delta").load(paths.table("silver", "daily_cost"))
        .groupBy("billing_month").agg(F.sum("cost").alias("total")).collect()
    }
    assert totals == pytest.approx(expected)
    assert [c.passed for c in summary.checks] == [True] * 4
    assert "2 month(s)" in [c for c in summary.checks if c.name == "cost_totals"][0].detail


def test_run_load_raises_on_failed_check(spark, lake):
    """A P1 outcome that predates every deployment version fails the resolution check."""
    paths, seeds = lake
    path = f"{paths.private}/p1_outcomes.jsonl"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("2026-10-02T03:25:38.360009", "2026-01-01T00:00:00.000000", 1))

    with pytest.raises(QualityCheckFailed) as error:
        run(spark, paths, seeds)

    assert "deployment_resolution" in str(error.value)
    assert "deployment_resolution" in [r.name for r in error.value.failed]
    # the failed run is kept: the reject table names the result and gold was still written
    assert DeltaTable.isDeltaTable(spark, paths.table("reject", "eval_unresolved"))
    assert spark.read.format("delta").load(paths.table("reject", "eval_unresolved")).count() == 1


def test_missing_cost_export_fails_before_writing(spark, loaded_lake, tmp_path):
    """An empty or missing export raises FileNotFoundError and leaves gold untouched (Review Focus 5)."""
    paths, seeds, _ = _copy(loaded_lake, tmp_path)
    versions = _versions(spark, paths)
    silver_cost = spark.read.format("delta").load(paths.table("silver", "daily_cost")).count()

    header = COST_FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    header_only = tmp_path / "header_only.csv"
    header_only.write_text(header + "\n", encoding="utf-8", newline="\n")
    zero_bytes = tmp_path / "zero_bytes.csv"
    zero_bytes.write_bytes(b"")
    missing = tmp_path / "does_not_exist.csv"

    for export in (header_only, zero_bytes, missing):
        with pytest.raises(FileNotFoundError, match=export.name):
            run(spark, paths, seeds, export=export)

    assert _versions(spark, paths) == versions
    assert spark.read.format("delta").load(paths.table("silver", "daily_cost")).count() == silver_cost


def test_missing_cost_export_writes_nothing_on_a_fresh_lake(spark, lake, tmp_path):
    paths, seeds = lake

    with pytest.raises(FileNotFoundError, match="nope.csv"):
        run(spark, paths, seeds, export=tmp_path / "nope.csv")

    assert not os.path.exists(paths.tables_root)
