"""The gold answers: cost per project, quality across models and cost per measured result.

Costs stay in their own currency. Repo estimates are USD list-price figures; Cost Management is
in its billing currency. They sit in separate columns, each with its own currency column, and
nothing here converts one into the other.
"""

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from lakehouse.attribution import SHARED_PROJECT, UNTAGGED
from lakehouse.paths import LakePaths

SCORED = "scored"
ESTIMATE_CURRENCY = "USD"
NO_MODEL_BASIS = "no_model"  # cost_basis of an arm with no model: its cost is a true zero
ALWAYS_PRESENT = [UNTAGGED, SHARED_PROJECT]
# The project each study measured (dim_study has no project column; the tracker's names).
STUDY_PROJECT = {"p1-three-arm": "p1", "p2-retrieval": "p2", "p3-agent": "p3"}


def _gold(spark: SparkSession, paths: LakePaths, name: str) -> DataFrame:
    return spark.read.format("delta").load(paths.table("gold", name))


def _project_totals(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """project, billing_currency, total from fact_daily_cost; `untagged` and the shared project
    are always present, with a zero total for each currency in the data that has none."""
    fact = _gold(spark, paths, "fact_daily_cost")
    totals = fact.groupBy("project", "billing_currency").agg(F.sum("cost").alias("total"))
    currencies = fact.select("billing_currency").distinct()
    if currencies.limit(1).count() == 0:
        currencies = spark.createDataFrame([(None,)], "billing_currency string")
    required = spark.createDataFrame([(p,) for p in ALWAYS_PRESENT], "project string").crossJoin(currencies)
    zeros = required.join(totals.select("project", "billing_currency"), ["project", "billing_currency"], "left_anti")
    return totals.unionByName(zeros.withColumn("total", F.lit(0.0)))


def cost_per_project(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Attributed Cost Management spend: project, billing_currency, total (billing currency)."""
    return _project_totals(spark, paths).orderBy("project", "billing_currency")


def quality_across_models(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Scored results per study, arm and deployment version, with each metric's mean.

    `model` and `model_version` come from the deployment version the result resolved to; an
    arm outside Azure (no version recorded in the dimension) shows the model version the study
    itself recorded. Quality is only comparable within a study.
    """
    facts = (
        _gold(spark, paths, "fact_eval_attempt")
        .where(F.col("attempt_kind") == SCORED)
        .withColumnRenamed("model_version", "recorded_model_version")
        .drop("model")  # null for P2, dim_arm's name for P1; the dimension's is used instead
    )
    deployments = _gold(spark, paths, "dim_model_deployment").select(
        "deployment_sk",
        F.col("model_name").alias("model"),
        F.col("model_version").alias("dim_model_version"),
    )
    joined = facts.join(deployments, "deployment_sk", "inner").withColumn(
        "model_version", F.coalesce("dim_model_version", "recorded_model_version")
    )
    return (
        joined.groupBy("study", "arm", "model", "model_version")
        .agg(
            F.count("*").alias("n"),
            F.avg(F.col("success").cast("double")).alias("success_rate"),
            F.avg("latency_ms").alias("latency_ms"),
            F.avg("groundedness").alias("groundedness"),
            F.avg("citation_recall").alias("citation_recall"),
            F.avg("citation_precision").alias("citation_precision"),
            F.avg(F.col("must_contain").cast("double")).alias("must_contain_rate"),
            F.avg(F.col("top1").cast("double")).alias("top1_rate"),
            F.avg("reciprocal_rank").alias("mrr"),
            F.avg("tool_call_accuracy").alias("tool_call_accuracy"),
            F.avg("task_adherence").alias("task_adherence"),
            F.avg("intent_resolution").alias("intent_resolution"),
        )
        .orderBy("study", "arm", "model_version")
    )


def cost_per_result(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Per project, estimated and actual cost per scored result, side by side.

    estimated: the studies' own USD figures (`estimated_currency` is USD).
    actual: the project's attributed Cost Management total in its billing currency
    (`actual_currency`); a project billed in two currencies gets one row per currency. The
    two are never added, converted or divided into one another.

    `unpriced_results` counts the scored results with no cost and a basis other than `no_model`
    (a model without a rate). When it is above zero the estimate is unknown, so the estimated
    total and per-result values are null rather than a sum that leaves those results out.
    """
    project = F.create_map(*[x for pair in STUDY_PROJECT.items() for x in (F.lit(pair[0]), F.lit(pair[1]))])
    facts = _gold(spark, paths, "fact_eval_attempt").where(F.col("attempt_kind") == SCORED)
    estimated = (
        facts.withColumn("project", project[F.col("study")])
        .groupBy("project")
        .agg(
            F.count("*").alias("results"),
            F.count(F.when(F.col("cost_usd").isNull() & (F.col("cost_basis") != NO_MODEL_BASIS), 1)).alias(
                "unpriced_results"
            ),
            F.sum("cost_usd").alias("estimated_usd_total"),
        )
        .withColumn(
            "estimated_usd_total",
            F.when(F.col("unpriced_results") == 0, F.col("estimated_usd_total")),
        )
        .withColumn("estimated_usd_per_result", F.col("estimated_usd_total") / F.col("results"))
        .withColumn("estimated_currency", F.lit(ESTIMATE_CURRENCY))
    )
    actual = _project_totals(spark, paths).select(
        "project",
        F.col("total").alias("actual_total"),
        F.col("billing_currency").alias("actual_currency"),
    )
    return (
        estimated.join(actual, "project", "left")
        .withColumn("actual_per_result", F.col("actual_total") / F.col("results"))
        .select(
            "project",
            "results",
            "unpriced_results",
            "estimated_usd_total",
            "estimated_usd_per_result",
            "estimated_currency",
            "actual_total",
            "actual_per_result",
            "actual_currency",
        )
        .orderBy("project", "actual_currency")
    )
