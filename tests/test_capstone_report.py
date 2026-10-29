"""The capstone report: records scored by rule, headline numbers computed by hand here."""

import json
from pathlib import Path

import pytest

import capstone_freeze
import capstone_report
from lakehouse.capstone.freeze import build_freeze
from lakehouse.capstone.report import ReportError, load_records, render, summarise

ROOT = Path(__file__).resolve().parent.parent
FREEZE = {"components": {}, "hash": "a" * 64, "short": "a" * 12}
OTHER = "b" * 12
SNAPSHOT = "2026-11-08T00:00:00Z"

# q03 is optional and has no expected answer, so it is dropped: three questions are scored.
QUESTIONS = [
    {"id": "q01", "text": "How much has p2 spent?", "kind": "money", "source": {"function": "f"}},
    {"id": "q02", "text": "How many checks failed for p7?", "kind": "count",
     "source": {"function": "g", "select": {"project": "p7", "verdict": "fail"}, "column": "n"}},
    {"id": "q03", "text": "How many gateway calls?", "kind": "count", "source": {"function": "h"}, "optional": True},
    {"id": "q04", "text": "How did the rate change between versions?", "kind": "unanswerable", "source": None},
]  # fmt: skip
EXPECTED = {"q01": {"value": 100.0, "currency": "AUD", "rows": 1}, "q02": {"value": 7, "currency": None, "rows": 1}}

REFUSAL = "The data has only one version per model, so I can't tell."
# Direct: q01 right, right, wrong; q02 right in all 3; q04 wrong, wrong, right.
#   per pass: 2, 2, 2 -> mean 2.00 of 3; correct in all 3: q02 -> 1; correct in none: none -> 0.
# Foundry: q01 right in all 3; q02 wrong in all 3; q04 right, wrong, wrong.
#   per pass: 2, 1, 1 -> mean 4/3 = 1.33 of 3; correct in all 3: q01 -> 1; correct in none: q02 -> 1.
ANSWERS = {
    "direct": {
        "q01": ["A$100.00", "A$100.50", "A$150 in total"],
        "q02": ["7 checks failed", "7", "7 failed and 3 passed"],
        "q04": ["It went up", "It rose by 12%", REFUSAL],
    },
    "foundry": {
        "q01": ["A$99.50", "AUD 100", "100 AUD"],
        "q02": ["7 passed and 3 failed", "None failed", "about 9"],
        "q04": [REFUSAL, "It is higher now", "Not sure | maybe up"],
    },
}


def _record(route, qid, n, text, short="a" * 12, **over):
    record = {
        "route": route,
        "freeze": short,
        "question_id": qid,
        "pass": n,
        "answer_text": text,
        "raw_output": json.dumps({"content": [{"type": "text", "text": text}]}),
        "tool_calls": 1 if route == "direct" else 2,
        "input_tokens": None if route == "direct" else 1000 + n,
        "output_tokens": None if route == "direct" else 50,
        "wall_ms": 20000 + n * 1000,
        "started_utc": f"2026-11-10T09:0{n}:00.000Z",
    }
    record.update(over)
    return record


def _records(routes=("direct", "foundry")):
    return [
        _record(route, qid, n, texts[n - 1])
        for route in routes
        for qid, texts in ANSWERS[route].items()
        for n in (1, 2, 3)
    ]


def _render(records, **over):
    meta = {"snapshot_time": SNAPSHOT, "freezes": 1}
    meta.update(over)
    return render(records, QUESTIONS, EXPECTED, FREEZE, **meta)


def test_headline_numbers_match_the_hand_count():
    summary = summarise(_records(), QUESTIONS, EXPECTED, FREEZE)

    assert summary["scored"] == ["q01", "q02", "q04"]
    assert summary["dropped"] == ["q03"]
    direct, foundry = summary["routes"]["direct"], summary["routes"]["foundry"]
    assert direct["per_pass"] == [2, 2, 2]
    assert direct["mean"] == pytest.approx(2.0)
    assert direct["all"] == ["q02"] and direct["none"] == []
    assert foundry["per_pass"] == [2, 1, 1]
    assert foundry["mean"] == pytest.approx(4 / 3)
    assert foundry["all"] == ["q01"] and foundry["none"] == ["q02"]


def test_the_headline_is_rendered_out_of_the_scored_count():
    text = _render(_records())

    assert "| Direct | 2.00 of 3 | 2, 2, 2 | 1 | 0 |" in text
    assert "| Through Foundry | 1.33 of 3 | 2, 1, 1 | 1 | 1 |" in text
    assert "3 questions are scored" in text
    assert "q03 is dropped" in text


def test_the_count_verdict_filter_is_applied():
    # "7 failed and 3 passed" is right for a fail count of 7; "7 passed and 3 failed" is wrong,
    # although a bare count score would find the 7 in it.
    summary = summarise(_records(), QUESTIONS, EXPECTED, FREEZE)
    verdicts = summary["routes"]["foundry"]["verdicts"]
    assert verdicts[("q02", 1)].correct is False
    assert summary["routes"]["direct"]["verdicts"][("q02", 3)].correct is True


def test_every_wrong_answer_is_quoted_and_right_ones_are_not():
    text = _render(_records())
    wrong = text.split("## Wrong answers", 1)[1]

    for quote in ("> A$150 in total", "> It went up", "> It rose by 12%", "> 7 passed and 3 failed",
                  "> None failed", "> about 9", "> It is higher now", "> Not sure | maybe up"):  # fmt: skip
        assert quote in wrong
    assert "> A$100.00" not in wrong and "> A$99.50" not in wrong
    assert wrong.count("\n> ") == 8


def test_the_per_question_table_has_verdicts_and_reasons_escaped():
    text = _render(_records())

    assert "| q01 | 1 | right | correct: within" in text
    assert "| q01 | 3 | wrong | mismatch:" in text
    assert "| q04 | 1 | wrong | refusal: the answer uses a direction or comparison word ('up') |" in text
    for line in text.splitlines():
        if line.startswith("| q04 | 3 |") and "foundry" in line.lower():
            pytest.fail("route names belong in the headings, not the rows")


def test_tool_calls_tokens_and_wall_time_are_reported_where_present():
    text = _render(_records())
    direct = text.split("### Direct", 1)[1].split("### Through Foundry", 1)[0]
    foundry = text.split("### Through Foundry", 1)[1]

    assert "Tool calls: 9 in all, 1.0 per question." in direct
    assert "Tokens: not recorded." in direct
    assert "Tool calls: 18 in all, 2.0 per question." in foundry
    # input tokens 1001 + 1002 + 1003, three questions each: 9018; output 50 x 9 = 450
    assert "Tokens: 9,018 in, 450 out." in foundry
    # wall: (21 + 22 + 23) s x 3 questions = 198 s, 22.0 s per question
    assert "Wall time: 198.0 s in all, 22.0 s per question." in direct
    assert "| q01 | 2 | right |" in direct and "| 22.0 |" in direct


def test_the_report_states_freeze_snapshot_freezes_and_cu():
    text = _render(_records(), freezes=2, cu={"direct": 1520.5, "foundry": None}, cu_unit="CU-seconds")

    assert f"Freeze: `{'a' * 12}`" in text
    assert f"Snapshot: {SNAPSHOT}" in text
    assert "Freezes: 2" in text
    assert "1,520.5 CU-seconds" in text
    assert "not given" in text


def test_records_from_another_freeze_fail():
    records = _records()
    records[4] = _record("direct", "q02", 2, "7", short=OTHER)

    with pytest.raises(ReportError, match="another freeze"):
        _render(records)


def test_a_duplicate_record_fails():
    records = _records() + [_record("direct", "q01", 1, "A$100.00")]

    with pytest.raises(ReportError, match="duplicate"):
        _render(records)


def test_a_missing_record_is_an_error_not_a_wrong_answer():
    records = [r for r in _records() if not (r["route"] == "foundry" and r["question_id"] == "q04" and r["pass"] == 2)]

    with pytest.raises(ReportError, match=r"missing.*foundry.*q04.*pass 2"):
        _render(records)


def test_an_invalid_record_fails():
    records = _records()
    records[0] = _record("direct", "q01", 1, "A$100.00", wall_ms="slow")

    with pytest.raises(ReportError, match="wall_ms"):
        _render(records)


def test_an_unknown_question_fails():
    with pytest.raises(ReportError, match="q99"):
        _render(_records() + [_record("direct", "q99", 1, "1")])


def test_a_scored_question_without_an_expected_answer_fails():
    expected = {"q01": EXPECTED["q01"]}

    with pytest.raises(ReportError, match="q02"):
        render(_records(), QUESTIONS, expected, FREEZE, snapshot_time=SNAPSHOT, freezes=1)


def test_records_for_a_dropped_question_are_left_out_and_said_so():
    extra = [_record("foundry", "q03", n, "12 calls") for n in (1, 2, 3)]
    summary = summarise(_records() + extra, QUESTIONS, EXPECTED, FREEZE)
    text = _render(_records() + extra)

    assert summary["routes"]["foundry"]["per_pass"] == [2, 1, 1]
    assert "3 records for dropped questions are left out" in text
    assert "> 12 calls" not in text


def test_a_route_without_records_is_reported_as_not_run():
    text = _render(_records(routes=("direct",)))

    assert "| Direct | 2.00 of 3 |" in text
    assert "| Through Foundry | not run |" in text


def test_the_scored_count_is_15_or_14_with_the_real_questions():
    questions = json.loads((ROOT / "capstone" / "questions.json").read_text(encoding="utf-8"))
    expected = {q["id"]: {"value": 1, "currency": "AUD", "rows": 1} for q in questions if q["source"]}
    labels = {"value": ["p1"], "currency": None, "rows": 1}
    expected.update({q["id"]: labels for q in questions if q["kind"] == "labels"})
    with_q12 = summarise([], questions, expected, FREEZE)
    del expected["q12"]
    without = summarise([], questions, expected, FREEZE)

    assert len(with_q12["scored"]) == 15 and with_q12["dropped"] == []
    assert len(without["scored"]) == 14 and without["dropped"] == ["q12"]
    text = render([], questions, expected, FREEZE, snapshot_time=SNAPSHOT, freezes=1)
    assert "14 questions are scored" in text and "q12 is dropped" in text


def test_quoted_answers_are_redacted_again():
    records = _records()
    records[2] = _record("direct", "q01", 3, "See https://leak.example/x for A$150")

    text = _render(records)

    assert "leak.example" not in text and "> See <url> for A$150" in text


def test_load_records_reads_jsonl_and_names_a_bad_line(tmp_path):
    good = tmp_path / "a.jsonl"
    good.write_text("\n".join(json.dumps(r) for r in _records()[:2]) + "\n", encoding="utf-8")
    (tmp_path / "ignored.jsonl.partial").write_text("not json\n", encoding="utf-8")
    assert len(load_records([tmp_path])) == 2

    bad = tmp_path / "b.jsonl"
    bad.write_text(json.dumps(_records()[0]) + "\n{oops\n", encoding="utf-8")
    with pytest.raises(ReportError, match=r"b\.jsonl.*line 2"):
        load_records([tmp_path])


# --- the CLI: verifies the freeze, then renders ---

INSTRUCTIONS = b"You are the analyst.\n"
MODELS = {"direct": "gpt-5.5", "foundry": "gpt-5.5"}


@pytest.fixture
def cli(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "data_agent.json").write_text("{}\n", encoding="utf-8")
    questions, expected, rules = tmp_path / "questions.json", tmp_path / "expected.json", tmp_path / "score.py"
    questions.write_text(json.dumps(QUESTIONS) + "\n", encoding="utf-8")
    expected.write_text(json.dumps(EXPECTED) + "\n", encoding="utf-8")
    rules.write_text("# rules\n", encoding="utf-8")
    monkeypatch.setattr(capstone_freeze, "read_instructions", lambda repo, commit: INSTRUCTIONS)
    freeze = build_freeze(questions, expected, SNAPSHOT, agent, INSTRUCTIONS, MODELS, rules=rules)
    (tmp_path / "freeze.json").write_text(json.dumps(freeze) + "\n", encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    for route in ("direct", "foundry"):
        lines = [json.dumps(_record(route, qid, n, texts[n - 1], short=freeze["short"]))
                 for qid, texts in ANSWERS[route].items() for n in (1, 2, 3)]  # fmt: skip
        (runs / f"{route}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    args = [
        "--runs", str(runs),
        "--out", str(tmp_path / "report.md"),
        "--freezes", "1",
        "--cu-direct", "1520.5", "--cu-unit", "CU-seconds",
        "--questions", str(questions), "--expected", str(expected), "--rules", str(rules),
        "--data-agent-dir", str(agent), "--snapshot-time", SNAPSHOT,
        "--analyst-repo", str(tmp_path), "--analyst-commit", "abc123",
        "--model-direct", MODELS["direct"], "--model-foundry", MODELS["foundry"],
        "--freeze", str(tmp_path / "freeze.json"),
    ]  # fmt: skip
    return {"args": args, "freeze": freeze, "tmp": tmp_path, "runs": runs}


def test_cli_renders_the_report(cli):
    assert capstone_report.main(cli["args"]) == 0

    raw = (cli["tmp"] / "report.md").read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    text = raw.decode("utf-8")
    assert f"Freeze: `{cli['freeze']['short']}`" in text
    assert "| Direct | 2.00 of 3 | 2, 2, 2 | 1 | 0 |" in text
    assert "1,520.5 CU-seconds" in text


def test_cli_refuses_a_broken_freeze(cli, capsys):
    args = list(cli["args"])
    args[args.index("--snapshot-time") + 1] = "2026-11-09T00:00:00Z"

    assert capstone_report.main(args) == 1
    assert "snapshot_time" in capsys.readouterr().err
    assert not (cli["tmp"] / "report.md").exists()


def test_cli_fails_on_records_from_another_freeze(cli, capsys):
    (cli["runs"] / "old.jsonl").write_text(json.dumps(_record("direct", "q01", 1, "A$1", short=OTHER)) + "\n")

    assert capstone_report.main(cli["args"]) == 2
    assert "another freeze" in capsys.readouterr().err
    assert not (cli["tmp"] / "report.md").exists()


def test_cli_needs_a_unit_with_a_cu_figure(cli, capsys):
    args = list(cli["args"])
    i = args.index("--cu-unit")
    del args[i : i + 2]

    assert capstone_report.main(args) == 2
    assert "--cu-unit" in capsys.readouterr().err
