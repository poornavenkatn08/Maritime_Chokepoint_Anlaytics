"""
freeze_spec.py
--------------
Lock the analysis specification before running Phase 5b, without needing git.

WHAT THIS DOES
--------------
Hashes the files that define the analysis - the event table and the estimator -
and records those hashes with a UTC timestamp in `config/FROZEN.json`.
`07_event_study.py` then re-hashes the same files at run time and refuses to
record a clean result if anything has changed since the freeze.

WHY
---
With 28 chokepoints and 2,792 days, anyone free to pick the date, the treated
units, and the controls AFTER seeing the data will find a dramatic number. The
freeze is what separates "I tested a prediction" from "I searched until something
looked good".

HONEST LIMITATION - READ THIS
-----------------------------
A local hash file is weaker evidence than a git history. You could edit the event
table and simply re-run `freeze_spec.py --force`. Nothing here prevents that.

What it does give you:
  - a hard stop if you change the spec and forget you did
  - a timestamped record of when each freeze happened
  - a `history` list, so re-freezing leaves a visible trail rather than erasing
    the previous one

If you later put this on GitHub - even via the web upload page, no CLI needed -
the commit dates on `config/event_table.yml` and `config/FROZEN.json` become
external corroboration. That is the version worth showing an interviewer.

Usage:
    python analysis/freeze_spec.py            # freeze
    python analysis/freeze_spec.py --check    # verify without changing anything
    python analysis/freeze_spec.py --force    # re-freeze, keeping the old record
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "config" / "FROZEN.json"

# Files whose contents define the analysis. Change any of them and the
# specification has changed.
TRACKED = [
    "config/event_table.yml",
    "analysis/eventstudy/estimator.py",
    "analysis/07_event_study.py",
]


def digest(rel: str) -> str | None:
    path = ROOT / rel
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current() -> dict[str, str | None]:
    return {rel: digest(rel) for rel in TRACKED}


def load() -> dict | None:
    if not FROZEN.exists():
        return None
    try:
        return json.loads(FROZEN.read_text())
    except Exception:  # noqa: BLE001
        return None


def compare(frozen: dict) -> list[str]:
    """Return the tracked files whose contents differ from the freeze."""
    now = current()
    recorded = frozen.get("hashes", {})
    return [rel for rel in TRACKED if now.get(rel) != recorded.get(rel)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Freeze the analysis specification.")
    ap.add_argument("--check", action="store_true", help="verify only, change nothing")
    ap.add_argument("--force", action="store_true", help="re-freeze over an existing record")
    ap.add_argument("--note", default="", help="why this freeze happened")
    args = ap.parse_args()

    missing = [rel for rel in TRACKED if digest(rel) is None]
    if missing:
        print("Cannot freeze - these files are missing:")
        for rel in missing:
            print(f"  {rel}")
        return 1

    frozen = load()

    # ---- check mode -------------------------------------------------------
    if args.check:
        if not frozen:
            print("NOT FROZEN. Run: python analysis/freeze_spec.py")
            return 1
        changed = compare(frozen)
        print(f"frozen at : {frozen['frozen_at_utc']}")
        if frozen.get("note"):
            print(f"note      : {frozen['note']}")
        if changed:
            print("\nSPEC HAS CHANGED since the freeze:")
            for rel in changed:
                print(f"  {rel}")
            print("\nEither revert those files, or re-freeze with --force and say why.")
            print("Re-freezing after seeing results is only defensible if the reason")
            print("is independent of the results. Write the reason down either way.")
            return 1
        print("\nOK - all tracked files match the freeze.")
        return 0

    # ---- freeze mode ------------------------------------------------------
    if frozen and not args.force:
        changed = compare(frozen)
        if not changed:
            print(f"Already frozen at {frozen['frozen_at_utc']}, nothing changed.")
            return 0
        print(f"Already frozen at {frozen['frozen_at_utc']}, but these files changed:")
        for rel in changed:
            print(f"  {rel}")
        print("\nRe-freeze with --force --note \"reason\" if the change is justified.")
        return 1

    history = frozen.get("history", []) if frozen else []
    if frozen:
        history.append({
            "frozen_at_utc": frozen["frozen_at_utc"],
            "hashes": frozen["hashes"],
            "note": frozen.get("note", ""),
        })

    record = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": args.note,
        "hashes": current(),
        "history": history,
    }
    FROZEN.write_text(json.dumps(record, indent=2))

    print(f"FROZEN at {record['frozen_at_utc']}")
    for rel, h in record["hashes"].items():
        print(f"  {h[:16]}  {rel}")
    if history:
        print(f"\n{len(history)} earlier freeze(s) kept in history.")
    print("\nNow run: python analysis/07_event_study.py --data data/gold/chokepoint_daily.parquet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
