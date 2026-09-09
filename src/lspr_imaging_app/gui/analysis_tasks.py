from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path

import numpy as np

try:
    import psutil as _psutil
except ImportError:  # pragma: no cover - diagnostic only, see _cpu_freq_text below
    _psutil = None

from lspr_imaging_app.domain.models import FormulaSpectrumResult, AreaRoi, AreaRoiDetectionSettings, ChromaticTransformModel, ImageDataset
from lspr_imaging_app.gui.worker import SensorgramComputationResult, SensorgramPointResult
from lspr_imaging_app.io.dataset import dataset_load_plane_roi, export_ome_zarr_dataset, load_image_array
from lspr_imaging_app.storage.workspace import load_preprocessing, load_processing_profile
from lspr_imaging_app.processing.analysis import (
    fit_curve_for_method,
    formula_value,
    metric_value_from_fit,
    metric_value_from_spectrum,
)
from lspr_imaging_app.processing.chromatic import (
    ChromaticRegistrationResult,
    annulus_reach_box,
    apply_affine_to_points,
    auto_track_landmarks_over_wavelengths,
    compose_affine_matrices,
    estimate_affine_chromatic_transform,
    fit_affine_matrix,
    fit_similarity_matrix,
    identity_affine_matrix,
    invert_affine_matrix,
    prepare_registration_image,
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
from lspr_imaging_app.processing.roi_array_geometry import ArrayGeometryEstimate, estimate_array_geometry, estimate_reference_ring_radii
from lspr_imaging_app.processing.roi_detection import detect_rois, ignored_pixel_mask
from lspr_imaging_app.processing.roi_histogram import estimate_roi_intensity_range
from lspr_imaging_app.processing.roi_math import REDUCTION_METHODS, reduce_sample_and_reference, reduce_sample_and_reference_all_methods
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


def _process_image_task(
    path_str: str,
    preprocessing,
    rois,
    external_mask: np.ndarray | None,
    mask_state,
    dispatch_started_at: float | None = None,
) -> tuple[np.ndarray, float | None]:
    # Timed unconditionally (one call per wavelength switch, not a hot loop -
    # see apply_preprocessing's log_stage_timing docstring for the same
    # reasoning). Separates three things that all fold into the "Image load"
    # total logged by on_image_refresh_ready: how long this task sat queued
    # on the thread pool before actually starting (queue), reading/decoding
    # the raw TIFF (read), and mask/spatial/flatten processing (already
    # logged separately inside apply_preprocessing) - without this split, a
    # slow switch's "Image load" number couldn't be attributed to any of the
    # three.
    raw_load_started_at = time.perf_counter()
    queue_wait_ms = (raw_load_started_at - dispatch_started_at) * 1000.0 if dispatch_started_at is not None else None
    raw_image = load_image_array(path_str)
    raw_load_elapsed_ms = (time.perf_counter() - raw_load_started_at) * 1000.0
    queue_text = f"{queue_wait_ms:.0f}ms" if queue_wait_ms is not None else "n/a"
    logging.getLogger("lspr_imaging_app.workflow").debug(
        f"Image raw load | queue={queue_text} read={raw_load_elapsed_ms:.0f}ms | {Path(path_str).name}"
    )
    mask_settings = preprocessing[1] if isinstance(preprocessing, tuple) else None
    external_mask_processed = bool(preprocessing[2]) if isinstance(preprocessing, tuple) and len(preprocessing) > 2 else False
    skip_crop = bool(preprocessing[3]) if isinstance(preprocessing, tuple) and len(preprocessing) > 3 else False
    preprocessing_settings = preprocessing[0] if isinstance(preprocessing, tuple) else preprocessing
    # This is the single-image display-refresh path (one call per wavelength
    # switch, see image_render_manager.start_pending_image_refresh) - unlike
    # apply_preprocessing's other, per-frame-loop callers, a stage-timing log
    # line here is cheap and directly answers "which stage (mask/spatial
    # transform/flatten-background) is actually slow" for a switch that felt
    # slow. See apply_preprocessing's log_stage_timing docstring.
    processed = apply_preprocessing(
        raw_image,
        preprocessing_settings,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        external_mask_processed=external_mask_processed,
        mask_state=mask_state,
        skip_crop=skip_crop,
        log_stage_timing=True,
    )
    # Reported back to the GUI thread (not just logged here) so
    # on_image_refresh_ready can split "Image load" into what this worker
    # itself spent vs. pure wait for the GUI thread to actually pick up the
    # finished result - the two have very different causes (this function's
    # own cost vs. the GUI thread being busy with something else) and
    # collapsing them into one number was hiding which one was the actual
    # problem on a slow switch.
    background_total_ms = (time.perf_counter() - dispatch_started_at) * 1000.0 if dispatch_started_at is not None else None
    return processed, background_total_ms


def _mask_candidate_task(path_str: str, mask_settings, tool_key: str) -> np.ndarray:
    raw_image = load_image_array(path_str).astype(np.float32, copy=False)
    return create_figure_mask(raw_image, mask_settings, tool_key)


def _detect_rois_task(
    image: np.ndarray,
    settings,
    external_mask: np.ndarray | None,
    rotation_fill_mask: np.ndarray | None = None,
    progress_callback=None,
) -> list[AreaRoi]:
    return detect_rois(
        image,
        settings,
        external_mask=external_mask,
        rotation_fill_mask=rotation_fill_mask,
        progress_callback=progress_callback,
    )


def _detect_rois_fully_automatic_task(
    image: np.ndarray,
    settings: AreaRoiDetectionSettings,
    external_mask: np.ndarray | None,
    rotation_fill_mask: np.ndarray | None,
    histogram_intensity_min: float,
    histogram_intensity_max: float,
    progress_callback=None,
) -> tuple[ArrayGeometryEstimate | None, tuple[float, float] | None, AreaRoiDetectionSettings | None, list[AreaRoi], dict]:
    """Infers array geometry (diameter, rows, cols, spacing) and the
    reference ring directly from image content (roi_array_geometry.py),
    auto-sets the histogram intensity range the same way the "wand" button
    does (roi_histogram.py), then hands off to the existing, tested
    detect_rois pipeline for the actual subpixel placement/scoring - this
    function's only job is estimating the parameters semi-automatic
    detection would otherwise need set by hand.

    Returns (geometry_estimate, intensity_range, settings_used, detected_rois,
    diagnostics). geometry_estimate is None if no confident periodic array
    was found, in which case the other three are (None, None, []) and the
    caller should fall back to semi-automatic detection instead of guessing;
    diagnostics["reason"] explains why in that case (see
    estimate_array_geometry's docstring).
    """
    if progress_callback is not None:
        progress_callback(5, "Fully automatic: finding the circle array...")
    valid_mask = ~ignored_pixel_mask(image, settings, external_mask=external_mask, rotation_fill_mask=rotation_fill_mask)
    diagnostics: dict = {}
    geometry = estimate_array_geometry(image, valid_mask=valid_mask, diagnostics=diagnostics)
    if geometry is None:
        return None, None, None, [], diagnostics

    if progress_callback is not None:
        progress_callback(30, "Fully automatic: setting histogram range...")
    intensity_range = estimate_roi_intensity_range(
        image[valid_mask],
        intensity_min=histogram_intensity_min,
        intensity_max=histogram_intensity_max,
    )

    resolved_settings = deepcopy(settings)
    resolved_settings.sample_radius_px = geometry.radius_px
    resolved_settings.array_rows = geometry.rows
    resolved_settings.array_cols = geometry.cols
    resolved_settings.array_spacing_px = int(round(geometry.spacing_px))
    resolved_settings.reference_inner_radius_px, resolved_settings.reference_outer_radius_px = estimate_reference_ring_radii(
        geometry.radius_px
    )
    if intensity_range is not None:
        resolved_settings.intensity_min_value, resolved_settings.intensity_max_value = intensity_range

    if progress_callback is not None:
        progress_callback(45, "Fully automatic: placing ROIs...")
    detected_rois = detect_rois(
        image,
        resolved_settings,
        external_mask=external_mask,
        rotation_fill_mask=rotation_fill_mask,
        progress_callback=(
            None
            if progress_callback is None
            else lambda percent, text: progress_callback(45 + int(percent * 0.55), text)
        ),
    )
    return geometry, intensity_range, resolved_settings, detected_rois, diagnostics


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


def _roi_reach_box(
    roi: AreaRoi,
    sample_x: float,
    sample_y: float,
    patch_h: int,
    patch_w: int,
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
) -> tuple[int, int, int, int] | None:
    """Local (patch-relative) (rx0, ry0, rx1, ry1) window bounding one ROI's
    own sample circle and reference ring, clipped to the patch. `None` for
    "mask" geometry (an arbitrary bitmap unrelated to any radius, so it has
    no reach to bound - same case `_selected_roi_masks_for_spectrum` already
    always treats as needing the full patch) or a degenerate (empty) window.

    Pulled out of what used to be `_means_for`'s own inline reach-limit
    computation (still the same formula/margin, unchanged) so
    `_fast_roi_mask_cache_entry` can build/cache each ROI's mask already
    cropped to this exact window instead of the full (patch_h, patch_w)
    patch - see that function's docstring for why a per-ROI full-patch mask
    was the dominant RAM cost of a bulk run with many selected ROIs. Sharing
    one function guarantees the cache builder and `_means_for`'s own
    patch/exclude-mask slicing can never disagree about where a given ROI's
    window is - if each instead computed it independently, any drift
    between the two copies would silently misalign a cached mask against
    the current patch (see `_means_for`'s own warning about that class of
    bug at its np.where-index comment).
    """
    if roi.sample_geometry_type == "mask" or roi.reference_geometry_type == "mask":
        return None
    _, outer_r = _effective_reference_radii(
        roi, max(reference_inner_radius_px, 0.0), max(reference_outer_radius_px, 0.0)
    )
    reach = max(float(roi.sample_radius_px), outer_r) + 2.0
    rx0 = max(int(np.floor(sample_x - reach)), 0)
    rx1 = min(int(np.ceil(sample_x + reach)) + 1, patch_w)
    ry0 = max(int(np.floor(sample_y - reach)), 0)
    ry1 = min(int(np.ceil(sample_y + reach)) + 1, patch_h)
    if rx1 <= rx0 or ry1 <= ry0:
        return None
    return rx0, ry0, rx1, ry1


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
    # A concrete matrix (identity when no chromatic correction is active),
    # always routed through the reach-limited transformed_*_mask functions
    # below. A previous "no affine" fast path here instead computed a full
    # image_height x image_width distance grid per ROI - O(patch area x ROI
    # count) instead of O(ROI count x ROI area) - profiled directly against a
    # 160-ROI/806x1288 case at ~30ms/ROI (~4.8s for one mask-cache build in
    # isolation, single-threaded), the dominant cost behind a real "78.6s
    # stall building masks for the first cube" report (see Follow-up #9 in
    # bulk_analysis_performance_investigation.md). transformed_annulus_mask's
    # reach-box math already handles an identity (or near-identity) matrix
    # correctly and cheaply - a 2x2 SVD per ROI, negligible next to the
    # full-grid cost it replaces.
    effective_affine_matrix = affine_matrix if affine_matrix is not None else identity_affine_matrix()
    px0, py0 = patch_origin_xy

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
                    effective_affine_matrix,
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
                    effective_affine_matrix,
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
                    effective_affine_matrix,
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
                    effective_affine_matrix,
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


def spectrum_read_region(
    dataset: ImageDataset,
    image_height: int,
    image_width: int,
    selected_rois: list[AreaRoi],
    reference_outer_radius_px: float,
    affine_matrices: list[np.ndarray | None],
) -> tuple[int, int, int, int] | None:
    """The region ("chunk") one spectrum-compute pass reads/processes at
    once - the single place format enters `_scoped_formula_spectrum_task`'s
    otherwise format-agnostic pipeline (everything downstream of this just
    reads/processes whatever box comes back, TIFF or OME-Zarr alike).

    TIFF's only real read unit is the whole plane: `dataset_load_plane_roi`'s
    TIFF fallback always loads the full file regardless of the requested
    region, so a smaller box wouldn't reduce I/O - it would only shrink
    which pixels the mask/reduction math touches, for no reason. OME-Zarr's
    chunked reads stay cheap at any size (see the `zarrs` codec pipeline
    fix), so the region is the ROI union bounding box - as tight as the
    actual selection allows, via `compute_roi_union_bounding_box`.
    """
    if not dataset.is_ome_zarr:
        if image_height <= 0 or image_width <= 0:
            return None
        return (0, 0, image_width, image_height)
    return compute_roi_union_bounding_box(
        selected_rois, reference_outer_radius_px, affine_matrices, image_height, image_width,
    )


def _roi_formula_spectrum_signature(
    spectral_cube_index: int,
    wavelength_values: tuple[float, ...],
    roi: AreaRoi,
    chromatic_signatures: tuple[object, ...],
    reduction_method: str = "mean",
    trimmed_mean_fraction: float = 0.10,
    exclusion_signatures: tuple[object, ...] = (),
) -> tuple[object, ...]:
    """Deliberately formula-independent: this signature validates
    sample_mean/reference_mean (the reduction), not the formula-combined
    value, so it stays valid across a formula switch - see
    AnalysisController._roi_reduction_signature_elements and
    processing.analysis.project_formula_spectrum."""
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
        str(reduction_method),
        round(float(trimmed_mean_fraction), 4),
        exclusion_signatures,
    )


def _formula_spectrum_roi_mask_cache_key(
    image_shape: tuple[int, int],
    selected_rois: list[AreaRoi],
    selected_roi_ids: tuple[int, ...],
    affine_matrix: np.ndarray | None,
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
    patch_origin_xy: tuple[int, int] = (0, 0),
) -> tuple[object, ...]:
    """`patch_origin_xy` only matters for the fast/zarr path, where masks are
    local to a scoped patch rather than the full image (see
    _selected_roi_masks_for_spectrum) - two same-shaped patches at different
    offsets are not the same mask. The slow/full-image path never passes it
    (always (0, 0), i.e. no offset), so its keys are unaffected.
    """
    affine_signature = None
    if affine_matrix is not None:
        affine_signature = tuple(round(float(value), 6) for value in np.asarray(affine_matrix, dtype=np.float64).ravel())
    return (
        tuple(int(value) for value in image_shape[:2]),
        (int(patch_origin_xy[0]), int(patch_origin_xy[1])),
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


def _scoped_formula_spectrum_task(
    dataset,
    spectral_cube_index: int,
    measurement_payload: list[tuple[float, np.ndarray | None, np.ndarray | None, object | None]],
    selected_rois: list[AreaRoi],
    selected_roi_ids: tuple[int, ...],
    reference_inner_radius_px: float,
    reference_outer_radius_px: float,
    box: tuple[int, int, int, int],
    preprocessing,
    raw_shape: tuple[int, int],
    roi_mask_cache,
    roi_mask_cache_lock,
    roi_mask_cache_max_size: int,
    mask_state=None,
    background_mask_settings=None,
    cancel_event: threading.Event | None = None,
    progress_callback=None,
    reduction_method: str = "mean",
    trimmed_mean_fraction: float = 0.10,
    formula_key: str = "absorbance",
    compute_all_reduction_methods: bool = True,
    worker_count_override: int | None = None,
) -> FormulaSpectrumResult:
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

    `roi_mask_cache`/`roi_mask_cache_lock`/`roi_mask_cache_max_size`: with
    many selected ROIs (e.g. a 170-spot array), rebuilding every
    sample/reference circle mask from scratch for every wavelength of every
    spectral cube is real, avoidable work when the ROI geometry, patch box,
    and per-wavelength chromatic transform are unchanged run to run (the
    common case when chromatic correction is off, since then affine_matrix
    is None for every wavelength and the box is the same for every spectral
    cube too) - the cache key folds in the patch's own shape and origin (see
    _formula_spectrum_roi_mask_cache_key) so a scoped/local mask is never
    confused with a full-image one.

    `worker_count_override`: this machine's calibrated per-wavelength read
    concurrency (see io/dataset.py's `calibrate_analysis_worker_count` and
    bulk_analysis_performance_investigation.md Follow-up #11/#12), when
    available - replaces the `os.cpu_count()`-based heuristic below
    entirely rather than just capping it, since that heuristic was tuned by
    hand on one specific machine and measured wrong (too high) on it too.
    `None` (calibration not yet finished, or failed) falls back to the
    heuristic exactly as before this parameter existed.
    """
    from lspr_imaging_app.processing.preprocess import (
        apply_preprocessing,
        raw_bounding_box_for_processed_box,
        resample_raw_patch_to_processed_box,
    )

    task_started = time.perf_counter()
    x0, y0, x1, y1 = box
    patch_h, patch_w = y1 - y0, x1 - x0
    active_reduction_method_key = str(reduction_method).strip().lower()
    if active_reduction_method_key not in REDUCTION_METHODS:
        active_reduction_method_key = "mean"
    flatten_background_enabled = bool(getattr(preprocessing, "flatten_background_enabled", False))
    if not flatten_background_enabled:
        raw_x0, raw_y0, raw_x1, raw_y1 = raw_bounding_box_for_processed_box(raw_shape, preprocessing, box)

    roi_accumulators: dict[int, dict[str, list]] = {
        int(roi.area_roi_id): {
            "wavelengths": [], "formula_values": [], "sample_mean": [], "reference_mean": [],
            "sample_pixel_count": [], "reference_pixel_count": [],
            "reduced_by_method": {method: [] for method in REDUCTION_METHODS},
        }
        for roi in selected_rois
    }
    combined_reduced_by_method: dict[str, list[tuple[float, float]]] = {method: [] for method in REDUCTION_METHODS}
    total = max(len(measurement_payload), 1)

    # Debug-only, always-cheap-to-compute stage timing (perf_counter, one
    # lock acquisition per wavelength, not per ROI - see the per-wavelength
    # accumulation in _load_wl below) - same convention as _process_image_
    # task's "Image raw load" timing, added to answer "which stage is
    # actually slow" for a "SG cube compute timing" outlier without
    # re-instrumenting from scratch each time.
    _stage_timing_lock = threading.Lock()
    _stage_timing_totals = {"io": 0.0, "resample": 0.0, "mask": 0.0, "where": 0.0, "reduce": 0.0}

    def _fast_roi_mask_cache_entry(affine_matrix_local: np.ndarray | None) -> dict[str, object]:
        """One cache entry per (patch shape/origin, ROI set, affine matrix) holds
        both the combined-selection mask and every individual ROI's own
        sample/reference masks, so a run with many selected ROIs (e.g. a
        170-spot array) rasterizes each ROI's circle/annulus once per unique
        affine transform instead of once per wavelength per spectral cube.
        """
        cache_key = _formula_spectrum_roi_mask_cache_key(
            (patch_h, patch_w), selected_rois, selected_roi_ids, affine_matrix_local,
            reference_inner_radius_px, reference_outer_radius_px, patch_origin_xy=(x0, y0),
        )
        with roi_mask_cache_lock:
            cached_value = roi_mask_cache.get(cache_key) if hasattr(roi_mask_cache, "get") else None
            if cached_value is not None:
                try:
                    roi_mask_cache.move_to_end(cache_key)
                except Exception:
                    pass
                return cached_value
        # Only the sample-mask union is ever read back out of "combined"
        # (see all_selected_sample_mask below - the reference-mask half was
        # computed and immediately discarded). Building it from the per-ROI
        # masks below (a cheap boolean OR) instead of a second, separate
        # _selected_roi_masks_for_spectrum call over every ROI at once avoids
        # rasterizing every ROI's sample circle twice - measured ~20-30%
        # avoidable overhead in this cache-build step on a 160-ROI selection.
        #
        # Each ROI's own pair is built (and cached) at its own small reach
        # window (_roi_reach_box), not the full (patch_h, patch_w) patch -
        # with many selected ROIs (a 170-spot array, per this function's own
        # docstring above), a full-patch-sized boolean array per ROI was the
        # dominant RAM cost of a bulk "Start analysis" run: up to
        # FORMULA_SPECTRUM_ROI_MASK_CACHE_SIZE cache entries, each holding
        # two full-patch arrays per selected ROI. _means_for consumes these
        # directly with no further slicing, because it computes the
        # identical window via the same _roi_reach_box call - see that
        # function's docstring. `combined_roi_mask` still has to stay
        # full-patch-sized (it's sliced later at whatever *other* ROI's
        # reach window happens to need it, via extra_exclude_mask), so each
        # small per-ROI mask is OR'd into its own sub-region of it rather
        # than OR'd directly (shapes wouldn't match once per-ROI masks are
        # no longer all patch-sized).
        per_roi_masks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        per_roi_reach_boxes: dict[int, tuple[int, int, int, int] | None] = {}
        for roi in selected_rois:
            roi_id = int(roi.area_roi_id)
            reach_box = _roi_reach_box(
                roi, float(roi.center_x), float(roi.center_y), patch_h, patch_w,
                reference_inner_radius_px, reference_outer_radius_px,
            )
            per_roi_reach_boxes[roi_id] = reach_box
            if reach_box is None:
                per_roi_masks[roi_id] = _selected_roi_masks_for_spectrum(
                    (patch_h, patch_w), [roi], (roi_id,), reference_inner_radius_px, reference_outer_radius_px,
                    affine_matrix_local, patch_origin_xy=(x0, y0),
                )
            else:
                rx0, ry0, rx1, ry1 = reach_box
                per_roi_masks[roi_id] = _selected_roi_masks_for_spectrum(
                    (ry1 - ry0, rx1 - rx0), [roi], (roi_id,), reference_inner_radius_px, reference_outer_radius_px,
                    affine_matrix_local, patch_origin_xy=(x0 + rx0, y0 + ry0),
                )
        combined_roi_mask = np.zeros((patch_h, patch_w), dtype=bool)
        for roi_id, (roi_sample_mask, _roi_reference_mask) in per_roi_masks.items():
            reach_box = per_roi_reach_boxes[roi_id]
            if reach_box is None:
                combined_roi_mask |= roi_sample_mask
            else:
                rx0, ry0, rx1, ry1 = reach_box
                combined_roi_mask[ry0:ry1, rx0:rx1] |= roi_sample_mask
        cached_value = {"combined": (combined_roi_mask, None), "per_roi": per_roi_masks}
        with roi_mask_cache_lock:
            roi_mask_cache[cache_key] = cached_value
            try:
                roi_mask_cache.move_to_end(cache_key)
            except Exception:
                pass
            while len(roi_mask_cache) > max(int(roi_mask_cache_max_size), 1):
                roi_mask_cache.popitem(last=False)
        return cached_value

    def _load_wl(item: tuple) -> tuple:
        index, (wavelength_nm, affine_matrix, external_mask, record) = item
        empty_reduced_by_method = {method: (float("nan"), float("nan")) for method in REDUCTION_METHODS}
        empty_entry = (float("nan"), float("nan"), float("nan"), 0, 0, empty_reduced_by_method)
        empty_per_roi = {int(roi.area_roi_id): empty_entry for roi in selected_rois}
        if cancel_event is not None and cancel_event.is_set():
            return (index, float(wavelength_nm), empty_entry, empty_per_roi)
        if record is None:
            return (index, float(wavelength_nm), empty_entry, empty_per_roi)

        _io_seconds = 0.0
        _resample_seconds = 0.0
        if flatten_background_enabled:
            _io_started = time.perf_counter()
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
            _io_seconds = time.perf_counter() - _io_started
        else:
            _io_started = time.perf_counter()
            raw_patch = dataset_load_plane_roi(dataset, int(spectral_cube_index), float(wavelength_nm), raw_y0, raw_y1, raw_x0, raw_x1, record=record)
            _io_seconds = time.perf_counter() - _io_started
            if raw_patch is None or raw_patch.size == 0:
                return (index, float(wavelength_nm), empty_entry, empty_per_roi)
            _resample_started = time.perf_counter()
            patch = resample_raw_patch_to_processed_box(
                np.asarray(raw_patch, dtype=np.float32), (raw_x0, raw_y0), raw_shape, preprocessing, box,
            )
            _resample_seconds = time.perf_counter() - _resample_started

        ignored_patch = None
        if external_mask is not None:
            mask_full = np.asarray(external_mask, dtype=bool)
            if mask_full.shape[0] >= y1 and mask_full.shape[1] >= x1:
                ignored_patch = mask_full[y0:y1, x0:x1]

        nonlocal_mask_seconds = 0.0
        nonlocal_where_seconds = 0.0
        nonlocal_reduce_seconds = 0.0

        def _means_for(
            rois_subset: list[AreaRoi],
            ids_subset: tuple[int, ...],
            sample_x: float | None = None,
            sample_y: float | None = None,
            extra_exclude_mask: np.ndarray | None = None,
            precomputed_masks: tuple[np.ndarray, np.ndarray] | None = None,
        ) -> tuple[float, float, float, int, int, dict[str, tuple[float, float]]]:
            nonlocal nonlocal_mask_seconds, nonlocal_where_seconds, nonlocal_reduce_seconds

            # Reach-limit patch/mask indexing to a small local box around this
            # ROI instead of scanning the full (patch_h, patch_w) patch for
            # every one of potentially hundreds of ROIs - confirmed via
            # cProfile to be the single largest cost in a bulk run once the
            # mask itself is cheap to build (see docs/tiff_vs_ome_zarr_read_
            # benchmark.md's "Finding 2": 55% of total cube time, 11.3x
            # measured speedup). Only safe for a single circle/annulus ROI,
            # whose extent is bounded by its own radius - "mask" geometry (an
            # arbitrary bitmap unrelated to any radius) already gets
            # full-patch treatment inside _selected_roi_masks_for_spectrum
            # itself, so this mirrors that existing split rather than
            # inventing a new rule. Same _roi_reach_box call
            # _fast_roi_mask_cache_entry uses to decide what size/origin to
            # cache this ROI's mask at, in the precomputed_masks case below -
            # see that function's docstring for why sharing it matters.
            single_roi = rois_subset[0] if len(rois_subset) == 1 else None
            reach_box = (
                _roi_reach_box(
                    single_roi, sample_x, sample_y, patch_h, patch_w,
                    reference_inner_radius_px, reference_outer_radius_px,
                )
                if single_roi is not None and sample_x is not None and sample_y is not None
                else None
            )
            rx0, ry0, rx1, ry1 = reach_box if reach_box is not None else (0, 0, patch_w, patch_h)

            local_patch = patch[ry0:ry1, rx0:rx1]
            if precomputed_masks is not None:
                # Already built (and, for a non-"mask"-geometry ROI, cached)
                # at exactly this (rx0, ry0, rx1, ry1) window by
                # _fast_roi_mask_cache_entry, via the same _roi_reach_box
                # call above - used as-is, no further slicing needed (and
                # none possible: the cache no longer stores a full-patch-
                # sized array to slice - see that function's docstring for
                # why).
                local_roi_mask, local_reference_mask = precomputed_masks
            else:
                _mask_started = time.perf_counter()
                roi_mask, reference_mask = _selected_roi_masks_for_spectrum(
                    (patch_h, patch_w), rois_subset, ids_subset, reference_inner_radius_px, reference_outer_radius_px,
                    affine_matrix, patch_origin_xy=(x0, y0),
                )
                nonlocal_mask_seconds += time.perf_counter() - _mask_started
                local_roi_mask = roi_mask[ry0:ry1, rx0:rx1]
                local_reference_mask = reference_mask[ry0:ry1, rx0:rx1]
            if extra_exclude_mask is not None:
                # Keeps a neighboring selected ROI's sample pixels out of THIS
                # roi's reference ring - otherwise a nearby selected ROI's
                # (often much brighter) sample spot can fall inside this
                # ROI's reference ring and bias its reference mean.
                local_reference_mask = local_reference_mask & ~extra_exclude_mask[ry0:ry1, rx0:rx1]
            if ignored_patch is not None:
                local_roi_mask = local_roi_mask & ~ignored_patch[ry0:ry1, rx0:rx1]
                local_reference_mask = local_reference_mask & ~ignored_patch[ry0:ry1, rx0:rx1]
            sample_pixels = local_patch[local_roi_mask]
            reference_pixels = local_patch[local_reference_mask]
            if sample_pixels.size == 0 or reference_pixels.size == 0:
                empty_reduced = {method: (float("nan"), float("nan")) for method in REDUCTION_METHODS}
                return float("nan"), float("nan"), float("nan"), int(sample_pixels.size), int(reference_pixels.size), empty_reduced
            # Patch-local indices from np.where must be shifted back by the
            # local reach box's own origin AND the patch's (x0, y0) origin so
            # a plane fit is evaluated in the same absolute coordinate frame
            # as roi.center_x/roi.center_y - a mismatch here would silently
            # produce plausible-looking wrong numbers rather than crashing.
            # Only computed when actually needed (see
            # compute_all_reduction_methods below) -
            # reduce_sample_and_reference_all_methods computes every
            # reduction method from these same pixel arrays so switching
            # Reduction afterward doesn't re-read pixels, but that guarantee
            # is only worth paying for on a cube someone might actually
            # switch Reduction on (a live single-cube preview); unconditionally
            # doing this np.where mask scan plus plane_fit's lstsq for every
            # ROI of every wavelength of a hundreds-of-cubes bulk sweep is
            # exactly what made "Start analysis" multiple seconds slower per
            # cube after reduce_sample_and_reference_all_methods was introduced.
            needs_plane_fit_coords = compute_all_reduction_methods or active_reduction_method_key == "plane_fit"
            if needs_plane_fit_coords:
                _where_started = time.perf_counter()
                reference_row_idx, reference_col_idx = np.where(local_reference_mask)
                nonlocal_where_seconds += time.perf_counter() - _where_started
                reference_yy = reference_row_idx.astype(np.float64) + float(y0 + ry0)
                reference_xx = reference_col_idx.astype(np.float64) + float(x0 + rx0)
            else:
                reference_yy = reference_xx = None
            _reduce_started = time.perf_counter()
            if compute_all_reduction_methods:
                reduced_by_method = reduce_sample_and_reference_all_methods(
                    sample_pixels, reference_pixels,
                    trimmed_mean_fraction=trimmed_mean_fraction,
                    reference_xx=reference_xx, reference_yy=reference_yy,
                    sample_x=sample_x, sample_y=sample_y,
                )
                sm, rm = reduced_by_method[active_reduction_method_key]
            else:
                sm, rm = reduce_sample_and_reference(
                    sample_pixels, reference_pixels, active_reduction_method_key,
                    trimmed_mean_fraction=trimmed_mean_fraction,
                    reference_xx=reference_xx, reference_yy=reference_yy,
                    sample_x=sample_x, sample_y=sample_y,
                )
                # Only the active method is computed - a bulk sweep of
                # hundreds of cubes pays this once per ROI per wavelength,
                # so even "cheap" methods add up at real ROI counts (a
                # tried-and-reverted "always compute mean/median/
                # trimmed_mean" version measured +349ms/cube at 160 ROIs -
                # see bulk_analysis_performance_investigation.md's Follow-up
                # #13/#14). Every other method's entry stays NaN ("not
                # computed" for this cube, not a real value).
                reduced_by_method = {
                    method: (sm, rm) if method == active_reduction_method_key else (float("nan"), float("nan"))
                    for method in REDUCTION_METHODS
                }
            nonlocal_reduce_seconds += time.perf_counter() - _reduce_started
            return formula_value(sm, rm, formula_key), sm, rm, int(sample_pixels.size), int(reference_pixels.size), reduced_by_method

        _mask_cache_entry_started = time.perf_counter()
        mask_cache_entry = _fast_roi_mask_cache_entry(affine_matrix)
        nonlocal_mask_seconds += time.perf_counter() - _mask_cache_entry_started

        # Union of every selected ROI's own sample area, used only for the
        # reference-ring exclusion above - not for pooling pixels. With one
        # selected ROI this is unnecessary (its own mask already excludes
        # itself), so it's skipped.
        all_selected_sample_mask = None
        if len(selected_rois) > 1:
            all_selected_sample_mask, _unused_reference_mask = mask_cache_entry["combined"]  # type: ignore[index]

        # Sample and reference ROIs are always reduced to one absorbance value
        # per ROI, independently - never by pooling pixels from multiple ROIs
        # into one sample/reference mean first (pooling before the ratio is
        # not the same calculation as averaging each ROI's own ratio
        # afterward, and mixes pixels from different physical apertures).
        # The "combined" value below, used when several ROIs are selected
        # together, is the average of these per-ROI absorbance values.
        per_roi_masks = mask_cache_entry["per_roi"]  # type: ignore[index]
        per_roi = {
            int(roi.area_roi_id): _means_for(
                [roi], (int(roi.area_roi_id),), roi.center_x, roi.center_y,
                extra_exclude_mask=all_selected_sample_mask,
                precomputed_masks=per_roi_masks[int(roi.area_roi_id)],
            )
            for roi in selected_rois
        }
        finite_entries = [entry for entry in per_roi.values() if np.isfinite(entry[0])]
        total_sample_px = sum(int(entry[3]) for entry in per_roi.values())
        total_reference_px = sum(int(entry[4]) for entry in per_roi.values())
        if finite_entries:
            combined_reduced_by_method_this_wavelength = {
                method: (
                    float(np.mean([entry[5][method][0] for entry in finite_entries])),
                    float(np.mean([entry[5][method][1] for entry in finite_entries])),
                )
                for method in REDUCTION_METHODS
            }
            combined = (
                float(np.mean([entry[0] for entry in finite_entries])),
                float(np.mean([entry[1] for entry in finite_entries])),
                float(np.mean([entry[2] for entry in finite_entries])),
                total_sample_px,
                total_reference_px,
                combined_reduced_by_method_this_wavelength,
            )
        else:
            empty_reduced = {method: (float("nan"), float("nan")) for method in REDUCTION_METHODS}
            combined = (float("nan"), float("nan"), float("nan"), total_sample_px, total_reference_px, empty_reduced)
        # One lock acquisition per wavelength (not per ROI) - _means_for above
        # accumulates into the nonlocal_* floats across all ROIs of this
        # wavelength first, so concurrent _load_wl calls (one per wavelength,
        # via the thread pool below) only ever contend on this single add.
        with _stage_timing_lock:
            _stage_timing_totals["io"] += _io_seconds
            _stage_timing_totals["resample"] += _resample_seconds
            _stage_timing_totals["mask"] += nonlocal_mask_seconds
            _stage_timing_totals["where"] += nonlocal_where_seconds
            _stage_timing_totals["reduce"] += nonlocal_reduce_seconds
        return (index, float(wavelength_nm), combined, per_roi)

    if worker_count_override is not None and worker_count_override > 0:
        # This machine's own calibrated value - see this function's
        # docstring and calibrate_analysis_worker_count. Takes priority
        # over the heuristic below entirely.
        worker_count = max(1, min(int(worker_count_override), len(measurement_payload)))
    else:
        # Fallback heuristic, used only until calibration finishes (or if
        # it fails) - capped at 4, not 8, per Follow-up #4 of
        # bulk_analysis_performance_investigation.md, which directly
        # measured (on the maintainer's specific 4-physical-core machine)
        # that per-wavelength read wall-clock is flat from 1-4 workers and
        # actively *worse* at 8. That cap does NOT generalize to other
        # hardware (Follow-up #11/#12's own finding) - it's a safe default
        # for the brief window before this machine's real number is known,
        # not a claim about what's optimal here.
        worker_count = max(1, min(max(int(os.cpu_count() or 2) // 2, 2), 4, len(measurement_payload)))
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
    formula_values: list[float] = []
    sample_mean_values: list[float] = []
    reference_mean_values: list[float] = []
    sample_pixel_counts: list[int] = []
    reference_pixel_counts: list[int] = []

    for r in results:
        if r is None:
            continue
        _, wl, (formula_val, sm, rm, spc, rpc, reduced_by_method_combined), per_roi = r
        wavelengths_out.append(float(wl))
        formula_values.append(float(formula_val))
        sample_mean_values.append(float(sm))
        reference_mean_values.append(float(rm))
        sample_pixel_counts.append(int(spc))
        reference_pixel_counts.append(int(rpc))
        for method in REDUCTION_METHODS:
            s, rr = reduced_by_method_combined[method]
            combined_reduced_by_method[method].append((float(s), float(rr)))
        for roi in selected_rois:
            roi_formula_val, roi_sm, roi_rm, roi_spc, roi_rpc, roi_reduced_by_method = per_roi[int(roi.area_roi_id)]
            accumulator = roi_accumulators[int(roi.area_roi_id)]
            accumulator["wavelengths"].append(float(wl))
            accumulator["formula_values"].append(float(roi_formula_val))
            accumulator["sample_mean"].append(float(roi_sm))
            accumulator["reference_mean"].append(float(roi_rm))
            accumulator["sample_pixel_count"].append(int(roi_spc))
            accumulator["reference_pixel_count"].append(int(roi_rpc))
            for method in REDUCTION_METHODS:
                s, rr = roi_reduced_by_method[method]
                accumulator["reduced_by_method"][method].append((float(s), float(rr)))

    logging.getLogger("lspr_imaging_app.workflow").debug(
        "SG scoped task stage timing | cube %s | io=%.1fms resample=%.1fms mask=%.1fms where=%.1fms reduce=%.1fms wavelengths=%s rois=%s",
        int(spectral_cube_index),
        _stage_timing_totals["io"] * 1000.0,
        _stage_timing_totals["resample"] * 1000.0,
        _stage_timing_totals["mask"] * 1000.0,
        _stage_timing_totals["where"] * 1000.0,
        _stage_timing_totals["reduce"] * 1000.0,
        total,
        len(selected_rois),
    )

    def _reduced_arrays_by_method(pairs_by_method: dict[str, list[tuple[float, float]]]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        return {
            method: (
                np.asarray([s for s, _r in pairs], dtype=np.float64),
                np.asarray([r for _s, r in pairs], dtype=np.float64),
            )
            for method, pairs in pairs_by_method.items()
        }

    roi_results: dict[int, FormulaSpectrumResult] = {}
    for roi in selected_rois:
        data = roi_accumulators[int(roi.area_roi_id)]
        roi_results[int(roi.area_roi_id)] = FormulaSpectrumResult(
            wavelengths_nm=np.asarray(data["wavelengths"], dtype=np.float64),
            formula_values=np.asarray(data["formula_values"], dtype=np.float64),
            sample_reduced_value=np.asarray(data["sample_mean"], dtype=np.float64),
            reference_reduced_value=np.asarray(data["reference_mean"], dtype=np.float64),
            sample_pixel_count=np.asarray(data["sample_pixel_count"], dtype=np.int32),
            reference_pixel_count=np.asarray(data["reference_pixel_count"], dtype=np.int32),
            reduction_method=str(reduction_method),
            formula_key=str(formula_key),
            reduced_values_by_method=_reduced_arrays_by_method(data["reduced_by_method"]),
        )

    return FormulaSpectrumResult(
        wavelengths_nm=np.asarray(wavelengths_out, dtype=np.float64),
        formula_values=np.asarray(formula_values, dtype=np.float64),
        sample_reduced_value=np.asarray(sample_mean_values, dtype=np.float64),
        reference_reduced_value=np.asarray(reference_mean_values, dtype=np.float64),
        sample_pixel_count=np.asarray(sample_pixel_counts, dtype=np.int32),
        reference_pixel_count=np.asarray(reference_pixel_counts, dtype=np.int32),
        reduction_method=str(reduction_method),
        formula_key=str(formula_key),
        total_seconds=time.perf_counter() - task_started,
        area_roi_results=roi_results,
        reduced_values_by_method=_reduced_arrays_by_method(combined_reduced_by_method),
    )


# How often (in freshly-computed cubes) to force a cyclic-GC pass during a
# sweep despite gc.disable() below - see bulk_analysis_performance_
# investigation.md, Follow-up #11 "Finding D": tifffile's own TiffFile/
# TiffPages/TiffTag/FileHandle objects form real reference cycles (one full
# cluster per file read), which plain reference counting can never free -
# leaving GC off for an entire multi-hundred-cube run measured at ~30MB/cube
# of accumulating, unreachable garbage (~9-10GB projected over a full
# ~314-cube run). Measured cost of a periodic collect(): ~50-65ms per call
# (recovering ~20,000+ objects each time, at this interval) - under 1%
# overhead against a ~1s/cube sweep, and it keeps RSS flat instead of
# growing unbounded.
SENSORGRAM_GC_COLLECT_INTERVAL_CUBES = 10


def _cpu_freq_text() -> str:
    """"current=NNNNMHz max=NNNNMHz", or "" when unavailable (psutil not
    installed, or this platform/CPU doesn't expose live frequency - both
    common, e.g. inside a VM or container). Logged once per cube (see "SG
    cube compute timing" below) to help confirm or rule out CPU frequency
    throttling (a Turbo Boost/Precision Boost PL2->PL1 step-down after
    sustained load) as the cause of a still-open, previously-observed
    mid-run per-cube slowdown - see bulk_analysis_performance_
    investigation.md's Follow-up #11 "leading hypothesis, not yet tested"
    and its own note that this exact measurement was what was missing to
    confirm or rule it out. Cheap enough to leave unconditionally on
    (measured ~2-3us/call - negligible against a ~1s/cube sweep, same
    reasoning as this file's existing time.perf_counter() stage timers).
    """
    if _psutil is None:
        return ""
    try:
        freq = _psutil.cpu_freq()
    except Exception:
        return ""
    if freq is None or not freq.current:
        return ""
    max_text = f" max={freq.max:.0f}MHz" if freq.max else ""
    return f" cpu_freq: current={freq.current:.0f}MHz{max_text}"


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
    metric_value_cache_get=None,
    spectral_cube_formula_spectrum_cache_get=None,
    spectral_cube_formula_spectrum_cache_store=None,
    wl_min: float | None = None,
    wl_max: float | None = None,
    fit_method_key: str = "poly",
    reduction_method: str = "mean",
    trimmed_mean_fraction: float = 0.10,
    formula_key: str = "absorbance",
    compute_all_reduction_methods: bool = True,
    worker_count_override: int | None = None,
) -> SensorgramComputationResult:
    # Cyclic GC is disabled for the run's duration (re-enabled + a one-off
    # collect() in the GUI-thread completion handlers, on_sensorgram_ready/
    # on_sensorgram_failed) - the periodic collect() below (every
    # SENSORGRAM_GC_COLLECT_INTERVAL_CUBES freshly-computed cubes) is still
    # needed despite this: reference counting alone does NOT free everything
    # here, see that constant's own comment.
    import gc as _gc

    _gc.disable()
    _gc_countdown = SENSORGRAM_GC_COLLECT_INTERVAL_CUBES
    task_started = time.perf_counter()
    spectral_cube_payloads: list[tuple[int, tuple[object, ...]]] = []
    total_input_count = len(spectral_cube_payloads_or_spectral_cubes) if hasattr(spectral_cube_payloads_or_spectral_cubes, "__len__") else 0
    prep_seconds = 0.0
    fit_seconds = 0.0
    prep_cancelled = False
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
                # Always collect this future first - as_completed only
                # yields it once it's already finished, so this is free (no
                # extra wait), and skipping it would throw away completed
                # work purely because of scheduling luck (many builder
                # threads can finish before the main thread even reaches its
                # first loop iteration). The cancel check below only decides
                # whether to keep waiting for *further*, not-yet-done cubes.
                payload = future.result()
                if payload is not None:
                    built_payloads.append((spectral_cube_index, payload))
                completed += 1
                if progress_callback is not None:
                    progress_callback(
                        int(round((completed / max(total_input_count, 1)) * 20.0)),
                        f"Preparing spectral cube reads {completed}/{total_input_count}",
                    )
                if cancel_event is not None and cancel_event.is_set():
                    # Stop waiting for/loading more cubes, but keep what's
                    # already in `built_payloads` - each already paid its
                    # (usually dominant) image I/O cost, so fitting them
                    # afterward is cheap and would otherwise be wasted.
                    # Previously this returned empty arrays unconditionally
                    # as soon as a cancel was seen, which threw away every
                    # already-loaded cube whenever Stop landed during this
                    # prep phase - the display had nothing to show and
                    # analysis_controller.py's per-point backup (which
                    # drives HDF5 export) never got a single point to
                    # persist, so Stop could make export produce an
                    # essentially empty file even after real work was done.
                    prep_cancelled = True
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
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
        # `prep_cancelled` means `spectral_cube_payloads` is already the
        # smaller, already-fully-loaded batch prep stopped early with - each
        # of those cubes already paid its I/O cost, so discarding all of them
        # outright (the pre-fix behavior) wasted real completed work and
        # could leave HDF5 export with nothing to persist even though cubes
        # were fully read. But exempting `prep_cancelled` from every
        # remaining cancellation check for the rest of this batch (the
        # original fix) went too far the other way: Stop pressed during prep
        # became unable to stop anything else afterward, no matter how many
        # cubes were still queued up. Exempt only until the FIRST cube of
        # this batch has actually been recorded - that alone guarantees a
        # prep-time Stop never returns a completely empty result - then go
        # back to honoring cancel_event normally for cube #2 onward, so
        # holding Stop through this phase still interrupts it.
        exempt_from_cancel_check = prep_cancelled and not spectral_cube_indices
        if not exempt_from_cancel_check and cancel_event is not None and cancel_event.is_set():
            return SensorgramComputationResult(
                spectral_cube_indices=np.asarray(spectral_cube_indices, dtype=np.int32),
                metric_values=np.asarray(metric_values, dtype=np.float64),
                metric_signal=np.asarray(metric_signals, dtype=np.float64),
                completed_count=len(spectral_cube_indices),
                total_count=len(spectral_cube_payloads),
                prep_seconds=prep_seconds,
                fit_seconds=time.perf_counter() - compute_started,
                total_seconds=time.perf_counter() - task_started,
                cancelled=True,
            )

        def spectral_cube_progress_callback(percent: int, text: str | None = None, *, spectral_cube_number: int = int(spectral_cube_index), position: int = index) -> None:
            if progress_callback is None:
                return
            inner_percent = float(np.clip(float(percent), 0.0, 100.0))
            overall = compute_base + (((position - 1) + (inner_percent / 100.0)) / total) * compute_span
            # Cube position is prefixed onto whatever the inner task reports
            # (e.g. "Fast spectrum 5/20: 650 nm") rather than only shown as a
            # fallback for when `text` is empty - the inner task always
            # supplies text, so without this prefix the status bar shows
            # per-wavelength progress with no indication of which cube (of
            # how many) is currently running.
            progress_callback(
                int(round(overall)),
                f"Cube {position}/{total} | {text}" if text else f"Sensorgram {position}/{total}: spectral cube {spectral_cube_number}",
            )

        # Disk-backed shortcut: a backed-up HDF5 row can supply the already-
        # reduced final metric_value for this exact (preprocessing/ROI/fit)
        # signature, skipping both the pixel read/spectrum build below AND
        # the fit - unlike spectral_cube_result_cache_get, which only ever
        # skips the read (see the callback's own docstring in
        # analysis_controller.py). `metric_signal` isn't persisted on disk
        # (only metric_value is), so a disk hit always reports it as NaN -
        # the same "no signal available" state a legacy/never-fitted point
        # already produces; nothing currently plots a full-sweep
        # metric_signal series (only the live single-cube preview does, which
        # this shortcut never touches), so this loses no working UI.
        disk_metric_value = metric_value_cache_get(spectral_cube_index) if metric_value_cache_get is not None else None
        roi_formula_spectrum_results = None
        per_roi_metric_values = None
        if disk_metric_value is not None:
            metric_float = float(disk_metric_value) if np.isfinite(disk_metric_value) else float("nan")
            signal_float = float("nan")
        else:
            spectrum = spectral_cube_result_cache_get(spectral_cube_index) if spectral_cube_result_cache_get is not None else None
            # Next-cheapest: a combined result already sitting in the
            # interactive per-ROI cache, or resumed from the HDF5 backup
            # (see AnalysisController._combined_absorbance_results_from_ram_or_disk) -
            # skips the pixel read just like spectral_cube_result_cache_get,
            # just via a different cache this loop didn't populate itself.
            if spectrum is None and spectral_cube_formula_spectrum_cache_get is not None:
                spectrum = spectral_cube_formula_spectrum_cache_get(spectral_cube_index)
            freshly_computed = False
            # Logged unconditionally (not just on a slow cube): cheap, and
            # matches the existing stage-timing convention used elsewhere
            # (e.g. _process_image_task's "Image raw load" log) for
            # answering "which stage is actually slow" without re-adding
            # instrumentation each time a slowdown gets reported.
            _cube_compute_started = time.perf_counter()
            if spectrum is None:
                _active_task = task_fn if task_fn is not None else _scoped_formula_spectrum_task
                _extra_task_kwargs = {}
                # Only passed when actually set - a custom task_fn (e.g. a
                # test's fixed-signature stub) never needs to know about
                # this, and omitting it when None keeps every such stub
                # working unchanged rather than requiring them all to grow
                # a **kwargs catch-all just for this.
                if worker_count_override is not None:
                    _extra_task_kwargs["worker_count_override"] = worker_count_override
                spectrum = _active_task(
                    *payload,
                    # None (not `cancel_event`) only for the one cube exempted
                    # above - letting its own per-wavelength loaders run to
                    # completion rather than bailing to empty/NaN results, so
                    # that first post-prep-cancel cube is a real, usable
                    # point rather than a discarded one.
                    cancel_event=None if exempt_from_cancel_check else cancel_event,
                    progress_callback=spectral_cube_progress_callback,
                    reduction_method=reduction_method,
                    trimmed_mean_fraction=trimmed_mean_fraction,
                    formula_key=formula_key,
                    compute_all_reduction_methods=compute_all_reduction_methods,
                    **_extra_task_kwargs,
                )
                freshly_computed = True
                if spectral_cube_result_cache_store is not None:
                    spectral_cube_result_cache_store(spectral_cube_index, spectrum)
                # See SENSORGRAM_GC_COLLECT_INTERVAL_CUBES's own comment -
                # only freshly-computed cubes create the tifffile reference
                # cycles this exists to reclaim, so a disk/cache-hit cube
                # doesn't count toward the interval.
                _gc_countdown -= 1
                if _gc_countdown <= 0:
                    _gc.collect()
                    _gc_countdown = SENSORGRAM_GC_COLLECT_INTERVAL_CUBES
            logging.getLogger("lspr_imaging_app.workflow").debug(
                "SG cube compute timing | cube %s | %.1fms | cache_hit=%s |%s",
                int(spectral_cube_index),
                (time.perf_counter() - _cube_compute_started) * 1000.0,
                not freshly_computed,
                _cpu_freq_text(),
            )
            if not exempt_from_cancel_check and cancel_event is not None and cancel_event.is_set():
                return SensorgramComputationResult(
                    spectral_cube_indices=np.asarray(spectral_cube_indices, dtype=np.int32),
                    metric_values=np.asarray(metric_values, dtype=np.float64),
                    metric_signal=np.asarray(metric_signals, dtype=np.float64),
                    completed_count=len(spectral_cube_indices),
                    total_count=len(spectral_cube_payloads),
                    prep_seconds=prep_seconds,
                    fit_seconds=time.perf_counter() - compute_started,
                    total_seconds=time.perf_counter() - task_started,
                    cancelled=True,
                )

            roi_formula_spectrum_results = getattr(spectrum, "area_roi_results", None) or None
            # Only a *fresh* compute needs to populate the interactive per-ROI
            # cache - RAM/disk hits above are already there (a disk hit was
            # just written into it by spectral_cube_absorbance_cache_get
            # itself; a spectral_cube_result_cache_get hit was already fresh-
            # computed and stored on some earlier call this run).
            if freshly_computed and roi_formula_spectrum_results and spectral_cube_formula_spectrum_cache_store is not None:
                spectral_cube_formula_spectrum_cache_store(spectral_cube_index, roi_formula_spectrum_results)

            if fit_method_key == "none":
                metric_value, metric_signal = metric_value_from_spectrum(
                    spectrum.wavelengths_nm, spectrum.formula_values, metric_key, wl_min=wl_min, wl_max=wl_max
                )
            else:
                fit = fit_curve_for_method(
                    spectrum.wavelengths_nm,
                    spectrum.formula_values,
                    fit_method_key,
                    poly_order=poly_order,
                    wl_min=wl_min,
                    wl_max=wl_max,
                )
                metric_value, metric_signal = metric_value_from_fit(fit, metric_key)
            metric_float = float(metric_value) if metric_value is not None and np.isfinite(metric_value) else float("nan")
            signal_float = float(metric_signal) if metric_signal is not None and np.isfinite(metric_signal) else float("nan")

            # Each selected ROI's own metric, fit from its own spectrum
            # (roi_formula_spectrum_results, populated above) under the same
            # fit_method_key/metric_key/poly_order/wl_min/wl_max as the
            # combined value just computed - "core data" for later per-ROI
            # statistical analysis, independent of whatever combination is
            # selected for the interactive combined trace above. Logged
            # once per cube (aggregated), not per ROI - see this repo's
            # stage-timing convention (CLAUDE.md's Performance Work rules).
            if roi_formula_spectrum_results:
                per_roi_metric_values = {}
                _per_roi_fit_started = time.perf_counter()
                for _roi_id, _roi_spectrum in roi_formula_spectrum_results.items():
                    if fit_method_key == "none":
                        _roi_metric_value, _roi_metric_signal = metric_value_from_spectrum(
                            _roi_spectrum.wavelengths_nm, _roi_spectrum.formula_values, metric_key, wl_min=wl_min, wl_max=wl_max
                        )
                    else:
                        _roi_fit = fit_curve_for_method(
                            _roi_spectrum.wavelengths_nm,
                            _roi_spectrum.formula_values,
                            fit_method_key,
                            poly_order=poly_order,
                            wl_min=wl_min,
                            wl_max=wl_max,
                        )
                        _roi_metric_value, _roi_metric_signal = metric_value_from_fit(_roi_fit, metric_key)
                    per_roi_metric_values[int(_roi_id)] = (
                        float(_roi_metric_value) if _roi_metric_value is not None and np.isfinite(_roi_metric_value) else float("nan"),
                        float(_roi_metric_signal) if _roi_metric_signal is not None and np.isfinite(_roi_metric_signal) else float("nan"),
                    )
                logging.getLogger("lspr_imaging_app.workflow").debug(
                    "SG per-roi metric fit timing | cube %s | rois=%s | %.1fms",
                    int(spectral_cube_index),
                    len(per_roi_metric_values),
                    (time.perf_counter() - _per_roi_fit_started) * 1000.0,
                )

        spectral_cube_indices.append(int(spectral_cube_index))
        metric_values.append(metric_float)
        metric_signals.append(signal_float)

        if partial_callback is not None:
            partial_callback(
                SensorgramPointResult(
                    spectral_cube_index=int(spectral_cube_index),
                    metric_value=None if not np.isfinite(metric_float) else metric_float,
                    metric_signal=None if not np.isfinite(signal_float) else signal_float,
                    roi_formula_spectrum_results=roi_formula_spectrum_results,
                    per_roi_metric_values=per_roi_metric_values,
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
        # `prep_cancelled` means the loop above ran to completion, but only
        # over the smaller batch prep managed to load before Stop was
        # pressed - still a stopped run, not a normal full completion.
        cancelled=prep_cancelled,
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
        # Also drop 0 nm (broadband/no-filter frame) here, mirroring
        # ChromaticController.candidate_chromatic_wavelengths -- the UI never
        # lets the user mark landmarks on it, so it must never be selected as
        # a sample wavelength either.
        reference_cube_wavelengths = sorted(
            {
                float(wavelength)
                for spectral_cube_index, wavelength, _path in record_specs
                if int(spectral_cube_index) == reference_spectral_cube and float(wavelength) != 0.0
            }
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

        # The reference wavelength need not itself be landmark-marked - the
        # sampled/marked wavelengths are their own independent evenly-spaced
        # grid (chromatic_sample_image_count), unrelated to whatever the
        # reference is currently set to. Fitting therefore anchors on a
        # landmark-marked sample instead (the one nearest the reference, to
        # keep the one extra interpolation step this needs as short as
        # possible), then every anchor-relative transform - whether directly
        # fit or itself interpolated - is re-expressed relative to the true
        # reference by composing it with a reference<->anchor transform (see
        # compose_affine_matrices). This is the ordinary "translate a
        # measurement between two arbitrary basepoints" trick: anchor->target
        # composed with reference->anchor gives reference->target, with no
        # requirement that the reference itself was ever directly measured.
        # When the reference *is* one of the landmark-marked samples (the
        # common case), landmark_anchor_wavelength lands exactly on it,
        # reference_to_anchor collapses to identity, and every result is
        # numerically identical to the anchor-relative matrix alone - i.e.
        # this subsumes the old (buggy) reference-anchored-only behavior
        # rather than changing it when the old assumption already held.
        complete_sample_wavelengths = [
            wavelength
            for wavelength in sampled_wavelengths
            if all(feature_id in landmarks_by_wavelength.get(float(wavelength), {}) for feature_id in expected_feature_ids)
        ]
        if not complete_sample_wavelengths:
            raise ValueError(
                f"Mark all {len(expected_feature_ids)} reference points on at least one sampled wavelength image "
                "before estimating chromatic transforms."
            )
        landmark_anchor_wavelength = float(
            min(complete_sample_wavelengths, key=lambda wavelength: abs(float(wavelength) - reference_wavelength))
        )
        anchor_landmarks = landmarks_by_wavelength[landmark_anchor_wavelength]
        anchor_points = np.asarray(
            [anchor_landmarks[feature_id] for feature_id in expected_feature_ids],
            dtype=np.float64,
        )

        sample_matrices: dict[float, np.ndarray] = {}
        sample_rmse: dict[float, float] = {}
        direct_feature_counts: dict[float, int] = {}
        total = max(len(sampled_wavelengths), 1)
        for index, wavelength in enumerate(sampled_wavelengths, start=1):
            marks = landmarks_by_wavelength.get(float(wavelength), {})
            missing = [feature_id for feature_id in expected_feature_ids if feature_id not in marks]
            if missing:
                raise ValueError(
                    f"Sample wavelength {wavelength:g} nm is missing reference point(s): "
                    + ", ".join(str(feature_id) for feature_id in missing)
                )
            if abs(float(wavelength) - landmark_anchor_wavelength) < 1e-6:
                matrix = identity_affine_matrix()
                rmse = 0.0
            else:
                target_points = np.asarray([marks[feature_id] for feature_id in expected_feature_ids], dtype=np.float64)
                if landmark_model == "similarity" and len(expected_feature_ids) >= 2:
                    matrix = fit_similarity_matrix(anchor_points, target_points)
                else:
                    matrix = fit_affine_matrix(anchor_points, target_points)
                residuals = np.sqrt(np.sum((apply_affine_to_points(anchor_points, matrix) - target_points) ** 2, axis=1))
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

        def _anchor_relative_matrix(wavelength_f64: float) -> np.ndarray:
            if wavelength_f64 in sample_matrices:
                return sample_matrices[wavelength_f64]
            interpolated_matrix = np.empty((2, 3), dtype=np.float64)
            for row in range(2):
                for col in range(3):
                    interpolated_matrix[row, col] = float(
                        np.interp(wavelength_f64, sample_axis, matrix_values_array[:, row, col])
                    )
            return interpolated_matrix

        # The one genuinely new interpolation this scheme needs: the anchor's
        # own transform *to* the true reference wavelength, estimated the
        # same way any other non-sampled wavelength's transform already is
        # (or read off directly, if the reference happens to coincide with a
        # sampled wavelength).
        anchor_to_reference = _anchor_relative_matrix(reference_wavelength)
        reference_to_anchor = invert_affine_matrix(anchor_to_reference)

        matrices_by_wavelength: dict[float, np.ndarray] = {}
        rmse_by_wavelength: dict[float, float] = {}
        feature_counts_by_wavelength: dict[float, int] = {}
        for wavelength in all_wavelengths:
            wavelength_f64 = float(wavelength)
            anchor_relative = _anchor_relative_matrix(wavelength_f64)
            matrices_by_wavelength[wavelength_f64] = compose_affine_matrices(anchor_relative, reference_to_anchor)
            if wavelength_f64 in sample_matrices:
                rmse_by_wavelength[wavelength_f64] = sample_rmse[wavelength_f64]
                feature_counts_by_wavelength[wavelength_f64] = direct_feature_counts[wavelength_f64]
            else:
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
    # Computed once and reused below: estimate_affine_chromatic_transform would
    # otherwise redo this full-image band-pass (two Gaussian blurs + two Sobel
    # passes) for the same reference image on every wavelength in the loop.
    reference_prepared = prepare_registration_image(reference_processed)
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
                reference_prepared=reference_prepared,
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
