"""The gateway's answer: cost per gateway call for the session's day (spec 4.4, ruling R14)."""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse.gateway.build import GOLD_TABLE
from lakehouse.paths import LakePaths

# The API Management gateway's resource group (project 4); its cost-export lines are the gateway's cost.
GATEWAY_RESOURCE_GROUP = "rg-releaselens-gateway"


def _gold(spark: SparkSession, paths: LakePaths, name: str) -> DataFrame:
    return spark.read.format("delta").load(paths.table("gold", name))


def cost_per_gateway_call(spark: SparkSession, paths: LakePaths, session: str) -> DataFrame:
    """One row per (UTC call date of the session's calls, billing currency).

    `calls` counts every call of the session that day, refused ones included, and `refused` those
    with `refused_by_gateway` true (0 when none; ruling R5, mirrored by the model's
    `Refused by gateway`). `tokens_total` is
    the day's tokens and `tokens_per_call` that over `calls` (R14: the event has no model, so the
    tokens are not priced). `apim_cost` is the day's gold `fact_daily_cost.cost` for
    `rg-releaselens-gateway` (compared case-insensitively), and `cost_per_call` that over `calls`.

    Computed the day after the session, because the cost export lags up to a day. A day with no
    cost rows yet keeps its row, with `billing_currency`, `apim_cost` and `cost_per_call` null.
    Costs stay in the billing currency, one row per currency; nothing is converted. A day billed
    in more than one currency has one row per currency, and `calls`, `refused`, `tokens_total` and
    `tokens_per_call` repeat on each of them: never sum them across rows.
    """
    calls = (
        _gold(spark, paths, GOLD_TABLE)
        .where(F.col("session") == session)
        .groupBy("call_date")
        .agg(
            F.count("*").alias("calls"),
            F.count(F.when(F.col("refused_by_gateway"), 1)).alias("refused"),
            F.sum("tokens").alias("tokens_total"),
        )
    )
    costs = (
        _gold(spark, paths, "fact_daily_cost")
        .where(F.lower(F.col("resource_group")) == GATEWAY_RESOURCE_GROUP)
        .groupBy(F.col("cost_date").alias("call_date"), "billing_currency")
        .agg(F.sum("cost").alias("apim_cost"))
    )
    return (
        calls.join(costs, "call_date", "left")
        .withColumn("tokens_per_call", F.col("tokens_total") / F.col("calls"))
        .withColumn("cost_per_call", F.col("apim_cost") / F.col("calls"))
        .select(
            "call_date",
            "calls",
            "refused",
            "tokens_total",
            "tokens_per_call",
            "billing_currency",
            "apim_cost",
            "cost_per_call",
        )
        .orderBy("call_date", "billing_currency")
    )
