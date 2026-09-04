# Tableau dashboard build guide

Three panels. Each answers one question. Resist adding a fourth.

**Data:** `tableau/data/chokepoint_weekly.csv`, `chokepoint_daily.csv`,
`corridor_substitution.csv`, plus `results/event_path_*.csv` from Phase 5b.

---

## Panel 1 — Corridor substitution over time

Dual-line chart, `chokepoint_weekly.csv`.

- Columns: `week` (continuous)
- Rows: `avg_daily_transits`
- Colour: `portname`, filtered to Suez Canal, Bab el-Mandeb, Cape of Good Hope
- Reference line at the event date, labelled with its rationale — not just the date

Reads as: two lines fall, one rises, at the same moment.

## Panel 2 — Event-study path

Line with confidence band, `results/event_path_red_sea_carrier_suspension.csv`.

- Columns: `rel` (weeks relative to event)
- Rows: `pct`
- Band: `ci_low_pct` to `ci_high_pct`
- Reference lines at x=0 and y=0

The leads sitting near zero *is* the evidence the design holds. Annotate that
directly on the panel — most readers won't know to look for it.

## Panel 3 — Chokepoint concentration

Bar chart, `chokepoint_daily.csv`, current-year filter.

- Rows: `portname` sorted descending by `avg_daily_transits`
- Colour: highlight treated chokepoints
- Tooltip: `yoy_pct` and `index_vs_365d_baseline`

Shows how much global traffic funnels through how few passages.

## KPI strip

Four tiles: current daily transits at Suez · change vs. 2023 baseline · Cape of
Good Hope change · estimated treatment effect with its randomization p-value.

## Required caption

Put this on the dashboard itself, not only in the README:

> Source: IMF PortWatch (IMF/University of Oxford), satellite AIS estimates —
> not customs-verified trade records. Retrospective analysis of public data.
> Effects are measured relative to control chokepoints; they are not attributed
> to any specific policy or actor. See repo for full limitations.

## Publishing

Tableau Public makes workbooks and their data public. That is fine — every input
here is already public. Confirm the caption is legible at default zoom before
publishing, since that is the version reviewers see.
