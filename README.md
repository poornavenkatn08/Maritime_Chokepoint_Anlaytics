# Maritime Chokepoint Disruption Analytics

**Retrospective analysis of public maritime traffic data. Not a production system,
and not affiliated with the IMF.**

Global shipping reroutes when chokepoints become unusable. This project measures
how much, using a difference-in-differences event study validated on 132
independently-dated disruptions before being applied to pre-registered
geopolitical windows.

**Stack:** Databricks (PySpark, Delta Lake, Lakeflow Declarative Pipelines) ·
Python (statsmodels) · SQL · Tableau Public

---

## The question

Press coverage of Suez traffic disagrees with itself. The Suez Canal Authority
reports year-over-year growth in vessels and revenue; other outlets report
transits still far below pre-diversion levels.

Both are true, and the primary data shows why. Measured against a 2023
pre-diversion baseline, Suez transits in 2026 YTD are still down sharply — so
year-over-year growth is real growth off a heavily depressed base. That
distinction is the kind of thing a warehouse plus a defensible estimator settles
and a headline does not.

## Data

[IMF PortWatch](https://portwatch.imf.org) (IMF + University of Oxford), derived
from satellite AIS signals via the UN Global Platform.

| Layer | Rows | Grain | Coverage |
|---|---|---|---|
| Daily chokepoint transits | ~78K | chokepoint × day | 2019-01-01 → present, 28 chokepoints |
| Daily port activity | ~5.76M | port × day | same window, 2,065 ports |
| Disruption registry | 132 | event | 2018-10 → present |
| Chokepoint reference | 28 | chokepoint | — |

The disruption registry is **overwhelmingly natural hazards** — 72 cyclones,
32 earthquakes, 14 floods, 4 droughts, 3 wildfires, 2 volcanoes, 5 other. It is
used to validate the estimator, not to supply geopolitical event dates. Those
live in `config/event_table.yml` and are committed before any estimate is run.

## Method

1. **Validate** the estimator on 132 events dated by someone else, on the port panel.
2. **Freeze** the specification and commit it.
3. **Apply** it to the pre-registered chokepoint windows.

Inference is by **randomization inference** — the treated label is reassigned
across control units thousands of times and the true estimate is placed in that
distribution. With fewer than 25 clusters, cluster-robust standard errors are not
trustworthy, so they are reported as a secondary reference only.

Parallel trends are checked and reported every time. Where they fail, the
unit-specific-trend specification becomes the headline and plain TWFE becomes the
sensitivity — a rule fixed in advance, not chosen per result.

## What this cannot tell you

No cost figures, no transit-time estimates, no vessel-level matching, no causal
attribution to any specific policy or actor. See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md),
written before results.

## Repo layout

```
extract/01_extract_portwatch.py    Phase 1  API → Parquet (runs locally)
databricks/02_bronze_load.py       Phase 2  volume → bronze, manifest verification
databricks/03_silver_pipeline.py   Phase 3  Lakeflow pipeline, 19 expectations
databricks/04_gold_marts.py        Phase 4  analysis-ready marts
databricks/05_export_marts.py      Phase 6a CSV export for Tableau
analysis/eventstudy/estimator.py            DiD, randomization inference, event paths
analysis/06_validate_method.py     Phase 5a validation on natural-hazard events
analysis/07_event_study.py         Phase 5b frozen spec on pre-registered windows
config/event_table.yml                      pre-registered windows (commit before running)
docs/                                       data model, limitations
```

## Running it

See [`RUNBOOK.md`](RUNBOOK.md) for the full step-by-step.

## Why the extract runs locally

Databricks Free Edition serverless compute restricts outbound internet access to
a limited set of trusted domains, so notebooks cannot call the PortWatch API.
Extraction runs locally and lands Parquet in a Unity Catalog volume. This
separates extraction from compute, which is the better pattern regardless.

Tableau Public cannot live-connect to Databricks, so the dashboard is built on
CSV extracts. Also deliberate.
