"""
07_event_study.py
-----------------
Phase 5b: apply the FROZEN specification to the pre-registered geopolitical windows.

DO NOT RUN THIS UNTIL:
  1. config/event_table.yml is committed to git, and
  2. 06_validate_method.py has been run and its output committed.

The whole credibility of this project rests on that ordering. If the event windows
are chosen after seeing results, the analysis is a story rather than a test, and an
interviewer who has done causal work will hear the difference.

Usage:
    python 07_event_study.py --data data/gold/chokepoint_daily.parquet
    python 07_event_study.py --event red_sea_carrier_suspension --daily
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from eventstudy import (  # noqa: E402
    build_panel,
    check_parallel_trends,
    estimate_did,
    event_study_path,
)

warnings.filterwarnings("ignore", category=FutureWarning)

CONFIG = Path(__file__).parent.parent / "config" / "event_table.yml"
OUT_DIR = Path("results")

N_PERMUTATIONS = 2000
N_BOOTSTRAP = 2000


def provenance() -> dict:
    """
    Record what the spec was when this ran.

    Primary mechanism is the hash freeze in config/FROZEN.json, which needs no
    git. Git details are recorded too when available, but are not required.
    """
    import freeze_spec

    record = {"run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    frozen = freeze_spec.load()
    if frozen is None:
        record["frozen"] = False
        record["spec_changed_since_freeze"] = None
        print("NOT FROZEN. Run this first:\n  python analysis/freeze_spec.py\n")
    else:
        changed = freeze_spec.compare(frozen)
        record["frozen"] = True
        record["frozen_at_utc"] = frozen["frozen_at_utc"]
        record["freeze_note"] = frozen.get("note", "")
        record["hashes"] = frozen["hashes"]
        record["spec_changed_since_freeze"] = changed
        record["n_prior_freezes"] = len(frozen.get("history", []))
        if changed:
            print("SPEC CHANGED SINCE FREEZE - these files differ:")
            for rel in changed:
                print(f"  {rel}")
            print("Results from this run are NOT a test of the frozen spec.\n")

    # Optional git corroboration; absent git is fine.
    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(args, text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except Exception:  # noqa: BLE001
            return None

    sha = run("git", "rev-parse", "HEAD")
    if sha:
        dirty = run("git", "status", "--porcelain")
        record["git"] = {
            "commit": sha,
            "working_tree_clean": dirty == "",
            "event_table_last_committed": run(
                "git", "log", "-1", "--format=%cI", "--", str(CONFIG)),
        }

    return record


def load_events() -> dict:
    if not CONFIG.exists():
        raise SystemExit(f"missing {CONFIG} - the event table must exist and be committed first")
    return yaml.safe_load(CONFIG.read_text())


def run_event(df: pd.DataFrame, name: str, spec: dict, freq: str) -> dict:
    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
    print(f"  hypothesis : {spec['hypothesis']}")
    print(f"  event date : {spec['event_date']}  ({spec['date_rationale']})")
    print(f"  treated    : {', '.join(spec['treated'])}")
    print(f"  controls   : {len(spec['controls'])} chokepoints")

    # Chokepoints on the same route are not independent units. Bab el-Mandeb and
    # Suez are two points on one corridor. Randomization inference must draw
    # placebo sets from within a corridor, or the null is too narrow and the
    # p-value is anti-conservative.
    corridors = spec.get("corridors")
    if corridors:
        df = df.copy()
        df["corridor"] = df["portname"].map(corridors).fillna("other")

    panel = build_panel(
        df,
        treated=spec["treated"],
        controls=spec["controls"],
        event_date=spec["event_date"],
        pre_days=spec.get("pre_days", 365),
        post_days=spec.get("post_days", 365),
        freq=freq,
        group_col="corridor" if corridors else None,
    )

    pt = check_parallel_trends(panel)
    print(f"\n  PARALLEL TRENDS\n    treated {pt.treated_slope:+.3f}/yr | "
          f"control {pt.control_slope:+.3f}/yr | diff {pt.differential:+.3f} (p={pt.p_value:.3f})")
    print(f"    {pt.verdict()}")

    # Pre-committed decision rule: if pre-trends do not pass cleanly, the
    # unit-trend specification is the headline and plain TWFE is the sensitivity.
    primary_trends = pt.verdict().startswith(("WEAK", "FAIL"))
    print(f"    -> headline spec: {'TWFE + unit trends' if primary_trends else 'TWFE'}")

    results = {}
    for use_trends in (False, True):
        res = estimate_did(
            panel,
            unit_trends=use_trends,
            n_permutations=N_PERMUTATIONS,
            n_bootstrap=N_BOOTSTRAP,
        )
        label = "with_unit_trends" if use_trends else "twfe"
        results[label] = {
            "beta": res.beta,
            "pct_effect": res.pct_effect,
            "ci_low_pct": res.ci_low,
            "ci_high_pct": res.ci_high,
            "se_cluster": res.se_cluster,
            "p_randomization": res.p_ri,
            "n_obs": res.n_obs,
            "n_treated": res.n_treated,
            "n_control": res.n_control,
        }
        print(f"\n  {res.spec}"); print(res.summary())

    path = event_study_path(panel)
    leads = path[path.rel < 0]
    print(f"\n  LEAD CHECK (should be near zero if the design is valid)")
    print(f"    mean lead {leads.pct.mean():+.1f}% | max |lead| {leads.pct.abs().max():.1f}%")

    OUT_DIR.mkdir(exist_ok=True)
    path.to_csv(OUT_DIR / f"event_path_{name}.csv", index=False)

    return {
        "event": name,
        "spec": spec,
        "headline_spec": "with_unit_trends" if primary_trends else "twfe",
        "parallel_trends": {
            "treated_slope_per_yr": pt.treated_slope,
            "control_slope_per_yr": pt.control_slope,
            "differential": pt.differential,
            "p_value": pt.p_value,
            "verdict": pt.verdict(),
        },
        "estimates": results,
        "lead_check": {
            "mean_lead_pct": float(leads.pct.mean()),
            "max_abs_lead_pct": float(leads.pct.abs().max()),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/gold/chokepoint_daily.parquet")
    ap.add_argument("--event", help="run a single event by key; default runs all")
    ap.add_argument("--daily", action="store_true", help="daily panel instead of weekly")
    args = ap.parse_args()

    df = pd.read_parquet(args.data)
    if "portname" not in df.columns or "n_total" not in df.columns:
        raise SystemExit("expected columns 'portname', 'date', 'n_total'")
    df["date"] = pd.to_datetime(df["date"])

    cfg = load_events()
    events = cfg["events"]
    if args.event:
        events = {args.event: events[args.event]}

    prov = provenance()
    if prov.get("spec_changed_since_freeze"):
        print("Continuing, but this run is exploratory - not a frozen-spec result.\n")

    out = {"provenance": prov, "freq": "D" if args.daily else "W", "results": []}
    for name, spec in events.items():
        out["results"].append(run_event(df, name, spec, "D" if args.daily else "W"))

    OUT_DIR.mkdir(exist_ok=True)
    dest = OUT_DIR / "event_study_results.json"
    dest.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
