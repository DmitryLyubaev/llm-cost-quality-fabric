"""The frozen 15 and the practice 10: their shape, the spread, and that each one resolves and scores."""

import importlib
import json
import re
from pathlib import Path

import pytest

from lakehouse.capstone.expected import ALLOWED_MODULES, MAX_ROWS, Source, compute_expected, resolve_function
from lakehouse.capstone.score import KINDS, score_question

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "capstone" / "questions.json"
PRACTICE = ROOT / "capstone" / "practice.json"
FIELDS = {"id", "text", "kind", "source"}
OPTIONAL_FIELDS = {"optional"}
GATEWAY = "lakehouse.gateway.answers.cost_per_gateway_call"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def _source_key(question):
    return json.dumps(question["source"], sort_keys=True)


@pytest.fixture(scope="module")
def frozen():
    return _load(QUESTIONS)


@pytest.fixture(scope="module")
def practice():
    return _load(PRACTICE)


# --- shape -----------------------------------------------------------------------------------


def _check_shape(questions):
    for q in questions:
        assert isinstance(q, dict)
        assert FIELDS <= set(q) <= FIELDS | OPTIONAL_FIELDS, q
        assert isinstance(q["id"], str) and q["id"]
        assert isinstance(q["text"], str) and q["text"].strip() == q["text"] and q["text"].endswith("?"), q["id"]
        assert q["kind"] in KINDS, q["id"]
        if "optional" in q:
            assert q["optional"] is True, f"{q['id']}: leave `optional` out rather than set it false"
        if q["kind"] == "unanswerable":
            assert q["source"] is None, q["id"]
            continue
        source = Source.from_dict(q["source"])
        assert set(q["source"]) == {"function", "args", "select", "column"}, q["id"]
        if q["kind"] == "labels":
            assert source.column is None, f"{q['id']}: a labels question takes the set of the first column"
        else:
            assert source.column is not None, f"{q['id']}: a {q['kind']} question needs one cell"


def test_the_files_are_lf_utf8_json_lists():
    for path in (QUESTIONS, PRACTICE):
        raw = path.read_bytes()
        assert b"\r" not in raw, path
        assert raw.endswith(b"\n"), path
        assert isinstance(json.loads(raw.decode("utf-8")), list)


def test_frozen_set_has_15_questions_with_unique_ids(frozen):
    ids = [q["id"] for q in frozen]

    assert len(frozen) == 15
    assert len(set(ids)) == 15
    assert ids == [f"q{n:02d}" for n in range(1, 16)]
    _check_shape(frozen)


def test_frozen_set_has_exactly_one_unanswerable_without_a_source(frozen):
    unanswerable = [q for q in frozen if q["kind"] == "unanswerable"]

    assert len(unanswerable) == 1
    assert unanswerable[0]["source"] is None
    assert "version" in unanswerable[0]["text"].lower()
    assert all(q["source"] is not None for q in frozen if q["kind"] != "unanswerable")


def test_at_most_one_question_is_optional_and_it_is_the_gateway_one(frozen, practice):
    optional = [q for q in frozen if q.get("optional")]

    assert len(optional) <= 1
    assert [q["source"]["function"] for q in optional] == [GATEWAY]
    assert not any(q.get("optional") for q in practice)


def test_frozen_set_covers_the_spread(frozen):
    functions = {q["source"]["function"] for q in frozen if q["source"]}
    kinds = [q["kind"] for q in frozen]
    selects = [q["source"]["select"] or {} for q in frozen if q["source"]]

    # spend: by project, untagged, one month; quality; cost per result; live checks; the gateway;
    # a date filter (and the join, through quality_across_models' deployment dimension)
    assert functions == {
        "lakehouse.answers.cost_per_project",
        "lakehouse.answers.quality_across_models",
        "lakehouse.answers.cost_per_result",
        GATEWAY,
        "lakehouse.capstone.answers.spend_for_month",
        "lakehouse.capstone.answers.live_check_counts",
        "lakehouse.capstone.answers.eval_count_by_arm",
        "lakehouse.capstone.answers.cost_by_project_between",
    }
    assert {"project": "untagged", "billing_currency": "AUD"} in selects
    assert {"study": "p2-retrieval", "arm": "E3"} in selects
    assert {k: kinds.count(k) for k in KINDS} == {"money": 5, "count": 5, "rate": 2, "labels": 2, "unanswerable": 1}


def test_practice_set_has_10_questions_that_do_not_overlap_the_frozen_set(frozen, practice):
    ids = [q["id"] for q in practice]

    assert len(practice) == 10
    assert len(set(ids)) == 10
    _check_shape(practice)
    assert not set(ids) & {q["id"] for q in frozen}
    assert not {_norm(q["text"]) for q in practice} & {_norm(q["text"]) for q in frozen}
    sources = {_source_key(q) for q in frozen if q["source"]}
    assert not [q["id"] for q in practice if q["source"] and _source_key(q) in sources]


def test_no_practice_question_reveals_part_of_a_frozen_label_set(frozen, practice):
    # a practice question with the same function, the same arguments and the frozen labels
    # question's filter (or more) answers for one member of that set
    for f in frozen:
        if f["kind"] != "labels":
            continue
        fsrc = f["source"]
        for p in practice:
            psrc = p["source"]
            if psrc is None or psrc["function"] != fsrc["function"] or psrc["args"] != fsrc["args"]:
                continue
            assert not (fsrc["select"] or {}).items() <= (psrc["select"] or {}).items(), (f["id"], p["id"])


# The live snapshot is taken after 8 November 2026, so every month asked about must be closed by then.
LAST_CLOSED_MONTH = "2026-10"


def test_every_month_and_date_asked_about_is_closed_at_the_snapshot(frozen, practice):
    for q in frozen + practice:
        args = (q["source"] or {}).get("args", {})
        for key in ("month", "start", "end"):
            if key in args:
                assert args[key][:7] <= LAST_CLOSED_MONTH, (q["id"], key, args[key])


def test_texts_are_unique_within_each_set(frozen, practice):
    for questions in (frozen, practice):
        texts = [_norm(q["text"]) for q in questions]
        assert len(set(texts)) == len(texts)


def test_every_source_names_an_existing_function(frozen, practice):
    for q in frozen + practice:
        if q["source"] is None:
            continue
        name = q["source"]["function"]
        module, _, attr = name.rpartition(".")
        assert module in ALLOWED_MODULES, q["id"]
        assert callable(getattr(importlib.import_module(module), attr, None)), q["id"]
        assert callable(resolve_function(name)), q["id"]


def test_no_identifier_in_the_question_files():
    for path in (QUESTIONS, PRACTICE):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.I), path
        assert "@" not in text and "://" not in text, path


# --- resolution on the fixture lake ----------------------------------------------------------


def _answer_for(expected, kind):
    """A well-formed answer to the expected value, as a careful analyst would write it."""
    value = expected["value"]
    if kind == "money":
        prefix = {"AUD": "A$", "USD": "US$"}.get(expected["currency"], "")
        return f"It was {prefix}{value:,.4f}."
    if kind == "count":
        return f"There were {value}."
    if kind == "rate":
        return f"The rate was {value * 100:.2f}%."
    return "They are " + ", ".join(value) + "."


@pytest.mark.parametrize("name", ["frozen", "practice"])
def test_every_required_question_resolves_and_scores_on_the_fixture(spark, loaded_lake, request, name):
    questions = request.getfixturevalue(name)
    required = [q for q in questions if q["source"] is not None and not q.get("optional")]

    expected = compute_expected(spark, loaded_lake[0], required)

    assert set(expected) == {q["id"] for q in required}
    for q in required:
        answer = expected[q["id"]]
        assert answer["rows"] <= MAX_ROWS, q["id"]
        if q["kind"] == "labels":
            assert answer["value"], q["id"]
            assert all(len(label) >= 2 for label in answer["value"]), q["id"]  # one letter matches prose
            # a pseudo-project in the answer is named as a project in the question
            for label in answer["value"]:
                if not re.fullmatch(r"p\d+", label):
                    assert label in q["text"], (q["id"], label)
        else:
            assert isinstance(answer["value"], int | float), q["id"]
        if q["kind"] == "count":
            assert isinstance(answer["value"], int), q["id"]
        assert score_question(_answer_for(answer, q["kind"]), answer, q).correct, q["id"]
    for q in questions:
        if q["kind"] == "unanswerable":
            assert score_question("That is not in the data.", None, q).correct
            assert not score_question("It rose from 50% to 60%.", None, q).correct


def test_the_optional_gateway_question_resolves_once_its_session_has_run(spark, loaded_lake, frozen):
    # the fixture lake has no gateway session, so the question is dropped there; plan 2 sets the session
    optional = [q for q in frozen if q.get("optional")]

    for q in optional:
        with pytest.raises(ValueError, match="no row"):
            compute_expected(spark, loaded_lake[0], [q])
