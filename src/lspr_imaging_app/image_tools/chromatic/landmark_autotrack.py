"""Automatic landmark detection + tracking across a wavelength sweep.

Finds a handful of trackable points on one image (a Harris corner response,
or a real particle centroid) and follows them wavelength-by-wavelength
through the rest of a sweep, so the user doesn't have to click landmarks on
every single wavelength image by hand - only on the few sample wavelengths
`ChromaticModule.refit()` actually fits a transform at (the rest are
obtained by interpolating those fitted transforms across wavelength; see
that module's docstring for the full workflow). Confirmed a real, actively
used feature (the backend of `gui/analysis_tasks.py`'s auto-detect worker
task on `develop`/`main`), not a scaffold - handle changes here with the
same care as any other production algorithm.

**Deliberately kept "solo standing"** (maintainer's explicit request,
2026-09-21): this file has exactly one real dependency outward -
`roi.detection`/`roi.model`, for the "match against a real detected
particle" refinement `detect_regional_spot_landmarks`/`track_spot_landmarks`
optionally use - and zero dependency on `ChromaticModule` or any other
Image Tools sub-module. `ChromaticModule` is expected to call in through
this file's public functions only (`auto_track_landmarks_over_wavelengths`
is the one actually driven by a worker task today;
`detect_regional_landmarks`/`detect_regional_spot_landmarks`/
`track_landmarks`/`track_spot_landmarks` are its own building blocks, also
exposed directly since a future single-image "detect here" or
single-step "track just this one" UI action may want to call one without
running the whole sweep; `default_landmark_anchors` is a UI preview helper
`gui/chromatic_controller.py` already calls directly on `develop`/`main`,
to show where auto-detection *would* place points before it's run). None
of this file's several private helpers (corner response, patch matching,
subpixel refinement, sector layout, trend-consistency drift correction) are
called from outside it. The intent is that this algorithm can be tuned,
retuned, or replaced later by touching only this one file.

**Two functions from the source this was split out of (`fitting.py`,
2026-09-20/21) were *not* carried over**: `_traceable_landmark_candidates`/
`_select_spread_landmarks`, an alternate landmark-selection strategy
(rank-then-greedily-spread candidate corners) that turns out to be dead
code in the old app too - grepped every call site; nothing in
`gui/`/`processing/` ever calls either one, only `default_landmark_anchors`'
`_landmark_sector_layout`-based per-anchor local search is actually wired
up. Left out to keep this file's surface matching what's genuinely used;
recoverable from `fitting.py`'s git history if ever needed.

Split out of the former single `fitting.py` (2026-09-21, maintainer's
request - see the rewrite build log for the full file-split reasoning).
`affine.py`/`warp.py` hold the separate, much simpler point-fitting/pixel-
warping math this file's output ultimately feeds into.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage, signal

try:
    from skimage.registration import phase_cross_correlation as _phase_cross_correlation
except Exception:  # pragma: no cover - optional acceleration path
    _phase_cross_correlation = None

from ...roi.detection import _masked_gaussian_filter, _refine_roi_center, detect_rois
from ...roi.model import AreaRoi, AreaRoiDetectionSettings


def prepare_registration_image(image: np.ndarray) -> np.ndarray:
    """Turn a raw wavelength image into a registration-friendly feature map.

    Band-pass filters out both fine sensor noise and the slowly-varying
    illumination background (difference of two Gaussians), then takes the
    gradient magnitude so registration matches on *edges/texture* rather than
    absolute intensity -- intensity itself varies a lot between wavelengths
    even at the same physical spot, but edge structure doesn't.
    """
    image_f32 = image.astype(np.float32, copy=False)
    smooth = ndimage.gaussian_filter(image_f32, sigma=1.2, mode="nearest")
    background = ndimage.gaussian_filter(image_f32, sigma=18.0, mode="nearest")
    band = smooth - background
    band -= float(np.median(band))
    scale = float(np.percentile(np.abs(band), 95.0))
    if scale > 1e-6:
        band /= scale
    gx = ndimage.sobel(band, axis=1, mode="nearest")
    gy = ndimage.sobel(band, axis=0, mode="nearest")
    gradient = np.hypot(gx, gy)
    gradient -= float(np.mean(gradient))
    gradient_scale = float(np.std(gradient))
    if gradient_scale > 1e-6:
        gradient /= gradient_scale
    return gradient.astype(np.float32, copy=False)


def detect_regional_landmarks(
    image: np.ndarray,
    feature_count: int,
    *,
    patch_radius_px: int = 10,
    subpixel_precision: int = 1,
    bounds: tuple[int, int, int, int] | None = None,
) -> dict[int, tuple[float, float]]:
    """Find `feature_count` trackable landmark points on the reference image.

    Lays out `feature_count` anchor positions on a roughly-even grid
    (`default_landmark_anchors`, optionally confined to `bounds` -- see
    there), then searches near each anchor for the strongest Harris corner
    response (`_corner_response`) as the actual landmark -- corners/
    junctions are far more reliably re-locatable across wavelengths than a
    flat or edge-only region. Falls back to the raw anchor position if no
    clear corner is found nearby, so every requested feature always gets a
    point. Returns `{feature_id: (x, y)}`.
    """
    prepared = prepare_registration_image(image)
    response = _corner_response(prepared)
    image_height, image_width = prepared.shape[:2]
    anchors = default_landmark_anchors((image_height, image_width), feature_count, bounds=bounds)
    regions = _landmark_regions((image_height, image_width), feature_count, bounds=bounds)
    edge_margin = max(int(patch_radius_px) * 2, 18)
    min_x = edge_margin
    min_y = edge_margin
    max_x = max(image_width - patch_radius_px - 1, min_x)
    max_y = max(image_height - patch_radius_px - 1, min_y)
    detected: dict[int, tuple[float, float]] = {}
    # Scale the search radius from the *bounds* rectangle when one is given,
    # not the full image -- otherwise a small user-chosen search area still
    # lets this primary search reach well outside it (regions/anchors would
    # respect bounds, but this generic radius wouldn't), defeating the point
    # of a custom area on a low-texture image where many candidate positions
    # score similarly.
    scale_dim = float(min(bounds[2], bounds[3])) if bounds is not None and bounds[2] > 0 and bounds[3] > 0 else float(min(image_width, image_height))
    search_radius = max(int(round(scale_dim * 0.09)), int(patch_radius_px) * 2, 14)
    for feature_id, anchor in anchors.items():
        center_x = int(round(anchor[0]))
        center_y = int(round(anchor[1]))
        local_radius = search_radius
        if feature_id == 1:
            local_radius = max(int(round(scale_dim * 0.045)), int(patch_radius_px) * 2, 10)
        x0 = int(max(center_x - local_radius, min_x))
        x1 = int(min(center_x + local_radius + 1, max_x + 1))
        y0 = int(max(center_y - local_radius, min_y))
        y1 = int(min(center_y + local_radius + 1, max_y + 1))
        point: tuple[float, float] | None = None
        if x1 > x0 and y1 > y0:
            local = response[y0:y1, x0:x1]
            if local.size:
                peak_flat = int(np.argmax(local))
                peak_y, peak_x = np.unravel_index(peak_flat, local.shape)
                refined_x, refined_y = _refine_peak_position(
                    local,
                    int(peak_x),
                    int(peak_y),
                    subpixel_precision,
                )
                point = (float(x0 + refined_x), float(y0 + refined_y))
        if point is None:
            region_x0, region_x1, region_y0, region_y1 = regions.get(feature_id, (0, image_width, 0, image_height))
            region_x0 = int(max(region_x0, min_x))
            region_x1 = int(min(region_x1, max_x + 1))
            region_y0 = int(max(region_y0, min_y))
            region_y1 = int(min(region_y1, max_y + 1))
            if region_x1 > region_x0 and region_y1 > region_y0:
                local = response[region_y0:region_y1, region_x0:region_x1]
                if local.size:
                    peak_flat = int(np.argmax(local))
                    peak_y, peak_x = np.unravel_index(peak_flat, local.shape)
                    refined_x, refined_y = _refine_peak_position(
                        local,
                        int(peak_x),
                        int(peak_y),
                        subpixel_precision,
                    )
                    point = (float(region_x0 + refined_x), float(region_y0 + refined_y))
        if point is None:
            point = (
                float(np.clip(anchor[0], min_x, max_x)),
                float(np.clip(anchor[1], min_y, max_y)),
            )
        detected[int(feature_id)] = point
    return detected


def track_landmarks(
    reference_image: np.ndarray,
    target_image: np.ndarray,
    reference_landmarks: dict[int, tuple[float, float]],
    *,
    search_radius_px: int = 28,
    patch_radius_px: int = 10,
    subpixel_precision: int = 1,
    predicted_positions: dict[int, tuple[float, float]] | None = None,
    reference_prepared: np.ndarray | None = None,
    target_prepared: np.ndarray | None = None,
) -> dict[int, tuple[float, float]]:
    """Follow `reference_landmarks` from `reference_image` into `target_image`.

    First estimates one global shift for the whole image via phase
    correlation, then, for each landmark, cuts a small patch around its
    reference position and normalized-cross-correlation-matches it against a
    search window centered on the shift-predicted position in the target
    image (`_match_patch`). Falls back to the plain global-shift prediction
    for any landmark whose patch match fails or scores too low.

    `predicted_positions`, if given, overrides the naive previous-position-
    plus-global-shift guess as the search center for specific landmarks --
    e.g. a smooth per-landmark wavelength trend (see
    `auto_track_landmarks_over_wavelengths`) -- letting the search start from
    a much better prior once one is available, instead of just the last
    tracked position.

    `reference_prepared`/`target_prepared`, if given, are used instead of
    recomputing `prepare_registration_image` on `reference_image`/`target_image`
    -- for a caller stepping through a wavelength sequence where each image
    serves as both a step's target and the next step's reference (see
    `auto_track_landmarks_over_wavelengths`), this avoids redoing the same
    full-image band-pass filter twice per image.
    """
    if not reference_landmarks:
        return {}
    if reference_prepared is None:
        reference_prepared = prepare_registration_image(reference_image)
    if target_prepared is None:
        target_prepared = prepare_registration_image(target_image)
    shift_x, shift_y, _score = multiscale_phase_correlation_shift(reference_prepared, target_prepared)
    image_height, image_width = target_prepared.shape[:2]
    half = max(int(patch_radius_px), 4)
    search_radius = max(int(search_radius_px), 8)
    tracked: dict[int, tuple[float, float]] = {}
    for feature_id, point in reference_landmarks.items():
        override = predicted_positions.get(int(feature_id)) if predicted_positions else None
        guess_x = float(override[0]) if override is not None else point[0] + shift_x
        guess_y = float(override[1]) if override is not None else point[1] + shift_y
        fallback_point = (
            float(np.clip(guess_x, 0.0, image_width - 1.0)),
            float(np.clip(guess_y, 0.0, image_height - 1.0)),
        )
        ref_x = int(round(point[0]))
        ref_y = int(round(point[1]))
        if (
            ref_x - half < 0
            or ref_y - half < 0
            or ref_x + half > reference_prepared.shape[1]
            or ref_y + half > reference_prepared.shape[0]
        ):
            tracked[int(feature_id)] = fallback_point
            continue
        reference_patch = reference_prepared[ref_y - half : ref_y + half, ref_x - half : ref_x + half]
        predicted_x = int(round(guess_x))
        predicted_y = int(round(guess_y))
        search_x0 = max(predicted_x - half - search_radius, 0)
        search_y0 = max(predicted_y - half - search_radius, 0)
        search_x1 = min(predicted_x + half + search_radius, image_width)
        search_y1 = min(predicted_y + half + search_radius, image_height)
        if search_x1 - search_x0 < reference_patch.shape[1] or search_y1 - search_y0 < reference_patch.shape[0]:
            tracked[int(feature_id)] = fallback_point
            continue
        search_area = target_prepared[search_y0:search_y1, search_x0:search_x1]
        peak_x, peak_y, score = _match_patch(
            reference_patch,
            search_area,
            score_threshold=1.2,
            subpixel_precision=subpixel_precision,
        )
        if peak_x is None or peak_y is None or score <= 0.0:
            tracked[int(feature_id)] = fallback_point
            continue
        tracked[int(feature_id)] = (
            float(np.clip(search_x0 + peak_x + half, 0, image_width - 1)),
            float(np.clip(search_y0 + peak_y + half, 0, image_height - 1)),
        )
    return tracked


def detect_regional_spot_landmarks(
    image: np.ndarray,
    feature_count: int,
    *,
    spot_radius_px: float = 10.0,
    spot_mode: str = "dark",
    patch_radius_px: int = 10,
    area_roi_settings: AreaRoiDetectionSettings | None = None,
    bounds: tuple[int, int, int, int] | None = None,
) -> dict[int, tuple[float, float]]:
    """Find `feature_count` trackable landmark points on the reference image,
    like `detect_regional_landmarks`, but each point is a real particle
    centroid instead of a Harris corner.

    If `area_roi_settings` is given, landmarks are seeded from the *full*
    array-aware spot detector used for ROI finding elsewhere in the app
    (`processing.roi_detection.detect_rois`): every real particle in the
    image is ranked and located first, then each anchor's own sector
    (`_landmark_regions`) claims its nearest one, falling back to a local
    circular-contrast search confined to that same sector if it contains no
    detected particle at all -- never a real particle claimed from a
    *different* sector, which would break the spatial spread this
    segmentation exists to guarantee. Without `area_roi_settings`, this goes
    straight to the same local search (`_refine_roi_center`), unconstrained
    to a sector, around each anchor position. `bounds`, if given, confines
    the whole anchor/sector grid to that rectangle -- see
    `_landmark_sector_layout`. Returns `{feature_id: (x, y)}`.
    """
    image_f32 = image.astype(np.float32, copy=False)
    image_height, image_width = image_f32.shape[:2]
    anchors = default_landmark_anchors((image_height, image_width), feature_count, bounds=bounds)

    if area_roi_settings is not None:
        # Zero/near-zero-score candidates (e.g. from a flat, featureless
        # region) aren't genuine particles -- without this filter, one could
        # still win a "nearest to anchor" match over a real, higher-scoring
        # spot that just happens to sit a bit further away.
        detected_rois = [roi for roi in detect_rois(image_f32, area_roi_settings) if roi.score > 0.0]
        # Matching must stay inside each anchor's own sector
        # (`_landmark_regions`, the same grid segmentation
        # `default_landmark_anchors` lays the anchors out on -- e.g. 3x5 for
        # 15 points). A plain "nearest detected spot" match with no region
        # boundary lets a dense, unevenly-distributed cluster of real
        # particles (e.g. only within a cropped/background-heavy area)
        # greedily absorb landmarks that were supposed to sample *other*
        # parts of the frame, defeating the whole point of spreading points
        # out to capture how the aberration varies with position. When a
        # sector genuinely contains no detected particle at all, this falls
        # back to a local search *confined to that sector's own box*
        # (below) rather than reaching into a different sector's territory
        # -- staying spread out matters more here than every point landing
        # on an equally "real" particle.
        regions = _landmark_regions((image_height, image_width), feature_count, bounds=bounds)
        claimed: set[int] = set()
        detected: dict[int, tuple[float, float]] = {}
        local_filtered: np.ndarray | None = None
        local_valid_mask: np.ndarray | None = None
        for feature_id, anchor in anchors.items():
            region = regions.get(feature_id)
            in_region: list[AreaRoi] = []
            if region is not None:
                x0, x1, y0, y1 = region
                in_region = [
                    roi
                    for roi in detected_rois
                    if id(roi) not in claimed and x0 <= roi.center_x < x1 and y0 <= roi.center_y < y1
                ]
            if in_region:
                nearest = min(in_region, key=lambda roi: (roi.center_x - anchor[0]) ** 2 + (roi.center_y - anchor[1]) ** 2)
                detected[int(feature_id)] = (float(nearest.center_x), float(nearest.center_y))
                claimed.add(id(nearest))
                continue
            if local_filtered is None:
                local_valid_mask = np.ones((image_height, image_width), dtype=bool)
                sigma = max(float(spot_radius_px) / 2.5, 1.0)
                local_filtered, _support = _masked_gaussian_filter(image_f32, local_valid_mask, sigma=sigma)
            x0, x1, y0, y1 = region if region is not None else (0, image_width, 0, image_height)
            seed_x = float(np.clip(anchor[0], x0, max(x1 - 1, x0)))
            seed_y = float(np.clip(anchor[1], y0, max(y1 - 1, y0)))
            region_search_radius = max((x1 - x0) / 2.0, (y1 - y0) / 2.0, float(spot_radius_px))
            refined_x, refined_y, _score, found = _refine_roi_center(
                filtered=local_filtered,
                valid_mask=local_valid_mask,
                seed_x=seed_x,
                seed_y=seed_y,
                radius=float(spot_radius_px),
                mode=spot_mode,
                search_radius=region_search_radius,
            )
            if not found:
                refined_x, refined_y = seed_x, seed_y
            detected[int(feature_id)] = (float(refined_x), float(refined_y))
        return detected

    valid_mask = np.ones((image_height, image_width), dtype=bool)
    sigma = max(float(spot_radius_px) / 2.5, 1.0)
    filtered, _support = _masked_gaussian_filter(image_f32, valid_mask, sigma=sigma)
    edge_margin = max(int(patch_radius_px) * 2, 18)
    min_x, min_y = edge_margin, edge_margin
    max_x = max(image_width - edge_margin - 1, min_x)
    max_y = max(image_height - edge_margin - 1, min_y)
    search_radius = max(float(spot_radius_px) * 2.5, 14.0)
    detected = {}
    for feature_id, anchor in anchors.items():
        seed_x = float(np.clip(anchor[0], min_x, max_x))
        seed_y = float(np.clip(anchor[1], min_y, max_y))
        refined_x, refined_y, _score, found = _refine_roi_center(
            filtered=filtered,
            valid_mask=valid_mask,
            seed_x=seed_x,
            seed_y=seed_y,
            radius=float(spot_radius_px),
            mode=spot_mode,
            search_radius=search_radius,
        )
        if not found:
            refined_x, refined_y = seed_x, seed_y
        detected[int(feature_id)] = (float(refined_x), float(refined_y))
    return detected


def track_spot_landmarks(
    reference_image: np.ndarray,
    target_image: np.ndarray,
    reference_landmarks: dict[int, tuple[float, float]],
    *,
    spot_radius_px: float = 10.0,
    spot_mode: str = "dark",
    search_radius_px: int = 12,
    predicted_positions: dict[int, tuple[float, float]] | None = None,
    area_roi_settings: AreaRoiDetectionSettings | None = None,
    reference_prepared: np.ndarray | None = None,
    target_prepared: np.ndarray | None = None,
) -> dict[int, tuple[float, float]]:
    """Follow `reference_landmarks` from `reference_image` into `target_image`
    by re-locating each one's spot centroid, instead of `track_landmarks`'s
    small-patch cross-correlation.

    First estimates one global shift for the whole image via phase
    correlation (same as `track_landmarks`), then locates each landmark near
    the shift-predicted position (or `predicted_positions`' override, if
    given -- see `track_landmarks`).

    If `area_roi_settings` is given, that location step matches each landmark
    to its nearest result from the full array-aware spot detector
    (`detect_rois`) -- the same upgrade `detect_regional_spot_landmarks`
    makes for the starting frame, applied here to every tracking step. This
    is slower (`detect_rois` scans the whole image, once per call) but far
    more reliable: a local circular-contrast search (the fallback below) can
    lock onto background texture or a stray pixel cluster that merely looks
    spot-like, while `detect_rois`'s ranked scoring and non-max suppression
    only returns genuine, already-validated particles. Falls back to the
    local search if no settings are given, and to the search center itself
    if no particle is found within `search_radius_px`.

    `reference_prepared`/`target_prepared`, if given, are used instead of
    recomputing `prepare_registration_image` for the global-shift step -- see
    `track_landmarks`.
    """
    if not reference_landmarks:
        return {}
    if reference_prepared is None:
        reference_prepared = prepare_registration_image(reference_image)
    if target_prepared is None:
        target_prepared = prepare_registration_image(target_image)
    shift_x, shift_y, _score = multiscale_phase_correlation_shift(reference_prepared, target_prepared)
    target_f32 = target_image.astype(np.float32, copy=False)
    image_height, image_width = target_f32.shape[:2]
    search_radius = max(float(search_radius_px), float(spot_radius_px) * 1.2)

    def predicted_position(feature_id: int, point: tuple[float, float]) -> tuple[float, float]:
        override = predicted_positions.get(int(feature_id)) if predicted_positions else None
        source = override if override is not None else (point[0] + shift_x, point[1] + shift_y)
        return (
            float(np.clip(source[0], 0.0, image_width - 1.0)),
            float(np.clip(source[1], 0.0, image_height - 1.0)),
        )

    tracked: dict[int, tuple[float, float]] = {}

    if area_roi_settings is not None:
        # See detect_regional_spot_landmarks: zero/near-zero-score candidates
        # aren't genuine particles and shouldn't win a nearest-position match.
        detected_rois = [roi for roi in detect_rois(target_f32, area_roi_settings) if roi.score > 0.0]
        if detected_rois:
            for feature_id, point in reference_landmarks.items():
                predicted_x, predicted_y = predicted_position(feature_id, point)
                nearest = min(
                    detected_rois,
                    key=lambda roi: (roi.center_x - predicted_x) ** 2 + (roi.center_y - predicted_y) ** 2,
                )
                distance = float(np.hypot(nearest.center_x - predicted_x, nearest.center_y - predicted_y))
                if distance <= search_radius:
                    tracked[int(feature_id)] = (float(nearest.center_x), float(nearest.center_y))
                else:
                    tracked[int(feature_id)] = (predicted_x, predicted_y)
            return tracked

    valid_mask = np.ones((image_height, image_width), dtype=bool)
    sigma = max(float(spot_radius_px) / 2.5, 1.0)
    filtered, _support = _masked_gaussian_filter(target_f32, valid_mask, sigma=sigma)
    for feature_id, point in reference_landmarks.items():
        predicted_x, predicted_y = predicted_position(feature_id, point)
        refined_x, refined_y, _score, found = _refine_roi_center(
            filtered=filtered,
            valid_mask=valid_mask,
            seed_x=predicted_x,
            seed_y=predicted_y,
            radius=float(spot_radius_px),
            mode=spot_mode,
            search_radius=search_radius,
        )
        if found:
            tracked[int(feature_id)] = (float(refined_x), float(refined_y))
        else:
            tracked[int(feature_id)] = (predicted_x, predicted_y)
    return tracked


def _predict_trend_position(
    history: list[tuple[float, float, float]],
    target_wavelength: float,
) -> tuple[float, float] | None:
    """Extrapolate a landmark's next position from its own recent trajectory.

    Chromatic aberration displacement is a smooth, slowly-varying function of
    wavelength, but the fit here is deliberately kept *linear* (constant
    local velocity) through the last up-to-4 already-accepted
    `(wavelength, x, y)` points, never quadratic-or-higher: whenever a raw
    tracked match gets rejected (see `_choose_trend_consistent_position`),
    the prediction itself becomes the next history point, so this function
    ends up extrapolating its own prior output. A quadratic fit re-applied to
    its own output that way compounds curvature error and can diverge
    (observed in practice as a slow-looking but runaway drift); a linear fit
    can't runaway the same way, and neighboring sampled wavelengths are close
    enough together that "smooth" and "locally linear" are practically the
    same assumption. Wavelengths are centered on the most recent point before
    fitting purely for numerical conditioning (their raw values, e.g.
    400-700 nm, are otherwise a poorly-scaled basis for a polynomial fit).

    A hard cap keeps the result within 10x the landmark's own recent step
    scale of its last position, as a second line of defense regardless of
    what the fit produces. Returns `None` with fewer than 2 points -- there's
    no trend yet to predict from.
    """
    if len(history) < 2:
        return None
    recent = history[-4:]
    wavelengths = np.asarray([point[0] for point in recent], dtype=np.float64)
    xs = np.asarray([point[1] for point in recent], dtype=np.float64)
    ys = np.asarray([point[2] for point in recent], dtype=np.float64)
    origin = wavelengths[-1]
    try:
        coeffs_x = np.polyfit(wavelengths - origin, xs, 1)
        coeffs_y = np.polyfit(wavelengths - origin, ys, 1)
    except (np.linalg.LinAlgError, ValueError):
        return None
    offset = target_wavelength - origin
    predicted_x = float(np.polyval(coeffs_x, offset))
    predicted_y = float(np.polyval(coeffs_y, offset))
    last_x, last_y = float(xs[-1]), float(ys[-1])
    step = float(np.hypot(predicted_x - last_x, predicted_y - last_y))
    limit = max(_recent_step_scale(history) * 10.0, 15.0)
    if step > limit > 0.0:
        scale = limit / step
        predicted_x = last_x + (predicted_x - last_x) * scale
        predicted_y = last_y + (predicted_y - last_y) * scale
    return predicted_x, predicted_y


def _recent_step_scale(history: list[tuple[float, float, float]]) -> float:
    """Median size (px) of the landmark's last few accepted steps -- the
    local scale used to judge whether a new candidate position is "close to
    the trend" or an outlier jump."""
    if len(history) < 2:
        return float("inf")
    steps = [
        float(np.hypot(next_point[1] - point[1], next_point[2] - point[2]))
        for point, next_point in zip(history[:-1], history[1:])
    ]
    return float(np.median(steps[-3:])) if steps else float("inf")


def _choose_trend_consistent_position(
    candidates: list[tuple[float, float]],
    predicted: tuple[float, float] | None,
    history: list[tuple[float, float, float]],
) -> tuple[float, float]:
    """Pick the best of one or more raw tracked candidates for a landmark's
    next position, using the smooth-trend prediction (`_predict_trend_position`)
    as a sanity check.

    With no trend yet (fewer than 2 prior points), there's nothing to
    validate against, so multiple simultaneous candidates (e.g. "both"
    landmark-kind mode) are simply averaged. Once a trend exists, the
    candidate closest to the prediction wins -- and if even the closest one
    is still far outside the landmark's own recent step scale, it's rejected
    outright in favor of the prediction itself. This is what stops one bad
    match (a mis-track onto the wrong spot/corner) from permanently dragging
    every wavelength after it off course, since the corrected position --
    not the bad raw match -- becomes the anchor for the next tracking step.
    """
    if not candidates:
        if predicted is not None:
            return predicted
        last = history[-1]
        return last[1], last[2]
    if predicted is None:
        return (
            float(np.mean([point[0] for point in candidates])),
            float(np.mean([point[1] for point in candidates])),
        )
    best = min(candidates, key=lambda point: np.hypot(point[0] - predicted[0], point[1] - predicted[1]))
    deviation = float(np.hypot(best[0] - predicted[0], best[1] - predicted[1]))
    tolerance = max(_recent_step_scale(history) * 5.0, 6.0)
    return best if deviation <= tolerance else predicted


def auto_track_landmarks_over_wavelengths(
    images_by_wavelength: list[tuple[float, np.ndarray]],
    feature_count: int,
    *,
    kind: str = "corner",
    spot_radius_px: float = 10.0,
    spot_mode: str = "dark",
    search_radius_px: int = 28,
    patch_radius_px: int = 10,
    subpixel_precision: int = 1,
    area_roi_settings: AreaRoiDetectionSettings | None = None,
    bounds: tuple[int, int, int, int] | None = None,
    progress_callback=None,
) -> dict[int, dict[float, tuple[float, float]]]:
    """Detect landmarks on the shortest-wavelength sampled image, then track
    them wavelength by wavelength through the rest of the sweep.

    `kind` selects what's tracked: `"corner"` (Harris-corner patch matching,
    `track_landmarks` -- precise but blur-sensitive), `"centroid"` (particle
    spot centroids, `track_spot_landmarks` -- coarser but far more robust to
    the focus blur that grows toward longer wavelengths), or `"both"` (try
    both trackers and let the smooth-trend consistency check
    (`_choose_trend_consistent_position`) pick between them at every step -
    the mode actually used in practice today).

    For `"centroid"`/`"both"`, `area_roi_settings` (if given) seeds the first
    image's landmarks from the full array-aware spot detector instead of a
    local search around a blind grid anchor -- see
    `detect_regional_spot_landmarks`. This only affects the *starting*
    position; per-step tracking stays on the cheap local search, now with a
    real particle to follow instead of a possibly-arbitrary starting point.
    `bounds` (`(x, y, width, height)` in image pixel space, e.g. a user-
    dragged overlay rectangle) confines where that starting grid of anchors
    is laid out in the first place -- see `_landmark_sector_layout`.

    Every step's chosen position is validated against a smooth per-landmark
    trend fit to its own trajectory so far, and replaced by the trend's own
    prediction if it deviates too far -- see `_choose_trend_consistent_position`.
    This also means tracking always steps wavelength-to-wavelength (never
    reference-to-target directly): real chromatic displacement grows with
    distance from the reference, so neighboring wavelengths are both the
    easiest step to track *and* the right scale to build the trend from.

    Returns `{feature_id: {wavelength: (x, y)}}`.
    """
    if not images_by_wavelength:
        return {}
    ordered = sorted(images_by_wavelength, key=lambda item: item[0])
    first_wavelength, first_image = ordered[0]

    if kind in ("centroid", "both"):
        current = detect_regional_spot_landmarks(
            first_image,
            feature_count,
            spot_radius_px=spot_radius_px,
            spot_mode=spot_mode,
            patch_radius_px=patch_radius_px,
            area_roi_settings=area_roi_settings,
            bounds=bounds,
        )
    else:
        current = detect_regional_landmarks(
            first_image,
            feature_count,
            patch_radius_px=patch_radius_px,
            subpixel_precision=subpixel_precision,
            bounds=bounds,
        )

    trajectories: dict[int, list[tuple[float, float, float]]] = {
        feature_id: [(first_wavelength, point[0], point[1])] for feature_id, point in current.items()
    }

    previous_image = first_image
    # Computed once per sampled image and carried over as next step's
    # reference -- each image serves as one step's target and the next
    # step's reference, so without this every image's full-image band-pass
    # filter (prepare_registration_image, the priciest part of a tracking
    # step) would otherwise be recomputed twice, and up to four times when
    # kind="both" runs both trackers against the same pair.
    previous_prepared = prepare_registration_image(first_image)
    total_steps = max(len(ordered) - 1, 1)
    for step_index, (wavelength, image) in enumerate(ordered[1:], start=1):
        current_prepared = prepare_registration_image(image)
        predicted_by_feature: dict[int, tuple[float, float]] = {}
        for feature_id, history in trajectories.items():
            predicted = _predict_trend_position(history, wavelength)
            if predicted is not None:
                predicted_by_feature[feature_id] = predicted

        candidates_by_feature: dict[int, list[tuple[float, float]]] = {feature_id: [] for feature_id in current}
        if kind in ("corner", "both"):
            for feature_id, point in track_landmarks(
                previous_image,
                image,
                current,
                search_radius_px=search_radius_px,
                patch_radius_px=patch_radius_px,
                subpixel_precision=subpixel_precision,
                predicted_positions=predicted_by_feature,
                reference_prepared=previous_prepared,
                target_prepared=current_prepared,
            ).items():
                candidates_by_feature[feature_id].append(point)
        if kind in ("centroid", "both"):
            for feature_id, point in track_spot_landmarks(
                previous_image,
                image,
                current,
                spot_radius_px=spot_radius_px,
                spot_mode=spot_mode,
                predicted_positions=predicted_by_feature,
                area_roi_settings=area_roi_settings,
                reference_prepared=previous_prepared,
                target_prepared=current_prepared,
            ).items():
                candidates_by_feature[feature_id].append(point)

        next_points: dict[int, tuple[float, float]] = {}
        for feature_id, history in trajectories.items():
            predicted = predicted_by_feature.get(feature_id)
            chosen = _choose_trend_consistent_position(candidates_by_feature.get(feature_id, []), predicted, history)
            next_points[feature_id] = chosen
            history.append((wavelength, chosen[0], chosen[1]))
        current = next_points
        previous_prepared = current_prepared
        previous_image = image
        if progress_callback is not None:
            progress_callback(step_index, total_steps)

    return {
        feature_id: {wavelength: (x, y) for wavelength, x, y in points}
        for feature_id, points in trajectories.items()
    }


def phase_correlation_shift(reference_image: np.ndarray, target_image: np.ndarray) -> tuple[float, float, float]:
    """Estimate the whole-image (x, y) translation that best aligns the two images.

    Classic FFT phase correlation (Kuglin, C. D. & Hines, D. C. "The phase
    correlation image alignment method." Proc. IEEE Conf. Cybernetics and
    Society, 1975): the cross-power spectrum of the two images' FFTs, with
    magnitude normalized to 1 at every frequency, has an inverse FFT that is
    a sharp peak located exactly at the true shift -- unlike a raw
    cross-correlation, whose peak width depends on image content. Only
    accurate to whole pixels; `multiscale_phase_correlation_shift` adds
    subpixel precision. Returns `(shift_x, shift_y, peak_sharpness)`.
    """
    reference = reference_image.astype(np.float32, copy=False)
    target = target_image.astype(np.float32, copy=False)
    eps = 1e-8
    axes = tuple(range(reference.ndim))
    reference_fft = np.fft.rfftn(reference, axes=axes)
    target_fft = np.fft.rfftn(target, axes=axes)
    cross_power = reference_fft * np.conj(target_fft)
    cross_power /= np.maximum(np.abs(cross_power), eps)
    correlation = np.fft.irfftn(cross_power, s=reference.shape, axes=axes)
    correlation_abs = np.abs(correlation)
    peak_index = np.unravel_index(int(np.argmax(correlation_abs)), correlation_abs.shape)
    shift_y = float(peak_index[0])
    shift_x = float(peak_index[1])
    if shift_y > reference.shape[0] / 2.0:
        shift_y -= float(reference.shape[0])
    if shift_x > reference.shape[1] / 2.0:
        shift_x -= float(reference.shape[1])
    peak_score = float(correlation_abs[peak_index])
    return -shift_x, -shift_y, peak_score


def multiscale_phase_correlation_shift(reference_image: np.ndarray, target_image: np.ndarray) -> tuple[float, float, float]:
    """Subpixel-accurate version of `phase_correlation_shift`.

    Prefers `skimage.registration.phase_cross_correlation`'s upsampled
    cross-power-spectrum method (Guizar-Sicairos, M., Thurman, S. T. &
    Fienup, J. R. "Efficient subpixel image registration algorithms."
    Opt. Lett. 33, 156-158, 2008), which gets subpixel precision without
    ever computing a full upsampled FFT. If scikit-image's registration
    module isn't installed, falls back to a manual coarse-then-fine scheme:
    a whole-pixel shift on a 2x-downsampled pair, then a second whole-pixel
    correction at full resolution after pre-shifting by the coarse estimate.
    """
    if _phase_cross_correlation is not None:
        shift_rc, error, _diffphase = _phase_cross_correlation(
            reference_image.astype(np.float32, copy=False),
            target_image.astype(np.float32, copy=False),
            upsample_factor=10,
        )
        shift_y = float(shift_rc[0])
        shift_x = float(shift_rc[1])
        peak_score = float(max(0.0, 1.0 - error))
        return -shift_x, -shift_y, peak_score
    downsample_factor = 0.5
    reference_small = ndimage.zoom(reference_image, downsample_factor, order=1)
    target_small = ndimage.zoom(target_image, downsample_factor, order=1)
    coarse_x, coarse_y, coarse_score = phase_correlation_shift(reference_small, target_small)
    coarse_x /= downsample_factor
    coarse_y /= downsample_factor
    refined_target = ndimage.shift(target_image, shift=(-coarse_y, -coarse_x), order=1, mode="nearest")
    fine_x, fine_y, fine_score = phase_correlation_shift(reference_image, refined_target)
    return coarse_x + fine_x, coarse_y + fine_y, max(coarse_score, fine_score)


def _corner_response(image: np.ndarray) -> np.ndarray:
    """Harris corner response (Harris, C. & Stephens, M. "A Combined Corner
    and Edge Detector." Proc. 4th Alvey Vision Conference, 1988):
    `det(structure_tensor) - k * trace(structure_tensor)**2` with the
    textbook `k = 0.04`, computed from Gaussian-smoothed products of the
    Sobel gradients. High values mark corners/junctions -- points whose
    local neighborhood looks different when shifted in *any* direction,
    which is exactly what makes a point reliably re-locatable in another
    wavelength's image. Boosted by local gradient magnitude so corners in
    higher-contrast regions win out over comparably-cornery but faint ones.
    """
    image_f32 = image.astype(np.float32, copy=False)
    gx = ndimage.sobel(image_f32, axis=1, mode="nearest")
    gy = ndimage.sobel(image_f32, axis=0, mode="nearest")
    a = ndimage.gaussian_filter(gx * gx, sigma=1.4, mode="nearest")
    b = ndimage.gaussian_filter(gx * gy, sigma=1.4, mode="nearest")
    c = ndimage.gaussian_filter(gy * gy, sigma=1.4, mode="nearest")
    trace = a + c
    determinant = a * c - b * b
    response = determinant - 0.04 * trace * trace
    response = np.maximum(response, 0.0)
    gradient = np.hypot(gx, gy)
    scale = float(np.percentile(gradient, 95.0))
    if scale > 1e-6:
        gradient /= scale
    return (response * (1.0 + gradient)).astype(np.float32, copy=False)


def default_landmark_anchors(
    image_shape: tuple[int, int],
    feature_count: int,
    *,
    bounds: tuple[int, int, int, int] | None = None,
) -> dict[int, tuple[float, float]]:
    centers, _regions = _landmark_sector_layout(image_shape, feature_count, bounds=bounds)
    return centers


def _landmark_regions(
    image_shape: tuple[int, int],
    feature_count: int,
    *,
    bounds: tuple[int, int, int, int] | None = None,
) -> dict[int, tuple[int, int, int, int]]:
    _anchors, regions = _landmark_sector_layout(image_shape, feature_count, bounds=bounds)
    return regions


def _landmark_sector_layout(
    image_shape: tuple[int, int],
    feature_count: int,
    *,
    bounds: tuple[int, int, int, int] | None = None,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[int, int, int, int]]]:
    """Lay out `feature_count` anchor points (and a region box per anchor,
    used to keep matched candidates spread out -- see
    `detect_regional_spot_landmarks`) on an evenly-spaced grid sized/oriented
    from the feature count (5->2x3, 15->3x5, 30->5x6; other counts pick a
    near-square grid).

    By default the grid spans nearly the whole image, inset by a small
    automatic margin. If `bounds` is given -- `(x, y, width, height)` in
    image pixel space, e.g. a user-dragged overlay rectangle -- the grid is
    confined to that rectangle instead, with a much smaller margin: the
    point of a user-chosen area is to already exclude whatever shouldn't be
    searched (a rotated image's blank corners, for instance), so there's no
    need to additionally eat into it with the large default inset.
    """
    image_height, image_width = image_shape[:2]
    max_features = 30
    count = max(1, min(int(feature_count), max_features))

    if bounds is not None and bounds[2] > 0 and bounds[3] > 0:
        origin_x = float(np.clip(bounds[0], 0, max(image_width - 1, 0)))
        origin_y = float(np.clip(bounds[1], 0, max(image_height - 1, 0)))
        extent_w = float(np.clip(bounds[2], 1, max(image_width - origin_x, 1)))
        extent_h = float(np.clip(bounds[3], 1, max(image_height - origin_y, 1)))
        min_dim = float(min(extent_w, extent_h))
        edge_margin = max(min_dim * 0.04, 6.0)
    else:
        origin_x, origin_y = 0.0, 0.0
        extent_w, extent_h = float(image_width), float(image_height)
        min_dim = float(min(image_width, image_height))
        edge_margin = max(min_dim * 0.06, 14.0)

    mid_x = origin_x + (extent_w - 1) * 0.5
    mid_y = origin_y + (extent_h - 1) * 0.5
    aspect = extent_w / max(extent_h, 1.0)
    if count == 5:
        grid_rows, grid_cols = ((2, 3) if aspect >= 1.0 else (3, 2))
    elif count == 15:
        grid_rows, grid_cols = ((3, 5) if aspect >= 1.0 else (5, 3))
    elif count == 30:
        grid_rows, grid_cols = ((5, 6) if aspect >= 1.0 else (6, 5))
    else:
        grid_cols = max(2, min(8, int(round(np.sqrt(max(count, 1) * max(aspect, 0.5))))))
        grid_rows = max(1, int(np.ceil(count / max(grid_cols, 1))))

    if grid_rows * grid_cols < count:
        grid_rows = int(np.ceil(count / max(grid_cols, 1)))

    x_coords = np.linspace(origin_x + edge_margin, origin_x + extent_w - 1 - edge_margin, num=max(grid_cols, 1), dtype=np.float64)
    y_coords = np.linspace(origin_y + edge_margin, origin_y + extent_h - 1 - edge_margin, num=max(grid_rows, 1), dtype=np.float64)
    candidates = [(float(x), float(y)) for y in y_coords for x in x_coords]
    if len(candidates) > count:
        selected = _select_evenly_spread_points(candidates, count, mid_x, mid_y)
    else:
        selected = candidates[:count]
    selected.sort(key=lambda point: (point[1], point[0]))

    anchors: dict[int, tuple[float, float]] = {}
    regions: dict[int, tuple[int, int, int, int]] = {}
    region_half_width = max(int(round(min_dim * 0.12)), 24)
    region_half_height = region_half_width
    for feature_id, point in enumerate(selected, start=1):
        x, y = point
        anchors[feature_id] = (float(x), float(y))
        x0 = max(int(round(x - region_half_width)), 0)
        x1 = min(int(round(x + region_half_width)) + 1, image_width)
        y0 = max(int(round(y - region_half_height)), 0)
        y1 = min(int(round(y + region_half_height)) + 1, image_height)
        regions[feature_id] = (x0, x1, y0, y1)
    return anchors, regions


def _select_evenly_spread_points(
    candidates: list[tuple[float, float]],
    count: int,
    center_x: float,
    center_y: float,
) -> list[tuple[float, float]]:
    if count <= 0 or not candidates:
        return []
    remaining = [tuple(point) for point in candidates]
    selected: list[tuple[float, float]] = []
    first_index = min(
        range(len(remaining)),
        key=lambda index: (remaining[index][0] - center_x) ** 2 + (remaining[index][1] - center_y) ** 2,
    )
    selected.append(remaining.pop(int(first_index)))
    while remaining and len(selected) < count:
        next_index = max(
            range(len(remaining)),
            key=lambda index: min(
                (remaining[index][0] - sel_x) ** 2 + (remaining[index][1] - sel_y) ** 2
                for sel_x, sel_y in selected
            ),
        )
        selected.append(remaining.pop(int(next_index)))
    return selected[:count]


def _match_patch(
    reference_patch: np.ndarray,
    search_area: np.ndarray,
    *,
    score_threshold: float,
    subpixel_precision: int = 1,
) -> tuple[float | None, float | None, float]:
    """Locate `reference_patch` inside `search_area` by normalized cross-correlation.

    Both patch and search area are zero-mean normalized first (the patch is
    also unit-variance normalized), then correlated via FFT-based
    convolution (`scipy.signal.fftconvolve`) rather than a direct sliding-
    window sum, which is far cheaper for the tile sizes used here. The
    result is z-scored against its own mean/std so `score_threshold` means
    "how many standard deviations above the background correlation level"
    rather than a raw, scale-dependent number. Returns `(None, None, score)`
    if the patch is flat (nothing to match) or the best match doesn't clear
    `score_threshold`.
    """
    patch = reference_patch.astype(np.float32, copy=False)
    search = search_area.astype(np.float32, copy=False)
    patch -= float(np.mean(patch))
    patch_std = float(np.std(patch))
    if patch_std < 1e-6:
        return None, None, 0.0
    patch /= patch_std
    search = search - float(np.mean(search))
    correlation = signal.fftconvolve(search, patch[::-1, ::-1], mode="valid")
    if correlation.size == 0:
        return None, None, 0.0
    peak_flat = int(np.argmax(correlation))
    peak_y, peak_x = np.unravel_index(peak_flat, correlation.shape)
    score = float((correlation[peak_y, peak_x] - np.mean(correlation)) / (np.std(correlation) + 1e-6))
    if score < float(score_threshold):
        return None, None, score
    refined_x, refined_y = _refine_peak_position(correlation, int(peak_x), int(peak_y), subpixel_precision)
    return float(refined_x), float(refined_y), score


def _normalized_subpixel_precision(subpixel_precision: int) -> int:
    value = int(subpixel_precision)
    if value <= 1:
        return 1
    if value <= 4:
        return 4
    return 9


def _subpixel_refinement_radius(subpixel_precision: int) -> int:
    normalized = _normalized_subpixel_precision(subpixel_precision)
    if normalized <= 1:
        return 0
    if normalized <= 4:
        return 1
    return 2


def _refine_peak_position(
    surface: np.ndarray,
    peak_x: int,
    peak_y: int,
    subpixel_precision: int,
) -> tuple[float, float]:
    """Refine an integer-pixel correlation peak to subpixel precision.

    Fits a 2D quadratic surface (`a*x^2 + b*y^2 + c*xy + d*x + e*y + f`) to a
    small neighborhood around the discrete peak by least squares, then
    solves for that quadratic's analytic maximum via its gradient/Hessian --
    standard parabolic-interpolation subpixel peak finding, the same idea
    used for subpixel PIV (particle image velocimetry) peak fitting. Falls
    back to the integer peak position if the neighborhood runs off the edge
    of `surface` or the fit is degenerate.
    """
    radius = _subpixel_refinement_radius(subpixel_precision)
    if radius <= 0:
        return float(peak_x), float(peak_y)
    if (
        peak_x - radius < 0
        or peak_y - radius < 0
        or peak_x + radius >= surface.shape[1]
        or peak_y + radius >= surface.shape[0]
    ):
        return float(peak_x), float(peak_y)

    local = surface[peak_y - radius : peak_y + radius + 1, peak_x - radius : peak_x + radius + 1].astype(np.float64, copy=False)
    yy, xx = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    design = np.column_stack(
        [
            (xx**2).ravel(),
            (yy**2).ravel(),
            (xx * yy).ravel(),
            xx.ravel(),
            yy.ravel(),
            np.ones(xx.size, dtype=np.float64),
        ]
    )
    try:
        coeffs, *_ = np.linalg.lstsq(design, local.ravel(), rcond=None)
    except np.linalg.LinAlgError:
        return float(peak_x), float(peak_y)
    if coeffs.size != 6 or not np.all(np.isfinite(coeffs)):
        return float(peak_x), float(peak_y)
    a, b, c, d, e, _f = [float(value) for value in coeffs]
    hessian = np.array([[2.0 * a, c], [c, 2.0 * b]], dtype=np.float64)
    gradient = np.array([-d, -e], dtype=np.float64)
    det = float(np.linalg.det(hessian))
    if not np.isfinite(det) or abs(det) < 1e-9:
        return float(peak_x), float(peak_y)
    try:
        offset_x, offset_y = np.linalg.solve(hessian, gradient)
    except np.linalg.LinAlgError:
        return float(peak_x), float(peak_y)
    if not np.all(np.isfinite([offset_x, offset_y])):
        return float(peak_x), float(peak_y)
    limit = float(radius) + 0.5
    offset_x = float(np.clip(offset_x, -limit, limit))
    offset_y = float(np.clip(offset_y, -limit, limit))
    return float(peak_x + offset_x), float(peak_y + offset_y)
