"""Decode GIBS NDVI tiles back to NDVI values, then stitch, crop and mask them to California.

Each colour in NASA's colormap (data/raw/MODIS_L3_NDVI.xml) is a unique 0.005-wide NDVI bin, stored as a
ref code: 0 = no data, 1-2 = NDVI below 0 (snow, water, bare ground, which are real values, not gaps),
3-142 = NDVI 0 to 1, 255 = outside California or tile missing.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DATA = ROOT / "data"
CACHE = DATA / "cache"

CA_LON = (-124.42, -114.12)
CA_LAT = (32.52, 42.02)
OUTSIDE = 255


def pix_deg(level: int) -> float:
    return 0.5625 / 2**level


def tile_deg(level: int) -> float:
    return 512 * pix_deg(level)


def tile_range(level: int) -> tuple[range, range]:
    td = tile_deg(level)
    cols = range(int((CA_LON[0] + 180) // td), int((CA_LON[1] + 180) // td) + 1)
    rows = range(int((90 - CA_LAT[1]) // td), int((90 - CA_LAT[0]) // td) + 1)
    return rows, cols


def window(level: int):
    rows, cols = tile_range(level)
    td, pd = tile_deg(level), pix_deg(level)
    ox, oy = -180 + cols.start * td, 90 - rows.start * td
    x0 = int((CA_LON[0] - ox) // pd)
    x1 = int(-(-(CA_LON[1] - ox) // pd))
    y0 = int((oy - CA_LAT[1]) // pd)
    y1 = int(-(-(oy - CA_LAT[0]) // pd))
    return (y0, y1, x0, x1), (ox, oy)


def window_geo(level: int):
    (y0, y1, x0, x1), (ox, oy) = window(level)
    pd = pix_deg(level)
    return {"lon0": ox + x0 * pd, "lat0": oy - y0 * pd, "pix_deg": pd, "height": y1 - y0, "width": x1 - x0}


def load_colormap():
    root = ET.parse(RAW / "MODIS_L3_NDVI.xml").getroot()
    rgb2ref: dict[tuple, int] = {}
    mid = np.full(143, np.nan)
    lo_edge = np.full(143, np.nan)
    for e in root.iter("ColorMapEntry"):
        ref = int(e.get("ref"))
        rgb2ref[tuple(int(v) for v in e.get("rgb").split(","))] = ref
        if e.get("nodata") != "true":
            lo, hi = (float(v) for v in re.findall(r"-?\d+\.?\d*", e.get("value"))[:2])
            mid[ref] = (lo + hi) / 2
            lo_edge[ref] = lo
    assert len(rgb2ref) == 143, "colormap colours are expected to be unique"
    return rgb2ref, mid, lo_edge


_RGB2REF, REF_MID, REF_LO = load_colormap()


def ref_to_ndvi(refs: np.ndarray) -> np.ndarray:
    lut = np.full(256, np.nan, dtype=np.float32)
    lut[:143] = REF_MID
    return lut[refs]


def ndvi_to_ref(v: np.ndarray) -> np.ndarray:
    edges = REF_LO[1:]
    ref = np.searchsorted(edges, np.nan_to_num(v, nan=-9), side="right").clip(1, 142).astype(np.uint8)
    ref[np.isnan(v)] = 0
    return ref


def decode_tile(path: Path) -> np.ndarray:
    im = Image.open(path)
    if im.mode == "P":
        pal = np.array(im.getpalette()[:768], dtype=np.uint8).reshape(-1, 3)
        lut = np.full(256, OUTSIDE, dtype=np.uint8)
        for i, rgb in enumerate(pal):
            lut[i] = _RGB2REF.get((int(rgb[0]), int(rgb[1]), int(rgb[2])), OUTSIDE)
        return lut[np.array(im)]
    # some tiles are not paletted (e.g. fully transparent ones): look colours up one by one
    rgba = np.array(im.convert("RGBA"))
    out = np.full(rgba.shape[:2], OUTSIDE, dtype=np.uint8)
    for rgb, ref in _RGB2REF.items():
        out[(rgba[..., :3] == rgb).all(-1)] = ref
    return out


def mosaic(level: int, date: str, prefix: str = "tiles") -> np.ndarray:
    rows, cols = tile_range(level)
    out = np.full((len(rows) * 512, len(cols) * 512), OUTSIDE, dtype=np.uint8)
    for ri, r in enumerate(rows):
        for ci, c in enumerate(cols):
            p = RAW / f"{prefix}_L{level}" / date / f"{r}_{c}.png"
            if p.exists():
                try:
                    out[ri * 512:(ri + 1) * 512, ci * 512:(ci + 1) * 512] = decode_tile(p)
                except Exception:           # a corrupt file is treated like a missing tile
                    pass
    return out


_mask_cache: dict[int, np.ndarray] = {}


def ca_mask(level: int) -> np.ndarray:
    if level in _mask_cache:
        return _mask_cache[level]
    g = window_geo(level)
    img = Image.new("L", (g["width"], g["height"]), 0)
    draw = ImageDraw.Draw(img)
    gj = json.loads((DATA / "ca_boundary.geojson").read_text(encoding="utf-8"))
    for poly in gj["features"][0]["geometry"]["coordinates"]:
        ring = poly[0]  # outer ring only, since California has no holes at this scale
        pts = [((lon - g["lon0"]) / g["pix_deg"], (g["lat0"] - lat) / g["pix_deg"]) for lon, lat in ring]
        draw.polygon(pts, fill=1)
    m = np.array(img, dtype=bool)
    _mask_cache[level] = m
    return m


def ca_mask_geo(lon0: float, lat0: float, pix: float, height: int, width: int) -> np.ndarray:
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    gj = json.loads((DATA / "ca_boundary.geojson").read_text(encoding="utf-8"))
    for poly in gj["features"][0]["geometry"]["coordinates"]:
        draw.polygon([((lon - lon0) / pix, (lat0 - lat) / pix) for lon, lat in poly[0]], fill=1)
    return np.array(img, dtype=bool)


def crop_and_mask(mos: np.ndarray, level: int) -> np.ndarray:
    (y0, y1, x0, x1), _ = window(level)
    out = mos[y0:y1, x0:x1].copy()
    out[~ca_mask(level)] = OUTSIDE
    return out


def derive_from_finer(level: int, src_level: int, date: str, min_valid: float = 0.5, prefix: str = "tiles") -> np.ndarray:
    """Rebuild a coarse frame from finer tiles by block-averaging, where GIBS' coarse tiles are corrupt."""
    f = 2 ** (src_level - level)
    g = window_geo(level)
    rows_s, cols_s = tile_range(src_level)
    td_s, pd_s = tile_deg(src_level), pix_deg(src_level)
    ox_s, oy_s = -180 + cols_s.start * td_s, 90 - rows_s.start * td_s
    xo = (g["lon0"] - ox_s) / pd_s
    yo = (oy_s - g["lat0"]) / pd_s
    assert abs(xo - round(xo)) < 1e-6 and abs(yo - round(yo)) < 1e-6, "grids are not aligned"
    xo, yo = round(xo), round(yo)
    H, W = g["height"], g["width"]
    src = mosaic(src_level, date, prefix)[yo:yo + H * f, xo:xo + W * f]
    assert src.shape == (H * f, W * f), "finer mosaic does not cover the target window"
    v = ref_to_ndvi(src).reshape(H, f, W, f)
    n_valid = np.isfinite(v).sum(axis=(1, 3))
    with np.errstate(invalid="ignore"):
        mean = np.nansum(v, axis=(1, 3)) / np.maximum(n_valid, 1)
    mean[n_valid < min_valid * f * f] = np.nan
    out = ndvi_to_ref(mean.astype(np.float32))
    out[~ca_mask(level)] = OUTSIDE
    return out


def list_dates(level: int, prefix: str = "tiles") -> list[str]:
    base = RAW / f"{prefix}_L{level}"
    return sorted(p.name for p in base.iterdir() if p.is_dir())
