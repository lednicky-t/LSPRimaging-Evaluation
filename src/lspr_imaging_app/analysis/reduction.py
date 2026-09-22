"""Pure per-ROI pixel reduction math (sketch §10: ports `roi_math.py`).

**Moved here from `roi/reduction.py` on 2026-09-21** - maintainer decision:
turning masked pixel values into a scalar (mean/median/trimmed_mean/
plane_fit) is an analysis computation, not a ROI concern, so this whole
file (not just the not-yet-built `weighted_*` variants) belongs under
`analysis/`, not `roi/`. The ROI Toolbox owns geometry/masks (see
`roi/rasterize.py`, which stays in `roi/` - it turns a shape into a mask,
never reads image pixel values); this module owns turning pixels into
numbers. Nothing on `rewrite` imported `roi/reduction.py` yet at the time
of the move (checked: `roi/toolbox.py` never referenced it), so this was a
zero-fixup file move, not a rewire.

No Qt import allowed in this file (AGENTS.md testing rule). AGENTS.md
non-negotiable invariants: never pool pixels across ROIs before computing
sample/reference ratios; always average already-fitted per-ROI values,
never average raw spectra and fit once.

**Placeholder shape corrected 2026-09-20** (same family as the
`AreaRoiGroup.group_id: int` and guessed `preprocess_image()` bugs already
caught on this branch): the scaffold guessed single-array functions
(`mean(values)`, `plane_fit(values)`). The real
`lspr_imaging_app/processing/roi_math.py` on `develop`/`main` is built
around **sample+reference pairs**, not lone arrays -
`reduce_sample_and_reference_all_methods()` computes every REDUCTION_METHODS
entry from one already-extracted pixel pair in a single call, which is what
lets switching "Reduction method" in the GUI be instant instead of
re-reading pixels. `plane_fit` isn't a per-array function at all - it fits a
plane to the *reference* ROI's pixels and evaluates it at the *sample* ROI's
center, needing the reference region's pixel coordinates and the sample
center as extra arguments. Ported verbatim below (diffed after - only
`REDUCTION_METHODS`'s docstring/import block unchanged, no logic altered).

The ``weighted_*`` variants are §6a (fractional pixel weighting) -
deliberately deferred until the analysis stage itself gets built (see
AGENTS.md); left as `NotImplementedError` stubs here rather than
implemented now.
"""

from __future__ import annotations

import numpy as np

REDUCTION_METHODS: tuple[str, ...] = ("mean", "median", "trimmed_mean", "plane_fit")

# Not user-adjustable (no GUI control) - a continuously-variable Trim %
# would mean a cached "trimmed_mean" reduction is only valid for whatever
# fraction happened to be set when it was computed, breaking the "every
# Reduction method is instantly available once a cube's pixels are read"
# guarantee the other three methods get for free (see analysis/tasks.py's
# write-through reduction cache, once built).
DEFAULT_TRIMMED_MEAN_FRACTION: float = 0.10


def reduce_mean(pixels: np.ndarray) -> float:
    """Plain pixel average - the original, still-default behavior."""
    return float(np.mean(pixels))


def reduce_median(pixels: np.ndarray) -> float:
    """Median pixel value - robust to a single hot/dead pixel or cosmic-ray hit."""
    return float(np.median(pixels))


def reduce_trimmed_mean(pixels: np.ndarray, trim_fraction: float = 0.10) -> float:
    """Mean after dropping the top/bottom trim_fraction of values from each
    tail - a middle ground between reduce_mean and reduce_median. Falls back
    to reduce_mean if trimming would leave nothing (tiny array + large
    trim_fraction).

    Sort-and-slice, not scipy.stats.trim_mean: measured ~24x faster
    (~15us/call vs ~360us/call at a realistic ~200-800px ROI size) for
    bit-identical output (verified against trim_mean across 200 randomized
    (size, fraction) cases, max abs diff ~1e-14 - float64 rounding noise,
    not a real difference) - scipy's per-call overhead here turned out to
    dominate the actual (cheap) arithmetic. Found while measuring the cost
    of computing every Reduction method for every ROI during a bulk sweep
    (see apps/LSPRi/eva/docs/bulk_analysis_performance_investigation.md) -
    trimmed_mean, not plane_fit, was unexpectedly the dominant cost there.
    """
    pixels = np.asarray(pixels, dtype=np.float64).ravel()
    fraction = min(max(float(trim_fraction), 0.0), 0.45)
    if pixels.size == 0 or fraction <= 0.0:
        return reduce_mean(pixels)
    trimmed_each_side = int(pixels.size * fraction)
    if trimmed_each_side * 2 >= pixels.size:
        return reduce_mean(pixels)
    sorted_pixels = np.sort(pixels)
    return float(np.mean(sorted_pixels[trimmed_each_side : pixels.size - trimmed_each_side]))


def reduce_plane_fit_reference(
    reference_pixels: np.ndarray,
    reference_xx: np.ndarray,
    reference_yy: np.ndarray,
    sample_x: float,
    sample_y: float,
) -> float:
    """Fit a plane z = a*x + b*y + c to the reference ROI's pixels (least
    squares), then evaluate that plane at the sample ROI's center instead of
    using the reference ring's raw mean. Corrects for a spatial illumination
    gradient between the sample and reference apertures - useful when they
    sit in different rows/columns under uneven illumination.

    Falls back to reduce_mean(reference_pixels) when there aren't enough
    points to fit a plane (fewer than 4) or the reference points are
    coordinate-degenerate (e.g. exactly collinear, giving a singular design
    matrix) - a plane fit needs spread in both axes to be well-posed, and a
    degenerate reference region shouldn't crash the whole computation.
    """
    values = np.asarray(reference_pixels, dtype=np.float64).ravel()
    xx = np.asarray(reference_xx, dtype=np.float64).ravel()
    yy = np.asarray(reference_yy, dtype=np.float64).ravel()
    if values.size < 4 or xx.size != values.size or yy.size != values.size:
        return reduce_mean(values)
    design = np.column_stack([xx, yy, np.ones_like(xx)])
    try:
        coefficients, _residuals, rank, _singular_values = np.linalg.lstsq(design, values, rcond=None)
    except np.linalg.LinAlgError:
        return reduce_mean(values)
    if rank < 3:
        return reduce_mean(values)
    a, b, c = coefficients
    plane_value = float(a * float(sample_x) + b * float(sample_y) + c)
    if not np.isfinite(plane_value):
        return reduce_mean(values)
    return plane_value


def reduce_sample_and_reference(
    sample_pixels: np.ndarray,
    reference_pixels: np.ndarray,
    reduction_method: str,
    *,
    trimmed_mean_fraction: float = 0.10,
    reference_xx: np.ndarray | None = None,
    reference_yy: np.ndarray | None = None,
    sample_x: float | None = None,
    sample_y: float | None = None,
) -> tuple[float, float]:
    """Single entry point for turning a ROI pair's masked sample/reference
    pixel arrays into one value each, per the selected reduction method
    ("mean"/"median"/"trimmed_mean" apply symmetrically to both sides;
    "plane_fit" corrects the reference side using its spatial coordinates
    and evaluates at the sample's location, while the sample side still uses
    a plain mean).
    """
    method = str(reduction_method).strip().lower()
    if method == "plane_fit":
        if reference_xx is None or reference_yy is None or sample_x is None or sample_y is None:
            raise ValueError("plane_fit reduction requires reference_xx/reference_yy/sample_x/sample_y")
        sample_value = reduce_mean(sample_pixels)
        reference_value = reduce_plane_fit_reference(reference_pixels, reference_xx, reference_yy, sample_x, sample_y)
        return sample_value, reference_value
    if method == "median":
        return reduce_median(sample_pixels), reduce_median(reference_pixels)
    if method == "trimmed_mean":
        return (
            reduce_trimmed_mean(sample_pixels, trimmed_mean_fraction),
            reduce_trimmed_mean(reference_pixels, trimmed_mean_fraction),
        )
    return reduce_mean(sample_pixels), reduce_mean(reference_pixels)  # "mean" (default)


def reduce_sample_and_reference_all_methods(
    sample_pixels: np.ndarray,
    reference_pixels: np.ndarray,
    *,
    trimmed_mean_fraction: float = 0.10,
    reference_xx: np.ndarray | None = None,
    reference_yy: np.ndarray | None = None,
    sample_x: float | None = None,
    sample_y: float | None = None,
) -> dict[str, tuple[float, float]]:
    """Computes every REDUCTION_METHODS entry's (sample, reference) pair from
    the SAME already-extracted pixel arrays in one call, keyed by method
    name. The expensive part - reading the image, applying preprocessing,
    and extracting these pixel arrays via the ROI mask - already happened
    before this is called; getting every reduction method "for free" here
    (each computed via the exact same underlying reduce_mean/reduce_median/
    reduce_trimmed_mean/reduce_plane_fit_reference call that
    reduce_sample_and_reference itself dispatches to, so results are
    bit-identical to calling that function individually per method) is what
    lets switching Reduction be instant afterward instead of re-reading
    pixels.

    "trimmed_mean" reflects whatever `trimmed_mean_fraction` was passed here
    - a later Trim % change still needs a fresh call (with the pixel arrays,
    which callers generally don't retain).

    plane_fit needs reference_xx/reference_yy/sample_x/sample_y; when any are
    None (caller didn't extract pixel coordinates), plane_fit falls back to
    reduce_mean(reference_pixels) - the same fallback reduce_plane_fit_
    reference itself uses for a degenerate/insufficient fit, just decided
    here instead since there are no coordinates to attempt a fit with at all.
    """
    sample_mean_value = reduce_mean(sample_pixels)
    results: dict[str, tuple[float, float]] = {
        "mean": (sample_mean_value, reduce_mean(reference_pixels)),
        "median": (reduce_median(sample_pixels), reduce_median(reference_pixels)),
        "trimmed_mean": (
            reduce_trimmed_mean(sample_pixels, trimmed_mean_fraction),
            reduce_trimmed_mean(reference_pixels, trimmed_mean_fraction),
        ),
    }
    if reference_xx is not None and reference_yy is not None and sample_x is not None and sample_y is not None:
        plane_fit_reference_value = reduce_plane_fit_reference(reference_pixels, reference_xx, reference_yy, sample_x, sample_y)
    else:
        plane_fit_reference_value = reduce_mean(reference_pixels)
    results["plane_fit"] = (sample_mean_value, plane_fit_reference_value)
    return results


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting. Not yet implemented - scaffolding
    only."""
    raise NotImplementedError


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting - needs a genuine weighted-median
    algorithm, not a pass-through. Not yet implemented - scaffolding only."""
    raise NotImplementedError


def weighted_trimmed_mean(values: np.ndarray, weights: np.ndarray, trim_fraction: float) -> float:
    """§6a fractional pixel weighting - needs a genuine weighted variant, not
    a pass-through. Not yet implemented - scaffolding only."""
    raise NotImplementedError


def weighted_plane_fit(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting. Not yet implemented - scaffolding
    only."""
    raise NotImplementedError
