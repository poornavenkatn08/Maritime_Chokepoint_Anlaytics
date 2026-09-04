# 🌊 Maritime Chokepoint Disruption Analytics

**Difference-in-differences event study on satellite AIS data · Databricks · PySpark · Delta Lake · Tableau**

> ⚠️ Retrospective analysis of public maritime traffic data. Not a production system, and not affiliated with the IMF.

When carriers suspended Red Sea routing in December 2023, traffic through Bab el-Mandeb and Suez fell **43.4%** relative to fourteen unaffected chokepoints. Traffic did not disappear — the Cape of Good Hope absorbed it.

[![Dashboard](https://img.shields.io/badge/Tableau-Live%20Dashboard-E97627?logo=tableau&logoColor=white)](https://public.tableau.com/app/profile/poorna.venkat.neelakantam/viz/Choke_Point/RedSeaDiversion)
[![Findings](https://img.shields.io/badge/Read-Findings-blue)](FINDINGS.md)
[![Limitations](https://img.shields.io/badge/Read-Limitations-orange)](docs/LIMITATIONS.md)

---

## 🎯 The Problem

Press coverage of Suez traffic disagrees with itself. The Suez Canal Authority reports year-over-year growth in vessels and revenue; other outlets report transits still far below pre-diversion levels.

Both are right. Measured against a 2023 baseline, Suez transits in 2026 remain roughly **45% down** — so year-over-year growth is real growth off a heavily depressed base. Settling that takes a warehouse and a defensible estimator, not a headline.

**The harder question:** traffic fell after December 2023, but so what? Global shipping could have declined for a dozen unrelated reasons. Proving the diversion caused it requires showing that *comparable chokepoints didn't move*.

## 💡 The Solution

A difference-in-differences event study measuring Bab el-Mandeb and Suez against fourteen control chokepoints with no plausible Asia–Europe rerouting exposure.

| Stage | What happens |
|---|---|
| **1. Validate** | Test the estimator on 49 disruption events dated independently by IMF/Oxford, plus a placebo run on dates with no disruption |
| **2. Freeze** | Lock the specification — dates, treated units, controls, clusters — with a content-hash record |
| **3. Apply** | Run the frozen spec on the pre-registered windows |

Inference is by **randomization inference**: the treated label is reassigned 2,000 times within correlation clusters and the true estimate placed in that distribution. With fewer than 25 units, cluster-robust standard errors aren't trustworthy and are reported only as a secondary reference.

## 📈 Key Results

| Finding | Estimate | 95% CI | Inference |
|---|---|---|---|
| 🔴 **Red Sea corridors** | **−43.4%** | −51.7% to −33.8% | randomization p < 0.0005 |
| 🔵 **Cape of Good Hope** | **+39.3%** | +31.1% to +47.7% | randomization p < 0.0005 |

**The inversion.** Cape transits ÷ Suez transits, 28-day moving average:

| Period | Ratio |
|---|---|
| 2019–2021 | 0.82–1.00 |
| 2022–2023 | 0.66 |
| **2024** | **2.16** |
| 2025 | 2.33 |
| 2026 YTD | 2.24 |

The ratio crosses parity within days of the event date and never returns.

**The lead check.** Weekly effects in the 52 weeks *before* the event average **−4.0%**, max absolute lead 12.7%. If something other than the December 2023 decision were driving the result, it would show up there. It doesn't.

**A finding reported as weak.** The Cape estimate fails its lead check (max lead 34.2%, pre-trend differential +0.303/yr) and is labelled **corroborating, not independent**. Plain TWFE would have given +76.7%; the pre-registered rule selected the unit-trend spec instead.

📄 **[Full findings →](FINDINGS.md)**

## 🏗️ Architecture

```
LOCAL                        DATABRICKS FREE EDITION          PRESENTATION
─────                        ───────────────────────          ────────────
01_extract_portwatch.py
  ArcGIS REST + Hub bulk
        │
        ▼
  data/raw/*.parquet ──►  /Volumes/…/raw  (Unity Catalog volume)
                                 │
                          BRONZE │ Delta
                                 ▼
                          SILVER │ Lakeflow pipeline · 22 expectations
                                 ▼
                          GOLD   │ 5 analysis-ready marts
                                 │
              ┌──────────────────┴───────────────┐
              ▼                                  ▼
       Python: statsmodels                CSV export ──► Tableau Public
       DiD · randomization inference                     5-panel dashboard
```

**Why extraction runs locally:** Databricks Free Edition serverless compute restricts outbound internet access, so notebooks can't reach the PortWatch API. Extraction runs locally and lands Parquet in a volume — which separates extraction from compute regardless.

**Why Tableau reads CSVs:** Tableau Public can't live-connect to Databricks. Deliberate, not an oversight.

## 📊 Data

[IMF PortWatch](https://portwatch.imf.org) (IMF + University of Oxford), derived from satellite AIS via the UN Global Platform.

| Layer | Rows | Grain | Coverage |
|---|---|---|---|
| Daily chokepoint transits | 78,176 | chokepoint × day | 2019-01-01 → 2026-08-23 · 28 chokepoints |
| Daily port activity | 5,761,350 | port × day | same window · 2,065 ports |
| Disruption registry | 132 | event | 2018-10 → 2026-08 |

The disruption registry is overwhelmingly natural hazards — 72 cyclones, 32 earthquakes, 14 floods. It **validates the estimator**; it does not supply geopolitical event dates. Those live in `config/event_table.yml`, committed before any estimate ran.

## 🛠️ Technical Toolkit

| Layer | Tools |
|---|---|
| **Extraction** | Python · `requests` · ArcGIS REST API · Parquet |
| **Lakehouse** | Databricks · PySpark · Delta Lake · Unity Catalog |
| **Pipeline** | Lakeflow Declarative Pipelines · 22 data-quality expectations |
| **Statistics** | `statsmodels` · two-way fixed effects · randomization inference · block bootstrap |
| **Visualization** | Tableau Public · 5-panel dashboard with parameter-driven event toggle |
| **Reproducibility** | Content-hash spec freeze · extraction manifest with SHA-256 |

## 📁 Repository Structure

```
extract/
  01_extract_portwatch.py      Phase 1   API → Parquet (runs locally)
  01b_topup_ports.py           Phase 1b  closes a stale bulk-export cache gap
databricks/
  02_bronze_load.py            Phase 2   volume → bronze + manifest verification
  03_silver_pipeline.py        Phase 3   Lakeflow pipeline + expectations
  04_gold_marts.py             Phase 4   analysis-ready marts
  05_export_marts.py           Phase 6a  CSV export for Tableau
analysis/
  eventstudy/estimator.py                DiD · randomization inference · event paths
  06_validate_method.py        Phase 5a  validation + placebo
  freeze_spec.py                         locks the spec (no git CLI needed)
  07_event_study.py            Phase 5b  frozen spec on pre-registered windows
config/event_table.yml                   pre-registered windows and clusters
tableau/DASHBOARD_GUIDE.md               panel-by-panel build guide
```

## 🚀 Running It

See **[RUNBOOK.md](RUNBOOK.md)** — every command in order, with each failure mode encountered and what it meant.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python extract/01_extract_portwatch.py --layers chokepoints disruptions chokepoint_ref
python extract/01b_topup_ports.py
# → Databricks: 02, 03 (pipeline), 04, 05
python analysis/06_validate_method.py --ports data/gold/port_daily.parquet \
                                      --events data/gold/disruption_events.parquet
python analysis/freeze_spec.py --note "pre-registration"
python analysis/07_event_study.py --data data/gold/chokepoint_daily.parquet
```

## ⚠️ What This Cannot Show

No cost figures, no transit-time estimates, no vessel-level matching, and no attribution to any specific policy or actor. PortWatch figures are AIS-derived **model estimates**, not customs-verified records.

**[Full limitations →](docs/LIMITATIONS.md)** — written before results, updated with what went wrong during the analysis.

---

## 🔗 Related Repositories

| Repository | Description |
|---|---|
| 🏦 [Credit Risk Analytics](https://github.com/poornavenkatn08/Credit_Risk_Analytics) | dbt + BigQuery fact-constellation warehouse, ~25 automated tests |
| 📊 [Dashboard Portfolio](https://github.com/poornavenkatn08/dashboards-portfolio) | Tableau & Power BI visualizations |
| 🐍 [Python Analytics Portfolio](https://github.com/poornavenkatn08/Python_Pandas-Data-Analysis-Portfolio) | Pandas, Scikit-learn, XGBoost analysis |
| 🗄️ [SQL Projects](https://github.com/poornavenkatn08/SQL-Projects) | CTEs, window functions, ETL pipelines |

---

## 📬 Let's Connect!

📧 [poornavn08@gmail.com](mailto:poornavn08@gmail.com)  
🔗 [LinkedIn](https://www.linkedin.com/in/pneelakantam/)  
💻 [GitHub](https://github.com/poornavenkatn08)  
📊 [Tableau Public](https://public.tableau.com/app/profile/poorna.venkat.neelakantam)

---

*Data source: IMF PortWatch (IMF / University of Oxford). See [CITATION.md](CITATION.md).*
