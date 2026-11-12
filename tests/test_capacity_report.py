"""Tests for tools/capacity_report.py, with a fake `run_dax` in place of the Power BI API."""

from datetime import datetime

import pytest

from capacity_report import (
    PROD_PIPELINE,
    PROD_WORKSPACE,
    clean,
    collect,
    daily_totals,
    find_runs,
    live,
    render,
    run_breakdown,
    sizing,
    smallest,
)

CAP = "00000000-0000-0000-0000-0000000000aa"
OTHER_WS = "11111111-1111-1111-1111-111111111111"
NB_LOAD = "22222222-2222-2222-2222-222222222222"
NB_INGEST = "33333333-3333-3333-3333-333333333333"
LAKEHOUSE = "44444444-4444-4444-4444-444444444444"
PIPELINE = PROD_PIPELINE.upper()  # the app returns upper-case GUIDs
WS = PROD_WORKSPACE.upper()


def hour(dt, ws, item, op, cus, dur=0.0, ops=1, thr=0.0, rej=0):
    return {"[Datetime]": dt, "[Workspace Id]": ws, "[Item Id]": item, "[Operation name]": op,
            "[cus]": cus, "[dur]": dur, "[ops]": ops, "[thr]": thr, "[rej]": rej}


HOURLY = [
    # Run 1: 13 Nov 06:00, spilling into 07:00.
    hour("2026-11-13T06:00:00", WS, PIPELINE, "ActivityRun", 20.0, 130, 1),
    hour("2026-11-13T07:00:00", WS, PIPELINE, "ActivityRun", 20.0, 130, 1),
    hour("2026-11-13T06:00:00", WS, NB_INGEST.upper(), "Notebook Pipeline Run", 800.0, 100),
    hour("2026-11-13T07:00:00", WS, NB_LOAD.upper(), "Notebook Pipeline Run", 1400.0, 140),
    hour("2026-11-13T06:00:00", WS, LAKEHOUSE.upper(), "OneLake Write via Redirect", 60.0, 0, 6),
    # Outside the window: not part of run 1, but counted in the day.
    hour("2026-11-13T09:00:00", WS, LAKEHOUSE.upper(), "OneLake Read via Proxy", 5.0, 0, 3),
    # Another workspace in the same hour: never part of a prod run.
    hour("2026-11-13T06:00:00", OTHER_WS, NB_LOAD.upper(), "Notebook Pipeline Run", 9000.0, 900),
    # Run 2: 14 Nov 06:00.
    hour("2026-11-14T06:00:00", WS, PIPELINE, "ActivityRun", 40.0, 250, 2),
    hour("2026-11-14T06:00:00", WS, NB_LOAD.upper(), "Notebook Pipeline Run", 1000.0, 125, thr=0.0),
    # A zero row the app returns for every day: dropped.
    hour("2026-11-12T00:00:00", WS, PIPELINE, "ActivityRun", 0.0, 0, 0),
]
ITEMS = [
    {"[Item Id]": PIPELINE.lower(), "[Workspace name]": "p9-prod", "[Item name]": "pl_daily_load", "[Item kind]": "Pipeline"},
    {"[Item Id]": NB_LOAD, "[Workspace name]": "p9-prod", "[Item name]": "nb_load", "[Item kind]": "SynapseNotebook"},
    {"[Item Id]": NB_INGEST, "[Workspace name]": "p9-prod", "[Item name]": "nb_ingest_public", "[Item kind]": "SynapseNotebook"},
    {"[Item Id]": LAKEHOUSE, "[Workspace name]": "p9-prod", "[Item name]": "lh_portfolio", "[Item kind]": "Lakehouse"},
]
STATES = [
    {"[Date]": "2026-11-13T00:00:00", "[InteractiveDelay]": 0.0, "[InteractiveRejection]": 0.0, "[BackgroundRejection]": 0.0, "[Overage]": 0.0},
    {"[Date]": "2026-11-14T00:00:00", "[InteractiveDelay]": 0.0, "[InteractiveRejection]": 0.0, "[BackgroundRejection]": 0.0, "[Overage]": None},
]
HEALTH = [{"[SKU]": "FTL64", "[BaseCU]": 64, "[Usage]": 0.002, "[MaxUsage]": 0.0067}]


class FakeDax:
    def __init__(self, states=STATES):
        self.queries: list[str] = []
        self.states = states

    def __call__(self, query: str) -> list[dict]:
        self.queries.append(query)
        if "Items, " in query:
            return ITEMS
        if "Metrics By Item Operation And Hour" in query:
            return HOURLY
        if "Capacity States By Day" in query:
            return self.states
        if "Capacity Health Summary" in query:
            return HEALTH
        raise AssertionError(query)


def test_live_sets_the_capacity_parameter_first():
    query = live(CAP, "ROW(\"a\", 1)")
    assert query.startswith(f"DEFINE\n  MPARAMETER 'CapacitiesList' = {{\"{CAP}\"}}\nEVALUATE\n")


@pytest.mark.parametrize("bad", ["", "  ", 'x"}) EVALUATE ROW("y', '"'])
def test_live_refuses_empty_or_quoted_ids(bad):
    with pytest.raises(ValueError):
        live(bad, "ROW(\"a\", 1)")


def test_clean_strips_table_and_brackets():
    assert clean([{"Items[Item Id]": "a", "[cus]": 1}]) == [{"Item Id": "a", "cus": 1}]


def test_find_runs_takes_the_first_pipeline_hour_of_each_day():
    rows = clean(HOURLY)
    assert find_runs(rows, PROD_PIPELINE) == [datetime(2026, 11, 13, 6), datetime(2026, 11, 14, 6)]


def test_find_runs_ignores_hours_without_operations():
    rows = clean([hour("2026-11-12T06:00:00", WS, PIPELINE, "ActivityRun", 0.0, 0, 0)])
    assert find_runs(rows, PROD_PIPELINE) == []


def test_run_breakdown_splits_the_two_hour_window():
    kinds = {r["Item Id"].lower(): r["Item kind"] for r in clean(ITEMS)}
    run = run_breakdown(clean(HOURLY), PROD_WORKSPACE, datetime(2026, 11, 13, 6), kinds)
    assert run["notebooks"] == 2200.0
    assert run["pipeline"] == 40.0
    assert run["storage"] == 60.0  # the 09:00 read is outside the window
    assert run["total"] == 2300.0
    vcores = sorted(s["avg_vcores"] for s in run["notebook_sessions"])
    assert vcores == [16.0, 20.0]  # 2 x CU-s / seconds


def test_run_breakdown_crosses_midnight():
    rows = clean([
        hour("2026-11-13T23:00:00", WS, PIPELINE, "ActivityRun", 10.0, 60, 1),
        hour("2026-11-14T00:00:00", WS, NB_LOAD.upper(), "Notebook Pipeline Run", 100.0, 50),
    ])
    run = run_breakdown(rows, PROD_WORKSPACE, datetime(2026, 11, 13, 23), {PIPELINE.lower(): "Pipeline"})
    assert (run["pipeline"], run["notebooks"]) == (10.0, 100.0)


def test_daily_totals_cover_every_workspace_and_drop_empty_days():
    days = daily_totals(clean(HOURLY))
    assert days == {"2026-11-13": 11305.0, "2026-11-14": 1040.0}


def test_sizing_budget_and_session_fit():
    rows = sizing(largest_run=2400.0, busiest_day=35_000.0, peak_vcores=20.7)
    by = {r["sku"]: r for r in rows}
    assert by["F2"]["budget_cus"] == 172_800
    assert by["F2"]["largest_run_pct"] == pytest.approx(100 * 2400 / 172_800)
    assert by["F2"]["fits_budget"] and not by["F2"]["fits_session"]
    assert not by["F8"]["fits_session"]  # 20.7 vCores needs three 8-vCore nodes; F8 allows two
    assert by["F16"]["fits_session"]
    assert smallest(rows, "fits_budget") == "F2"
    assert smallest(rows, "fits_session") == "F16"


def test_sizing_when_a_day_exceeds_small_budgets():
    rows = sizing(largest_run=0.0, busiest_day=200_000.0, peak_vcores=8.0)
    assert smallest(rows, "fits_budget") == "F4"
    assert smallest(rows, "fits_session") == "F2"  # one Medium node holds 8 vCores


def test_collect_and_render_end_to_end():
    fake = FakeDax()
    report = collect(fake, CAP)
    assert [r["start"] for r in report["runs"]] == ["2026-11-13T06:00:00", "2026-11-14T06:00:00"]
    assert report["throttled_days"] == []
    assert report["throttling_minutes"] == 0
    assert report["peak_notebook_vcores"] == 20.0
    assert report["smallest_by_budget"] == "F2"
    assert report["smallest_by_session"] == "F16"
    names = {s["item"] for r in report["runs"] for s in r["notebook_sessions"]}
    assert names == {"nb_load", "nb_ingest_public"}
    # Every usage query is scoped to the capacity; the import-mode Items query needs no parameter.
    usage = [q for q in fake.queries if "Items, " not in q]
    assert usage and all(q.startswith("DEFINE\n  MPARAMETER 'CapacitiesList'") for q in usage)
    text = render(report)
    assert CAP not in text
    assert "FTL64 (64 CU)" in text and "peak 0.670%" in text
    assert "Smallest SKU by CU budget: F2; smallest whose starter pool fits the measured session: F16" in text


def test_collect_reports_days_with_overage():
    states = STATES + [{"[Date]": "2026-11-15T00:00:00", "[InteractiveDelay]": 0.0, "[InteractiveRejection]": 0.0,
                        "[BackgroundRejection]": 0.0, "[Overage]": 12.5}]
    report = collect(FakeDax(states), CAP)
    assert [d["Date"] for d in report["throttled_days"]] == ["2026-11-15T00:00:00"]
