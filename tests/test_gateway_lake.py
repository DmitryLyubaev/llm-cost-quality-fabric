"""The gateway's lakehouse side: silver gateway_calls, gold fact_gateway_call, the count check, the
cost-per-call answer and the notebook's entry point (spec 4.4; rulings R1, R14, R15, R16).

All data is synthetic: request IDs are labels, not GUIDs, and the session names are made up.
"""

import json
from datetime import UTC, date, datetime

import pytest
from pyspark.sql import functions as F

from lakehouse import fabric_entry
from lakehouse.checks import CheckResult, QualityCheckFailed, check_gateway_counts
from lakehouse.gateway.answers import cost_per_gateway_call
from lakehouse.gateway.build import (
    FACT_GATEWAY_CALL_SCHEMA,
    build_fact_gateway_call,
    build_gateway_calls,
    count_duplicate_request_ids,
    merge_gateway_calls,
)
from lakehouse.gateway.contract import EVENT_FIELDS
from lakehouse.gold.build import PROJECTS

SESSION = "p10-2026-11-12"
OTHER_SESSION = "p10-2026-11-19"
FIELDS = [name for name, _ in EVENT_FIELDS]
SOURCE_SCHEMA = (
    "event_time string, request_id string, session string, api string, operation string, "
    "caller string, answered_by string, status int, refused_by_gateway boolean, tokens int, "
    "latency_ms int"
)
COST_SCHEMA = "cost_date date, resource_group string, cost double, billing_currency string, project string"


def _call(request_id, event_time="2026-11-12T01:00:00Z", session=SESSION, *, answered_by="primary",
          status=200, refused=False, tokens=120, latency_ms=850, caller="owner"):
    return (event_time, request_id, session, "releaselens-api", "chat", caller, answered_by, status,
            refused, tokens, latency_ms)


def _refused(request_id, event_time="2026-11-12T02:00:00Z", session=SESSION):
    return _call(request_id, event_time, session, answered_by=None, status=429, refused=True, tokens=None,
                 latency_ms=3)


def _source(spark, tmp_lake, rows, schema=SOURCE_SCHEMA) -> str:
    """The shortcut table (R1: Tables/dbo/gateway_calls) holding `rows`; returns its path."""
    path = f"{tmp_lake.tables_root}/dbo/gateway_calls"
    spark.createDataFrame(rows, schema).write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).save(path)
    return path


def _gold_dims(spark, paths, days=(date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)),
               projects=PROJECTS, costs=()):
    """Project 9's gold tables as far as the gateway load reads them."""
    spark.createDataFrame([(d,) for d in days], "date date").write.format("delta").mode("overwrite").save(
        paths.table("gold", "dim_date")
    )
    spark.createDataFrame(list(projects), "project string, name string").write.format("delta").mode(
        "overwrite"
    ).save(paths.table("gold", "dim_project"))
    spark.createDataFrame(list(costs), COST_SCHEMA).write.format("delta").mode("overwrite").save(
        paths.table("gold", "fact_daily_cost")
    )


def _silver_rows(spark, paths):
    df = spark.read.format("delta").load(paths.table("silver", "gateway_calls"))
    return {r["request_id"]: r for r in df.collect()}


def _gold_rows(spark, paths):
    df = spark.read.format("delta").load(paths.table("gold", "fact_gateway_call"))
    return {r["request_id"]: r for r in df.collect()}


def _load(spark, tmp_lake, rows, costs=()):
    """Source -> silver MERGE -> gold, the way load_gateway runs them."""
    source = _source(spark, tmp_lake, rows)
    _gold_dims(spark, tmp_lake, costs=costs)
    merge_gateway_calls(spark, build_gateway_calls(spark, source), tmp_lake.table("silver", "gateway_calls"))
    return build_fact_gateway_call(spark, tmp_lake)


# --- silver ----------------------------------------------------------------------------------


def test_silver_has_exactly_the_contract_columns_and_types(spark, tmp_lake):
    rows = [_call("req-01")]
    extra = SOURCE_SCHEMA + ", ingested string"
    source = _source(spark, tmp_lake, [r + ("x",) for r in rows], extra)

    df = build_gateway_calls(spark, source)

    assert df.columns == FIELDS
    types = dict(df.dtypes)
    assert types["event_time"] == "timestamp"
    assert types["status"] == types["tokens"] == types["latency_ms"] == "int"
    assert types["refused_by_gateway"] == "boolean"
    assert types["request_id"] == types["caller"] == "string"


def test_a_source_missing_contract_columns_is_refused_naming_them(spark, tmp_lake):
    schema = SOURCE_SCHEMA.replace(", tokens int", "").replace(", latency_ms int", "")
    source = _source(spark, tmp_lake, [_call("req-01")[:-2]], schema)

    with pytest.raises(ValueError, match="latency_ms") as raised:
        build_gateway_calls(spark, source)
    assert "tokens" in str(raised.value)


def test_a_refused_call_is_kept_in_silver_and_gold_with_null_backend_and_tokens(spark, tmp_lake):
    _load(spark, tmp_lake, [_call("req-01"), _refused("req-02")])

    for rows in (_silver_rows(spark, tmp_lake), _gold_rows(spark, tmp_lake)):
        refused = rows["req-02"]
        assert refused["answered_by"] is None
        assert refused["tokens"] is None
        assert refused["refused_by_gateway"] is True
        assert refused["status"] == 429


def test_replay_rows_never_reach_silver(spark, tmp_lake):
    rows = [_call("req-01"), _call("req-02", session="replay"), _refused("req-03", session="replay")]
    _load(spark, tmp_lake, rows)

    assert set(_silver_rows(spark, tmp_lake)) == {"req-01"}
    assert set(_gold_rows(spark, tmp_lake)) == {"req-01"}


def test_the_same_request_id_twice_gives_one_silver_row(spark, tmp_lake):
    """R15: an at-least-once redelivery of the same payload is one call."""
    rows = [_call("req-01"), _call("req-01"), _call("req-02")]
    source = _source(spark, tmp_lake, rows)

    df = build_gateway_calls(spark, source)

    assert sorted(r["request_id"] for r in df.collect()) == ["req-01", "req-02"]
    assert count_duplicate_request_ids(spark, source) == 1


def test_dedup_keeps_the_latest_event_time_then_a_stable_order(spark, tmp_lake):
    early = _call("req-01", "2026-11-12T01:00:00Z", latency_ms=900)
    late = _call("req-01", "2026-11-12T01:00:05Z", latency_ms=700)
    tie_a = _call("req-02", "2026-11-12T01:00:00Z", latency_ms=500)
    tie_b = _call("req-02", "2026-11-12T01:00:00Z", latency_ms=400)

    first = {r["request_id"]: r for r in build_gateway_calls(spark, _source(spark, tmp_lake, [early, late, tie_a, tie_b])).collect()}
    second = {r["request_id"]: r for r in build_gateway_calls(spark, _source(spark, tmp_lake, [tie_b, late, tie_a, early])).collect()}

    assert first["req-01"]["latency_ms"] == 700
    assert first["req-02"]["latency_ms"] == second["req-02"]["latency_ms"]
    assert first == second


def test_replay_copies_do_not_count_as_duplicates(spark, tmp_lake):
    source = _source(spark, tmp_lake, [_call("req-01"), _call("req-01", session="replay")])
    assert count_duplicate_request_ids(spark, source) == 0


def test_offset_and_z_times_give_the_same_instant_and_call_date(spark, tmp_lake):
    """09:30 on 13 Nov in Brisbane is 23:30 on 12 Nov in UTC: the call date is the UTC one."""
    rows = [
        _call("req-01", "2026-11-13T09:30:00+10:00"),
        _call("req-02", "2026-11-12T23:30:00Z"),
        _call("req-03", "2026-11-12T23:30:00.000+00:00"),
    ]
    _load(spark, tmp_lake, rows)

    gold = _gold_rows(spark, tmp_lake)
    instants = {gold[k]["event_time"].astimezone(UTC) for k in gold}
    assert instants == {datetime(2026, 11, 12, 23, 30, tzinfo=UTC)}
    assert {gold[k]["call_date"] for k in gold} == {date(2026, 11, 12)}


def test_a_timestamp_source_is_kept_as_the_same_instant(spark, tmp_lake):
    """The OneLake copy holds event_time as a timestamp, not a string."""
    schema = SOURCE_SCHEMA.replace("event_time string", "event_time timestamp")
    moment = datetime(2026, 11, 12, 23, 30, tzinfo=UTC)
    source = _source(spark, tmp_lake, [(moment,) + _call("req-01")[1:]], schema)

    row = build_gateway_calls(spark, source).first()

    assert row["event_time"].astimezone(UTC) == moment


def test_an_unreadable_event_time_is_refused(spark, tmp_lake):
    source = _source(spark, tmp_lake, [_call("req-01", "not a time")])
    with pytest.raises(ValueError, match="event_time"):
        build_gateway_calls(spark, source)


def test_a_malformed_replay_row_does_not_block_the_session(spark, tmp_lake):
    rows = [_call("req-01"), _call("req-02", "not a time", session="replay")]
    source = _source(spark, tmp_lake, rows)

    assert [r["request_id"] for r in build_gateway_calls(spark, source).collect()] == ["req-01"]
    assert count_duplicate_request_ids(spark, source) == 0


def test_the_build_refuses_a_session_that_is_not_utc(spark, tmp_lake):
    source = _source(spark, tmp_lake, [_call("req-01")])
    spark.conf.set("spark.sql.session.timeZone", "Australia/Brisbane")
    try:
        with pytest.raises(ValueError, match="UTC"):
            build_gateway_calls(spark, source)
    finally:
        spark.conf.set("spark.sql.session.timeZone", "UTC")


def test_merge_creates_the_table_then_a_rerun_writes_nothing(spark, tmp_lake):
    source = _source(spark, tmp_lake, [_call("req-01"), _call("req-01"), _refused("req-02")])
    table = tmp_lake.table("silver", "gateway_calls")

    first = merge_gateway_calls(spark, build_gateway_calls(spark, source), table)
    again = merge_gateway_calls(spark, build_gateway_calls(spark, source), table)

    assert first == {"inserted": 2, "updated": 0}
    assert again == {"inserted": 0, "updated": 0}
    assert spark.read.format("delta").load(table).count() == 2


def test_merge_updates_only_rows_whose_values_differ(spark, tmp_lake):
    table = tmp_lake.table("silver", "gateway_calls")
    merge_gateway_calls(spark, build_gateway_calls(spark, _source(spark, tmp_lake, [_call("req-01"), _call("req-02")])), table)

    changed = [_call("req-01"), _call("req-02", latency_ms=1), _call("req-03")]
    counts = merge_gateway_calls(spark, build_gateway_calls(spark, _source(spark, tmp_lake, changed)), table)

    assert counts == {"inserted": 1, "updated": 1}
    assert _silver_rows(spark, tmp_lake)["req-02"]["latency_ms"] == 1


def test_merge_updates_a_value_that_was_null(spark, tmp_lake):
    """The update condition is null-safe: null -> a value counts as a change."""
    table = tmp_lake.table("silver", "gateway_calls")
    before = [_call("req-01", answered_by=None, tokens=None, status=502)]
    merge_gateway_calls(spark, build_gateway_calls(spark, _source(spark, tmp_lake, before)), table)

    after = [_call("req-01", answered_by="secondary", tokens=90, status=502)]
    counts = merge_gateway_calls(spark, build_gateway_calls(spark, _source(spark, tmp_lake, after)), table)

    assert counts == {"inserted": 0, "updated": 1}
    row = _silver_rows(spark, tmp_lake)["req-01"]
    assert (row["answered_by"], row["tokens"]) == ("secondary", 90)


# --- gold ------------------------------------------------------------------------------------


def test_gold_is_one_row_per_call_with_call_date_and_project_p4(spark, tmp_lake):
    count = _load(spark, tmp_lake, [_call("req-01"), _refused("req-02"), _call("req-03", session=OTHER_SESSION)])

    gold = spark.read.format("delta").load(tmp_lake.table("gold", "fact_gateway_call"))
    assert count == 3
    assert gold.columns == FIELDS + ["call_date", "project"]
    assert gold.schema == FACT_GATEWAY_CALL_SCHEMA  # ruling R2: the always-present step uses it too
    assert {r["project"] for r in gold.collect()} == {"p4"}
    assert dict(gold.dtypes)["call_date"] == "date"


def test_gold_refuses_a_call_date_missing_from_dim_date(spark, tmp_lake):
    """R16: the build raises, naming the date, instead of dropping the call."""
    source = _source(spark, tmp_lake, [_call("req-01", "2026-11-20T01:00:00Z")])
    _gold_dims(spark, tmp_lake)
    merge_gateway_calls(spark, build_gateway_calls(spark, source), tmp_lake.table("silver", "gateway_calls"))

    with pytest.raises(ValueError, match="2026-11-20"):
        build_fact_gateway_call(spark, tmp_lake)


def test_gold_refuses_a_dim_project_without_p4(spark, tmp_lake):
    source = _source(spark, tmp_lake, [_call("req-01")])
    _gold_dims(spark, tmp_lake, projects=[p for p in PROJECTS if p[0] != "p4"])
    merge_gateway_calls(spark, build_gateway_calls(spark, source), tmp_lake.table("silver", "gateway_calls"))

    with pytest.raises(ValueError, match="p4"):
        build_fact_gateway_call(spark, tmp_lake)


# --- the count check -------------------------------------------------------------------------


def test_count_check_passes_when_gold_matches_the_eventhouse(spark, tmp_lake):
    _load(spark, tmp_lake, [_call("req-01"), _refused("req-02"), _call("req-03", session=OTHER_SESSION)])

    result = check_gateway_counts(spark, tmp_lake, SESSION, 2)

    assert isinstance(result, CheckResult)
    assert result.passed, result.detail
    assert "2" in result.detail


def test_count_check_fails_on_a_count_mismatch_and_names_both_counts(spark, tmp_lake):
    _load(spark, tmp_lake, [_call("req-01"), _refused("req-02")])

    result = check_gateway_counts(spark, tmp_lake, SESSION, 3)

    assert not result.passed
    assert "2" in result.detail and "3" in result.detail
    assert "req-" not in result.detail


def test_count_check_fails_on_a_duplicate_request_id_in_gold(spark, tmp_lake):
    _load(spark, tmp_lake, [_call("req-01"), _call("req-02")])
    gold = tmp_lake.table("gold", "fact_gateway_call")
    spark.read.format("delta").load(gold).where(F.col("request_id") == "req-01").write.format("delta").mode(
        "append"
    ).save(gold)

    result = check_gateway_counts(spark, tmp_lake, SESSION, 3)

    assert not result.passed
    assert "duplicate" in result.detail
    assert "req-" not in result.detail


# --- cost per gateway call -------------------------------------------------------------------


def _cost(day, cost, rg="rg-releaselens-gateway", currency="AUD", project="p4"):
    return (day, rg, cost, currency, project)


def _answer(spark, paths, session=SESSION):
    return {(r["call_date"], r["billing_currency"]): r for r in cost_per_gateway_call(spark, paths, session).collect()}


def test_cost_per_call_divides_the_days_gateway_cost_by_every_call(spark, tmp_lake):
    rows = [_call("req-01", tokens=100), _call("req-02", tokens=300), _refused("req-03"), _refused("req-04"),
            _call("req-05", session=OTHER_SESSION)]
    costs = [_cost(date(2026, 11, 12), 3.0), _cost(date(2026, 11, 12), 1.0), _cost(date(2026, 11, 11), 50.0)]
    _load(spark, tmp_lake, rows, costs)

    frame = cost_per_gateway_call(spark, tmp_lake, SESSION)
    answer = _answer(spark, tmp_lake)

    assert frame.columns == [
        "call_date", "calls", "tokens_total", "tokens_per_call", "billing_currency", "apim_cost", "cost_per_call",
    ]
    row = answer[(date(2026, 11, 12), "AUD")]
    assert row["calls"] == 4  # refused calls counted
    assert row["tokens_total"] == 400
    assert row["tokens_per_call"] == pytest.approx(100.0)
    assert row["apim_cost"] == pytest.approx(4.0)
    assert row["cost_per_call"] == pytest.approx(1.0)
    assert len(answer) == 1


def test_cost_per_call_without_cost_rows_keeps_the_day_with_null_cost(spark, tmp_lake):
    _load(spark, tmp_lake, [_call("req-01"), _refused("req-02")], [_cost(date(2026, 11, 11), 5.0)])

    answer = _answer(spark, tmp_lake)

    assert list(answer) == [(date(2026, 11, 12), None)]
    row = answer[(date(2026, 11, 12), None)]
    assert row["calls"] == 2
    assert row["apim_cost"] is None and row["cost_per_call"] is None


def test_cost_per_call_matches_the_resource_group_case_insensitively(spark, tmp_lake):
    costs = [_cost(date(2026, 11, 12), 2.0, rg="RG-ReleaseLens-Gateway"), _cost(date(2026, 11, 12), 2.0)]
    _load(spark, tmp_lake, [_call("req-01"), _call("req-02")], costs)

    row = _answer(spark, tmp_lake)[(date(2026, 11, 12), "AUD")]

    assert row["apim_cost"] == pytest.approx(4.0)
    assert row["cost_per_call"] == pytest.approx(2.0)


def test_cost_per_call_ignores_other_resource_groups(spark, tmp_lake):
    costs = [
        _cost(date(2026, 11, 12), 1.0),
        _cost(date(2026, 11, 12), 90.0, rg="rg-releaselens", project="p1"),
        _cost(date(2026, 11, 12), 70.0, rg="rg-p10-event-hubs", project="p10"),
        _cost(date(2026, 11, 12), 30.0, rg="rg-releaselens-gateway-old"),
    ]
    _load(spark, tmp_lake, [_call("req-01")], costs)

    row = _answer(spark, tmp_lake)[(date(2026, 11, 12), "AUD")]

    assert row["apim_cost"] == pytest.approx(1.0)


def test_cost_per_call_keeps_each_billing_currency_apart(spark, tmp_lake):
    costs = [_cost(date(2026, 11, 12), 2.0), _cost(date(2026, 11, 12), 1.0, currency="USD")]
    _load(spark, tmp_lake, [_call("req-01"), _call("req-02")], costs)

    answer = _answer(spark, tmp_lake)

    assert set(answer) == {(date(2026, 11, 12), "AUD"), (date(2026, 11, 12), "USD")}
    assert answer[(date(2026, 11, 12), "USD")]["cost_per_call"] == pytest.approx(0.5)


def test_a_session_across_utc_midnight_gives_each_day_its_own_row(spark, tmp_lake):
    rows = [
        _call("req-01", "2026-11-12T23:30:00Z"),
        _refused("req-02", "2026-11-12T23:45:00Z"),
        _call("req-03", "2026-11-13T00:30:00Z"),
    ]
    costs = [_cost(date(2026, 11, 12), 6.0), _cost(date(2026, 11, 13), 5.0)]
    _load(spark, tmp_lake, rows, costs)

    answer = _answer(spark, tmp_lake)

    assert set(answer) == {(date(2026, 11, 12), "AUD"), (date(2026, 11, 13), "AUD")}
    first, second = answer[(date(2026, 11, 12), "AUD")], answer[(date(2026, 11, 13), "AUD")]
    assert (first["calls"], first["apim_cost"], first["cost_per_call"]) == (2, 6.0, 3.0)
    assert (second["calls"], second["apim_cost"], second["cost_per_call"]) == (1, 5.0, 5.0)


def test_cost_per_call_docstring_says_when_and_in_what_currency():
    doc = cost_per_gateway_call.__doc__.lower()
    assert "day after" in doc
    assert "billing currency" in doc and "convert" in doc
    assert "never sum them across rows" in " ".join(doc.split())


# --- the notebook's entry point --------------------------------------------------------------


@pytest.fixture
def entry_lake(spark, tmp_lake, monkeypatch):
    monkeypatch.setattr(fabric_entry, "fabric_paths", lambda context: tmp_lake)
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: spark)
    return tmp_lake


def test_load_gateway_runs_silver_gold_check_and_answer(spark, entry_lake):
    rows = [_call("req-01"), _call("req-01"), _refused("req-02"), _call("req-03", session="replay")]
    _source(spark, entry_lake, rows)
    _gold_dims(spark, entry_lake, costs=[_cost(date(2026, 11, 12), 4.0)])

    summary = fabric_entry.load_gateway(SESSION, "2", {})

    json.dumps(summary)
    assert summary["silver"] == {"inserted": 2, "updated": 0}
    assert summary["duplicate_request_ids"] == 1
    assert summary["gold_rows"] == 2
    assert [c["name"] for c in summary["checks"]] == ["gateway_counts"]
    assert all(c["passed"] for c in summary["checks"])
    assert summary["cost_per_call"] == [
        {
            "call_date": "2026-11-12",
            "calls": 2,
            "tokens_total": 120,
            "tokens_per_call": 60.0,
            "billing_currency": "AUD",
            "apim_cost": 4.0,
            "cost_per_call": 2.0,
        }
    ]

    again = fabric_entry.load_gateway(SESSION, "2", {})
    assert again["silver"] == {"inserted": 0, "updated": 0}


def test_load_gateway_raises_when_the_count_check_fails(spark, entry_lake):
    _source(spark, entry_lake, [_call("req-01")])
    _gold_dims(spark, entry_lake)

    with pytest.raises(QualityCheckFailed, match="gateway_counts"):
        fabric_entry.load_gateway(SESSION, "5", {})


@pytest.mark.parametrize("count", ["", "two", "2.0", "-1", None])
def test_load_gateway_refuses_a_non_integer_count(count):
    with pytest.raises(ValueError, match="eventhouse_count"):
        fabric_entry.load_gateway(SESSION, count, {})


@pytest.mark.parametrize("session", ["", "   ", "replay"])
def test_load_gateway_refuses_an_empty_or_replay_session(session):
    with pytest.raises(ValueError, match="session"):
        fabric_entry.load_gateway(session, "2", {})


def test_load_gateway_reads_the_shortcut_under_dbo(spark, entry_lake, monkeypatch):
    """R1: the shortcut sits at Tables/dbo/gateway_calls."""
    seen = []
    real = fabric_entry.build_gateway_calls

    def spy(spark_, source_path):
        seen.append(source_path)
        return real(spark_, source_path)

    monkeypatch.setattr(fabric_entry, "build_gateway_calls", spy)
    _source(spark, entry_lake, [_call("req-01")])
    _gold_dims(spark, entry_lake)

    fabric_entry.load_gateway(SESSION, "1", {})

    assert seen == [f"{entry_lake.tables_root}/dbo/gateway_calls"]


def test_the_gateway_table_stays_out_of_project_9s_rerun_fingerprint():
    from lakehouse.gold.build import GOLD_TABLES, GRAINS

    assert GRAINS["fact_gateway_call"] == "gateway call"
    assert "fact_gateway_call" not in GOLD_TABLES


def test_the_cast_names_derive_from_the_spark_types():
    """One source for the contract's Spark types: the casts' DDL names and the gold schema agree."""
    from lakehouse.gateway.build import SPARK_TYPE_OBJECTS, SPARK_TYPES

    assert SPARK_TYPES == {"datetime": "timestamp", "string": "string", "int": "int", "bool": "boolean"}
    assert [f.dataType for f in FACT_GATEWAY_CALL_SCHEMA][: len(EVENT_FIELDS)] == [
        SPARK_TYPE_OBJECTS[kind] for _, kind in EVENT_FIELDS
    ]
