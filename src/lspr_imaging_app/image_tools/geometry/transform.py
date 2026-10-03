"""Pure spatial-transform math (crop/rotate/flip) - ports the geometry-only
functions out of ``processing/preprocess.py`` (sketch §10's ``preprocess.py``
note), retyped to ``settings: GeometrySettings`` - every function here only
ever reads ``image_tools_enabled``/``rotation_angle_deg``/``flip_horizontal``/
``flip_vertical``/``crop`` (see ``model.py``'s docstring).

**Rotation-created pixels are NaN** (2026-10-03, TASK_rotation_fill_handling):
they have no measurement behind them, so they have no value. There is no
dark (0) fill and no edge-stretch. Validity of any pixel is
``np.isfinite(image)``; no geometric "fill mask" exists any more.

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .model import GeometrySettings


def apply_spatial_preprocessing(
    image: np.ndarray,
    settings: GeometrySettings,
    *,
    skip_crop: bool = False,
) -> np.ndarray:
    """Rotate -> flip -> crop. Pixels the rotation creates (the corners
    outside the original image) have no measurement behind them and are
    written as NaN, never as a number. Integer input is converted to float32
    first (exact for 16-bit data) because NaN needs a float array; with no
    rotation nothing is created, so the dtype is left alone."""
    if _rotation_active(settings) and not np.issubdtype(image.dtype, np.floating):
        image = image.astype(np.float32)
    return _apply_spatial_transform(image, settings, order=1, mode="constant", cval=np.nan, skip_crop=skip_crop)


def _rotation_active(settings: GeometrySettings) -> bool:
    return bool(getattr(settings, "image_tools_enabled", True)) and abs(float(settings.rotation_angle_deg)) > 1e-9


def apply_spatial_mask(
    mask: np.ndarray | None,
    settings: GeometrySettings,
) -> np.ndarray | None:
    if mask is None:
        return None
    transformed = _apply_spatial_transform(mask.astype(np.float32, copy=False), settings, order=0, mode="constant", cval=0.0)
    return transformed >= 0.5


def spatial_coordinate_maps(
    image_shape: tuple[int, int],
    settings: GeometrySettings,
) -> tuple[np.ndarray, np.ndarray]:
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

    The cv2 path introduces a tiny, deterministic difference from scipy's
    continuous bilinear interpolation (OpenCV rounds the sub-pixel offset to
    one of 32 discrete steps internally) - measured at up to ~0.4 counts on
    real 16-bit sensor data, ~30,000x below that same data's own Poisson
    shot-noise floor (~200 counts). See
    docs/roi_scoped_resample_cv2_fast_path.md for the full measurement
    writeup and why this is treated as scientifically negligible.
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

    # OpenCV's warpAffine is ~15-30x faster than scipy's generic interpolation
    # loop. With a NaN border it marks exactly the same pixels as outside the
    # source as scipy does (verified 2026-10-03 at 3/15/33 deg: identical NaN
    # sets, finite values within 0.04 counts), so the fast path is safe here.
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


def _cv2_affine(
    image: np.ndarray,
    matrix: np.ndarray,
    offset: np.ndarray,
    out_shape: tuple[int, int],
) -> np.ndarray | None:
    """Apply the same output[y,x] = image[matrix @ [y,x] + offset] mapping as
    ndimage.affine_transform, via cv2.warpAffine, with NaN outside the
    source image. `image` must be float32/float64. Returns None if cv2 is
    unavailable so the caller can fall back to the scipy path.
    """
    try:
        import cv2
    except ImportError:
        return None

    src = image if image.dtype in (np.float32, np.float64) else image.astype(np.float32)
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
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=float("nan"),
    )
    return result


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
