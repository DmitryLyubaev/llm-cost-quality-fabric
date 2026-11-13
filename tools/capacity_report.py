"""Read project 9's capacity use from Microsoft's Fabric Capacity Metrics app (plan 3, step 15).

Local only, not run in CI. It sends DAX to the app's semantic model through the Power BI REST
API (`executeQueries`) as whoever `az` is signed in as, and prints, per prod pipeline run, the
CU-seconds of each item and operation; throttling, rejections and overage by day; and how the
measured use compares with each F SKU. The query logic is pure (it takes a `run_dax(query) ->
rows` callable), and the tests exercise it with a fake one.

    CAPACITY_ID=<capacity GUID> python tools/capacity_report.py [--json out.json]

The capacity ID comes only from the environment and is never printed. The app's usage tables are
DirectQuery and return rows only when the model's `CapacitiesList` M parameter names a capacity,
so every usage query starts with `DEFINE MPARAMETER`. They are live: a run shows minutes after it
ends, not after the app's daily import refresh. Times are the app's local time (its UTC_offset,
10 = Brisbane for this project).
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta

RunDax = Callable[[str], list[dict]]


class QueryError(RuntimeError):
    """An executeQueries failure; its message holds only the HTTP status and error code."""

PROD_WORKSPACE = "bcaf8b2a-3413-4e0e-bbe1-45be8855629e"
PROD_PIPELINE = "225e8da0-7d29-4ac4-9636-a84bbff3292d"
DAY_SECONDS = 86_400
MEDIUM_NODE_VCORES = 8
NOTEBOOK_KINDS = {"SynapseNotebook", "Notebook"}
PBI = "https://api.powerbi.com/v1.0/myorg"

# (SKU, capacity units, max Spark vCores with burst, starter pool max Medium nodes). Read
# 2026-11-12 from Microsoft Learn: fabric/enterprise/licenses (CUs),
# fabric/data-engineering/spark-job-concurrency-and-queueing (burst; 1 CU = 2 Spark vCores) and
# fabric/data-engineering/configure-starter-pools (starter pools use Medium nodes; max nodes).
SKUS = [
    ("F2", 2, 20, 1),
    ("F4", 4, 24, 1),
    ("F8", 8, 48, 2),
    ("F16", 16, 96, 4),
    ("F32", 32, 192, 8),
    ("F64", 64, 384, 16),
]

H = "'Metrics By Item Operation And Hour'"
HOURLY = f"""SUMMARIZECOLUMNS({H}[Datetime], {H}[Workspace Id], {H}[Item Id], {H}[Operation name],
  "cus", SUM({H}[CU (s)]), "dur", SUM({H}[Duration (s)]), "ops", SUM({H}[Operations]),
  "thr", SUM({H}[Throttling (min)]), "rej", SUM({H}[Rejected operations]))"""
STATES = """SELECTCOLUMNS('Capacity States By Day', "Date", [Date],
  "InteractiveDelay", [InteractiveDelayMinutes], "InteractiveRejection", [InteractiveRejectionMinutes],
  "BackgroundRejection", [BackgroundRejectionMinutes], "Overage", [OverageBillingCUs])"""
HEALTH = """SELECTCOLUMNS('Capacity Health Summary', "SKU", [CurrentSKU], "BaseCU", [CurrentBaseCU],
  "Usage", [Usage], "MaxUsage", [MaxUsage])"""
ITEMS = """SELECTCOLUMNS(Items, "Item Id", [Item Id], "Workspace name", [Workspace name],
  "Item name", [Item name], "Item kind", [Item kind])"""


def live(capacity_id: str, body: str) -> str:
    """A DAX query over the app's DirectQuery tables, scoped to one capacity."""
    if '"' in capacity_id or not capacity_id.strip():
        raise ValueError("capacity id must be a non-empty GUID")
    return f"DEFINE\n  MPARAMETER 'CapacitiesList' = {{\"{capacity_id}\"}}\nEVALUATE\n{body}"


def clean(rows: list[dict]) -> list[dict]:
    """executeQueries keys columns as `table[col]` or `[col]`; keep just `col`."""
    return [{key.rsplit("[", 1)[-1].rstrip("]"): value for key, value in row.items()} for row in rows]


def _hour(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _id(value) -> str:
    return (value or "").lower()


def find_runs(hourly: list[dict], pipeline_id: str) -> list[datetime]:
    """Start hour of each pipeline run.

    A run is a stretch of consecutive hours with pipeline activity; a gap of more than an hour
    starts a new one, so a run may cross midnight and a day may hold several runs.
    """
    hours = sorted({_hour(r["Datetime"]) for r in hourly if _id(r["Item Id"]) == _id(pipeline_id) and (r["ops"] or 0) > 0})
    starts: list[datetime] = []
    previous = None
    for hour in hours:
        if previous is None or hour - previous > timedelta(hours=1):
            starts.append(hour)
        previous = hour
    return starts


def run_breakdown(hourly: list[dict], workspace_id: str, start: datetime, kinds: dict[str, str]) -> dict:
    """CU-seconds in the workspace over the run's start hour and the next.

    Split into notebooks (any operation on a notebook item, or named "Notebook ..."), the pipeline
    itself, and everything else (OneLake reads and writes against the lakehouse and SQL endpoint).
    The window can include other traffic in those two hours, such as someone reading the tables.
    `avg_vcores` is 2 x CU-seconds / duration: the average Spark vCores allocated over the session
    (one CU is two vCores). The metrics don't give the peak.
    """
    window = {start, start + timedelta(hours=1)}
    out = {"start": start.isoformat(), "notebooks": 0.0, "pipeline": 0.0, "storage": 0.0, "notebook_sessions": []}
    for r in hourly:
        if _id(r["Workspace Id"]) != _id(workspace_id) or _hour(r["Datetime"]) not in window:
            continue
        cus = r["cus"] or 0.0
        item = _id(r["Item Id"])
        if kinds.get(item) in NOTEBOOK_KINDS or (r["Operation name"] or "").startswith("Notebook"):
            out["notebooks"] += cus
            if r["dur"]:
                out["notebook_sessions"].append({"item": item, "cus": cus, "seconds": r["dur"], "avg_vcores": 2 * cus / r["dur"]})
        elif kinds.get(item) == "Pipeline":
            out["pipeline"] += cus
        else:
            out["storage"] += cus
    out["total"] = out["notebooks"] + out["pipeline"] + out["storage"]
    return out


def daily_totals(hourly: list[dict]) -> dict[str, float]:
    """Whole-capacity CU-seconds per local day, every workspace."""
    days: dict[str, float] = defaultdict(float)
    for r in hourly:
        days[_hour(r["Datetime"]).date().isoformat()] += r["cus"] or 0.0
    return {day: total for day, total in sorted(days.items()) if total > 0}


def sizing(largest_run: float, busiest_day: float, peak_vcores: float) -> list[dict]:
    """Each F SKU against the measured use.

    Background work is smoothed over 24 hours, so a SKU's budget is CU x 86,400 CU-seconds a day.
    `fits_budget`: the busiest whole-capacity day fits in that budget. The Spark checks ask whether
    the busiest session's average allocated vCores, in whole Medium nodes, fit the starter pool's
    node cap (`fits_starter_pool`) or a custom pool using the SKU's burst vCores
    (`fits_custom_pool`). The peak isn't measured, so these are lower bounds on what the trial
    gave; with fewer nodes Spark still runs the work.
    """
    # The small epsilon keeps a session of exactly one node (8.0001 vCores from rounding) at one.
    nodes_needed = math.ceil(peak_vcores / MEDIUM_NODE_VCORES - 1e-3) if peak_vcores else 0
    rows = []
    for sku, cu, burst, max_nodes in SKUS:
        budget = cu * DAY_SECONDS
        rows.append({
            "sku": sku, "cu": cu, "budget_cus": budget,
            "largest_run_pct": 100 * largest_run / budget, "busiest_day_pct": 100 * busiest_day / budget,
            "burst_vcores": burst, "max_nodes": max_nodes,
            "nodes_needed": nodes_needed, "fits_budget": busiest_day <= budget,
            "fits_starter_pool": max_nodes >= nodes_needed, "fits_custom_pool": burst // MEDIUM_NODE_VCORES >= nodes_needed,
        })
    return rows


def smallest(rows: list[dict], key: str) -> str | None:
    return next((r["sku"] for r in rows if r[key]), None)


def collect(run_dax: RunDax, capacity_id: str, workspace_id: str = PROD_WORKSPACE, pipeline_id: str = PROD_PIPELINE) -> dict:
    items = clean(run_dax("EVALUATE " + ITEMS))
    kinds = {_id(r["Item Id"]): r["Item kind"] for r in items}
    names = {_id(r["Item Id"]): r["Item name"] for r in items}
    hourly = [r for r in clean(run_dax(live(capacity_id, HOURLY))) if (r["cus"] or 0) > 0 or (r["ops"] or 0) > 0]
    states = clean(run_dax(live(capacity_id, STATES)))
    health = clean(run_dax(live(capacity_id, HEALTH)))
    runs = [run_breakdown(hourly, workspace_id, start, kinds) for start in find_runs(hourly, pipeline_id)]
    for run in runs:
        for session in run["notebook_sessions"]:
            session["item"] = names.get(session["item"], session["item"])
    days = daily_totals(hourly)
    largest = max((r["total"] for r in runs), default=0.0)
    busiest = max(days.values(), default=0.0)
    peak_vcores = max((s["avg_vcores"] for r in runs for s in r["notebook_sessions"]), default=0.0)
    sizes = sizing(largest, busiest, peak_vcores)
    return {
        "health": health,
        "throttled_days": [s for s in states if any((s[k] or 0) > 0 for k in ("InteractiveDelay", "InteractiveRejection", "BackgroundRejection", "Overage"))],
        "throttling_minutes": sum(r["thr"] or 0 for r in hourly),
        "rejected_operations": sum(r["rej"] or 0 for r in hourly),
        "runs": runs,
        "days": days,
        "peak_notebook_vcores": peak_vcores,
        "sizing": sizes,
        "smallest_by_budget": smallest(sizes, "fits_budget"),
        "smallest_by_starter_pool": smallest(sizes, "fits_starter_pool"),
        "smallest_by_custom_pool": smallest(sizes, "fits_custom_pool"),
    }


def render(report: dict) -> str:
    lines = []
    for h in report["health"]:
        lines.append(f"Capacity {h['SKU']} ({h['BaseCU']} CU): average use {100 * (h['Usage'] or 0):.3f}%, peak {100 * (h['MaxUsage'] or 0):.3f}%")
    lines.append(f"Throttling minutes {report['throttling_minutes']:g}; rejected operations {report['rejected_operations']}; "
                 f"days with delay, rejection or overage: {len(report['throttled_days'])}")
    lines.append("Prod runs (local start hour): total = notebooks + pipeline + storage CU-seconds")
    for r in report["runs"]:
        sessions = ", ".join(f"{s['item']} {s['cus']:,.1f} CU-s / {s['seconds']:,.0f} s ({s['avg_vcores']:.1f} vCores)" for s in r["notebook_sessions"])
        lines.append(f"  {r['start']}: {r['total']:,.1f} = {r['notebooks']:,.1f} + {r['pipeline']:,.1f} + {r['storage']:,.1f}  [{sessions}]")
    lines.append("Whole capacity per local day (CU-seconds):")
    lines += [f"  {day} {total:,.1f}" for day, total in report["days"].items()]
    lines.append(f"Busiest notebook session: {report['peak_notebook_vcores']:.1f} Spark vCores allocated on average (peak not measured)")
    for s in report["sizing"]:
        lines.append(f"  {s['sku']:>4}: largest run {s['largest_run_pct']:.2f}% of a day's budget, busiest day {s['busiest_day_pct']:.2f}%; "
                     f"burst {s['burst_vcores']} vCores, starter pool up to {s['max_nodes']} Medium node(s)")
    lines.append(f"Smallest SKU by CU budget: {report['smallest_by_budget']}; keeping the busiest session's nodes: "
                 f"{report['smallest_by_starter_pool']} with the starter pool, {report['smallest_by_custom_pool']} with a custom pool")
    return "\n".join(lines)


def power_bi_dax() -> RunDax:  # pragma: no cover - network
    """A `run_dax` that queries the Capacity Metrics app's model as the signed-in `az` account."""
    import requests

    az = shutil.which("az") or "az"  # az.cmd on Windows
    token = subprocess.run([az, "account", "get-access-token", "--resource", "https://analysis.windows.net/powerbi/api",
                            "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True, check=True).stdout.strip()
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    def get(url: str) -> list[dict]:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        return response.json()["value"]

    group = next(g["id"] for g in get(f"{PBI}/groups") if "capacity metrics" in g["name"].lower())
    datasets = get(f"{PBI}/groups/{group}/datasets")
    dataset = next((d["id"] for d in datasets if "capacity metrics" in d["name"].lower()), datasets[0]["id"])

    def run_dax(query: str) -> list[dict]:
        response = session.post(f"{PBI}/groups/{group}/datasets/{dataset}/executeQueries",
                                json={"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}, timeout=300)
        if response.status_code != 200:
            try:
                code = response.json().get("error", {}).get("code", "")
            except ValueError:
                code = ""
            raise QueryError(f"executeQueries failed: HTTP {response.status_code} {code}".rstrip())
        body = response.json()
        if "tables" not in body.get("results", [{}])[0]:
            raise QueryError("executeQueries failed: no result table")
        return body["results"][0]["tables"][0]["rows"]

    return run_dax


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - network
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", default=PROD_WORKSPACE)
    parser.add_argument("--pipeline", default=PROD_PIPELINE)
    parser.add_argument("--json", help="also write the report as JSON to this path")
    args = parser.parse_args(argv)
    capacity_id = os.environ.get("CAPACITY_ID", "")
    if not capacity_id:
        print("capacity_report: set CAPACITY_ID", file=sys.stderr)
        return 2
    try:
        report = collect(power_bi_dax(), capacity_id, args.workspace, args.pipeline)
    except Exception as exc:  # noqa: BLE001 - print only the type, never a URL or ID
        print(f"capacity_report: {type(exc).__name__}: {exc}" if isinstance(exc, QueryError) else f"capacity_report: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(render(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
