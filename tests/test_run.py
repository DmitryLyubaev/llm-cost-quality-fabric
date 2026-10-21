import os
import shutil
from pathlib import Path

import pytest
from delta.tables import DeltaTable

from lake_helpers import COST_FIXTURE, make_lake, run
from lakehouse.checks import QualityCheckFailed
from lakehouse.gold.build import GOLD_TABLES, REJECT_NAME
from lakehouse.gold.fingerprint import gold_fingerprint
from lakehouse.paths import LakePaths
from lakehouse.run import RunSummary


@pytest.fixture
def lake(tmp_path):
    return make_lake(tmp_path)


def _listing(folder):
    return sorted(str(p) for p in Path(folder).rglob("*"))


def _versions(spark, paths):
    tables = [paths.table("gold", name) for name in GOLD_TABLES] + [paths.table("reject", "eval_unresolved")]
    return {path: DeltaTable.forPath(spark, path).history(1).first()["version"] for path in tables}


def _copy(loaded_lake, tmp_path):
    """A copy of the loaded lake's tables (bronze included) to run against again."""
    paths, private, seeds, summary = loaded_lake
    target = tmp_path / "lake"
    shutil.copytree(paths.root, target)
    return LakePaths(str(target)), private, seeds, summary


def test_run_load_twice_is_identical(spark, loaded_lake, tmp_path):
    """The session's first run_load, then a second one on the same tree and date."""
    paths, private, seeds, first = _copy(loaded_lake, tmp_path)
    bronze_before = _listing(paths.bronze)
    private_before = sorted(os.listdir(private))

    second = run(spark, paths, private, seeds)

    assert isinstance(first, RunSummary)
    assert first.fingerprint == second.fingerprint == gold_fingerprint(spark, paths)
    assert first.row_counts == second.row_counts
    assert set(first.row_counts) == set(GOLD_TABLES) | {REJECT_NAME}
    assert first.row_counts[REJECT_NAME] == 0
    # 6 P1 + 13 P2 (12 scored, 1 repeat) + 5 P3 (4 scored, 1 superseded); 11 cost lines
    assert first.row_counts["fact_eval_attempt"] == 6 + 13 + 5
    assert first.row_counts["fact_daily_cost"] == 11
    # bronze and the private extracts are only read
    assert _listing(paths.bronze) == bronze_before and sorted(os.listdir(private)) == private_before
    assert [c.passed for c in second.checks] == [True] * 4
    assert {c.name for c in second.checks} == {
        "deployment_resolution", "cost_totals", "published_figures", "rerun_unchanged",
    }
    assert second.load_date.isoformat() == "2026-10-21"


def test_run_load_raises_on_failed_check(spark, lake):
    """A P1 outcome that predates every deployment version fails the resolution check."""
    paths, private, seeds = lake
    path = f"{private}/p1_outcomes.jsonl"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("2026-10-02T03:25:38.360009", "2026-01-01T00:00:00.000000", 1))

    with pytest.raises(QualityCheckFailed) as error:
        run(spark, paths, private, seeds)

    assert "deployment_resolution" in str(error.value)
    assert "deployment_resolution" in [r.name for r in error.value.failed]
    # the failed run is kept: the reject table names the result and gold was still written
    assert DeltaTable.isDeltaTable(spark, paths.table("reject", "eval_unresolved"))
    assert spark.read.format("delta").load(paths.table("reject", "eval_unresolved")).count() == 1


def test_missing_cost_export_fails_before_writing(spark, loaded_lake, tmp_path):
    """An empty or missing export raises FileNotFoundError and leaves gold untouched (Review Focus 5)."""
    paths, private, seeds, _ = _copy(loaded_lake, tmp_path)
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
            run(spark, paths, private, seeds, export=export)

    assert _versions(spark, paths) == versions
    assert spark.read.format("delta").load(paths.table("silver", "daily_cost")).count() == silver_cost


def test_missing_cost_export_writes_nothing_on_a_fresh_lake(spark, lake, tmp_path):
    paths, private, seeds = lake

    with pytest.raises(FileNotFoundError, match="nope.csv"):
        run(spark, paths, private, seeds, export=tmp_path / "nope.csv")

    assert not os.path.exists(paths.silver) and not os.path.exists(paths.gold)
    assert not os.path.exists(paths.reject)


def test_export_with_another_month_is_refused_without_writing(spark, lake, tmp_path):
    paths, private, seeds = lake
    other = tmp_path / "september.csv"
    other.write_text(COST_FIXTURE.read_text(encoding="utf-8").replace("2026-10-", "2026-09-"), encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match="2026-10"):
        run(spark, paths, private, seeds, export=other)

    assert not os.path.exists(paths.gold)
