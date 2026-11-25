"""Helpers of the array finder that work on a contrast map (pure: numpy / scipy / cv2, no Qt).

The old detector built on these (`find_array`, with its result type, `lattice_nodes` and `refine_centers`) was retired 2026-11-25 together with the old Array group;
what is left is what `array_finder.py` shares:

- `prepare_contrast`: the contrast map of an image, spots positive (`image_features.contrast_map`; ``bright=True`` flips the sign for bright spots).
- `scan_spot_radius`: the spot size when no ROI says it: every radius of `SIZE_SCAN_RADII_PX` runs a candidate search and a lattice fit on a half-size copy and is scored by
  how many candidates fit one common lattice (debris and small features do not form a lattice, so they cannot win). Added 2026-10-20: it replaced a single scale-space
  guess that failed on 97 of 156 real images (whole array found in 59 / 156 -> 156 / 156).
- `matched_filter` and `refine_nodes`: a disk-minus-ring matched filter for the whole image and the sub-pixel peak of it near each node.

Space: pixel coordinates of the image passed in (pixel centres at integers); all lengths are diameters or pitches in pixels.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import cv2
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from ..image_features import contrast_map, fill_invalid, odd_size
from ..progress import Cancelled, StageProgress

__all__ = [
    "ArrayPrior",
    "Cancelled",
    "matched_filter",
    "prepare_contrast",
    "refine_nodes",
    "scan_spot_radius",
]

MIN_SPOTS = 6

SIZE_SCAN_RADII_PX = np.geomspace(3.0, 60.0, 27)
"""Spot radii tried when no diameter is given: diameter 6-120 px in ~12 % steps. The isolated-disk test accepts a
search radius within about +-25 % of the true one (measured 2026-10-20: diameter 34-44 px found every spot of a
17 px-radius array, 26 and 54 px none), so a 12 % ladder cannot step over the true size."""
SIZE_SCAN_GOOD_FRACTION = 0.9
"""Radii scoring at least this fraction of the best lattice count count as equally good; the middle of the longest
run of them is taken (the most margin on both sides of that +-25 % window)."""


@dataclass(frozen=True)
class ArrayPrior:
    """What the user already knows; every field is optional (``None`` = estimate it)."""

    diameter_px: float | None = None
    rows: int | None = None
    cols: int | None = None
    pitch_x_px: float | None = None
    pitch_y_px: float | None = None








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




# -- spot size -------------------------------------------------------------------------


def _scan_spot_radius(filled: np.ndarray, valid: np.ndarray, step: StageProgress, diagnostics: dict) -> float | None:
    """The search radius when no diameter is given: every radius of `SIZE_SCAN_RADII_PX` runs the candidate search
    and the lattice fit on a half-size copy, and is scored by how many candidates fit one common lattice (debris and
    small features do not form a lattice, so they cannot win). The middle of the longest run of near-best radii is
    returned; ``None`` (with ``diagnostics["reason"]``) when no radius gives a lattice.

    Replaces a single scale-space guess (`image_features.estimate_feature_radius`) that jumped between coarse steps
    and, on 2026-10-20, put a 17 px-radius array at 27-34 px (long wavelengths) or 6 px (another cube): the
    isolated-disk test then rejected every spot. Before / after on that dataset (26 wavelengths x 3 cubes x with /
    without background removal, 156 images): whole array found in 59 / 156 -> 156 / 156, chosen radius 17.9-19.0 px."""
    half = cv2.resize(filled, (filled.shape[1] // 2, filled.shape[0] // 2), interpolation=cv2.INTER_AREA)
    valid_half = cv2.resize(valid.astype(np.float32), (half.shape[1], half.shape[0]), interpolation=cv2.INTER_AREA) > 0.999
    all_valid = bool(valid_half.all())
    distance = None if all_valid else ndimage.distance_transform_edt(valid_half)
    scores: list[int] = []
    for k, radius in enumerate(SIZE_SCAN_RADII_PX):
        step("size", k / len(SIZE_SCAN_RADII_PX), f"Array: trying spot size {2.0 * radius:.0f} px...")
        r = float(radius) / 2.0  # on the half-size copy
        usable = np.ones(half.shape, dtype=bool) if distance is None else distance > r
        contrast = contrast_map(half, int(np.clip(odd_size(6.0 * r), 15, 251)), 0.5)
        points = _find_spots(contrast, r, usable)
        if len(points) >= MIN_SPOTS:
            points = points[_is_isolated_disk(contrast, points, r)]
        lattice = _fit_lattice(points, r, ArrayPrior(), {}) if len(points) >= MIN_SPOTS else None
        scores.append(0 if lattice is None else int(len(lattice[3])))
    step("size", 1.0, "Array: spot size found.")
    diagnostics["size_scan"] = [(round(2.0 * float(r), 1), s) for r, s in zip(SIZE_SCAN_RADII_PX, scores)]
    best = max(scores)
    if best < MIN_SPOTS:
        diagnostics["reason"] = (
            f"No regular array of round spots found at any spot size from {2 * SIZE_SCAN_RADII_PX[0]:.0f} to "
            f"{2 * SIZE_SCAN_RADII_PX[-1]:.0f} px. If the spots are there, select a ROI on one of them (Array > Find)."
        )
        return None
    good = [i for i, s in enumerate(scores) if s >= SIZE_SCAN_GOOD_FRACTION * best]
    runs: list[list[int]] = [[good[0]]]
    for i in good[1:]:
        if i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    longest = max(runs, key=len)
    radius = float(np.sqrt(SIZE_SCAN_RADII_PX[longest[0]] * SIZE_SCAN_RADII_PX[longest[-1]]))  # geometric middle
    diagnostics["size_scan_radius_px"] = radius
    return radius


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
    try:
        for _ in range(4):
            indices = _assign_indices(points, origin, a1, a2)
            if len(indices) < MIN_SPOTS:
                return fail(f"Only {len(indices)} spots fit a common lattice (need at least {MIN_SPOTS}).")
            origin, a1, a2 = _lsq(points, indices, origin, a1, a2)
        indices = _assign_indices(points, origin, a1, a2)
    except np.linalg.LinAlgError:
        # The two lattice vectors became (nearly) parallel during the fit - found 2026-10-20 while scanning sizes,
        # where a wrong size gives a few scattered candidates. Without this it surfaced as an unexpected error.
        return fail("The candidate spots do not form a two-dimensional grid.")
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


# Public names of three helpers the array finder (`array_finder.py`) shares. Aliases only: the old finder's behaviour is unchanged (the two are to be compared).
scan_spot_radius = _scan_spot_radius
matched_filter = _matched_filter
refine_nodes = _refine_nodes
