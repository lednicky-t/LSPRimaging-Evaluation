"""Pure mask-creation math - ports the mask-only functions out of
``processing/preprocess.py`` (sketch §10's ``preprocess.py`` note), verbatim
apart from retyping ``settings: MaskSettings`` to this package's own
``MaskSettings`` (same fields, just relocated - see ``model.py``'s
docstring).

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .model import MaskSettings


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
