"""Pure spatial-transform math (crop/rotate/flip) - ports the geometry-only
functions out of ``processing/preprocess.py`` (sketch §10's ``preprocess.py``
note), retyped to ``settings: GeometrySettings`` - every function here only
ever reads ``image_tools_enabled``/``rotation_angle_deg``/``flip_horizontal``/
``flip_vertical``/``crop`` (see ``model.py``'s docstring).

**Rotation-created pixels are NaN** (2026-10-03, TASK_rotation_fill_handling):
they have no measurement behind them, so they have no value. There is no
dark (0) fill and no edge-stretch. Validity of any pixel is
``np.isfinite(image)``; no geometric "fill mask" exists any more.

No Qt import allowed in this file (CLAUDE.md testing rule).
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
from scipy import ndimage

from .model import CropDefinition, GeometrySettings


def apply_spatial_preprocessing(
    image: np.ndarray,
    settings: GeometrySettings,
    *,
    skip_crop: bool = False,
    fast: bool = False,
) -> np.ndarray:
    """Rotate -> flip -> crop. Pixels the rotation creates (the corners
    outside the original image) have no measurement behind them and are
    written as NaN, never as a number. Integer input is converted to float32
    first (exact for 16-bit data) because NaN needs a float array; with no
    rotation nothing is created, so the dtype is left alone.

    **Rotation runs through OpenCV (2026-10-11)**: one `cv2.warpAffine` straight
    at the final size instead of scipy's full-canvas `ndimage.rotate` plus a
    slice. Same shape, same NaN pixels. Since 2026-10-20 the warp is Lanczos4
    (~19 ms per 900x1300 frame), not bilinear: see `_cv2_affine` for why and
    the measurement.
    Without rotation, flip and crop are exact array views and need no resampling.

    `fast` (2026-10-20, maintainer decision "fast while moving, exact at rest"): bilinear
    instead of Lanczos4, ~4 ms instead of ~10-25 ms per 1300x900 frame (audit in
    docs/rotated_zarr_export_interpolation_2026-10-20.md). Only for frames shown while the
    slideshow plays; the panel re-renders the frame exactly when Play stops. Same NaN pixels."""
    if _cv2_geometry_usable(image, settings):
        return _warp_geometry_cv2(image, settings, skip_crop=skip_crop, fast=fast)
    if _rotation_active(settings) and not np.issubdtype(image.dtype, np.floating):
        image = image.astype(np.float32)
    return _apply_spatial_transform(image, settings, order=1, mode="constant", cval=np.nan, skip_crop=skip_crop)


def _cv2_geometry_usable(image: np.ndarray, settings: GeometrySettings) -> bool:
    """True when the OpenCV path applies: a rotation is active on a plain 2-D
    image. Near-1-px-wide/tall inputs stay on scipy (its two-step rotate-then-
    flip is the only one verified at that extreme)."""
    return _rotation_active(settings) and image.ndim == 2 and min(image.shape) > 4


def geometry_cache_key(settings: GeometrySettings) -> tuple:
    """Hashable fingerprint of exactly the fields the spatial transform reads
    (`image_tools_enabled`, rotation, flips, crop) - for caches of anything
    derived from them. Display-only fields (units, scale bar...) are left out
    on purpose, so changing them does not invalidate the cache."""
    crop = settings.crop
    return (
        bool(settings.image_tools_enabled),
        float(settings.rotation_angle_deg),
        bool(settings.flip_horizontal),
        bool(settings.flip_vertical),
        bool(crop.enabled),
        int(crop.x),
        int(crop.y),
        int(crop.width),
        int(crop.height),
    )


def _warp_geometry_cv2(
    image: np.ndarray,
    settings: GeometrySettings,
    *,
    skip_crop: bool = False,
    nearest: bool = False,
    border_value: float = float("nan"),
    fast: bool = False,
) -> np.ndarray:
    """Rotate + flip (+ crop) in one `cv2.warpAffine` at the output size.
    `nearest` is for masks (no blending of 0/1); `border_value` fills pixels
    the rotation creates (NaN for images, 0 for masks); `fast`: bilinear (see `apply_spatial_preprocessing`)."""
    effective = replace(settings, crop=CropDefinition()) if skip_crop else settings
    matrix, offset, out_shape = _combined_export_transform(image.shape[:2], effective)
    if out_shape[0] <= 0 or out_shape[1] <= 0:
        return np.zeros(out_shape, dtype=image.dtype)
    return _cv2_affine(image, matrix, offset, out_shape, nearest=nearest, border_value=border_value, fast=fast)


def apply_spatial_mask(
    mask: np.ndarray | None,
    settings: GeometrySettings,
) -> np.ndarray | None:
    if mask is None:
        return None
    if mask.dtype == np.bool_ and spatial_transform_is_identity(settings):
        # Nothing moves, so the general path below hands back this very mask (a bool survives the float32 round trip and the ``>= 0.5``
        # test) after two full-plane conversions, ~3 ms per 1 MP mask. A copy, because the general path always returns a new array.
        # Only bool: for another dtype the threshold is not the same as ``bool()``, so those keep the general path.
        return mask.copy(order="K")
    if _cv2_geometry_usable(mask, settings):
        # uint8 0/1 with nearest-neighbour sampling: stays an exact 0/1 mask.
        warped = _warp_geometry_cv2(mask.astype(np.uint8, copy=False), settings, nearest=True, border_value=0.0)
        return warped >= 1
    transformed = _apply_spatial_transform(mask.astype(np.float32, copy=False), settings, order=0, mode="constant", cval=0.0)
    return transformed >= 0.5


def spatial_coordinate_maps(
    image_shape: tuple[int, int],
    settings: GeometrySettings,
) -> tuple[np.ndarray, np.ndarray]:
    """For every processed pixel, the raw (x, y) it was sampled from, as two
    float32 images. With a rotation active these are evaluated straight from
    the transform (the same affine the image itself uses), clamped to the raw
    image like scipy's ``mode="nearest"`` - exact, and no resampling needed."""
    probe = np.zeros(image_shape, dtype=np.uint8)
    if _cv2_geometry_usable(probe, settings):
        matrix, offset, out_shape = _combined_export_transform(tuple(image_shape), settings)
        rows = np.arange(out_shape[0], dtype=np.float64)[:, None]
        cols = np.arange(out_shape[1], dtype=np.float64)[None, :]
        # raw (row, col) = matrix @ [row, col] + offset
        raw_y = matrix[0, 0] * rows + matrix[0, 1] * cols + offset[0]
        raw_x = matrix[1, 0] * rows + matrix[1, 1] * cols + offset[1]
        y_map = np.clip(raw_y, 0.0, image_shape[0] - 1.0).astype(np.float32)
        x_map = np.clip(raw_x, 0.0, image_shape[1] - 1.0).astype(np.float32)
        return x_map, y_map
    yy, xx = np.indices(image_shape, dtype=np.float32)
    x_map = _apply_spatial_transform(xx, settings, order=1, mode="nearest", cval=0.0)
    y_map = _apply_spatial_transform(yy, settings, order=1, mode="nearest", cval=0.0)
    return x_map, y_map


def spatial_output_shape(
    image_shape: tuple[int, int],
    settings: GeometrySettings,
) -> tuple[int, int]:
    height, width = max(int(image_shape[0]), 1), max(int(image_shape[1]), 1)
    # Use the exact same transform path as the real image pipeline, because
    # ndimage.rotate(..., reshape=True) can differ by 1 px from simple trig.
    probe = np.zeros((height, width), dtype=np.uint8)
    if _cv2_geometry_usable(probe, settings):
        # `_rotation_geometry` reproduces scipy's output size exactly (the
        # image path above uses it too), so no probe image is resampled.
        _matrix, _offset, out_shape = _combined_export_transform((height, width), settings)
        return int(out_shape[0]), int(out_shape[1])
    transformed = _apply_spatial_transform(probe, settings, order=0, mode="constant", cval=0.0)
    return int(transformed.shape[0]), int(transformed.shape[1])


def _rotation_active(settings: GeometrySettings) -> bool:
    return bool(getattr(settings, "image_tools_enabled", True)) and abs(float(settings.rotation_angle_deg)) > 1e-9


def spatial_transform_is_identity(settings: GeometrySettings) -> bool:
    """True when rotate -> flip -> crop (`apply_spatial_preprocessing`) hands the image back exactly as it is: image tools off, or no
    rotation, no flip and no crop. Then processed pixel (y, x) is raw pixel (y, x), so a reader may fetch just the pixels it needs
    (the analysis scan's chunk reads, `analysis/scan.py`). Mirrors the conditions of `_apply_spatial_transform`."""
    if not bool(getattr(settings, "image_tools_enabled", True)):
        return True
    crop = settings.crop
    cropped = bool(crop.enabled and crop.width > 0 and crop.height > 0)
    return not (abs(float(settings.rotation_angle_deg)) > 1e-9 or settings.flip_horizontal or settings.flip_vertical or cropped)


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
    settings: GeometrySettings,
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


def combined_geometry_affine_xy(
    raw_shape: tuple[int, int],
    old_settings: GeometrySettings,
    new_settings: GeometrySettings,
) -> np.ndarray:
    """The 2x3 forward affine `[[a,b,tx],[c,d,ty]]` (`target_xy = M @ [x, y, 1]`,
    same convention as ``image_tools/chromatic/affine.py``'s fitted matrices,
    and the one ``roi/rasterize.py``'s mask-warp helpers already expect)
    mapping an OLD-processed-space point/pixel straight to its NEW-processed-
    space position, after a rotation/flip/crop edit - used to keep ROI
    centers and freeform ROI masks aligned with the image instead of going
    stale (``docs/image_tools_coordinate_spaces.md``'s "known gap").

    Built from ``combined_transform_for_box``'s existing processed-space ->
    raw-space map (``box=(0, 0, 1, 1)`` picks out just the crop origin/matrix,
    independent of any particular box size - see that function's own
    convention), composed old -> raw -> new. The old-space matrix's inverse
    is its own transpose: rotation is orthogonal and a flip is a +-1
    diagonal, and a product of orthogonal matrices is orthogonal, so this
    never needs a general (and potentially ill-conditioned) matrix inverse.

    **Verified two ways, not just derived** (see the rewrite build log's
    matching entry): against ``apply_spatial_preprocessing`` itself (plant a
    marked point in a raw image, render it under both settings, check this
    matrix predicts the same before/after positions the real renderer
    produces - agreement within resampling/centroid noise, <0.22px, across
    rotation/flip/crop combinations) and, with no image or interpolation
    involved at all, as a pure round-trip (old point -> raw -> new -> raw)
    matching the original raw point to 1e-14, i.e. floating-point noise, not
    approximation error.
    """
    old_matrix_rc, old_offset_rc, _ = combined_transform_for_box(raw_shape, old_settings, (0, 0, 1, 1))
    new_matrix_rc, new_offset_rc, _ = combined_transform_for_box(raw_shape, new_settings, (0, 0, 1, 1))
    new_inverse_rc = new_matrix_rc.T  # orthogonal -> transpose is the exact inverse
    combined_matrix_rc = new_inverse_rc @ old_matrix_rc
    combined_offset_rc = new_inverse_rc @ (old_offset_rc - new_offset_rc)
    return np.array(
        [
            [combined_matrix_rc[1, 1], combined_matrix_rc[1, 0], combined_offset_rc[1]],
            [combined_matrix_rc[0, 1], combined_matrix_rc[0, 0], combined_offset_rc[0]],
        ]
    )


def raw_to_processed_affine_xy(raw_shape: tuple[int, int], settings: GeometrySettings) -> np.ndarray:
    """2x3 affine from raw pixel (x, y) to processed-space (x, y) under ``settings`` (identity when the image tools
    are off). The inverse direction is `invert_affine_matrix` of this. Used where something lives in raw pixels but
    is drawn or compared in processed space (the OME-Zarr export writes raw pixels; ROIs are in processed space).
    Same machinery as `combined_geometry_affine_xy`, with "no rotation/flip/crop" as the old settings."""
    if not bool(getattr(settings, "image_tools_enabled", False)):
        return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    raw_settings = replace(
        settings,
        rotation_angle_deg=0.0,
        flip_horizontal=False,
        flip_vertical=False,
        crop=replace(settings.crop, enabled=False),
    )
    return combined_geometry_affine_xy(raw_shape, raw_settings, settings)


def remap_point_for_geometry_change(
    point_xy: tuple[float, float],
    raw_shape: tuple[int, int],
    old_settings: GeometrySettings,
    new_settings: GeometrySettings,
) -> tuple[float, float]:
    """Where an OLD-processed-space point ends up in NEW-processed-space
    after a rotation/flip/crop edit - see `combined_geometry_affine_xy`."""
    matrix = combined_geometry_affine_xy(raw_shape, old_settings, new_settings)
    x, y = float(point_xy[0]), float(point_xy[1])
    out = matrix @ np.array([x, y, 1.0])
    return float(out[0]), float(out[1])


def combined_transform_for_box(
    in_shape: tuple[int, int],
    settings: GeometrySettings,
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
    settings: GeometrySettings,
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

    # Clamp to a valid, non-empty, in-bounds box. A box inside a rotated
    # canvas's NaN corner can have its true (unclamped) raw extent fall
    # entirely outside the raw image - clamping x0/x1 (or y0/y1)
    # independently to [0, dim] could then invert (x0 > x1). Clamping x0 into
    # a valid index first, then x1 to be at least x0+1, guarantees a valid
    # box instead of an empty/inverted slice (the resample then yields NaN
    # for every pixel that really lies outside the raw image).
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
    settings: GeometrySettings,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    """Resample a raw-space patch (already read via a chunk-aware partial
    read, at `raw_patch_origin_xy` within the full raw image) directly into
    the final PROCESSED-space `box`, applying rotation/flip. Mirrors
    apply_spatial_preprocessing_export's interpolation (rotation-created
    pixels are NaN, including in its cv2 fast path) but scoped to a small box instead of the whole plane, and reading from an
    already-cropped-to-the-needed-region raw patch instead of the full raw
    array.

    The cv2 path resamples with Lanczos4 since 2026-10-20 (see `_cv2_affine`);
    the scipy fallback (no OpenCV) is bilinear. Earlier bilinear measurement:
    docs/roi_scoped_resample_cv2_fast_path.md.
    """
    matrix, offset, out_shape = combined_transform_for_box(in_shape, settings, box)
    if out_shape[0] <= 0 or out_shape[1] <= 0:
        return np.zeros(out_shape, dtype=raw_patch.dtype)
    raw_x0, raw_y0 = raw_patch_origin_xy
    # raw_coord = matrix @ [r,c] + offset (full raw image coords); raw_patch's
    # own local coords are raw_coord - (raw_y0, raw_x0), i.e. a plain
    # subtraction from the offset term, not a matrix-multiplied one.
    local_offset = offset - np.array([float(raw_y0), float(raw_x0)])
    if _rotation_active(settings) and not np.issubdtype(raw_patch.dtype, np.floating):
        raw_patch = raw_patch.astype(np.float32)
    cv2_result = _cv2_affine(raw_patch, matrix, local_offset, out_shape)
    if cv2_result is not None:
        return cv2_result
    return ndimage.affine_transform(
        raw_patch,
        matrix,
        local_offset,
        output_shape=out_shape,
        order=1,
        mode="constant",
        cval=np.nan,
        prefilter=False,
    )


def apply_spatial_preprocessing_export(
    image: np.ndarray,
    settings: GeometrySettings,
) -> np.ndarray:
    """Fast equivalent of apply_spatial_preprocessing for the OME-Zarr export
    hot path: resamples directly at the final rotate/flip/crop size instead
    of materializing the full ndimage.rotate(reshape=True) canvas and slicing
    it down afterward. When the configured crop is much smaller than the full
    spectral_cube_index, this avoids interpolating pixels that would just be discarded.
    Produces the same output as apply_spatial_preprocessing (rotation-created
    pixels are NaN, same crop clamping) - only the amount of work differs.
    The result is float32 whenever a rotation is active (NaN needs a float
    array); a caller storing it must not cast it to an integer dtype.
    """
    if not bool(getattr(settings, "image_tools_enabled", True)):
        return image

    angle = float(settings.rotation_angle_deg)
    if abs(angle) <= 1e-9 or min(image.shape[:2]) <= 4:
        # No rotation: flip + crop are cheap exact array ops already, no
        # interpolation involved, so the existing path is both simplest and exact.
        # Degenerate (near-1px-wide/tall) inputs are also routed here: scipy's
        # affine_transform mishandles boundary fill for combined rotate+flip
        # matrices at that extreme (verified empirically), while the two-step
        # rotate-then-flip path scipy normally uses does not hit that quirk.
        return apply_spatial_preprocessing(image, settings)

    if not np.issubdtype(image.dtype, np.floating):
        image = image.astype(np.float32)
    matrix, offset, out_shape = _combined_export_transform(image.shape[:2], settings)
    if out_shape[0] <= 0 or out_shape[1] <= 0:
        return np.zeros(out_shape, dtype=image.dtype)

    # OpenCV's warpAffine (Lanczos4 since 2026-10-20, see `_cv2_affine`) marks
    # exactly the same pixels as outside the source as scipy does (re-verified
    # 2026-10-20 at 1.4/-7.3/33 deg: identical NaN sets). The scipy fallback
    # below is bilinear and only runs without OpenCV.
    cv2_result = _cv2_affine(image, matrix, offset, out_shape)
    if cv2_result is not None:
        return cv2_result

    return ndimage.affine_transform(
        image,
        matrix,
        offset,
        output_shape=out_shape,
        order=1,
        mode="constant",
        cval=np.nan,
        prefilter=False,
    )


_EXPORT_PLAN_CACHE: dict = {}
_EXPORT_PLAN_CACHE_MAX = 4


def _export_plan(shape: tuple[int, int], settings: GeometrySettings) -> tuple[np.ndarray, tuple[int, int], np.ndarray]:
    """What every plane of one export shares (the warp matrix, the output size and the outside-source mask), worked out once per
    image shape and geometry instead of once per plane (the mask alone is 4.5 ms of a ~170 ms plane). The mask is 1 MB; a handful
    are kept, so a worker that sees one shape holds exactly one. Never modify the returned arrays."""
    key = (tuple(int(v) for v in shape), geometry_cache_key(settings))
    plan = _EXPORT_PLAN_CACHE.get(key)
    if plan is None:
        matrix, offset, out_shape = _combined_export_transform(shape, settings)
        cv2_matrix = np.array(
            [[matrix[1, 1], matrix[1, 0], offset[1]], [matrix[0, 1], matrix[0, 0], offset[0]]], dtype=np.float64
        )
        plan = (cv2_matrix, out_shape, _outside_source_mask(matrix, offset, out_shape, shape))
        if len(_EXPORT_PLAN_CACHE) >= _EXPORT_PLAN_CACHE_MAX:
            _EXPORT_PLAN_CACHE.pop(next(iter(_EXPORT_PLAN_CACHE)))
        _EXPORT_PLAN_CACHE[key] = plan
    return plan


def apply_spatial_preprocessing_export_stored(
    image: np.ndarray,
    settings: GeometrySettings,
    dtype: np.dtype | type,
    step: int = 1,
) -> np.ndarray | None:
    """Rotate / flip / crop an integer plane straight into the export's stored integer dtype (2026-10-22), or ``None`` when this
    shortcut does not apply and the caller must take the general route (`apply_spatial_preprocessing_export`, then
    `_zarr_export_worker._to_stored`).

    **The same numbers as the general route, bit for bit** (pinned by a test over angles, flips, crops and steps, and by identical
    shard files on the maintainer's data): the plane is Lanczos4-resampled in float32 exactly as `_cv2_affine` does, pixels with no
    source pixel become 0, values are rounded (half to even) onto the camera's value ``step`` and clipped to the dtype. What differs
    is only how much work it takes (measured 2026-10-22, 1300 x 900 uint16, one thread):

    - the values are divided by ``step`` *before* the warp, not after. A power of two scales every float32 product and sum exactly,
      so the warp sees the same numbers, only smaller, and the rounding to the step becomes a plain rint: no ``np.where`` /
      divide / multiply passes over a 4 MB float array;
    - the outside-source mask and the matrix come from `_export_plan` (once per export, not per plane);
    - there is no NaN round trip: an integer plane has no NaN, and the pixels with no source are zeroed directly.

    Applies only to a plain 2-D integer plane that a rotation changes, stored in an unsigned dtype, with a power-of-two ``step``
    (16 for a 12-bit camera): anything else returns ``None``."""
    dtype = np.dtype(dtype)
    step = max(int(step), 1)
    if (
        not np.issubdtype(image.dtype, np.integer)
        or not np.issubdtype(dtype, np.unsignedinteger)
        or step & (step - 1)
        or not _cv2_geometry_usable(image, settings)
    ):
        return None
    cv2_matrix, out_shape, outside = _export_plan(image.shape[:2], settings)
    out_h, out_w = out_shape
    if out_h <= 0 or out_w <= 0:
        return None
    scaled = np.ascontiguousarray(image, dtype=np.float32)
    if step > 1:
        scaled *= np.float32(1.0 / step)
    result = cv2.warpAffine(
        scaled, cv2_matrix, (out_w, out_h), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE
    )
    result[outside] = 0.0
    np.rint(result, out=result)
    np.clip(result, 0, np.iinfo(dtype).max // step, out=result)
    stored = result.astype(dtype)
    if step > 1:
        stored *= dtype.type(step)
    return stored


def _cv2_affine(
    image: np.ndarray,
    matrix: np.ndarray,
    offset: np.ndarray,
    out_shape: tuple[int, int],
    *,
    nearest: bool = False,
    border_value: float = float("nan"),
    fast: bool = False,
) -> np.ndarray | None:
    """Apply the same output[y,x] = image[matrix @ [y,x] + offset] mapping as
    ndimage.affine_transform, via cv2.warpAffine, with `border_value` (NaN by
    default) outside the source image. Float32/float64 (or, with `nearest`,
    uint8) input; anything else is converted to float32. Returns None if cv2
    is unavailable so the caller can fall back to the scipy path.

    **Images are resampled with Lanczos4 (2026-10-20, maintainer decision)**, masks
    (`nearest`) with nearest neighbour. Measured on real frames (1.4 deg, 144 ROIs;
    docs/rotated_zarr_export_interpolation_2026-10-20.md, tools/rotation_interpolation_lab.py):
    bilinear, used before, averaged neighbouring pixels - it kept only 72 % of the
    pixel noise (60-80 % depending on the sub-pixel phase, a moire in the noise) and
    moved ROI sample/reference ratios by ~720 ppm RMS, close to one frame's own ROI
    noise (~890 ppm). Lanczos4 keeps 94 % of the noise and moves the ratios by
    ~140 ppm, as close as cubic/quintic splines and an FFT (sinc) rotation, at
    19 ms instead of 7 ms per 1300x900 frame. OpenCV's 1/32 px position step does not
    limit it (it equals the quintic spline there).

    Which pixels are NaN does not change with the method: the Lanczos warp runs with
    a replicated border (its 8x8 kernel would otherwise carry the NaN border 3-4 px
    into the image), then exactly the pixels whose source position lies outside the
    raw image get `border_value` (`_outside_source_mask`). NaN inside the source (none
    in practice: masks never write into the image) is kept to the footprint bilinear
    gives, never grown to the kernel's.

    `fast`: the bilinear warp used before 2026-10-20 (NaN border, same NaN pixels), for the
    slideshow preview only."""
    if nearest and image.dtype == np.uint8:
        src = image
    else:
        src = image if image.dtype in (np.float32, np.float64) else image.astype(np.float32)
    src = np.ascontiguousarray(src)

    out_h, out_w = out_shape
    # cv2 uses (x, y) axis order and a src<-dst matrix when WARP_INVERSE_MAP is
    # set - a straight axis swap of our (row, col) convention matrix/offset.
    cv2_matrix = np.array(
        [[matrix[1, 1], matrix[1, 0], offset[1]],
         [matrix[0, 1], matrix[0, 0], offset[0]]],
        dtype=np.float64,
    )
    if nearest or fast:
        return cv2.warpAffine(
            src, cv2_matrix, (out_w, out_h), flags=(cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR) | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT, borderValue=border_value,
        )
    # Integer input cannot hold NaN (it is converted to float32 above): skip the ~1 ms scan.
    source_nan = np.isnan(src) if np.issubdtype(image.dtype, np.floating) else None
    has_source_nan = source_nan is not None and bool(source_nan.any())
    if has_source_nan:
        src = np.where(source_nan, np.nanmean(src), src).astype(src.dtype, copy=False)
    result = cv2.warpAffine(
        src, cv2_matrix, (out_w, out_h), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    result[_outside_source_mask(matrix, offset, out_shape, src.shape[:2])] = border_value
    if has_source_nan:
        footprint = cv2.warpAffine(
            source_nan.astype(np.float32), cv2_matrix, (out_w, out_h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
        )
        result[footprint > 0] = np.nan
    return result


def _outside_source_mask(
    matrix: np.ndarray, offset: np.ndarray, out_shape: tuple[int, int], src_shape: tuple[int, int]
) -> np.ndarray:
    """True for every output pixel whose source position (`matrix @ [r, c] + offset`) lies outside the source image
    ([0, size - 1] on both axes) - the pixels a rotation creates. Per row the inside part is one interval of columns
    (the map is affine), so this compares column indices against two bounds per row instead of building the full
    coordinate maps."""
    out_h, out_w = out_shape
    rows = np.arange(out_h, dtype=np.float64)
    lo = np.full(out_h, -np.inf)
    hi = np.full(out_h, np.inf)
    eps = 1e-9
    for axis, size in ((0, src_shape[0]), (1, src_shape[1])):
        base = matrix[axis, 0] * rows + offset[axis]  # source coordinate at column 0
        slope = float(matrix[axis, 1])
        if abs(slope) < 1e-12:
            outside = (base < -eps) | (base > size - 1 + eps)
            lo[outside], hi[outside] = np.inf, -np.inf
            continue
        a = (-eps - base) / slope
        b = (size - 1 + eps - base) / slope
        lo = np.maximum(lo, np.minimum(a, b))
        hi = np.minimum(hi, np.maximum(a, b))
    cols = np.arange(out_w, dtype=np.float64)[None, :]
    return (cols < lo[:, None]) | (cols > hi[:, None])


def _apply_spatial_transform(
    image: np.ndarray,
    settings: GeometrySettings,
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
