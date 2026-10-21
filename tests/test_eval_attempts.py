import csv
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lakehouse.silver.eval_attempts import (
    EVAL_COLUMNS,
    EVAL_KEY,
    build_eval_attempts,
    estimate_cost,
    merge_eval_attempts,
    parse_p3_run,
)

ROOT = Path(__file__).resolve().parent.parent
SEEDS = str(ROOT / "seeds")
P3_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p3"
LOAD_DATE = date(2026, 10, 21)
STUDY_DIR = "study-synthetic"


def _fixture(name: str) -> dict:
    return json.loads((P3_FIXTURES / name).read_text(encoding="utf-8"))


def _score_row(scenario: str, engine: str) -> dict:
    rows = _fixture("eval-scores.json")["rows"]
    return next(r for r in rows if r["scenario_id"] == scenario and r["engine"] == engine)


def _p1_row(**over) -> dict:
    row = {
        "study": "p1-three-arm", "run_id": "p1-three-arm-20261002T032538Z",
        "started_at": "2026-10-02T13:25:38.360009+10:00", "item_id": "gq-001", "arm": "A",
        "pass_index": 0, "attempt_kind": "scored", "provider": "anthropic",
        "model": "claude-sonnet-5", "success": True, "latency_ms": 3520.5, "tokens_in": 1010,
        "tokens_out": 238, "cached_tokens": 0, "cost_usd": 0.02, "judge_cost_usd": 0.005,
        "groundedness": 1.0, "citation_recall": 0.5, "citation_precision": 1.0,
        "must_contain": True,
    }
    row.update(over)
    return row


def _p2_row(**over) -> dict:
    row = {
        "study": "p2-retrieval", "run_id": "p2-retrieval-20261003T210637Z",
        "started_at": "2026-10-03T21:06:37.000000+00:00", "item_id": "q-001", "arm": "E2",
        "pass_index": 1, "attempt_kind": "scored", "success": True, "latency_ms": 85.0,
        "tokens_in": 12, "target_rank": 2, "top1": False, "reciprocal_rank": 0.5,
    }
    row.update(over)
    return row


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


@pytest.fixture
def lake_inputs(tmp_path):
    """Synthetic bronze (P3 fixtures) and private (P1, P2) folders."""
    bronze = tmp_path / "bronze"
    results = (
        bronze / "public" / f"load_date={LOAD_DATE.isoformat()}"
        / "foundry-workshop-agent" / "results" / STUDY_DIR
    )
    shutil.copytree(P3_FIXTURES, results)
    private = tmp_path / "private"
    private.mkdir()
    _write_jsonl(private / "p1_outcomes.jsonl", [
        _p1_row(),
        _p1_row(arm="Z", provider="azure-openai", model="gpt-4.1-mini-2025-04-14",
                tokens_in=2834, tokens_out=110, cost_usd=0.001, judge_cost_usd=0.004,
                started_at="2026-10-02T03:25:38.360009+00:00"),
    ])
    _write_jsonl(private / "p2_results.jsonl", [
        _p2_row(),
        _p2_row(arm="E1", tokens_in=0, target_rank=None, top1=False, reciprocal_rank=0.0),
        _p2_row(attempt_kind="determinism_repeat"),
    ])
    return str(bronze), str(private)


@pytest.fixture
def silver_df(spark, lake_inputs):
    bronze, private = lake_inputs
    return build_eval_attempts(spark, bronze, private, SEEDS, LOAD_DATE)


def _by_key(df, **where):
    cond = F.lit(True)
    for k, v in where.items():
        cond = cond & (F.col(k) == v)
    rows = df.filter(cond).collect()
    assert len(rows) == 1, where
    return rows[0]


def test_parse_p3_sums_tokens_and_scores():
    run = _fixture("s01.gpt.p1.json")
    run["calls"] = [
        {"index": 1, "inputTokens": 1168, "outputTokens": 64, "modelId": "gpt-5.6-luna"},
        {"index": 2, "inputTokens": 900, "outputTokens": 30, "modelId": "gpt-5.6-luna"},
    ]

    row = parse_p3_run(run, _score_row("s01", "gpt")["scores"], "scored")

    assert row["study"] == "p3-agent"
    assert (row["item_id"], row["arm"], row["pass_index"]) == ("s01", "gpt", 1)
    assert (row["tokens_in"], row["tokens_out"]) == (2068, 94)
    assert row["model_version"] == "gpt-5.6-luna"
    assert row["provider"] == "azure"
    assert row["model"] == "gpt-5.6-luna"
    assert row["success"] is True
    assert row["latency_ms"] == run["ms"]
    assert row["started_at"] == run["startedAt"]
    assert (row["tool_call_accuracy"], row["task_adherence"], row["intent_resolution"]) == (
        5.0, 1.0, 5.0)


def test_parse_p3_without_calls_has_no_model_version():
    row = parse_p3_run(_fixture("s02.claude.p1.json"), _score_row("s02", "claude")["scores"], "scored")

    assert row["model_version"] is None
    assert (row["tokens_in"], row["tokens_out"]) == (0, 0)


def test_first_attempt_is_superseded():
    run = _fixture("repeats/s01.gpt.p1.first-attempt.json")

    row = parse_p3_run(run, None, "superseded")

    assert row["attempt_kind"] == "superseded"
    assert row["tool_call_accuracy"] is None
    assert row["task_adherence"] is None
    assert row["intent_resolution"] is None


def test_estimate_cost():
    rates = {"claude-haiku-4-5": (1.00, 5.00)}

    assert estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000, rates) == pytest.approx(6.00)
    assert estimate_cost("claude-haiku-4-5", 0, 0, rates) == 0.0


def test_columns_and_utc(silver_df):
    assert silver_df.columns == EVAL_COLUMNS
    assert EVAL_COLUMNS[:5] == EVAL_KEY

    def utc_text(**key):
        # session time zone is UTC, so the string cast shows the stored UTC instant
        text = silver_df.withColumn("t", F.col("started_at").cast("string"))
        return _by_key(text, **key)["t"]

    # input was 13:25:38.360009+10:00, i.e. 03:25:38.360009 UTC
    assert utc_text(study="p1-three-arm", arm="A") == "2026-10-02 03:25:38.360009"
    assert utc_text(study="p1-three-arm", arm="Z") == "2026-10-02 03:25:38.360009"
    # a P3 instant with 7 fractional digits lands on the right UTC instant too
    assert utc_text(study="p3-agent", item_id="s01", arm="claude",
                    attempt_kind="scored") == "2026-10-06 00:40:00.123456"
    assert dict(silver_df.dtypes)["started_at"] == "timestamp"


def test_all_studies_land_and_keys_are_unique(silver_df):
    assert silver_df.count() == 2 + 3 + 5
    assert silver_df.select(*EVAL_KEY).distinct().count() == silver_df.count()
    assert {r["load_date"] for r in silver_df.select("load_date").distinct().collect()} == {LOAD_DATE}


def test_p1_keeps_recorded_cost_including_judge(silver_df):
    row = _by_key(silver_df, study="p1-three-arm", arm="Z")

    assert row["cost_usd"] == pytest.approx(0.005)
    assert row["cost_basis"] == "recorded"
    assert row["provider"] == "azure-openai"
    assert row["model"] == "gpt-4.1-mini"  # from dim_arm
    assert row["model_version"] == "gpt-4.1-mini-2025-04-14"  # the outcome's own string
    assert row["tool_call_accuracy"] is None and row["top1"] is None
    arm_a = _by_key(silver_df, study="p1-three-arm", arm="A")
    assert arm_a["cost_usd"] == pytest.approx(0.025)
    assert arm_a["citation_recall"] == 0.5
    assert arm_a["must_contain"] is True


def test_p2_cost_from_dim_arm_model_and_nulls(silver_df):
    scored = _by_key(silver_df, study="p2-retrieval", arm="E2", attempt_kind="scored")
    repeat = _by_key(silver_df, study="p2-retrieval", arm="E2", attempt_kind="determinism_repeat")
    none_arm = _by_key(silver_df, study="p2-retrieval", arm="E1")

    assert scored["cost_usd"] == pytest.approx(12 * 0.02 / 1_000_000)
    assert scored["cost_basis"] == "tokens_x_rate"
    assert repeat["cost_usd"] == pytest.approx(scored["cost_usd"])
    assert none_arm["cost_usd"] == 0.0
    assert none_arm["cost_basis"] == "no_model"
    for row in (scored, none_arm):
        assert row["provider"] is None and row["model"] is None and row["model_version"] is None
    assert (scored["target_rank"], scored["top1"], scored["reciprocal_rank"]) == (2, False, 0.5)
    assert scored["tokens_out"] is None and scored["groundedness"] is None


def test_p3_rows_use_estimate_and_scores(silver_df):
    rates = {
        r["model"]: (float(r["input_per_m_usd"]), float(r["output_per_m_usd"]))
        for r in csv.DictReader((ROOT / "seeds" / "model_rates.csv").open(encoding="utf-8"))
    }
    gpt = _by_key(silver_df, study="p3-agent", item_id="s01", arm="gpt", attempt_kind="scored")
    claude = _by_key(silver_df, study="p3-agent", item_id="s01", arm="claude", attempt_kind="scored")
    first = _by_key(silver_df, study="p3-agent", item_id="s01", arm="gpt", attempt_kind="superseded")
    nocalls = _by_key(silver_df, study="p3-agent", item_id="s02", arm="claude", attempt_kind="scored")

    assert gpt["run_id"] == STUDY_DIR
    assert (gpt["tokens_in"], gpt["tokens_out"]) == (2068, 94)
    assert gpt["cost_usd"] == pytest.approx(estimate_cost("gpt-5.6-luna", 2068, 94, rates))
    assert gpt["cost_basis"] == "tokens_x_rate"
    assert claude["cost_usd"] == pytest.approx(estimate_cost("claude-haiku-4-5", 2000, 100, rates))
    assert claude["model_version"] == "claude-haiku-4-5-20251001"
    assert (gpt["tool_call_accuracy"], gpt["task_adherence"], gpt["intent_resolution"]) == (5.0, 1.0, 5.0)
    assert first["tool_call_accuracy"] is None
    assert first["success"] is False
    assert nocalls["model_version"] is None and nocalls["cost_usd"] == 0.0
    assert gpt["provider"] == "azure" and claude["provider"] == "azure"


def test_dim_arm_seed_matches_confirmed_models():
    with (ROOT / "seeds" / "dim_arm.csv").open(encoding="utf-8") as f:
        dim = {(r["study"], r["arm"]): r for r in csv.DictReader(f)}

    expected_model = {
        ("p1-three-arm", "A"): "claude-sonnet-5", ("p1-three-arm", "Z"): "gpt-4.1-mini",
        ("p1-three-arm", "O"): "gpt-4.1-mini",
        ("p2-retrieval", "E1"): "no-model", ("p2-retrieval", "S1"): "no-model",
        ("p2-retrieval", "E2"): "text-embedding-3-small",
        ("p2-retrieval", "S2"): "text-embedding-3-small",
        ("p2-retrieval", "S3"): "text-embedding-3-small",
        ("p2-retrieval", "E3"): "text-embedding-3-large",
        ("p3-agent", "gpt"): "gpt-5.6-luna", ("p3-agent", "claude"): "claude-haiku-4-5",
    }
    assert {k: v["model"] for k, v in dim.items()} == expected_model
    assert dim[("p1-three-arm", "Z")]["account_alias"] == "rl-primary"
    assert dim[("p1-three-arm", "Z")]["deployment_name"] == "releaselens-chat"
    assert dim[("p1-three-arm", "O")]["deployment_name"] == "gpt-4.1-mini@openai"
    assert dim[("p2-retrieval", "E3")]["deployment_name"] == "releaselens-embed-large"
    assert dim[("p3-agent", "gpt")]["account_alias"] == "fwa"


def _merge_metrics(spark, path: str) -> dict:
    return DeltaTable.forPath(spark, path).history(1).select("operationMetrics").first()[0]


def test_merge_rerun_changes_nothing(spark, silver_df, tmp_path):
    table = str(tmp_path / "silver" / "eval_attempts")

    merge_eval_attempts(spark, silver_df, table)
    assert spark.read.format("delta").load(table).count() == 10
    merge_eval_attempts(spark, silver_df, table)

    history = DeltaTable.forPath(spark, table).history()
    assert history.filter(F.col("operation") == "MERGE").count() == 1  # the rerun was a MERGE too
    metrics = _merge_metrics(spark, table)
    assert int(metrics["numTargetRowsInserted"]) == 0
    assert int(metrics["numTargetRowsUpdated"]) == 0
    assert spark.read.format("delta").load(table).count() == 10


def test_merge_updates_changed_row(spark, silver_df, tmp_path):
    table = str(tmp_path / "silver" / "eval_attempts")
    merge_eval_attempts(spark, silver_df, table)

    changed = silver_df.withColumn(
        "latency_ms",
        F.when((F.col("study") == "p2-retrieval") & (F.col("arm") == "E1"), F.lit(999.0))
        .otherwise(F.col("latency_ms")),
    )
    merge_eval_attempts(spark, changed, table)

    metrics = _merge_metrics(spark, table)
    assert int(metrics["numTargetRowsUpdated"]) == 1
    assert int(metrics["numTargetRowsInserted"]) == 0
    stored = spark.read.format("delta").load(table)
    assert stored.count() == 10
    assert _by_key(stored, study="p2-retrieval", arm="E1")["latency_ms"] == 999.0


def test_merge_inserts_new_rows(spark, silver_df, tmp_path):
    table = str(tmp_path / "silver" / "eval_attempts")
    merge_eval_attempts(spark, silver_df.filter(F.col("study") != "p3-agent"), table)

    merge_eval_attempts(spark, silver_df, table)

    metrics = _merge_metrics(spark, table)
    assert int(metrics["numTargetRowsInserted"]) == 5
    assert int(metrics["numTargetRowsUpdated"]) == 0
