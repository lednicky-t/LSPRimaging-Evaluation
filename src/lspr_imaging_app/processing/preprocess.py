from __future__ import annotations

import numpy as np
from scipy import ndimage

from lspr_imaging_app.domain.models import DetectedSpot, PreprocessingSettings, SpotDetectionSettings, MaskSettings
from lspr_imaging_app.processing.spot_detection import ignored_pixel_mask


def apply_preprocessing(
    image: np.ndarray,
    settings: PreprocessingSettings,
    spots: list[DetectedSpot] | None = None,
    mask_settings: SpotDetectionSettings | None = None,
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
            spots=spots if settings.flatten_background_exclude_spots else None,
            mask_settings=mask_settings if settings.flatten_background_exclude_mask else None,
            external_mask=None,  # Already applied above
        )

    return processed


def apply_spatial_preprocessing(
    image: np.ndarray,
    settings: PreprocessingSettings,
) -> np.ndarray:
    return _apply_spatial_transform(image, settings, order=1, mode="nearest", cval=0.0)


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


def _apply_spatial_transform(
    image: np.ndarray,
    settings: PreprocessingSettings,
    *,
    order: int,
    mode: str,
    cval: float,
) -> np.ndarray:
    processed = image

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

    if not bool(getattr(settings, "image_tools_enabled", True)):
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
    spots: list[DetectedSpot] | None = None,
    mask_settings: SpotDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
) -> np.ndarray:
    image_f32 = image.astype(np.float32, copy=False)
    background = estimate_background_profile(
        image_f32,
        sigma_px=sigma_px,
        binning=binning,
        spots=spots,
        mask_settings=mask_settings,
        external_mask=external_mask,
    )
    valid_mask = ~_combined_exclusion_mask(
        image_f32,
        spots=spots,
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
    spots: list[DetectedSpot] | None = None,
    mask_settings: SpotDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
) -> np.ndarray:
    image_f32 = image.astype(np.float32, copy=False)
    sigma = max(float(sigma_px), 1.0)
    exclusion_mask = _combined_exclusion_mask(
        image_f32,
        spots=spots,
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


def _spot_exclusion_mask(
    image_shape: tuple[int, int],
    spots: list[DetectedSpot] | None = None,
) -> np.ndarray:
    exclusion_mask = np.zeros(image_shape, dtype=bool)
    if not spots:
        return exclusion_mask

    for spot in spots:
        exclusion_radius = max(float(spot.radius_px) * 1.35, float(spot.radius_px) + 2.0)
        radius_ceil = int(np.ceil(exclusion_radius))
        x0 = max(int(np.floor(spot.center_x)) - radius_ceil, 0)
        x1 = min(int(np.floor(spot.center_x)) + radius_ceil + 1, image_shape[1])
        y0 = max(int(np.floor(spot.center_y)) - radius_ceil, 0)
        y1 = min(int(np.floor(spot.center_y)) + radius_ceil + 1, image_shape[0])
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.ogrid[y0:y1, x0:x1]
        distance_sq = (xx - float(spot.center_x)) ** 2 + (yy - float(spot.center_y)) ** 2
        exclusion_mask[y0:y1, x0:x1] |= distance_sq <= exclusion_radius**2
    return exclusion_mask


def _combined_exclusion_mask(
    image: np.ndarray,
    *,
    spots: list[DetectedSpot] | None = None,
    mask_settings: SpotDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
) -> np.ndarray:
    exclusion_mask = _spot_exclusion_mask(image.shape[:2], spots)
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
