"""Silver costs: the Cost Management export, one row per resource x meter x day, with a project.

Exports restate the month to date, so silver replaces a whole billing month at a time
(`replace_month`). Costs stay in the export's billing currency; nothing is converted. The export
is read through its manifests (`find_latest_runs`), never by guessing file names.
"""

import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import reduce
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from pyspark.errors import AnalysisException
from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import MapType, StringType, StructField, StructType

from lakehouse.attribution import Attribution, RgRule, SessionWindow, attribute, parse_tags

# Silver name -> the export headers that may carry it, first match wins. This is the ONLY place
# the export's own column names appear. Header matching is case-insensitive.
#
# Two dataset generations are accepted:
#   * Pay-as-you-go (MOSA) 2019-11-01 schema, 24 columns, per Microsoft Learn "Cost and usage
#     details (pay-as-you-go)" (docs/research/2026-10-22-fabric-facts.md section 10):
#     UsageDateTime, InstanceId, ResourceGroup, MeterId, MeterName, PreTaxCost, Currency, Tags.
#   * The newer EA/MCA-style names an export created later may emit for the same account (Date,
#     ResourceId, ResourceGroupName, CostInBillingCurrency, BillingCurrencyCode / BillingCurrency;
#     the cost details file also uses Cost), per "Understand usage details fields"
#     https://learn.microsoft.com/en-us/azure/cost-management-billing/automate/understand-usage-details-fields
# No real export exists yet; a real header is checked in plan 3. A header set that matches none of a
# silver column's names raises ValueError listing the headers found.
COST_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "cost_date": ("UsageDateTime", "Date"),
    "resource_id": ("InstanceId", "ResourceId"),
    "resource_group": ("ResourceGroup", "ResourceGroupName"),
    "meter_id": ("MeterId",),
    "meter_name": ("MeterName",),
    "cost": ("PreTaxCost", "CostInBillingCurrency", "Cost"),
    "billing_currency": ("Currency", "BillingCurrencyCode", "BillingCurrency"),
    "tags": ("Tags",),
}

COST_COLUMNS = [
    "cost_date",
    "resource_id",
    "resource_group",
    "meter_id",
    "meter_name",
    "cost",
    "billing_currency",
    "tags",
    "billing_month",
    "project",
    "attribution_rule",
]

_ATTRIBUTION = StructType(
    [StructField("project", StringType()), StructField("rule", StringType())]
)
_ISO_DATE = r"^\d{4}-\d{2}-\d{2}"
_MONTH = re.compile(r"\d{4}-\d{2}")


_MONTH_FOLDER = re.compile(r"(\d{8})-(\d{8})")


@dataclass(frozen=True)
class ExportRun:
    """One export run: its month folder, run id, submit time, part files and manifest row count."""

    month_folder: str
    run_id: str
    submitted: datetime
    blobs: tuple[str, ...]
    data_row_count: int


def month_range(month_folder: str) -> tuple[date, date]:
    """The (first, last) day of an export's `YYYYMMDD-YYYYMMDD` folder name; ValueError otherwise."""
    found = _MONTH_FOLDER.fullmatch(month_folder)
    if not found:
        raise ValueError(f"export folder {month_folder!r} is not YYYYMMDD-YYYYMMDD")
    try:
        first, last = (datetime.strptime(part, "%Y%m%d").date() for part in found.groups())
    except ValueError as error:
        raise ValueError(f"export folder {month_folder!r} is not YYYYMMDD-YYYYMMDD: {error}") from error
    return first, last


def resolve_cost_headers(headers: list[str]) -> dict[str, str]:
    """Silver name -> the export header that carries it (first alias wins, case-insensitive).

    ValueError, listing the headers found, if some silver column has no accepted header.
    """
    by_lower: dict[str, str] = {}
    for header in headers:
        by_lower.setdefault(header.strip().lower(), header)
    resolved: dict[str, str] = {}
    missing = []
    for silver, aliases in COST_COLUMN_ALIASES.items():
        match = next((by_lower[a.lower()] for a in aliases if a.lower() in by_lower), None)
        if match is None:
            missing.append(f"{silver} (one of {', '.join(aliases)})")
        else:
            resolved[silver] = match
    if missing:
        raise ValueError(f"cost export lacks columns {'; '.join(missing)}; headers found: {headers}")
    return resolved


def _hadoop_path(spark: SparkSession, path: str):
    return spark._jvm.org.apache.hadoop.fs.Path(path)


def _uri_path(uri: str) -> str:
    return unquote(urlparse(uri).path)


def _parse_manifest(text: str, where: str) -> tuple[datetime, str, int, list[str]]:
    """(submitted, run id, data row count, blob names) of a manifest; ValueError if it lacks them."""
    try:
        manifest = json.loads(text)
        info = manifest["runInfo"]
        submitted = datetime.fromisoformat(info["submittedTime"])
        if submitted.tzinfo is None:
            submitted = submitted.replace(tzinfo=UTC)
        return (
            submitted,
            str(info["runId"]),
            int(manifest["dataRowCount"]),
            [str(blob["blobName"]) for blob in manifest["blobs"]],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"manifest {where} is unreadable ({type(error).__name__}: {error}); it needs "
            "runInfo.submittedTime, runInfo.runId, dataRowCount and blobs[].blobName"
        ) from error


def find_latest_runs(spark: SparkSession, export_root: str) -> list[ExportRun]:
    """The latest run of each month folder under `export_root`, oldest month first.

    Reads every `manifest.json` below the root (layout `<root>/<ExportName>/<YYYYMMDD-YYYYMMDD>/
    <RunID>/manifest.json`), groups the runs by month folder and keeps the one with the latest
    `runInfo.submittedTime` (the greater run id on a tie). Each blob is resolved as its manifest's
    own folder plus the basename of `blobName`; one missing on disk raises FileNotFoundError naming
    it. FileNotFoundError naming `export_root` if there is no manifest at all.
    """
    root = export_root.rstrip("/")
    try:
        manifests = (
            spark.read.option("recursiveFileLookup", True)
            .option("pathGlobFilter", "manifest.json")
            .text(export_root, wholetext=True)
            .select(F.col("_metadata.file_path").alias("path"), F.col("value"))
            .collect()
        )
    except AnalysisException as error:
        if "PATH_NOT_FOUND" in str(error) or "does not exist" in str(error):
            raise FileNotFoundError(f"cost export not found: {export_root}") from error
        raise
    if not manifests:
        raise FileNotFoundError(f"cost export has no manifest.json: {export_root}")

    root_path = _hadoop_path(spark, export_root)
    fs = root_path.getFileSystem(spark._jsc.hadoopConfiguration())
    root_dir = _uri_path(fs.makeQualified(root_path).toUri().toString()).rstrip("/")
    latest: dict[str, tuple[tuple[datetime, str], ExportRun]] = {}
    for row in manifests:
        manifest_path = _uri_path(row["path"])
        if not manifest_path.startswith(root_dir + "/"):
            raise ValueError(f"manifest {row['path']} is not under {export_root}")
        run_dir = manifest_path[len(root_dir) + 1 :].rsplit("/", 1)[0]
        folders = run_dir.split("/")
        if len(folders) < 3:
            raise ValueError(
                f"manifest {row['path']} is not under <ExportName>/<YYYYMMDD-YYYYMMDD>/<RunID>"
            )
        month_folder = folders[-2]
        month_range(month_folder)
        submitted, run_id, rows, names = _parse_manifest(row["value"], row["path"])
        if month_folder in latest and latest[month_folder][0] >= (submitted, run_id):
            continue
        # A real blobName is container-relative, with a prefix we cannot know through a shortcut,
        # so each part file is taken from the manifest's own folder.  # verify in plan 3
        blobs = tuple(f"{root}/{run_dir}/{PurePosixPath(name).name}" for name in names)
        for blob in blobs:
            if not fs.exists(_hadoop_path(spark, blob)):
                raise FileNotFoundError(f"cost export part file listed in {row['path']} is missing: {blob}")
        latest[month_folder] = ((submitted, run_id), ExportRun(month_folder, run_id, submitted, blobs, rows))
    return [latest[month][1] for month in sorted(latest)]


def _read_header(spark: SparkSession, blob: str) -> list[str]:
    """The first line of a part file split as CSV; ValueError if the file is empty."""
    first = spark.read.text(blob).limit(1).collect()
    if not first or not first[0]["value"].strip():
        raise ValueError(f"cost export part file {blob} has no header line")
    return next(csv.reader([first[0]["value"].lstrip("﻿")]))


def _read_run(spark: SparkSession, run: ExportRun) -> DataFrame:
    """One run's part files as strings under silver names; every part must carry the same header."""
    headers = {blob: _read_header(spark, blob) for blob in run.blobs}
    first = run.blobs[0]
    wanted = [h.strip().lower() for h in headers[first]]
    for blob, header in headers.items():
        if [h.strip().lower() for h in header] != wanted:
            raise ValueError(
                f"cost export part files of run {run.run_id} differ in header: "
                f"{first} has {headers[first]}, {blob} has {header}"
            )
    resolved = resolve_cost_headers(headers[first])
    schema = StructType([StructField(name, StringType()) for name in headers[first]])
    frame = (
        spark.read.schema(schema)
        .option("header", True)
        .option("mode", "FAILFAST")
        .option("escape", '"')
        .option("multiLine", True)
        .csv(list(run.blobs))  # a .csv.gz part is decompressed by its extension
    )
    # Materialised before the select below: Spark's CSV column pruning would otherwise skip the
    # FAILFAST check for columns nobody selects (the export is small).
    frame = frame.localCheckpoint()
    return frame.select(*[F.col(f"`{resolved[silver]}`").alias(silver) for silver in COST_COLUMN_ALIASES])


def read_cost_export(spark: SparkSession, export_root: str) -> tuple[DataFrame, dict[str, int]]:
    """The latest run of each month under `export_root` as strings under silver names.

    Returns the rows and `{month_folder: dataRowCount}` from the manifests. A malformed row, part
    files whose headers differ, or a header lacking a silver column's name raises.
    """
    runs = find_latest_runs(spark, export_root)
    expected = {run.month_folder: run.data_row_count for run in runs}
    frames = [_read_run(spark, run) for run in runs if run.blobs]
    if not frames:
        empty = StructType([StructField(name, StringType()) for name in COST_COLUMN_ALIASES])
        return spark.createDataFrame([], empty), expected
    return reduce(DataFrame.unionByName, frames), expected


def _select_silver_inputs(export_df: DataFrame) -> DataFrame:
    """The export's silver-named columns as strings; ValueError naming any that is missing."""
    missing = [name for name in COST_COLUMN_ALIASES if name not in export_df.columns]
    if missing:
        raise ValueError(f"cost export lacks columns {missing}; found {export_df.columns}")
    return export_df.select(
        *[F.col(f"`{name}`").cast("string").alias(name) for name in COST_COLUMN_ALIASES]
    )


def _parsed_date(column: str) -> Column:
    """The date in `column` (ISO or US month-first), null where it cannot be read; never raises."""
    dated = F.trim(column)
    text = F.substring(dated, 1, 10)
    return F.when(dated.rlike(_ISO_DATE), F.try_to_date(text, "yyyy-MM-dd")).otherwise(
        F.try_to_date(text, "MM/dd/yyyy")
    )


def _require_readable(raw: DataFrame) -> None:
    """ValueError if a row's Cost is empty or unreadable, or its Date text is unreadable.

    Done with non-raising casts so the answer does not depend on the session's ANSI setting.
    """
    counts = raw.agg(
        F.count(F.when(F.trim("cost").try_cast("double").isNull(), 1)).alias("cost"),
        F.count(
            F.when((F.trim("cost_date") != "") & _parsed_date("cost_date").isNull(), 1)
        ).alias("cost_date"),
    ).first()
    problems = []
    if counts["cost"]:
        problems.append(f"{counts['cost']} row(s) have a cost that is empty or not a number")
    if counts["cost_date"]:
        problems.append(f"{counts['cost_date']} row(s) have a cost_date that is not a date")
    if problems:
        raise ValueError("cost export is not readable: " + "; ".join(problems))


def normalise_costs(
    spark: SparkSession,
    export_df: DataFrame,
    rg_rules: list[RgRule],
    windows: list[SessionWindow],
) -> DataFrame:
    """Silver rows (COST_COLUMNS) from a raw export frame: typed, month-stamped, attributed.

    Every export row is kept. A row whose Cost is empty or not a number, or whose Date has text
    that is not a date, raises ValueError naming the count and the column (whatever the session's
    ANSI setting, so a total can never lose a row without a word); an unattributable row gets
    project `untagged`.
    """
    # plain lambda: Spark warns when it cannot infer an eval type from parse_tags's type hints
    parse_tags_udf = F.udf(lambda raw: parse_tags(raw), MapType(StringType(), StringType()))

    def _attribute(resource_group, cost_date, tags):
        got: Attribution = attribute(resource_group, cost_date, tags or {}, rg_rules, windows)
        return got.project, got.rule

    attribute_udf = F.udf(_attribute, _ATTRIBUTION)

    raw = _select_silver_inputs(export_df)
    _require_readable(raw)
    dated = F.trim("cost_date")
    return (
        raw.withColumn(
            "cost_date",
            # The export's date format is unconfirmed (plan 2): accept ISO and US month-first.
            F.when(dated.rlike(_ISO_DATE), F.to_date(F.substring(dated, 1, 10), "yyyy-MM-dd")).otherwise(
                F.to_date(F.substring(dated, 1, 10), "MM/dd/yyyy")
            ),
        )
        .withColumn("cost", F.trim("cost").cast("double"))
        .withColumn("tags", parse_tags_udf("tags"))
        .withColumn("billing_month", F.date_format("cost_date", "yyyy-MM"))
        .withColumn("_attr", attribute_udf("resource_group", "cost_date", "tags"))
        .withColumn("project", F.col("_attr.project"))
        .withColumn("attribution_rule", F.col("_attr.rule"))
        .select(*COST_COLUMNS)
    )


def replace_month(spark: SparkSession, df: DataFrame, table_path: str, billing_month: str) -> None:
    """Replace one billing month of the Delta table with df; other months are untouched.

    Raises ValueError, before writing anything, if df holds any month other than
    `billing_month`. A restated month that lost rows loses them from the table too.
    """
    if not _MONTH.fullmatch(billing_month):
        raise ValueError(f"billing_month must be YYYY-MM, got {billing_month!r}")
    months = {r["billing_month"] for r in df.select("billing_month").distinct().collect()}
    foreign = sorted(months - {billing_month}, key=str)
    if foreign:
        raise ValueError(f"expected only billing month {billing_month}, also found {foreign}")
    (
        df.write.format("delta")
        .mode("overwrite")
        .partitionBy("billing_month")
        .option("replaceWhere", f"billing_month = '{billing_month}'")
        .save(table_path)
    )
