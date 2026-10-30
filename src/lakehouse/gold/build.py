"""Gold star schema: dimensions, three facts and the reject table, rebuilt in full each run.

The data is tiny (about 1,760 attempts, a month of cost lines, under a hundred checks), so every
gold table is overwritten from silver and the seeds. A rebuild from unchanged inputs writes the
same rows, which `gold_fingerprint` proves.

The Type 2 join: attempt -> `dim_arm` on (study, arm) -> `dim_model_deployment` on
(account_alias, deployment_name) with `valid_from <= started_at < coalesce(valid_to, 9999-12-31)`.
An attempt that resolves to exactly one version lands in `fact_eval_attempt`; one that resolves
to none or to several goes to `reject.eval_unresolved` with a reason. Nothing is dropped.

Keys for relationships: `dim_study.study_key` (`study|run_id`) and `dim_arm.arm_key` (`study|arm`)
are single-column keys that `fact_eval_attempt` carries as `study_key` and `arm_key` (with
`deployment_sk` already there). Each of the three also carries `project`, read from the
`study_projects.csv` seed; a study the seed does not name fails the build.

Always-present tables (project 11): the Direct Lake model errors on a missing table, so the build
also creates `fact_gateway_call` and `security.user_project` empty when they are absent
(`lakehouse.security.ensure_table`). It never touches them once they exist, so project 10's
gateway load and the access loader keep their rows, and neither is in `GOLD_TABLES`, the counts
or the re-run fingerprint.
"""

from datetime import date

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, DoubleType, StringType, StructField, StructType

from lakehouse.gateway.build import FACT_GATEWAY_CALL_SCHEMA
from lakehouse.gateway.build import GOLD_TABLE as GATEWAY_GOLD_TABLE
from lakehouse.paths import LakePaths
from lakehouse.security import USER_PROJECT_LAYER, USER_PROJECT_SCHEMA, USER_PROJECT_TABLE, ensure_table
from lakehouse.seeding import read_study_projects, seed_df
from lakehouse.silver.deployments import read_dim_arm
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

# Gold tables built by project 10's gateway load (lakehouse.gateway.build) after a session, not by
# build_gold: they stay out of GOLD_TABLES, so project 9's re-run fingerprint never sees them.
SESSION_TABLES = [GATEWAY_GOLD_TABLE]
# The access table for row-level security (lakehouse.security); never in git, never fingerprinted.
USER_PROJECT_NAME = f"{USER_PROJECT_LAYER}.{USER_PROJECT_TABLE}"

# One row per ... (spec 3.3). Plan 2 renders this into the README.
GRAINS: dict[str, str] = {
    "fact_eval_attempt": "study x item x arm x pass x repeat",
    "fact_daily_cost": "cost-export line: resource x meter x day",
    "fact_live_check": "check x session",
    "fact_gateway_call": "gateway call",
    "dim_model_deployment": "deployment version (Type 2)",
    "dim_project": "project 1-10, plus capstone, releaselens-shared and untagged",
    "dim_study": "study run",
    "dim_arm": "arm within a study",
    "dim_date": "day",
    REJECT_NAME: "eval attempt that resolves to no deployment version or to several",
    USER_PROJECT_NAME: "user (UPN) x project the user may see",
}

# Project names from the portfolio tracker's project table; `capstone` is the phase 4 capstone
# (infra/capstone); the last two are the attribution fall-backs (lakehouse.attribution).
PROJECTS = [
    ("p1", "Azure OpenAI, keyless"),
    ("p2", "Measured RAG benchmark on Azure AI Search"),
    ("p3", "Foundry agent"),
    ("p4", "API Management as the AI gateway"),
    ("p5", "Azure Functions: ingestion and a search tool"),
    ("p6", "App Service front end"),
    ("p7", "Short-lived build agents on Container Apps jobs"),
    ("p8", "Azure Pipelines delivery for project 2"),
    ("p9", "Fabric lakehouse and batch pipelines"),
    ("p10", "Fabric Real-Time Intelligence"),
    ("capstone", "Phase 4 capstone: data agent"),
    ("releaselens-shared", "ReleaseLens shared resources"),
    ("untagged", "Untagged spend"),
]

_LIVE_CHECK_SCHEMA = StructType(
    [
        StructField("project", StringType()),
        StructField("session_date", DateType()),
        StructField("session", StringType()),
        StructField("check_id", StringType()),
        StructField("verdict", StringType()),
        StructField("measured_value", DoubleType()),
        StructField("unit", StringType()),
        StructField("source_report", StringType()),
        StructField("source_sha", StringType()),
    ]
)


def _silver(spark: SparkSession, path: str) -> DataFrame:
    """A silver Delta table; a missing one is an error that names the path."""
    if not DeltaTable.isDeltaTable(spark, path):
        raise ValueError(f"silver table not found at {path}: build silver before gold")
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


def _project_map(mapping: dict[str, str]):
    """A Spark map column expression study -> project."""
    return F.create_map(*[F.lit(x) for pair in mapping.items() for x in pair])


def _require_projects(mapping: dict[str, str], *named_frames: tuple[str, DataFrame]) -> None:
    """ValueError unless every study in the (name, frame) pairs is one the seed maps to a project.

    A null or blank study is refused, naming the frame. A study with leading or trailing spaces is
    refused as such, not stripped: the project lookup (`_project_map`) matches the study exactly,
    so a stripped comparison would pass a padded study here and leave its rows with a null project.
    The seed's own studies are stripped when it is read (`read_study_projects`).
    """
    studies: set[str] = set()
    for name, frame in named_frames:
        found = {r["study"] for r in frame.select("study").distinct().collect()}
        if any(study is None or not study.strip() for study in found):
            raise ValueError(f"rows with a null or blank study in {name}")
        studies |= found
    padded = sorted(s for s in studies if s != s.strip())
    if padded:
        raise ValueError(f"study names with leading or trailing spaces: {padded!r}")
    missing = sorted(s for s in studies if s not in mapping)
    if missing:
        raise ValueError(f"study_projects.csv does not name the project of study {missing}")


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


def optimize_tables(spark: SparkSession, paths: list[str]) -> None:
    """Compact the Delta table at each path (`OPTIMIZE`): small files become fewer, larger ones.

    A Direct Lake on OneLake table with more than 1,000 Parquet files gives an error instead of
    falling back, and the daily Delta MERGEs keep adding files, so every load compacts the tables
    the semantic model reads. Compaction rewrites files, not rows: a content fingerprint, as
    project 9's re-run check uses, is the same before and after.
    """
    for path in paths:
        DeltaTable.forPath(spark, path).optimize().executeCompaction()


def build_gold(spark: SparkSession, paths: LakePaths, seeds_dir: str) -> dict[str, int]:
    """Rebuild every gold table and the reject table; return table -> row count.

    Gold tables are keyed by plain name, the reject table by `reject.eval_unresolved`. Then creates
    the always-present tables (`SESSION_TABLES` and `security.user_project`) empty if absent; they
    are never overwritten here and are not in the returned counts.
    """
    attempts = _silver(spark, paths.table("silver", "eval_attempts"))
    costs = _silver(spark, paths.table("silver", "daily_cost"))
    deployments = _silver(spark, paths.table("silver", "model_deployment"))
    dim_arm = read_dim_arm(spark, seeds_dir)
    study_projects = read_study_projects(seeds_dir)
    checks = seed_df(spark, seeds_dir, "live_checks.csv", _LIVE_CHECK_SCHEMA)

    # A blank cell is null, and `isin` is null for null, so a null verdict needs its own guard.
    blank = checks.where(F.col("verdict").isNull() | (F.trim("verdict") == "")).collect()
    if blank:
        rows = sorted(f"{r['project']}/{r['session']}/{r['check_id']}" for r in blank)
        raise ValueError(f"live_checks.csv has rows with a null or empty verdict (project/session/check): {rows}")
    bad = [r["verdict"] for r in checks.where(~F.col("verdict").isin(*VERDICTS)).distinct().collect()]
    if bad:
        raise ValueError(f"live_checks.csv has verdicts outside {VERDICTS}: {bad}")

    _require_projects(study_projects, ("silver eval_attempts", attempts), ("dim_arm.csv", dim_arm))
    project_of = _project_map(study_projects)
    study_key = F.concat_ws("|", "study", "run_id")
    arm_key = F.concat_ws("|", "study", "arm")

    resolved, unresolved = resolve_attempts(attempts, dim_arm, deployments)
    resolved = resolved.select(
        "*", study_key.alias("study_key"), arm_key.alias("arm_key"), project_of[F.col("study")].alias("project")
    )
    dim_arm = dim_arm.select("*", arm_key.alias("arm_key"), project_of[F.col("study")].alias("project"))

    dim_study = (
        attempts.groupBy("study", "run_id")
        .agg(F.min("started_at").alias("started_at"))
        .select("*", study_key.alias("study_key"), project_of[F.col("study")].alias("project"))
    )
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
        "pricing_currency",
        "fx_pricing_to_billing",
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
    # Last, so a failed build above leaves them as they were. Created only when absent.
    ensure_table(spark, paths.table("gold", GATEWAY_GOLD_TABLE), FACT_GATEWAY_CALL_SCHEMA)
    ensure_table(spark, paths.table(USER_PROJECT_LAYER, USER_PROJECT_TABLE), USER_PROJECT_SCHEMA)
    return counts
