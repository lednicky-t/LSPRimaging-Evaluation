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
CLAUDE.md's zarr rule), and only one `AnalysisWorker` task is ever in
flight. `write_cell` is only ever called from that same thread, sequentially
(one cell at a time, never interleaved with another writer). Reads never
touch the file directly at query time - `AnalysisEngine` loads everything
into memory once via `read_all_cells` (at construction, or whenever a
caller chooses to rehydrate) and answers `get_metric`/`get_spectrum` from
that in-memory copy, same as it already did before this file existed. This
sidesteps HDF5's lack of safe concurrent cross-thread read/write entirely,
rather than adding locking to work around it.

No Qt import allowed in this file (CLAUDE.md testing rule) - h5py file I/O
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
from .tasks import CellResult, WavelengthCoverage

STORE_SCHEMA_NAME = "lspri_rewrite_analysis_store"
STORE_SCHEMA_VERSION = "1.1"  # 1.1 (2026-10-03): + per-cell coverage/flags datasets; 1.0 files read fine (coverage = None)


_COVERAGE_COLUMNS = [
    "n_sample_nominal", "n_sample_valid", "sample_valid_fraction",
    "n_reference_nominal", "n_reference_valid", "reference_valid_fraction",
    "reference_min_sector_fraction",
]


def _coverage_row(coverage: WavelengthCoverage) -> list[float]:
    return [float(getattr(coverage, name)) for name in _COVERAGE_COLUMNS]


def _read_coverage(group: h5py.Group) -> tuple[WavelengthCoverage, ...] | None:
    """`None` for a cell written before coverage was recorded (store 1.0)."""
    if "coverage" not in group or "coverage_flags" not in group:
        return None
    rows = group["coverage"][()]
    flags = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in group["coverage_flags"][()]]
    return tuple(
        WavelengthCoverage(
            n_sample_nominal=int(row[0]), n_sample_valid=int(row[1]), sample_valid_fraction=float(row[2]),
            n_reference_nominal=int(row[3]), n_reference_valid=int(row[4]), reference_valid_fraction=float(row[5]),
            reference_min_sector_fraction=float(row[6]), flag=flag,
        )
        for row, flag in zip(rows, flags)
    )


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
        if result.coverage is not None:
            # One row per wavelength: n_sample_nominal, n_sample_valid,
            # sample_valid_fraction, n_reference_nominal, n_reference_valid,
            # reference_valid_fraction, reference_min_sector_fraction.
            group.create_dataset(
                "coverage",
                data=np.asarray([_coverage_row(c) for c in result.coverage], dtype=np.float64).reshape(-1, len(_COVERAGE_COLUMNS)),
            )
            group["coverage"].attrs["columns"] = json.dumps(_COVERAGE_COLUMNS)
            group.create_dataset(
                "coverage_flags",
                data=np.asarray([c.flag for c in result.coverage], dtype=h5py.string_dtype(encoding="utf-8")),
            )
        group.attrs["roi_geometry_json"] = json.dumps(result.provenance.roi_geometry)
        group.attrs["reduction_method"] = result.provenance.reduction_method
        group.attrs["per_wavelength_settings_json"] = json.dumps(list(result.provenance.per_wavelength_settings))


def _roi_group_id(key: str) -> int | None:
    """`"roi_7"` -> 7. `None` for anything else, including the temporary
    `"roi_tmp_..."` names `remap_cell_roi_ids` uses mid-rename."""
    if not key.startswith("roi_"):
        return None
    try:
        return int(key[len("roi_"):])
    except ValueError:
        return None


def remap_cell_roi_ids(h5_path: Path, id_map: dict[int, int]) -> None:
    """Re-file every stored cell under its ROI's new id: ``/cells/roi_<old>``
    becomes ``/cells/roi_<new>``. A stored ROI id absent from ``id_map`` no
    longer exists, so its cells are deleted.

    Used when ROI ids are renumbered (a delete closes the gap, or the user
    reorders). Results are keyed by the id, so without this a result would
    stay filed under a number that now belongs to a different ROI.

    HDF5 ``move`` renames a group's link and copies no data, so this costs
    the same however many cells there are. A permutation can contain a cycle
    (1->2 and 2->1), so every group that changes id is first moved to a
    temporary name, then to its final one. ``id_map`` must not send two ids to
    the same target; that would merge two ROIs' cells.

    A crash between the two steps leaves ``roi_tmp_*`` groups behind. The
    reader skips them, so the worst case is a few cells shown as "not
    analyzed" and recomputed, never a cell shown under the wrong ROI."""
    if len(set(id_map.values())) != len(id_map):
        raise ValueError("id_map sends two ROI ids to the same target")
    if not h5_path.exists():
        return
    with h5py.File(h5_path, "a") as handle:
        cells = handle.get("cells")
        if cells is None:
            return
        stored = {roi_id: key for key in list(cells) if (roi_id := _roi_group_id(key)) is not None}
        for roi_id, key in stored.items():
            if roi_id not in id_map:
                del cells[key]
        moving = {old: new for old, new in id_map.items() if old != new and old in stored}
        for old in moving:
            cells.move(f"roi_{old}", f"roi_tmp_{old}")
        for old, new in moving.items():
            cells.move(f"roi_tmp_{old}", f"roi_{new}")


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
            roi_id = _roi_group_id(roi_key)
            if roi_id is None:
                continue
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
                    coverage=_read_coverage(group),
                )
    return results
