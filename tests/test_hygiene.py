import json
import uuid

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


def test_ignores_guid_patterns(tmp_path):
    f = tmp_path / "item.txt"
    f.write_text(f"itemId = {uuid.uuid4()}\n", encoding="utf-8")

    assert check_hygiene([f], forbidden=[]) == []
