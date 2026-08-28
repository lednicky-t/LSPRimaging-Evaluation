"""[λ] mode's cube-invariant chromatic-geometry sharing - computing the
per-wavelength chromatic affine matrix and the resulting scoped-read box
once from a reference cube, instead of recomputing the same numbers per
cube. Mixed into AnalysisController (see analysis_controller.py's class
declaration) - `self` here is the AnalysisController instance, so these
methods use the same `self.window` state as the rest of the class. Split
out on its own because this cluster is genuinely self-contained: every
method here only calls other methods in this same file (plus `self.window`
for pixel/geometry lookups that are genuinely MainWindow's), and its only
caller from the rest of AnalysisController is `_start_sensorgram_worker`
(in analysis_worker_mixin.py).
"""

from __future__ import annotations

import logging

import numpy as np

from lspr_imaging_app.domain.exclusions import is_excluded
from lspr_imaging_app.domain.models import AreaRoi
from lspr_imaging_app.processing.chromatic import warp_boolean_mask_affine
from lspr_imaging_app.gui.analysis_types import SpectrumSettingsSnapshot, SharedWavelengthGeometry


class AnalysisChromaticGeometryMixin:
    @staticmethod
    def _affine_maps_match(a: dict[float, object], b: dict[float, object]) -> bool:
        if set(a.keys()) != set(b.keys()):
            return False
        for wavelength, matrix_a in a.items():
            matrix_b = b[wavelength]
            if (matrix_a is None) != (matrix_b is None):
                return False
            if matrix_a is not None and not np.allclose(matrix_a, matrix_b, atol=1e-9):
                return False
        return True

    def _affine_matrix_by_wavelength_for_cube(self, spectral_cube_index: int) -> dict[float, object] | None:
        result: dict[float, object] = {}
        for wavelength in self.window._wavelength_values:
            record = self.window._record_map.get((spectral_cube_index, wavelength))
            if record is None or is_excluded(self.window._state.image_exclusions, spectral_cube_index, wavelength):
                continue
            affine_matrix = self.window._chromatic_affine_for_image_key((spectral_cube_index, float(wavelength)))
            result[float(wavelength)] = None if affine_matrix is None else np.asarray(affine_matrix, dtype=np.float64)
        return result or None

    def _diagnose_shared_wavelength_geometry(
        self,
        spectral_cubes: list[int],
        affine_by_wavelength: dict[float, object],
    ) -> bool | None:
        """Reports - never gates - whether the reference cube's (`spectral_cubes[0]`)
        per-wavelength chromatic geometry actually matches the last cube in
        the run. This used to also decide whether [λ] mode's shared-geometry
        shortcut was used at all, falling back to per-cube computation on a
        mismatch - removed at the maintainer's request: in
        practice this app's chromatic correction is wavelength-only by
        construction (see SharedWavelengthGeometry's docstring), and the
        gate could reject a run for reasons unrelated to the transform
        itself actually differing - e.g. the reference cube and the check
        cube simply not sharing the exact same set of excluded/missing
        wavelengths, which trips the "same wavelengths present" half of the
        comparison even when every wavelength they *do* share agrees
        perfectly. Kept as a diagnostic (logged) so a real mismatch is still
        visible if it ever happens, without silently blocking the mode.
        Returns True/False for the comparison result, or None if there was
        only one cube (nothing to compare) or the check cube had no usable
        geometry of its own to compare against.
        """
        if len(spectral_cubes) <= 1:
            return None
        reference_cube = int(spectral_cubes[0])
        check_cube = int(spectral_cubes[-1])
        check_affine = self._affine_matrix_by_wavelength_for_cube(check_cube)
        if check_affine is None:
            return None
        matches = self._affine_maps_match(affine_by_wavelength, check_affine)
        logger = logging.getLogger("lspr_imaging_app.workflow")
        if matches:
            logger.info("[λ] mode: cube %s and cube %s agree on chromatic geometry.", reference_cube, check_cube)
        else:
            logger.warning(
                "[λ] mode: cube %s and cube %s disagree on chromatic geometry "
                "(diagnostic only - not blocking; see _diagnose_shared_wavelength_geometry).",
                reference_cube, check_cube,
            )
        return matches

    def _build_shared_wavelength_geometry(
        self,
        spectral_cubes: list[int],
        selected_source_rois: list[AreaRoi],
        settings_snapshot: SpectrumSettingsSnapshot,
    ) -> SharedWavelengthGeometry | None:
        """[λ] mode: compute the per-wavelength chromatic affine and the
        resulting scoped-read box once, from `spectral_cubes[0]`, instead of
        recomputing the same numbers for every cube (see
        SharedWavelengthGeometry's docstring for why this is valid). Trusts
        the user's toggle - see _diagnose_shared_wavelength_geometry, called
        below purely for logging, not as a gate. Only returns None when
        there's genuinely nothing to build from (no data for the reference
        cube at all), never because of a cross-cube mismatch.

        Known trade-off from not gating on the diagnostic any more: if the
        reference cube (spectral_cubes[0]) is missing a wavelength that
        other cubes in the run actually have (e.g. that one wavelength was
        excluded only on this cube), every cube in the run will use no
        chromatic correction (identity/None) for that wavelength, rather
        than each cube's own - since there is no longer a per-cube fallback
        for a wavelength missing from the shared map. Picking the reference
        cube more carefully (e.g. the one with the most complete wavelength
        set) would close this gap but was left out to keep this change
        focused on removing the gate, not changing reference-cube selection.
        """
        from lspr_imaging_app.gui.analysis_tasks import compute_roi_union_bounding_box
        from lspr_imaging_app.io.dataset import load_image_shape
        from lspr_imaging_app.processing.preprocess import spatial_output_shape

        if not spectral_cubes or self.window._state.dataset is None:
            return None
        reference_cube = int(spectral_cubes[0])
        affine_by_wavelength = self._affine_matrix_by_wavelength_for_cube(reference_cube)
        if affine_by_wavelength is None:
            return None
        self._diagnose_shared_wavelength_geometry(spectral_cubes, affine_by_wavelength)

        first_record = None
        for wavelength in self.window._wavelength_values:
            record = self.window._record_map.get((reference_cube, wavelength))
            if record is not None and not is_excluded(self.window._state.image_exclusions, reference_cube, wavelength):
                first_record = record
                break
        if first_record is None:
            return None
        try:
            raw_shape = load_image_shape(str(first_record.path))
        except Exception:
            return None
        image_height, image_width = spatial_output_shape(raw_shape, settings_snapshot.preprocessing)

        affine_matrices = [affine_by_wavelength.get(float(wavelength)) for wavelength in self.window._wavelength_values]
        box = compute_roi_union_bounding_box(
            selected_source_rois,
            float(settings_snapshot.area_roi_settings.reference_outer_radius_px),
            affine_matrices,
            image_height,
            image_width,
        )
        if box is None:
            return None
        return SharedWavelengthGeometry(
            affine_matrix_by_wavelength=affine_by_wavelength,
            raw_shape=raw_shape,
            image_height=image_height,
            image_width=image_width,
            box=box,
        )

    def _external_mask_by_wavelength_for_cube(
        self,
        spectral_cube_index: int,
        affine_by_wavelength: dict[float, object],
    ) -> dict[float, object]:
        """The fully-resolved (fetched, chromatic-warped, wavelength-diffed)
        marked-pixels mask for every wavelength of one cube - the exact same
        per-wavelength result `_prepare_fast_spectrum_payload_for_spectral_cube`
        computes fresh per cube today. Used both directly (as today's
        per-cube behavior) and as the one-time reference-cube computation
        [λ] mode reuses across every other cube (see
        _build_shared_wavelength_mask) - reuses `affine_by_wavelength` rather
        than re-deriving it, so both modes warp the mask with the exact same
        transform their spectrum payload will use.
        """
        result: dict[float, object] = {}
        for wavelength in self.window._wavelength_values:
            record = self.window._record_map.get((spectral_cube_index, wavelength))
            if record is None or is_excluded(self.window._state.image_exclusions, spectral_cube_index, wavelength):
                continue
            external_mask, _ = self.window._effective_external_mask_for_record(record.path, processed_space=True)
            if external_mask is not None:
                external_mask = np.asarray(external_mask, dtype=bool)
                affine_matrix = affine_by_wavelength.get(float(wavelength))
                if affine_matrix is not None:
                    external_mask = warp_boolean_mask_affine(external_mask, affine_matrix)
                external_mask = self.window._apply_mask_wavelength_diff(
                    external_mask, (spectral_cube_index, float(wavelength))
                )
            result[float(wavelength)] = external_mask
        return result

    def _build_shared_wavelength_mask(
        self,
        shared_geometry: SharedWavelengthGeometry,
        reference_cube: int,
    ) -> dict[float, object]:
        """[λ] mode's counterpart to _build_shared_wavelength_geometry: the
        marked-pixels mask, resolved once from the reference cube and reused
        for every cube in the run, instead of being fetched from disk fresh
        per cube (a real, avoidable per-cube disk read whenever the mask
        panel's "ignore marked pixels" is on - see the maintainer's report
        that this, not chromatic geometry, was the actual bottleneck behind
        a slow "Preparing spectral cube reads" phase). Trusts the toggle the
        same way the geometry side does - no cross-cube verification here
        (unlike the geometry side, there's no cheap way to check two cubes'
        masks agree without doing the same disk reads this is meant to
        avoid); this is exactly the "assume it's the same everywhere" case
        the maintainer described for their current single-mask-per-
        wavelength workflow, not (yet) the more general per-cube-recompute-
        with-translation mode planned for later.
        """
        return self._external_mask_by_wavelength_for_cube(reference_cube, shared_geometry.affine_matrix_by_wavelength)
