"""The security layer: the access table `security.user_project` and the always-present step.

A Direct Lake on OneLake model errors on a missing table, so the tables it reads that a load may
not have written yet (project 10's `fact_gateway_call` and the access table) are created empty by
`ensure_table`, which never touches a table that exists.

`security.user_project` maps users (UPNs) to the projects they may see; the model's `ProjectLead`
role filters on it. UPNs are never committed: they live only in the git-ignored
`private/access.csv` and in OneLake. `load_access` reads that file (uploaded to
`Files/private/access/`) and overwrites the table. It never prints a UPN, its errors name the line
number only, and it returns counts only.
"""

import csv

from delta.tables import DeltaTable
from pyspark.sql import SparkSession
from pyspark.sql.types import StringType, StructField, StructType

from lakehouse.paths import LakePaths

USER_PROJECT_LAYER = "security"
USER_PROJECT_TABLE = "user_project"
USER_PROJECT_SCHEMA = StructType(
    [StructField("upn", StringType(), nullable=False), StructField("project", StringType(), nullable=False)]
)
ACCESS_HEADER = ["upn", "project"]


def ensure_table(spark: SparkSession, path: str, schema: StructType) -> bool:
    """Create an empty Delta table with `schema` at `path` if none is there; return whether it did.

    An existing table is never touched: not emptied, overwritten or altered, whatever its schema.
    The table is made with Delta's table builder, which keeps `schema`'s NOT NULL columns (a
    DataFrame write would make every column nullable).
    """
    if DeltaTable.isDeltaTable(spark, path):
        return False
    DeltaTable.createIfNotExists(spark).location(path).addColumns(schema).execute()
    return True


def _read_access(csv_path: str, projects: list[str]) -> list[tuple[str, str]]:
    """The file's (upn, project) rows, UPNs stripped and lower-cased, checked.

    Line numbers count the header as line 1, as an editor shows them. No message holds a cell's
    text, because any cell might be a UPN (a missing header puts one in line 1).
    """
    known = set(projects)
    rows: list[tuple[str, str]] = []
    first_line: dict[tuple[str, str], int] = {}
    # utf-8-sig: a spreadsheet's CSV export may start with a byte-order mark.
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if [cell.strip().lower() for cell in header or []] != ACCESS_HEADER:
            raise ValueError("access file line 1: the header must be exactly upn,project")
        for record in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in record):  # a blank line, or one of only spaces and commas
                continue
            if len(record) != len(ACCESS_HEADER):
                raise ValueError(f"access file line {line}: expected 2 fields (upn,project), got {len(record)}")
            upn, project = record[0].strip().lower(), record[1].strip()
            if not upn:
                raise ValueError(f"access file line {line}: the upn is blank")
            if not project:
                raise ValueError(f"access file line {line}: the project is blank")
            if project not in known:
                raise ValueError(f"access file line {line}: the project is not in PROJECTS in lakehouse/gold/build.py")
            pair = (upn, project)
            if pair in first_line:
                raise ValueError(f"access file line {line}: duplicates line {first_line[pair]}")
            first_line[pair] = line
            rows.append(pair)
    if not rows:  # ruling R3: an empty table would leave every lead seeing nothing
        raise ValueError("access file has no rows")
    return rows


def load_access(spark: SparkSession, paths: LakePaths, csv_path: str, projects: list[str]) -> dict:
    """Overwrite `security.user_project` from the `upn,project` file at `csv_path` (a local path).

    UPNs are stripped and lower-cased; one row per (upn, project), so a user with two projects has
    two rows. ValueError, naming the line number only and before anything is written, for a wrong
    header, a row without exactly two fields, a blank UPN or project, a project not in `projects`
    or a duplicate pair; and ValueError for a file with no data rows (ruling R3). Returns `{"rows": n, "users": n, "projects": n}`; nothing is printed.
    """
    rows = _read_access(csv_path, projects)
    path = paths.table(USER_PROJECT_LAYER, USER_PROJECT_TABLE)
    # Created first with the NOT NULL schema, then overwritten without overwriteSchema, so the
    # table keeps that schema (and a table with another schema fails here instead of drifting).
    ensure_table(spark, path, USER_PROJECT_SCHEMA)
    spark.createDataFrame(rows, USER_PROJECT_SCHEMA).write.format("delta").mode("overwrite").save(path)
    return {
        "rows": len(rows),
        "users": len({upn for upn, _ in rows}),
        "projects": len({project for _, project in rows}),
    }
