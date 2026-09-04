# Data model

```
BRONZE                          SILVER                              GOLD
──────                          ──────                              ────
bronze_chokepoint_daily ──┐
                          ├──► silver_chokepoint_daily ──┐
bronze_chokepoint_ref ────┤         (19 expectations)    ├──► fct_chokepoint_daily
                          ├──► dim_chokepoint ───────────┤
                          │                              ├──► mart_corridor_substitution
                          └──► silver_chokepoint_features┘
                                 rolling 7/28/365, YoY         ┌──► mart_event_study_panel
bronze_disruptions ──────────► silver_disruption_events ───────┤
                                                               └──► mart_port_disruption_scorecard
bronze_port_daily ───────────► silver_port_daily ──────────────────────────┘
```

## Grain

| Table | Grain | Rows |
|---|---|---|
| `silver_chokepoint_daily` | chokepoint × day | ~78K |
| `silver_port_daily` | port × day | ~5.76M |
| `silver_disruption_events` | event | 132 |
| `dim_chokepoint` | chokepoint | 28 |
| `fct_chokepoint_daily` | chokepoint × day | ~78K |
| `mart_event_study_panel` | event × chokepoint × day | large — filter before querying |
| `mart_corridor_substitution` | corridor pair × day | ~8K |
| `mart_port_disruption_scorecard` | event × port | ~2K |

## Key columns

`silver_chokepoint_features` adds `ma_7`, `ma_28`, `ma_365`, `yoy_pct`,
`index_vs_365d_baseline`, `log_transits`. The estimator consumes `n_total` and
applies `log1p` itself — `log_transits` exists for dashboard use.

`dim_chokepoint.control_eligible` encodes the pre-registered exclusion rule:
false for low-volume chokepoints (Bering, Magellan) and for those carrying their
own concurrent geopolitical shock (Kerch, Bosporus, Taiwan, Hormuz).

## Why there is no date dimension

Unlike the Credit Risk project, real calendar dates exist here, so `date` is a
genuine date column and day-of-week and month are derived directly. A separate
`dim_date` would add a join without adding information at this scale.
