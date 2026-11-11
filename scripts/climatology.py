"""Per-pixel seasonal climatology from the 1 km cube: median NDVI for each 16-day slot over 2001-2025."""
import json
from datetime import date

import numpy as np

import ndvi

SLOT_DOY = np.arange(23) * 16 + 1


def slot_of(d: str) -> int:
    return (date.fromisoformat(d).timetuple().tm_yday - 1) // 16


def main():
    cube = np.load(ndvi.CACHE / "cube_L6.npy", mmap_mode="r")
    dates = json.load(open(ndvi.CACHE / "cube_L6.json"))["dates"]
    mask = ndvi.ca_mask(6)
    years = np.array([int(d[:4]) for d in dates])
    slots = np.array([slot_of(d) for d in dates])

    H, W = mask.shape
    clim = np.full((23, H, W), np.nan, dtype=np.float32)
    for s in range(23):
        idx = np.where((slots == s) & (years >= 2001))[0]
        stack = ndvi.ref_to_ndvi(np.asarray(cube[idx]))
        with np.errstate(all="ignore"):
            clim[s] = np.nanmedian(stack, axis=0)
        print(f"slot {s:2d} (doy {SLOT_DOY[s]:3d}): {len(idx)} years", flush=True)
    clim[:, ~mask] = np.nan

    with np.errstate(all="ignore"):
        amp = np.nanmax(clim, axis=0) - np.nanmin(clim, axis=0)
        peak = np.where(np.isfinite(amp), np.nanargmax(np.where(np.isfinite(clim), clim, -9), axis=0), -1).astype(np.int8)
    np.save(ndvi.CACHE / "clim_L6.npy", clim)
    np.save(ndvi.CACHE / "amp_L6.npy", amp.astype(np.float32))
    np.save(ndvi.CACHE / "peakslot_L6.npy", peak)
    print("saved. valid pixels:", int(np.isfinite(amp).sum()), "of", int(mask.sum()))


if __name__ == "__main__":
    main()
