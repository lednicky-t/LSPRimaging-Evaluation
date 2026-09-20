"""Pure raster mask-editing math: threshold/contrast candidate generation,
morphology, brush painting, and candidate merging.

**Renamed from `creation.py` (2026-09-21)**, per a design conversation with
the maintainer: this isn't just "how MaskModule builds its own candidate
masks" - every function here is a plain array-in-array-out operation with
no dependency on `MaskModule`, `MaskSettings`, or anything Mask-specific
(the threshold/contrast functions below take scalar parameters directly,
not a `MaskSettings` object, specifically so a caller with no `MaskSettings`
instance - e.g. a future ROI mask-drawing command - can use them too). The
maintainer's own framing: these are "raster tools/editor" functions in the
sense Photoshop/Gwyddion use the term (threshold, morphology, freehand
paint), not owned by any one consumer.

**The intended shared consumers**: `MaskModule`'s whole-image raw-pixel-
space ignore mask, and (not built yet) `RoiToolbox`'s per-ROI arbitrary
mask geometry (`AreaRoi.sample_mask`/`reference_mask`, a `RoiMask` -
`roi/model.py`). Both are "a raster canvas authored once, edited with these
same tools" - they differ only in size (full image vs. a small cropped
bounding box) and in what happens *around* an edit (MaskModule's own
per-wavelength-diff bookkeeping when chromatic correction is on and the
displayed wavelength isn't the reference - a Mask-specific wrinkle these
functions deliberately know nothing about; see `brush_stamp_bounds`'s
docstring) and *after* one (both are meant to be forward-warped through
`ChromaticModule.warp_mask()` per wavelength at use time - already built
for the ignore mask, flagged as a real, scoped future task for `RoiMask` in
`roi/rasterize.py`'s own docstring).

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def create_histogram_mask(
    image: np.ndarray,
    *,
    min_value: float | None,
    max_value: float | None,
) -> np.ndarray:
    """Threshold `image` by an intensity range - pixels *outside*
    `[min_value, max_value]` are `True` (masked/excluded). Either bound may
    be `None` to leave that side unconstrained; both `None` means nothing
    is excluded.

    Doesn't correspond to the old app's actual "histogram highlight" tool
    (`gui/mask_controller.py`'s `current_histogram_highlight_mask_raw`,
    which selects by *displayed* value through processed<->raw coordinate
    maps - a different, not-yet-ported algorithm, see `mask/module.py`'s
    docstring) - this is a plain value-range threshold, useful in its own
    right regardless.
    """
    if min_value is None and max_value is None:
        return np.zeros(image.shape[:2], dtype=bool)
    mask = np.ones(image.shape[:2], dtype=bool)
    if min_value is not None:
        mask &= image >= min_value
    if max_value is not None:
        mask &= image <= max_value
    return ~mask  # Invert so True means masked (excluded)


def create_relative_contrast_mask(
    image: np.ndarray,
    *,
    sigma_px: float,
    threshold_fraction: float,
) -> np.ndarray:
    """Mask pixels whose intensity deviates from their own local
    (Gaussian-blurred) background by more than `threshold_fraction` of that
    background's value - catches bright/dark features regardless of the
    image's overall intensity level, unlike a fixed absolute threshold."""
    sigma = max(float(sigma_px), 1.0)
    image_f32 = image.astype(np.float32, copy=False)
    profile = ndimage.gaussian_filter(image_f32, sigma=sigma, mode="nearest")
    fraction = max(float(threshold_fraction), 0.0)
    safe_profile = np.maximum(profile, 1e-6)
    relative_delta = (image_f32 - safe_profile) / safe_profile
    return np.abs(relative_delta) >= fraction


def create_local_contrast_mask(
    image: np.ndarray,
    *,
    sigma_px: float,
    z_threshold: float,
) -> np.ndarray:
    """Mask pixels whose intensity is more than `z_threshold` local standard
    deviations away from their own local (Gaussian-blurred) mean - a local
    z-score threshold, more sensitive to sharp features in a noisy/textured
    background than `create_relative_contrast_mask`'s plain ratio."""
    sigma = max(float(sigma_px), 1.0)
    image_f32 = image.astype(np.float32, copy=False)
    local_mean = ndimage.gaussian_filter(image_f32, sigma=sigma, mode="nearest")
    local_sq_mean = ndimage.gaussian_filter(image_f32 * image_f32, sigma=sigma, mode="nearest")
    local_var = np.maximum(local_sq_mean - local_mean * local_mean, 0.0)
    local_std = np.sqrt(local_var)
    threshold = max(float(z_threshold), 0.1)
    z_score = np.abs(image_f32 - local_mean) / np.maximum(local_std, 1e-6)
    return z_score >= threshold


def apply_morphology_to_mask(mask: np.ndarray, operation: str, radius_px: int) -> np.ndarray:
    """Apply a morphological operation (`"erode"`/`"dilate"`/`"open"`/
    `"close"`) to `mask`. Unknown `operation` returns `mask` unchanged."""
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


def brush_stamp_bounds(
    canvas_shape: tuple[int, int],
    center_xy: tuple[float, float],
    radius_px: float,
) -> tuple[int, int, int, int, np.ndarray] | None:
    """The circular brush footprint for one stroke point, clamped to
    `canvas_shape`. Returns `(x0, x1, y0, y1, local_mask)` - `local_mask`
    is a boolean array shaped `(y1 - y0, x1 - x0)`, `True` where the brush
    covers that pixel - or `None` if the stroke has no overlap with the
    canvas at all (fully off-edge).

    Deliberately just the footprint, not a paint operation: this function
    doesn't know or care whether the caller then writes `value` directly
    into a canonical array (`apply_brush_stamp` below does exactly that),
    or does something else with the covered pixels - e.g. `MaskModule`'s
    real old-app behavior when chromatic correction is on and the
    displayed wavelength isn't the reference, where a stroke doesn't touch
    the canonical mask at all but instead accumulates into a sparse
    per-wavelength diff dict (`{(row, col): value}`, see
    `chromatic/warp.py`'s `apply_mask_wavelength_diff`) so it survives as a
    manual per-wavelength touch-up layered on top of the chromatic-warped
    canonical mask, rather than corrupting the canonical mask with a
    wavelength-specific edit. That branching is Mask's own concern to own
    (see the mask/ROI design conversation in the rewrite build log,
    2026-09-21 entries) - this function only ever answers "which pixels
    does this stroke cover," identically for any caller.
    """
    height, width = canvas_shape[:2]
    center_x, center_y = float(center_xy[0]), float(center_xy[1])
    radius = max(float(radius_px), 0.5)
    x0 = max(int(np.floor(center_x - radius)), 0)
    x1 = min(int(np.ceil(center_x + radius)) + 1, width)
    y0 = max(int(np.floor(center_y - radius)), 0)
    y1 = min(int(np.ceil(center_y + radius)) + 1, height)
    if x1 <= x0 or y1 <= y0:
        return None
    yy, xx = np.ogrid[y0:y1, x0:x1]
    local_mask = (xx - center_x) ** 2 + (yy - center_y) ** 2 <= radius * radius
    return x0, x1, y0, y1, local_mask


def apply_brush_stamp(
    canvas: np.ndarray,
    center_xy: tuple[float, float],
    radius_px: float,
    *,
    value: bool,
) -> np.ndarray:
    """Non-mutating: paint one circular brush stroke directly into a copy
    of `canvas` (`value=True` to add to the mask, `False` to erase from
    it). Built on `brush_stamp_bounds` - use that function directly instead
    when the caller needs to route the covered pixels somewhere other than
    a direct array write (see its docstring)."""
    result = canvas.copy()
    bounds = brush_stamp_bounds(canvas.shape, center_xy, radius_px)
    if bounds is None:
        return result
    x0, x1, y0, y1, local_mask = bounds
    region = result[y0:y1, x0:x1]
    region[local_mask] = bool(value)
    return result


def merge_mask_candidate(current: np.ndarray, candidate: np.ndarray, *, subtract: bool) -> np.ndarray:
    """Combine a freshly-computed candidate mask into an existing one -
    `subtract=False` ORs it in (add to the mask), `subtract=True` ANDs out
    the candidate's pixels (erase from the mask). Non-mutating."""
    if subtract:
        return np.logical_and(current, ~candidate)
    return np.logical_or(current, candidate)
