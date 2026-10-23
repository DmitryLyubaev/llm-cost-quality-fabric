"""Entry points the Fabric notebooks call; they hold no logic beyond wiring Fabric to the package.

The notebooks pass `notebookutils.runtime.context` in, so this module never imports notebookutils
and imports cleanly in CI and locally.
"""

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession

from lakehouse.bronze.fetch_public import fetch_manifest
from lakehouse.checks import QualityCheckFailed, check_gateway_counts
from lakehouse.gateway.answers import cost_per_gateway_call
from lakehouse.gateway.build import (
    REPLAY_SESSION,
    SILVER_TABLE,
    build_fact_gateway_call,
    build_gateway_calls,
    count_duplicate_request_ids,
    merge_gateway_calls,
)
from lakehouse.paths import LakePaths
from lakehouse.run import RunSummary, run_load
from lakehouse.seeding import SEEDS_DIR

ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
# The default lakehouse's Files section as a local path. Documented in the Fabric notebook docs.
FILES_MOUNT = "/lakehouse/default/Files"
HTTP_TIMEOUT_SECONDS = 60


def fabric_paths(context: dict) -> LakePaths:
    """The lake's paths from a runtime context (`currentWorkspaceId`, `defaultLakehouseId`)."""
    # verify in plan 3: the context keys are those of notebookutils.runtime.context
    ids = {}
    for key in ("currentWorkspaceId", "defaultLakehouseId"):
        if not context.get(key):
            raise ValueError(f"runtime context has no {key!r}")
        ids[key] = context[key]
    root = f"abfss://{ids['currentWorkspaceId']}@{ONELAKE_HOST}/{ids['defaultLakehouseId']}"
    return LakePaths(
        tables_root=f"{root}/Tables",
        files_root=f"{root}/Files",
        files_mount=FILES_MOUNT,
    )


def get_active_spark() -> SparkSession:
    """The notebook's Spark session; this module never creates one."""
    session = SparkSession.getActiveSession()
    if session is None:
        raise RuntimeError("no active Spark session")
    return session


def _requests_get(url: str) -> tuple[int, bytes]:
    import requests

    response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
    return response.status_code, response.content


def _write_local(dest: str, body: bytes) -> None:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


def ingest_public(load_date: str, context: dict, http_get=None) -> int:
    """Land the public files under bronze for `load_date` (YYYY-MM-DD); returns the file count."""
    paths = fabric_paths(context)
    manifest = json.loads((Path(SEEDS_DIR) / "ingest_manifest.json").read_text(encoding="utf-8"))
    written = fetch_manifest(
        manifest,
        paths.bronze_local,
        date.fromisoformat(load_date),
        http_get or _requests_get,
        write=_write_local,
    )
    return len(written)


def _summary_dict(summary: RunSummary) -> dict:
    return {
        "load_date": summary.load_date.isoformat(),
        "row_counts": dict(summary.row_counts),
        "checks": [asdict(check) for check in summary.checks],
        "fingerprint": dict(summary.fingerprint),
    }


def load(load_date: str, context: dict, cost_export_dir: str = "Files/cost_export") -> dict:
    """Build silver and gold for `load_date`; a failed quality check propagates (the activity fails)."""
    paths = fabric_paths(context)
    summary = run_load(
        get_active_spark(),
        paths,
        SEEDS_DIR,
        cost_export_root=f"{paths.files_root}/{cost_export_dir.removeprefix('Files/')}",
        load_date=date.fromisoformat(load_date),
    )
    return _summary_dict(summary)


# Ruling R1: the shortcut to the Eventhouse's OneLake copy sits in the lakehouse's dbo schema.
GATEWAY_SOURCE = "dbo/gateway_calls"


def _plain(value):
    """A Spark row value as JSON-ready Python (dates as ISO text)."""
    return value.isoformat() if isinstance(value, date) else value


def load_gateway(session: str, eventhouse_count: str, context: dict) -> dict:
    """Project 10's gateway load, run once after a session (never from the pipeline).

    Silver from the shortcut (MERGE into `gateway_calls`), gold `fact_gateway_call`, then the
    count check against `eventhouse_count` (the Eventhouse's distinct count for `session`, read
    by the operator); a failed check raises `QualityCheckFailed`. Returns the MERGE counts, the
    number of request IDs that had more than one source row (R15), the gold row count, the check
    and the cost-per-call rows, all JSON-ready.

    ValueError, before reading anything, for an empty or "replay" session or an
    `eventhouse_count` that is not a non-negative integer.
    """
    name = (session or "").strip()
    if not name or name == REPLAY_SESSION:
        raise ValueError(f"session must name a measured session, not {session!r}")
    text = str(eventhouse_count).strip() if eventhouse_count is not None else ""
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"eventhouse_count must be a non-negative integer, got {eventhouse_count!r}")
    expected = int(text)

    paths = fabric_paths(context)
    spark = get_active_spark()
    # As run_load does: unreadable text fails, and dates are cut in UTC.
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.session.timeZone", "UTC")

    source = f"{paths.tables_root}/{GATEWAY_SOURCE}"
    calls = build_gateway_calls(spark, source)
    duplicates = count_duplicate_request_ids(spark, source)
    merged = merge_gateway_calls(spark, calls, paths.table("silver", SILVER_TABLE))
    gold_rows = build_fact_gateway_call(spark, paths)

    check = check_gateway_counts(spark, paths, name, expected)
    if not check.passed:
        raise QualityCheckFailed([check])

    answer = [
        {key: _plain(value) for key, value in row.asDict().items()}
        for row in cost_per_gateway_call(spark, paths, name).collect()
    ]
    return {
        "session": name,
        "silver": merged,
        "duplicate_request_ids": duplicates,
        "gold_rows": gold_rows,
        "checks": [asdict(check)],
        "cost_per_call": answer,
    }
