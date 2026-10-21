import pytest

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
