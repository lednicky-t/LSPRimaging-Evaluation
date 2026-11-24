"""Noise of step 1 with / without the ignore mask. usage: mask_analysis.py NPZ SESSION_JSON"""
from __future__ import annotations
import json
import math
import sys

sys.path.insert(0, r"c:\Users\Admin\Documents\GitHub\LSPR-Suite\apps\LSPRi\eva\src")
import numpy as np
from lspr_imaging_app.analysis import query
from lspr_imaging_app.analysis.settings import MetricSettings

d = np.load(sys.argv[1])
metric = MetricSettings(**json.load(open(sys.argv[2]))["analysis"]["metric"])
wl = d["wl"]
ids = [int(i) for i in d["roi_ids"]]
nC = d["mean_with"].shape[0]
print(f"{nC} cubes (step 1), {len(ids)} ROIs, {len(wl)} wavelengths; fit {metric.fit_method} order {metric.poly_order}, {metric.metric_key}, {metric.metric_range} {metric.metric_top_fraction}")


def sig_diff(x):
    x = x[np.isfinite(x)]
    dd = np.diff(x)
    return float(np.std(dd, ddof=1) / math.sqrt(2)) if len(dd) > 3 else np.nan


def per_variant(tag):
    M, N = d[f"mean_{tag}"], d[f"nmask_{tag}"]
    out = {}
    for r, rid in enumerate(ids):
        S, R = M[:, r, :, 0], M[:, r, :, 1]
        pos = np.full(nC, np.nan)
        for c in range(nC):
            try:
                _f, x, _y = query.fit_and_metric(query.formula_spectrum(wl, S[c], R[c], "absorbance"), metric)
            except Exception:  # noqa: BLE001 - a spectrum the fit cannot handle counts as "no value"
                x = None
            if x is not None:
                pos[c] = x
        A = np.log10(np.maximum(R, 1e-9) / np.maximum(S, 1e-9))
        Am = np.nanmean(A, axis=0)
        k = int(np.argmax(Am))
        ns = np.array([sig_diff(A[:, j]) for j in range(len(wl))])
        out[rid] = dict(
            pos=pos, pos_sig=sig_diff(pos), pos_std=float(np.nanstd(pos, ddof=1)), pos_mean=float(np.nanmean(pos)),
            A_mean=Am, A_peak=float(Am[k]), A_sig_peak=float(ns[k]),
            ns_peak=float(ns[k] / abs(Am[k])), ns_med=float(np.nanmedian(ns / np.abs(Am))),
            nS=float(np.median(d[f"nval_{tag}"][:, r, :, 0])), nR=float(np.median(d[f"nval_{tag}"][:, r, :, 1])),
            msk_S=float(np.median(N[:, r, :, 0])), msk_R=float(np.median(N[:, r, :, 1])),
            nom_S=float(np.median(d[f"nnom_{tag}"][:, r, :, 0])), nom_R=float(np.median(d[f"nnom_{tag}"][:, r, :, 1])),
        )
    return out


W, NOM = per_variant("with"), per_variant("without")
aff = [rid for rid in ids if W[rid]["msk_S"] > 0 or W[rid]["msk_R"] > 0]
affS = [rid for rid in ids if W[rid]["msk_S"] > 0]
affR = [rid for rid in ids if W[rid]["msk_R"] > 0 and W[rid]["msk_S"] == 0]
un = [rid for rid in ids if rid not in aff]
print(f"\nROIs touched by the mask in step 1: {len(aff)} of {len(ids)}  (sample disc touched: {len(affS)}; only the rim touched: {len(affR)}; untouched: {len(un)})")
same = all(np.array_equal(W[r]["pos"], NOM[r]["pos"], equal_nan=True) for r in un)
print(f"untouched ROIs give bit-identical peak positions with / without mask: {same}")
frac = np.array([W[r]["msk_S"] / NOM[r]["nom_S"] for r in affS]) if affS else np.array([])
if len(frac):
    print(f"share of the sample disc masked (affected discs): median {np.median(frac) * 100:.1f} %, range {frac.min() * 100:.1f}..{frac.max() * 100:.1f} %")
rimfrac = np.array([W[r]["msk_R"] / NOM[r]["nom_R"] for r in aff if W[r]["msk_R"] > 0])
if len(rimfrac):
    print(f"masked pixels in the rim (affected): median {np.median(rimfrac) * 100:.1f} % of the rim's pixels, range {rimfrac.min() * 100:.1f}..{rimfrac.max() * 100:.1f} %")


def summarise(group, name):
    if not group:
        print(f"\n{name}: none")
        return
    r_pos = np.array([W[g]["pos_sig"] / NOM[g]["pos_sig"] for g in group])
    r_ns = np.array([W[g]["ns_peak"] / NOM[g]["ns_peak"] for g in group])
    r_nsm = np.array([W[g]["ns_med"] / NOM[g]["ns_med"] for g in group])
    off = np.array([W[g]["pos_mean"] - NOM[g]["pos_mean"] for g in group])
    ps_o = np.array([NOM[g]["pos_sig"] for g in group])
    ps_w = np.array([W[g]["pos_sig"] for g in group])
    print(f"\n{name} (n = {len(group)} ROIs)")
    print(f"  peak-position noise (nm, cube-to-cube): without mask median {np.median(ps_o):.3f}, with mask median {np.median(ps_w):.3f}")
    print(f"  ratio with/without: median {np.median(r_pos):.3f}, range {r_pos.min():.3f}..{r_pos.max():.3f}; ROIs better with mask {int(np.sum(r_pos < 0.97))}, worse {int(np.sum(r_pos > 1.03))}, within +-3 %: {int(np.sum(np.abs(r_pos - 1) <= 0.03))}")
    print(f"  noise/signal of absorbance at its peak wavelength: without {np.median([NOM[g]['ns_peak'] for g in group]) * 100:.3f} %, with {np.median([W[g]['ns_peak'] for g in group]) * 100:.3f} %; ratio median {np.median(r_ns):.3f} [{r_ns.min():.3f}..{r_ns.max():.3f}]")
    print(f"  noise/signal, median over wavelengths: ratio median {np.median(r_nsm):.3f} [{r_nsm.min():.3f}..{r_nsm.max():.3f}]")
    print(f"  mean peak position shift with - without (nm): median {np.median(off):+.3f}, range {off.min():+.3f}..{off.max():+.3f}; in units of the ROI's own noise: median {np.median(np.abs(off) / ps_o):.2f}")


summarise(affS, "Sample disc touched")
summarise(affR, "Only the rim touched")
summarise(aff, "All touched")
summarise(un, "Untouched (control: must be 1.000)")
allr = np.array([W[g]["pos_sig"] / NOM[g]["pos_sig"] for g in ids])
print(f"\nWhole image, all {len(ids)} ROIs: median noise-ratio with/without {np.median(allr):.4f}; geometric mean {np.exp(np.mean(np.log(allr))):.4f}")
print(f"median final-value noise over all ROIs: without {np.median([NOM[g]['pos_sig'] for g in ids]):.3f} nm, with {np.median([W[g]['pos_sig'] for g in ids]):.3f} nm")
print("\nper affected ROI (sorted by masked share of the disc):")
print(f"{'ROI':>4}{'disc masked':>12}{'rim masked':>11}{'noise w/o [nm]':>15}{'noise with':>11}{'ratio':>7}{'offset [nm]':>12}{'N/S w/o %':>10}{'N/S with %':>11}")
for g in sorted(aff, key=lambda g: -(W[g]["msk_S"] / NOM[g]["nom_S"])):
    print(f"{g:>4}{W[g]['msk_S'] / NOM[g]['nom_S'] * 100:>11.1f}%{W[g]['msk_R'] / NOM[g]['nom_R'] * 100:>10.1f}%{NOM[g]['pos_sig']:>15.3f}{W[g]['pos_sig']:>11.3f}{W[g]['pos_sig'] / NOM[g]['pos_sig']:>7.2f}{W[g]['pos_mean'] - NOM[g]['pos_mean']:>+12.3f}{NOM[g]['ns_peak'] * 100:>10.3f}{W[g]['ns_peak'] * 100:>11.3f}")

print("\nBy how much of the disc the mask removes (ratio = noise with / noise without):")
sh = {g: W[g]["msk_S"] / NOM[g]["nom_S"] for g in affS}
rng = np.random.default_rng(1)
for lo, hi in ((0, .02), (.02, .05), (.05, .2), (.2, .5), (.5, 1.01)):
    grp = [g for g in affS if lo <= sh[g] < hi]
    if not grp:
        continue
    r = np.array([W[g]["pos_sig"] / NOM[g]["pos_sig"] for g in grp])
    n = np.array([W[g]["ns_peak"] / NOM[g]["ns_peak"] for g in grp])
    o = np.array([W[g]["pos_mean"] - NOM[g]["pos_mean"] for g in grp])
    bs = [np.median(rng.choice(r, len(r))) for _ in range(3000)]
    print(f"  {lo*100:>4.0f}-{min(hi,1)*100:<4.0f}% masked: n={len(grp):3d}  noise ratio median {np.median(r):.3f} [95% {np.percentile(bs,2.5):.3f}..{np.percentile(bs,97.5):.3f}]  N/S ratio median {np.median(n):.3f}  peak offset median {np.median(o):+.3f} nm [{o.min():+.2f}..{o.max():+.2f}]")
r = np.array([W[g]["pos_sig"] / NOM[g]["pos_sig"] for g in affS])
bs = [np.median(rng.choice(r, len(r))) for _ in range(3000)]
print(f"all 156: median noise ratio {np.median(r):.3f}, 95% interval of the median {np.percentile(bs,2.5):.3f}..{np.percentile(bs,97.5):.3f}")
x = np.array([sh[g] for g in affS])
print("rank correlation masked share vs noise ratio:", float(np.corrcoef(np.argsort(np.argsort(x)), np.argsort(np.argsort(r)))[0,1]))
