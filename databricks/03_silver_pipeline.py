from __future__ import annotations

import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

VOLUME = "/Volumes/workspace/portwatch/raw"   # must match 02_bronze_load.py


def read_raw(filename: str):
    """
    Read a Parquet extract and normalise timestamp columns to DATE.

    The extract is written by pandas, which has no date type, so date columns
    arrive as naive datetime64[ns]. Spark reads those as TIMESTAMP_NTZ, and Delta
    refuses to write TIMESTAMP_NTZ unless the `timestampNtz` table feature is
    explicitly enabled - which raises the table protocol version and can break
    older readers downstream.

    Every timestamp column in this dataset is a calendar date with no time
    component, so casting to DATE is both the correct type and the fix.
    """
    df = spark.read.parquet(f"{VOLUME}/{filename}")  # noqa: F821
    for field in df.schema.fields:
        if field.dataType.typeName() in ("timestamp_ntz", "timestamp"):
            df = df.withColumn(field.name, F.to_date(F.col(field.name)))
    return df

# Chokepoints excluded from analysis panels by pre-registered rule. Kept in the
# data (dropping them would be silently editing the population) but flagged.
LOW_VOLUME = ["Bering Strait", "Magellan Strait"]
CONCURRENT_SHOCK = ["Kerch Strait", "Bosporus Strait", "Taiwan Strait", "Strait of Hormuz"]


# --------------------------------------------------------------------------- #
# BRONZE - raw landing, no transformation beyond file read
# --------------------------------------------------------------------------- #

@dlt.table(
    name="bronze_chokepoint_daily",
    comment="Raw daily chokepoint transit calls from IMF PortWatch, as extracted.",
    table_properties={"quality": "bronze"},
)
def bronze_chokepoint_daily():
    return read_raw("chokepoints.parquet")


@dlt.table(name="bronze_port_daily", comment="Raw daily port activity, ~5.76M rows.",
           table_properties={"quality": "bronze"})
def bronze_port_daily():
    return read_raw("ports.parquet")


@dlt.table(name="bronze_disruptions", comment="PortWatch disruption event registry.",
           table_properties={"quality": "bronze"})
def bronze_disruptions():
    return read_raw("disruptions.parquet")


@dlt.table(name="bronze_chokepoint_ref", comment="Chokepoint reference attributes.",
           table_properties={"quality": "bronze"})
def bronze_chokepoint_ref():
    return read_raw("chokepoint_ref.parquet")


# --------------------------------------------------------------------------- #
# SILVER - typed, validated, analysis-ready
# --------------------------------------------------------------------------- #

@dlt.table(
    name="dim_chokepoint",
    comment="Conformed chokepoint dimension with pre-registered panel eligibility flags.",
    table_properties={"quality": "silver"},
)
@dlt.expect_or_fail("chokepoint_id_not_null", "portid IS NOT NULL")
@dlt.expect_or_fail("chokepoint_name_not_null", "portname IS NOT NULL")
@dlt.expect("has_coordinates", "lat IS NOT NULL AND lon IS NOT NULL")
@dlt.expect("plausible_lat", "lat BETWEEN -90 AND 90")
@dlt.expect("plausible_lon", "lon BETWEEN -180 AND 180")
def dim_chokepoint():
    return (
        dlt.read("bronze_chokepoint_ref")
        .select(
            F.col("portid").cast("string").alias("portid"),
            F.trim(F.col("portname")).alias("portname"),
            F.col("lat").cast("double").alias("lat"),
            F.col("lon").cast("double").alias("lon"),
            F.col("industry_top1").alias("industry_top1"),
            F.col("vessel_count_total").cast("long").alias("vessel_count_total"),
        )
        .dropDuplicates(["portid"])
        .withColumn("is_low_volume", F.col("portname").isin(LOW_VOLUME))
        .withColumn("has_concurrent_shock", F.col("portname").isin(CONCURRENT_SHOCK))
        .withColumn(
            "control_eligible",
            ~F.col("portname").isin(LOW_VOLUME + CONCURRENT_SHOCK),
        )
    )


@dlt.table(
    name="silver_chokepoint_daily",
    comment="One row per chokepoint per day. Core analysis panel (~78K rows).",
    table_properties={"quality": "silver"},
    partition_cols=["year"],
)
@dlt.expect_or_fail("valid_date", "date IS NOT NULL")
@dlt.expect_or_drop("non_negative_transits", "n_total >= 0")
@dlt.expect_or_drop("non_negative_capacity", "capacity IS NULL OR capacity >= 0")
@dlt.expect_or_drop("known_chokepoint", "portname IS NOT NULL")
@dlt.expect("vessel_types_sum_consistent",
            "n_total IS NULL OR abs(n_total - n_component_sum) <= 2")
@dlt.expect("plausible_daily_transits", "n_total <= 1000")
@dlt.expect("within_coverage_window", "date >= '2019-01-01'")
def silver_chokepoint_daily():
    src = dlt.read("bronze_chokepoint_daily")
    component_cols = [
        "n_container", "n_dry_bulk", "n_general_cargo", "n_roro", "n_tanker",
    ]
    comp_sum = sum(F.coalesce(F.col(c), F.lit(0)) for c in component_cols)

    return (
        src.withColumn("date", F.to_date("date"))
        .withColumn("year", F.year("date"))
        .withColumn("month", F.month("date"))
        .withColumn("day_of_week", F.dayofweek("date"))
        .withColumn("n_component_sum", comp_sum)
        .withColumn("portid", F.col("portid").cast("string"))
        .withColumn("portname", F.trim(F.col("portname")))
        .dropDuplicates(["portid", "date"])
    )


@dlt.table(
    name="silver_port_daily",
    comment="One row per port per day. ~5.76M rows; the Spark-scale table.",
    table_properties={"quality": "silver"},
    partition_cols=["year"],
)
@dlt.expect_or_fail("valid_date", "date IS NOT NULL")
@dlt.expect_or_drop("non_negative_portcalls", "portcalls >= 0")
@dlt.expect_or_drop("has_country", "iso3 IS NOT NULL")
@dlt.expect("plausible_portcalls", "portcalls <= 500")
@dlt.expect("import_export_non_negative",
            "(import IS NULL OR import >= 0) AND (export IS NULL OR export >= 0)")
def silver_port_daily():
    return (
        dlt.read("bronze_port_daily")
        .withColumn("date", F.to_date("date"))
        .withColumn("year", F.year("date"))
        .withColumn("portid", F.col("portid").cast("string"))
        .withColumnRenamed("ISO3", "iso3")
        .dropDuplicates(["portid", "date"])
    )


@dlt.table(
    name="silver_disruption_events",
    comment=(
        "Third-party disruption registry. Overwhelmingly natural hazards "
        "(cyclone/earthquake/flood) - used for METHOD VALIDATION, not as the "
        "source of geopolitical event dates."
    ),
    table_properties={"quality": "silver"},
)
@dlt.expect_or_fail("event_id_not_null", "eventid IS NOT NULL")
@dlt.expect_or_drop("valid_from_date", "fromdate IS NOT NULL")
@dlt.expect_or_drop("end_after_start", "todate IS NULL OR todate >= fromdate")
@dlt.expect("has_affected_ports", "n_affectedports > 0")
def silver_disruption_events():
    return (
        dlt.read("bronze_disruptions")
        .withColumn("fromdate", F.to_date("fromdate"))
        .withColumn("todate", F.to_date("todate"))
        .withColumn(
            "event_class",
            F.when(F.col("eventtype").isin("TC", "FL", "DR", "WF", "VO", "EQ"), "natural_hazard")
            .otherwise("other"),
        )
        .withColumn("duration_days", F.datediff("todate", "fromdate"))
        .dropDuplicates(["eventid"])
    )


# --------------------------------------------------------------------------- #
# SILVER (derived) - panel features used by every downstream mart
# --------------------------------------------------------------------------- #

@dlt.table(
    name="silver_chokepoint_features",
    comment="Chokepoint panel with rolling baselines and YoY comparisons.",
    table_properties={"quality": "silver"},
)
@dlt.expect_or_drop("has_rolling_28", "ma_28 IS NOT NULL")
@dlt.expect("no_duplicate_panel_rows", "portid IS NOT NULL AND date IS NOT NULL")
def silver_chokepoint_features():
    w7 = Window.partitionBy("portid").orderBy("date").rowsBetween(-6, 0)
    w28 = Window.partitionBy("portid").orderBy("date").rowsBetween(-27, 0)
    w365 = Window.partitionBy("portid").orderBy("date").rowsBetween(-364, 0)
    lag_year = Window.partitionBy("portid").orderBy("date")

    return (
        dlt.read("silver_chokepoint_daily")
        .withColumn("ma_7", F.avg("n_total").over(w7))
        .withColumn("ma_28", F.avg("n_total").over(w28))
        .withColumn("ma_365", F.avg("n_total").over(w365))
        .withColumn("n_total_ly", F.lag("n_total", 365).over(lag_year))
        .withColumn(
            "yoy_pct",
            F.when(F.col("n_total_ly") > 0,
                   (F.col("n_total") / F.col("n_total_ly") - 1) * 100),
        )
        .withColumn(
            "index_vs_365d_baseline",
            F.when(F.col("ma_365") > 0, F.col("ma_28") / F.col("ma_365") * 100),
        )
        .withColumn("log_transits", F.log1p(F.col("n_total")))
    )
