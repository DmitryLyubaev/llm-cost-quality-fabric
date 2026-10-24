import os
import sys
import tempfile

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession


def get_spark(app_name: str = "lakehouse-tests") -> SparkSession:
    """Local, Delta-enabled Spark session with the time zone fixed to UTC."""
    # Workers must run the same Python as the driver (the venv, not the system python3).
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    builder = (
        SparkSession.builder.appName(app_name)
        .master("local[2]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.warehouse.dir", tempfile.mkdtemp(prefix="lakehouse-warehouse-"))
        .config("spark.ui.enabled", "false")
        # The status store keeps per-job and per-query records in the driver heap; a long run
        # of builds (the test suite) exhausted the default 1 GB heap, so keep fewer records.
        # 2 GB ran out on CI after about 17,000 stages once project 11's tests were added
        # (PR #7); GitHub's runner has 16 GB.
        .config("spark.driver.memory", "4g")
        .config("spark.ui.retainedJobs", "50")
        .config("spark.ui.retainedStages", "50")
        .config("spark.sql.ui.retainedExecutions", "50")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark
