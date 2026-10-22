"""Checks on the Fabric item files under fabric/ (what git sync and fabric-cicd read)."""
import json
import re
from pathlib import Path

import pytest
import yaml

from check_hygiene import check_hygiene

FABRIC = Path(__file__).resolve().parents[1] / "fabric"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NOTEBOOKS = ["nb_ingest_public", "nb_load"]
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


def test_expected_items_exist():
    assert set(_logical_ids()) == {
        "lh_portfolio.Lakehouse",
        "nb_ingest_public.Notebook",
        "nb_load.Notebook",
        "pl_daily_load.DataPipeline",
    }


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


@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_has_a_parameter_cell(name):
    text = _notebook(name)
    assert text.startswith("# Fabric notebook source\n")
    lines = text.splitlines()
    start = lines.index("# PARAMETERS CELL ********************")
    cell = []
    for line in lines[start + 1:]:
        if line.startswith("# METADATA") or line.startswith("# CELL"):
            break
        cell.append(line)
    assert 'load_date = ""' in cell
    assert 'wheel = "lakehouse-0.1.0-py3-none-any.whl"' in cell
    assert "%pip install /lakehouse/default/Files/wheels/{wheel}" in lines


@pytest.mark.parametrize("name", NOTEBOOKS)
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
    ],
)
def test_notebook_calls_its_entry_point_with_the_runtime_context(name, call):
    text = _notebook(name)
    assert call in text
    assert "notebookutils.runtime.context" in text
    assert "print(" in text


@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_has_no_forbidden_patterns_or_foreign_ids(name):
    text = _notebook(name)
    for pattern in FORBIDDEN_IN_NOTEBOOKS:
        assert not pattern.search(text), pattern.pattern
    allowed = {EMPTY_GUID, _logical_ids()["lh_portfolio.Lakehouse"]}
    assert set(GUID.findall(text)) <= allowed


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
            "wheel": {"value": "lakehouse-0.1.0-py3-none-any.whl", "type": "string"},
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


def test_parameter_file_repoints_lakehouse_and_enables_prod_schedule():
    params = _parameters()
    lakehouse_id = _logical_ids()["lh_portfolio.Lakehouse"]
    text = _notebook("nb_load")

    replaced = {}
    for rule in params["find_replace"]:
        assert set(rule["replace_value"]) == {"PROD"}
        assert rule["is_regex"] == "true"
        match = re.search(rule["find_value"], text)
        assert match, rule["find_value"]
        replaced[match.group(1)] = rule["replace_value"]["PROD"]
    assert replaced[lakehouse_id] == "$items.Lakehouse.lh_portfolio.$id"
    assert replaced[EMPTY_GUID] == "$workspace.$id"

    schedule_rules = params["key_value_replace"]
    assert len(schedule_rules) == 1
    assert schedule_rules[0]["find_key"] == '$.schedules[?(@.jobType=="Execute")].enabled'
    assert schedule_rules[0]["replace_value"] == {"PROD": True}
