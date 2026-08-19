from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import savgol_filter

SMOOTHING_METHODS: tuple[str, ...] = ("none", "savgol", "moving_average")
SPIKE_REJECTION_METHODS: tuple[str, ...] = ("hampel", "running_median")


def _clamp_odd_window(window: int, n: int) -> int:
    """Largest odd window <= max(n, 1) and >= 1, never bigger than the data
    itself - degenerate parameter combinations are clamped, not raised, same
    style as fit_absorbance_curve's effective_order clamp."""
    limit = max(int(n), 1)
    window = min(max(int(window), 1), limit)
    if window % 2 == 0:
        window -= 1
    return max(window, 1)


def _fill_nans_by_interpolation(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Linearly interpolate interior NaN gaps (by position, since sensorgram
    points are roughly evenly spaced) so filters that don't understand NaN
    (savgol_filter, np.convolve) have something to operate on. Leading/
    trailing NaNs are filled by nearest-value extrapolation (np.interp's
    default) rather than left as NaN, since the filters need a full array;
    callers restore the original NaN positions afterward so gaps aren't
    invented in the output. Returns (filled, finite_mask)."""
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
    moving average, standard choice for spectroscopy/sensorgram time traces.
    NaN gaps are interpolated before filtering and restored afterward so a
    handful of missing frames doesn't blank out a window-sized neighborhood."""
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
    """Simple box-filter smoothing. NaN-aware (interpolates gaps first) and
    edge-corrected (divides by the actual number of samples each output
    point saw, not the nominal window size, so points near the ends aren't
    biased toward zero the way a naive zero-padded convolution would be)."""
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
    (median absolute deviations, x1.4826 to be consistent with a Gaussian
    SD) from its rolling median is replaced by that median. Targets
    single-frame transients (bubbles, focus glitches) without smearing real
    kinetic trends the way an unconditional smoother would. Returns a new
    array - the point count/x-alignment stays intact, only outlier values
    are replaced, not removed."""
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
    """Unconditional running-median filter - every finite point is replaced
    by its window's median. Simpler and more aggressive than the Hampel
    filter's "only replace real outliers" rule; offered as the plain
    "just smooth over anything unusual" alternative."""
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
    """Subtracts the mean y value within [window_start, window_end] (in the
    trace's own x-axis units) from the whole trace, so it reads as relative
    shift from that reference window instead of an absolute value - the
    standard way to compare sensorgrams across ROIs/experiments with
    different absolute baselines. Returns (corrected_y, baseline_value);
    baseline_value is NaN (and y is returned unchanged) if no window is set
    or no points fall inside it - a bad/empty window is a common user-error
    mode this needs to surface, not crash on."""
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


def aggregate_group_traces(
    member_traces: dict[int, np.ndarray],
    center: str = "mean",
    band: str = "sd",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Combine multiple ROI pairs' already-computed traces (one array per
    member, all pre-aligned to the same x/time axis) into a center trace plus
    a symmetric band - never touches raw pixels, each member's trace is
    computed independently through the normal ROI's-math/Metric-trace
    pipeline first. NaN-aware per time point (np.nanmean/nanmedian/nanstd),
    so one member missing a frame doesn't blank out that whole time point for
    the group. SEM = SD / sqrt(n_valid), with n_valid computed per time point
    since members can have different numbers of valid (non-NaN) frames at
    different points. Returns (center_trace, band_low, band_high); all empty
    arrays if member_traces is empty.
    """
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
            with np.errstate(divide="ignore", invalid="ignore"):
                spread = np.where(n_valid > 0, spread / np.sqrt(np.maximum(n_valid, 1.0)), np.nan)
    band_low = center_trace - spread
    band_high = center_trace + spread
    return center_trace, band_low, band_high
