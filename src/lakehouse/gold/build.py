"""Gold star schema: dimensions, three facts and the reject table, rebuilt in full each run.

The data is tiny (about 1,760 attempts, a month of cost lines, under a hundred checks), so every
gold table is overwritten from silver and the seeds. A rebuild from unchanged inputs writes the
same rows, which `gold_fingerprint` proves.

The Type 2 join: attempt -> `dim_arm` on (study, arm) -> `dim_model_deployment` on
(account_alias, deployment_name) with `valid_from <= started_at < coalesce(valid_to, 9999-12-31)`.
An attempt that resolves to exactly one version lands in `fact_eval_attempt`; one that resolves
to none or to several goes to `reject.eval_unresolved` with a reason. Nothing is dropped.
"""

from datetime import date

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, DoubleType, StringType, StructField, StructType

from lakehouse.paths import LakePaths
from lakehouse.silver.eval_attempts import EVAL_COLUMNS, EVAL_KEY

DATE_FROM = date(2026, 9, 1)  # the lakehouse covers the portfolio from September 2026
OPEN_END = "9999-12-31"
VERDICTS = ("pass", "fail", "not-run")
REJECT_NAME = "reject.eval_unresolved"
REASON_NO_VERSION = "no_version"
REASON_MULTIPLE = "multiple_versions"

GOLD_TABLES = [
    "dim_date",
    "dim_project",
    "dim_study",
    "dim_arm",
    "dim_model_deployment",
    "fact_eval_attempt",
    "fact_daily_cost",
    "fact_live_check",
]

# One row per ... (spec 3.3). Plan 2 renders this into the README.
GRAINS: dict[str, str] = {
    "fact_eval_attempt": "study x item x arm x pass x repeat",
    "fact_daily_cost": "cost-export line: resource x meter x day",
    "fact_live_check": "check x session",
    "dim_model_deployment": "deployment version (Type 2)",
    "dim_project": "project 1-8, plus releaselens-shared and untagged",
    "dim_study": "study run",
    "dim_arm": "arm within a study",
    "dim_date": "day",
    REJECT_NAME: "eval attempt that resolves to no deployment version or to several",
}

# Project names from the portfolio tracker's project table; the two pseudo-projects are
# the attribution fall-backs (lakehouse.attribution).
PROJECTS = [
    ("p1", "Azure OpenAI, keyless"),
    ("p2", "Measured RAG benchmark on Azure AI Search"),
    ("p3", "Foundry agent"),
    ("p4", "API Management as the AI gateway"),
    ("p5", "Azure Functions: ingestion and a search tool"),
    ("p6", "App Service front end"),
    ("p7", "Short-lived build agents on Container Apps jobs"),
    ("p8", "Azure Pipelines delivery for project 2"),
    ("releaselens-shared", "ReleaseLens shared resources"),
    ("untagged", "Untagged spend"),
]

_DIM_ARM_SCHEMA = StructType(
    [StructField(name, StringType()) for name in
     ("study", "arm", "label", "account_alias", "deployment_name", "model")]
)
_LIVE_CHECK_SCHEMA = StructType(
    [
        StructField("project", StringType()),
        StructField("session_date", DateType()),
        StructField("check_id", StringType()),
        StructField("verdict", StringType()),
        StructField("measured_value", DoubleType()),
        StructField("unit", StringType()),
        StructField("source_report", StringType()),
        StructField("source_sha", StringType()),
    ]
)


def _read_seed(spark: SparkSession, path: str, schema: StructType) -> DataFrame:
    return (
        spark.read.option("header", True)
        .option("mode", "FAILFAST")
        .option("enforceSchema", False)
        .schema(schema)
        .csv(path)
    )


_EMPTY_COSTS = (
    "cost_date date, resource_id string, resource_group string, meter_id string, meter_name string, "
    "cost double, billing_currency string, billing_month string, project string, attribution_rule string"
)


def _delta(spark: SparkSession, path: str) -> DataFrame:
    return spark.read.format("delta").load(path)


def _write(df: DataFrame, path: str) -> int:
    """Overwrite the Delta table at `path` with df and return its row count."""
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(path)
    return df.sparkSession.read.format("delta").load(path).count()


def resolve_attempts(attempts: DataFrame, dim_arm: DataFrame, deployments: DataFrame):
    """Split attempts into (resolved, unresolved) by the Type 2 join.

    resolved: EVAL_COLUMNS + deployment_sk + started_date, one row per attempt that matched
    exactly one version. unresolved: EVAL_COLUMNS + reason, for zero or several matches.
    """
    arm = dim_arm.select("study", "arm", "account_alias", "deployment_name").alias("arm")
    dep = deployments.select(
        "account_alias", "deployment_name", "deployment_sk", "valid_from", "valid_to"
    ).alias("dep")
    on_version = (
        (F.col("arm.account_alias") == F.col("dep.account_alias"))
        & (F.col("arm.deployment_name") == F.col("dep.deployment_name"))
        & (F.col("a.started_at") >= F.col("dep.valid_from"))
        & (F.col("a.started_at") < F.coalesce(F.col("dep.valid_to"), F.lit(OPEN_END).cast("timestamp")))
    )
    joined = (
        attempts.alias("a")
        .join(arm, (F.col("a.study") == F.col("arm.study")) & (F.col("a.arm") == F.col("arm.arm")), "left")
        .join(dep, on_version, "left")
        .select(*[F.col(f"a.{c}") for c in EVAL_COLUMNS], F.col("dep.deployment_sk").alias("deployment_sk"))
    )
    per_attempt = Window.partitionBy(*EVAL_KEY)
    counted = joined.withColumn("_matches", F.count("deployment_sk").over(per_attempt))

    resolved = (
        counted.where(F.col("_matches") == 1)
        .withColumn("started_date", F.to_date("started_at"))
        .select(*EVAL_COLUMNS, "deployment_sk", "started_date")
    )
    unresolved = (
        counted.where(F.col("_matches") != 1)
        .withColumn("reason", F.when(F.col("_matches") == 0, REASON_NO_VERSION).otherwise(REASON_MULTIPLE))
        .select(*EVAL_COLUMNS, "reason")
        .dropDuplicates(EVAL_KEY)
    )
    return resolved, unresolved


def _dim_date(spark: SparkSession, last: date) -> DataFrame:
    days = spark.createDataFrame([(DATE_FROM, max(last, DATE_FROM))], "first date, last date").select(
        F.explode(F.sequence("first", "last")).alias("date")
    )
    return days.select(
        "date",
        F.year("date").alias("year"),
        F.month("date").alias("month"),
        F.dayofmonth("date").alias("day"),
        F.date_format("date", "yyyy-MM").alias("year_month"),
        ((F.dayofweek("date") + 5) % 7 + 1).alias("iso_day_of_week")  # Monday = 1,
    )


def _latest(*frames_and_columns) -> date:
    """The latest date over (DataFrame, date column) pairs; DATE_FROM if all are empty."""
    latest = DATE_FROM
    for frame, column in frames_and_columns:
        value = frame.agg(F.max(F.to_date(column))).first()[0]
        if value is not None and value > latest:
            latest = value
    return latest


def build_gold(spark: SparkSession, paths: LakePaths, seeds_dir: str) -> dict[str, int]:
    """Rebuild every gold table and the reject table; return table -> row count.

    Gold tables are keyed by plain name, the reject table by `reject.eval_unresolved`.
    """
    attempts = _delta(spark, paths.table("silver", "eval_attempts"))
    cost_path = paths.table("silver", "daily_cost")
    # No cost export has landed yet on a fresh lake: gold then has an empty cost fact.
    if DeltaTable.isDeltaTable(spark, cost_path):
        costs = _delta(spark, cost_path)
    else:
        costs = spark.createDataFrame([], _EMPTY_COSTS)
    deployments = _delta(spark, paths.table("silver", "model_deployment"))
    dim_arm = _read_seed(spark, f"{seeds_dir}/dim_arm.csv", _DIM_ARM_SCHEMA)
    checks = _read_seed(spark, f"{seeds_dir}/live_checks.csv", _LIVE_CHECK_SCHEMA)

    bad = [r["verdict"] for r in checks.where(~F.col("verdict").isin(*VERDICTS)).distinct().collect()]
    if bad:
        raise ValueError(f"live_checks.csv has verdicts outside {VERDICTS}: {bad}")

    resolved, unresolved = resolve_attempts(attempts, dim_arm, deployments)

    dim_study = attempts.groupBy("study", "run_id").agg(F.min("started_at").alias("started_at"))
    dim_project = spark.createDataFrame(PROJECTS, "project string, name string")
    # R18: the raw resource ID never reaches gold; a short hash of it identifies the resource.
    fact_cost = costs.select(
        "cost_date",
        F.substring(F.sha2(F.lower("resource_id"), 256), 1, 16).alias("resource_key"),
        "resource_group",
        "meter_id",
        "meter_name",
        "cost",
        "billing_currency",
        "project",
        "attribution_rule",
        "billing_month",
    )

    last = _latest((attempts, "started_at"), (costs, "cost_date"), (checks, "session_date"))

    tables = {
        "dim_date": _dim_date(spark, last),
        "dim_project": dim_project,
        "dim_study": dim_study,
        "dim_arm": dim_arm,
        "dim_model_deployment": deployments,
        "fact_eval_attempt": resolved,
        "fact_daily_cost": fact_cost,
        "fact_live_check": checks,
    }
    counts = {name: _write(df, paths.table("gold", name)) for name, df in tables.items()}
    counts[REJECT_NAME] = _write(unresolved, paths.table("reject", "eval_unresolved"))
    return counts
