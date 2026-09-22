"""Pure background-model estimation (touches real pixels; the "estimate" half
of the estimate/apply split discussed in sketch §7 "Background") - ports the
background functions out of ``processing/preprocess.py`` (sketch §10's
``preprocess.py`` note) verbatim.

**Estimate/apply split built 2026-09-21**: ``flatten_background`` below now
computes the background estimate (and, on the binned+region path, the
scalar baseline) itself, then hands both to ``apply.apply_background()`` for
the actual subtract-recenter-clip - it no longer inlines that arithmetic.
Behavior is unchanged (verified byte-for-byte identical against the
pre-split inline version); this only moves the "cheap formula" half into its
own reusable function, per ``apply.py``'s docstring for why it isn't a
cached "model" object.

Note the ``mask_settings`` parameter below is ``AreaRoiDetectionSettings``
(ROI's own ignored-pixel settings, via ``ignored_pixel_mask``) - not this
app's ``image_tools.mask.MaskSettings``. Confusing, but that's the existing
naming in the source this was ported from; preserved as-is rather than
renamed during the port to keep the diff verbatim.

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ...roi.detection import ignored_pixel_mask
from ...roi.model import AreaRoi, AreaRoiDetectionSettings
from .apply import apply_background


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
            return apply_background(image_f32, background_full, baseline)
        x0, y0, x1, y1 = region
        return apply_background(image_f32[y0:y1, x0:x1], background_full[y0:y1, x0:x1], baseline)

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
    return apply_background(image_f32[y0:y1, x0:x1], background_region, baseline)


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
