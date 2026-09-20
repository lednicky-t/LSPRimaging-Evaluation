"""Apply a chromatic affine transform to pixel arrays (images/masks), as
opposed to `affine.py`'s point-based math. Depends only on `affine.py`'s
`invert_affine_matrix` and `scipy.ndimage` - no landmark detection, no Qt.

Split out of the former single `fitting.py` (2026-09-21) - see the rewrite
build log for the file-split reasoning.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .affine import invert_affine_matrix


def warp_image_affine(
    image: np.ndarray,
    affine_matrix: np.ndarray,
    *,
    output_shape: tuple[int, int] | None = None,
    order: int = 1,
    cval: float = 0.0,
) -> np.ndarray:
    if output_shape is None:
        output_shape = image.shape[:2]
    inverse_xy = invert_affine_matrix(affine_matrix)
    ixx, ixy = float(inverse_xy[0, 0]), float(inverse_xy[0, 1])
    iyx, iyy = float(inverse_xy[1, 0]), float(inverse_xy[1, 1])
    off_x, off_y = float(inverse_xy[0, 2]), float(inverse_xy[1, 2])
    matrix_rc = np.array([[iyy, iyx], [ixy, ixx]], dtype=np.float64)
    offset_rc = np.array([off_y, off_x], dtype=np.float64)
    return ndimage.affine_transform(
        image,
        matrix=matrix_rc,
        offset=offset_rc,
        output_shape=output_shape,
        order=order,
        mode="constant",
        cval=cval,
        prefilter=order > 1,
    )


def warp_boolean_mask_affine(
    mask: np.ndarray,
    affine_matrix: np.ndarray,
    *,
    output_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    """Warp a boolean mask through a chromatic affine (nearest-neighbor, so the
    result stays a clean boolean array) -- lets a full-image mask (e.g. the
    ignore-mask) follow a specific wavelength's per-wavelength-corrected
    geometry, the same way roi.rasterize's transformed_disk_mask/
    transformed_annulus_mask already do for circle/annulus ROIs.
    """
    warped = warp_image_affine(
        mask.astype(np.float32, copy=False),
        affine_matrix,
        output_shape=output_shape,
        order=0,
        cval=0.0,
    )
    return warped >= 0.5


def apply_mask_wavelength_diff(mask: np.ndarray, diff: dict[tuple[int, int], bool] | None) -> np.ndarray:
    """Apply a sparse per-wavelength ignore-mask diff - `{(row, col): value}`,
    already in `mask`'s own pixel space - on top of `mask`. Non-mutating; a
    no-op when `diff` is empty/None, which is the common case (most
    wavelengths have no manual edits - see MaskController.apply_wavelength_diff,
    the mask analogue of AreaRoi.per_wavelength for ROI positions). Entries
    outside `mask`'s bounds are ignored rather than raising, since a diff can
    outlive a preprocessing change that shrinks the image (e.g. a new crop).
    """
    if not diff:
        return mask
    result = mask.copy()
    height, width = result.shape[:2]
    for (row, col), value in diff.items():
        if 0 <= row < height and 0 <= col < width:
            result[row, col] = bool(value)
    return result
