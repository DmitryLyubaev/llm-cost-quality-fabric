"""Silver costs: the Cost Management export, one row per resource x meter x day, with a project.

Exports restate the month to date, so silver replaces a whole billing month at a time
(`replace_month`). Costs stay in the export's billing currency; nothing is converted.
"""

import re

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import MapType, StringType, StructField, StructType

from lakehouse.attribution import Attribution, RgRule, SessionWindow, attribute, parse_tags

# Export header -> silver name. This is the ONLY place the export's own column names appear.
#
# Source: Microsoft Learn, "Understand usage details fields" (cost details file fields for
# pay-as-you-go / Microsoft Online Services Program, EA, MCA and MPA accounts),
# https://learn.microsoft.com/en-us/azure/cost-management-billing/automate/understand-usage-details-fields
# (page ms.date 2025-06-26, updated 2026-03-25), read 2026-10-21. No real export exists yet:
# plan 2 confirms this map against the first real file. Where the page lists several names:
#   Date              also UsageDateTime (EA, pay-as-you-go)
#   ResourceId        also InstanceId (EA, pay-as-you-go; the page's older-API table maps it to ResourceId)
#   Cost              also PreTaxCost, and CostInBillingCurrency (listed for EA and MCA only; the page
#                     says Cost, listed for pay-as-you-go, means CostInBillingCurrency)
#   BillingCurrency   also BillingCurrencyCode ("See BillingCurrency") and Currency (EA, pay-as-you-go)
#   ResourceGroup, MeterId, MeterName, Tags have one name each.
# Header matching is case-insensitive because the page says casing differs between account types.
COST_COLUMN_MAP: dict[str, str] = {
    "Date": "cost_date",
    "ResourceId": "resource_id",
    "ResourceGroup": "resource_group",
    "MeterId": "meter_id",
    "MeterName": "meter_name",
    "Cost": "cost",
    "BillingCurrency": "billing_currency",
    "Tags": "tags",
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


def read_cost_export(spark: SparkSession, path: str) -> DataFrame:
    """The export CSV as strings under its own header; a malformed row raises.

    FAILFAST rejects rows whose field count does not match the header. The frame is
    materialised here because Spark's CSV column pruning would otherwise skip the check
    for columns nobody selects (the export is small).
    """
    frame = (
        spark.read.option("header", True)
        .option("mode", "FAILFAST")
        .option("escape", '"')
        .option("multiLine", True)
        .csv(path)
    )
    return frame.localCheckpoint()


def _select_silver_inputs(export_df: DataFrame) -> DataFrame:
    """Rename the export's columns to silver names (case-insensitive); fail on a missing one."""
    by_lower = {c.lower(): c for c in export_df.columns}
    missing = [name for name in COST_COLUMN_MAP if name.lower() not in by_lower]
    if missing:
        raise ValueError(f"cost export lacks columns {missing}; found {export_df.columns}")
    return export_df.select(
        *[
            F.col(f"`{by_lower[name.lower()]}`").cast("string").alias(silver)
            for name, silver in COST_COLUMN_MAP.items()
        ]
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
