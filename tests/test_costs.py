import codecs
import gzip
import json
from datetime import date

import pytest
from delta.tables import DeltaTable
from pyspark.sql import functions as F

from lake_helpers import COST_EXPORT_ROOT, cost_export_maker as maker
from lakehouse.attribution import load_rg_rules, load_session_windows
from lakehouse.seeding import SEEDS_DIR
from lakehouse.silver.costs import (
    COST_COLUMN_ALIASES,
    COST_COLUMNS,
    OPTIONAL_COST_ALIASES,
    ExportRun,
    find_latest_runs,
    month_range,
    normalise_costs,
    read_cost_export,
    replace_month,
    resolve_cost_headers,
)

RULES = load_rg_rules(SEEDS_DIR)
WINDOWS = load_session_windows(SEEDS_DIR)
ROWS = 11
SILVER_INPUTS = [*COST_COLUMN_ALIASES, *OPTIONAL_COST_ALIASES]
MOSA_HEADER = list(maker.HEADERS["mosa"].values())
NEW_HEADER = list(maker.HEADERS["new"].values())
SUBMITTED = "2026-10-21T03:30:09Z"
BOM = codecs.BOM_UTF8


def _rows(month="2026-10"):
    return [{**r, "cost_date": r["cost_date"].replace("2026-10", month)} for r in maker.OCTOBER_ROWS]


def _folder(month):
    year, mon = month.split("-")
    last = {"09": "30", "10": "31"}[mon]
    return f"{year}{mon}01-{year}{mon}{last}"


def _write(tmp_path, name="export", month="2026-10", style="mosa", run=maker.RUN_B):
    """An export root with one run holding the 11 October rows moved to `month`."""
    root = tmp_path / name
    maker.write_run(root, _folder(month), run, SUBMITTED, [_rows(month)], style=style)
    return root


def _export(spark, tmp_path, name="export", month="2026-10", style="mosa"):
    return read_cost_export(spark, str(_write(tmp_path, name, month, style)))[0]


def _silver(spark, tmp_path, name="export", month="2026-10", style="mosa"):
    return normalise_costs(spark, _export(spark, tmp_path, name, month, style), RULES, WINDOWS)


def _total(df, column="cost"):
    return df.agg(F.sum(column)).first()[0]


def test_aliases_cover_every_silver_input():
    assert set(COST_COLUMN_ALIASES) == {
        "cost_date", "resource_id", "resource_group", "meter_id", "meter_name",
        "cost", "billing_currency", "tags",
    }
    assert COST_COLUMN_ALIASES["cost_date"] == ("UsageDateTime", "Date")
    assert COST_COLUMN_ALIASES["cost"] == ("PreTaxCost", "CostInBillingCurrency", "Cost")


def test_optional_aliases_are_separate_from_the_required_ones():
    assert OPTIONAL_COST_ALIASES == {
        "pricing_currency": ("PricingCurrency", "PricingCurrencyCode"),
        "fx_pricing_to_billing": ("ExchangeRatePricingToBilling",),
    }
    assert not set(OPTIONAL_COST_ALIASES) & set(COST_COLUMN_ALIASES)
    assert COST_COLUMNS[COST_COLUMNS.index("billing_currency") + 1 : COST_COLUMNS.index("billing_currency") + 3] == [
        "pricing_currency", "fx_pricing_to_billing",
    ]


def test_mosa_headers_resolve(spark, tmp_path):
    assert resolve_cost_headers(MOSA_HEADER) == {
        "cost_date": "UsageDateTime", "resource_id": "InstanceId", "resource_group": "ResourceGroup",
        "meter_id": "MeterId", "meter_name": "MeterName", "cost": "PreTaxCost",
        "billing_currency": "Currency", "tags": "Tags",
    }
    frame = _export(spark, tmp_path, style="mosa")
    assert frame.columns == SILVER_INPUTS
    assert frame.count() == ROWS
    assert frame.filter(F.col("meter_id") == "meter-apim").first()["cost"] == "0.41"


def test_new_headers_resolve(spark, tmp_path):
    """The newer names, in camelCase: matching ignores case."""
    assert resolve_cost_headers(NEW_HEADER) == {
        "cost_date": "date", "resource_id": "resourceId", "resource_group": "resourceGroupName",
        "meter_id": "meterId", "meter_name": "meterName", "cost": "costInBillingCurrency",
        "billing_currency": "billingCurrencyCode", "tags": "tags",
        "pricing_currency": "pricingCurrency", "fx_pricing_to_billing": "exchangeRatePricingToBilling",
    }
    frame = _export(spark, tmp_path, style="new")
    assert frame.columns == SILVER_INPUTS
    assert frame.count() == ROWS
    assert frame.filter(F.col("meter_id") == "meter-apim").first()["cost"] == "0.41"


def test_first_alias_wins_and_matching_ignores_case():
    header = ["COST", "PRETAXCOST", "usagedatetime", "Date", "instanceid", "RESOURCEGROUP",
              "meterid", "MeterName", "currency", "billingcurrencycode", "TAGS"]
    got = resolve_cost_headers(header)
    assert got["cost"] == "PRETAXCOST" and got["cost_date"] == "usagedatetime"
    assert got["billing_currency"] == "currency"


def test_optional_headers_match_ignoring_case_and_may_be_absent():
    got = resolve_cost_headers([*MOSA_HEADER, "PRICINGCURRENCYCODE", "exchangeratepricingtobilling"])
    assert got["pricing_currency"] == "PRICINGCURRENCYCODE"
    assert got["fx_pricing_to_billing"] == "exchangeratepricingtobilling"
    assert "pricing_currency" not in resolve_cost_headers(MOSA_HEADER)  # absent is not an error


def test_unknown_headers_raise_listing_found(spark, tmp_path):
    with pytest.raises(ValueError, match=r"Foo.*Bar|Bar.*Foo") as error:
        resolve_cost_headers(["Foo", "Bar"])
    assert "cost_date" in str(error.value)

    # a header set that lacks only the cost column, read from a real export
    root = tmp_path / "bad"
    text = maker.csv_text(_rows(), "mosa").replace("PreTaxCost", "Charge")
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()], part_texts=[text])
    with pytest.raises(ValueError, match=r"Charge.*cost|cost.*Charge"):
        read_cost_export(spark, str(root))


def test_latest_run_per_month_only(spark):
    """Review Focus 2: October is run-b's rows, not run-a's; September's run-c is read as well."""
    runs = find_latest_runs(spark, str(COST_EXPORT_ROOT))
    assert [(r.month_folder, r.run_id) for r in runs] == [(maker.SEP, maker.RUN_C), (maker.OCT, maker.RUN_B)]
    assert all(isinstance(r, ExportRun) for r in runs)

    frame, expected = read_cost_export(spark, str(COST_EXPORT_ROOT))

    assert expected == {maker.SEP: 4, maker.OCT: 11}
    assert frame.count() == 15
    assert frame.filter(F.col("meter_id") == "meter-stale").count() == 0  # run-a's extra row
    assert frame.filter(F.col("cost_date").startswith("2026-09")).count() == 4
    assert frame.columns == SILVER_INPUTS
    # the two months carry different header schemas and both land under silver names
    assert frame.filter(F.col("meter_id") == "meter-apim").count() == 2


def test_superseded_run_is_not_read(spark, tmp_path):
    root = tmp_path / "export"
    maker.write_run(root, maker.OCT, maker.RUN_A, "2026-10-20T03:30:05Z", [_rows() + [maker.STALE_ROW]])
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()])

    frame, expected = read_cost_export(spark, str(root))

    assert expected == {maker.OCT: ROWS} and frame.count() == ROWS


def test_equal_submitted_time_breaks_the_tie_on_run_id(spark, tmp_path):
    root = tmp_path / "export"
    maker.write_run(root, maker.OCT, "run-1", SUBMITTED, [_rows()[:3]])
    maker.write_run(root, maker.OCT, "run-2", SUBMITTED, [_rows()[:5]])

    runs = find_latest_runs(spark, str(root))

    assert [(r.run_id, r.data_row_count) for r in runs] == [("run-2", 5)]


def test_runs_of_one_calendar_month_compete_across_export_names(spark, tmp_path):
    """A one-time export of 1-22 October submitted earlier and the daily export of the whole of
    October submitted later overlap: only the daily run is read, so no day is counted twice."""
    root = tmp_path / "export"
    maker.write_run(
        root, "20261001-20261022", "run-onetime", "2026-10-20T03:30:05Z",
        [_rows()[:3] + [maker.STALE_ROW]], export_name="portfolio-onetime",
    )
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()])

    runs = find_latest_runs(spark, str(root))
    frame, expected = read_cost_export(spark, str(root))

    assert [(r.month_folder, r.run_id) for r in runs] == [(maker.OCT, maker.RUN_B)]
    assert expected == {maker.OCT: ROWS} and frame.count() == ROWS
    assert frame.filter(F.col("meter_id") == "meter-stale").count() == 0


def test_a_later_one_time_export_wins_its_month(spark, tmp_path):
    root = tmp_path / "export"
    maker.write_run(root, maker.OCT, maker.RUN_B, "2026-10-20T03:30:05Z", [_rows()])
    maker.write_run(
        root, "20261001-20261022", "run-onetime", SUBMITTED, [_rows()[:3]], export_name="portfolio-onetime",
    )

    runs = find_latest_runs(spark, str(root))

    assert [(r.month_folder, r.run_id, r.data_row_count) for r in runs] == [
        ("20261001-20261022", "run-onetime", 3)
    ]


def test_a_run_spanning_two_calendar_months_raises_naming_it(spark, tmp_path):
    root = tmp_path / "export"
    maker.write_run(root, "20260915-20261014", maker.RUN_B, SUBMITTED, [_rows()[:3]])

    with pytest.raises(ValueError, match="20260915-20261014"):
        find_latest_runs(spark, str(root))


def test_a_superseded_run_with_a_missing_part_is_not_checked(spark, tmp_path):
    root = tmp_path / "export"
    # the superseded run has the bigger manifest, so Spark lists it first
    maker.write_run(root, maker.OCT, maker.RUN_A, "2026-10-20T03:30:05Z", [_rows()[:6], _rows()[6:]])
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()])
    (root / maker.EXPORT_NAME / maker.OCT / maker.RUN_A / "part1.csv.gz").unlink()

    runs = find_latest_runs(spark, str(root))

    assert [r.run_id for r in runs] == [maker.RUN_B]


def test_a_manifest_with_a_utf8_bom_parses(spark, tmp_path):
    root = _write(tmp_path)
    path = root / maker.EXPORT_NAME / maker.OCT / maker.RUN_B / "manifest.json"
    path.write_bytes(BOM + path.read_bytes())

    runs = find_latest_runs(spark, str(root))

    assert [(r.run_id, r.data_row_count) for r in runs] == [(maker.RUN_B, ROWS)]


def test_find_latest_runs_describes_the_run(spark):
    run = {r.month_folder: r for r in find_latest_runs(spark, str(COST_EXPORT_ROOT))}[maker.OCT]

    assert run.data_row_count == 11
    assert run.submitted.isoformat().startswith("2026-10-21T03:30:09")
    assert [b.rsplit("/", 1)[-1] for b in run.blobs] == ["part0.csv.gz", "part1.csv.gz"]
    # resolved next to the manifest, not from the manifest's container-relative blobName
    assert all(b.startswith(str(COST_EXPORT_ROOT)) and f"/{maker.OCT}/{maker.RUN_B}/" in b for b in run.blobs)


def test_blob_listed_but_missing_raises_naming_it(spark, tmp_path):
    root = _write(tmp_path)
    (root / maker.EXPORT_NAME / maker.OCT / maker.RUN_B / "part0.csv.gz").unlink()

    with pytest.raises(FileNotFoundError, match="part0.csv.gz"):
        find_latest_runs(spark, str(root))


def test_no_manifest_raises(spark, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "stray.txt").write_text("x", encoding="utf-8")

    for root in (empty, tmp_path / "does_not_exist"):
        with pytest.raises(FileNotFoundError, match=root.name):
            read_cost_export(spark, str(root))
        with pytest.raises(FileNotFoundError, match=root.name):
            find_latest_runs(spark, str(root))


def test_manifest_without_run_info_is_an_error(spark, tmp_path):
    root = _write(tmp_path)
    path = root / maker.EXPORT_NAME / maker.OCT / maker.RUN_B / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["runInfo"]["submittedTime"]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="submittedTime"):
        find_latest_runs(spark, str(root))


def test_part_files_with_different_headers_raise(spark, tmp_path):
    root = tmp_path / "export"
    texts = [maker.csv_text(_rows()[:6], "mosa"), maker.csv_text(_rows()[6:], "new")]
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()[:6], _rows()[6:]], part_texts=texts)

    with pytest.raises(ValueError, match=r"part1.*header|header.*part1"):
        read_cost_export(spark, str(root))


def test_parts_may_be_plain_csv_and_header_case_may_differ_between_parts(spark, tmp_path):
    root = tmp_path / "export"
    first = maker.csv_text(_rows()[:6], "mosa")
    second = maker.csv_text(_rows()[6:], "mosa").replace("PreTaxCost", "PRETAXCOST")
    maker.write_run(
        root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()[:6], _rows()[6:]],
        part_texts=[first, second], gzipped=False,
    )

    frame, _ = read_cost_export(spark, str(root))

    assert frame.count() == ROWS


def test_a_part_without_a_header_is_an_error(spark, tmp_path):
    root = tmp_path / "export"
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()], part_texts=[""])

    with pytest.raises(ValueError, match="part0.csv.gz"):
        read_cost_export(spark, str(root))


def test_month_range():
    assert month_range("20261001-20261031") == (date(2026, 10, 1), date(2026, 10, 31))
    with pytest.raises(ValueError, match="2026-10"):
        month_range("2026-10")


def test_normalise_columns_and_month(spark, tmp_path):
    silver = _silver(spark, tmp_path)
    assert silver.columns == COST_COLUMNS
    assert silver.count() == ROWS
    assert {r[0] for r in silver.select("billing_month").distinct().collect()} == {"2026-10"}


def test_no_row_dropped(spark, tmp_path):
    export = _export(spark, tmp_path)
    silver = normalise_costs(spark, export, RULES, WINDOWS)
    assert silver.count() == export.count() == ROWS
    expected = sum(float(r["cost"]) for r in export.collect())
    assert _total(silver) == pytest.approx(expected, abs=1e-9)


def test_both_header_styles_give_the_same_silver_rows(spark, tmp_path):
    mosa = _silver(spark, tmp_path, "m", style="mosa")
    new = _silver(spark, tmp_path, "n", style="new")
    # the newer export also carries the pricing currency and rate, which the MOSA one cannot
    rate = ["pricing_currency", "fx_pricing_to_billing"]
    assert sorted(map(tuple, mosa.drop("tags", *rate).collect())) == sorted(
        map(tuple, new.drop("tags", *rate).collect())
    )


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


def test_missing_silver_column_fails_loudly(spark, tmp_path):
    export = _export(spark, tmp_path).drop("meter_id")
    with pytest.raises(ValueError, match="meter_id"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_us_style_dates_are_accepted(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "cost_date", F.date_format(F.to_date("cost_date"), "MM/dd/yyyy")
    )
    silver = normalise_costs(spark, export, RULES, WINDOWS)
    assert silver.filter(F.col("cost_date") == date(2026, 10, 3)).count() == 1


def test_malformed_row_fails_loudly(spark, tmp_path):
    """FAILFAST survives the pruned select: a row with extra fields raises on read."""
    root = tmp_path / "bad"
    text = maker.csv_text(_rows(), "mosa") + "2026-10-21,/x,rg-x,m,n,c,1,2,AUD,,EXTRA,COLUMNS\n"
    maker.write_run(root, maker.OCT, maker.RUN_B, SUBMITTED, [_rows()], part_texts=[text])
    with pytest.raises(Exception):
        read_cost_export(spark, str(root))[0].collect()


def test_non_numeric_cost_fails_loudly(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn("cost", F.lit("n/a"))
    with pytest.raises(Exception):
        normalise_costs(spark, export, RULES, WINDOWS).collect()


def test_empty_cost_is_an_error(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "cost", F.when(F.col("meter_id") == "meter-blob", F.lit(None)).otherwise(F.col("cost"))
    )
    with pytest.raises(ValueError, match=r"1 row.*cost"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_unreadable_cost_is_an_error_even_without_ansi(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "cost", F.when(F.col("meter_id") == "meter-blob", F.lit("abc")).otherwise(F.col("cost"))
    )
    with pytest.raises(ValueError, match=r"1 row.*cost"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_the_export_rate_is_carried_into_silver(spark, tmp_path):
    new = _silver(spark, tmp_path, style="new")
    assert {(r["pricing_currency"], r["fx_pricing_to_billing"]) for r in new.collect()} == {("USD", 1.5)}
    assert dict(new.dtypes)["pricing_currency"] == "string"
    assert dict(new.dtypes)["fx_pricing_to_billing"] == "double"


def test_an_export_without_the_rate_columns_gives_null_columns(spark, tmp_path):
    """The MOSA 2019-11-01 schema has neither column: silver keeps working, with nulls."""
    mosa = _silver(spark, tmp_path, style="mosa")
    assert mosa.count() == ROWS
    assert mosa.filter(F.col("pricing_currency").isNotNull() | F.col("fx_pricing_to_billing").isNotNull()).count() == 0
    assert dict(mosa.dtypes)["fx_pricing_to_billing"] == "double"


def test_an_empty_rate_is_null_and_a_non_numeric_rate_is_an_error(spark, tmp_path):
    export = _export(spark, tmp_path, style="new")
    blank = export.withColumn(
        "fx_pricing_to_billing",
        F.when(F.col("meter_id") == "meter-blob", F.lit(" ")).otherwise(F.col("fx_pricing_to_billing")),
    )
    silver = normalise_costs(spark, blank, RULES, WINDOWS)
    assert silver.filter(F.col("fx_pricing_to_billing").isNull()).count() == 1

    bad = export.withColumn(
        "fx_pricing_to_billing",
        F.when(F.col("meter_id") == "meter-blob", F.lit("one point five")).otherwise(F.col("fx_pricing_to_billing")),
    )
    with pytest.raises(ValueError, match=r"1 row.*fx_pricing_to_billing"):
        normalise_costs(spark, bad, RULES, WINDOWS)


def test_unreadable_date_is_an_error(spark, tmp_path):
    export = _export(spark, tmp_path).withColumn(
        "cost_date", F.when(F.col("meter_id") == "meter-blob", F.lit("someday")).otherwise(F.col("cost_date"))
    )
    with pytest.raises(ValueError, match=r"1 row.*cost_date"):
        normalise_costs(spark, export, RULES, WINDOWS)


def test_fixture_generator_is_reproducible(tmp_path):
    """The committed tree is what the generator writes: manifests byte for byte, parts by content."""
    maker.build(tmp_path)
    committed = sorted(p.relative_to(COST_EXPORT_ROOT) for p in COST_EXPORT_ROOT.rglob("*") if p.is_file())
    assert committed == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    for rel in committed:
        mine, theirs = (tmp_path / rel).read_bytes(), (COST_EXPORT_ROOT / rel).read_bytes()
        if rel.suffix == ".gz":
            assert theirs[4:8] == b"\x00\x00\x00\x00"  # the gzip header's mtime field
            assert gzip.decompress(mine) == gzip.decompress(theirs), rel
        else:
            assert mine == theirs, rel
            assert b"\r" not in theirs


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


def test_replace_month_adds_new_columns_to_a_table_written_before_them(spark, tmp_path, tmp_lake):
    """A silver table written with the older column set accepts the restated month with the new."""
    table = tmp_lake.table("silver", "fact_cost")
    rate = ["pricing_currency", "fx_pricing_to_billing"]
    sept = _silver(spark, tmp_path, "sept", "2026-09")
    replace_month(spark, sept.drop(*rate), table, "2026-09")
    old = _silver(spark, tmp_path, "oct-old", "2026-10", style="new").drop(*rate)
    replace_month(spark, old, table, "2026-10")
    assert not set(rate) & set(spark.read.format("delta").load(table).columns)

    replace_month(spark, _silver(spark, tmp_path, "oct-new", "2026-10", style="new"), table, "2026-10")

    current = spark.read.format("delta").load(table)
    assert set(rate) <= set(current.columns)
    assert _months(spark, table) == {"2026-09": ROWS, "2026-10": ROWS}
    assert current.filter("billing_month = '2026-10'").filter(F.col("fx_pricing_to_billing") == 1.5).count() == ROWS
    assert current.filter("billing_month = '2026-09'").filter(F.col("fx_pricing_to_billing").isNotNull()).count() == 0


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
