"""The analysis store: ``data.h5`` - per-(ROI, cube) reduced spectra +
provenance (sketch §5 "The analysis store: one file, per-cell provenance").
New code, not a port.

**Deliberately separate from `packages/lspr_io`'s `lspr_measurement` schema
(2026-09-22 design decision, maintainer confirmed)**: that schema already
does something structurally similar - per-ROI absorbance spectra, every
reduction method stored (schema 6.7), a `signature_hash` for cache validity
(schema 6.6) - but that hash is one opaque combined value covering every
input at once. This rewrite's provenance design deliberately moved *away*
from a single combined hash toward separate, individually-versioned,
human-readable files (a real mask PNG, a real chromatic-model JSON) after
the maintainer pushed back on an earlier hash-based draft twice. Reusing
`lspr_measurement`'s mechanism as-is would have quietly reintroduced
exactly what was steered away from - so this store uses its own minimal,
independent identity stamp instead of that schema, compatibility with the
stable app's export format is explicitly not a goal right now.

**Avoids concurrent HDF5 access by construction, not locking**: `compute_cell`
runs on `AnalysisWorker`'s background thread (never `QThreadPool` -
AGENTS.md's zarr rule), and only one `AnalysisWorker` task is ever in
flight. `write_cell` is only ever called from that same thread, sequentially
(one cell at a time, never interleaved with another writer). Reads never
touch the file directly at query time - `AnalysisEngine` loads everything
into memory once via `read_all_cells` (at construction, or whenever a
caller chooses to rehydrate) and answers `get_metric`/`get_spectrum` from
that in-memory copy, same as it already did before this file existed. This
sidesteps HDF5's lack of safe concurrent cross-thread read/write entirely,
rather than adding locking to work around it.

No Qt import allowed in this file (AGENTS.md testing rule) - h5py file I/O
is not a Qt dependency.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from ..version_rewrite import APP_VERSION, REWRITE_APP_NAME
from .provenance import ProvenanceRecord
from .tasks import CellResult

STORE_SCHEMA_NAME = "lspri_rewrite_analysis_store"
STORE_SCHEMA_VERSION = "1.0"


def _cell_group_path(roi_id: int, cube_index: int) -> str:
    return f"/cells/roi_{int(roi_id)}/cube_{int(cube_index)}"


def _ensure_identity(handle: h5py.File) -> None:
    """Stamps a minimal, independent identity block the first time a file
    is created - not `lspr_io`'s `lspr_measurement` schema (see module
    docstring for why). Only writes once; a reopened existing file is left
    alone (its original creation stamp stays accurate)."""
    if "schema_name" in handle.attrs:
        return
    handle.attrs["schema_name"] = STORE_SCHEMA_NAME
    handle.attrs["schema_version"] = STORE_SCHEMA_VERSION
    handle.attrs["app_name"] = REWRITE_APP_NAME
    handle.attrs["app_version"] = APP_VERSION
    handle.attrs["created_at_utc"] = datetime.now(timezone.utc).isoformat()


def write_cell(h5_path: Path, roi_id: int, cube_index: int, result: CellResult) -> None:
    """Persist one computed cell (values + provenance) into `data.h5`,
    creating the file (with its identity stamp) if it doesn't exist yet.
    Overwrites any previous entry for this exact (roi_id, cube_index) - a
    recompute always replaces, never appends a second copy, matching "one
    ongoing file per dataset" (sketch §5)."""
    h5_path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(h5_path, "a") as handle:
        _ensure_identity(handle)
        group_path = _cell_group_path(roi_id, cube_index)
        if group_path in handle:
            del handle[group_path]
        group = handle.create_group(group_path)
        group.create_dataset("wavelengths_nm", data=np.asarray(result.wavelengths_nm, dtype=np.float64))
        group.create_dataset("sample_values", data=np.asarray(result.sample_values, dtype=np.float64))
        group.create_dataset("reference_values", data=np.asarray(result.reference_values, dtype=np.float64))
        group.attrs["roi_geometry_json"] = json.dumps(result.provenance.roi_geometry)
        group.attrs["reduction_method"] = result.provenance.reduction_method
        group.attrs["per_wavelength_settings_json"] = json.dumps(list(result.provenance.per_wavelength_settings))


def read_all_cells(h5_path: Path) -> dict[tuple[int, int], CellResult]:
    """Bulk-load every stored cell from `h5_path` - empty dict if the file
    doesn't exist yet (a fresh dataset, not an error). The only reader this
    module exposes, deliberately: no per-cell `read_cell`/`fingerprint_for`
    that would open the file at query time - see module docstring for why."""
    if not h5_path.exists():
        return {}
    results: dict[tuple[int, int], CellResult] = {}
    with h5py.File(h5_path, "r") as handle:
        cells_group = handle.get("cells")
        if cells_group is None:
            return results
        for roi_key in cells_group:
            if not roi_key.startswith("roi_"):
                continue
            roi_id = int(roi_key[len("roi_"):])
            roi_group = cells_group[roi_key]
            for cube_key in roi_group:
                if not cube_key.startswith("cube_"):
                    continue
                cube_index = int(cube_key[len("cube_"):])
                group = roi_group[cube_key]
                per_wavelength_settings = tuple(
                    (float(wl), int(version)) for wl, version in json.loads(group.attrs["per_wavelength_settings_json"])
                )
                provenance = ProvenanceRecord(
                    roi_geometry=json.loads(group.attrs["roi_geometry_json"]),
                    reduction_method=str(group.attrs["reduction_method"]),
                    per_wavelength_settings=per_wavelength_settings,
                )
                results[(roi_id, cube_index)] = CellResult(
                    wavelengths_nm=tuple(float(v) for v in group["wavelengths_nm"][()]),
                    sample_values=tuple(float(v) for v in group["sample_values"][()]),
                    reference_values=tuple(float(v) for v in group["reference_values"][()]),
                    provenance=provenance,
                )
    return results
