import json
from datetime import date

import pytest

from lakehouse import fabric_entry
from lakehouse.checks import CheckResult, QualityCheckFailed
from lakehouse.gold.build import PROJECTS
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


def _access_folder(root, *names):
    folder = root / "private" / "access"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_text("upn,project\n", encoding="utf-8")
    return folder


def test_load_access_reads_the_single_csv_under_the_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    folder = _access_folder(tmp_path, "access.csv", "notes.txt")
    spark_stub = object()
    seen = {}

    def fake_load_access(spark, paths, csv_path, projects):
        seen.update(spark=spark, paths=paths, csv_path=csv_path, projects=projects)
        return {"rows": 2, "users": 1, "projects": 2}

    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: spark_stub)
    monkeypatch.setattr(fabric_entry.security, "load_access", fake_load_access)

    result = fabric_entry.load_access(CONTEXT)

    assert result == {"rows": 2, "users": 1, "projects": 2}
    json.dumps(result)
    assert seen["spark"] is spark_stub
    assert seen["paths"] == fabric_entry.fabric_paths(CONTEXT)
    assert seen["csv_path"] == str(folder / "access.csv")
    assert seen["projects"] == [project for project, _ in PROJECTS]


def test_load_access_takes_another_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "leads.csv").write_text("upn,project\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())
    monkeypatch.setattr(
        fabric_entry.security, "load_access", lambda spark, paths, csv_path, projects: seen.setdefault("p", csv_path)
    )

    fabric_entry.load_access(CONTEXT, csv_dir="Files/elsewhere")

    assert seen["p"] == str(folder / "leads.csv")


@pytest.mark.parametrize("names", [(), ("a.csv", "b.csv")])
def test_load_access_needs_exactly_one_csv(tmp_path, monkeypatch, names):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    _access_folder(tmp_path, *names)
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())
    monkeypatch.setattr(fabric_entry.security, "load_access", lambda *args: pytest.fail("must not load"))

    with pytest.raises(ValueError, match=rf"exactly one \.csv.*found {len(names)}"):
        fabric_entry.load_access(CONTEXT)


def test_load_access_without_the_folder_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())

    with pytest.raises(ValueError, match=r"Files/private/access.*found 0"):
        fabric_entry.load_access(CONTEXT)


def test_load_access_needs_the_files_mount(monkeypatch):
    """Without the mount the folder would be an abfss:// URI, which pathlib cannot list."""
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", None)
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())
    monkeypatch.setattr(fabric_entry.security, "load_access", lambda *args: pytest.fail("must not load"))

    with pytest.raises(ValueError, match=r"files mount"):
        fabric_entry.load_access(CONTEXT)


# --- check_model: the model-versus-gold check (Task 6) ------------------------------------------


class _Scalar:
    """A numpy scalar as far as the normaliser sees one: `.item()` gives the Python value."""

    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class _Timestamp:
    """A pandas Timestamp as far as the normaliser sees one."""

    def __init__(self, moment):
        self.moment = moment

    def to_pydatetime(self):
        return self.moment


class NAType:
    """pandas' NA as far as the normaliser sees it: a type of that name (comparing it raises)."""

    def __eq__(self, other):
        raise TypeError("boolean value of NA is ambiguous")

    __ne__ = __eq__


class _Frame:
    """The part of the pandas DataFrame `evaluate_dax` returns that the wrapper reads."""

    def __init__(self, columns, rows):
        self.columns = list(columns)
        self.rows = [tuple(r) for r in rows]

    def itertuples(self, index=True, name="Pandas"):
        assert index is False and name is None
        return iter(self.rows)


def test_dax_rows_take_the_names_inside_the_brackets():
    from datetime import datetime

    frame = _Frame(
        ["fact_daily_cost[billing_currency]", "'dim project'[project]", "[total]", "[n]", "[call_date]", "[at]"],
        [
            ["AUD", "p1", _Scalar(12.5), _Scalar(3), _Timestamp(datetime(2026, 11, 12)),
             _Timestamp(datetime(2026, 11, 12, 1, 30))],
            [None, "p2", float("nan"), _Scalar(float("nan")), None, NAType()],
        ],
    )

    rows = fabric_entry._dax_rows(frame)

    assert rows == [
        {"billing_currency": "AUD", "project": "p1", "total": 12.5, "n": 3, "call_date": date(2026, 11, 12),
         "at": datetime(2026, 11, 12, 1, 30)},
        {"billing_currency": None, "project": "p2", "total": None, "n": None, "call_date": None, "at": None},
    ]
    assert type(rows[0]["n"]) is int


def test_dax_rows_refuse_two_columns_with_one_name():
    frame = _Frame(["fact_eval_attempt[model_version]", "dim_model_deployment[model_version]"], [])
    with pytest.raises(ValueError, match="model_version"):
        fabric_entry._dax_rows(frame)


class _Conf:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


class _Spark:
    def __init__(self):
        self.conf = _Conf()


@pytest.fixture
def model_check(monkeypatch):
    """check_model with a fake semantic link and a stub check; returns what each saw."""
    seen = {"dax": [], "spark": _Spark()}

    def fake_evaluate_dax(**kwargs):
        seen["dax"].append(kwargs)
        return _Frame(["fact_daily_cost[project]", "[total]"], [["p1", _Scalar(1.5)]])

    def fake_check(spark, paths, evaluate, sessions):
        seen.update(paths=paths, sessions=sessions, spark_seen=spark)
        seen["owner_rows"] = evaluate("EVALUATE owner", None)
        seen["user_rows"] = evaluate("EVALUATE lead", "lead.one@example.test")
        return seen.get("result", CheckResult("model_matches_gold", True, "matches"))

    monkeypatch.setattr(fabric_entry, "_sempy_evaluate_dax", fake_evaluate_dax)
    monkeypatch.setattr(fabric_entry, "check_model_matches_gold", fake_check)
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: seen["spark"])
    return seen


def test_check_model_calls_evaluate_dax_as_the_owner_then_as_a_user(model_check):
    summary = fabric_entry.check_model(CONTEXT, " p10-2026-11-12, p10-2026-11-19 ,, ")

    assert model_check["dax"] == [
        {"dataset": "sm_portfolio", "dax_string": "EVALUATE owner"},
        {"dataset": "sm_portfolio", "dax_string": "EVALUATE lead", "effective_user_name": "lead.one@example.test"},
    ]
    assert model_check["owner_rows"] == model_check["user_rows"] == [{"project": "p1", "total": 1.5}]
    assert model_check["sessions"] == ["p10-2026-11-12", "p10-2026-11-19"]
    assert model_check["paths"] == fabric_entry.fabric_paths(CONTEXT)
    assert model_check["spark"].conf.values["spark.sql.session.timeZone"] == "UTC"
    assert summary == {
        "sessions": ["p10-2026-11-12", "p10-2026-11-19"],
        "checks": [{"name": "model_matches_gold", "passed": True, "detail": "matches"}],
    }
    json.dumps(summary)


@pytest.mark.parametrize("sessions", ["", "  ", " , "])
def test_check_model_with_no_sessions_passes_an_empty_list(model_check, sessions):
    fabric_entry.check_model(CONTEXT, sessions)
    assert model_check["sessions"] == []


def test_check_model_raises_on_a_mismatch(model_check):
    model_check["result"] = CheckResult("model_matches_gold", False, "cost_per_project differs")

    with pytest.raises(QualityCheckFailed, match="model_matches_gold: cost_per_project differs"):
        fabric_entry.check_model(CONTEXT)


def test_semantic_link_is_imported_only_when_called():
    """Fabric Runtime 2.0 preinstalls sempy; CI does not have it, so the module must import without it."""
    import sys

    assert "sempy" not in sys.modules
    with pytest.raises(ModuleNotFoundError, match="sempy"):
        fabric_entry._sempy_evaluate_dax(dataset="sm_portfolio", dax_string="EVALUATE {1}")
