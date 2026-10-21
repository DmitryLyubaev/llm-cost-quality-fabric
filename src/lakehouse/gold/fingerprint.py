"""A content fingerprint per gold and reject table, to show a rebuild changed nothing."""

import hashlib

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from lakehouse.gold.build import GOLD_TABLES, REJECT_NAME
from lakehouse.paths import LakePaths


def table_fingerprint(spark: SparkSession, path: str) -> str:
    """sha256 over the table's rows, sorted by all columns, as JSON lines (UTC session)."""
    df = spark.read.format("delta").load(path)
    lines = df.orderBy(*df.columns).select(F.to_json(F.struct(*df.columns)).alias("line")).collect()
    digest = hashlib.sha256()
    for row in lines:
        digest.update(row["line"].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def gold_fingerprint(spark: SparkSession, paths: LakePaths) -> dict[str, str]:
    """Table -> fingerprint for every gold table and the reject table (same keys as build_gold)."""
    result = {name: table_fingerprint(spark, paths.table("gold", name)) for name in GOLD_TABLES}
    result[REJECT_NAME] = table_fingerprint(spark, paths.table("reject", "eval_unresolved"))
    return result
