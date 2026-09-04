"""
06_validate_method.py
---------------------
Phase 5a: validate the estimator on events nobody let me choose.

WHY THIS EXISTS
---------------
The geopolitical event windows in config/event_table.yml are dates I selected.
Even pre-registered, that is a weaker position than dates set by someone else.

The PortWatch disruption registry contains 132 events dated independently by the
IMF/Oxford team. Those dates were not chosen by me and were not chosen with this
analysis in mind.

IMPORTANT - the usable pool is much smaller than 132. Of the 132 events, 44 list
no affected ports at all, and applying the `n_affectedports >= 3` filter below
leaves 54: 42 tropical cyclones, 7 floods, 3 earthquakes, 2 other. So this is
predominantly a validation against cyclone-induced port disruption, which is
narrower than "132 events". Report 54 and the cyclone skew, not 132.

So: run the same estimator on those events first. If it behaves sensibly there -
detects real port disruptions, returns null effects on placebo assignments, shows
flat pre-trends - then applying it to the geopolitical windows is a defensible
extension rather than a fresh assertion.

TRANSFER ASSUMPTION (state this in FINDINGS.md, do not bury it)
--------------------------------------------------------------
Validation runs on the PORT panel, because natural hazards hit ports, not
chokepoints. Application runs on the CHOKEPOINT panel. The estimator, outcome
transform, and panel structure are identical; the units are not. The claim being
made is "this estimator recovers known effects in analogous daily maritime count
data", NOT "this estimator was validated on the exact panel used for the result".

Usage:
    python 06_validate_method.py --ports data/gold/port_daily.parquet \
                                 --events data/gold/disruption_events.parquet

    # false-positive check on dates with no disruption
    python 06_validate_method.py --ports data/gold/port_daily.parquet \
                                 --events data/gold/disruption_events.parquet --placebo
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from eventstudy import build_panel, check_parallel_trends, estimate_did  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)

OUT_DIR = Path("results")
MIN_PORT_CALLS = 5      # exclude near-empty ports; same rule as the chokepoint panel
N_CONTROLS = 20         # matched control ports per event
MIN_EVENTS = 25         # below this the validation is not informative


def pick_controls(ports: pd.DataFrame, treated: list[str], event_date: str,
                  n: int, seed: int) -> list[str]:
    """
    Controls are chosen by pre-period volume similarity, in a different country
    from any treated port, and never among the treated set.
    """
    lo = pd.Timestamp(event_date) - pd.Timedelta(days=365)
    pre = ports[(ports["date"] >= lo) & (ports["date"] < event_date)]

    treated_countries = set(ports.loc[ports["portid"].isin(treated), "iso3"].unique())
    volume = pre.groupby("portid").agg(mean_calls=("portcalls", "mean"),
                                       iso3=("iso3", "first"),
                                       n_days=("portcalls", "size"))
    target = volume.loc[volume.index.isin(treated), "mean_calls"].mean()

    pool = volume[
        (~volume.index.isin(treated))
        & (~volume["iso3"].isin(treated_countries))
        & (volume["mean_calls"] >= MIN_PORT_CALLS)
        & (volume["n_days"] >= 300)
    ].copy()
    if len(pool) < n:
        return []

    pool["dist"] = (pool["mean_calls"] - target).abs()
    return list(pool.nsmallest(n, "dist").index)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", default="data/gold/port_daily.parquet")
    ap.add_argument("--events", default="data/gold/disruption_events.parquet")
    ap.add_argument("--n-permutations", type=int, default=500)
    ap.add_argument("--pre-days", type=int, default=90,
                    help="baseline window before the event")
    ap.add_argument("--post-buffer", type=int, default=7,
                    help="days kept after the event ENDS")
    ap.add_argument("--min-post", type=int, default=14,
                    help="floor on the post window")
    ap.add_argument("--freq", default="D", choices=["D", "W"],
                    help="D recommended: these events are short")
    ap.add_argument("--placebo", action="store_true",
                    help=("shift every event date by 9-15 months so it lands on a "
                          "date with no disruption, keeping the same ports and "
                          "controls. This is the real false-positive test: expect "
                          "roughly 5%% significant at alpha=0.05. Much above that "
                          "means the inference is too permissive."))
    ap.add_argument("--seed", type=int, default=20260830)
    args = ap.parse_args()

    ports = pd.read_parquet(args.ports)
    ports["date"] = pd.to_datetime(ports["date"])
    ports["portid"] = ports["portid"].astype(str)

    events = pd.read_parquet(args.events)
    events["fromdate"] = pd.to_datetime(events["fromdate"])
    events = events[events["n_affectedports"].fillna(0) >= 3]
    print(f"{len(events)} candidate events with >= 3 affected ports\n")

    placebo_rng = np.random.default_rng(args.seed + 1)
    data_lo, data_hi = ports["date"].min(), ports["date"].max()
    if args.placebo:
        print("PLACEBO MODE - event dates shifted to no-disruption periods.")
        print("Any 'effect' found here is noise by construction.\n")

    rows = []
    for _, ev in events.iterrows():
        affected = [p.strip() for p in str(ev["affectedports"]).replace(";", ",").split(",")]
        affected = [p for p in affected if p and p in set(ports["portid"])]
        if len(affected) < 3:
            continue

        controls = pick_controls(ports, affected, ev["fromdate"], N_CONTROLS, args.seed)
        if not controls:
            continue

        # Window matched to how long THIS event actually lasted.
        # Median duration in this registry is 6 days; a fixed 180-day post window
        # dilutes a 6-day closure to roughly 3% of the post-period mean, which is
        # why the first pass returned nulls. Duration comes from the event record,
        # never from the outcome, so this does not tune the window to the result.
        dur = ev.get("duration_days")
        dur = 0 if pd.isna(dur) else max(0, int(dur))
        post_days = max(args.min_post, dur + args.post_buffer)

        event_date = ev["fromdate"]
        if args.placebo:
            shift = int(placebo_rng.choice([-450, -365, -270, 270, 365, 450]))
            shifted = event_date + pd.Timedelta(days=shift)
            # Keep the shifted window fully inside the data range.
            if not (data_lo + pd.Timedelta(days=args.pre_days) <= shifted
                    <= data_hi - pd.Timedelta(days=post_days)):
                continue
            event_date = shifted

        try:
            panel = build_panel(
                ports, treated=affected, controls=controls,
                event_date=str(event_date.date()),
                pre_days=args.pre_days, post_days=post_days,
                unit_col="portid", value_col="portcalls", freq=args.freq,
            )
            pt = check_parallel_trends(panel)
            res = estimate_did(panel, unit_trends=False,
                               n_permutations=args.n_permutations, n_bootstrap=0)
        except Exception as err:  # noqa: BLE001
            print(f"  skip {ev['eventid']}: {err}")
            continue

        rows.append({
            "eventid": ev["eventid"], "eventtype": ev["eventtype"],
            "eventname": ev.get("eventname"), "date": str(event_date.date()),
            "is_placebo": args.placebo,
            "n_treated": len(affected), "duration_days": dur,
            "pre_days": args.pre_days, "post_days": post_days,
            "pct_effect": res.pct_effect,
            "p_randomization": res.p_ri, "pretrend_diff": pt.differential,
            "pretrend_p": pt.p_value,
        })
        print(f"  {ev['eventtype']} {str(ev['fromdate'].date())} "
              f"n={len(affected):>3}  effect={res.pct_effect:+6.1f}%  p={res.p_ri}")

    if len(rows) < MIN_EVENTS:
        print(f"\nOnly {len(rows)} events estimated; need >= {MIN_EVENTS}. "
              "Validation is inconclusive - do not proceed to Phase 5b.")
        return 1

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(exist_ok=True)
    stem = "method_validation_placebo" if args.placebo else "method_validation"
    df.to_csv(OUT_DIR / f"{stem}.csv", index=False)

    sig = df[df["p_randomization"] < 0.05]
    summary = {
        "window": {
            "pre_days": args.pre_days,
            "post_days_rule": f"max({args.min_post}, duration + {args.post_buffer})",
            "freq": args.freq,
            "median_post_days": float(df["post_days"].median()),
        },
        "n_events": len(df),
        "median_effect_pct": float(df["pct_effect"].median()),
        "share_negative": float((df["pct_effect"] < 0).mean()),
        "mode": "placebo" if args.placebo else "real_events",
        # On real events this is DETECTION POWER. Only in --placebo mode is it
        # a false-positive rate.
        "share_significant_at_05": float(len(sig) / len(df)),
        "median_pretrend_differential": float(df["pretrend_diff"].median()),
        "share_pretrend_pass": float((df["pretrend_p"] > 0.10).mean()),
        "by_type": df.groupby("eventtype")["pct_effect"].median().round(2).to_dict(),
    }

    print("\n" + "=" * 66)
    print("METHOD VALIDATION SUMMARY")
    print("=" * 66)
    for k, v in summary.items():
        print(f"  {k:32s} {v}")

    print("\nINTERPRETATION GUIDE (decide BEFORE reading the numbers above):")
    if args.placebo:
        print("  - These dates have no disruption, so the true effect is zero.")
        print("  - share_significant_at_05 IS the false-positive rate here.")
        print("    Near 0.05 means calibrated inference. Much above means too permissive,")
        print("    and the real-event detection rate is not trustworthy.")
        print("  - median_effect_pct should sit near zero.")
    else:
        print("  - Most estimated effects should be negative. Disruptions reduce port calls.")
        print("  - share_significant_at_05 is DETECTION POWER, not a false-positive rate:")
        print("    these are real disruptions. Run with --placebo for the false-positive test.")
        print("  - share_pretrend_pass well below ~0.85 means control matching is too loose.")
    print("\n  If the estimator fails here, fix it here. Do not proceed to 07 and")
    print("  hope the geopolitical panel behaves better.")

    (OUT_DIR / f"{stem}_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {OUT_DIR}/{stem}.csv and {stem}_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
