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
from lspr_imaging_app.processing.analysis import absorbance_from_means, fit_absorbance_curve, metric_value_from_fit
from lspr_imaging_app.processing.chromatic import (
    ChromaticRegistrationResult,
    apply_affine_to_points,
    detect_regional_landmarks,
    estimate_affine_chromatic_transform,
    fit_affine_matrix,
    identity_affine_matrix,
    track_landmarks,
    transformed_annulus_mask,
    transformed_disk_mask,
)
from lspr_imaging_app.processing.preprocess import (
    apply_preprocessing,
    apply_spatial_mask,
    apply_spatial_preprocessing,
    estimate_background_profile,
)
from lspr_imaging_app.processing.spot_detection import detect_spots, ignored_pixel_mask, refresh_roi_metrics


def _process_image_task(path_str: str, preprocessing, rois, external_mask: np.ndarray | None, mask_state) -> np.ndarray:
    raw_image = load_image_array(path_str)
    mask_settings = preprocessing[1] if isinstance(preprocessing, tuple) else None
    external_mask_processed = bool(preprocessing[2]) if isinstance(preprocessing, tuple) and len(preprocessing) > 2 else False
    preprocessing_settings = preprocessing[0] if isinstance(preprocessing, tuple) else preprocessing
    return apply_preprocessing(
        raw_image,
        preprocessing_settings,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        external_mask_processed=external_mask_processed,
        mask_state=mask_state,
    )


def _refresh_roi_metrics_task(
    image: np.ndarray,
    settings,
    rois,
    external_mask: np.ndarray | None,
) -> list[AreaRoi]:
    return refresh_roi_metrics(image, settings, rois, external_mask=external_mask)


def _detect_spots_task(
    image: np.ndarray,
    settings,
    external_mask: np.ndarray | None,
    progress_callback=None,
) -> list[AreaRoi]:
    return detect_spots(image, settings, external_mask=external_mask, progress_callback=progress_callback)


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
    )


def _ome_zarr_export_task(
    dataset,
    destination: Path,
    chunk_size_px: int,
    compression_enabled: bool,
    preprocessing=None,
    *,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> Path:
    return export_ome_zarr_dataset(
        dataset,
        destination,
        chunk_size_px=chunk_size_px,
        compression_enabled=compression_enabled,
        preprocessing=preprocessing,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


def _selected_roi_masks_for_spectrum(
    image_shape: tuple[int, int],
    source_rois: list[AreaRoi],
    selected_spot_ids: tuple[int, ...],
    ring_inner_radius_px: float,
    ring_outer_radius_px: float,
    affine_matrix: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    image_height, image_width = image_shape[:2]
    roi_mask = np.zeros((image_height, image_width), dtype=bool)
    ring_mask = np.zeros((image_height, image_width), dtype=bool)
    if not source_rois:
        return roi_mask, ring_mask

    selected_ids = set(int(spot_id) for spot_id in selected_spot_ids) if selected_spot_ids else None
    effective_rois = [roi for roi in source_rois if selected_ids is None or roi.area_roi_id in selected_ids]
    if not effective_rois:
        return roi_mask, ring_mask

    ring_inner_radius = float(max(ring_inner_radius_px, 0.0))
    ring_outer_radius = float(max(ring_outer_radius_px, ring_inner_radius))
    use_affine = affine_matrix is not None and not np.allclose(
        np.asarray(affine_matrix, dtype=np.float64),
        identity_affine_matrix(),
        atol=1e-9,
    )
    if not use_affine:
        yy, xx = np.indices((image_height, image_width), dtype=np.float32)
        for roi in effective_rois:
            distance_sq = (xx - float(roi.center_x)) ** 2 + (yy - float(roi.center_y)) ** 2
            roi_mask |= distance_sq <= float(roi.sample_radius_px) ** 2
            if ring_outer_radius > 0.0:
                outer_mask = distance_sq <= ring_outer_radius**2
                inner_mask = distance_sq < ring_inner_radius**2 if ring_inner_radius > 0.0 else np.zeros_like(outer_mask)
                ring_mask |= outer_mask & ~inner_mask
        ring_mask &= ~roi_mask
        return roi_mask, ring_mask

    for roi in effective_rois:
        roi_mask |= transformed_disk_mask(
            (image_height, image_width),
            (float(roi.center_x), float(roi.center_y)),
            float(roi.sample_radius_px),
            affine_matrix,
        )
        if ring_outer_radius > 0.0:
            ring_mask |= transformed_annulus_mask(
                (image_height, image_width),
                (float(roi.center_x), float(roi.center_y)),
                float(ring_inner_radius),
                float(ring_outer_radius),
                affine_matrix,
            )
    ring_mask &= ~roi_mask
    return roi_mask, ring_mask


def _roi_absorbance_signature(
    frame: int,
    wavelength_values: tuple[float, ...],
    roi: AreaRoi,
    chromatic_signatures: tuple[object, ...],
) -> tuple[object, ...]:
    return (
        int(frame),
        tuple(round(float(value), 6) for value in wavelength_values),
        int(roi.area_roi_id),
        round(float(roi.center_x), 3),
        round(float(roi.center_y), 3),
        round(float(roi.sample_radius_px), 3),
        round(float(roi.reference_inner_diameter_px or 0.0), 3),
        round(float(roi.reference_outer_diameter_px or 0.0), 3),
        chromatic_signatures,
    )


def _absorbance_roi_mask_cache_key(
    image_shape: tuple[int, int],
    selected_rois: list[AreaRoi],
    selected_spot_ids: tuple[int, ...],
    affine_matrix: np.ndarray | None,
    ring_inner_radius_px: float,
    ring_outer_radius_px: float,
) -> tuple[object, ...]:
    affine_signature = None
    if affine_matrix is not None:
        affine_signature = tuple(round(float(value), 6) for value in np.asarray(affine_matrix, dtype=np.float64).ravel())
    return (
        tuple(int(value) for value in image_shape[:2]),
        tuple(int(spot_id) for spot_id in selected_spot_ids),
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
            )
            for roi in selected_rois
        ),
        affine_signature,
        round(float(ring_inner_radius_px), 3),
        round(float(ring_outer_radius_px), 3),
    )


def _absorbance_spectrum_task(
    measurement_payload: list[tuple[float, str, list[AreaRoi], np.ndarray | None, bool, np.ndarray | None]],
    preprocessing,
    flatten_mask_settings,
    measurement_settings,
    roi_mask_cache,
    roi_mask_cache_lock,
    roi_mask_cache_max_size: int,
    source_rois: list[AreaRoi],
    selected_spot_ids: tuple[int, ...],
    ring_inner_radius_px: float,
    ring_outer_radius_px: float,
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
    selected_spot_id_set = set(selected_spot_ids)
    selected_rois = [roi for roi in source_rois if roi.area_roi_id in selected_spot_id_set]
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
            ring_inner_radius_px,
            ring_outer_radius_px,
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
        combined_roi_mask, combined_ring_mask = _selected_roi_masks_for_spectrum(
            image_shape,
            source_rois,
            selected_ids_local,
            ring_inner_radius_px,
            ring_outer_radius_px,
            affine_matrix_local,
        )
        per_roi_masks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        for roi in selected_rois_local:
            per_roi_masks[int(roi.area_roi_id)] = _selected_roi_masks_for_spectrum(
                image_shape,
                [roi],
                (int(roi.area_roi_id),),
                ring_inner_radius_px,
                ring_outer_radius_px,
                affine_matrix_local,
            )
        cached_value = {
            "shape": tuple(int(value) for value in image_shape[:2]),
            "combined": (combined_roi_mask, combined_ring_mask),
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
        index, (wavelength_nm, path_str, preprocessing_spots, affine_matrix, external_mask_processed, external_mask) = item
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
            rois=preprocessing_spots,
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
        roi_mask_cache_entry = _build_roi_mask_cache(current_shape, selected_rois, selected_spot_ids, affine_matrix)
        ignored_mask = ignored_pixel_mask(processed, measurement_settings, external_mask=external_mask)
        combined_roi_mask, combined_ring_mask = roi_mask_cache_entry["combined"]  # type: ignore[index]
        roi_mask = np.array(combined_roi_mask, dtype=bool, copy=True)
        ring_mask = np.array(combined_ring_mask, dtype=bool, copy=True)
        roi_mask &= ~ignored_mask
        ring_mask &= ~ignored_mask
        ring_mask &= ~roi_mask

        spot_pixels = processed[roi_mask]
        ring_pixels = processed[ring_mask]
        if spot_pixels.size == 0 or ring_pixels.size == 0:
            spot_mean = float("nan")
            ring_mean = float("nan")
            absorbance = float("nan")
        else:
            spot_mean = float(np.mean(spot_pixels))
            ring_mean = float(np.mean(ring_pixels))
            absorbance = absorbance_from_means(spot_mean, ring_mean)

        wavelengths.append(float(wavelength_nm))
        absorbance_values.append(absorbance)
        sample_mean_values.append(spot_mean)
        reference_mean_values.append(ring_mean)
        sample_pixel_counts.append(int(spot_pixels.size))
        reference_pixel_counts.append(int(ring_pixels.size))

        for roi in selected_rois:
            per_roi_masks = roi_mask_cache_entry["per_roi"]  # type: ignore[index]
            roi_mask_template, ring_mask_template = per_roi_masks[int(roi.area_roi_id)]
            roi_mask_single = np.array(roi_mask_template, dtype=bool, copy=True)
            ring_mask_single = np.array(ring_mask_template, dtype=bool, copy=True)
            roi_mask_single &= ~ignored_mask
            ring_mask_single &= ~ignored_mask
            ring_mask_single &= ~roi_mask_single

            spot_pixels_single = processed[roi_mask_single]
            ring_pixels_single = processed[ring_mask_single]
            if spot_pixels_single.size == 0 or ring_pixels_single.size == 0:
                spot_mean_single = float("nan")
                ring_mean_single = float("nan")
                absorbance_single = float("nan")
            else:
                spot_mean_single = float(np.mean(spot_pixels_single))
                ring_mean_single = float(np.mean(ring_pixels_single))
                absorbance_single = absorbance_from_means(spot_mean_single, ring_mean_single)

            accumulator = roi_accumulators[int(roi.area_roi_id)]
            accumulator["wavelengths"].append(float(wavelength_nm))
            accumulator["absorbance"].append(absorbance_single)
            accumulator["sample_mean"].append(spot_mean_single)
            accumulator["reference_mean"].append(ring_mean_single)
            accumulator["sample_pixel_count"].append(int(spot_pixels_single.size))
            accumulator["reference_pixel_count"].append(int(ring_pixels_single.size))
        roi_seconds += time.perf_counter() - roi_started

        if progress_callback is not None:
            progress_callback(
                int(round(index / total * 100.0)),
                f"Spectral absorbance {index}/{total}: {float(wavelength_nm):g} nm",
            )

    spot_results: dict[int, AbsorbanceSpectrumResult] = {}
    for roi in selected_rois:
        data = roi_accumulators[int(roi.area_roi_id)]
        spot_results[int(roi.area_roi_id)] = AbsorbanceSpectrumResult(
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
        area_roi_results=spot_results,
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
    frame_index: int,
    wavelengths: list,
    record_map: dict,
    roi,
    sample_radius_px: float,
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
    crop_x: int,
    crop_y: int,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
) -> AbsorbanceSpectrumResult:
    """Fast single-ROI absorbance spectrum using OME-Zarr spatial reads.

    Reads only the spatial bounding box of the circle+ring from each zarr plane,
    then computes sample and reference means directly from the patch.
    Global background flattening is intentionally skipped — the reference ring
    provides per-ROI local normalization, which is valid for circle+ring geometry.
    Only applicable when there is no rotation or flip transform active.
    """
    task_started = time.perf_counter()
    cx = float(roi.center_x)
    cy = float(roi.center_y)
    r_outer = float(reference_outer_radius_px)
    margin = 3

    raw_cx = cx + int(crop_x)
    raw_cy = cy + int(crop_y)
    y0 = max(0, int(raw_cy - r_outer) - margin)
    y1 = int(raw_cy + r_outer) + margin + 1
    x0 = max(0, int(raw_cx - r_outer) - margin)
    x1 = int(raw_cx + r_outer) + margin + 1
    center_py = raw_cy - y0
    center_px = raw_cx - x0

    wavelengths_list = list(wavelengths)
    total = max(len(wavelengths_list), 1)

    def _load_wl(idx_wl: tuple) -> tuple:
        idx, wl = idx_wl
        if cancel_event is not None and cancel_event.is_set():
            return (idx, float(wl), float("nan"), float("nan"), float("nan"), 0, 0)
        record = record_map.get((int(frame_index), float(wl)))
        if record is None:
            return (idx, float(wl), float("nan"), float("nan"), float("nan"), 0, 0)
        patch = dataset_load_plane_roi(dataset, int(frame_index), float(wl), y0, y1, x0, x1, record=record)
        if patch is None or patch.size == 0:
            return (idx, float(wl), float("nan"), float("nan"), float("nan"), 0, 0)
        ph, pw = patch.shape[:2]
        y_idx, x_idx = np.mgrid[0:ph, 0:pw]
        dist2 = (y_idx - center_py) ** 2 + (x_idx - center_px) ** 2
        sample_mask = dist2 <= float(sample_radius_px) ** 2
        dist = np.sqrt(dist2)
        ring_mask = (dist >= float(reference_inner_radius_px)) & (dist <= r_outer)
        ring_mask &= ~sample_mask
        sample_pixels = patch[sample_mask]
        ring_pixels = patch[ring_mask]
        if sample_pixels.size == 0 or ring_pixels.size == 0:
            return (idx, float(wl), float("nan"), float("nan"), float("nan"), 0, 0)
        sm = float(np.mean(sample_pixels))
        rm = float(np.mean(ring_pixels))
        return (idx, float(wl), absorbance_from_means(sm, rm), sm, rm, int(sample_pixels.size), int(ring_pixels.size))

    worker_count = max(1, min(max(int(os.cpu_count() or 2) // 2, 2), 8, len(wavelengths_list)))
    indexed = list(enumerate(wavelengths_list))
    results: list = [None] * len(wavelengths_list)

    if worker_count <= 1:
        for idx_wl in indexed:
            results[idx_wl[0]] = _load_wl(idx_wl)
            if progress_callback is not None:
                progress_callback(
                    int(round((idx_wl[0] + 1) / total * 100)),
                    f"Fast spectrum {idx_wl[0]+1}/{total}: {float(idx_wl[1]):g} nm",
                )
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(_load_wl, idx_wl): idx_wl[0] for idx_wl in indexed}
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
        _, wl, abs_val, sm, rm, spc, rpc = r
        wavelengths_out.append(float(wl))
        absorbance_values.append(float(abs_val))
        sample_mean_values.append(float(sm))
        reference_mean_values.append(float(rm))
        sample_pixel_counts.append(int(spc))
        reference_pixel_counts.append(int(rpc))

    per_roi = AbsorbanceSpectrumResult(
        wavelengths_nm=np.asarray(wavelengths_out, dtype=np.float64),
        absorbance=np.asarray(absorbance_values, dtype=np.float64),
        sample_mean=np.asarray(sample_mean_values, dtype=np.float64),
        reference_mean=np.asarray(reference_mean_values, dtype=np.float64),
        sample_pixel_count=np.asarray(sample_pixel_counts, dtype=np.int32),
        reference_pixel_count=np.asarray(reference_pixel_counts, dtype=np.int32),
    )
    return AbsorbanceSpectrumResult(
        wavelengths_nm=per_roi.wavelengths_nm,
        absorbance=per_roi.absorbance,
        sample_mean=per_roi.sample_mean,
        reference_mean=per_roi.reference_mean,
        sample_pixel_count=per_roi.sample_pixel_count,
        reference_pixel_count=per_roi.reference_pixel_count,
        total_seconds=time.perf_counter() - task_started,
        area_roi_results={int(roi.area_roi_id): per_roi},
    )


def _sensorgram_metric_task(
    frame_payloads_or_frames,
    poly_order: int,
    metric_key: str,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
    partial_callback=None,
    frame_payload_builder=None,
    task_fn=None,
) -> SensorgramComputationResult:
    task_started = time.perf_counter()
    frame_payloads: list[tuple[int, tuple[object, ...]]] = []
    total_input_count = len(frame_payloads_or_frames) if hasattr(frame_payloads_or_frames, "__len__") else 0
    prep_seconds = 0.0
    fit_seconds = 0.0
    if frame_payload_builder is not None:
        frames = [int(frame) for frame in frame_payloads_or_frames]
        total_input_count = len(frames)
        if not frames:
            return SensorgramComputationResult(
                frame_indices=np.asarray([], dtype=np.int32),
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
            future_map = {executor.submit(frame_payload_builder, int(frame)): int(frame) for frame in frames}
            for future in as_completed(future_map):
                frame_index = int(future_map[future])
                if cancel_event is not None and cancel_event.is_set():
                    return SensorgramComputationResult(
                        frame_indices=np.asarray([], dtype=np.int32),
                        metric_values=np.asarray([], dtype=np.float64),
                        metric_signal=np.asarray([], dtype=np.float64),
                        completed_count=len(built_payloads),
                        total_count=total_input_count,
                        cancelled=True,
                    )
                payload = future.result()
                if payload is not None:
                    built_payloads.append((frame_index, payload))
                completed += 1
                if progress_callback is not None:
                    progress_callback(
                        int(round((completed / max(total_input_count, 1)) * 20.0)),
                        f"Preparing sensorgram {completed}/{total_input_count} frames",
                    )
        prep_seconds = time.perf_counter() - prep_started
        frame_payloads = sorted(built_payloads, key=lambda item: item[0])
    else:
        frame_payloads = list(frame_payloads_or_frames)
    frame_indices: list[int] = []
    metric_values: list[float] = []
    metric_signals: list[float] = []
    total = max(len(frame_payloads), 1)
    compute_base = 20.0 if frame_payload_builder is not None else 0.0
    compute_span = 80.0 if frame_payload_builder is not None else 100.0
    compute_started = time.perf_counter()

    for index, (frame_index, payload) in enumerate(frame_payloads, start=1):
        if cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                frame_indices=np.asarray(frame_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(frame_indices),
                total_count=len(frame_payloads),
                prep_seconds=prep_seconds,
                fit_seconds=fit_seconds,
                total_seconds=time.perf_counter() - task_started,
                cancelled=True,
            )

        def frame_progress_callback(percent: int, text: str | None = None, *, frame_number: int = int(frame_index), position: int = index) -> None:
            if progress_callback is None:
                return
            inner_percent = float(np.clip(float(percent), 0.0, 100.0))
            overall = compute_base + (((position - 1) + (inner_percent / 100.0)) / total) * compute_span
            progress_callback(
                int(round(overall)),
                text or f"Sensorgram {position}/{total}: frame {frame_number}",
            )

        _active_task = task_fn if task_fn is not None else _absorbance_spectrum_task
        spectrum = _active_task(
            *payload,
            cancel_event=cancel_event,
            progress_callback=frame_progress_callback,
        )
        if cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                frame_indices=np.asarray(frame_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(frame_indices),
                total_count=len(frame_payloads),
                cancelled=True,
            )

        fit = fit_absorbance_curve(
            spectrum.wavelengths_nm,
            spectrum.absorbance,
            poly_order=poly_order,
        )
        metric_value, metric_signal = metric_value_from_fit(fit, metric_key)
        metric_float = float(metric_value) if metric_value is not None and np.isfinite(metric_value) else float("nan")
        signal_float = float(metric_signal) if metric_signal is not None and np.isfinite(metric_signal) else float("nan")

        frame_indices.append(int(frame_index))
        metric_values.append(metric_float)
        metric_signals.append(signal_float)

        if partial_callback is not None:
            partial_callback(
                SensorgramPointResult(
                    frame_index=int(frame_index),
                    metric_value=None if not np.isfinite(metric_float) else metric_float,
                    metric_signal=None if not np.isfinite(signal_float) else signal_float,
                )
            )
        if progress_callback is not None:
            progress_callback(
                int(round(compute_base + (index / total) * compute_span)),
                f"Sensorgram {index}/{total}: frame {int(frame_index)}",
            )
    fit_seconds = time.perf_counter() - compute_started

    return SensorgramComputationResult(
        frame_indices=np.asarray(frame_indices, dtype=np.int32),
        metric_values=np.asarray(metric_values, dtype=np.float64),
        metric_signal=np.asarray(metric_signals, dtype=np.float64),
        completed_count=len(frame_indices),
        total_count=len(frame_payloads),
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
    progress_callback=None,
) -> list[tuple[int, int, float, float, float]]:
    if not sample_payload:
        return []
    preprocessing_settings = deepcopy(preprocessing)
    preprocessing_settings.flatten_background_enabled = False
    preprocessing_settings.chromatic_correction_enabled = False
    processed_images: list[tuple[int, float, np.ndarray]] = []
    total = max(len(sample_payload), 1)
    for index, (frame, wavelength, path_str) in enumerate(sample_payload, start=1):
        raw_image = load_image_array(path_str)
        processed = apply_spatial_preprocessing(raw_image, preprocessing_settings)
        processed_images.append((int(frame), float(wavelength), processed))
        if progress_callback is not None:
            progress_callback(
                int(round((index / total) * 40)),
                f"Loading sampled chromatic image {index}/{total}...",
            )
    first_frame, first_wavelength, first_image = processed_images[0]
    current_landmarks = detect_regional_landmarks(
        first_image,
        int(feature_count),
        subpixel_precision=int(subpixel_precision),
    )
    observations: list[tuple[int, int, float, float, float]] = [
        (int(feature_id), int(first_frame), float(first_wavelength), float(point[0]), float(point[1]))
        for feature_id, point in sorted(current_landmarks.items())
    ]
    if progress_callback is not None:
        progress_callback(50, f"Detected reference points on sampled image 1/{total}.")
    previous_image = first_image
    for index, (frame, wavelength, image) in enumerate(processed_images[1:], start=2):
        current_landmarks = track_landmarks(
            previous_image,
            image,
            current_landmarks,
            subpixel_precision=int(subpixel_precision),
        )
        for feature_id, point in sorted(current_landmarks.items()):
            observations.append((int(feature_id), int(frame), float(wavelength), float(point[0]), float(point[1])))
        previous_image = image
        if progress_callback is not None:
            progress_callback(
                int(round(50 + ((index - 1) / max(total - 1, 1)) * 50)),
                f"Tracked reference points on sampled image {index}/{total}...",
            )
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
    models: list[ChromaticTransformModel] = []
    if mode == "landmark_radial":
        if not landmarks_payload:
            raise ValueError("No chromatic reference points are available. Start the radial workflow and mark reference points first.")
        reference_frame, reference_wavelength = int(reference_key[0]), float(reference_key[1])
        all_wavelengths = sorted({float(wavelength) for _frame, wavelength, _path in record_specs})
        sampled_wavelengths = _sampled_wavelengths(
            all_wavelengths,
            int(getattr(preprocessing, "chromatic_sample_image_count", 5)),
        )
        feature_count = max(int(getattr(preprocessing, "chromatic_feature_count", 5)), 1)
        expected_feature_ids = list(range(1, feature_count + 1))

        landmarks_by_wavelength: dict[float, dict[int, tuple[float, float]]] = {}
        for landmark_id, frame, wavelength, x_px, y_px in landmarks_payload:
            if int(frame) != reference_frame:
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

        for frame, wavelength, _path_str in record_specs:
            matrix = matrices_by_wavelength[float(wavelength)]
            models.append(
                ChromaticTransformModel(
                    frame_index=int(frame),
                    wavelength_nm=float(wavelength),
                    model_kind="landmark_affine",
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

    reference_path = next((path_str for frame, wavelength, path_str in record_specs if (frame, wavelength) == reference_key), None)
    if reference_path is None:
        raise ValueError("Reference image is missing from the dataset.")
    reference_raw = load_image_array(reference_path)
    reference_processed = apply_spatial_preprocessing(reference_raw, preprocessing)
    tile_size = int(max(preprocessing.chromatic_tile_size_px, 24))
    search_radius = int(max(preprocessing.chromatic_search_radius_px, 6))
    total = max(len(record_specs), 1)
    for index, (frame, wavelength, path_str) in enumerate(record_specs, start=1):
        if (frame, wavelength) == reference_key:
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
                frame_index=int(frame),
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
                f"Chromatic correction {index}/{total}: {wavelength:g} nm frame {frame}",
            )
    return models
