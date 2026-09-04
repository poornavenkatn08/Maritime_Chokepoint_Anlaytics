# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 6a - Export gold marts to CSV for Tableau Public
# MAGIC Tableau Public cannot live-connect to Databricks, so the dashboard is built
# MAGIC on extracts. This is a deliberate choice, documented in the README.
# MAGIC Exports are aggregated to keep files small enough for Tableau Public.

# COMMAND ----------
from pyspark.sql import functions as F
spark.sql("USE supply.portwatch")   # must match 02_bronze_load.py
OUT = "/Volumes/supply/portwatch/raw/exports"
dbutils.fs.mkdirs(OUT)

def export(df, name, max_rows=800_000):
    n = df.count()
    assert n <= max_rows, f"{name}: {n:,} rows exceeds the Tableau Public comfort limit"
    (df.coalesce(1).write.mode("overwrite").option("header", True).csv(f"{OUT}/{name}"))
    print(f"exported {name}: {n:,} rows")

# COMMAND ----------
export(spark.table("fct_chokepoint_daily")
         .select("portname","date","year","month","n_total","n_container","n_tanker",
                 "capacity","ma_7","ma_28","yoy_pct","index_vs_365d_baseline"),
       "chokepoint_daily")

export(spark.table("mart_corridor_substitution"), "corridor_substitution")

export(spark.table("mart_chokepoint_map"), "chokepoint_map")

# Weekly rollup keeps the dashboard responsive.
export(spark.table("fct_chokepoint_daily")
         .withColumn("week", F.date_trunc("week", F.col("date")))
         .groupBy("portname","week")
         .agg(F.avg("n_total").alias("avg_daily_transits"),
              F.avg("capacity").alias("avg_daily_capacity")),
       "chokepoint_weekly")

# COMMAND ----------
# MAGIC %md
# MAGIC Download each folder's part file from the volume, rename to `<name>.csv`,
# MAGIC and place it in `tableau/data/`. Then follow tableau/DASHBOARD_GUIDE.md.

# COMMAND ----------
display(dbutils.fs.ls(OUT))
