"""The always-present tables (`ensure_table`) and the access loader for `security.user_project`.

Every UPN here is made up (`example.invalid`), and no test prints one: the loader's errors name the
line number only (Review Focus 2).
"""

import pytest
from delta.tables import DeltaTable
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

from lakehouse.gold.build import PROJECTS
from lakehouse.security import USER_PROJECT_SCHEMA, ensure_table, load_access

KNOWN = [project for project, _ in PROJECTS]
SMALL = StructType([StructField("id", IntegerType()), StructField("name", StringType())])


def _rows(spark, path):
    return spark.read.format("delta").load(path).collect()


def _access(tmp_path, *lines, header="upn,project"):
    path = tmp_path / "access.csv"
    path.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8", newline="")
    return str(path)


def _user_project(spark, paths):
    return {(r["upn"], r["project"]) for r in _rows(spark, paths.table("security", "user_project"))}


# --- ensure_table ----------------------------------------------------------------------------


def test_ensure_table_creates_an_empty_table_once(spark, tmp_lake):
    path = tmp_lake.table("gold", "always_here")

    assert ensure_table(spark, path, SMALL) is True
    assert DeltaTable.isDeltaTable(spark, path)
    table = spark.read.format("delta").load(path)
    assert table.schema == SMALL
    assert table.count() == 0

    assert ensure_table(spark, path, SMALL) is False
    history = DeltaTable.forPath(spark, path).history().collect()
    assert len(history) == 1  # the second call wrote nothing


def test_ensure_table_never_overwrites_a_filled_table(spark, tmp_lake):
    """Review Focus 3: a filled session table is never emptied or overwritten, whatever schema is asked."""
    path = tmp_lake.table("gold", "filled")
    spark.createDataFrame([(1, "a"), (2, "b")], SMALL).write.format("delta").save(path)
    versions = len(DeltaTable.forPath(spark, path).history().collect())

    other = StructType([StructField("something_else", StringType())])
    assert ensure_table(spark, path, SMALL) is False
    assert ensure_table(spark, path, other) is False

    assert sorted((r["id"], r["name"]) for r in _rows(spark, path)) == [(1, "a"), (2, "b")]
    assert spark.read.format("delta").load(path).schema == SMALL
    assert len(DeltaTable.forPath(spark, path).history().collect()) == versions


def test_ensure_table_keeps_a_not_null_schema(spark, tmp_lake):
    path = tmp_lake.table("security", "user_project")

    assert ensure_table(spark, path, USER_PROJECT_SCHEMA)

    assert spark.read.format("delta").load(path).schema == USER_PROJECT_SCHEMA
    assert [f.nullable for f in USER_PROJECT_SCHEMA] == [False, False]
    assert USER_PROJECT_SCHEMA.fieldNames() == ["upn", "project"]


# --- load_access -----------------------------------------------------------------------------


def test_load_access_stores_upns_lower_case_and_stripped(spark, tmp_lake, tmp_path):
    csv_path = _access(tmp_path, "  Lead.P4@Example.Invalid ,p4", "lead-p1@example.invalid, p1 ")

    result = load_access(spark, tmp_lake, csv_path, KNOWN)

    assert _user_project(spark, tmp_lake) == {
        ("lead.p4@example.invalid", "p4"),
        ("lead-p1@example.invalid", "p1"),
    }
    assert result == {"rows": 2, "users": 2, "projects": 2}


def test_two_projects_for_one_user_give_two_rows(spark, tmp_lake, tmp_path):
    csv_path = _access(tmp_path, "lead@example.invalid,p4", "LEAD@example.invalid,p5", "other@example.invalid,p4")

    result = load_access(spark, tmp_lake, csv_path, KNOWN)

    assert _user_project(spark, tmp_lake) == {
        ("lead@example.invalid", "p4"),
        ("lead@example.invalid", "p5"),
        ("other@example.invalid", "p4"),
    }
    assert result == {"rows": 3, "users": 2, "projects": 2}


def test_load_access_overwrites_the_table_and_keeps_its_schema(spark, tmp_lake, tmp_path):
    load_access(spark, tmp_lake, _access(tmp_path, "first@example.invalid,p1"), KNOWN)
    load_access(spark, tmp_lake, _access(tmp_path, "second@example.invalid,p2"), KNOWN)

    assert _user_project(spark, tmp_lake) == {("second@example.invalid", "p2")}
    table = spark.read.format("delta").load(tmp_lake.table("security", "user_project"))
    assert table.schema == USER_PROJECT_SCHEMA


def test_the_result_holds_counts_only(spark, tmp_lake, tmp_path, capsys):
    csv_path = _access(tmp_path, "lead@example.invalid,p4")

    result = load_access(spark, tmp_lake, csv_path, KNOWN)

    assert set(result) == {"rows", "users", "projects"}
    assert all(type(value) is int for value in result.values())
    out, err = capsys.readouterr()
    assert "@" not in out + err


@pytest.mark.parametrize(
    "lines, line",
    [
        (["lead@example.invalid,p4", " ,p4"], 3),  # a blank UPN
        (["lead@example.invalid,p4", ",p5"], 3),  # an empty UPN
        (["lead@example.invalid,p99"], 2),  # an unknown project
        (["lead@example.invalid,"], 2),  # a blank project
        (["lead@example.invalid,p4", "other@example.invalid,p5", "Lead@Example.invalid ,p4"], 4),  # duplicate
        (["lead@example.invalid,p4,extra"], 2),  # a third field
        (["lead@example.invalid"], 2),  # a missing field
    ],
)
def test_a_bad_row_raises_naming_the_line_and_never_the_upn(spark, tmp_lake, tmp_path, lines, line):
    """Review Focus 2: line numbers count the header as line 1, as an editor does."""
    csv_path = _access(tmp_path, *lines)

    with pytest.raises(ValueError, match=rf"\bline {line}\b") as caught:
        load_access(spark, tmp_lake, csv_path, KNOWN)

    message = str(caught.value)
    assert "@" not in message
    assert "lead" not in message.lower() and "other" not in message.lower()
    assert not DeltaTable.isDeltaTable(spark, tmp_lake.table("security", "user_project"))


def test_a_bad_row_leaves_the_loaded_table_alone(spark, tmp_lake, tmp_path):
    load_access(spark, tmp_lake, _access(tmp_path, "lead@example.invalid,p4"), KNOWN)

    with pytest.raises(ValueError, match=r"line 2"):
        load_access(spark, tmp_lake, _access(tmp_path, "lead@example.invalid,p99"), KNOWN)

    assert _user_project(spark, tmp_lake) == {("lead@example.invalid", "p4")}


@pytest.mark.parametrize("header", ["upn", "project,upn", "upn,project,note", "lead@example.invalid,p4"])
def test_a_wrong_header_raises_without_echoing_it(spark, tmp_lake, tmp_path, header):
    csv_path = _access(tmp_path, "lead@example.invalid,p4", header=header)

    with pytest.raises(ValueError, match=r"line 1") as caught:
        load_access(spark, tmp_lake, csv_path, KNOWN)

    assert "@" not in str(caught.value)
