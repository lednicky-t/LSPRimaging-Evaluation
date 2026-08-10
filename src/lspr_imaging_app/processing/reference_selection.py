from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks


def bimodal_dip_contrast(image: np.ndarray, *, bins: int = 256) -> float:
    """Fractional intensity drop from an image's background peak to its darkest peak.

    LSPR transmission images are bimodal: a bright background population plus
    a darker population from absorbing spots. This returns the background-to-spot
    drop as a fraction of the background level (0..1), which is invariant to the
    absolute illumination brightness -- unlike a raw min/max or percentile range,
    it isn't fooled by a wavelength simply being brighter overall. Higher means
    darker spots relative to the background, i.e. stronger absorbance. Returns
    0.0 if no bimodal structure is found (e.g. a blank or near-uniform frame).
    """
    finite = image[np.isfinite(image)]
    if finite.size == 0:
        return 0.0
    lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return 0.0
    histogram, edges = np.histogram(finite, bins=bins, range=(lo, hi))
    smoothed = gaussian_filter1d(histogram.astype(np.float64), sigma=max(1.0, bins / 100.0))
    # find_peaks requires both neighbors, so a population pinned to the very
    # first/last bin (e.g. sensor-clipped spot values) would never register as
    # a peak; pad with a zero count on each side so edge bins can still win.
    padded = np.concatenate(([0.0], smoothed, [0.0]))
    peak_indices, _ = find_peaks(padded, prominence=smoothed.max() * 0.01)
    if peak_indices.size == 0:
        return 0.0
    peak_indices = peak_indices - 1
    centers = 0.5 * (edges[:-1] + edges[1:])
    peak_values = centers[peak_indices]
    peak_heights = smoothed[peak_indices]
    background_value = float(peak_values[np.argmax(peak_heights)])
    darkest_value = float(peak_values.min())
    if background_value <= 0:
        return 0.0
    return (background_value - darkest_value) / background_value
