"""Stitch cached tiles into one California-masked cube: data/cache/cube_L6.npy (Terra) or cube_aqua_L6.npy (Aqua).

Usage: python scripts/build_cube.py --level 6 [--layer aqua]
"""
import argparse
import json
import time

import numpy as np

import ndvi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=6)
    ap.add_argument("--layer", choices=["terra", "aqua"], default="terra")
    args = ap.parse_args()
    prefix = "tiles" if args.layer == "terra" else "tiles_aqua"
    tag = "" if args.layer == "terra" else "aqua_"

    dates = ndvi.list_dates(args.level, prefix)
    l8 = ndvi.RAW / f"{prefix}_L8"
    if args.level < 8 and l8.is_dir():          # include dates that only have 250 m tiles
        dates = sorted(set(dates) | {p.name for p in l8.iterdir() if p.is_dir()})
    g = ndvi.window_geo(args.level)
    mask = ndvi.ca_mask(args.level)
    ndvi.CACHE.mkdir(parents=True, exist_ok=True)
    out_path = ndvi.CACHE / f"cube_{tag}L{args.level}.npy"
    cube = np.lib.format.open_memmap(out_path, mode="w+", dtype=np.uint8, shape=(len(dates), g["height"], g["width"]))

    print(f"level {args.level}: {len(dates)} dates, window {g['height']}x{g['width']}, "
          f"CA pixels {int(mask.sum()):,}", flush=True)
    stats = []
    t0 = time.time()
    for i, d in enumerate(dates):
        frame = ndvi.crop_and_mask(ndvi.mosaic(args.level, d, prefix), args.level)
        derived = False
        dropped = False
        # where GIBS' 1 km tiles are corrupt, rebuild the frame from 250 m tiles
        if args.level < 8 and (frame[mask] == ndvi.OUTSIDE).mean() > 0.5:
            if (ndvi.RAW / f"{prefix}_L8" / d).is_dir():
                frame = ndvi.derive_from_finer(args.level, 8, d, prefix=prefix)
                derived = True
                print(f"  {d}: coarse tiles corrupt -> rebuilt from 250 m tiles", flush=True)
            elif args.layer == "aqua":
                # nothing to rebuild from: leave the frame missing
                frame[:] = ndvi.OUTSIDE
                dropped = True
                print(f"  {d}: coarse tiles corrupt or missing -> frame dropped", flush=True)
            else:
                print(f"  {d}: WARNING coarse tiles missing and no 250 m tiles to rebuild from", flush=True)
        cube[i] = frame
        inside = frame[mask]
        stats.append({
            "date": d,
            "fill": float((inside == 0).mean()),
            "missing_tile": float((inside == ndvi.OUTSIDE).mean()),
            "derived_from_250m": derived,
            "dropped": dropped,
        })
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(dates)}  {time.time() - t0:.0f}s", flush=True)
    cube.flush()

    meta = {"level": args.level, "layer": args.layer, "dates": dates, "geo": g, "frame_stats": stats}
    (ndvi.CACHE / f"cube_{tag}L{args.level}.json").write_text(json.dumps(meta), encoding="utf-8")
    worst = sorted(stats, key=lambda s: -(s["fill"] + s["missing_tile"]))[:5]
    print("done. worst frames by fill + missing share:")
    for s in worst:
        print(f"   {s['date']}  fill {s['fill']:.1%}  missing_tile {s['missing_tile']:.1%}")
    print(f"mean fill share: {np.mean([s['fill'] for s in stats]):.2%}")


if __name__ == "__main__":
    main()
