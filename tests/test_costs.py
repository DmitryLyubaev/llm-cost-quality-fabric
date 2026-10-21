from datetime import date
from pathlib import Path

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lakehouse.attribution import load_rg_rules, load_session_windows
from lakehouse.silver.costs import (
    COST_COLUMN_MAP,
    COST_COLUMNS,
    normalise_costs,
    read_cost_export,
    replace_month,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "cost_export_2026-10.csv"
RULES = load_rg_rules(ROOT / "seeds" / "rg_rules.csv")
WINDOWS = load_session_windows(ROOT / "seeds" / "session_windows.csv")
ROWS = 11


def _export(spark, tmp_path, name="export.csv", month="2026-10"):
    text = FIXTURE.read_text(encoding="utf-8")
    path = tmp_path / name
    path.write_text(text.replace("2026-10-", f"{month}-"), encoding="utf-8", newline="\n")
    return read_cost_export(spark, str(path))


def _silver(spark, tmp_path, name="export.csv", month="2026-10"):
    return normalise_costs(spark, _export(spark, tmp_path, name, month), RULES, WINDOWS)


def _total(df, column="cost"):
    return df.agg(F.sum(column)).first()[0]


def test_column_map_covers_every_silver_input():
    assert set(COST_COLUMN_MAP.values()) == {
        "cost_date", "resource_id", "resource_group", "meter_id", "meter_name",
        "cost", "billing_currency", "tags",
    }


def test_normalise_columns_and_month(spark, tmp_path):
    silver = _silver(spark, tmp_path)
    assert silver.columns == COST_COLUMNS
    assert silver.count() == ROWS
    assert {r[0] for r in silver.select("billing_month").distinct().collect()} == {"2026-10"}


def test_no_row_dropped(spark, tmp_path):
    export = _export(spark, tmp_path)
    silver = normalise_costs(spark, export, RULES, WINDOWS)
    assert silver.count() == export.count() == ROWS
    expected = sum(float(r["Cost"]) for r in export.collect())
    assert _total(silver) == pytest.approx(expected, abs=1e-9)


def test_attribution_applied_per_row(spark, tmp_path):
    rows = {
        (r["resource_group"], r["cost_date"]): (r["project"], r["attribution_rule"])
        for r in _silver(spark, tmp_path).collect()
    }
    assert rows[("rg-releaselens-bootstrap", date(2026, 10, 2))] == ("p1", "shared_window")
    assert rows[("rg-releaselens-bootstrap", date(2026, 10, 3))] == ("p2", "shared_window")
    assert rows[("rg-releaselens-bootstrap", date(2026, 10, 25))] == ("releaselens-shared", "shared_default")
    assert rows[("rg-releaselens-search", date(2026, 10, 4))] == ("p2", "shared_window")
    assert rows[("rg-releaselens-gateway", date(2026, 10, 8))] == ("p4", "rg_rule")
    assert rows[("RG-FWA-eval", date(2026, 10, 12))] == ("p3", "rg_rule")
    assert rows[("rg-releaselens-webapp", date(2026, 10, 14))] == ("p6", "rg_rule")
    assert rows[("rg-p8-platform", date(2026, 10, 16))] == ("p8", "rg_rule")
    assert rows[("rg-unknown", date(2026, 10, 18))] == ("releaselens-shared", "tag")
    assert rows[("rg-unknown", date(2026, 10, 19))] == ("untagged", "untagged")
    assert rows[(None, date(2026, 10, 20))] == ("untagged", "untagged")


def test_tags_are_parsed_into_a_lower_cased_map(spark, tmp_path):
    silver = _silver(spark, tmp_path)
    row = silver.filter(F.col("cost_date") == date(2026, 10, 3)).first()
    assert row["tags"] == {"project": "ReleaseLens", "purpose": "bootstrap"}
    empty = silver.filter(F.col("cost_date") == date(2026, 10, 25)).first()
    assert empty["tags"] == {}
    capitalised = silver.filter(F.col("cost_date") == date(2026, 10, 18)).first()
    assert capitalised["tags"] == {"project": "ReleaseLens"}


def test_header_names_match_case_insensitively(spark, tmp_path):
    export = _export(spark, tmp_path)
    shouty = export.toDF(*[c.upper() for c in export.columns])
    assert normalise_costs(spark, shouty, RULES, WINDOWS).count() == ROWS


def test_missing_export_column_fails_loudly(spark, tmp_path):
    export = _export(spark, tmp_path).drop("MeterId")
    with pytest.raises(ValueError, match="MeterId"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_us_style_dates_are_accepted(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn("Date", F.date_format(F.to_date("Date"), "MM/dd/yyyy"))
    silver = normalise_costs(spark, export, RULES, WINDOWS)
    assert silver.filter(F.col("cost_date") == date(2026, 10, 3)).count() == 1


def test_malformed_row_fails_loudly(spark, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(
        FIXTURE.read_text(encoding="utf-8") + "2026-10-21,/x,rg-x,m,n,c,1,2,AUD,,EXTRA,COLUMNS\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(Exception):
        read_cost_export(spark, str(bad)).collect()


def test_non_numeric_cost_fails_loudly(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn("Cost", F.lit("n/a"))
    with pytest.raises(Exception):
        normalise_costs(spark, export, RULES, WINDOWS).collect()


def test_empty_cost_is_an_error(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "Cost", F.when(F.col("MeterId") == "meter-blob", F.lit(None)).otherwise(F.col("Cost"))
    )
    with pytest.raises(ValueError, match=r"1 row.*cost"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_unreadable_cost_is_an_error_even_without_ansi(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "Cost", F.when(F.col("MeterId") == "meter-blob", F.lit("abc")).otherwise(F.col("Cost"))
    )
    with pytest.raises(ValueError, match=r"1 row.*cost"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_unreadable_date_is_an_error(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "Date", F.when(F.col("MeterId") == "meter-blob", F.lit("someday")).otherwise(F.col("Date"))
    )
    with pytest.raises(ValueError, match=r"1 row.*cost_date"):
        normalise_costs(spark, export, RULES, WINDOWS)


def _months(spark, path):
    frame = spark.read.format("delta").load(path)
    return {r["billing_month"]: r["n"] for r in frame.groupBy("billing_month").agg(F.count("*").alias("n")).collect()}


def test_replace_month_only_touches_that_month(spark, tmp_path, tmp_lake):
    table = tmp_lake.table("silver", "fact_cost")
    sept = _silver(spark, tmp_path, "sept.csv", "2026-09")
    octo = _silver(spark, tmp_path, "oct.csv", "2026-10")
    replace_month(spark, sept, table, "2026-09")
    replace_month(spark, octo, table, "2026-10")
    sept_total = _total(spark.read.format("delta").load(table).filter("billing_month = '2026-09'"))

    restated = octo.withColumn("cost", F.col("cost") * 2)
    replace_month(spark, restated, table, "2026-10")

    current = spark.read.format("delta").load(table)
    assert _months(spark, table) == {"2026-09": ROWS, "2026-10": ROWS}
    assert _total(current.filter("billing_month = '2026-09'")) == pytest.approx(sept_total)
    assert _total(current.filter("billing_month = '2026-10'")) == pytest.approx(2 * _total(octo))


def test_replace_month_shrinks(spark, tmp_path, tmp_lake):
    table = tmp_lake.table("silver", "fact_cost")
    octo = _silver(spark, tmp_path)
    replace_month(spark, octo, table, "2026-10")

    restated = octo.filter(~F.col("resource_group").eqNullSafe("rg-releaselens-gateway"))
    replace_month(spark, restated, table, "2026-10")

    current = spark.read.format("delta").load(table)
    assert current.filter(F.col("resource_group") == "rg-releaselens-gateway").count() == 0
    assert current.count() == ROWS - 1


def test_replace_month_is_idempotent(spark, tmp_path, tmp_lake):
    table = tmp_lake.table("silver", "fact_cost")
    octo = _silver(spark, tmp_path)
    replace_month(spark, octo, table, "2026-10")
    replace_month(spark, octo, table, "2026-10")
    assert spark.read.format("delta").load(table).count() == ROWS


def test_replace_month_rejects_other_months(spark, tmp_path, tmp_lake):
    table = tmp_lake.table("silver", "fact_cost")
    sept = _silver(spark, tmp_path, "sept.csv", "2026-09")
    octo = _silver(spark, tmp_path, "oct.csv", "2026-10")
    replace_month(spark, octo, table, "2026-10")
    mixed = octo.unionByName(sept)

    with pytest.raises(ValueError, match="2026-09"):
        replace_month(spark, mixed, table, "2026-10")
    # the table is untouched by the refused write
    assert _months(spark, table) == {"2026-10": ROWS}
    assert DeltaTable.isDeltaTable(spark, table)
