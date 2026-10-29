"""The gold snapshot into lh_capstone, its lakehouse item and the capstone's two notebooks."""

import inspect
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from delta.tables import DeltaTable

from lake_helpers import copy_lake
from lakehouse import fabric_entry
from lakehouse.capstone import snapshot
from lakehouse.capstone.expected import compute_expected
from lakehouse.capstone.snapshot import SNAPSHOT_FILE, SNAPSHOT_TABLES, copy_gold
from lakehouse.gateway.build import GOLD_TABLE as GATEWAY_GOLD_TABLE
from lakehouse.gold.build import GOLD_TABLES
from lakehouse.paths import LakePaths
from test_fabric_items import (
    CELL_MARKER,
    EMPTY_GUID,
    FORBIDDEN_IN_NOTEBOOKS,
    GUID,
    MARKDOWN_MARKER,
    PARAMETERS_MARKER,
    _cells,
    _meta_blocks,
    _wheel_name,
)

ROOT = Path(__file__).resolve().parents[1]
CAPSTONE = ROOT / "fabric-capstone"
LAKEHOUSE = CAPSTONE / "lh_capstone.Lakehouse"
SNAPSHOT_NOTEBOOK = "nb_capstone_snapshot"
EXPECTED_NOTEBOOK = "nb_capstone_expected"
SOURCE_WS = "11111111-2222-3333-4444-555555555555"
SOURCE_LH = "66666666-7777-8888-9999-aaaaaaaaaaaa"
CONTEXT = {"currentWorkspaceId": "ws-guid", "defaultLakehouseId": "lh-guid"}
# Spec 4.2: the tables the data agent selects, which are the tables the snapshot copies.
SPEC_TABLES = {
    "fact_daily_cost",
    "fact_eval_attempt",
    "fact_live_check",
    "fact_gateway_call",
    "dim_project",
    "dim_study",
    "dim_arm",
    "dim_model_deployment",
    "dim_date",
}


def _gold(spark, paths, name):
    return spark.read.format("delta").load(paths.table("gold", name))


def _latest_version(spark, paths, name) -> int:
    return DeltaTable.forPath(spark, paths.table("gold", name)).history(1).collect()[0]["version"]


def _gold_dirs(paths: LakePaths) -> set[str]:
    folder = Path(paths.tables_root) / "gold"
    return {p.name for p in folder.iterdir() if p.is_dir()} if folder.is_dir() else set()


# --- copy_gold -----------------------------------------------------------------------------------


def test_snapshot_tables_are_the_spec_tables():
    assert set(SNAPSHOT_TABLES) == SPEC_TABLES
    assert len(SNAPSHOT_TABLES) == len(SPEC_TABLES)
    assert set(SNAPSHOT_TABLES) == set(GOLD_TABLES) | {GATEWAY_GOLD_TABLE}


def test_copy_gold_copies_exactly_the_named_tables_and_counts_them(spark, loaded_lake, tmp_path):
    source = loaded_lake[0]
    target = LakePaths.local(str(tmp_path / "capstone"))
    named = ["fact_daily_cost", "dim_project", "fact_gateway_call"]
    before = datetime.now(timezone.utc).replace(microsecond=0)

    result = copy_gold(spark, source, target, named)

    after = datetime.now(timezone.utc)
    assert set(result) == {"snapshot_time", "versions", "rows"}
    assert result["versions"] == {name: _latest_version(spark, source, name) for name in named}
    assert _gold_dirs(target) == set(named)
    assert result["rows"] == {name: _gold(spark, source, name).count() for name in named}
    assert result["rows"]["fact_daily_cost"] > 0
    assert result["rows"]["fact_gateway_call"] == 0  # an empty table is still copied
    for name in named:
        copied, original = _gold(spark, target, name), _gold(spark, source, name)
        assert copied.schema == original.schema, name
        assert copied.exceptAll(original).count() == 0, name
        assert original.exceptAll(copied).count() == 0, name
    text = result["snapshot_time"]
    assert text.endswith("Z")
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    assert before <= stamp <= after


def test_copy_gold_writes_the_same_dict_to_files_capstone(spark, loaded_lake, tmp_path):
    target = LakePaths.local(str(tmp_path / "capstone"))

    result = copy_gold(spark, loaded_lake[0], target, ["dim_date"])

    written = Path(target.files_root) / SNAPSHOT_FILE
    assert SNAPSHOT_FILE == "capstone/snapshot.json"
    assert json.loads(written.read_text(encoding="utf-8")) == result
    assert b"\r" not in written.read_bytes()


def test_copy_gold_writes_through_the_files_mount_when_there_is_one(spark, loaded_lake, tmp_path):
    local = LakePaths.local(str(tmp_path / "capstone"))
    mount = tmp_path / "mount"
    target = LakePaths(local.tables_root, "abfss://not-used/Files", files_mount=str(mount))

    result = copy_gold(spark, loaded_lake[0], target, ["dim_date"])

    assert json.loads((mount / SNAPSHOT_FILE).read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("mount", [None, ""])
def test_copy_gold_refuses_a_remote_files_root_without_a_mount_before_writing(spark, loaded_lake, tmp_path, mount):
    local = LakePaths.local(str(tmp_path / "capstone"))
    target = LakePaths(local.tables_root, "abfss://ws@host/lh/Files", files_mount=mount)

    with pytest.raises(ValueError, match="files mount"):
        copy_gold(spark, loaded_lake[0], target, ["dim_date"])

    assert not Path(local.tables_root).exists()


def test_copy_gold_reads_each_table_at_the_version_it_recorded(spark, tmp_path, monkeypatch):
    # A load that lands between recording the versions and reading must not reach the snapshot.
    source = LakePaths.local(str(tmp_path / "source"))
    path = source.table("gold", "dim_project")
    spark.createDataFrame([("p1", "one")], "project string, name string").write.format("delta").save(path)
    spark.createDataFrame([("p1", "one"), ("p2", "two")], "project string, name string").write.format(
        "delta"
    ).mode("overwrite").save(path)
    assert _latest_version(spark, source, "dim_project") == 1
    monkeypatch.setattr(snapshot, "_latest_version", lambda spark, path: 0)  # as if v1 landed just after
    target = LakePaths.local(str(tmp_path / "capstone"))

    result = copy_gold(spark, source, target, ["dim_project"])

    assert result["versions"] == {"dim_project": 0}
    assert result["rows"] == {"dim_project": 1}
    assert [r["project"] for r in _gold(spark, target, "dim_project").collect()] == ["p1"]


def test_copy_gold_overwrites_an_existing_target_table(spark, loaded_lake, tmp_path):
    source = loaded_lake[0]
    target = LakePaths.local(str(tmp_path / "capstone"))
    stale = spark.createDataFrame([("old", 1)], "junk string, n int")
    stale.write.format("delta").save(target.table("gold", "dim_project"))

    result = copy_gold(spark, source, target, ["dim_project"])

    copied = _gold(spark, target, "dim_project")
    assert copied.columns == _gold(spark, source, "dim_project").columns
    assert copied.count() == result["rows"]["dim_project"] == 13


def test_copy_gold_copies_all_nine_tables(spark, loaded_lake, tmp_path):
    target = LakePaths.local(str(tmp_path / "capstone"))

    result = copy_gold(spark, loaded_lake[0], target, list(SNAPSHOT_TABLES))

    assert set(result["rows"]) == SPEC_TABLES
    assert _gold_dirs(target) == SPEC_TABLES


@pytest.mark.parametrize(
    "tables, message",
    [
        ([], "at least one"),
        (["dim_date", "dim_date"], "more than once"),
        (["dim_date", "user_project"], "user_project"),
        (["../silver/cost"], "silver"),
        ("dim_date", "list"),
    ],
)
def test_copy_gold_refuses_bad_table_lists_before_writing(spark, loaded_lake, tmp_path, tables, message):
    target = LakePaths.local(str(tmp_path / "capstone"))

    with pytest.raises(ValueError, match=message):
        copy_gold(spark, loaded_lake[0], target, tables)

    assert not Path(target.tables_root).exists()
    assert not Path(target.files_root).exists()


def test_copy_gold_refuses_a_missing_source_table_before_writing(spark, loaded_lake, tmp_path):
    source = copy_lake(loaded_lake[0], tmp_path / "source")
    shutil.rmtree(source.table("gold", "dim_study"))
    target = LakePaths.local(str(tmp_path / "capstone"))

    with pytest.raises(ValueError, match="dim_study"):
        copy_gold(spark, source, target, ["dim_date", "dim_study"])

    assert _gold_dirs(target) == set()


def test_copy_gold_refuses_to_copy_a_lake_onto_itself(spark, loaded_lake):
    paths = loaded_lake[0]

    with pytest.raises(ValueError, match="itself"):
        copy_gold(spark, paths, paths, ["dim_date"])


# --- the entry points ----------------------------------------------------------------------------


def _capture_copy(monkeypatch):
    seen = {}

    def fake_copy_gold(spark, source, target, tables):
        seen.update(spark=spark, source=source, target=target, tables=tables)
        return {"snapshot_time": "2026-11-08T00:00:00Z", "rows": {t: 1 for t in tables}}

    spark_stub = object()
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: spark_stub)
    monkeypatch.setattr(fabric_entry, "copy_gold", fake_copy_gold)
    return seen, spark_stub


def test_snapshot_gold_reads_the_source_lakehouse_and_writes_the_default_one(monkeypatch):
    seen, spark_stub = _capture_copy(monkeypatch)

    result = fabric_entry.snapshot_gold(SOURCE_WS, SOURCE_LH, CONTEXT)

    assert seen["spark"] is spark_stub
    root = f"abfss://{SOURCE_WS}@{fabric_entry.ONELAKE_HOST}/{SOURCE_LH}"
    assert seen["source"].tables_root == f"{root}/Tables"
    assert seen["source"].files_root == f"{root}/Files"
    assert seen["source"].files_mount is None  # the source is not mounted; only its tables are read
    assert seen["target"] == fabric_entry.fabric_paths(CONTEXT)
    assert seen["tables"] == list(SNAPSHOT_TABLES)
    assert result == {"snapshot_time": "2026-11-08T00:00:00Z", "rows": {t: 1 for t in SNAPSHOT_TABLES}}
    json.dumps(result)


def test_snapshot_gold_always_copies_all_nine_tables():
    # a partial snapshot would leave lh_capstone mixing two days of gold
    assert list(inspect.signature(fabric_entry.snapshot_gold).parameters) == [
        "source_workspace_id",
        "source_lakehouse_id",
        "context",
    ]


def test_snapshot_gold_trims_the_ids(monkeypatch):
    seen, _ = _capture_copy(monkeypatch)

    fabric_entry.snapshot_gold(SOURCE_WS.upper(), f" {SOURCE_LH} ", CONTEXT)

    assert seen["tables"] == list(SNAPSHOT_TABLES)
    assert SOURCE_WS.upper() in seen["source"].tables_root
    assert f"/{SOURCE_LH}/Tables" in seen["source"].tables_root


@pytest.mark.parametrize(
    "workspace, lakehouse",
    [("", SOURCE_LH), (SOURCE_WS, ""), ("not-a-guid", SOURCE_LH), (SOURCE_WS, f"{SOURCE_LH}/../x")],
)
def test_snapshot_gold_needs_two_guids(monkeypatch, workspace, lakehouse):
    _capture_copy(monkeypatch)
    monkeypatch.setattr(fabric_entry, "copy_gold", lambda *a: pytest.fail("must not copy"))

    with pytest.raises(ValueError, match="GUID"):
        fabric_entry.snapshot_gold(workspace, lakehouse, CONTEXT)


def test_snapshot_gold_refuses_its_own_default_lakehouse_as_the_source(monkeypatch):
    _capture_copy(monkeypatch)
    monkeypatch.setattr(fabric_entry, "copy_gold", lambda *a: pytest.fail("must not copy"))
    context = {"currentWorkspaceId": SOURCE_WS, "defaultLakehouseId": SOURCE_LH.upper()}

    with pytest.raises(ValueError, match="default lakehouse"):
        fabric_entry.snapshot_gold(SOURCE_WS, SOURCE_LH, context)


def test_capstone_expected_reads_questions_and_writes_expected_beside_them(spark, loaded_lake, tmp_path, monkeypatch):
    paths = loaded_lake[0]
    mount = tmp_path / "mount"
    folder = mount / "capstone"
    folder.mkdir(parents=True)
    questions = [
        q
        for q in json.loads((ROOT / "capstone" / "questions.json").read_text(encoding="utf-8"))
        if q["id"] in ("q01", "q05", "q11", "q15")
    ]
    (folder / "questions.json").write_text(json.dumps(questions), encoding="utf-8")
    seen = {}

    def fake_paths(context):
        seen["context"] = context
        return LakePaths(paths.tables_root, paths.files_root, files_mount=str(mount))

    monkeypatch.setattr(fabric_entry, "fabric_paths", fake_paths)
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: spark)

    result = fabric_entry.capstone_expected(CONTEXT)

    assert seen["context"] is CONTEXT
    want = compute_expected(spark, paths, questions)
    assert result == want
    assert set(result) == {"q01", "q05", "q11"}
    raw = (folder / "expected.json").read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n")
    assert json.loads(raw) == want
    assert raw.decode("utf-8") == json.dumps(want, indent=2, sort_keys=True) + "\n"
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"


def test_capstone_expected_needs_the_uploaded_questions(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric_entry, "FILES_MOUNT", str(tmp_path))
    monkeypatch.setattr(fabric_entry, "get_active_spark", lambda: object())
    monkeypatch.setattr(fabric_entry, "compute_expected", lambda *a: pytest.fail("must not compute"))

    with pytest.raises(FileNotFoundError, match="questions.json"):
        fabric_entry.capstone_expected(CONTEXT)

    assert not (tmp_path / "capstone" / "expected.json").exists()


def test_snapshot_module_has_no_live_imports():
    text = Path(snapshot.__file__).read_text(encoding="utf-8")
    assert "notebookutils" not in text
    assert "abfss://" not in text


# --- the lakehouse item ----------------------------------------------------------------------------


def _platform(folder: Path) -> dict:
    return json.loads((folder / ".platform").read_text(encoding="utf-8"))


def test_lh_capstone_is_a_schema_enabled_lakehouse_with_no_shortcut_tracking():
    platform = _platform(LAKEHOUSE)
    assert platform["$schema"].endswith("/platformProperties/2.0.0/schema.json")
    assert platform["metadata"] == {"type": "Lakehouse", "displayName": "lh_capstone"}
    assert platform["config"]["version"] == "2.0"
    assert GUID.fullmatch(platform["config"]["logicalId"])
    assert platform["config"]["logicalId"] != EMPTY_GUID
    # a defaultSchema is what marks a lakehouse as schema-enabled in its git form
    meta = json.loads((LAKEHOUSE / "lakehouse.metadata.json").read_text(encoding="utf-8"))
    assert meta == {"defaultSchema": "dbo"}
    alm = (LAKEHOUSE / "alm.settings.json").read_bytes()
    assert alm == (ROOT / "fabric" / "lh_portfolio.Lakehouse" / "alm.settings.json").read_bytes()
    assert sorted(p.name for p in LAKEHOUSE.iterdir()) == [
        ".platform",
        "alm.settings.json",
        "lakehouse.metadata.json",
    ]


def test_capstone_logical_ids_are_unique_and_new():
    ids = [_platform(folder)["config"]["logicalId"] for folder in CAPSTONE.iterdir() if (folder / ".platform").is_file()]
    assert len(ids) == 4  # lh_capstone, da_portfolio and the two notebooks
    fabric_ids = {
        json.loads(p.read_text(encoding="utf-8"))["config"]["logicalId"] for p in (ROOT / "fabric").rglob(".platform")
    }
    assert len(set(ids)) == len(ids)
    assert not set(ids) & fabric_ids


# --- the notebooks -----------------------------------------------------------------------------------


def _notebook(name: str) -> str:
    return (CAPSTONE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", [SNAPSHOT_NOTEBOOK, EXPECTED_NOTEBOOK])
def test_notebook_platform_names_the_folder(name):
    platform = _platform(CAPSTONE / f"{name}.Notebook")
    assert platform["metadata"] == {"type": "Notebook", "displayName": name}
    assert platform["config"]["version"] == "2.0"
    assert GUID.fullmatch(platform["config"]["logicalId"])
    assert sorted(p.name for p in (CAPSTONE / f"{name}.Notebook").iterdir()) == [".platform", "notebook-content.py"]


@pytest.mark.parametrize(
    "name, parameters, call",
    [
        (
            SNAPSHOT_NOTEBOOK,
            ['source_workspace_id = ""', 'source_lakehouse_id = ""'],
            "summary = lakehouse.fabric_entry.snapshot_gold("
            "source_workspace_id, source_lakehouse_id, notebookutils.runtime.context)",
        ),
        (
            EXPECTED_NOTEBOOK,
            None,
            "summary = lakehouse.fabric_entry.capstone_expected(notebookutils.runtime.context)",
        ),
    ],
)
def test_notebook_installs_the_wheel_then_takes_its_parameters_then_makes_one_call(name, parameters, call):
    text = _notebook(name)
    assert text.startswith("# Fabric notebook source\n")
    code_cells = [(m, code) for m, code in _cells(text) if m != MARKDOWN_MARKER]
    install = f"%pip install /lakehouse/default/Files/wheels/{_wheel_name()}"
    assert code_cells[0] == (CELL_MARKER, [install])
    if parameters is None:
        assert PARAMETERS_MARKER not in text
        rest = code_cells[1:]
    else:
        assert code_cells[1] == (PARAMETERS_MARKER, parameters)  # empty: set at run time, never committed
        rest = code_cells[2:]
    assert [m for m, _ in rest] == [CELL_MARKER]  # then one cell, which makes the one call
    calls = [line for line in rest[0][1] if "lakehouse.fabric_entry." in line]
    assert calls == [call]
    assert rest[0][1][-1] == "print(json.dumps(summary, indent=2, sort_keys=True, default=str))"


@pytest.mark.parametrize("name", [SNAPSHOT_NOTEBOOK, EXPECTED_NOTEBOOK])
def test_notebook_default_lakehouse_is_lh_capstone_in_this_workspace(name):
    lakehouse_id = _platform(LAKEHOUSE)["config"]["logicalId"]
    header = _meta_blocks(_notebook(name))[0]
    assert header["kernel_info"]["name"] == "synapse_pyspark"
    assert header["dependencies"]["lakehouse"] == {
        "default_lakehouse": lakehouse_id,
        "default_lakehouse_name": "lh_capstone",
        "default_lakehouse_workspace_id": EMPTY_GUID,
        "known_lakehouses": [{"id": lakehouse_id}],
    }


@pytest.mark.parametrize("name", [SNAPSHOT_NOTEBOOK, EXPECTED_NOTEBOOK])
def test_notebook_has_no_forbidden_patterns_or_foreign_ids(name):
    text = _notebook(name)
    for pattern in FORBIDDEN_IN_NOTEBOOKS:
        assert not pattern.search(text), pattern.pattern
    assert set(GUID.findall(text)) <= {EMPTY_GUID, _platform(LAKEHOUSE)["config"]["logicalId"]}
    assert "@" not in text  # no address: the source's OneLake path is built in the package


def test_snapshot_notebook_has_no_tables_parameter():
    parameters = _notebook(SNAPSHOT_NOTEBOOK).split(PARAMETERS_MARKER, 1)[1].split("# METADATA", 1)[0]
    assert "tables" not in parameters
    assert "tables" not in _notebook(SNAPSHOT_NOTEBOOK).rsplit(CELL_MARKER, 1)[1]


def test_snapshot_notebook_says_how_and_when_it_runs():
    text = _notebook(SNAPSHOT_NOTEBOOK)
    markdown = " ".join(text.split(MARKDOWN_MARKER, 1)[1].split(PARAMETERS_MARKER, 1)[0].lower().split())
    assert "by hand" in markdown
    assert "lh_portfolio" in markdown and "lh_capstone" in markdown
    assert "never committed" in markdown
    assert "snapshot.json" in markdown
    assert "all nine gold tables" in markdown


def test_expected_notebook_says_where_it_reads_and_writes():
    text = _notebook(EXPECTED_NOTEBOOK)
    markdown = " ".join(text.split(MARKDOWN_MARKER, 1)[1].split(CELL_MARKER, 1)[0].lower().split())
    assert "files/capstone/questions.json" in markdown
    assert "expected.json" in markdown
    assert "after the snapshot" in markdown


# --- placement ---------------------------------------------------------------------------------------


def test_nothing_in_fabric_or_the_deploy_workflow_references_the_capstone():
    names = ("capstone", "da_portfolio", "lh_capstone")
    files = [p for p in (ROOT / "fabric").rglob("*") if p.is_file()]
    files.append(ROOT / ".github" / "workflows" / "deploy.yml")
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for name in names:
            assert name not in text, f"{path.relative_to(ROOT)} mentions {name}"


def test_capstone_items_live_only_in_fabric_capstone():
    assert not list((ROOT / "fabric").glob("*capstone*"))
    assert not list((ROOT / "fabric").glob("da_portfolio*"))
    assert sorted(p.name for p in CAPSTONE.iterdir() if p.is_dir()) == [
        "da_portfolio.DataAgent",
        "lh_capstone.Lakehouse",
        "nb_capstone_expected.Notebook",
        "nb_capstone_snapshot.Notebook",
    ]
