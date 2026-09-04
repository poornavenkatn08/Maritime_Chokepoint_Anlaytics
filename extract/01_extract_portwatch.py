"""
01_extract_portwatch.py
-----------------------
Phase 1 (local extract) for the Maritime Chokepoint Disruption project.

WHY THIS RUNS LOCALLY AND NOT IN DATABRICKS
-------------------------------------------
Databricks Free Edition serverless compute restricts outbound internet access to a
limited set of trusted domains, so a notebook cannot reliably call the ArcGIS /
PortWatch endpoints. This script therefore runs on the local machine, writes
Parquet to ./data/raw/, and those files are uploaded to a Unity Catalog volume,
which becomes the bronze landing zone. (Outbound internet can be unlocked via
LinkedIn verification on Free Edition, but the volume-landing pattern is the more
portable design and is what gets documented in the README.)

SOURCE
------
IMF PortWatch (IMF + University of Oxford), served from ArcGIS Online.
All endpoints below were resolved from the ArcGIS item registry, not guessed.

Usage:
    python 01_extract_portwatch.py --layers chokepoints disruptions
    python 01_extract_portwatch.py --layers ports          # ~5.8M rows, bulk CSV
    python 01_extract_portwatch.py --layers all
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

ARCGIS_SERVICES = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
HUB_DOWNLOAD = "https://hub.arcgis.com/api/download/v1/items/{item_id}/csv"

LAYERS = {
    # Core panel: 28 chokepoints x daily, 2019-01-01 onward. ~78K rows.
    "chokepoints": {
        "service": "Daily_Chokepoints_Data",
        "item_id": "3da2b9ca97684916b75c4013f95d18ab",
        "mode": "paginate",
        "grain": "one chokepoint-day",
    },
    # Third-party event table. 132 events, 2018-10 onward.
    # NOTE: overwhelmingly natural hazards (cyclone/earthquake/flood), NOT
    # geopolitical events. Used to validate the event-study method, not to
    # supply the geopolitical event dates. See docs/EVENT_TABLE.md.
    "disruptions": {
        "service": "portwatch_disruptions_database",
        "item_id": "d9b37bf4b2104c85aebdcc0c1d8a2ab7",
        "mode": "paginate",
        "grain": "one disruption event",
    },
    # Chokepoint reference/lookup (names, coordinates, descriptors).
    "chokepoint_ref": {
        "service": "PortWatch_chokepoints_database",
        "item_id": "fa9a5800b0ee4855af8b2944ab1e07af",
        "mode": "paginate",
        "grain": "one chokepoint",
    },
    # ~5.8M rows. Too large to page at 1000/request; use the Hub bulk CSV export.
    "ports": {
        "service": "Daily_Ports_Data",
        "item_id": "83b1bbc7b3354c5fb1f40673bb8f852e",
        "mode": "bulk",
        "grain": "one port-day",
    },
}

PAGE_SIZE = 1000  # server maxRecordCount
OUT_DIR = Path("data/raw")
TIMEOUT = 90
MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("portwatch")


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

def _get(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    """GET with exponential backoff. Raises on final failure."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as err:  # noqa: PERF203
            last_err = err
            wait = 2 ** attempt
            log.warning("request failed (%s), retrying in %ss", err.__class__.__name__, wait)
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} attempts: {url}") from last_err


def paginate_layer(session: requests.Session, service: str) -> pd.DataFrame:
    """Page through an ArcGIS FeatureServer layer and return a flat DataFrame."""
    url = f"{ARCGIS_SERVICES}/{service}/FeatureServer/0/query"

    count = _get(session, url, {"where": "1=1", "returnCountOnly": "true", "f": "json"}).json()
    total = count.get("count", 0)
    log.info("%s: %s rows to fetch", service, f"{total:,}")

    rows: list[dict] = []
    offset = 0
    while offset < total:
        payload = _get(
            session,
            url,
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "orderByFields": "ObjectId ASC",
                "f": "json",
            },
        ).json()

        if "error" in payload:
            raise RuntimeError(f"ArcGIS error on {service}: {payload['error']}")

        features = payload.get("features", [])
        if not features:
            log.warning("%s: empty page at offset %s, stopping early", service, offset)
            break

        rows.extend(f["attributes"] for f in features)
        offset += len(features)
        if offset % 10_000 < PAGE_SIZE:
            log.info("  %s: %s / %s", service, f"{offset:,}", f"{total:,}")

    df = pd.DataFrame(rows)
    if len(df) != total:
        log.warning("%s: fetched %s rows but server reported %s", service, len(df), total)
    return df


def bulk_download(session: requests.Session, item_id: str) -> pd.DataFrame:
    """Use the ArcGIS Hub export endpoint for tables too large to page."""
    meta = _get(
        session,
        HUB_DOWNLOAD.format(item_id=item_id),
        {"redirect": "false", "layers": "0"},
    ).json()

    status = meta.get("status")
    result_url = meta.get("resultUrl")
    log.info("hub export status=%s", status)

    # The cache may still be building; poll until a result URL is ready.
    waited = 0
    while not result_url and waited < 600:
        time.sleep(20)
        waited += 20
        meta = _get(
            session,
            HUB_DOWNLOAD.format(item_id=item_id),
            {"redirect": "false", "layers": "0"},
        ).json()
        result_url = meta.get("resultUrl")
        log.info("  waiting for export cache (%ss elapsed, status=%s)", waited, meta.get("status"))

    if not result_url:
        raise RuntimeError("Hub export did not become available; fall back to paginate_layer.")

    log.info("downloading bulk CSV ...")
    resp = _get(session, result_url)
    return pd.read_csv(io.BytesIO(resp.content), low_memory=False)


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

def normalise(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Light, non-destructive cleanup. Real transformation happens in silver."""
    df = df.drop(columns=[c for c in ("ObjectId", "Shape__Area", "Shape__Length") if c in df.columns])
    df.columns = [c.strip().lower() for c in df.columns]

    # ArcGIS returns some date fields as epoch-millis ints and others as ISO strings.
    for col in ("date", "fromdate", "todate", "editdate"):
        if col not in df.columns:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce", utc=True).dt.tz_localize(None)
        else:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if name == "chokepoints" and "date" in df.columns:
        df = df.sort_values(["portname", "date"]).reset_index(drop=True)
    return df


def write_parquet(df: pd.DataFrame, name: str) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.parquet"
    df.to_parquet(path, index=False, compression="snappy")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    entry = {
        "layer": name,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "file": str(path),
        "size_mb": round(path.stat().st_size / 1_048_576, 2),
        "sha256_16": digest,
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if "date" in df.columns and df["date"].notna().any():
        entry["date_min"] = str(df["date"].min().date())
        entry["date_max"] = str(df["date"].max().date())

    log.info("wrote %s (%s rows, %s MB)", path, f"{len(df):,}", entry["size_mb"])
    return entry


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="Extract IMF PortWatch layers to Parquet.")
    parser.add_argument(
        "--layers",
        nargs="+",
        default=["chokepoints", "disruptions", "chokepoint_ref"],
        choices=[*LAYERS.keys(), "all"],
    )
    args = parser.parse_args()

    selected = list(LAYERS) if "all" in args.layers else args.layers
    manifest = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": "portfolio-analytics/1.0 (research use)"})
        for name in selected:
            cfg = LAYERS[name]
            log.info("--- %s (%s) ---", name, cfg["grain"])
            try:
                if cfg["mode"] == "bulk":
                    raw = bulk_download(session, cfg["item_id"])
                else:
                    raw = paginate_layer(session, cfg["service"])
                manifest.append(write_parquet(normalise(raw, name), name))
            except Exception as err:  # noqa: BLE001
                log.error("FAILED %s: %s", name, err)
                manifest.append({"layer": name, "error": str(err)})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info("manifest -> %s", manifest_path)

    return 0 if all("error" not in m for m in manifest) else 1


if __name__ == "__main__":
    sys.exit(main())
