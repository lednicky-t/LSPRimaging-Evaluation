"""Pooled analysis of rim_scan2 output. usage: rim_analysis2.py NPZ SESSION_JSON"""

from __future__ import annotations
import json
import math
import sys

sys.path.insert(0, r"c:\Users\Admin\Documents\GitHub\LSPR-Suite\apps\LSPRi\eva\src")
import numpy as np
from lspr_imaging_app.analysis import query
from lspr_imaging_app.analysis.settings import MetricSettings

d = np.load(sys.argv[1])
mean, std, nval = d["mean"], d["std"], d["nval"]
labels = [str(x) for x in d["labels"]]
wl = d["wl"]
bases = [int(b) for b in d["base_ids"]]
n_cubes = mean.shape[0]
metric = MetricSettings(**json.load(open(sys.argv[2]))["analysis"]["metric"])
print(
    f"{n_cubes} cubes, {len(bases)} ROIs ({bases}); fit: {metric.fit_method} order {metric.poly_order}, {metric.metric_key}, range {metric.metric_range}"
)
VARS = [
    "A k=0.25",
    "A k=0.5",
    "A k=1.0",
    "A k=2.0",
    "A k=4.0",
    "A k=8.0",
    "B gap=0 k=1",
    "B gap=3 k=1",
    "B gap=15 k=1",
    "B gap=25 k=1",
    "B gap=40 k=1",
]


def sigma(series):
    dd = np.diff(series[np.isfinite(series)])
    return float(np.std(dd, ddof=1) / math.sqrt(2)), float(
        1.4826 * np.median(np.abs(dd - np.median(dd))) / math.sqrt(2)
    )


res = {}
for b in bases:
    for v in VARS:
        r = labels.index(f"{b}|{v}")
        S, Rf = mean[:, r, :, 0], mean[:, r, :, 1]
        pos = np.full(n_cubes, np.nan)
        for c in range(n_cubes):
            try:
                _f, x, _y = query.fit_and_metric(
                    query.formula_spectrum(wl, S[c], Rf[c], "absorbance"), metric
                )
            except Exception:  # noqa: BLE001 - a spectrum the fit cannot handle counts as "no value"
                x = None
            if x is not None:
                pos[c] = x
        A = np.log10(np.maximum(Rf, 1e-9) / np.maximum(S, 1e-9))
        ref_rel = np.median(
            [sigma(Rf[:, k])[0] / np.nanmean(Rf[:, k]) for k in range(len(wl))]
        )
        pix_rel = np.nanmedian(
            std[:, r, :, 1] / np.sqrt(nval[:, r, :, 1].astype(float)) / Rf
        )
        cor = np.median(
            [
                np.corrcoef(np.diff(np.log(S[:, k])), np.diff(np.log(Rf[:, k])))[0, 1]
                for k in range(len(wl))
            ]
        )
        res[(b, v)] = dict(
            pos=pos,
            pos_sig=sigma(pos),
            A_sig=np.median([sigma(A[:, k])[0] for k in range(len(wl))]),
            A_mean=np.nanmean(A, axis=0),
            n_ref=float(np.nanmedian(nval[:, r, :, 1])),
            n_smp=float(np.nanmedian(nval[:, r, :, 0])),
            ref_rel=ref_rel,
            pix_rel=pix_rel,
            cor=cor,
            masked=float(np.mean(d["nmask"][:, r, :, :] > 0)),
        )
print(
    "\nROI | final-value noise (nm, plain) at the default-size rim A k=1.0, pixels in sample / rim, how the rim's own noise compares with independent pixels"
)
for b in bases:
    x = res[(b, "A k=1.0")]
    xy = d["base_xy"][bases.index(b)]
    print(
        f"  ROI {b:3d} at ({xy[0]:4.0f},{xy[1]:3.0f})  noise {x['pos_sig'][0]:.3f} nm   sample px {x['n_smp']:.0f}  rim px {x['n_ref']:.0f}   rim-mean noise {x['ref_rel'] * 1e3:.2f}e-3 vs independent-pixel {x['pix_rel'] * 1e3:.2f}e-3  ({x['ref_rel'] / x['pix_rel']:.1f}x)  masked share {x['masked'] * 100:.1f}%"
    )

rng = np.random.default_rng(1)
print(
    "\n(1) Noise of the FINAL VALUE relative to the same ROI's A k=1.0 (median over ROIs, range = min..max, 95 % interval = bootstrap over ROIs of the geometric mean)"
)
print(
    f"{'variant':<15}{'median':>8}{'min':>7}{'max':>7}{'geo-mean [95 %]':>22}{'ROIs better':>13}   offset of the position vs k=1 (nm): median [min, max]"
)
for v in VARS:
    ratio = np.array(
        [res[(b, v)]["pos_sig"][0] / res[(b, "A k=1.0")]["pos_sig"][0] for b in bases]
    )
    off = np.array(
        [
            np.nanmean(res[(b, v)]["pos"]) - np.nanmean(res[(b, "A k=1.0")]["pos"])
            for b in bases
        ]
    )
    g = [np.exp(np.mean(np.log(rng.choice(ratio, len(ratio))))) for _ in range(4000)]
    print(
        f"{v:<15}{np.median(ratio):>8.2f}{ratio.min():>7.2f}{ratio.max():>7.2f}{np.exp(np.mean(np.log(ratio))):>10.2f} [{np.percentile(g, 2.5):.2f},{np.percentile(g, 97.5):.2f}]{int(np.sum(ratio < 0.97)):>8d}/{len(bases)}      {np.median(off):+.3f} [{off.min():+.3f}, {off.max():+.3f}]"
    )

print(
    "\n(2) Noise of the absorbance at one wavelength (median over wavelengths), relative to A k=1.0, and the independent-pixel model"
)
ns = np.median([res[(b, "A k=1.0")]["n_smp"] for b in bases])
print(
    f"{'variant':<15}{'measured (median over ROIs)':>28}{'model sqrt(1/Ns+1/Nr)':>24}{'median corr(sample, rim) of changes':>38}"
)
for v in VARS:
    m = np.median([res[(b, v)]["A_sig"] / res[(b, "A k=1.0")]["A_sig"] for b in bases])
    nr = np.median([res[(b, v)]["n_ref"] for b in bases])
    n1 = np.median([res[(b, "A k=1.0")]["n_ref"] for b in bases])
    model = math.sqrt(1 / ns + 1 / nr) / math.sqrt(1 / ns + 1 / n1)
    print(
        f"{v:<15}{m:>28.2f}{model:>24.2f}{np.median([res[(b, v)]['cor'] for b in bases]):>38.2f}"
    )

print(
    "\n(3) Absorbance level offset vs A k=1.0 (mean over wavelengths; median over ROIs [min,max])"
)
for v in VARS:
    off = np.array(
        [np.mean(res[(b, v)]["A_mean"] - res[(b, "A k=1.0")]["A_mean"]) for b in bases]
    )
    shp = np.array(
        [np.std(res[(b, v)]["A_mean"] - res[(b, "A k=1.0")]["A_mean"]) for b in bases]
    )
    print(
        f"  {v:<15} level {np.median(off):+.4f} [{off.min():+.4f}, {off.max():+.4f}]   wavelength-dependent part (std over wl) median {np.median(shp):.4f}"
    )
