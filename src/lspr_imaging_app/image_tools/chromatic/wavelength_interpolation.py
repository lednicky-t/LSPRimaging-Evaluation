"""Fit chromatic transforms at a few sampled wavelengths, interpolate the
rest - the piece `ChromaticModule.refit()`'s own docstring named as not yet
extracted, now built as part of actually implementing `refit()`
(2026-09-21).

Ported from the old app's `gui/analysis_tasks.py` (`_estimate_chromatic_
models_task`'s `mode == "landmark_radial"` branch, its only live branch -
see the rewrite build log's chromatic-fitting file-split entry) and
`_sampled_wavelengths`/`_normalized_odd_count`, kept as pure functions with
no Qt/worker/dataset dependency (AGENTS.md testing rule) - `ChromaticModule.
refit()` supplies already-resolved landmarks/wavelengths and owns the
undo/signal/model-storage side of things.

**The core idea**: fitting a transform at every wavelength in a cube is
unnecessary and slow - chromatic aberration varies smoothly with
wavelength, so fitting only a handful of evenly-spaced *sampled*
wavelengths (where the user has marked landmark points) and linearly
interpolating each fitted matrix's coefficients across every other
wavelength is both cheap and accurate enough. The reference wavelength
need not itself be one of the sampled/marked wavelengths - fitting anchors
on whichever sampled wavelength is closest to the reference instead, and
every result (whether directly fit or itself interpolated) is re-expressed
relative to the true reference by composing with a reference<->anchor
transform (`compose_affine_matrices`) - the ordinary "translate a
measurement between two arbitrary basepoints" trick, needing no assumption
that the reference itself was ever directly measured.
"""

from __future__ import annotations

import numpy as np

from .affine import apply_affine_to_points, compose_affine_matrices, fit_affine_matrix, fit_similarity_matrix, invert_affine_matrix, identity_affine_matrix


def normalized_odd_count(value: int, minimum: int, maximum: int) -> int:
    """Clamp `value` into `[minimum, maximum]`, rounding up to the nearest
    odd number when possible (an odd sample count always has a true middle
    wavelength, used as the default anchor-proximity tiebreak)."""
    normalized = max(int(value), int(minimum))
    if normalized % 2 == 0:
        normalized += 1
    if normalized > int(maximum):
        normalized = int(maximum)
        if normalized % 2 == 0:
            normalized = max(int(minimum), normalized - 1)
    return max(normalized, int(minimum))


def sampled_wavelengths(wavelengths_nm: list[float], sample_count: int) -> list[float]:
    """Pick an evenly-spaced subset of `wavelengths_nm` (already sorted, no
    duplicates expected) of size `normalized_odd_count(sample_count, ...)`,
    always including the first and last. A single-wavelength input always
    returns that one wavelength."""
    if not wavelengths_nm:
        return []
    maximum = len(wavelengths_nm)
    minimum = 1 if maximum == 1 else min(3, maximum)
    count = min(normalized_odd_count(sample_count, minimum, maximum), maximum)
    if count % 2 == 0:
        count = max(1, count - 1)
    if count == 1:
        return [float(wavelengths_nm[len(wavelengths_nm) // 2])]
    indices = [int(round(index * (maximum - 1) / (count - 1))) for index in range(count)]
    indices = sorted(dict.fromkeys(indices))
    return [float(wavelengths_nm[index]) for index in indices]


def fit_wavelength_transforms(
    landmarks_by_wavelength: dict[float, dict[int, tuple[float, float]]],
    sample_wavelengths: list[float],
    expected_feature_ids: list[int],
    reference_wavelength: float,
    target_wavelengths: list[float],
    *,
    use_similarity: bool,
) -> dict[float, tuple[np.ndarray, float, int]]:
    """Fit at `sample_wavelengths`, interpolate/compose across every
    `target_wavelengths` entry, re-anchored onto `reference_wavelength`.

    `landmarks_by_wavelength` must already be filtered to the reference
    cube's own landmarks only (the caller's job - a landmark marked on a
    different cube is meaningless here, matching the old app's own
    `spectral_cube_index != reference_spectral_cube: continue` filter).

    Returns `{wavelength: (affine_matrix, rmse_px, feature_count)}` for
    every entry in `target_wavelengths`. Raises `ValueError` if no sample
    wavelength has every `expected_feature_ids` marked (nothing to anchor
    on), or if any individual sample wavelength is missing points once
    landmark-marking has started - matching the old app's exact two error
    cases and messages.
    """
    if not sample_wavelengths:
        raise ValueError("No sample wavelengths available - load a dataset with at least one narrowband wavelength.")

    complete_sample_wavelengths = [
        wavelength
        for wavelength in sample_wavelengths
        if all(feature_id in landmarks_by_wavelength.get(float(wavelength), {}) for feature_id in expected_feature_ids)
    ]
    if not complete_sample_wavelengths:
        raise ValueError(
            f"Mark all {len(expected_feature_ids)} reference points on at least one sampled wavelength image "
            "before estimating chromatic transforms."
        )
    anchor_wavelength = float(
        min(complete_sample_wavelengths, key=lambda wavelength: abs(float(wavelength) - reference_wavelength))
    )
    anchor_landmarks = landmarks_by_wavelength[anchor_wavelength]
    anchor_points = np.asarray([anchor_landmarks[feature_id] for feature_id in expected_feature_ids], dtype=np.float64)

    sample_matrices: dict[float, np.ndarray] = {}
    sample_rmse: dict[float, float] = {}
    for wavelength in sample_wavelengths:
        marks = landmarks_by_wavelength.get(float(wavelength), {})
        missing = [feature_id for feature_id in expected_feature_ids if feature_id not in marks]
        if missing:
            raise ValueError(
                f"Sample wavelength {wavelength:g} nm is missing reference point(s): "
                + ", ".join(str(feature_id) for feature_id in missing)
            )
        if abs(float(wavelength) - anchor_wavelength) < 1e-6:
            matrix = identity_affine_matrix()
            rmse = 0.0
        else:
            target_points = np.asarray([marks[feature_id] for feature_id in expected_feature_ids], dtype=np.float64)
            if use_similarity and len(expected_feature_ids) >= 2:
                matrix = fit_similarity_matrix(anchor_points, target_points)
            else:
                matrix = fit_affine_matrix(anchor_points, target_points)
            residuals = np.sqrt(np.sum((apply_affine_to_points(anchor_points, matrix) - target_points) ** 2, axis=1))
            rmse = float(np.sqrt(np.mean(residuals**2))) if residuals.size else 0.0
        sample_matrices[float(wavelength)] = matrix
        sample_rmse[float(wavelength)] = rmse

    sorted_sample_wavelengths = sorted(sample_matrices)
    sample_axis = np.asarray(sorted_sample_wavelengths, dtype=np.float64)
    matrix_values_array = np.asarray([sample_matrices[w] for w in sorted_sample_wavelengths], dtype=np.float64)
    rmse_values = np.asarray([sample_rmse[w] for w in sorted_sample_wavelengths], dtype=np.float64)

    def anchor_relative_matrix(wavelength: float) -> np.ndarray:
        if wavelength in sample_matrices:
            return sample_matrices[wavelength]
        interpolated = np.empty((2, 3), dtype=np.float64)
        for row in range(2):
            for col in range(3):
                interpolated[row, col] = float(np.interp(wavelength, sample_axis, matrix_values_array[:, row, col]))
        return interpolated

    # The one genuinely new interpolation this scheme needs: the anchor's
    # own transform *to* the true reference wavelength, estimated the same
    # way any other non-sampled wavelength's transform already is (or read
    # off directly, if the reference happens to coincide with a sampled
    # wavelength).
    reference_to_anchor = invert_affine_matrix(anchor_relative_matrix(float(reference_wavelength)))

    results: dict[float, tuple[np.ndarray, float, int]] = {}
    for wavelength in target_wavelengths:
        wavelength_f64 = float(wavelength)
        anchor_relative = anchor_relative_matrix(wavelength_f64)
        matrix = compose_affine_matrices(anchor_relative, reference_to_anchor)
        if wavelength_f64 in sample_matrices:
            rmse = sample_rmse[wavelength_f64]
            feature_count = len(expected_feature_ids)
        else:
            rmse = float(np.interp(wavelength_f64, sample_axis, rmse_values))
            feature_count = len(expected_feature_ids)
        results[wavelength_f64] = (matrix, rmse, feature_count)
    return results
