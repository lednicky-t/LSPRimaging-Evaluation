"""Pure spatial-transform math (crop/rotate/flip) - ports the geometry-only
functions out of ``processing/preprocess.py`` (sketch §10's ``preprocess.py``
note), verbatim apart from retyping ``settings: PreprocessingSettings`` to
``settings: GeometrySettings`` - every function here only ever reads
``image_tools_enabled``/``rotation_angle_deg``/``rotation_fill_dark``/
``flip_horizontal``/``flip_vertical``/``crop``, all of which carry over
unchanged (see ``model.py``'s docstring).

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .model import GeometrySettings

_CV2_SUPPORTED_DTYPES = (np.uint8, np.uint16, np.int16, np.float32, np.float64)


def apply_spatial_preprocessing(
    image: np.ndarray,
    settings: GeometrySettings,
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
    settings: GeometrySettings,
) -> np.ndarray | None:
    if mask is None:
        return None
    transformed = _apply_spatial_transform(mask.astype(np.float32, copy=False), settings, order=0, mode="constant", cval=0.0)
    return transformed >= 0.5


def rotation_fill_pixel_mask(
    raw_shape: tuple[int, int],
    settings: GeometrySettings,
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
    settings: GeometrySettings,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    """Resample a raw-space patch (already read via a chunk-aware partial
    read, at `raw_patch_origin_xy` within the full raw image) directly into
    the final PROCESSED-space `box`, applying rotation/flip. Mirrors
    apply_spatial_preprocessing_export's interpolation (same fill-mode
    handling, including its cv2 fast path for the nearest/edge-stretch case)
    but scoped to a small box instead of the whole plane, and reading from an
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
    fill_mode = "constant" if bool(settings.rotation_fill_dark) else "nearest"
    if fill_mode == "nearest":
        cv2_result = _cv2_affine(raw_patch, matrix, local_offset, out_shape)
        if cv2_result is not None:
            return cv2_result
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
    settings: GeometrySettings,
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
