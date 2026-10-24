"""Silver `gateway_calls` and gold `fact_gateway_call`, from the Eventhouse's OneLake copy (spec 4.4).

The source is the lakehouse shortcut to the Eventhouse table (ruling R1: `Tables/dbo/gateway_calls`).
Silver keeps exactly the contract's fields (`EVENT_FIELDS`), with `event_time` as a UTC timestamp,
drops replay events and keeps one row per `request_id` (ruling R15). The OneLake copy has no
ingestion time, so a duplicate (Event Hubs delivers at least once) is resolved deterministically: the
latest `event_time`, then a fixed ordering of the remaining columns. Silver is MERGEd on `request_id`,
so a re-run writes nothing.

Gold `fact_gateway_call` is one row per call, rebuilt from silver each run, keyed to project 9's
`dim_date` (`call_date`, the UTC date) and `dim_project` (`p4`, the gateway's project). A key the
dimensions lack is an error (ruling R16), never a dropped call. The table is not in project 9's
`GOLD_TABLES`, so its re-run fingerprint never sees this one-off session dataset. Its schema is
`FACT_GATEWAY_CALL_SCHEMA` (project 11's ruling R2): project 9's gold build creates the table empty
with the same schema before any session has run, because the Direct Lake model needs it to exist.
"""

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DateType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from lakehouse.gateway.contract import EVENT_FIELDS
from lakehouse.paths import LakePaths

REPLAY_SESSION = "replay"
GATEWAY_PROJECT = "p4"
SILVER_TABLE = "gateway_calls"
GOLD_TABLE = "fact_gateway_call"
KEY = "request_id"
FIELDS = [name for name, _ in EVENT_FIELDS]
# The contract's types as Spark types (the Eventhouse table uses the same: int is 32-bit).
SPARK_TYPES = {"datetime": "timestamp", "string": "string", "int": "int", "bool": "boolean"}
_SPARK_TYPE_OBJECTS = {"timestamp": TimestampType(), "string": StringType(), "int": IntegerType(),
                       "boolean": BooleanType()}
# Gold `fact_gateway_call` as the writer stores it: the contract's fields, the UTC call date and the
# project. Every column is nullable, as a Delta write from a DataFrame leaves it (ruling R2: the
# writer casts to this schema, and the always-present step creates the empty table with it).
FACT_GATEWAY_CALL_SCHEMA = StructType(
    [StructField(name, _SPARK_TYPE_OBJECTS[SPARK_TYPES[kind]]) for name, kind in EVENT_FIELDS]
    + [StructField("call_date", DateType()), StructField("project", StringType())]
)


def require_utc(spark: SparkSession, what: str) -> None:
    """ValueError unless the session time zone is UTC: dates are cut in the session's zone."""
    zone = spark.conf.get("spark.sql.session.timeZone")
    if zone != "UTC":
        raise ValueError(f"{what} needs a UTC session (spark.sql.session.timeZone), got {zone!r}")


def _delta(spark: SparkSession, path: str, what: str) -> DataFrame:
    if not DeltaTable.isDeltaTable(spark, path):
        raise ValueError(f"{what} not found at {path}")
    return spark.read.format("delta").load(path)


def _typed_events(spark: SparkSession, source_path: str) -> DataFrame:
    """The source's contract columns, typed, with replay events dropped (duplicates kept)."""
    require_utc(spark, "the gateway build")
    source = _delta(spark, source_path, "gateway source table")
    missing = [name for name in FIELDS if name not in source.columns]
    if missing:
        raise ValueError(f"gateway source table lacks contract column(s): {', '.join(missing)}")

    columns = []
    for name, kind in EVENT_FIELDS:
        if name == "event_time" and isinstance(source.schema[name].dataType, StringType):
            # ISO 8601 with an offset (Z or +hh:mm): the parse keeps the instant. Unreadable text
            # becomes null here and is refused below, with a message that names the column.
            columns.append(F.try_to_timestamp(F.col(name)).alias(name))
        else:
            columns.append(F.col(name).cast(SPARK_TYPES[kind]).alias(name))
    # Replay rows go first, so a malformed replay row can never block a session's load.
    typed = source.select(*columns).where(~F.col("session").eqNullSafe(F.lit(REPLAY_SESSION)))

    unreadable = typed.where(F.col("event_time").isNull()).count()
    if unreadable:
        raise ValueError(f"gateway source has {unreadable} row(s) with a null or unreadable event_time")
    return typed


def build_gateway_calls(spark: SparkSession, source_path: str) -> DataFrame:
    """Silver's rows: exactly `EVENT_FIELDS`, no replay events, one row per `request_id` (R15).

    ValueError naming the columns if the source lacks any contract field, or if a non-replay
    row's `event_time` is null or cannot be read.
    """
    events = _typed_events(spark, source_path)
    rest = [name for name in FIELDS if name not in (KEY, "event_time")]
    latest_first = Window.partitionBy(KEY).orderBy(
        F.col("event_time").desc(), *[F.col(name).asc_nulls_first() for name in rest]
    )
    return (
        events.withColumn("_rank", F.row_number().over(latest_first))
        .where(F.col("_rank") == 1)
        .select(*FIELDS)
    )


def count_duplicate_request_ids(spark: SparkSession, source_path: str) -> int:
    """How many non-replay `request_id`s have more than one row in the source (R15's figure)."""
    events = _typed_events(spark, source_path)
    return events.groupBy(KEY).count().where(F.col("count") > 1).count()


def merge_gateway_calls(spark: SparkSession, df: DataFrame, table_path: str) -> dict:
    """MERGE `df` into silver on `request_id`; return `{"inserted": n, "updated": n}`.

    Creates the table if it is absent. A matched row is updated only when some column differs
    (null-safe), so re-running the same input inserts 0 and updates 0.
    """
    if not DeltaTable.isDeltaTable(spark, table_path):
        df.write.format("delta").save(table_path)
        return {"inserted": spark.read.format("delta").load(table_path).count(), "updated": 0}
    changed = " OR ".join(f"NOT (t.{c} <=> s.{c})" for c in FIELDS if c != KEY)
    metrics = (
        DeltaTable.forPath(spark, table_path).alias("t")
        .merge(df.alias("s"), f"t.{KEY} = s.{KEY}")
        .whenMatchedUpdateAll(condition=changed)
        .whenNotMatchedInsertAll()
        .execute()
        .first()
    )
    return {"inserted": int(metrics["num_inserted_rows"]), "updated": int(metrics["num_updated_rows"])}


def build_fact_gateway_call(spark: SparkSession, paths: LakePaths) -> int:
    """Overwrite gold `fact_gateway_call` from silver; return its row count.

    Grain: one gateway call. Columns: the contract's fields, `call_date` (the UTC date of
    `event_time`) and `project` (`p4`). ValueError (R16) if gold `dim_project` lacks `p4` or gold
    `dim_date` lacks a call date, naming the dates: run project 9's load for the cost day first.
    """
    require_utc(spark, "fact_gateway_call")
    silver = _delta(spark, paths.table("silver", SILVER_TABLE), "silver gateway_calls")
    dim_date = _delta(spark, paths.table("gold", "dim_date"), "gold dim_date (run project 9's load first)")
    dim_project = _delta(
        spark, paths.table("gold", "dim_project"), "gold dim_project (run project 9's load first)"
    )

    if dim_project.where(F.col("project") == GATEWAY_PROJECT).limit(1).count() == 0:
        raise ValueError(f"gold dim_project has no {GATEWAY_PROJECT!r}: run project 9's load first")

    built = silver.select(
        *FIELDS, F.to_date("event_time").alias("call_date"), F.lit(GATEWAY_PROJECT).alias("project")
    )
    # Ruling R2: the columns and types come from the one schema the always-present step also uses.
    facts = built.select(*[F.col(f.name).cast(f.dataType).alias(f.name) for f in FACT_GATEWAY_CALL_SCHEMA])
    missing = (
        facts.select("call_date")
        .distinct()
        .join(dim_date.select(F.col("date").alias("call_date")), "call_date", "left_anti")
        .orderBy("call_date")
        .collect()
    )
    if missing:
        days = ", ".join(r["call_date"].isoformat() for r in missing)
        raise ValueError(
            f"gold dim_date lacks call date(s) {days}: run project 9's load for the cost day first"
        )

    path = paths.table("gold", GOLD_TABLE)
    facts.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(path)
    return spark.read.format("delta").load(path).count()
