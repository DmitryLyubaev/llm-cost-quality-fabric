"""Entry points the Fabric notebooks call; they hold no logic beyond wiring Fabric to the package.

The notebooks pass `notebookutils.runtime.context` in, so this module never imports notebookutils
and imports cleanly in CI and locally.
"""

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pyspark.sql import SparkSession

from lakehouse.bronze.fetch_public import fetch_manifest
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
