"""The value-based leak check: no example's result shows a frozen question's answer (p12 plan 2, step 2).

tests/test_capstone_agent.py checks leaks structurally (an example on the same answer function as a
frozen question). This check compares values, so it also catches an example on another function, or
with other arguments, whose result happens to hold a frozen answer on the real snapshot.
"""

import json
from pathlib import Path

import pytest

from lakehouse.capstone.expected import compute_expected
from lakehouse.capstone.leaks import figure_matches, value_leaks

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = json.loads((ROOT / "capstone" / "questions.json").read_text(encoding="utf-8"))
FEWSHOT_SOURCES = json.loads((ROOT / "capstone" / "fewshots-sources.json").read_text(encoding="utf-8"))
# q12 asks about project 10's live session, which the fixture lake has no calls for.
NOT_ON_THE_FIXTURE = {"q12"}


def _question(qid: str) -> dict:
    return next(q for q in QUESTIONS if q["id"] == qid)


def _example(example_id: str, function: str, columns: list[str], args=None, select=None) -> dict:
    source = {"function": function, "args": args or {}}
    if select:
        source["select"] = select
    return {"id": example_id, "columns": columns, "source": source}


# --- the tolerance (pure) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, value, want, matches",
    [
        ("money", 100.9, 100.0, True),  # within 1%
        ("money", 101.5, 100.0, False),
        ("money", 0.505, 0.5, True),  # within 0.01, wider than 1% of 0.5
        ("money", 0.52, 0.5, False),
        ("rate", 0.984, 0.983, True),
        ("rate", 0.99, 0.983, False),
        ("count", 7, 7, True),
        ("count", 7.0, 7, True),
        ("count", 8, 7, False),
        ("labels", 1, 1, False),  # only single-cell kinds are compared
        ("money", None, 1.0, False),
        ("money", "1.0", 1.0, False),  # a label column is never a figure
        ("count", True, 1, False),
    ],
)
def test_figure_matches_uses_the_scorers_tolerance(kind, value, want, matches):
    assert figure_matches(kind, value, want) is matches


# --- on the fixture lake (Spark) -----------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_questions(spark, loaded_lake) -> list[dict]:
    """The frozen questions the fixture lake can answer: all but the ones it has no rows for."""
    kept = []
    for question in QUESTIONS:
        try:
            compute_expected(spark, loaded_lake[0], [question])
        except ValueError:
            assert question["id"] in NOT_ON_THE_FIXTURE, question["id"]
            continue
        kept.append(question)
    assert {q["id"] for q in QUESTIONS} - {q["id"] for q in kept} == NOT_ON_THE_FIXTURE
    return kept


def test_an_example_built_to_equal_q02_is_caught(spark, loaded_lake):
    paths = loaded_lake[0]
    q02 = _question("q02")
    columns = ["project", "billing_currency", "total"]
    examples = [
        # q02's own call: the same function and selector
        _example("same", "lakehouse.answers.cost_per_project", columns, select={"project": "untagged"}),
        # another function whose result holds the same figure: every day in range
        _example(
            "range",
            "lakehouse.capstone.answers.cost_by_project_between",
            columns,
            args={"start": "2000-01-01", "end": "2099-12-31"},
        ),
        # the same function for another project shows another figure
        _example("other", "lakehouse.answers.cost_per_project", columns, select={"project": "p4"}),
        # q02's figure without its selector columns cannot be read as q02's answer
        _example("no-keys", "lakehouse.answers.cost_per_project", ["total"], select={"project": "untagged"}),
    ]

    leaks = value_leaks(spark, paths, [q02], examples)

    assert leaks == [{"example_id": "range", "question_id": "q02"}, {"example_id": "same", "question_id": "q02"}]


def test_a_count_leaks_only_when_exactly_equal(spark, loaded_lake):
    paths = loaded_lake[0]
    q07 = _question("q07")  # eval_count_by_arm, arm claude, column n
    want = compute_expected(spark, paths, [q07])["q07"]["value"]
    study = q07["source"]["args"]["study"]
    shown = _example("shown", "lakehouse.capstone.answers.eval_count_by_arm", ["arm", "n"], args={"study": study})
    other = _example(
        "other-arm", "lakehouse.capstone.answers.eval_count_by_arm", ["arm", "n"], args={"study": study},
        select={"arm": "gpt"},
    )

    leaks = value_leaks(spark, paths, [q07], [shown, other])

    assert isinstance(want, int) and want > 0
    assert leaks == [{"example_id": "shown", "question_id": "q07"}]


def test_the_shipped_examples_report_none_on_the_fixture(spark, loaded_lake, fixture_questions):
    assert value_leaks(spark, loaded_lake[0], fixture_questions, FEWSHOT_SOURCES) == []


def test_findings_are_ids_only_and_reuse_given_expected_answers(spark, loaded_lake, fixture_questions):
    paths = loaded_lake[0]
    q02 = _question("q02")
    example = _example(
        "same", "lakehouse.answers.cost_per_project", ["project", "billing_currency", "total"],
        select={"project": "untagged"},
    )
    expected = compute_expected(spark, paths, [q02])

    leaks = value_leaks(spark, paths, [q02], [example], expected=expected)

    assert leaks == [{"example_id": "same", "question_id": "q02"}]
    # a stale expected value is what is compared: nothing is recomputed
    stale = {"q02": {**expected["q02"], "value": expected["q02"]["value"] + 1000}}
    assert value_leaks(spark, paths, [q02], [example], expected=stale) == []


def test_an_example_column_missing_from_its_function_is_refused(spark, loaded_lake):
    q02 = _question("q02")
    example = _example("bad", "lakehouse.answers.cost_per_project", ["project", "billing_currency", "nope"])
    with pytest.raises(ValueError, match="bad"):
        value_leaks(spark, loaded_lake[0], [q02], [example])
