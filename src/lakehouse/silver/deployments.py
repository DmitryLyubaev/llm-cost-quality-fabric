"""Silver model deployments: a Type 2 slowly changing dimension from Terraform git history.

`seeds/deployment_changes.csv` lists one row per change to a deployment (a Terraform commit, or a
manual row where git has no record). `scd2_rows` turns those changes into versions with
`valid_from`, `valid_to` and `is_current`; `apply_scd2` MERGEs them into a Delta table, so
re-applying the same changes writes nothing. Timestamps are UTC (session time zone UTC).
"""

from datetime import UTC, datetime
from functools import reduce
from operator import or_
from pathlib import Path

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

DEPLOYMENT_KEY = ["account_alias", "deployment_name"]
TRACKED = ["model_name", "model_version", "sku", "capacity", "region_alias"]

EXTERNAL_ALIAS = "external"  # models called outside Azure (dim_arm account_alias)
NONE_ALIAS = "none"  # arms that call no model
NO_MODEL = "no-model"
OPEN_FROM = datetime(1900, 1, 1, tzinfo=UTC)  # members that exist "always" start here

SEEDS_DIR = str(Path(__file__).resolve().parents[3] / "seeds")

# Times are read as ISO strings (with any offset) and converted to UTC timestamps in scd2_rows.
CHANGES_SCHEMA = StructType(
    [
        StructField("account_alias", StringType()),
        StructField("deployment_name", StringType()),
        StructField("model_name", StringType()),
        StructField("model_version", StringType()),
        StructField("sku", StringType()),
        StructField("capacity", LongType()),
        StructField("region_alias", StringType()),
        StructField("committed_at", StringType()),
        StructField("effective_from_override", StringType()),
        StructField("source", StringType()),
        StructField("repo", StringType()),
        StructField("sha", StringType()),
        StructField("path", StringType()),
        StructField("evidence", StringType()),
    ]
)
_DIM_ARM_SCHEMA = StructType(
    [StructField(name, StringType()) for name in
     ("study", "arm", "label", "account_alias", "deployment_name", "model")]
)

DIM_SCHEMA = StructType(
    [
        StructField("deployment_sk", StringType()),
        *[f for f in CHANGES_SCHEMA.fields if f.name in DEPLOYMENT_KEY + TRACKED],
        StructField("valid_from", TimestampType()),
        StructField("valid_to", TimestampType()),
        StructField("is_current", BooleanType()),
    ]
)
DIM_COLUMNS = DIM_SCHEMA.fieldNames()
_SK_TIME = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"  # valid_from as UTC text; the session is UTC


def read_seed(spark: SparkSession, path: str, schema: StructType) -> DataFrame:
    """A seed CSV read strictly with `schema`: a malformed row or a header mismatch fails the read."""
    return (
        spark.read.option("header", True)
        .option("mode", "FAILFAST")
        .option("enforceSchema", False)
        .schema(schema)
        .csv(path)
    )


def read_dim_arm(spark: SparkSession, seeds_dir: str) -> DataFrame:
    """`seeds/dim_arm.csv` (all columns strings), read strictly."""
    return read_seed(spark, f"{seeds_dir}/dim_arm.csv", _DIM_ARM_SCHEMA)


def read_deployment_changes(spark: SparkSession, path: str) -> DataFrame:
    """The change seed, read with CHANGES_SCHEMA; a malformed row or header fails the read."""
    return read_seed(spark, path, CHANGES_SCHEMA)


def _members(spark: SparkSession, seeds_dir: str) -> DataFrame:
    """One open row per external model in dim_arm, plus the no-model member."""
    dim_arm = read_dim_arm(spark, seeds_dir)
    external = (
        dim_arm.where(F.col("account_alias") == EXTERNAL_ALIAS)
        .select(
            F.col("account_alias"),
            F.col("deployment_name"),
            F.col("model").alias("model_name"),
        )
        .distinct()
    )
    none = spark.createDataFrame(
        [(NONE_ALIAS, NO_MODEL, NO_MODEL)], "account_alias string, deployment_name string, model_name string"
    )
    members = external.unionByName(none)
    for field in DIM_SCHEMA.fields:
        if field.name not in members.columns and field.name not in ("deployment_sk", "valid_to", "is_current"):
            members = members.withColumn(field.name, F.lit(None).cast(field.dataType))
    return members.withColumn("valid_from", F.lit(OPEN_FROM).cast("timestamp"))


def _with_sk(df: DataFrame) -> DataFrame:
    text = F.concat_ws("|", *DEPLOYMENT_KEY, F.date_format("valid_from", _SK_TIME))
    return df.withColumn("deployment_sk", F.substring(F.sha2(text, 256), 1, 16))


def scd2_rows(changes: DataFrame, seeds_dir: str = SEEDS_DIR) -> DataFrame:
    """Versions (DIM_COLUMNS) from a change list, plus the external and no-model members.

    Per key, changes are ordered by effective time (`effective_from_override`, else
    `committed_at`) and a change that repeats the previous attributes is dropped. A version ends
    when the next one starts; the last is open (`valid_to` null, `is_current` true).
    """
    spark = changes.sparkSession
    effective = F.to_timestamp(F.coalesce("effective_from_override", "committed_at"))
    by_key = Window.partitionBy(*DEPLOYMENT_KEY).orderBy("valid_from", *TRACKED)

    staged = changes.select(*DEPLOYMENT_KEY, *TRACKED, effective.alias("valid_from"))
    differs = [~F.col(c).eqNullSafe(F.lag(c).over(by_key)) for c in TRACKED]
    first_or_changed = reduce(or_, differs, F.row_number().over(by_key) == 1)
    kept = (
        staged.withColumn("_keep", first_or_changed)
        .where("_keep")
        .drop("_keep")
        .withColumn("valid_to", F.lead("valid_from").over(by_key))
        .withColumn("is_current", F.col("valid_to").isNull())
    )
    versions = _with_sk(kept)
    members = _with_sk(
        _members(spark, seeds_dir)
        .withColumn("valid_to", F.lit(None).cast("timestamp"))
        .withColumn("is_current", F.lit(True))
    )
    return versions.select(*DIM_COLUMNS).unionByName(members.select(*DIM_COLUMNS))


def _check(df: DataFrame) -> None:
    """Refuse versions the MERGE cannot place: no effective time, or two at the same instant."""
    if df.where(F.col("valid_from").isNull()).limit(1).count():
        raise ValueError("a change has neither effective_from_override nor committed_at")
    clash = df.groupBy("deployment_sk").count().where("count > 1").limit(1).collect()
    if clash:
        raise ValueError(
            f"two different versions of one deployment share the same valid_from (sk {clash[0][0]})"
        )


def apply_scd2(spark: SparkSession, changes: DataFrame, table_path: str, seeds_dir: str = SEEDS_DIR) -> None:
    """Make the Delta table at `table_path` exactly equal to `scd2_rows(changes)` (created if absent).

    One MERGE on `deployment_sk`: a matched version is updated (every column) only when some
    column differs, null-safe; an unmatched one is inserted; a table row the changes no longer
    produce (an override moved its `valid_from`, so its sk changed) is deleted. Re-applying the
    same changes therefore inserts, updates and deletes nothing.
    """
    df = scd2_rows(changes, seeds_dir)
    _check(df)
    if not DeltaTable.isDeltaTable(spark, table_path):
        df.write.format("delta").save(table_path)
        return
    changed = " OR ".join(f"NOT (t.{c} <=> s.{c})" for c in DIM_COLUMNS if c != "deployment_sk")
    (
        DeltaTable.forPath(spark, table_path).alias("t")
        .merge(df.alias("s"), "t.deployment_sk <=> s.deployment_sk")
        .whenMatchedUpdateAll(condition=changed)
        .whenNotMatchedInsertAll()
        .whenNotMatchedBySourceDelete()
        .execute()
    )
