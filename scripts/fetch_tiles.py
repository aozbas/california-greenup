"""Download MODIS NDVI tiles for California from NASA GIBS into data/raw/.

Usage: python scripts/fetch_tiles.py --level 6 [--layer aqua] [--years 2023]
"""
import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CAPS_PATH = RAW / "WMTSCapabilities.xml"

# Aqua's composites start 8 days after Terra's.
LAYERS = {
    "terra": ("MODIS_Terra_L3_NDVI_16Day", "tiles"),
    "aqua": ("MODIS_Aqua_L3_NDVI_16Day", "tiles_aqua"),
}
LAYER, PREFIX = LAYERS["terra"]
CAPS_URL = "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml"
TILE_URL = (
    "https://gibs.earthdata.nasa.gov/wmts/epsg4326/best/"
    "{layer}/default/{date}/250m/{z}/{row}/{col}.png"
)

# GIBS tile grid: 512 px tiles from (-180, 90). A level-0 pixel is 0.5625 deg, halving each level.
CA_LON = (-124.42, -114.12)
CA_LAT = (32.52, 42.02)


def tile_range(level: int) -> tuple[range, range]:
    deg = 512 * 0.5625 / 2**level
    cols = range(int((CA_LON[0] + 180) // deg), int((CA_LON[1] + 180) // deg) + 1)
    rows = range(int((90 - CA_LAT[1]) // deg), int((90 - CA_LAT[0]) // deg) + 1)
    return rows, cols

WORKERS = 6
RETRIES = 4


def get_capabilities() -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    if not CAPS_PATH.exists():
        r = requests.get(CAPS_URL, timeout=120)
        r.raise_for_status()
        CAPS_PATH.write_bytes(r.content)
    return CAPS_PATH.read_text(encoding="utf-8")


def layer_dates() -> list[date]:
    ns = {"w": "http://www.opengis.net/wmts/1.0", "ows": "http://www.opengis.net/ows/1.1"}
    root = ET.fromstring(get_capabilities())
    for layer in root.iter("{http://www.opengis.net/wmts/1.0}Layer"):
        ident = layer.find("ows:Identifier", ns)
        if ident is None or ident.text != LAYER:
            continue
        out: list[date] = []
        for v in layer.find("w:Dimension", ns).findall("w:Value", ns):
            m = re.match(r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})/P(\d+)D", v.text)
            if not m:
                continue
            start, end, step = date.fromisoformat(m[1]), date.fromisoformat(m[2]), int(m[3])
            d = start
            while d <= end:
                out.append(d)
                d += timedelta(days=step)
        return sorted(set(out))
    raise SystemExit(f"Layer {LAYER} not found in capabilities")


def fetch_one(session: requests.Session, level: int, d: date, row: int, col: int) -> tuple[str, str]:
    path = RAW / f"{PREFIX}_L{level}" / d.isoformat() / f"{row}_{col}.png"
    if path.exists() and path.stat().st_size > 0:
        return "cached", str(path)
    url = TILE_URL.format(layer=LAYER, date=d.isoformat(), z=level, row=row, col=col)
    last = ""
    for attempt in range(RETRIES):
        try:
            r = session.get(url, timeout=60)
            if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/png"):
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                # GIBS can snap to a nearby date, so record the one it actually served.
                actual = r.headers.get("Layer-Time-Actual", "")[:10]
                if actual and actual != d.isoformat():
                    return "snapped:" + actual, str(path)
                return "ok", str(path)
            last = f"HTTP {r.status_code} {r.headers.get('Content-Type', '')}"
            if r.status_code == 200 and attempt >= 1:
                break  # a non-PNG body twice means the tile is broken on GIBS' side, so stop retrying
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(1.5 * (attempt + 1))
    return "failed:" + last, url


def main() -> int:
    global LAYER, PREFIX
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=list(LAYERS), default="terra", help="MODIS satellite (Aqua is offset 8 days from Terra)")
    ap.add_argument("--level", type=int, default=6, choices=range(0, 9), help="GIBS zoom level (8 = 250 m)")
    ap.add_argument("--years", nargs="*", type=int, help="limit to these years")
    ap.add_argument("--limit", type=int, help="only the first N dates (for testing)")
    ap.add_argument("--dates", nargs="*", help="only these composite dates (YYYY-MM-DD)")
    args = ap.parse_args()
    LAYER, PREFIX = LAYERS[args.layer]

    dates = layer_dates()
    # 2026 is incomplete.
    dates = [d for d in dates if d.year <= 2025]
    if args.years:
        dates = [d for d in dates if d.year in set(args.years)]
    if args.dates:
        dates = [d for d in dates if d.isoformat() in set(args.dates)]
    if args.limit:
        dates = dates[: args.limit]

    rows, cols = tile_range(args.level)
    jobs = [(args.level, d, r, c) for d in dates for r in rows for c in cols]
    print(f"level {args.level}: {len(dates)} dates x {len(rows) * len(cols)} tiles "
          f"(rows {rows.start}-{rows.stop - 1}, cols {cols.start}-{cols.stop - 1}) = {len(jobs)} requests", flush=True)

    counts: dict[str, int] = {}
    problems: list[str] = []
    t0 = time.time()
    with requests.Session() as s, ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch_one, s, *j): j for j in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            status, info = f.result()
            key = status.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
            if key in ("failed", "snapped"):
                problems.append(f"{futs[f][1]} r{futs[f][2]} c{futs[f][3]}: {status} {info}")
            if i % 200 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  {counts}  {time.time() - t0:.0f}s", flush=True)

    print("done:", counts)
    log = RAW / (f"failed_tiles_L{args.level}.txt" if args.layer == "terra" else f"failed_tiles_{args.layer}_L{args.level}.txt")
    if problems:
        log.write_text("\n".join(problems), encoding="utf-8")
        print(f"full problem list written to {log}")
    elif log.exists():
        log.unlink()
    if problems:
        print(f"{len(problems)} problem tiles (first 20):")
        print("\n".join(problems[:20]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
