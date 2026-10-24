"""The rule-based scorer: the number parser and each rule, pass and fail. Pure Python, no Spark."""

import pytest

from lakehouse.capstone import score as score_module
from lakehouse.capstone.score import (
    KINDS,
    REFUSAL_PHRASES,
    REFUSAL_VERBS,
    Number,
    Verdict,
    parse_numbers,
    score,
    score_question,
)


def _money(value, currency="AUD"):
    return {"value": value, "currency": currency, "rows": 1}


def _plain(value):
    return {"value": value, "currency": None, "rows": 1}


def _labels(*values):
    return {"value": list(values), "currency": None, "rows": len(values)}


def N(value, percent=False, currency=None, *, marked=None, decimal=None, range=False):  # noqa: A002
    """A Number, with `marked` defaulting to whether a currency is named and `decimal` to a fraction."""
    return Number(
        float(value),
        percent,
        currency,
        marked=currency is not None if marked is None else marked,
        decimal=(float(value) != int(float(value))) if decimal is None else decimal,
        range=range,
    )


# --- parse_numbers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # Review Focus 1: the four formats
        ("A$1,234.56", [N(1234.56, currency="AUD")]),
        ("98.3%", [N(98.3, True)]),
        ("1.2k", [N(1200, decimal=True)]),
        ("0.0047 USD", [N(0.0047, currency="USD")]),
        # every currency marker
        ("AU$3.50", [N(3.5, currency="AUD")]),
        ("AUD 3.50", [N(3.5, currency="AUD")]),
        ("3.50 AUD", [N(3.5, currency="AUD")]),
        ("$3.50", [N(3.5, marked=True)]),
        ("$3.50 AUD", [N(3.5, currency="AUD")]),
        ("US$0.02", [N(0.02, currency="USD")]),
        ("USD 12", [N(12, currency="USD")]),
        ("A$ 4.28", [N(4.28, currency="AUD")]),
        ("€3.50", [N(3.5, currency="EUR")]),
        # separators and suffixes
        ("1,234,567", [N(1234567)]),
        ("2.5M", [N(2500000, decimal=True)]),
        ("A$1.2m", [N(1200000, currency="AUD", decimal=True)]),
        ("12K", [N(12000)]),
        ("A$1.2k", [N(1200, currency="AUD", decimal=True)]),
        ("1.5x faster", [N(1.5)]),
        ("0.5", [N(0.5)]),
        ("1.0", [N(1, decimal=True)]),
        (".75", [N(0.75)]),
        ("-0.25", [N(-0.25)]),
        ("-A$3.00", [N(-3, currency="AUD", decimal=True)]),
        ("A$-3.00", [N(-3, currency="AUD", decimal=True)]),
        # spaced thousands after a currency marker, with plain, no-break and narrow no-break spaces
        ("A$ 1 234", [N(1234, currency="AUD")]),
        ("A$1 234.50", [N(1234.5, currency="AUD")]),
        ("A$ 1 234", [N(1234, currency="AUD")]),
        ("A$1 234", [N(1234, currency="AUD")]),
        ("1 234 calls", [N(1), N(234)]),  # without a marker, a space separates numbers
        # percents
        ("98.3 %", [N(98.3, True)]),
        ("27 percent", [N(27, True)]),
        ("27 per cent", [N(27, True)]),
        ("27percent", [N(27, True)]),
        # ranges keep their first end and are flagged
        ("5-7 checks", [N(5, range=True)]),
        ("5–7 checks", [N(5, range=True)]),
        ("A$3.40-A$3.60", [N(3.4, currency="AUD", range=True)]),
        ("A$3.40–A$3.60", [N(3.4, currency="AUD", range=True)]),
        ("6 to 8 checks", [N(6, range=True)]),
        ("from A$3.40 to A$3.60", [N(3.4, currency="AUD", range=True)]),
        ("between 5 and 7 checks", [N(5, range=True)]),
        ("Between A$3.40 and A$3.60", [N(3.4, currency="AUD", range=True)]),
        # an em dash, or a hyphen or en dash with spaces on both sides, separates two figures
        ("98.3% — 59 of 60", [N(98.3, True), N(59), N(60)]),
        ("98.3%—59 of 60", [N(98.3, True), N(59), N(60)]),
        ("A$3.50 - 12 days", [N(3.5, currency="AUD"), N(12)]),
        ("A$3.50 – 12 days", [N(3.5, currency="AUD"), N(12)]),
        ("between 5 checks", [N(5)]),
        ("6 to go", [N(6)]),
        # sentence punctuation is not part of the number
        ("It spent A$3.50.", [N(3.5, currency="AUD")]),
        ("6, 8 and 12", [N(6), N(8), N(12)]),
        # a month name followed by a count keeps the count
        ("In October 6 checks failed", [N(6)]),
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
        "on 10/25/2026",
        "in October 2026",
        "on 25 October 2026",
        "on 25 October",
        "on Oct 9, 2026",
        "project 3",
        "the top-1 rate",
        "the top 1 rate",
        "3rd",
        "3ms",
        "1.5xyz",
        "",
    ],
)
def test_labels_dates_and_ids_are_not_numbers(text):
    assert parse_numbers(text) == []


def test_parse_numbers_in_a_sentence_keeps_only_quantities():
    text = "In October 2026 (2026-10), project p3 spent A$3.50 over 12 days; gpt-5.6-luna was 98.3% right."

    assert parse_numbers(text) == [N(3.5, currency="AUD"), N(12), N(98.3, True)]


def test_parse_numbers_normalises_unicode_spaces_and_minus():
    assert parse_numbers("A$ 1,234.56 and −0.5") == [N(1234.56, currency="AUD"), N(-0.5)]


def test_a_suffix_does_not_backtrack_into_the_digits():
    # "1.5x" is 1.5, never 1 (from backtracking off the ".5x")
    assert [n.value for n in parse_numbers("1.5x")] == [1.5]
    assert [n.value for n in parse_numbers("2.75xl")] == []


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
    assert score("A$ 1 234.56 in all", _money(1234.56), "money").correct
    assert score("A$1.2m", _money(1_200_000.0), "money").correct


def test_money_in_only_another_currency_is_wrong():
    verdict = score("US$3.50", _money(3.5, "AUD"), "money")

    assert not verdict.correct
    assert "currency" in verdict.reason


def test_money_ignores_a_figure_in_another_currency():
    # ruling R4: the US$ conversion is neither a match nor a shotgun figure; the A$ figure decides
    assert score("A$3.50 (about US$2.30)", _money(3.5, "AUD"), "money").correct
    assert score("A$3.50, or US$1, US$2 and US$3 at various rates", _money(3.5, "AUD"), "money").correct
    assert not score("A$3.70 (about US$3.50)", _money(3.5, "AUD"), "money").correct


def test_money_with_no_expected_currency_accepts_any_stated_currency():
    assert score("A$0.88 per result", _plain(0.875), "money").correct
    assert score("US$0.88 per result", _plain(0.875), "money").correct


def test_money_prefers_the_figures_with_a_currency():
    # the day count and the year are not money once a figure carries a currency
    assert score("Over 12 days in 2026, p3 spent A$3.50.", _money(3.5), "money").correct


def test_money_prefers_a_bare_dollar_figure_over_plain_numbers():
    answer = "In 2026, across 10 projects, p3 ran 12 days and spent $3.50"

    assert score(answer, _money(3.5), "money").correct
    assert not score("Across 10 projects, p3 spent $3.80 over 3.5 days", _money(3.5), "money").correct


def test_separators_are_not_ranges():
    assert score("The success rate was 98.3% — 59 of 60 runs.", _plain(0.9833), "rate").correct
    assert score("p3 spent A$3.50 - 12 days of usage.", _money(3.5), "money").correct
    assert score("In October 2026 - 6 checks failed", _plain(6), "count").correct


@pytest.mark.parametrize("answer", ["A$3.40 to A$3.60", "between A$3.40 and A$3.60", "A$3.40–A$3.60"])
def test_every_range_form_is_unreadable_for_money(answer):
    verdict = score(answer, _money(3.4), "money")

    assert not verdict.correct
    assert "unreadable" in verdict.reason


def test_plain_numbers_are_not_money_when_another_figure_is_marked():
    # the only marked figure is in another currency, so "3.5 days" is not read as the money
    verdict = score("p3 spent US$2.30 over 3.5 days", _money(3.5), "money")

    assert not verdict.correct
    assert "currency" in verdict.reason


def test_a_money_range_is_unreadable():
    verdict = score("Between A$3.40-A$3.60", _money(3.4), "money")

    assert not verdict.correct
    assert "unreadable" in verdict.reason


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
    verdict = score("6%, A$6.00, $6", _plain(6), "count")

    assert not verdict.correct
    assert "unreadable" in verdict.reason


def test_count_with_a_suffix():
    assert score("1.2k calls", _plain(1200), "count").correct


def test_a_count_range_is_unreadable():
    verdict = score("5-7 checks failed", _plain(5), "count")

    assert not verdict.correct
    assert "unreadable" in verdict.reason


def test_a_month_name_does_not_swallow_the_count():
    assert score("In October 6 checks failed", _plain(6), "count").correct


# --- counts next to a verdict ----------------------------------------------------------------


def test_the_count_next_to_the_asked_verdict_decides():
    assert not score("p7 ran 6 checks; 2 failed", _plain(6), "count", verdict="fail").correct
    assert score("p7 ran 25 checks; 6 failed", _plain(6), "count", verdict="fail").correct
    assert score("6 failed and 10 passed", _plain(6), "count", verdict="fail").correct
    assert score("10 passed, 6 failed", _plain(6), "count", verdict="fail").correct
    assert not score("6 passed, 10 failed", _plain(6), "count", verdict="fail").correct
    assert score("6 passed, 10 failed", _plain(6), "count", verdict="pass").correct


def test_several_counts_without_the_asked_verdict_are_wrong():
    verdict = score("p7 ran 25 checks and 6 passed", _plain(6), "count", verdict="fail")

    assert not verdict.correct
    assert "fail" in verdict.reason


def test_one_count_needs_no_verdict_word():
    assert score("6", _plain(6), "count", verdict="fail").correct
    assert score("p7 had 6.", _plain(6), "count", verdict="pass").correct


def test_several_counts_with_no_verdict_word_at_all_use_the_plain_rule():
    assert score("p7: 6 of 25 checks", _plain(6), "count", verdict="fail").correct


def test_not_run_words():
    assert score("2 checks were not run and 8 passed", _plain(2), "count", verdict="not-run").correct


def test_an_unknown_verdict_raises():
    with pytest.raises(ValueError, match="verdict"):
        score("6", _plain(6), "count", verdict="maybe")


def test_score_question_passes_the_selected_verdict():
    question = {
        "id": "q09",
        "kind": "count",
        "source": {"function": "f", "args": {}, "select": {"project": "p7", "verdict": "fail"}, "column": "n"},
    }

    assert not score_question("p7 ran 6 checks; 2 failed", _plain(6), question).correct
    assert score_question("p7 had 6 failed checks", _plain(6), question).correct
    assert score_question("That is not in the data.", None, {"id": "u", "kind": "unanswerable", "source": None}).correct


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
    assert score("50 per cent", _plain(0.5), "rate").correct
    assert score("0.5", _plain(0.5), "rate").correct
    assert not score("50", _plain(0.5), "rate").correct


def test_rate_zero_and_one():
    assert score("0%", _plain(0.0), "rate").correct
    assert score("100%", _plain(1.0), "rate").correct
    assert score("1.0", _plain(1.0), "rate").correct
    assert score("0.0", _plain(0.0), "rate").correct


def test_rate_never_reads_a_bare_whole_number():
    verdict = score("E3 scored 0% on its 1 query", _plain(1.0), "rate")

    assert not verdict.correct
    assert not score("It was 1 of 1", _plain(1.0), "rate").correct
    assert not score("0 of 2", _plain(0.0), "rate").correct


def test_rate_uses_only_percents_when_there_are_any():
    # the 0.5 (a latency in seconds) is not a rate once the answer states a percent
    assert not score("98% at 0.5 seconds", _plain(0.5), "rate").correct
    assert score("98% at 0.5 seconds", _plain(0.98), "rate").correct


def test_a_rate_range_is_unreadable():
    assert not score("between 95%-99%", _plain(0.95), "rate").correct


# --- labels ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "p4, p6, p7 and p8",
        "P8, P7, P6, P4.",
        "Projects p4 (2 failures), p6 (2), p7 (6) and p8 (2) each had a failed check.",
        "- p4\n- p6\n- p7\n- p8",
        "Projects 4, 6, 7 and 8.",
        "p4, p6, p7 and p8; p5 had none.",
        "Every project except p5: p4, p6, p7 and p8.",
        "p4, p6, p7 and p8, but not p5.",
    ],
)
def test_labels_pass_as_an_equal_set(answer):
    assert score(answer, _labels("p4", "p6", "p7", "p8"), "labels").correct


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("p4, p6 and p7", "missing"),
        ("p4, p5, p6, p7 and p8", "extra"),
        ("p4, p6, p7, p8 and p10", "extra"),
        ("Projects 4, 5, 6, 7 and 8", "extra"),
    ],
)
def test_labels_fail_when_the_sets_differ(answer, why):
    verdict = score(answer, _labels("p4", "p6", "p7", "p8"), "labels")

    assert not verdict.correct
    assert why in verdict.reason


def test_labels_map_project_numbers():
    assert score("Projects 1 and 4.", _labels("p1", "p4"), "labels").correct
    assert score("Project 1 and project 4.", _labels("p1", "p4"), "labels").correct


def test_labels_negated_mentions_are_not_present():
    assert score("p1 and p4; p2 had none.", _labels("p1", "p4"), "labels").correct
    assert score("All of them except p5 and p6: that leaves p1 and p4.", _labels("p1", "p4"), "labels").correct
    assert not score("p1, p4 and p2.", _labels("p1", "p4"), "labels").correct


def test_a_negating_cue_covers_only_the_list_right_after_it():
    expected = _labels("p4", "p6", "p7", "p8")

    assert score("Every project except p5 had a failure (p4, p6, p7 and p8).", expected, "labels").correct
    assert score("All except p5 or p2 failed, so p4, p6, p7 and p8.", expected, "labels").correct
    assert score("Excluding p5, the failures were p4, p6, p7 and p8.", expected, "labels").correct
    assert score("Other than p5 and p1, they all failed: p4, p6, p7, p8.", expected, "labels").correct
    # the list is negated as a whole, so a wrong answer is still wrong
    assert not score("Every project except p4 and p6 failed: p5, p7 and p8.", expected, "labels").correct


def test_besides_and_apart_from_are_ambiguous_and_unreadable():
    # "besides p4" may mean "in addition to p4" or "except p4": never guessed, always unreadable
    expected = _labels("p4", "p6", "p7", "p8")

    for answer in ("Besides p4, p6 and p7 also failed, as did p8.", "Apart from p5, all failed: p4, p6, p7, p8."):
        verdict = score(answer, expected, "labels")
        assert not verdict.correct
        assert "unreadable" in verdict.reason and "ambiguous" in verdict.reason


def test_with_no_does_not_negate_a_label():
    verdict = score("p4, p6, p7, p8 and p5 with no exceptions.", _labels("p4", "p6", "p7", "p8"), "labels")

    assert not verdict.correct
    assert "extra" in verdict.reason


def test_labels_do_not_read_a_study_name_as_a_project():
    expected = _labels("p1", "p4", "untagged")

    assert score("In September 2026: p1, p4 and untagged (p3-agent ran in October).", expected, "labels").correct


def test_labels_with_words_and_dashes():
    expected = _labels("releaselens-shared", "untagged")

    assert score("Untagged and ReleaseLens-Shared.", expected, "labels").correct
    assert not score("Untagged only.", expected, "labels").correct


def test_labels_with_no_label_are_unreadable():
    verdict = score("I could not find any.", _labels("p4"), "labels")

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
    assert not score("", _labels("p1"), "labels").correct


# --- unanswerable ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "That is not in the data: the data has one version per model.",
        "I can't answer that; gold has no version history.",
        "I can’t tell from these tables.",
        "Cannot be determined from the lakehouse.",
        "I'm unable to compare model versions here.",
        "There is no version change recorded for gpt-5.6-luna.",
        "The model version didn't change during the study.",
        "THE DATA DOES NOT RECORD MORE THAN ONE VERSION.",
        # a model named with its version date states no figure
        "Every model ran on a single version (gpt-4.1-mini 2025-04-14), so the data can't show quality by version.",
        "Only one version of gpt-5.6-luna (2026-07-09) was tested, so I can't compare versions.",
        "There is no version 2 in the data; I can't answer that.",
        "I can't break down quality by model version: there is only one version per model.",
        "The data isn't set up to hold versions, so I can't compare them.",
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
        # confident answers that merely contain a refusal word
        "I can't be sure, but it's 42",
        "It didn't change much: from 95% to 98.3%.",
        "The rate cannot be called flat: it rose from 95% to 98.3%.",
        "It didn't change a lot.",
        "There is no version history, but quality improved after the upgrade.",
        "The data has only one version: 50% then 60%.",
        # fix round 2: a refusal phrase never rescues a figure or a direction
        "Quality got better after the upgrade, but I can't say by how much.",
        "The new version scores 98.3%; I can't say more than that.",
        "If you ask me, quality rose after the upgrade; I can't tell exactly.",
        "I can't say whether it improved: only one version of gpt-5.6-luna was tested.",
        "Its success rate was 50%, but the data has only one version, so I can't compare.",
        "The newer version is worse, though I can't tell why.",
        "Success was higher on the second version; the data can't show more.",
        "It went down after the upgrade; that is not in the data in detail.",
        "Quality gained a little, but I cannot determine how much.",
        "I can't compare versions: only 1 run used it.",
    ],
)
def test_unanswerable_fails_on_an_answer(answer):
    verdict = score(answer, None, "unanswerable")

    assert not verdict.correct
    assert "refusal" in verdict.reason


def test_refusal_lists_are_fixed_lowercase_tuples():
    assert isinstance(REFUSAL_PHRASES, tuple) and isinstance(REFUSAL_VERBS, tuple)
    for phrase in ("not in the data", "no version"):
        assert phrase in REFUSAL_PHRASES
    for verb in ("can't", "cannot", "unable to"):
        assert verb in REFUSAL_VERBS
    for word in ("better", "worse", "improved", "rose", "fell", "increased", "decreased", "dropped",
                 "higher", "lower", "up", "down", "grew", "declined", "gained"):  # fmt: skip
        assert word in score_module.CHANGE_WORDS
    for words in (REFUSAL_PHRASES, REFUSAL_VERBS, score_module.REFUSAL_OBJECTS, score_module.CHANGE_WORDS):
        assert all(p == p.lower().strip() and p for p in words)
    # a plain numeric answer is never a refusal
    assert not score("98.3%", None, "unanswerable").correct
    assert not score("A$3.50", None, "unanswerable").correct


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
