"""The capstone's answer functions and the expected-answer builder, on the fixture lake."""

import importlib

import pytest
from pyspark.sql import functions as F

import lakehouse.capstone.answers as capstone_answers
from lakehouse.capstone import expected
from lakehouse.capstone.answers import (
    cost_by_project_between,
    eval_count_by_arm,
    live_check_counts,
    spend_for_month,
)
from lakehouse.capstone.expected import MAX_ROWS, Source, compute_expected, resolve_function


def _gold(spark, paths, name):
    return spark.read.format("delta").load(paths.table("gold", name))


def _totals(frame, value="total"):
    return {(r["project"], r["billing_currency"]): r[value] for r in frame.collect()}


def _question(qid, function, args=None, select=None, column=None, kind="money"):
    return {
        "id": qid,
        "text": f"question {qid}",
        "kind": kind,
        "source": {"function": function, "args": args or {}, "select": select, "column": column},
    }


def _fake_answer(monkeypatch, name, fn):
    """Install `fn` as a public function of lakehouse.capstone.answers, as if defined there."""
    fn.__module__ = "lakehouse.capstone.answers"
    monkeypatch.setattr(capstone_answers, name, fn, raising=False)
    return f"lakehouse.capstone.answers.{name}"


# --- the four answer functions ---------------------------------------------------------------


def test_spend_for_month_matches_gold(spark, loaded_lake):
    paths = loaded_lake[0]

    frame = spend_for_month(spark, paths, month="2026-10")

    assert frame.columns == ["project", "billing_currency", "total"]
    fact = _gold(spark, paths, "fact_daily_cost").where(F.col("billing_month") == "2026-10")
    want = _totals(fact.groupBy("project", "billing_currency").agg(F.sum("cost").alias("t")), "t")
    got = _totals(frame)
    assert got == pytest.approx(want)
    assert got[("p3", "AUD")] == pytest.approx(3.5)
    assert len(got) == 8


def test_spend_for_month_other_month_and_empty_month(spark, loaded_lake):
    paths = loaded_lake[0]

    september = {r["project"]: r["total"] for r in spend_for_month(spark, paths, month="2026-09").collect()}
    empty = spend_for_month(spark, paths, month="2020-01")

    assert september == pytest.approx({"p1": 1.78, "p4": 0.2, "untagged": 0.11})
    assert empty.count() == 0
    assert empty.columns == ["project", "billing_currency", "total"]


@pytest.mark.parametrize("month", ["2026-9", "2026-13", "202610", "2026-10-01", "", None])
def test_spend_for_month_refuses_a_bad_month(spark, loaded_lake, month):
    with pytest.raises(ValueError, match="month"):
        spend_for_month(spark, loaded_lake[0], month=month)


def test_live_check_counts_matches_gold(spark, loaded_lake):
    paths = loaded_lake[0]

    frame = live_check_counts(spark, paths)

    assert frame.columns == ["project", "verdict", "n"]
    pairs = [(r["project"], r["verdict"]) for r in frame.collect()]
    got = {(r["project"], r["verdict"]): r["n"] for r in frame.collect()}
    assert got[("p7", "fail")] == 6
    assert got[("p5", "pass")] == 8
    assert ("p5", "fail") not in got
    assert sum(got.values()) == _gold(spark, paths, "fact_live_check").count()
    assert pairs == sorted(pairs)


def test_eval_count_by_arm_counts_scored_attempts_only(spark, loaded_lake):
    paths = loaded_lake[0]

    p1 = eval_count_by_arm(spark, paths, study="p1-three-arm")
    p2 = {r["arm"]: r["n"] for r in eval_count_by_arm(spark, paths, study="p2-retrieval").collect()}
    p3 = {r["arm"]: r["n"] for r in eval_count_by_arm(spark, paths, study="p3-agent").collect()}

    assert p1.columns == ["arm", "n"]
    assert {r["arm"]: r["n"] for r in p1.collect()} == {"A": 2, "O": 2, "Z": 2}
    # E1 has one determinism repeat and p3's gpt arm one superseded attempt; neither is scored.
    assert p2 == {"E1": 2, "E2": 2, "E3": 2, "S1": 2, "S2": 2, "S3": 2}
    assert p3 == {"claude": 2, "gpt": 2}
    assert eval_count_by_arm(spark, paths, study="no-such-study").count() == 0


def test_cost_by_project_between_is_a_closed_range(spark, loaded_lake):
    paths = loaded_lake[0]
    fact = _gold(spark, paths, "fact_daily_cost")

    def oracle(start, end):
        inside = fact.where(
            (F.col("cost_date") >= F.lit(start).cast("date")) & (F.col("cost_date") <= F.lit(end).cast("date"))
        )
        return _totals(inside.groupBy("project", "billing_currency").agg(F.sum("cost").alias("t")), "t")

    frame = cost_by_project_between(spark, paths, start="2026-09-28", end="2026-10-02")
    got = _totals(frame)

    assert frame.columns == ["project", "billing_currency", "total"]
    assert got and got == pytest.approx(oracle("2026-09-28", "2026-10-02"))
    # both ends are in: a one-day range returns that day's cost, and one day less is less
    one_day = _totals(cost_by_project_between(spark, paths, start="2026-10-25", end="2026-10-25"))
    assert one_day and one_day == pytest.approx(oracle("2026-10-25", "2026-10-25"))
    shorter = cost_by_project_between(spark, paths, start="2026-09-28", end="2026-10-01")
    assert sum(r["total"] for r in shorter.collect()) < sum(got.values())


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-10-05", "2026-10-01"), ("2026-10", "2026-10-02"), ("2026-10-01", "nope"), (None, "2026-10-02")],
)
def test_cost_by_project_between_refuses_bad_dates(spark, loaded_lake, start, end):
    with pytest.raises(ValueError):
        cost_by_project_between(spark, loaded_lake[0], start=start, end=end)


def test_each_answer_function_says_the_question_it_serves():
    for fn in (spend_for_month, live_check_counts, eval_count_by_arm, cost_by_project_between):
        assert fn.__doc__ and "question" in fn.__doc__.lower(), fn.__name__


# --- compute_expected ------------------------------------------------------------------------


def test_compute_expected_cell_question(spark, loaded_lake):
    q = _question("q01", "lakehouse.answers.cost_per_project", select={"project": "untagged"}, column="total")

    result = compute_expected(spark, loaded_lake[0], [q])

    assert set(result) == {"q01"}
    assert result["q01"]["value"] == pytest.approx(0.51)
    assert isinstance(result["q01"]["value"], float)
    assert result["q01"]["currency"] == "AUD"
    assert result["q01"]["rows"] == 1


def test_compute_expected_cells_from_the_capstone_functions(spark, loaded_lake):
    qs = [
        _question("a", "lakehouse.capstone.answers.spend_for_month", {"month": "2026-10"}, {"project": "p3"}, "total"),
        _question(
            "b", "lakehouse.capstone.answers.live_check_counts", None, {"project": "p7", "verdict": "fail"}, "n",
            kind="count",
        ),
        _question(
            "c", "lakehouse.capstone.answers.eval_count_by_arm", {"study": "p3-agent"}, {"arm": "gpt"}, "n",
            kind="count",
        ),
    ]

    result = compute_expected(spark, loaded_lake[0], qs)

    assert result["a"] == {"value": pytest.approx(3.5), "currency": "AUD", "rows": 1}
    assert result["b"] == {"value": 6, "currency": None, "rows": 1}
    assert result["c"] == {"value": 2, "currency": None, "rows": 1}
    assert isinstance(result["b"]["value"], int)


def test_compute_expected_reaches_the_gateway_module(spark, loaded_lake):
    # the fixture has no gateway calls: the function is called (not refused) and its empty answer matches none
    q = _question("g", "lakehouse.gateway.answers.cost_per_gateway_call", {"session": "s"}, column="calls")

    with pytest.raises(ValueError, match="no row"):
        compute_expected(spark, loaded_lake[0], [q])


def test_compute_expected_set_question(spark, loaded_lake):
    q = _question("q02", "lakehouse.capstone.answers.live_check_counts", select={"verdict": "fail"}, kind="labels")

    result = compute_expected(spark, loaded_lake[0], [q])

    assert result["q02"] == {"value": ["p4", "p6", "p7", "p8"], "currency": None, "rows": 4}


def test_compute_expected_set_is_sorted_deduplicated_strings(spark, loaded_lake):
    # no selector: every row's first column, so `project` repeats and must collapse
    q = _question("q", "lakehouse.capstone.answers.live_check_counts", kind="labels")

    result = compute_expected(spark, loaded_lake[0], [q])["q"]

    assert result["value"] == ["p4", "p5", "p6", "p7", "p8"]
    assert result["rows"] == 14
    assert all(isinstance(v, str) for v in result["value"])


def test_compute_expected_set_currency_when_the_rows_share_one(spark, loaded_lake):
    q = _question("q", "lakehouse.answers.cost_per_project", select={"billing_currency": "AUD"}, kind="labels")

    result = compute_expected(spark, loaded_lake[0], [q])["q"]

    assert result["currency"] == "AUD"
    assert "untagged" in result["value"]
    assert result["rows"] == len(result["value"])


def test_compute_expected_skips_a_question_without_a_source(spark, loaded_lake):
    qs = [
        {"id": "u", "text": "unanswerable", "kind": "unanswerable", "source": None},
        _question("k", "lakehouse.answers.cost_per_project", select={"project": "untagged"}, column="total"),
    ]

    assert set(compute_expected(spark, loaded_lake[0], qs)) == {"k"}


def test_selector_matching_nothing_raises(spark, loaded_lake):
    q = _question("q", "lakehouse.answers.cost_per_project", select={"project": "no-such"}, column="total")

    with pytest.raises(ValueError, match="no row"):
        compute_expected(spark, loaded_lake[0], [q])


def test_set_selector_matching_nothing_raises(spark, loaded_lake):
    q = _question("q", "lakehouse.capstone.answers.live_check_counts", select={"verdict": "maybe"}, kind="labels")

    with pytest.raises(ValueError, match="no row"):
        compute_expected(spark, loaded_lake[0], [q])


@pytest.mark.parametrize("project", ["Untagged", "UNTAGGED", "untagged "])
def test_selector_is_case_sensitive_and_exact(spark, loaded_lake, project):
    q = _question("q", "lakehouse.answers.cost_per_project", select={"project": project}, column="total")

    with pytest.raises(ValueError, match="no row"):
        compute_expected(spark, loaded_lake[0], [q])


def test_selector_matching_two_rows_for_a_cell_raises(spark, loaded_lake):
    q = _question("q", "lakehouse.capstone.answers.live_check_counts", select={"project": "p7"}, column="n")

    with pytest.raises(ValueError, match="more than one"):
        compute_expected(spark, loaded_lake[0], [q])


def test_more_than_the_row_cap_raises(spark, loaded_lake, monkeypatch):
    def thirty(spark, paths):
        return spark.range(MAX_ROWS + 5).withColumn("label", F.concat(F.lit("l"), F.col("id").cast("string")))

    name = _fake_answer(monkeypatch, "thirty", thirty)

    with pytest.raises(ValueError, match="25"):
        compute_expected(spark, loaded_lake[0], [_question("big", name, kind="labels")])

    # the cap is on the selected rows: a selector that narrows it to one row is fine
    one = _question("one", name, select={"id": "7"}, column="id", kind="count")
    assert compute_expected(spark, loaded_lake[0], [one])["one"] == {"value": 7, "currency": None, "rows": 1}


def test_exactly_the_row_cap_is_accepted(spark, loaded_lake, monkeypatch):
    def exactly(spark, paths):
        return spark.range(MAX_ROWS).withColumn("label", F.col("id").cast("string"))

    name = _fake_answer(monkeypatch, "exactly", exactly)

    result = compute_expected(spark, loaded_lake[0], [_question("q", name, kind="labels")])

    assert result["q"]["rows"] == MAX_ROWS
    assert len(result["q"]["value"]) == MAX_ROWS


def test_nan_and_none_become_none_and_numbers_stay_numbers(spark, loaded_lake, monkeypatch):
    def odd(spark, paths):
        return spark.createDataFrame(
            [("nan", float("nan")), ("none", None), ("dec", 2.5)], "k string, v double"
        )

    name = _fake_answer(monkeypatch, "odd", odd)

    def cell(key):
        q = _question(key, name, select={"k": key}, column="v")
        return compute_expected(spark, loaded_lake[0], [q])[key]

    assert cell("nan") == {"value": None, "currency": None, "rows": 1}
    assert cell("none") == {"value": None, "currency": None, "rows": 1}
    assert cell("dec")["value"] == 2.5


def test_a_bad_selector_or_cell_column_raises(spark, loaded_lake):
    bad_filter = _question("q", "lakehouse.answers.cost_per_project", select={"nope": "x"}, column="total")
    bad_cell = _question("q", "lakehouse.answers.cost_per_project", select={"project": "untagged"}, column="nope")

    with pytest.raises(ValueError, match="nope"):
        compute_expected(spark, loaded_lake[0], [bad_filter])
    with pytest.raises(ValueError, match="nope"):
        compute_expected(spark, loaded_lake[0], [bad_cell])


def test_a_non_string_selector_value_raises(spark, loaded_lake):
    q = _question("q", "lakehouse.answers.cost_per_project", select={"total": 0.51}, column="total")

    with pytest.raises(ValueError, match="string"):
        compute_expected(spark, loaded_lake[0], [q])


def test_a_non_numeric_cell_raises(spark, loaded_lake):
    q = _question("q", "lakehouse.answers.cost_per_project", select={"project": "untagged"}, column="billing_currency")

    with pytest.raises(ValueError, match="number"):
        compute_expected(spark, loaded_lake[0], [q])


def test_bad_arguments_name_the_question(spark, loaded_lake):
    q = _question("q77", "lakehouse.capstone.answers.spend_for_month", {"mnth": "2026-10"})

    with pytest.raises(ValueError, match="q77"):
        compute_expected(spark, loaded_lake[0], [q])


def test_duplicate_question_ids_raise(spark, loaded_lake):
    q = _question("same", "lakehouse.answers.cost_per_project", select={"project": "untagged"}, column="total")

    with pytest.raises(ValueError, match="same"):
        compute_expected(spark, loaded_lake[0], [q, dict(q)])


def test_an_unknown_function_raises(spark, loaded_lake):
    q = _question("q", "lakehouse.answers.no_such_answer")

    with pytest.raises(ValueError, match="no_such_answer"):
        compute_expected(spark, loaded_lake[0], [q])


# --- Source and the module guard -------------------------------------------------------------


def test_source_from_dict_defaults_and_refusals():
    source = Source.from_dict({"function": "lakehouse.answers.cost_per_project"})

    assert source == Source("lakehouse.answers.cost_per_project", {}, None, None)
    with pytest.raises(ValueError, match="extra"):
        Source.from_dict({"function": "lakehouse.answers.cost_per_project", "extra": 1})
    with pytest.raises(ValueError, match="function"):
        Source.from_dict({"args": {}})


@pytest.mark.parametrize(
    "name",
    [
        "lakehouse.answers.cost_per_project",
        "lakehouse.gateway.answers.cost_per_gateway_call",
        "lakehouse.capstone.answers.spend_for_month",
    ],
)
def test_the_three_modules_are_importable(name):
    assert callable(resolve_function(name))


@pytest.mark.parametrize(
    "name",
    [
        "os.getcwd",
        "builtins.eval",
        "subprocess.run",
        "lakehouse.checks.check_cost_totals",
        "lakehouse.gold.build.build_gold",
        "lakehouse.answers_evil.cost_per_project",
        "lakehouse.answers.cost_per_project.__call__",
        "lakehouse.capstone.expected.compute_expected",
        "cost_per_project",
        "",
        ".cost_per_project",
    ],
)
def test_other_modules_are_refused_before_any_import(name, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("importlib was reached")

    monkeypatch.setattr(importlib, "import_module", boom)

    with pytest.raises(ValueError):
        resolve_function(name)


@pytest.mark.parametrize(
    "name",
    [
        "lakehouse.answers._gold",
        "lakehouse.answers.__doc__",
        "lakehouse.capstone.answers._private",
        "lakehouse.answers.",
        "lakehouse.answers.F",
        "lakehouse.answers.SparkSession",
        "lakehouse.answers.ALWAYS_PRESENT",
    ],
)
def test_private_and_non_function_names_are_refused(name):
    with pytest.raises(ValueError):
        resolve_function(name)


def test_the_allowlist_is_exactly_the_three_modules():
    assert set(expected.ALLOWED_MODULES) == {
        "lakehouse.answers",
        "lakehouse.gateway.answers",
        "lakehouse.capstone.answers",
    }
