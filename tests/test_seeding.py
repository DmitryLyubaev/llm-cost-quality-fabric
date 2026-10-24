import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest
from pyspark.sql.types import DateType, DoubleType, LongType, StringType, StructField, StructType

from lakehouse.seeding import SEEDS_DIR, read_seed_rows, read_study_projects, seed_df

SCHEMA = StructType(
    [
        StructField("name", StringType()),
        StructField("count", LongType()),
        StructField("rate", DoubleType()),
        StructField("day", DateType()),
    ]
)
ROOT = Path(__file__).resolve().parent.parent


def _seed(tmp_path, text, name="things.csv"):
    (tmp_path / name).write_text(text, encoding="utf-8", newline="")
    return str(tmp_path)


def test_seed_df_matches_schema(spark, tmp_path):
    seeds = _seed(tmp_path, 'name,count,rate,day\na,3,0.5,2026-10-09\n"b, c",,,\n')

    df = seed_df(spark, seeds, "things.csv", SCHEMA)

    assert df.schema == SCHEMA
    rows = [tuple(r) for r in df.orderBy("name").collect()]
    assert rows == [("a", 3, 0.5, date(2026, 10, 9)), ("b, c", None, None, None)]


def test_seed_df_bad_value_names_row_and_column(spark, tmp_path):
    seeds = _seed(tmp_path, "name,count,rate,day\na,1,0.5,2026-10-09\nb,two,0.5,2026-10-09\n")

    with pytest.raises(ValueError, match=r"things\.csv.*row 2.*column 'count'.*'two'"):
        seed_df(spark, seeds, "things.csv", SCHEMA)


def test_header_mismatch_raises(spark, tmp_path):
    seeds = _seed(tmp_path, "name,rate,count,day\na,0.5,1,2026-10-09\n")

    with pytest.raises(ValueError, match=r"things\.csv.*header"):
        seed_df(spark, seeds, "things.csv", SCHEMA)


def test_read_seed_rows_rejects_wrong_field_count(tmp_path):
    seeds = _seed(tmp_path, "a,b\n1,2\n3\n")

    with pytest.raises(ValueError, match=r"things\.csv.*row 2"):
        read_seed_rows(seeds, "things.csv")


def test_read_seed_rows_rejects_empty_file(tmp_path):
    seeds = _seed(tmp_path, "")

    with pytest.raises(ValueError, match=r"things\.csv.*header"):
        read_seed_rows(seeds, "things.csv")


def test_packaged_seeds_dir_holds_the_seeds():
    names = {p.name for p in Path(SEEDS_DIR).iterdir()}

    assert {"dim_arm.csv", "ingest_manifest.json", "rg_rules.csv", "study_projects.csv"} <= names
    assert Path(SEEDS_DIR).parts[-2:] == ("lakehouse", "seeds")


def _build_wheel(tmp_path) -> Path:
    out = tmp_path / "dist"
    args = [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(ROOT)]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:  # build isolation needs the network for setuptools
        result = subprocess.run([*args, "--no-isolation"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    (wheel,) = out.glob("lakehouse-*.whl")
    return wheel


def test_wheel_does_not_require_spark_at_runtime(tmp_path):
    # Fabric already provides Spark and Delta; the wheel must not pull them in on %pip install.
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as z:
        (meta,) = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
        requires = [
            line.split(":", 1)[1].strip()
            for line in z.read(meta).decode("utf-8").splitlines()
            if line.startswith("Requires-Dist:")
        ]
    names = ("pyspark", "delta-spark")
    spark = [r for r in requires if r.split(";")[0].strip().lower().startswith(names)]
    assert spark, requires  # still declared, in the spark extra
    for r in spark:
        assert 'extra == "spark"' in r, r
    runtime = [r for r in requires if "extra ==" not in r]
    assert [r.split(">")[0].split("=")[0].strip() for r in runtime] == ["requests"], runtime


def test_wheel_contains_seeds(tmp_path):
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
    expected = {p.name for p in Path(SEEDS_DIR).iterdir() if p.suffix in (".csv", ".json")}
    assert expected  # the seeds exist
    for name in expected:
        assert f"lakehouse/seeds/{name}" in names


def test_study_projects_seed_maps_each_study_and_cites_its_report():
    assert read_study_projects(SEEDS_DIR) == {"p1-three-arm": "p1", "p2-retrieval": "p2", "p3-agent": "p3"}
    assert all(row["evidence"].strip() for row in read_seed_rows(SEEDS_DIR, "study_projects.csv"))


def test_study_projects_refuses_a_wrong_header_a_blank_cell_or_a_repeated_study(tmp_path):
    for text, message in (
        ("study,project\np1-three-arm,p1\n", r"header"),
        ("study,project,evidence\np1-three-arm,,x\n", r"row 1.*blank"),
        ("study,project,evidence\na,p1,x\na,p2,y\n", r"row 2.*'a' appears twice"),
    ):
        seeds = _seed(tmp_path, text, name="study_projects.csv")
        with pytest.raises(ValueError, match=message):
            read_study_projects(seeds)
