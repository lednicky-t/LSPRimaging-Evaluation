"""ROI mask rasterization - the one dispatcher for every geometry type
(circle/annulus/mask), plus `rasterize_fractional` (§6a fractional pixel
weighting, built 2026-09-22) - a coverage-weighted variant of the same
dispatch via one shared supersample-and-downsample engine, not a per-shape
rewrite.

No Qt import allowed in this file (CLAUDE.md testing rule). CLAUDE.md
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
surface per CLAUDE.md's "Module boundaries") - this module never imports or
calls into Chromatic itself, keeping the boundary one-directional.

``rasterize_sample``/``rasterize_reference`` (and their ``_for_patch``
variants) are the real per-ROI, per-side dispatchers, ported from the inline
``if the sample is a mask ... else transformed_disk_mask(...)`` (since 2026-11-18 routed by ``AreaRoi.shape``)
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
path that exists specifically to avoid that (CLAUDE.md's non-negotiable
invariant: "cache per-ROI analysis masks at that ROI's own small bounding
box, never full-image-plane size - full-size caching measured 8-14GB RAM
at realistic ROI counts"). Built instead: `_mask_reach_box` (the
arbitrary-mask analogue of `annulus_reach_box` below, transforming the
stored `RoiMask`'s bounding-box corners through `affine_matrix` rather
than a circle's radius) bounds how far the warped result can reach in
target space, and `_warp_roi_mask_into_box`/`expand_mask_to_patch_warped`
warp only within that bound, reading directly from `roi_mask.mask`'s own
small array - the full source/target canvases are never materialized.

**2026-11-18 (phase 2.2 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``):** the full-plane ``rasterize_sample`` of a mask no longer
warps a full canvas either; every non-circular region (a rectangle, a mask, a border) is found inside its own box by ``_region_hits``, and the
scan asks for flat pixel indices straight from it (``sample_flat_indices`` / ``reference_flat_indices``), so the scan and the drawing use one
set of pixels. ``expand_mask_to_patch_warped`` (the box warp's old wrapper) is gone: ``_region_hits`` does its job.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence

import numpy as np
from scipy import ndimage

from ..image_tools.chromatic.affine import apply_affine_to_points, invert_affine_matrix
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.geometry.transform import combined_geometry_affine_xy
from ..progress import Cancelled
from .mask_regions import EIGHT_CONNECTED
from ..domain.roi_model import AreaRoi, RoiMask
from ..domain.roi_shapes import (
    MASK_BORDER_SLACK_PX,
    NO_BORDER,
    Border,
    Circle,
    MaskShape,
    Rectangle,
    effective_border,
    ring_diameters,
    ring_radii,
)

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


# -- mask geometry: re-anchor to a new rotation/flip/crop (2026-09-28) ------
# `_mask_reach_box`/`_warp_roi_mask_into_box` were built for the *chromatic*
# per-frame affine; a geometry (rotate/flip/crop) edit needs the identical
# "warp a small cropped mask through a forward affine, without ever
# materializing a full-canvas array" operation, just with a different matrix
# (`combined_geometry_affine_xy`, old-processed -> new-processed) - so it
# reuses both helpers rather than re-implementing the warp.


def remap_roi_mask(
    roi_mask: RoiMask,
    raw_shape: tuple[int, int],
    old_settings: GeometrySettings,
    new_settings: GeometrySettings,
) -> RoiMask | None:
    """`roi_mask`, re-expressed in NEW-processed-space after a rotation/flip/
    crop edit that changed `old_settings` to `new_settings` - the mask-
    geometry counterpart of
    `image_tools.geometry.transform.remap_point_for_geometry_change` for an
    ROI's ``center_x``/``center_y``. `box` (from `_mask_reach_box`) is
    already in NEW-processed space and is not clamped to `raw_shape` here -
    that would be a dimension mismatch (raw-space bounds against a
    processed-space box); like every other `RoiMask`-producing path in this
    module, a box that ends up partially or fully outside the visible image
    is left for the point of use to clip, not clipped at creation
    (`_blit`'s own "clip, don't crash" convention) - so an extreme crop
    offset alone never triggers the `None` case below; it still returns a
    valid, correctly-positioned mask, however far outside the new crop that
    position is (verified: 200 randomized rotation/flip/crop combinations,
    including wildly-offset crops, never produced `None` - see the rewrite
    build log's matching entry).

    Returns `None` only when the affine-warped mask has no True pixels left
    at all. **This is a defensive guard, not a realistic outcome of a plain
    geometry edit**: rotation/flip here are always orthogonal maps and crop
    only ever contributes a translation (see `combined_geometry_affine_xy`)
    - that composition is bijective and (up to ~1px boundary rounding, not
    zero) area-preserving, so a non-empty mask was not observed to ever warp
    to nothing in testing (not even a single-pixel mask, across 179
    rotation angles). Kept anyway as a cheap safety net, and reported rather
    than silently returned as an all-False mask - which would read as "no
    exclusion" instead of "this needs attention" - should some future change
    to this pipeline (e.g. a scale factor) ever make it reachable; the
    caller (`RoiToolbox.remap_all`) surfaces that to the user.

    Re-crops the warped result to its own true bounding box afterward (like
    `crop_mask`, but offset by `box`'s own origin rather than assuming
    (0, 0)), so the mask stays a *small* array anchored near wherever it
    moved to, never the reach box's own (deliberately padded) extent."""
    matrix = combined_geometry_affine_xy(raw_shape, old_settings, new_settings)
    box = _mask_reach_box(roi_mask, matrix)
    box_x0, box_y0, box_x1, box_y1 = box
    warped = _warp_roi_mask_into_box(roi_mask, matrix, box)
    yy, xx = np.nonzero(warped)
    if xx.size == 0:
        return None
    x0, x1 = int(xx.min()), int(xx.max()) + 1
    y0, y1 = int(yy.min()), int(yy.max()) + 1
    return RoiMask(x0=box_x0 + x0, y0=box_y0 + y0, mask=warped[y0:y1, x0:x1].copy())


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
# affine.py/warp.py/landmark_autotrack.py, the last since removed) - only this module's docstring
# context changed, the math did not.


def transformed_circle_points(
    center_xy: tuple[float, float],
    diameter_px: float,
    affine_matrix: np.ndarray,
    theta: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    center_x, center_y = float(center_xy[0]), float(center_xy[1])
    radius = float(diameter_px) / 2.0
    xs = center_x + radius * np.cos(theta)
    ys = center_y + radius * np.sin(theta)
    points = np.column_stack((xs, ys)).astype(np.float64, copy=False)
    transformed = apply_affine_to_points(points, affine_matrix)
    return transformed[:, 0], transformed[:, 1]


def transformed_disk_mask(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    diameter_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    return transformed_annulus_mask(image_shape, center_xy, 0.0, diameter_px, affine_matrix)


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


def _annulus_in_plane_box(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_diameter_px: float,
    outer_diameter_px: float,
    affine_matrix: np.ndarray,
) -> tuple[int, int, int, int, np.ndarray] | None:
    """The annulus (or disk, inner = 0) mask inside its own reach box only:
    ``(x0, y0, x1, y1, local_mask)`` with the box clipped to the image, or
    `None` when the shape is empty or entirely outside the image. Shared by
    `transformed_annulus_mask` (which embeds it into a full plane) and
    `union_roi_masks` (which ORs it straight into a shared plane)."""
    image_height, image_width = image_shape[:2]
    inner_radius = max(float(inner_diameter_px), 0.0) / 2.0
    outer_radius = max(float(outer_diameter_px) / 2.0, inner_radius)
    if outer_radius <= 0.0:
        return None

    transformed_center, reach = annulus_reach_box(center_xy, outer_radius, affine_matrix)
    x0 = max(int(np.floor(transformed_center[0] - reach)), 0)
    x1 = min(int(np.ceil(transformed_center[0] + reach)) + 1, image_width)
    y0 = max(int(np.floor(transformed_center[1] - reach)), 0)
    y1 = min(int(np.ceil(transformed_center[1] + reach)) + 1, image_height)
    if x0 >= x1 or y0 >= y1:
        return None
    return x0, y0, x1, y1, _annulus_mask_in_box(x0, y0, x1, y1, center_xy, inner_radius, outer_radius, affine_matrix)


def transformed_annulus_mask(
    image_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_diameter_px: float,
    outer_diameter_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    image_height, image_width = image_shape[:2]
    mask = np.zeros((image_height, image_width), dtype=bool)
    placed = _annulus_in_plane_box(image_shape, center_xy, inner_diameter_px, outer_diameter_px, affine_matrix)
    if placed is not None:
        x0, y0, x1, y1, mask_local = placed
        mask[y0:y1, x0:x1] = mask_local
    return mask


def transformed_annulus_mask_for_patch(
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    center_xy: tuple[float, float],
    inner_diameter_px: float,
    outer_diameter_px: float,
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
    inner_radius = max(float(inner_diameter_px), 0.0) / 2.0
    outer_radius = max(float(outer_diameter_px) / 2.0, inner_radius)
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
    diameter_px: float,
    affine_matrix: np.ndarray,
) -> np.ndarray:
    return transformed_annulus_mask_for_patch(patch_origin_xy, patch_shape, center_xy, 0.0, diameter_px, affine_matrix)


# -- per-ROI, per-side dispatcher: routes by the ROI's shape (circle / rectangle / mask) and its border ---------------------------------
# A circle goes through the exact code above (its ring radii come from `roi_shapes.ring_radii`, the one place they are computed). A
# rectangle, and the border of a rectangle or of a mask, are found by the signed-distance code below (phase 2 of
# docs/roi_shapes_and_linking_plan_2026-11-18.md): the same pattern as the annulus (map the pixels of a target-space box back through the
# inverse affine to source space, test them there), with the test "signed distance from the shape's edge" instead of "distance from the
# centre". A mask's own region (the sample side) keeps the scipy warp above.


def effective_reference_diameters(roi: AreaRoi, default_border: Border) -> tuple[float, float]:
    """Border-ring (inner, outer) diameters of a *circular* ROI: its own border, else the shared default. Public because the Image panel
    and the ROI table show the ring as diameters, from the very numbers this module rasterizes with (so the drawn ring and the measured
    ring cannot disagree). Raises ``ValueError`` for a ROI that is not a circle: only a circle's border is a ring."""
    if not isinstance(roi.shape, Circle):
        raise ValueError("ring diameters exist only for circular ROIs")
    return ring_diameters(roi.shape, effective_border(roi.border, default_border))


def _signed_distance_rectangle(sx: np.ndarray, sy: np.ndarray, cx: float, cy: float, rect: Rectangle) -> np.ndarray:
    """Distance of the points from the rectangle's edge, outwards positive (0 on the edge, negative inside). Exact: the outside part is the
    Euclidean distance to the rectangle, so its outer corners are rounded."""
    angle = np.deg2rad(float(rect.angle_deg))
    cos, sin = np.cos(angle), np.sin(angle)
    dx, dy = sx - cx, sy - cy
    u = dx * cos + dy * sin  # along the rectangle's own x axis
    v = dy * cos - dx * sin
    qx = np.abs(u) - float(rect.width_px) / 2.0
    qy = np.abs(v) - float(rect.height_px) / 2.0
    return np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) + np.minimum(np.maximum(qx, qy), 0.0)


@functools.lru_cache(maxsize=512)  # one entry per mask ROI (~20 KB for a ROI-sized one). 64 thrashed at 160 masks: an EDT per call, 169 vs 99 ms per layout
def _mask_sdf(x0: int, y0: int, mask_bytes: bytes, height: int, width: int, pad: int) -> tuple[np.ndarray, int, int]:
    """Signed distance from a mask's edge at every pixel of its bounding box padded by ``pad`` pixels, and the source position of that
    array's pixel (0, 0). The edge lies half a pixel from the pixel centres: outside a pixel at centre distance ``d`` from the nearest mask
    pixel is ``d - 0.5`` from the edge, inside it is ``0.5 - d`` (``d`` = distance to the nearest pixel that is not in the mask). Exact along
    the axes, a slight over-estimate of the true distance to the pixel staircase along diagonals. Cached: the mask does not change between
    the frames of a scan."""
    padded = np.pad(np.frombuffer(mask_bytes, dtype=bool).reshape(height, width), pad, constant_values=False)
    sdf = np.where(padded, 0.5 - ndimage.distance_transform_edt(padded), ndimage.distance_transform_edt(~padded) - 0.5)
    sdf.flags.writeable = False
    return sdf, x0 - pad, y0 - pad


@functools.lru_cache(maxsize=512)
def _mask_band(x0: int, y0: int, mask_bytes: bytes, height: int, width: int, pad: int, gap: float, thickness: float) -> tuple[np.ndarray, int, int]:
    """The rim of a pixel map: a bool array over the map's bounding box padded by ``pad`` pixels, and the source position of its pixel (0, 0).

    **Every blob has a rim of its own** (maintainer, 2026-11-19): a pixel is in the rim when its distance from the edge of *some* blob is between ``gap`` and
    ``gap + thickness`` (the distance of `_mask_sdf`, measured from that blob alone), and it is not a pixel of another blob. The rims of the blobs
    together are the one reference region of the ROI. Measured from the map as a whole instead, a pixel that lies in blob A's band but near blob B would
    be dropped; per blob it is kept. A map of one blob (or none) is exactly what it was: the distance map of the whole map, cut at the same two numbers.
    Blobs are 8-connected (`mask_regions.EIGHT_CONNECTED`). Cached, with the distance map under it: the map does not change between the frames of a scan."""
    padded = np.pad(np.frombuffer(mask_bytes, dtype=bool).reshape(height, width), pad, constant_values=False)
    labels, count = ndimage.label(padded, structure=EIGHT_CONNECTED)
    if count <= 1:
        sdf, _, _ = _mask_sdf(x0, y0, mask_bytes, height, width, pad)
        band = (sdf >= gap) & (sdf <= gap + thickness)
    else:
        band = np.zeros(padded.shape, dtype=bool)
        for number, window in enumerate(ndimage.find_objects(labels), start=1):
            if window is None:
                continue
            rows = slice(max(window[0].start - pad, 0), min(window[0].stop + pad, padded.shape[0]))  # the blob's box and the rim's reach: nothing farther can be in its rim
            cols = slice(max(window[1].start - pad, 0), min(window[1].stop + pad, padded.shape[1]))
            blob = labels[rows, cols] == number
            sdf = np.where(blob, 0.5 - ndimage.distance_transform_edt(blob), ndimage.distance_transform_edt(~blob) - 0.5)
            hit = (sdf >= gap) & (sdf <= gap + thickness)
            hit &= ~(padded[rows, cols] & ~blob)  # a pixel of another blob is that blob's own, never this blob's rim
            band[rows, cols] |= hit
    band.flags.writeable = False
    return band, x0 - pad, y0 - pad


def _mask_band_at(mask: RoiMask, border: Border, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Which (source-space) points lie in the rim of a pixel map (`_mask_band`), read at the nearest stored pixel; ``False`` beyond the padded box."""
    height, width = mask.mask.shape[:2]
    band, origin_x, origin_y = _mask_band(
        int(mask.x0), int(mask.y0), np.ascontiguousarray(mask.mask, dtype=bool).tobytes(), int(height), int(width),
        int(np.ceil(_border_extent(border))) + 2, float(border.gap_px), float(border.thickness_px),
    )
    col = np.round(sx - origin_x).astype(np.int64)
    row = np.round(sy - origin_y).astype(np.int64)
    inside = (row >= 0) & (row < band.shape[0]) & (col >= 0) & (col < band.shape[1])
    out = np.zeros(sx.shape, dtype=bool)
    out[inside] = band[row[inside], col[inside]]
    return out


def _signed_distance_mask(mask: RoiMask, extra: float, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Signed distance of the (source-space) points from a mask's edge, nearest stored pixel; ``+inf`` farther than ``extra`` outside the mask."""
    height, width = mask.mask.shape[:2]
    sdf, origin_x, origin_y = _mask_sdf(
        int(mask.x0), int(mask.y0), np.ascontiguousarray(mask.mask, dtype=bool).tobytes(), int(height), int(width), int(np.ceil(extra)) + 2
    )
    col = np.round(sx - origin_x).astype(np.int64)
    row = np.round(sy - origin_y).astype(np.int64)
    inside = (row >= 0) & (row < sdf.shape[0]) & (col >= 0) & (col < sdf.shape[1])
    out = np.full(sx.shape, np.inf)
    out[inside] = sdf[row[inside], col[inside]]
    return out


def _border_extent(border: Border) -> float:
    """How far outside the shape's edge a border reaches (pixels)."""
    return max(float(border.gap_px) + float(border.thickness_px), 0.0) if border.enabled else 0.0


def _region_test(roi: AreaRoi, region: str, border: Border, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Which source-space points (flat arrays) belong to the ROI's ``region`` (``"sample"`` or ``"border"``): the shape's own region, or the
    points whose signed distance from its edge is between the border's gap and gap + thickness (both inclusive). Rectangles and masks
    only: a circle's region is found by its own, exact, squared-distance code."""
    shape = roi.shape
    cx, cy = float(roi.center_x), float(roi.center_y)
    if isinstance(shape, Rectangle):
        distance = _signed_distance_rectangle(sx, sy, cx, cy, shape)
    elif isinstance(shape, MaskShape):
        if region == "border":
            return _mask_band_at(shape.mask, border, sx, sy)  # a rim of its own for every blob, not a band from the whole map
        distance = _signed_distance_mask(shape.mask, 0.0, sx, sy)
    else:
        raise TypeError(f"_region_test serves rectangles and masks, not {type(shape).__name__}")
    if region == "sample":
        return distance <= 0.0
    gap = float(border.gap_px)
    return (distance >= gap) & (distance <= gap + float(border.thickness_px))


def region_source_bounds(roi: AreaRoi, region: str, border: Border) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` in source space that holds the ROI's ``region``."""
    shape = roi.shape
    extra = _border_extent(border) if region == "border" else 0.0
    cx, cy = float(roi.center_x), float(roi.center_y)
    if isinstance(shape, Rectangle):
        angle = np.deg2rad(float(shape.angle_deg))
        half_w, half_h = float(shape.width_px) / 2.0, float(shape.height_px) / 2.0
        half_x = abs(half_w * np.cos(angle)) + abs(half_h * np.sin(angle))
        half_y = abs(half_w * np.sin(angle)) + abs(half_h * np.cos(angle))
        return cx - half_x - extra, cy - half_y - extra, cx + half_x + extra, cy + half_y + extra
    if isinstance(shape, MaskShape):
        height, width = shape.mask.mask.shape[:2]
        x0, y0 = float(shape.mask.x0) - 0.5, float(shape.mask.y0) - 0.5
        if region == "border":  # its distance map is read at whole pixels: a border pixel can lie up to MASK_BORDER_SLACK_PX beyond (found 2026-11-18)
            extra += MASK_BORDER_SLACK_PX
        return x0 - extra, y0 - extra, x0 + width + extra, y0 + height + extra
    radius = max(float(shape.diameter_px), 0.0) / 2.0 + extra
    return cx - radius, cy - radius, cx + radius, cy + radius


def _region_reach_box(roi: AreaRoi, region: str, affine_matrix: np.ndarray, border: Border) -> tuple[int, int, int, int]:
    """Target-space box ``(x0, y0, x1, y1)`` (end exclusive, not clipped to anything) that holds the ROI's ``region`` after the affine: the
    box of the transformed corners of its source-space bounds, plus a few pixels of safety margin."""
    if region == "sample" and isinstance(roi.shape, MaskShape):
        return _mask_reach_box(roi.shape.mask, affine_matrix)  # a mask's own region is warped, not measured (see `_region_in_box`)
    sx0, sy0, sx1, sy1 = region_source_bounds(roi, region, border)
    corners = np.array([[sx0, sy0], [sx1, sy0], [sx0, sy1], [sx1, sy1]], dtype=np.float64)
    moved = apply_affine_to_points(corners, affine_matrix)
    x_min, y_min = moved.min(axis=0)
    x_max, y_max = moved.max(axis=0)
    return int(np.floor(x_min)) - 2, int(np.floor(y_min)) - 2, int(np.ceil(x_max)) + 3, int(np.ceil(y_max)) + 3


def _region_in_box(roi: AreaRoi, region: str, box: tuple[int, int, int, int], affine_matrix: np.ndarray, border: Border) -> np.ndarray:
    """The ROI's ``region`` over the pixels of the target-space ``box`` (``(x0, y0, x1, y1)``): each pixel centre is mapped back through the
    inverse affine and tested in source space. Shaped ``(y1 - y0, x1 - x0)``."""
    x0, y0, x1, y1 = box
    height, width = y1 - y0, x1 - x0
    if height <= 0 or width <= 0:
        return np.zeros((max(height, 0), max(width, 0)), dtype=bool)
    if region == "sample" and isinstance(roi.shape, MaskShape):
        # A mask's own region: its stored pixels warped through the affine, nearest neighbour (the scipy call every mask ROI has used). The patch
        # variants always did this inside a box; the full-plane `rasterize_sample` now does too (2026-11-18, phase 2.2), so one place decides.
        return _warp_roi_mask_into_box(roi.shape.mask, affine_matrix, box)
    yy, xx = np.indices((height, width), dtype=np.float64)
    source = apply_affine_to_points(np.column_stack((xx.ravel() + x0, yy.ravel() + y0)), invert_affine_matrix(affine_matrix))
    return _region_test(roi, region, border, source[:, 0], source[:, 1]).reshape((height, width))


def _region_hits(
    roi: AreaRoi, region: str, window: tuple[int, int, int, int], affine_matrix: np.ndarray, border: Border
) -> tuple[int, int, np.ndarray]:
    """``(x0, y0, hits)``: the ROI's ``region`` inside ``window`` (``(x0, y0, x1, y1)``, end exclusive: the image, or a patch of it), as a bool
    box whose top-left pixel is ``(x0, y0)``. Only the part of the window the region can reach is computed, never the whole window (the
    invariant at the top of this module). ``hits`` is empty (zero-sized) when the region misses the window."""
    wx0, wy0, wx1, wy1 = window
    bx0, by0, bx1, by1 = _region_reach_box(roi, region, affine_matrix, border)
    x0, y0, x1, y1 = max(bx0, wx0), max(by0, wy0), min(bx1, wx1), min(by1, wy1)
    if x0 >= x1 or y0 >= y1:
        return wx0, wy0, np.zeros((0, 0), dtype=bool)
    return x0, y0, _region_in_box(roi, region, (x0, y0, x1, y1), affine_matrix, border)


def _place_region(
    roi: AreaRoi, region: str, origin_xy: tuple[int, int], shape: tuple[int, int], affine_matrix: np.ndarray, border: Border
) -> np.ndarray:
    """The ROI's ``region`` as a bool array of ``shape`` that starts at ``origin_xy`` in the image (a whole plane: origin 0, 0; or a patch)."""
    out = np.zeros((int(shape[0]), int(shape[1])), dtype=bool)
    ox, oy = int(origin_xy[0]), int(origin_xy[1])
    x0, y0, hits = _region_hits(roi, region, (ox, oy, ox + out.shape[1], oy + out.shape[0]), affine_matrix, border)
    if hits.size:
        out[y0 - oy:y0 - oy + hits.shape[0], x0 - ox:x0 - ox + hits.shape[1]] = hits
    return out


def _flat_indices(roi: AreaRoi, region: str, image_shape: tuple[int, int], affine_matrix: np.ndarray, border: Border) -> np.ndarray:
    """Row-major flat indices (into an ``image_shape`` image, ascending, int64) of the pixels of the ROI's ``region``: exactly
    ``np.flatnonzero(<the full-plane mask of that region>)``, made without ever creating the plane."""
    height, width = int(image_shape[0]), int(image_shape[1])
    x0, y0, hits = _region_hits(roi, region, (0, 0, width, height), affine_matrix, border)
    if not hits.size:
        return np.zeros(0, dtype=np.int64)
    rows, cols = np.nonzero(hits)  # row-major, so ascending in the image too
    return (rows.astype(np.int64) + y0) * width + (cols.astype(np.int64) + x0)


def sample_flat_indices(roi: AreaRoi, image_shape: tuple[int, int], affine_matrix: np.ndarray) -> np.ndarray:
    """The pixels of ``rasterize_sample(roi, image_shape, affine_matrix)`` as ascending row-major flat indices (int64), found inside the ROI's
    own box: what the scan wants for a rectangle or a mask, without a full plane per ROI (a plane is 1.2 MB at 1.2 MP, ~1 ms to scan for its
    ``True`` pixels). A circle goes through its plane, as it always did (the scan finds circles all at once, `frame_reduce.locate_pixels`)."""
    if isinstance(roi.shape, Circle):
        return np.flatnonzero(rasterize_sample(roi, image_shape, affine_matrix)).astype(np.int64, copy=False)
    return _flat_indices(roi, "sample", image_shape, affine_matrix, NO_BORDER)


def reference_flat_indices(
    roi: AreaRoi, image_shape: tuple[int, int], affine_matrix: np.ndarray, *, default_border: Border = NO_BORDER
) -> np.ndarray:
    """The pixels of ``rasterize_reference(roi, image_shape, affine_matrix, default_border=...)`` as ascending flat indices (see
    :func:`sample_flat_indices`). Empty when the ROI's border is switched off."""
    border = effective_border(roi.border, default_border)
    if not border.enabled:
        return np.zeros(0, dtype=np.int64)
    if isinstance(roi.shape, Circle):
        return np.flatnonzero(rasterize_reference(roi, image_shape, affine_matrix, default_border=default_border)).astype(np.int64, copy=False)
    return _flat_indices(roi, "border", image_shape, affine_matrix, border)


def _or_region(plane: np.ndarray, roi: AreaRoi, region: str, affine_matrix: np.ndarray, border: Border) -> None:
    """OR the ROI's ``region`` into the full-image bool ``plane``, touching only the region's own box (a rectangle or a mask)."""
    height, width = plane.shape[:2]
    x0, y0, hits = _region_hits(roi, region, (0, 0, width, height), affine_matrix, border)
    if hits.size:
        plane[y0:y0 + hits.shape[0], x0:x0 + hits.shape[1]] |= hits


def region_contains(roi: AreaRoi, region: str, border: Border, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """Which points ``(sx, sy)`` (arrays, **source space**, pixel-index coordinates, the space ROI centres and masks are stored in) lie in the
    ROI's ``region`` (``"sample"`` or ``"border"``) of any shape. For hit-testing a point the user pointed at (the display point is first pulled
    back through the frame's affine); a mask is looked up at the nearest stored pixel. A border needs ``border.enabled``: a ROI without one
    contains nothing of it."""
    sx, sy = np.asarray(sx, dtype=np.float64), np.asarray(sy, dtype=np.float64)
    shape = roi.shape
    if region == "border" and not border.enabled:
        return np.zeros(sx.shape, dtype=bool)
    if isinstance(shape, Circle):
        distance = np.hypot(sx - float(roi.center_x), sy - float(roi.center_y))
        radius = max(float(shape.diameter_px), 0.0) / 2.0
        if region == "sample":
            return distance <= radius
        inner, outer = ring_radii(shape, border)
        return (distance >= inner) & (distance <= outer)
    return _region_test(roi, region, border, sx.ravel(), sy.ravel()).reshape(sx.shape)


def mask_border_pixels(mask: RoiMask, border: Border) -> tuple[int, int, np.ndarray]:
    """``(x0, y0, band)``: the pixels of a mask's border in the mask's own (source) pixel grid, a bool array whose top-left pixel is ``(x0, y0)``.
    Exactly the pixels `rasterize_reference` finds under the identity affine; what the overlay draws for a mask's border. Empty (zero-sized)
    for a border that is off."""
    if not border.enabled:
        return int(mask.x0), int(mask.y0), np.zeros((0, 0), dtype=bool)
    height, width = mask.mask.shape[:2]
    pad = int(np.ceil(_border_extent(border))) + 2
    band, origin_x, origin_y = _mask_band(
        int(mask.x0), int(mask.y0), np.ascontiguousarray(mask.mask, dtype=bool).tobytes(), int(height), int(width), pad,
        float(border.gap_px), float(border.thickness_px),
    )
    return origin_x, origin_y, band  # read only and shared: a caller copies before changing it


def _ring_diameters_of(circle: Circle, border: Border) -> tuple[float, float]:
    inner, outer = ring_radii(circle, border)
    return inner * 2.0, outer * 2.0  # a power of two: the annulus code halves them again exactly


def rasterize_sample(
    roi: AreaRoi,
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """One ROI's sample-region mask, full-image-sized.

    A mask is re-warped by `affine_matrix` like every other shape (2026-09-21): ``shape.mask`` is authored in the reference frame's
    processed-image space and forward-transformed, so it follows the current wavelength's geometry instead of sitting at a fixed pixel
    location regardless of wavelength. Since 2026-11-18 (phase 2.2) the warp is made inside the mask's own box (the patch variant always was),
    not on a full-image canvas: the same pixels for any ordinary affine (pinned by ``tests/unit/test_lspri_scan_circle_golden.py``), except
    that a mask touching the image edge keeps the pixels scipy's full-canvas call used to drop at the border, and that an affine whose
    shift is an exact half pixel breaks the resulting rounding ties the other way (neither is a measured case)."""
    shape = roi.shape
    if isinstance(shape, MaskShape):
        return _place_region(roi, "sample", (0, 0), image_shape[:2], affine_matrix, NO_BORDER)
    if isinstance(shape, Circle):
        return transformed_disk_mask(image_shape, (float(roi.center_x), float(roi.center_y)), float(shape.diameter_px), affine_matrix)
    return _place_region(roi, "sample", (0, 0), image_shape[:2], affine_matrix, NO_BORDER)


def rasterize_sample_for_patch(
    roi: AreaRoi,
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    affine_matrix: np.ndarray,
) -> np.ndarray:
    """Same as `rasterize_sample`, scoped to a patch (see `transformed_disk_mask_for_patch`); a rectangle or a mask is found inside its own
    reach box, so the full-canvas cost this function exists to avoid is never paid."""
    shape = roi.shape
    if isinstance(shape, Circle):
        return transformed_disk_mask_for_patch(
            patch_origin_xy, patch_shape, (float(roi.center_x), float(roi.center_y)), float(shape.diameter_px), affine_matrix
        )
    return _place_region(roi, "sample", patch_origin_xy, patch_shape[:2], affine_matrix, NO_BORDER)


def rasterize_reference(
    roi: AreaRoi,
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    *,
    default_border: Border = NO_BORDER,
) -> np.ndarray:
    """One ROI's built-in reference region (its border), full-image-sized. Empty when the ROI's border is switched off (``NO_BORDER``; a ROI
    without its own border uses `default_border`, which is itself ``NO_BORDER`` unless the caller says otherwise).

    A circle's border is the ring ``R + g .. R + g + t`` around the centre; a rectangle's or a mask's is the band of points whose distance
    from the shape's edge is between ``g`` and ``g + t`` (see `roi_shapes`)."""
    border = effective_border(roi.border, default_border)
    height, width = image_shape[:2]
    if not border.enabled:
        return np.zeros((height, width), dtype=bool)
    shape = roi.shape
    if isinstance(shape, Circle):
        inner_diameter, outer_diameter = _ring_diameters_of(shape, border)
        if outer_diameter <= 0.0:
            return np.zeros((height, width), dtype=bool)
        return transformed_annulus_mask(
            image_shape, (float(roi.center_x), float(roi.center_y)), inner_diameter, outer_diameter, affine_matrix
        )
    return _place_region(roi, "border", (0, 0), (height, width), affine_matrix, border)


def rasterize_reference_for_patch(
    roi: AreaRoi,
    patch_origin_xy: tuple[int, int],
    patch_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    *,
    default_border: Border = NO_BORDER,
) -> np.ndarray:
    """Same as `rasterize_reference`, scoped to a patch."""
    border = effective_border(roi.border, default_border)
    patch_h, patch_w = patch_shape[:2]
    if not border.enabled:
        return np.zeros((patch_h, patch_w), dtype=bool)
    shape = roi.shape
    if isinstance(shape, Circle):
        inner_diameter, outer_diameter = _ring_diameters_of(shape, border)
        if outer_diameter <= 0.0:
            return np.zeros((patch_h, patch_w), dtype=bool)
        return transformed_annulus_mask_for_patch(
            patch_origin_xy, patch_shape, (float(roi.center_x), float(roi.center_y)), inner_diameter, outer_diameter, affine_matrix
        )
    return _place_region(roi, "border", patch_origin_xy, (patch_h, patch_w), affine_matrix, border)


def union_roi_masks(
    rois: Sequence[AreaRoi],
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    *,
    default_border: Border = NO_BORDER,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """``(sample, reference)``: the union of every ROI's sample region and of every ROI's border, each a full-image bool plane.

    Gives exactly what OR-ing `rasterize_sample` / `rasterize_reference` over `rois` gives, but a circle or ring is rasterized only inside
    its own reach box and ORed into the shared plane. The one-at-a-time form allocates and ORs two full planes per ROI (4 MB each at
    2048 x 2048), which measured 4.2 s for 1000 ROIs on the GUI thread (Histogram panel, 2026-10-07). Rectangles and masks are ORed inside their own
    box too since 2026-11-18 (the same pixels as their planes: `_region_hits` is what `rasterize_*` use).

    `cancelled` is checked once per ROI; when it returns true this raises `progress.Cancelled` (CLAUDE.md: heavy work can be cancelled)."""
    image_height, image_width = image_shape[:2]
    sample = np.zeros((image_height, image_width), dtype=bool)
    reference = np.zeros((image_height, image_width), dtype=bool)
    for roi in rois:
        if cancelled is not None and cancelled():
            raise Cancelled()
        shape = roi.shape
        if isinstance(shape, Circle):
            placed = _annulus_in_plane_box(
                image_shape, (float(roi.center_x), float(roi.center_y)), 0.0, float(shape.diameter_px), affine_matrix
            )
            if placed is not None:
                x0, y0, x1, y1, local = placed
                sample[y0:y1, x0:x1] |= local
        else:
            _or_region(sample, roi, "sample", affine_matrix, NO_BORDER)

        border = effective_border(roi.border, default_border)
        if not border.enabled:
            continue
        if isinstance(shape, Circle):
            inner, outer = _ring_diameters_of(shape, border)
            if outer > 0.0:
                placed = _annulus_in_plane_box(image_shape, (float(roi.center_x), float(roi.center_y)), inner, outer, affine_matrix)
                if placed is not None:
                    x0, y0, x1, y1, local = placed
                    reference[y0:y1, x0:x1] |= local
        else:
            _or_region(reference, roi, "border", affine_matrix, border)
    return sample, reference


# -- §6a fractional pixel weighting (built 2026-09-22) ----------------------
# One shared engine (_reach_box_coverage) for every geometry type, per
# CLAUDE.md's "don't reach for shape-specific exact-intersection formulas" -
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
    default_border: Border = NO_BORDER,
) -> np.ndarray:
    """§6a fractional pixel weighting: one ROI's sample- or reference-region
    coverage, full-image-sized, with values in `[0, 1]` instead of
    `rasterize_sample`/`rasterize_reference`'s plain boolean - 1.0 where a
    pixel is fully inside the ROI, 0.0 fully outside, and a fraction at
    whichever edge the affine-warped boundary cuts through. Works for
    whichever shape `roi` has (circle, rectangle, mask) and for its border,
    by building the matching `point_test` and handing it to the shared
    `_reach_box_coverage` engine; see that function's docstring for why one
    engine suffices for every geometry type instead of a per-shape rewrite.

    Reach-box-bounded internally (never supersamples beyond where the shape
    can possibly reach, CLAUDE.md's non-negotiable invariant) even though
    the *returned* array is full-`image_shape`-sized - matching
    `transformed_annulus_mask`'s own full-size-output-but-bounded-cost
    pattern, not a new cost profile. A patch-scoped (`_for_patch`) variant,
    mirroring `rasterize_sample_for_patch`, is deliberately not built here -
    nothing calls this yet (no caller exists until `analysis/tasks.py` is
    built), so a second variant would be speculative; add one once a real
    caller needs it.
    """
    image_height, image_width = image_shape[:2]
    shape = roi.shape
    border = effective_border(roi.border, default_border)
    region = "sample" if side == "sample" else "border"
    if region == "border" and not border.enabled:
        return np.zeros((image_height, image_width), dtype=np.float32)

    if isinstance(shape, MaskShape) and region == "sample":
        reach_x0, reach_y0, reach_x1, reach_y1 = _mask_reach_box(shape.mask, affine_matrix)
        box = (
            max(reach_x0, 0), max(reach_y0, 0),
            min(reach_x1, image_width), min(reach_y1, image_height),
        )
        point_test = _mask_point_test(shape.mask)
    elif isinstance(shape, Circle):
        center_xy = (float(roi.center_x), float(roi.center_y))
        if region == "sample":
            inner_radius, outer_radius = 0.0, float(shape.diameter_px) / 2.0
        else:
            inner_radius, outer_radius = ring_radii(shape, border)
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
    else:  # a rectangle, or a mask's border: the signed-distance test
        reach_x0, reach_y0, reach_x1, reach_y1 = _region_reach_box(roi, region, affine_matrix, border)
        box = (max(reach_x0, 0), max(reach_y0, 0), min(reach_x1, image_width), min(reach_y1, image_height))

        def point_test(source_x: np.ndarray, source_y: np.ndarray) -> np.ndarray:
            return _region_test(roi, region, border, source_x, source_y)

    box_x0, box_y0, box_x1, box_y1 = box
    if box_x0 >= box_x1 or box_y0 >= box_y1:
        return np.zeros((image_height, image_width), dtype=np.float32)
    coverage_local = _reach_box_coverage(box, affine_matrix, supersample_factor, point_test)
    coverage = np.zeros((image_height, image_width), dtype=np.float32)
    coverage[box_y0:box_y1, box_x0:box_x1] = coverage_local
    return coverage
