"""Pure-math half of the Histogram panel: binning and the "wand" auto-range.

No Qt, no window/module references - takes plain arrays and numbers, returns
plain arrays and numbers, so it is testable and reusable without a running
GUI (CLAUDE.md: "Don't mix scientific code with GUI code").
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks

DEFAULT_INTENSITY_MIN = 0.0
DEFAULT_INTENSITY_MAX = 65535.0
"""A 16-bit sensor's full range - the common case (CLAUDE.md: "x-axis is
more or less given by image depth, usually 16bit"). Passed as a parameter
everywhere below rather than hard-coded into the functions themselves, so a
future dtype-aware caller can supply a different depth without another
refactor - unlike the stable app, where this pair was a `MainWindow` class
constant reached into from several unrelated methods."""

DEFAULT_BIN_WIDTH = 512.0
"""Matches the stable app's default (`histogram_bins_spin`'s starting
value) - a validated starting point for a 16-bit sensor, not re-derived
here."""


def histogram_edges(
    bin_width: float,
    *,
    floor: float = DEFAULT_INTENSITY_MIN,
    ceiling: float = DEFAULT_INTENSITY_MAX,
) -> np.ndarray:
    """Bin edges `bin_width` apart, spanning the full `[floor, ceiling]`
    range - fixed regardless of what the current frame's own pixel values
    happen to span (maintainer's spec, 2026-09-29: "x-axis should be from
    min to max, using 0 as min and max of 16bit value as max"). Every curve
    is binned against the same edges either way, so this only changes how
    much empty axis is visible around the data, never a curve's shape.
    """
    if bin_width <= 0:
        raise ValueError(f"bin_width must be > 0, got {bin_width}")
    if ceiling <= floor:
        raise ValueError(f"ceiling must be > floor, got floor={floor}, ceiling={ceiling}")
    return np.arange(floor, ceiling + bin_width, bin_width)


def population_counts(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Raw per-bin counts for `values` against `edges` - non-finite values
    (NaN from a masked/invalid pixel) are dropped first, matching
    `histogram_edges`'s own finite-only convention."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    counts, _ = np.histogram(finite, bins=edges)
    return counts.astype(np.float64)


def excluded_pixel_text(image: np.ndarray) -> str | None:
    """"12 345 px no data, 10.4%" for the pixels of `image` without a value
    (NaN), or `None` when there are none. Those pixels are left out of every
    count, percentage and normalisation in this panel."""
    total = int(image.size)
    excluded = total - int(np.count_nonzero(np.isfinite(image)))
    if excluded == 0 or total == 0:
        return None
    return f"{excluded:,} px no data, {excluded / total * 100.0:.1f}%".replace(",", " ")


def as_percent(counts: np.ndarray, total_pixel_count: int) -> np.ndarray:
    """`counts` as a percentage of `total_pixel_count` (the "All pixels"
    population's own total, shared by every curve so they stay comparable to
    each other) - the panel's %-of-total Y-axis mode."""
    if total_pixel_count <= 0:
        return np.zeros_like(counts)
    return counts / float(total_pixel_count) * 100.0


def as_normalized(counts: np.ndarray) -> np.ndarray:
    """`counts` divided by its own peak, so the tallest bin reads as `1.0`
    (maintainer's spec, 2026-09-30: "normalization would be towards highest
    value"). Deliberately per-curve, not shared across curves the way
    `as_percent`'s `total_pixel_count` is - this mode exists to compare
    *shape* (where each population's peak sits, how wide it is) rather than
    relative population size, so each curve gets its own independent peak.
    An empty/all-zero curve (e.g. no ROI drawn yet) has no peak to divide
    by and stays at zero rather than dividing by zero."""
    peak = float(np.max(counts)) if counts.size else 0.0
    if peak <= 0.0:
        return np.zeros_like(counts)
    return counts / peak


def estimate_roi_intensity_range(
    values: np.ndarray,
    *,
    intensity_min: float = DEFAULT_INTENSITY_MIN,
    intensity_max: float = DEFAULT_INTENSITY_MAX,
    debris_ceiling_fraction: float = 0.23,
    bins: int = 512,
) -> tuple[float, float] | None:
    """Auto-locate the darker ("ROI") population's intensity band in a
    reference image's histogram, so the Highlight range - which
    semi-automatic ROI detection searches within - can be set automatically
    instead of the user dragging it by hand every time.

    Verbatim port of the stable app's `processing/roi_histogram.py` (same
    name, same algorithm, same docstring below) - this function is pure
    numpy/scipy with zero GUI coupling, so it needed no rework, only a new
    home. Assumes the same bimodal-histogram physics as the stable app's
    `bimodal_dip_contrast`, plus specifics confirmed to hold for LSPRi
    reference images on a 16-bit sensor (0..65535):

    - Below roughly `debris_ceiling_fraction` of the full range (~15000 on a
      16-bit sensor) is debris/dust, not a real ROI - it can form its own
      histogram peak and must be excluded even when prominent.
    - Real ROIs never go fully black: the true ROI peak sits clearly above
      that debris band (typically ~20000-50000, though this is not hard-coded
      since it shifts with exposure - only the debris floor is).
    - The background peak is always the taller of the two real peaks, because
      the background covers more of the frame than the ROIs do - this (not
      absolute position) is what identifies which peak is background.
    - The ROI peak sits at or below the background peak's intensity: ROIs
      absorb light, so they are never brighter than background. This is why
      detection always runs on the reference image, which has the strongest
      such contrast.

    Returns (lower, upper) bounds - the histogram valleys flanking the ROI
    peak - or None if no separable ROI peak is found above the debris band
    (e.g. a near-uniform or unimodal image), so the caller can fall back to
    asking the user to set the range by hand rather than guessing.
    """
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0 or intensity_max <= intensity_min:
        return None

    counts, edges = np.histogram(finite, bins=bins, range=(intensity_min, intensity_max))
    smoothed = gaussian_filter1d(counts.astype(np.float64), sigma=max(1.0, bins / 100.0))
    if smoothed.max() <= 0.0:
        return None
    centers = 0.5 * (edges[:-1] + edges[1:])

    # Zero-pad so a peak pinned to the very first/last bin can still register
    # (find_peaks requires both neighbors) - same trick as bimodal_dip_contrast.
    padded = np.concatenate(([0.0], smoothed, [0.0]))
    peak_indices, _ = find_peaks(padded, prominence=smoothed.max() * 0.01)
    if peak_indices.size == 0:
        return None
    peak_indices = peak_indices - 1

    debris_ceiling = intensity_min + debris_ceiling_fraction * (intensity_max - intensity_min)
    candidates = [int(i) for i in peak_indices if centers[i] > debris_ceiling]
    if len(candidates) < 2:
        return None

    candidates.sort(key=lambda i: smoothed[i], reverse=True)
    background_index = candidates[0]
    roi_candidates = [i for i in candidates[1:] if centers[i] < centers[background_index]]
    if not roi_candidates:
        return None
    roi_index = max(roi_candidates, key=lambda i: smoothed[i])

    lower_start = int(np.searchsorted(centers, debris_ceiling))
    if lower_start < roi_index:
        lower_index = lower_start + int(np.argmin(smoothed[lower_start : roi_index + 1]))
    else:
        lower_index = roi_index
    upper_index = roi_index + int(np.argmin(smoothed[roi_index : background_index + 1]))

    lower_bound = float(centers[lower_index])
    upper_bound = float(centers[upper_index])
    if upper_bound <= lower_bound:
        return None
    return lower_bound, upper_bound
