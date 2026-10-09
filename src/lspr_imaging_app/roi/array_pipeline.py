"""One entry point per Array action: detect, refine, place (pure: numpy / scipy / cv2, no Qt).

Three actions, all returning an `ArrayResult` that the ROI toolbox can store:

- `detect_array`: **Auto** (nothing given) or **Semi** (some of diameter / rows / cols / pitch given): find
  the array in the image, size the sample disks and the reference rings. The priors are checked against the
  result (warnings); the search itself is not narrowed by them yet.
- `refine_array`: take existing ROIs (an array the user has placed and labelled), move each to the best
  position near where it is, and re-measure the sizes.
- `place_array`: **Manual**: stamp a lattice from the typed numbers. No image needed; with an image and
  ``snap_to_image`` each node is moved to the nearest spot.

Space: pixel coordinates of the image passed in. The caller passes the reference-wavelength image of the
current cube in processed space (rotated / cropped / background-removed as the user has it). Lengths are
diameters (or pitches) in pixels, floats. Nothing here reads a dataset or changes an image.

Sizes
- Sample diameter: measured by `edge_size` (the chosen model). ``size_mode="uniform"`` gives every ROI the
  **smallest** measured value (after dropping spots more than 3 robust sigmas below the median, so one
  debris-affected spot does not shrink the whole array); ``"individual"`` gives each ROI its own.
- Reference ring: `ring_size`. Uniform: the robust **maximum** inner diameter (clear of every disk).
  Overlap rule: rings may overlap each other, a ring never counts sample-disk pixels (the analysis removes
  them), sample disks are masked by the user's mask only.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from ..image_features import FeatureSearchError
from ..progress import Cancelled, ProgressCallback, StageProgress
from .array_detection import ArrayPrior, find_array, lattice_nodes, prepare_contrast, refine_centers
from .edge_size import EdgeSizeParams, SpotSize, measure_spot_sizes
from .ring_size import RingParams, SpotRing, measure_rings, neighbour_warnings, outer_from_inner, ring_from_inner, summarize_rings

__all__ = [
    "ArrayError",
    "ArrayResult",
    "ArraySettings",
    "Cancelled",
    "ROTATE_SUGGESTION_DEG",
    "SIZE_MODES",
    "detect_array",
    "place_array",
    "refine_array",
]

SIZE_MODES: tuple[str, ...] = ("uniform", "individual")
ROTATE_SUGGESTION_DEG = 1.0
"""Above this tilt the user is asked whether to rotate the image."""

STAGE_WEIGHTS = {"find": 0.55, "prepare": 0.10, "sample": 0.15, "ring": 0.20}


@dataclass(frozen=True)
class ArraySettings:
    prior: ArrayPrior = field(default_factory=ArrayPrior)
    bright: bool = False
    """Spots brighter than the background (default: darker, as in the instrument)."""
    edge: EdgeSizeParams = field(default_factory=EdgeSizeParams)
    size_mode: str = "uniform"
    ring: RingParams = field(default_factory=RingParams)
    ring_size_mode: str = "uniform"
    # manual placement
    anchor_xy: tuple[float, float] = (0.0, 0.0)
    """Centre of the first spot (row 0, column 0)."""
    rotation_deg: float = 0.0
    snap_to_image: bool = False


@dataclass(frozen=True)
class ArrayResult:
    rows: int
    cols: int
    pitch_x_px: float
    pitch_y_px: float
    tilt_deg: float
    anchor_xy: tuple[float, float]
    centers_xy: np.ndarray
    """(N, 2), row-major for a detected / placed array; the input order for a refinement."""
    found: np.ndarray
    """(N,) bool: a real spot was located (False = the lattice position / the unchanged input)."""
    sample_diameters_px: np.ndarray
    ring_inner_px: np.ndarray
    ring_outer_px: np.ndarray
    warnings: tuple[str, ...] = ()
    rotate_suggestion_deg: float | None = None
    """The tilt to remove, when it is above `ROTATE_SUGGESTION_DEG` (the caller asks the user)."""
    report: str = ""

    @property
    def count(self) -> int:
        return len(self.centers_xy)


# -- detect (auto / semi) --------------------------------------------------------------


def detect_array(
    image: np.ndarray,
    settings: ArraySettings,
    *,
    valid_mask: np.ndarray | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ArrayResult:
    """Find and size the array. Raises `ArrayError` (readable) if none is found, `Cancelled` on cancel."""
    _check_settings(settings)
    step = StageProgress(STAGE_WEIGHTS, progress, cancelled)
    diagnostics: dict = {}
    fit = find_array(
        image,
        valid_mask=valid_mask,
        prior=settings.prior,
        bright=settings.bright,
        progress=lambda fraction, text: step("find", fraction, text),
        cancelled=cancelled,
        diagnostics=diagnostics,
    )
    if fit is None:
        raise ArrayError(diagnostics.get("reason", "No array found."))
    step("prepare", 0.0, "Array: preparing the image for sizing...")
    contrast, valid = _contrast(image, fit.rough_diameter_px, valid_mask, settings)
    step("prepare", 1.0, "Array: measuring the spots...")
    found = fit.found
    pitch_limit = 0.5 * min(fit.pitch_x_px, fit.pitch_y_px)
    sample, ring_inner, ring_outer, sizing_warnings = _size_all(
        contrast, valid, fit.centers_xy, found, fit.rough_diameter_px, pitch_limit, settings, step
    )
    warnings = list(fit.warnings) + sizing_warnings
    if (~found).any():
        warnings.append(f"{int((~found).sum())} of {fit.count} spots could not be located; they stay at their lattice positions.")
    suggestion = fit.tilt_deg if abs(fit.tilt_deg) > ROTATE_SUGGESTION_DEG else None
    report = (
        f"{fit.rows} x {fit.cols} array, pitch x {fit.pitch_x_px:.1f} px / y {fit.pitch_y_px:.1f} px, tilt {fit.tilt_deg:.2f} deg, "
        f"{fit.found_count} of {fit.count} spots located (lattice residual {fit.residual_rms_px:.2f} px rms); "
        f"sample {_range(sample)} px, ring inner {_range(ring_inner)} / outer {_range(ring_outer)} px."
    )
    return ArrayResult(
        rows=fit.rows,
        cols=fit.cols,
        pitch_x_px=fit.pitch_x_px,
        pitch_y_px=fit.pitch_y_px,
        tilt_deg=fit.tilt_deg,
        anchor_xy=fit.origin_xy,
        centers_xy=fit.centers_xy,
        found=found,
        sample_diameters_px=sample,
        ring_inner_px=ring_inner,
        ring_outer_px=ring_outer,
        warnings=tuple(warnings),
        rotate_suggestion_deg=suggestion,
        report=report,
    )


# -- refine an existing set of ROIs ----------------------------------------------------


def refine_array(
    image: np.ndarray,
    centers_xy: np.ndarray,
    sample_diameters_px: np.ndarray,
    settings: ArraySettings,
    *,
    valid_mask: np.ndarray | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ArrayResult:
    """Re-fit positions and sizes of ROIs that are already placed (their current sizes set the search scale).

    Each ROI may move by at most 0.35 x the distance to its nearest neighbouring ROI (0.7 sample diameters if
    it has none). A ROI with no signal in that window keeps its position (``found = False``) and its size."""
    _check_settings(settings)
    centers = np.asarray(centers_xy, dtype=np.float64).reshape(-1, 2)
    sample_in = np.broadcast_to(np.asarray(sample_diameters_px, dtype=np.float64), (len(centers),)).copy()
    if len(centers) == 0:
        raise ArrayError("Nothing to refine: no ROIs selected.")
    step = StageProgress(STAGE_WEIGHTS, progress, cancelled)
    rough = float(np.median(sample_in))
    step("find", 0.0, "Array: preparing the image...")
    contrast, valid = _contrast(image, rough, valid_mask, settings)
    nearest = _nearest_neighbour_distance(centers)
    window = 0.35 * nearest if math.isfinite(nearest) else 0.7 * rough
    step("find", 0.5, "Array: moving the ROIs to their spots...")
    moved, scores = refine_centers(contrast, centers, rough / 2.0, window, valid)
    reference = float(np.median(scores[scores > 0])) if (scores > 0).any() else 0.0
    found = scores >= 0.4 * reference if reference > 0 else np.zeros(len(centers), dtype=bool)
    new_centers = np.where(found[:, None], moved, centers)
    step("find", 1.0, "Array: positions refined.")
    pitch_limit = 0.5 * nearest if math.isfinite(nearest) else 1.8 * rough
    sample, ring_inner, ring_outer, sizing_warnings = _size_all(
        contrast, valid, new_centers, found, rough, pitch_limit, settings, step, fallback_sample=sample_in
    )
    shift = np.hypot(*(new_centers - centers).T)
    warnings = sizing_warnings
    if (~found).any():
        warnings.append(f"{int((~found).sum())} of {len(centers)} ROIs found no spot near them and were left in place.")
    report = (
        f"Refined {int(found.sum())} of {len(centers)} ROIs (median move {float(np.median(shift[found])) if found.any() else 0.0:.2f} px, "
        f"max {float(shift.max()):.2f} px); sample {_range(sample)} px, ring inner {_range(ring_inner)} / outer {_range(ring_outer)} px."
    )
    return ArrayResult(
        rows=0,
        cols=0,
        pitch_x_px=math.nan,
        pitch_y_px=math.nan,
        tilt_deg=math.nan,
        anchor_xy=(float(new_centers[0, 0]), float(new_centers[0, 1])),
        centers_xy=new_centers,
        found=found,
        sample_diameters_px=sample,
        ring_inner_px=ring_inner,
        ring_outer_px=ring_outer,
        warnings=tuple(warnings),
        report=report,
    )


# -- manual placement ------------------------------------------------------------------


def place_array(
    settings: ArraySettings,
    *,
    rows: int,
    cols: int,
    pitch_x_px: float,
    pitch_y_px: float,
    image: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ArrayResult:
    """Stamp a ``rows x cols`` lattice at ``settings.anchor_xy`` (rotated by ``settings.rotation_deg``) with the
    sample diameter ``settings.prior.diameter_px`` and a ring from the ring settings (no image needed: the
    ring uses the ratio rule even if the measured one is selected). With an image and ``snap_to_image``
    every node is moved to its nearest spot and the sizes are measured."""
    _check_settings(settings)
    diameter = settings.prior.diameter_px
    if not diameter or diameter <= 0:
        raise ArrayError("Manual placement needs the disk diameter.")
    if rows < 1 or cols < 1:
        raise ArrayError("Rows and columns must be at least 1.")
    if pitch_x_px <= 0 or pitch_y_px <= 0:
        raise ArrayError("The pitch must be positive.")
    angle = math.radians(settings.rotation_deg)
    a1 = (pitch_x_px * math.cos(angle), pitch_x_px * math.sin(angle))
    a2 = (-pitch_y_px * math.sin(angle), pitch_y_px * math.cos(angle))
    nodes = lattice_nodes(rows, cols, settings.anchor_xy, a1, a2)
    warnings: list[str] = []
    if min(pitch_x_px, pitch_y_px) <= diameter:
        warnings.append("The pitch is not larger than the disk diameter: the disks touch or overlap.")
    count = len(nodes)
    found = np.zeros(count, dtype=bool)
    centers = nodes
    sample = np.full(count, float(diameter))
    ring_params = settings.ring if settings.ring.inner_mode == "ratio" else _as_ratio(settings.ring)
    inner_outer = [ring_from_inner(ring_params.inner_ratio * diameter, diameter, ring_params)] * count
    ring_inner = np.array([i for i, _o in inner_outer], dtype=np.float64)
    ring_outer = np.array([o for _i, o in inner_outer], dtype=np.float64)
    if settings.snap_to_image and image is not None:
        step = StageProgress(STAGE_WEIGHTS, progress, cancelled)
        contrast, valid = _contrast(image, float(diameter), valid_mask, settings)
        window = 0.35 * min(pitch_x_px, pitch_y_px)
        moved, scores = refine_centers(contrast, nodes, diameter / 2.0, window, valid)
        reference = float(np.median(scores[scores > 0])) if (scores > 0).any() else 0.0
        found = scores >= 0.4 * reference if reference > 0 else found
        centers = np.where(found[:, None], moved, nodes)
        sample, ring_inner, ring_outer, sizing_warnings = _size_all(
            contrast, valid, centers, found, float(diameter), 0.5 * min(pitch_x_px, pitch_y_px), settings, step,
            fallback_sample=sample,
        )
        warnings += sizing_warnings
        if (~found).any():
            warnings.append(f"{int((~found).sum())} of {count} nodes found no spot and stay at their typed positions.")
    report = f"Placed a {rows} x {cols} array, pitch x {pitch_x_px:.1f} px / y {pitch_y_px:.1f} px, rotation {settings.rotation_deg:.2f} deg."
    return ArrayResult(
        rows=int(rows),
        cols=int(cols),
        pitch_x_px=float(pitch_x_px),
        pitch_y_px=float(pitch_y_px),
        tilt_deg=float(settings.rotation_deg),
        anchor_xy=(float(settings.anchor_xy[0]), float(settings.anchor_xy[1])),
        centers_xy=np.asarray(centers, dtype=np.float64),
        found=found,
        sample_diameters_px=sample,
        ring_inner_px=ring_inner,
        ring_outer_px=ring_outer,
        warnings=tuple(warnings),
        report=report,
    )


# -- shared steps ----------------------------------------------------------------------


class ArrayError(Exception):
    """A readable reason why an Array action could not run (shown to the user as is)."""


def _check_settings(settings: ArraySettings) -> None:
    if settings.size_mode not in SIZE_MODES:
        raise ValueError(f"size_mode must be one of {SIZE_MODES}, got {settings.size_mode!r}")
    if settings.ring_size_mode not in SIZE_MODES:
        raise ValueError(f"ring_size_mode must be one of {SIZE_MODES}, got {settings.ring_size_mode!r}")


def _contrast(image: np.ndarray, rough_diameter_px: float, valid_mask: np.ndarray | None, settings: ArraySettings):
    try:
        return prepare_contrast(image, rough_diameter_px / 2.0, valid_mask=valid_mask, bright=settings.bright, smooth_sigma=0.7)
    except FeatureSearchError as error:
        raise ArrayError(str(error)) from error


def _as_ratio(ring: RingParams) -> RingParams:
    from dataclasses import replace

    return replace(ring, inner_mode="ratio")


def _nearest_neighbour_distance(centers: np.ndarray) -> float:
    if len(centers) < 2:
        return math.inf
    distances, _ = cKDTree(centers).query(centers, k=2)
    return float(np.median(distances[:, 1]))


def _robust_min(values: np.ndarray) -> float:
    median = float(np.median(values))
    sigma = 1.4826 * float(np.median(np.abs(values - median)))
    return float(values[values >= median - 3.0 * max(sigma, 0.25)].min())


def _size_all(
    contrast: np.ndarray,
    valid: np.ndarray,
    centers: np.ndarray,
    located: np.ndarray,
    rough_diameter: float,
    pitch_limit: float,
    settings: ArraySettings,
    step: StageProgress,
    fallback_sample: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """(sample diameters, ring inner, ring outer, warnings) for every spot; unlocated spots take the array value."""
    count = len(centers)
    warnings: list[str] = []
    edge = settings.edge if settings.edge.max_radius_px is not None else _with_limit(settings.edge, pitch_limit)
    step("sample", 0.0, "Array: measuring the sample disks...")
    indices = np.flatnonzero(located)
    sizes: list[SpotSize] = measure_spot_sizes(contrast, centers[indices], rough_diameter, edge, valid) if len(indices) else []
    measured = np.full(count, math.nan)
    for index, size in zip(indices, sizes, strict=True):
        if size.ok:
            measured[index] = size.diameter_px
    ok = np.isfinite(measured)
    if not ok.any():
        raise ArrayError("The spots could not be measured (no edge found). Check the image, the edge model and the contrast.")
    if (~ok & located).any():
        warnings.append(f"{int((~ok & located).sum())} spots could not be measured and take the array value.")
    if settings.size_mode == "uniform":
        value = _robust_min(measured[ok])
        sample = np.full(count, value)
    else:
        array_value = float(np.median(measured[ok]))
        base = fallback_sample if fallback_sample is not None else np.full(count, array_value)
        sample = np.where(ok, measured, np.where(np.isfinite(base), base, array_value))
    step("sample", 1.0, "Array: measuring the reference rings...")

    ring_params = settings.ring
    if ring_params.max_radius_px is None:
        from dataclasses import replace

        ring_params = replace(ring_params, max_radius_px=pitch_limit)
    ring_indices = np.flatnonzero(ok)
    rings: list[SpotRing] = measure_rings(contrast, centers[ring_indices], sample[ring_indices], ring_params, valid)
    inner = np.full(count, math.nan)
    outer = np.full(count, math.nan)
    for index, ring in zip(ring_indices, rings, strict=True):
        if ring.ok:
            inner[index], outer[index] = ring.inner_diameter_px, ring.outer_diameter_px
    ring_ok = np.isfinite(inner)
    if not ring_ok.any():
        raise ArrayError("The reference rings could not be measured (the spots do not fade into the background).")
    if settings.ring_size_mode == "uniform":
        summary = summarize_rings([r for r in rings if r.ok])
        uniform_inner = summary["inner_robust"]
        inner = np.full(count, uniform_inner)
        outer = np.array([outer_from_inner(uniform_inner, d, ring_params) for d in sample])
    else:
        typical_inner = float(np.median(inner[ring_ok]))
        for index in np.flatnonzero(~ring_ok):
            inner[index], outer[index] = ring_from_inner(typical_inner, sample[index], ring_params)
    step("ring", 1.0, "Array: done.")
    final_rings = [SpotRing(float(i), float(o), math.nan) for i, o in zip(inner, outer, strict=True)]
    warnings += neighbour_warnings(centers, sample, final_rings)
    return sample, inner, outer, warnings


def _with_limit(edge: EdgeSizeParams, limit: float) -> EdgeSizeParams:
    from dataclasses import replace

    return replace(edge, max_radius_px=limit)


def _range(values: np.ndarray) -> str:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return "-"
    low, high = float(finite.min()), float(finite.max())
    return f"{low:.1f}" if abs(high - low) < 0.05 else f"{low:.1f} - {high:.1f}"
