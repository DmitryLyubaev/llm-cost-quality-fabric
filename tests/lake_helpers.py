"""Shared helpers for the tests that load the synthetic fixture lake (tests/fixtures/lake)."""

import importlib.util
import shutil
from datetime import date
from pathlib import Path

from lakehouse.paths import LakePaths
from lakehouse.run import run_load
from lakehouse.seeding import SEEDS_DIR

ROOT = Path(__file__).resolve().parent.parent
SEEDS = SEEDS_DIR
LAKE_FIXTURE = ROOT / "tests" / "fixtures" / "lake"
COST_EXPORT_ROOT = ROOT / "tests" / "fixtures" / "cost_export"  # a Cost Management export tree
FIGURES = str(LAKE_FIXTURE / "published_figures.csv")
FIGURES_DIR = str(LAKE_FIXTURE)  # a seeds folder as far as published_figures.csv goes
LOAD_DATE = date(2026, 10, 21)


def _load_cost_export_maker():
    """The committed fixture generator, which also writes the exports the tests build."""
    path = ROOT / "tests" / "fixtures" / "make_cost_export_fixture.py"
    spec = importlib.util.spec_from_file_location("make_cost_export_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cost_export_maker = _load_cost_export_maker()


def make_lake(root: Path) -> tuple[LakePaths, str]:
    """A lake under `root` with the fixture bronze tree and private extracts; return (paths, seeds_dir).

    The seeds are a copy of the real ones, except that published_figures.csv holds the fixture's
    own figures (the real figures describe the real data).
    """
    paths = LakePaths.local(str(root / "lake"))
    shutil.copytree(LAKE_FIXTURE / "bronze", paths.bronze)
    shutil.copytree(LAKE_FIXTURE / "private", paths.private)
    seeds = root / "seeds"
    shutil.copytree(SEEDS_DIR, seeds)
    shutil.copyfile(FIGURES, seeds / "published_figures.csv")
    return paths, str(seeds)


def copy_lake(paths: LakePaths, target: Path) -> LakePaths:
    """A copy of a local lake's tables and files under `target`, to run against again."""
    copy = LakePaths.local(str(target))
    shutil.copytree(paths.tables_root, copy.tables_root)
    shutil.copytree(paths.files_root, copy.files_root)
    return copy


def run(spark, paths, seeds, export=COST_EXPORT_ROOT, load_date=LOAD_DATE, **options):
    return run_load(spark, paths, seeds, str(export), load_date, **options)
