import json
import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lake_helpers import copy_lake, cost_export_maker, make_lake, run
from lakehouse.checks import QualityCheckFailed
from lakehouse.gold.build import GOLD_TABLES, REJECT_NAME, optimize_tables
from lakehouse.gold.fingerprint import gold_fingerprint, table_fingerprint
from lakehouse.run import RunSummary, inputs_digest
import lakehouse.run as run_module
from lakehouse.silver.costs import ExportRun


@pytest.fixture
def lake(tmp_path):
    return make_lake(tmp_path)


def _listing(folder):
    return sorted(str(p) for p in Path(folder).rglob("*"))


def _log(spark, paths):
    rows = spark.read.format("delta").load(paths.table("silver", "run_log")).orderBy("recorded_at").collect()
    return [(r["load_date"], json.loads(r["fingerprint"])) for r in rows]


def _log_rows(spark, paths):
    rows = spark.read.format("delta").load(paths.table("silver", "run_log")).orderBy("recorded_at").collect()
    return [r.asDict() for r in rows]


def _rerun_detail(summary):
    return [c for c in summary.checks if c.name == "rerun_unchanged"][0]


def _export_run(run_id):
    return ExportRun("20261001-20261031", run_id, datetime(2026, 10, 21, tzinfo=UTC), (), 1)


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
    # 6 P1 + 13 P2 (12 scored, 1 repeat) + 5 P3 (4 scored, 1 superseded); 15 cost lines
    # (11 in October from the latest run, 4 in September)
    assert first.row_counts["fact_eval_attempt"] == 6 + 13 + 5
    assert first.row_counts["fact_daily_cost"] == 15
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
            run(spark, paths, seeds, export=paths.files_root + "/nope")
        assert spark.conf.get("spark.sql.ansi.enabled") == "true"
    finally:
        spark.conf.set("spark.sql.ansi.enabled", original)


def test_export_spanning_two_months_loads_both(spark, lake):
    """Every month's latest run is replaced, and each month's silver total matches the export's."""
    paths, seeds = lake
    maker = cost_export_maker
    expected = {
        "2026-10": sum(float(r["cost"]) for r in maker.OCTOBER_ROWS),  # the latest run, not the stale one
        "2026-09": sum(float(r["cost"]) for r in maker.SEPTEMBER_ROWS),
    }

    summary = run(spark, paths, seeds)

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
    """No manifest, or manifests that list no rows, raise FileNotFoundError and leave gold untouched
    (Review Focus 5)."""
    paths, seeds, _ = _copy(loaded_lake, tmp_path)
    versions = _versions(spark, paths)
    silver_cost = spark.read.format("delta").load(paths.table("silver", "daily_cost")).count()

    no_rows = tmp_path / "no_rows"
    cost_export_maker.write_run(no_rows, cost_export_maker.OCT, cost_export_maker.RUN_B,
                                "2026-10-21T03:30:09Z", [])
    no_manifest = tmp_path / "no_manifest"
    no_manifest.mkdir()
    (no_manifest / "part0.csv.gz").write_bytes(b"")
    missing = tmp_path / "does_not_exist"

    for export in (no_rows, no_manifest, missing):
        with pytest.raises(FileNotFoundError, match=export.name):
            run(spark, paths, seeds, export=export)

    assert _versions(spark, paths) == versions
    assert spark.read.format("delta").load(paths.table("silver", "daily_cost")).count() == silver_cost


def test_missing_cost_export_writes_nothing_on_a_fresh_lake(spark, lake, tmp_path):
    paths, seeds = lake

    with pytest.raises(FileNotFoundError, match="nope"):
        run(spark, paths, seeds, export=tmp_path / "nope")

    assert not os.path.exists(paths.tables_root)


def test_log_row_records_the_inputs_digest_and_status(spark, loaded_lake):
    paths, seeds, _ = loaded_lake
    (row,) = _log_rows(spark, paths)

    assert row["status"] == "passed"
    assert row["inputs_digest"] == inputs_digest(
        seeds,
        json.loads(Path(seeds, "ingest_manifest.json").read_text(encoding="utf-8")),
        [_export_run(cost_export_maker.RUN_B), _export_run(cost_export_maker.RUN_C)],
    )
    assert len(row["inputs_digest"]) == 16


def test_same_inputs_still_compared(spark, loaded_lake, tmp_path):
    """Same seeds, manifest and export runs: the gold fingerprint is compared and a difference fails."""
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
    assert len(_log_rows(spark, paths)) == 1


def test_changed_seed_is_new_baseline(spark, loaded_lake, tmp_path):
    """A seed edit legitimately changes gold; the re-run check must not treat that as drift."""
    paths, seeds_src, first = _copy(loaded_lake, tmp_path)
    seeds = tmp_path / "seeds_changed"
    shutil.copytree(seeds_src, seeds)
    live = seeds / "live_checks.csv"
    text = live.read_text(encoding="utf-8")
    assert ",failover,pass,45,requests," in text
    live.write_text(text.replace(",failover,pass,45,requests,", ",failover,pass,46,requests,", 1),
                    encoding="utf-8", newline="")

    second = run(spark, paths, str(seeds))

    detail = _rerun_detail(second)
    assert detail.passed and detail.detail == "new baseline: inputs changed"
    assert second.fingerprint != first.fingerprint
    rows = _log_rows(spark, paths)
    assert len(rows) == 2 and rows[0]["inputs_digest"] != rows[1]["inputs_digest"]

    # and the new baseline is what the next run is compared with
    third = run(spark, paths, str(seeds))
    assert "identical" in _rerun_detail(third).detail


def test_digest_changes_with_export_run(tmp_path):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "a.csv").write_bytes(b"x,y\n1,2\n")
    manifest = {"owner": "someone", "sources": []}
    runs = [_export_run("run-1")]

    base = inputs_digest(str(seeds), manifest, runs)

    assert base == inputs_digest(str(seeds), manifest, list(runs))
    assert base != inputs_digest(str(seeds), manifest, [_export_run("run-2")])
    assert base != inputs_digest(str(seeds), manifest, runs + [_export_run("run-0")])
    assert base != inputs_digest(str(seeds), {**manifest, "owner": "other"}, runs)
    assert inputs_digest(str(seeds), manifest, [_export_run("b"), _export_run("a")]) == inputs_digest(
        str(seeds), manifest, [_export_run("a"), _export_run("b")]
    )
    (seeds / "a.csv").write_bytes(b"x,y\n1,3\n")
    assert base != inputs_digest(str(seeds), manifest, runs)


def _digest_inputs(tmp_path):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "a.csv").write_bytes(b"x,y\n1,2\n")
    return str(seeds), {"owner": "someone", "sources": []}, [_export_run("run-1")]


def test_digest_changes_when_a_module_source_changes_without_a_record(tmp_path, monkeypatch):
    """An editable install has no RECORD listing the package files: the sources are hashed."""
    package = tmp_path / "pkg" / "lakehouse"
    shutil.copytree(Path(run_module.__file__).parent, package)
    monkeypatch.setattr(run_module, "_installed_record", lambda: None)
    monkeypatch.setattr(run_module, "_package_dir", lambda: package)
    seeds, manifest, runs = _digest_inputs(tmp_path)

    base = inputs_digest(seeds, manifest, runs)
    assert base == inputs_digest(seeds, manifest, runs)
    module = package / "silver" / "costs.py"
    module.write_bytes(module.read_bytes() + b"\n# changed\n")

    assert base != inputs_digest(seeds, manifest, runs)


def test_digest_follows_the_installed_record(tmp_path, monkeypatch):
    """An installed wheel's RECORD carries every file's hash, so it stands for the code."""
    seeds, manifest, runs = _digest_inputs(tmp_path)
    record = "lakehouse/__init__.py,sha256=aaa,10\nlakehouse/run.py,sha256=bbb,20\n"
    monkeypatch.setattr(run_module, "_installed_record", lambda: record)
    base = inputs_digest(seeds, manifest, runs)

    monkeypatch.setattr(run_module, "_installed_record", lambda: record.replace("bbb", "ccc"))

    assert base != inputs_digest(seeds, manifest, runs)


def test_digest_changes_when_a_seed_is_renamed(tmp_path):
    seeds, manifest, runs = _digest_inputs(tmp_path)
    base = inputs_digest(seeds, manifest, runs)

    (Path(seeds) / "a.csv").rename(Path(seeds) / "b.csv")

    assert base != inputs_digest(seeds, manifest, runs)


def test_digest_separates_its_components(tmp_path):
    """Moving bytes from the end of one seed to the start of the next changes the digest."""
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "a.csv").write_bytes(b"x,y\n")
    (seeds / "b.csv").write_bytes(b"1,2\n")
    base = inputs_digest(str(seeds), {}, [])

    (seeds / "a.csv").write_bytes(b"x,y\n1,2\n")
    (seeds / "b.csv").write_bytes(b"")

    assert base != inputs_digest(str(seeds), {}, [])


def test_run_log_without_the_new_columns_still_works(spark, loaded_lake, tmp_path):
    """An older log (no inputs_digest or status) is read as null digest: a new baseline, never a match;
    the earlier-date guard still counts its rows."""
    paths, seeds, first = _copy(loaded_lake, tmp_path)
    log = paths.table("silver", "run_log")
    legacy = (
        spark.read.format("delta").load(log).select("load_date", "fingerprint", "recorded_at")
    )
    legacy.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(log)
    assert "inputs_digest" not in spark.read.format("delta").load(log).columns

    with pytest.raises(ValueError, match="earlier"):
        run(spark, paths, seeds, load_date=date(2026, 10, 20))
    second = run(spark, paths, seeds)

    assert _rerun_detail(second).detail == "new baseline: inputs changed"
    rows = _log_rows(spark, paths)
    assert [r["inputs_digest"] is None for r in rows] == [True, False]
    assert [r["status"] for r in rows] == [None, "passed"]


def test_earlier_date_guard_counts_only_passed_rows(spark, loaded_lake, tmp_path):
    paths, seeds, _ = _copy(loaded_lake, tmp_path)
    log = paths.table("silver", "run_log")
    spark.sql(
        f"INSERT INTO delta.`{log}` VALUES (DATE'2026-10-25', '{{}}', TIMESTAMP'2026-10-25 00:00:00', 'abc', 'failed')"
    )

    run(spark, paths, seeds)  # 2026-10-21 is not earlier than the latest *passed* date


def _parquet_files(spark, path):
    return DeltaTable.forPath(spark, path).detail().first()["numFiles"]


def _latest_operation(spark, path):
    return DeltaTable.forPath(spark, path).history(1).first()["operation"]


def test_optimize_tables_compacts_small_files_and_keeps_the_content(spark, tmp_path):
    """OPTIMIZE rewrites files, not rows: the content fingerprint is the same before and after.
    (A table of one file has nothing to compact and gets no OPTIMIZE entry, so this one is built
    from several appends, the way the daily MERGEs fragment a table.)"""
    path = str(tmp_path / "fragmented")
    for n in range(5):
        spark.range(n * 10, n * 10 + 10).write.format("delta").mode("append").save(path)
    files_before = _parquet_files(spark, path)
    assert files_before >= 5
    before = table_fingerprint(spark, path)

    optimize_tables(spark, [path])

    assert _latest_operation(spark, path) == "OPTIMIZE"
    files_after = _parquet_files(spark, path)
    assert 1 <= files_after < files_before  # fewer files; how many depends on the target file size
    assert table_fingerprint(spark, path) == before


def test_run_load_compacts_every_gold_table_before_fingerprinting(spark, loaded_lake, tmp_path, monkeypatch):
    """Direct Lake on OneLake errors past 1,000 Parquet files per table, and the daily MERGEs keep
    adding files, so each load compacts the gold tables, once they are built and before the
    fingerprint is taken. The fingerprint is content-based, so the re-run check is unchanged."""
    paths, seeds, first = _copy(loaded_lake, tmp_path)
    calls = []
    events = []  # OPTIMIZE and the fingerprint, in the order run_load makes them
    real_optimize = run_module.optimize_tables
    real_fingerprint = run_module.gold_fingerprint

    def optimize_spy(spark_session, table_paths):
        assert all(DeltaTable.isDeltaTable(spark_session, p) for p in table_paths)
        calls.append(list(table_paths))
        events.append("optimize")
        real_optimize(spark_session, table_paths)

    def fingerprint_spy(spark_session, lake_paths):
        events.append("fingerprint")
        return real_fingerprint(spark_session, lake_paths)

    monkeypatch.setattr(run_module, "optimize_tables", optimize_spy)
    monkeypatch.setattr(run_module, "gold_fingerprint", fingerprint_spy)

    second = run(spark, paths, seeds)

    assert events == ["optimize", "fingerprint"]
    assert calls == [[paths.table("gold", name) for name in GOLD_TABLES]]
    assert "identical" in _rerun_detail(second).detail
    assert second.fingerprint == first.fingerprint == gold_fingerprint(spark, paths)
