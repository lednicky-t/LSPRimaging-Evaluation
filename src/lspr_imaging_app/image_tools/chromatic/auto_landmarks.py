"""Automatic chromatic-correction landmarks: find dark features on the
reference wavelength image, then follow them wavelength by wavelength.

Pure computation (numpy/scipy/cv2): no Qt, no dataset, no files. Images come
in through a `load_image(wavelength_nm)` callable (already in processed
space: rotated/flipped/cropped, possibly with NaN where rotation created
pixels), results go out as plain arrays. Progress and cancellation follow the
rule in CLAUDE.md through `lspr_imaging_app.progress`.

Why this replaces `landmark_autotrack.py` for the rewrite (evidence:
`docs/chromatic_landmark_lab_findings_2026-10-04.md`, script
`tools/chromatic_landmark_lab.py`): on the real bulk-sensitivity dataset it
tracks 15 landmarks over 26 wavelengths with a leave-one-out error of about
0.14 px, versus Harris-corner / centroid tracking with flagged losses. It
also needs no shape assumption.

**Physical assumptions** (maintainer, 2026-10-04): features are always darker
than the background, at every wavelength (contrast never flips). Size, shape
and placement vary with the sample design, so none is hard-coded: size is
measured from the reference image, shape is never tested (a candidate only
has to be localisable in both x and y), placement is an even grid.

**Method**
1. Contrast map `(background - image) / background` (background = grey
   closing, so dark features of any shape become positive).
2. Feature size from a scale-space blob search on the reference.
3. Candidates = blob peaks at that size, kept if localisable; one chosen per
   node of an nx x ny grid inside the image minus a border (Hungarian
   assignment, so none is used twice).
4. Walk outward from the reference wavelength, one tracked wavelength at a
   time, searching only `max_step_px` (per neighbouring dataset wavelength)
   around the previous position with patch cross-correlation on contrast
   maps (sub-pixel peak). Every step fits a robust similarity model;
   landmarks that disagree are *flagged*, never silently replaced.
5. A landmark flagged at any wavelength is dropped entirely (reported, not
   hidden), so every landmark that remains is trustworthy at every wavelength
   and `ChromaticModule.refit()` can use them all.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage
from scipy.optimize import linear_sum_assignment

from ...image_features import FeatureSearchError, contrast_map, estimate_feature_radius, fill_invalid
from ...image_features import odd_size as _odd
from ...progress import Cancelled, ProgressCallback, StageProgress

__all__ = [
    "AutoLandmarkError",
    "AutoLandmarkParams",
    "AutoLandmarkResult",
    "Cancelled",
    "STAGE_WEIGHTS",
    "detect_and_track",
    "grid_for_count",
    "snap_landmark_count",
    "sample_count_for_stride",
]

# Share of the progress bar per stage, from measured times on the
# 26-wavelength development dataset (TIFF reads; images 80-84 %, feature
# search 14 %, tracking 2-4 %, fit ~0 %). "images" = reading planes plus
# building their contrast maps; with OME-Zarr reads it only gets bigger.
STAGE_WEIGHTS = {"images": 0.80, "features": 0.13, "tracking": 0.06, "fit": 0.01}

_MIN_LANDMARKS_FOR_FIT = 4


AutoLandmarkError = FeatureSearchError
"""A failure the user should read (not enough features, bad input). The same class the shared
feature helpers raise, so one ``except`` covers both."""


@dataclass(frozen=True)
class AutoLandmarkParams:
    reference_wavelength_nm: float
    all_wavelengths_nm: tuple[float, ...]
    """Every spectral wavelength of the reference cube (dark frame excluded),
    sorted. Used only to turn "k tracked wavelengths apart" into a search
    radius: a step across k dataset wavelengths may move k times as far."""
    sample_wavelengths_nm: tuple[float, ...]
    """The wavelengths to track (`ChromaticModule.sample_wavelengths_for_cube`);
    the reference wavelength is always tracked too, as the starting point."""
    landmark_count: int = 15
    border_fraction: float = 0.05
    max_step_px: float = 5.0
    feature_diameter_px: float | None = None
    """None = measure from the reference image."""
    min_score: float = 0.5
    strength_fraction: float = 0.3
    quality_fraction: float = 0.3


@dataclass(frozen=True)
class AutoLandmarkResult:
    wavelengths_nm: tuple[float, ...]
    """Tracked wavelengths, ascending; always contains the reference."""
    positions_px: np.ndarray
    """(n_wavelengths, n_kept, 2), x then y, processed-image pixels."""
    reference_wavelength_nm: float
    requested_count: int
    feature_diameter_px: float
    loo_mean_px: float
    """Mean leave-one-out error of the per-wavelength similarity fits (px):
    how well a landmark is predicted from the others - the honest quality
    number (in-sample residuals are optimistic)."""
    loo_max_px: float
    spread_fraction: tuple[float, float] = (1.0, 1.0)
    """Landmark extent as a fraction of the image width and height (how far
    apart they are: a fit extrapolates badly outside the landmarks)."""
    dropped: tuple[str, ...] = ()
    """Human-readable reason per dropped landmark."""
    timings_s: dict[str, float] = field(default_factory=dict)

    @property
    def kept_count(self) -> int:
        return int(self.positions_px.shape[1])


# -- grid helpers (pure, unit-tested) ---------------------------------------


def grid_for_count(count: int, aspect: float = 1.5) -> tuple[int, int]:
    """(nx, ny) with nx * ny == count whose shape best matches the image
    `aspect` (width / height). 15 -> 5x3, 32 -> 8x4, 60 -> 10x6 for a
    landscape image. Needs at least 2 rows."""
    count = max(int(count), 4)
    best: tuple[float, int, int] | None = None
    for ny in range(2, count + 1):
        if count % ny:
            continue
        nx = count // ny
        if nx < 2:
            continue
        mismatch = abs(np.log((nx / ny) / max(aspect, 1e-6)))
        if best is None or mismatch < best[0]:
            best = (mismatch, nx, ny)
    if best is None:
        raise ValueError(f"{count} landmarks cannot form a grid with at least 2 rows and columns")
    return best[1], best[2]


def snap_landmark_count(count: int, aspect: float = 1.5, tolerance: float = 0.45) -> int:
    """Nearest landmark count that forms a reasonably shaped grid (so 13,
    which is a 13x1 strip, becomes 12 or 14)."""
    count = max(int(count), 4)
    for delta in range(0, 40):
        for candidate in (count - delta, count + delta):
            if candidate < 4:
                continue
            try:
                nx, ny = grid_for_count(candidate, aspect)
            except ValueError:
                continue
            if abs(np.log((nx / ny) / max(aspect, 1e-6))) <= tolerance:
                return candidate
    return count


def sample_count_for_stride(wavelength_count: int, stride: int) -> int:
    """How many wavelengths "every `stride`-th" means (both ends included).
    stride 1 = all of them."""
    wavelength_count = max(int(wavelength_count), 1)
    stride = max(int(stride), 1)
    if wavelength_count <= 2 or stride == 1:
        return wavelength_count
    return int(np.ceil((wavelength_count - 1) / stride)) + 1


# -- images -------------------------------------------------------------


def _patch_half(radius: float) -> int:
    return max(int(np.ceil(1.4 * radius)), 5)


# -- candidates -----------------------------------------------------------


def _parabolic(values: np.ndarray, i: int) -> float:
    if i <= 0 or i >= len(values) - 1:
        return 0.0
    a, b, c = values[i - 1], values[i], values[i + 1]
    denom = a - 2 * b + c
    return 0.0 if abs(denom) < 1e-12 else float(np.clip(0.5 * (a - c) / denom, -0.5, 0.5))


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 1e-12 else 1.0


def _trackability(contrast: np.ndarray, pos: tuple[float, float], radius: float) -> float:
    """1 - (worst-direction correlation of the patch with itself shifted a
    little, 8 directions). Round blobs, corners and irregular shapes score
    well; a stripe scores ~0 because it can slide along its own length
    unnoticed. Shape-agnostic."""
    size = 2 * _patch_half(radius) + 1
    d = max(1.5, 0.1 * radius)
    base = cv2.getRectSubPix(contrast, (size, size), (float(pos[0]), float(pos[1])))
    worst = -1.0
    for k in range(8):
        a = 2 * np.pi * k / 8
        shifted = cv2.getRectSubPix(
            contrast, (size, size), (float(pos[0] + d * np.cos(a)), float(pos[1] + d * np.sin(a)))
        )
        worst = max(worst, _ncc(base, shifted))
    return 1.0 - worst


def _find_candidates(
    contrast: np.ndarray,
    radius: float,
    search_px: float,
    strength_fraction: float,
    usable: np.ndarray,
) -> list[tuple[float, float, float, float]]:
    """Dark blobs at the measured size: [(x, y, strength, trackability)].
    `usable` marks pixels far enough from invalid (NaN) data."""
    height, width = contrast.shape
    sigma = radius / np.sqrt(2.0)
    response = -(sigma**2) * ndimage.gaussian_laplace(contrast, sigma)
    footprint = _odd(3.0 * radius)
    peak = (response == ndimage.maximum_filter(response, size=footprint)) & (response > 0)
    ys, xs = np.nonzero(peak)
    values = response[ys, xs]
    if len(values) == 0:
        return []
    reference = float(np.median(np.sort(values)[::-1][:20]))
    # The whole feature (plus a few px) must be inside the image. The matching
    # patch may overhang the edge a little: OpenCV replicates edge pixels, and a
    # landmark that then tracks badly is flagged and dropped anyway. A larger
    # margin (patch + search window, ~47 px here) removed the outer ring of
    # features and left cropped images with bunched-up landmarks.
    margin = int(np.ceil(1.15 * radius)) + 6
    out: list[tuple[float, float, float, float]] = []
    for x, y, v in zip(xs, ys, values):
        if v < strength_fraction * reference:
            continue
        if not (margin <= x < width - margin and margin <= y < height - margin) or not usable[y, x]:
            continue
        dx = _parabolic(response[y, :], int(x)) if 0 < x < width - 1 else 0.0
        dy = _parabolic(response[:, x], int(y)) if 0 < y < height - 1 else 0.0
        pos = (float(x + dx), float(y + dy))
        out.append((pos[0], pos[1], float(v), _trackability(contrast, pos, radius)))
    return out


def _select_landmarks(
    candidates: list[tuple[float, float, float, float]],
    grid: tuple[int, int],
    bounds: tuple[float, float, float, float],
    quality_fraction: float,
    tolerance_px: float = 0.0,
) -> list[tuple[float, float]]:
    """One candidate per node of an nx x ny grid over `bounds` (Hungarian
    assignment). Candidates less localisable than `quality_fraction` x the
    median are dropped first (stripes, smeared or ambiguous features)."""
    nx, ny = grid
    x0, y0, x1, y1 = bounds
    # A feature whose edge touches the border rectangle counts as inside it.
    inside = [
        c for c in candidates if x0 - tolerance_px <= c[0] <= x1 + tolerance_px and y0 - tolerance_px <= c[1] <= y1 + tolerance_px
    ]
    if inside:
        floor = quality_fraction * float(np.median([c[3] for c in inside]))
        inside = [c for c in inside if c[3] >= floor]
    if len(inside) < nx * ny:
        raise AutoLandmarkError(
            f"Only {len(inside)} usable features were found in the image, but {nx * ny} landmarks were requested. "
            "Ask for fewer landmarks."
        )
    points = np.array([(c[0], c[1]) for c in inside])
    # Lay the grid over where usable features really are, not over the raw
    # border rectangle: candidates cannot sit within a patch plus a search
    # window of the edge, so nodes placed out there would all snap inwards and
    # the outer rows/columns would bunch up (seen on a cropped image).
    xs = np.linspace(points[:, 0].min(), points[:, 0].max(), nx)
    ys = np.linspace(points[:, 1].min(), points[:, 1].max(), ny)
    nodes = np.array([(x, y) for y in ys for x in xs])
    cost = np.hypot(nodes[:, None, 0] - points[None, :, 0], nodes[:, None, 1] - points[None, :, 1])
    rows, cols = linear_sum_assignment(cost)
    return [(float(points[c][0]), float(points[c][1])) for _r, c in sorted(zip(rows, cols))]


# -- similarity model (complex form: z' = a z + b, a = s e^{i theta}) ------------


def _fit_similarity(src: np.ndarray, dst: np.ndarray) -> tuple[complex, complex]:
    z = src[:, 0] + 1j * src[:, 1]
    w = dst[:, 0] + 1j * dst[:, 1]
    zc, wc = z.mean(), w.mean()
    dz, dw = z - zc, w - wc
    denom = float((np.abs(dz) ** 2).sum())
    a = (np.conj(dz) * dw).sum() / denom if denom > 0 else 1.0 + 0j
    return a, wc - a * zc


def _apply_similarity(a: complex, b: complex, points: np.ndarray) -> np.ndarray:
    out = a * (points[:, 0] + 1j * points[:, 1]) + b
    return np.column_stack([out.real, out.imag])


def _robust_similarity(
    src: np.ndarray, dst: np.ndarray, floor_px: float = 0.4, k: float = 3.5
) -> tuple[complex, complex, np.ndarray]:
    """Similarity fit that rejects landmarks whose residual is far above the
    typical (MAD-based) one; never rejects below 4 inliers."""
    inlier = np.ones(len(src), dtype=bool)
    a, b = _fit_similarity(src, dst)
    for _ in range(5):
        residual = np.hypot(*(_apply_similarity(a, b, src) - dst).T)
        scale = 1.4826 * float(np.median(residual[inlier])) if inlier.any() else 0.0
        updated = residual <= max(floor_px, k * scale)
        if updated.sum() < _MIN_LANDMARKS_FOR_FIT or np.array_equal(updated, inlier):
            break
        inlier = updated
        a, b = _fit_similarity(src[inlier], dst[inlier])
    return a, b, inlier


# -- tracking -------------------------------------------------------------


def _track_ncc(
    template_map: np.ndarray,
    target_map: np.ndarray,
    template_pos: np.ndarray,
    search_pos: np.ndarray,
    radius: float,
    max_step: float,
) -> tuple[float, float, float, bool]:
    """Cross-correlate a contrast patch around `template_pos` against
    `target_map` near `search_pos`. Returns (x, y, score, hit_limit)."""
    size = 2 * _patch_half(radius) + 1
    template = cv2.getRectSubPix(template_map, (size, size), (float(template_pos[0]), float(template_pos[1])))
    cx, cy = int(round(search_pos[0])), int(round(search_pos[1]))
    s = int(np.ceil(max_step)) + 1
    region = cv2.getRectSubPix(target_map, (size + 2 * s, size + 2 * s), (float(cx), float(cy)))
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    jj, ii = np.mgrid[0 : result.shape[0], 0 : result.shape[1]]
    allowed = np.hypot(ii - s, jj - s) <= max_step + 0.5
    masked = np.where(allowed, result, -1.0)
    j, i = np.unravel_index(int(np.argmax(masked)), masked.shape)
    dx = _parabolic(result[j, :], i) if 0 < i < result.shape[1] - 1 else 0.0
    dy = _parabolic(result[:, i], j) if 0 < j < result.shape[0] - 1 else 0.0
    hit_limit = bool(np.hypot(i - s, j - s) >= max_step - 0.5)
    return cx + (i - s) + dx, cy + (j - s) + dy, float(masked[j, i]), hit_limit


def _track_all(
    maps: list[np.ndarray],
    reference_index: int,
    reference_points: list[tuple[float, float]],
    radius: float,
    step_limit_px: list[float],
    max_step_px: float,
    min_score: float,
    report: StageProgress,
) -> tuple[dict[int, np.ndarray], dict[int, list[str]]]:
    """Outward from `reference_index`, one tracked image at a time. Returns
    positions and a status ("ok" | "outlier" | "lowscore" | "atlimit") per
    landmark per image. Flagged landmarks continue from the step model's
    prediction (so they may recover) but keep their flag."""
    count = len(reference_points)
    positions = {reference_index: np.array(reference_points, dtype=float)}
    status = {reference_index: ["ok"] * count}
    total = max(len(maps) - 1, 1)
    done = 0
    for direction in (+1, -1):
        previous = reference_index
        index = reference_index + direction
        while 0 <= index < len(maps):
            limit = step_limit_px[index]
            previous_points = positions[previous]
            new_points = np.zeros((count, 2))
            scores = np.zeros(count)
            at_limit = np.zeros(count, dtype=bool)
            for k in range(count):
                x, y, score, hit = _track_ncc(
                    maps[previous], maps[index], previous_points[k], previous_points[k], radius, limit
                )
                new_points[k] = (x, y)
                scores[k] = score
                at_limit[k] = hit
            good = scores >= min_score
            state = ["ok"] * count
            predicted = previous_points.copy()
            if good.sum() >= _MIN_LANDMARKS_FOR_FIT:
                a, b, inlier = _robust_similarity(previous_points[good], new_points[good], floor_px=0.4 * limit / max_step_px)
                for local, k in enumerate(np.nonzero(good)[0]):
                    if not inlier[local]:
                        state[k] = "outlier"
                predicted = _apply_similarity(a, b, previous_points)
            for k in range(count):
                if not good[k]:
                    state[k] = "lowscore"
                elif at_limit[k] and state[k] == "ok":
                    state[k] = "atlimit"
                if state[k] != "ok":
                    new_points[k] = predicted[k]
            positions[index], status[index] = new_points, state
            previous = index
            index += direction
            done += 1
            report("tracking", done / total, f"Tracking landmarks ({done}/{total})")
    return positions, status


def _leave_one_out_residuals(positions: np.ndarray, reference_index: int) -> np.ndarray:
    """(wavelengths, landmarks) distance (px) between each landmark's tracked
    position and where the *other* landmarks' similarity fit predicts it. 0 at
    the reference wavelength. Leave-one-out, so a bad landmark cannot pull the
    fit towards itself and hide."""
    count = positions.shape[1]
    reference = positions[reference_index]
    residual = np.zeros(positions.shape[:2])
    for index in range(positions.shape[0]):
        if index == reference_index:
            continue
        target = positions[index]
        for j in range(count):
            keep = np.arange(count) != j
            a, b = _fit_similarity(reference[keep], target[keep])
            residual[index, j] = float(np.hypot(*(_apply_similarity(a, b, reference[j : j + 1])[0] - target[j])))
    return residual


def _prune_inconsistent(
    positions: np.ndarray,
    wavelengths: list[float],
    reference_index: int,
    floor_px: float = 0.5,
    factor: float = 3.0,
) -> tuple[list[int], list[tuple[int, str]]]:
    """Drop landmarks that stay far from the fit.

    Each tracking step only rejects a landmark that *jumps*; one that drifts a
    little per step (a particle whose look changes with wavelength) passes every
    step and ends up a pixel off. So after tracking, a landmark whose worst
    leave-one-out residual exceeds `max(floor_px, factor x the median landmark's
    worst)` is dropped (worst first, refitting after each drop, never below
    `_MIN_LANDMARKS_FOR_FIT`). Returns (indices kept, [(index, reason)])."""
    active = list(range(positions.shape[1]))
    dropped: list[tuple[int, str]] = []
    while len(active) > _MIN_LANDMARKS_FOR_FIT:
        residual = _leave_one_out_residuals(positions[:, active], reference_index)
        worst = residual.max(axis=0)
        tolerance = max(floor_px, factor * float(np.median(worst)))
        candidate = int(np.argmax(worst))
        if worst[candidate] <= tolerance:
            break
        at = wavelengths[int(np.argmax(residual[:, candidate]))]
        dropped.append((active[candidate], f"{worst[candidate]:.1f} px from the fit at {at:g} nm"))
        del active[candidate]
    return active, dropped


def _fit_quality(positions: np.ndarray, reference_index: int) -> tuple[float, float]:
    """(mean, max) leave-one-out error over the non-reference wavelengths."""
    reference = positions[reference_index]
    errors: list[float] = []
    for index in range(positions.shape[0]):
        if index == reference_index:
            continue
        source, target = reference, positions[index]
        squared = []
        for j in range(len(source)):
            keep = np.arange(len(source)) != j
            a, b = _fit_similarity(source[keep], target[keep])
            squared.append(float(np.hypot(*(_apply_similarity(a, b, source[j : j + 1])[0] - target[j]))) ** 2)
        errors.append(float(np.sqrt(np.mean(squared))))
    if not errors:
        return 0.0, 0.0
    return float(np.mean(errors)), float(np.max(errors))


# -- the pipeline ---------------------------------------------------------


def detect_and_track(
    params: AutoLandmarkParams,
    load_image: Callable[[float], np.ndarray],
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> AutoLandmarkResult:
    """Find landmarks on the reference wavelength and track them across
    `params.sample_wavelengths_nm`. Raises `AutoLandmarkError` (readable
    message) when it cannot, and `Cancelled` if `cancelled()` turns true."""
    report = StageProgress(STAGE_WEIGHTS, progress, cancelled)
    timings: dict[str, float] = {}
    started = time.perf_counter()

    reference_nm = float(params.reference_wavelength_nm)
    tracked = sorted({float(w) for w in params.sample_wavelengths_nm} | {reference_nm})
    if len(tracked) < 2:
        raise AutoLandmarkError("At least two wavelengths are needed to measure chromatic shifts.")
    reference_index = tracked.index(reference_nm)
    spectral = sorted(float(w) for w in params.all_wavelengths_nm)

    def dataset_index(wavelength: float) -> int:
        return int(np.argmin([abs(w - wavelength) for w in spectral])) if spectral else 0

    # How many neighbouring dataset wavelengths a tracking step spans: it may move that many times as far.
    step_limit = [0.0] * len(tracked)
    for i in range(len(tracked)):
        toward = i - 1 if i > reference_index else i + 1
        if 0 <= toward < len(tracked) and i != reference_index:
            span = abs(dataset_index(tracked[i]) - dataset_index(tracked[toward]))
            step_limit[i] = params.max_step_px * max(span, 1)
    longest_step = max(step_limit)

    # -- images: reference first (feature size needs it), then the rest
    reference_raw = np.asarray(load_image(reference_nm), dtype=np.float32)
    reference_image, reference_valid = fill_invalid(reference_raw)
    height, width = reference_image.shape
    if params.feature_diameter_px:
        radius = float(params.feature_diameter_px) / 2.0
    else:
        radius = estimate_feature_radius(contrast_map(reference_image, 101, 1.0))
    background_px = int(np.clip(_odd(6.0 * radius), 31, 251))
    smooth_sigma = float(np.clip(0.1 * radius, 0.8, 2.0))

    maps: list[np.ndarray] = []
    for position, wavelength in enumerate(tracked):
        if position == reference_index:
            image = reference_image
        else:
            image, _valid = fill_invalid(np.asarray(load_image(wavelength), dtype=np.float32))
            if image.shape != reference_image.shape:
                raise AutoLandmarkError("Images of different sizes cannot be compared.")
        maps.append(contrast_map(image, background_px, smooth_sigma))
        report("images", (position + 1) / len(tracked), f"Preparing image {position + 1}/{len(tracked)} ({wavelength:g} nm)")
    timings["images"] = time.perf_counter() - started

    # -- landmarks on the reference
    stage_start = time.perf_counter()
    margin = int(np.ceil(1.15 * radius)) + 6
    usable = (
        ndimage.distance_transform_edt(reference_valid) > margin
        if not reference_valid.all()
        else np.ones(reference_valid.shape, dtype=bool)
    )
    candidates = _find_candidates(maps[reference_index], radius, longest_step, params.strength_fraction, usable)
    nx, ny = grid_for_count(params.landmark_count, width / height)
    border = float(np.clip(params.border_fraction, 0.0, 0.3))
    bounds = (width * border, height * border, width * (1.0 - border), height * (1.0 - border))
    reference_points = _select_landmarks(candidates, (nx, ny), bounds, params.quality_fraction, radius)
    report("features", 1.0, f"Selected {len(reference_points)} landmarks")
    timings["features"] = time.perf_counter() - stage_start

    # -- track
    stage_start = time.perf_counter()
    positions, status = _track_all(
        maps, reference_index, reference_points, radius, step_limit, params.max_step_px, params.min_score, report
    )
    timings["tracking"] = time.perf_counter() - stage_start

    # -- keep only landmarks that were fine at every wavelength
    stage_start = time.perf_counter()
    keep: list[int] = []
    dropped: list[str] = []
    for k in range(len(reference_points)):
        problems = [(tracked[i], status[i][k]) for i in range(len(tracked)) if status[i][k] != "ok"]
        if problems:
            wavelength, why = problems[0]
            dropped.append(f"landmark {k + 1}: {why} at {wavelength:g} nm")
        else:
            keep.append(k)
    if len(keep) < _MIN_LANDMARKS_FOR_FIT:
        raise AutoLandmarkError(
            f"Only {len(keep)} of {len(reference_points)} landmarks could be followed through all wavelengths "
            f"(need at least {_MIN_LANDMARKS_FOR_FIT}). First problems: " + "; ".join(dropped[:3])
        )
    stacked = np.stack([positions[i][keep] for i in range(len(tracked))])
    consistent, inconsistent = _prune_inconsistent(stacked, tracked, reference_index)
    for local, reason in inconsistent:
        dropped.append(f"landmark {keep[local] + 1}: {reason}")
    keep = [keep[local] for local in consistent]
    stacked = stacked[:, consistent]
    loo_mean, loo_max = _fit_quality(stacked, reference_index)
    report("fit", 1.0, "Done")
    timings["fit"] = time.perf_counter() - stage_start

    return AutoLandmarkResult(
        wavelengths_nm=tuple(tracked),
        positions_px=stacked,
        reference_wavelength_nm=reference_nm,
        requested_count=len(reference_points),
        feature_diameter_px=2.0 * radius,
        loo_mean_px=loo_mean,
        loo_max_px=loo_max,
        spread_fraction=(
            float(np.ptp(stacked[reference_index][:, 0])) / width,
            float(np.ptp(stacked[reference_index][:, 1])) / height,
        ),
        dropped=tuple(dropped),
        timings_s=timings,
    )
