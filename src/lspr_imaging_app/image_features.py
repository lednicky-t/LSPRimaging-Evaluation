"""Image-feature helpers shared by Chromatic's automatic landmarks and the ROI array tools.

Pure computation (numpy / scipy / cv2): no Qt, no dataset, no files. Extracted
2026-10-08 from `image_tools/chromatic/auto_landmarks.py` (unchanged behaviour)
so that `roi/` can use the same contrast map without importing from another
module's package: `image_tools/chromatic` stays private except for its
`affine_for()` / `warp_mask()`.

Assumption (maintainer, 2026-10-04): features of interest are *darker* than the
background. `contrast_map` makes them positive; for bright features flip the sign
of the image before calling it.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage

__all__ = ["FeatureSearchError", "contrast_map", "estimate_feature_radius", "fill_invalid", "odd_size"]


class FeatureSearchError(Exception):
    """A failure the user should read (no valid pixels, no features found)."""


def odd_size(value: float) -> int:
    n = int(round(value))
    return n if n % 2 else n + 1


def fill_invalid(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(image with NaN filled by a smooth local average, valid mask).

    Rotation leaves NaN corners. Filling them with the surrounding level
    (normalised convolution) keeps a fake edge from appearing at the data
    boundary; the mask lets candidate selection stay away from that area."""
    image = np.asarray(image, dtype=np.float32)
    valid = np.isfinite(image)
    if valid.all():
        return image, valid
    if not valid.any():
        raise FeatureSearchError("The image has no valid pixels.")
    values = np.where(valid, image, 0.0).astype(np.float32)
    weight = valid.astype(np.float32)
    numerator = ndimage.gaussian_filter(values, 15.0, mode="nearest")
    denominator = ndimage.gaussian_filter(weight, 15.0, mode="nearest")
    fill = np.full(image.shape, float(np.median(image[valid])), dtype=np.float32)
    np.divide(numerator, denominator, out=fill, where=denominator > 1e-3)
    return np.where(valid, image, fill), valid


def contrast_map(image: np.ndarray, background_px: int = 101, smooth_sigma: float = 1.5) -> np.ndarray:
    """(background - image) / background: dark features become positive.

    Background = grey closing (removes dark features smaller than
    `background_px`, so keep it larger than the biggest feature), computed
    on a half-resolution copy because the background is smooth. Relative
    contrast makes every wavelength comparable although overall brightness
    and feature depth change with wavelength."""
    height, width = image.shape
    smooth = ndimage.gaussian_filter(image, smooth_sigma)
    small = cv2.resize(smooth, (width // 2, height // 2), interpolation=cv2.INTER_AREA)
    background = ndimage.grey_closing(small, size=(odd_size(background_px / 2),) * 2)
    background = ndimage.gaussian_filter(background, 3.0)
    background = cv2.resize(background, (width, height), interpolation=cv2.INTER_LINEAR)
    return ((background - smooth) / np.maximum(background, 1.0)).astype(np.float32)


def estimate_feature_radius(contrast: np.ndarray) -> float:
    """Typical feature radius (px) from a scale-space blob search: the
    scale-normalised Laplacian-of-Gaussian of a blob of radius r peaks at
    sigma = r / sqrt(2). Median over the strongest peaks, so a few odd
    objects do not matter. No shape assumption beyond "blob-sized"."""
    small = cv2.resize(contrast, (contrast.shape[1] // 2, contrast.shape[0] // 2), interpolation=cv2.INTER_AREA)
    sigmas = np.geomspace(1.0, 25.0, 14)
    stack = np.stack([-(s**2) * ndimage.gaussian_laplace(small, s) for s in sigmas])
    best, which = stack.max(axis=0), stack.argmax(axis=0)
    peak = (best == ndimage.maximum_filter(best, size=9)) & (best > 0.3 * best.max())
    ys, xs = np.nonzero(peak)
    top = np.argsort(best[ys, xs])[::-1][:30]
    if len(top) == 0:
        raise FeatureSearchError("Could not find any dark features to measure their size. Set the feature diameter manually.")
    sigma = float(np.median(sigmas[which[ys[top], xs[top]]]))
    return max(sigma * np.sqrt(2.0) * 2.0, 2.0)  # x2: half-resolution map
