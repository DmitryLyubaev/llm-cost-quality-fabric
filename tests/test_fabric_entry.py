import json
from datetime import date

import pytest

from lakehouse import fabric_entry
from lakehouse.checks import CheckResult, QualityCheckFailed
from lakehouse.paths import LakePaths
from lakehouse.run import RunSummary

CONTEXT = {"currentWorkspaceId": "ws-guid", "defaultLakehouseId": "lh-guid"}
ONELAKE = "abfss://ws-guid@onelake.dfs.fabric.microsoft.com/lh-guid"


def test_fabric_paths_from_context():
    paths = fabric_entry.fabric_paths(CONTEXT)
    assert paths == LakePaths(
        tables_root=f"{ONELAKE}/Tables",
        files_root=f"{ONELAKE}/Files",
        files_mount="/lakehouse/default/Files",
    )


@pytest.mark.parametrize("missing", ["currentWorkspaceId", "defaultLakehouseId"])
def test_missing_context_key_raises(missing):
    context = {k: v for k, v in CONTEXT.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        fabric_entry.fabric_paths(context)


def test_ingest_public_writes_under_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    calls = []

    def http_get(url):
        calls.append(url)
        return 200, b"body"

    count = fabric_entry.ingest_public("2026-10-20", CONTEXT, http_get=http_get)

    written = [p for p in (tmp_path / "bronze" / "public" / "load_date=2026-10-20").rglob("*") if p.is_file()]
    assert count == len(written) == len(calls) > 0
    assert all(p.read_bytes() == b"body" for p in written)


def test_ingest_public_non_200_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    with pytest.raises(RuntimeError, match="404"):
        fabric_entry.ingest_public("2026-10-20", CONTEXT, http_get=lambda url: (404, b""))


def test_default_http_get_returns_status_before_body(monkeypatch):
    import requests

    class Response:
        status_code = 404
        content = b"nope"

    seen = {}

    def fake_get(url, timeout):
        seen["args"] = (url, timeout)
        return Response()

    monkeypatch.setattr(requests, "get", fake_get)
    assert fabric_entry._requests_get("https://example.invalid/x") == (404, b"nope")
    assert seen["args"] == ("https://example.invalid/x", 60)


def test_get_active_spark_raises_without_session(monkeypatch):
    from pyspark.sql import SparkSession

    monkeypatch.setattr(SparkSession, "getActiveSession", staticmethod(lambda: None))
    with pytest.raises(RuntimeError, match="no active Spark session"):
        fabric_entry.get_active_spark()


def test_get_active_spark_returns_session(spark):
    assert fabric_entry.get_active_spark() is spark


def test_load_propagates_quality_failure(monkeypatch):
    def failing(*args, **kwargs):
        raise QualityCheckFailed([CheckResult("c", False, "bad")])

    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())
    monkeypatch.setattr(fabric_entry, "run_load", failing)
    with pytest.raises(QualityCheckFailed):
        fabric_entry.load("2026-10-20", CONTEXT)


def test_load_returns_jsonable_summary(monkeypatch):
    captured = {}
    spark_stub = object()

    def fake_run_load(spark, paths, seeds_dir, cost_export_root, load_date, **kwargs):
        captured.update(spark=spark, paths=paths, seeds_dir=seeds_dir, root=cost_export_root, date=load_date)
        return RunSummary(
            load_date=load_date,
            row_counts={"gold.fact": 3},
            checks=[CheckResult("rows", True, "ok")],
            fingerprint={"inputs": "abc"},
        )

    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: spark_stub)
    monkeypatch.setattr(fabric_entry, "run_load", fake_run_load)
    summary = fabric_entry.load("2026-10-20", CONTEXT)

    assert summary == {
        "load_date": "2026-10-20",
        "row_counts": {"gold.fact": 3},
        "checks": [{"name": "rows", "passed": True, "detail": "ok"}],
        "fingerprint": {"inputs": "abc"},
    }
    json.dumps(summary)
    assert captured["spark"] is spark_stub
    assert captured["date"] == date(2026, 10, 20)
    assert captured["root"] == f"{ONELAKE}/Files/cost_export"
    assert captured["seeds_dir"] == fabric_entry.SEEDS_DIR


def test_load_custom_cost_export_dir(monkeypatch):
    seen = {}

    def fake_run_load(spark, paths, seeds_dir, cost_export_root, load_date, **kwargs):
        seen["root"] = cost_export_root
        return RunSummary(load_date, {}, [], {})

    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())
    monkeypatch.setattr(fabric_entry, "run_load", fake_run_load)
    fabric_entry.load("2026-10-20", CONTEXT, cost_export_dir="Files/exports/x")
    assert seen["root"] == f"{ONELAKE}/Files/exports/x"
