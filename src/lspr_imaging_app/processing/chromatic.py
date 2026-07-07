from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import numpy as np
from scipy import ndimage, signal

try:
    from skimage.registration import phase_cross_correlation as _phase_cross_correlation
except Exception:  # pragma: no cover - optional acceleration path
    _phase_cross_correlation = None

from lspr_imaging_app.domain.models import AreaRoi


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
) -> dict[int, tuple[float, float]]:
    prepared = prepare_registration_image(image)
    response = _corner_response(prepared)
    image_height, image_width = prepared.shape[:2]
    anchors = default_landmark_anchors((image_height, image_width), feature_count)
    regions = _landmark_regions((image_height, image_width), feature_count)
    edge_margin = max(int(patch_radius_px) * 2, 18)
    min_x = edge_margin
    min_y = edge_margin
    max_x = max(image_width - patch_radius_px - 1, min_x)
    max_y = max(image_height - patch_radius_px - 1, min_y)
    detected: dict[int, tuple[float, float]] = {}
    search_radius = max(int(round(min(image_width, image_height) * 0.09)), int(patch_radius_px) * 2, 14)
    for feature_id, anchor in anchors.items():
        center_x = int(round(anchor[0]))
        center_y = int(round(anchor[1]))
        local_radius = search_radius
        if feature_id == 1:
            local_radius = max(int(round(min(image_width, image_height) * 0.045)), int(patch_radius_px) * 2, 10)
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
) -> dict[int, tuple[float, float]]:
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
        ref_x = int(round(point[0]))
        ref_y = int(round(point[1]))
        if (
            ref_x - half < 0
            or ref_y - half < 0
            or ref_x + half > reference_prepared.shape[1]
            or ref_y + half > reference_prepared.shape[0]
        ):
            tracked[int(feature_id)] = (
                float(np.clip(point[0] + shift_x, 0.0, image_width - 1.0)),
                float(np.clip(point[1] + shift_y, 0.0, image_height - 1.0)),
            )
            continue
        reference_patch = reference_prepared[ref_y - half : ref_y + half, ref_x - half : ref_x + half]
        predicted_x = int(round(point[0] + shift_x))
        predicted_y = int(round(point[1] + shift_y))
        search_x0 = max(predicted_x - half - search_radius, 0)
        search_y0 = max(predicted_y - half - search_radius, 0)
        search_x1 = min(predicted_x + half + search_radius, image_width)
        search_y1 = min(predicted_y + half + search_radius, image_height)
        fallback_point = (
            float(np.clip(point[0] + shift_x, 0.0, image_width - 1.0)),
            float(np.clip(point[1] + shift_y, 0.0, image_height - 1.0)),
        )
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
    design = np.column_stack((source_points_xy[:, 0], source_points_xy[:, 1], np.ones(source_points_xy.shape[0])))
    coeff_x, _, _, _ = np.linalg.lstsq(design, target_points_xy[:, 0], rcond=None)
    coeff_y, _, _, _ = np.linalg.lstsq(design, target_points_xy[:, 1], rcond=None)
    return np.vstack((coeff_x, coeff_y)).astype(np.float64, copy=False)


def apply_affine_to_points(points_xy: np.ndarray, affine_matrix: np.ndarray) -> np.ndarray:
    if points_xy.size == 0:
        return points_xy.astype(np.float64, copy=True)
    design = np.column_stack((points_xy[:, 0], points_xy[:, 1], np.ones(points_xy.shape[0], dtype=np.float64)))
    return design @ affine_matrix.T


def affine_residuals(source_points_xy: np.ndarray, target_points_xy: np.ndarray, affine_matrix: np.ndarray) -> np.ndarray:
    predicted = apply_affine_to_points(source_points_xy, affine_matrix)
    return np.sqrt(np.sum((predicted - target_points_xy) ** 2, axis=1))


def invert_affine_matrix(affine_matrix: np.ndarray) -> np.ndarray:
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
    matrix = np.asarray(affine_matrix, dtype=np.float64)
    linear = matrix[:, :2]
    scale_x = float(np.hypot(linear[0, 0], linear[1, 0]))
    scale_y = float(np.hypot(linear[0, 1], linear[1, 1]))
    scale = max((scale_x + scale_y) * 0.5, 1e-12)
    angle_rad = float(np.arctan2(linear[1, 0], linear[0, 0]))
    return scale, angle_rad, float(matrix[0, 2]), float(matrix[1, 2])


def compose_similarity_matrix(scale: float, angle_rad: float, shift_x_px: float, shift_y_px: float) -> np.ndarray:
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
) -> dict[int, tuple[float, float]]:
    centers, _regions = _landmark_sector_layout(image_shape, feature_count)
    return centers


def _landmark_regions(
    image_shape: tuple[int, int],
    feature_count: int,
) -> dict[int, tuple[int, int, int, int]]:
    _anchors, regions = _landmark_sector_layout(image_shape, feature_count)
    return regions


def _landmark_sector_layout(
    image_shape: tuple[int, int],
    feature_count: int,
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[int, int, int, int]]]:
    image_height, image_width = image_shape[:2]
    max_features = 30
    count = max(1, min(int(feature_count), max_features))
    min_dim = float(min(image_width, image_height))
    edge_margin = max(min_dim * 0.06, 14.0)
    mid_x = float(image_width - 1) * 0.5
    mid_y = float(image_height - 1) * 0.5
    aspect = float(image_width) / max(float(image_height), 1.0)
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

    x_coords = np.linspace(edge_margin, float(image_width - 1) - edge_margin, num=max(grid_cols, 1), dtype=np.float64)
    y_coords = np.linspace(edge_margin, float(image_height - 1) - edge_margin, num=max(grid_rows, 1), dtype=np.float64)
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
