"""ROI mask rasterization - the one dispatcher for every geometry type
(circle/annulus/mask today; §6a fractional weighting extends the same
dispatch, not a per-shape rewrite).

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
That left
"which module rasterizes what" ambiguous once ``roi_rasterize.py`` turned
out to only handle the arbitrary-mask escape hatch. Chosen fix: pull the
circle/annulus math (``transformed_circle_points``, ``transformed_disk_mask``,
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

Mask-geometry ROIs are **not** re-warped by the chromatic affine transform
here, matching the current app's own documented limitation (a mask sits at
the same absolute pixel location for every wavelength; AGENTS.md's "masks
are forward-transformed via Chromatic's warp_mask()" invariant describes the
target state for a *future* chromatic-corrected-mask feature, not something
this port silently adds - see the inline note on ``rasterize_sample``).
"""

from __future__ import annotations

import numpy as np

from ..image_tools.chromatic.affine import apply_affine_to_points, invert_affine_matrix
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


def _effective_reference_radii(
    roi: AreaRoi,
    default_inner_radius_px: float,
    default_outer_radius_px: float,
) -> tuple[float, float]:
    """Reference-ring radii to use for one ROI.

    Each ROI may carry its own reference_inner_diameter_px/outer_diameter_px
    (set via the ROI table or the "Edit reference ROI region" dialog) to
    override the shared area_roi_settings default for that ROI only. Falls
    back to the shared default when the ROI has no override.
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

    Mask geometry is not re-warped by `affine_matrix` — it sits at the same
    absolute pixel location for every wavelength, matching the current app's
    documented behavior (fine for the current opt-in use of "mask" geometry;
    revisit via Chromatic's `warp_mask()` if chromatic-corrected arbitrary
    masks are needed).
    """
    if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
        return expand_mask(roi.sample_mask, image_shape)
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
    `transformed_disk_mask_for_patch`)."""
    if roi.sample_geometry_type == "mask" and roi.sample_mask is not None:
        return expand_mask_to_patch(roi.sample_mask, patch_origin_xy, patch_shape)
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
    region). See `rasterize_sample` on why mask geometry isn't affine-warped.
    """
    image_height, image_width = image_shape[:2]
    if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
        return expand_mask(roi.reference_mask, image_shape)
    if roi.reference_geometry_type == "none":
        return np.zeros((image_height, image_width), dtype=bool)
    inner_radius, outer_radius = _effective_reference_radii(roi, default_inner_radius_px, default_outer_radius_px)
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
    """Same as `rasterize_reference`, scoped to a patch."""
    patch_h, patch_w = patch_shape[:2]
    if roi.reference_geometry_type == "mask" and roi.reference_mask is not None:
        return expand_mask_to_patch(roi.reference_mask, patch_origin_xy, patch_shape)
    if roi.reference_geometry_type == "none":
        return np.zeros((patch_h, patch_w), dtype=bool)
    inner_radius, outer_radius = _effective_reference_radii(roi, default_inner_radius_px, default_outer_radius_px)
    if outer_radius <= 0.0:
        return np.zeros((patch_h, patch_w), dtype=bool)
    return transformed_annulus_mask_for_patch(
        patch_origin_xy, patch_shape, (float(roi.center_x), float(roi.center_y)), inner_radius, outer_radius, affine_matrix
    )


# -- §6a fractional pixel weighting: genuinely new work, not a port ---------


def rasterize_fractional(
    roi: AreaRoi,
    side: str,
    image_shape: tuple[int, int],
    affine_matrix: np.ndarray,
    supersample_factor: int = 8,
    **kwargs: object,
) -> np.ndarray:
    """§6a fractional pixel weighting via supersample-and-downsample, for
    whichever geometry `roi`'s `side` ("sample"/"reference") actually has -
    circle, annulus, or mask. One shared implementation for every geometry
    type (AGENTS.md §6a: don't reach for shape-specific exact-intersection
    formulas). Not yet implemented - scaffolding only.
    """
    raise NotImplementedError
