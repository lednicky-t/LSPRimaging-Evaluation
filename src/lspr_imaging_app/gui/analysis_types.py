"""Shared plain data types used across AnalysisController and its mixins
(analysis_worker_mixin.py, analysis_chromatic_geometry_mixin.py). Kept in
their own module (no logic, no `self.window` dependency) so those mixin
files and analysis_controller.py itself can all import from here without a
circular import between analysis_controller.py and its own mixins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from lspr_imaging_app.domain.models import FitResult


class SpectrumSettingsSnapshot(NamedTuple):
    """One deep-copied, read-only snapshot of the settings a spectrum/sensorgram
    computation needs (preprocessing, ROI-detection settings, mask panel state),
    taken once up front and shared by reference across every spectral-cube and
    per-wavelength worker thread in a run.

    Deep-copying these fresh per spectral cube (the old behavior) was both
    wasteful (repeated array copies for state that never changes during a run)
    and a latent correctness gap: cube payloads are built concurrently on a
    thread pool, so an in-flight settings edit could let different cubes of the
    *same* run silently use different settings. Nothing reachable from the
    spectrum/sensorgram task functions mutates these objects in place (verified
    for apply_preprocessing, flatten_background, estimate_background_profile,
    and the ROI mask builders) - only the live GUI state itself is ever mutated
    in place, which is exactly what taking this snapshot once protects against.
    """

    preprocessing: object
    area_roi_settings: object
    mask_state: object | None


class SharedWavelengthGeometry(NamedTuple):
    """[λ] mode's cube-invariant geometry: one chromatic affine matrix per
    wavelength, and the scoped-read box those matrices (plus the fixed ROI
    positions) produce - computed once from a single reference cube and
    reused for every cube in the run, instead of recomputing the identical
    numbers per cube (the default [λ,t] mode's behavior).

    Valid only because this app's chromatic correction is estimated once,
    from one reference cube's landmarks, and the resulting per-wavelength
    matrix is broadcast identically to every cube - never re-estimated per
    cube (verified against `_estimate_chromatic_models_task`,
    analysis_tasks.py: the affine matrix is looked up purely by wavelength,
    `matrices_by_wavelength[float(wavelength)]`, then copied onto every
    cube's model). `_diagnose_shared_wavelength_geometry` cross-checks a
    second cube against the reference and logs the result, but - at the
    maintainer's request - no longer gates on it: this mode trusts the
    user's toggle rather than silently falling back to per-cube computation
    on a mismatch (which was rejecting runs for reasons unrelated to the
    transform itself differing, e.g. the two cubes not sharing the exact
    same set of excluded wavelengths).
    """

    affine_matrix_by_wavelength: dict[float, object]
    raw_shape: tuple[int, int]
    image_height: int
    image_width: int
    box: tuple[int, int, int, int]


@dataclass(slots=True)
class SpectrumSeriesComputedData:
    """Result of `PlotManager.compute_spectrum_series_data`: one ROI's
    NaN-filtered, wavelength-sorted spectrum points plus its curve fit (if
    any) - everything the plotted curve needs, computed once. Split out from
    the render step (`PlotManager.render_spectrum_series`) so a caller that
    also needs the fit for a metric/current-point calculation (see
    AnalysisWorkerMixin._compute_formula_spectrum_result) can reuse this
    object instead of triggering a second, independent fit evaluation for
    the same spectrum - `add_spectrum_series` used to do exactly that
    (fit computed once inside it to draw the curve, then again by its
    caller for the metric).

    Lives here (not in plot_manager.py) because it's produced in
    plot_manager.py but consumed in analysis_worker_mixin.py too - same
    circular-import reason SpectrumSettingsSnapshot/SharedWavelengthGeometry
    live here rather than in analysis_controller.py.
    """

    x_values: np.ndarray
    y_values: np.ndarray
    fit: FitResult | None
