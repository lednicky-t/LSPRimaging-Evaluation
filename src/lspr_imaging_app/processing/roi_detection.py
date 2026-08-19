from __future__ import annotations

from dataclasses import replace
from math import hypot

import numpy as np
from scipy import ndimage

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings


def ignored_pixel_mask(
    image: np.ndarray,
    settings: AreaRoiDetectionSettings,
    external_mask: np.ndarray | None = None,
) -> np.ndarray:
    image_shape = image.shape[:2]
    combined_mask = _normalized_external_mask(image_shape, external_mask)
    if image.size == 0:
        return combined_mask
    if not settings.ignore_marked_pixels:
        return np.zeros(image_shape, dtype=bool)
    return combined_mask


def _normalized_external_mask(
    image_shape: tuple[int, int],
    external_mask: np.ndarray | None,
) -> np.ndarray:
    if external_mask is None:
        return np.zeros(image_shape, dtype=bool)
    mask = np.asarray(external_mask, dtype=bool)
    if mask.shape != image_shape:
        return np.zeros(image_shape, dtype=bool)
    return mask


def detect_rois(
    image: np.ndarray,
    settings: AreaRoiDetectionSettings,
    external_mask: np.ndarray | None = None,
    progress_callback=None,
) -> list[AreaRoi]:
    if image.size == 0:
        return []

    def report_progress(percent: int, text: str) -> None:
        if progress_callback is not None:
            progress_callback(int(max(0, min(percent, 100))), text)

    report_progress(5, "ROI detection: preparing mask...")
    image_f32 = image.astype(np.float32, copy=False)
    valid_mask = ~ignored_pixel_mask(image_f32, settings, external_mask=external_mask)
    if not np.any(valid_mask):
        report_progress(100, "ROI detection: no valid pixels.")
        return []

    radius = float(settings.sample_radius_px)
    sigma = max(radius / 2.5, 1.0)
    report_progress(12, "ROI detection: smoothing image...")
    filtered, filter_support = _masked_gaussian_filter(image_f32, valid_mask, sigma=sigma)
    search_image = -filtered if settings.mode == "dark" else filtered

    filtered_values = filtered[valid_mask]
    intensity_min = (
        float(np.min(filtered_values)) if settings.intensity_min_value is None else float(settings.intensity_min_value)
    )
    intensity_max = (
        float(np.max(filtered_values)) if settings.intensity_max_value is None else float(settings.intensity_max_value)
    )
    if intensity_min > intensity_max:
        intensity_min, intensity_max = intensity_max, intensity_min

    candidate_mask = (filtered >= intensity_min) & (filtered <= intensity_max)
    candidate_mask &= valid_mask & (filter_support > 0.05)

    neighborhood = max(int(settings.array_spacing_px if int(settings.array_spacing_px) > 0 else settings.sample_radius_px * 2), 3)
    searchable = np.where(candidate_mask, search_image, -np.inf)
    local_max = searchable == ndimage.maximum_filter(searchable, size=neighborhood, mode="nearest")
    candidates = np.argwhere(local_max & candidate_mask)
    if candidates.size == 0:
        report_progress(100, "ROI detection: no candidates found.")
        return []

    report_progress(24, f"ROI detection: scoring {len(candidates)} candidates...")
    scored: list[tuple[float, float, float]] = []
    total_candidates = max(len(candidates), 1)
    progress_step = max(total_candidates // 20, 1)
    for index, (y, x) in enumerate(candidates, start=1):
        scored.append(
            (
                _roi_circular_contrast_score(
                    filtered=filtered,
                    valid_mask=valid_mask,
                    center_x=float(x),
                    center_y=float(y),
                    radius=radius,
                    mode=settings.mode,
                ),
                float(x),
                float(y),
            )
        )
        if index % progress_step == 0 or index == total_candidates:
            scaled = 24 + int(round((index / total_candidates) * 28))
            report_progress(scaled, f"ROI detection: scored {index}/{total_candidates} candidates...")
    scored.sort(reverse=True)

    accepted: list[AreaRoi] = []
    min_distance = float(settings.array_spacing_px if int(settings.array_spacing_px) > 0 else settings.sample_radius_px * 2)
    image_height, image_width = image.shape[:2]
    total_scored = max(len(scored), 1)
    for index, (score, x, y) in enumerate(scored, start=1):
        if score <= 0.0:
            # scored is sorted descending, so no later candidate can score
            # higher; a non-positive score means no contrast in the
            # requested (bright/dark) direction, matching the same floor
            # _refine_roi_center applies below.
            break
        if any(hypot(x - roi.center_x, y - roi.center_y) < min_distance for roi in accepted):
            continue
        accepted.append(
            AreaRoi(
                area_roi_id=len(accepted) + 1,
                center_x=x,
                center_y=y,
                sample_radius_px=radius,
                score=score,
                sample_diameter_px=float(radius * 2.0),
            )
        )
        expected_count = max(int(settings.array_rows), 0) * max(int(settings.array_cols), 0)
        max_rois = expected_count if expected_count > 0 else len(candidates)
        if len(accepted) >= int(max_rois):
            break
        if index % max(total_scored // 10, 1) == 0 or index == total_scored:
            scaled = 52 + int(round((index / total_scored) * 20))
            report_progress(scaled, f"ROI detection: accepted {len(accepted)} ROIs...")

    report_progress(74, "ROI detection: refining ROIs...")
    accepted = [
        _refine_detected_roi(
            roi,
            filtered=filtered,
            valid_mask=valid_mask,
            radius=radius,
            mode=settings.mode,
        )
        for roi in accepted
    ]

    expected_count = max(int(settings.array_rows), 0) * max(int(settings.array_cols), 0)
    if expected_count > 0 and int(settings.array_spacing_px) > 0 and accepted:
        report_progress(84, "ROI detection: fitting grid...")
        accepted = _fit_grid_array(
            accepted=accepted,
            filtered=filtered,
            valid_mask=valid_mask,
            rows=int(settings.array_rows),
            cols=int(settings.array_cols),
            spacing=float(settings.array_spacing_px),
            radius=radius,
            mode=settings.mode,
        )

    for index, roi in enumerate(accepted, start=1):
        roi.area_roi_id = index
    report_progress(100, f"ROI detection: detected {len(accepted)} ROIs.")
    return accepted


def refresh_roi_metrics(
    image: np.ndarray,
    settings: AreaRoiDetectionSettings,
    rois: list[AreaRoi],
    external_mask: np.ndarray | None = None,
) -> list[AreaRoi]:
    """Recompute each ROI's contrast score at its current position and
    radius, without moving it.

    Unlike detect_rois/_refine_detected_roi, which search nearby positions
    for a locally better center, this only re-scores each ROI where it
    already is - used after the user finishes editing ROI positions/sizes or
    the exclusion mask, so the ROI table's Score column reflects the current
    image and geometry instead of a stale value from initial detection.
    """
    if image.size == 0 or not rois:
        return rois
    image_f32 = image.astype(np.float32, copy=False)
    valid_mask = ~ignored_pixel_mask(image_f32, settings, external_mask=external_mask)
    if not np.any(valid_mask):
        return rois
    sigma = max(float(settings.sample_radius_px) / 2.5, 1.0)
    filtered, _ = _masked_gaussian_filter(image_f32, valid_mask, sigma=sigma)
    return [
        replace(
            roi,
            score=_roi_circular_contrast_score(
                filtered=filtered,
                valid_mask=valid_mask,
                center_x=float(roi.center_x),
                center_y=float(roi.center_y),
                radius=float(roi.sample_radius_px),
                mode=settings.mode,
            ),
        )
        for roi in rois
    ]


def _masked_gaussian_filter(image: np.ndarray, valid_mask: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    weights = valid_mask.astype(np.float32, copy=False)
    numerator = ndimage.gaussian_filter(image * weights, sigma=sigma, mode="nearest")
    denominator = ndimage.gaussian_filter(weights, sigma=sigma, mode="nearest")
    fallback = float(np.mean(image[valid_mask])) if np.any(valid_mask) else 0.0
    filtered = np.full_like(numerator, fallback)
    np.divide(numerator, denominator, out=filtered, where=denominator > 1e-6)
    return filtered, denominator


def _refine_detected_roi(
    roi: AreaRoi,
    *,
    filtered: np.ndarray,
    valid_mask: np.ndarray,
    radius: float,
    mode: str,
    search_radius: float | None = None,
    fallback_x: float | None = None,
    fallback_y: float | None = None,
) -> AreaRoi:
    refined_x, refined_y, refined_score, found_signal = _refine_roi_center(
        filtered=filtered,
        valid_mask=valid_mask,
        seed_x=roi.center_x,
        seed_y=roi.center_y,
        radius=radius,
        mode=mode,
        search_radius=search_radius,
    )
    if not found_signal:
        refined_x = roi.center_x if fallback_x is None else fallback_x
        refined_y = roi.center_y if fallback_y is None else fallback_y
    return AreaRoi(
        area_roi_id=roi.area_roi_id,
        center_x=refined_x,
        center_y=refined_y,
        sample_radius_px=roi.sample_radius_px,
        score=max(roi.score, refined_score),
        sample_color_hex=roi.sample_color_hex,
        reference_color_hex=roi.reference_color_hex,
        sample_diameter_px=roi.sample_diameter_px,
        reference_inner_diameter_px=roi.reference_inner_diameter_px,
        reference_outer_diameter_px=roi.reference_outer_diameter_px,
        support_mean_radius_px=roi.support_mean_radius_px,
        support_radius_std_px=roi.support_radius_std_px,
        support_value_mean=roi.support_value_mean,
        support_value_std=roi.support_value_std,
        quality_score=roi.quality_score,
        inferred=roi.inferred and not found_signal,
    )


def _refine_roi_center(
    *,
    filtered: np.ndarray,
    valid_mask: np.ndarray,
    seed_x: float,
    seed_y: float,
    radius: float,
    mode: str,
    search_radius: float | None = None,
) -> tuple[float, float, float, bool]:
    local_radius = max(float(search_radius) if search_radius is not None else radius * 1.6, 2.0)
    x_min = max(int(np.floor(seed_x - local_radius)), 0)
    x_max = min(int(np.ceil(seed_x + local_radius)) + 1, filtered.shape[1])
    y_min = max(int(np.floor(seed_y - local_radius)), 0)
    y_max = min(int(np.ceil(seed_y + local_radius)) + 1, filtered.shape[0])
    if x_min >= x_max or y_min >= y_max:
        return seed_x, seed_y, 0.0, False

    best_x = seed_x
    best_y = seed_y
    best_score = float("-inf")
    penalty = max(radius * 0.03, 0.01)
    search_step = 1 if local_radius <= 8.0 else 2

    for y in range(y_min, y_max, search_step):
        for x in range(x_min, x_max, search_step):
            if not bool(valid_mask[y, x]):
                continue
            distance_sq = (float(x) - seed_x) ** 2 + (float(y) - seed_y) ** 2
            if distance_sq > local_radius**2:
                continue
            score = _roi_circular_contrast_score(
                filtered=filtered,
                valid_mask=valid_mask,
                center_x=float(x),
                center_y=float(y),
                radius=radius,
                mode=mode,
            )
            if score <= 0.0:
                continue
            score -= penalty * distance_sq
            if score > best_score:
                best_score = score
                best_x = float(x)
                best_y = float(y)

    if best_score == float("-inf"):
        return seed_x, seed_y, 0.0, False

    return best_x, best_y, float(best_score), True


def _filter_by_array_support(
    accepted: list[AreaRoi],
    expected_count: int,
    expected_spacing: float,
) -> list[AreaRoi]:
    tolerance = max(expected_spacing * 0.35, 4.0)

    def support_score(roi: AreaRoi) -> tuple[int, float]:
        aligned_neighbors = 0
        for other in accepted:
            if other is roi:
                continue
            dx = abs(other.center_x - roi.center_x)
            dy = abs(other.center_y - roi.center_y)
            if abs(dx - expected_spacing) <= tolerance and dy <= tolerance:
                aligned_neighbors += 1
            elif abs(dy - expected_spacing) <= tolerance and dx <= tolerance:
                aligned_neighbors += 1
        return aligned_neighbors, roi.score

    ranked = sorted(accepted, key=support_score, reverse=True)
    return ranked[:expected_count]


def _fit_grid_array(
    accepted: list[AreaRoi],
    filtered: np.ndarray,
    valid_mask: np.ndarray,
    rows: int,
    cols: int,
    spacing: float,
    radius: float,
    mode: str,
) -> list[AreaRoi]:
    if rows <= 0 or cols <= 0 or spacing <= 0.0:
        return accepted

    supported = _filter_by_array_support(accepted, rows * cols, spacing)
    xs = np.asarray([roi.center_x for roi in supported], dtype=np.float64)
    ys = np.asarray([roi.center_y for roi in supported], dtype=np.float64)
    if xs.size == 0 or ys.size == 0:
        return accepted

    origin_x = _estimate_grid_origin_1d(xs, spacing, cols, image_size=float(filtered.shape[1]))
    origin_y = _estimate_grid_origin_1d(ys, spacing, rows, image_size=float(filtered.shape[0]))
    tolerance = max(spacing * 0.45, radius * 1.5, 5.0)
    refine_radius = min(tolerance, max(radius * 2.0, 4.0))

    fitted: list[AreaRoi] = []
    used_indices: set[int] = set()
    for row in range(rows):
        for col in range(cols):
            grid_x = origin_x + col * spacing
            grid_y = origin_y + row * spacing

            nearest: AreaRoi | None = None
            nearest_distance = float("inf")
            nearest_index = -1
            for index, roi in enumerate(accepted):
                if index in used_indices:
                    continue
                distance = hypot(roi.center_x - grid_x, roi.center_y - grid_y)
                if distance < nearest_distance:
                    nearest = roi
                    nearest_distance = distance
                    nearest_index = index

            if nearest is not None and nearest_distance <= tolerance:
                used_indices.add(nearest_index)
                fitted.append(
                    _refine_detected_roi(
                        nearest,
                        filtered=filtered,
                        valid_mask=valid_mask,
                        radius=radius,
                        mode=mode,
                        search_radius=refine_radius,
                        fallback_x=grid_x,
                        fallback_y=grid_y,
                    )
                )
                continue

            refined_x, refined_y, refined_score, found_signal = _refine_roi_center(
                filtered=filtered,
                valid_mask=valid_mask,
                seed_x=grid_x,
                seed_y=grid_y,
                radius=radius,
                mode=mode,
                search_radius=refine_radius,
            )
            fitted.append(
                AreaRoi(
                    area_roi_id=0,
                    center_x=refined_x if found_signal else grid_x,
                    center_y=refined_y if found_signal else grid_y,
                    sample_radius_px=radius,
                    score=refined_score,
                    sample_diameter_px=float(radius * 2.0),
                    inferred=not found_signal,
                )
            )

    return fitted


def _roi_circular_contrast_score(
    *,
    filtered: np.ndarray,
    valid_mask: np.ndarray,
    center_x: float,
    center_y: float,
    radius: float,
    mode: str,
) -> float:
    inner_radius = max(radius * 0.85, 1.0)
    outer_radius = max(radius * 1.65, inner_radius + 1.0)
    x_min = max(int(np.floor(center_x - outer_radius)), 0)
    x_max = min(int(np.ceil(center_x + outer_radius)) + 1, filtered.shape[1])
    y_min = max(int(np.floor(center_y - outer_radius)), 0)
    y_max = min(int(np.ceil(center_y + outer_radius)) + 1, filtered.shape[0])
    if x_min >= x_max or y_min >= y_max:
        return 0.0

    yy, xx = np.mgrid[y_min:y_max, x_min:x_max]
    distance = np.hypot(xx - center_x, yy - center_y)
    local_valid = valid_mask[y_min:y_max, x_min:x_max]
    disk_mask = local_valid & (distance <= inner_radius)
    ring_mask = local_valid & (distance >= outer_radius * 0.72) & (distance <= outer_radius)
    if not np.any(disk_mask) or not np.any(ring_mask):
        return 0.0

    disk_mean = float(np.mean(filtered[y_min:y_max, x_min:x_max][disk_mask]))
    ring_mean = float(np.mean(filtered[y_min:y_max, x_min:x_max][ring_mask]))
    if mode == "dark":
        return ring_mean - disk_mean
    return disk_mean - ring_mean


def _estimate_grid_origin_1d(values: np.ndarray, spacing: float, count: int, image_size: float) -> float:
    if values.size == 0 or spacing <= 0.0 or count <= 0:
        return 0.0

    values = np.asarray(values, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0

    max_origin = max(float(image_size) - 1.0 - (max(count - 1, 0) * spacing), 0.0)
    if max_origin <= 0.0:
        return 0.0

    phase = float(np.median(np.mod(values, spacing)))
    lower_shift = int(np.ceil((0.0 - phase) / spacing))
    upper_shift = int(np.floor((max_origin - phase) / spacing))
    if lower_shift > upper_shift:
        return min(max(phase, 0.0), max_origin)

    grid_indices = np.arange(count, dtype=np.float64)
    best_origin = min(max(phase + lower_shift * spacing, 0.0), max_origin)
    best_score = float("inf")
    for shift in range(lower_shift, upper_shift + 1):
        origin = phase + shift * spacing
        if origin < 0.0 or origin > max_origin:
            continue
        grid_positions = origin + grid_indices * spacing
        distances = np.abs(values[:, None] - grid_positions[None, :])
        score = float(np.sum(np.min(distances, axis=1) ** 2))
        if score < best_score - 1e-9 or (abs(score - best_score) <= 1e-9 and origin < best_origin):
            best_origin = origin
            best_score = score

    return min(max(best_origin, 0.0), max_origin)
