"""The packaged seed files, read strictly in Python.

The seeds ship inside the wheel (`lakehouse/seeds/`), so Spark never reads a seed path: every seed
goes through `read_seed_rows` (Python `csv`) and, for a DataFrame, `seed_df`. A header that is not
the schema, a short or long row, or a value that does not convert fails loudly, naming the file and
the row (1 is the first data row) and, for a value, the column. Nothing becomes a silent null: only a
blank cell is null.
"""

import csv
import math
from collections.abc import Callable
from datetime import date
from importlib import resources
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import (
    BooleanType,
    DataType,
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructType,
)

SEEDS_DIR: str = str(resources.files("lakehouse") / "seeds")


def read_seed_rows(seeds_dir: str, name: str) -> list[dict[str, str]]:
    """The data rows of `seeds_dir/name` as dicts of strings (UTF-8).

    Raises ValueError naming the file if the header is missing or a row has the wrong field count.
    """
    path = Path(seeds_dir) / name
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            raise ValueError(f"seed {name}: header is missing")
        rows = []
        for number, fields in enumerate(reader, start=1):
            if len(fields) != len(header):
                raise ValueError(
                    f"seed {name}: row {number} has {len(fields)} field(s), header has {len(header)}"
                )
            rows.append(dict(zip(header, fields, strict=True)))
    return rows


STUDY_PROJECTS_HEADER = ["study", "project", "evidence"]


def read_study_projects(seeds_dir: str, name: str = "study_projects.csv") -> dict[str, str]:
    """`study_projects.csv` as study -> project, read strictly.

    ValueError naming the file if the header is not `study,project,evidence`, a study or project
    is blank, or a study appears twice.
    """
    header = _header(seeds_dir, name)
    if header != STUDY_PROJECTS_HEADER:
        raise ValueError(f"seed {name}: header {header} is not {STUDY_PROJECTS_HEADER}")
    mapping: dict[str, str] = {}
    for number, row in enumerate(read_seed_rows(seeds_dir, name), start=1):
        study, project = row["study"].strip(), row["project"].strip()
        if not study or not project:
            raise ValueError(f"seed {name}: row {number} has a blank study or project")
        if study in mapping:
            raise ValueError(f"seed {name}: row {number}: study {study!r} appears twice")
        mapping[study] = project
    return mapping


def _header(seeds_dir: str, name: str) -> list[str]:
    with open(Path(seeds_dir) / name, encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle), [])


def _to_int(text: str) -> int:
    if "_" in text:  # Python accepts 1_000; a CSV number should not
        raise ValueError(f"not an integer: {text!r}")
    return int(text)


def _to_double(text: str) -> float:
    if "_" in text:
        raise ValueError(f"not a number: {text!r}")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {text!r}")
    return value


def _to_bool(text: str) -> bool:
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    raise ValueError(f"not true or false: {text!r}")


_CONVERTERS: dict[type[DataType], Callable[[str], object]] = {
    StringType: str,
    LongType: _to_int,
    IntegerType: _to_int,
    DoubleType: _to_double,
    BooleanType: _to_bool,
    DateType: date.fromisoformat,
}


def seed_df(spark: SparkSession, seeds_dir: str, name: str, schema: StructType) -> DataFrame:
    """A seed read strictly into `schema`; a blank cell is null.

    The header must equal `schema.fieldNames()` exactly. A value that does not convert to its
    column's type raises ValueError naming the file, the row and the column.
    """
    header = _header(seeds_dir, name)
    if header != schema.fieldNames():
        raise ValueError(f"seed {name}: header {header} is not the schema's {schema.fieldNames()}")
    rows = read_seed_rows(seeds_dir, name)
    converters = []
    for field in schema.fields:
        convert = _CONVERTERS.get(type(field.dataType))
        if convert is None:
            raise ValueError(f"seed {name}: column {field.name!r} has unsupported type {field.dataType}")
        converters.append((field.name, convert))

    typed = []
    for number, row in enumerate(rows, start=1):
        values = []
        for column, convert in converters:
            text = row[column]
            if text == "":
                values.append(None)
                continue
            try:
                values.append(convert(text))
            except ValueError as error:
                raise ValueError(f"seed {name}: row {number}, column {column!r}: {text!r} ({error})") from error
        typed.append(tuple(values))
    return spark.createDataFrame(typed, schema)
