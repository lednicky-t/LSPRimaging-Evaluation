from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path

import numpy as np

from lspr_imaging_app.domain.models import AbsorbanceSpectrumResult, AreaRoi, ChromaticTransformModel
from lspr_imaging_app.gui.worker import SensorgramComputationResult, SensorgramPointResult
from lspr_imaging_app.io.dataset import dataset_load_plane_roi, export_ome_zarr_dataset, load_image_array
from lspr_imaging_app.storage.workspace import load_preprocessing, load_processing_profile
from lspr_imaging_app.processing.analysis import (
    absorbance_from_means,
    fit_absorbance_curve,
    metric_value_from_fit,
    metric_value_from_spectrum,
)
from lspr_imaging_app.processing.chromatic import (
    ChromaticRegistrationResult,
    annulus_reach_box,
    apply_affine_to_points,
    auto_track_landmarks_over_wavelengths,
    estimate_affine_chromatic_transform,
    fit_affine_matrix,
    fit_similarity_matrix,
    identity_affine_matrix,
    transformed_annulus_mask,
    transformed_annulus_mask_for_patch,
    transformed_disk_mask,
    transformed_disk_mask_for_patch,
)
from lspr_imaging_app.processing.preprocess import (
    apply_preprocessing,
    apply_spatial_mask,
    apply_spatial_preprocessing,
    create_figure_mask,
    estimate_background_profile,
)
from lspr_imaging_app.processing.reference_selection import bimodal_dip_contrast
from lspr_imaging_app.processing.roi_detection import detect_rois, ignored_pixel_mask, refresh_roi_metrics
from lspr_imaging_app.processing.roi_rasterize import expand_mask, expand_mask_to_patch


def _load_processing_state_task(
    profile_path: Path | None, preprocessing_path: Path | None
) -> tuple[str, object, str | None, str | None]:
    """Mirrors SessionStateManager.load_processing_state_for_dataset's fallback order
    (try the full processing profile, then the lighter preprocessing-only file, then
    give up and let the caller reset to defaults) entirely off the GUI thread - both
    `load_processing_profile`/`load_preprocessing` do blocking file I/O + JSON parsing
    that isn't free once a session's analysis cache holds a few hundred spectra.

    Returns (outcome, payload, profile_error, preprocessing_error):
      - ("profile", <load_processing_profile(...) return tuple>, None, None)
      - ("preprocessing", <PreprocessingSettings>, <profile error text, if any>, None)
      - ("defaults", None, <profile error text, if any>, <preprocessing error text, if any>)
    Errors are returned as text, not raised - each corresponds to a file that failed to
    parse, which the original code treats as "fall back to the next option," not as an
    unexpected failure of this task itself.
    """
    profile_error: str | None = None
    if profile_path is not None and profile_path.exists():
        try:
            return "profile", load_processing_profile(profile_path), None, None
        except Exception as exc:
            profile_error = str(exc)
    if preprocessing_path is not None and preprocessing_path.exists():
        try:
            return "preprocessing", load_preprocessing(preprocessing_path), profile_error, None
        except Exception as exc:
            return "defaults", None, profile_error, str(exc)
    return "defaults", None, profile_error, None


def _process_image_task(path_str: str, preprocessing, rois, external_mask: np.ndarray | None, mask_state) -> np.ndarray:
    raw_image = load_image_array(path_str)
    mask_settings = preprocessing[1] if isinstance(preprocessing, tuple) else None
    external_mask_processed = bool(preprocessing[2]) if isinstance(preprocessing, tuple) and len(preprocessing) > 2 else False
    skip_crop = bool(preprocessing[3]) if isinstance(preprocessing, tuple) and len(preprocessing) > 3 else False
    preprocessing_settings = preprocessing[0] if isinstance(preprocessing, tuple) else preprocessing
    return apply_preprocessing(
        raw_image,
        preprocessing_settings,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        external_mask_processed=external_mask_processed,
        mask_state=mask_state,
        skip_crop=skip_crop,
    )


def _mask_candidate_task(path_str: str, mask_settings, tool_key: str) -> np.ndarray:
    raw_image = load_image_array(path_str).astype(np.float32, copy=False)
    return create_figure_mask(raw_image, mask_settings, tool_key)


def _score_reference_candidates_task(path_strs: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for path_str in path_strs:
        try:
            image = load_image_array(path_str).astype(np.float32, copy=False)
        except Exception:
            scores[path_str] = float("-inf")
            continue
        scores[path_str] = bimodal_dip_contrast(image)
    return scores


def _refresh_roi_metrics_task(
    image: np.ndarray,
    settings,
    rois,
    external_mask: np.ndarray | None,
) -> list[AreaRoi]:
    return refresh_roi_metrics(image, settings, rois, external_mask=external_mask)


def _detect_rois_task(
    image: np.ndarray,
    settings,
    external_mask: np.ndarray | None,
    progress_callback=None,
) -> list[AreaRoi]:
    return detect_rois(image, settings, external_mask=external_mask, progress_callback=progress_callback)


def _background_profile_task(
    path_str: str,
    preprocessing,
    sigma_px: float,
    rois,
    external_mask: np.ndarray | None,
    progress_callback=None,
) -> np.ndarray:
    if progress_callback is not None:
        progress_callback(5, "Background profile: loading image...")
    raw_image = load_image_array(path_str)
    mask_settings = preprocessing[1] if isinstance(preprocessing, tuple) else None
    external_mask_processed = bool(preprocessing[2]) if isinstance(preprocessing, tuple) and len(preprocessing) > 2 else False
    preprocessing_settings = preprocessing[0] if isinstance(preprocessing, tuple) else preprocessing
    if progress_callback is not None:
        progress_callback(25, "Background profile: applying spatial transforms...")
    spatial = apply_spatial_preprocessing(raw_image, preprocessing_settings)
    if external_mask is None:
        processed_external_mask = None
    elif external_mask_processed:
        processed_external_mask = external_mask.astype(bool, copy=False)
    else:
        processed_external_mask = apply_spatial_mask(external_mask, preprocessing_settings)
    if progress_callback is not None:
        progress_callback(55, "Background profile: estimating smooth surface...")
    return estimate_background_profile(
        spatial,
        sigma_px=sigma_px,
        binning=max(int(getattr(preprocessing_settings, "flatten_background_binning", 2)), 1),
        rois=rois,
        mask_settings=mask_settings,
        external_mask=processed_external_mask,
        exclusion_dilation_px=int(getattr(preprocessing_settings, "flatten_background_exclusion_dilation_px", 0)),
    )


def _ome_zarr_export_task(
    dataset,
    destination: Path,
    chunk_size_px: int,
    compression_enabled: bool,
    preprocessing=None,
    shard_mode: str = "per_image",
    *,
    excluded_rules=None,
    skip_excluded: bool = False,
    cancel_event: threading.Event | None = None,
    adaptive_workers_enabled: bool = True,
    adaptive_batch_mb: int = 1024,
    progress_callback=None,
) -> Path:
    return export_ome_zarr_dataset(
        dataset,
        destination,
        chunk_size_px=chunk_size_px,
        compression_enabled=compression_enabled,
        preprocessing=preprocessing,
        shard_mode=shard_mode,
        excluded_rules=excluded_rules,
        skip_excluded=skip_excluded,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        adaptive_workers_enabled=adaptive_workers_enabled,
        adaptive_batch_mb=adaptive_batch_mb,
    )


def _effective_reference_radii(
    roi: AreaRoi,
    default_inner_radius_px: float,
    default_outer_radius_px: float,
) -> tuple[float, float]:
    """Reference-ring radii to use for one ROI.

    Each ROI may carry its own reference_inner_diameter_px/outer_diameter_px
    (set via the ROI table or the "Edit reference ROI region" dialog) to
    override the shared area_roi_settings default for that ROI only. Falls
    back to the shared default when the ROI has no override.
    """
    inner_radius = (
        float(roi.reference_inner_diameter_px) / 2.0
        if roi.reference_inner_diameter_px is not None
        else float(default_inner_radius_px)
    )
    outer_radius = (
        float(roi.reference_outer_diameter_px) / 2.0
        if roi.reference_outer_diameter_px is not None
        else float(default_outer_radius_px)
    )
    inner_radius = max(inner_radius, 0.0)
    outer_radius = max(outer_radius, inner_radius)
    return inner_radius, outer_radius


def _selected_roi_masks_for_spectrum(
    image_shape: tuple[int, int],
    source_rois: list[AreaRoi],
    selected_roi_ids: tuple[int, ...],
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
    affine_matrix: np.ndarray | None,
    *,
    patch_origin_xy: tuple[int, int] = (0, 0),
) -> tuple[np.ndarray, np.ndarray]:
    """Build sample-ROI/reference-ROI masks for the selected ROIs.

    ROI centers (`roi.center_x/center_y`) are always in full processed-image
    coordinates. By default (`patch_origin_xy=(0, 0)` and `image_shape` the
    full image) this returns full-image-sized masks, as before. When called
    with `image_shape` set to a smaller patch's shape and `patch_origin_xy`
    set to that patch's top-left corner (in full-image coordinates), it
    returns masks local to that patch instead — for use with a
    zarr-chunk-aware partial read that only loaded that patch, rather than
    the whole plane.
    """
    image_height, image_width = image_shape[:2]
    roi_mask = np.zeros((image_height, image_width), dtype=bool)
    reference_mask = np.zeros((image_height, image_width), dtype=bool)
    if not source_rois:
        return roi_mask, reference_mask

    selected_ids = set(int(roi_id) for roi_id in selected_roi_ids) if selected_roi_ids else None
    effective_rois = [roi for roi in source_rois if selected_ids is None or roi.area_roi_id in selected_ids]
    if not effective_rois:
        return roi_mask, reference_mask

    default_inner_radius = float(max(reference_inner_radius_px, 0.0))
    default_outer_radius = float(max(reference_outer_radius_px, default_inner_radius))
    use_affine = affine_matrix is not None and not np.allclose(
        np.asarray(affine_matrix, dtype=np.float64),
        identity_affine_matrix(),
        atol=1e-9,
    )
    px0, py0 = patch_origin_xy
    if not use_affine:
        yy, xx = np.indices((image_height, image_width), dtype=np.float32)
        xx = xx + px0
        yy = yy + py0
        for roi in effective_rois:
            # "mask"-geometry ROIs carry their own cropped bitmap and skip the
            # circle/annulus formula below; sample and reference are dispatched
            # independently so one can be a mask while the other stays a circle.
            needs_distance = roi.sample_geometry_type != "mask" or roi.reference_geometry_type not in ("mask", "none")
            distance_sq = (xx - float(roi.center_x)) ** 2 + (yy - float(roi.center_y)) ** 2 if needs_distance else None

            if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
                roi_mask |= expand_mask_to_patch(roi.sample_mask, (px0, py0), (image_height, image_width))
            else:
                roi_mask |= distance_sq <= float(roi.sample_radius_px) ** 2

            inner_radius, outer_radius = _effective_reference_radii(roi, default_inner_radius, default_outer_radius)
            if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
                reference_mask |= expand_mask_to_patch(roi.reference_mask, (px0, py0), (image_height, image_width))
            elif roi.reference_geometry_type != "none" and outer_radius > 0.0:
                outer_mask = distance_sq <= outer_radius**2
                inner_mask = distance_sq < inner_radius**2 if inner_radius > 0.0 else np.zeros_like(outer_mask)
                reference_mask |= outer_mask & ~inner_mask
        reference_mask &= ~roi_mask
        return roi_mask, reference_mask

    # (0, 0) covers both the original full-image call convention (all existing
    # callers) and a patch that happens to start at the image origin — either
    # way, absolute and patch-local coordinates coincide, so the reach-window
    # optimized functions apply directly and give an identical result while
    # keeping their existing performance characteristics. Only a genuinely
    # offset patch needs the explicit-origin "_for_patch" variants below.
    if px0 == 0 and py0 == 0:
        for roi in effective_rois:
            # Note: mask-geometry ROIs are not re-warped by the chromatic
            # affine transform (unlike circle/annulus, which are recomputed
            # per wavelength) — they sit at the same absolute pixel location
            # for every wavelength. Fine for the current opt-in use of "mask"
            # geometry; revisit if chromatic-corrected arbitrary masks are needed.
            if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
                roi_mask |= expand_mask(roi.sample_mask, (image_height, image_width))
            else:
                roi_mask |= transformed_disk_mask(
                    (image_height, image_width),
                    (float(roi.center_x), float(roi.center_y)),
                    float(roi.sample_radius_px),
                    affine_matrix,
                )
            inner_radius, outer_radius = _effective_reference_radii(roi, default_inner_radius, default_outer_radius)
            if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
                reference_mask |= expand_mask(roi.reference_mask, (image_height, image_width))
            elif roi.reference_geometry_type != "none" and outer_radius > 0.0:
                reference_mask |= transformed_annulus_mask(
                    (image_height, image_width),
                    (float(roi.center_x), float(roi.center_y)),
                    float(inner_radius),
                    float(outer_radius),
                    affine_matrix,
                )
    else:
        for roi in effective_rois:
            if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
                roi_mask |= expand_mask_to_patch(roi.sample_mask, (px0, py0), (image_height, image_width))
            else:
                roi_mask |= transformed_disk_mask_for_patch(
                    (px0, py0),
                    (image_height, image_width),
                    (float(roi.center_x), float(roi.center_y)),
                    float(roi.sample_radius_px),
                    affine_matrix,
                )
            inner_radius, outer_radius = _effective_reference_radii(roi, default_inner_radius, default_outer_radius)
            if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
                reference_mask |= expand_mask_to_patch(roi.reference_mask, (px0, py0), (image_height, image_width))
            elif roi.reference_geometry_type != "none" and outer_radius > 0.0:
                reference_mask |= transformed_annulus_mask_for_patch(
                    (px0, py0),
                    (image_height, image_width),
                    (float(roi.center_x), float(roi.center_y)),
                    float(inner_radius),
                    float(outer_radius),
                    affine_matrix,
                )
    reference_mask &= ~roi_mask
    return roi_mask, reference_mask


def compute_roi_union_bounding_box(
    selected_rois: list[AreaRoi],
    reference_outer_radius_px: float,
    affine_matrices: list[np.ndarray | None],
    image_height: int,
    image_width: int,
    margin_px: float = 3.0,
) -> tuple[int, int, int, int] | None:
    """Smallest axis-aligned box (x0, y0, x1, y1), in full processed-image
    coordinates, guaranteed to contain every selected ROI's sample circle and
    reference ROI ring, across every given per-wavelength chromatic transform
    (pass `[None]` if chromatic correction isn't active). Used to decide the
    single region a zarr-chunk-aware partial read needs to cover for every
    wavelength, instead of loading the whole plane. Returns None if there's
    nothing to bound (no ROIs) or the box would cover the whole image anyway.
    """
    if not selected_rois or image_height <= 0 or image_width <= 0:
        return None
    reference_outer = float(max(reference_outer_radius_px, 0.0))
    matrices = affine_matrices if affine_matrices else [None]

    x_min, y_min = float("inf"), float("inf")
    x_max, y_max = float("-inf"), float("-inf")
    for roi in selected_rois:
        cx, cy = float(roi.center_x), float(roi.center_y)
        roi_reference_outer = (
            float(roi.reference_outer_diameter_px) / 2.0
            if roi.reference_outer_diameter_px is not None
            else reference_outer
        )
        roi_reach = max(roi_reference_outer, float(roi.sample_radius_px))
        for matrix in matrices:
            matrix_arr = None if matrix is None else np.asarray(matrix, dtype=np.float64)
            if matrix_arr is None or np.allclose(matrix_arr, identity_affine_matrix(), atol=1e-9):
                tx, ty, reach = cx, cy, roi_reach + margin_px
            else:
                (tx, ty), reach = annulus_reach_box((cx, cy), roi_reach, matrix_arr)
            x_min, x_max = min(x_min, tx - reach), max(x_max, tx + reach)
            y_min, y_max = min(y_min, ty - reach), max(y_max, ty + reach)

    x0 = max(int(np.floor(x_min)), 0)
    y0 = max(int(np.floor(y_min)), 0)
    x1 = min(int(np.ceil(x_max)) + 1, image_width)
    y1 = min(int(np.ceil(y_max)) + 1, image_height)
    if x0 >= x1 or y0 >= y1:
        return None
    return x0, y0, x1, y1


def roi_union_box_is_worth_scoping(
    box: tuple[int, int, int, int],
    image_height: int,
    image_width: int,
    max_area_fraction: float = 0.6,
) -> bool:
    """Whether a scoped read of `box` is meaningfully cheaper than loading the
    whole plane. If selected ROIs are scattered enough that their union
    covers most of the image anyway, a full-plane load is simpler and just as
    fast, so callers should fall back rather than bother with a partial read.
    """
    x0, y0, x1, y1 = box
    full_area = image_height * image_width
    if full_area <= 0:
        return False
    box_area = (x1 - x0) * (y1 - y0)
    return box_area <= max_area_fraction * full_area


def _roi_absorbance_signature(
    spectral_cube_index: int,
    wavelength_values: tuple[float, ...],
    roi: AreaRoi,
    chromatic_signatures: tuple[object, ...],
) -> tuple[object, ...]:
    return (
        int(spectral_cube_index),
        tuple(round(float(value), 6) for value in wavelength_values),
        int(roi.area_roi_id),
        round(float(roi.center_x), 3),
        round(float(roi.center_y), 3),
        round(float(roi.sample_radius_px), 3),
        round(float(roi.reference_inner_diameter_px or 0.0), 3),
        round(float(roi.reference_outer_diameter_px or 0.0), 3),
        roi.sample_geometry_type,
        roi.reference_geometry_type,
        _roi_mask_signature(roi.sample_mask),
        _roi_mask_signature(roi.reference_mask),
        chromatic_signatures,
    )


def _absorbance_roi_mask_cache_key(
    image_shape: tuple[int, int],
    selected_rois: list[AreaRoi],
    selected_roi_ids: tuple[int, ...],
    affine_matrix: np.ndarray | None,
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
) -> tuple[object, ...]:
    affine_signature = None
    if affine_matrix is not None:
        affine_signature = tuple(round(float(value), 6) for value in np.asarray(affine_matrix, dtype=np.float64).ravel())
    return (
        tuple(int(value) for value in image_shape[:2]),
        tuple(int(roi_id) for roi_id in selected_roi_ids),
        tuple(
            (
                int(roi.area_roi_id),
                round(float(roi.center_x), 3),
                round(float(roi.center_y), 3),
                round(float(roi.sample_radius_px), 3),
                round(float(roi.reference_inner_diameter_px or 0.0), 3),
                round(float(roi.reference_outer_diameter_px or 0.0), 3),
                roi.sample_color_hex or "",
                roi.reference_color_hex or "",
                roi.sample_geometry_type,
                roi.reference_geometry_type,
                _roi_mask_signature(roi.sample_mask),
                _roi_mask_signature(roi.reference_mask),
            )
            for roi in selected_rois
        ),
        affine_signature,
        round(float(reference_inner_radius_px), 3),
        round(float(reference_outer_radius_px), 3),
    )


def _roi_mask_signature(roi_mask) -> tuple[object, ...] | None:
    """Cheap fingerprint for a RoiMask so the ROI mask cache correctly
    invalidates when a user edits a "mask"-geometry ROI's bitmap in place
    (same area_roi_id, different pixels).
    """
    if roi_mask is None:
        return None
    return (roi_mask.x0, roi_mask.y0, roi_mask.mask.shape, hash(roi_mask.mask.tobytes()))


def _absorbance_spectrum_task(
    measurement_payload: list[tuple[float, str, list[AreaRoi], np.ndarray | None, bool, np.ndarray | None]],
    preprocessing,
    flatten_mask_settings,
    measurement_settings,
    roi_mask_cache,
    roi_mask_cache_lock,
    roi_mask_cache_max_size: int,
    source_rois: list[AreaRoi],
    selected_roi_ids: tuple[int, ...],
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
    mask_state,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> AbsorbanceSpectrumResult:
    task_started = time.perf_counter()
    load_seconds = 0.0
    roi_seconds = 0.0
    cache_stats = {
        "image_hits": 0,
        "image_builds": 0,
        "roi_hits": 0,
        "roi_builds": 0,
    }
    selected_roi_id_set = set(selected_roi_ids)
    selected_rois = [roi for roi in source_rois if roi.area_roi_id in selected_roi_id_set]
    roi_accumulators: dict[int, dict[str, list[float] | list[int]]] = {
        int(roi.area_roi_id): {
            "wavelengths": [],
            "absorbance": [],
            "sample_mean": [],
            "reference_mean": [],
            "sample_pixel_count": [],
            "reference_pixel_count": [],
        }
        for roi in selected_rois
    }
    wavelengths: list[float] = []
    absorbance_values: list[float] = []
    sample_mean_values: list[float] = []
    reference_mean_values: list[float] = []
    sample_pixel_counts: list[int] = []
    reference_pixel_counts: list[int] = []
    total = max(len(measurement_payload), 1)

    def _build_roi_mask_cache(
        image_shape: tuple[int, int],
        selected_rois_local: list[AreaRoi],
        selected_ids_local: tuple[int, ...],
        affine_matrix_local: np.ndarray | None,
    ) -> dict[str, object]:
        logger = logging.getLogger("lspr_imaging_app.workflow")
        cache_key = _absorbance_roi_mask_cache_key(
            image_shape,
            selected_rois_local,
            selected_ids_local,
            affine_matrix_local,
            reference_inner_radius_px,
            reference_outer_radius_px,
        )
        with roi_mask_cache_lock:
            cached_value = roi_mask_cache.get(cache_key) if hasattr(roi_mask_cache, "get") else None
            if cached_value is not None:
                try:
                    roi_mask_cache.move_to_end(cache_key)
                except Exception:
                    pass
                cache_stats["roi_hits"] += 1
                logger.debug(
                    "ROI cache hit | shape=%sx%s rois=%s",
                    int(image_shape[0]),
                    int(image_shape[1]),
                    len(selected_rois_local),
                )
                return cached_value
        combined_roi_mask, combined_reference_mask = _selected_roi_masks_for_spectrum(
            image_shape,
            source_rois,
            selected_ids_local,
            reference_inner_radius_px,
            reference_outer_radius_px,
            affine_matrix_local,
        )
        per_roi_masks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for roi in selected_rois_local:
            per_roi_masks[int(roi.area_roi_id)] = _selected_roi_masks_for_spectrum(
                image_shape,
                [roi],
                (int(roi.area_roi_id),),
                reference_inner_radius_px,
                reference_outer_radius_px,
                affine_matrix_local,
            )
        cached_value = {
            "shape": tuple(int(value) for value in image_shape[:2]),
            "combined": (combined_roi_mask, combined_reference_mask),
            "per_roi": per_roi_masks,
        }
        with roi_mask_cache_lock:
            roi_mask_cache[cache_key] = cached_value
            try:
                roi_mask_cache.move_to_end(cache_key)
            except Exception:
                pass
            while len(roi_mask_cache) > max(int(roi_mask_cache_max_size), 1):
                roi_mask_cache.popitem(last=False)
        cache_stats["roi_builds"] += 1
        logger.debug(
            "ROI cache built | shape=%sx%s rois=%s",
            int(image_shape[0]),
            int(image_shape[1]),
            len(selected_rois_local),
        )
        return cached_value

    def _load_and_preprocess_measurement(
        item: tuple[int, tuple[float, str, list[AreaRoi], np.ndarray | None, bool, np.ndarray | None]]
    ) -> tuple[int, float, np.ndarray, np.ndarray | None, np.ndarray | None, float]:
        index, (wavelength_nm, path_str, preprocessing_rois, affine_matrix, external_mask_processed, external_mask) = item
        load_started = time.perf_counter()
        cache_info_before = getattr(load_image_array, "cache_info", None)
        before_hits = cache_info_before().hits if callable(cache_info_before) else None
        before_misses = cache_info_before().misses if callable(cache_info_before) else None
        raw_image = load_image_array(path_str)
        if callable(cache_info_before):
            cache_info_after = cache_info_before()
            if before_hits is not None and cache_info_after.hits > before_hits:
                cache_stats["image_hits"] += int(cache_info_after.hits - before_hits)
            if before_misses is not None and cache_info_after.misses > before_misses:
                cache_stats["image_builds"] += int(cache_info_after.misses - before_misses)
        processed = apply_preprocessing(
            raw_image,
            preprocessing,
            rois=preprocessing_rois,
            mask_settings=flatten_mask_settings,
            external_mask=external_mask,
            external_mask_processed=external_mask_processed,
            mask_state=mask_state,
        ).astype(np.float32, copy=False)
        load_duration = time.perf_counter() - load_started
        return int(index), float(wavelength_nm), processed, affine_matrix, external_mask, load_duration

    worker_count = max(1, min(int(os.cpu_count() or 1), 4, len(measurement_payload)))
    prepared_measurements: list[tuple[int, float, np.ndarray, np.ndarray | None, np.ndarray | None, float]] = []
    if worker_count <= 1:
        for index, item in enumerate(measurement_payload, start=1):
            prepared_measurements.append(_load_and_preprocess_measurement((index, item)))
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_load_and_preprocess_measurement, (index, item)) for index, item in enumerate(measurement_payload, start=1)]
            for future in as_completed(futures):
                prepared_measurements.append(future.result())
        prepared_measurements.sort(key=lambda item: item[0])

    for index, wavelength_nm, processed, affine_matrix, external_mask, load_duration in prepared_measurements:
        if cancel_event is not None and cancel_event.is_set():
            return AbsorbanceSpectrumResult(
                wavelengths_nm=np.asarray([], dtype=np.float64),
                absorbance=np.asarray([], dtype=np.float64),
                sample_mean=np.asarray([], dtype=np.float64),
                reference_mean=np.asarray([], dtype=np.float64),
                sample_pixel_count=np.asarray([], dtype=np.int32),
                reference_pixel_count=np.asarray([], dtype=np.int32),
                load_seconds=load_seconds,
                roi_seconds=roi_seconds,
                total_seconds=time.perf_counter() - task_started,
            )
        load_seconds += float(load_duration)

        roi_started = time.perf_counter()
        current_shape = tuple(int(value) for value in processed.shape[:2])
        roi_mask_cache_entry = _build_roi_mask_cache(current_shape, selected_rois, selected_roi_ids, affine_matrix)
        ignored_mask = ignored_pixel_mask(processed, measurement_settings, external_mask=external_mask)
        combined_roi_mask, combined_reference_mask = roi_mask_cache_entry["combined"]  # type: ignore[index]
        roi_mask = np.array(combined_roi_mask, dtype=bool, copy=True)
        reference_mask = np.array(combined_reference_mask, dtype=bool, copy=True)
        roi_mask &= ~ignored_mask
        reference_mask &= ~ignored_mask
        reference_mask &= ~roi_mask

        sample_pixels = processed[roi_mask]
        reference_pixels = processed[reference_mask]
        if sample_pixels.size == 0 or reference_pixels.size == 0:
            sample_mean = float("nan")
            reference_mean = float("nan")
            absorbance = float("nan")
        else:
            sample_mean = float(np.mean(sample_pixels))
            reference_mean = float(np.mean(reference_pixels))
            absorbance = absorbance_from_means(sample_mean, reference_mean)

        wavelengths.append(float(wavelength_nm))
        absorbance_values.append(absorbance)
        sample_mean_values.append(sample_mean)
        reference_mean_values.append(reference_mean)
        sample_pixel_counts.append(int(sample_pixels.size))
        reference_pixel_counts.append(int(reference_pixels.size))

        for roi in selected_rois:
            per_roi_masks = roi_mask_cache_entry["per_roi"]  # type: ignore[index]
            roi_mask_template, reference_mask_template = per_roi_masks[int(roi.area_roi_id)]
            roi_mask_single = np.array(roi_mask_template, dtype=bool, copy=True)
            reference_mask_single = np.array(reference_mask_template, dtype=bool, copy=True)
            roi_mask_single &= ~ignored_mask
            reference_mask_single &= ~ignored_mask
            reference_mask_single &= ~roi_mask_single

            sample_pixels_single = processed[roi_mask_single]
            reference_pixels_single = processed[reference_mask_single]
            if sample_pixels_single.size == 0 or reference_pixels_single.size == 0:
                sample_mean_single = float("nan")
                reference_mean_single = float("nan")
                absorbance_single = float("nan")
            else:
                sample_mean_single = float(np.mean(sample_pixels_single))
                reference_mean_single = float(np.mean(reference_pixels_single))
                absorbance_single = absorbance_from_means(sample_mean_single, reference_mean_single)

            accumulator = roi_accumulators[int(roi.area_roi_id)]
            accumulator["wavelengths"].append(float(wavelength_nm))
            accumulator["absorbance"].append(absorbance_single)
            accumulator["sample_mean"].append(sample_mean_single)
            accumulator["reference_mean"].append(reference_mean_single)
            accumulator["sample_pixel_count"].append(int(sample_pixels_single.size))
            accumulator["reference_pixel_count"].append(int(reference_pixels_single.size))
        roi_seconds += time.perf_counter() - roi_started

        if progress_callback is not None:
            progress_callback(
                int(round(index / total * 100.0)),
                f"Spectral absorbance {index}/{total}: {float(wavelength_nm):g} nm",
            )

    roi_results: dict[int, AbsorbanceSpectrumResult] = {}
    for roi in selected_rois:
        data = roi_accumulators[int(roi.area_roi_id)]
        roi_results[int(roi.area_roi_id)] = AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(data["wavelengths"], dtype=np.float64),
            absorbance=np.asarray(data["absorbance"], dtype=np.float64),
            sample_mean=np.asarray(data["sample_mean"], dtype=np.float64),
            reference_mean=np.asarray(data["reference_mean"], dtype=np.float64),
            sample_pixel_count=np.asarray(data["sample_pixel_count"], dtype=np.int32),
            reference_pixel_count=np.asarray(data["reference_pixel_count"], dtype=np.int32),
        )

    result = AbsorbanceSpectrumResult(
        wavelengths_nm=np.asarray(wavelengths, dtype=np.float64),
        absorbance=np.asarray(absorbance_values, dtype=np.float64),
        sample_mean=np.asarray(sample_mean_values, dtype=np.float64),
        reference_mean=np.asarray(reference_mean_values, dtype=np.float64),
        sample_pixel_count=np.asarray(sample_pixel_counts, dtype=np.int32),
        reference_pixel_count=np.asarray(reference_pixel_counts, dtype=np.int32),
        load_seconds=load_seconds,
        roi_seconds=roi_seconds,
        total_seconds=time.perf_counter() - task_started,
        area_roi_results=roi_results,
    )
    logging.getLogger("lspr_imaging_app.workflow").debug(
        "Spec cache summary | img hit=%s build=%s | roi hit=%s build=%s",
        int(cache_stats["image_hits"]),
        int(cache_stats["image_builds"]),
        int(cache_stats["roi_hits"]),
        int(cache_stats["roi_builds"]),
    )
    return result


def _absorbance_spectrum_fast_task(
    dataset,
    spectral_cube_index: int,
    measurement_payload: list[tuple[float, np.ndarray | None, np.ndarray | None]],
    record_map: dict,
    selected_rois: list[AreaRoi],
    selected_roi_ids: tuple[int, ...],
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
    box: tuple[int, int, int, int],
    preprocessing,
    raw_shape: tuple[int, int],
    mask_state=None,
    background_mask_settings=None,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> AbsorbanceSpectrumResult:
    """Fast multi-ROI absorbance spectrum using OME-Zarr chunk-aware spatial reads.

    `box` is a single bounding region, in full PROCESSED-image coordinates
    (matching ROI centers, external masks, etc.), precomputed by the caller to
    cover every selected ROI's sample circle and reference ROI ring across all
    wavelengths' chromatic transforms.

    Normally (no background flattening), rotation/flip/crop are handled by
    reading only the (possibly larger) enclosing raw-space box
    (raw_bounding_box_for_processed_box) and resampling it directly into
    `box` (resample_raw_patch_to_processed_box) — the same affine-transform
    approach already validated for OME-Zarr export, just scoped to a small
    region instead of the whole plane.

    When `preprocessing.flatten_background_enabled` is on, the background
    estimate genuinely needs the whole image (same cost as the slow path,
    same as TIFF — nothing scoped there), so this instead loads the full
    plane and calls apply_preprocessing(..., region=box), which threads the
    region through to flatten_background so only the ROI-chunk's background
    values get computed/upsampled/subtracted — the same "load once, only
    finish the last step for the region actually read" idea, just with a
    full-plane load instead of a chunk-aware one for this one case.

    Supports multiple ROIs, chromatic correction, and external/ignored-pixel
    masks either way.
    """
    from lspr_imaging_app.processing.preprocess import (
        apply_preprocessing,
        raw_bounding_box_for_processed_box,
        resample_raw_patch_to_processed_box,
    )

    task_started = time.perf_counter()
    x0, y0, x1, y1 = box
    patch_h, patch_w = y1 - y0, x1 - x0
    flatten_background_enabled = bool(getattr(preprocessing, "flatten_background_enabled", False))
    if not flatten_background_enabled:
        raw_x0, raw_y0, raw_x1, raw_y1 = raw_bounding_box_for_processed_box(raw_shape, preprocessing, box)

    roi_accumulators: dict[int, dict[str, list]] = {
        int(roi.area_roi_id): {
            "wavelengths": [], "absorbance": [], "sample_mean": [], "reference_mean": [],
            "sample_pixel_count": [], "reference_pixel_count": [],
        }
        for roi in selected_rois
    }
    total = max(len(measurement_payload), 1)

    def _load_wl(item: tuple) -> tuple:
        index, (wavelength_nm, affine_matrix, external_mask) = item
        empty_per_roi = {
            int(roi.area_roi_id): (float("nan"), float("nan"), float("nan"), 0, 0) for roi in selected_rois
        }
        if cancel_event is not None and cancel_event.is_set():
            return (index, float(wavelength_nm), (float("nan"), float("nan"), float("nan"), 0, 0), empty_per_roi)
        record = record_map.get((int(spectral_cube_index), float(wavelength_nm)))
        if record is None:
            return (index, float(wavelength_nm), (float("nan"), float("nan"), float("nan"), 0, 0), empty_per_roi)

        if flatten_background_enabled:
            raw_image = load_image_array(str(record.path))
            background_rois = selected_rois if bool(getattr(preprocessing, "flatten_background_exclude_area_rois", True)) else None
            background_mask = background_mask_settings if bool(getattr(preprocessing, "flatten_background_exclude_mask", False)) else None
            patch = apply_preprocessing(
                raw_image, preprocessing,
                rois=background_rois, mask_settings=background_mask,
                external_mask=external_mask, external_mask_processed=True,
                mask_state=mask_state, region=box,
            )
            patch = np.asarray(patch, dtype=np.float32)
        else:
            raw_patch = dataset_load_plane_roi(dataset, int(spectral_cube_index), float(wavelength_nm), raw_y0, raw_y1, raw_x0, raw_x1, record=record)
            if raw_patch is None or raw_patch.size == 0:
                return (index, float(wavelength_nm), (float("nan"), float("nan"), float("nan"), 0, 0), empty_per_roi)
            patch = resample_raw_patch_to_processed_box(
                np.asarray(raw_patch, dtype=np.float32), (raw_x0, raw_y0), raw_shape, preprocessing, box,
            )

        ignored_patch = None
        if external_mask is not None:
            mask_full = np.asarray(external_mask, dtype=bool)
            if mask_full.shape[0] >= y1 and mask_full.shape[1] >= x1:
                ignored_patch = mask_full[y0:y1, x0:x1]

        def _means_for(rois_subset: list[AreaRoi], ids_subset: tuple[int, ...]) -> tuple[float, float, float, int, int]:
            roi_mask, reference_mask = _selected_roi_masks_for_spectrum(
                (patch_h, patch_w), rois_subset, ids_subset, reference_inner_radius_px, reference_outer_radius_px,
                affine_matrix, patch_origin_xy=(x0, y0),
            )
            if ignored_patch is not None:
                roi_mask = roi_mask & ~ignored_patch
                reference_mask = reference_mask & ~ignored_patch
            sample_pixels = patch[roi_mask]
            reference_pixels = patch[reference_mask]
            if sample_pixels.size == 0 or reference_pixels.size == 0:
                return float("nan"), float("nan"), float("nan"), int(sample_pixels.size), int(reference_pixels.size)
            sm = float(np.mean(sample_pixels))
            rm = float(np.mean(reference_pixels))
            return absorbance_from_means(sm, rm), sm, rm, int(sample_pixels.size), int(reference_pixels.size)

        combined = _means_for(selected_rois, selected_roi_ids)
        per_roi = {
            int(roi.area_roi_id): _means_for([roi], (int(roi.area_roi_id),)) for roi in selected_rois
        }
        return (index, float(wavelength_nm), combined, per_roi)

    worker_count = max(1, min(max(int(os.cpu_count() or 2) // 2, 2), 8, len(measurement_payload)))
    indexed = list(enumerate(measurement_payload))
    results: list = [None] * len(measurement_payload)

    if worker_count <= 1:
        for item in indexed:
            results[item[0]] = _load_wl(item)
            if progress_callback is not None:
                progress_callback(
                    int(round((item[0] + 1) / total * 100)),
                    f"Fast spectrum {item[0]+1}/{total}: {float(item[1][0]):g} nm",
                )
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(_load_wl, item): item[0] for item in indexed}
            done_count = 0
            for future in as_completed(future_map):
                idx = int(future_map[future])
                results[idx] = future.result()
                done_count += 1
                if progress_callback is not None:
                    progress_callback(
                        int(round(done_count / total * 100)),
                        f"Fast spectrum {done_count}/{total}",
                    )

    wavelengths_out: list[float] = []
    absorbance_values: list[float] = []
    sample_mean_values: list[float] = []
    reference_mean_values: list[float] = []
    sample_pixel_counts: list[int] = []
    reference_pixel_counts: list[int] = []

    for r in results:
        if r is None:
            continue
        _, wl, (abs_val, sm, rm, spc, rpc), per_roi = r
        wavelengths_out.append(float(wl))
        absorbance_values.append(float(abs_val))
        sample_mean_values.append(float(sm))
        reference_mean_values.append(float(rm))
        sample_pixel_counts.append(int(spc))
        reference_pixel_counts.append(int(rpc))
        for roi in selected_rois:
            roi_abs, roi_sm, roi_rm, roi_spc, roi_rpc = per_roi[int(roi.area_roi_id)]
            accumulator = roi_accumulators[int(roi.area_roi_id)]
            accumulator["wavelengths"].append(float(wl))
            accumulator["absorbance"].append(float(roi_abs))
            accumulator["sample_mean"].append(float(roi_sm))
            accumulator["reference_mean"].append(float(roi_rm))
            accumulator["sample_pixel_count"].append(int(roi_spc))
            accumulator["reference_pixel_count"].append(int(roi_rpc))

    roi_results: dict[int, AbsorbanceSpectrumResult] = {}
    for roi in selected_rois:
        data = roi_accumulators[int(roi.area_roi_id)]
        roi_results[int(roi.area_roi_id)] = AbsorbanceSpectrumResult(
            wavelengths_nm=np.asarray(data["wavelengths"], dtype=np.float64),
            absorbance=np.asarray(data["absorbance"], dtype=np.float64),
            sample_mean=np.asarray(data["sample_mean"], dtype=np.float64),
            reference_mean=np.asarray(data["reference_mean"], dtype=np.float64),
            sample_pixel_count=np.asarray(data["sample_pixel_count"], dtype=np.int32),
            reference_pixel_count=np.asarray(data["reference_pixel_count"], dtype=np.int32),
        )

    return AbsorbanceSpectrumResult(
        wavelengths_nm=np.asarray(wavelengths_out, dtype=np.float64),
        absorbance=np.asarray(absorbance_values, dtype=np.float64),
        sample_mean=np.asarray(sample_mean_values, dtype=np.float64),
        reference_mean=np.asarray(reference_mean_values, dtype=np.float64),
        sample_pixel_count=np.asarray(sample_pixel_counts, dtype=np.int32),
        reference_pixel_count=np.asarray(reference_pixel_counts, dtype=np.int32),
        total_seconds=time.perf_counter() - task_started,
        area_roi_results=roi_results,
    )


def _sensorgram_metric_task(
    spectral_cube_payloads_or_spectral_cubes,
    poly_order: int,
    metric_key: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
    partial_callback=None,
    spectral_cube_payload_builder=None,
    task_fn=None,
    spectral_cube_result_cache_get=None,
    spectral_cube_result_cache_store=None,
    wl_min: float | None = None,
    wl_max: float | None = None,
    fit_method_key: str = "poly",
) -> SensorgramComputationResult:
    task_started = time.perf_counter()
    spectral_cube_payloads: list[tuple[int, tuple[object, ...]]] = []
    total_input_count = len(spectral_cube_payloads_or_spectral_cubes) if hasattr(spectral_cube_payloads_or_spectral_cubes, "__len__") else 0
    prep_seconds = 0.0
    fit_seconds = 0.0
    if spectral_cube_payload_builder is not None:
        spectral_cubes = [int(spectral_cube_index) for spectral_cube_index in spectral_cube_payloads_or_spectral_cubes]
        total_input_count = len(spectral_cubes)
        if not spectral_cubes:
            return SensorgramComputationResult(
                spectral_cube_indices=np.asarray([], dtype=np.int32),
                metric_values=np.asarray([], dtype=np.float64),
                metric_signal=np.asarray([], dtype=np.float64),
                completed_count=0,
                total_count=0,
                prep_seconds=0.0,
                fit_seconds=0.0,
                total_seconds=time.perf_counter() - task_started,
                cancelled=False,
            )
        prep_started = time.perf_counter()
        completed = 0
        built_payloads: list[tuple[int, tuple[object, ...]]] = []
        worker_count = max(2, min(4, os.cpu_count() or 2))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(spectral_cube_payload_builder, int(spectral_cube_index)): int(spectral_cube_index) for spectral_cube_index in spectral_cubes}
            for future in as_completed(future_map):
                spectral_cube_index = int(future_map[future])
                if cancel_event is not None and cancel_event.is_set():
                    return SensorgramComputationResult(
                        spectral_cube_indices=np.asarray([], dtype=np.int32),
                        metric_values=np.asarray([], dtype=np.float64),
                        metric_signal=np.asarray([], dtype=np.float64),
                        completed_count=len(built_payloads),
                        total_count=total_input_count,
                        cancelled=True,
                    )
                payload = future.result()
                if payload is not None:
                    built_payloads.append((spectral_cube_index, payload))
                completed += 1
                if progress_callback is not None:
                    progress_callback(
                        int(round((completed / max(total_input_count, 1)) * 20.0)),
                        f"Preparing sensorgram {completed}/{total_input_count} spectral cubes",
                    )
        prep_seconds = time.perf_counter() - prep_started
        spectral_cube_payloads = sorted(built_payloads, key=lambda item: item[0])
    else:
        spectral_cube_payloads = list(spectral_cube_payloads_or_spectral_cubes)
    spectral_cube_indices: list[int] = []
    metric_values: list[float] = []
    metric_signals: list[float] = []
    total = max(len(spectral_cube_payloads), 1)
    compute_base = 20.0 if spectral_cube_payload_builder is not None else 0.0
    compute_span = 80.0 if spectral_cube_payload_builder is not None else 100.0
    compute_started = time.perf_counter()

    for index, (spectral_cube_index, payload) in enumerate(spectral_cube_payloads, start=1):
        if cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                spectral_cube_indices=np.asarray(spectral_cube_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(spectral_cube_indices),
                total_count=len(spectral_cube_payloads),
                prep_seconds=prep_seconds,
                fit_seconds=fit_seconds,
                total_seconds=time.perf_counter() - task_started,
                cancelled=True,
            )

        def spectral_cube_progress_callback(percent: int, text: str | None = None, *, spectral_cube_number: int = int(spectral_cube_index), position: int = index) -> None:
            if progress_callback is None:
                return
            inner_percent = float(np.clip(float(percent), 0.0, 100.0))
            overall = compute_base + (((position - 1) + (inner_percent / 100.0)) / total) * compute_span
            progress_callback(
                int(round(overall)),
                text or f"Sensorgram {position}/{total}: spectral cube {spectral_cube_number}",
            )

        spectrum = spectral_cube_result_cache_get(spectral_cube_index) if spectral_cube_result_cache_get is not None else None
        if spectrum is None:
            _active_task = task_fn if task_fn is not None else _absorbance_spectrum_task
            spectrum = _active_task(
                *payload,
                cancel_event=cancel_event,
                progress_callback=spectral_cube_progress_callback,
            )
            if spectral_cube_result_cache_store is not None:
                spectral_cube_result_cache_store(spectral_cube_index, spectrum)
        if cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                spectral_cube_indices=np.asarray(spectral_cube_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(spectral_cube_indices),
                total_count=len(spectral_cube_payloads),
                cancelled=True,
            )

        if fit_method_key == "none":
            metric_value, metric_signal = metric_value_from_spectrum(
                spectrum.wavelengths_nm, spectrum.absorbance, metric_key, wl_min=wl_min, wl_max=wl_max
            )
        else:
            fit = fit_absorbance_curve(
                spectrum.wavelengths_nm,
                spectrum.absorbance,
                poly_order=poly_order,
                wl_min=wl_min,
                wl_max=wl_max,
            )
            metric_value, metric_signal = metric_value_from_fit(fit, metric_key)
        metric_float = float(metric_value) if metric_value is not None and np.isfinite(metric_value) else float("nan")
        signal_float = float(metric_signal) if metric_signal is not None and np.isfinite(metric_signal) else float("nan")

        spectral_cube_indices.append(int(spectral_cube_index))
        metric_values.append(metric_float)
        metric_signals.append(signal_float)

        if partial_callback is not None:
            partial_callback(
                SensorgramPointResult(
                    spectral_cube_index=int(spectral_cube_index),
                    metric_value=None if not np.isfinite(metric_float) else metric_float,
                    metric_signal=None if not np.isfinite(signal_float) else signal_float,
                )
            )
        if progress_callback is not None:
            progress_callback(
                int(round(compute_base + (index / total) * compute_span)),
                f"Sensorgram {index}/{total}: spectral cube {int(spectral_cube_index)}",
            )
    fit_seconds = time.perf_counter() - compute_started

    return SensorgramComputationResult(
        spectral_cube_indices=np.asarray(spectral_cube_indices, dtype=np.int32),
        metric_values=np.asarray(metric_values, dtype=np.float64),
        metric_signal=np.asarray(metric_signals, dtype=np.float64),
        completed_count=len(spectral_cube_indices),
        total_count=len(spectral_cube_payloads),
        prep_seconds=prep_seconds,
        fit_seconds=fit_seconds,
        total_seconds=time.perf_counter() - task_started,
        cancelled=False,
    )


def _auto_chromatic_landmarks_task(
    sample_payload: list[tuple[int, float, str]],
    preprocessing,
    feature_count: int,
    subpixel_precision: int,
    spot_radius_px: float = 10.0,
    spot_mode: str = "dark",
    area_roi_settings=None,
    progress_callback=None,
) -> list[tuple[int, int, float, float, float]]:
    if not sample_payload:
        return []
    preprocessing_settings = deepcopy(preprocessing)
    preprocessing_settings.flatten_background_enabled = False
    preprocessing_settings.chromatic_correction_enabled = False
    processed_images: list[tuple[int, float, np.ndarray]] = []
    total = max(len(sample_payload), 1)
    for index, (spectral_cube_index, wavelength, path_str) in enumerate(sample_payload, start=1):
        raw_image = load_image_array(path_str)
        processed = apply_spatial_preprocessing(raw_image, preprocessing_settings)
        processed_images.append((int(spectral_cube_index), float(wavelength), processed))
        if progress_callback is not None:
            progress_callback(
                int(round((index / total) * 40)),
                f"Loading sampled chromatic image {index}/{total}...",
            )
    sample_spectral_cube_index = processed_images[0][0]
    images_by_wavelength = [(wavelength, image) for _spectral_cube, wavelength, image in processed_images]
    landmark_kind = str(getattr(preprocessing, "chromatic_landmark_kind", "corner") or "corner")
    grid_bounds = getattr(preprocessing, "chromatic_grid_bounds", None)
    bounds = None
    if (
        grid_bounds is not None
        and bool(getattr(grid_bounds, "enabled", False))
        and int(getattr(grid_bounds, "width", 0)) > 0
        and int(getattr(grid_bounds, "height", 0)) > 0
    ):
        bounds = (int(grid_bounds.x), int(grid_bounds.y), int(grid_bounds.width), int(grid_bounds.height))
    tracking_total = max(len(images_by_wavelength) - 1, 1)

    def on_tracking_progress(step_index: int, step_total: int) -> None:
        if progress_callback is not None:
            progress_callback(
                int(round(50 + (step_index / max(step_total, 1)) * 50)),
                f"Tracked reference points on sampled image {step_index + 1}/{tracking_total + 1}...",
            )

    if progress_callback is not None:
        progress_callback(45, "Detecting reference points on the first sampled image...")
    trajectories = auto_track_landmarks_over_wavelengths(
        images_by_wavelength,
        int(feature_count),
        kind=landmark_kind,
        spot_radius_px=float(spot_radius_px),
        spot_mode=str(spot_mode),
        subpixel_precision=int(subpixel_precision),
        area_roi_settings=area_roi_settings,
        bounds=bounds,
        progress_callback=on_tracking_progress,
    )
    if progress_callback is not None:
        progress_callback(50, f"Detected reference points on sampled image 1/{total}.")
    observations: list[tuple[int, int, float, float, float]] = [
        (int(feature_id), int(sample_spectral_cube_index), float(wavelength), float(point[0]), float(point[1]))
        for feature_id, per_wavelength in sorted(trajectories.items())
        for wavelength, point in sorted(per_wavelength.items())
    ]
    return observations


def _normalized_odd_count(value: int, minimum: int, maximum: int) -> int:
    normalized = max(int(value), int(minimum))
    if normalized % 2 == 0:
        normalized += 1
    if normalized > int(maximum):
        normalized = int(maximum)
        if normalized % 2 == 0:
            normalized = max(int(minimum), normalized - 1)
    return max(normalized, int(minimum))


def _sampled_wavelengths(wavelengths_nm: list[float], sample_count: int) -> list[float]:
    if not wavelengths_nm:
        return []
    maximum = len(wavelengths_nm)
    minimum = 1 if maximum == 1 else min(3, maximum)
    count = min(_normalized_odd_count(sample_count, minimum, maximum), maximum)
    if count % 2 == 0:
        count = max(1, count - 1)
    if count == 1:
        return [float(wavelengths_nm[len(wavelengths_nm) // 2])]
    indices = [int(round(index * (maximum - 1) / (count - 1))) for index in range(count)]
    indices = sorted(dict.fromkeys(indices))
    return [float(wavelengths_nm[index]) for index in indices]


def _estimate_chromatic_models_task(
    record_specs: list[tuple[int, float, str]],
    preprocessing,
    reference_key: tuple[int, float],
    landmarks_payload: list[tuple[int, int, float, float, float]] | None = None,
    progress_callback=None,
) -> list[ChromaticTransformModel]:
    mode = str(getattr(preprocessing, "chromatic_registration_mode", "landmark_radial") or "landmark_radial")
    landmark_model = str(getattr(preprocessing, "chromatic_landmark_model", "similarity") or "similarity")
    models: list[ChromaticTransformModel] = []
    if mode == "landmark_radial":
        if not landmarks_payload:
            raise ValueError("No chromatic reference points are available. Start the radial workflow and mark reference points first.")
        reference_spectral_cube, reference_wavelength = int(reference_key[0]), float(reference_key[1])
        all_wavelengths = sorted({float(wavelength) for _spectral_cube, wavelength, _path in record_specs})
        # Sample candidates must come from the reference cube's own (already
        # exclusion-filtered) wavelengths, not the union across every cube --
        # otherwise this can pick a "sample wavelength" that was never offered
        # to the user for landmark-marking (excluded on the reference cube but
        # not elsewhere), producing a spurious "missing reference point" error.
        reference_cube_wavelengths = sorted(
            {float(wavelength) for spectral_cube_index, wavelength, _path in record_specs if int(spectral_cube_index) == reference_spectral_cube}
        )
        sampled_wavelengths = _sampled_wavelengths(
            reference_cube_wavelengths,
            int(getattr(preprocessing, "chromatic_sample_image_count", 5)),
        )
        feature_count = max(int(getattr(preprocessing, "chromatic_feature_count", 5)), 1)
        expected_feature_ids = list(range(1, feature_count + 1))

        landmarks_by_wavelength: dict[float, dict[int, tuple[float, float]]] = {}
        for landmark_id, spectral_cube_index, wavelength, x_px, y_px in landmarks_payload:
            if int(spectral_cube_index) != reference_spectral_cube:
                continue
            marks = landmarks_by_wavelength.setdefault(float(wavelength), {})
            marks[int(landmark_id)] = (float(x_px), float(y_px))

        reference_landmarks = landmarks_by_wavelength.get(reference_wavelength, {})
        missing_reference = [feature_id for feature_id in expected_feature_ids if feature_id not in reference_landmarks]
        if missing_reference:
            raise ValueError(
                f"Reference wavelength {reference_wavelength:g} nm is missing reference point(s): "
                + ", ".join(str(feature_id) for feature_id in missing_reference)
            )

        sample_matrices: dict[float, np.ndarray] = {}
        sample_rmse: dict[float, float] = {}
        direct_feature_counts: dict[float, int] = {}
        total = max(len(sampled_wavelengths), 1)
        reference_points = np.asarray(
            [reference_landmarks[feature_id] for feature_id in expected_feature_ids],
            dtype=np.float64,
        )
        for index, wavelength in enumerate(sampled_wavelengths, start=1):
            marks = landmarks_by_wavelength.get(float(wavelength), {})
            missing = [feature_id for feature_id in expected_feature_ids if feature_id not in marks]
            if missing:
                raise ValueError(
                    f"Sample wavelength {wavelength:g} nm is missing reference point(s): "
                    + ", ".join(str(feature_id) for feature_id in missing)
                )
            if abs(float(wavelength) - reference_wavelength) < 1e-6:
                matrix = identity_affine_matrix()
                rmse = 0.0
            else:
                target_points = np.asarray([marks[feature_id] for feature_id in expected_feature_ids], dtype=np.float64)
                if landmark_model == "similarity" and len(expected_feature_ids) >= 2:
                    matrix = fit_similarity_matrix(reference_points, target_points)
                else:
                    matrix = fit_affine_matrix(reference_points, target_points)
                residuals = np.sqrt(np.sum((apply_affine_to_points(reference_points, matrix) - target_points) ** 2, axis=1))
                rmse = float(np.sqrt(np.mean(residuals**2))) if residuals.size else 0.0
            sample_matrices[float(wavelength)] = matrix
            sample_rmse[float(wavelength)] = rmse
            direct_feature_counts[float(wavelength)] = len(expected_feature_ids)
            if progress_callback is not None:
                progress_callback(
                    int(round(index / total * 100.0)),
                    f"Chromatic correction {index}/{total}: {wavelength:g} nm",
                )

        sorted_sample_wavelengths = sorted(sample_matrices)
        matrix_values = []
        rmse_values = []
        for wavelength in sorted_sample_wavelengths:
            matrix_values.append(np.asarray(sample_matrices[wavelength], dtype=np.float64))
            rmse_values.append(sample_rmse[wavelength])
        sample_axis = np.asarray(sorted_sample_wavelengths, dtype=np.float64)
        matrix_values_array = np.asarray(matrix_values, dtype=np.float64)

        matrices_by_wavelength: dict[float, np.ndarray] = {}
        rmse_by_wavelength: dict[float, float] = {}
        feature_counts_by_wavelength: dict[float, int] = {}
        for wavelength in all_wavelengths:
            wavelength_f64 = float(wavelength)
            if wavelength_f64 in sample_matrices:
                matrices_by_wavelength[wavelength_f64] = sample_matrices[wavelength_f64]
                rmse_by_wavelength[wavelength_f64] = sample_rmse[wavelength_f64]
                feature_counts_by_wavelength[wavelength_f64] = direct_feature_counts[wavelength_f64]
                continue
            interpolated_matrix = np.empty((2, 3), dtype=np.float64)
            for row in range(2):
                for col in range(3):
                    interpolated_matrix[row, col] = float(
                        np.interp(
                            wavelength_f64,
                            sample_axis,
                            matrix_values_array[:, row, col],
                        )
                    )
            matrices_by_wavelength[wavelength_f64] = interpolated_matrix
            rmse_by_wavelength[wavelength_f64] = float(np.interp(wavelength_f64, sample_axis, np.asarray(rmse_values, dtype=np.float64)))
            feature_counts_by_wavelength[wavelength_f64] = len(expected_feature_ids)

        landmark_model_kind = "landmark_similarity" if landmark_model == "similarity" else "landmark_affine"
        for spectral_cube_index, wavelength, _path_str in record_specs:
            matrix = matrices_by_wavelength[float(wavelength)]
            models.append(
                ChromaticTransformModel(
                    spectral_cube_index=int(spectral_cube_index),
                    wavelength_nm=float(wavelength),
                    model_kind=landmark_model_kind,
                    affine_matrix=[[float(value) for value in row] for row in matrix.tolist()],
                    global_shift_x_px=float(matrix[0, 2]),
                    global_shift_y_px=float(matrix[1, 2]),
                    rmse_px=float(rmse_by_wavelength[float(wavelength)]),
                    mean_score=1.0,
                    min_score=1.0,
                    tile_count=int(feature_counts_by_wavelength[float(wavelength)]),
                    inlier_count=int(feature_counts_by_wavelength[float(wavelength)]),
                )
            )
        return models

    reference_path = next((path_str for spectral_cube_index, wavelength, path_str in record_specs if (spectral_cube_index, wavelength) == reference_key), None)
    if reference_path is None:
        raise ValueError("Reference image is missing from the dataset.")
    reference_raw = load_image_array(reference_path)
    reference_processed = apply_spatial_preprocessing(reference_raw, preprocessing)
    tile_size = int(max(preprocessing.chromatic_tile_size_px, 24))
    search_radius = int(max(preprocessing.chromatic_search_radius_px, 6))
    total = max(len(record_specs), 1)
    for index, (spectral_cube_index, wavelength, path_str) in enumerate(record_specs, start=1):
        if (spectral_cube_index, wavelength) == reference_key:
            result = ChromaticRegistrationResult(
                affine_matrix=identity_affine_matrix(),
                global_shift_x_px=0.0,
                global_shift_y_px=0.0,
                rmse_px=0.0,
                mean_score=0.0,
                min_score=0.0,
                tile_count=0,
                inlier_count=0,
            )
        else:
            target_raw = load_image_array(path_str)
            target_processed = apply_spatial_preprocessing(target_raw, preprocessing)
            result = estimate_affine_chromatic_transform(
                reference_processed,
                target_processed,
                mode=mode,
                tile_size_px=tile_size,
                search_radius_px=search_radius,
                subpixel_precision=int(getattr(preprocessing, "chromatic_subpixel_precision", 4)),
            )
        models.append(
            ChromaticTransformModel(
                spectral_cube_index=int(spectral_cube_index),
                wavelength_nm=float(wavelength),
                model_kind="image_affine",
                affine_matrix=[[float(value) for value in row] for row in result.affine_matrix.tolist()],
                global_shift_x_px=float(result.global_shift_x_px),
                global_shift_y_px=float(result.global_shift_y_px),
                rmse_px=float(result.rmse_px),
                mean_score=float(result.mean_score),
                min_score=float(result.min_score),
                tile_count=int(result.tile_count),
                inlier_count=int(result.inlier_count),
            )
        )
        if progress_callback is not None:
            progress_callback(
                int(round(index / total * 100.0)),
                f"Chromatic correction {index}/{total}: {wavelength:g} nm spectral cube {spectral_cube_index}",
            )
    return models
