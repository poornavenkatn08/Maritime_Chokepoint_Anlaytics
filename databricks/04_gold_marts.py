# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 4 - Gold marts
# MAGIC Analysis-ready tables. Pre-aggregated so the 2X-Small warehouse never
# MAGIC scans the 5.76M-row port table interactively.

# COMMAND ----------
from pyspark.sql import functions as F, Window
CATALOG, SCHEMA = "supply", "portwatch"   # must match 02_bronze_load.py
spark.sql(f"USE {CATALOG}.{SCHEMA}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## fct_chokepoint_daily - the core analysis panel

# COMMAND ----------

(spark.table("silver_chokepoint_features")
   .select("portid","portname","date","year","month","day_of_week",
           "n_total","n_container","n_tanker","n_dry_bulk","n_general_cargo","n_roro",
           "capacity","ma_7","ma_28","ma_365","yoy_pct","index_vs_365d_baseline","log_transits")
   .write.mode("overwrite").option("overwriteSchema","true")
   .saveAsTable("fct_chokepoint_daily"))

# COMMAND ----------
# MAGIC %md ## mart_corridor_substitution
# MAGIC Paired corridors that are routing alternatives for the same cargo.
# MAGIC A substitution ratio is descriptive: it does NOT establish that a vessel
# MAGIC absent from one corridor is the same vessel present in the other.

# COMMAND ----------
PAIRS = [("Suez Canal","Cape of Good Hope","asia_europe"),
         ("Bab el-Mandeb Strait","Cape of Good Hope","asia_europe"),
         ("Panama Canal","Magellan Strait","americas_interocean")]
rows = []
for origin, alt, corridor in PAIRS:
    rows.append(spark.table("fct_chokepoint_daily")
        .filter(F.col("portname").isin(origin, alt))
        .groupBy("date").pivot("portname",[origin,alt]).agg(F.first("ma_28"))
        .withColumnRenamed(origin,"origin_ma28").withColumnRenamed(alt,"alt_ma28")
        .withColumn("origin", F.lit(origin)).withColumn("alternative", F.lit(alt))
        .withColumn("corridor", F.lit(corridor)))
sub = rows[0]
for r in rows[1:]:
    sub = sub.unionByName(r)
(sub.filter("origin_ma28 IS NOT NULL AND alt_ma28 IS NOT NULL")
    .withColumn("substitution_ratio", F.col("alt_ma28")/F.col("origin_ma28"))
    .write.mode("overwrite").option("overwriteSchema","true")
    .saveAsTable("mart_corridor_substitution"))

# COMMAND ----------
# MAGIC %md ## mart_event_study_panel
# MAGIC Chokepoint x relative-day, the exact shape the estimator consumes.

# COMMAND ----------
events = spark.table("silver_disruption_events").filter("event_class = 'natural_hazard'")
panel = (spark.table("fct_chokepoint_daily").alias("f")
    .crossJoin(events.select("eventid","fromdate","eventtype").alias("e"))
    .withColumn("rel_day", F.datediff("f.date","e.fromdate"))
    .filter("rel_day BETWEEN -365 AND 365")
    .select("eventid","eventtype","portid","portname","f.date","rel_day",
            "n_total","log_transits"))
panel.write.mode("overwrite").option("overwriteSchema","true").saveAsTable("mart_event_study_panel")

# COMMAND ----------
# MAGIC %md
# MAGIC ## mart_port_disruption_scorecard - uses the 5.76M-row table

# COMMAND ----------

affected = (spark.table("silver_disruption_events")
    .select("eventid","eventtype","fromdate","todate",
            F.explode(F.split(F.col("affectedports"), r"[,;]\s*")).alias("portid"))
    .withColumn("portid", F.trim("portid")).filter("portid <> ''"))
pd_ = spark.table("silver_port_daily")
sc = (affected.join(pd_, "portid")
      .withColumn("rel_day", F.datediff(pd_["date"], affected["fromdate"]))
      .filter("rel_day BETWEEN -90 AND 90")
      .withColumn("window", F.when(F.col("rel_day") < 0, "pre").otherwise("post"))
      .groupBy("eventid","eventtype","portid","window")
      .agg(F.avg("portcalls").alias("avg_portcalls"), F.count("*").alias("n_days")))
(sc.groupBy("eventid","eventtype","portid")
   .pivot("window",["pre","post"]).agg(F.first("avg_portcalls"))
   .withColumn("pct_change", (F.col("post")/F.col("pre")-1)*100)
   .write.mode("overwrite").option("overwriteSchema","true")
   .saveAsTable("mart_port_disruption_scorecard"))

# COMMAND ----------
for t in ["fct_chokepoint_daily","mart_corridor_substitution",
          "mart_event_study_panel","mart_port_disruption_scorecard"]:
    print(f"{t:36s} {spark.table(t).count():>12,} rows")
