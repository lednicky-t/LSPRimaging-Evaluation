"""Pure dict/dataclass <-> JSON-friendly-dict transforms for the two result
types the analysis session-cache persists (AbsorbanceSpectrumResult,
SensorgramComputationResult). Extracted out of AnalysisController - these
functions never read `self.window`/any Qt state, so they don't need to be
methods on the controller at all.

AnalysisController keeps thin `staticmethod` forwarders under the original
(underscore-prefixed) names, since external callers (main_window.py) already
reference them as `AnalysisController._method(...)`.
"""

from __future__ import annotations

import numpy as np

from lspr_imaging_app.domain.models import FormulaSpectrumResult
from lspr_imaging_app.gui.worker import SensorgramComputationResult


def serialize_formula_spectrum_result(result: FormulaSpectrumResult) -> dict:
    return {
        "wavelengths_nm": [float(value) for value in np.asarray(result.wavelengths_nm, dtype=np.float64)],
        "formula_values": [float(value) for value in np.asarray(result.formula_values, dtype=np.float64)],
        "sample_mean": [float(value) for value in np.asarray(result.sample_mean, dtype=np.float64)],
        "reference_mean": [float(value) for value in np.asarray(result.reference_mean, dtype=np.float64)],
        "sample_pixel_count": [int(value) for value in np.asarray(result.sample_pixel_count, dtype=np.int32)],
        "reference_pixel_count": [int(value) for value in np.asarray(result.reference_pixel_count, dtype=np.int32)],
        "load_seconds": float(result.load_seconds),
        "roi_seconds": float(result.roi_seconds),
        "fit_seconds": float(result.fit_seconds),
        "total_seconds": float(result.total_seconds),
        "reduction_method": str(result.reduction_method),
        "formula_key": str(result.formula_key),
        "area_roi_results": {
            str(int(roi_id)): serialize_formula_spectrum_result(roi_result)
            for roi_id, roi_result in (result.area_roi_results or {}).items()
        },
    }


def deserialize_formula_spectrum_result(payload) -> FormulaSpectrumResult:
    if not isinstance(payload, dict):
        return FormulaSpectrumResult(
            wavelengths_nm=np.asarray([], dtype=np.float64),
            formula_values=np.asarray([], dtype=np.float64),
            sample_mean=np.asarray([], dtype=np.float64),
            reference_mean=np.asarray([], dtype=np.float64),
            sample_pixel_count=np.asarray([], dtype=np.int32),
            reference_pixel_count=np.asarray([], dtype=np.int32),
        )
    raw_roi_results = payload.get("area_roi_results") or payload.get("roi_results", {})
    roi_results: dict[int, FormulaSpectrumResult] = {}
    if isinstance(raw_roi_results, dict):
        for key, value in raw_roi_results.items():
            try:
                roi_id = int(key)
            except Exception:
                continue
            roi_results[roi_id] = deserialize_formula_spectrum_result(value)
    return FormulaSpectrumResult(
        wavelengths_nm=np.asarray(payload.get("wavelengths_nm", []), dtype=np.float64),
        # "formula_values" is the current key; "absorbance" is the pre-rename
        # key still present in session files saved before this field was
        # renamed (it always held whatever formula was selected, not
        # necessarily actual absorbance - only the label was misleading).
        formula_values=np.asarray(payload.get("formula_values", payload.get("absorbance", [])), dtype=np.float64),
        sample_mean=np.asarray(payload.get("sample_mean") or payload.get("spot_mean", []), dtype=np.float64),
        reference_mean=np.asarray(payload.get("reference_mean") or payload.get("ring_mean", []), dtype=np.float64),
        sample_pixel_count=np.asarray(payload.get("sample_pixel_count") or payload.get("spot_pixel_count", []), dtype=np.int32),
        reference_pixel_count=np.asarray(payload.get("reference_pixel_count") or payload.get("ring_pixel_count", []), dtype=np.int32),
        load_seconds=float(payload.get("load_seconds", 0.0)),
        roi_seconds=float(payload.get("roi_seconds", 0.0)),
        fit_seconds=float(payload.get("fit_seconds", 0.0)),
        total_seconds=float(payload.get("total_seconds", 0.0)),
        reduction_method=str(payload.get("reduction_method", "mean")),
        formula_key=str(payload.get("formula_key", "absorbance")),
        area_roi_results=roi_results,
    )


def serialize_sensorgram_result(result: SensorgramComputationResult) -> dict:
    return {
        "spectral_cube_indices": [int(value) for value in np.asarray(result.spectral_cube_indices, dtype=np.int32)],
        "metric_values": [float(value) for value in np.asarray(result.metric_values, dtype=np.float64)],
        "metric_signal": [float(value) for value in np.asarray(result.metric_signal, dtype=np.float64)],
        "completed_count": int(result.completed_count),
        "total_count": int(result.total_count),
        "prep_seconds": float(result.prep_seconds),
        "fit_seconds": float(result.fit_seconds),
        "total_seconds": float(result.total_seconds),
        "cancelled": bool(result.cancelled),
    }


def deserialize_sensorgram_result(payload) -> SensorgramComputationResult:
    if not isinstance(payload, dict):
        return SensorgramComputationResult(
            spectral_cube_indices=np.asarray([], dtype=np.int32),
            metric_values=np.asarray([], dtype=np.float64),
            metric_signal=np.asarray([], dtype=np.float64),
            completed_count=0,
            total_count=0,
            cancelled=False,
        )
    return SensorgramComputationResult(
        spectral_cube_indices=np.asarray(payload.get("spectral_cube_indices", []), dtype=np.int32),
        metric_values=np.asarray(payload.get("metric_values", []), dtype=np.float64),
        metric_signal=np.asarray(payload.get("metric_signal", []), dtype=np.float64),
        completed_count=int(payload.get("completed_count", 0)),
        total_count=int(payload.get("total_count", 0)),
        prep_seconds=float(payload.get("prep_seconds", 0.0)),
        fit_seconds=float(payload.get("fit_seconds", 0.0)),
        total_seconds=float(payload.get("total_seconds", 0.0)),
        cancelled=bool(payload.get("cancelled", False)),
    )
