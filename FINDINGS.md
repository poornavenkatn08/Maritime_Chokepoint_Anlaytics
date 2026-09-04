# Findings

**Retrospective analysis of public maritime traffic data. Not a production system,
and not affiliated with the IMF.**

All estimates come from a specification frozen before the analysis ran. See
`config/FROZEN.json` for the freeze record and `config/event_table.yml` for the
pre-registered windows.

---

## Summary

When carriers suspended Red Sea routing in December 2023, traffic through Bab
el-Mandeb and Suez fell **43.4%** relative to fourteen unaffected chokepoints.
Traffic did not disappear. The Cape of Good Hope absorbed it, and the ratio of
Cape traffic to Suez traffic inverted from 0.66 to roughly 2.2 within weeks and
has stayed there for over two years.

| Finding | Estimate | 95% CI | Inference |
|---|---|---|---|
| Red Sea corridors | **−43.4%** | −51.7% to −33.8% | randomization p < 0.0005 |
| Cape of Good Hope | **+39.3%** | +31.1% to +47.7% | randomization p < 0.0005 |

Headline figures use the unit-trend specification. That choice was fixed in
advance by a rule in `07_event_study.py`: where the parallel-trends check does not
pass cleanly, unit trends become the headline and plain two-way fixed effects
become the sensitivity. Plain TWFE gives −49.3% and +76.7%. Reporting those
larger numbers instead would be selecting the answer that flatters the result.

---

## Finding 1 — The diversion

Bab el-Mandeb and Suez, measured against fourteen control chokepoints with no
plausible exposure to Asia–Europe rerouting:

- **−43.4%** (unit trends), −49.3% (TWFE)
- Randomization inference over 2,000 permutations, clustered by corridor
- Pre-period trend differential +0.104 log points/year (p = 0.765) — statistically
  indistinguishable but substantively large, which is why unit trends lead

**The lead check is the important diagnostic.** Weekly effects in the 52 weeks
*before* the event average **−4.0%**, with a maximum absolute lead of 12.7%. If
something other than the December 2023 decision were driving the result, it would
show up there. It does not.

The onset is staged rather than instantaneous: roughly −12% in the event week,
−30% by week 1, −56% by week 6. That matches carriers announcing suspensions
individually over several weeks rather than a single simultaneous stop. The
confidence band is correspondingly wide in weeks 0–3 and tightens as a new
equilibrium sets in.

## Finding 2 — The inversion

The clearest evidence needs no statistics. Cape of Good Hope transits divided by
Suez transits, 28-day moving average:

| Period | Ratio |
|---|---|
| 2019–2021 | 0.82–1.00 |
| 2022–2023 | 0.66 |
| 2024 | 2.16 |
| 2025 | 2.33 |
| 2026 YTD | 2.24 |

The ratio crosses parity within days of the event date and never returns. Note
that 2022–2023 sits *below* the 2019–2021 baseline: Suez was gaining share right
up until the diversion. That is the pre-trend the parallel-trends check flagged,
and it is why the naive TWFE estimate overstates the effect — it credits the
diversion with reversing a trend already running the other way.

## Finding 3 — The Cape estimate is weaker than it looks

Reported as corroborating evidence, not as an independent result.

- Pre-period trend differential **+0.303** log points/year — three times the Red
  Sea window's +0.104
- Maximum absolute lead **34.2%**, against 12.7% for the Red Sea window

**The lead check fails.** Cape traffic was already moving substantially before
December 2023. The unit-trend estimate of +39.3% strips out most of that drift,
which is why it is nearly half the TWFE figure of +76.7% — but a failed lead check
means the design assumption does not hold cleanly here.

The Cape panel also has a single treated unit, so its block-bootstrap interval is
coarse and randomization inference carries the inference.

The pre-registered event table already stated this was the substitution side of
the same routing decision rather than an independent test. The diagnostics agree.

## Finding 4 — A second, larger shock, kept separate

Traffic through the Strait of Hormuz collapsed from roughly 78 transits/day in
February 2026 to 3/day in March, and has remained between 3 and 13 since. That is
a ~95% decline sustained for six months, larger than anything in the Red Sea
window.

This corresponds to the 2026 Strait of Hormuz crisis and is **not** analysed here.
It postdates the Red Sea study window (which ends December 2024), so it does not
contaminate Findings 1–3. It is reported because the pipeline surfaced it from
primary AIS data, and because any 2026 comparison that blends the two events
produces a misleading picture.

The dashboard therefore carries two separate change measures and a toggle between
them. They are never averaged.

---

## Method validation

The estimator was tested on disruption events dated independently by the
IMF/Oxford team before being applied to the pre-registered windows.

| Metric | Real events | Interpretation |
|---|---|---|
| Events estimated | 49 | of 54 qualifying, from 132 in the registry |
| Median effect | −7.5% | disruptions reduce port calls |
| Share negative | 77.6% | direction is consistent |
| Share significant at α = 0.05 | 34.7% | detection power |
| Pre-trend pass rate | 0.816 | marginal against a 0.85 threshold |

**The first validation attempt failed and the failure is instructive.** A fixed
180-day post-window returned a median effect of +0.7% with 46.9% of effects
negative — a coin flip. Median event duration in this registry is 6 days, and 51
of 53 events last under two weeks. A 6-day port closure inside a 180-day window
moves the post-period mean by roughly 3% even if the port shuts completely. The
window was matched to each event's recorded duration
(`post_days = max(14, duration + 7)`), taken from the event record rather than
from any outcome. Effects then appeared.

A placebo run shifting every event date 9–15 months onto periods with no
disruption returned a median effect near zero (−0.24%) and 53.3% negative — the
estimator correctly finds nothing where there is nothing. That run also exposed
an inflated false-positive rate, traced to randomization inference drawing placebo
groups at random when real treated groups are geographically clustered.
Randomization inference now draws within correlation clusters (country for ports,
corridor for chokepoints).

**Transfer assumption.** Validation runs on the port panel, because natural
hazards affect ports rather than chokepoints. Application runs on the chokepoint
panel. Same estimator, same outcome type, different units. The claim is that this
estimator recovers known effects in analogous daily maritime count data — not
that it was validated on the identical panel.

**Composition caveat.** The 54 usable events are 42 tropical cyclones, 7 floods,
3 earthquakes, 2 other. This is predominantly a validation against cyclone-induced
port disruption, not a general one. Floods alone showed a near-null median
(+0.79%), which may mean regional flooding does not reliably halt port operations.

---

## What this does not show

See `docs/LIMITATIONS.md` in full. The short version: PortWatch figures are
satellite-AIS model estimates rather than customs-verified records; there is no
cost model, no transit-time estimate, and no vessel-level matching, so a ship
absent from one corridor cannot be shown to be the ship present in another; and
no result here is attributed to any specific policy or actor.

---

## Data

IMF PortWatch (IMF + University of Oxford), satellite AIS via the UN Global
Platform.

| Layer | Rows | Coverage |
|---|---|---|
| Daily chokepoint transits | 78,176 | 2019-01-01 → 2026-08-23, 28 chokepoints |
| Daily port activity | 5,761,350 | same window, 2,065 ports |
| Disruption registry | 132 events | 2018-10 → 2026-08 |

Extraction timestamps and file hashes are recorded in `data/raw/_manifest.json`.
