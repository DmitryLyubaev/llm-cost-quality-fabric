"""The model-versus-gold check (spec 4.5, plan 1 Task 6): the DAX queries, the pure comparison and
the check, run offline against a fake `evaluate` that serves gold's own answers.

The UPNs here are made up (`example.test`); the check must never put one in its detail.
"""

import copy
import re
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest
from pyspark.sql import functions as F

from lake_helpers import copy_lake
from lakehouse import answers, semantic
from lakehouse.gateway.answers import cost_per_gateway_call
from lakehouse.gateway.build import FACT_GATEWAY_CALL_SCHEMA, GOLD_TABLE
from lakehouse.gold.build import PROJECTS
from lakehouse.security import USER_PROJECT_SCHEMA
from lakehouse.semantic import (
    KEYS,
    MEASURE_NAMES,
    QUERIES,
    QUERY_COLUMNS,
    UNTAGGED_COST,
    UNTAGGED_QUERY,
    Mismatch,
    compare,
)
from test_semantic_model import _measures, _model_columns, _unresolved

ANSWERS = ["cost_per_project", "quality_across_models", "cost_per_result", "cost_per_gateway_call"]
LEAD_ONE = "lead.one@example.test"
LEAD_TWO = "lead.two@example.test"
ACCESS = [(LEAD_ONE, "p1"), (LEAD_TWO, "p2"), (LEAD_TWO, "p3")]
SESSION = "p10-2026-11-12"


# --- compare ---------------------------------------------------------------------------------


def _row(project="p1", currency="AUD", total=10.0, **more):
    return {"project": project, "billing_currency": currency, "total": total, **more}


KEY = ["project", "billing_currency"]


def test_compare_equal_rows_give_nothing():
    rows = [_row(), _row("p2", total=3.25)]
    assert compare("cost_per_project", rows, copy.deepcopy(rows), KEY) == []


def test_compare_ignores_row_order():
    rows = [_row(), _row("p2", total=3.25)]
    assert compare("cost_per_project", rows, list(reversed(rows)), KEY) == []


def test_compare_floats_within_the_tolerance_are_equal():
    assert compare("cost_per_project", [_row(total=10.0)], [_row(total=10.0049)], KEY) == []
    assert compare("cost_per_project", [_row(total=10.0)], [_row(total=9.995)], KEY) == []


def test_compare_floats_over_the_tolerance_differ():
    assert compare("cost_per_project", [_row(total=10.0)], [_row(total=10.006)], KEY) == [
        Mismatch("cost_per_project", ("p1", "AUD"), "total", 10.0, 10.006)
    ]


def test_compare_counts_must_be_exact():
    keys = ["study"]
    assert compare("q", [{"study": "s", "n": 3}], [{"study": "s", "n": 3.0}], keys) == []
    assert compare("q", [{"study": "s", "n": 3}], [{"study": "s", "n": 4}], keys) == [
        Mismatch("q", ("s",), "n", 3, 4)
    ]
    assert compare("q", [{"study": "s", "n": 3}], [{"study": "s", "n": 3.001}], keys) == [
        Mismatch("q", ("s",), "n", 3, 3.001)
    ]


def test_compare_gold_zero_against_dax_blank_is_equal():
    """Review Focus 4: gold's `_project_totals` gives 0 where the measure gives blank."""
    expected = [_row("untagged", total=0.0), _row("releaselens-shared", total=0, n=0)]
    blank = [_row("untagged", total=None), _row("releaselens-shared", total=float("nan"))]
    assert compare("cost_per_project", expected, blank, KEY) == []
    # a blank column the query did not return, and a row SUMMARIZECOLUMNS left out
    assert compare("cost_per_project", expected, [{"project": "untagged", "billing_currency": "AUD"}], KEY) == []
    assert compare("cost_per_project", expected, [], KEY) == []


def test_compare_null_against_null_is_equal():
    assert compare("cost_per_project", [_row(total=None)], [_row(total=None)], KEY) == []
    assert compare("cost_per_project", [_row(total=None)], [_row(total=float("nan"))], KEY) == []


def test_compare_gold_null_against_dax_zero_differs():
    assert compare("cost_per_project", [_row(total=None)], [_row(total=0.0)], KEY) == [
        Mismatch("cost_per_project", ("p1", "AUD"), "total", None, 0.0)
    ]


def test_compare_an_extra_row_differs():
    expected = [_row()]
    actual = [_row(), _row("p9", total=4.0)]
    assert compare("cost_per_project", expected, actual, KEY) == [
        Mismatch("cost_per_project", ("p9", "AUD"), "total", None, 4.0)
    ]


def test_compare_a_missing_row_differs():
    expected = [_row(), _row("p2", currency="USD", total=7.5)]
    assert compare("cost_per_project", expected, [_row()], KEY) == [
        Mismatch("cost_per_project", ("p2", "USD"), "total", 7.5, None)
    ]


def test_compare_text_must_match_exactly():
    assert compare("r", [{"project": "p1", "fx_month": "2026-10"}], [{"project": "p1", "fx_month": "2026-09"}],
                   ["project"]) == [Mismatch("r", ("p1",), "fx_month", "2026-10", "2026-09")]


def test_compare_takes_a_decimal_as_a_float():
    assert compare("cost_per_project", [_row(total=10.0)], [_row(total=Decimal("10.004"))], KEY) == []
    assert compare("cost_per_project", [_row(total=Decimal("10.0"))], [_row(total=10.004)], KEY) == []
    assert len(compare("cost_per_project", [_row(total=10.0)], [_row(total=Decimal("10.01"))], KEY)) == 1
    assert compare("cost_per_project", [_row(total=Decimal("0"))], [_row(total=None)], KEY) == []


def test_compare_takes_any_real_number():
    assert compare("cost_per_project", [_row(total=1 / 3)], [_row(total=Fraction(1, 3))], KEY) == []
    assert len(compare("cost_per_project", [_row(total=0.5)], [_row(total=Fraction(1, 3))], KEY)) == 1


def test_compare_takes_gold_nan_as_null():
    assert compare("cost_per_project", [_row(total=float("nan"))], [_row(total=None)], KEY) == []
    assert len(compare("cost_per_project", [_row(total=float("nan"))], [_row(total=0.0)], KEY)) == 1


def test_compare_takes_numpy_scalars():
    numpy = pytest.importorskip("numpy")
    keys = ["study"]
    assert compare("q", [{"study": "s", "n": 3}], [{"study": "s", "n": numpy.int64(3)}], keys) == []
    assert len(compare("q", [{"study": "s", "n": 3}], [{"study": "s", "n": numpy.int64(4)}], keys)) == 1
    assert compare("q", [{"study": "s", "x": numpy.float64("nan")}], [{"study": "s", "x": None}], keys) == []
    assert compare("q", [{"study": "s", "x": 0.25}], [{"study": "s", "x": numpy.float64(0.252)}], keys) == []


def test_compare_a_duplicate_key_differs():
    expected = [_row()]
    actual = [_row(), _row()]
    mismatches = compare("cost_per_project", expected, actual, KEY)
    assert mismatches == [Mismatch("cost_per_project", ("p1", "AUD"), semantic.DUPLICATE_KEY, 1, 2)]


def test_compare_matches_a_blank_key_to_a_null_key():
    expected = [{"call_date": date(2026, 11, 12), "billing_currency": None, "calls": 2}]
    actual = [{"call_date": date(2026, 11, 12), "billing_currency": float("nan"), "calls": 2}]
    assert compare("g", expected, actual, ["call_date", "billing_currency"]) == []


# --- the queries -----------------------------------------------------------------------------


def _answer_columns(spark, paths, name):
    if name == "cost_per_gateway_call":
        return cost_per_gateway_call(spark, paths, SESSION).columns
    return getattr(answers, name)(spark, paths).columns


# The documented differences between a query's aliases and its answer's columns:
# (answer columns the query does not return, query columns the answer does not have).
DIFFERENCES = {
    "cost_per_project": (set(), set()),
    # the quality grain: Python coalesces the two versions, as gold does (no calculated columns)
    "quality_across_models": ({"model_version"}, {"dim_model_version", "recorded_model_version"}),
    "cost_per_result": (set(), set()),
    # ruling R5: `Cost per 1,000 calls` is compared with 1000 x cost_per_call; one row per session
    "cost_per_gateway_call": ({"cost_per_call"}, {"cost_per_1000_calls", "session"}),
}


def test_there_is_one_query_per_gold_answer():
    assert list(QUERIES) == ANSWERS
    assert set(QUERY_COLUMNS) == set(KEYS) == set(ANSWERS)


@pytest.mark.parametrize("name", ANSWERS)
def test_query_aliases_equal_the_answer_columns(spark, loaded_lake, name):
    paths, _, _ = loaded_lake
    returned = set(_answer_columns(spark, paths, name))
    removed, added = DIFFERENCES[name]
    assert removed <= returned
    assert set(QUERY_COLUMNS[name]) == (returned - removed) | added


@pytest.mark.parametrize("name", ANSWERS)
def test_compared_columns_are_the_same_on_both_sides(spark, loaded_lake, name):
    """The gold rows the check builds and the shaped DAX rows have the same columns, keys included."""
    paths, _, _ = loaded_lake
    gold = semantic.gold_rows(spark, paths, name, [SESSION])
    shaped = semantic.shape_rows(name, [{column: None for column in QUERY_COLUMNS[name]}])
    gold_columns = set(gold[0]) if gold else set(semantic.compared_columns(name))
    assert set(shaped[0]) == gold_columns == set(semantic.compared_columns(name))
    assert set(KEYS[name]) <= gold_columns


@pytest.mark.parametrize("name", ANSWERS)
def test_every_alias_is_in_its_query(name):
    query = QUERIES[name]
    assert query.lstrip().startswith(("EVALUATE", "DEFINE"))
    assert "SUMMARIZECOLUMNS(" in query
    for alias in QUERY_COLUMNS[name]:
        assert f'"{alias}", ' in query, alias


_DEFINED = re.compile(r"MEASURE\s+([A-Za-z_]\w*)\[([^\]]+)\]")


@pytest.mark.parametrize("name", ANSWERS)
def test_every_dax_reference_in_the_queries_resolves(name):
    """Each `Table[column]` is a model column (or a measure the query defines on that table), and
    each `[x]` a model measure, a query measure or a column the query itself added."""
    query = QUERIES[name]
    columns = {table: set(names) for table, names in _model_columns().items()}
    defined = set()
    for table, measure in _DEFINED.findall(query):
        columns[table].add(measure)
        defined.add(measure)
    added = set(re.findall(r'"([A-Za-z_]\w*)", ', query))
    assert _unresolved(query, columns, set(_measures()) | defined | added) == []


def test_untagged_query_reads_the_untagged_measure_only():
    assert UNTAGGED_QUERY.lstrip().startswith("EVALUATE")
    assert _unresolved(UNTAGGED_QUERY, _model_columns(), set(_measures()) | {"total"}) == []
    assert '"total", [Untagged cost]' in UNTAGGED_QUERY
    assert UNTAGGED_COST not in QUERIES  # owner only, and not one of the four answers


def test_query_measures_never_reuse_a_model_name():
    names = {n.lower() for n in _measures()}
    names |= {column.lower() for table in _model_columns().values() for column in table}
    for name in ANSWERS:
        for _, measure in _DEFINED.findall(QUERIES[name]):
            assert measure.lower() not in names, measure


def test_named_measures_exist_in_the_model():
    measures = _measures()
    for name, mapping in MEASURE_NAMES.items():
        if name == UNTAGGED_COST:
            assert mapping == {"total": "Untagged cost"}
            query = UNTAGGED_QUERY
        else:
            assert set(mapping) <= set(QUERY_COLUMNS[name]) | set(semantic.compared_columns(name)), name
            query = QUERIES[name]
        for measure in mapping.values():
            assert measure in measures, measure
            assert f"[{measure}]" in query, measure


def test_cost_query_sums_aud_only_through_the_measure():
    """R6: `Cost (AUD)` is the figure compared; gold rows in another currency fail the check."""
    assert "[Cost (AUD)]" in QUERIES["cost_per_project"]
    assert MEASURE_NAMES["cost_per_project"] == {"total": "Cost (AUD)"}


def test_gateway_query_takes_no_session_text():
    """The sessions are kept in Python, so no operator text is ever spliced into DAX."""
    assert SESSION not in QUERIES["cost_per_gateway_call"]
    assert "{" not in QUERIES["cost_per_gateway_call"]


# --- the gateway rows ------------------------------------------------------------------------


def _write_gateway_calls(spark, paths):
    calls = [
        ("2026-11-12T01:00:00Z", "req-01", SESSION, 100, False),
        ("2026-11-12T02:00:00Z", "req-02", SESSION, None, True),
        ("2026-11-12T03:00:00Z", "req-03", "p10-other", 50, False),
        ("2026-11-13T01:00:00Z", "req-04", SESSION, 300, False),
    ]
    frame = spark.createDataFrame(
        [(r, s, t, refused) for _, r, s, t, refused in calls],
        "request_id string, session string, tokens int, refused_by_gateway boolean",
    ).join(spark.createDataFrame([(r, e) for e, r, *_ in calls], "request_id string, event_time string"), "request_id")
    frame = frame.withColumn("event_time", F.to_timestamp("event_time")).withColumn(
        "call_date", F.to_date("event_time")
    )
    columns = [F.col(f.name).cast(f.dataType) if f.name in frame.columns else F.lit(None).cast(f.dataType).alias(f.name)
               for f in FACT_GATEWAY_CALL_SCHEMA]
    frame.select(*columns).write.format("delta").mode("overwrite").save(paths.table("gold", GOLD_TABLE))


def _gateway_lake(spark, paths):
    _write_gateway_calls(spark, paths)
    spark.createDataFrame(
        [(date(2026, 11, 12), "rg-releaselens-gateway", 4.0, "AUD", "p4")],
        "cost_date date, resource_group string, cost double, billing_currency string, project string",
    ).write.format("delta").mode("overwrite").save(paths.table("gold", "fact_daily_cost"))


def test_gateway_gold_rows_carry_the_session_refused_and_cost_per_1000_calls(spark, tmp_lake):
    _gateway_lake(spark, tmp_lake)

    rows = semantic.gold_rows(spark, tmp_lake, "cost_per_gateway_call", [SESSION])

    by_day = {r["call_date"]: r for r in rows}
    assert set(by_day) == {date(2026, 11, 12), date(2026, 11, 13)}
    costed = by_day[date(2026, 11, 12)]
    assert costed["session"] == SESSION
    assert (costed["calls"], costed["refused"], costed["tokens_total"]) == (2, 1, 100)
    assert costed["cost_per_1000_calls"] == pytest.approx(2000.0)  # 4.0 over 2 calls, per 1,000
    assert "cost_per_call" not in costed
    uncosted = by_day[date(2026, 11, 13)]
    assert uncosted["billing_currency"] is None and uncosted["cost_per_1000_calls"] is None


def test_gateway_rows_of_other_sessions_are_not_compared(spark, tmp_lake):
    _gateway_lake(spark, tmp_lake)
    gold = semantic.gold_rows(spark, tmp_lake, "cost_per_gateway_call", [SESSION])
    other = {**gold[0], "session": "p10-other", "calls": 99}

    actual = semantic.shape_rows("cost_per_gateway_call", copy.deepcopy(gold) + [other], [SESSION])

    assert compare("cost_per_gateway_call", gold, actual, KEYS["cost_per_gateway_call"]) == []


# --- the check, against a fake model that serves gold's own answers ---------------------------


@pytest.fixture(scope="module")
def checked_lake(spark, loaded_lake, tmp_path_factory):
    """A copy of the loaded lake whose access table holds two made-up leads."""
    paths, _, _ = loaded_lake
    lake = copy_lake(paths, tmp_path_factory.mktemp("checked"))
    spark.createDataFrame(ACCESS, USER_PROJECT_SCHEMA).write.format("delta").mode("overwrite").save(
        lake.table("security", "user_project")
    )
    return lake


def _figures_blank(row, keys):
    """True when every figure is 0 or None: SUMMARIZECOLUMNS leaves such a row out."""
    return all(value is None or (isinstance(value, (int, float)) and value == 0)
               for column, value in row.items() if column not in keys)


class FakeModel:
    """`evaluate(dax, user)` over gold's answers, in the shape the DAX queries return them.

    As DAX would, it leaves out rows whose figures are all 0 or None, and serves one quality row
    with the deployment's version blank and the study's recorded version set (`recorded_only`), so
    the coalescing runs end to end. As a user it keeps only that user's projects, as the
    `ProjectLead` role does, unless the user is in `leaks`. `tamper(name, rows, user)` may change
    the rows before they are served.
    """

    def __init__(self, spark, paths, sessions=(), access=ACCESS):
        self.study_projects = {
            r["study"]: r["project"]
            for r in spark.read.format("delta").load(paths.table("gold", "dim_study")).collect()
        }
        self.access = {}
        for upn, project in access:
            self.access.setdefault(upn, set()).add(project)
        self.rows = {}
        self.recorded_only = None
        for name in ANSWERS:
            rows = semantic.gold_rows(spark, paths, name, list(sessions))
            if name == "cost_per_project":  # `Cost (AUD)` sums AUD lines only
                rows = [r for r in rows if r["billing_currency"] == "AUD"]
                self.untagged = sum(r["total"] for r in rows if r["project"] == "untagged") or None
            rows = [r for r in rows if not _figures_blank(r, KEYS[name])]
            if name == "quality_across_models":
                shaped = []
                for r in rows:
                    version = r.pop("model_version")
                    if self.recorded_only is None and version is not None:
                        self.recorded_only = (r["study"], r["arm"], version)
                        shaped.append({**r, "dim_model_version": None, "recorded_model_version": version})
                    else:
                        shaped.append({**r, "dim_model_version": version, "recorded_model_version": None})
                rows = shaped
            self.rows[name] = rows
        self.rows[UNTAGGED_COST] = [{"total": self.untagged}]
        self.calls = []
        self.leaks = set()
        self.tamper = None

    def project_of(self, name, row):
        return self.study_projects.get(row["study"]) if name == "quality_across_models" else row.get("project")

    def __call__(self, dax, user):
        queries = {**QUERIES, UNTAGGED_COST: UNTAGGED_QUERY}
        name = next(n for n, q in queries.items() if q == dax)
        self.calls.append((name, user))
        rows = copy.deepcopy(self.rows[name])
        if user is not None and user not in self.leaks:
            rows = [r for r in rows if self.project_of(name, r) in self.access[user]]
        if self.tamper:
            rows = self.tamper(name, rows, user)
        return rows


@pytest.fixture(scope="module")
def fake_model(spark, checked_lake):
    return FakeModel(spark, checked_lake)


@pytest.fixture
def model(fake_model):
    fake = copy.copy(fake_model)
    fake.calls, fake.leaks, fake.tamper = [], set(), None
    return fake


@pytest.fixture(scope="module")
def gateway_checked_lake(spark, checked_lake, tmp_path_factory):
    """The checked lake with gateway calls for SESSION (and another session) in gold."""
    lake = copy_lake(checked_lake, tmp_path_factory.mktemp("gateway_checked"))
    _write_gateway_calls(spark, lake)
    return lake


def _lake_with_access(spark, base, target, access):
    lake = copy_lake(base, target)
    spark.createDataFrame(access, USER_PROJECT_SCHEMA).write.format("delta").mode("overwrite").save(
        lake.table("security", "user_project")
    )
    return lake


def _projects_without_data(model):
    """Projects with no row in any answer the leads see (and not the two always-present ones)."""
    seen = {model.project_of(name, r) for name in ("cost_per_project", "quality_across_models")
            for r in model.rows[name]}
    seen |= {r["project"] for r in model.rows["cost_per_result"]}
    empty = [p for p, _ in PROJECTS if p not in seen and p not in ("untagged", "releaselens-shared")]
    assert len(empty) >= 2, empty
    return empty


def _no_upn(detail):
    assert "@" not in detail
    assert "lead.one" not in detail and "lead.two" not in detail and "example.test" not in detail


def test_check_passes_when_the_model_serves_gold(spark, checked_lake, model):
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert result.passed, result.detail
    assert result.name == "model_matches_gold"
    _no_upn(result.detail)
    assert "the lead of p1: " in result.detail and "the lead of p2, p3: " in result.detail
    assert "nothing compared" not in result.detail
    assert "cost_per_gateway_call skipped" in result.detail
    assert model.recorded_only is not None  # a quality row matched only through the recorded version
    owner = [name for name, user in model.calls if user is None]
    assert owner == [UNTAGGED_COST, "cost_per_project", "quality_across_models", "cost_per_result"]
    # as each lead, only the two answers whose figures do not depend on lines the lead cannot see
    by_user = {}
    for name, user in model.calls:
        if user is not None:
            by_user.setdefault(user, []).append(name)
    assert by_user == {upn: ["cost_per_project", "quality_across_models"] for upn in (LEAD_ONE, LEAD_TWO)}


def test_check_with_sessions_compares_the_gateway_answer(spark, gateway_checked_lake):
    model = FakeModel(spark, gateway_checked_lake, sessions=[SESSION])
    assert model.rows["cost_per_gateway_call"]  # there is something to compare

    result = semantic.check_model_matches_gold(spark, gateway_checked_lake, model, [SESSION, SESSION])

    assert result.passed, result.detail
    assert "skipped" not in result.detail
    assert ("cost_per_gateway_call", None) in model.calls


def test_check_fails_on_a_tampered_gateway_figure(spark, gateway_checked_lake):
    model = FakeModel(spark, gateway_checked_lake, sessions=[SESSION])

    def tamper(name, rows, user):
        if name == "cost_per_gateway_call":
            rows[0]["refused"] = (rows[0]["refused"] or 0) + 1
        return rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, gateway_checked_lake, model, [SESSION])

    assert not result.passed
    assert "measure 'Refused by gateway' (refused)" in result.detail


def test_check_fails_on_a_session_with_no_gold_calls(spark, gateway_checked_lake):
    """Minor 1: a mistyped session must not pass with nothing compared."""
    model = FakeModel(spark, gateway_checked_lake, sessions=[SESSION])

    result = semantic.check_model_matches_gold(spark, gateway_checked_lake, model, [SESSION, "p10-2026-11-21"])

    assert not result.passed
    assert "session 'p10-2026-11-21' has no calls in gold fact_gateway_call" in result.detail
    assert SESSION + "'" not in result.detail  # the real session is fine


def test_check_fails_on_a_wrong_untagged_cost(spark, checked_lake, model):
    """Minor 2: `Untagged cost` is evaluated as the owner against gold's untagged AUD total."""
    def tamper(name, rows, user):
        if name == UNTAGGED_COST:
            rows[0]["total"] = (rows[0]["total"] or 0) + 2.0
        return rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    assert "untagged_cost measure 'Untagged cost' (total) at the whole model" in result.detail


def test_check_fails_when_a_lead_sees_nothing_of_their_projects(spark, checked_lake, model):
    """I-1: a lead whose queries return no rows while the owner has rows in their projects fails."""
    def tamper(name, rows, user):
        return [] if user == LEAD_ONE else rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    assert "the lead of p1: cost_per_project measure 'Cost (AUD)' (total)" in result.detail
    assert ", lead None" in result.detail
    _no_upn(result.detail)


def test_check_fails_when_no_lead_compared_a_row(spark, checked_lake, fake_model, tmp_path):
    """I-1: every lead's projects hold no data, so nothing was compared as a lead: the check fails."""
    first, second = _projects_without_data(fake_model)[:2]
    access = [(LEAD_ONE, first), (LEAD_TWO, second)]
    lake = _lake_with_access(spark, checked_lake, tmp_path / "empty_leads", access)
    model = FakeModel(spark, lake, access=access)

    result = semantic.check_model_matches_gold(spark, lake, model, [])

    assert not result.passed
    assert "no lead compared a row" in result.detail
    assert f"the lead of {first}" in result.detail and f"the lead of {second}" in result.detail
    _no_upn(result.detail)


def test_check_notes_a_lead_with_nothing_compared_and_passes(spark, checked_lake, fake_model, tmp_path):
    """I-1: one lead compares rows and another has none; it passes, and the note names the second."""
    empty = _projects_without_data(fake_model)[0]
    access = [(LEAD_ONE, "p1"), (LEAD_TWO, empty)]
    lake = _lake_with_access(spark, checked_lake, tmp_path / "one_empty_lead", access)
    model = FakeModel(spark, lake, access=access)

    result = semantic.check_model_matches_gold(spark, lake, model, [])

    assert result.passed, result.detail
    assert f"nothing compared for the lead of {empty} (no rows in their projects)" in result.detail
    assert "the lead of p1: " in result.detail and "the lead of p1: 0 row(s)" not in result.detail
    _no_upn(result.detail)


def test_check_fails_on_a_tampered_figure_naming_the_measure_and_key(spark, checked_lake, model):
    target = next(r for r in model.rows["cost_per_project"] if r["total"])

    def tamper(name, rows, user):
        if name == "cost_per_project" and user is None:
            for r in rows:
                if r["project"] == target["project"]:
                    r["total"] += 1.0
        return rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    where = f"project={target['project']}, billing_currency=AUD"
    assert f"cost_per_project measure 'Cost (AUD)' (total) at {where}: gold {target['total']!r}, model " in result.detail
    assert f"{target['total'] + 1.0!r}" in result.detail
    _no_upn(result.detail)


def test_check_fails_on_a_tampered_quality_mean(spark, checked_lake, model):
    def tamper(name, rows, user):
        if name == "quality_across_models" and user is None and rows:
            rows[0]["n"] += 1
        return rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    assert "Results (n)" in result.detail


def test_check_fails_when_a_lead_sees_a_second_project_without_naming_them(spark, checked_lake, model):
    model.leaks = {LEAD_ONE}

    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    assert "the lead of p1" in result.detail
    assert "outside their projects" in result.detail
    _no_upn(result.detail)


def test_check_fails_when_a_lead_sees_other_figures_for_their_project(spark, checked_lake, model):
    def tamper(name, rows, user):
        if name == "cost_per_project" and user == LEAD_TWO:
            for r in rows:
                r["total"] = (r["total"] or 0) + 5.0
        return rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    assert "the lead of p2, p3: cost_per_project measure 'Cost (AUD)' (total)" in result.detail
    assert "owner " in result.detail and "lead " in result.detail
    _no_upn(result.detail)


def test_a_failing_lead_query_is_reported_without_its_text(spark, checked_lake, model):
    def tamper(name, rows, user):
        if user == LEAD_ONE:
            raise RuntimeError(f"cannot impersonate {LEAD_ONE}")
        return rows

    model.tamper = tamper
    result = semantic.check_model_matches_gold(spark, checked_lake, model, [])

    assert not result.passed
    assert "the lead of p1" in result.detail and "RuntimeError" in result.detail
    _no_upn(result.detail)


def test_check_fails_with_no_leads_in_the_access_table(spark, loaded_lake, fake_model):
    paths, _, _ = loaded_lake  # its user_project is the empty always-present table

    result = semantic.check_model_matches_gold(spark, paths, copy.copy(fake_model), [])

    assert not result.passed
    assert "security.user_project" in result.detail


def test_check_fails_on_a_gold_cost_in_another_currency(spark, checked_lake, tmp_path):
    """R6: `Cost (AUD)` sums AUD only, so a gold row in another currency fails, naming the row."""
    lake = copy_lake(checked_lake, tmp_path / "usd")
    facts = lake.table("gold", "fact_daily_cost")
    one = spark.read.format("delta").load(facts).where(F.col("project") == "p1").limit(1)
    one.withColumn("billing_currency", F.lit("USD")).write.format("delta").mode("append").save(facts)
    fake = FakeModel(spark, lake)

    result = semantic.check_model_matches_gold(spark, lake, fake, [])

    assert not result.passed
    assert "project=p1, billing_currency=USD" in result.detail
    assert "USD" in result.detail and "Cost (AUD)" in result.detail
