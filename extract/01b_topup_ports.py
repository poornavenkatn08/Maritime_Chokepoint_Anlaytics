"""
01b_topup_ports.py
------------------
Close the gap between the ArcGIS Hub bulk CSV export and the live FeatureServer.

WHY THIS EXISTS
---------------
`01_extract_portwatch.py --layers ports` pulls Daily_Ports_Data through the Hub
bulk CSV export, because paginating 5.7M rows at 1,000 rows per request would
take ~5,800 calls.

But the Hub export is a CACHE, and it lags the live service. Observed on
2026-08-30: the live service held 5,761,350 rows while the export returned
5,703,530 - a shortfall of exactly 57,820 rows, which is 2,065 ports x 28 days.
The cache was 28 days stale.

Left unfixed, the ports table ends about a month before the chokepoints table,
which is the kind of silent inconsistency that produces a wrong answer rather
than an error message.

This script reads the existing Parquet, finds its last date, pages the live
service for everything after it, and appends. Typically ~60 requests.

Usage:
    python extract/01b_topup_ports.py
    python extract/01b_topup_ports.py --file data/raw/ports.parquet --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

SERVICE = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
    "/Daily_Ports_Data/FeatureServer/0/query"
)
PAGE_SIZE = 1000
TIMEOUT = 90
MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("topup")


def _get(session: requests.Session, params: dict) -> dict:
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(SERVICE, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload
        except Exception as err:  # noqa: BLE001
            last_err = err
            wait = 2 ** attempt
            log.warning("request failed (%s), retry in %ss", err, wait)
            time.sleep(wait)
    raise RuntimeError(f"request failed after {MAX_RETRIES} attempts") from last_err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/raw/ports.parquet")
    ap.add_argument("--dry-run", action="store_true", help="report the gap, change nothing")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"{path} not found - run 01_extract_portwatch.py --layers ports first")

    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    local_max = df["date"].max()
    log.info("local file : %s rows, through %s", f"{len(df):,}", local_max.date())

    with requests.Session() as session:
        session.headers.update({"User-Agent": "portfolio-analytics/1.0 (research use)"})

        live_total = _get(session, {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
        live_max = _get(session, {
            "where": "1=1", "outFields": "date", "returnGeometry": "false",
            "resultRecordCount": 1, "orderByFields": "date DESC", "f": "json",
        })["features"][0]["attributes"]["date"]
        live_max = pd.to_datetime(live_max)

        gap_rows = live_total - len(df)
        gap_days = (live_max - local_max).days
        log.info("live svc   : %s rows, through %s", f"{live_total:,}", live_max.date())
        log.info("gap        : %s rows, %s days", f"{gap_rows:,}", gap_days)

        if gap_days <= 0:
            log.info("already current - nothing to do")
            return 0
        if args.dry_run:
            log.info("dry run - stopping here")
            return 0

        where = f"date > DATE '{local_max.date()}'"
        total = _get(session, {"where": where, "returnCountOnly": "true", "f": "json"})["count"]
        log.info("fetching %s rows in %s pages", f"{total:,}", -(-total // PAGE_SIZE))

        rows: list[dict] = []
        offset = 0
        while offset < total:
            payload = _get(session, {
                "where": where, "outFields": "*", "returnGeometry": "false",
                "resultOffset": offset, "resultRecordCount": PAGE_SIZE,
                "orderByFields": "ObjectId ASC", "f": "json",
            })
            feats = payload.get("features", [])
            if not feats:
                log.warning("empty page at offset %s, stopping", offset)
                break
            rows.extend(f["attributes"] for f in feats)
            offset += len(feats)
            if offset % 10_000 < PAGE_SIZE:
                log.info("  %s / %s", f"{offset:,}", f"{total:,}")

    if not rows:
        log.error("no rows returned; leaving the file untouched")
        return 1

    new = pd.DataFrame(rows)
    new = new.drop(columns=[c for c in ("ObjectId",) if c in new.columns])
    new.columns = [c.strip().lower() for c in new.columns]
    if pd.api.types.is_numeric_dtype(new["date"]):
        new["date"] = pd.to_datetime(new["date"], unit="ms", utc=True).dt.tz_localize(None)
    else:
        new["date"] = pd.to_datetime(new["date"])

    missing = set(df.columns) - set(new.columns)
    extra = set(new.columns) - set(df.columns)
    if missing or extra:
        log.error("schema mismatch. missing=%s extra=%s", sorted(missing), sorted(extra))
        return 1

    combined = (
        pd.concat([df, new[df.columns]], ignore_index=True)
        .drop_duplicates(subset=["portid", "date"], keep="last")
        .sort_values(["portid", "date"])
        .reset_index(drop=True)
    )

    backup = path.with_suffix(".parquet.bak")
    path.rename(backup)
    combined.to_parquet(path, index=False, compression="snappy")
    log.info("wrote %s: %s rows, through %s (backup at %s)",
             path, f"{len(combined):,}", combined["date"].max().date(), backup.name)

    # Keep the manifest honest - it is the provenance record.
    mpath = path.parent / "_manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text())
        for entry in manifest:
            if entry.get("layer") == "ports":
                entry.update({
                    "rows": int(len(combined)),
                    "date_min": str(combined["date"].min().date()),
                    "date_max": str(combined["date"].max().date()),
                    "size_mb": round(path.stat().st_size / 1_048_576, 2),
                    "topped_up_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": (
                        "Hub bulk export was stale; rows after the cached cutoff were "
                        "appended from the live FeatureServer."
                    ),
                })
        mpath.write_text(json.dumps(manifest, indent=2))
        log.info("manifest updated")

    return 0


if __name__ == "__main__":
    sys.exit(main())
