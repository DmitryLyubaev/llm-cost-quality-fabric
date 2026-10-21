import pytest

from lake_helpers import make_lake, run
from lakehouse.paths import LakePaths
from lakehouse.spark import get_spark


@pytest.fixture(scope="session")
def spark():
    session = get_spark()
    yield session
    session.stop()


@pytest.fixture
def tmp_lake(tmp_path) -> LakePaths:
    return LakePaths(str(tmp_path))


@pytest.fixture(scope="session")
def loaded_lake(spark, tmp_path_factory):
    """The synthetic fixture lake after one run_load (about two minutes, so shared by the session).

    Returns (paths, private_root, seeds_dir, summary). Read-only: tests that change tables or
    run again work on a copy of `paths.root`.
    """
    paths, private, seeds = make_lake(tmp_path_factory.mktemp("loaded"))
    return paths, private, seeds, run(spark, paths, private, seeds)
