"""From a stored scan to what the Spectra panel draws (2026-10-23). Pure, no Qt.

``compute_spectra`` takes the numbers of ``scan.h5`` (:class:`~.scan_store.StoredScan`) and, for some ROIs on one cube, applies the layers of
``query.py``: the formula (absorbance by default), the fit and the metric. Formula, fit and metric are **never stored** (CLAUDE.md): this
runs again whenever one of them changes, on a worker thread (a polynomial fit is ~1 ms, a Gaussian more, and 160 ROIs add up; never on the
GUI thread). Each ROI is treated alone; nothing is pooled across ROIs.

A wavelength without a value (no valid pixel on either side) is left out of the spectrum, the fit and the metric, and counted in
``n_missing`` so the panel can say so; it is never filled in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .query import FormulaSpectrum, fit_and_metric, formula_spectrum
from .settings import MetricSettings
from .spectrum_errors import formula_errors, standard_error
from .template_shift import MIN_TEMPLATE_POINTS, ReferenceTemplate

if TYPE_CHECKING:  # only a type here: importing it would load h5py into the helper process that runs `fit_spectra`
    from .scan_store import StoredScan

SAMPLE, REFERENCE = 0, 1


@dataclass(frozen=True)
class SpectraRequest:
    """What to compute: the ROIs, one cube (its index in the dataset), the formula and the fit / metric settings (copies)."""

    roi_ids: tuple[int, ...]
    cube: int
    formula_key: str
    metric: MetricSettings


@dataclass
class RoiSpectrum:
    roi_id: int
    wavelengths_nm: np.ndarray
    values: np.ndarray
    errors: np.ndarray  # one-sigma error of each value (see spectrum_errors); NaN where unknown
    fit_wavelengths_nm: np.ndarray | None = None  # None: no fit asked for, or it could not be made
    fit_values: np.ndarray | None = None
    metric_wavelength_nm: float | None = None
    metric_value: float | None = None
    n_missing: int = 0  # wavelengths of the scan without a value for this ROI on this cube
    note: str = ""  # why there is nothing to draw, or what went wrong with the fit
    shift_nm: float | None = None  # template shift only (template_shift.py): the sideways shift against the ROI's reference spectrum, and what was measured with it
    scale: float | None = None  # amplitude relative to the reference
    tilt: float | None = None  # change of the baseline across the range, in the units of the spectrum (not part of the shift)


def _primary_method(stored: StoredScan) -> str:
    return "mean" if "mean" in stored.values else next(iter(stored.values))


@dataclass
class SpectrumFitJob:
    """A spectrum ready for its fit (`fit_spectra`): the result so far (data, errors, notes), the formula spectrum and, for the template shift,
    the ROI's reference. Small and picklable, so the fit can run in the helper process (``trace_pool.TracePool.run_spectra``)."""

    result: RoiSpectrum
    spectrum: FormulaSpectrum
    template: ReferenceTemplate | None


TemplateCache = dict  # (roi_id, formula_key, metric.template_key()) -> ReferenceTemplate | None, for one stored scan


def compute_spectra(
    stored: StoredScan,
    request: SpectraRequest,
    *,
    templates: TemplateCache | None = None,
    fit_runner: Callable[[list[SpectrumFitJob], MetricSettings], list[RoiSpectrum]] | None = None,
) -> list[RoiSpectrum]:
    """One :class:`RoiSpectrum` per requested ROI, in the order requested. A ROI that is not in the file (added after the scan), or a cube
    that was never scanned, gives an empty spectrum with a ``note``.

    ``templates``: a cache of the ROIs' reference spectra (template shift) kept by the caller for this stored scan; the reference of a ROI is the
    mean of its reference cubes, the same for every cube shown, so it is built once instead of at every cube change. ``fit_runner``: run the fits
    elsewhere (the helper process); None = here. The numbers are the same either way (`fit_spectra` is what runs)."""
    prepared = prepare_spectra(stored, request, templates)
    jobs = [p for p in prepared if isinstance(p, SpectrumFitJob)]
    if not jobs:
        return [p for p in prepared if isinstance(p, RoiSpectrum)]
    fitted = iter((fit_runner or fit_spectra)(jobs, request.metric))
    return [next(fitted) if isinstance(p, SpectrumFitJob) else p for p in prepared]


def prepare_spectra(stored: StoredScan, request: SpectraRequest, templates: TemplateCache | None = None) -> list[RoiSpectrum | SpectrumFitJob]:
    """Per requested ROI, in order: a finished :class:`RoiSpectrum` (nothing to fit: a note says why) or a :class:`SpectrumFitJob`."""
    empty = np.empty(0)
    try:
        cube_row = stored.cube_indices.index(int(request.cube))
    except ValueError:
        return [RoiSpectrum(r, empty, empty, empty, note="This cube is not in the scan file.") for r in request.roi_ids]
    mean = stored.values[_primary_method(stored)]
    wavelengths = np.asarray(stored.wavelength_nm, dtype=np.float64)
    metric = request.metric
    rows: dict[int, int] = {}
    for row, stored_id in enumerate(stored.roi_ids):
        rows.setdefault(stored_id, row)  # the first row of an id, as list.index gave
    out: list[RoiSpectrum | SpectrumFitJob] = []
    for roi_id in request.roi_ids:
        row = rows.get(roi_id)
        if row is None:
            out.append(RoiSpectrum(roi_id, empty, empty, empty, note="This ROI is not in the scan file: run the scan again."))
            continue
        if not stored.cell_hash[cube_row, row]:
            out.append(RoiSpectrum(roi_id, empty, empty, empty, note="This cube has not been scanned yet."))
            continue
        sample, reference = mean[cube_row, row, :, SAMPLE], mean[cube_row, row, :, REFERENCE]
        n_sample, n_reference = stored.n_valid[cube_row, row, :, SAMPLE], stored.n_valid[cube_row, row, :, REFERENCE]
        usable = np.isfinite(sample) & np.isfinite(reference) & (n_sample > 0) & (n_reference > 0)
        n_missing = int((~usable).sum())
        if not usable.any():
            out.append(RoiSpectrum(roi_id, empty, empty, empty, n_missing=n_missing, note="No wavelength has a value for this ROI."))
            continue
        spectrum = formula_spectrum(wavelengths[usable], sample[usable], reference[usable], request.formula_key)
        errors = formula_errors(
            spectrum.sample_values, spectrum.reference_values,
            standard_error(stored.std[cube_row, row, :, SAMPLE][usable], n_sample[usable]),
            standard_error(stored.std[cube_row, row, :, REFERENCE][usable], n_reference[usable]),
            request.formula_key,
        )
        result = RoiSpectrum(roi_id, spectrum.wavelengths_nm, spectrum.values, errors, n_missing=n_missing)
        template = None
        if metric.uses_template:  # the reference of THIS ROI: the mean of its reference cubes (all cubes of the ROI are needed, not only the one shown)
            from .scan_trace import trace_input  # imported here: scan_trace itself imports this module
            from .trace_fit import reference_template

            key = (roi_id, request.formula_key, metric.template_key())
            if templates is not None and key in templates:
                template = templates[key]
            else:
                template = reference_template(trace_input(stored, roi_id), request.formula_key, metric)
                if templates is not None:
                    templates[key] = template
            if template is None:
                first, last = metric.reference_cube_range()
                if len(wavelengths) < MIN_TEMPLATE_POINTS:
                    result.note = f"The template shift needs at least {MIN_TEMPLATE_POINTS} scanned wavelengths (this scan has {len(wavelengths)})."
                else:
                    result.note = f"The reference cubes {first}-{last} have no usable data for this ROI: choose other reference cubes."
                out.append(result)
                continue
        out.append(SpectrumFitJob(result, spectrum, template))
    return out


def _batch_solved(jobs: list[SpectrumFitJob], metric: MetricSettings) -> dict[int, np.ndarray | None]:
    """For the iterative fits (Gaussian, Voigt): every job's coefficients from one solver call per set of windowed wavelengths (2026-11-03: 160
    spectra one at a time took 0.67 s Gaussian / 0.85 s Voigt per cube). Job index -> coefficients, or None where the batch did not converge
    (that spectrum goes to scipy, as the per-spectrum path sends it). Jobs not listed are fitted the usual way. The solvers fit each row alone, so
    the coefficients are those of a one-row call."""
    from .fit_window import spectrum_window
    from .gaussian_fit import solve_gaussian
    from .query import _windowed
    from .voigt_fit import solve_voigt

    method = str(metric.fit_method).strip().lower()
    if method not in ("gaussian", "voigt") or metric.uses_template or len(jobs) < 2:
        return {}
    solve, min_points = (solve_gaussian, 4) if method == "gaussian" else (solve_voigt, 5)
    groups: dict[bytes, tuple[np.ndarray, list[int], list[np.ndarray]]] = {}
    for i, job in enumerate(jobs):
        wl_min, wl_max = spectrum_window(job.spectrum.wavelengths_nm, job.spectrum.values, metric)
        x, y = _windowed(job.spectrum.wavelengths_nm, job.spectrum.values, wl_min, wl_max)
        if x.size < min_points:
            continue  # the per-spectrum path gives its "too few points" result
        entry = groups.setdefault(x.tobytes(), (x, [], []))
        entry[1].append(i)
        entry[2].append(y)
    solved: dict[int, np.ndarray | None] = {}
    for x, indices, rows in groups.values():
        params, ok = solve(x, np.vstack(rows))
        for k, i in enumerate(indices):
            solved[i] = params[k] if ok[k] else None
    return solved


def fit_spectra(jobs: list[SpectrumFitJob], metric: MetricSettings) -> list[RoiSpectrum]:
    """The fit and the metric of each job (``query.fit_and_metric``), in order. Pure numpy / scipy: runs in the helper process as well.
    The Gaussian and Voigt solvers run once for all jobs (``_batch_solved``); what follows the solve is per job, as before."""
    out: list[RoiSpectrum] = []
    solved = _batch_solved(jobs, metric)
    for index, job in enumerate(jobs):
        result, spectrum, template = job.result, job.spectrum, job.template
        try:
            if index in solved:
                coefficients = solved[index]  # None: the batch did not converge on it -> scipy, as the one-spectrum path would
                fit, x, y = fit_and_metric(spectrum, metric, template=template, solved=coefficients, gaussian_fast_first=coefficients is not None)
            else:
                fit, x, y = fit_and_metric(spectrum, metric, template=template)
            if fit is not None:
                if fit.fitted_wavelengths_nm.size > 1:
                    result.fit_wavelengths_nm, result.fit_values = fit.fitted_wavelengths_nm, fit.fitted_values
                    if template is not None:
                        result.shift_nm, result.scale, _offset, result.tilt = (float(v) for v in fit.coefficients)
                elif template is not None:
                    result.note = "No shift could be found for this spectrum (too few points, or a shift beyond 60 nm)."
                else:
                    result.note = "The fit needs at least two points."
            result.metric_wavelength_nm, result.metric_value = x, y
        except (RuntimeError, ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:  # degenerate data
            result.note = f"The fit failed: {exc}"
        out.append(result)
    return out
