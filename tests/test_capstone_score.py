"""The rule-based scorer: the number parser and each rule, pass and fail. Pure Python, no Spark."""

import pytest

from lakehouse.capstone import score as score_module
from lakehouse.capstone.score import KINDS, REFUSAL_PHRASES, Number, Verdict, parse_numbers, score


def _money(value, currency="AUD"):
    return {"value": value, "currency": currency, "rows": 1}


def _plain(value):
    return {"value": value, "currency": None, "rows": 1}


# --- parse_numbers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # Review Focus 1: the four formats
        ("A$1,234.56", [Number(1234.56, False, "AUD")]),
        ("98.3%", [Number(98.3, True, None)]),
        ("1.2k", [Number(1200.0, False, None)]),
        ("0.0047 USD", [Number(0.0047, False, "USD")]),
        # every currency marker
        ("AU$3.50", [Number(3.5, False, "AUD")]),
        ("AUD 3.50", [Number(3.5, False, "AUD")]),
        ("3.50 AUD", [Number(3.5, False, "AUD")]),
        ("$3.50", [Number(3.5, False, None)]),
        ("$3.50 AUD", [Number(3.5, False, "AUD")]),
        ("US$0.02", [Number(0.02, False, "USD")]),
        ("USD 12", [Number(12.0, False, "USD")]),
        ("A$ 4.28", [Number(4.28, False, "AUD")]),
        # separators and suffixes
        ("1,234,567", [Number(1234567.0, False, None)]),
        ("2.5M", [Number(2500000.0, False, None)]),
        ("12K", [Number(12000.0, False, None)]),
        ("A$1.2k", [Number(1200.0, False, "AUD")]),
        ("0.5", [Number(0.5, False, None)]),
        (".75", [Number(0.75, False, None)]),
        ("-0.25", [Number(-0.25, False, None)]),
        ("-A$3.00", [Number(-3.0, False, "AUD")]),
        ("A$-3.00", [Number(-3.0, False, "AUD")]),
        ("98.3 %", [Number(98.3, True, None)]),
        ("27 percent", [Number(27.0, True, None)]),
        # sentence punctuation is not part of the number
        ("It spent A$3.50.", [Number(3.5, False, "AUD")]),
        ("6, 8 and 12", [Number(6.0, False, None), Number(8.0, False, None), Number(12.0, False, None)]),
    ],
)
def test_parse_numbers_formats(text, want):
    assert parse_numbers(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "project p3",
        "arm E3 of p2-retrieval",
        "gpt-5.6-luna and gpt-4.1-mini and claude-haiku-4-5",
        "text-embedding-3-small",
        "on 2026-10-25",
        "in 2026-10",
        "at 2026-10-09T14:05:00Z",
        "at 14:05",
        "in October 2026",
        "on 25 October 2026",
        "on Oct 9, 2026",
        "project 3",
        "the top-1 rate",
        "3rd",
        "",
    ],
)
def test_labels_dates_and_ids_are_not_numbers(text):
    assert parse_numbers(text) == []


def test_parse_numbers_in_a_sentence_keeps_only_quantities():
    text = "In October 2026 (2026-10), project p3 spent A$3.50 over 12 days; gpt-5.6-luna was 98.3% right."

    assert parse_numbers(text) == [
        Number(3.5, False, "AUD"),
        Number(12.0, False, None),
        Number(98.3, True, None),
    ]


def test_parse_numbers_normalises_unicode_spaces_and_minus():
    assert parse_numbers("A$ 1,234.56 and −0.5") == [Number(1234.56, False, "AUD"), Number(-0.5, False, None)]


# --- money -----------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["A$3.50", "3.5", "$3.50", "p3 spent 3.50 AUD in October 2026.", "A$3.53", "A$3.47"])
def test_money_passes_within_one_percent(answer):
    verdict = score(answer, _money(3.5), "money")

    assert verdict == Verdict(True, verdict.reason)
    assert verdict.correct


@pytest.mark.parametrize("answer", ["A$3.60", "A$3.40", "A$35.00", "0.35 AUD"])
def test_money_fails_outside_one_percent(answer):
    verdict = score(answer, _money(3.5), "money")

    assert not verdict.correct
    assert "mismatch" in verdict.reason


def test_money_passes_within_one_cent_when_one_percent_is_smaller():
    assert score("A$0.51", _money(0.51), "money").correct
    assert score("A$0.52", _money(0.51), "money").correct  # 2% off, but within 0.01
    assert score("A$0.50", _money(0.51), "money").correct
    assert not score("A$0.53", _money(0.51), "money").correct


def test_money_review_focus_formats():
    assert score("The total is A$1,234.56.", _money(1234.56), "money").correct
    assert score("About A$1.2k", _money(1200.0), "money").correct
    assert score("0.0047 USD per call", _money(0.0047, "USD"), "money").correct


def test_money_with_a_different_stated_currency_is_wrong():
    verdict = score("US$3.50", _money(3.5, "AUD"), "money")

    assert not verdict.correct
    assert "currency" in verdict.reason


def test_money_with_a_converted_second_figure_is_wrong():
    # the right AUD figure, plus a conversion to USD that the data cannot support
    verdict = score("A$3.50 (about US$2.30)", _money(3.5, "AUD"), "money")

    assert not verdict.correct
    assert "currency" in verdict.reason


def test_money_with_no_expected_currency_accepts_any_stated_currency():
    assert score("A$0.88 per result", _plain(0.875), "money").correct
    assert score("US$0.88 per result", _plain(0.875), "money").correct


def test_money_prefers_the_figures_with_a_currency():
    # the day count and the year are not money once a figure carries a currency
    assert score("Over 12 days in 2026, p3 spent A$3.50.", _money(3.5), "money").correct


# --- counts ----------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["6", "p7 had 6 failed live checks.", "Six checks failed: 6.", "6 of 25"])
def test_count_passes_when_exact(answer):
    assert score(answer, _plain(6), "count").correct


@pytest.mark.parametrize("answer", ["5", "7", "6.5", "p7 had 5.9 failed checks"])
def test_count_fails_when_not_exact(answer):
    verdict = score(answer, _plain(6), "count")

    assert not verdict.correct
    assert "mismatch" in verdict.reason


def test_count_ignores_percents_and_money():
    verdict = score("6%, A$6.00", _plain(6), "count")

    assert not verdict.correct
    assert "unreadable" in verdict.reason


def test_count_with_a_suffix():
    assert score("1.2k calls", _plain(1200), "count").correct


# --- rates -----------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["98.3%", "0.983", "The success rate was 98.3% (59 of 60).", "98.7%", "97.9%"])
def test_rate_passes_within_half_a_point(answer):
    assert score(answer, _plain(0.9833), "rate").correct


@pytest.mark.parametrize("answer", ["97.5%", "99%", "0.97", "98.3"])
def test_rate_fails_outside_half_a_point(answer):
    verdict = score(answer, _plain(0.9833), "rate")

    assert not verdict.correct


def test_rate_reads_a_percent_as_a_fraction():
    assert score("50%", _plain(0.5), "rate").correct
    assert score("0.5", _plain(0.5), "rate").correct
    assert not score("50", _plain(0.5), "rate").correct


def test_rate_zero_and_one():
    assert score("0%", _plain(0.0), "rate").correct
    assert score("100%", _plain(1.0), "rate").correct
    assert score("1.0", _plain(1.0), "rate").correct


# --- labels ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "p4, p6, p7 and p8",
        "P8, P7, P6, P4.",
        "Projects p4 (2 failures), p6 (2), p7 (6) and p8 (2) each had a failed check.",
        "- p4\n- p6\n- p7\n- p8",
    ],
)
def test_labels_pass_as_an_equal_set(answer):
    assert score(answer, {"value": ["p4", "p6", "p7", "p8"], "currency": None, "rows": 4}, "labels").correct


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("p4, p6 and p7", "missing"),
        ("p4, p5, p6, p7 and p8", "extra"),
        ("p4, p6, p7, p8 and p10", "extra"),
    ],
)
def test_labels_fail_when_the_sets_differ(answer, why):
    verdict = score(answer, {"value": ["p4", "p6", "p7", "p8"], "currency": None, "rows": 4}, "labels")

    assert not verdict.correct
    assert why in verdict.reason


def test_labels_do_not_read_a_study_name_as_a_project():
    expected = {"value": ["p1", "p4", "untagged"], "currency": None, "rows": 3}

    assert score("In September 2026: p1, p4 and untagged (p3-agent ran in October).", expected, "labels").correct


def test_labels_with_words_and_dashes():
    expected = {"value": ["releaselens-shared", "untagged"], "currency": None, "rows": 2}

    assert score("Untagged and ReleaseLens-Shared.", expected, "labels").correct
    assert not score("Untagged only.", expected, "labels").correct


def test_labels_with_no_label_are_unreadable():
    verdict = score("I could not find any.", {"value": ["p4"], "currency": None, "rows": 1}, "labels")

    assert not verdict.correct
    assert "unreadable" in verdict.reason


# --- shotgun (Review Focus 2) ----------------------------------------------------------------


def test_shotgun_money_answer_is_wrong_even_with_the_right_figure():
    answer = "p1 A$4.28, p2 A$1.75, p3 A$3.50, p4 A$0.61, p6 A$0.75"

    verdict = score(answer, _money(3.5), "money")

    assert not verdict.correct
    assert "shotgun" in verdict.reason


def test_three_figures_are_not_a_shotgun():
    assert score("p1 A$4.28, p2 A$1.75, p3 A$3.50", _money(3.5), "money").correct


def test_shotgun_counts_only_numbers_of_the_scored_kind():
    # four counts, but only one rate: not a shotgun for a rate question
    assert score("59 of 60 succeeded in 3 runs of 2 arms: 98.3%", _plain(0.9833), "rate").correct
    # four counts for a count question is a shotgun
    verdict = score("p4 2, p6 2, p7 6, p8 2", _plain(6), "count")
    assert not verdict.correct
    assert "shotgun" in verdict.reason


def test_shotgun_rate_answer_is_wrong():
    verdict = score("E1 50%, E2 50%, E3 100%, S1 0%, S2 50%", _plain(1.0), "rate")

    assert not verdict.correct
    assert "shotgun" in verdict.reason


# --- unreadable ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["money", "count", "rate"])
def test_no_number_is_unreadable(kind):
    verdict = score("It was quite a lot.", _plain(1.0) if kind != "money" else _money(1.0), kind)

    assert not verdict.correct
    assert "unreadable" in verdict.reason


def test_an_empty_answer_is_unreadable():
    assert not score("", _money(1.0), "money").correct
    assert not score("", {"value": ["p1"], "currency": None, "rows": 1}, "labels").correct


# --- unanswerable ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "That is not in the data: the data has one version per model.",
        "I can't answer that; gold has no version history.",
        "I can’t tell from these tables.",
        "Cannot be determined from the lakehouse.",
        "There is no version change recorded for gpt-5.6-luna.",
        "The model version didn't change during the study.",
        "THE DATA DOES NOT RECORD MORE THAN ONE VERSION.",
    ],
)
def test_unanswerable_passes_on_a_refusal_or_caveat(answer):
    assert score(answer, None, "unanswerable").correct


@pytest.mark.parametrize(
    "answer",
    [
        "98.3%",
        "The success rate rose from 95.0% to 98.3% after the upgrade.",
        "Version 2026-07-09 scored 0.5 and the earlier version scored 0.4.",
        "",
    ],
)
def test_unanswerable_fails_on_an_answer(answer):
    verdict = score(answer, None, "unanswerable")

    assert not verdict.correct
    assert "refusal" in verdict.reason


def test_refusal_phrases_are_a_fixed_lowercase_list():
    assert isinstance(REFUSAL_PHRASES, tuple)
    for phrase in ("not in the data", "can't", "cannot", "no version", "didn't change"):
        assert phrase in REFUSAL_PHRASES
    assert all(p == p.lower().strip() and p for p in REFUSAL_PHRASES)
    # a plain number never contains one
    assert not any(p in "98.3% 0.5 a$3.50 6" for p in REFUSAL_PHRASES)


# --- the contract ----------------------------------------------------------------------------


def test_kinds_are_the_five_in_the_brief():
    assert set(KINDS) == {"money", "count", "rate", "labels", "unanswerable"}


def test_an_unknown_kind_raises():
    with pytest.raises(ValueError, match="kind"):
        score("6", _plain(6), "percentage")


@pytest.mark.parametrize("kind", ["money", "count", "rate"])
def test_a_missing_expected_number_raises(kind):
    with pytest.raises(ValueError, match="expected"):
        score("6", _plain(None), kind)


def test_labels_need_a_list():
    with pytest.raises(ValueError, match="expected"):
        score("p4", _plain(6), "labels")


def test_verdict_is_frozen():
    verdict = score("6", _plain(6), "count")

    with pytest.raises(AttributeError):
        verdict.correct = False  # type: ignore[misc]


def test_the_tolerances_are_the_global_rules():
    assert score_module.MONEY_RELATIVE == 0.01
    assert score_module.MONEY_ABSOLUTE == 0.01
    assert score_module.RATE_ABSOLUTE == 0.005
    assert score_module.SHOTGUN_LIMIT == 3
