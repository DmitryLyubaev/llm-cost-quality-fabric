"""Silver eval attempts: one row per attempt, one schema for P1, P2 and P3.

P1 and P2 come from the sanitised private extract; P3 comes from the public study files in
bronze. Rows are merged into a Delta table keyed on EVAL_KEY, so a rerun changes nothing.
"""

import json
import re
from datetime import UTC, date, datetime

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

P1_STUDY = "p1-three-arm"
P2_STUDY = "p2-retrieval"
P3_STUDY = "p3-agent"

EVAL_KEY = ["study", "item_id", "arm", "pass_index", "attempt_kind"]
EVAL_SCHEMA = StructType(
    [
        StructField("study", StringType()),
        StructField("item_id", StringType()),
        StructField("arm", StringType()),
        StructField("pass_index", IntegerType()),
        StructField("attempt_kind", StringType()),
        StructField("run_id", StringType()),
        StructField("started_at", TimestampType()),
        StructField("provider", StringType()),
        StructField("model", StringType()),
        StructField("model_version", StringType()),
        StructField("success", BooleanType()),
        StructField("latency_ms", DoubleType()),
        StructField("tokens_in", LongType()),
        StructField("tokens_out", LongType()),
        StructField("cached_tokens", LongType()),
        StructField("cost_usd", DoubleType()),
        StructField("cost_basis", StringType()),
        StructField("groundedness", DoubleType()),
        StructField("citation_recall", DoubleType()),
        StructField("citation_precision", DoubleType()),
        StructField("must_contain", BooleanType()),
        StructField("top1", BooleanType()),
        StructField("reciprocal_rank", DoubleType()),
        StructField("target_rank", IntegerType()),
        StructField("tool_call_accuracy", DoubleType()),
        StructField("task_adherence", DoubleType()),
        StructField("intent_resolution", DoubleType()),
        StructField("load_date", DateType()),
    ]
)
EVAL_COLUMNS = EVAL_SCHEMA.fieldNames()

NO_MODEL = "no-model"  # the dim_arm model of arms that call no model (P2 E1 and S1)
P3_PROVIDER = "azure"  # both P3 engines ran in Azure Foundry (ruling R1)
P3_EVALUATORS = ("tool_call_accuracy", "task_adherence", "intent_resolution")

# Source schemas of the private extract (Task 2). Columns a study lacks are simply null.
_P1_SOURCE = StructType(
    [
        StructField("study", StringType()),
        StructField("run_id", StringType()),
        StructField("started_at", StringType()),
        StructField("item_id", StringType()),
        StructField("arm", StringType()),
        StructField("pass_index", IntegerType()),
        StructField("attempt_kind", StringType()),
        StructField("provider", StringType()),
        StructField("model", StringType()),
        StructField("success", BooleanType()),
        StructField("latency_ms", DoubleType()),
        StructField("tokens_in", LongType()),
        StructField("tokens_out", LongType()),
        StructField("cached_tokens", LongType()),
        StructField("cost_usd", DoubleType()),
        StructField("judge_cost_usd", DoubleType()),
        StructField("groundedness", DoubleType()),
        StructField("citation_recall", DoubleType()),
        StructField("citation_precision", DoubleType()),
        StructField("must_contain", BooleanType()),
    ]
)
_P2_SOURCE = StructType(
    [
        StructField("study", StringType()),
        StructField("run_id", StringType()),
        StructField("started_at", StringType()),
        StructField("item_id", StringType()),
        StructField("arm", StringType()),
        StructField("pass_index", IntegerType()),
        StructField("attempt_kind", StringType()),
        StructField("success", BooleanType()),
        StructField("latency_ms", DoubleType()),
        StructField("tokens_in", LongType()),
        StructField("target_rank", IntegerType()),
        StructField("top1", BooleanType()),
        StructField("reciprocal_rank", DoubleType()),
    ]
)
_RATES_SCHEMA = StructType(
    [
        StructField("model", StringType()),
        StructField("input_per_m_usd", DoubleType()),
        StructField("output_per_m_usd", DoubleType()),
        StructField("rate_date", StringType()),
        StructField("source", StringType()),
    ]
)


def estimate_cost(
    model: str, tokens_in: int, tokens_out: int, rates: dict[str, tuple[float, float]]
) -> float:
    """USD cost of the tokens at the model's (input, output) price per million tokens."""
    rate_in, rate_out = rates[model]
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


def parse_p3_run(run: dict, scores: dict | None, attempt_kind: str) -> dict:
    """One silver row (without cost, run_id and load_date) from a P3 run file.

    `scores` is the `scores` mapping of the matching eval-scores row, or None (superseded
    first attempts are not scored). `started_at` is returned as the file's own ISO string.
    """
    calls = run.get("calls") or []
    scores = (scores or {}).get("scores", scores or {})
    row = {
        "study": P3_STUDY,
        "item_id": run["scenarioId"],
        "arm": run["engine"],
        "pass_index": run["pass"],
        "attempt_kind": attempt_kind,
        "started_at": run["startedAt"],
        "provider": P3_PROVIDER,
        "model": run["model"],
        "model_version": calls[0].get("modelId") if calls else None,
        "success": run["success"],
        "latency_ms": run["ms"],
        "tokens_in": sum(c["inputTokens"] for c in calls),
        "tokens_out": sum(c["outputTokens"] for c in calls),
    }
    for evaluator in P3_EVALUATORS:
        row[evaluator] = (scores.get(evaluator) or {}).get("score")
    return row


def _utc(iso: str) -> datetime:
    """Parse an ISO timestamp (any number of fractional digits, any offset) to UTC."""
    moment = datetime.fromisoformat(iso)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _pass_no(value) -> int:
    return int(str(value).lstrip("pP"))


_P3_PATH = re.compile(r"/results/(?P<study>[^/]+)/(?P<rest>.+)$")
_P3_SCORED = re.compile(r"s\d+\.[a-z]+\.p\d+\.json")
_P3_FIRST = re.compile(r"repeats/s\d+\.[a-z]+\.p\d+\.first-attempt\.json")


def _read_p3_rows(spark: SparkSession, bronze_root: str, load_date: date) -> list[dict]:
    results = (
        f"{bronze_root}/public/load_date={load_date.isoformat()}"
        "/foundry-workshop-agent/results"
    )
    files = (
        spark.read.format("binaryFile")
        .option("recursiveFileLookup", "true")
        .option("pathGlobFilter", "*.json")
        .load(results)
        .select("path", "content")
        .collect()
    )
    runs: list[tuple[str, str, dict]] = []  # (study dir, attempt kind, run json)
    score_index: dict[tuple, dict] = {}

    for f in files:
        match = _P3_PATH.search(f["path"])
        if not match:
            continue
        study_dir, rest = match["study"], match["rest"]
        body = json.loads(bytes(f["content"]).decode("utf-8"))
        if rest == "eval-scores.json":
            for r in body["rows"]:
                key = (study_dir, r["scenario_id"], r["engine"], _pass_no(r["pass"]))
                score_index[key] = r["scores"]
        elif _P3_SCORED.fullmatch(rest):
            runs.append((study_dir, "scored", body))
        elif _P3_FIRST.fullmatch(rest):
            runs.append((study_dir, "superseded", body))

    rows = []
    for study_dir, kind, body in sorted(runs, key=lambda r: (r[0], r[1], r[2]["scenarioId"],
                                                              r[2]["engine"], r[2]["pass"])):
        scores = None
        if kind == "scored":
            scores = score_index.get(
                (study_dir, body["scenarioId"], body["engine"], _pass_no(body["pass"]))
            )
        row = parse_p3_run(body, scores, kind)
        row["run_id"] = study_dir
        row["started_at"] = _utc(row["started_at"])
        rows.append(row)
    return rows


def _to_schema_tuple(row: dict) -> tuple:
    """Row values in EVAL_SCHEMA order; JSON integers bound for DoubleType become floats."""
    values = []
    for field in EVAL_SCHEMA.fields:
        value = row.get(field.name)
        if value is not None and isinstance(field.dataType, DoubleType):
            value = float(value)
        values.append(value)
    return tuple(values)


def _stage(df: DataFrame) -> DataFrame:
    """Add the columns a study lacks as typed nulls and order them as EVAL_COLUMNS."""
    for field in EVAL_SCHEMA.fields:
        if field.name not in df.columns:
            df = df.withColumn(field.name, F.lit(None).cast(field.dataType))
    return df.select(*EVAL_COLUMNS)


def build_eval_attempts(
    spark: SparkSession, bronze_root: str, private_root: str, seeds_dir: str, load_date: date
) -> DataFrame:
    """P1, P2 and P3 attempts in one schema (EVAL_COLUMNS), costs filled, UTC timestamps."""
    p1 = (
        spark.read.schema(_P1_SOURCE).option("mode", "FAILFAST").json(f"{private_root}/p1_outcomes.jsonl")
        .withColumn("started_at", F.to_timestamp("started_at"))
        .withColumn("model_version", F.col("model"))  # R1: the outcome's own model string
        .withColumn("model", F.lit(None).cast("string"))  # filled from dim_arm below
        .withColumn(
            "cost_usd",
            F.when(
                F.col("cost_usd").isNull() & F.col("judge_cost_usd").isNull(), F.lit(None)
            ).otherwise(F.coalesce("cost_usd", F.lit(0.0)) + F.coalesce("judge_cost_usd", F.lit(0.0))),
        )
        .withColumn("cost_basis", F.lit("recorded"))
        .drop("judge_cost_usd")
    )
    p2 = (
        spark.read.schema(_P2_SOURCE).option("mode", "FAILFAST").json(f"{private_root}/p2_results.jsonl")
        .withColumn("started_at", F.to_timestamp("started_at"))
    )
    p3 = spark.createDataFrame(
        [
            _to_schema_tuple(row)
            for row in _read_p3_rows(spark, bronze_root, load_date)
        ],
        schema=EVAL_SCHEMA,
    )
    staged = _stage(p1).unionByName(_stage(p2)).unionByName(p3)

    dim_arm = (
        spark.read.option("header", True).csv(f"{seeds_dir}/dim_arm.csv")
        .select("study", "arm", F.col("model").alias("dim_model"))
    )
    rates = spark.read.option("header", True).schema(_RATES_SCHEMA).csv(f"{seeds_dir}/model_rates.csv")
    rates = rates.select(
        F.col("model").alias("dim_model"),
        F.col("input_per_m_usd").alias("rate_in"),
        F.col("output_per_m_usd").alias("rate_out"),
    )
    joined = staged.join(dim_arm, ["study", "arm"], "left").join(F.broadcast(rates), "dim_model", "left")

    recorded = F.col("cost_basis") == "recorded"
    no_model = F.col("dim_model") == NO_MODEL
    estimate = (
        F.coalesce("tokens_in", F.lit(0)) * F.col("rate_in")
        + F.coalesce("tokens_out", F.lit(0)) * F.col("rate_out")
    ) / 1_000_000
    result = (
        joined
        .withColumn("model", F.when(F.col("study") == P1_STUDY, F.col("dim_model")).otherwise(F.col("model")))
        .withColumn(
            "cost_usd",
            F.when(recorded, F.col("cost_usd"))
            .when(no_model, F.lit(0.0))
            .otherwise(estimate),  # null when the arm has no rate
        )
        .withColumn(
            "cost_basis",
            F.when(recorded, F.lit("recorded"))
            .when(no_model, F.lit("no_model"))
            .when(F.col("rate_in").isNull(), F.lit("rate_missing"))
            .otherwise(F.lit("tokens_x_rate")),
        )
        .withColumn("load_date", F.lit(load_date).cast("date"))
    )
    return result.select(*EVAL_COLUMNS)


def merge_eval_attempts(spark: SparkSession, df: DataFrame, table_path: str) -> None:
    """MERGE df into the Delta table on EVAL_KEY; create the table if it does not exist.

    A matched row is updated only when some non-key column other than load_date differs (null-safe); an unmatched
    row is inserted. Rerunning the same df therefore writes no row.
    """
    if not DeltaTable.isDeltaTable(spark, table_path):
        df.write.format("delta").save(table_path)
        return
    # load_date is bookkeeping: it keeps the first-seen date and only moves when a real column changes.
    compared = [c for c in EVAL_COLUMNS if c not in EVAL_KEY and c != "load_date"]
    on = " AND ".join(f"t.{c} <=> s.{c}" for c in EVAL_KEY)
    changed = " OR ".join(f"NOT (t.{c} <=> s.{c})" for c in compared)
    (
        DeltaTable.forPath(spark, table_path).alias("t")
        .merge(df.alias("s"), on)
        .whenMatchedUpdateAll(condition=changed)
        .whenNotMatchedInsertAll()
        .execute()
    )
