"""ROI mask rasterization - the one dispatcher for every geometry type
(circle/annulus/mask), plus `rasterize_fractional` (§6a fractional pixel
weighting, built 2026-09-22) - a coverage-weighted variant of the same
dispatch via one shared supersample-and-downsample engine, not a per-shape
rewrite.

No Qt import allowed in this file (AGENTS.md testing rule). AGENTS.md
non-negotiable invariant: cache per-ROI analysis masks at that ROI's own
small bounding box, never full-image-plane size (full-size caching measured
8-14GB RAM at realistic ROI counts) - every function here that returns a
full-image-sized array still does its own internal reach-box shrinking
before filling it in, exactly like the code it was ported/unified from.

**Unified here 2026-09-20** (maintainer's explicit choice - see
``docs/rewrite_build_log_2026-09.md``): sketch §10 originally split this
module from circle/annulus rasterization, which the current app keeps in
``processing/chromatic.py`` alongside the chromatic-correction math (ported
to ``image_tools/chromatic/affine.py`` - originally a single ``fitting.py``,
later split into several files, see that build log's 2026-09-21 entry).
That left "which module rasterizes what" ambiguous once ``roi_rasterize.py``
turned out to only handle the arbitrary-mask escape hatch. Chosen fix: pull
the circle/annulus math (``transformed_circle_points``, ``transformed_disk_mask``,
``transformed_annulus_mask`` and their ``_for_patch`` variants) out of
Chromatic and into this module, so every geometry type an ``AreaRoi`` can
have - circle, annulus, or mask - rasterizes through one place. These
functions take an ``affine_matrix`` as a plain parameter (obtained by the
caller from ``ChromaticModule.affine_for()``, the module's only public
surface per AGENTS.md's "Module boundaries") - this module never imports or
calls into Chromatic itself, keeping the boundary one-directional.

``rasterize_sample``/``rasterize_reference`` (and their ``_for_patch``
variants) are the real per-ROI, per-side dispatchers, ported from the inline
``if roi.sample_geometry_type == "mask" ... else transformed_disk_mask(...)``
logic in ``gui/analysis_tasks.py``'s ``_selected_roi_masks_for_spectrum`` -
consolidated into one reusable place instead of re-implemented at each call
site. That function's own multi-ROI OR-accumulation loop (iterate every
*selected* ROI, combine into one mask, patch/reach-window caching) stays
analysis-layer work for the not-yet-built ``analysis/tasks.py`` to own; this
module only rasterizes one ROI's one side at a time.

**Mask-geometry ROIs are now re-warped by the chromatic affine transform in
`rasterize_sample`/`rasterize_reference` (2026-09-21, closing the gap
flagged in the previous entry)** - `expand_mask` places the stored
`RoiMask` (authored in the reference frame's processed-image space) into a
full-image-sized array, then `image_tools.chromatic.warp.
warp_boolean_mask_affine` maps it through the same `affine_matrix`
parameter the circle/annulus branch already takes, exactly the same
pattern, no new one. Both non-patch functions already return a full-
image-sized array regardless (that's what `expand_mask` always did), so
this adds no new memory cost there.

**`rasterize_sample_for_patch`/`rasterize_reference_for_patch`'s mask
branch is now warped too (2026-09-21)** - turned out not to be the
"trivial, same pattern" fix it looked like from the outside. Naively
warping the full expanded mask and *then* cropping to the patch would have
materialized a full-image-sized intermediate array inside the one code
path that exists specifically to avoid that (AGENTS.md's non-negotiable
invariant: "cache per-ROI analysis masks at that ROI's own small bounding
box, never full-image-plane size - full-size caching measured 8-14GB RAM
at realistic ROI counts"). Built instead: `_mask_reach_box` (the
arbitrary-mask analogue of `annulus_reach_box` below, transforming the
stored `RoiMask`'s bounding-box corners through `affine_matrix` rather
than a circle's radius) bounds how far the warped result can reach in
target space, and `_warp_roi_mask_into_box`/`expand_mask_to_patch_warped`
warp only within that bound, reading directly from `roi_mask.mask`'s own
small array - the full source/target canvases are never materialized.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy import ndimage

from ..image_tools.chromatic.affine import apply_affine_to_points, invert_affine_matrix
from ..image_tools.chromatic.warp import warp_boolean_mask_affine
from .model import AreaRoi, RoiMask

# -- arbitrary-mask geometry: crop/expand between stored and working form ---


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
    `transformed_*_mask_for_patch` below.
    """
    out = np.zeros((int(patch_shape[0]), int(patch_shape[1])), dtype=bool)
    px0, py0 = int(patch_origin_xy[0]), int(patch_origin_xy[1])
    _blit(out, roi_mask, offset_x=-px0, offset_y=-py0)
    return out


# -- mask geometry: reach-box-limited affine warp (2026-09-21) --------------
# The mask-geometry counterpart to annulus_reach_box/_annulus_mask_in_box
# below - built once circle/annulus's own reach-box pattern made clear what
# shape the fix needed, see module docstring for why "warp the full mask,
# then crop to the patch" was rejected instead.


def _mask_reach_box(roi_mask: RoiMask, affine_matrix: np.ndarray) -> tuple[int, int, int, int]:
    """The axis-aligned target-space bounding box `roi_mask`'s own bounding
    box maps to under `affine_matrix` - the arbitrary-mask analogue of
    `annulus_reach_box`, for a rectangle's corners instead of a circle's
    radius. A small integer margin is added on every side as a safety
    margin around the float->int rounding, matching `annulus_reach_box`'s
    own `+ 3.0` padding in spirit."""
    x0, y0 = float(roi_mask.x0), float(roi_mask.y0)
    height, width = roi_mask.mask.shape[:2]
    corners = np.array(
        [[x0, y0], [x0 + width, y0], [x0, y0 + height], [x0 + width, y0 + height]],
        dtype=np.float64,
    )
    transformed = apply_affine_to_points(corners, affine_matrix)
    x_min, y_min = transformed.min(axis=0)
    x_max, y_max = transformed.max(axis=0)
    return (
        int(np.floor(x_min)) - 2,
        int(np.floor(y_min)) - 2,
        int(np.ceil(x_max)) + 2,
        int(np.ceil(y_max)) + 2,
    )


def _warp_roi_mask_into_box(
    roi_mask: RoiMask,
    affine_matrix: np.ndarray,
    box: tuple[int, int, int, int],
) -> np.ndarray:
    """Warp `roi_mask` (authored in source/reference space) through
    `affine_matrix`, returning only `box`'s `(x0, y0, x1, y1)` sub-region of
    the result - never materializing the full target-space canvas, the
    reason `rasterize_sample_for_patch`/`rasterize_reference_for_patch`
    exist in the first place. Nearest-neighbor (`order=0`), matching
    `chromatic.warp.warp_boolean_mask_affine`'s boolean-safe convention -
    this reimplements that function's inverse-matrix construction rather
    than calling it, since it needs to target an arbitrary sub-box instead
    of always starting output at (0, 0), and to read from `roi_mask.mask`'s
    own small local array (indexed from `(0, 0)`, offset by
    `roi_mask.x0`/`roi_mask.y0`) instead of a full-canvas-sized input.

    **Pads `roi_mask.mask` by a few pixels before warping** - found by
    testing against `expand_mask` + `warp_boolean_mask_affine` on a full
    canvas (the already-verified ground truth) rather than assumed:
    `scipy.ndimage.affine_transform`'s `order=0`/`mode="constant"` boundary
    handling does not treat a computed source coordinate within half a
    pixel of index 0 (or the last valid index) as in-bounds the way a
    simple "round to nearest" model would predict - any coordinate that
    rounds to a valid edge index but isn't itself comfortably inside
    `[0, size)` gets the constant fill instead, silently dropping real
    mask content that legitimately sits at `roi_mask.mask`'s own edge
    (verified directly: `affine_transform` on a tiny array with an offset
    of `-0.1` already returns the fill value, not index 0). Padding with a
    margin of false pixels keeps every real sample comfortably away from
    that boundary, at negligible cost (the padded array is still small -
    this is the same reach-box-bounded array the whole point of this
    function is to keep small, just a few pixels larger on each side).
    """
    box_x0, box_y0, box_x1, box_y1 = box
    box_h, box_w = box_y1 - box_y0, box_x1 - box_x0
    if box_h <= 0 or box_w <= 0:
        return np.zeros((max(box_h, 0), max(box_w, 0)), dtype=bool)
    pad = 4
    padded_mask = np.pad(roi_mask.mask, pad, mode="constant", constant_values=False)
    inverse_xy = invert_affine_matrix(affine_matrix)
    ixx, ixy = float(inverse_xy[0, 0]), float(inverse_xy[0, 1])
    iyx, iyy = float(inverse_xy[1, 0]), float(inverse_xy[1, 1])
    off_x, off_y = float(inverse_xy[0, 2]), float(inverse_xy[1, 2])
    matrix_rc = np.array([[iyy, iyx], [ixy, ixx]], dtype=np.float64)
    # Full-canvas offset (as warp_boolean_mask_affine would compute it),
    # shifted for this box's own origin (box_y0/box_x0 - we're resolving
    # target pixel (r + box_y0, c + box_x0) for local pixel (r, c)) and
    # the padded array's own local origin (index (0, 0) of `padded_mask`
    # corresponds to source position (roi_mask.y0 - pad, roi_mask.x0 - pad),
    # not roi_mask.x0/y0 directly, now that padding shifted it).
    offset_rc = np.array([off_y, off_x], dtype=np.float64)
    box_origin_rc = np.array([float(box_y0), float(box_x0)], dtype=np.float64)
    mask_origin_rc = np.array([float(roi_mask.y0 - pad), float(roi_mask.x0 - pad)], dtype=np.float64)
    local_offset = offset_rc + matrix_rc @ box_origin_rc - mask_origin_rc
    warped = ndimage.affine_transform(
        padded_mask.astype(np.float32, copy=False),
        matrix=matrix_rc,
        offset=local_offset,
        output_shape=(box_h, box_w),
        order=0,
        mode="constant",
        cval=0.0,
        prefilter=False,
    )
    return warped >= 0.5


def expand_mask_to_patch_warped(
    roi_mask: RoiMask,
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Like `expand_mask_to_patch`, but forward-transformed through
    `affine_matrix` first (see `rasterize_sample`'s mask-geometry warp) -
    the patch-scoped counterpart, bounded by `_mask_reach_box` so only the
    region the warped mask can actually reach is ever touched, never the
    full patch/image regardless of patch size (AGENTS.md's non-negotiable
    invariant - see this module's own docstring for why "warp then crop"
    was rejected instead)."""
    patch_h, patch_w = patch_shape[:2]
    px0, py0 = int(patch_origin_xy[0]), int(patch_origin_xy[1])
    reach_x0, reach_y0, reach_x1, reach_y1 = _mask_reach_box(roi_mask, affine_matrix)
    local_x0 = max(reach_x0 - px0, 0)
    local_x1 = min(reach_x1 - px0, patch_w)
    local_y0 = max(reach_y0 - py0, 0)
    local_y1 = min(reach_y1 - py0, patch_h)
    out = np.zeros((patch_h, patch_w), dtype=bool)
    if local_x0 >= local_x1 or local_y0 >= local_y1:
        return out
    warped_local = _warp_roi_mask_into_box(
        roi_mask, affine_matrix, (px0 + local_x0, py0 + local_y0, px0 + local_x1, py0 + local_y1)
    )
    out[local_y0:local_y1, local_x0:local_x1] = warped_local
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


# -- circle/annulus geometry: reach-box-limited affine rasterization --------
# Moved verbatim from image_tools/chromatic/fitting.py (itself a verbatim
# port of processing/chromatic.py, before fitting.py was later split into
# affine.py/warp.py/landmark_autotrack.py) - only this module's docstring
# context changed, the math did not.


def transformed_circle_points(
    center_xy: tuple[float, float],
    radius_px: float,
    affine_matrix: np.ndarray,
    theta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    center_x, center_y = float(center_xy[0]), float(center_xy[1])
    xs = center_x + float(radius_px) * np.cos(theta)
    ys = center_y + float(radius_px) * np.sin(theta)
    points = np.column_stack((xs, ys)).astype(np.float64, copy=False)
    transformed = apply_affine_to_points(points, affine_matrix)
    return transformed[:, 0], transformed[:, 1]


def transformed_disk_mask(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    return transformed_annulus_mask(image_shape, center_xy, 0.0, radius_px, affine_matrix)


def _annulus_mask_in_box(
    box_x0: int,
    box_y0: int,
    box_x1: int,
    box_y1: int,
    center_xy: tuple[float, float],
    inner_radius: float,
    outer_radius: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Core annulus-mask math for an explicit target-space box [x0:x1, y0:y1]
    (in the same coordinate space `affine_matrix` maps *into*). For every pixel
    in that box, maps back through the inverse affine to source space and
    checks distance from `center_xy` against [inner_radius, outer_radius].
    Returns a mask shaped (box_y1 - box_y0, box_x1 - box_x0).
    """
    box_h, box_w = box_y1 - box_y0, box_x1 - box_x0
    if box_h <= 0 or box_w <= 0:
        return np.zeros((max(box_h, 0), max(box_w, 0)), dtype=bool)
    yy, xx = np.indices((box_h, box_w), dtype=np.float64)
    target_points = np.column_stack((xx.ravel() + box_x0, yy.ravel() + box_y0))
    inverse_affine = invert_affine_matrix(affine_matrix)
    source_points = apply_affine_to_points(target_points, inverse_affine)
    dx = source_points[:, 0] - float(center_xy[0])
    dy = source_points[:, 1] - float(center_xy[1])
    distance_sq = dx * dx + dy * dy
    mask_local = (distance_sq <= outer_radius * outer_radius) & (distance_sq >= inner_radius * inner_radius)
    return mask_local.reshape((box_h, box_w))


def annulus_reach_box(
    center_xy: tuple[float, float],
    outer_radius: float,
    affine_matrix: np.ndarray,
) -> tuple[float, float]:
    """How far (in target space) the transformed annulus can reach from its
    transformed center, and the transformed center itself — shared by the
    full-image and patch-scoped variants so both use the same tight bound.
    """
    linear = np.asarray(affine_matrix[:, :2], dtype=np.float64)
    singular_values = np.linalg.svd(linear, compute_uv=False)
    max_scale = max(float(np.max(singular_values)), 1e-6)
    transformed_center = apply_affine_to_points(np.asarray([[center_xy[0], center_xy[1]]], dtype=np.float64), affine_matrix)[0]
    reach = outer_radius * max_scale + 3.0
    return transformed_center, reach


def transformed_annulus_mask(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_radius_px: float,
    outer_radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    image_height, image_width = image_shape[:2]
    inner_radius = max(float(inner_radius_px), 0.0)
    outer_radius = max(float(outer_radius_px), inner_radius)
    if outer_radius <= 0.0:
        return np.zeros((image_height, image_width), dtype=bool)

    transformed_center, reach = annulus_reach_box(center_xy, outer_radius, affine_matrix)
    x0 = max(int(np.floor(transformed_center[0] - reach)), 0)
    x1 = min(int(np.ceil(transformed_center[0] + reach)) + 1, image_width)
    y0 = max(int(np.floor(transformed_center[1] - reach)), 0)
    y1 = min(int(np.ceil(transformed_center[1] + reach)) + 1, image_height)
    if x0 >= x1 or y0 >= y1:
        return np.zeros((image_height, image_width), dtype=bool)

    mask_local = _annulus_mask_in_box(x0, y0, x1, y1, center_xy, inner_radius, outer_radius, affine_matrix)
    mask = np.zeros((image_height, image_width), dtype=bool)
    mask[y0:y1, x0:x1] = mask_local
    return mask


def transformed_annulus_mask_for_patch(
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_radius_px: float,
    outer_radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Same geometry as transformed_annulus_mask, but scoped to a patch that's
    already been read from a smaller region of the target space (e.g. a
    zarr-chunk-aware partial read around one or more ROIs) — returns a mask
    shaped `patch_shape`, local to `patch_origin_xy`, instead of embedding into
    a full-image-sized array.

    Does its own reach-based shrinking (same `annulus_reach_box` bound
    `transformed_annulus_mask` uses), clipped to the given patch's own
    bounds, rather than computing over the entire patch regardless of the
    ROI's actual size. This matters once the patch can be large relative to
    one ROI - e.g. the OME-Zarr scoped path's per-ROI mask build, where
    `patch_shape` is the union box for *every* selected ROI, not just this
    one - a widely scattered multi-ROI selection can make that patch nearly
    as big as the full image; recomputing every one of hundreds of ROIs'
    individual masks over that whole patch measured at ~48s/wavelength on a
    real 160-ROI dataset (see apps/LSPRi/eva/docs/
    bulk_analysis_performance_investigation.md, "Follow-up #3"). Bounded work
    per ROI regardless of patch size closes that gap.
    """
    patch_h, patch_w = patch_shape[:2]
    inner_radius = max(float(inner_radius_px), 0.0)
    outer_radius = max(float(outer_radius_px), inner_radius)
    mask = np.zeros((patch_h, patch_w), dtype=bool)
    if outer_radius <= 0.0:
        return mask
    px0, py0 = int(patch_origin_xy[0]), int(patch_origin_xy[1])
    transformed_center, reach = annulus_reach_box(center_xy, outer_radius, affine_matrix)
    local_x0 = max(int(np.floor(transformed_center[0] - reach)) - px0, 0)
    local_x1 = min(int(np.ceil(transformed_center[0] + reach)) + 1 - px0, patch_w)
    local_y0 = max(int(np.floor(transformed_center[1] - reach)) - py0, 0)
    local_y1 = min(int(np.ceil(transformed_center[1] + reach)) + 1 - py0, patch_h)
    if local_x0 >= local_x1 or local_y0 >= local_y1:
        return mask
    mask_local = _annulus_mask_in_box(
        px0 + local_x0, py0 + local_y0, px0 + local_x1, py0 + local_y1,
        center_xy, inner_radius, outer_radius, affine_matrix,
    )
    mask[local_y0:local_y1, local_x0:local_x1] = mask_local
    return mask


def transformed_disk_mask_for_patch(
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    center_xy: tuple[float, float],
    radius_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    return transformed_annulus_mask_for_patch(patch_origin_xy, patch_shape, center_xy, 0.0, radius_px, affine_matrix)


# -- per-ROI, per-side dispatcher: routes "circle"/"annulus"/"mask" ---------
# Ported from the inline dispatch in gui/analysis_tasks.py's
# _selected_roi_masks_for_spectrum (the per-ROI body of its loop, not its
# multi-ROI OR-accumulation - that stays analysis/tasks.py's job).


def effective_reference_radii(
    roi: AreaRoi,
    default_inner_radius_px: float,
    default_outer_radius_px: float,
) -> tuple[float, float]:
    """Reference-ring radii to use for one ROI.

    Each ROI may carry its own reference_inner_diameter_px/outer_diameter_px
    (set via the ROI table or the "Edit reference ROI region" dialog) to
    override the shared area_roi_settings default for that ROI only. Falls
    back to the shared default when the ROI has no override.

    Public as of 2026-09-23 (was ``_effective_reference_radii``): the Image
    panel draws the reference ring it is about to measure, so it needs the
    same radii this module rasterizes with. Re-deriving the override rule in
    the panel would mean the drawn ring and the measured ring could disagree
    - which is exactly the class of silent error overlays exist to rule out.
    """
    inner_radius = (
        float(roi.reference_inner_diameter_px) / 2.0
        if roi.reference_inner_diameter_px is not None
        else float(default_inner_radius_px)
    )
    outer_radius = (
        float(roi.reference_outer_diameter_px) / 2.0
        if roi.reference_outer_diameter_px is not None
        else float(default_outer_radius_px)
    )
    inner_radius = max(inner_radius, 0.0)
    outer_radius = max(outer_radius, inner_radius)
    return inner_radius, outer_radius


def rasterize_sample(
    roi: AreaRoi,
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """One ROI's sample-region mask, full-image-sized.

    Mask geometry is re-warped by `affine_matrix` like every other geometry
    type (2026-09-21) - `roi.sample_mask` is authored in the reference
    frame's processed-image space, expanded to full-image size, then
    forward-transformed the same way `transformed_disk_mask` transforms a
    circle's points, so it follows the current wavelength's geometry
    instead of sitting at a fixed pixel location regardless of wavelength.
    """
    if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
        expanded = expand_mask(roi.sample_mask, image_shape)
        return warp_boolean_mask_affine(expanded, affine_matrix, output_shape=image_shape)
    return transformed_disk_mask(
        image_shape, (float(roi.center_x), float(roi.center_y)), float(roi.sample_radius_px), affine_matrix
    )


def rasterize_sample_for_patch(
    roi: AreaRoi,
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Same as `rasterize_sample`, scoped to a patch (see
    `transformed_disk_mask_for_patch`). Mask geometry is now affine-warped
    here too (2026-09-21), via `expand_mask_to_patch_warped`'s own
    reach-box bound - the full-canvas-cost trap this function exists to
    avoid, see this module's own docstring."""
    if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
        return expand_mask_to_patch_warped(roi.sample_mask, patch_origin_xy, patch_shape, affine_matrix)
    return transformed_disk_mask_for_patch(
        patch_origin_xy, patch_shape, (float(roi.center_x), float(roi.center_y)), float(roi.sample_radius_px), affine_matrix
    )


def rasterize_reference(
    roi: AreaRoi,
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    *,
    default_inner_radius_px: float = 0.0,
    default_outer_radius_px: float = 0.0,
) -> np.ndarray:
    """One ROI's reference-region mask, full-image-sized. Empty when
    `reference_geometry_type == "none"` (some ROIs have no reference
    region). See `rasterize_sample` on mask geometry's affine-warp.
    """
    image_height, image_width = image_shape[:2]
    if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
        expanded = expand_mask(roi.reference_mask, image_shape)
        return warp_boolean_mask_affine(expanded, affine_matrix, output_shape=image_shape)
    if roi.reference_geometry_type == "none":
        return np.zeros((image_height, image_width), dtype=bool)
    inner_radius, outer_radius = effective_reference_radii(roi, default_inner_radius_px, default_outer_radius_px)
    if outer_radius <= 0.0:
        return np.zeros((image_height, image_width), dtype=bool)
    return transformed_annulus_mask(
        image_shape, (float(roi.center_x), float(roi.center_y)), inner_radius, outer_radius, affine_matrix
    )


def rasterize_reference_for_patch(
    roi: AreaRoi,
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    *,
    default_inner_radius_px: float = 0.0,
    default_outer_radius_px: float = 0.0,
) -> np.ndarray:
    """Same as `rasterize_reference`, scoped to a patch. Mask geometry is
    affine-warped here too - see `rasterize_sample_for_patch`."""
    patch_h, patch_w = patch_shape[:2]
    if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
        return expand_mask_to_patch_warped(roi.reference_mask, patch_origin_xy, patch_shape, affine_matrix)
    if roi.reference_geometry_type == "none":
        return np.zeros((patch_h, patch_w), dtype=bool)
    inner_radius, outer_radius = effective_reference_radii(roi, default_inner_radius_px, default_outer_radius_px)
    if outer_radius <= 0.0:
        return np.zeros((patch_h, patch_w), dtype=bool)
    return transformed_annulus_mask_for_patch(
        patch_origin_xy, patch_shape, (float(roi.center_x), float(roi.center_y)), inner_radius, outer_radius, affine_matrix
    )


# -- §6a fractional pixel weighting (built 2026-09-22) ----------------------
# One shared engine (_reach_box_coverage) for every geometry type, per
# AGENTS.md's "don't reach for shape-specific exact-intersection formulas" -
# only the `point_test` callable plugged into it differs per geometry.


def _reach_box_coverage(
    box: tuple[int, int, int, int],
    affine_matrix: np.ndarray,
    supersample_factor: int,
    point_test: Callable[[np.ndarray, np.ndarray], np.ndarray],
) -> np.ndarray:
    """§6a's shared supersample-and-downsample engine. Generalizes
    `_annulus_mask_in_box`'s own pattern - map target-space points back
    through the inverse affine to native/source space and test them there -
    from one sample point per output pixel (a pixel's center) to
    `supersample_factor ** 2` evenly-spaced sub-pixel samples per pixel,
    averaged into a [0, 1] coverage fraction. Returns an array shaped
    `(box_y1 - box_y0, box_x1 - box_x0)`.

    `point_test` is the only thing that differs per geometry type: a
    distance-from-center formula for circle/annulus
    (`_circle_annulus_point_test`), a nearest-neighbor lookup into the
    stored `RoiMask` array for mask geometry (`_mask_point_test`) - the
    sampling/averaging/reach-box-bounding machinery itself is identical
    either way, so a new geometry type only ever needs a new `point_test`,
    never a new copy of this function.

    Because `affine_matrix` is linear, an evenly-spaced sub-pixel grid in
    target space maps to an evenly-spaced (just skewed/scaled) grid in
    source space - so this stays exactly correct for any shear/anisotropic-
    scale affine, not just similarity transforms. That matters here: the
    real chromatic fit (`fit_affine_matrix`, ordinary-least-squares over
    matched landmarks) is an unconstrained 6-parameter affine, so a circle
    can genuinely warp into an ellipse in target space - this is why the
    per-pixel-sample-then-inverse-map approach is used instead of the
    simpler-looking "draw a same-radius circle at the transformed center",
    which would be wrong whenever the fit has any shear or anisotropic
    scale.
    """
    box_x0, box_y0, box_x1, box_y1 = box
    box_h, box_w = box_y1 - box_y0, box_x1 - box_x0
    if box_h <= 0 or box_w <= 0:
        return np.zeros((max(box_h, 0), max(box_w, 0)), dtype=np.float32)
    n = max(int(supersample_factor), 1)
    ss_h, ss_w = box_h * n, box_w * n
    ss_yy, ss_xx = np.indices((ss_h, ss_w), dtype=np.float64)
    # Sub-sample (J + 0.5) / n is the closed form of "pixel j's k-th of n
    # evenly-spaced sub-offsets, (k + 0.5) / n" for J = j * n + k - avoids
    # building/broadcasting a separate per-pixel offset grid.
    target_x = box_x0 + (ss_xx.ravel() + 0.5) / n
    target_y = box_y0 + (ss_yy.ravel() + 0.5) / n
    target_points = np.column_stack((target_x, target_y))
    inverse_affine = invert_affine_matrix(affine_matrix)
    source_points = apply_affine_to_points(target_points, inverse_affine)
    covered = point_test(source_points[:, 0], source_points[:, 1]).reshape(ss_h, ss_w)
    # Average-pool n x n sub-samples back to one coverage fraction per output
    # pixel - same reshape-mean technique as background/estimate.py's
    # _bin_array_mean, just downsampling a coverage grid instead of an image.
    coverage = covered.reshape(box_h, n, box_w, n).mean(axis=(1, 3), dtype=np.float64)
    return coverage.astype(np.float32, copy=False)


def _circle_annulus_point_test(
    center_xy: tuple[float, float],
    inner_radius: float,
    outer_radius: float,
) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """Native-space point test for `_reach_box_coverage`: is
    `(source_x, source_y)` within `[inner_radius, outer_radius]` of
    `center_xy`? Same formula `_annulus_mask_in_box` uses per-pixel-center,
    evaluated per sub-sample here instead. `inner_radius=0.0` gives a plain
    disk (the sample side's geometry).
    """
    center_x, center_y = float(center_xy[0]), float(center_xy[1])
    inner_sq = float(inner_radius) * float(inner_radius)
    outer_sq = float(outer_radius) * float(outer_radius)

    def point_test(source_x: np.ndarray, source_y: np.ndarray) -> np.ndarray:
        dx = source_x - center_x
        dy = source_y - center_y
        distance_sq = dx * dx + dy * dy
        return (distance_sq <= outer_sq) & (distance_sq >= inner_sq)

    return point_test


def _mask_point_test(roi_mask: RoiMask) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """Native-space point test for `_reach_box_coverage`, mask geometry: is
    `(source_x, source_y)` - rounded to the nearest stored mask pixel -
    `True` in `roi_mask.mask`? A mask has no continuous boundary beyond its
    own stored pixels (unlike circle/annulus's exact formula), so
    "coverage" here reflects how many of a target pixel's back-projected
    sub-samples land on a `True` source pixel, nearest-neighbor - not a
    claim of sub-pixel precision the stored mask never had. Out-of-bounds
    sub-samples (outside `roi_mask`'s own small stored array) count as not
    covered, same as `expand_mask`'s implicit zero-fill.
    """
    mask = roi_mask.mask
    mask_h, mask_w = mask.shape[:2]
    origin_x, origin_y = int(roi_mask.x0), int(roi_mask.y0)

    def point_test(source_x: np.ndarray, source_y: np.ndarray) -> np.ndarray:
        col = np.round(source_x - origin_x).astype(np.int64)
        row = np.round(source_y - origin_y).astype(np.int64)
        in_bounds = (row >= 0) & (row < mask_h) & (col >= 0) & (col < mask_w)
        covered = np.zeros(source_x.shape, dtype=bool)
        covered[in_bounds] = mask[row[in_bounds], col[in_bounds]]
        return covered

    return point_test


def rasterize_fractional(
    roi: AreaRoi,
    side: str,
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    supersample_factor: int = 8,
    *,
    default_inner_radius_px: float = 0.0,
    default_outer_radius_px: float = 0.0,
) -> np.ndarray:
    """§6a fractional pixel weighting: one ROI's sample- or reference-region
    coverage, full-image-sized, with values in `[0, 1]` instead of
    `rasterize_sample`/`rasterize_reference`'s plain boolean - 1.0 where a
    pixel is fully inside the ROI, 0.0 fully outside, and a fraction at
    whichever edge the affine-warped boundary cuts through. Works for
    whichever geometry `roi`'s `side` ("sample"/"reference") actually has -
    circle, annulus, or mask - by building the matching `point_test` and
    handing it to the shared `_reach_box_coverage` engine; see that
    function's docstring for why one engine suffices for every geometry
    type instead of a per-shape rewrite.

    Reach-box-bounded internally (never supersamples beyond where the shape
    can possibly reach, AGENTS.md's non-negotiable invariant) even though
    the *returned* array is full-`image_shape`-sized - matching
    `transformed_annulus_mask`'s own full-size-output-but-bounded-cost
    pattern, not a new cost profile. A patch-scoped (`_for_patch`) variant,
    mirroring `rasterize_sample_for_patch`, is deliberately not built here -
    nothing calls this yet (no caller exists until `analysis/tasks.py` is
    built), so a second variant would be speculative; add one once a real
    caller needs it.
    """
    image_height, image_width = image_shape[:2]
    geometry_type = roi.sample_geometry_type if side == "sample" else roi.reference_geometry_type
    if side == "reference" and geometry_type == "none":
        return np.zeros((image_height, image_width), dtype=np.float32)

    if geometry_type == "mask":
        roi_mask = roi.sample_mask if side == "sample" else roi.reference_mask
        if roi_mask is None:
            return np.zeros((image_height, image_width), dtype=np.float32)
        reach_x0, reach_y0, reach_x1, reach_y1 = _mask_reach_box(roi_mask, affine_matrix)
        box = (
            max(reach_x0, 0), max(reach_y0, 0),
            min(reach_x1, image_width), min(reach_y1, image_height),
        )
        point_test = _mask_point_test(roi_mask)
    else:
        center_xy = (float(roi.center_x), float(roi.center_y))
        if side == "sample":
            inner_radius, outer_radius = 0.0, float(roi.sample_radius_px)
        else:
            inner_radius, outer_radius = effective_reference_radii(roi, default_inner_radius_px, default_outer_radius_px)
        if outer_radius <= 0.0:
            return np.zeros((image_height, image_width), dtype=np.float32)
        transformed_center, reach = annulus_reach_box(center_xy, outer_radius, affine_matrix)
        box = (
            max(int(np.floor(transformed_center[0] - reach)), 0),
            max(int(np.floor(transformed_center[1] - reach)), 0),
            min(int(np.ceil(transformed_center[0] + reach)) + 1, image_width),
            min(int(np.ceil(transformed_center[1] + reach)) + 1, image_height),
        )
        point_test = _circle_annulus_point_test(center_xy, inner_radius, outer_radius)

    box_x0, box_y0, box_x1, box_y1 = box
    if box_x0 >= box_x1 or box_y0 >= box_y1:
        return np.zeros((image_height, image_width), dtype=np.float32)
    coverage_local = _reach_box_coverage(box, affine_matrix, supersample_factor, point_test)
    coverage = np.zeros((image_height, image_width), dtype=np.float32)
    coverage[box_y0:box_y1, box_x0:box_x1] = coverage_local
    return coverage
