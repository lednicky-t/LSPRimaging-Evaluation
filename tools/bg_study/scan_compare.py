"""How much does background removal change the analysis output? Compare whole-dataset step-1 scans made with different
background settings (`tools/frame_reduce_lab.py --sections scan --scan-all --background ... --save X.npz`).

Read-only; works on the saved .npz files (ROI x cube x wavelength x [sample, reference] means). Output quantity:
absorbance A = log10(reference / sample) per ROI, cube and wavelength, plus a spectral centroid of A per ROI and cube
(sum(wl * A) / sum(A): a simple stand-in for the peak position until step 3, the fit, exists).

Metrics (all medians over ROIs and wavelengths, so one bad ROI does not dominate):

  short-term noise   cube-to-cube scatter: std of first differences over cubes / sqrt(2). Slow real changes (the pump
                     plan) hardly enter; the same real fast changes enter every variant equally.
  vs ROI median      per ROI, std over cubes of (A - the across-ROI median at that cube): what is left after the common
                     real signal is removed, i.e. ROI-specific drift + noise. This is where illumination drift shows.
  ROI spread         robust std (1.4826 MAD) across ROIs at one cube and wavelength: how uniform the array looks.
  ring stability     per ROI, std over cubes of reference / (across-ROI median reference), in %: illumination-shape drift
                     seen by the rings.

    python tools/bg_study/scan_compare.py off=scan_off.npz session=scan_s39.npz s24=scan_s24.npz
"""

from __future__ import annotations

import sys

import numpy as np


def robust_std(x: np.ndarray, axis: int) -> np.ndarray:
    med = np.median(x, axis=axis, keepdims=True)
    return 1.4826 * np.median(np.abs(x - med), axis=axis)


def metrics(path: str) -> dict:
    z = np.load(path)
    v = z["value_mean"]  # (roi, cube, wl, 2)
    wl = z["wavelengths"] if "wavelengths" in z else 470.0 + 10.0 * np.arange(v.shape[2])
    sample, reference = v[..., 0], v[..., 1]
    a = np.log10(reference / sample)
    out = {}
    out["A level (median)"] = float(np.median(a))
    out["A short-term noise (mA)"] = 1e3 * float(np.median(np.std(np.diff(a, axis=1), axis=1) / np.sqrt(2)))
    resid = a - np.median(a, axis=0, keepdims=True)
    out["A vs ROI median, std over cubes (mA)"] = 1e3 * float(np.median(np.std(resid, axis=1)))
    out["A ROI spread (mA)"] = 1e3 * float(np.median(robust_std(a, axis=0)))
    ring = reference / np.median(reference, axis=0, keepdims=True)
    out["ring vs ROI median, std over cubes (%)"] = 100.0 * float(np.median(np.std(ring, axis=1)))
    centroid = (a * wl[None, None, :]).sum(axis=2) / a.sum(axis=2)  # (roi, cube), nm
    out["centroid short-term noise (nm)"] = float(np.median(np.std(np.diff(centroid, axis=1), axis=1) / np.sqrt(2)))
    c_resid = centroid - np.median(centroid, axis=0, keepdims=True)
    out["centroid vs ROI median, std over cubes (nm)"] = float(np.median(np.std(c_resid, axis=1)))
    out["centroid ROI spread (nm)"] = float(np.median(robust_std(centroid, axis=0)))
    out["_a"] = a
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    runs = {}
    for item in argv:
        label, path = item.split("=", 1)
        runs[label] = metrics(path)
    labels = list(runs)
    keys = [k for k in runs[labels[0]] if not k.startswith("_")]
    width = max(len(k) for k in keys)
    print(f"{'':{width}s} " + " ".join(f"{label:>12s}" for label in labels))
    for key in keys:
        print(f"{key:{width}s} " + " ".join(f"{runs[label][key]:12.4f}" for label in labels))
    base = labels[0]
    for label in labels[1:]:
        d = runs[label]["_a"] - runs[base]["_a"]
        print(f"\n{label} - {base}: A difference median {1e3 * np.median(d):+.3f} mA, robust spread {1e3 * float(robust_std(d.ravel(), 0)):.3f} mA, "
              f"max |d| {1e3 * np.abs(d).max():.2f} mA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
