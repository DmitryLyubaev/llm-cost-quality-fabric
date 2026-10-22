"""`run_load`: silver, gold and the quality checks for one load date.

Bronze ingest is not called here: plan 2's pipeline runs `fetch_manifest` as its own activity,
and this reads what it landed for `load_date`. Nothing is written before the cost export has
been found and read, so a missing or empty export can never replace a full month with nothing.

Every run builds once. The re-run check compares this run's gold fingerprint with the one the
last successful run of the same load date and the same inputs digest recorded in
`silver/run_log`; a first run for a date, or one whose inputs (seeds, ingest manifest, export
runs, package code) changed since, is a new baseline and passes. An entry is appended only after
every check has passed, so a failed run never becomes the baseline.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import distribution
from pathlib import Path

from delta.tables import DeltaTable

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, StringType, StructField, StructType, TimestampType

from lakehouse.attribution import load_rg_rules, load_session_windows
from lakehouse.checks import (
    CheckResult,
    QualityCheckFailed,
    check_cost_totals,
    check_deployment_resolution,
    check_published_figures,
    check_rerun_unchanged,
)
from lakehouse.gold.build import build_gold
from lakehouse.gold.fingerprint import gold_fingerprint
from lakehouse.paths import LakePaths
from lakehouse.silver.costs import ExportRun, find_latest_runs, normalise_costs, read_export_runs, replace_month
from lakehouse.silver.deployments import apply_scd2, read_deployment_changes
from lakehouse.silver.eval_attempts import build_eval_attempts, merge_eval_attempts


RUN_LOG = "run_log"
_RUN_LOG_SCHEMA = StructType(
    [
        StructField("load_date", DateType()),
        StructField("fingerprint", StringType()),  # JSON object: gold table -> content hash
        StructField("recorded_at", TimestampType()),
        StructField("inputs_digest", StringType()),  # null in rows written before the digest existed
        StructField("status", StringType()),  # null in rows written before status existed: they passed
    ]
)
PASSED = "passed"
MANIFEST_FILE = "ingest_manifest.json"


@dataclass
class RunSummary:
    load_date: date
    row_counts: dict[str, int]
    checks: list[CheckResult]
    fingerprint: dict[str, str]


def _read_export(spark: SparkSession, root: str) -> tuple[DataFrame, dict[str, int], list[ExportRun]]:
    """The cost export's rows, manifest row counts per month folder and the runs they come from.

    FileNotFoundError naming `root` if it holds no manifest, or its latest manifests list no rows.
    """
    runs = find_latest_runs(spark, root)
    expected_rows = {run.month_folder: run.data_row_count for run in runs}
    export = read_export_runs(spark, runs)
    if sum(expected_rows.values()) == 0 or export.limit(1).count() == 0:
        raise FileNotFoundError(f"cost export lists no data rows: {root}")
    return export, expected_rows, runs


def _installed_record() -> str | None:
    """The installed `lakehouse` distribution's RECORD, or None when it does not list the
    package's files (an editable install's RECORD lists only its own metadata)."""
    record = distribution("lakehouse").read_text("RECORD")
    if record is None or not any(line.startswith("lakehouse/") for line in record.splitlines()):
        return None
    return record


def _package_dir() -> Path:
    """The directory the running `lakehouse` package is imported from."""
    return Path(__file__).resolve().parent


def _code_components() -> list[tuple[str, bytes]]:
    """What stands for the package code: the RECORD of an installed wheel (it carries every file's
    hash), else the version plus every `*.py` under the package directory by relative path."""
    record = _installed_record()
    if record is not None:
        return [("code:record", record.encode("utf-8"))]
    package = _package_dir()
    sources = sorted((p.relative_to(package).as_posix(), p) for p in package.rglob("*.py"))
    return [("code:version", distribution("lakehouse").version.encode("utf-8"))] + [
        (f"code:source:{name}", path.read_bytes()) for name, path in sources
    ]


def _feed(digest, name: str, data: bytes) -> None:
    """One component, framed by lengths so no two different component lists hash alike."""
    label = name.encode("utf-8")
    digest.update(len(label).to_bytes(8, "big"))
    digest.update(label)
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)


def inputs_digest(seeds_dir: str, manifest: dict, export_runs: list[ExportRun]) -> str:
    """First 16 hex chars of a sha256 over everything a run reads besides bronze.

    Hashed in order, each component as its name, its length and its bytes: the package code
    (`_code_components`), every file in `seeds_dir` by name (bytes, the ingest manifest included),
    the ingest manifest as JSON with sorted keys, and the export runs' ids sorted. A run whose
    digest differs from the last one's is a new baseline: its gold may legitimately differ.
    """
    digest = hashlib.sha256()
    for name, data in _code_components():
        _feed(digest, name, data)
    for path in sorted(Path(seeds_dir).iterdir(), key=lambda p: p.name):
        if path.is_file():
            _feed(digest, f"seed:{path.name}", path.read_bytes())
    _feed(digest, "manifest", json.dumps(manifest, sort_keys=True).encode("utf-8"))
    for run_id in sorted(r.run_id for r in export_runs):
        _feed(digest, "export_run", run_id.encode("utf-8"))
    return digest.hexdigest()[:16]


def _build(
    spark: SparkSession,
    paths: LakePaths,
    seeds_dir: str,
    export: DataFrame,
    load_date: date,
) -> dict[str, int]:
    """Silver (cost months replace, attempts MERGE, deployments MERGE), then gold; the row counts.

    Every billing month in the export is replaced, one `replace_month` each; months silver holds
    that the export lacks are untouched.
    """
    costs = normalise_costs(
        spark,
        export,
        load_rg_rules(seeds_dir),
        load_session_windows(seeds_dir),
    )
    months = [r["billing_month"] for r in costs.select("billing_month").distinct().collect()]
    if None in months:
        raise ValueError("cost export has a row whose date could not be read; nothing was written")
    for month in sorted(months):
        replace_month(spark, costs.where(F.col("billing_month") == month), paths.table("silver", "daily_cost"), month)

    attempts = build_eval_attempts(spark, paths.bronze, paths.private, seeds_dir, load_date)
    merge_eval_attempts(spark, attempts, paths.table("silver", "eval_attempts"))

    changes = read_deployment_changes(spark, seeds_dir)
    apply_scd2(spark, changes, paths.table("silver", "model_deployment"), seeds_dir)

    return build_gold(spark, paths, seeds_dir)


def _passed_log(spark: SparkSession, log_path: str) -> DataFrame | None:
    """The run log's passed rows (a null status is a row from before status existed), or None.

    A log written before `inputs_digest` and `status` existed reads them as null.
    """
    if not DeltaTable.isDeltaTable(spark, log_path):
        return None
    log = spark.read.format("delta").load(log_path)
    for name in ("inputs_digest", "status"):
        if name not in log.columns:
            log = log.withColumn(name, F.lit(None).cast("string"))
    return log.where(F.col("status").isNull() | (F.col("status") == PASSED))


def _previous_run(
    spark: SparkSession, log_path: str, load_date: date, digest: str
) -> tuple[bool, dict[str, str] | None]:
    """(a passed run of `load_date` exists, the fingerprint of the latest one with this digest).

    A null digest (an older log row) never matches.
    """
    log = _passed_log(spark, log_path)
    if log is None:
        return False, None
    same_date = log.where(F.col("load_date") == F.lit(load_date))
    if same_date.limit(1).count() == 0:
        return False, None
    latest = (
        same_date.where(F.col("inputs_digest") == F.lit(digest))
        .orderBy(F.col("recorded_at").desc())
        .first()
    )
    return True, (json.loads(latest["fingerprint"]) if latest else None)


def _latest_logged_date(spark: SparkSession, log_path: str) -> date | None:
    """The latest `load_date` of a passed run in the log, or None when there is none."""
    log = _passed_log(spark, log_path)
    return None if log is None else log.agg(F.max("load_date")).first()[0]


def _append_log(
    spark: SparkSession, log_path: str, load_date: date, fingerprint: dict[str, str], digest: str
) -> None:
    row = (load_date, json.dumps(fingerprint, sort_keys=True), datetime.now(UTC), digest, PASSED)
    (
        spark.createDataFrame([row], _RUN_LOG_SCHEMA)
        .write.format("delta").mode("append").option("mergeSchema", "true").save(log_path)
    )


def run_load(
    spark: SparkSession,
    paths: LakePaths,
    seeds_dir: str,
    cost_export_root: str,
    load_date: date,
    *,
    allow_earlier: bool = False,
) -> RunSummary:
    """Build silver and gold for `load_date`, run the four checks and return the summary.

    Sets the session to ANSI SQL and UTC first: Fabric's session is not built by `get_spark`, and
    both are part of the lake's contract (unreadable text fails, timestamps are UTC).

    Raises ValueError, before reading or writing anything, if `load_date` is earlier than the
    latest load date of a passed run in the run log, because rebuilding an earlier export would roll the
    published costs back; pass `allow_earlier=True` to do it on purpose. Re-running the latest
    date is always allowed. Raises FileNotFoundError, before writing anything, if
    `cost_export_root` holds no manifest or its latest manifests list no rows. Raises
    QualityCheckFailed, after everything has been written and every check has
    run, if any check fails; the reject table and gold stay in place as the record of the run,
    but the run is not logged, so it is never the baseline for a later re-run.
    """
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    log_path = paths.table("silver", RUN_LOG)
    latest = _latest_logged_date(spark, log_path)
    if latest is not None and load_date < latest and not allow_earlier:
        raise ValueError(
            f"load date {load_date.isoformat()} is earlier than the latest logged load date "
            f"{latest.isoformat()}; rebuilding it would roll published costs back "
            "(pass allow_earlier=True to do so on purpose)"
        )
    export, expected_rows, export_runs = _read_export(spark, cost_export_root)
    manifest = json.loads((Path(seeds_dir) / MANIFEST_FILE).read_text(encoding="utf-8"))
    digest = inputs_digest(seeds_dir, manifest, export_runs)

    row_counts = _build(spark, paths, seeds_dir, export, load_date)
    fingerprint = gold_fingerprint(spark, paths)
    seen_date, previous = _previous_run(spark, log_path, load_date, digest)

    if previous is not None:
        rerun = check_rerun_unchanged(previous, fingerprint)
    elif seen_date:
        rerun = CheckResult("rerun_unchanged", True, "new baseline: inputs changed")
    else:
        rerun = CheckResult("rerun_unchanged", True, "first run for this load date")
    checks = [
        check_deployment_resolution(spark, paths),
        check_cost_totals(spark, paths, export, expected_rows),
        check_published_figures(spark, paths, seeds_dir),
        rerun,
    ]
    failed = [c for c in checks if not c.passed]
    if failed:
        raise QualityCheckFailed(failed)
    _append_log(spark, log_path, load_date, fingerprint, digest)
    return RunSummary(load_date=load_date, row_counts=row_counts, checks=checks, fingerprint=fingerprint)
