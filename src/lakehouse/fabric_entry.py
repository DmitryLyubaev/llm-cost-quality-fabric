"""Entry points the Fabric notebooks call; they hold no logic beyond wiring Fabric to the package.

The notebooks pass `notebookutils.runtime.context` in, so this module never imports notebookutils
and imports cleanly in CI and locally.
"""

import json
import math
import re
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from pyspark.sql import SparkSession

from lakehouse import security
from lakehouse.bronze.fetch_public import fetch_manifest
from lakehouse.checks import QualityCheckFailed, check_gateway_counts
from lakehouse.gateway.answers import cost_per_gateway_call
from lakehouse.gateway.build import (
    GOLD_TABLE,
    REPLAY_SESSION,
    SILVER_TABLE,
    build_fact_gateway_call,
    build_gateway_calls,
    count_duplicate_request_ids,
    merge_gateway_calls,
)
from lakehouse.gold.build import PROJECTS, optimize_tables
from lakehouse.paths import LakePaths
from lakehouse.run import RunSummary, run_load
from lakehouse.seeding import SEEDS_DIR
from lakehouse.semantic import check_model_matches_gold

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

    Silver from the shortcut (MERGE into `gateway_calls`), gold `fact_gateway_call`, an OPTIMIZE of
    both (Direct Lake errors past 1,000 files per table), then the count check against
    `eventhouse_count` (the Eventhouse's distinct count for `session`, read by the operator); a
    failed check raises `QualityCheckFailed`. Returns the MERGE counts, the number of request IDs
    that had more than one source row (R15), the gold row count, the check and the cost-per-call
    rows, all JSON-ready.

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
    # Direct Lake on OneLake errors past 1,000 Parquet files per table, and every session adds some.
    optimize_tables(spark, [paths.table("silver", SILVER_TABLE), paths.table("gold", GOLD_TABLE)])

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


def load_access(context: dict, csv_dir: str = "Files/private/access") -> dict:
    """Overwrite `security.user_project` from the single CSV in `csv_dir` (under the lakehouse's Files).

    The file is git-ignored `private/access.csv` (`upn,project`), uploaded by hand; it is read
    through the Files mount. ValueError, before anything is written, if the folder does not hold
    exactly one `.csv` file, if no files mount is set, or (from `security.load_access`) for a bad
    row, naming its line number only, or a file with no rows. Returns counts only (`rows`, `users`, `projects`), never a UPN.
    """
    paths = fabric_paths(context)
    if not paths.files_mount:  # files_root is an abfss:// URI, which pathlib cannot list
        raise ValueError("load_access reads the access file through the files mount, and none is set")
    folder = Path(paths.files_mount) / csv_dir.removeprefix("Files/")
    files = sorted(folder.glob("*.csv")) if folder.is_dir() else []
    if len(files) != 1:
        raise ValueError(f"{csv_dir} must hold exactly one .csv file, found {len(files)}")
    projects = [project for project, _ in PROJECTS]
    return security.load_access(get_active_spark(), paths, str(files[0]), projects)


# --- the model-versus-gold check (project 11, spec 4.5) ----------------------------------------

MODEL_NAME = "sm_portfolio"
# `table[column]`, `'table name'[column]` or `[column]`: evaluate_dax names a result column by the
# DAX column it came from; the queries' SELECTCOLUMNS gives every column a bare name (`[total]`).
_DAX_COLUMN = re.compile(r"^(?:'(?:[^']|'')*'|[^'\[\]]*)\[(.+)\]$")


def _sempy_evaluate_dax(**arguments):
    """Semantic link's `evaluate_dax`; the only live call of the check.

    `sempy.fabric.evaluate_dax(dataset, dax_string, workspace=None, verbose=0, num_rows=None,
    role=None, effective_user_name=None, use_readwrite_connection=False, credential=None)` returns
    a FabricDataFrame (a pandas DataFrame). Read from Learn's sempy.fabric reference
    (learn.microsoft.com/python/api/semantic-link-sempy/sempy.fabric, updated 2026-10-08);
    Fabric Runtime 2.0 ships semantic-link-sempy 0.14.1. `workspace` None resolves to the attached
    lakehouse's workspace (lh_portfolio's, the model's own). No `credential`: the notebook runs as
    a user, whose default token works; the service principal's is not supported. Imported here, so
    this module imports without sempy (CI, local).
    """
    import sempy.fabric

    return sempy.fabric.evaluate_dax(**arguments)


def _dax_column(name: str) -> str:
    match = _DAX_COLUMN.match(name)
    return match.group(1) if match else name


def _dax_value(value):
    """A pandas cell as plain Python: NaN, NaT, pandas' NA and None as None, numpy scalars
    unwrapped, and a midnight timestamp as a date (DAX has no date type; gold's dates are dates)."""
    if value is None or type(value).__name__ in ("NAType", "NaTType"):  # pd.NA (nullable dtypes), pd.NaT
        return None
    if hasattr(value, "to_pydatetime"):  # a pandas Timestamp (NaT fails the self-comparison below)
        if value != value:
            return None
        value = value.to_pydatetime()
    elif hasattr(value, "item") and not isinstance(value, (str, bytes)):  # a numpy scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime) and value.tzinfo is None and value.time() == datetime.min.time():
        return value.date()
    return value


def _dax_rows(frame) -> list[dict]:
    """evaluate_dax's DataFrame as plain dicts named as the queries' aliases.

    ValueError if two columns would share a name, since one would hide the other.
    """
    names = [_dax_column(str(column)) for column in frame.columns]
    if len(set(names)) != len(names):
        raise ValueError(f"DAX result columns share a name once normalised: {', '.join(names)}")
    return [
        {name: _dax_value(value) for name, value in zip(names, record)}
        for record in frame.itertuples(index=False, name=None)
    ]


def check_model(context: dict, sessions: str = "") -> dict:
    """Project 11's model-versus-gold check, run by hand as a user (never from the pipeline).

    Runs `semantic.check_model_matches_gold` with semantic link's `evaluate_dax` against
    `sm_portfolio`: as the caller (the owner, unfiltered), then as each lead through
    `effective_user_name`. `sessions` is a comma-separated list of gateway sessions to compare; empty
    skips the gateway answer. A failed check raises `QualityCheckFailed`. Returns the sessions and
    the check, JSON-ready; neither ever holds a UPN.
    """
    # stripped, blanks dropped, and each name once (in the order given)
    names = list(dict.fromkeys(name.strip() for name in (sessions or "").split(",") if name.strip()))
    paths = fabric_paths(context)
    spark = get_active_spark()
    # As run_load does: dates and cost_per_result's month are cut in UTC.
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.session.timeZone", "UTC")

    def evaluate(dax: str, user: str | None) -> list[dict]:
        arguments = {"dataset": MODEL_NAME, "dax_string": dax}
        if user is not None:
            arguments["effective_user_name"] = user
        return _dax_rows(_sempy_evaluate_dax(**arguments))

    result = check_model_matches_gold(spark, paths, evaluate, names)
    if not result.passed:
        raise QualityCheckFailed([result])
    return {"sessions": names, "checks": [asdict(result)]}
