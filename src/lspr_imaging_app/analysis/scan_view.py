"""From a stored scan to what the Spectra panel draws (2026-10-23). Pure, no Qt.

``compute_spectra`` takes the numbers of ``scan.h5`` (:class:`~.scan_store.StoredScan`) and, for some ROIs on one cube, applies the layers of
``query.py``: the formula (absorbance by default), the fit and the metric. Formula, fit and metric are **never stored** (CLAUDE.md): this
runs again whenever one of them changes, on a worker thread (a polynomial fit is ~1 ms, a Gaussian more, and 160 ROIs add up; never on the
GUI thread). Each ROI is treated alone; nothing is pooled across ROIs.

A wavelength without a value (no valid pixel on either side) is left out of the spectrum, the fit and the metric, and counted in
``n_missing`` so the panel can say so; it is never filled in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .query import fit_and_metric, formula_spectrum
from .scan_store import StoredScan
from .settings import MetricSettings
from .spectrum_errors import formula_errors, standard_error

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
        try:
            fit, x, y = fit_and_metric(spectrum, metric)
            if fit is not None:
                if fit.fitted_wavelengths_nm.size > 1:
                    result.fit_wavelengths_nm, result.fit_values = fit.fitted_wavelengths_nm, fit.fitted_values
                else:
                    result.note = "The fit needs at least two points."
            result.metric_wavelength_nm, result.metric_value = x, y
        except (RuntimeError, ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:  # degenerate data
            result.note = f"The fit failed: {exc}"
        out.append(result)
    return out


def roi_ids_in(stored: StoredScan, roi_ids: Sequence[int]) -> tuple[int, ...]:
    """The requested ROI ids that are in the file, in the order requested."""
    known = set(stored.roi_ids)
    return tuple(r for r in roi_ids if r in known)
