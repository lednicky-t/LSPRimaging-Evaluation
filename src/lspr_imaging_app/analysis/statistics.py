"""Pillar II - post-processing on an already-computed metric trace
(`docs/analysis_pipeline_layers.md`).

Ports `processing/trace_statistics.py` verbatim and adds the one thing the
old app left spread across the GUI: `apply_statistics`, which applies the
documented order in one place instead of at each call site.

**What this layer is for, in the maintainer's own words**: showing 170 raw
sensorgram traces at once doesn't mean anything to look at. Statistics
exists to make many ROIs' output legible - *and only that*.

**The boundary is strict.** This reads layer-3 trace values - one number
per ROI per spectral cube - and nothing else. It never reads a spectrum
(layer 2) and never reads pixels (layer 1). It changes how Pillar I's
output is *displayed*; it never changes how Pillar I computes anything, and
nothing here can invalidate a stored cell.

**Averaging happens here, on already-fitted values, never on spectra.**
That is the correction `analysis_pipeline_layers.md` records under "The one
gap this closes": the old app used to average each ROI's absorbance per
wavelength and fit the metric once on the averaged spectrum. For a
nonlinear fit, "average then fit" and "fit then average" are not the same
number.

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import savgol_filter

from .settings import StatisticsSettings


def _clamp_odd_window(window: int, n: int) -> int:
    """Largest odd window <= max(n, 1) and >= 1, never bigger than the data
    itself - degenerate parameter combinations are clamped, not raised,
    the same style as `query.fit_polynomial`'s order clamp."""
    limit = max(int(n), 1)
    window = min(max(int(window), 1), limit)
    if window % 2 == 0:
        window -= 1
    return max(window, 1)


def _fill_nans_by_interpolation(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Linearly interpolate interior NaN gaps (by position, since sensorgram
    points are roughly evenly spaced) so filters that don't understand NaN
    (`savgol_filter`, `np.convolve`) have something to operate on.
    Leading/trailing NaNs are filled by nearest-value extrapolation rather
    than left as NaN, since the filters need a full array; callers restore
    the original NaN positions afterwards so gaps aren't invented in the
    output. Returns (filled, finite_mask).

    A NaN here is the normal "this cell has not been analyzed yet" case,
    not an error - the store legitimately returns `None` for cells nobody
    has computed, and the trace keeps its length so the x-axis stays
    aligned."""
    values = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(values)
    if np.all(finite) or not np.any(finite):
        return values.copy(), finite
    idx = np.arange(values.size, dtype=np.float64)
    filled = values.copy()
    filled[~finite] = np.interp(idx[~finite], idx[finite], values[finite])
    return filled, finite


def smooth_savgol(values: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """Savitzky-Golay smoothing - preserves peak shape better than a plain
    moving average, the standard choice for spectroscopy and sensorgram
    time traces."""
    values = np.asarray(values, dtype=np.float64)
    if values.size < 3:
        return values.copy()
    filled, finite = _fill_nans_by_interpolation(values)
    if not np.any(finite):
        return values.copy()
    window_length = _clamp_odd_window(window, values.size)
    order = min(max(int(polyorder), 0), window_length - 1)
    smoothed = savgol_filter(filled, window_length=window_length, polyorder=order, mode="interp")
    result = smoothed.astype(np.float64, copy=True)
    result[~finite] = np.nan
    return result


def smooth_moving_average(values: np.ndarray, window: int) -> np.ndarray:
    """Simple box filter. Edge-corrected: divides by the number of samples
    each output point actually saw, not the nominal window size, so points
    near the ends aren't biased toward zero the way a naive zero-padded
    convolution would be."""
    values = np.asarray(values, dtype=np.float64)
    n = values.size
    if n == 0:
        return values.copy()
    filled, finite = _fill_nans_by_interpolation(values)
    if not np.any(finite):
        return values.copy()
    window_size = min(max(int(window), 1), n)
    kernel = np.ones(window_size, dtype=np.float64)
    counts = np.convolve(np.ones(n, dtype=np.float64), kernel, mode="same")
    smoothed = np.convolve(filled, kernel, mode="same") / counts
    result = smoothed.astype(np.float64, copy=True)
    result[~finite] = np.nan
    return result


def reject_spikes_hampel(values: np.ndarray, window: int = 5, threshold: float = 3.5) -> np.ndarray:
    """Classic Hampel filter: a point more than `threshold` scaled-MADs
    (median absolute deviations, x1.4826 for consistency with a Gaussian
    SD) from its rolling median is replaced by that median. Targets
    single-frame transients - a bubble, a focus glitch - without smearing
    real kinetic trends the way an unconditional smoother would. The point
    count and x-alignment stay intact; outliers are replaced, not
    removed."""
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return values.copy()
    filled, finite = _fill_nans_by_interpolation(values)
    if not np.any(finite):
        return values.copy()
    window_size = _clamp_odd_window(window, values.size)
    rolling_median = median_filter(filled, size=window_size, mode="nearest")
    deviation = np.abs(filled - rolling_median)
    scaled_mad = median_filter(deviation, size=window_size, mode="nearest") * 1.4826
    # A lone spike in an otherwise-flat window can leave the *local* MAD at
    # exactly 0 (only 1 of `window` points deviates, so the median of
    # deviations is 0 too) - flooring the comparison denominator instead of
    # gating on "MAD > 0" means that point's clearly nonzero deviation still
    # gets flagged, while a genuinely flat window (deviation == 0 everywhere)
    # still compares 0 > threshold*floor and correctly stays untouched.
    mad_floor = 1e-9
    is_outlier = finite & (deviation > float(threshold) * np.maximum(scaled_mad, mad_floor))
    result = values.copy()
    result[is_outlier] = rolling_median[is_outlier]
    return result


def reject_spikes_running_median(values: np.ndarray, window: int = 5) -> np.ndarray:
    """Unconditional running median - every finite point is replaced by its
    window's median. Simpler and more aggressive than Hampel's "only replace
    real outliers" rule; the plain "smooth over anything unusual"
    alternative."""
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return values.copy()
    filled, finite = _fill_nans_by_interpolation(values)
    if not np.any(finite):
        return values.copy()
    window_size = _clamp_odd_window(window, values.size)
    filtered = median_filter(filled, size=window_size, mode="nearest")
    result = values.copy()
    result[finite] = filtered[finite]
    return result


def normalize_to_baseline_window(
    x_values: np.ndarray,
    y_values: np.ndarray,
    window_start: float | None,
    window_end: float | None,
) -> tuple[np.ndarray, float]:
    """Subtract the mean y within [window_start, window_end] (in the trace's
    own x units) from the whole trace, so it reads as relative shift from
    that reference window instead of an absolute value - the standard way to
    compare sensorgrams across ROIs and experiments with different absolute
    baselines.

    Returns (corrected_y, baseline_value). `baseline_value` is NaN and y is
    returned unchanged when no window is set or no points fall inside it: a
    bad or empty window is a common user error that needs to be visible, not
    fatal."""
    y = np.asarray(y_values, dtype=np.float64)
    if window_start is None or window_end is None:
        return y.copy(), float("nan")
    x = np.asarray(x_values, dtype=np.float64)
    lo, hi = (window_start, window_end) if window_start <= window_end else (window_end, window_start)
    mask = np.isfinite(x) & np.isfinite(y) & (x >= float(lo)) & (x <= float(hi))
    if not np.any(mask):
        return y.copy(), float("nan")
    baseline = float(np.mean(y[mask]))
    return y - baseline, baseline


def apply_statistics(
    x_values: np.ndarray,
    y_values: np.ndarray,
    settings: StatisticsSettings,
) -> tuple[np.ndarray, float]:
    """One ROI's trace through the whole of Pillar II, in the fixed order.

    **Spike rejection, then smoothing, then baseline** - the order is not a
    preference. Spikes go first so a transient outlier is removed before a
    smoother can blend it into its neighbours (smoothing first would spread
    one bad frame across a whole window, and the spike filter would then no
    longer see an outlier to reject). Baseline is last because it is a plain
    offset: applying it earlier would give the same answer, and applying it
    last keeps the returned baseline value meaningful against the trace
    actually plotted.

    New in the rewrite. The stable app applies the same three steps in the
    same order, but does it inline in the sensorgram controller, so the
    ordering rule lives in a comment rather than in one callable function.

    Returns (trace, baseline_value); the baseline is NaN when correction is
    off or the window selected nothing."""
    y = np.asarray(y_values, dtype=np.float64)

    if settings.spike_rejection_enabled:
        method = str(settings.spike_rejection_method).strip().lower()
        if method == "running_median":
            y = reject_spikes_running_median(y, window=settings.spike_rejection_window)
        else:
            y = reject_spikes_hampel(
                y, window=settings.spike_rejection_window, threshold=settings.spike_rejection_threshold
            )

    method = str(settings.smoothing_method).strip().lower()
    if method == "savgol":
        y = smooth_savgol(y, settings.smoothing_window, settings.smoothing_polyorder)
    elif method == "moving_average":
        y = smooth_moving_average(y, settings.smoothing_window)

    if settings.baseline_enabled:
        return normalize_to_baseline_window(
            x_values, y, settings.baseline_window_start, settings.baseline_window_end
        )
    return y, float("nan")


def aggregate_traces(
    member_traces: dict[int, np.ndarray],
    center: str = "mean",
    band: str = "sd",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Combine several ROIs' already-computed traces (one array per member,
    all pre-aligned to the same x axis) into a centre trace plus a
    symmetric band.

    Never touches raw pixels or spectra: each member's trace has already
    been through layers 1-3 independently. This is the "average the fitted
    values, not the spectra" rule made concrete.

    NaN-aware per time point, so one member missing a frame doesn't blank
    out that whole time point for the group. Returns
    (center, band_low, band_high); all empty if `member_traces` is empty."""
    if not member_traces:
        empty = np.asarray([], dtype=np.float64)
        return empty, empty, empty
    stacked = np.vstack([np.asarray(trace, dtype=np.float64) for trace in member_traces.values()])
    with np.errstate(invalid="ignore"):
        if str(center).strip().lower() == "median":
            center_trace = np.nanmedian(stacked, axis=0)
        else:
            center_trace = np.nanmean(stacked, axis=0)
        spread = np.nanstd(stacked, axis=0)
        if str(band).strip().lower() == "sem":
            n_valid = np.sum(np.isfinite(stacked), axis=0).astype(np.float64)
            # SEM needs the unbiased *sample* std (ddof=1), not the
            # population std used for the "sd" band above - ddof=0 would
            # understate the band (e.g. ~29% too narrow at n=2). Only
            # defined for n_valid >= 2; fewer valid members -> NaN.
            with np.errstate(invalid="ignore"):
                sample_spread = np.nanstd(stacked, axis=0, ddof=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                spread = np.where(n_valid > 1, sample_spread / np.sqrt(n_valid), np.nan)
    return center_trace, center_trace - spread, center_trace + spread
