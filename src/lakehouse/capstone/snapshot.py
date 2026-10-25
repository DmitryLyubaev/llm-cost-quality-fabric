"""The capstone's frozen snapshot: prod gold copied once into the capstone lakehouse `lh_capstone`.

The data agent reads only the snapshot, and the expected answers are computed from it, so neither
moves while prod's daily load runs on (spec 4.1). `copy_gold` is a plain Delta copy: each named
gold table in the target is overwritten with the source's rows and schema, read at a pinned Delta
version. The snapshot time, the versions and the row counts go to `Files/capstone/snapshot.json` in
the target; the freeze hashes that time.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from delta.tables import DeltaTable
from pyspark.errors import AnalysisException
from pyspark.sql import SparkSession

from lakehouse.gateway.build import GOLD_TABLE as GATEWAY_GOLD_TABLE
from lakehouse.gold.build import GOLD_TABLES
from lakehouse.paths import LakePaths

# The gold tables the data agent selects (spec 4.2): project 9's eight and project 10's gateway table.
# Not `security.user_project`: the lakehouse source bypasses the model's row-level security.
SNAPSHOT_TABLES = [*GOLD_TABLES, GATEWAY_GOLD_TABLE]
SNAPSHOT_FILE = "capstone/snapshot.json"  # under the target's Files


def _check_tables(tables) -> list[str]:
    if not isinstance(tables, list):
        raise ValueError(f"tables must be a list of gold table names, got {type(tables).__name__}")
    if not tables:
        raise ValueError("name at least one gold table to copy")
    unknown = [t for t in tables if t not in SNAPSHOT_TABLES]
    if unknown:
        raise ValueError(f"not snapshot tables: {unknown}; expected some of {SNAPSHOT_TABLES}")
    repeated = sorted({t for t in tables if tables.count(t) > 1})
    if repeated:
        raise ValueError(f"tables named more than once: {repeated}")
    return list(tables)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _latest_version(spark: SparkSession, path: str) -> int:
    """The Delta table's current version (its latest commit)."""
    return DeltaTable.forPath(spark, path).history(1).collect()[0]["version"]


def copy_gold(spark: SparkSession, source: LakePaths, target: LakePaths, tables: list[str]) -> dict:
    """Overwrite each named gold table in `target` with `source`'s; returns the snapshot record.

    The record is `{"snapshot_time": "yyyy-MM-ddTHH:mm:ssZ", "versions": {table: v}, "rows":
    {table: n}}`: the UTC time the copy started, the Delta version each source table was read at,
    and each copied table's row count, read back from the target. Every version is recorded before
    any table is read, and each table is read at its version (`versionAsOf`), so a load that commits
    to the source during the copy cannot mix into the snapshot. The same record is written as JSON
    (LF, sorted keys) to `Files/capstone/snapshot.json` in the target, through its files mount when
    it has one.

    ValueError, before anything is written, when `tables` is not a non-empty list of distinct
    `SNAPSHOT_TABLES` names, when source and target are the same lake, when the target has no files
    mount and its files root is a URI rather than a local path, or when a source table is not a
    readable Delta table.
    """
    names = _check_tables(tables)
    if source.tables_root.rstrip("/") == target.tables_root.rstrip("/"):
        raise ValueError("the snapshot's source and target are the same lake: it would copy a lake onto itself")
    files = target.files_mount or target.files_root
    if "://" in files:
        raise ValueError(f"snapshot.json is written with plain file APIs, so the target needs a files mount, not {files}")

    snapshot_time = _utc_now()
    versions = {}
    for name in names:
        try:
            versions[name] = _latest_version(spark, source.table("gold", name))
        except AnalysisException as exc:
            raise ValueError(f"the source has no readable gold table {name}: {exc}") from exc
    frames = {
        name: spark.read.format("delta").option("versionAsOf", version).load(source.table("gold", name))
        for name, version in versions.items()
    }

    rows = {}
    for name, frame in frames.items():
        path = target.table("gold", name)
        frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(path)
        rows[name] = spark.read.format("delta").load(path).count()

    record = {"snapshot_time": snapshot_time, "versions": versions, "rows": rows}
    destination = Path(files) / SNAPSHOT_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return record
