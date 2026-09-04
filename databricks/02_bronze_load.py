CATALOG, SCHEMA, VOLUME = "workspace", "portwatch", "raw"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")

VOL = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
print("upload the Parquet files AND _manifest.json from data/raw/ to:", VOL)

# Guard against a stray duplicate schema in another catalog.
for other in [c.catalog for c in spark.sql("SHOW CATALOGS").collect() if c.catalog != CATALOG]:
    hit = spark.sql(f"SHOW SCHEMAS IN {other} LIKE '{SCHEMA}'").collect()
    if hit:
        print(f"  NOTE: {other}.{SCHEMA} also exists. Drop it so you don't split your data:")
        print(f"        spark.sql(\"DROP SCHEMA IF EXISTS {other}.{SCHEMA} CASCADE\")")

import json, os

EXPECTED = ["chokepoints", "ports", "disruptions", "chokepoint_ref"]
present = os.listdir(VOL)
print("in volume:", present, "\n")

missing = [n for n in EXPECTED if f"{n}.parquet" not in present]
if missing:
    raise SystemExit(f"upload these Parquet files first: {missing}")

mpath = f"{VOL}/_manifest.json"
if os.path.exists(mpath):
    manifest = json.load(open(mpath))
else:
    manifest = None
    print("WARNING: _manifest.json is not in the volume.")
    print("Row counts cannot be checked against the extract, and you lose the")
    print("provenance record. Upload it before trusting any downstream result.\n")

ok = True
for name in EXPECTED:
    actual = spark.read.parquet(f"{VOL}/{name}.parquet").count()
    entry = next((e for e in manifest if e.get("layer") == name), None) if manifest else None
    if entry and "rows" in entry:
        match = actual == entry["rows"]
        ok &= match
        print(f"{'OK  ' if match else 'FAIL'} {name:16s} manifest={entry['rows']:>9,}  volume={actual:>9,}")
    else:
        print(f"?    {name:16s} volume={actual:>9,}  (no manifest entry)")

assert ok, "row counts disagree with the manifest - re-run the extract before continuing"

df = spark.read.parquet(f"{VOL}/chokepoints.parquet")
print("chokepoints:", df.select("portname").distinct().count(), "(expect 28)")
df.selectExpr("min(date) AS first_day", "max(date) AS last_day").show()
df.groupBy("portname").count().orderBy("count").show(5, truncate=False)
