"""Helpers shared with export_site.py (quantisation, regions), plus the data export for an early design prototype."""
import json
import shutil
import time
from datetime import date

import numpy as np
from PIL import Image, ImageDraw

import climatology
import ndvi
import regions

OUT = ndvi.ROOT / "lab" / "data"
Q_SCALE, Q_OFF = 250.0, 0.2          # q = round(250 × (ndvi + 0.2) / 1.2)
MISSING = 255
MIN_RANGE = 0.08


def quant(v):
    q = np.clip(np.rint((v + Q_OFF) / 1.2 * Q_SCALE), 0, 250).astype(np.uint8)
    q[~np.isfinite(v)] = MISSING
    return q


def dequant(q):
    v = q.astype(np.float32) / Q_SCALE * 1.2 - Q_OFF
    v[q == MISSING] = np.nan
    return v


def build_cube():
    cube6 = np.load(ndvi.CACHE / "cube_L6.npy", mmap_mode="r")
    meta6 = json.load(open(ndvi.CACHE / "cube_L6.json"))
    dates = meta6["dates"]
    g6 = ndvi.window_geo(6)
    H6, W6 = g6["height"], g6["width"]
    H2, W2 = (H6 + 1) // 2, (W6 + 1) // 2
    m6 = ndvi.ca_mask(6)
    m2 = np.pad(m6, ((0, H2 * 2 - H6), (0, W2 * 2 - W6))).reshape(H2, 2, W2, 2).mean(axis=(1, 3)) >= 0.5
    ys, xs = np.nonzero(m2)
    N, T = len(ys), len(dates)
    print(f"2 km grid {H2}x{W2}, {N} land pixels, {T} dates", flush=True)
    cube = np.empty((T, N), dtype=np.uint8)
    t0 = time.time()
    for t in range(T):
        f = ndvi.ref_to_ndvi(np.asarray(cube6[t]))
        f = np.pad(f, ((0, H2 * 2 - H6), (0, W2 * 2 - W6)), constant_values=np.nan).reshape(H2, 2, W2, 2)
        ok = np.isfinite(f)
        cnt = ok.sum(axis=(1, 3))
        mean = np.where(ok, f, 0).sum(axis=(1, 3)) / np.maximum(cnt, 1)
        mean[cnt < 2] = np.nan
        cube[t] = quant(mean[ys, xs])
        if (t + 1) % 150 == 0:
            print(f"  {t + 1}/{T} {time.time() - t0:.0f}s", flush=True)
    geo = {"lon0": g6["lon0"], "lat0": g6["lat0"], "pix": g6["pix_deg"] * 2, "width": W2, "height": H2}
    return cube, dates, m2, ys, xs, geo


def build_clim(cube, dates):
    years = np.array([int(d[:4]) for d in dates])
    slots = np.array([climatology.slot_of(d) for d in dates])
    N = cube.shape[1]
    clim = np.full((23, N), np.nan, dtype=np.float32)
    for s in range(23):
        idx = np.where((slots == s) & (years >= 2001))[0]
        with np.errstate(all="ignore"):
            clim[s] = np.nanmedian(dequant(cube[idx]), axis=0)
    return clim, years, slots


def kmeans(X, k, seed=1, iters=60):
    rng = np.random.default_rng(seed)
    centers = [X[rng.integers(len(X))]]
    d2 = ((X - centers[0]) ** 2).sum(1)
    for _ in range(1, k):
        i = rng.choice(len(X), p=d2 / d2.sum())
        centers.append(X[i])
        d2 = np.minimum(d2, ((X - X[i]) ** 2).sum(1))
    C = np.array(centers)
    xx = (X ** 2).sum(1)[:, None]
    for _ in range(iters):
        D = xx - 2 * X @ C.T + (C ** 2).sum(1)[None]
        lab = D.argmin(1)
        newC = np.array([X[lab == j].mean(0) if (lab == j).any() else C[j] for j in range(k)])
        if np.allclose(newC, C, atol=1e-5):
            break
        C = newC
    D = xx - 2 * X @ C.T + (C ** 2).sum(1)[None]
    lab = D.argmin(1)
    return lab, float(D[np.arange(len(X)), lab].sum())


def build_clusters(clim, ks=(6, 8, 10, 12)):
    N = clim.shape[1]
    X = clim.T.copy()
    ok = np.isfinite(X).sum(1) >= 20
    for i in np.where(ok & ~np.isfinite(X).all(1))[0]:  # fill a few gaps with the pixel's own mean
        row = X[i]
        row[~np.isfinite(row)] = np.nanmean(row)
    out = {}
    for k in ks:
        best = None
        for seed in range(4):
            lab, inertia = kmeans(X[ok], k, seed=seed)
            if best is None or inertia < best[1]:
                best = (lab, inertia)
        lab_ok = best[0]
        stats = []
        for j in range(k):
            sel = X[ok][lab_ok == j]
            mean = sel.mean(0)
            stats.append({
                "old": j, "mean": mean, "p25": np.percentile(sel, 25, axis=0), "p75": np.percentile(sel, 75, axis=0),
                "n": len(sel), "peak": int(mean.argmax()), "trough": int(mean.argmin()),
                "swing": float(mean.max() - mean.min()), "level": float(mean.mean()),
            })
        order = sorted(range(k), key=lambda j: (stats[j]["swing"] >= MIN_RANGE, stats[j]["level"] if stats[j]["swing"] < MIN_RANGE else stats[j]["peak"]))
        remap = {old: new for new, old in enumerate(order)}
        labels = np.full(N, 255, dtype=np.uint8)
        labels[ok] = np.array([remap[v] for v in lab_ok], dtype=np.uint8)
        out[k] = {
            "labels": labels,
            "clusters": [{
                "id": remap[s["old"]], "share": s["n"] / ok.sum(), "peak": s["peak"], "trough": s["trough"],
                "swing": round(s["swing"], 4), "level": round(s["level"], 4),
                "mean": [round(float(v), 4) for v in s["mean"]],
                "p25": [round(float(v), 4) for v in s["p25"]], "p75": [round(float(v), 4) for v in s["p75"]],
            } for s in sorted(stats, key=lambda s: remap[s["old"]])],
        }
        print(f"  k={k}: sizes " + ", ".join(f"{c['share']:.0%}" for c in out[k]["clusters"]), flush=True)
    return out


def build_greenup(cube, dates, clim):
    N = cube.shape[1]
    T_full = 26 * 23
    X = np.full((T_full, N), np.nan, dtype=np.float32)
    dq = dequant(cube)
    for ti, d in enumerate(dates):
        X[(int(d[:4]) - 2000) * 23 + climatology.slot_of(d)] = dq[ti]
    ord_t = np.array([date(2000 + t // 23, 1, 1).toordinal() + (t % 23) * 16 + 8 for t in range(T_full)], dtype=np.float64)

    with np.errstate(all="ignore"):
        s0 = np.nanargmin(np.where(np.isfinite(clim), clim, 9), axis=0)
        s1 = np.nanargmax(np.where(np.isfinite(clim), clim, -9), axis=0)
        rng = clim[s1, np.arange(N)] - clim[s0, np.arange(N)]
    seasonal = np.isfinite(rng) & (rng >= MIN_RANGE) & (np.isfinite(clim).sum(0) >= 20)
    lo = clim[s0, np.arange(N)]
    thr = lo + 0.5 * rng
    cols = np.arange(N)
    out = np.full((25, N), 65535, dtype=np.uint16)
    for yi, y in enumerate(range(2001, 2026)):
        t0 = (y - 2000) * 23 + s0
        found = ~seasonal.copy()                       # non-seasonal pixels count as "done" (stay missing)
        res = np.full(N, np.nan)
        jan1 = date(y, 1, 1).toordinal()
        for j in range(1, 23):
            t = t0 + j
            valid = (t < T_full - 1) & ~found
            tc = np.clip(t, 1, T_full - 2)
            prev, cur, nxt = X[tc - 1, cols], X[tc, cols], X[tc + 1, cols]
            hit = valid & np.isfinite(prev) & np.isfinite(cur) & (prev < thr) & (cur >= thr) & (~np.isfinite(nxt) | (nxt >= thr))
            if hit.any():
                frac = (thr[hit] - prev[hit]) / (cur[hit] - prev[hit])
                co = ord_t[tc[hit] - 1] + frac * (ord_t[tc[hit]] - ord_t[tc[hit] - 1])
                res[hit] = co - jan1
                found |= hit
        good = np.isfinite(res) & (res >= 0) & (res < 730)
        out[yi, good] = np.rint(res[good]).astype(np.uint16)

    G = out.astype(np.float64)
    G[out == 65535] = np.nan
    yrs = np.arange(2001, 2026, dtype=np.float64)[:, None] - 2013.0
    ok = np.isfinite(G)
    n = ok.sum(0)
    with np.errstate(all="ignore"):
        sx = np.where(ok, yrs, 0).sum(0)
        sy = np.where(ok, G, 0).sum(0)
        sxx = np.where(ok, yrs ** 2, 0).sum(0)
        sxy = np.where(ok, yrs * G, 0).sum(0)
        slope = (n * sxy - sx * sy) / (n * sxx - sx ** 2)
        icpt = (sy - slope * sx) / n
        resid = np.where(ok, G - (icpt + slope * yrs), 0)
        s2 = (resid ** 2).sum(0) / (n - 2)
        se = np.sqrt(s2 / (sxx - sx ** 2 / n))
        tstat = slope / se
        mean_off = sy / n
    bad = n < 15
    for a in (slope, tstat, mean_off):
        a[bad] = np.nan
    trend = np.stack([slope * 10, tstat, mean_off]).astype(np.float32)   # slope in days per decade
    print(f"  green-up: {seasonal.mean():.0%} of land has a seasonal cycle; median cycle length found in "
          f"{np.nanmedian(np.isfinite(G).mean(0)[seasonal]):.0%} of years", flush=True)
    return out, trend, seasonal


def build_regions(geo, ys, xs):
    img = Image.new("L", (geo["width"], geo["height"]), 0)
    d = ImageDraw.Draw(img)
    for i, (k, (name, col, ring)) in enumerate(regions.REGIONS.items(), 1):
        d.polygon([((lon - geo["lon0"]) / geo["pix"], (geo["lat0"] - lat) / geo["pix"]) for lon, lat in ring], fill=i)
    arr = np.array(img, dtype=np.uint8)
    return arr[ys, xs]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cube, dates, m2, ys, xs, geo = build_cube()
    clim, years, slots = build_clim(cube, dates)
    print("clusters ...", flush=True)
    clusters = build_clusters(clim)
    print("green-up ...", flush=True)
    greenup, trend, seasonal = build_greenup(cube, dates, clim)
    reg = build_regions(geo, ys, xs)

    cube.tofile(OUT / "cube.bin")
    quant(clim).tofile(OUT / "clim.bin")
    m2.astype(np.uint8).tofile(OUT / "land.bin")
    reg.tofile(OUT / "regions.bin")
    greenup.tofile(OUT / "greenup.bin")
    trend.tofile(OUT / "trend.bin")
    for k, v in clusters.items():
        v["labels"].tofile(OUT / f"clusters_k{k}.bin")
    shutil.copy(ndvi.DATA / "ca_boundary.geojson", OUT / "ca_boundary.geojson")
    shutil.copy(ndvi.DATA / "regions.geojson", OUT / "regions.geojson")
    meta = {
        "grid": geo, "N": int(len(ys)), "T": len(dates), "dates": dates,
        "years": years.tolist(), "slots": slots.tolist(),
        "quant": {"scale": Q_SCALE, "offset": Q_OFF, "missing": MISSING},
        "regions": [{"id": i, "key": k, "name": v[0], "color": v[1]} for i, (k, v) in enumerate(regions.REGIONS.items(), 1)],
        "clusters": {str(k): v["clusters"] for k, v in clusters.items()},
        "greenup": {"years": [2001, 2025], "missing": 65535, "min_range": MIN_RANGE},
        "files_mb": {f.name: round(f.stat().st_size / 1e6, 2) for f in sorted(OUT.glob("*.bin"))},
    }
    (OUT / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f}s:", meta["files_mb"])


if __name__ == "__main__":
    main()
