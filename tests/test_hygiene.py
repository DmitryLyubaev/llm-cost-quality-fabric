import json
import uuid

import pytest

from check_hygiene import check_hygiene


def _notebook(path, outputs):
    nb = {
        "cells": [{"cell_type": "code", "source": ["1 + 1"], "outputs": outputs, "metadata": {}}],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb), encoding="utf-8")
    return path


def test_flags_notebook_outputs(tmp_path):
    dirty = _notebook(tmp_path / "dirty.ipynb", [{"output_type": "stream", "text": ["2"]}])
    clean = _notebook(tmp_path / "clean.ipynb", [])

    problems = check_hygiene([dirty, clean], forbidden=[])

    assert len(problems) == 1
    assert "dirty.ipynb" in problems[0]


def test_flags_forbidden_values(tmp_path):
    f = tmp_path / "config.txt"
    f.write_text("token = abc-secret-value\n", encoding="utf-8")

    problems = check_hygiene([f], forbidden=["abc-secret-value"])

    assert len(problems) == 1
    assert "config.txt" in problems[0]
    assert "abc-secret-value" not in problems[0]
    assert check_hygiene([f], forbidden=[]) == []


def _shortcuts(folder, content):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "shortcuts.metadata.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_flags_nonempty_shortcuts_file(tmp_path):
    shortcut = {
        "name": "cost_export",
        "path": "/Files",
        "target": {"type": "AdlsGen2", "adlsGen2": {"location": "x", "subpath": "/y"}},
    }
    path = _shortcuts(tmp_path / "lh.Lakehouse", json.dumps([shortcut]))

    problems = check_hygiene([path], forbidden=[])

    assert len(problems) == 1
    assert "shortcuts.metadata.json" in problems[0]


def test_allows_empty_shortcuts_file(tmp_path):
    path = _shortcuts(tmp_path / "lh.Lakehouse", "[]\n")

    assert check_hygiene([path], forbidden=[]) == []


def test_flags_unparseable_shortcuts_file(tmp_path):
    path = _shortcuts(tmp_path / "lh.Lakehouse", "[{")

    assert len(check_hygiene([path], forbidden=[])) == 1


def _fabric_notebook(folder, body):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "notebook-content.py"
    path.write_text("# Fabric notebook source\n\n# CELL ********************\n\n" + body, encoding="utf-8")
    return path


@pytest.mark.parametrize("marker", ["# OUTPUT ********************", "# CELL OUTPUT ****"])
def test_flags_notebook_output_marker(tmp_path, marker):
    dirty = _fabric_notebook(tmp_path / "dirty.Notebook", f"print(2)\n\n{marker}\n\n# 2\n")
    clean = _fabric_notebook(tmp_path / "clean.Notebook", "print(2)  # OUTPUT is fine mid-line\n")

    problems = check_hygiene([dirty, clean], forbidden=[])

    assert len(problems) == 1
    assert "dirty.Notebook" in problems[0]


def test_output_marker_rule_only_applies_to_notebook_content(tmp_path):
    other = tmp_path / "notes.py"
    other.write_text("# OUTPUT of the job\n", encoding="utf-8")

    assert check_hygiene([other], forbidden=[]) == []


def test_ignores_guid_patterns(tmp_path):
    f = tmp_path / "item.txt"
    f.write_text(f"itemId = {uuid.uuid4()}\n", encoding="utf-8")

    assert check_hygiene([f], forbidden=[]) == []


# Joined at run time so this file does not itself hold a host the repo-wide check would flag.
SERVICEBUS = "." + "servicebus.windows.net"
KUSTO = "." + "kusto.fabric.microsoft.com"


@pytest.mark.parametrize(
    "line",
    [
        f"endpoint = sb://evh-demo-01{SERVICEBUS}/",
        f'"clusterUri": "https://trd-abc123.z4{KUSTO}"',
        f"host: EVH-DEMO{SERVICEBUS.upper()}",
    ],
)
def test_flags_event_hubs_and_kusto_hosts(tmp_path, line):
    f = tmp_path / "item.json"
    f.write_text(line + "\n", encoding="utf-8")

    problems = check_hygiene([f], forbidden=[])

    assert len(problems) == 1
    assert "item.json" in problems[0]
    assert "evh-demo" not in problems[0].lower() and "trd-abc123" not in problems[0]


@pytest.mark.parametrize(
    "line",
    [
        f"Reject any committed `{SERVICEBUS[1:]}` or `{KUSTO[1:]}` host.",
        f'endpoint = "${{var.event_hub_namespace}}{SERVICEBUS}"',
        f"<namespace>{SERVICEBUS}",
    ],
)
def test_allows_host_suffixes_without_a_name(tmp_path, line):
    f = tmp_path / "notes.md"
    f.write_text(line + "\n", encoding="utf-8")

    assert check_hygiene([f], forbidden=[]) == []
