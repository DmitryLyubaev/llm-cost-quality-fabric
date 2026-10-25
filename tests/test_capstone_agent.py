"""The data agent definition `fabric-capstone/da_portfolio.DataAgent/` (spec 4.2).

The example queries run on the fixture lake through Spark SQL, with a `gold` database registered
over the fixture's gold Delta tables, and must return what their paired answer function returns
(Review Focus 5). The definition must name only real tables and columns and hold no identifier.
"""

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from check_hygiene import check_hygiene
from lake_helpers import copy_lake
from lakehouse.capstone.expected import MAX_ROWS, Source, _select_rows, resolve_function
from lakehouse.capstone.snapshot import SNAPSHOT_TABLES
from lakehouse.gateway.build import FACT_GATEWAY_CALL_SCHEMA
from lakehouse.gateway.build import GOLD_TABLE as GATEWAY_GOLD_TABLE

ROOT = Path(__file__).resolve().parents[1]
CAPSTONE = ROOT / "fabric-capstone"
AGENT = CAPSTONE / "da_portfolio.DataAgent"
CONFIG = AGENT / "Files" / "Config"
DRAFT = CONFIG / "draft"
SOURCE_DIR = DRAFT / "lakehouse-tables-lh_capstone"
FEWSHOT_SOURCES = ROOT / "capstone" / "fewshots-sources.json"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PLATFORM_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"
)
NEAR_COPY = 0.8  # token Jaccard overlap at or above which an example question is a near-copy
FLOAT_TOLERANCE = 1e-9
INSTRUCTIONS_LIMIT = 15_000  # characters, for agent-level instructions (research file, section 2)
GATEWAY_SESSION = "gateway-demo"  # the example's session; the fixture lake has none, so the test adds it
BOOLEAN_COLUMNS = ("success", "top1", "must_contain", "refused_by_gateway", "is_current")
# Spark SQL and the SQL analytics endpoint's T-SQL differ; the examples keep to what both read.
NOT_PORTABLE = [
    (re.compile(r"\bLIMIT\b", re.IGNORECASE), "LIMIT (T-SQL has TOP)"),
    (re.compile(r"\bTOP\b", re.IGNORECASE), "TOP (Spark SQL has LIMIT)"),
    (re.compile(r"`"), "backtick quoting"),
    (re.compile(r'"'), "double quotes"),
    (re.compile(r"\|\|"), "|| concatenation"),
    (re.compile(r"::"), ":: casts"),
    (re.compile(r";"), "a statement separator"),
    (re.compile(r"\b(ILIKE|NVL|IIF|IF|QUALIFY|DATE_TRUNC|ISNULL|LEN|GETDATE)\s*\(", re.IGNORECASE), "a dialect function"),
    (re.compile(r"\b(TRUE|FALSE)\b", re.IGNORECASE), "a boolean literal (T-SQL has none)"),
    (re.compile(r"\bAS\s+(DOUBLE|REAL|DECIMAL|NUMERIC|STRING|VARCHAR|BOOLEAN)\b", re.IGNORECASE), "a dialect type"),
]
# Files the git form of a data agent holds (research file, section 3); `published/` is Fabric's.
AGENT_FILES = [
    ".platform",
    "Files/Config/data_agent.json",
    "Files/Config/draft/lakehouse-tables-lh_capstone/datasource.json",
    "Files/Config/draft/lakehouse-tables-lh_capstone/fewshots.json",
    "Files/Config/draft/stage_config.json",
]


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _datasource() -> dict:
    return _json(SOURCE_DIR / "datasource.json")


def _fewshots() -> list[dict]:
    return _json(SOURCE_DIR / "fewshots.json")["fewShots"]


# Read once at collection, so each example is its own test case (empty until the file exists).
_SHOTS = _fewshots() if (SOURCE_DIR / "fewshots.json").is_file() else []


def _sources() -> dict[str, Source]:
    return {entry["id"]: Source.from_dict(entry["source"]) for entry in _json(FEWSHOT_SOURCES)}


def _ai_instructions() -> str:
    return _json(DRAFT / "stage_config.json")["aiInstructions"]


def _instruction_texts() -> dict[str, str]:
    source = _datasource()
    return {
        "aiInstructions": _ai_instructions(),
        "dataSourceInstructions": source["dataSourceInstructions"],
        "userDescription": source["userDescription"],
    }


def _fewshot_ids() -> list[str]:
    return [shot["id"] for shot in _fewshots()]


# --- the files -------------------------------------------------------------------------------------


def test_the_definition_holds_exactly_the_git_files():
    files = sorted(p.relative_to(AGENT).as_posix() for p in AGENT.rglob("*") if p.is_file())
    assert files == sorted(AGENT_FILES)
    assert not any(p.is_symlink() for p in AGENT.rglob("*"))  # the freeze rejects links


def test_platform_names_the_data_agent():
    platform = _json(AGENT / ".platform")
    assert platform["$schema"] == PLATFORM_SCHEMA
    assert platform["metadata"] == {"type": "DataAgent", "displayName": "da_portfolio"}
    assert platform["config"]["version"] == "2.0"
    assert GUID.fullmatch(platform["config"]["logicalId"])
    assert platform["config"]["logicalId"] != EMPTY_GUID


def test_data_agent_and_stage_config_follow_the_definition_format():
    assert _json(CONFIG / "data_agent.json") == {"$schema": "2.1.0"}
    stage = _json(DRAFT / "stage_config.json")
    assert set(stage) == {"$schema", "aiInstructions"}
    assert stage["$schema"] == "1.0.0"
    assert 0 < len(stage["aiInstructions"]) <= INSTRUCTIONS_LIMIT


def test_datasource_selects_the_nine_gold_tables_of_lh_capstone():
    source = _datasource()
    assert source["type"] == "lakehouse_tables"
    assert source["displayName"] == "lh_capstone"
    assert source["workspaceId"] == EMPTY_GUID
    assert source["artifactId"] == EMPTY_GUID
    schemas = source["elements"]
    assert [(s["display_name"], s["type"], s["is_selected"]) for s in schemas] == [
        ("gold", "lakehouse_tables.schema", True)
    ]
    tables = schemas[0]["children"]
    assert all(t["type"] == "lakehouse_tables.table" and t["is_selected"] is True for t in tables)
    assert all(t["children"] == [] for t in tables)  # column-level selection is ignored
    names = [t["display_name"] for t in tables]
    assert sorted(names) == sorted(SNAPSHOT_TABLES)
    assert len(names) == len(set(names)) == 9


def test_datasource_holds_only_the_empty_guid():
    text = (SOURCE_DIR / "datasource.json").read_text(encoding="utf-8")
    assert set(GUID.findall(text)) == {EMPTY_GUID}


def test_the_definition_holds_no_url_address_or_foreign_id():
    platform_id = _json(AGENT / ".platform")["config"]["logicalId"]
    allowed_ids = {EMPTY_GUID, platform_id} | set(_fewshot_ids())
    for path in (p for p in AGENT.rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8")
        if path.name == ".platform":
            text = text.replace(PLATFORM_SCHEMA, "")  # the one public schema URL every item carries
        rel = path.relative_to(AGENT).as_posix()
        assert "@" not in text, rel
        assert not re.search(r"https?://|abfss://|onelake|\.(com|net|io)\b", text, re.IGNORECASE), rel
        assert set(GUID.findall(text)) <= allowed_ids, rel


def test_capstone_files_pass_hygiene_with_lf_endings():
    files = [p for p in CAPSTONE.rglob("*") if p.is_file()] + [FEWSHOT_SOURCES]
    assert check_hygiene(files, forbidden=[]) == []
    for path in files:
        raw = path.read_bytes()
        assert b"\r" not in raw, path
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert raw.endswith(b"\n"), path


# --- the example queries: shape ----------------------------------------------------------------------


def test_about_twenty_examples_each_with_a_guid_a_question_and_a_query():
    shots = _fewshots()
    assert _json(SOURCE_DIR / "fewshots.json")["$schema"] == "1.0.0"
    assert 18 <= len(shots) <= 25
    for shot in shots:
        assert set(shot) == {"id", "question", "query"}
        assert GUID.fullmatch(shot["id"]) and shot["id"] != EMPTY_GUID
        assert shot["question"].strip() == shot["question"] and shot["question"].endswith("?")
        assert shot["query"].strip().upper().startswith(("SELECT", "WITH", "--"))
    ids = [shot["id"] for shot in shots]
    assert len(ids) == len(set(ids))
    questions = [shot["question"].lower() for shot in shots]
    assert len(questions) == len(set(questions))


def test_every_example_has_exactly_one_paired_source():
    entries = _json(FEWSHOT_SOURCES)
    assert [e["id"] for e in entries] == [shot["id"] for shot in _fewshots()]
    for entry in entries:
        assert set(entry) == {"id", "source"}
        source = Source.from_dict(entry["source"])
        resolve_function(source.function)


@pytest.mark.parametrize("shot", _SHOTS, ids=lambda s: s["id"][:8])
def test_example_query_is_gold_qualified_and_portable(shot):
    query = shot["query"]
    assert re.search(r"\bgold\.\w+", query)
    for name in re.findall(r"\bgold\.(\w+)", query):
        assert name in SNAPSHOT_TABLES, name
    for table in SNAPSHOT_TABLES:
        for match in re.finditer(rf"\b{table}\b", query):
            assert query[: match.start()].endswith("gold."), f"{table} is not gold-qualified"
    code = re.sub(r"--[^\n]*", "", query)  # comments may say anything
    for pattern, what in NOT_PORTABLE:
        assert not pattern.search(code), what
    # a bare bit column is not a predicate in T-SQL, and a bit is not a boolean in Spark: CAST it
    for match in re.finditer(rf"(?:\w+\.)?\b({'|'.join(BOOLEAN_COLUMNS)})\b", code):
        assert re.search(r"CAST\(\s*$", code[: match.start()], re.IGNORECASE), match.group(0)


def _tokens(text: str) -> set[str]:
    return set(re.sub(r"[^\w\s]", "", text.lower()).split())


def _overlap(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    return len(x & y) / len(x | y)


def test_overlap_measure():
    assert _overlap("How many live checks failed for p7?", "how many LIVE checks failed, for p7") == 1.0
    assert _overlap("a b", "c d") == 0.0
    assert _overlap("What is p2's top-1 rate?", "what is p2s top1 rate") == 1.0


@pytest.mark.parametrize("name", ["questions.json", "practice.json"])
def test_no_example_question_is_a_frozen_or_practice_question_or_a_near_copy(name):
    others = [q["text"] for q in _json(ROOT / "capstone" / name)]
    for shot in _fewshots():
        for other in others:
            assert shot["question"].lower() != other.lower(), other
            overlap = _overlap(shot["question"], other)
            assert overlap < NEAR_COPY, (shot["question"], other, round(overlap, 3))


# --- the example queries: results on the fixture lake ----------------------------------------------------


def _add_gateway_session(spark, paths):
    """Gateway calls for the example's session on two days the fixture has gateway cost lines."""
    rows = []
    for i, (day, refused) in enumerate([("2026-09-29", False), ("2026-09-29", True), ("2026-10-08", False)]):
        call_day = date.fromisoformat(day)
        rows.append(
            {
                "event_time": datetime(call_day.year, call_day.month, call_day.day, 1, i),
                "request_id": f"req-{i}",
                "session": GATEWAY_SESSION,
                "api": "chat",
                "operation": "completions",
                "caller": "demo",
                "answered_by": None if refused else "primary",
                "status": 429 if refused else 200,
                "refused_by_gateway": refused,
                "tokens": None if refused else 100 + i,
                "latency_ms": 40 + i,
                "call_date": call_day,
                "project": "p4",
            }
        )
    frame = spark.createDataFrame([tuple(r[f.name] for f in FACT_GATEWAY_CALL_SCHEMA) for r in rows], FACT_GATEWAY_CALL_SCHEMA)
    frame.write.format("delta").mode("append").save(paths.table("gold", GATEWAY_GOLD_TABLE))


@pytest.fixture(scope="module")
def gold_db(spark, loaded_lake, tmp_path_factory):
    """A copy of the fixture lake with a gateway session, and a Spark database `gold` over its tables."""
    root = tmp_path_factory.mktemp("agent")
    paths = copy_lake(loaded_lake[0], root / "lake")
    _add_gateway_session(spark, paths)
    spark.sql("DROP DATABASE IF EXISTS gold CASCADE")
    spark.sql(f"CREATE DATABASE gold LOCATION '{(root / 'db').as_posix()}'")
    for table in SNAPSHOT_TABLES:
        spark.sql(f"CREATE TABLE gold.{table} USING DELTA LOCATION '{paths.table('gold', table)}'")
    yield paths
    spark.sql("DROP DATABASE IF EXISTS gold CASCADE")  # external tables: the files stay


def _cell(value):
    """A result cell as plain Python: Decimal as float, int as float (a count is a count in both)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Decimal | int):
        return float(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _sort_key(row: tuple) -> tuple:
    return tuple(
        (0, "") if v is None else (1, round(v, 6)) if isinstance(v, float) else (2, str(v)) for v in row
    )


def _as_set(rows: list[tuple]) -> list[tuple]:
    """The distinct rows, in a stable order; floats that agree to 1e-9 are one value."""
    unique: list[tuple] = []
    for row in sorted(rows, key=_sort_key):
        if not (unique and _same(unique[-1], row)):
            unique.append(row)
    return unique


def _same(a: tuple, b: tuple) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if isinstance(x, float) and isinstance(y, float):
            if abs(x - y) > FLOAT_TOLERANCE:
                return False
        elif x != y:
            return False
    return True


def test_row_comparison():
    assert _same((1.0, "a"), (1.0 + 1e-12, "a"))
    assert not _same((1.0, "a"), (1.0 + 1e-6, "a"))
    assert not _same((None,), (0.0,))
    assert _as_set([(2.0,), (1.0,), (1.0 + 1e-12,)]) == [(1.0,), (2.0,)]
    assert _cell(Decimal("0.5")) == 0.5 and _cell(3) == 3.0 and _cell(True) is True


def _query_rows(spark, query: str) -> tuple[list[str], list[tuple]]:
    frame = spark.sql(query)
    rows = frame.limit(MAX_ROWS + 1).collect()
    return [c.lower() for c in frame.columns], [tuple(_cell(v) for v in row) for row in rows]


def _source_rows(spark, paths, shot_id: str, source: Source, columns: list[str]) -> list[tuple]:
    frame = resolve_function(source.function)(spark, paths, **source.args)
    missing = [c for c in columns if c not in frame.columns]
    assert not missing, f"the query's columns {missing} are not in {source.function}'s {frame.columns}"
    if source.column is not None:
        assert source.column in columns, f"the query does not return {source.column!r}"
    rows = _select_rows(frame, source, shot_id)  # ValueError on no row or more than 25
    return [tuple(_cell(row[c]) for c in columns) for row in rows]


@pytest.mark.parametrize("shot", _SHOTS, ids=lambda s: s["id"][:8])
def test_example_query_returns_what_its_answer_function_returns(spark, gold_db, shot):
    source = _sources()[shot["id"]]

    columns, got = _query_rows(spark, shot["query"])

    assert len(got) <= MAX_ROWS, "the data agent caps an answer at 25 rows"
    assert got, "the query returns no row on the fixture, so it proves nothing"
    assert len(columns) == len(set(columns))
    want = _source_rows(spark, gold_db, shot["id"], source, columns)
    assert len(_as_set(got)) == len(got), "the query returns a row twice"
    got_set, want_set = _as_set(got), _as_set(want)
    assert len(got_set) == len(want_set), (got_set, want_set)
    assert all(_same(a, b) for a, b in zip(got_set, want_set)), (got_set, want_set)


def test_the_gateway_example_reads_the_added_session(spark, gold_db):
    gateway = [s for s in _fewshots() if "gold.fact_gateway_call" in s["query"]]
    assert len(gateway) == 1
    assert f"'{GATEWAY_SESSION}'" in gateway[0]["query"]
    assert _sources()[gateway[0]["id"]].args == {"session": GATEWAY_SESSION}


# --- the instructions ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gold_columns(spark, loaded_lake) -> dict[str, set[str]]:
    paths = loaded_lake[0]
    return {t: set(spark.read.format("delta").load(paths.table("gold", t)).columns) for t in SNAPSHOT_TABLES}


def _unknown_names(text: str, columns: dict[str, set[str]]) -> list[str]:
    """Every table or column the text names that gold does not have."""
    every_column = set().union(*columns.values())
    unknown = []
    for name in re.findall(r"`([^`]+)`", text):
        if name == "gold":  # the schema
            continue
        parts = name.split(".")
        if parts[0] == "gold":
            parts = parts[1:]
            if len(parts) != 1 or parts[0] not in columns:
                unknown.append(name)
        elif len(parts) == 2:
            if parts[0] not in columns or parts[1] not in columns[parts[0]]:
                unknown.append(name)
        elif len(parts) != 1 or (name not in columns and name not in every_column):
            unknown.append(name)
    # Outside backticks: `gold.<table>`, then `<table>.<column>`, then a bare fact_ or dim_ name,
    # each removed once checked so a name is reported once.
    plain = re.sub(r"`[^`]*`", " ", text)

    def check_schema(match):
        if match.group(1) not in columns:
            unknown.append(match.group(0))
        return " "

    def check_column(match):
        table, column = match.group(1), match.group(2)
        if table not in columns or column not in columns[table]:
            unknown.append(match.group(0))
        return " "

    plain = re.sub(r"\bgold\.(\w+)", check_schema, plain)
    plain = re.sub(r"\b((?:fact|dim)_\w+)\.(\w+)", check_column, plain)
    unknown.extend(table for table in re.findall(r"\b(?:fact|dim)_\w+", plain) if table not in columns)
    return unknown


def test_name_check_catches_made_up_names():
    columns = {"fact_daily_cost": {"cost", "project"}, "dim_project": {"project", "name"}}
    good = "`gold.fact_daily_cost`, `fact_daily_cost.cost`, `name`, dim_project.project and `gold`"
    assert _unknown_names(good, columns) == []
    assert _unknown_names("`fact_daily_cost.amount`", columns) == ["fact_daily_cost.amount"]
    assert _unknown_names("`currency`", columns) == ["currency"]
    assert _unknown_names("see fact_cost", columns) == ["fact_cost"]
    assert _unknown_names("gold.fact_costs", columns) == ["gold.fact_costs"]
    assert _unknown_names("dim_project.title", columns) == ["dim_project.title"]


@pytest.mark.parametrize("field", ["aiInstructions", "dataSourceInstructions", "userDescription"])
def test_instructions_name_only_real_gold_tables_and_columns(gold_columns, field):
    text = _instruction_texts()[field]
    assert text.strip()
    assert _unknown_names(text, gold_columns) == []


def test_data_source_instructions_describe_every_table():
    text = _datasource()["dataSourceInstructions"]
    for heading in ("## General knowledge", "## Table descriptions", "## When asked about"):
        assert heading in text
    for table in SNAPSHOT_TABLES:
        assert f"`gold.{table}`" in text, table
    assert len(text) <= INSTRUCTIONS_LIMIT


def test_ai_instructions_follow_the_template_and_state_the_portfolio_rules():
    text = _ai_instructions()
    for heading in ("## Objective", "## Data sources", "## Key terminology", "## Response guidelines", "## Handling common topics"):
        assert heading in text
    flat = " ".join(text.lower().split())
    # spec 4.2: the portfolio's rules
    assert "costs stay in their billing currency" in flat
    assert "never convert" in flat
    assert "estimated and actual costs are never summed" in flat
    assert '"untagged" and "releaselens-shared" are projects' in flat
    assert "the model version did not change between measured runs" in flat
    # figures the scorer can read, and refusals it scores as refusals (ruling R5)
    assert "digits" in flat and "currency code" in flat
    assert "give no figure" in flat
    assert "25 rows" in flat
