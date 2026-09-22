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

**The ``weighted_*`` variants are built (2026-09-22)** - §6a (fractional
pixel weighting), real implementations now, not `NotImplementedError`
stubs. Each has its own docstring explaining its specific design choice
and the empirical verification behind it (matching the unweighted
counterpart exactly, or to float noise, in the degenerate equal-weights
case - AGENTS.md's testing rule) - `weighted_plane_fit`'s signature was
also corrected in the process (the scaffold's `(values, weights)`
placeholder couldn't have actually fit a plane; real callers need pixel
coordinates too). Consumed by `roi/rasterize.py`'s `rasterize_fractional`
(built earlier this rewrite) producing the weight arrays these functions
take - not yet wired into `analysis/tasks.py`'s `compute_cell`, which
still uses the binary `rasterize_sample`/`rasterize_reference` (see that
file's own module docstring for why - deliberate, not an oversight).
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
    """§6a fractional pixel weighting - the standard weighted average
    (`sum(values * weights) / sum(weights)`). Falls back to `reduce_mean`
    if every weight is zero (e.g. a coverage mask with no overlap at all -
    matches `reduce_plane_fit_reference`'s "shouldn't crash the whole
    computation" convention for other degenerate cases in this file).
    Reduces exactly to `reduce_mean` when every weight is equal (any
    positive constant, not just 1.0) - a weighted average with uniform
    weights is definitionally the arithmetic mean."""
    values = np.asarray(values, dtype=np.float64).ravel()
    weights = np.asarray(weights, dtype=np.float64).ravel()
    total_weight = float(np.sum(weights))
    if total_weight <= 0.0:
        return reduce_mean(values)
    return float(np.sum(values * weights) / total_weight)


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """§6a fractional pixel weighting - a genuine weighted median via linear
    interpolation on the weighted cumulative distribution (`cum_weight[i] =
    sum(weights[:i+1]) - 0.5 * weights[i]`, normalized, then `np.interp` to
    the 0.5 crossing) - not `values[weights.argmax()]` or any other
    pass-through. **Verified (2026-09-22, not just derived by hand) to
    match `reduce_median` (`np.median`) to within float noise (~1e-13) over
    2000 random trials when every weight is equal**, including matching
    `np.median`'s "average the two middle values" convention for
    even-length arrays - a naive weighted-median formula (e.g. "smallest
    value where cumulative weight >= half the total") does *not* have this
    property for even n, which would have silently broken the AGENTS.md
    degenerate-case parity rule.

    Falls back to `reduce_mean` if every weight is zero, same as
    `weighted_mean`."""
    values = np.asarray(values, dtype=np.float64).ravel()
    weights = np.asarray(weights, dtype=np.float64).ravel()
    total_weight = float(np.sum(weights))
    if total_weight <= 0.0:
        return reduce_mean(values)
    sorter = np.argsort(values)
    sorted_values, sorted_weights = values[sorter], weights[sorter]
    weighted_positions = (np.cumsum(sorted_weights) - 0.5 * sorted_weights) / total_weight
    return float(np.interp(0.5, weighted_positions, sorted_values))


def weighted_trimmed_mean(values: np.ndarray, weights: np.ndarray, trim_fraction: float) -> float:
    """§6a fractional pixel weighting. **Trims by element count, then takes
    the weighted mean of what remains - not a weight-based trim** (a
    deliberate design choice, not the "obvious" generalization): trimming
    `trim_fraction` of the total *weight* from each tail, rather than
    `trim_fraction` of the element *count*, would only coincidentally
    reduce to `reduce_trimmed_mean`'s exact count-based slicing when every
    weight happens to be equal - element-count trimming reduces to it
    *by construction* (same slice indices; a weighted mean over elements
    that all share one weight is definitionally the arithmetic mean of
    those elements), which is what makes the parity guarantee below
    provable rather than approximate. **Verified 2026-09-22**: exact
    (0.0 max diff, not just within tolerance) against `reduce_trimmed_mean`
    over 3000 random trials at equal weights across several trim
    fractions; a non-uniform-weight sanity check (down-weighting two large
    outlier values to 1% while trimming) confirms the weighting genuinely
    changes the result, not a no-op.

    Falls back to a plain `weighted_mean` (not `reduce_mean`) when trimming
    would leave nothing or `trim_fraction<=0` - unlike the unweighted
    version's fallback, this preserves the weighting information rather
    than discarding it."""
    values = np.asarray(values, dtype=np.float64).ravel()
    weights = np.asarray(weights, dtype=np.float64).ravel()
    fraction = min(max(float(trim_fraction), 0.0), 0.45)
    if values.size == 0 or fraction <= 0.0:
        return weighted_mean(values, weights)
    trimmed_each_side = int(values.size * fraction)
    if trimmed_each_side * 2 >= values.size:
        return weighted_mean(values, weights)
    sorter = np.argsort(values)
    sorted_values, sorted_weights = values[sorter], weights[sorter]
    middle_values = sorted_values[trimmed_each_side: values.size - trimmed_each_side]
    middle_weights = sorted_weights[trimmed_each_side: values.size - trimmed_each_side]
    return weighted_mean(middle_values, middle_weights)


def weighted_plane_fit(
    reference_pixels: np.ndarray,
    reference_xx: np.ndarray,
    reference_yy: np.ndarray,
    weights: np.ndarray,
    sample_x: float,
    sample_y: float,
) -> float:
    """§6a fractional pixel weighting, for `reduce_plane_fit_reference`.
    **Signature corrected from the original scaffold's `(values, weights)`
    placeholder** (same family as this codebase's other guessed-placeholder
    corrections, e.g. `AreaRoiGroup.group_id: int`): a plane fit needs
    pixel coordinates and the sample-side evaluation point, not just values
    and weights - the scaffold's shape couldn't have actually fit a plane
    at all.

    Weighted least squares via the standard sqrt(weight)-scaling trick
    (scale every design-matrix row and target value by `sqrt(weight)`
    before the same `np.linalg.lstsq` call `reduce_plane_fit_reference`
    already uses) rather than a different algorithm - scaling both sides of
    a least-squares problem by the same per-row constant doesn't change
    its minimizer when every weight is equal, which is exactly what makes
    the parity guarantee below hold, and keeps this implementation close
    to the one it's a generalization of. **Verified 2026-09-22**: exact
    (0.0 max diff) against `reduce_plane_fit_reference` over 1000 random
    trials at equal weights, matches its fallback behavior for the
    degenerate <4-point case, and a down-weighted-outlier sanity check
    (one badly-off pixel weighted to 0.1%) pulls the fitted value back
    much closer to the true underlying plane than the unweighted fit does.

    Falls back to `weighted_mean(reference_pixels, weights)` for the same
    degenerate cases `reduce_plane_fit_reference` falls back to
    `reduce_mean` for (fewer than 4 points, coordinate-degenerate fit,
    non-finite result) - preserves weighting in the fallback rather than
    discarding it, same reasoning as `weighted_trimmed_mean`."""
    values = np.asarray(reference_pixels, dtype=np.float64).ravel()
    xx = np.asarray(reference_xx, dtype=np.float64).ravel()
    yy = np.asarray(reference_yy, dtype=np.float64).ravel()
    w = np.asarray(weights, dtype=np.float64).ravel()
    if values.size < 4 or xx.size != values.size or yy.size != values.size or w.size != values.size:
        return weighted_mean(values, w if w.size == values.size else np.ones_like(values))
    sqrt_weights = np.sqrt(np.clip(w, 0.0, None))
    design = np.column_stack([xx, yy, np.ones_like(xx)]) * sqrt_weights[:, None]
    target = values * sqrt_weights
    try:
        coefficients, _residuals, rank, _singular_values = np.linalg.lstsq(design, target, rcond=None)
    except np.linalg.LinAlgError:
        return weighted_mean(values, w)
    if rank < 3:
        return weighted_mean(values, w)
    a, b, c = coefficients
    plane_value = float(a * float(sample_x) + b * float(sample_y) + c)
    if not np.isfinite(plane_value):
        return weighted_mean(values, w)
    return plane_value
