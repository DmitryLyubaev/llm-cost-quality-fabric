"""Checks on the Fabric item files under fabric/ (what git sync and fabric-cicd read)."""
import fnmatch
import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml
from jsonpath_ng.ext import parse as jsonpath_parse  # the parser fabric-cicd 1.4.0 uses

from check_hygiene import check_hygiene

ROOT = Path(__file__).resolve().parents[1]
FABRIC = ROOT / "fabric"
CELL_MARKER = "# CELL ********************"
PARAMETERS_MARKER = "# PARAMETERS CELL ********************"
MARKDOWN_MARKER = "# MARKDOWN ********************"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NOTEBOOKS = ["nb_ingest_public", "nb_load"]  # the pipeline's, in activity order
GATEWAY_NOTEBOOK = "nb_gateway_load"  # run by hand after a session, never by the pipeline
ACCESS_NOTEBOOK = "nb_access_load"  # run by hand after access.csv is uploaded, never by the pipeline
HAND_RUN_NOTEBOOKS = [GATEWAY_NOTEBOOK, ACCESS_NOTEBOOK]
ALL_NOTEBOOKS = NOTEBOOKS + HAND_RUN_NOTEBOOKS
PARAMETERS = {  # None: the notebook has no parameters cell
    "nb_ingest_public": ['load_date = ""'],
    "nb_load": ['load_date = ""'],
    GATEWAY_NOTEBOOK: ['session = ""', 'eventhouse_count = ""'],
    ACCESS_NOTEBOOK: None,
}
# Patterns that would mean a storage endpoint or a lake path with real IDs slipped in.
FORBIDDEN_IN_NOTEBOOKS = [
    re.compile(r"abfss://", re.IGNORECASE),
    re.compile(r"onelake\.dfs", re.IGNORECASE),
    re.compile(r"\.core\.windows\.net", re.IGNORECASE),
    re.compile(r"^# (CELL )?OUTPUT", re.MULTILINE),
]


def _platforms() -> dict[Path, dict]:
    return {p: json.loads(p.read_text(encoding="utf-8")) for p in FABRIC.rglob(".platform")}


def _logical_ids() -> dict[str, str]:
    """{'<displayName>.<type>': logicalId} for every item under fabric/."""
    return {
        f"{d['metadata']['displayName']}.{d['metadata']['type']}": d["config"]["logicalId"]
        for d in _platforms().values()
    }


def _notebook(name: str) -> str:
    return (FABRIC / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")


def _meta_blocks(text: str) -> list[dict]:
    """Each `# META` JSON block of a notebook-content.py, parsed."""
    blocks, current = [], None
    for line in text.splitlines():
        if line.startswith("# META "):  # not "# METADATA ****", the block's marker line
            current = (current or []) + [line[len("# META"):]]
        elif current is not None:
            blocks.append(json.loads("\n".join(current)))
            current = None
    if current is not None:
        blocks.append(json.loads("\n".join(current)))
    return blocks


def _pipeline() -> dict:
    path = FABRIC / "pl_daily_load.DataPipeline" / "pipeline-content.json"
    return json.loads(path.read_text(encoding="utf-8"))


REALTIME_ITEMS = {
    "eh_gateway.Eventhouse",
    "kql_gateway.KQLDatabase",
    "kq_gateway.KQLQueryset",
    "rtd_gateway.KQLDashboard",
    "act_gateway.Reflex",
    "es_gateway.Eventstream",
}


def test_expected_items_exist():
    assert set(_logical_ids()) == {
        "lh_portfolio.Lakehouse",
        "nb_ingest_public.Notebook",
        "nb_load.Notebook",
        "nb_gateway_load.Notebook",
        "nb_access_load.Notebook",
        "pl_daily_load.DataPipeline",
        "sm_portfolio.SemanticModel",
    } | REALTIME_ITEMS


def test_every_platform_parses_with_a_unique_logical_id():
    platforms = _platforms()
    assert platforms
    ids = []
    for path, doc in platforms.items():
        assert doc["$schema"].endswith("/platformProperties/2.0.0/schema.json"), path
        assert doc["config"]["version"] == "2.0", path
        folder_name, folder_type = path.parent.name.rsplit(".", 1)
        assert doc["metadata"] == {"type": folder_type, "displayName": folder_name}, path
        assert GUID.fullmatch(doc["config"]["logicalId"]), path
        assert doc["config"]["logicalId"] != EMPTY_GUID, path
        ids.append(doc["config"]["logicalId"])
    assert len(ids) == len(set(ids))


def test_lakehouse_has_default_schema_and_no_shortcut_tracking():
    folder = FABRIC / "lh_portfolio.Lakehouse"
    meta = json.loads((folder / "lakehouse.metadata.json").read_text(encoding="utf-8"))
    assert meta == {"defaultSchema": "dbo"}

    alm = json.loads((folder / "alm.settings.json").read_text(encoding="utf-8"))
    shortcuts = [t for t in alm["objectTypes"] if t["name"] == "Shortcuts"]
    assert len(shortcuts) == 1
    assert shortcuts[0]["state"] == "Disabled"
    subtypes = {s["name"]: s["state"] for s in shortcuts[0]["subObjectTypes"]}
    assert set(subtypes) == {
        "Shortcuts.OneLake",
        "Shortcuts.AdlsGen2",
        "Shortcuts.Dataverse",
        "Shortcuts.AmazonS3",
        "Shortcuts.S3Compatible",
        "Shortcuts.GoogleCloudStorage",
        "Shortcuts.AzureBlobStorage",
        "Shortcuts.OneDriveSharePoint",
    }
    assert set(subtypes.values()) == {"Disabled"}


def _cells(text: str) -> list[tuple[str, list[str]]]:
    """(marker, code lines) for each cell, in order; comments and blank lines dropped."""
    cells = []
    for line in text.splitlines():
        if line in (CELL_MARKER, PARAMETERS_MARKER, MARKDOWN_MARKER):
            cells.append((line, []))
        elif line.startswith("# METADATA"):
            cells.append(("# METADATA", []))  # ends the cell before it
        elif cells and line.strip() and not line.startswith("#"):
            cells[-1][1].append(line)
    return [(marker, code) for marker, code in cells if marker != "# METADATA"]


def _wheel_name() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return f"lakehouse-{pyproject['project']['version']}-py3-none-any.whl"


@pytest.mark.parametrize("name", ALL_NOTEBOOKS)
def test_notebook_installs_the_wheel_first_then_takes_its_parameters(name):
    text = _notebook(name)
    assert text.startswith("# Fabric notebook source\n")
    code_cells = [(m, code) for m, code in _cells(text) if m != MARKDOWN_MARKER]
    # %pip restarts Python, so it must run before the parameters cell sets anything.
    install = f"%pip install /lakehouse/default/Files/wheels/{_wheel_name()}"
    assert code_cells[0] == (CELL_MARKER, [install])
    if PARAMETERS[name] is None:
        assert PARAMETERS_MARKER not in text
        assert [m for m, _ in code_cells] == [CELL_MARKER, CELL_MARKER]  # the install, then the call
        return
    assert code_cells[1] == (PARAMETERS_MARKER, PARAMETERS[name])
    assert [m for m, _ in code_cells].count(PARAMETERS_MARKER) == 1


@pytest.mark.parametrize("name", ALL_NOTEBOOKS)
def test_notebook_wheel_name_matches_the_package_version(name):
    installs = re.findall(r"^%pip install \S*/([^/\s]+\.whl)$", _notebook(name), re.MULTILINE)
    assert installs == [_wheel_name()]


@pytest.mark.parametrize("name", ALL_NOTEBOOKS)
def test_notebook_default_lakehouse_is_lh_portfolio_in_this_workspace(name):
    lakehouse_id = _logical_ids()["lh_portfolio.Lakehouse"]
    header = _meta_blocks(_notebook(name))[0]
    assert header["kernel_info"]["name"] == "synapse_pyspark"
    assert header["dependencies"]["lakehouse"] == {
        "default_lakehouse": lakehouse_id,
        "default_lakehouse_name": "lh_portfolio",
        "default_lakehouse_workspace_id": EMPTY_GUID,
        "known_lakehouses": [{"id": lakehouse_id}],
    }


@pytest.mark.parametrize(
    "name, call",
    [
        ("nb_ingest_public", "lakehouse.fabric_entry.ingest_public("),
        ("nb_load", "lakehouse.fabric_entry.load("),
        (
            GATEWAY_NOTEBOOK,
            "lakehouse.fabric_entry.load_gateway(session, eventhouse_count, notebookutils.runtime.context)",
        ),
        (ACCESS_NOTEBOOK, "lakehouse.fabric_entry.load_access(notebookutils.runtime.context)"),
    ],
)
def test_notebook_calls_its_entry_point_with_the_runtime_context(name, call):
    text = _notebook(name)
    assert call in text
    assert "notebookutils.runtime.context" in text
    assert "print(" in text


@pytest.mark.parametrize("name", ALL_NOTEBOOKS)
def test_notebook_has_no_forbidden_patterns_or_foreign_ids(name):
    text = _notebook(name)
    for pattern in FORBIDDEN_IN_NOTEBOOKS:
        assert not pattern.search(text), pattern.pattern
    allowed = {EMPTY_GUID, _logical_ids()["lh_portfolio.Lakehouse"]}
    assert set(GUID.findall(text)) <= allowed


def test_gateway_notebook_says_it_runs_once_after_the_session():
    text = _notebook(GATEWAY_NOTEBOOK)
    markdown = text.split(MARKDOWN_MARKER, 1)[1].split("# PARAMETERS CELL", 1)[0].lower()
    assert "once" in markdown
    assert "after the session" in markdown
    assert "never from the pipeline" in markdown
    assert "json.dumps(" in text


def test_access_notebook_runs_by_hand_and_prints_counts_only():
    text = _notebook(ACCESS_NOTEBOOK)
    markdown = text.split(MARKDOWN_MARKER, 1)[1].split(CELL_MARKER, 1)[0].lower()
    assert "files/private/access/" in markdown
    assert "security.user_project" in markdown
    assert "never from the pipeline" in markdown
    assert "counts" in markdown
    call_cell = text.rsplit(CELL_MARKER, 1)[1]
    assert call_cell.count("print(") == 1
    assert "print(json.dumps(summary" in call_cell
    assert "@" not in text


@pytest.mark.parametrize("name", HAND_RUN_NOTEBOOKS)
def test_pipeline_never_runs_the_hand_run_notebooks(name):
    notebook_id = _logical_ids()[f"{name}.Notebook"]
    for path in (FABRIC / "pl_daily_load.DataPipeline").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert notebook_id not in text, path
            assert name not in text, path


def test_fabric_files_pass_hygiene():
    files = [p for p in FABRIC.rglob("*") if p.is_file()]
    assert check_hygiene(files, forbidden=[]) == []


def test_pipeline_runs_ingest_then_load_with_retries():
    ids = _logical_ids()
    props = _pipeline()["properties"]
    assert props["parameters"] == {"load_date": {"type": "string", "defaultValue": ""}}

    activities = props["activities"]
    assert [a["type"] for a in activities] == ["TridentNotebook", "TridentNotebook"]
    assert [a["name"] for a in activities] == ["ingest", "load"]
    assert activities[0]["dependsOn"] == []
    assert activities[1]["dependsOn"] == [
        {"activity": "ingest", "dependencyConditions": ["Succeeded"]}
    ]

    expression = (
        "@if(empty(pipeline().parameters.load_date), "
        "formatDateTime(pipeline().TriggerTime, 'yyyy-MM-dd'), pipeline().parameters.load_date)"
    )
    for activity, notebook in zip(activities, NOTEBOOKS):
        assert activity["policy"]["retry"] == 2
        assert activity["policy"]["retryIntervalInSeconds"] == 60
        assert activity["policy"]["timeout"] == "0.01:00:00"
        settings = activity["typeProperties"]
        assert settings["notebookId"] == ids[f"{notebook}.Notebook"]
        assert settings["workspaceId"] == EMPTY_GUID
        assert settings["parameters"] == {
            "load_date": {"value": {"value": expression, "type": "Expression"}, "type": "string"},
            "_inlineInstallationEnabled": {"value": True, "type": "bool"},
        }


def test_schedule_is_daily_brisbane_and_disabled_in_git():
    path = FABRIC / "pl_daily_load.DataPipeline" / ".schedules"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["schedules"]) == 1
    schedule = doc["schedules"][0]
    assert schedule["enabled"] is False
    assert schedule["jobType"] == "Execute"
    config = schedule["configuration"]
    assert config["type"] == "Daily"
    assert config["times"] == ["06:00"]
    assert config["localTimeZoneId"] == "E. Australia Standard Time"
    # A start in the past fires a run at once; plan 3 moves it if it has passed by deploy time.
    assert config["startDateTime"] == "2026-11-01T06:00:00"
    assert config["endDateTime"].startswith("2099-01-01")


def _strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def _parameters() -> dict:
    return yaml.safe_load((FABRIC / "parameter.yml").read_text(encoding="utf-8"))


def test_parameter_file_names_only_items_that_exist():
    named = set()
    for value in _strings(_parameters()):
        named.update(re.findall(r"\$items\.([A-Za-z]+)\.([^.$\s]+)\.\$", value))
    assert named
    for item_type, item_name in named:
        assert list(FABRIC.rglob(f"{item_name}.{item_type}")), f"{item_name}.{item_type}"


@pytest.mark.parametrize("name", ALL_NOTEBOOKS)
def test_parameter_file_repoints_lakehouse_and_enables_prod_schedule(name):
    params = _parameters()
    lakehouse_id = _logical_ids()["lh_portfolio.Lakehouse"]
    text = _notebook(name)

    replaced = {}
    notebook_rules = [r for r in params["find_replace"] if r["item_type"] == "Notebook"]
    assert len(notebook_rules) == 3
    for rule in notebook_rules:
        assert "item_name" not in rule  # so every notebook, the gateway's included, is repointed
        assert set(rule["replace_value"]) == {"PROD"}
        assert rule["is_regex"] == "true"
        match = re.search(rule["find_value"], text)
        assert match, rule["find_value"]
        replaced[match.group(1)] = rule["replace_value"]["PROD"]
    assert replaced[lakehouse_id] == "$items.Lakehouse.lh_portfolio.$id"
    assert replaced[EMPTY_GUID] == "$workspace.$id"

    schedule_rules = [r for r in params["key_value_replace"] if r["item_type"] == "DataPipeline"]
    assert len(schedule_rules) == 1
    assert schedule_rules[0]["find_key"] == '$.schedules[?(@.jobType=="Execute")].enabled'
    assert schedule_rules[0]["replace_value"] == {"PROD": True}


# --- Real-time items (project 10) ----------------------------------------------------------------

KQL_DIR = FABRIC / "kql"
QUERY_FILES = sorted((KQL_DIR / "queries").glob("*.kql"))
ALERT_FIGURES = {"failover_minutes", "token_spike_minutes"}
# The two alert tiles add one line to the published query: the dashboard's predefined time range,
# ending at the start of the current minute so Activator never reads a bin that is still filling.
ALERT_FILTER = "| where minute >= _startTime and minute < bin(_endTime, 1m)\n"
TIME_VARIABLES = {"_startTime", "_endTime"}
# fabric-cicd (the deployer) keeps a KQL database inside its Eventhouse's `.children` folder.
KQL_DATABASE_DIR = "eh_gateway.Eventhouse/.children/kql_gateway.KQLDatabase"


def _item_dir(item: str) -> Path:
    return FABRIC / (KQL_DATABASE_DIR if item == "kql_gateway.KQLDatabase" else item)


def _item_files(item: str) -> list[Path]:
    """The item's own files (child items' folders, such as `.children`, are left out)."""
    return [p for p in _item_dir(item).iterdir() if p.is_file()]


def _json(relative: str):
    return json.loads((FABRIC / relative).read_text(encoding="utf-8"))


def _query_text(figure_id: str) -> str:
    return (KQL_DIR / "queries" / f"{figure_id}.kql").read_text(encoding="utf-8")


def _figure_of(text: str) -> str:
    """The published figure a dashboard query is built from (its text starts with the file's)."""
    matches = [p.stem for p in QUERY_FILES if text.startswith(p.read_text(encoding="utf-8"))]
    assert len(matches) == 1, text
    return matches[0]


def test_eventhouse_has_no_properties():
    assert _json("eh_gateway.Eventhouse/EventhouseProperties.json") == {}


def test_kql_database_sits_in_eh_gateway_and_its_schema_is_the_table_file():
    ids = _logical_ids()
    assert not (FABRIC / "kql_gateway.KQLDatabase").exists()
    assert (FABRIC / KQL_DATABASE_DIR / ".platform").is_file()
    props = _json(f"{KQL_DATABASE_DIR}/DatabaseProperties.json")
    assert props["databaseType"] == "ReadWrite"
    assert props["parentEventhouseItemId"] == ids["eh_gateway.Eventhouse"]
    schema = (FABRIC / KQL_DATABASE_DIR / "DatabaseSchema.kql").read_bytes()
    assert schema == (KQL_DIR / "gateway_calls.kql").read_bytes()


def test_queryset_holds_every_published_query_on_kql_gateway():
    queryset = _json("kq_gateway.KQLQueryset/RealTimeQueryset.json")["queryset"]
    assert queryset["version"] == "1.0.0"
    [source] = queryset["dataSources"]
    # Empty cluster URI: the same-workspace git form; the deploy fills it in (see parameter.yml).
    assert source["clusterUri"] == ""
    assert source["type"] == "Fabric"
    assert source["databaseItemId"] == _logical_ids()["kql_gateway.KQLDatabase"]
    assert source["databaseItemName"] == "kql_gateway"

    tabs = {tab["title"]: tab for tab in queryset["tabs"]}
    assert sorted(tabs) == [p.stem for p in QUERY_FILES]
    for figure_id, tab in tabs.items():
        assert tab["content"] == _query_text(figure_id)
        assert tab["dataSourceId"] == source["id"]


def _dashboard() -> dict:
    return _json("rtd_gateway.KQLDashboard/RealTimeDashboard.json")


def _dashboard_tiles() -> dict[str, tuple[dict, dict]]:
    """{figure_id: (tile, query)} for every tile of the dashboard."""
    dashboard = _dashboard()
    queries = {q["id"]: q for q in dashboard["queries"]}
    tiles = {}
    for tile in dashboard["tiles"]:
        assert tile["queryRef"]["kind"] == "query"
        query = queries[tile["queryRef"]["queryId"]]
        figure_id = _figure_of(query["text"])
        assert figure_id not in tiles, figure_id
        tiles[figure_id] = (tile, query)
    return tiles


def test_dashboard_reads_kql_gateway_in_this_workspace():
    dashboard = _dashboard()
    [source] = dashboard["dataSources"]
    kql_id = _logical_ids()["kql_gateway.KQLDatabase"]
    assert source["kind"] == "kusto-trident"
    assert source["clusterUri"] == ""
    assert source["name"] == "kql_gateway"  # fabric-cicd looks the database up by this name
    assert source["database"] == kql_id
    assert source["databaseArtifactId"] == kql_id
    assert source["workspace"] == EMPTY_GUID
    page_ids = {p["id"] for p in dashboard["pages"]}
    for query in dashboard["queries"]:
        assert query["dataSource"] == {"kind": "inline", "dataSourceId": source["id"]}
    for tile in dashboard["tiles"]:
        assert tile["pageId"] in page_ids


def test_dashboard_has_one_tile_per_published_figure_with_its_query():
    tiles = _dashboard_tiles()
    assert sorted(tiles) == [p.stem for p in QUERY_FILES]
    for figure_id, (tile, query) in tiles.items():
        expected = _query_text(figure_id)
        if figure_id in ALERT_FIGURES:
            expected += ALERT_FILTER
            assert set(query["usedVariables"]) == TIME_VARIABLES
        else:
            assert query["usedVariables"] == []
        assert query["text"] == expected
    assert tiles["delay_p50_p95_max"][0]["visualType"] == "card"  # Stat


def test_alert_tiles_are_stat_or_column_with_a_predefined_time_range():
    tiles = _dashboard_tiles()
    for figure_id in ALERT_FIGURES:
        tile, query = tiles[figure_id]
        assert tile["visualType"] in {"card", "column"}  # Stat or Column
        assert query["text"].endswith(ALERT_FILTER)
        assert "make-series" not in query["text"]

    # One time range on the dashboard, and a predefined (relative) one: Activator rejects a custom
    # range, which the schema calls "fixed" (absolute start and end).
    durations = [p for p in _dashboard()["parameters"] if p["kind"] == "duration"]
    assert len(durations) == 1
    assert {durations[0]["beginVariableName"], durations[0]["endVariableName"]} == TIME_VARIABLES
    assert durations[0]["defaultValue"]["kind"] == "dynamic"


REFLEX_TIME_BINDING = "let _startTime = ago(1h);\nlet _endTime = now();\n"
REFLEX_FUNCTION = 'database("kql_gateway").GatewayCalls()'


def _reflex() -> dict[str, dict]:
    entities = _json("act_gateway.Reflex/ReflexEntities.json")
    by_id = {e["uniqueIdentifier"]: e for e in entities}
    assert len(by_id) == len(entities)
    return by_id


def _instance(entity: dict) -> dict:
    return json.loads(entity["payload"]["definition"]["instance"])


def _entity_ids(node) -> list[str]:
    """Every `entityId` argument value in a template instance, in order."""
    found = []
    if isinstance(node, dict):
        if node.get("name") == "entityId" and "value" in node:
            found.append(node["value"])
        for value in node.values():
            found.extend(_entity_ids(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_entity_ids(value))
    return found


def _rule_threshold(rule: dict) -> float:
    rows = [row for step in _instance(rule)["steps"] for row in step["rows"]]
    [becomes] = [row for row in rows if row["kind"] == "NumberBecomes"]
    args = {a["name"]: a["value"] for a in becomes["arguments"]}
    assert args["op"] == "BecomesGreaterThan"
    return args["value"]


def _rule_source(entities: dict[str, dict], rule: dict) -> dict:
    """Follow rule -> attribute -> event -> source through the instances' entityId references."""
    [attribute_id] = _entity_ids(_instance(rule))
    [event_id] = _entity_ids(_instance(entities[attribute_id]))
    [source_id] = _entity_ids(_instance(entities[event_id]))
    return entities[source_id]


def test_reflex_references_resolve():
    entities = _reflex()
    containers = {i for i, e in entities.items() if e["type"] == "container-v1"}
    assert len(containers) == 1
    for entity in entities.values():
        payload = entity["payload"]
        for key in ("parentContainer", "parentObject"):
            if key in payload:
                assert payload[key]["targetUniqueIdentifier"] in entities
        if entity["type"] != "container-v1":
            assert payload["parentContainer"]["targetUniqueIdentifier"] in containers
        if "instance" in payload.get("definition", {}):
            for ref in _entity_ids(_instance(entity)):
                assert ref in entities


def test_reflex_has_exactly_two_rules_on_the_alert_tiles():
    entities = _reflex()
    tiles = _dashboard_tiles()
    rules = [
        e for e in entities.values()
        if e["type"] == "timeSeriesView-v1" and e["payload"]["definition"]["type"] == "Rule"
    ]
    assert len(rules) == 2

    eventhouse_id = _logical_ids()["eh_gateway.Eventhouse"]
    watched = {}
    for rule in rules:
        source = _rule_source(entities, rule)
        assert source["type"] == "kqlSource-v1"
        assert source["payload"]["eventhouseItem"]["targetUniqueIdentifier"] == eventhouse_id
        # The tile's query with two differences only: the tile's time range bound the way the dashboard
        # binds it, and the function named in kql_gateway (the Eventhouse also has a default database).
        query = source["payload"]["query"]["queryString"]
        assert query.startswith(REFLEX_TIME_BINDING)
        figure_id = next(f for f in ALERT_FIGURES if query.endswith(tiles[f][1]["text"].split("GatewayCalls()")[1]))
        tile_text = tiles[figure_id][1]["text"]
        assert tile_text.count("GatewayCalls()") == 1
        assert query == REFLEX_TIME_BINDING + tile_text.replace("GatewayCalls()", REFLEX_FUNCTION)
        watched[figure_id] = rule
        # Off in git, so dev never alerts by itself (design 4.3); parameter.yml turns them on in PROD.
        assert rule["payload"]["definition"]["settings"]["shouldRun"] is False
    assert set(watched) == ALERT_FIGURES

    threshold = int(re.match(r"let threshold = (\d+);", _query_text("token_spike_minutes")).group(1))
    assert threshold == 5000
    assert _rule_threshold(watched["token_spike_minutes"]) == threshold
    assert _rule_threshold(watched["failover_minutes"]) == 0


def test_eventstream_reads_event_hubs_into_the_gateway_table():
    topology = _json("es_gateway.Eventstream/eventstream.json")
    [source] = topology["sources"]
    assert source["type"] == "AzureEventHub"
    props = source["properties"]
    assert props["consumerGroupName"] == "fabric"
    # The connection (namespace, hub, workspace identity) is injected in PROD; git holds a placeholder.
    assert props["dataConnectionId"] == EMPTY_GUID
    assert props["inputSerialization"] == {"type": "Json", "properties": {"encoding": "UTF8"}}

    [stream] = topology["streams"]
    assert stream["type"] == "DefaultStream"
    assert stream["inputNodes"] == [{"name": source["name"]}]

    [destination] = topology["destinations"]
    assert destination["type"] == "Eventhouse"
    assert destination["inputNodes"] == [{"name": stream["name"]}]
    properties = destination["properties"]
    assert properties == {
        "dataIngestionMode": "DirectIngestion",
        "workspaceId": EMPTY_GUID,
        "itemId": _logical_ids()["kql_gateway.KQLDatabase"],
        "tableName": "gateway_calls",
        "connectionName": properties["connectionName"],
        "mappingRuleName": "gateway_calls_json",
    }
    assert 0 < len(properties["connectionName"]) <= 40
    assert 'mapping "gateway_calls_json"' in (KQL_DIR / "gateway_calls.kql").read_text(encoding="utf-8")
    assert topology["operators"] == []

    assert _json("es_gateway.Eventstream/eventstreamProperties.json") == {
        "retentionTimeInDays": 1,
        "eventThroughputLevel": "Low",
    }


def test_eventstream_holds_no_namespace_or_hub():
    for path in (FABRIC / "es_gateway.Eventstream").iterdir():
        text = path.read_text(encoding="utf-8").lower()
        assert "servicebus" not in text, path
        assert "gateway-calls" not in text, path
        assert "namespace" not in text, path


def _declared_ids(node) -> set[str]:
    """GUIDs a file declares for its own parts (tiles, pages, queries, tabs, entities, steps)."""
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("id", "uniqueIdentifier") and isinstance(value, str):
                found.add(value)
            elif key == "instance" and isinstance(value, str):
                found |= _declared_ids(json.loads(value))
            else:
                found |= _declared_ids(value)
    elif isinstance(node, list):
        for value in node:
            found |= _declared_ids(value)
    return found


@pytest.mark.parametrize("item", sorted(REALTIME_ITEMS))
def test_realtime_item_holds_no_foreign_ids_or_hosts(item):
    allowed = set(_logical_ids().values()) | {EMPTY_GUID}
    files = [p for p in _item_files(item) if p.name != ".platform"]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        declared = _declared_ids(json.loads(text)) if path.suffix == ".json" else set()
        assert set(GUID.findall(text)) <= allowed | declared, path
        assert "https://" not in text, path
        assert ".kusto." not in text.lower(), path


def test_realtime_files_use_lf_line_endings():
    for item in REALTIME_ITEMS:
        for path in _item_files(item):
            assert b"\r\n" not in path.read_bytes(), path


def _key_rules(item_type: str) -> list[dict]:
    return [r for r in _parameters()["key_value_replace"] if r["item_type"] == item_type]


def _rule_files(rule: dict) -> list[Path]:
    """The files a key_value_replace rule applies to: its item (by type and name) and its file glob."""
    folders = [
        path.parent for path, doc in _platforms().items()
        if doc["metadata"] == {"type": rule["item_type"], "displayName": rule["item_name"]}
    ]
    assert len(folders) == 1, rule
    return [
        p for p in folders[0].iterdir()
        if p.is_file() and fnmatch.fnmatch(p.relative_to(FABRIC).as_posix(), rule["file_path"])
    ]


def _leaves(node, path=()) -> dict[tuple, object]:
    """{path: value} for every scalar in a JSON document."""
    if isinstance(node, dict):
        return {k: v for key, value in node.items() for k, v in _leaves(value, path + (key,)).items()}
    if isinstance(node, list):
        return {k: v for i, value in enumerate(node) for k, v in _leaves(value, path + (i,)).items()}
    return {path: node}


# What each key_value_replace rule must change, worked out from the documents without JSONPath.
EXPECTED_KEY_LEAVES = {
    "act_gateway": lambda doc: [
        (i, "payload", "definition", "settings", "shouldRun")
        for i, e in enumerate(doc)
        if e["type"] == "timeSeriesView-v1" and e["payload"]["definition"]["type"] == "Rule"
    ],
    "pl_daily_load": lambda doc: [
        ("schedules", i, "enabled") for i, s in enumerate(doc["schedules"]) if s["jobType"] == "Execute"
    ],
    "kq_gateway": lambda doc: [("queryset", "dataSources", 0, "clusterUri")],
    "rtd_gateway": lambda doc: [("dataSources", 0, "clusterUri")],
    "es_gateway": lambda doc: [("sources", 0, "properties", "dataConnectionId")],
}
EXPECTED_KEY_MATCHES = {"act_gateway": 2, "pl_daily_load": 1, "kq_gateway": 1, "rtd_gateway": 1, "es_gateway": 1}


def _key_value_rules() -> list[dict]:
    return _parameters()["key_value_replace"]


def test_every_key_value_rule_is_checked_and_matches_something():
    rules = _key_value_rules()
    assert sorted(r["item_name"] for r in rules) == sorted(EXPECTED_KEY_MATCHES)
    for rule in rules:
        files = _rule_files(rule)
        assert files, rule
        for path in files:
            doc = json.loads(path.read_text(encoding="utf-8"))
            assert jsonpath_parse(rule["find_key"]).find(doc), (rule["find_key"], path)


@pytest.mark.parametrize("rule", _key_value_rules(), ids=lambda r: r["item_name"])
def test_key_value_rule_changes_exactly_its_leaves_in_prod(rule):
    """Apply the rule as fabric-cicd 1.4.0 does (jsonpath_ng.ext parse, find, full_path.update)."""
    [path] = _rule_files(rule)
    doc = json.loads(path.read_text(encoding="utf-8"))
    before = _leaves(doc)
    expected = EXPECTED_KEY_LEAVES[rule["item_name"]](doc)

    matches = jsonpath_parse(rule["find_key"]).find(doc)
    assert len(matches) == EXPECTED_KEY_MATCHES[rule["item_name"]] == len(expected)
    value = rule["replace_value"]["PROD"]
    for match in matches:
        match.full_path.update(doc, value)

    after = _leaves(doc)
    assert set(after) == set(before)
    changed = {key for key in before if before[key] != after[key]}
    assert changed == set(expected)
    assert all(after[key] == value for key in expected)


def test_parameter_file_turns_every_activator_rule_on_in_prod_only():
    [rule] = _key_rules("Reflex")
    assert rule["item_name"] == "act_gateway"
    assert rule["replace_value"] == {"PROD": True}
    entities = _json("act_gateway.Reflex/ReflexEntities.json")
    for key in EXPECTED_KEY_LEAVES["act_gateway"](entities):
        assert _leaves(entities)[key] is False  # git (dev) value; PROD gets True


def test_parameter_file_points_queryset_and_dashboard_at_the_deployed_eventhouse():
    token = "$items.Eventhouse.eh_gateway.$queryserviceuri"
    [queryset_rule] = _key_rules("KQLQueryset")
    assert queryset_rule["find_key"] == "$.queryset.dataSources[*].clusterUri"
    assert queryset_rule["replace_value"] == {"PROD": token}
    assert queryset_rule["item_name"] == "kq_gateway"
    [dashboard_rule] = _key_rules("KQLDashboard")
    assert dashboard_rule["find_key"] == "$.dataSources[*].clusterUri"
    assert dashboard_rule["replace_value"] == {"PROD": token}
    assert dashboard_rule["item_name"] == "rtd_gateway"


def test_parameter_file_injects_the_event_hubs_connection_from_the_environment():
    [rule] = _key_rules("Eventstream")
    assert rule["item_name"] == "es_gateway"
    assert rule["find_key"] == '$.sources[?(@.type=="AzureEventHub")].properties.dataConnectionId'
    assert rule["replace_value"] == {"PROD": "$ENV:P10_EVENTHUB_CONNECTION_ID"}


def test_parameter_file_only_targets_prod_and_known_environment_variables():
    params = _parameters()
    for section in ("find_replace", "key_value_replace"):
        for rule in params[section]:
            assert set(rule["replace_value"]) == {"PROD"}
    text = (FABRIC / "parameter.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"\$ENV:(\w+)", text)) == {"P10_EVENTHUB_CONNECTION_ID", "P11_MODEL_CONNECTION_ID"}
    assert "enable_environment_variable_replacement" in text


# --- The semantic model (project 11): rebinding and the connection -------------------------------

MODEL_EXPRESSIONS = FABRIC / "sm_portfolio.SemanticModel" / "definition" / "expressions.tmdl"
_G = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ONELAKE_SOURCE = re.compile(rf'AzureStorage\.DataLake\("https://onelake\.dfs\.fabric\.microsoft\.com/({_G})/({_G})"')
# What fabric-cicd resolves the two dynamic variables to on a PROD deploy (made-up IDs).
PROD_WORKSPACE = "12121212-aaaa-bbbb-cccc-000000000012"
PROD_LAKEHOUSE = "34343434-aaaa-bbbb-cccc-000000000034"
RESOLVED = {"$workspace.$id": PROD_WORKSPACE, "$items.Lakehouse.lh_portfolio.$id": PROD_LAKEHOUSE}
# fabric_cicd/_common/_validate_env_vars.py, VALID_GUID_REGEX (1.4.0): what a binding connection_id must be.
VALID_GUID = re.compile(r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$")


def _find_replace_rules(item_type: str) -> list[dict]:
    return [r for r in _parameters()["find_replace"] if r["item_type"] == item_type]


def _apply_find_replace(rule: dict, text: str, value: str) -> str:
    """As fabric-cicd 1.4.0 applies an `is_regex` rule (fabric_workspace.py, _replace_parameters):
    re.sub over every match, swapping only capture group 1 for the resolved value."""
    pattern = re.compile(rule["find_value"])
    assert pattern.groups == 1  # fabric-cicd's _validate_regex_structure
    return pattern.sub(
        lambda m: m.group(0)[: m.start(1) - m.start(0)] + value + m.group(0)[m.end(1) - m.start(0) :],
        text,
    )


def test_every_find_replace_rule_is_checked():
    """The notebook rules are evaluated above, the model's below; a new item type needs its own test."""
    rules = _parameters()["find_replace"]
    assert sorted(r["item_type"] for r in rules) == ["Notebook"] * 3 + ["SemanticModel"] * 2


def test_model_rules_target_only_the_expression_file():
    model_files = [
        p for path, doc in _platforms().items() if doc["metadata"]["type"] == "SemanticModel"
        for p in path.parent.rglob("*") if p.is_file()
    ]
    for rule in _find_replace_rules("SemanticModel"):
        assert rule["is_regex"] == "true"
        assert rule["file_path"] == "**/definition/expressions.tmdl"
        assert set(rule["replace_value"]) == {"PROD"}
        assert sorted(FABRIC.glob(rule["file_path"])) == [MODEL_EXPRESSIONS]
        for path in model_files:
            matches = re.findall(rule["find_value"], path.read_text(encoding="utf-8"))
            assert len(matches) == (1 if path == MODEL_EXPRESSIONS else 0), (rule["find_value"], path)


def test_model_rules_repoint_the_workspace_and_the_lakehouse():
    text = MODEL_EXPRESSIONS.read_text(encoding="utf-8")
    source = ONELAKE_SOURCE.search(text)
    roles = {}
    for rule in _find_replace_rules("SemanticModel"):
        match = re.search(rule["find_value"], text)
        # which of the URL's two GUIDs the capture group holds
        roles[match.start(1) - source.start()] = rule["replace_value"]["PROD"]
    assert roles == {
        source.start(1) - source.start(): "$workspace.$id",
        source.start(2) - source.start(): "$items.Lakehouse.lh_portfolio.$id",
    }


@pytest.mark.parametrize("order", [1, -1], ids=["as_written", "reversed"])
def test_model_rules_change_only_the_two_guids_in_prod(order):
    """Every find_replace rule fabric-cicd would apply to the file (its item type and glob match), in
    turn: the second still matches after the first has swapped its GUID, and nothing else changes."""
    text = MODEL_EXPRESSIONS.read_text(encoding="utf-8")
    applied = [
        r for r in _parameters()["find_replace"]
        if r["item_type"] == "SemanticModel" and MODEL_EXPRESSIONS in FABRIC.glob(r["file_path"])
    ]
    assert len(applied) == 2
    result = text
    for rule in applied[::order]:
        assert len(re.findall(rule["find_value"], result)) == 1
        result = _apply_find_replace(rule, result, RESOLVED[rule["replace_value"]["PROD"]])

    source = ONELAKE_SOURCE.search(text)
    expected = (
        text[: source.start(1)] + PROD_WORKSPACE + text[source.end(1) : source.start(2)]
        + PROD_LAKEHOUSE + text[source.end(2) :]
    )
    assert result == expected
    # the notebook rules, filtered out by item type, would not have matched the model either
    for rule in _find_replace_rules("Notebook"):
        assert not re.search(rule["find_value"], text)


def test_parameter_file_binds_the_model_to_the_connection_from_the_environment():
    """fabric-cicd 1.4.0 binds after publish (_items/_semanticmodel.py, post_publish_all); the new
    dictionary format, since the legacy list format takes no per-environment value."""
    assert _parameters()["semantic_model_binding"] == {
        "models": [
            {"semantic_model_name": "sm_portfolio", "connection_id": {"PROD": "$ENV:P11_MODEL_CONNECTION_ID"}}
        ]
    }


def _with_environment(raw: str, environment: dict) -> dict:
    """fabric-cicd 1.4.0's replace_variables_in_parameter_file (_parameter/_utils.py): every
    `$ENV:<name>` token in the raw file is replaced before the YAML is loaded, and a token whose
    variable is not set is kept as it is."""
    return yaml.safe_load(
        re.sub(r"\$ENV:(\w+)", lambda m: environment.get(m.group(1), m.group(0)), raw)
    )


def test_the_binding_connection_resolves_to_a_guid_only_when_the_variable_is_set():
    raw = (FABRIC / "parameter.yml").read_text(encoding="utf-8")
    connection = "56565656-aaaa-bbbb-cccc-000000000056"

    def bound(environment):
        return _with_environment(raw, environment)["semantic_model_binding"]["models"][0]["connection_id"]["PROD"]

    assert bound({"P11_MODEL_CONNECTION_ID": connection}) == connection
    # the gate closed: deploy.py stands in the empty GUID, which passes fabric-cicd's GUID check
    assert VALID_GUID.match(bound({"P11_MODEL_CONNECTION_ID": EMPTY_GUID}))
    # unset, the token stays and fabric-cicd refuses the whole file: hence the stand-in
    assert not VALID_GUID.match(bound({}))
