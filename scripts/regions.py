"""The six hand-drawn regions as lon/lat polygons. Writes data/regions.geojson."""
import json

import numpy as np
from PIL import Image, ImageDraw

import ndvi

# name -> (short label, colour, [(lon, lat), ...])
REGIONS = {
    "north_coast": ("North Coast redwoods", "#0b6e4f", [
        (-124.15, 41.75), (-123.65, 41.75), (-123.6, 40.2), (-123.85, 39.6), (-124.05, 39.6), (-124.2, 40.3)]),
    "sacramento_valley": ("Sacramento Valley", "#e0a100", [
        (-122.25, 39.95), (-121.85, 39.95), (-121.55, 39.35), (-121.45, 38.7), (-121.35, 38.35),
        (-121.9, 38.4), (-122.0, 38.9), (-122.2, 39.4)]),
    "sierra": ("Sierra Nevada", "#1b6ca8", [
        (-120.05, 38.55), (-119.35, 38.55), (-118.55, 37.85), (-118.25, 37.15),
        (-118.55, 36.45), (-119.05, 36.45), (-119.65, 37.05), (-120.0, 37.75)]),
    "san_joaquin": ("San Joaquin Valley", "#7b4fa0", [
        (-120.9, 37.75), (-120.3, 37.1), (-119.75, 36.55), (-119.35, 36.0), (-119.0, 35.45),
        (-119.6, 35.3), (-120.15, 35.8), (-120.6, 36.4), (-121.05, 37.0), (-121.5, 37.6), (-121.55, 37.75)]),
    "coast_range": ("Coast Range grassland / oak", "#7a9a01", [
        (-121.45, 36.95), (-120.95, 36.95), (-120.4, 36.3), (-120.2, 35.65),
        (-120.65, 35.35), (-121.2, 35.75), (-121.45, 36.35)]),
    "mojave": ("Mojave Desert", "#b5485d", [
        (-117.4, 35.7), (-116.2, 35.7), (-115.7, 35.0), (-116.0, 34.4), (-117.1, 34.4), (-117.5, 34.9)]),
}


def rasterize(level: int, ring) -> np.ndarray:
    g = ndvi.window_geo(level)
    img = Image.new("L", (g["width"], g["height"]), 0)
    ImageDraw.Draw(img).polygon(
        [((lon - g["lon0"]) / g["pix_deg"], (g["lat0"] - lat) / g["pix_deg"]) for lon, lat in ring], fill=1)
    return np.array(img, dtype=bool)


def region_masks(level: int) -> dict[str, np.ndarray]:
    ca = ndvi.ca_mask(level)
    return {k: rasterize(level, v[2]) & ca for k, v in REGIONS.items()}


def write_geojson(path):
    feats = [{
        "type": "Feature",
        "properties": {"id": k, "name": v[0], "color": v[1]},
        "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in v[2]] + [list(v[2][0])]]},
    } for k, v in REGIONS.items()]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    write_geojson(ndvi.DATA / "regions.geojson")
    for k, m in region_masks(6).items():
        print(f"{k:18s} {int(m.sum()):6d} pixels at 1 km")
