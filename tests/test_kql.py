"""Text-level checks on the KQL (no Kusto is available offline): the table, its mapping, the shared
function and the published queries are read as text and the rules the design sets are asserted."""
import json
import re
from pathlib import Path

import pytest

from lakehouse.gateway.contract import EVENT_FIELDS

KQL_DIR = Path(__file__).resolve().parents[1] / "fabric" / "kql"
QUERIES_DIR = KQL_DIR / "queries"
PUBLISHED = KQL_DIR / "PUBLISHED.md"
TABLE_FILE = KQL_DIR / "gateway_calls.kql"

FIGURE_IDS = [
    "delay_p50_p95_max",
    "refusals_per_minute",
    "answered_by_per_minute",
    "tokens_per_minute_per_caller",
    "session_counts",
    "failover_minutes",
    "token_spike_minutes",
]
PER_MINUTE = [
    "refusals_per_minute",
    "answered_by_per_minute",
    "tokens_per_minute_per_caller",
    "failover_minutes",
    "token_spike_minutes",
]


def _code(path: Path) -> str:
    """The file's text without `//` comment lines (comments may mention what the code must not do)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("//"))


def _table_columns() -> list[tuple[str, str]]:
    match = re.search(r"\.create-merge\s+table\s+gateway_calls\s*\((.*?)\)", _code(TABLE_FILE), re.DOTALL)
    assert match, "no .create-merge table gateway_calls command"
    columns = []
    for part in match.group(1).split(","):
        name, kind = part.strip().split(":")
        columns.append((name.strip(), kind.strip()))
    return columns


def _function_body() -> str:
    match = re.search(r"\.create-or-alter\s+function\b[^{]*\bGatewayCalls\(\)\s*\{(.*?)\n\}", _code(TABLE_FILE), re.DOTALL)
    assert match, "no .create-or-alter function GatewayCalls()"
    return match.group(1)


def _mapping() -> list[dict]:
    match = re.search(
        r"ingestion\s+json\s+mapping\s+\"gateway_calls_json\"\s+'(.*?)'", _code(TABLE_FILE), re.DOTALL
    )
    assert match, "no json mapping named gateway_calls_json"
    return json.loads(match.group(1))  # the documented form: {"column", "datatype", "Properties": {"Path"}}


def test_table_columns_equal_event_fields():
    assert _table_columns() == EVENT_FIELDS


def test_table_command_creates_gateway_calls_only():
    code = _code(TABLE_FILE)
    assert set(re.findall(r"\.create(?:-merge|-or-alter)?\s+table\s+(\w+)", code)) == {"gateway_calls"}
    assert ".create-merge table" in code


def test_mapping_is_json_paths_for_every_field_in_order():
    mapping = _mapping()
    assert [(m["column"], m["datatype"]) for m in mapping] == EVENT_FIELDS
    assert [m["Properties"]["Path"] for m in mapping] == [f"$.{name}" for name, _ in EVENT_FIELDS]
    assert all(set(m) == {"column", "datatype", "Properties"} for m in mapping)
    assert ".create-or-alter table gateway_calls ingestion json mapping" in _code(TABLE_FILE)


def test_function_filters_replay_and_dedups_on_ingestion_time():
    body = _function_body()
    assert 'session != "replay"' in body
    assert re.search(r"summarize\s+arg_max\(\s*ingested\s*,\s*\*\s*\)[^|]*\bby\s+request_id", body)
    assert re.search(r"extend\s+ingested\s*=\s*ingestion_time\(\)", body)
    assert body.index("ingestion_time()") < body.index("arg_max")


def test_function_filters_replay_before_the_dedup():
    body = _function_body()
    assert body.index('session != "replay"') < body.index("arg_max")


def test_function_keeps_first_availability():
    body = _function_body()
    assert re.search(r"first_ingested\s*=\s*min\(ingested\)", body)
    assert re.search(r"summarize\s+arg_max\(ingested,\s*\*\)\s*,\s*first_ingested\s*=\s*min\(ingested\)\s+by\s+request_id", body)


def test_function_returns_event_fields_then_ingested_then_first_ingested():
    body = _function_body()
    match = re.search(r"\|\s*project\s+(.*)$", body.strip(), re.DOTALL)
    assert match, "the function must end with a project that fixes the column order"
    projected = [c.strip() for c in match.group(1).split(",")]
    assert projected == [name for name, _ in EVENT_FIELDS] + ["ingested", "first_ingested"]


def test_published_ids_match_files_and_table():
    files = sorted(p.stem for p in QUERIES_DIR.glob("*.kql"))
    assert files == sorted(FIGURE_IDS)

    rows = {}
    for line in PUBLISHED.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 3 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = (cells[1], cells[2].strip("`"))
    assert sorted(rows) == sorted(FIGURE_IDS)
    for figure_id, (measures, file) in rows.items():
        assert measures
        assert file == f"queries/{figure_id}.kql"
        assert (KQL_DIR / file).is_file()


@pytest.mark.parametrize("figure_id", FIGURE_IDS)
def test_query_starts_from_gateway_calls(figure_id):
    code = _code(QUERIES_DIR / f"{figure_id}.kql")
    assert "GatewayCalls()" in code
    assert not re.search(r"\bgateway_calls\b", code), "queries read the function, never the raw table"
    statements = [line.strip() for line in code.splitlines() if line.strip()]
    first = statements[1] if statements[0].startswith("let ") else statements[0]
    assert first == "GatewayCalls()"


@pytest.mark.parametrize("figure_id", PER_MINUTE)
def test_per_minute_queries_bin_event_time(figure_id):
    assert "bin(event_time, 1m)" in _code(QUERIES_DIR / f"{figure_id}.kql")


def test_refusals_count_the_refused_flag_only():
    code = _code(QUERIES_DIR / "refusals_per_minute.kql")
    assert "refused_by_gateway == true" in code
    assert "answered_by" not in code


def test_answered_by_per_minute_leaves_out_calls_with_no_backend():
    assert "isnotempty(answered_by)" in _code(QUERIES_DIR / "answered_by_per_minute.kql")


def test_failover_minutes_are_secondary_answers():
    code = _code(QUERIES_DIR / "failover_minutes.kql")
    assert 'answered_by == "secondary"' in code


def test_token_spike_threshold_is_a_let_at_the_top():
    code = _code(QUERIES_DIR / "token_spike_minutes.kql")
    assert code.strip().splitlines()[0] == "let threshold = 5000;"
    assert re.search(r"sum\(tokens\)", code)
    assert re.search(r">\s*threshold\b", code)
    assert re.search(r"by\s+minute\s*=\s*bin\(event_time, 1m\)\s*,\s*caller|by\s+caller\s*,\s*minute", code)


def test_delay_query_measures_ingestion_minus_event_time_in_seconds():
    code = _code(QUERIES_DIR / "delay_p50_p95_max.kql")
    assert re.search(r"\(first_ingested\s*-\s*event_time\)\s*/\s*1s", code)
    assert not re.search(r"\(ingested\s*-", code), "the delay is to first availability, not the last duplicate"
    assert "percentile(" in code and ", 50)" in code and ", 95)" in code
    assert "max(" in code and "n = count()" in code


def test_session_counts_are_distinct_requests_by_session_and_caller():
    code = _code(QUERIES_DIR / "session_counts.kql")
    assert re.search(r"calls\s*=\s*count\(\)", code), "GatewayCalls() is one row per request_id, so count() is exact"
    assert "dcount" not in code
    assert re.search(r"by\s+session\s*,\s*caller", code)


def test_no_guid_or_url_in_any_kql_or_published_file():
    guid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
    files = [TABLE_FILE, PUBLISHED, *QUERIES_DIR.glob("*.kql")]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not guid.search(text), path.name
        assert not re.search(r"https?://|\.kusto\.|\.servicebus\.", text, re.IGNORECASE), path.name


def test_published_notes_estimates_and_clock_skew():
    text = PUBLISHED.read_text(encoding="utf-8")
    assert "estimated, using T-Digest" in text
    assert "max (exact)" in text
    assert "negative" in text and "first_ingested" in text
    # the delay query reports p50, p95, max and n: the skew note must not mention a minimum it lacks
    assert "negative minimum" not in text
    assert "min(" not in (QUERIES_DIR / "delay_p50_p95_max.kql").read_text(encoding="utf-8")


def test_comment_blocks_are_separated_from_the_next_command():
    lines = TABLE_FILE.read_text(encoding="utf-8").splitlines()
    for before, after in zip(lines, lines[1:]):
        assert not (before.startswith("//") and after.startswith(".")), after[:40]


def test_files_use_lf_endings():
    for path in [TABLE_FILE, PUBLISHED, *QUERIES_DIR.glob("*.kql")]:
        assert b"\r" not in path.read_bytes(), path.name
