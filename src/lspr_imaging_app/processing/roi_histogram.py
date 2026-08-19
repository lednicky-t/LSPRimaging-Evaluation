from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks


def estimate_roi_intensity_range(
    values: np.ndarray,
    *,
    intensity_min: float = 0.0,
    intensity_max: float = 65535.0,
    debris_ceiling_fraction: float = 0.23,
    bins: int = 512,
) -> tuple[float, float] | None:
    """Auto-locate the darker ("ROI") population's intensity band in a
    reference image's histogram, so the histogram highlight range - which
    detect_rois (roi_detection.py) searches within - can be set automatically
    instead of the user dragging it by hand every time.

    This assumes the same bimodal-histogram physics as bimodal_dip_contrast
    in reference_selection.py, plus a few more specifics the maintainer
    confirmed hold for LSPRi reference images on a 16-bit sensor (0..65535):
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
        # No second, separable peak above the debris band: either the image
        # is unimodal (no visible ROI/background split) or the only other
        # population found is debris itself. Either way there's nothing
        # reliable to auto-set here - don't guess.
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
