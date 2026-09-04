# RUNBOOK — step by step

Every command in order. Copy-paste runnable. Estimated total: 35–45 hours of work
across roughly three weeks, but the *commands* themselves take about two hours.

Legend: **[LOCAL]** runs on your machine · **[DBX]** runs in Databricks.

---

## Phase 0 — Setup (20 min) **[LOCAL]**

```bash
git clone <your-repo-url> chokepoint-analytics
cd chokepoint-analytics

python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Verify:**
```bash
python -c "import pandas, statsmodels, requests, yaml; print('ok')"
```

> ⚠️ Never name a Python variable `C` in any analysis script. It shadows patsy's
> `C()` categorical function and produces a confusing `'list' object is not
> callable` error inside model formulas. This cost me a debugging cycle; it will
> cost you one too.

---

## Phase 1 — Extract (5 min) **[LOCAL]**

```bash
python extract/01_extract_portwatch.py --layers chokepoints disruptions chokepoint_ref
```
Runtime ~25s. Then the large table separately (it is a bulk CSV export and can
take several minutes):
```bash
python extract/01_extract_portwatch.py --layers ports
```

**Verify — these numbers should match:**
```bash
cat data/raw/_manifest.json | python -m json.tool | grep -E '"layer"|"rows"'
```

| Layer | Expected rows |
|---|---|
| chokepoints | ~78,000 (grows daily) |
| ports | ~5,760,000 |
| disruptions | 132 |
| chokepoint_ref | 28 |

**Commit now.** The manifest records extraction timestamps and file hashes; it is
your provenance record.
```bash
git add data/raw/_manifest.json && git commit -m "extract: portwatch snapshot"
```

---

## Phase 2 — Databricks setup (3–4 hrs, mostly first-time learning) **[DBX]**

1. Sign up at the Databricks Free Edition signup page. Use **Sign in with Google**
   or email OTP — SSO is not supported.
   - If you hit a 401 on signup, community reports suggest using a Gmail address
     with no dots in it.
2. Optional but recommended: **Verify with LinkedIn** in your account settings.
   This unlocks outbound internet access and limited serverless GPU. Without it,
   notebooks cannot reach external APIs — which is exactly why Phase 1 runs locally.
3. Import `databricks/02_bronze_load.py` as a notebook. Run cell 1 to create the
   catalog, schema, and volume.
4. Upload the four Parquet files **and `_manifest.json`** from `data/raw/` to
   `/Volumes/main/portwatch/raw` (Catalog Explorer → volume → Upload).
5. Run the rest of `02_bronze_load.py`.

**Verify:** the notebook asserts volume row counts against the manifest and fails
loudly if they disagree. Do not continue past a failure — re-run the extract.

**Free Edition ceilings to respect from here on:**

| Resource | Limit |
|---|---|
| Compute | Serverless only; no custom clusters |
| SQL warehouse | One, `2X-Small` |
| Jobs | Max 5 concurrent tasks |
| Lakeflow pipelines | One active pipeline **per type** |
| Languages | Python and SQL only (no R, no Scala) |
| Quota | Exceed it and compute stops for the rest of the day |

---

## Phase 3 — Lakeflow pipeline / silver (6–8 hrs) **[DBX]**

1. Workspace → **Jobs & Pipelines** → **Create pipeline** → **ETL pipeline**.
2. Source: `databricks/03_silver_pipeline.py`.
3. Settings: catalog `main`, schema `portwatch`, serverless enabled.
4. **Start** the pipeline.

**Verify:** open the Data Quality tab. Expect 8 tables and 19 expectations.

```sql
-- Every expectation, with its violation rate
SELECT * FROM event_log(TABLE(main.portwatch.silver_chokepoint_daily))
WHERE event_type = 'flow_progress';
```

**What to do when expectations fail:**

| Expectation | If it fires, it means | Action |
|---|---|---|
| `vessel_types_sum_consistent` | Component vessel counts don't sum to `n_total` | Expected at low rates — PortWatch classes some vessels outside the five reported types. Note the rate in `docs/LIMITATIONS.md`. |
| `plausible_daily_transits` | A chokepoint exceeded 1,000 transits/day | Investigate before dismissing. Likely a source change. |
| `non_negative_transits` | Negative counts | Source error; rows are dropped. Report the count. |
| `end_after_start` | Disruption `todate` precedes `fromdate` | Source error; drop and report. |

Do not loosen an expectation to make it pass. Either fix the transformation or
document the violation rate.

---

## Phase 4 — Gold marts (6–8 hrs) **[DBX]**

Run `databricks/04_gold_marts.py`.

> The `mart_port_disruption_scorecard` cell touches the 5.76M-row table. Run it
> **once**, at the end. Re-running it repeatedly is the fastest way to burn your
> daily quota. Develop against `fct_chokepoint_daily` (~78K rows) instead.

**Verify:** the final cell prints row counts for all four marts. `fct_chokepoint_daily`
should match your chokepoints extract exactly.

---

## Phase 5a — Method validation (the part that must not be skipped)

Export the two tables you need, then **[LOCAL]**:

```bash
python analysis/06_validate_method.py \
    --ports  data/gold/port_daily.parquet \
    --events data/gold/disruption_events.parquet
```

Runtime: 20–60 min depending on how many events qualify.

**Decision gate — write down your interpretation rule before reading the output:**

- Most effects negative? Good — disruptions should reduce port calls.
- If nearly *everything* is significant, your inference is too permissive. Stop
  and fix it. A method that finds an effect everywhere finds nothing.
- `share_pretrend_pass` well below ~0.85 means control matching is too loose.

If validation fails, fix it here. Do not proceed hoping the geopolitical panel
behaves better.

```bash
git add results/method_validation*.json results/method_validation.csv
git commit -m "validate: estimator behaviour on 132 independently-dated events"
```

---

## Phase 5b — Freeze, then estimate

**This ordering is the entire credibility of the project.**

```bash
# 1. Confirm the event table was committed BEFORE this run
git log -1 --format=%cI -- config/event_table.yml

# 2. Working tree must be clean
git status --porcelain      # must print nothing

# 3. Run the frozen spec
python analysis/07_event_study.py --data data/gold/chokepoint_daily.parquet
```

The script records the commit SHA and the event-table commit timestamp into
`results/event_study_results.json`, and warns if the tree is dirty.

**Reading the output:**

| Diagnostic | What you want | If it's wrong |
|---|---|---|
| Parallel trends | p > 0.10 and small differential | Headline switches to the unit-trend spec automatically |
| Lead check | Near zero | Large leads mean the effect started before the event — your date is wrong, or something else is driving it |
| Randomization p | Compare the true effect against placebo assignments | This is your primary inference, not the cluster SE |
| Bootstrap CI | With only 2 treated units it is coarse | Report it *alongside* the randomization p, never instead of it |

If a result changes your mind about a window, **do not edit the existing entry.**
Add a new one with its rationale and report both.

---

## Phase 6 — Tableau + docs (6–8 hrs)

1. **[DBX]** Run `databricks/05_export_marts.py`.
2. Download the part files from `/Volumes/main/portwatch/raw/exports`, rename each
   to `<name>.csv`, place in `tableau/data/`.
3. Follow `tableau/DASHBOARD_GUIDE.md`.
4. Fill in `FINDINGS.md` and finish `docs/LIMITATIONS.md`.

Tableau Public cannot live-connect to Databricks — extracts are the only route.
Say so in the README rather than letting a reviewer assume you didn't know.

---

## Full command sequence

```bash
# LOCAL
pip install -r requirements.txt
python extract/01_extract_portwatch.py --layers chokepoints disruptions chokepoint_ref
python extract/01_extract_portwatch.py --layers ports
git add data/raw/_manifest.json && git commit -m "extract: portwatch snapshot"

# DBX: upload to volume, run 02, create+run pipeline from 03, run 04

# LOCAL
python analysis/06_validate_method.py --ports data/gold/port_daily.parquet \
                                      --events data/gold/disruption_events.parquet
git add results/ && git commit -m "validate: estimator behaviour"
git status --porcelain          # must be empty
python analysis/07_event_study.py --data data/gold/chokepoint_daily.parquet

# DBX: run 05, download exports
# LOCAL: build dashboard, write FINDINGS.md
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `'list' object is not callable` in a formula | A variable named `C` shadows patsy's `C()` | Rename it |
| `SingularMatrixWarning` with unit trends | Average linear trend is collinear with period FE | Already handled: one unit is held out as reference |
| Compute won't start | Daily quota exhausted | Wait for reset; develop on the 78K panel |
| Extract returns 0 rows | ArcGIS pagination changed | Check `maxRecordCount` on the layer endpoint |
| Pipeline fails on `expect_or_fail` | Genuine data problem | Investigate the source; don't relax the expectation |
| `need at least 4 pre-periods` | Window too short | Increase `pre_days` in `config/event_table.yml` |
