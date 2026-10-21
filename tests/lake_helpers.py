"""Shared helpers for the tests that load the synthetic fixture lake (tests/fixtures/lake)."""

import shutil
from datetime import date
from pathlib import Path

from lakehouse.paths import LakePaths
from lakehouse.run import run_load

ROOT = Path(__file__).resolve().parent.parent
SEEDS = str(ROOT / "seeds")
LAKE_FIXTURE = ROOT / "tests" / "fixtures" / "lake"
COST_FIXTURE = ROOT / "tests" / "fixtures" / "cost_export_2026-10.csv"
FIGURES = str(LAKE_FIXTURE / "published_figures.csv")
LOAD_DATE = date(2026, 10, 21)


def make_lake(root: Path) -> tuple[LakePaths, str]:
    """A lake under `root` with the fixture bronze tree and private extracts; return (paths, seeds_dir).

    The seeds are a copy of the real ones, except that published_figures.csv holds the fixture's
    own figures (the real figures describe the real data).
    """
    paths = LakePaths.local(str(root / "lake"))
    shutil.copytree(LAKE_FIXTURE / "bronze", paths.bronze)
    shutil.copytree(LAKE_FIXTURE / "private", paths.private)
    seeds = root / "seeds"
    shutil.copytree(ROOT / "seeds", seeds)
    shutil.copyfile(FIGURES, seeds / "published_figures.csv")
    return paths, str(seeds)


def copy_lake(paths: LakePaths, target: Path) -> LakePaths:
    """A copy of a local lake's tables and files under `target`, to run against again."""
    copy = LakePaths.local(str(target))
    shutil.copytree(paths.tables_root, copy.tables_root)
    shutil.copytree(paths.files_root, copy.files_root)
    return copy


def run(spark, paths, seeds, export=COST_FIXTURE, load_date=LOAD_DATE, **options):
    return run_load(spark, paths, seeds, str(export), load_date, **options)
