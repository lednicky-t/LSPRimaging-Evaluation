"""Find a regular array of round spots in an image (pure: numpy / scipy / cv2, no Qt).

Replaces the old pair `processing/roi_array_geometry.estimate_array_geometry` (global
min-max normalisation, square pitch only, axis-aligned only, row / column counts from 1-D
clustering) + `roi/detection._fit_grid_array` (integer pitch, per-pixel Python loop; 47 s on
a 1300 x 900 image with 170 spots, `tools/array_lab.py`, 2026-10-08).

Space: pixel coordinates of the image passed in (pixel centres at integers). The caller
passes the **reference-wavelength image of the current cube** in processed space (rotated /
cropped / background-removed as the user has it) and stores the result in the reference frame.
All lengths are diameters or pitches in pixels, floats (resolution 0.1 px or better).

Method
1. Contrast map (`image_features.contrast_map`): spots become positive whatever the
   illumination; `bright=True` flips the sign for bright spots.
2. Spot size prior (`ArrayPrior.diameter_px` or the scale-space estimate) then candidate
   spots: Laplacian-of-Gaussian peaks at that size, on a reduced copy (positions only need
   to be good to a pixel or two here).
3. Lattice: the two shortest strong neighbour vectors give the lattice vectors a1 (mostly
   along x) and a2 (mostly along y); every candidate is given integer indices (i, j); origin
   and both vectors are then fitted by least squares on the inliers (repeated with the
   updated lattice). Pitch x, pitch y and the tilt of the rows come from this fit, so a
   tilted array or different x / y pitches are handled, and one pass gives rows, columns and
   the residual of every spot.
4. Centres: a matched filter (disk minus surrounding ring) computed once for the whole image,
   then a sub-pixel peak near each lattice node. A node with no signal keeps its lattice
   position and is reported ``found = False`` (the old code called this ``inferred``).

Sizing the spots is a separate step (`edge_size.py`); the diameter in `ArrayFit` is only the
rough value the search used.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from ..image_features import FeatureSearchError, contrast_map, estimate_feature_radius, fill_invalid, odd_size
from ..progress import Cancelled, ProgressCallback, StageProgress

__all__ = [
    "ArrayFit",
    "ArrayPrior",
    "Cancelled",
    "STAGE_WEIGHTS",
    "find_array",
    "lattice_nodes",
    "prepare_contrast",
    "refine_centers",
]

STAGE_WEIGHTS = {"contrast": 0.25, "candidates": 0.20, "lattice": 0.10, "refine": 0.45}
"""Share of the progress bar per stage. Placeholder until measured on the real dataset."""

MIN_SPOTS = 6
MIN_OCCUPANCY = 0.5


@dataclass(frozen=True)
class ArrayPrior:
    """What the user already knows; every field is optional (``None`` = estimate it)."""

    diameter_px: float | None = None
    rows: int | None = None
    cols: int | None = None
    pitch_x_px: float | None = None
    pitch_y_px: float | None = None


@dataclass(frozen=True)
class ArrayFit:
    rows: int
    cols: int
    pitch_x_px: float
    """Length of the lattice vector along the rows (`a1`)."""
    pitch_y_px: float
    """Length of the lattice vector along the columns (`a2`)."""
    tilt_deg: float
    """Angle of `a1` from the image x axis (positive = clockwise on screen, y points down)."""
    skew_deg: float
    """Angle between `a1` and `a2` minus 90 (0 for a rectangular array)."""
    origin_xy: tuple[float, float]
    """Lattice position of node (row 0, col 0)."""
    a1_xy: tuple[float, float]
    a2_xy: tuple[float, float]
    rough_diameter_px: float
    centers_xy: np.ndarray
    """(rows * cols, 2), row-major (row 0 first), matched-filter centres where found, else lattice positions."""
    found: np.ndarray
    """(rows * cols,) bool: a real spot was located at this node."""
    scores: np.ndarray
    """(rows * cols,) matched-filter score at the centre (contrast units)."""
    residual_rms_px: float
    """RMS distance of the found centres from the fitted lattice."""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return self.rows * self.cols

    @property
    def found_count(self) -> int:
        return int(self.found.sum())


def lattice_nodes(
    rows: int,
    cols: int,
    origin_xy: tuple[float, float],
    a1_xy: tuple[float, float],
    a2_xy: tuple[float, float],
) -> np.ndarray:
    """Node positions (rows * cols, 2), row-major, of a lattice: origin + col * a1 + row * a2.
    Also what a manual "place array" uses."""
    if rows < 1 or cols < 1:
        raise ValueError("rows and cols must be at least 1")
    j, i = np.mgrid[0:rows, 0:cols]
    i = i.reshape(-1, 1).astype(np.float64)
    j = j.reshape(-1, 1).astype(np.float64)
    return np.asarray(origin_xy, dtype=np.float64) + i * np.asarray(a1_xy, dtype=np.float64) + j * np.asarray(a2_xy, dtype=np.float64)


def find_array(
    image: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    prior: ArrayPrior | None = None,
    bright: bool = False,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
    diagnostics: dict | None = None,
) -> ArrayFit | None:
    """The array in `image`, or ``None`` with ``diagnostics["reason"]`` saying why not.

    `valid_mask` (True = usable) is combined with the finite pixels of `image`. Raises
    `Cancelled` when `cancelled()` becomes true at a progress checkpoint."""
    prior = prior or ArrayPrior()
    diagnostics = diagnostics if diagnostics is not None else {}
    step = StageProgress(STAGE_WEIGHTS, progress, cancelled)

    def fail(reason: str) -> None:
        diagnostics["reason"] = reason
        return None

    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 2 or image.size == 0:
        return fail("The image is empty or not 2-D.")
    step("contrast", 0.0, "Array: preparing the image...")
    if bright:
        # contrast_map wants dark spots: mirror the intensity range (stays positive, keeps the scale)
        finite_values = image[np.isfinite(image)]
        if finite_values.size:
            image = (float(finite_values.max()) + float(finite_values.min())) - image
    try:
        filled, finite = fill_invalid(image)
    except FeatureSearchError as error:
        return fail(str(error))
    valid = finite if valid_mask is None or valid_mask.shape != image.shape else (finite & valid_mask)
    if not valid.any():
        return fail("No valid (unmasked) pixels to search.")
    # Light smoothing: the matched filter and the LoG do the real averaging. The background size is tied
    # to the spot size once known; a first coarse pass needs one for the size estimate.
    coarse = contrast_map(filled, 101, 1.0)
    step("contrast", 0.6, "Array: measuring the spot size...")
    try:
        radius = 0.5 * float(prior.diameter_px) if prior.diameter_px else estimate_feature_radius(coarse)
    except FeatureSearchError as error:
        return fail(str(error))
    if radius < 2.0:
        return fail(f"Spots are too small to search for (radius {radius:.1f} px).")
    background_px = int(np.clip(odd_size(6.0 * radius), 31, 251))
    contrast = contrast_map(filled, background_px, 1.0)
    diagnostics["rough_diameter_px"] = 2.0 * radius
    step("contrast", 1.0, "Array: contrast map ready.")

    # -- candidates ---------------------------------------------------------------------
    usable = ndimage.distance_transform_edt(valid) > radius if not valid.all() else np.ones(valid.shape, dtype=bool)
    step("candidates", 0.0, "Array: searching for spots...")
    points = _find_spots(contrast, radius, usable)
    diagnostics["blob_count"] = int(len(points))
    points = points[_is_isolated_disk(contrast, points, radius)] if len(points) else points
    diagnostics["candidate_count"] = int(len(points))
    step("candidates", 1.0, f"Array: {len(points)} candidate spots.")
    if len(points) < MIN_SPOTS:
        return fail(
            f"Only {len(points)} candidate spots (need at least {MIN_SPOTS}). The contrast may be too low, "
            "or the spots a different size from the search size."
        )

    # -- lattice -------------------------------------------------------------------------
    step("lattice", 0.0, "Array: fitting the lattice...")
    lattice = _fit_lattice(points, radius, prior, diagnostics)
    if lattice is None:
        return None  # diagnostics["reason"] set
    origin, a1, a2, indices, rows, cols = lattice
    step("lattice", 1.0, f"Array: {rows} x {cols} lattice.")
    occupancy = len(indices) / float(rows * cols)
    diagnostics["occupancy"] = occupancy
    if rows < 2 or cols < 2:
        return fail(f"Only {rows} row(s) x {cols} column(s): no periodicity to measure a pitch from.")
    if occupancy < MIN_OCCUPANCY:
        return fail(
            f"Only {occupancy * 100:.0f}% of the {rows} x {cols} lattice has a spot near it: too sparse to trust."
        )

    # -- refine ---------------------------------------------------------------------------
    step("refine", 0.0, "Array: refining the centres...")
    score_map = _matched_filter(contrast, radius)
    step("refine", 0.5, "Array: locating the spots...")
    nodes = lattice_nodes(rows, cols, origin, a1, a2)
    window = 0.35 * min(np.hypot(*a1), np.hypot(*a2))
    centers, scores = _refine_nodes(score_map, nodes, window, usable)
    reference = float(np.median(scores[_indices_to_flat(indices, cols, rows)])) if len(indices) else 0.0
    found = scores >= 0.4 * reference
    centers = np.where(found[:, None], centers, nodes)
    # Tighten the lattice on the refined centres of the found spots (sub-pixel), then report its residual.
    origin, a1, a2 = _refit(centers, cols, found, origin, a1, a2)
    lattice_positions = lattice_nodes(rows, cols, origin, a1, a2)
    residual = float(np.sqrt(np.mean(np.sum((centers[found] - lattice_positions[found]) ** 2, axis=1)))) if found.any() else math.nan
    step("refine", 1.0, "Array: done.")

    warnings: list[str] = []
    if prior.rows and prior.rows != rows or prior.cols and prior.cols != cols:
        warnings.append(f"Found {rows} x {cols}, but {prior.rows or '?'} x {prior.cols or '?'} was expected.")
    if prior.pitch_x_px and abs(np.hypot(*a1) - prior.pitch_x_px) > 0.05 * prior.pitch_x_px:
        warnings.append(f"Pitch x {np.hypot(*a1):.1f} px differs from the expected {prior.pitch_x_px:.1f} px.")
    if prior.pitch_y_px and abs(np.hypot(*a2) - prior.pitch_y_px) > 0.05 * prior.pitch_y_px:
        warnings.append(f"Pitch y {np.hypot(*a2):.1f} px differs from the expected {prior.pitch_y_px:.1f} px.")
    diagnostics["reason"] = "ok"
    tilt = math.degrees(math.atan2(a1[1], a1[0]))
    skew = math.degrees(math.acos(float(np.clip(np.dot(a1, a2) / (np.hypot(*a1) * np.hypot(*a2)), -1.0, 1.0)))) - 90.0
    return ArrayFit(
        rows=rows,
        cols=cols,
        pitch_x_px=float(np.hypot(*a1)),
        pitch_y_px=float(np.hypot(*a2)),
        tilt_deg=tilt,
        skew_deg=skew,
        origin_xy=(float(origin[0]), float(origin[1])),
        a1_xy=(float(a1[0]), float(a1[1])),
        a2_xy=(float(a2[0]), float(a2[1])),
        rough_diameter_px=2.0 * radius,
        centers_xy=centers,
        found=found,
        scores=scores,
        residual_rms_px=residual,
        warnings=tuple(warnings),
    )


def prepare_contrast(
    image: np.ndarray,
    radius_px: float,
    *,
    valid_mask: np.ndarray | None = None,
    bright: bool = False,
    smooth_sigma: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """(contrast map with the spots positive, valid mask) for an image, the way `find_array` prepares it.

    `radius_px` (spot radius) sets the background scale. Raises `FeatureSearchError` for an image with no
    valid pixels. Used by the sizing steps so they see the same contrast the detector saw."""
    image = np.asarray(image, dtype=np.float32)
    if bright:
        finite_values = image[np.isfinite(image)]
        if finite_values.size:
            image = (float(finite_values.max()) + float(finite_values.min())) - image
    filled, finite = fill_invalid(image)
    valid = finite if valid_mask is None or valid_mask.shape != image.shape else (finite & valid_mask)
    background_px = int(np.clip(odd_size(6.0 * radius_px), 31, 251))
    return contrast_map(filled, background_px, smooth_sigma), valid


def refine_centers(
    contrast: np.ndarray,
    centers_xy: np.ndarray,
    radius_px: float,
    window_px: float,
    valid_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Move each centre to the sub-pixel maximum of the matched filter within `window_px` of it.

    Returns (centres, scores); a centre with no usable signal in its window comes back unchanged with score 0.
    Used to refine an existing set of ROIs (the "Refine array" action) and to snap a manual array to the image."""
    usable = valid_mask if valid_mask is not None else np.ones(contrast.shape, dtype=bool)
    return _refine_nodes(_matched_filter(contrast, radius_px), np.asarray(centers_xy, dtype=np.float64), float(window_px), usable)


# -- candidates ------------------------------------------------------------------------


def _find_spots(contrast: np.ndarray, radius: float, usable: np.ndarray, strength_fraction: float = 0.3) -> np.ndarray:
    """Peaks of the scale-normalised Laplacian of Gaussian at the spot size. (N, 2) x, y; about a pixel
    accurate (the matched filter refines them later)."""
    factor = int(np.clip(radius // 6, 1, 4))  # work on a reduced copy: the response is smooth at this scale
    if factor > 1:
        small = cv2.resize(contrast, (contrast.shape[1] // factor, contrast.shape[0] // factor), interpolation=cv2.INTER_AREA)
    else:
        small = contrast
    small_radius = radius / factor
    sigma = small_radius / math.sqrt(2.0)
    response = -(sigma**2) * ndimage.gaussian_laplace(small, sigma)
    footprint = odd_size(1.5 * small_radius)
    peak = (response == ndimage.maximum_filter(response, size=footprint)) & (response > 0)
    ys, xs = np.nonzero(peak)
    values = response[ys, xs]
    if len(values) == 0:
        return np.empty((0, 2))
    reference = float(np.median(np.sort(values)[::-1][:20]))
    keep = values >= strength_fraction * reference
    xs, ys = xs[keep], ys[keep]
    # back to full resolution (pixel centre of an area-reduced pixel)
    x_full = (xs + 0.5) * factor - 0.5
    y_full = (ys + 0.5) * factor - 0.5
    inside = (x_full >= 0) & (y_full >= 0) & (x_full <= contrast.shape[1] - 1) & (y_full <= contrast.shape[0] - 1)
    x_full, y_full = x_full[inside], y_full[inside]
    ok = usable[np.round(y_full).astype(int), np.round(x_full).astype(int)]
    return np.stack([x_full[ok], y_full[ok]], axis=1)


def _is_isolated_disk(contrast: np.ndarray, points: np.ndarray, radius: float, sectors: int = 8) -> np.ndarray:
    """True where a candidate is an *isolated disk*: (a) around it the surrounding ring
    (1.3 .. 1.8 radii) is much lighter than its inside in all but at most 2 of 8 directions (a neighbouring
    border or a piece of debris may darken one side), and (b) the inside is filled (at least 85 % of the
    disk samples reach half the core contrast). Rejects dark bands, dark corners, the border of the sample
    holder and thin strips along an edge, which have a spot-like core but a dark side or a hollow inside.
    Directions that fall outside the image are ignored (a spot cut by the image edge still passes) but at
    least 5 of the 8 ring sectors must be inside."""
    height, width = contrast.shape
    radii = np.array([1.3, 1.55, 1.8]) * radius
    angles = (np.arange(sectors * 2) + 0.5) * math.pi / sectors  # two samples per sector
    xs = points[:, 0, None, None] + radii[None, :, None] * np.cos(angles)[None, None, :]
    ys = points[:, 1, None, None] + radii[None, :, None] * np.sin(angles)[None, None, :]
    inside = (xs >= 0) & (xs <= width - 1) & (ys >= 0) & (ys <= height - 1)
    ring = ndimage.map_coordinates(contrast, [ys.ravel(), xs.ravel()], order=1, mode="nearest").reshape(xs.shape).astype(np.float64)
    ring[~inside] = np.nan
    ring = ring.reshape(len(points), len(radii), sectors, 2).transpose(0, 2, 1, 3).reshape(len(points), sectors, -1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a sector wholly outside the image is NaN on purpose
        sector_mean = np.nanmean(ring, axis=2)  # (N, sectors), NaN where outside the image
    valid_sectors = np.isfinite(sector_mean).sum(axis=1)
    core = ndimage.map_coordinates(contrast, [points[:, 1], points[:, 0]], order=1, mode="nearest").astype(np.float64)
    with np.errstate(invalid="ignore"):
        dark_sectors = (sector_mean > 0.5 * core[:, None]).sum(axis=1)  # NaN compares False
    disk_radii = np.array([0.25, 0.5, 0.8]) * radius
    disk_angles = np.arange(16) * (2.0 * math.pi / 16)
    dx = points[:, 0, None, None] + disk_radii[None, :, None] * np.cos(disk_angles)[None, None, :]
    dy = points[:, 1, None, None] + disk_radii[None, :, None] * np.sin(disk_angles)[None, None, :]
    disk = ndimage.map_coordinates(contrast, [dy.ravel(), dx.ravel()], order=1, mode="nearest").reshape(dx.shape)
    filled = (disk >= 0.5 * core[:, None, None]).reshape(len(points), -1).mean(axis=1)
    return (valid_sectors >= 5) & (core > 0) & (dark_sectors <= 2) & (filled >= 0.85)


# -- lattice ---------------------------------------------------------------------------


def _fit_lattice(points: np.ndarray, radius: float, prior: ArrayPrior, diagnostics: dict):
    """((origin, a1, a2, indices (N_in, 3) = [point index, i, j], rows, cols) or None."""

    def fail(reason: str):
        diagnostics["reason"] = reason
        return None

    vectors = _neighbour_vectors(points)
    if len(vectors) < 3:
        return fail("The candidate spots are too far apart to form a lattice.")
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    nearest = float(np.median(lengths))
    if nearest <= 1.2 * radius:
        return fail("The candidate spots are closer together than their own size: no clean lattice.")
    a1, a2 = _lattice_vectors(vectors, lengths, nearest)
    if a1 is None:
        return fail("Could not find two independent neighbour directions: the spots do not form a grid.")
    # a1 is the vector more along x, pointing right; a2 points down.
    if abs(a1[0]) < abs(a2[0]):
        a1, a2 = a2, a1
    if a1[0] < 0:
        a1 = -a1
    if a2[1] < 0:
        a2 = -a2
    origin = points[int(np.argmin(np.hypot(*(points - np.median(points, axis=0)).T)))]
    indices = None
    for _ in range(4):
        indices = _assign_indices(points, origin, a1, a2)
        if len(indices) < MIN_SPOTS:
            return fail(f"Only {len(indices)} spots fit a common lattice (need at least {MIN_SPOTS}).")
        origin, a1, a2 = _lsq(points, indices, origin, a1, a2)
    indices = _assign_indices(points, origin, a1, a2)
    if len(indices) < MIN_SPOTS:
        return fail(f"Only {len(indices)} spots fit a common lattice (need at least {MIN_SPOTS}).")
    i_min, j_min = int(indices[:, 1].min()), int(indices[:, 2].min())
    origin = origin + i_min * a1 + j_min * a2
    indices = indices.copy()
    indices[:, 1] -= i_min
    indices[:, 2] -= j_min
    cols, rows = int(indices[:, 1].max()) + 1, int(indices[:, 2].max()) + 1
    diagnostics.update(
        lattice_spots=int(len(indices)), pitch_x_px=float(np.hypot(*a1)), pitch_y_px=float(np.hypot(*a2)), rows=rows, cols=cols
    )
    return origin, a1, a2, indices, rows, cols


def _neighbour_vectors(points: np.ndarray, neighbours: int = 8) -> np.ndarray:
    """Vectors to the nearest neighbours, one per direction (v and -v are the same lattice vector)."""
    k = min(neighbours + 1, len(points))
    _dist, idx = cKDTree(points).query(points, k=k)
    vectors = (points[idx[:, 1:]] - points[:, None, :]).reshape(-1, 2)
    vectors = vectors[np.hypot(vectors[:, 0], vectors[:, 1]) > 0]
    flip = (vectors[:, 0] < 0) | ((vectors[:, 0] == 0) & (vectors[:, 1] < 0))
    vectors[flip] *= -1.0
    # keep the short ones only: a few nearest-neighbour distances, not distant pairs across the image
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    nearest = float(np.percentile(lengths, 10))
    return vectors[lengths <= 2.3 * nearest]


def _lattice_vectors(vectors: np.ndarray, lengths: np.ndarray, nearest: float):
    """The two shortest strong neighbour vectors that are not parallel."""
    tolerance = 0.2 * nearest
    tree = cKDTree(vectors)
    support = np.array([len(n) for n in tree.query_ball_point(vectors, tolerance)])
    strong = support >= 0.6 * support.max()
    order = np.argsort(lengths)
    first = next((int(k) for k in order if strong[k]), None)
    if first is None:
        return None, None
    a1 = np.median(vectors[tree.query_ball_point(vectors[first], tolerance)], axis=0)
    unit = a1 / np.hypot(*a1)
    cross = np.abs(vectors[:, 0] * unit[1] - vectors[:, 1] * unit[0]) / lengths  # sin of the angle to a1
    second = next((int(k) for k in order if strong[k] and cross[k] > 0.5), None)
    if second is None:
        return None, None
    a2 = np.median(vectors[tree.query_ball_point(vectors[second], tolerance)], axis=0)
    return a1.astype(np.float64), a2.astype(np.float64)


def _assign_indices(points: np.ndarray, origin: np.ndarray, a1: np.ndarray, a2: np.ndarray) -> np.ndarray:
    """Integer lattice indices of every point that sits close to a node: rows [point, i, j].
    Two points on one node: the closer one wins."""
    matrix = np.column_stack([a1, a2])
    coefficients = np.linalg.solve(matrix, (points - origin).T).T
    rounded = np.round(coefficients)
    residual = np.hypot(*(points - (origin + rounded @ matrix.T)).T)
    limit = 0.25 * min(np.hypot(*a1), np.hypot(*a2))
    good = np.flatnonzero(residual < limit)
    best: dict[tuple[int, int], int] = {}
    for p in good:
        key = (int(rounded[p, 0]), int(rounded[p, 1]))
        if key not in best or residual[p] < residual[best[key]]:
            best[key] = int(p)
    return np.array([[p, i, j] for (i, j), p in best.items()], dtype=np.int64).reshape(-1, 3)


def _lsq(points: np.ndarray, indices: np.ndarray, origin: np.ndarray, a1: np.ndarray, a2: np.ndarray):
    """Least-squares origin, a1, a2 from points with integer indices."""
    design = np.column_stack([np.ones(len(indices)), indices[:, 1], indices[:, 2]])
    target = points[indices[:, 0]]
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    return solution[0], solution[1], solution[2]


def _refit(centers: np.ndarray, cols: int, found: np.ndarray, origin, a1, a2):
    """Re-fit origin / a1 / a2 on refined centres of the found nodes (indices are row-major)."""
    flat = np.flatnonzero(found)
    if len(flat) < MIN_SPOTS:
        return origin, a1, a2
    indices = np.column_stack([flat, flat % cols, flat // cols])
    return _lsq(centers, indices, origin, a1, a2)


def _indices_to_flat(indices: np.ndarray, cols: int, rows: int) -> np.ndarray:
    return indices[:, 2] * cols + indices[:, 1]


# -- centres ---------------------------------------------------------------------------


def _matched_filter(contrast: np.ndarray, radius: float) -> np.ndarray:
    """Mean contrast inside a disk minus mean contrast in a surrounding ring, at every pixel (one pass)."""
    outer = max(int(math.ceil(1.8 * radius)), 3)
    yy, xx = np.mgrid[-outer : outer + 1, -outer : outer + 1]
    distance = np.hypot(xx, yy)
    disk = distance <= 0.85 * radius
    ring = (distance >= 1.3 * radius) & (distance <= 1.8 * radius)
    kernel = np.zeros(distance.shape, dtype=np.float32)
    kernel[disk] = 1.0 / disk.sum()
    kernel[ring] = -1.0 / max(ring.sum(), 1)
    return cv2.filter2D(contrast.astype(np.float32), -1, kernel, borderType=cv2.BORDER_REPLICATE)


def _refine_nodes(score_map: np.ndarray, nodes: np.ndarray, window: float, usable: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sub-pixel maximum of the score map near every node. Returns centres (N, 2) and the score there."""
    height, width = score_map.shape
    half = max(int(math.ceil(window)), 1)
    centers = nodes.copy()
    scores = np.zeros(len(nodes), dtype=np.float64)
    for n, (x, y) in enumerate(nodes):
        x0, y0 = int(round(x)) - half, int(round(y)) - half
        x1, y1 = x0 + 2 * half + 1, y0 + 2 * half + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            continue
        patch = score_map[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        patch = np.where(np.hypot(xx - x, yy - y) <= window, patch, -np.inf)
        flat = int(np.argmax(patch))
        py, px = divmod(flat, patch.shape[1])
        if not np.isfinite(patch[py, px]) or not usable[y0 + py, x0 + px]:
            continue
        dx = _parabolic(patch[py, :], px)
        dy = _parabolic(patch[:, px], py)
        refined = (x0 + px + dx, y0 + py + dy)
        if math.hypot(refined[0] - x, refined[1] - y) >= window - 1.0:
            continue  # the maximum sits on the edge of the window: that is a neighbour's skirt, not a spot here
        centers[n] = refined
        scores[n] = float(patch[py, px])
    return centers, scores


def _parabolic(values: np.ndarray, i: int) -> float:
    if i <= 0 or i >= len(values) - 1 or not np.all(np.isfinite(values[i - 1 : i + 2])):
        return 0.0
    a, b, c = values[i - 1], values[i], values[i + 1]
    denominator = a - 2 * b + c
    return 0.0 if abs(denominator) < 1e-12 else float(np.clip(0.5 * (a - c) / denominator, -0.5, 0.5))
