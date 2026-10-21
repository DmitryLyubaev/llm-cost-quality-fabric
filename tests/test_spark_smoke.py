def test_delta_round_trip(spark, tmp_lake):
    path = tmp_lake.table("silver", "t")
    spark.createDataFrame([(1, "a"), (2, "b")], ["id", "name"]).write.format("delta").save(path)

    assert spark.read.format("delta").load(path).count() == 2
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"
