"""What the golden-reference path (``analysis.tasks.compute_cell``) needs besides the ROI, gathered from the app's modules.

Until 2026-10-29 this was the input-gathering half of ``AnalysisEngine`` (``analysis/engine.py``, removed in cut-over phase 2e: no UI ever started a
run, the scan replaced it). Several tools still compare the scan with ``compute_cell`` or time its stages, and read the same six things from
the engine; they now read them from here. The code is the engine's own, moved unchanged, with the engine's defaults (the app never passed
anything else). Nothing in ``src/`` imports this file.

``st.build_modules`` (``tools/analysis_stage_timing.py``) attaches one as ``modules.reference_inputs``.
"""

from __future__ import annotations

from lspr_imaging_app.analysis.provenance import DEFAULT_REFERENCE_EXCLUSION_MODE, FrameNamingScheme
from lspr_imaging_app.analysis.reduction import DEFAULT_TRIMMED_MEAN_FRACTION
from lspr_imaging_app.analysis.tasks import DEFAULT_COVERAGE_THRESHOLDS, CoverageThresholds, WavelengthComputeInput
from lspr_imaging_app.dataset.model import is_dark_frame_wavelength


class ReferenceInputs:
    """Read-only view of the modules, shaped like the arguments of ``compute_cell``. Call it from the thread that owns the modules."""

    trimmed_mean_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION

    def __init__(self, modules) -> None:
        self._m = modules

    def reference_exclusion_mode(self) -> str:
        return DEFAULT_REFERENCE_EXCLUSION_MODE

    def coverage_thresholds(self) -> CoverageThresholds:
        return DEFAULT_COVERAGE_THRESHOLDS

    def default_reference_diameters(self) -> tuple[float, float]:
        detection = self._m.roi_toolbox.detection_settings()
        return detection.reference_inner_diameter_px, detection.reference_outer_diameter_px

    def naming(self) -> FrameNamingScheme:
        """The file-name scheme of the stored snapshots. The dark frame is left out and the 0 nm rule is fixed (not the frame roles): changing a
        role must never change the file names of stored files."""
        dataset = self._m.dataset
        cube_indices = list(dataset.spectral_cubes())
        wavelengths: list[float] = []
        for cube_index in cube_indices:
            wavelengths.extend(wl for wl in dataset.wavelengths_for_cube(cube_index) if not is_dark_frame_wavelength(wl))
        return FrameNamingScheme.for_dataset(cube_indices, wavelengths)

    def gather_wavelength_inputs(self, cube_index: int) -> dict[float, WavelengthComputeInput]:
        """One cube's per-wavelength inputs for ``compute_cell``. Frames that are not spectral points (the dark frame, frames the user marked)
        are left out: they must never become an entry of a ``CellResult``."""
        m = self._m
        geometry = m.geometry.settings()
        background = m.background.settings()
        inputs: dict[float, WavelengthComputeInput] = {}
        for wavelength_nm in m.dataset.wavelengths_for_cube(cube_index):
            if not m.frame_roles.is_spectral(cube_index, wavelength_nm):
                continue
            mask_resolution = m.mask.resolve_mask_source((cube_index, wavelength_nm))
            resolved_mask = mask_authored_frame = mask_scope = mask_warp_affine = None
            if mask_resolution is not None:
                mask_authored_frame, resolved_mask, mask_scope = mask_resolution
                frame = (int(cube_index), float(wavelength_nm))
                # Only a mask authored at another frame is re-registered; affine_between would give the identity otherwise.
                if mask_authored_frame != frame:
                    mask_warp_affine = m.chromatic.affine_between(mask_authored_frame, frame)
            inputs[wavelength_nm] = WavelengthComputeInput(
                wavelength_nm=wavelength_nm,
                raw_image=m.dataset.load_plane(cube_index, wavelength_nm),
                geometry_settings=geometry, background_settings=background,
                chromatic_affine=m.chromatic.affine_for((cube_index, wavelength_nm)),
                resolved_mask=resolved_mask, mask_authored_frame=mask_authored_frame, mask_scope=mask_scope,
                mask_warp_affine=mask_warp_affine,
            )
        return inputs
