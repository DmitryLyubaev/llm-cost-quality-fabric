"""Checks on the semantic model `sm_portfolio`: its TMDL under fabric/sm_portfolio.SemanticModel.

The model is Direct Lake on OneLake over `lh_portfolio`'s `gold` and `security` schemas (spec 4.1),
with one row-level security role (spec 4.2). The tests read the TMDL with `tmdl_reader` and compare
it with what a real load writes (`loaded_lake`), so a gold column renamed or retyped by a later
change to project 9 fails here instead of drifting in silence (Review Focus 1).
"""
import importlib
import json
import re
from pathlib import Path

import pytest
from pyspark.sql import functions as F
from pyspark.sql import types as T

from lakehouse import answers
from lakehouse.gateway.answers import GATEWAY_RESOURCE_GROUP
from lakehouse.gold.build import GOLD_TABLES, SESSION_TABLES
from lakehouse.security import USER_PROJECT_LAYER, USER_PROJECT_TABLE
from tmdl_reader import Node, parse_file

ROOT = Path(__file__).resolve().parents[1]
FABRIC = ROOT / "fabric"
MODEL = FABRIC / "sm_portfolio.SemanticModel"
DEFINITION = MODEL / "definition"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ONELAKE = "https://onelake.dfs.fabric.microsoft.com/"
EXPRESSION = "DirectLake - lh_portfolio"
# Plan 2 commits dev's real workspace and lakehouse IDs; until then both are the empty GUID.
SOURCE = (
    f'AzureStorage.DataLake("{ONELAKE}{EMPTY_GUID}/{EMPTY_GUID}", [HierarchicalNavigation=true])'
)
PBISM_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json"
)
MODEL_TABLES = set(GOLD_TABLES) | set(SESSION_TABLES) | {USER_PROJECT_TABLE}

# Spark type -> TMDL dataType (the brief's mapping). An unlisted type is a KeyError, so a new
# gold type fails the column test until the mapping is decided.
TMDL_TYPES = {
    T.StringType: "string",
    T.IntegerType: "int64",
    T.LongType: "int64",
    T.DoubleType: "double",
    T.BooleanType: "boolean",
    T.DateType: "dateTime",
    T.TimestampType: "dateTime",
    T.DecimalType: "decimal",
}

# Relationships, all many-to-one and single-direction (from the fact to the dimension).
DATE_COLUMNS = {
    "fact_daily_cost": "cost_date",
    "fact_eval_attempt": "started_date",
    "fact_live_check": "session_date",
    "fact_gateway_call": "call_date",
}
RELATIONSHIPS = (
    {(f"{fact}.{column}", "dim_date.date") for fact, column in DATE_COLUMNS.items()}
    | {(f"{fact}.project", "dim_project.project") for fact in DATE_COLUMNS}
    | {
        ("fact_eval_attempt.study_key", "dim_study.study_key"),
        ("fact_eval_attempt.arm_key", "dim_arm.arm_key"),
        ("fact_eval_attempt.deployment_sk", "dim_model_deployment.deployment_sk"),
    }
)

ROLE = "ProjectLead"
USER_FILTER = "[upn] = USERPRINCIPALNAME()"
PROJECT_FILTER = (
    "[project] IN CALCULATETABLE(VALUES(user_project[project]), user_project[upn] = USERPRINCIPALNAME())"
)

SPEND, QUALITY, GATEWAY = "Spend", "Quality", "Gateway"
COST_PER_PROJECT = "answers.cost_per_project"
QUALITY_ACROSS_MODELS = "answers.quality_across_models"
COST_PER_RESULT = "answers.cost_per_result"
COST_PER_GATEWAY_CALL = "gateway.answers.cost_per_gateway_call"
# Quality's mean measures: measure -> (quality_across_models column, fact_eval_attempt column).
# `Groundedness (mean)` and `Tokens (total)` are qualified because a measure may not share its
# name with any column in the model, and DAX names are case-insensitive (`groundedness`, `tokens`).
QUALITY_METRICS = {
    "Latency (ms)": ("latency_ms", "latency_ms"),
    "Groundedness (mean)": ("groundedness", "groundedness"),
    "Citation recall": ("citation_recall", "citation_recall"),
    "Citation precision": ("citation_precision", "citation_precision"),
    "Must-contain rate": ("must_contain_rate", "must_contain"),
    "Top-1 rate": ("top1_rate", "top1"),
    "MRR": ("mrr", "reciprocal_rank"),
    "Tool-call accuracy": ("tool_call_accuracy", "tool_call_accuracy"),
    "Task adherence": ("task_adherence", "task_adherence"),
    "Intent resolution": ("intent_resolution", "intent_resolution"),
}
# measure -> (table, display folder, the gold answer it mirrors), spec 4.1 and ruling R1.
MEASURES = {
    "Cost (AUD)": ("fact_daily_cost", SPEND, COST_PER_PROJECT),
    "Untagged cost": ("fact_daily_cost", SPEND, COST_PER_PROJECT),
    "Results (n)": ("fact_eval_attempt", QUALITY, QUALITY_ACROSS_MODELS),
    "Success rate": ("fact_eval_attempt", QUALITY, QUALITY_ACROSS_MODELS),
    **{name: ("fact_eval_attempt", QUALITY, QUALITY_ACROSS_MODELS) for name in QUALITY_METRICS},
    "Estimated cost per result (USD)": ("fact_eval_attempt", QUALITY, COST_PER_RESULT),
    "Estimated cost per result (AUD)": ("fact_eval_attempt", QUALITY, COST_PER_RESULT),
    "Calls": ("fact_gateway_call", GATEWAY, COST_PER_GATEWAY_CALL),
    "Refused by gateway": ("fact_gateway_call", GATEWAY, COST_PER_GATEWAY_CALL),
    "Tokens (total)": ("fact_gateway_call", GATEWAY, COST_PER_GATEWAY_CALL),
    "Tokens per call": ("fact_gateway_call", GATEWAY, COST_PER_GATEWAY_CALL),
    "Cost per 1,000 calls": ("fact_gateway_call", GATEWAY, COST_PER_GATEWAY_CALL),
}
SCORED_FILTER = f'KEEPFILTERS(fact_eval_attempt[attempt_kind] = "{answers.SCORED}")'


def _tables() -> dict[str, Node]:
    tables = {}
    for path in sorted((DEFINITION / "tables").glob("*.tmdl")):
        nodes = parse_file(path)
        assert [n.keyword for n in nodes] == ["table"], path
        assert nodes[0].name == path.stem, path  # one file per table, named after it
        tables[nodes[0].name] = nodes[0]
    assert tables, DEFINITION / "tables"
    return tables


def _columns(table: Node) -> dict[str, Node]:
    return {column.name: column for column in table.all("column")}


def _measures() -> dict[str, tuple[str, Node]]:
    """{measure name: (table name, node)} over the whole model."""
    found = {}
    for table_name, table in _tables().items():
        for measure in table.all("measure"):
            assert measure.name not in found, measure.name
            found[measure.name] = (table_name, measure)
    return found


def _relationships() -> list[Node]:
    nodes = parse_file(DEFINITION / "relationships.tmdl")
    assert {n.keyword for n in nodes} == {"relationship"}
    return nodes


def _role() -> Node:
    nodes = parse_file(DEFINITION / "roles" / f"{ROLE}.tmdl")
    assert len(nodes) == 1 and nodes[0].keyword == "role"
    return nodes[0]


def _model_files() -> list[Path]:
    files = sorted(p for p in MODEL.rglob("*") if p.is_file())
    assert files, MODEL
    return files


def _layer(table: str) -> str:
    return USER_PROJECT_LAYER if table == USER_PROJECT_TABLE else "gold"


# --- Item files ----------------------------------------------------------------------------------


def test_item_has_the_documented_definition_parts():
    platform = json.loads((MODEL / ".platform").read_text(encoding="utf-8"))
    assert platform["metadata"] == {"type": "SemanticModel", "displayName": "sm_portfolio"}

    pbism = json.loads((MODEL / "definition.pbism").read_text(encoding="utf-8"))
    assert pbism["$schema"] == PBISM_SCHEMA
    assert pbism["version"] == "5.0"

    database = parse_file(DEFINITION / "database.tmdl")
    assert [n.keyword for n in database] == ["database"]
    assert int(database[0].properties["compatibilityLevel"]) >= 1604  # Direct Lake's minimum

    model = parse_file(DEFINITION / "model.tmdl")
    assert [n.keyword for n in model] == ["model"]
    assert model[0].properties["culture"] == "en-AU"
    assert model[0].properties.get("discourageImplicitMeasures") is True


def test_model_files_use_lf_line_endings():
    for path in _model_files():
        assert b"\r\n" not in path.read_bytes(), path


# --- Tables, columns and partitions --------------------------------------------------------------


def test_model_tables_match_gold():
    assert set(_tables()) == MODEL_TABLES


def test_model_columns_match_a_real_load(spark, loaded_lake):
    """Every gold column is in the model with its mapped type, and the model has no other column."""
    paths, _, _ = loaded_lake
    for name, table in _tables().items():
        schema = spark.read.format("delta").load(paths.table(_layer(name), name)).schema
        expected = {f.name: TMDL_TYPES[type(f.dataType)] for f in schema.fields}
        columns = _columns(table)
        assert {c.name: c.properties.get("sourceColumn") for c in columns.values()} == {
            c: c for c in columns
        }, name  # each column is named after its gold column
        actual = {c.properties["sourceColumn"]: c.properties.get("dataType") for c in columns.values()}
        assert actual == expected, name


def test_keys_and_the_access_table_are_hidden():
    for name, table in _tables().items():
        assert (table.properties.get("isHidden") is True) == (name == USER_PROJECT_TABLE), name
        if name == USER_PROJECT_TABLE:
            continue
        for column in table.all("column"):
            is_key = column.name.endswith(("_key", "_sk"))
            assert (column.properties.get("isHidden") is True) == is_key, f"{name}.{column.name}"


def test_model_has_no_calculated_columns_or_tables():
    for name, table in _tables().items():
        for column in table.all("column"):
            assert column.expression is None, f"{name}.{column.name}"
        for partition in table.all("partition"):
            assert partition.expression == "entity", name


def test_every_partition_is_direct_lake_on_onelake():
    expressions = parse_file(DEFINITION / "expressions.tmdl")
    assert [(n.keyword, n.name) for n in expressions] == [("expression", EXPRESSION)]
    body = expressions[0].expression
    assert re.fullmatch(
        r"let\n\s+Source = (.+)\nin\n\s+Source", body
    ), body
    assert re.search(r"Source = (.+)\n", body).group(1) == SOURCE

    for name, table in _tables().items():
        assert table.properties.get("sourceLineageTag") == f"[{_layer(name)}].[{name}]", name
        partition = table.one("partition")
        assert partition.name == name
        assert partition.expression == "entity"
        assert partition.properties["mode"] == "directLake"
        source = partition.one("source")
        assert source.properties == {
            "entityName": name,
            "schemaName": _layer(name),
            "expressionSource": f"'{EXPRESSION}'",
        }, name


# --- Relationships -------------------------------------------------------------------------------


def test_relationships_are_many_to_one_and_single_direction():
    found = set()
    for relationship in _relationships():
        props = relationship.properties
        found.add((props["fromColumn"], props["toColumn"]))
        # Unset properties take TOM's defaults: many-to-one, one direction, active.
        assert props.get("fromCardinality", "many") == "many", relationship.name
        assert props.get("toCardinality", "one") == "one", relationship.name
        assert props.get("crossFilteringBehavior", "oneDirection") == "oneDirection", relationship.name
        assert props.get("isActive", "true") == "true", relationship.name
    assert found == RELATIONSHIPS
    assert not any(USER_PROJECT_TABLE in side for pair in found for side in pair)


def test_relationship_columns_exist_with_matching_types():
    tables = _tables()
    for from_column, to_column in RELATIONSHIPS:
        types = []
        for ref in (from_column, to_column):
            table, column = ref.split(".")
            types.append(_columns(tables[table])[column].properties["dataType"])
        assert types[0] == types[1], (from_column, to_column)


def test_one_sides_are_unique(spark, loaded_lake):
    """Direct Lake fails a query when a relationship's one side holds a duplicate."""
    paths, _, _ = loaded_lake
    for relationship in _relationships():
        table, column = relationship.properties["toColumn"].split(".")
        df = spark.read.format("delta").load(paths.table(_layer(table), table))
        stats = df.agg(F.count("*").alias("rows"), F.countDistinct(column).alias("distinct")).first()
        assert stats["rows"] > 0, table
        assert stats["rows"] == stats["distinct"], f"{table}.{column}"
        assert df.where(F.col(column).isNull()).count() == 0, f"{table}.{column}"


# --- Row-level security --------------------------------------------------------------------------


def test_role_filters_every_table_with_a_project_column():
    role = _role()
    assert role.name == ROLE
    assert role.properties == {"modelPermission": "read"}
    with_project = {name for name, table in _tables().items() if "project" in _columns(table)}
    expected = {USER_PROJECT_TABLE: USER_FILTER} | {
        name: PROJECT_FILTER for name in with_project - {USER_PROJECT_TABLE}
    }
    assert {p.name: p.expression for p in role.all("tablePermission")} == expected
    assert {"dim_project", "fact_eval_attempt", "fact_gateway_call"} <= set(expected)


def test_role_has_no_members_in_git():
    assert _role().all("member") == []


# --- Measures ------------------------------------------------------------------------------------


def test_every_measure_names_its_gold_answer():
    measures = _measures()
    assert set(measures) == set(MEASURES)
    for name, (table, node) in measures.items():
        expected_table, folder, answer = MEASURES[name]
        assert table == expected_table, name
        assert node.properties.get("displayFolder") == folder, name
        assert node.description == f"Mirrors lakehouse.{answer}", name
        module, function = re.fullmatch(r"Mirrors lakehouse\.([\w.]+)\.(\w+)", node.description).groups()
        assert callable(getattr(importlib.import_module(f"lakehouse.{module}"), function)), name
        assert node.expression and node.properties.get("formatString"), name


def test_measure_names_never_shadow_a_column():
    """A measure may not share its name with any column in the model (DAX names ignore case)."""
    columns = {column.lower() for table in _tables().values() for column in _columns(table)}
    names = [name.lower() for name in _measures()]
    assert len(names) == len(set(names))
    assert not columns & set(names)


def test_quality_measures_cover_every_metric_quality_across_models_returns(spark, loaded_lake):
    paths, _, _ = loaded_lake
    returned = set(answers.quality_across_models(spark, paths).columns)
    keys_and_counts = {"study", "arm", "model", "model_version", "n", "success_rate"}
    assert {answer for answer, _ in QUALITY_METRICS.values()} == returned - keys_and_counts
    measures = _measures()
    for name, (_, column) in QUALITY_METRICS.items():
        assert f"fact_eval_attempt[{column}]" in measures[name][1].expression, name


def test_measures_use_the_answers_constants():
    measures = {name: node.expression for name, (_, node) in _measures().items()}
    for name, (_, folder, _) in MEASURES.items():
        if folder == QUALITY:  # every quality figure counts scored results only
            assert SCORED_FILTER in measures[name], name
    assert f'fact_daily_cost[project] = "{answers.UNTAGGED}"' in measures["Untagged cost"]
    assert f'<> "{answers.NO_MODEL_BASIS}"' in measures["Estimated cost per result (USD)"]
    assert f'= "{answers.PRICING_CURRENCY}"' in measures["Estimated cost per result (AUD)"]
    assert f'fact_daily_cost[resource_group] = "{GATEWAY_RESOURCE_GROUP}"' in measures["Cost per 1,000 calls"]


# --- Identifiers ---------------------------------------------------------------------------------


def _logical_ids() -> set[str]:
    return {
        json.loads(p.read_text(encoding="utf-8"))["config"]["logicalId"] for p in FABRIC.rglob(".platform")
    }


def test_no_identifiers_in_the_model():
    allowed_ids = _logical_ids() | {EMPTY_GUID}
    schema_value = re.compile(r'"\$schema"\s*:\s*"[^"]*"')
    for path in _model_files():
        text = path.read_text(encoding="utf-8")
        assert "@" not in text, path
        assert set(GUID.findall(text)) <= allowed_ids, path
        urls = re.findall(r"[a-z][a-z0-9+.-]*://[^\s\"')]+", schema_value.sub("", text), re.IGNORECASE)
        assert all(url.startswith(ONELAKE) for url in urls), (path, urls)


@pytest.mark.parametrize(
    "text",
    [
        "table t\n\tcolumn a\n\t\tdataType: string\n\t\tsourceColumn: a\n",
        "measure 'It''s' =\n\t\t1 + 1\n\tformatString: 0\n",
        "role r\n\ttablePermission t = [a] = \"b\"\n",
    ],
)
def test_reader_reads_the_tmdl_shapes_the_model_uses(text, tmp_path):
    path = tmp_path / "x.tmdl"
    path.write_text(text, encoding="utf-8")
    node = parse_file(path)[0]
    if node.keyword == "table":
        assert node.one("column").properties == {"dataType": "string", "sourceColumn": "a"}
    elif node.keyword == "measure":
        assert (node.name, node.expression, node.properties) == ("It's", "1 + 1", {"formatString": "0"})
    else:
        assert node.one("tablePermission").expression == '[a] = "b"'
