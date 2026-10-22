from datetime import date, timedelta

import pytest

from lakehouse.attribution import (
    Attribution,
    RgRule,
    SessionWindow,
    attribute,
    load_rg_rules,
    load_session_windows,
    parse_tags,
)
from lakehouse.seeding import SEEDS_DIR

SEEDS = SEEDS_DIR
RULES = load_rg_rules(SEEDS)
WINDOWS = load_session_windows(SEEDS)


def test_parse_tags_formats():
    assert parse_tags('{"project":"ReleaseLens"}') == {"project": "ReleaseLens"}
    assert parse_tags('"project": "ReleaseLens","purpose": "x"') == {
        "project": "ReleaseLens",
        "purpose": "x",
    }
    assert parse_tags('{"Project": "a"}') == {"project": "a"}  # keys lower-cased
    assert parse_tags("") == {}
    assert parse_tags("   ") == {}
    assert parse_tags(None) == {}


def test_parse_tags_rejects_garbage_loudly():
    with pytest.raises(ValueError):
        parse_tags("not json at all")
    with pytest.raises(ValueError):
        parse_tags('["a", "b"]')


def test_seed_rules_load_with_shared_flag_and_globs():
    by_pattern = {r.pattern: r for r in RULES}
    assert by_pattern["rg-releaselens-bootstrap"].shared is True
    assert by_pattern["rg-releaselens-bootstrap"].project is None
    assert by_pattern["rg-releaselens-gateway"].project == "p4"
    assert by_pattern["rg-p7-*"].project == "p7"


def test_rg_rule_beats_tag():
    got = attribute("rg-releaselens-gateway", date(2026, 10, 8), {"project": "releaselens"}, RULES, WINDOWS)
    assert got == Attribution("p4", "rg_rule")


def test_rg_rule_exact_name_does_not_match_longer_names():
    # rg-releaselens is p1 only as an exact name; rg-releaselens-gateway is its own rule
    assert attribute("rg-releaselens", date(2026, 10, 1), {}, RULES, WINDOWS) == Attribution("p1", "rg_rule")
    got = attribute("rg-releaselens-other", date(2026, 10, 1), {}, RULES, WINDOWS)
    assert got == Attribution("untagged", "untagged")


def test_rg_rule_glob_is_case_insensitive():
    assert attribute("RG-FWA-Eval", date(2026, 10, 12), {}, RULES, WINDOWS) == Attribution("p3", "rg_rule")
    assert attribute("Rg-P7-agents", date(2026, 10, 15), {}, RULES, WINDOWS) == Attribution("p7", "rg_rule")


def test_p9_cost_export_group_is_attributed_to_p9():
    assert attribute("rg-p9-cost-export", date(2026, 10, 25), {}, RULES, WINDOWS) == Attribution("p9", "rg_rule")


def test_rg_rule_date_window():
    rules = [RgRule("rg-x", "p9", False, date(2026, 10, 5), date(2026, 10, 7))]
    assert attribute("rg-x", date(2026, 10, 5), {}, rules, []) == Attribution("p9", "rg_rule")
    assert attribute("rg-x", date(2026, 10, 7), {}, rules, []) == Attribution("p9", "rg_rule")
    assert attribute("rg-x", date(2026, 10, 4), {}, rules, []) == Attribution("untagged", "untagged")
    assert attribute("rg-x", date(2026, 10, 8), {}, rules, []) == Attribution("untagged", "untagged")


def test_shared_split_by_window():
    got = attribute("rg-releaselens-bootstrap", date(2026, 10, 3), {}, RULES, WINDOWS)
    assert got == Attribution("p2", "shared_window")
    got = attribute("rg-releaselens-bootstrap", date(2026, 10, 25), {}, RULES, WINDOWS)
    assert got == Attribution("releaselens-shared", "shared_default")
    got = attribute("rg-releaselens-search", date(2026, 10, 10), {}, RULES, WINDOWS)
    assert got == Attribution("p5", "shared_window")


def test_shared_group_ignores_tag():
    got = attribute(
        "rg-releaselens-bootstrap", date(2026, 10, 25), {"project": "foundry-workshop-agent"}, RULES, WINDOWS
    )
    assert got == Attribution("releaselens-shared", "shared_default")


def test_tag_case_insensitive():
    got = attribute("rg-unknown", date(2026, 10, 18), {"project": "ReleaseLens"}, RULES, WINDOWS)
    assert got == Attribution("releaselens-shared", "tag")
    got = attribute("rg-unknown", date(2026, 10, 18), {"project": "Foundry-Workshop-Agent"}, RULES, WINDOWS)
    assert got == Attribution("p3", "tag")


def test_untagged_fallback():
    untagged = Attribution("untagged", "untagged")
    assert attribute("rg-unknown", date(2026, 10, 18), {}, RULES, WINDOWS) == untagged
    assert attribute("rg-unknown", date(2026, 10, 18), {"project": "other"}, RULES, WINDOWS) == untagged
    assert attribute(None, date(2026, 10, 18), {}, RULES, WINDOWS) == untagged
    assert attribute("", date(2026, 10, 18), {}, RULES, WINDOWS) == untagged


def test_group_none_can_still_use_tag():
    got = attribute(None, date(2026, 10, 18), {"project": "releaselens"}, RULES, WINDOWS)
    assert got == Attribution("releaselens-shared", "tag")


def test_windows_do_not_overlap():
    windows = sorted(load_session_windows(SEEDS), key=lambda w: w.start_date)
    assert windows
    for w in windows:
        assert w.start_date <= w.end_date
    for earlier, later in zip(windows, windows[1:]):
        assert earlier.end_date < later.start_date, f"{earlier.project} overlaps {later.project}"


def test_every_row_has_evidence():
    assert all(w.evidence for w in WINDOWS)
    assert all(r.evidence for r in RULES)


def test_every_day_of_a_window_resolves_to_its_project():
    for w in WINDOWS:
        day = w.start_date
        while day <= w.end_date:
            got = attribute("rg-releaselens-bootstrap", day, {}, RULES, WINDOWS)
            assert got == Attribution(w.project, "shared_window")
            day += timedelta(days=1)


def test_session_window_dataclass_fields():
    w = SessionWindow("p1", date(2026, 1, 1), date(2026, 1, 2), "x")
    assert (w.project, w.start_date, w.end_date) == ("p1", date(2026, 1, 1), date(2026, 1, 2))
