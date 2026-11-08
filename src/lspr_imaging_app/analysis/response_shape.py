"""The response-shape readout (2026-11-06). Pure numpy / scipy, no Qt.

**What it measures.** A shift readout (template, fitted peak) asks "how far did the peak move?". This asks "how much of the expected change is in this
spectrum?", where the expected change is *measured*: the mean spectrum of a signal cube range minus the mean of a baseline cube range. Each spectrum is
projected on that change, each wavelength weighted by how much it responds and how noisy it is (a matched filter, ``w = change / noise variance``).
For a bulk refractive-index step it uses the amplitude and width change as well as the shift, so it is quieter than any pure-shift estimator (record
``docs/snr_methods_comparison_2026-11-06.md``: SNR per ROI 125-147 against 101-130 for the template shift on five step pairs).

**Units: shift-equivalent nm.** The weights are scaled so that a pure 1 nm shift of the baseline spectrum reads exactly 1 nm. A change that also
carries an amplitude / width change reads more than its shift (the 0-70 % ethylene glycol step: 8.7 nm against 7.1 nm for the template). The value
plotted is the baseline spectrum's peak (``template_shift.template_peak_nm``) + that shift-equivalent, like the template shift.

**Per group.** The change, the noise and the baseline spectrum are averaged over the ROIs of the ROI's group (union of its groups when it is in more than
one; every ROI when it is in none) and the weights come from those averages. A ROI is in its own group's average; measured effect on its score < 1 %.
Consequence: the values of a ROI depend on the other ROIs of its group, so moving or re-masking one ROI changes its group-mates slightly. Cheap (no fit,
milliseconds for every ROI), so it is always computed for all ROIs of the scan at once.

**Rules.** Every spectrum is first centred (its own mean over wavelength removed), so an offset never reads as a response. A cube needs a value at every
wavelength (else it is a gap: a partial spectrum would have a different mean). A ROI needs >= 2 baseline cubes and >= 1 signal cube to take part in the
calibration; a ROI's values need its own baseline mean (its reference point), so a ROI without baseline cubes has no values.
Caveat: the readout counts any change shaped like the calibration change, not only a shift; a binding response may be shaped differently from a bulk step.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import CubicSpline

from .template_shift import template_peak_nm

MIN_POINTS = 4  # wavelengths needed for the derivative of the baseline spectrum (cubic spline)


class ResponseError(ValueError):
    """The calibration cannot be made; the message says why, in words for the user."""


@dataclass(frozen=True)
class ResponseCalibration:
    """Per ROI (rows in the order of `roi_ids`): the weights ``[roi, wavelength]`` (applied to a centred spectrum minus `reference`), the ROI's own centred
    baseline spectrum `reference` and its baseline peak `peak_nm`. A row of NaN = no values for that ROI."""

    roi_ids: tuple[int, ...]
    wavelengths_nm: np.ndarray
    weights: np.ndarray
    reference: np.ndarray
    peak_nm: np.ndarray


def _centred(values: np.ndarray) -> np.ndarray:
    """``values`` ``[..., wavelength]`` minus each spectrum's own mean; a spectrum with any NaN becomes all NaN."""
    complete = np.isfinite(values).all(axis=-1, keepdims=True)
    with np.errstate(invalid="ignore"):
        out = values - values.mean(axis=-1, keepdims=True)
    return np.where(complete, out, np.nan)


def pool_rows(roi_ids: Sequence[int], groups: Sequence[Sequence[int]]) -> list[np.ndarray]:
    """Per ROI, the rows (indices into `roi_ids`) whose calibration it shares: the union of the groups that hold it, or every ROI when it is in none."""
    row_of = {int(r): i for i, r in enumerate(roi_ids)}
    member_rows = [np.array(sorted({row_of[int(m)] for m in g if int(m) in row_of}), dtype=np.int64) for g in groups]
    everyone = np.arange(len(roi_ids), dtype=np.int64)
    out = []
    for i in range(len(roi_ids)):
        mine = [rows for rows in member_rows if i in set(rows.tolist())]
        out.append(np.unique(np.concatenate(mine)) if mine else everyone)
    return out


def calibrate(
    roi_ids: Sequence[int],
    wavelengths_nm: np.ndarray,
    values: np.ndarray,
    base_cubes: np.ndarray,
    signal_cubes: np.ndarray,
    groups: Sequence[Sequence[int]] = (),
) -> ResponseCalibration:
    """The calibration. `values`: ``[cube, roi, wavelength]`` (formula values, NaN = no value), wavelengths ascending; `base_cubes` / `signal_cubes`:
    boolean per cube. Raises :class:`ResponseError` when no ROI can be calibrated."""
    wl = np.asarray(wavelengths_nm, dtype=np.float64)
    if wl.ndim != 1 or wl.size < MIN_POINTS or np.any(np.diff(wl) <= 0):
        raise ResponseError(f"The response shape needs at least {MIN_POINTS} scanned wavelengths in ascending order.")
    base, signal = np.asarray(base_cubes, dtype=bool), np.asarray(signal_cubes, dtype=bool)
    if np.any(base & signal):
        raise ResponseError("The baseline and signal cubes overlap: choose two separate ranges.")
    centred = _centred(np.asarray(values, dtype=np.float64))  # [cube, roi, wl]
    n_roi = centred.shape[1]
    have = np.isfinite(centred[..., 0])  # [cube, roi]: a complete spectrum
    n_base, n_signal = (have & base[:, None]).sum(0), (have & signal[:, None]).sum(0)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices are expected (ROIs without cubes)
        complete = np.where(np.isfinite(centred).all(-1, keepdims=True), np.asarray(values, dtype=np.float64), np.nan)
        ref = np.nanmean(centred[base], axis=0)  # [roi, wl]
        change = np.nanmean(centred[signal], axis=0) - ref
        var = np.nanvar(centred[base], axis=0, ddof=1)
        raw_base = np.nanmean(complete[base], axis=0)  # the same cubes as `ref`, not centred: for the peak
    usable = (n_base >= 2) & (n_signal >= 1) & np.isfinite(change).all(1) & np.isfinite(var).all(1) & (var > 0).all(1)
    if not usable.any():
        raise ResponseError("No ROI has at least two complete baseline cubes and one complete signal cube: check the two cube ranges.")
    weights = np.full((n_roi, wl.size), np.nan)
    peak = np.full(n_roi, np.nan)
    for i, rows in enumerate(pool_rows(roi_ids, groups)):
        if n_base[i] < 1:
            continue  # no own reference point
        rows = rows[usable[rows]]
        if rows.size == 0:
            continue
        mean_ref = ref[rows].mean(0)
        unit_shift = -CubicSpline(wl, mean_ref)(wl, 1)  # change of the (centred) spectrum for a +1 nm shift
        unit_shift -= unit_shift.mean()
        w = change[rows].mean(0) / var[rows].mean(0)
        gain = float(w @ unit_shift)
        if not np.isfinite(gain) or abs(gain) < 1e-12 * float(np.abs(w).sum() * np.abs(unit_shift).sum() + 1e-300):
            continue  # the change has nothing of a shift in it: no nm scale
        weights[i] = w / gain
        peak[i] = template_peak_nm(wl, raw_base[i])
    if not np.isfinite(peak).any():
        raise ResponseError("The change between the two cube ranges cannot be scaled to nm (no shift in it): check the two cube ranges.")
    return ResponseCalibration(tuple(int(r) for r in roi_ids), wl, weights, ref, peak)


def readout(calibration: ResponseCalibration, values: np.ndarray) -> np.ndarray:
    """The readout of every ROI at every cube: ``float64 [roi, cube]`` in nm (baseline peak + shift-equivalent); NaN for an incomplete spectrum or an
    uncalibrated ROI. `values`: ``[cube, roi, wavelength]``, the same ROIs and wavelengths as the calibration."""
    centred = _centred(np.asarray(values, dtype=np.float64))
    delta = centred - calibration.reference[None]
    shift = np.einsum("crw,rw->rc", delta, calibration.weights)
    return shift + calibration.peak_nm[:, None]
