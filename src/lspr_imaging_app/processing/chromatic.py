"""Per-wavelength chromatic-aberration registration.

Each wavelength plane is geometrically registered against a reference plane
(the plane a wavelength's own affine transform should map *onto*) so that a
pixel at a given (x, y) in the reference corresponds to the same physical
sample location in every other wavelength. The pipeline combines three
standard building blocks rather than one named published algorithm:

1. A global translation estimate via FFT phase correlation
   (Kuglin, C. D. & Hines, D. C. "The phase correlation image alignment
   method." Proc. IEEE Conf. Cybernetics and Society, 1975), refined with
   `skimage.registration.phase_cross_correlation`'s upsampled cross-power
   spectrum when available (Guizar-Sicairos, M., Thurman, S. T. & Fienup,
   J. R. "Efficient subpixel image registration algorithms." Opt. Lett. 33,
   156-158, 2008).
2. Local tie points found by normalized cross-correlation template matching
   on a grid of tiles, searched near the position the global shift predicts.
3. A full affine (or similarity) transform fit through those tie points,
   with a small iterative outlier-rejection loop (refit, drop the
   highest-residual points, refit again) in place of a full RANSAC.

`estimate_affine_chromatic_transform` runs the whole pipeline;
`detect_regional_landmarks`/`track_landmarks` instead locate and follow a
small set of user-visible feature points (for the manual/landmark
correction path) using a Harris corner response
(Harris, C. & Stephens, M. "A Combined Corner and Edge Detector." Proc. 4th
Alvey Vision Conference, 1988) in place of template matching.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

try:
    from skimage.registration import phase_cross_correlation as _phase_cross_correlation
except Exception:  # pragma: no cover - optional acceleration path
    _phase_cross_correlation = None

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings
from lspr_imaging_app.processing.spot_detection import _masked_gaussian_filter, _refine_spot_center, detect_spots


@dataclass(slots=True)
class ChromaticRegistrationResult:
    affine_matrix: np.ndarray
    global_shift_x_px: float
    global_shift_y_px: float
    rmse_px: float
    mean_score: float
    min_score: float
    tile_count: int
    inlier_count: int


def identity_affine_matrix() -> np.ndarray:
    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)


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
    """
    if not reference_landmarks:
        return {}
    reference_prepared = prepare_registration_image(reference_image)
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
            half,
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
    (`processing.spot_detection.detect_spots`): every real particle in the
    image is ranked and located first, then each anchor's own sector
    (`_landmark_regions`) claims its nearest one, falling back to a local
    circular-contrast search confined to that same sector if it contains no
    detected particle at all -- never a real particle claimed from a
    *different* sector, which would break the spatial spread this
    segmentation exists to guarantee. Without `area_roi_settings`, this goes
    straight to the same local search (`_refine_spot_center`), unconstrained
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
        detected_rois = [roi for roi in detect_spots(image_f32, area_roi_settings) if roi.score > 0.0]
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
            refined_x, refined_y, _score, found = _refine_spot_center(
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
        refined_x, refined_y, _score, found = _refine_spot_center(
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
    (`detect_spots`) -- the same upgrade `detect_regional_spot_landmarks`
    makes for the starting frame, applied here to every tracking step. This
    is slower (`detect_spots` scans the whole image, once per call) but far
    more reliable: a local circular-contrast search (the fallback below) can
    lock onto background texture or a stray pixel cluster that merely looks
    spot-like, while `detect_spots`'s ranked scoring and non-max suppression
    only returns genuine, already-validated particles. Falls back to the
    local search if no settings are given, and to the search center itself
    if no particle is found within `search_radius_px`.
    """
    if not reference_landmarks:
        return {}
    reference_prepared = prepare_registration_image(reference_image)
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
        detected_rois = [roi for roi in detect_spots(target_f32, area_roi_settings) if roi.score > 0.0]
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
        refined_x, refined_y, _score, found = _refine_spot_center(
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
    (`_choose_trend_consistent_position`) pick between them at every step).

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
    total_steps = max(len(ordered) - 1, 1)
    for step_index, (wavelength, image) in enumerate(ordered[1:], start=1):
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
            ).items():
                candidates_by_feature[feature_id].append(point)

        next_points: dict[int, tuple[float, float]] = {}
        for feature_id, history in trajectories.items():
            predicted = predicted_by_feature.get(feature_id)
            chosen = _choose_trend_consistent_position(candidates_by_feature.get(feature_id, []), predicted, history)
            next_points[feature_id] = chosen
            history.append((wavelength, chosen[0], chosen[1]))
        current = next_points
        previous_image = image
        if progress_callback is not None:
            progress_callback(step_index, total_steps)

    return {
        feature_id: {wavelength: (x, y) for wavelength, x, y in points}
        for feature_id, points in trajectories.items()
    }


def estimate_affine_chromatic_transform(
    reference_image: np.ndarray,
    target_image: np.ndarray,
    *,
    mode: str = "fast",
    tile_size_px: int = 96,
    search_radius_px: int = 24,
    spacing_px: int | None = None,
    subpixel_precision: int = 1,
) -> ChromaticRegistrationResult:
    """Estimate the affine transform mapping `target_image` onto `reference_image`.

    1. One global (x, y) shift via phase correlation over the whole image.
    2. A grid of tile-sized tie points: for each tile in the reference, a
       normalized-cross-correlation search (`_match_patch`) around the
       shift-predicted position in the target, skipping near-uniform tiles
       (`max_ref_std`) that carry no useful texture to match.
    3. An affine fit through the tie points (`fit_affine_matrix`), then (in
       "robust" mode) a few rounds of refit-and-drop-the-worst-residuals to
       reject bad tie points before the final fit -- a lightweight stand-in
       for full RANSAC, since the tie points are already fairly clean.

    Falls back to a pure-translation matrix (no rotation/scale) if fewer than
    3 tie points survive, since an affine fit needs at least 3 non-collinear
    correspondences.
    """
    reference = prepare_registration_image(reference_image)
    target = prepare_registration_image(target_image)
    if mode == "robust":
        global_shift_x, global_shift_y, _global_score = multiscale_phase_correlation_shift(reference, target)
    else:
        global_shift_x, global_shift_y, _global_score = phase_correlation_shift(reference, target)

    image_height, image_width = reference.shape[:2]
    tile_size = int(max(tile_size_px, 24))
    search_radius = int(max(search_radius_px, 6))
    if mode == "robust":
        tile_size = max(tile_size, 64)
        search_radius = max(int(round(search_radius * 1.5)), 10)
        spacing = int(max(spacing_px or max(tile_size // 3, 16), 10))
        score_threshold = 1.4
        max_ref_std = 0.035
    else:
        spacing = int(max(spacing_px or max(tile_size // 2, 24), 12))
        score_threshold = 2.0
        max_ref_std = 0.05
    half = tile_size // 2

    source_points: list[tuple[float, float]] = []
    target_points: list[tuple[float, float]] = []
    scores: list[float] = []

    for center_y in range(half + search_radius, image_height - half - search_radius, spacing):
        for center_x in range(half + search_radius, image_width - half - search_radius, spacing):
            reference_patch = reference[center_y - half : center_y + half, center_x - half : center_x + half]
            if reference_patch.shape != (tile_size, tile_size):
                continue
            if float(np.std(reference_patch)) < max_ref_std:
                continue

            predicted_x = int(round(center_x + global_shift_x))
            predicted_y = int(round(center_y + global_shift_y))
            search_x0 = predicted_x - half - search_radius
            search_y0 = predicted_y - half - search_radius
            search_x1 = predicted_x + half + search_radius
            search_y1 = predicted_y + half + search_radius
            if search_x0 < 0 or search_y0 < 0 or search_x1 > image_width or search_y1 > image_height:
                continue

            search_area = target[search_y0:search_y1, search_x0:search_x1]
            peak_x, peak_y, score = _match_patch(
                reference_patch,
                search_area,
                half,
                score_threshold=score_threshold,
                subpixel_precision=subpixel_precision,
            )
            if peak_x is None or peak_y is None:
                continue
            source_points.append((float(center_x), float(center_y)))
            target_points.append((float(search_x0 + peak_x + half), float(search_y0 + peak_y + half)))
            scores.append(float(score))

    if len(source_points) < 3:
        matrix = np.array(
            [[1.0, 0.0, float(global_shift_x)], [0.0, 1.0, float(global_shift_y)]],
            dtype=np.float64,
        )
        return ChromaticRegistrationResult(
            affine_matrix=matrix,
            global_shift_x_px=float(global_shift_x),
            global_shift_y_px=float(global_shift_y),
            rmse_px=0.0,
            mean_score=float(np.mean(scores)) if scores else 0.0,
            min_score=float(np.min(scores)) if scores else 0.0,
            tile_count=len(source_points),
            inlier_count=len(source_points),
        )

    source_array = np.asarray(source_points, dtype=np.float64)
    target_array = np.asarray(target_points, dtype=np.float64)
    score_array = np.asarray(scores, dtype=np.float64)
    matrix = fit_affine_matrix(source_array, target_array)
    residuals = affine_residuals(source_array, target_array, matrix)
    iterations = 3 if mode == "robust" else 1
    inliers = np.ones(source_array.shape[0], dtype=bool)
    for _ in range(iterations):
        residual_threshold = max(1.8 if mode == "robust" else 2.5, float(np.median(residuals[inliers])) * (2.0 if mode == "robust" else 2.5)) if residuals.size else (1.8 if mode == "robust" else 2.5)
        next_inliers = residuals <= residual_threshold
        if int(np.count_nonzero(next_inliers)) < 3:
            break
        if np.array_equal(next_inliers, inliers) and _ > 0:
            break
        inliers = next_inliers
        matrix = fit_affine_matrix(source_array[inliers], target_array[inliers])
        residuals = affine_residuals(source_array, target_array, matrix)
    if int(np.count_nonzero(inliers)) >= 3:
        score_array = score_array[inliers]
        residuals = residuals[inliers]
    rmse = float(np.sqrt(np.mean(residuals**2))) if residuals.size else 0.0
    return ChromaticRegistrationResult(
        affine_matrix=matrix,
        global_shift_x_px=float(global_shift_x),
        global_shift_y_px=float(global_shift_y),
        rmse_px=rmse,
        mean_score=float(np.mean(score_array)) if score_array.size else 0.0,
        min_score=float(np.min(score_array)) if score_array.size else 0.0,
        tile_count=len(source_points),
        inlier_count=int(score_array.size),
    )


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


def fit_affine_matrix(source_points_xy: np.ndarray, target_points_xy: np.ndarray) -> np.ndarray:
    """Ordinary-least-squares affine fit (rotation + scale + shear + translation)
    through matched point pairs.

    Each output coordinate (target x, target y) is an independent linear
    combination of (source x, source y, 1), solved by least squares
    (`np.linalg.lstsq`) rather than an exact solve, so it works cleanly with
    more than the minimum 3 point pairs -- extra, noisy correspondences
    average out rather than making the system unsolvable. Returns a 2x3
    matrix `[[a, b, tx], [c, d, ty]]` such that
    `target = matrix @ [source_x, source_y, 1]`.
    """
    design = np.column_stack((source_points_xy[:, 0], source_points_xy[:, 1], np.ones(source_points_xy.shape[0])))
    coeff_x, _, _, _ = np.linalg.lstsq(design, target_points_xy[:, 0], rcond=None)
    coeff_y, _, _, _ = np.linalg.lstsq(design, target_points_xy[:, 1], rcond=None)
    return np.vstack((coeff_x, coeff_y)).astype(np.float64, copy=False)


def apply_affine_to_points(points_xy: np.ndarray, affine_matrix: np.ndarray) -> np.ndarray:
    """Map `points_xy` (N x 2) through the 2x3 affine `matrix @ [x, y, 1]`."""
    if points_xy.size == 0:
        return points_xy.astype(np.float64, copy=True)
    design = np.column_stack((points_xy[:, 0], points_xy[:, 1], np.ones(points_xy.shape[0], dtype=np.float64)))
    return design @ affine_matrix.T


def affine_residuals(source_points_xy: np.ndarray, target_points_xy: np.ndarray, affine_matrix: np.ndarray) -> np.ndarray:
    """Per-point distance (px) between `affine_matrix @ source` and `target` -- the
    fit-quality/outlier-rejection metric used throughout this module."""
    predicted = apply_affine_to_points(source_points_xy, affine_matrix)
    return np.sqrt(np.sum((predicted - target_points_xy) ** 2, axis=1))


def invert_affine_matrix(affine_matrix: np.ndarray) -> np.ndarray:
    """Invert a 2x3 affine matrix (linear part + translation), so
    `apply_affine_to_points(apply_affine_to_points(p, m), invert_affine_matrix(m)) == p`.
    """
    linear = np.asarray(affine_matrix[:, :2], dtype=np.float64)
    translation = np.asarray(affine_matrix[:, 2], dtype=np.float64)
    inverse_linear = np.linalg.inv(linear)
    inverse_translation = -inverse_linear @ translation
    return np.column_stack((inverse_linear, inverse_translation))


def warp_image_affine(
    image: np.ndarray,
    affine_matrix: np.ndarray,
    *,
    output_shape: tuple[int, int] | None = None,
    order: int = 1,
    cval: float = 0.0,
) -> np.ndarray:
    if output_shape is None:
        output_shape = image.shape[:2]
    inverse_xy = invert_affine_matrix(affine_matrix)
    ixx, ixy = float(inverse_xy[0, 0]), float(inverse_xy[0, 1])
    iyx, iyy = float(inverse_xy[1, 0]), float(inverse_xy[1, 1])
    off_x, off_y = float(inverse_xy[0, 2]), float(inverse_xy[1, 2])
    matrix_rc = np.array([[iyy, iyx], [ixy, ixx]], dtype=np.float64)
    offset_rc = np.array([off_y, off_x], dtype=np.float64)
    return ndimage.affine_transform(
        image,
        matrix=matrix_rc,
        offset=offset_rc,
        output_shape=output_shape,
        order=order,
        mode="constant",
        cval=cval,
        prefilter=order > 1,
    )


def transformed_circle_points(
    center_xy: tuple[float, float],
    radius_px: float,
    affine_matrix: np.ndarray,
    theta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    center_x, center_y = float(center_xy[0]), float(center_xy[1])
    xs = center_x + float(radius_px) * np.cos(theta)
    ys = center_y + float(radius_px) * np.sin(theta)
    points = np.column_stack((xs, ys)).astype(np.float64, copy=False)
    transformed = apply_affine_to_points(points, affine_matrix)
    return transformed[:, 0], transformed[:, 1]


def transformed_disk_mask(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    return transformed_annulus_mask(image_shape, center_xy, 0.0, radius_px, affine_matrix)


def _annulus_mask_in_box(
    box_x0: int,
    box_y0: int,
    box_x1: int,
    box_y1: int,
    center_xy: tuple[float, float],
    inner_radius: float,
    outer_radius: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Core annulus-mask math for an explicit target-space box [x0:x1, y0:y1]
    (in the same coordinate space `affine_matrix` maps *into*). For every pixel
    in that box, maps back through the inverse affine to source space and
    checks distance from `center_xy` against [inner_radius, outer_radius].
    Returns a mask shaped (box_y1 - box_y0, box_x1 - box_x0).
    """
    box_h, box_w = box_y1 - box_y0, box_x1 - box_x0
    if box_h <= 0 or box_w <= 0:
        return np.zeros((max(box_h, 0), max(box_w, 0)), dtype=bool)
    yy, xx = np.indices((box_h, box_w), dtype=np.float64)
    target_points = np.column_stack((xx.ravel() + box_x0, yy.ravel() + box_y0))
    inverse_affine = invert_affine_matrix(affine_matrix)
    source_points = apply_affine_to_points(target_points, inverse_affine)
    dx = source_points[:, 0] - float(center_xy[0])
    dy = source_points[:, 1] - float(center_xy[1])
    distance_sq = dx * dx + dy * dy
    mask_local = (distance_sq <= outer_radius * outer_radius) & (distance_sq >= inner_radius * inner_radius)
    return mask_local.reshape((box_h, box_w))


def annulus_reach_box(
    center_xy: tuple[float, float],
    outer_radius: float,
    affine_matrix: np.ndarray,
) -> tuple[float, float]:
    """How far (in target space) the transformed annulus can reach from its
    transformed center, and the transformed center itself — shared by the
    full-image and patch-scoped variants so both use the same tight bound.
    """
    linear = np.asarray(affine_matrix[:, :2], dtype=np.float64)
    singular_values = np.linalg.svd(linear, compute_uv=False)
    max_scale = max(float(np.max(singular_values)), 1e-6)
    transformed_center = apply_affine_to_points(np.asarray([[center_xy[0], center_xy[1]]], dtype=np.float64), affine_matrix)[0]
    reach = outer_radius * max_scale + 3.0
    return transformed_center, reach


def transformed_annulus_mask(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_radius_px: float,
    outer_radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    image_height, image_width = image_shape[:2]
    inner_radius = max(float(inner_radius_px), 0.0)
    outer_radius = max(float(outer_radius_px), inner_radius)
    if outer_radius <= 0.0:
        return np.zeros((image_height, image_width), dtype=bool)

    transformed_center, reach = annulus_reach_box(center_xy, outer_radius, affine_matrix)
    x0 = max(int(np.floor(transformed_center[0] - reach)), 0)
    x1 = min(int(np.ceil(transformed_center[0] + reach)) + 1, image_width)
    y0 = max(int(np.floor(transformed_center[1] - reach)), 0)
    y1 = min(int(np.ceil(transformed_center[1] + reach)) + 1, image_height)
    if x0 >= x1 or y0 >= y1:
        return np.zeros((image_height, image_width), dtype=bool)

    mask_local = _annulus_mask_in_box(x0, y0, x1, y1, center_xy, inner_radius, outer_radius, affine_matrix)
    mask = np.zeros((image_height, image_width), dtype=bool)
    mask[y0:y1, x0:x1] = mask_local
    return mask


def transformed_annulus_mask_for_patch(
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_radius_px: float,
    outer_radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Same geometry as transformed_annulus_mask, but scoped to a patch that's
    already been read from a smaller region of the target space (e.g. a
    zarr-chunk-aware partial read around one or more ROIs) — returns a mask
    shaped `patch_shape`, local to `patch_origin_xy`, instead of embedding into
    a full-image-sized array. Use when the caller has already decided the
    patch is the right region (e.g. a per-ROI or union bounding box); this
    does not do its own reach-based shrinking beyond the given patch.
    """
    patch_h, patch_w = patch_shape[:2]
    inner_radius = max(float(inner_radius_px), 0.0)
    outer_radius = max(float(outer_radius_px), inner_radius)
    if outer_radius <= 0.0:
        return np.zeros((patch_h, patch_w), dtype=bool)
    px0, py0 = int(patch_origin_xy[0]), int(patch_origin_xy[1])
    return _annulus_mask_in_box(px0, py0, px0 + patch_w, py0 + patch_h, center_xy, inner_radius, outer_radius, affine_matrix)


def transformed_disk_mask_for_patch(
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    center_xy: tuple[float, float],
    radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    return transformed_annulus_mask_for_patch(patch_origin_xy, patch_shape, center_xy, 0.0, radius_px, affine_matrix)


def fit_similarity_matrix(source_points_xy: np.ndarray, target_points_xy: np.ndarray) -> np.ndarray:
    """Least-squares fit of a *similarity* transform (uniform scale + rotation +
    translation only -- no shear or independent x/y scale) through matched
    point pairs.

    Implements Umeyama's closed-form solution (Umeyama, S. "Least-squares
    estimation of transformation parameters between two point patterns."
    IEEE Trans. Pattern Anal. Mach. Intell. 13(4), 376-380, 1991): center
    both point sets, take the SVD of their cross-covariance, and read the
    optimal rotation off `V @ U.T` (flipping the last singular vector if
    that rotation has determinant < 0, which would mean a reflection rather
    than a rotation); the optimal scale is the sum of singular values
    divided by the source points' variance.

    Used for the "radial"/landmark-based correction mode, where a handful of
    user-picked points should only ever imply a rigid-plus-zoom transform,
    not an arbitrary shear -- so a small number of noisy landmarks can't
    accidentally warp the image. Raises `ValueError` with fewer than 2 point
    pairs, or if the source points are degenerate (all coincident).
    """
    if source_points_xy.shape[0] < 2 or target_points_xy.shape[0] < 2:
        raise ValueError("At least two landmark pairs are required for the radial landmark model.")
    source = np.asarray(source_points_xy, dtype=np.float64)
    target = np.asarray(target_points_xy, dtype=np.float64)
    source_mean = np.mean(source, axis=0)
    target_mean = np.mean(target, axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean
    covariance = source_centered.T @ target_centered / max(source.shape[0], 1)
    u, singular_values, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1.0
        rotation = vt.T @ u.T
    source_variance = float(np.mean(np.sum(source_centered**2, axis=1)))
    if source_variance <= 1e-12:
        raise ValueError("Landmarks are degenerate and cannot define a radial transform.")
    scale = float(np.sum(singular_values) / source_variance)
    linear = scale * rotation
    translation = target_mean - linear @ source_mean
    return np.column_stack((linear, translation))


def decompose_similarity_matrix(affine_matrix: np.ndarray) -> tuple[float, float, float, float]:
    """Read a similarity matrix's `(scale, angle_rad, shift_x_px, shift_y_px)`
    back out of its 2x3 form -- the inverse of `compose_similarity_matrix`,
    used to show a human-editable scale/rotation/shift in the GUI instead of
    raw matrix coefficients.
    """
    matrix = np.asarray(affine_matrix, dtype=np.float64)
    linear = matrix[:, :2]
    scale_x = float(np.hypot(linear[0, 0], linear[1, 0]))
    scale_y = float(np.hypot(linear[0, 1], linear[1, 1]))
    scale = max((scale_x + scale_y) * 0.5, 1e-12)
    angle_rad = float(np.arctan2(linear[1, 0], linear[0, 0]))
    return scale, angle_rad, float(matrix[0, 2]), float(matrix[1, 2])


def compose_similarity_matrix(scale: float, angle_rad: float, shift_x_px: float, shift_y_px: float) -> np.ndarray:
    """Build a 2x3 similarity matrix from `(scale, angle_rad, shift_x_px,
    shift_y_px)` -- the inverse of `decompose_similarity_matrix`."""
    cos_angle = float(np.cos(angle_rad))
    sin_angle = float(np.sin(angle_rad))
    return np.array(
        [
            [float(scale) * cos_angle, -float(scale) * sin_angle, float(shift_x_px)],
            [float(scale) * sin_angle, float(scale) * cos_angle, float(shift_y_px)],
        ],
        dtype=np.float64,
    )


def transform_spots_affine(
    rois: list[AreaRoi],
    affine_matrix: np.ndarray,
    *,
    clamp_shape: tuple[int, int] | None = None,
) -> list[AreaRoi]:
    if not rois:
        return []
    source_points = np.asarray([(roi.center_x, roi.center_y) for roi in rois], dtype=np.float64)
    target_points = apply_affine_to_points(source_points, affine_matrix)
    linear = np.asarray(affine_matrix[:, :2], dtype=np.float64)
    singular_values = np.linalg.svd(linear, compute_uv=False)
    scale = float(np.max(singular_values))
    scale = max(scale, 1e-6)
    transformed: list[AreaRoi] = []
    max_x = float(clamp_shape[1] - 1) if clamp_shape is not None else None
    max_y = float(clamp_shape[0] - 1) if clamp_shape is not None else None
    for index, roi in enumerate(rois):
        target_x = float(target_points[index, 0])
        target_y = float(target_points[index, 1])
        transformed_roi = deepcopy(roi)
        transformed_roi.center_x = target_x
        transformed_roi.center_y = target_y
        transformed_roi.sample_radius_px = max(float(roi.sample_radius_px) * scale, 1.0)
        if max_x is not None and max_y is not None:
            transformed_roi.center_x = float(np.clip(transformed_roi.center_x, 0.0, max_x))
            transformed_roi.center_y = float(np.clip(transformed_roi.center_y, 0.0, max_y))
        transformed.append(transformed_roi)
    return transformed


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


def _traceable_landmark_candidates(
    image: np.ndarray,
    feature_count: int,
    *,
    patch_radius_px: int = 10,
) -> list[tuple[float, float, float]]:
    prepared = prepare_registration_image(image)
    response = _corner_response(prepared)
    image_height, image_width = prepared.shape[:2]
    if image_height <= 0 or image_width <= 0:
        return []

    border_margin = max(int(round(min(image_height, image_width) * 0.08)), int(patch_radius_px) * 2, 18)
    suppression = max(3, int(round(max(int(patch_radius_px) * 2 + 1, 5))))
    local_max = ndimage.maximum_filter(response, size=suppression, mode="nearest")
    candidate_mask = np.isfinite(response) & (response > 0.0) & (response == local_max)
    if border_margin * 2 < image_width:
        candidate_mask[:, :border_margin] = False
        candidate_mask[:, image_width - border_margin :] = False
    if border_margin * 2 < image_height:
        candidate_mask[:border_margin, :] = False
        candidate_mask[image_height - border_margin :, :] = False

    ys, xs = np.nonzero(candidate_mask)
    if xs.size == 0:
        return []

    scores = response[ys, xs].astype(np.float64, copy=False)
    center_x = float(image_width - 1) * 0.5
    center_y = float(image_height - 1) * 0.5
    max_radius = max(float(np.hypot(center_x, center_y)), 1e-6)
    radial_distance = np.hypot(xs.astype(np.float64) - center_x, ys.astype(np.float64) - center_y) / max_radius
    combined_score = scores * (1.0 + 0.35 * radial_distance)
    order = np.argsort(combined_score)[::-1]
    return [(float(xs[index]), float(ys[index]), float(combined_score[index])) for index in order]


def _select_spread_landmarks(
    candidates: list[tuple[float, float, float]],
    feature_count: int,
    image_shape: tuple[int, int],
    *,
    patch_radius_px: int = 10,
) -> list[tuple[float, float, float]]:
    image_height, image_width = image_shape[:2]
    count = max(int(feature_count), 1)
    if not candidates:
        return []

    target_spacing = max(
        int(round(np.sqrt(max(float(image_height * image_width), 1.0) / max(count, 1)) * 0.75)),
        int(patch_radius_px) * 3,
        18,
    )
    spacing_candidates = [target_spacing, int(round(target_spacing * 0.85)), int(round(target_spacing * 0.7)), int(round(target_spacing * 0.55)), 0]

    def pick(min_distance: float) -> list[tuple[float, float, float]]:
        selected: list[tuple[float, float, float]] = []
        min_distance_sq = float(min_distance) * float(min_distance)
        for candidate in candidates:
            x, y, _score = candidate
            if not selected:
                selected.append(candidate)
                if len(selected) >= count:
                    break
                continue
            if any((x - sel_x) ** 2 + (y - sel_y) ** 2 < min_distance_sq for sel_x, sel_y, _sel_score in selected):
                continue
            selected.append(candidate)
            if len(selected) >= count:
                break
        return selected

    selected: list[tuple[float, float, float]] = []
    for spacing in spacing_candidates:
        selected = pick(float(spacing))
        if len(selected) >= count:
            break

    if len(selected) < count:
        chosen_points = {(round(x, 6), round(y, 6)) for x, y, _score in selected}
        for candidate in candidates:
            key = (round(candidate[0], 6), round(candidate[1], 6))
            if key in chosen_points:
                continue
            selected.append(candidate)
            chosen_points.add(key)
            if len(selected) >= count:
                break

    return selected[:count]


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
    patch_half: int,
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
