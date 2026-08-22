from __future__ import annotations

import logging
import time

import numpy as np
from scipy import ndimage

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings, MaskSettings, PreprocessingSettings
from lspr_imaging_app.processing.roi_detection import ignored_pixel_mask

_LOGGER = logging.getLogger(__name__)


def apply_preprocessing(
    image: np.ndarray,
    settings: PreprocessingSettings,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    external_mask_processed: bool = False,
    mask_state: MaskSettings | None = None,
    region: tuple[int, int, int, int] | None = None,
    skip_crop: bool = False,
    log_stage_timing: bool = False,
) -> np.ndarray:
    """`region` (x0, y0, x1, y1), if given, only affects what's *returned* —
    masking and the rotate/flip/crop step always run on the whole image
    (spatial alignment and, when background flattening is on, the background
    estimate itself both need full-image context regardless of region). When
    background flattening is on, `region` is passed through to
    flatten_background to skip materializing/upsampling values nobody reads;
    otherwise the full processed image is just sliced before returning.

    `skip_crop`, if set, applies rotation/flip as usual but skips the final
    crop slice — used for the live display while the crop/rotate tool is
    open, so the crop rectangle is always drawn over the same rotated/flipped
    canvas its coordinates are defined against (see the CLAUDE.md pitfall on
    `image_tools_enabled`: getting this canvas wrong is what causes crop
    settings to compound into an over-cropped image on session restore).

    `log_stage_timing`, if set, emits one DEBUG log line breaking the call
    down into mask/spatial-transform/flatten-background time (stays invisible
    in the console unless Debug mode is on - see
    workflow_log_controller.append_workflow_log_entry - but always reaches
    the session log file, matching every other DEBUG line). Opt-in and
    defaults off: this function also runs inside tight per-frame loops (batch
    sensorgram/absorbance calculations, patch-based ROI reads), where a log
    line per call would flood the log for no benefit - only the single-image
    display refresh path (_process_image_task) passes True.
    """
    stage_started_at = time.perf_counter() if log_stage_timing else 0.0

    # Apply mask to raw image before spatial preprocessing
    masked_image = image

    # Apply new mask system to raw image
    combined_mask = None
    if mask_state is not None:
        if mask_state.histogram_enabled and mask_state.histogram_mask is not None:
            if combined_mask is None:
                combined_mask = mask_state.histogram_mask.copy()
            else:
                combined_mask |= mask_state.histogram_mask

        if mask_state.figure_enabled and mask_state.figure_mask is not None:
            if combined_mask is None:
                combined_mask = mask_state.figure_mask.copy()
            else:
                combined_mask |= mask_state.figure_mask

    # Apply combined new masks
    if combined_mask is not None:
        masked_image = np.where(combined_mask.astype(bool), 0, masked_image)

    # Apply legacy external mask if provided, in raw image coordinates
    if external_mask is not None and not external_mask_processed:
        masked_image = np.where(external_mask.astype(bool), 0, masked_image)

    if log_stage_timing:
        mask_elapsed = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()

    processed = apply_spatial_preprocessing(masked_image, settings, skip_crop=skip_crop)

    # A pre-transformed external mask (caller already rotated/flipped/cropped
    # it to match `processed`, e.g. via apply_spatial_mask) can't be applied
    # before the spatial transform above like the raw-space case is - do it
    # here instead. Previously this branch did nothing: the mask was neither
    # zeroed into `processed` nor forwarded to flatten_background, so a
    # processed-space mask (the normal case - every caller resolves masks via
    # processed_space=True) silently had no effect on background flattening,
    # letting masked-out regions (e.g. rotation-fill black edges) still pull
    # on the local background average.
    processed_exclusion_mask = None
    if external_mask is not None and external_mask_processed:
        candidate = np.asarray(external_mask, dtype=bool)
        if candidate.shape == processed.shape[:2]:
            processed_exclusion_mask = candidate
            processed = np.where(processed_exclusion_mask, 0, processed)

    if log_stage_timing:
        spatial_elapsed = time.perf_counter() - stage_started_at

    if settings.flatten_background_enabled:
        if log_stage_timing:
            stage_started_at = time.perf_counter()
        # Rotation-fill pixels (see rotation_fill_pixel_mask) are excluded from
        # the background estimate unconditionally - unlike the mask/ROI
        # exclusions above, this isn't optional curation the
        # flatten_background_exclude_mask toggle should gate: those pixels
        # never had a real measurement, so letting them pull on the local
        # background average is never correct, toggle or not.
        rotation_mask = rotation_fill_pixel_mask(image.shape[:2], settings, skip_crop=skip_crop)
        processed = flatten_background(
            processed,
            sigma_px=float(settings.flatten_background_sigma_px),
            binning=max(int(getattr(settings, "flatten_background_binning", 2)), 1),
            rois=rois if settings.flatten_background_exclude_area_rois else None,
            mask_settings=mask_settings if settings.flatten_background_exclude_mask else None,
            external_mask=processed_exclusion_mask if settings.flatten_background_exclude_mask else None,
            rotation_fill_mask=rotation_mask,
            region=region,
            exclusion_dilation_px=int(getattr(settings, "flatten_background_exclusion_dilation_px", 0)),
        )
        if log_stage_timing:
            flatten_elapsed = time.perf_counter() - stage_started_at
            _LOGGER.debug(
                "Image process stages | mask=%.0fms spatial=%.0fms flatten=%.0fms total=%.0fms | rois=%d",
                mask_elapsed * 1000.0, spatial_elapsed * 1000.0, flatten_elapsed * 1000.0,
                (mask_elapsed + spatial_elapsed + flatten_elapsed) * 1000.0,
                len(rois) if rois else 0,
            )
        return processed

    if log_stage_timing:
        _LOGGER.debug(
            "Image process stages | mask=%.0fms spatial=%.0fms flatten=off total=%.0fms",
            mask_elapsed * 1000.0, spatial_elapsed * 1000.0, (mask_elapsed + spatial_elapsed) * 1000.0,
        )

    if region is not None:
        x0, y0, x1, y1 = region
        return processed[y0:y1, x0:x1]
    return processed


def apply_spatial_preprocessing(
    image: np.ndarray,
    settings: PreprocessingSettings,
    *,
    skip_crop: bool = False,
) -> np.ndarray:
    # Pixels added by rotation (the corners outside the original image) have no
    # real measurement behind them. "nearest" stretches the nearest edge pixel
    # into that area (no scientific meaning, but visually seamless); the
    # rotation_fill_dark toggle instead fills it with 0 so it's clearly marked
    # as "not data".
    fill_mode = "constant" if bool(settings.rotation_fill_dark) else "nearest"
    return _apply_spatial_transform(image, settings, order=1, mode=fill_mode, cval=0.0, skip_crop=skip_crop)


def apply_spatial_mask(
    mask: np.ndarray | None,
    settings: PreprocessingSettings,
) -> np.ndarray | None:
    if mask is None:
        return None
    transformed = _apply_spatial_transform(mask.astype(np.float32, copy=False), settings, order=0, mode="constant", cval=0.0)
    return transformed >= 0.5


def rotation_fill_pixel_mask(
    raw_shape: tuple[int, int],
    settings: PreprocessingSettings,
    *,
    skip_crop: bool = False,
) -> np.ndarray | None:
    """Boolean mask, in the same processed-image space apply_spatial_preprocessing
    returns, marking pixels rotation padding synthesized - no source
    measurement behind them at all, independent of whether rotation_fill_dark
    shows that padding as black (constant fill) or as a stretched copy of the
    nearest edge pixel ("nearest" fill, see apply_spatial_preprocessing):
    either way it isn't real data.

    Returns None when no rotation is active - flip and crop alone never add
    pixels the source image didn't have, so there's nothing to mark.
    """
    if not bool(getattr(settings, "image_tools_enabled", True)):
        return None
    if abs(float(settings.rotation_angle_deg)) <= 1e-9:
        return None
    valid_source = np.ones(raw_shape, dtype=np.float32)
    transformed = _apply_spatial_transform(valid_source, settings, order=0, mode="constant", cval=0.0, skip_crop=skip_crop)
    return transformed < 0.5


def spatial_coordinate_maps(
    image_shape: tuple[int, int],
    settings: PreprocessingSettings,
) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.indices(image_shape, dtype=np.float32)
    x_map = _apply_spatial_transform(xx, settings, order=1, mode="nearest", cval=0.0)
    y_map = _apply_spatial_transform(yy, settings, order=1, mode="nearest", cval=0.0)
    return x_map, y_map


def spatial_output_shape(
    image_shape: tuple[int, int],
    settings: PreprocessingSettings,
) -> tuple[int, int]:
    height, width = max(int(image_shape[0]), 1), max(int(image_shape[1]), 1)
    # Use the exact same transform path as the real image pipeline, because
    # ndimage.rotate(..., reshape=True) can differ by 1 px from simple trig.
    probe = np.zeros((height, width), dtype=np.uint8)
    transformed = _apply_spatial_transform(probe, settings, order=0, mode="constant", cval=0.0)
    return int(transformed.shape[0]), int(transformed.shape[1])


def _rotation_geometry(
    in_shape: tuple[int, int],
    angle_deg: float,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Reproduce scipy.ndimage.rotate(reshape=True)'s geometry formula exactly
    (same matrix/offset/output-shape math as scipy/ndimage/_interpolation.py)
    without allocating the full rotated array. For any pixel (r, c) of that
    *full* rotated canvas, the corresponding source pixel is
    `rot_matrix @ [r, c] + offset`.
    """
    from scipy import special

    iy, ix = int(in_shape[0]), int(in_shape[1])
    c, s = special.cosdg(angle_deg), special.sindg(angle_deg)
    rot_matrix = np.array([[c, s], [-s, c]])

    out_bounds = rot_matrix @ np.array([[0, 0, iy, iy], [0, ix, 0, ix]], dtype=float)
    out_plane_shape = (np.ptp(out_bounds, axis=1) + 0.5).astype(int)
    out_h, out_w = int(out_plane_shape[0]), int(out_plane_shape[1])

    out_center = rot_matrix @ ((out_plane_shape - 1) / 2)
    in_center = np.array([(iy - 1) / 2, (ix - 1) / 2])
    offset = in_center - out_center
    return rot_matrix, offset, (out_h, out_w)


def _combined_export_transform(
    in_shape: tuple[int, int],
    settings: PreprocessingSettings,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Fold rotate -> flip -> crop into one affine map from the final
    (post-crop) output straight back to source pixels, so a plane can be
    resampled directly at its final size instead of interpolating the full
    rotated canvas (ndimage.rotate(reshape=True)) and discarding most of it
    in a slice. Pixel-for-pixel equivalent to _apply_spatial_transform;
    verified in scripts used to build this — see OME-Zarr export perf notes.
    Export-hot-path use only; GUI preview/mask paths are untouched.
    """
    angle = float(settings.rotation_angle_deg)
    rot_matrix, offset, (out_h, out_w) = _rotation_geometry(in_shape, angle)

    fy_sign = -1.0 if settings.flip_vertical else 1.0
    fy_const = float(out_h - 1) if settings.flip_vertical else 0.0
    fx_sign = -1.0 if settings.flip_horizontal else 1.0
    fx_const = float(out_w - 1) if settings.flip_horizontal else 0.0
    flip_diag = np.array([[fy_sign, 0.0], [0.0, fx_sign]])
    flip_const = np.array([fy_const, fx_const])

    crop = settings.crop
    if crop.enabled and crop.width > 0 and crop.height > 0:
        x0 = max(0, min(int(crop.x), out_w - 1))
        y0 = max(0, min(int(crop.y), out_h - 1))
        x1 = max(x0 + 1, min(x0 + int(crop.width), out_w))
        y1 = max(y0 + 1, min(y0 + int(crop.height), out_h))
    else:
        y0, x0, y1, x1 = 0, 0, out_h, out_w

    crop_origin = np.array([float(y0), float(x0)])
    translate = flip_diag @ crop_origin + flip_const

    combined_matrix = rot_matrix @ flip_diag
    combined_offset = rot_matrix @ translate + offset
    return combined_matrix, combined_offset, (y1 - y0, x1 - x0)


def combined_transform_for_box(
    in_shape: tuple[int, int],
    settings: PreprocessingSettings,
    box: tuple[int, int, int, int],
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Like _combined_export_transform, but maps an arbitrary PROCESSED-space
    sub-box — e.g. a small region around one or more ROIs, in the same
    coordinate system as ROI centers, rather than the whole
    rotate+flip+crop output — back to raw input coordinates.

    `box` is `(x0, y0, x1, y1)` in PROCESSED-space (i.e. relative to the
    existing crop, if any — same space as _combined_export_transform's
    output and as ROI centers). Used to read+resample just a small patch
    around selected ROIs when rotation/flip is active, instead of the whole
    plane — the same idea as _combined_export_transform, just parameterized
    by an arbitrary target box instead of always the full configured crop.
    """
    angle = float(settings.rotation_angle_deg)
    rot_matrix, offset, (out_h, out_w) = _rotation_geometry(in_shape, angle)

    fy_sign = -1.0 if settings.flip_vertical else 1.0
    fy_const = float(out_h - 1) if settings.flip_vertical else 0.0
    fx_sign = -1.0 if settings.flip_horizontal else 1.0
    fx_const = float(out_w - 1) if settings.flip_horizontal else 0.0
    flip_diag = np.array([[fy_sign, 0.0], [0.0, fx_sign]])
    flip_const = np.array([fy_const, fx_const])

    crop = settings.crop
    if crop.enabled and crop.width > 0 and crop.height > 0:
        crop_x0 = max(0, min(int(crop.x), out_w - 1))
        crop_y0 = max(0, min(int(crop.y), out_h - 1))
    else:
        crop_x0, crop_y0 = 0, 0

    box_x0, box_y0, box_x1, box_y1 = box
    # Shift the box from processed-space (relative to the existing crop) into
    # rotated+flipped-canvas space (what the crop itself is defined against),
    # matching _combined_export_transform's crop_origin convention exactly.
    canvas_origin = np.array([float(crop_y0 + box_y0), float(crop_x0 + box_x0)])
    translate = flip_diag @ canvas_origin + flip_const

    combined_matrix = rot_matrix @ flip_diag
    combined_offset = rot_matrix @ translate + offset
    return combined_matrix, combined_offset, (box_y1 - box_y0, box_x1 - box_x0)


def raw_bounding_box_for_processed_box(
    in_shape: tuple[int, int],
    settings: PreprocessingSettings,
    box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """The axis-aligned raw-space box that must be read to resample a given
    PROCESSED-space box, accounting for rotation. Rotating a rectangle
    produces a rotated rectangle in source space, so this returns the
    (possibly larger) enclosing axis-aligned box — mapping the processed
    box's 4 corners back through the same combined transform and taking the
    min/max.
    """
    matrix, offset, _ = combined_transform_for_box(in_shape, settings, box)
    box_x0, box_y0, box_x1, box_y1 = box
    box_h, box_w = box_y1 - box_y0, box_x1 - box_x0
    corners = np.array([[0, 0], [0, box_w], [box_h, 0], [box_h, box_w]], dtype=np.float64)
    raw_corners = (matrix @ corners.T).T + offset
    raw_y0 = int(np.floor(raw_corners[:, 0].min()))
    raw_y1 = int(np.ceil(raw_corners[:, 0].max())) + 1
    raw_x0 = int(np.floor(raw_corners[:, 1].min()))
    raw_x1 = int(np.ceil(raw_corners[:, 1].max())) + 1

    # Clamp to a valid, non-empty, in-bounds box. A box near a rotated
    # canvas's edge-stretch-filled corner can have its true (unclamped) raw
    # extent fall entirely outside the raw image — clamping x0/x1 (or y0/y1)
    # independently to [0, dim] could then invert (x0 > x1). Clamping x0 into
    # a valid index first, then x1 to be at least x0+1, guarantees a valid
    # box that still contains the correct nearest-edge pixel for "nearest"
    # fill mode, instead of an empty/inverted slice.
    raw_height, raw_width = int(in_shape[0]), int(in_shape[1])
    raw_y0 = min(max(raw_y0, 0), raw_height - 1)
    raw_x0 = min(max(raw_x0, 0), raw_width - 1)
    raw_y1 = max(min(raw_y1, raw_height), raw_y0 + 1)
    raw_x1 = max(min(raw_x1, raw_width), raw_x0 + 1)
    return raw_x0, raw_y0, raw_x1, raw_y1


def resample_raw_patch_to_processed_box(
    raw_patch: np.ndarray,
    raw_patch_origin_xy: tuple[int, int],
    in_shape: tuple[int, int],
    settings: PreprocessingSettings,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    """Resample a raw-space patch (already read via a chunk-aware partial
    read, at `raw_patch_origin_xy` within the full raw image) directly into
    the final PROCESSED-space `box`, applying rotation/flip. Mirrors
    apply_spatial_preprocessing_export's interpolation (scipy affine_transform
    with the same fill-mode handling) but scoped to a small box instead of
    the whole plane, and reading from an already-cropped-to-the-needed-region
    raw patch instead of the full raw array.
    """
    matrix, offset, out_shape = combined_transform_for_box(in_shape, settings, box)
    if out_shape[0] <= 0 or out_shape[1] <= 0:
        return np.zeros(out_shape, dtype=raw_patch.dtype)
    raw_x0, raw_y0 = raw_patch_origin_xy
    # raw_coord = matrix @ [r,c] + offset (full raw image coords); raw_patch's
    # own local coords are raw_coord - (raw_y0, raw_x0), i.e. a plain
    # subtraction from the offset term, not a matrix-multiplied one.
    local_offset = offset - np.array([float(raw_y0), float(raw_x0)])
    fill_mode = "constant" if bool(settings.rotation_fill_dark) else "nearest"
    return ndimage.affine_transform(
        raw_patch,
        matrix,
        local_offset,
        output_shape=out_shape,
        order=1,
        mode=fill_mode,
        cval=0.0,
        prefilter=False,
    )


def apply_spatial_preprocessing_export(
    image: np.ndarray,
    settings: PreprocessingSettings,
) -> np.ndarray:
    """Fast equivalent of apply_spatial_preprocessing for the OME-Zarr export
    hot path: resamples directly at the final rotate/flip/crop size instead
    of materializing the full ndimage.rotate(reshape=True) canvas and slicing
    it down afterward. When the configured crop is much smaller than the full
    spectral_cube_index, this avoids interpolating pixels that would just be discarded.
    Produces the same output as apply_spatial_preprocessing (same fill mode,
    same crop clamping) — only the amount of work differs.
    """
    if not bool(getattr(settings, "image_tools_enabled", True)):
        return image

    fill_mode = "constant" if bool(settings.rotation_fill_dark) else "nearest"
    angle = float(settings.rotation_angle_deg)
    if abs(angle) <= 1e-9 or min(image.shape[:2]) <= 4:
        # No rotation: flip + crop are cheap exact array ops already, no
        # interpolation involved, so the existing path is both simplest and exact.
        # Degenerate (near-1px-wide/tall) inputs are also routed here: scipy's
        # affine_transform mishandles boundary fill for combined rotate+flip
        # matrices at that extreme (verified empirically), while the two-step
        # rotate-then-flip path scipy normally uses does not hit that quirk.
        return _apply_spatial_transform(image, settings, order=1, mode=fill_mode, cval=0.0)

    matrix, offset, out_shape = _combined_export_transform(image.shape[:2], settings)
    if out_shape[0] <= 0 or out_shape[1] <= 0:
        return np.zeros(out_shape, dtype=image.dtype)

    # OpenCV's warpAffine is ~15-30x faster than scipy's generic interpolation
    # loop (SIMD C++ vs a generic spline routine), but it disagrees with scipy
    # on which pixels count as "outside the source image" near a rotated
    # boundary. With edge-stretch fill ("nearest") that boundary ambiguity is
    # invisible (<1 intensity count either way). With constant/cval fill
    # (rotation_fill_dark=True) it is not: it flips ~2.6% of pixels between a
    # real value and the fill value (verified empirically) — unacceptable for
    # a feature whose whole purpose is marking "not real data" precisely. So
    # OpenCV is only used for the nearest-mode case; constant-fill keeps using
    # the scipy path above, which is exact.
    if fill_mode == "nearest":
        cv2_result = _cv2_affine(image, matrix, offset, out_shape)
        if cv2_result is not None:
            return cv2_result

    return ndimage.affine_transform(
        image,
        matrix,
        offset,
        output_shape=out_shape,
        order=1,
        mode=fill_mode,
        cval=0.0,
        prefilter=False,
    )


_CV2_SUPPORTED_DTYPES = (np.uint8, np.uint16, np.int16, np.float32, np.float64)


def _cv2_affine(
    image: np.ndarray,
    matrix: np.ndarray,
    offset: np.ndarray,
    out_shape: tuple[int, int],
) -> np.ndarray | None:
    """Apply the same output[y,x] = image[matrix @ [y,x] + offset] mapping as
    ndimage.affine_transform, via cv2.warpAffine, for the nearest/edge-stretch
    fill case only (see caller). Returns None if cv2 is unavailable so the
    caller can fall back to the scipy path.
    """
    try:
        import cv2
    except ImportError:
        return None

    src = image if image.dtype in _CV2_SUPPORTED_DTYPES else image.astype(np.float32, copy=False)
    src = np.ascontiguousarray(src)

    out_h, out_w = out_shape
    # cv2 uses (x, y) axis order and a src<-dst matrix when WARP_INVERSE_MAP is
    # set — a straight axis swap of our (row, col) convention matrix/offset.
    cv2_matrix = np.array(
        [[matrix[1, 1], matrix[1, 0], offset[1]],
         [matrix[0, 1], matrix[0, 0], offset[0]]],
        dtype=np.float64,
    )
    result = cv2.warpAffine(
        src,
        cv2_matrix,
        (out_w, out_h),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return result if result.dtype == image.dtype else result.astype(image.dtype, copy=False)


def _apply_spatial_transform(
    image: np.ndarray,
    settings: PreprocessingSettings,
    *,
    order: int,
    mode: str,
    cval: float,
    skip_crop: bool = False,
) -> np.ndarray:
    processed = image

    if not bool(getattr(settings, "image_tools_enabled", True)):
        return processed

    angle = float(settings.rotation_angle_deg)
    if abs(angle) > 1e-9:
        processed = ndimage.rotate(
            processed,
            angle=angle,
            reshape=True,
            order=order,
            mode=mode,
            cval=cval,
            prefilter=order > 1,
        )

    if settings.flip_horizontal:
        processed = np.fliplr(processed)

    if settings.flip_vertical:
        processed = np.flipud(processed)

    if skip_crop:
        return processed

    crop = settings.crop
    if crop.enabled and crop.width > 0 and crop.height > 0:
        max_y, max_x = processed.shape[:2]
        x0 = max(0, min(int(crop.x), max_x - 1))
        y0 = max(0, min(int(crop.y), max_y - 1))
        x1 = max(x0 + 1, min(x0 + int(crop.width), max_x))
        y1 = max(y0 + 1, min(y0 + int(crop.height), max_y))
        processed = processed[y0:y1, x0:x1]

    return processed


def flatten_background(
    image: np.ndarray,
    *,
    sigma_px: float,
    binning: int = 1,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    rotation_fill_mask: np.ndarray | None = None,
    region: tuple[int, int, int, int] | None = None,
    exclusion_dilation_px: int = 0,
) -> np.ndarray:
    """Background-flatten `image`. The background estimate is always computed
    from the *whole* image (same cost/accuracy for TIFF and zarr — nothing
    about the estimate itself is scoped). When `region` (x0, y0, x1, y1) is
    given, only that region of the flattened result is returned — for a
    ROI-based calculation, nothing downstream ever reads the rest of the
    flattened image, so materializing it is pure waste, especially the
    upsample-back-to-full-resolution step inside estimate_background_profile
    when binning is enabled. `image` itself must still be the full image
    (needed for the blur and the exclusion mask/baseline), only the *output*
    is scoped.

    `exclusion_dilation_px` grows the combined ROI/mask exclusion zone by that
    many pixels (in the direction of more exclusion) before it's used to
    weight the background estimate. Exists because a mask or ROI boundary
    doesn't always land exactly on the true edge of the region it's meant to
    exclude (e.g. a hand-painted mask or a rotation-fill edge can have a
    stray transition pixel or two just outside it) — dilating gives a margin
    against that without requiring pixel-perfect masks. Purely a background
    estimation concern: it never touches the returned image's pixel values or
    any other computation.
    """
    image_f32 = image.astype(np.float32, copy=False)
    binning_factor = max(int(binning), 1)
    dilation_px = max(int(exclusion_dilation_px), 0)

    if region is None or binning_factor <= 1:
        # No separate binned intermediate to exploit when binning is off, and
        # the baseline needs a whole-image statistic regardless of region —
        # compute the background once, exactly as before, and slice locally
        # if a region was requested. Zero behavior/perf change from before
        # this function accepted `region` at all.
        background_full = estimate_background_profile(
            image_f32,
            sigma_px=sigma_px,
            binning=binning,
            rois=rois,
            mask_settings=mask_settings,
            external_mask=external_mask,
            rotation_fill_mask=rotation_fill_mask,
            exclusion_dilation_px=dilation_px,
        )
        valid_mask = ~_combined_exclusion_mask(
            image_f32, rois=rois, mask_settings=mask_settings, external_mask=external_mask,
            rotation_fill_mask=rotation_fill_mask, dilation_px=dilation_px,
        )
        baseline = float(np.median(background_full[valid_mask])) if np.any(valid_mask) else float(np.median(background_full))
        if region is None:
            flattened = image_f32 - background_full + baseline
        else:
            x0, y0, x1, y1 = region
            flattened = image_f32[y0:y1, x0:x1] - background_full[y0:y1, x0:x1] + baseline
        return np.clip(flattened, 0.0, 65535.0)

    # binning_factor > 1 and a region was requested: skip the expensive
    # full-resolution upsample entirely, only computing the small region's
    # background values (see estimate_background_profile / _background_baseline).
    background_region = estimate_background_profile(
        image_f32,
        sigma_px=sigma_px,
        binning=binning,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        rotation_fill_mask=rotation_fill_mask,
        region=region,
        exclusion_dilation_px=dilation_px,
    )
    baseline = _background_baseline(
        image_f32,
        sigma_px=sigma_px,
        binning=binning,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        rotation_fill_mask=rotation_fill_mask,
        exclusion_dilation_px=dilation_px,
    )
    x0, y0, x1, y1 = region
    flattened = image_f32[y0:y1, x0:x1] - background_region + baseline
    return np.clip(flattened, 0.0, 65535.0)


def estimate_background_profile(
    image: np.ndarray,
    *,
    sigma_px: float,
    binning: int = 1,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    rotation_fill_mask: np.ndarray | None = None,
    region: tuple[int, int, int, int] | None = None,
    exclusion_dilation_px: int = 0,
) -> np.ndarray:
    """Estimate the smooth spatial background of `image`. When `region` is
    given, only that (x0, y0, x1, y1) region of the estimate is returned —
    the expensive part this avoids is the upsample-back-to-full-resolution
    step (ndimage.zoom over the whole image) when binning is enabled; the
    Gaussian blur that produces the underlying (binned) estimate still runs
    over the whole image either way, same as the non-regional path.
    """
    image_f32 = image.astype(np.float32, copy=False)
    sigma = max(float(sigma_px), 1.0)
    exclusion_mask = _combined_exclusion_mask(
        image_f32,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        rotation_fill_mask=rotation_fill_mask,
        dilation_px=exclusion_dilation_px,
    )
    valid_mask = ~exclusion_mask
    weights = valid_mask.astype(np.float32, copy=False)
    binning_factor = max(int(binning), 1)
    if binning_factor > 1:
        binned_weighted = _bin_array_mean(image_f32 * weights, binning_factor)
        binned_weights = _bin_array_mean(weights, binning_factor)
        binned_sigma = max(sigma / float(binning_factor), 1.0)
        numerator_small = ndimage.gaussian_filter(binned_weighted, sigma=binned_sigma, mode="nearest")
        denominator_small = ndimage.gaussian_filter(binned_weights, sigma=binned_sigma, mode="nearest")
        fallback = float(np.median(image_f32[valid_mask])) if np.any(valid_mask) else float(np.median(image_f32))
        background_small = np.full_like(numerator_small, fallback)
        np.divide(numerator_small, denominator_small, out=background_small, where=denominator_small > 1e-6)
        if region is not None:
            return _resize_region_to_shape(background_small, image_f32.shape[:2], region)
        background = _resize_to_shape(background_small, image_f32.shape[:2])
        return background.astype(np.float32, copy=False)

    numerator = ndimage.gaussian_filter(image_f32 * weights, sigma=sigma, mode="nearest")
    denominator = ndimage.gaussian_filter(weights, sigma=sigma, mode="nearest")

    fallback = float(np.median(image_f32[valid_mask])) if np.any(valid_mask) else float(np.median(image_f32))
    background = np.full_like(image_f32, fallback)
    np.divide(numerator, denominator, out=background, where=denominator > 1e-6)
    if region is not None:
        x0, y0, x1, y1 = region
        return background[y0:y1, x0:x1]
    return background


def _background_baseline(
    image_f32: np.ndarray,
    *,
    sigma_px: float,
    binning: int = 1,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    rotation_fill_mask: np.ndarray | None = None,
    exclusion_dilation_px: int = 0,
) -> float:
    """The scalar re-centering value flatten_background adds back after
    subtracting the spatially-varying background estimate. This is
    inherently a whole-image statistic (a representative "typical" background
    level), so unlike the estimate's spatial shape it can't be scoped to a
    ROI region. When binning is enabled, it's computed from the already-cheap
    binned arrays rather than re-deriving it from a full-resolution estimate
    that region callers never otherwise materialize — background is itself
    the output of a large-sigma blur, so it has no meaningful variation at
    scales finer than one binned cell, making this equivalent in practice to
    the full-resolution median, not merely a rough approximation of it.
    """
    binning_factor = max(int(binning), 1)
    exclusion_mask = _combined_exclusion_mask(
        image_f32,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
        rotation_fill_mask=rotation_fill_mask,
        dilation_px=exclusion_dilation_px,
    )
    valid_mask = ~exclusion_mask
    if binning_factor <= 1:
        background = estimate_background_profile(
            image_f32,
            sigma_px=sigma_px,
            binning=binning,
            rois=rois,
            mask_settings=mask_settings,
            external_mask=external_mask,
            rotation_fill_mask=rotation_fill_mask,
            exclusion_dilation_px=exclusion_dilation_px,
        )
        return float(np.median(background[valid_mask])) if np.any(valid_mask) else float(np.median(background))

    weights = valid_mask.astype(np.float32, copy=False)
    sigma = max(float(sigma_px), 1.0)
    binned_weighted = _bin_array_mean(image_f32 * weights, binning_factor)
    binned_weights = _bin_array_mean(weights, binning_factor)
    binned_sigma = max(sigma / float(binning_factor), 1.0)
    numerator_small = ndimage.gaussian_filter(binned_weighted, sigma=binned_sigma, mode="nearest")
    denominator_small = ndimage.gaussian_filter(binned_weights, sigma=binned_sigma, mode="nearest")
    fallback = float(np.median(image_f32[valid_mask])) if np.any(valid_mask) else float(np.median(image_f32))
    background_small = np.full_like(numerator_small, fallback)
    np.divide(numerator_small, denominator_small, out=background_small, where=denominator_small > 1e-6)
    binned_valid_mask = binned_weights > 0.5
    if np.any(binned_valid_mask):
        return float(np.median(background_small[binned_valid_mask]))
    return float(np.median(background_small))


def _bin_array_mean(array: np.ndarray, factor: int) -> np.ndarray:
    if factor <= 1:
        return array.astype(np.float32, copy=False)
    height, width = array.shape[:2]
    padded_height = ((height + factor - 1) // factor) * factor
    padded_width = ((width + factor - 1) // factor) * factor
    if padded_height != height or padded_width != width:
        pad_spec = ((0, padded_height - height), (0, padded_width - width))
        padded = np.pad(array, pad_spec, mode="edge")
    else:
        padded = array
    reshaped = padded.reshape(padded_height // factor, factor, padded_width // factor, factor)
    return reshaped.mean(axis=(1, 3), dtype=np.float32)


def _resize_to_shape(array: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if array.shape[:2] == shape:
        return array.astype(np.float32, copy=False)
    zoom_factors = (shape[0] / float(array.shape[0]), shape[1] / float(array.shape[1]))
    resized = ndimage.zoom(array, zoom_factors, order=1, mode="nearest", prefilter=False)
    if resized.shape[:2] == shape:
        return resized.astype(np.float32, copy=False)
    corrected = np.empty(shape, dtype=np.float32)
    copy_height = min(shape[0], resized.shape[0])
    copy_width = min(shape[1], resized.shape[1])
    corrected[:copy_height, :copy_width] = resized[:copy_height, :copy_width]
    if copy_height < shape[0]:
        corrected[copy_height:, :copy_width] = resized[copy_height - 1 : copy_height, :copy_width]
    if copy_width < shape[1]:
        corrected[:copy_height, copy_width:] = corrected[:copy_height, copy_width - 1 : copy_width]
    if copy_height < shape[0] and copy_width < shape[1]:
        corrected[copy_height:, copy_width:] = corrected[copy_height - 1 : copy_height, copy_width - 1 : copy_width]
    return corrected


def _resize_region_to_shape(
    array: np.ndarray,
    shape: tuple[int, int],
    region: tuple[int, int, int, int],
) -> np.ndarray:
    """Same result as _resize_to_shape(array, shape)[y0:y1, x0:x1], without
    materializing the full upsampled array — for background flattening on a
    zarr dataset, only a small ROI-sized region of the upsampled background
    is ever actually used, so upsampling the whole image (an operation whose
    cost scales with the full image, same as scipy's rotate/warp cost we
    already optimized elsewhere) is pure waste.

    `region` is (x0, y0, x1, y1) in the target `shape`'s coordinates.

    Replicates ndimage.zoom's exact grid_mode=False coordinate formula
    (`source = out_idx * (in_dim - 1) / (out_dim - 1)`, read from scipy's own
    zoom() source — not the naive reciprocal-of-zoom-factor guess) via
    map_coordinates restricted to the region, plus _resize_to_shape's edge
    replication for the rare case where ndimage.zoom's actual output shape
    (rounded from the requested zoom factor) doesn't exactly match `shape`.
    """
    x0, y0, x1, y1 = region
    if array.shape[:2] == shape:
        return array[y0:y1, x0:x1].astype(np.float32, copy=False)

    zoom_factors = (shape[0] / float(array.shape[0]), shape[1] / float(array.shape[1]))
    # Same rounding scipy's zoom() uses internally to size its output —
    # needed to know whether _resize_to_shape's edge-replication correction
    # applies (rare, only on floating-point rounding mismatches).
    resized_shape = (
        int(round(array.shape[0] * zoom_factors[0])),
        int(round(array.shape[1] * zoom_factors[1])),
    )

    def _sample(oy: np.ndarray, ox: np.ndarray) -> np.ndarray:
        # ndimage.zoom's exact grid_mode=False mapping: endpoints aligned,
        # linear in between — not oy/zoom_factor.
        src_y = oy * (array.shape[0] - 1) / max(resized_shape[0] - 1, 1)
        src_x = ox * (array.shape[1] - 1) / max(resized_shape[1] - 1, 1)
        return ndimage.map_coordinates(
            array, [src_y, src_x], order=1, mode="nearest", prefilter=False,
        )

    yy, xx = np.meshgrid(np.arange(y0, y1), np.arange(x0, x1), indexing="ij")

    if resized_shape[0] >= shape[0] and resized_shape[1] >= shape[1]:
        # No edge-replication needed anywhere in `shape` — the common case.
        return _sample(yy.astype(np.float64), xx.astype(np.float64)).astype(np.float32, copy=False)

    # Rare correction path: for rows/cols beyond resized_shape, _resize_to_shape
    # replicates the last valid row/col instead of extrapolating the zoom.
    clamped_y = np.minimum(yy, resized_shape[0] - 1).astype(np.float64)
    clamped_x = np.minimum(xx, resized_shape[1] - 1).astype(np.float64)
    return _sample(clamped_y, clamped_x).astype(np.float32, copy=False)


def _roi_exclusion_mask(
    image_shape: tuple[int, int],
    rois: list[AreaRoi] | None = None,
) -> np.ndarray:
    exclusion_mask = np.zeros(image_shape, dtype=bool)
    if not rois:
        return exclusion_mask

    for roi in rois:
        exclusion_radius = max(float(roi.sample_radius_px) * 1.35, float(roi.sample_radius_px) + 2.0)
        radius_ceil = int(np.ceil(exclusion_radius))
        x0 = max(int(np.floor(roi.center_x)) - radius_ceil, 0)
        x1 = min(int(np.floor(roi.center_x)) + radius_ceil + 1, image_shape[1])
        y0 = max(int(np.floor(roi.center_y)) - radius_ceil, 0)
        y1 = min(int(np.floor(roi.center_y)) + radius_ceil + 1, image_shape[0])
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.ogrid[y0:y1, x0:x1]
        distance_sq = (xx - float(roi.center_x)) ** 2 + (yy - float(roi.center_y)) ** 2
        exclusion_mask[y0:y1, x0:x1] |= distance_sq <= exclusion_radius**2
    return exclusion_mask


def _combined_exclusion_mask(
    image: np.ndarray,
    *,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    rotation_fill_mask: np.ndarray | None = None,
    dilation_px: int = 0,
) -> np.ndarray:
    exclusion_mask = _roi_exclusion_mask(image.shape[:2], rois)
    if mask_settings is not None:
        exclusion_mask |= ignored_pixel_mask(
            image.astype(np.float32, copy=False),
            mask_settings,
            external_mask=external_mask,
        )
    # Rotation-fill pixels are folded in unconditionally, independent of
    # mask_settings/external_mask above - see the comment on the
    # flatten_background call site in apply_preprocessing for why.
    if rotation_fill_mask is not None and rotation_fill_mask.shape == exclusion_mask.shape:
        exclusion_mask = exclusion_mask | rotation_fill_mask
    dilation = max(int(dilation_px), 0)
    if dilation > 0 and np.any(exclusion_mask):
        exclusion_mask = ndimage.binary_dilation(exclusion_mask, iterations=dilation)
    return exclusion_mask


def create_histogram_mask(image: np.ndarray, settings: MaskSettings) -> np.ndarray:
    """Create mask based on intensity histogram ranges."""
    if settings.histogram_min_value is None and settings.histogram_max_value is None:
        return np.zeros(image.shape[:2], dtype=bool)

    mask = np.ones(image.shape[:2], dtype=bool)
    if settings.histogram_min_value is not None:
        mask &= (image >= settings.histogram_min_value)
    if settings.histogram_max_value is not None:
        mask &= (image <= settings.histogram_max_value)
    return ~mask  # Invert so True means masked (excluded)


def create_figure_mask(image: np.ndarray, settings: MaskSettings, mode: str) -> np.ndarray:
    """Create mask based on figure-based algorithms (relative, local contrast)."""
    if mode == "relative":
        sigma = max(float(settings.relative_profile_sigma_px), 1.0)
        profile = ndimage.gaussian_filter(image.astype(np.float32, copy=False), sigma=sigma, mode="nearest")
        threshold_fraction = max(float(settings.relative_threshold_fraction), 0.0)
        safe_profile = np.maximum(profile, 1e-6)
        relative_delta = (image.astype(np.float32, copy=False) - safe_profile) / safe_profile
        return np.abs(relative_delta) >= threshold_fraction
    elif mode == "local_contrast":
        sigma = max(float(settings.local_contrast_sigma_px), 1.0)
        image_f32 = image.astype(np.float32, copy=False)
        local_mean = ndimage.gaussian_filter(image_f32, sigma=sigma, mode="nearest")
        local_sq_mean = ndimage.gaussian_filter(image_f32 * image_f32, sigma=sigma, mode="nearest")
        local_var = np.maximum(local_sq_mean - local_mean * local_mean, 0.0)
        local_std = np.sqrt(local_var)
        z_threshold = max(float(settings.local_contrast_z_threshold), 0.1)
        z_score = np.abs(image_f32 - local_mean) / np.maximum(local_std, 1e-6)
        return z_score >= z_threshold
    else:
        return np.zeros(image.shape[:2], dtype=bool)


def apply_morphology_to_mask(mask: np.ndarray, operation: str, radius_px: int) -> np.ndarray:
    """Apply morphological operations to mask."""
    from scipy import ndimage
    radius = max(int(radius_px), 1)
    struct_elem = ndimage.generate_binary_structure(2, 1)

    if operation == "erode":
        return ndimage.binary_erosion(mask, structure=struct_elem, iterations=radius)
    elif operation == "dilate":
        return ndimage.binary_dilation(mask, structure=struct_elem, iterations=radius)
    elif operation == "open":
        return ndimage.binary_opening(mask, structure=struct_elem, iterations=radius)
    elif operation == "close":
        return ndimage.binary_closing(mask, structure=struct_elem, iterations=radius)
    else:
        return mask
