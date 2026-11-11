"""Build data/ca_boundary.geojson, the California outline, from us-atlas (US Census boundaries)."""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SRC = RAW / "states-10m.json"
OUT = ROOT / "data" / "ca_boundary.geojson"
URL = "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-10m.json"
CA_ID = "06"


def decode_arcs(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def ring_coords(arcs, ring):
    out = []
    for i in ring:
        pts = arcs[i] if i >= 0 else arcs[~i][::-1]
        out.extend(pts if not out else pts[1:])  # drop duplicate joint point
    return [list(p) for p in out]


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    if not SRC.exists():
        r = requests.get(URL, timeout=60)
        r.raise_for_status()
        SRC.write_bytes(r.content)
    topo = json.loads(SRC.read_text(encoding="utf-8"))
    arcs = decode_arcs(topo)
    geom = next(g for g in topo["objects"]["states"]["geometries"] if g["id"] == CA_ID)
    polys = [geom["arcs"]] if geom["type"] == "Polygon" else geom["arcs"]
    coords = [[ring_coords(arcs, ring) for ring in poly] for poly in polys]
    feature = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "California"},
            "geometry": {"type": "MultiPolygon", "coordinates": coords},
        }],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(feature, separators=(",", ":")), encoding="utf-8")
    xs = [p[0] for poly in coords for ring in poly for p in ring]
    ys = [p[1] for poly in coords for ring in poly for p in ring]
    print(f"{len(coords)} polygons, bbox lon {min(xs):.2f}..{max(xs):.2f} lat {min(ys):.2f}..{max(ys):.2f}")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
