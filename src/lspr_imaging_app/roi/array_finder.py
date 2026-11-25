"""Array finder: fill in an array from the image, guided by 0, 1 or 2 ROIs (pure: numpy / scipy / cv2, no Qt).

Design and decisions: `docs/array_finder_design_2026-11-25.md`. Not a rewrite of `array_detection.py` (the old Detect stays as it is so the two can be
compared); it shares only three small helpers with it.

Two parts, because the first is slow and the second must be instant while the user moves a slider:

1. `find_candidates` (slow, off the GUI thread, cancellable): the contrast map, the template (the seed ROI's own picture, or a plain disk), the normalised
   cross-correlation with the image at a few sizes, and its peaks. Result: `Candidates`.
2. `build_array` (fast): the thresholds (**similarity**, **strength**, **position tolerance**, **size tolerance**) turn the candidates into an array: the
   lattice (`array_lattice.fit_lattice`), the members, the strays (similar but off the lattice), the missing nodes, the size of every spot. Result: `FinderResult`.

Space: pixel-index coordinates of the image passed in (the reference-wavelength image of the cube on screen, in processed space, like `find_array`). Lengths are
diameters or pitches in pixels. Nothing here reads a dataset or changes an image.

What the numbers mean
- *Similarity*: normalised cross-correlation of the spot with the template, -1 .. 1 (1 = the same picture). A spot of the same kind scores 0.8 - 0.95.
- *Strength*: the spot's contrast relative to the seed's (or, without a seed, to the typical good spot). Keeps out faint things that merely look alike.
- *Position tolerance*: how far from its node a spot may sit, as a fraction of the pitch.
- *Size tolerance*: how far a spot's measured diameter may be from the array's, as a fraction of it (only when the size is not fixed).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
from scipy import ndimage

from ..domain.roi_shapes import Border, Shape
from ..image_features import FeatureSearchError, fill_invalid, odd_size
from ..progress import Cancelled, ProgressCallback, StageProgress
from .array_detection import matched_filter, prepare_contrast, refine_nodes, scan_spot_radius
from .array_lattice import LatticeFit, fit_lattice, node_positions
from .edge_size import EdgeSizeParams, measure_spot_sizes

__all__ = [
    "Candidates",
    "Cancelled",
    "FinderError",
    "FinderResult",
    "FinderSeed",
    "FinderSettings",
    "build_array",
    "candidate_key",
    "find_candidates",
]

STAGE_WEIGHTS = {"size": 0.55, "contrast": 0.05, "match": 0.35, "peaks": 0.05}
SIMILARITY_FLOOR = 0.3
"""Peaks below this are never kept: the similarity slider can only go up from here (the slow part keeps every peak above it)."""
MAX_CANDIDATES = 6000
SIZE_STEP = 0.08
"""Spacing of the sizes tried when the size is free (8 %: a spot a few percent off still correlates at the nearest size)."""
MIN_DIAMETER_PX = 4.0


class FinderError(Exception):
    """A readable reason why the finder could not run (shown to the user as is)."""


@dataclass(frozen=True)
class FinderSeed:
    """One guide ROI: where it is and what it looks like (its centre in pixel-index coordinates)."""

    x: float
    y: float
    diameter_px: float
    """Nominal size (twice the reach for a rectangle or a pixel map)."""
    shape: Shape | None = None
    """The ROI's own shape: copies of it are made when the type is fixed."""
    border: Border | None = None


@dataclass(frozen=True)
class FinderSettings:
    similarity: float = 0.6
    strength: float = 0.35
    position_tol: float = 0.05
    size_tol: float = 0.25
    fix_size: bool = True
    """With a seed: every spot gets the seed's size (and shape, with ``fix_type``). Without: the size is measured (``size_mode``)."""
    fix_type: bool = True
    size_mode: str = "uniform"
    """Measured sizes: ``"uniform"`` (the smallest robust value, so no ROI reaches past a spot: as Detect does) or ``"individual"``."""
    seeds_are_corners: bool = True
    """Two seeds limit the array to the box they span."""
    place_missing: bool = False
    """Nodes with no spot: leave out (default) or put a ROI on the lattice position."""
    bright: bool = False
    use_mask: bool = True
    """Leave the user's masked pixels out of the matching (filled in from their surroundings), so a spot damaged where the mask is can still be found."""
    min_members: int = 4


def candidate_key(settings: FinderSettings, seeds: tuple[FinderSeed, ...]) -> tuple:
    """What `find_candidates` depends on: if it is unchanged, the cached `Candidates` are still right and only `build_array` has to run again."""
    fixed = bool(settings.fix_size and seeds)  # a fixed size tries one template size: the size tolerance is not read
    return (bool(settings.bright), bool(settings.use_mask), fixed, None if fixed else round(float(settings.size_tol), 3), tuple((round(s.x, 2), round(s.y, 2), round(s.diameter_px, 2)) for s in seeds))


@dataclass(frozen=True)
class Candidates:
    """The slow part's result: every peak of the correlation map, with what `build_array` needs to judge it."""

    points: np.ndarray
    """``(N, 2)`` x, y of the peaks (sub-pixel)."""
    similarity: np.ndarray
    strength: np.ndarray
    diameter: np.ndarray
    """``(N,)`` size (px) of the template scale that matched best: a rough value, the measured size replaces it."""
    diameter_px: float
    """The search size (the seeds' median, or the scanned one)."""
    contrast: np.ndarray = field(repr=False)
    valid: np.ndarray = field(repr=False)
    seeds: tuple[FinderSeed, ...] = ()
    shape: tuple[int, int] = (0, 0)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinderResult:
    ordered: bool
    """A lattice was found. ``False``: the similar spots are returned as free positions (``rows`` = ``cols`` = 0)."""
    rows: int
    cols: int
    origin: np.ndarray
    a1: np.ndarray
    a2: np.ndarray
    kind: str
    """``"rectangular"``, ``"triangular"``, ``"oblique"``, ``"line"`` or ``"free"``."""
    pitch_x_px: float
    pitch_y_px: float
    tilt_deg: float
    member_row: np.ndarray
    member_col: np.ndarray
    member_xy: np.ndarray
    """``(M, 2)`` the spots that are made (row-major; seeds included, ``is_seed``): measured centres, lattice positions for placed missing nodes."""
    member_diameter: np.ndarray
    member_similarity: np.ndarray
    is_seed: np.ndarray
    is_placed: np.ndarray
    """True where the node had no spot and the ROI was put on the lattice (``place_missing``)."""
    missing_xy: np.ndarray
    """``(K, 2)`` nodes of the array with no spot (drawn dashed; they become ROIs only with ``place_missing``)."""
    stray_xy: np.ndarray
    stray_diameter: np.ndarray
    stray_reason: tuple[str, ...]
    residual_rms_px: float
    funnel: dict[str, int]
    seeds: tuple[FinderSeed, ...]
    warnings: tuple[str, ...]
    report: str

    @property
    def count(self) -> int:
        return int(len(self.member_xy))

    @property
    def new_count(self) -> int:
        return int(self.count - self.is_seed.sum())


# -- part 1: candidates -------------------------------------------------------------------------


def find_candidates(
    image: np.ndarray,
    seeds: tuple[FinderSeed, ...],
    settings: FinderSettings,
    *,
    valid_mask: np.ndarray | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Candidates:
    """The correlation peaks of the template in ``image``. Raises `FinderError` (readable) or `Cancelled`.

    ``valid_mask`` (True = use; the user's mask inverted; only read when ``settings.use_mask`` and it has the image's shape): the masked pixels are treated like missing
    data when the picture is prepared, that is filled in smoothly from the pixels around them (`image_features.fill_invalid`), so dirt or damage the user has masked does
    not spoil the match. They are **not** treated as outside the image: a spot with masked pixels in it is still looked for, sized and placed."""
    step = StageProgress(STAGE_WEIGHTS, progress, cancelled)
    raw = np.asarray(image, dtype=np.float32)
    if raw.ndim != 2 or raw.size == 0:
        raise FinderError("The image is empty or not 2-D.")
    data_valid = np.isfinite(raw)  # what is really missing (the corners a rotation leaves): spots near it are not searched
    masked_px = 0
    if settings.use_mask and valid_mask is not None and valid_mask.shape == raw.shape and not valid_mask.all():
        masked_px = int((~valid_mask & data_valid).sum())
        raw = np.where(valid_mask, raw, np.float32(np.nan))
    step("size", 0.0, "Find: preparing the image...")
    flipped = raw
    if settings.bright:  # the contrast map wants dark spots: mirror the intensity range (as `find_array` does)
        finite_values = raw[np.isfinite(raw)]
        if finite_values.size:
            flipped = (float(finite_values.max()) + float(finite_values.min())) - raw
    try:
        filled, _finite = fill_invalid(flipped)
    except FeatureSearchError as error:
        raise FinderError(str(error)) from error
    valid = data_valid
    if not valid.any():
        raise FinderError("No valid pixels to search.")

    warnings: list[str] = []
    if masked_px:
        warnings.append(f"{masked_px} masked pixels were filled in from their surroundings before matching.")
    if seeds:
        diameters = np.array([s.diameter_px for s in seeds], dtype=np.float64)
        diameter = float(np.median(diameters))
        if (np.abs(diameters / diameter - 1.0) > 0.15).any():
            warnings.append("The selected ROIs differ in size by more than 15 %: the template is made of all of them.")
        step("size", 1.0, "Find: spot size from the selected ROI(s).")
    else:
        diagnostics: dict = {}
        radius = scan_spot_radius(filled, valid, step, diagnostics)
        if radius is None:
            raise FinderError(diagnostics.get("reason", "No spot size found."))
        diameter = 2.0 * float(radius)
    if diameter < MIN_DIAMETER_PX:
        raise FinderError(f"The spots are too small to search for (diameter {diameter:.1f} px).")
    radius = 0.5 * diameter

    step("contrast", 0.0, "Find: contrast map...")
    try:
        contrast, _finite = prepare_contrast(raw, radius, bright=settings.bright, smooth_sigma=0.7)  # (masked pixels are NaN in `raw`: filled there)
    except FeatureSearchError as error:
        raise FinderError(str(error)) from error
    step("contrast", 1.0, "Find: contrast map ready.")
    usable = ndimage.distance_transform_edt(valid) > 0.5 * radius if not valid.all() else np.ones(valid.shape, dtype=bool)

    refined_seeds = _refined(seeds, contrast, usable)
    template = _template(refined_seeds, contrast, radius)
    fixed = bool(settings.fix_size and seeds)
    scales = [1.0] if fixed or settings.size_tol <= 0 else list(np.linspace(1.0 - settings.size_tol, 1.0 + settings.size_tol, 1 + 2 * max(1, round(settings.size_tol / SIZE_STEP))))
    best = np.full(contrast.shape, -1.0, dtype=np.float32)
    best_scale = np.ones(contrast.shape, dtype=np.float32)
    for k, scale in enumerate(scales):
        step("match", k / len(scales), f"Find: comparing the spots with the template ({k + 1} of {len(scales)})...")
        correlation = _correlate(contrast, _scaled(template, scale))
        better = correlation > best
        best = np.where(better, correlation, best)
        best_scale = np.where(better, np.float32(scale), best_scale)
    step("match", 1.0, "Find: comparison done.")

    step("peaks", 0.0, "Find: looking for peaks...")
    points, similarity, scale_of = _peaks(best, best_scale, usable, diameter)
    core = ndimage.gaussian_filter(contrast, max(0.25 * radius, 0.7))
    strength = _sample(core, points)
    reference = _strength_reference(refined_seeds, core, strength, similarity)
    strength = strength / reference if reference > 0 else np.zeros_like(strength)
    step("peaks", 1.0, f"Find: {len(points)} peaks.")
    return Candidates(
        points=points, similarity=similarity, strength=strength, diameter=diameter * scale_of, diameter_px=diameter,
        contrast=contrast, valid=valid, seeds=refined_seeds, shape=raw.shape, warnings=tuple(warnings),
    )


def _refined(seeds: tuple[FinderSeed, ...], contrast: np.ndarray, usable: np.ndarray) -> tuple[FinderSeed, ...]:
    """Seeds whose centre is moved to the spot's own (matched filter, within half a diameter), so the template is centred on the spot and the found centres
    are the spots' centres, not the user's drawing error carried to every copy. A seed with no spot near it keeps its place. Only the *template* uses the
    moved centre; the ROI itself stays where it was drawn (`FinderResult.is_seed`)."""
    out = []
    for seed in seeds:
        radius = 0.5 * seed.diameter_px
        centre, score = refine_nodes(matched_filter(contrast, radius), np.array([[seed.x, seed.y]]), 0.5 * seed.diameter_px, usable)
        out.append(FinderSeed(float(centre[0, 0]), float(centre[0, 1]), seed.diameter_px, seed.shape, seed.border) if score[0] > 0 else seed)
    return tuple(out)


def _template(seeds: tuple[FinderSeed, ...], contrast: np.ndarray, radius: float) -> np.ndarray:
    """The picture to look for: the mean of the seeds' neighbourhoods (centred to a fraction of a pixel), or a soft disk when there is no seed."""
    half = int(math.ceil(1.25 * max(radius, *(0.5 * s.diameter_px for s in seeds)))) + 2 if seeds else int(math.ceil(1.25 * radius)) + 2
    side = 2 * half + 1
    if not seeds:
        yy, xx = np.mgrid[-half : half + 1, -half : half + 1]
        return (1.0 / (1.0 + np.exp((np.hypot(xx, yy) - radius) / max(0.08 * radius, 0.5)))).astype(np.float32)
    patches = [cv2.getRectSubPix(contrast, (side, side), (float(s.x), float(s.y))) for s in seeds]
    return np.mean(patches, axis=0).astype(np.float32)


def _scaled(template: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return template
    side = max(odd_size(template.shape[0] * scale), 7)
    return cv2.resize(template, (side, side), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)


def _correlate(contrast: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation at every pixel (the template centred on it); the image edge is extended so spots cut by it can still match."""
    pad_y, pad_x = template.shape[0] // 2, template.shape[1] // 2
    padded = cv2.copyMakeBorder(contrast, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_REPLICATE)
    result = cv2.matchTemplate(padded, template, cv2.TM_CCOEFF_NORMED)
    return np.clip(np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0), -1.0, 1.0).astype(np.float32)


def _peaks(best: np.ndarray, best_scale: np.ndarray, usable: np.ndarray, diameter: float):
    """Local maxima of the correlation map above the floor, sub-pixel by a parabola through each peak's neighbours. ``(points, similarity, scale)``."""
    footprint = max(odd_size(0.8 * diameter), 3)
    is_peak = (best == ndimage.maximum_filter(best, size=footprint)) & (best >= SIMILARITY_FLOOR) & usable
    ys, xs = np.nonzero(is_peak)
    if len(ys) > MAX_CANDIDATES:
        top = np.argsort(best[ys, xs])[::-1][:MAX_CANDIDATES]
        ys, xs = ys[top], xs[top]
    height, width = best.shape
    inside = (xs > 0) & (ys > 0) & (xs < width - 1) & (ys < height - 1)
    dx = np.zeros(len(xs))
    dy = np.zeros(len(xs))
    i = np.flatnonzero(inside)
    centre = best[ys[i], xs[i]].astype(np.float64)
    for offset, (axis_a, axis_b) in ((dx, (best[ys[i], xs[i] - 1], best[ys[i], xs[i] + 1])), (dy, (best[ys[i] - 1, xs[i]], best[ys[i] + 1, xs[i]]))):
        denominator = axis_a - 2.0 * centre + axis_b
        shift = np.where(np.abs(denominator) < 1e-9, 0.0, 0.5 * (axis_a - axis_b) / np.where(np.abs(denominator) < 1e-9, 1.0, denominator))
        offset[i] = np.clip(shift, -0.5, 0.5)
    points = np.column_stack([xs + dx, ys + dy]).astype(np.float64)
    return points, best[ys, xs].astype(np.float64), best_scale[ys, xs].astype(np.float64)


def _sample(plane: np.ndarray, points: np.ndarray) -> np.ndarray:
    if len(points) == 0:
        return np.empty(0)
    return ndimage.map_coordinates(plane, [points[:, 1], points[:, 0]], order=1, mode="nearest").astype(np.float64)


def _strength_reference(seeds: tuple[FinderSeed, ...], core: np.ndarray, strength: np.ndarray, similarity: np.ndarray) -> float:
    """What strength 1 means: the seeds' own contrast, else the typical one of the 20 best-looking spots."""
    if seeds:
        values = _sample(core, np.array([[s.x, s.y] for s in seeds]))
        return float(np.median(values))
    if len(strength) == 0:
        return 0.0
    best = np.argsort(similarity)[::-1][:20]
    return float(np.median(strength[best]))


# -- part 2: the array --------------------------------------------------------------------------


def build_array(candidates: Candidates, settings: FinderSettings) -> FinderResult:
    """The array the thresholds make of ``candidates``. Raises `FinderError` when there is nothing to build from (the reason is the message)."""
    seeds = candidates.seeds
    keep = (candidates.similarity >= settings.similarity) & (candidates.strength >= settings.strength)
    points = candidates.points[keep]
    similarity = candidates.similarity[keep]
    rough = candidates.diameter[keep]
    funnel: dict[str, int] = {"peaks": int(len(candidates.points)), "similar": int(keep.sum())}
    # the seeds are the user's own ROIs: they are always in the cloud, at their own place, and replace the peak next to them
    seed_xy = np.array([[s.x, s.y] for s in seeds], dtype=np.float64).reshape(-1, 2)
    if len(seed_xy) and len(points):
        near = np.min(np.hypot(points[:, None, 0] - seed_xy[None, :, 0], points[:, None, 1] - seed_xy[None, :, 1]), axis=1) < 0.4 * candidates.diameter_px
        points, similarity, rough = points[~near], similarity[~near], rough[~near]
    points = np.vstack([points, seed_xy])
    similarity = np.concatenate([similarity, np.ones(len(seed_xy))])
    rough = np.concatenate([rough, np.full(len(seed_xy), candidates.diameter_px)])
    is_seed_point = np.concatenate([np.zeros(len(points) - len(seed_xy), dtype=bool), np.ones(len(seed_xy), dtype=bool)])
    funnel["similar"] += int(len(seed_xy))
    if len(points) == len(seed_xy):
        raise FinderError("No spot is similar enough" + (" besides the selected ROI(s)" if len(seed_xy) else "") + ". Lower the similarity or the strength, or select a ROI on a spot of the array.")

    warnings = list(candidates.warnings)
    lattice = fit_lattice(points, similarity, tolerance=settings.position_tol, anchors=seed_xy if len(seed_xy) else None, min_members=max(2, settings.min_members))
    if lattice is None:
        return _free_set(candidates, settings, points, similarity, rough, is_seed_point, funnel, warnings)
    return _ordered(candidates, settings, lattice, points, similarity, rough, is_seed_point, funnel, warnings)


def _ordered(candidates, settings, lattice: LatticeFit, points, similarity, rough, is_seed_point, funnel, warnings) -> FinderResult:
    member = lattice.member.copy()
    column, row = lattice.column.copy(), lattice.row.copy()
    reasons = [""] * len(points)
    for index in np.flatnonzero(~member):
        reasons[index] = "off the lattice"
    seed_points = np.flatnonzero(is_seed_point)
    for index in seed_points:
        if not member[index]:
            warnings.append("A selected ROI is not on the lattice the other spots form (position tolerance).")
    funnel["on lattice"] = int(member.sum())

    # extent: the box the two seeds span (when they are corners), else the box of the members
    column_range = (int(column[member].min()), int(column[member].max()))
    row_range = (int(row[member].min()), int(row[member].max()))
    corner_seeds = [i for i in seed_points if member[i]]
    if settings.seeds_are_corners and len(seed_points) == 2 and len(corner_seeds) == 2:
        column_range = (int(min(column[corner_seeds])), int(max(column[corner_seeds])))
        row_range = (int(min(row[corner_seeds])), int(max(row[corner_seeds])))
        outside = member & ((column < column_range[0]) | (column > column_range[1]) | (row < row_range[0]) | (row > row_range[1]))
        for index in np.flatnonzero(outside):
            reasons[index] = "outside the corner ROIs"
        member &= ~outside
        funnel["inside the corner ROIs"] = int(member.sum())
    cols = column_range[1] - column_range[0] + 1
    rows = 1 if lattice.one_dimensional else row_range[1] - row_range[0] + 1
    origin = lattice.origin + column_range[0] * lattice.a1 + (0 if lattice.one_dimensional else row_range[0]) * lattice.a2
    column = column - column_range[0]
    row = row - (0 if lattice.one_dimensional else row_range[0])

    # sizes (and the size gate); a spot whose size is off the array's is a stray
    diameter, member, reasons = _sizes(candidates, settings, points, rough, member, reasons, lattice)
    funnel["right size"] = int(member.sum())

    # refit the lattice on what is left: the positions of the empty nodes follow the real spots
    fitted_origin, a1, a2 = _refit(points, column, row, member, origin, lattice)
    node_column, node_row = np.meshgrid(np.arange(cols), np.arange(rows))
    node_column, node_row = node_column.ravel(), node_row.ravel()
    taken = {(int(r), int(c)) for r, c in zip(row[member], column[member], strict=True)}
    empty = np.array([(int(r), int(c)) not in taken for r, c in zip(node_row, node_column, strict=True)], dtype=bool)
    empty_xy = node_positions(fitted_origin, a1, a2, node_column[empty], node_row[empty])
    height, width = candidates.shape
    on_image = (empty_xy[:, 0] >= 0) & (empty_xy[:, 1] >= 0) & (empty_xy[:, 0] <= width - 1) & (empty_xy[:, 1] <= height - 1)
    if on_image.any():
        on_image[on_image] = candidates.valid[np.round(empty_xy[on_image, 1]).astype(int), np.round(empty_xy[on_image, 0]).astype(int)]
    empty_xy, empty_row, empty_column = empty_xy[on_image], node_row[empty][on_image], node_column[empty][on_image]

    order = np.lexsort((column[member], row[member]))  # row-major
    m_index = np.flatnonzero(member)[order]
    xy = points[m_index].copy()
    sizes = diameter[m_index].copy()
    sim = similarity[m_index].copy()
    seed = is_seed_point[m_index].copy()
    m_row, m_col = row[m_index].copy(), column[m_index].copy()
    placed = np.zeros(len(m_index), dtype=bool)
    if settings.place_missing and len(empty_xy):
        reference = float(np.median(sizes)) if len(sizes) else candidates.diameter_px
        xy = np.vstack([xy, empty_xy])
        sizes = np.concatenate([sizes, np.full(len(empty_xy), reference)])
        sim = np.concatenate([sim, np.zeros(len(empty_xy))])
        seed = np.concatenate([seed, np.zeros(len(empty_xy), dtype=bool)])
        m_row, m_col = np.concatenate([m_row, empty_row]), np.concatenate([m_col, empty_column])
        placed = np.concatenate([placed, np.ones(len(empty_xy), dtype=bool)])
        again = np.lexsort((m_col, m_row))
        xy, sizes, sim, seed, m_row, m_col, placed = xy[again], sizes[again], sim[again], seed[again], m_row[again], m_col[again], placed[again]
        empty_xy = np.empty((0, 2))
    stray_index = np.flatnonzero(~member & ~is_seed_point)
    funnel["placed"] = int(len(xy))
    pitch_x, pitch_y = float(np.hypot(*a1)), float(np.hypot(*a2))
    found_nodes = np.flatnonzero(~placed)
    lattice_xy = node_positions(fitted_origin, a1, a2, m_col[found_nodes], m_row[found_nodes]) if len(found_nodes) else np.empty((0, 2))
    residual = float(np.sqrt(np.mean(np.sum((xy[found_nodes] - lattice_xy) ** 2, axis=1)))) if len(found_nodes) else math.nan
    kind = _kind(a1, a2, lattice.one_dimensional)
    if len(empty_xy) or placed.any():
        gaps = len(empty_xy) + int(placed.sum())
        warnings.append(f"{gaps} of {rows * cols} nodes have no spot" + (" (a ROI is placed on each, as asked)." if placed.any() else " (left out)."))
    result = FinderResult(
        ordered=True, rows=int(rows), cols=int(cols), origin=np.asarray(fitted_origin), a1=a1, a2=a2, kind=kind, pitch_x_px=pitch_x, pitch_y_px=pitch_y,
        tilt_deg=math.degrees(math.atan2(a1[1], a1[0])), member_row=m_row, member_col=m_col, member_xy=xy, member_diameter=sizes, member_similarity=sim,
        is_seed=seed, is_placed=placed, missing_xy=empty_xy, stray_xy=points[stray_index], stray_diameter=rough[stray_index],
        stray_reason=tuple(reasons[i] for i in stray_index), residual_rms_px=residual, funnel=funnel, seeds=candidates.seeds, warnings=tuple(warnings), report="",
    )
    return _with_report(result)


def _refit(points, column, row, member, origin, lattice: LatticeFit):
    """Origin and vectors by least squares on the members' own centres (the lattice positions the empty nodes get). Falls back to the first fit."""
    if member.sum() < 3:
        return origin, lattice.a1, lattice.a2
    design = np.column_stack([np.ones(int(member.sum())), column[member], row[member]]).astype(np.float64)
    if np.linalg.matrix_rank(design) < 3:
        return origin, lattice.a1, lattice.a2
    solution, *_ = np.linalg.lstsq(design, points[member], rcond=None)
    return solution[0], solution[1], solution[2]


def _kind(a1: np.ndarray, a2: np.ndarray, one_dimensional: bool) -> str:
    if one_dimensional:
        return "line"
    cosine = float(np.dot(a1, a2) / (np.hypot(*a1) * np.hypot(*a2)))
    angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    if abs(angle - 90.0) < 4.0:
        return "rectangular"
    if abs(angle - 60.0) < 4.0 and abs(np.hypot(*a1) - np.hypot(*a2)) < 0.08 * np.hypot(*a1):
        return "triangular"
    return "oblique"


def _sizes(candidates: Candidates, settings: FinderSettings, points, rough, member, reasons, lattice):
    """The diameter of every point (measured for the members) and the members that pass the size gate."""
    diameter = rough.copy()
    seeds = candidates.seeds
    if settings.fix_size and seeds:
        diameter[:] = float(np.median([s.diameter_px for s in seeds]))
        return diameter, member, reasons
    index = np.flatnonzero(member)
    if len(index) == 0:
        return diameter, member, reasons
    pitch = float(min(np.hypot(*lattice.a1), np.hypot(*lattice.a2)))
    edge = EdgeSizeParams(max_radius_px=0.5 * pitch)
    sizes = measure_spot_sizes(candidates.contrast, points[index], candidates.diameter_px, edge, candidates.valid)
    measured = np.array([s.diameter_px if s.ok else math.nan for s in sizes], dtype=np.float64)
    ok = np.isfinite(measured)
    if not ok.any():
        return diameter, member, reasons
    median = float(np.median(measured[ok]))
    off = ok & (np.abs(measured / median - 1.0) > settings.size_tol)  # the size gate
    member = member.copy()
    for k in np.flatnonzero(off):
        member[index[k]] = False
        reasons[index[k]] = "size off"
    ok &= ~off
    if settings.size_mode == "uniform" and ok.any():
        values = measured[ok]
        centre = float(np.median(values))
        sigma = 1.4826 * float(np.median(np.abs(values - centre)))
        diameter[:] = float(values[values >= centre - 3.0 * max(sigma, 0.25)].min())
    else:
        diameter[index[ok]] = measured[ok]
        diameter[index[~ok]] = median
    return diameter, member, reasons


def _free_set(candidates, settings, points, similarity, rough, is_seed_point, funnel, warnings) -> FinderResult:
    """No lattice: the similar spots as they are (free positions), sized like an array's."""
    funnel["on lattice"] = 0
    diameter = rough.copy()
    if settings.fix_size and candidates.seeds:
        diameter[:] = float(np.median([s.diameter_px for s in candidates.seeds]))
    order = np.lexsort((points[:, 0], np.round(points[:, 1] / max(candidates.diameter_px, 1.0))))
    funnel["placed"] = int(len(points))
    warnings.append("The spots do not form a lattice (position tolerance): they are offered as free positions.")
    zero = np.zeros(2)
    result = FinderResult(
        ordered=False, rows=0, cols=0, origin=zero, a1=zero, a2=zero, kind="free", pitch_x_px=math.nan, pitch_y_px=math.nan, tilt_deg=math.nan,
        member_row=np.zeros(len(points), dtype=np.int64)[order], member_col=np.arange(len(points))[order], member_xy=points[order], member_diameter=diameter[order],
        member_similarity=similarity[order], is_seed=is_seed_point[order], is_placed=np.zeros(len(points), dtype=bool), missing_xy=np.empty((0, 2)),
        stray_xy=np.empty((0, 2)), stray_diameter=np.empty(0), stray_reason=(), residual_rms_px=math.nan, funnel=funnel, seeds=candidates.seeds,
        warnings=tuple(warnings), report="",
    )
    return _with_report(result)


def _with_report(result: FinderResult) -> FinderResult:
    funnel = " -> ".join(f"{count} {name}" for name, count in result.funnel.items())
    if result.ordered:
        sizes = result.member_diameter
        text = (
            f"{result.rows} x {result.cols} {result.kind} array, pitch {result.pitch_x_px:.1f} / {result.pitch_y_px:.1f} px, tilt {result.tilt_deg:.2f} deg, "
            f"{result.count} spots (diameter {float(sizes.min()):.1f} - {float(sizes.max()):.1f} px, rms off the lattice {result.residual_rms_px:.2f} px). {funnel}."
        )
    else:
        text = f"No lattice: {result.count} similar spots as free positions. {funnel}."
    return replace(result, report=text)
