import json
import re
from pathlib import Path

import pytest

from lakehouse.extract.private import assert_clean, extract_p1, extract_p2

FIXTURES = Path(__file__).parent / "fixtures"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

P1_KEYS = {
    "study", "run_id", "started_at", "item_id", "arm", "pass_index", "attempt_kind", "provider",
    "model", "success", "latency_ms", "tokens_in", "tokens_out", "cached_tokens", "cost_usd",
    "judge_cost_usd", "groundedness", "citation_recall", "citation_precision", "must_contain",
}
P2_KEYS = {
    "study", "run_id", "started_at", "item_id", "arm", "pass_index", "attempt_kind", "success",
    "latency_ms", "tokens_in", "target_rank", "top1", "reciprocal_rank",
}


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def p1():
    return _load("p1_report.json")


@pytest.fixture
def p2():
    return _load("p2_report.json")


@pytest.fixture
def questions():
    lines = (FIXTURES / "p2_questions.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _p2_row(rows, qid, arm, kind="scored"):
    (row,) = [r for r in rows if r["item_id"] == qid and r["arm"] == arm and r["attempt_kind"] == kind]
    return row


def test_p1_row_shape(p1):
    rows = extract_p1(p1)

    assert len(rows) == 12
    assert all(set(r) == P1_KEYS for r in rows)
    assert all(r["study"] == "p1-three-arm" and r["attempt_kind"] == "scored" for r in rows)
    assert all(r["run_id"] == p1["run_id"] and r["started_at"] == p1["started_at"] for r in rows)

    first, src = rows[0], p1["outcomes"][0]
    assert first["item_id"] == src["id"]
    assert first["cached_tokens"] == src["cache_read_input_tokens"]
    assert first["must_contain"] == src["must_contain_satisfied"]
    assert first["success"] is True

    no_reply = [r for r, o in zip(rows, p1["outcomes"]) if o["no_reply"]]
    assert len(no_reply) == 1
    assert no_reply[0]["success"] is False


def test_p1_error_is_not_success(p1):
    p1["outcomes"][0]["error"] = "boom"

    assert extract_p1(p1)[0]["success"] is False


def test_p2_row_shape(p2, questions):
    rows = extract_p2(p2, questions)

    assert len(rows) == 7
    assert all(set(r) == P2_KEYS for r in rows)
    assert all(r["study"] == "p2-retrieval" and r["pass_index"] == 1 for r in rows)
    row = _p2_row(rows, "r-001", "E1")
    assert row["latency_ms"] == 110
    assert row["tokens_in"] == 12
    assert row["success"] is True
    assert row["run_id"] == p2["run_id"] and row["started_at"] == p2["started_at"]


def test_p2_rank_and_metrics(p2, questions):
    rows = extract_p2(p2, questions)

    row = _p2_row(rows, "r-001", "E1")
    assert row["target_rank"] == 3
    assert row["top1"] is False
    assert row["reciprocal_rank"] == pytest.approx(1 / 3, abs=1e-4)

    best = _p2_row(rows, "r-002", "E1")
    assert best["target_rank"] == 1
    assert best["top1"] is True
    assert best["reciprocal_rank"] == 1.0


def test_p2_target_missing_from_hits(p2, questions):
    rows = extract_p2(p2, questions)

    row = _p2_row(rows, "r-003", "E1")
    assert row["target_rank"] is None
    assert row["top1"] is False
    assert row["reciprocal_rank"] == 0.0


def test_p2_error_is_not_success(p2, questions):
    p2["results"][0]["error"] = "boom"

    assert _p2_row(extract_p2(p2, questions), "r-001", "E1")["success"] is False


def test_p2_repeat_kind(p2, questions):
    rows = extract_p2(p2, questions)

    repeat = _p2_row(rows, "r-001", "E1", kind="determinism_repeat")
    assert repeat["latency_ms"] == 115
    assert [r["attempt_kind"] for r in rows].count("determinism_repeat") == 1
    assert [r["attempt_kind"] for r in rows].count("scored") == 6


def test_p2_unknown_question_is_an_error(p2, questions):
    with pytest.raises(ValueError, match="r-001"):
        extract_p2(p2, [q for q in questions if q["qid"] != "r-001"])


def test_no_text_or_identifiers(p1, p2, questions):
    raw = json.dumps([p1, p2])
    assert "https://" in raw  # the inputs do carry URLs, so the check below is not vacuous

    rows = extract_p1(p1) + extract_p2(p2, questions)

    assert_clean(rows)
    for row in rows:
        for value in row.values():
            if isinstance(value, str):
                assert not GUID.search(value)
                assert "https://" not in value


def test_assert_clean_rejects_answer():
    with pytest.raises(ValueError, match="answer"):
        assert_clean([{"item_id": "q-1", "answer": "text"}])


@pytest.mark.parametrize("field", ["question", "groundedness_reason", "citations", "hits", "chunk_id"])
def test_assert_clean_rejects_each_forbidden_key(field):
    with pytest.raises(ValueError, match=field):
        assert_clean([{field: "x"}])


def test_assert_clean_rejects_guid_and_url_values():
    with pytest.raises(ValueError, match="run_id"):
        assert_clean([{"run_id": "123e4567-e89b-12d3-a456-426614174000"}])
    with pytest.raises(ValueError, match="model"):
        assert_clean([{"model": "see https://example.invalid/x"}])
