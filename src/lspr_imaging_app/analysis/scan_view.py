"""From a stored scan to what the Spectra panel draws (2026-10-23). Pure, no Qt.

``compute_spectra`` takes the numbers of ``scan.h5`` (:class:`~.scan_store.StoredScan`) and, for some ROIs on one cube, applies the layers of
``query.py``: the formula (absorbance by default), the fit and the metric. Formula, fit and metric are **never stored** (CLAUDE.md): this
runs again whenever one of them changes, on a worker thread (a polynomial fit is ~1 ms, a Gaussian more, and 160 ROIs add up; never on the
GUI thread). Each ROI is treated alone; nothing is pooled across ROIs.

A wavelength without a value (no valid pixel on either side) is left out of the spectrum, the fit and the metric, and counted in
``n_missing`` so the panel can say so; it is never filled in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .query import fit_and_metric, formula_spectrum
from .scan_store import StoredScan
from .settings import MetricSettings
from .spectrum_errors import formula_errors, standard_error
from .template_shift import MIN_TEMPLATE_POINTS

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


def compute_spectra(stored: StoredScan, request: SpectraRequest) -> list[RoiSpectrum]:
    """One :class:`RoiSpectrum` per requested ROI, in the order requested. A ROI that is not in the file (added after the scan), or a cube
    that was never scanned, gives an empty spectrum with a ``note``."""
    empty = np.empty(0)
    try:
        cube_row = stored.cube_indices.index(int(request.cube))
    except ValueError:
        return [RoiSpectrum(r, empty, empty, empty, note="This cube is not in the scan file.") for r in request.roi_ids]
    mean = stored.values[_primary_method(stored)]
    wavelengths = np.asarray(stored.wavelength_nm, dtype=np.float64)
    metric = request.metric
    out: list[RoiSpectrum] = []
    for roi_id in request.roi_ids:
        if roi_id not in stored.roi_ids:
            out.append(RoiSpectrum(roi_id, empty, empty, empty, note="This ROI is not in the scan file: run the scan again."))
            continue
        row = stored.roi_ids.index(roi_id)
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

            template = reference_template(trace_input(stored, roi_id), request.formula_key, metric)
            if template is None:
                first, last = metric.reference_cube_range()
                if len(wavelengths) < MIN_TEMPLATE_POINTS:
                    result.note = f"The template shift needs at least {MIN_TEMPLATE_POINTS} scanned wavelengths (this scan has {len(wavelengths)})."
                else:
                    result.note = f"The reference cubes {first}-{last} have no usable data for this ROI: choose other reference cubes."
                out.append(result)
                continue
        try:
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
