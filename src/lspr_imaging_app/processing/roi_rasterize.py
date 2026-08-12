"""Cropped-mask utilities for the arbitrary-mask ROI geometry fallback.

Circle/annulus ROI geometry is rasterized on demand by
`processing.chromatic.transformed_disk_mask`/`transformed_annulus_mask` — this
module does not duplicate that. It only handles the "mask" geometry escape
hatch: an ROI whose sample or reference region cannot be described as a
circle/annulus is stored as a boolean mask cropped to its bounding box
(`domain.models.RoiMask`), not a full-image-sized array. These helpers convert
between that cropped form and the full-image or patch-local boolean arrays the
rest of the analysis pipeline expects, clipping safely if the mask extends
outside the target bounds.
"""

from __future__ import annotations

import numpy as np

from lspr_imaging_app.domain.models import RoiMask


def crop_mask(mask: np.ndarray) -> RoiMask:
    """Crop a full-size boolean mask to its bounding box.

    Raises ValueError if the mask has no True pixels — an empty ROI mask
    cannot be stored (there would be nothing to reconstruct).
    """
    yy, xx = np.nonzero(mask)
    if xx.size == 0:
        raise ValueError("Cannot store an empty ROI mask.")
    x0, x1 = int(xx.min()), int(xx.max()) + 1
    y0, y1 = int(yy.min()), int(yy.max()) + 1
    return RoiMask(x0=x0, y0=y0, mask=np.asarray(mask[y0:y1, x0:x1], dtype=bool).copy())


def expand_mask(roi_mask: RoiMask, image_shape: tuple[int, int]) -> np.ndarray:
    """Place a cropped mask into a full-image-sized boolean array."""
    height, width = image_shape[:2]
    out = np.zeros((height, width), dtype=bool)
    _blit(out, roi_mask, offset_x=0, offset_y=0)
    return out


def expand_mask_to_patch(
    roi_mask: RoiMask,
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
) -> np.ndarray:
    """Same as `expand_mask`, but into an array local to a patch that starts at
    `patch_origin_xy` in full-image coordinates — mirrors
    `transformed_*_mask_for_patch` in `processing.chromatic`.
    """
    out = np.zeros((int(patch_shape[0]), int(patch_shape[1])), dtype=bool)
    px0, py0 = int(patch_origin_xy[0]), int(patch_origin_xy[1])
    _blit(out, roi_mask, offset_x=-px0, offset_y=-py0)
    return out


def _blit(out: np.ndarray, roi_mask: RoiMask, *, offset_x: int, offset_y: int) -> None:
    """OR `roi_mask.mask` into `out` at (roi_mask.x0 + offset_x, roi_mask.y0 +
    offset_y), clipped safely to `out`'s bounds. A mask that ends up fully
    outside `out` is a silent no-op (matches the spec's "clip, don't crash"
    boundary-handling rule) rather than an error.
    """
    height, width = out.shape[:2]
    mask_h, mask_w = roi_mask.mask.shape[:2]
    dst_x0 = roi_mask.x0 + offset_x
    dst_y0 = roi_mask.y0 + offset_y

    src_x0 = max(0, -dst_x0)
    src_y0 = max(0, -dst_y0)
    clipped_dst_x0 = max(0, dst_x0)
    clipped_dst_y0 = max(0, dst_y0)
    clipped_dst_x1 = min(width, dst_x0 + mask_w)
    clipped_dst_y1 = min(height, dst_y0 + mask_h)
    if clipped_dst_x0 >= clipped_dst_x1 or clipped_dst_y0 >= clipped_dst_y1:
        return

    src_x1 = src_x0 + (clipped_dst_x1 - clipped_dst_x0)
    src_y1 = src_y0 + (clipped_dst_y1 - clipped_dst_y0)
    out[clipped_dst_y0:clipped_dst_y1, clipped_dst_x0:clipped_dst_x1] |= roi_mask.mask[src_y0:src_y1, src_x0:src_x1]
