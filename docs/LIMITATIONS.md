# Limitations

Written before results, so that nothing here is a concession extracted after the fact.

## What the data is

PortWatch figures are **model estimates derived from satellite AIS signals**, not
customs-verified records. Transit *counts* are the more reliable series; volume
and capacity estimates carry additional modelling error. Every figure in this
project inherits that uncertainty, and no error bar here reflects it — the
confidence intervals describe sampling variation in the panel, not measurement
error in the underlying estimates.

## What this project cannot tell you

| Question | Why not |
|---|---|
| Did a specific policy or attack cause the diversion? | Multiple events overlap in every window. The design identifies a change relative to controls at a chosen date, not the mechanism. |
| How much did rerouting cost? | No freight rate, fuel, charter, or insurance data. Any dollar figure would be invented. |
| How many extra days did cargo take? | No vessel-level origin-destination routing. Corridor counts cannot be converted to transit times. |
| Is a vessel missing from corridor A the same vessel appearing in corridor B? | Counts are aggregate. The substitution ratio is descriptive co-movement, not matched vessel tracking. |
| What happens next? | This is retrospective. No forecast is offered. |

## Design limitations

- **Two treated units.** The Red Sea window has only Bab el-Mandeb and Suez. Block
  bootstrap CIs over two units are coarse; randomization inference is the primary
  inference method and the CI is reported alongside it, never instead of it.
- **Pre-trend differential.** Treated chokepoints were growing faster than controls
  pre-period. The unit-trend specification is the headline where this matters, and
  both specifications are always reported.
- **Serial correlation.** Daily maritime traffic is strongly autocorrelated. Weekly
  collapsing reduces this but does not remove it. Randomization inference does not
  depend on the error structure, which is why it is primary.
- **Validation transfer.** The estimator is validated on the *port* panel using
  natural-hazard events, then applied to the *chokepoint* panel. Same estimator,
  same outcome type, different units. The claim is "this estimator recovers known
  effects in analogous daily maritime count data" — not that it was validated on
  the identical panel.
- **No synthetic control.** A synthetic control would likely improve the
  counterfactual. Not implemented; noted rather than hidden.
- **Control selection is a judgement call.** The exclusion rules in
  `config/event_table.yml` were fixed before estimation, but they are still my
  rules. Results under alternative control sets are reported as sensitivity.

## What is out of scope, deliberately

No claim about which party was right, who was responsible, or what any government
should do. The analysis describes shipping traffic. Everything beyond that is
outside what the data supports.
