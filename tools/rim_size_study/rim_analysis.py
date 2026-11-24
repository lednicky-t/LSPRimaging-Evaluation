"""Analyse the rim-size scan of ROI 75 (read-only; works on the .npz the scan script wrote)."""

from __future__ import annotations

import math
import sys

sys.path.insert(0, r"c:\Users\Admin\Documents\GitHub\LSPR-Suite\apps\LSPRi\eva\src")
import numpy as np

from lspr_imaging_app.analysis import query
from lspr_imaging_app.analysis.settings import MetricSettings

d = np.load(sys.argv[1])
mean, std, nval = d["mean"], d["std"], d["nval"]  # (cube, roi, wl, side)
ids = list(d["roi_ids"])
labels = [str(x) for x in d["labels"]]
wl = d["wl"]
sample_area = float(d["sample_area"])
n_cubes = mean.shape[0]
variant_rows = [i for i, lab in enumerate(labels) if lab]
print(
    f"{n_cubes} cubes, {len(wl)} wavelengths {wl[0]:.0f}-{wl[-1]:.0f} nm, sample disc ~{sample_area:.0f} px"
)

metric = (
    MetricSettings()
)  # defaults = the session's: polynomial order 3, centroid of the top 20 %


def noise_of(series: np.ndarray) -> tuple[float, float]:
    """(plain, robust) noise of a time series, from the difference of neighbouring cubes: a slow drift cancels, white noise gives sigma*sqrt(2)."""
    diff = np.diff(series[np.isfinite(series)])
    plain = float(np.std(diff, ddof=1) / math.sqrt(2.0))
    robust = float(1.4826 * np.median(np.abs(diff - np.median(diff))) / math.sqrt(2.0))
    return plain, robust


peak_wl_index = None
rows_out = []
metric_series: dict[str, np.ndarray] = {}
for r in variant_rows:
    lab = labels[r]
    S = mean[:, r, :, 0]
    Rf = mean[:, r, :, 1]
    n_ref = nval[:, r, :, 1].astype(float)
    n_smp = nval[:, r, :, 0].astype(float)
    # absorbance spectrum per cube -> the app's own fit -> the metric (centroid position, nm)
    pos = np.full(n_cubes, np.nan)
    height = np.full(n_cubes, np.nan)
    for c in range(n_cubes):
        spec = query.formula_spectrum(wl, S[c], Rf[c], "absorbance")
        try:
            _fit, x, y = query.fit_and_metric(spec, metric)
        except Exception:  # noqa: BLE001 - a spectrum the fit cannot handle counts as "no value"
            x = y = None
        if x is not None:
            pos[c], height[c] = x, (np.nan if y is None else y)
    metric_series[lab] = pos
    A = np.log10(np.maximum(Rf, 1e-9) / np.maximum(S, 1e-9))
    # per-wavelength noise of the absorbance, median over wavelengths
    a_noise = np.median([noise_of(A[:, k])[1] for k in range(len(wl))])
    ref_rel = np.median(
        [noise_of(Rf[:, k])[1] / np.nanmean(Rf[:, k]) for k in range(len(wl))]
    )
    smp_rel = np.median(
        [noise_of(S[:, k])[1] / np.nanmean(S[:, k]) for k in range(len(wl))]
    )
    # what independent pixels would give: pixel std / sqrt(N) / mean
    pix_rel = np.nanmedian(std[:, r, :, 1] / np.sqrt(n_ref) / Rf)
    rows_out.append(
        dict(
            lab=lab,
            k=np.nanmedian(n_ref) / np.nanmedian(n_smp),
            n_ref=np.nanmedian(n_ref),
            ref_rel=ref_rel,
            pix_rel=pix_rel,
            smp_rel=smp_rel,
            a_noise=a_noise,
            pos_mean=np.nanmean(pos),
            pos_noise=noise_of(pos),
            a_mean=np.nanmean(A, axis=0),
            masked_cubes=int(
                np.sum(np.nanmin(n_ref, axis=1) < np.nanmax(n_ref) * 0.97)
            ),
        )
    )

orig = next(x for x in rows_out if x["lab"] == "orig")
k1 = next(x for x in rows_out if x["lab"] == "A k=1.0")
print(
    "\n(1) Noise sources, per variant. Noise = robust sigma from cube-to-cube differences, median over the 26 wavelengths."
)
print(
    f"{'variant':<18}{'ref px':>7}{'k':>6}{'ref mean noise':>16}{'if indep. px':>14}{'sample noise':>14}{'A noise':>10}"
)
for x in rows_out:
    print(
        f"{x['lab']:<18}{x['n_ref']:>7.0f}{x['k']:>6.2f}{x['ref_rel'] * 1e3:>14.2f}e-3{x['pix_rel'] * 1e3:>12.2f}e-3{x['smp_rel'] * 1e3:>12.2f}e-3{x['a_noise'] * 1e3:>8.2f}e-3"
    )

print(
    "\n(2) Final value (centroid of the order-3 polynomial, top 20 %): noise in nm and offset from the original rim."
)
print(
    f"{'variant':<18}{'k':>6}{'noise plain':>13}{'noise robust':>14}{'vs A k=1':>10}{'mean pos nm':>13}{'offset nm':>11}"
)
for x in rows_out:
    p, rob = x["pos_noise"]
    print(
        f"{x['lab']:<18}{x['k']:>6.2f}{p:>11.4f}nm{rob:>12.4f}nm{rob / k1['pos_noise'][1]:>10.2f}{x['pos_mean']:>13.3f}{x['pos_mean'] - orig['pos_mean']:>+11.3f}"
    )

# paired bootstrap of the noise ratio of the final value (resample difference indices; same indices for every variant)
rng = np.random.default_rng(0)
base_d = np.diff(metric_series["A k=1.0"])
ok = np.isfinite(base_d)
print(
    "\n(3) Noise of the final value relative to A k=1.0 (the original-sized rim), paired bootstrap 95 % interval:"
)
for lab, series in metric_series.items():
    dd = np.diff(series)
    good = ok & np.isfinite(dd)
    ratios = []
    for _ in range(2000):
        idx = rng.integers(0, good.sum(), good.sum())
        ratios.append(np.std(dd[good][idx], ddof=1) / np.std(base_d[good][idx], ddof=1))
    lo, hi = np.percentile(ratios, [2.5, 97.5])
    print(
        f"  {lab:<18} {np.std(dd[good], ddof=1) / np.std(base_d[good], ddof=1):5.2f}  [{lo:.2f}, {hi:.2f}]"
    )

# predicted noise from the model sqrt(1/N_s + 1/N_r)
print(
    "\n(4) Model: noise of the ratio ~ sqrt(1/N_sample + 1/N_ref); relative to k=1 (A k=1.0), compared with the measured absorbance noise:"
)
ns = np.nanmedian(
    nval[:, [i for i, name in enumerate(labels) if name == "A k=1.0"][0], :, 0]
)
for x in rows_out:
    model = math.sqrt(1.0 / ns + 1.0 / x["n_ref"]) / math.sqrt(
        1.0 / ns + 1.0 / k1["n_ref"]
    )
    print(
        f"  {x['lab']:<18} model {model:5.2f}   measured {x['a_noise'] / k1['a_noise']:5.2f}"
    )

# bias of the spectrum level (not shape): mean absorbance over cubes, per variant, relative to orig
print(
    "\n(5) Mean absorbance level over the 26 wavelengths vs the original rim (offset), and its spread over wavelengths (shape change):"
)
for x in rows_out:
    diff = x["a_mean"] - orig["a_mean"]
    print(
        f"  {x['lab']:<18} mean offset {np.mean(diff):+.4f}   wavelength-dependent part (std over wl) {np.std(diff):.4f}"
    )
print(
    "\ncubes with a changed pixel count in the reference (masks etc.), per variant:",
    {x["lab"]: x["masked_cubes"] for x in rows_out},
)
