"""Build the site's data (data/web/) from the Terra and Aqua 1 km cubes.

Both satellites are merged into one timeline (a frame about every 8 days) on a 2 km grid. NDVI is stored as
q = round(250 × (ndvi + 0.2) / 1.2), with 255 for missing. Each year file is delta-coded along time.

Usage: python scripts/export_site.py
"""
import gzip
import json
import shutil
import time
from datetime import date

import numpy as np

import export_lab as L
import ndvi
import regions

OUT = ndvi.ROOT / "data" / "web"
EPOCH = date(2000, 1, 1)
MISSING = L.MISSING


def load_sensor(tag, cube_name, json_name):
    cube6 = np.load(ndvi.CACHE / cube_name, mmap_mode="r")
    meta = json.load(open(ndvi.CACHE / json_name))
    return cube6, meta["dates"], meta["frame_stats"]


def window_lengths(starts):
    """length in days of each composite window (16, except the last of a year, which is cut short)"""
    lens = []
    for i, d in enumerate(starts):
        if i + 1 < len(starts):
            nxt = date.fromisoformat(starts[i + 1])
            n = (nxt - date.fromisoformat(d)).days
            lens.append(n if 1 <= n <= 16 else 16)
        else:
            lens.append(13 if date.fromisoformat(d).timetuple().tm_yday >= 350 else 16)
    return lens


def block_quantise(cube6, mask2, H2, W2, ys, xs):
    T, H6, W6 = cube6.shape
    out = np.empty((T, len(ys)), dtype=np.uint8)
    for t in range(T):
        f = ndvi.ref_to_ndvi(np.asarray(cube6[t]))
        f = np.pad(f, ((0, H2 * 2 - H6), (0, W2 * 2 - W6)), constant_values=np.nan).reshape(H2, 2, W2, 2)
        ok = np.isfinite(f)
        cnt = ok.sum(axis=(1, 3))
        mean = np.where(ok, f, 0).sum(axis=(1, 3)) / np.maximum(cnt, 1)
        mean[cnt < 2] = np.nan
        out[t] = L.quant(mean[ys, xs])
    return out


def sensor_check(terra_q, terra_day, aqua_q, aqua_day, region_ids):
    """Per-pixel mean of (Aqua - Terra interpolated to the Aqua date), in NDVI units."""
    td = np.asarray(terra_day)
    N = terra_q.shape[1]
    s = np.zeros(N)
    c = np.zeros(N)
    for a, day in enumerate(aqua_day):
        j = np.searchsorted(td, day)
        if j <= 0 or j >= len(td):
            continue
        d0, d1 = td[j - 1], td[j]
        if d1 - d0 > 20 or day - d0 > 12 or d1 - day > 12:       # need Terra neighbours on both sides
            continue
        w = (day - d0) / (d1 - d0)
        t0, t1 = terra_q[j - 1].astype(float), terra_q[j].astype(float)
        ok = (t0 != MISSING) & (t1 != MISSING) & (aqua_q[a] != MISSING)
        tv = (1 - w) * t0 + w * t1
        diff = (aqua_q[a].astype(float) - tv) / L.Q_SCALE * 1.2
        s[ok] += diff[ok]
        c[ok] += 1
    with np.errstate(all="ignore"):
        return np.where(c >= 20, s / c, np.nan), c


def write_year(path, frames):
    delta = np.empty_like(frames)
    delta[0] = frames[0]
    delta[1:] = (frames[1:].astype(np.int16) - frames[:-1].astype(np.int16)) % 256
    path.write_bytes(gzip.compress(delta.tobytes(), compresslevel=9, mtime=0))


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    cT, datesT, statsT = load_sensor("terra", "cube_L6.npy", "cube_L6.json")
    cA, datesA, statsA = load_sensor("aqua", "cube_aqua_L6.npy", "cube_aqua_L6.json")

    g6 = ndvi.window_geo(6)
    H6, W6 = g6["height"], g6["width"]
    H2, W2 = (H6 + 1) // 2, (W6 + 1) // 2
    m6 = ndvi.ca_mask(6)
    m2 = np.pad(m6, ((0, H2 * 2 - H6), (0, W2 * 2 - W6))).reshape(H2, 2, W2, 2).mean(axis=(1, 3)) >= 0.5
    ys, xs = np.nonzero(m2)
    N = len(ys)
    geo = {"lon0": g6["lon0"], "lat0": g6["lat0"], "pix": g6["pix_deg"] * 2, "width": W2, "height": H2}
    print(f"2 km grid {H2}x{W2}, {N} land pixels", flush=True)

    qT = block_quantise(cT, m2, H2, W2, ys, xs)
    qA = block_quantise(cA, m2, H2, W2, ys, xs)
    keepA = np.array([not s.get("dropped", False) and (qA[i] != MISSING).any() for i, s in enumerate(statsA)])
    print(f"Terra {len(datesT)} frames, Aqua {len(datesA)} frames ({int((~keepA).sum())} dropped)", flush=True)

    def table(starts, lens, sensor, stats, keep=None):
        rows = []
        for i, (d, n) in enumerate(zip(starts, lens)):
            if keep is not None and not keep[i]:
                continue
            ds = date.fromisoformat(d)
            centre = (ds - EPOCH).days + (n - 1) / 2
            c = date.fromordinal(EPOCH.toordinal() + int(round(centre)))
            rows.append({"src": i, "sensor": sensor, "start": d, "day": centre, "doy": c.timetuple().tm_yday,
                         "year": c.year, "slot": (ds.timetuple().tm_yday - 1) // 8, "fill": stats[i]["fill"]})
        return rows

    obsT = table(datesT, window_lengths(datesT), "T", statsT)
    obsA = table(datesA, window_lengths(datesA), "A", statsA, keepA)

    dT = [o["day"] for o in obsT]
    dA = [o["day"] for o in obsA]
    qA_kept = qA[[o["src"] for o in obsA]]
    off, cnt = sensor_check(qT, dT, qA_kept, dA, None)
    reg = L.build_regions(geo, ys, xs)
    check = {"overall_mean_offset": float(np.nanmean(off)), "overall_median_offset": float(np.nanmedian(off)),
             "p5": float(np.nanpercentile(off, 5)), "p95": float(np.nanpercentile(off, 95)), "regions": {}}
    for r in regions_meta():
        m = reg == r["id"]
        check["regions"][r["key"]] = float(np.nanmedian(off[m]))
    print("Aqua minus Terra (interpolated), NDVI units: mean %.4f, median %.4f, 5-95%% [%.3f, %.3f]" % (
        check["overall_mean_offset"], check["overall_median_offset"], check["p5"], check["p95"]))
    for k, v in check["regions"].items():
        print(f"   {k:18s} {v:+.4f}")
    corrected = abs(check["overall_median_offset"]) >= 0.01
    check["rule"] = "merge as is if |median offset| < 0.01 NDVI, otherwise subtract the per-pixel median offset from Aqua"
    check["corrected"] = bool(corrected)
    if corrected:
        offq = np.nan_to_num(off, nan=0.0) / 1.2 * L.Q_SCALE
        adj = qA.astype(np.float32)
        ok = adj != MISSING
        adj[ok] = np.clip(np.rint(adj[ok] - np.broadcast_to(offq, adj.shape)[ok]), 0, 250)
        qA = adj.astype(np.uint8)
        print("   -> correction applied to Aqua", flush=True)

    obs = []
    for o in obsT:
        obs.append({**o, "q": qT[o["src"]]})
    for o in obsA:
        obs.append({**o, "q": qA[o["src"]]})
    obs.sort(key=lambda o: o["day"])
    obs = [o for o in obs if o["year"] <= 2025]       # Aqua's last composite is centred on Jan 2, 2026

    # drop clearly broken composites (objective rules, noise is kept)
    def state_median(q):
        v = q[q != MISSING]
        return float(np.median(v)) / L.Q_SCALE * 1.2 - L.Q_OFF if len(v) else np.nan
    dropped = []
    while True:
        med = np.array([state_median(o["q"]) for o in obs])
        bad = None
        for i, o in enumerate(obs):
            if o["fill"] > 0.20:
                bad = (i, f"{o['fill']:.0%} of pixels empty"); break
            if 0 < i < len(obs) - 1:
                a, b = med[i - 1], med[i + 1]
                if abs(med[i] - (a + b) / 2) > 0.15 and abs(a - b) < 0.08:
                    bad = (i, f"statewide median {med[i]:.2f} vs neighbours {a:.2f} / {b:.2f}"); break
        if bad is None:
            break
        o = obs.pop(bad[0])
        dropped.append({"start": o["start"], "sensor": o["sensor"], "why": bad[1]})
        print(f"  dropped composite {o['start']} ({o['sensor']}): {bad[1]}", flush=True)
    check["dropped_composites"] = dropped
    T = len(obs)
    print(f"{T} observations on the merged time axis ({obs[0]['start']} .. {obs[-1]['start']})", flush=True)

    years = sorted({o["year"] for o in obs})
    year_info = []
    k_in_year = {}
    for y in years:
        idx = [i for i, o in enumerate(obs) if o["year"] == y]
        frames = np.stack([obs[i]["q"] for i in idx])
        write_year(OUT / f"ndvi_{y}.gz", frames)
        for k, i in enumerate(idx):
            k_in_year[i] = k
        year_info.append({"year": y, "file": f"ndvi_{y}.gz", "n": len(idx), "first": idx[0]})

    # usual value for each 8-day slot: median over 2001-2025
    all_q = np.stack([o["q"] for o in obs])
    slots = np.array([o["slot"] for o in obs])
    years_arr = np.array([o["year"] for o in obs])
    clim = np.full((46, N), MISSING, dtype=np.uint8)
    for s in range(46):
        idx = np.where((slots == s) & (years_arr >= 2001))[0]
        if len(idx) == 0:
            continue
        v = L.dequant(all_q[idx])
        with np.errstate(all="ignore"):
            clim[s] = L.quant(np.nanmedian(v, axis=0))
    clim.tofile(OUT / "clim8.bin")

    areas = {"state": None, **{r["key"]: (reg == r["id"]) for r in regions_meta()}}
    series = {}
    for key, m in areas.items():
        vals = all_q if m is None else all_q[:, m]
        out = []
        for t in range(T):
            row = vals[t]
            row = row[row != MISSING]
            out.append(round(float(np.median(row)) / L.Q_SCALE * 1.2 - L.Q_OFF, 4) if len(row) else None)
        series[key] = out
    (OUT / "series.json").write_text(json.dumps(series), encoding="utf-8")

    m2.astype(np.uint8).tofile(OUT / "land.bin")
    reg.tofile(OUT / "regions.bin")
    shutil.copy(ndvi.DATA / "ca_boundary.geojson", OUT / "ca_boundary.geojson")
    meta = {
        "grid": geo, "N": N, "T": T, "quant": {"scale": L.Q_SCALE, "offset": L.Q_OFF, "missing": MISSING},
        "obs": {
            "day": [round(o["day"], 1) for o in obs], "doy": [o["doy"] for o in obs], "year": [o["year"] for o in obs],
            "slot": [o["slot"] for o in obs], "sensor": "".join(o["sensor"] for o in obs),
            "k": [k_in_year[i] for i in range(T)], "start": [o["start"] for o in obs],
        },
        "years": year_info, "epoch": EPOCH.isoformat(),
        "regions": regions_meta(), "sensor_check": check,
        "files_mb": {f.name: round(f.stat().st_size / 1e6, 2) for f in sorted(OUT.glob("*")) if f.is_file()},
    }
    (OUT / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.glob("*") if f.is_file()) / 1e6
    biggest = max(f.stat().st_size for f in OUT.glob("*") if f.is_file()) / 1e6
    print(f"done in {time.time() - t0:.0f}s: {len(list(OUT.glob('*')))} files, {total:.1f} MB total, largest {biggest:.1f} MB")


def regions_meta():
    return [{"id": i, "key": k, "name": v[0], "color": v[1]} for i, (k, v) in enumerate(regions.REGIONS.items(), 1)]


if __name__ == "__main__":
    main()
