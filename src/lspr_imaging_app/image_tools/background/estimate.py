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

No Qt import allowed in this file (CLAUDE.md testing rule).
"""

from __future__ import annotations

import cv2
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
    region: tuple[int, int, int, int] | None = None,
    exclusion_dilation_px: int = 0,
) -> np.ndarray:
    """Background-flatten `image` (divide by the background estimate, see
    `apply.apply_background`).
    The background estimate is always computed from the *whole* image (same
    cost/accuracy for TIFF and zarr - nothing about the estimate itself is
    scoped). When `region` (x0, y0, x1, y1) is given, only that region of the
    flattened result is returned.

    `exclusion_dilation_px` grows the combined ROI/mask exclusion zone by that
    many pixels (in the direction of more exclusion) before it's used to
    weight the background estimate. Exists because a mask or ROI boundary
    doesn't always land exactly on the true edge of the region it's meant to
    exclude (e.g. a hand-painted mask or a rotation-fill edge can have a
    stray transition pixel or two just outside it) - dilating gives a margin
    against that without requiring pixel-perfect masks. Purely a background
    estimation concern: it never touches the returned image's pixel values or
    any other computation.
    """
    image_f32 = image.astype(np.float32, copy=False)
    valid_mask = ~_combined_exclusion_mask(
        image_f32, rois=rois, mask_settings=mask_settings, external_mask=external_mask,
        dilation_px=max(int(exclusion_dilation_px), 0),
    )
    background, baseline = _background_and_baseline(image_f32, valid_mask, sigma_px, binning)
    if region is not None:
        x0, y0, x1, y1 = region
        return apply_background(image_f32[y0:y1, x0:x1], background[y0:y1, x0:x1], baseline)
    return apply_background(image_f32, background, baseline)


def estimate_background_profile(
    image: np.ndarray,
    *,
    sigma_px: float,
    binning: int = 1,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    region: tuple[int, int, int, int] | None = None,
    exclusion_dilation_px: int = 0,
) -> np.ndarray:
    """Estimate the smooth spatial background of `image`: at every pixel, the
    average of the nearby *valid* pixels (a Gaussian-weighted mean that skips
    the excluded ones), i.e. "what the surrounding substrate reads here" in
    the image's own intensity units. When `region` is given, only that
    (x0, y0, x1, y1) region is returned.
    """
    image_f32 = image.astype(np.float32, copy=False)
    valid_mask = ~_combined_exclusion_mask(
        image_f32, rois=rois, mask_settings=mask_settings, external_mask=external_mask,
        dilation_px=max(int(exclusion_dilation_px), 0),
    )
    background = _profile_from_mask(image_f32, valid_mask, sigma_px, binning)
    if region is not None:
        x0, y0, x1, y1 = region
        return background[y0:y1, x0:x1]
    return background


def _profile_from_mask(image_f32: np.ndarray, valid_mask: np.ndarray, sigma_px: float, binning: int) -> np.ndarray:
    return _background_and_baseline(image_f32, valid_mask, sigma_px, binning, want_baseline=False)[0]


def _background_and_baseline(
    image_f32: np.ndarray,
    valid_mask: np.ndarray,
    sigma_px: float,
    binning: int,
    want_baseline: bool = True,
) -> tuple[np.ndarray, float]:
    """The full-resolution background estimate and the scalar baseline (the
    median background level over the valid pixels - the "typical white" that
    `apply_background` re-centres on).

    **cv2 fast path (2026-10-06) for `binning > 1`** - the same maths as the
    unbinned path below (cv2 full-resolution blur too), done on a reduced grid: area-average the
    image and the validity weights by `binning`, blur both with a Gaussian
    of `sigma / binning`, divide (weighted mean), upsample linearly. Measured
    on real 1269x742 frames at ~10x the speed of the previous scipy
    bin/blur/`ndimage.zoom` version with no loss on any quality metric (see
    `docs/background_method_study_2026-10-06.md`). The only differences are
    sub-pixel: cv2's grid is centre-aligned (the old `zoom` aligned corner
    pixels, shifting the estimate by up to half a bin) and cv2's kernel is
    truncated at ~3 sigma, not 4.

    The baseline comes from the already-computed small grid (the estimate has
    no structure finer than one bin, so this equals the full-resolution
    median to within noise) - no full-resolution median needed.
    """
    sigma = max(float(sigma_px), 1.0)
    factor = max(int(binning), 1)
    if factor > 1:
        height, width = image_f32.shape[:2]
        small_shape = (-(-width // factor), -(-height // factor))  # cv2 wants (w, h); ceil division
        weights = valid_mask.astype(np.float32, copy=False)
        binned_weighted = cv2.resize(np.where(valid_mask, image_f32, 0.0).astype(np.float32, copy=False), small_shape, interpolation=cv2.INTER_AREA)
        binned_weights = cv2.resize(weights, small_shape, interpolation=cv2.INTER_AREA)
        binned_sigma = max(sigma / float(factor), 1.0)
        numerator = cv2.GaussianBlur(binned_weighted, (0, 0), binned_sigma, borderType=cv2.BORDER_REPLICATE)
        denominator = cv2.GaussianBlur(binned_weights, (0, 0), binned_sigma, borderType=cv2.BORDER_REPLICATE)
        has_support = denominator > 1e-6
        background_small = np.empty_like(numerator)
        np.divide(numerator, denominator, out=background_small, where=has_support)
        if not has_support.all():  # rare: only then is the (8 ms) median fallback needed
            background_small[~has_support] = _fallback_level(image_f32, valid_mask)
        background = cv2.resize(background_small, (width, height), interpolation=cv2.INTER_LINEAR)
        if not want_baseline:
            return background, float("nan")
        binned_valid = binned_weights > 0.5
        baseline = float(np.median(background_small[binned_valid if np.any(binned_valid) else slice(None)]))
        return background, baseline

    weights = valid_mask.astype(np.float32, copy=False)
    numerator = cv2.GaussianBlur(np.where(valid_mask, image_f32, 0.0).astype(np.float32, copy=False), (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
    denominator = cv2.GaussianBlur(weights, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
    has_support = denominator > 1e-6
    background = np.empty_like(image_f32)
    np.divide(numerator, denominator, out=background, where=has_support)
    if not has_support.all():
        background[~has_support] = _fallback_level(image_f32, valid_mask)
    if not want_baseline:
        return background, float("nan")
    baseline = float(np.median(background[valid_mask])) if np.any(valid_mask) else float(np.median(background))
    return background, baseline


def _fallback_level(image_f32: np.ndarray, valid_mask: np.ndarray) -> float:
    """Scalar stand-in used where a pixel's neighbourhood has no usable
    weight: the median of the valid pixels, else of any finite pixel, else NaN
    (nothing measured at all - the result is then NaN everywhere, never 0)."""
    if np.any(valid_mask):
        return float(np.median(image_f32[valid_mask]))
    finite = image_f32[np.isfinite(image_f32)]
    return float(np.median(finite)) if finite.size else float("nan")


def _roi_exclusion_mask(
    image_shape: tuple[int, int],
    rois: list[AreaRoi] | None = None,
) -> np.ndarray:
    exclusion_mask = np.zeros(image_shape, dtype=bool)
    if not rois:
        return exclusion_mask

    for roi in rois:
        sample_radius = float(roi.sample_diameter_px) / 2.0
        exclusion_radius = max(sample_radius * 1.35, sample_radius + 2.0)
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
    dilation_px: int = 0,
) -> np.ndarray:
    exclusion_mask = _roi_exclusion_mask(image.shape[:2], rois)
    if mask_settings is not None:
        exclusion_mask |= ignored_pixel_mask(
            image.astype(np.float32, copy=False),
            mask_settings,
            external_mask=external_mask,
        )
    # Pixels with no measurement (NaN - e.g. created by rotation) are always
    # excluded, independent of the toggles above: they carry no value, so
    # they can neither pull on the local average nor be averaged into it.
    exclusion_mask = exclusion_mask | ~np.isfinite(image)
    dilation = max(int(dilation_px), 0)
    if dilation > 0 and np.any(exclusion_mask):
        exclusion_mask = ndimage.binary_dilation(exclusion_mask, iterations=dilation)
    return exclusion_mask
