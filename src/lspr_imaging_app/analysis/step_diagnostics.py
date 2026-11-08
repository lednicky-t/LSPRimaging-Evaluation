"""Checks that tell the user when the data behave systematically "wrong" (2026-11-06). Pure numpy / scipy, no Qt.

Built from the S1/B1 investigation of 2026-11-06 (`docs/snr_methods_comparison_2026-11-06.md`), where three steps were 1.7x noisier because one pump ran
faster, and the noise was in the sample ROIs, not in the reference ROIs or the light. Each check turns one such finding into a number with a
threshold and a sentence. **Nothing here changes a result**: the checks only read the scan, the metric traces and the plan.

**Inputs.** The stored scan (`StoredScan`), the formula, the metric traces of the ROIs (``{roi id: float64 [cube]}``, the Sensorgram's own), which cubes
belong to which step (`step_stats.StepMembership`), optionally an evaluation pair (baseline and signal step, as positions in ``membership.steps``), the ROI
groups and the template-shift traces (for the "how much of the shift does the method read" check). Noise everywhere is the step noise of the app: the SD
about the least-squares line through the step (`step_stats`), so a slow linear drift is not counted as noise.

**Checks** (``Finding.check``):

1. ``noisy_step``: the metric noise of each step against the median of all steps; with the plan's device settings, which settings all noisy steps share.
2. ``noise_source``: where the absorbance noise of a step is: the sample ROIs, the reference ROIs, or light shared by every ROI.
3. ``drift``: noise that is not white inside a step (step noise against cube-to-cube noise) and steps that have not settled (slope x duration).
4. ``shared``: fluctuations every ROI sees together (correlation of the ROIs' residuals).
5. ``efficiency``: the metric noise in the baseline step against the Cramér-Rao limit of a pure shift for the measured absorbance noise.
6. ``gain``: how much of the template shift the metric reads, so noise in nm is compared fairly between methods.
8. ``masks``: pixel counts that change inside a step, or differ between baseline and signal.
9. ``outliers``: ROIs whose noise or signal is far from the others (robust z), and groups that are noisier as a whole.
10. ``shape``: what the change between baseline and signal is made of (shift, amplitude, width, rest).

(7, image motion, reads the images and lives with the Image panel: `image_motion.py`.)
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import CubicSpline

from .query import formula_values
from .scan_store import StoredScan
from .scan_view import REFERENCE, SAMPLE, _primary_method
from .step_stats import StepMembership

OK, WARN, INFO = "ok", "warn", "info"

NOISY_STEP_RATIO = 1.3  # a step this much noisier than the median step is flagged
SOURCE_CORRELATION_DROP = 0.15  # sample-reference correlation this much below the median step: the extra noise is not shared by the reference ROI
SHARED_FLICKER_RATIO = 2.0  # light noise shared by every ROI this much above the median step
DRIFT_RATIO = 1.3  # step noise / cube-to-cube noise above this: the noise is not white inside the step
SETTLE_SIGMAS = 3.0  # |slope| x duration above this many noise SDs: the step has not settled
SHARED_CORRELATION = 0.3  # median ROI-to-ROI correlation of the residuals above this: something all ROIs see
EFFICIENCY_LIMIT = 1.3  # metric noise / Cramér-Rao limit above this: the metric wastes information
GAIN_LOW = 0.8  # metric reads less than this fraction of the template shift
OUTLIER_Z = 3.5  # robust z (median / 1.4826 MAD) above this
NOISY_GROUP_RATIO = 1.5  # a group whose median noise is this much above the median ROI
SHAPE_NON_SHIFT = 0.25  # share of the change a pure shift does not explain above this: methods disagree on "shift"
GRADIENT_T = 4.0  # a gradient across the field counts when its t value is above this ...
GRADIENT_CHANGE = 0.05  # ... and it changes the signal (or noise) by more than this fraction across the field
DEVICE_QUIET_SHARE = 1 / 3  # a device setting of every noisy step is reported when at most this share of the quiet steps has it


@dataclass(frozen=True)
class Finding:
    """One check's result: ``level`` ok / warn / info, a short ``title``, a ``detail`` sentence and the ``numbers`` behind it (for the report and the log)."""

    check: str
    level: str
    title: str
    detail: str
    numbers: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class StepNumbers:
    """Per step (columns in the order of ``membership.steps``), the numbers the checks were made from: medians over the ROIs."""

    labels: tuple[str, ...]
    n_cubes: np.ndarray
    metric_noise_nm: np.ndarray  # step noise of the metric (SD about the line)
    p2p_noise_nm: np.ndarray  # cube-to-cube noise of the metric (SD of first differences / sqrt 2)
    drift_sigmas: np.ndarray  # |slope| x duration / step noise
    absorbance_noise: np.ndarray  # step noise of the formula values, per wavelength
    sample_noise: np.ndarray  # step noise of log10(sample intensity)
    reference_noise: np.ndarray  # step noise of log10(reference intensity)
    sample_reference_corr: np.ndarray
    shared_light: np.ndarray  # step noise of the ROI-mean log10(sample): light fluctuations every ROI sees
    roi_corr: np.ndarray  # median ROI-to-ROI correlation of the metric residuals


@dataclass(frozen=True)
class DiagnosticsReport:
    findings: tuple[Finding, ...]
    steps: StepNumbers | None


# -- small helpers ---------------------------------------------------------------------------------------------------------------


def _residuals(values: np.ndarray, t_s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Least-squares line through each column of ``values`` ``[cube, ...]`` (NaN = missing): ``(residuals, slope per s, n)``; residual NaN where missing."""
    v = values.reshape(values.shape[0], -1)
    ok = np.isfinite(v)
    n = ok.sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        tm = np.where(ok, t_s[:, None], 0).sum(0) / n
        vm = np.where(ok, v, 0).sum(0) / n
        dt = np.where(ok, t_s[:, None] - tm, 0)
        dv = np.where(ok, v - vm, 0)
        slope = (dt * dv).sum(0) / (dt**2).sum(0)
    res = np.where(ok, dv - slope * dt, np.nan)
    shape = values.shape[1:]
    return res.reshape(values.shape), slope.reshape(shape), n.reshape(shape)


def _line_sd(residuals: np.ndarray, n: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.sqrt(np.nansum(residuals**2, axis=0) / np.where(n > 2, n - 2, np.nan))


def _nanmedian(values: np.ndarray, axis=None) -> np.ndarray | float:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(values, axis=axis)


def _robust_z(values: np.ndarray) -> np.ndarray:
    med = _nanmedian(values)
    mad = 1.4826 * _nanmedian(np.abs(values - med))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (values - med) / mad if mad > 0 else np.zeros_like(values)


def _median_offdiag_corr(res: np.ndarray) -> float:
    """``res`` ``[cube, roi]``: the median correlation between the residuals of two ROIs (complete cubes only)."""
    ok = np.isfinite(res).all(axis=1)
    r = res[ok]
    keep = np.nanstd(r, axis=0) > 0 if r.size else np.zeros(res.shape[1], bool)
    r = r[:, keep]
    if r.shape[0] < 4 or r.shape[1] < 2:
        return float("nan")
    c = np.corrcoef(r.T)
    return float(_nanmedian(c[~np.eye(c.shape[0], dtype=bool)]))


def _scan_arrays(stored: StoredScan, formula_key: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``(wavelengths ascending, formula values, log10 sample, log10 reference)``, the arrays ``[cube, roi, wavelength]``, NaN where not usable."""
    mean = stored.values[_primary_method(stored)]
    sample, reference = mean[..., SAMPLE], mean[..., REFERENCE]
    usable = np.isfinite(sample) & np.isfinite(reference) & (stored.n_valid[..., SAMPLE] > 0) & (stored.n_valid[..., REFERENCE] > 0)
    usable &= (np.asarray(stored.cell_hash) != 0)[..., None]
    with np.errstate(all="ignore"):
        values = formula_values(sample, reference, formula_key)
        log_s, log_r = np.log10(np.maximum(sample, 1e-12)), np.log10(np.maximum(reference, 1e-12))
    wl = np.asarray(stored.wavelength_nm, dtype=np.float64)
    order = np.argsort(wl, kind="stable")
    pick = lambda a: np.where(usable, a, np.nan)[..., order]  # noqa: E731
    return wl[order], pick(values), pick(log_s), pick(log_r)


def _step_times(membership: StepMembership, cubes: np.ndarray) -> np.ndarray:
    t = membership.cube_first_unix_ms[cubes]
    return (t - t[0]) / 1000.0


# -- the per-step numbers --------------------------------------------------------------------------------------------------------


def step_numbers(stored: StoredScan, formula_key: str, traces: Mapping[int, np.ndarray], membership: StepMembership) -> StepNumbers:
    """Everything checks 1-4 read, per step (``traces`` must hold every ROI of the scan that is to be judged; ROI order = ``sorted(traces)``)."""
    _wl, values, log_s, log_r = _scan_arrays(stored, formula_key)
    row_of = {int(r): i for i, r in enumerate(stored.roi_ids)}
    roi_ids = [r for r in sorted(traces) if r in row_of]
    rows = [row_of[r] for r in roi_ids]
    T = np.vstack([traces[r] for r in roi_ids]).T if roi_ids else np.empty((len(stored.cube_indices), 0))  # [cube, roi]
    n_step = len(membership.steps)
    out = {k: np.full(n_step, np.nan) for k in ("metric", "p2p", "drift", "abs", "s", "r", "corr", "light", "roi_corr")}
    n_cubes = membership.used.sum(axis=1).astype(np.int64)
    for k in range(n_step):
        cubes = np.flatnonzero(membership.used[k])
        if cubes.size < 4 or not rows:
            continue
        t = _step_times(membership, cubes)
        res, slope, n = _residuals(T[cubes], t)
        sd = _line_sd(res, n)
        out["metric"][k] = _nanmedian(sd)
        with np.errstate(invalid="ignore"):
            p2p = np.nanstd(np.diff(T[cubes], axis=0), axis=0, ddof=1) / np.sqrt(2.0)
            out["drift"][k] = _nanmedian(np.abs(slope) * (t[-1] - t[0]) / sd)
        out["p2p"][k] = _nanmedian(p2p)
        out["roi_corr"][k] = _median_offdiag_corr(res)
        sub = values[cubes][:, rows]
        ra, _, na = _residuals(sub, t)
        out["abs"][k] = _nanmedian(_line_sd(ra, na))
        rs, _, ns = _residuals(log_s[cubes][:, rows], t)
        rr, _, nr = _residuals(log_r[cubes][:, rows], t)
        out["s"][k], out["r"][k] = _nanmedian(_line_sd(rs, ns)), _nanmedian(_line_sd(rr, nr))
        with np.errstate(invalid="ignore", divide="ignore"):
            num = np.nansum(rs * rr, axis=0)
            den = np.sqrt(np.nansum(rs**2, axis=0) * np.nansum(rr**2, axis=0))
            out["corr"][k] = _nanmedian(num / den)
            light = np.nanmean(rs, axis=1)  # [cube, wl]: the part of the sample noise every ROI shares
            out["light"][k] = _nanmedian(np.nanstd(light, axis=0, ddof=2))
    labels = tuple(f"{s.order}: {s.label}" if getattr(s, "order", 0) else str(s.label) for s in membership.steps)
    return StepNumbers(labels, n_cubes, out["metric"], out["p2p"], out["drift"], out["abs"], out["s"], out["r"], out["corr"], out["light"], out["roi_corr"])


# -- the checks ------------------------------------------------------------------------------------------------------------------


def _device_settings(step: object) -> set[tuple[str, str]]:
    devices = getattr(step, "devices", None) or {}
    flat: set[tuple[str, str]] = set()

    def walk(prefix: str, value: object) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif value not in (None, ""):
            flat.add((prefix, str(value)))

    walk("", devices)
    return flat


def check_noisy_steps(numbers: StepNumbers, steps: Sequence[object]) -> list[Finding]:
    noise = numbers.metric_noise_nm
    if np.isfinite(noise).sum() < 3:
        return [Finding("noisy_step", INFO, "Noisy steps: not enough steps", "At least three steps with four or more cubes are needed to compare their noise.")]
    reference = float(_nanmedian(noise))
    ratio = noise / reference
    noisy = [k for k in range(len(noise)) if np.isfinite(ratio[k]) and ratio[k] > NOISY_STEP_RATIO]
    if not noisy:
        return [Finding("noisy_step", OK, "Step noise is even", f"No step is more than {NOISY_STEP_RATIO:.1f}x noisier than the median step ({reference:.4f} nm).",
                        {"median_noise_nm": reference})]
    names = ", ".join(f"{numbers.labels[k]} ({ratio[k]:.1f}x)" for k in noisy)
    detail = f"{len(noisy)} step(s) noisier than {NOISY_STEP_RATIO:.1f}x the median step ({reference:.4f} nm): {names}."
    quiet = [k for k in range(len(noise)) if np.isfinite(ratio[k]) and ratio[k] <= NOISY_STEP_RATIO]
    common = set.intersection(*(_device_settings(steps[k]) for k in noisy))
    in_quiet = {item: sum(item in _device_settings(steps[k]) for k in quiet) for item in common}
    shared = sorted(item for item, n in in_quiet.items() if not quiet or n <= DEVICE_QUIET_SHARE * len(quiet))
    if shared:
        detail += " Device settings every noisy step has: " + "; ".join(f"{k} = {v} (in {in_quiet[(k, v)]} of {len(quiet)} quiet steps)" for k, v in shared) + "."
    elif any(_device_settings(steps[k]) for k in noisy):
        detail += " No device setting is common to all of them and rare in the quiet steps."
    return [Finding("noisy_step", WARN, "Some steps are noisier", detail,
                    {"median_noise_nm": reference, "noisy_steps": [numbers.labels[k] for k in noisy], "ratios": [float(ratio[k]) for k in noisy],
                     "shared_device_settings": [list(item) for item in shared]})]


def check_noise_source(numbers: StepNumbers) -> list[Finding]:
    found: list[Finding] = []
    abs_ref = float(_nanmedian(numbers.absorbance_noise))
    corr_ref = float(_nanmedian(numbers.sample_reference_corr))
    light_ref = float(_nanmedian(numbers.shared_light))
    s_ref, r_ref = float(_nanmedian(numbers.sample_noise)), float(_nanmedian(numbers.reference_noise))
    for k, label in enumerate(numbers.labels):
        a = numbers.absorbance_noise[k]
        if not np.isfinite(a) or a <= NOISY_STEP_RATIO * abs_ref:
            continue
        parts = []
        if numbers.sample_reference_corr[k] < corr_ref - SOURCE_CORRELATION_DROP and numbers.sample_noise[k] / s_ref > numbers.reference_noise[k] / r_ref:
            parts.append(f"in the sample ROIs, not the reference ROIs (sample ROI noise {numbers.sample_noise[k] / s_ref:.1f}x, reference ROI {numbers.reference_noise[k] / r_ref:.1f}x the median step; "
                         f"sample-reference correlation {numbers.sample_reference_corr[k]:.2f} against {corr_ref:.2f})")
        if numbers.shared_light[k] > SHARED_FLICKER_RATIO * light_ref:
            parts.append(f"light fluctuations that every ROI sees are {numbers.shared_light[k] / light_ref:.1f}x the median step (the reference ROI cancels most of them; "
                         "what it does not cancel may be the extra noise)")
        if not parts:
            parts.append("in sample and reference ROIs alike, not shared by the ROIs")
        found.append(Finding("noise_source", WARN, f"Where the extra noise of {label} is",
                             f"Absorbance noise {a / abs_ref:.1f}x the median step: " + "; ".join(parts) + ".",
                             {"step": label, "absorbance_noise": float(a), "median_absorbance_noise": abs_ref}))
    return found or [Finding("noise_source", OK, "Absorbance noise is even", f"No step has more than {NOISY_STEP_RATIO:.1f}x the median absorbance noise.")]


def check_drift(numbers: StepNumbers) -> list[Finding]:
    found: list[Finding] = []
    for k, label in enumerate(numbers.labels):
        ratio = numbers.metric_noise_nm[k] / numbers.p2p_noise_nm[k] if numbers.p2p_noise_nm[k] > 0 else np.nan
        if np.isfinite(ratio) and ratio > DRIFT_RATIO:
            found.append(Finding("drift", WARN, f"{label}: slow wander", f"The step noise is {ratio:.2f}x the cube-to-cube noise: the trace wanders (not white noise), "
                                 "so the mean of the step is less certain than its SD suggests.", {"step": label, "ratio": float(ratio)}))
        if np.isfinite(numbers.drift_sigmas[k]) and numbers.drift_sigmas[k] > SETTLE_SIGMAS:
            found.append(Finding("drift", WARN, f"{label}: not settled", f"The trace drifts {numbers.drift_sigmas[k]:.1f} noise SDs over the step: it has not reached "
                                 "a plateau. Consider skipping the start of the step or a longer step.", {"step": label, "drift_sigmas": float(numbers.drift_sigmas[k])}))
    return found or [Finding("drift", OK, "Steps are settled", "No step wanders or drifts beyond the thresholds.")]


def check_shared(numbers: StepNumbers) -> list[Finding]:
    high = [(numbers.labels[k], c) for k, c in enumerate(numbers.roi_corr) if np.isfinite(c) and c > SHARED_CORRELATION]
    if not high:
        return [Finding("shared", OK, "ROI noise is independent", f"In no step do the ROIs fluctuate together (median correlation <= {SHARED_CORRELATION}).")]
    return [Finding("shared", WARN, "ROIs fluctuate together", "In " + ", ".join(f"{name} (correlation {c:.2f})" for name, c in high) + " the ROIs move together: "
                    "a real change all of them see (temperature, flow, mixing). Averaging ROIs does not remove it.", {"steps": [name for name, _ in high]})]


def cramer_rao_nm(wl: np.ndarray, spectra: np.ndarray, t_s: np.ndarray) -> np.ndarray:
    """Per ROI, the Cramér-Rao limit (nm) of a pure shift with a free scale and offset, from the spectra ``[cube, roi, wl]`` of one step (per-wavelength
    noise about the line through the step, assumed white)."""
    res, _, n = _residuals(spectra, t_s)
    sigma = _line_sd(res, n)  # [roi, wl]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(spectra, axis=0)
    out = np.full(spectra.shape[1], np.nan)
    for i in range(spectra.shape[1]):
        if not (np.isfinite(mean[i]).all() and np.isfinite(sigma[i]).all() and (sigma[i] > 0).all()):
            continue
        J = np.c_[CubicSpline(wl, mean[i])(wl, 1), mean[i], np.ones_like(wl)] / sigma[i][:, None]
        try:
            out[i] = np.sqrt(np.linalg.inv(J.T @ J)[0, 0])
        except np.linalg.LinAlgError:
            pass
    return out


def check_pair(
    stored: StoredScan, formula_key: str, traces: Mapping[int, np.ndarray], membership: StepMembership, pair: tuple[int, int],
    template_traces: Mapping[int, np.ndarray] | None = None, groups: Sequence[tuple[str, Sequence[int]]] = (), method_is_shape: bool = False,
) -> list[Finding]:
    """Checks 5, 6, 8, 9, 10 for one evaluation pair; ``pair`` = (baseline step, signal step) as positions in ``membership.steps``."""
    b, s = pair
    base, sig = np.flatnonzero(membership.used[b]), np.flatnonzero(membership.used[s])
    names = (membership.steps[b].label, membership.steps[s].label)
    if base.size < 4 or sig.size < 1:
        return [Finding("pair", INFO, "Evaluation pair: too few cubes", f"The baseline needs >= 4 cubes and the signal >= 1 (have {base.size} and {sig.size}).")]
    wl, values, _ls, _lr = _scan_arrays(stored, formula_key)
    row_of = {int(r): i for i, r in enumerate(stored.roi_ids)}
    roi_ids = [r for r in sorted(traces) if r in row_of]
    rows = np.array([row_of[r] for r in roi_ids], dtype=np.int64)
    T = np.vstack([traces[r] for r in roi_ids])  # [roi, cube]
    tb = _step_times(membership, base)
    res, _, n = _residuals(T[:, base].T, tb)
    noise = _line_sd(res, n)  # [roi]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        signal = np.nanmean(T[:, sig], 1) - np.nanmean(T[:, base], 1)
    found: list[Finding] = []

    # 6. gain against the template shift (first: the efficiency is judged in the template's nm)
    gain = float("nan")
    if template_traces is not None:
        tt = np.vstack([template_traces.get(r, np.full(T.shape[1], np.nan)) for r in roi_ids])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            t_signal = np.nanmean(tt[:, sig], 1) - np.nanmean(tt[:, base], 1)
        gain = float(_nanmedian(signal / t_signal))
        if np.isfinite(gain):
            level = WARN if abs(gain) < GAIN_LOW else OK
            found.append(Finding("gain", level, f"The metric reads {100 * gain:.0f} % of the template shift",
                                 ("Its noise in nm is smaller by the same factor: compare signal-to-noise, not noise, between methods." if level == WARN
                                  else "Noise in nm can be compared with the template shift directly."), {"gain": gain}))

    # 5. efficiency against the pure-shift limit, in the template's nm (noise / gain)
    crb = cramer_rao_nm(wl, values[base][:, rows], tb)
    scale = abs(gain) if np.isfinite(gain) and gain != 0 else 1.0
    eff = float(_nanmedian(noise / scale / crb))
    if np.isfinite(eff):
        if not np.isfinite(gain) and not method_is_shape:
            found.append(Finding("efficiency", INFO, "Noise against the pure-shift limit", f"Metric noise {eff:.2f}x the Cramér-Rao limit of a pure shift, in the metric's own "
                                 "nm (a method that reads only part of the shift looks better than it is: see the gain check).", {"ratio": eff}))
        elif method_is_shape or eff < 0.9:
            found.append(Finding("efficiency", INFO, "Noise against the pure-shift limit", f"Metric noise {eff:.2f}x the Cramér-Rao limit of a pure shift (in template nm). Below 1 "
                                 "is possible for a metric that also uses the amplitude / width change (centroid, response shape).", {"ratio": eff}))
        elif eff > EFFICIENCY_LIMIT:
            found.append(Finding("efficiency", WARN, "The metric wastes information", f"Its noise in the baseline is {eff:.2f}x the Cramér-Rao limit of a pure shift for this "
                                 "absorbance noise: another method could be quieter (template shift, all-points centroid).", {"ratio": eff}))
        else:
            found.append(Finding("efficiency", OK, "The metric is near the noise limit", f"Noise {eff:.2f}x the Cramér-Rao limit of a pure shift: no shift method can do much better.",
                                 {"ratio": eff}))

    # 8. masks / pixels
    nv = stored.n_valid[..., SAMPLE][:, rows] if stored.n_valid.ndim == 4 else stored.n_valid[:, rows]
    ids = np.asarray(roi_ids)
    changing = {nm: ids[(np.ptp(nv[c], axis=0) > 0).any(axis=-1)].tolist() for nm, c in (("baseline", base), ("signal", sig))}
    differ = ids[(np.abs(np.median(nv[sig], 0) - np.median(nv[base], 0)) > 0).any(axis=-1)].tolist()
    if any(changing.values()) or differ:
        def show(v: list) -> str:
            return (", ".join(str(r) for r in v[:15]) + (" ..." if len(v) > 15 else "")) if v else "none"

        found.append(Finding("masks", WARN, "Pixels change inside the pair", f"ROIs whose pixel count changes inside the baseline: {show(changing['baseline'])}; inside the "
                             f"signal: {show(changing['signal'])}; ROIs that use different pixels in baseline and signal: {show(differ)}. A mask change can look like a step.",
                             {**changing, "differ": differ}))
    else:
        found.append(Finding("masks", OK, "Masks and pixels are steady", "Every ROI uses the same pixels throughout the baseline and the signal."))

    # 9. outliers
    zn, zs = _robust_z(noise), _robust_z(signal)
    def what(i: int) -> str:
        if abs(zn[i]) >= abs(zs[i]):
            return "noisier" if zn[i] > 0 else "quieter"
        return f"signal {signal[i]:+.2f} nm" if np.isfinite(signal[i]) else "signal"

    odd = [(roi_ids[i], what(i), float(max(abs(zn[i]), abs(zs[i])))) for i in range(len(roi_ids)) if max(abs(zn[i]), abs(zs[i])) > OUTLIER_Z]
    med_noise = float(_nanmedian(noise))
    noisy_groups = []
    index = {r: i for i, r in enumerate(roi_ids)}
    for name, members in groups:
        idx = [index[int(m)] for m in members if int(m) in index]
        if idx and _nanmedian(noise[idx]) > NOISY_GROUP_RATIO * med_noise:
            noisy_groups.append((name, float(_nanmedian(noise[idx]) / med_noise)))
    gradients = _gradients(stored, roi_ids, sig, {"signal": signal, "noise": noise})
    if odd or noisy_groups or gradients:
        text = []
        if odd:
            text.append(f"ROIs far from the others (median signal {float(_nanmedian(signal)):+.2f} nm): "
                        + ", ".join(f"ROI {r} ({w}, z {z:.1f})" for r, w, z in sorted(odd, key=lambda x: -x[2])[:12]))
        if noisy_groups:
            text.append("noisier groups: " + ", ".join(f"{g} ({x:.1f}x)" for g, x in noisy_groups))
        if gradients:
            text.append("a gradient across the image: " + ", ".join(gradients) + " (focus, illumination or flow?)")
        found.append(Finding("outliers", WARN, "Some ROIs stand out", "; ".join(text) + ".",
                             {"rois": [r for r, _, _ in odd], "groups": [g for g, _ in noisy_groups], "gradients": gradients}))
    else:
        found.append(Finding("outliers", OK, "No outlier ROIs", f"No ROI's noise or signal has a robust z above {OUTLIER_Z}."))

    # 10. what the change is made of
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mb = np.nanmean(values[base][:, rows], axis=(0, 1))
        ms = np.nanmean(values[sig][:, rows], axis=(0, 1))
    if np.isfinite(mb).all() and np.isfinite(ms).all():
        spline = CubicSpline(wl, mb)
        basis = np.c_[-spline(wl, 1), mb, spline(wl, 2), np.ones_like(wl)]  # shift, amplitude, width, offset
        change = ms - mb
        total = float(np.sum(change**2)) or 1.0
        explained = {}
        for n_terms, name in ((1, "shift"), (2, "+ amplitude"), (3, "+ width")):  # nested: what each added term explains on top of the ones before
            cols = np.r_[0:n_terms, 3]  # always with the offset
            coef, *_ = np.linalg.lstsq(basis[:, cols], change, rcond=None)
            explained[name] = 1.0 - float(np.sum((change - basis[:, cols] @ coef) ** 2)) / total
        shift_only = explained["shift"]
        text = (f"a pure shift explains {100 * shift_only:.0f} % of it, with an amplitude change {100 * explained['+ amplitude']:.0f} %, "
                f"with a width change too {100 * explained['+ width']:.0f} %")
        level = WARN if 1.0 - shift_only > SHAPE_NON_SHIFT else OK
        found.append(Finding("shape", level, "What the change is made of", f"The change {names[0]} -> {names[1]} (ROI average): {text}. "
                             + ("Much of it is not a shift: peak, centroid and template methods will report different 'shifts'; the response shape uses all of it."
                                if level == WARN else "It is mostly a shift."), {"explained": explained}))
    return found


def _gradients(stored: StoredScan, roi_ids: Sequence[int], cubes: np.ndarray, quantities: Mapping[str, np.ndarray]) -> list[str]:
    """Linear gradients of each quantity over the ROI centres (the layout the scan used for `cubes`), as sentences; empty when none or no positions."""
    layout_ids = np.asarray(stored.layout_id)[cubes]
    if layout_ids.size == 0 or not stored.layouts or not (layout_ids >= 0).any():
        return []
    layout = stored.layouts.get(int(np.bincount(layout_ids[layout_ids >= 0]).argmax()))
    if not layout:
        return []
    try:
        centres = {int(r["roi_id"]): (float(r["center_x"]), float(r["center_y"])) for r in json.loads(layout)["rois"]}
    except (ValueError, KeyError, TypeError):
        return []
    xy = np.array([centres.get(int(r), (np.nan, np.nan)) for r in roi_ids])
    out = []
    for name, q in quantities.items():
        ok = np.isfinite(q) & np.isfinite(xy).all(1)
        if ok.sum() < 10:
            continue
        X = np.c_[np.ones(ok.sum()), xy[ok]]
        coef, *_ = np.linalg.lstsq(X, q[ok], rcond=None)
        res = q[ok] - X @ coef
        cov = np.linalg.pinv(X.T @ X) * float(res @ res) / (ok.sum() - 3)
        level = abs(float(np.median(q[ok]))) or 1.0
        for axis, label in ((1, "left to right"), (2, "top to bottom")):
            t = coef[axis] / np.sqrt(cov[axis, axis]) if cov[axis, axis] > 0 else 0.0
            change = coef[axis] * np.ptp(xy[ok, axis - 1]) / level
            if abs(t) > GRADIENT_T and abs(change) > GRADIENT_CHANGE:
                out.append(f"{name} {100 * change:+.0f} % {label}")
    return out


def run_diagnostics(
    stored: StoredScan,
    formula_key: str,
    traces: Mapping[int, np.ndarray],
    membership: StepMembership,
    *,
    pair: tuple[int, int] | None = None,
    template_traces: Mapping[int, np.ndarray] | None = None,
    groups: Sequence[tuple[str, Sequence[int]]] = (),
    method_is_shape: bool = False,
) -> DiagnosticsReport:
    """All checks (1-6, 8-10) in one call. ``pair`` None: the step checks only."""
    if not membership.steps:
        return DiagnosticsReport((Finding("steps", INFO, "No experiment steps", "The checks compare steps: load or add the experimental plan first."),), None)
    numbers = step_numbers(stored, formula_key, traces, membership)
    findings = [*check_noisy_steps(numbers, membership.steps), *check_noise_source(numbers), *check_drift(numbers), *check_shared(numbers)]
    if pair is not None:
        findings += check_pair(stored, formula_key, traces, membership, pair, template_traces, groups, method_is_shape)
    return DiagnosticsReport(tuple(findings), numbers)
