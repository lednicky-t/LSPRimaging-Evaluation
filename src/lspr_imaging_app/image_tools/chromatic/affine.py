"""Core point-based affine/similarity fitting - the actual "points in, matrix
out" math the whole Chromatic correction feature is built on.

Deliberately dependency-free (only `numpy` + `roi.model.AreaRoi`'s type):
no image registration, no landmark detection, no Qt. Given a handful of
matched `(source, target)` point pairs (however they were obtained - a
human clicking landmarks, or `landmark_autotrack.py`'s automatic finder),
these functions fit, apply, invert, and chain the 2x3 affine transform that
maps one wavelength's pixel space onto another's, and move `AreaRoi`
centers through it. `warp.py` is the pixel-array counterpart (apply the
same matrix to an image/mask instead of a handful of points).

Split out of the former single `fitting.py` (2026-09-21, maintainer's
request - the "several genuinely independent functions" observation) -
see the rewrite build log for the reasoning behind the file split and for
what specifically moved where.
"""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    # Type-only: roi/toolbox.py now imports this module directly
    # (2026-09-21, display_position()), and roi/__init__.py imports
    # toolbox.py - a real (not just type-checking-time) import here would
    # be circular (roi -> toolbox -> chromatic.affine -> roi.model ->
    # (needs the roi package, already mid-init) -> ImportError). Safe to
    # defer to TYPE_CHECKING only: AreaRoi is never used as a runtime
    # value in this file (`from __future__ import annotations` already
    # means the type hint itself is never evaluated), only as a type
    # annotation on transform_rois_affine's `rois` parameter.
    from ...roi.model import AreaRoi


def identity_affine_matrix() -> np.ndarray:
    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)


def fit_affine_matrix(source_points_xy: np.ndarray, target_points_xy: np.ndarray) -> np.ndarray:
    """Ordinary-least-squares affine fit (rotation + scale + shear + translation)
    through matched point pairs.

    Each output coordinate (target x, target y) is an independent linear
    combination of (source x, source y, 1), solved by least squares
    (`np.linalg.lstsq`) rather than an exact solve, so it works cleanly with
    more than the minimum 3 point pairs -- extra, noisy correspondences
    average out rather than making the system unsolvable. Returns a 2x3
    matrix `[[a, b, tx], [c, d, ty]]` such that
    `target = matrix @ [source_x, source_y, 1]`.
    """
    design = np.column_stack((source_points_xy[:, 0], source_points_xy[:, 1], np.ones(source_points_xy.shape[0])))
    coeff_x, _, _, _ = np.linalg.lstsq(design, target_points_xy[:, 0], rcond=None)
    coeff_y, _, _, _ = np.linalg.lstsq(design, target_points_xy[:, 1], rcond=None)
    return np.vstack((coeff_x, coeff_y)).astype(np.float64, copy=False)


def apply_affine_to_points(points_xy: np.ndarray, affine_matrix: np.ndarray) -> np.ndarray:
    """Map `points_xy` (N x 2) through the 2x3 affine `matrix @ [x, y, 1]`."""
    if points_xy.size == 0:
        return points_xy.astype(np.float64, copy=True)
    design = np.column_stack((points_xy[:, 0], points_xy[:, 1], np.ones(points_xy.shape[0], dtype=np.float64)))
    return design @ affine_matrix.T


def affine_residuals(source_points_xy: np.ndarray, target_points_xy: np.ndarray, affine_matrix: np.ndarray) -> np.ndarray:
    """Per-point distance (px) between `affine_matrix @ source` and `target` -- the
    fit-quality/outlier-rejection metric used throughout this module."""
    predicted = apply_affine_to_points(source_points_xy, affine_matrix)
    return np.sqrt(np.sum((predicted - target_points_xy) ** 2, axis=1))


def invert_affine_matrix(affine_matrix: np.ndarray) -> np.ndarray:
    """Invert a 2x3 affine matrix (linear part + translation), so
    `apply_affine_to_points(apply_affine_to_points(p, m), invert_affine_matrix(m)) == p`.
    """
    linear = np.asarray(affine_matrix[:, :2], dtype=np.float64)
    translation = np.asarray(affine_matrix[:, 2], dtype=np.float64)
    inverse_linear = np.linalg.inv(linear)
    inverse_translation = -inverse_linear @ translation
    return np.column_stack((inverse_linear, inverse_translation))


def compose_affine_matrices(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """Chain two 2x3 affines into the single matrix equivalent to applying
    `inner` first, then `outer`:
    `apply_affine_to_points(p, compose_affine_matrices(outer, inner))
    == apply_affine_to_points(apply_affine_to_points(p, inner), outer)`.

    Standard affine composition: if `inner(x) = B x + b` and `outer(x) = A x + a`,
    then `outer(inner(x)) = A(Bx + b) + a = (AB) x + (Ab + a)`. Used to re-anchor a
    chain of landmark-fitted transforms onto a wavelength that was never itself
    landmark-marked -- e.g. `compose_affine_matrices(anchor_to_target, reference_to_anchor)`
    turns a landmark-fitted "anchor -> target" transform into a "true reference ->
    target" one, via a reference<->anchor transform obtained separately (typically
    itself interpolated, since the reference wavelength need not be landmark-marked).
    """
    outer_linear = np.asarray(outer[:, :2], dtype=np.float64)
    outer_translation = np.asarray(outer[:, 2], dtype=np.float64)
    inner_linear = np.asarray(inner[:, :2], dtype=np.float64)
    inner_translation = np.asarray(inner[:, 2], dtype=np.float64)
    composed_linear = outer_linear @ inner_linear
    composed_translation = outer_linear @ inner_translation + outer_translation
    return np.column_stack((composed_linear, composed_translation))


def fit_similarity_matrix(source_points_xy: np.ndarray, target_points_xy: np.ndarray) -> np.ndarray:
    """Least-squares fit of a *similarity* transform (uniform scale + rotation +
    translation only -- no shear or independent x/y scale) through matched
    point pairs.

    Implements Umeyama's closed-form solution (Umeyama, S. "Least-squares
    estimation of transformation parameters between two point patterns."
    IEEE Trans. Pattern Anal. Mach. Intell. 13(4), 376-380, 1991): center
    both point sets, take the SVD of their cross-covariance, and read the
    optimal rotation off `V @ U.T` (flipping the last singular vector if
    that rotation has determinant < 0, which would mean a reflection rather
    than a rotation); the optimal scale is the sum of singular values
    divided by the source points' variance.

    Used for the "radial"/landmark-based correction mode, where a handful of
    user-picked points should only ever imply a rigid-plus-zoom transform,
    not an arbitrary shear -- so a small number of noisy landmarks can't
    accidentally warp the image. Raises `ValueError` with fewer than 2 point
    pairs, or if the source points are degenerate (all coincident).
    """
    if source_points_xy.shape[0] < 2 or target_points_xy.shape[0] < 2:
        raise ValueError("At least two landmark pairs are required for the radial landmark model.")
    source = np.asarray(source_points_xy, dtype=np.float64)
    target = np.asarray(target_points_xy, dtype=np.float64)
    source_mean = np.mean(source, axis=0)
    target_mean = np.mean(target, axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean
    covariance = source_centered.T @ target_centered / max(source.shape[0], 1)
    u, singular_values, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1.0
        rotation = vt.T @ u.T
    source_variance = float(np.mean(np.sum(source_centered**2, axis=1)))
    if source_variance <= 1e-12:
        raise ValueError("Landmarks are degenerate and cannot define a radial transform.")
    scale = float(np.sum(singular_values) / source_variance)
    linear = scale * rotation
    translation = target_mean - linear @ source_mean
    return np.column_stack((linear, translation))


def decompose_similarity_matrix(affine_matrix: np.ndarray) -> tuple[float, float, float, float]:
    """Read a similarity matrix's `(scale, angle_rad, shift_x_px, shift_y_px)`
    back out of its 2x3 form -- the inverse of `compose_similarity_matrix`,
    used to show a human-editable scale/rotation/shift in the GUI instead of
    raw matrix coefficients.
    """
    matrix = np.asarray(affine_matrix, dtype=np.float64)
    linear = matrix[:, :2]
    scale_x = float(np.hypot(linear[0, 0], linear[1, 0]))
    scale_y = float(np.hypot(linear[0, 1], linear[1, 1]))
    scale = max((scale_x + scale_y) * 0.5, 1e-12)
    angle_rad = float(np.arctan2(linear[1, 0], linear[0, 0]))
    return scale, angle_rad, float(matrix[0, 2]), float(matrix[1, 2])


def compose_similarity_matrix(scale: float, angle_rad: float, shift_x_px: float, shift_y_px: float) -> np.ndarray:
    """Build a 2x3 similarity matrix from `(scale, angle_rad, shift_x_px,
    shift_y_px)` -- the inverse of `decompose_similarity_matrix`."""
    cos_angle = float(np.cos(angle_rad))
    sin_angle = float(np.sin(angle_rad))
    return np.array(
        [
            [float(scale) * cos_angle, -float(scale) * sin_angle, float(shift_x_px)],
            [float(scale) * sin_angle, float(scale) * cos_angle, float(shift_y_px)],
        ],
        dtype=np.float64,
    )


def transform_rois_affine(
    rois: list[AreaRoi],
    affine_matrix: np.ndarray,
    *,
    clamp_shape: tuple[int, int] | None = None,
) -> list[AreaRoi]:
    if not rois:
        return []
    source_points = np.asarray([(roi.center_x, roi.center_y) for roi in rois], dtype=np.float64)
    target_points = apply_affine_to_points(source_points, affine_matrix)
    linear = np.asarray(affine_matrix[:, :2], dtype=np.float64)
    singular_values = np.linalg.svd(linear, compute_uv=False)
    scale = float(np.max(singular_values))
    scale = max(scale, 1e-6)
    transformed: list[AreaRoi] = []
    max_x = float(clamp_shape[1] - 1) if clamp_shape is not None else None
    max_y = float(clamp_shape[0] - 1) if clamp_shape is not None else None
    for index, roi in enumerate(rois):
        target_x = float(target_points[index, 0])
        target_y = float(target_points[index, 1])
        # Shallow copy, not deepcopy: only center_x/center_y/sample_radius_px
        # get reassigned below (rebinding a scalar attribute on the copy
        # never touches the original), and every caller of this function's
        # result (ROI overlay rendering, rois_for_preprocessing's exclusion
        # mask) only ever reads those three fields off the transformed
        # copy - never per_wavelength, which stays shared by reference and
        # is never mutated through it. Deep-copying it here on every ROI on
        # every call, just to discard the copy unused, was a real cost when
        # per_wavelength held one entry per (cube, wavelength) in the whole
        # dataset under the old eager-population design; it's just a handful
        # of manual nudges now, but the shallow copy is still the right call.
        transformed_roi = copy(roi)
        transformed_roi.center_x = target_x
        transformed_roi.center_y = target_y
        transformed_roi.sample_radius_px = max(float(roi.sample_radius_px) * scale, 1.0)
        if max_x is not None and max_y is not None:
            transformed_roi.center_x = float(np.clip(transformed_roi.center_x, 0.0, max_x))
            transformed_roi.center_y = float(np.clip(transformed_roi.center_y, 0.0, max_y))
        transformed.append(transformed_roi)
    return transformed
