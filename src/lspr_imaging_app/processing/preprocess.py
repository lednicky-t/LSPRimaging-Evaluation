from __future__ import annotations

import numpy as np
from scipy import ndimage

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiDetectionSettings, MaskSettings, PreprocessingSettings
from lspr_imaging_app.processing.spot_detection import ignored_pixel_mask


def apply_preprocessing(
    image: np.ndarray,
    settings: PreprocessingSettings,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    external_mask_processed: bool = False,
    mask_state: MaskSettings | None = None,
) -> np.ndarray:
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
    
    # Apply legacy external mask if provided
    if external_mask is not None and not external_mask_processed:
        # external_mask is already in raw image coordinates
        masked_image = np.where(external_mask.astype(bool), 0, masked_image)
    
    processed = apply_spatial_preprocessing(masked_image, settings)
    
    if settings.flatten_background_enabled:
        processed = flatten_background(
            processed,
            sigma_px=float(settings.flatten_background_sigma_px),
            binning=max(int(getattr(settings, "flatten_background_binning", 2)), 1),
            rois=rois if settings.flatten_background_exclude_area_rois else None,
            mask_settings=mask_settings if settings.flatten_background_exclude_mask else None,
            external_mask=None,  # Already applied above
        )

    return processed


def apply_spatial_preprocessing(
    image: np.ndarray,
    settings: PreprocessingSettings,
) -> np.ndarray:
    # Pixels added by rotation (the corners outside the original frame) have no
    # real measurement behind them. "nearest" stretches the nearest edge pixel
    # into that area (no scientific meaning, but visually seamless); the
    # rotation_fill_dark toggle instead fills it with 0 so it's clearly marked
    # as "not data".
    fill_mode = "constant" if bool(settings.rotation_fill_dark) else "nearest"
    return _apply_spatial_transform(image, settings, order=1, mode=fill_mode, cval=0.0)


def apply_spatial_mask(
    mask: np.ndarray | None,
    settings: PreprocessingSettings,
) -> np.ndarray | None:
    if mask is None:
        return None
    transformed = _apply_spatial_transform(mask.astype(np.float32, copy=False), settings, order=0, mode="constant", cval=0.0)
    return transformed >= 0.5


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


def apply_spatial_preprocessing_export(
    image: np.ndarray,
    settings: PreprocessingSettings,
) -> np.ndarray:
    """Fast equivalent of apply_spatial_preprocessing for the OME-Zarr export
    hot path: resamples directly at the final rotate/flip/crop size instead
    of materializing the full ndimage.rotate(reshape=True) canvas and slicing
    it down afterward. When the configured crop is much smaller than the full
    frame, this avoids interpolating pixels that would just be discarded.
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
) -> np.ndarray:
    image_f32 = image.astype(np.float32, copy=False)
    background = estimate_background_profile(
        image_f32,
        sigma_px=sigma_px,
        binning=binning,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
    )
    valid_mask = ~_combined_exclusion_mask(
        image_f32,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
    )

    baseline = float(np.median(background[valid_mask])) if np.any(valid_mask) else float(np.median(background))
    flattened = image_f32 - background + baseline
    return np.clip(flattened, 0.0, 65535.0)


def estimate_background_profile(
    image: np.ndarray,
    *,
    sigma_px: float,
    binning: int = 1,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
) -> np.ndarray:
    image_f32 = image.astype(np.float32, copy=False)
    sigma = max(float(sigma_px), 1.0)
    exclusion_mask = _combined_exclusion_mask(
        image_f32,
        rois=rois,
        mask_settings=mask_settings,
        external_mask=external_mask,
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
        background = _resize_to_shape(background_small, image_f32.shape[:2])
        return background.astype(np.float32, copy=False)

    numerator = ndimage.gaussian_filter(image_f32 * weights, sigma=sigma, mode="nearest")
    denominator = ndimage.gaussian_filter(weights, sigma=sigma, mode="nearest")

    fallback = float(np.median(image_f32[valid_mask])) if np.any(valid_mask) else float(np.median(image_f32))
    background = np.full_like(image_f32, fallback)
    np.divide(numerator, denominator, out=background, where=denominator > 1e-6)
    return background


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
) -> np.ndarray:
    exclusion_mask = _roi_exclusion_mask(image.shape[:2], rois)
    if mask_settings is not None:
        exclusion_mask |= ignored_pixel_mask(
            image.astype(np.float32, copy=False),
            mask_settings,
            external_mask=external_mask,
        )
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
