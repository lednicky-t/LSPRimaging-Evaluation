"""Pure JSON-canonicalization/hash helpers for analysis cache signatures.
Extracted out of AnalysisController - none of these read `self.window`/any
Qt state.

AnalysisController keeps thin `staticmethod` forwarders under the original
(underscore-prefixed) names, since external callers (main_window.py) already
reference them as `AnalysisController._method(...)`.
"""

from __future__ import annotations

import hashlib
import json

from lspr_imaging_app.domain.models import FormulaSpectrumResult


def analysis_cache_signature_to_json(value):
    if isinstance(value, tuple):
        return [analysis_cache_signature_to_json(item) for item in value]
    if isinstance(value, list):
        return [analysis_cache_signature_to_json(item) for item in value]
    return value


def analysis_cache_signature_from_json(value):
    if isinstance(value, list):
        return tuple(analysis_cache_signature_from_json(item) for item in value)
    return value


def signature_hash(signature: tuple[object, ...]) -> str:
    """Stable, compact hash of a cache-signature tuple, for persisting into
    the HDF5 measurement-export backup (see `storage/measurement_export.py`'s
    `signature_hash` column) so a reopened session can tell whether an
    on-disk row is still valid under the current settings, without storing
    or comparing the larger, code-coupled signature tuple itself. Reuses the
    same tuple/list-to-JSON canonicalization already used to persist
    signatures into the session's `analysis_cache` JSON, so the same
    signature always hashes the same way regardless of where it's used."""
    canonical = analysis_cache_signature_to_json(signature)
    encoded = json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def formula_spectral_cube_signature(signature: tuple[object, ...] | None) -> tuple[object, ...] | None:
    if signature is None or len(signature) < 4:
        return None
    return (signature[0], signature[1], signature[3])


def formula_spectrum_result_covers_roi_ids(result: FormulaSpectrumResult, selected_roi_ids: tuple[int, ...]) -> bool:
    if not selected_roi_ids:
        return False
    if not result.area_roi_results:
        return len(selected_roi_ids) == 1
    available_ids = {int(roi_id) for roi_id in result.area_roi_results.keys()}
    return all(int(roi_id) in available_ids for roi_id in selected_roi_ids)
