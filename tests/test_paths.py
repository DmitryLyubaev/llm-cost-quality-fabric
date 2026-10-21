import pytest

from lakehouse.paths import LakePaths


def test_table_paths_are_schema_style():
    paths = LakePaths("/lh/Tables", "/lh/Files")

    assert paths.table("gold", "fact_daily_cost") == "/lh/Tables/gold/fact_daily_cost"
    assert paths.table("silver", "daily_cost") == "/lh/Tables/silver/daily_cost"
    assert paths.table("reject", "eval_unresolved") == "/lh/Tables/reject/eval_unresolved"


@pytest.mark.parametrize("layer", ["bronze", "private", "", "Gold"])
def test_unknown_layer_raises(layer):
    with pytest.raises(ValueError, match="layer"):
        LakePaths("/lh/Tables", "/lh/Files").table(layer, "t")


def test_bronze_local_uses_mount_when_given():
    paths = LakePaths("Tables", "Files", files_mount="/lakehouse/default/Files")

    assert paths.bronze == "Files/bronze"
    assert paths.bronze_local == "/lakehouse/default/Files/bronze"
    assert paths.private == "Files/bronze/private"


def test_bronze_local_defaults_to_the_files_root():
    paths = LakePaths("/lh/Tables", "/lh/Files")

    assert paths.bronze_local == paths.bronze == "/lh/Files/bronze"


def test_local_layout():
    paths = LakePaths.local("/tmp/lake")

    assert paths == LakePaths("/tmp/lake/Tables", "/tmp/lake/Files")
    assert paths.table("silver", "t") == "/tmp/lake/Tables/silver/t"
    assert paths.bronze == "/tmp/lake/Files/bronze"
    assert paths.private == "/tmp/lake/Files/bronze/private"
