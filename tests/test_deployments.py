import hashlib
from pathlib import Path

import pytest
from delta.tables import DeltaTable

from lakehouse.silver.deployments import (
    CHANGES_SCHEMA,
    DEPLOYMENT_KEY,
    OPEN_FROM,
    apply_scd2,
    read_deployment_changes,
    scd2_rows,
)

SEEDS = str(Path(__file__).resolve().parent.parent / "seeds")
KEY = ("rl-primary", "releaselens-chat")
MEMBERS = "account_alias IN ('external', 'none')"


def _change(committed_at, capacity, version="2025-04-14", override=None, key=KEY):
    return {
        "account_alias": key[0],
        "deployment_name": key[1],
        "model_name": "gpt-4.1-mini",
        "model_version": version,
        "sku": "GlobalStandard",
        "capacity": capacity,
        "region_alias": "primary",
        "committed_at": committed_at,
        "effective_from_override": override,
    }


def _changes(spark, *rows):
    data = [tuple(r.get(f.name) for f in CHANGES_SCHEMA.fields) for r in rows]
    return spark.createDataFrame(data, schema=CHANGES_SCHEMA)


def _text(df):
    """Timestamps as UTC text (the session time zone is UTC): collected datetimes would be
    naive and in the Python process's zone."""
    return df.selectExpr(*[
        f"CAST({c} AS STRING) AS {c}" if c in ("valid_from", "valid_to") else c for c in df.columns
    ])


def _azure(df):
    """The Terraform-sourced rows, ordered by key and valid_from."""
    ordered = df.where(f"NOT ({MEMBERS})").orderBy("account_alias", "deployment_name", "valid_from")
    return [r.asDict() for r in _text(ordered).collect()]


def _utc(year, month, day, hour=0, minute=0, second=0):
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"


def _merge_metrics(spark, path):
    history = DeltaTable.forPath(spark, path).history(1)
    row = history.select("operation", "operationMetrics").first()
    assert row["operation"] == "MERGE"
    return row["operationMetrics"]


def test_new_version_closes_old(spark):
    changes = _changes(
        spark,
        _change("2026-09-27T15:51:42+10:00", 100),
        _change("2026-10-02T13:00:09+10:00", 300),
    )
    rows = _azure(scd2_rows(changes, SEEDS))

    assert len(rows) == 2
    first, second = rows
    assert first["valid_from"] == _utc(2026, 9, 27, 5, 51, 42)
    assert first["valid_to"] == second["valid_from"] == _utc(2026, 10, 2, 3, 0, 9)
    assert first["is_current"] is False
    assert (first["capacity"], second["capacity"]) == (100, 300)
    assert second["valid_to"] is None and second["is_current"] is True


def test_surrogate_key_is_hash_of_key_and_valid_from(spark):
    changes = _changes(spark, _change("2026-09-27T05:51:42+00:00", 100))
    rows = _azure(scd2_rows(changes, SEEDS))

    text = "rl-primary|releaselens-chat|2026-09-27T05:51:42.000000Z"
    assert rows[0]["deployment_sk"] == hashlib.sha256(text.encode()).hexdigest()[:16]
    assert DEPLOYMENT_KEY == ["account_alias", "deployment_name"]


def test_noop_change_collapses(spark):
    changes = _changes(
        spark,
        _change("2026-09-27T00:00:00+00:00", 100),
        _change("2026-09-28T00:00:00+00:00", 100),
        _change("2026-09-29T00:00:00+00:00", 300),
        _change("2026-09-30T00:00:00+00:00", 300),
    )
    rows = _azure(scd2_rows(changes, SEEDS))

    assert [r["capacity"] for r in rows] == [100, 300]
    assert rows[0]["valid_to"] == rows[1]["valid_from"] == _utc(2026, 9, 29)


def test_first_row_is_kept_even_when_every_attribute_is_null(spark):
    bare = {"account_alias": "a", "deployment_name": "d", "committed_at": "2026-01-01T00:00:00+00:00"}
    rows = _azure(scd2_rows(_changes(spark, bare), SEEDS))

    assert len(rows) == 1 and rows[0]["is_current"] is True


def test_two_changes_same_day(spark):
    changes = _changes(
        spark,
        _change("2026-10-05T20:00:00+00:00", 25),
        _change("2026-10-05T08:00:00+00:00", 50),
    )
    rows = _azure(scd2_rows(changes, SEEDS))

    assert [r["capacity"] for r in rows] == [50, 25]
    assert rows[0]["valid_to"] == rows[1]["valid_from"] == _utc(2026, 10, 5, 20)


def test_override_wins(spark):
    changes = _changes(
        spark,
        _change("2026-10-05T10:00:00+00:00", 25),
        _change(None, 80, override="2026-10-06T00:36:37.662848Z"),
    )
    rows = _azure(scd2_rows(changes, SEEDS))

    assert [r["capacity"] for r in rows] == [25, 80]
    assert rows[1]["valid_from"] == "2026-10-06 00:36:37.662848"
    assert rows[0]["valid_to"] == rows[1]["valid_from"]


def test_override_replaces_committed_at_on_the_same_row(spark):
    changes = _changes(spark, _change("2026-10-05T10:00:00+00:00", 25, override="2026-10-01T00:00:00Z"))
    rows = _azure(scd2_rows(changes, SEEDS))

    assert rows[0]["valid_from"] == _utc(2026, 10, 1)


def test_keys_are_independent(spark):
    changes = _changes(
        spark,
        _change("2026-10-01T00:00:00+00:00", 100),
        _change("2026-10-02T00:00:00+00:00", 100, key=("rl-sea", "releaselens-chat")),
    )
    rows = _azure(scd2_rows(changes, SEEDS))

    assert len(rows) == 2 and all(r["is_current"] for r in rows)


def test_external_and_none_members(spark):
    members = {
        (r["account_alias"], r["deployment_name"]): r
        for r in _text(scd2_rows(_changes(spark), SEEDS).where(MEMBERS)).collect()
    }

    assert set(members) == {
        ("external", "claude-sonnet-5"),
        ("external", "gpt-4.1-mini@openai"),
        ("none", "no-model"),
    }
    assert OPEN_FROM.isoformat() == "1900-01-01T00:00:00+00:00"
    for row in members.values():
        assert row["valid_from"] == _utc(1900, 1, 1)
        assert row["valid_to"] is None
        assert row["is_current"] is True
        assert row["sku"] is None and row["capacity"] is None
    assert members[("external", "gpt-4.1-mini@openai")]["model_name"] == "gpt-4.1-mini"
    assert members[("none", "no-model")]["model_name"] == "no-model"


def test_reapply_is_noop(spark, tmp_path):
    table = str(tmp_path / "silver" / "dim_model_deployment")
    changes = _changes(
        spark,
        _change("2026-09-27T00:00:00+00:00", 100),
        _change("2026-10-02T00:00:00+00:00", 300),
    )

    apply_scd2(spark, changes, table, SEEDS)
    assert spark.read.format("delta").load(table).count() == 5  # 2 versions + 2 external + none
    apply_scd2(spark, changes, table, SEEDS)

    metrics = _merge_metrics(spark, table)
    assert int(metrics["numTargetRowsInserted"]) == 0
    assert int(metrics["numTargetRowsUpdated"]) == 0
    assert spark.read.format("delta").load(table).count() == 5


def test_incremental_change(spark, tmp_path):
    table = str(tmp_path / "silver" / "dim_model_deployment")
    first = [_change("2026-09-27T00:00:00+00:00", 100), _change("2026-10-02T00:00:00+00:00", 300)]
    apply_scd2(spark, _changes(spark, *first), table, SEEDS)

    third = _change("2026-10-09T00:00:00+00:00", 500)
    apply_scd2(spark, _changes(spark, *first, third), table, SEEDS)

    metrics = _merge_metrics(spark, table)
    assert int(metrics["numTargetRowsInserted"]) == 1
    assert int(metrics["numTargetRowsUpdated"]) == 1
    rows = _azure(spark.read.format("delta").load(table))
    assert [r["capacity"] for r in rows] == [100, 300, 500]
    assert [r["is_current"] for r in rows] == [False, False, True]
    assert rows[1]["valid_to"] == rows[2]["valid_from"] == _utc(2026, 10, 9)


def test_same_instant_with_different_attributes_is_rejected(spark, tmp_path):
    table = str(tmp_path / "silver" / "dim_model_deployment")
    changes = _changes(
        spark,
        _change("2026-09-27T00:00:00+00:00", 100),
        _change("2026-09-27T00:00:00+00:00", 300),
    )

    with pytest.raises(ValueError, match="same valid_from"):
        apply_scd2(spark, changes, table, SEEDS)


def test_committed_seed_resolves_every_dim_arm_deployment(spark):
    changes = read_deployment_changes(spark, f"{SEEDS}/deployment_changes.csv")
    assert changes.columns == CHANGES_SCHEMA.fieldNames()
    rows = scd2_rows(changes, SEEDS)

    current = {
        (r["account_alias"], r["deployment_name"]): r.asDict()
        for r in rows.where("is_current").collect()
    }
    keys = rows.select(*DEPLOYMENT_KEY).distinct().count()
    assert len(current) == keys  # exactly one current member per key

    dim_arm = spark.read.option("header", True).csv(f"{SEEDS}/dim_arm.csv").collect()
    for arm in dim_arm:
        assert (arm["account_alias"], arm["deployment_name"]) in current
    assert current[("fwa", "gpt-5.6-luna")]["capacity"] == 80
    assert current[("fwa", "claude-haiku-4-5")]["capacity"] == 80
    assert current[("fwa", "claude-haiku-4-5")]["model_version"] == "2"
    assert current[("rl-primary", "releaselens-chat")]["capacity"] == 300
    assert current[("rl-primary", "releaselens-chat-failover-test")]["capacity"] == 1
