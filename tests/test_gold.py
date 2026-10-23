import csv
import hashlib
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lake_helpers import cost_export_maker
from lakehouse.attribution import load_rg_rules, load_session_windows
from lakehouse.gold.build import GOLD_TABLES, GRAINS, PROJECTS, SESSION_TABLES, build_gold
from lakehouse.gold.fingerprint import gold_fingerprint
from lakehouse.seeding import SEEDS_DIR
from lakehouse.silver.costs import normalise_costs, read_cost_export, replace_month
from lakehouse.silver.deployments import (
    CHANGES_SCHEMA,
    apply_scd2,
)
from lakehouse.silver.eval_attempts import EVAL_SCHEMA, merge_eval_attempts

SEEDS = SEEDS_DIR
REJECT = "reject.eval_unresolved"

CHAT = ("rl-primary", "releaselens-chat")
EMBED = ("rl-primary", "releaselens-embed-small")
V1_COMMIT = "2026-09-27T15:51:42+10:00"  # 2026-09-27 05:51:42 UTC
V2_COMMIT = "2026-10-02T13:00:09+10:00"  # 2026-10-02 03:00:09 UTC
V2_UTC = datetime(2026, 10, 2, 3, 0, 9, tzinfo=UTC)


def _utc(*parts):
    return datetime(*parts, tzinfo=UTC)


def _change(key, committed_at, capacity, name="gpt-4.1-mini", version="2025-04-14"):
    return {
        "account_alias": key[0],
        "deployment_name": key[1],
        "model_name": name,
        "model_version": version,
        "sku": "GlobalStandard",
        "capacity": capacity,
        "region_alias": "primary",
        "committed_at": committed_at,
    }


def _silver_deployments(spark, paths):
    rows = [
        _change(CHAT, V1_COMMIT, 100),
        _change(CHAT, V2_COMMIT, 300),
        _change(EMBED, V1_COMMIT, 350, "text-embedding-3-small", "1"),
    ]
    data = [tuple(r.get(f.name) for f in CHANGES_SCHEMA.fields) for r in rows]
    apply_scd2(
        spark,
        spark.createDataFrame(data, schema=CHANGES_SCHEMA),
        paths.table("silver", "model_deployment"),
        SEEDS,
    )


def _attempt(study, arm, started_at, item="q1", run_id="run-1", **extra):
    row = dict.fromkeys(EVAL_SCHEMA.fieldNames())
    row.update(
        study=study, item_id=item, arm=arm, pass_index=1, attempt_kind="scored",
        run_id=run_id, started_at=started_at, success=True, latency_ms=12.5,
        tokens_in=10, tokens_out=5, cost_usd=0.001, cost_basis="tokens_x_rate",
        load_date=date(2026, 10, 21),
    )
    row.update(extra)
    return tuple(row[name] for name in EVAL_SCHEMA.fieldNames())


def _silver_attempts(spark, paths, attempts):
    df = spark.createDataFrame(attempts, schema=EVAL_SCHEMA)
    merge_eval_attempts(spark, df, paths.table("silver", "eval_attempts"))


def _silver_costs(spark, paths, tmp_path, style="mosa"):
    rules = load_rg_rules(SEEDS)
    windows = load_session_windows(SEEDS)
    export = tmp_path / "export"  # one run: the 11 October rows
    cost_export_maker.write_run(
        export, cost_export_maker.OCT, cost_export_maker.RUN_B, "2026-10-21T03:30:09Z",
        [cost_export_maker.OCTOBER_ROWS], style=style,
    )
    frame = normalise_costs(spark, read_cost_export(spark, str(export))[0], rules, windows)
    replace_month(spark, frame, paths.table("silver", "daily_cost"), "2026-10")
    return frame.count()


@pytest.fixture
def silver(spark, tmp_lake, tmp_path):
    """A lake with synthetic silver tables: two P1 Z attempts either side of version 2 and more."""
    _silver_deployments(spark, tmp_lake)
    _silver_attempts(
        spark,
        tmp_lake,
        [
            _attempt("p1-three-arm", "Z", _utc(2026, 9, 30), item="before-v2"),
            _attempt("p1-three-arm", "Z", V2_UTC, item="at-v2"),
            _attempt("p1-three-arm", "Z", _utc(2026, 10, 3), item="after-v2", run_id="run-2"),
            _attempt("p1-three-arm", "A", _utc(2026, 9, 30), item="external"),
            _attempt("p2-retrieval", "E1", _utc(2026, 10, 3), item="no-model"),
            _attempt("p2-retrieval", "E2", _utc(2026, 10, 3), item="embed"),
        ],
    )
    cost_rows = _silver_costs(spark, tmp_lake, tmp_path)
    return tmp_lake, cost_rows


def _gold(spark, paths, name, layer="gold"):
    return spark.read.format("delta").load(paths.table(layer, name))


def _sk(spark, paths, key, valid_from):
    row = (
        spark.read.format("delta").load(paths.table("silver", "model_deployment"))
        .where((F.col("account_alias") == key[0]) & (F.col("deployment_name") == key[1]))
        .where(F.col("valid_from") == F.lit(valid_from).cast("timestamp"))
        .first()
    )
    return row["deployment_sk"]


def _resolved(spark, paths):
    return {
        r["item_id"]: r["deployment_sk"]
        for r in _gold(spark, paths, "fact_eval_attempt").select("item_id", "deployment_sk").collect()
    }


def test_boundary_joins_new_version(spark, silver):
    paths, _ = silver
    build_gold(spark, paths, SEEDS)

    resolved = _resolved(spark, paths)

    assert resolved["at-v2"] == _sk(spark, paths, CHAT, V2_UTC)
    assert resolved["after-v2"] == _sk(spark, paths, CHAT, V2_UTC)
    v1 = _sk(spark, paths, CHAT, _utc(2026, 9, 27, 5, 51, 42))
    assert resolved["before-v2"] == v1
    assert v1 != resolved["at-v2"]


def test_mixed_offsets(spark, tmp_lake, tmp_path):
    """A +10:00 commit time and a +00:00 result time for one instant join as equal."""
    _silver_deployments(spark, tmp_lake)
    _silver_costs(spark, tmp_lake, tmp_path)
    result_time = datetime.fromisoformat("2026-10-02T03:00:09+00:00")
    assert result_time == datetime.fromisoformat(V2_COMMIT)
    _silver_attempts(spark, tmp_lake, [_attempt("p1-three-arm", "Z", result_time, item="same-instant")])

    build_gold(spark, tmp_lake, SEEDS)

    assert _resolved(spark, tmp_lake) == {"same-instant": _sk(spark, tmp_lake, CHAT, V2_UTC)}


def test_external_and_no_model_arms_resolve(spark, silver):
    paths, _ = silver
    build_gold(spark, paths, SEEDS)

    resolved = _resolved(spark, paths)
    members = {
        (r["account_alias"], r["deployment_name"]): r["deployment_sk"]
        for r in _gold(spark, paths, "dim_model_deployment").collect()
    }

    assert resolved["external"] == members[("external", "claude-sonnet-5")]
    assert resolved["no-model"] == members[("none", "no-model")]
    assert resolved["embed"] == _sk(spark, paths, EMBED, _utc(2026, 9, 27, 5, 51, 42))
    assert None not in resolved.values()


def test_before_first_version_rejected(spark, tmp_lake, tmp_path):
    _silver_deployments(spark, tmp_lake)
    cost_rows = _silver_costs(spark, tmp_lake, tmp_path)
    attempts = [
        _attempt("p1-three-arm", "Z", _utc(2026, 9, 1), item="too-early"),
        _attempt("p1-three-arm", "Z", _utc(2026, 10, 3), item="fine"),
        _attempt("p1-three-arm", "A", _utc(2026, 9, 1), item="external-early"),
    ]
    _silver_attempts(spark, tmp_lake, attempts)

    counts = build_gold(spark, tmp_lake, SEEDS)

    rejected = _gold(spark, tmp_lake, "eval_unresolved", "reject")
    assert [(r["item_id"], r["reason"]) for r in rejected.collect()] == [("too-early", "no_version")]
    assert set(_resolved(spark, tmp_lake)) == {"fine", "external-early"}
    assert counts["fact_eval_attempt"] + counts[REJECT] == len(attempts)
    assert counts["fact_daily_cost"] == cost_rows
    assert "deployment_sk" not in rejected.columns


def test_overlapping_versions_rejected(spark, tmp_lake, tmp_path):
    """Two versions covering one instant: not guessed between, sent to the reject table."""
    _silver_deployments(spark, tmp_lake)
    _silver_costs(spark, tmp_lake, tmp_path)
    path = tmp_lake.table("silver", "model_deployment")
    table = DeltaTable.forPath(spark, path)
    table.update(
        F.col("deployment_name") == CHAT[1],
        {"valid_to": F.lit(None).cast("timestamp")},  # v1 never closes, so it overlaps v2
    )
    _silver_attempts(spark, tmp_lake, [_attempt("p1-three-arm", "Z", _utc(2026, 10, 3), item="two-versions")])

    counts = build_gold(spark, tmp_lake, SEEDS)

    rejected = _gold(spark, tmp_lake, "eval_unresolved", "reject").collect()
    assert [(r["item_id"], r["reason"]) for r in rejected] == [("two-versions", "multiple_versions")]
    assert counts["fact_eval_attempt"] == 0
    assert counts[REJECT] == 1


def test_missing_silver_costs_raises_naming_the_path(spark, tmp_lake):
    _silver_deployments(spark, tmp_lake)
    _silver_attempts(spark, tmp_lake, [_attempt("p1-three-arm", "Z", _utc(2026, 10, 3))])

    with pytest.raises(ValueError, match="daily_cost") as error:
        build_gold(spark, tmp_lake, SEEDS)

    assert tmp_lake.table("silver", "daily_cost") in str(error.value)
    assert not DeltaTable.isDeltaTable(spark, tmp_lake.table("gold", "fact_daily_cost"))


def test_fact_carries_silver_measures(spark, silver):
    paths, _ = silver
    build_gold(spark, paths, SEEDS)

    fact = _gold(spark, paths, "fact_eval_attempt")
    row = fact.where("item_id = 'at-v2'").first()

    assert row["tokens_in"] == 10 and row["tokens_out"] == 5
    assert row["cost_usd"] == pytest.approx(0.001)
    assert row["groundedness"] is None  # not measured here: stays null
    assert row["started_date"] == date(2026, 10, 2)
    assert fact.count() == 6


def test_dimensions(spark, silver):
    paths, _ = silver
    counts = build_gold(spark, paths, SEEDS)

    projects = {r["project"]: r["name"] for r in _gold(spark, paths, "dim_project").collect()}
    assert sorted(projects) == [
        "p1", "p10", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "releaselens-shared", "untagged",
    ]
    assert projects["p1"] == "Azure OpenAI, keyless"
    assert projects["p8"] == "Azure Pipelines delivery for project 2"
    assert projects["p9"] == "Fabric lakehouse and batch pipelines"
    assert projects["p10"] == "Fabric Real-Time Intelligence"
    assert len(projects) == 12

    studies = {
        (r["study"], r["run_id"]): r["started_at"]
        for r in _gold(spark, paths, "dim_study").collect()
    }
    assert set(studies) == {("p1-three-arm", "run-1"), ("p1-three-arm", "run-2"), ("p2-retrieval", "run-1")}
    assert studies[("p1-three-arm", "run-1")].astimezone(UTC) == _utc(2026, 9, 30)

    assert counts["dim_arm"] == 11
    assert counts["dim_model_deployment"] == _gold(spark, paths, "dim_model_deployment").count()


def test_dim_date_spans_to_latest_fact_date(spark, silver):
    paths, _ = silver
    build_gold(spark, paths, SEEDS)

    dates = [r["date"] for r in _gold(spark, paths, "dim_date").orderBy("date").collect()]

    # the fixture's latest cost line is 2026-10-25; live checks run to 2026-10-21
    assert dates[0] == date(2026, 9, 1)
    assert dates[-1] == date(2026, 10, 25)
    assert len(dates) == 55
    assert len(set(dates)) == len(dates)


def test_cost_fact_hides_resource_ids(spark, silver):
    paths, cost_rows = silver
    counts = build_gold(spark, paths, SEEDS)

    fact = _gold(spark, paths, "fact_daily_cost")
    assert counts["fact_daily_cost"] == cost_rows

    assert "resource_id" not in fact.columns and "tags" not in fact.columns
    flat = " ".join(str(v) for row in fact.collect() for v in row)
    assert "subscriptions" not in flat and "synthetic-openai" not in flat

    expected = hashlib.sha256(
        b"/subscriptions/synthetic-sub/resourcegroups/rg-releaselens-bootstrap/providers/"
        b"microsoft.cognitiveservices/accounts/synthetic-openai"
    ).hexdigest()[:16]
    keys = {r["resource_key"] for r in fact.collect()}
    assert expected in keys
    assert all(re.fullmatch(r"[0-9a-f]{16}", k) for k in keys if k)
    assert {"cost_date", "resource_group", "meter_name", "cost", "billing_currency", "project"} <= set(fact.columns)


def test_cost_fact_carries_the_export_rate_and_keeps_its_grain(spark, tmp_lake, tmp_path):
    _silver_deployments(spark, tmp_lake)
    _silver_attempts(spark, tmp_lake, [_attempt("p1-three-arm", "Z", _utc(2026, 10, 3))])
    cost_rows = _silver_costs(spark, tmp_lake, tmp_path, style="new")

    counts = build_gold(spark, tmp_lake, SEEDS)

    fact = _gold(spark, tmp_lake, "fact_daily_cost")
    columns = fact.columns
    assert columns[columns.index("billing_currency") + 1 : columns.index("billing_currency") + 3] == [
        "pricing_currency", "fx_pricing_to_billing",
    ]
    assert counts["fact_daily_cost"] == cost_rows == fact.count()
    assert {(r["pricing_currency"], r["fx_pricing_to_billing"]) for r in fact.collect()} == {("USD", 1.5)}
    assert "resource_id" not in columns


def test_cost_fact_rate_is_null_for_an_export_without_it(spark, silver):
    paths, _ = silver  # the MOSA-style October export has no pricing currency or rate
    build_gold(spark, paths, SEEDS)

    fact = _gold(spark, paths, "fact_daily_cost")

    assert {"pricing_currency", "fx_pricing_to_billing"} <= set(fact.columns)
    assert fact.filter(F.col("pricing_currency").isNotNull() | F.col("fx_pricing_to_billing").isNotNull()).count() == 0


def test_live_check_fact_matches_seed(spark, silver):
    paths, _ = silver
    counts = build_gold(spark, paths, SEEDS)

    with open(Path(SEEDS) / "live_checks.csv", encoding="utf-8", newline="") as f:
        seed = list(csv.DictReader(f))
    fact = _gold(spark, paths, "fact_live_check")

    assert counts["fact_live_check"] == len(seed) == fact.count()
    assert {r["verdict"] for r in fact.collect()} == {"pass", "fail", "not-run"}


def test_a_null_or_empty_verdict_in_the_live_checks_seed_is_refused(spark, silver, tmp_path):
    """A blank verdict cell is null, which `isin` would let through: the guard names the row."""
    paths, _ = silver
    seeds = tmp_path / "seeds"
    shutil.copytree(SEEDS, seeds)
    with open(seeds / "live_checks.csv", "a", encoding="utf-8", newline="") as seed:
        seed.write("p4,2026-10-09,session-x,check-y,,,,docs/gateway-report.md," + "0" * 40 + "\n")

    with pytest.raises(ValueError, match=r"null or empty verdict.*p4.*session-x.*check-y"):
        build_gold(spark, paths, str(seeds))

    assert not DeltaTable.isDeltaTable(spark, paths.table("gold", "fact_live_check"))


def test_every_table_has_a_grain():
    assert set(GRAINS) == set(GOLD_TABLES) | set(SESSION_TABLES) | {REJECT}
    assert not set(SESSION_TABLES) & set(GOLD_TABLES)  # outside project 9's re-run fingerprint
    assert GRAINS["fact_gateway_call"] == "gateway call"
    assert GRAINS["dim_project"] == "project 1-10, plus releaselens-shared and untagged"
    assert GRAINS["fact_eval_attempt"].startswith("study x item x arm x pass")
    assert all(text for text in GRAINS.values())


def test_rebuild_is_identical(spark, silver):
    paths, _ = silver
    first_counts = build_gold(spark, paths, SEEDS)
    first_print = gold_fingerprint(spark, paths)

    second_counts = build_gold(spark, paths, SEEDS)
    second_print = gold_fingerprint(spark, paths)

    assert first_counts == second_counts
    assert first_print == second_print
    assert set(first_print) == set(GOLD_TABLES) | {REJECT}
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in first_print.values())


def test_fingerprint_detects_change(spark, silver):
    paths, _ = silver
    build_gold(spark, paths, SEEDS)
    before = gold_fingerprint(spark, paths)

    DeltaTable.forPath(spark, paths.table("gold", "dim_project")).update(
        F.col("project") == "p1", {"name": F.lit("Renamed")}
    )
    after = gold_fingerprint(spark, paths)

    assert {t for t in before if before[t] != after[t]} == {"dim_project"}


def test_p10_sits_before_the_pseudo_projects():
    names = [p for p, _ in PROJECTS]
    assert names[-3:] == ["p10", "releaselens-shared", "untagged"]


def test_live_checks_seed_valid():
    allowed_reports = {
        "docs/gateway-report.md",
        "docs/functions-report.md",
        "docs/webapp-report.md",
        "docs/agents-report.md",
        "docs/agents-results-2026-10-15.json",
        "docs/pipelines-report.md",
    }
    with open(Path(SEEDS) / "live_checks.csv", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = reader.fieldnames
    assert header == [
        "project", "session_date", "session", "check_id", "verdict", "measured_value", "unit",
        "source_report", "source_sha",
    ]
    keys = [(r["project"], r["session"], r["check_id"]) for r in rows]
    assert len(keys) == len(set(keys)), "(project, session, check_id) must be unique"
    assert rows
    for row in rows:
        assert row["verdict"] in {"pass", "fail", "not-run"}
        assert row["source_report"] in allowed_reports
        assert row["project"] in {"p4", "p5", "p6", "p7", "p8"}
        assert date.fromisoformat(row["session_date"])
        assert re.fullmatch(r"[0-9a-f]{40}", row["source_sha"])
        assert row["check_id"] and row["session"]
        assert bool(row["measured_value"]) == bool(row["unit"])
        if row["measured_value"]:
            float(row["measured_value"])
        assert not re.search(r"https?://|[0-9a-f]{8}-[0-9a-f]{4}-", " ".join(row.values()), re.I)
    # the functions report's first T2/T3 attempt ("no response") is kept as not-run
    verdicts = {(r["project"], r["session"], r["check_id"]): r["verdict"] for r in rows}
    assert verdicts[("p5", "run-9748b95", "T2")] == "not-run"
    assert verdicts[("p5", "run-9748b95", "T3")] == "not-run"
    # every report contributes rows, and failures are kept
    assert {r["source_report"] for r in rows} == allowed_reports
    assert {r["verdict"] for r in rows} == {"pass", "fail", "not-run"}
