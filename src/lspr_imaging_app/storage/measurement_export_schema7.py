"""HDF5 schema major 7 for LSPRi eva: fixed-size, pre-allocated datasets under
a `/rois/<roi_id>/...` layout, replacing today's (schema 6.x) resizable,
append-and-grow `/processed/absorbance_spectra/<roi_id>/` and
`/processed/sensorgram/<roi_id>/` groups.

Why this exists: HDF5's per-write bookkeeping cost for a resizable dataset
grows with how many times it has EVER been resized over the file's life, not
with its size (see `apps/LSPRi/eva/docs/measurement_backup_performance_and_
crash_recovery.md`, "Bug D"). LSPRi eva knows the dataset's total spectral-cube
count and wavelength count before any analysis runs, so each ROI's arrays are
allocated at their final fixed shape up front - writing to row N is then a
direct, O(1) seek-and-write regardless of write order or count, for the
file's entire lifetime.

Scope: LSPRi eva only. sLSPR acq is untouched and stays on its own writer/
schema major 6 - nothing here is imported by, or changes behavior for, any
other app. This module does not touch `packages/lspr_io`'s shared
`LSPR_MEASUREMENT_SCHEMA_MAJOR`/`_MINOR`/`_VERSION` constants at all (those
govern every OTHER app's file-stamping too, via `standard_measurement_
metadata`) - schema-7 identity is written directly via `write_measurement_
root_metadata`/`write_measurement_manifest_metadata` with explicit,
locally-defined values instead, so nothing about how sLSPR acq stamps its own
(still, and staying, major-6) files changes.

Old (schema 6.x) `measurement_backup.h5` files are read/written exactly as
before by `measurement_export.py`'s existing methods - this module is only
ever used for a BRAND NEW backup file, which always starts life as schema 7.

Deliberately Qt-free (no imports from `gui/`), matching `measurement_export.
py`'s own convention - this module must be usable without a running Qt
application.

Layout:

    /manifest/spectral_cube_indices       - FIXED 1-D int64, the authoritative
                                             cube-index list this file's arrays
                                             are sized for. Row N of every
                                             per-ROI array corresponds to
                                             spectral_cube_indices[N], NOT
                                             necessarily cube_index N itself -
                                             cube indices are not guaranteed
                                             dense/zero-based (io/dataset.py).
    /axes/wavelengths_nm                  - FIXED 1-D float64, shared across
                                             every ROI in the file
    /rois/<roi_id>/definition             - unchanged (create_roi_index_entry)
    /rois/<roi_id>/spectra/timestamp_utc_ms       - FIXED 1-D int64, sentinel -1
    /rois/<roi_id>/spectra/signature_hash         - FIXED 1-D string, "" = never
                                                     computed
    /rois/<roi_id>/spectra/reduced/<method>/sample     - FIXED 2-D float32
    /rois/<roi_id>/spectra/reduced/<method>/reference  - (n_cubes, n_wavelengths),
                                                          NaN fill, one pair
                                                          created per reduction
                                                          method actually
                                                          written for this ROI
    /rois/<roi_id>/processed/metrics/value          - FIXED 1-D float64, NaN fill
    /rois/<roi_id>/processed/metrics/signature_hash - FIXED 1-D string, "" fill
    /rois/<roi_id>/processed/metrics (group attrs)  - metric_name, formula_key,
                                                       combined_roi_ids (same
                                                       fields `set_sensorgram_
                                                       metric` already stamps
                                                       today, moved here)

No separate `absorbance`/`formula_values` array: it's always cheaply
derivable from `(sample, reference, formula_key)` via `processing/analysis.py`'s
`formula_value()`. No flat "active method" columns alongside per-method
subgroups (unlike schema 6.6/6.7's duality, which exists only to keep
pre-6.7 readers working): every real caller already populates
`reduced_values_by_method` with every method it computed (NaN for the rest),
so schema 7 just writes whichever methods are given, keyed by name - one
representation, not two. No `cube_index` column: the row position already
encodes it via the manifest's position map, computed once per writer via
`spectral_cube_position_map`. No file-wide dedup key-set scan: a write reads
its own slot directly and overwrites in place - dedup is a per-slot compare,
not bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import h5py
import numpy as np

from lspr_io import (
    LSPR_MEASUREMENT_SCHEMA_NAME,
    LSPR_MEASUREMENT_FORMAT_NAME,
    LSPR_MEASUREMENT_FORMAT_VERSION,
    write_measurement_root_metadata,
    write_measurement_manifest_metadata,
)

SCHEMA7_MAJOR = 7
SCHEMA7_MINOR = 0
SCHEMA7_VERSION = f"{SCHEMA7_MAJOR}.{SCHEMA7_MINOR}"

_STRING_DTYPE = h5py.string_dtype(encoding="utf-8")
_TIMESTAMP_FILL = -1
_HASH_FILL = ""


def write_schema7_identity(
    handle: h5py.File,
    *,
    created_by: str,
    started_at_utc: datetime,
    app_name: str,
    app_version: str,
    experiment_name: str = "",
) -> h5py.Group:
    """Stamps root + manifest identity metadata for a brand-new schema-7
    file, and returns the created `manifest` group. Mirrors `measurement_
    export.py`'s own `__init__` identity-stamping exactly, except the
    schema_version/major/minor values are this module's own constants, not
    `packages/lspr_io`'s shared `LSPR_MEASUREMENT_SCHEMA_*` constants - see
    this module's docstring for why."""
    identity_kwargs: dict[str, Any] = dict(
        schema_name=LSPR_MEASUREMENT_SCHEMA_NAME,
        schema_version=SCHEMA7_VERSION,
        schema_major=SCHEMA7_MAJOR,
        schema_minor=SCHEMA7_MINOR,
        format_name=LSPR_MEASUREMENT_FORMAT_NAME,
        format_version=LSPR_MEASUREMENT_FORMAT_VERSION,
        created_by=created_by,
        started_at_utc=started_at_utc,
        app_name=app_name,
        app_version=app_version,
        experiment_name=experiment_name,
    )
    write_measurement_root_metadata(handle, **identity_kwargs)
    manifest = handle.create_group("manifest")
    write_measurement_manifest_metadata(manifest, **identity_kwargs, extra_attrs={"manifest_kind": "measurement"})
    return manifest


def write_spectral_cube_manifest(handle: h5py.File, spectral_cube_indices: list[int]) -> None:
    """Writes the one-time, authoritative cube-index list a new schema-7
    file's arrays are sized against. Must be called exactly once, at
    creation - every per-ROI array's row count is `len(spectral_cube_indices)`
    and every row position is resolved against this list via
    `spectral_cube_position_map`, never recomputed independently."""
    manifest = handle.require_group("manifest")
    manifest.create_dataset("spectral_cube_indices", data=np.asarray(spectral_cube_indices, dtype=np.int64))


def spectral_cube_position_map(handle: h5py.File) -> dict[int, int]:
    """`cube_index -> row position` for every per-ROI array in this file,
    rebuilt fresh from the persisted manifest list on every open (not
    assumed from the live dataset's own in-memory cube list) - a real
    mismatch (this backup doesn't match whatever dataset is currently open)
    is something a caller should detect, not silently misinterpret."""
    indices = handle["manifest"]["spectral_cube_indices"][...]
    return {int(cube_index): position for position, cube_index in enumerate(indices)}


def write_wavelength_axis(handle: h5py.File, wavelengths_nm: np.ndarray) -> None:
    axes = handle.require_group("axes")
    if "wavelengths_nm" not in axes:
        axes.create_dataset("wavelengths_nm", data=np.asarray(wavelengths_nm, dtype=np.float64))


def read_wavelength_axis(handle: h5py.File) -> np.ndarray:
    return np.asarray(handle["axes"]["wavelengths_nm"][...], dtype=np.float64)


def roi_spectra_group(handle: h5py.File, roi_id: str, *, n_cubes: int) -> h5py.Group:
    """Lazily creates `/rois/<roi_id>/spectra/` with its two always-present,
    fixed-size columns - per-method reduced-value arrays are created
    separately, on first actual use, by `ensure_reduced_method_arrays`.

    Unlike schema 6.x's `_absorbance_group` (which calls `create_roi_index_
    entry` to soft-link `/rois/<roi_id>/absorbance_spectra` to where the real
    data actually lives, `/processed/absorbance_spectra/<roi_id>`), no
    linking is needed here: the real data already lives directly under
    `/rois/<roi_id>/spectra` in this layout - there's nowhere else for a
    link to point to. `/rois/<roi_id>/definition` itself is written once,
    eagerly for every known ROI, by the existing (format-agnostic)
    `write_roi_definitions` - not repeated per write here."""
    rois = handle.require_group("rois")
    roi_group = rois.require_group(roi_id)
    spectra = roi_group.require_group("spectra")
    if "timestamp_utc_ms" not in spectra:
        spectra.create_dataset("timestamp_utc_ms", data=np.full(n_cubes, _TIMESTAMP_FILL, dtype=np.int64))
    if "signature_hash" not in spectra:
        spectra.create_dataset(
            "signature_hash", data=np.full(n_cubes, _HASH_FILL, dtype=object), dtype=_STRING_DTYPE
        )
    return spectra


def ensure_reduced_method_arrays(
    spectra_group: h5py.Group, method: str, *, n_cubes: int, n_wavelengths: int
) -> tuple[h5py.Dataset, h5py.Dataset]:
    """Lazily creates `/rois/<roi_id>/spectra/reduced/<method>/{sample,
    reference}` - one pair per reduction method actually written for this
    ROI, mirroring schema 6.7's `reduced_values/<method>/` lazy-subgroup
    pattern, minus the flat "active method" duplicate columns 6.7 also kept
    (see this module's own docstring for why that duality isn't needed
    here).

    Known, deliberately-unaddressed edge case: once a method's arrays exist
    (any row of this ROI ever supplied it), every OTHER row reads back as
    "having" that method too - via its NaN fill value, since there's no
    per-(cube, method) "was this actually supplied" marker, unlike schema
    6.7's `reduced_values_start_row` (which solves the analogous problem
    for its own, chronologically-different scenario - a method introduced
    partway through a file's history, not a row selectively omitting one).
    Not a gap in practice: every real caller (`reduce_sample_and_reference_
    all_methods`) already populates every `REDUCTION_METHODS` key on every
    row, NaN for whichever wasn't actually active - a row genuinely missing
    a key never happens via any call site in this app today. Flagged here,
    not fixed, per this repo's "don't add handling for scenarios that can't
    happen" rule - revisit if a future caller ever legitimately needs to
    omit a method per-row rather than NaN-fill it."""
    reduced = spectra_group.require_group("reduced")
    method_group = reduced.require_group(method)
    if "sample" not in method_group:
        method_group.create_dataset("sample", data=np.full((n_cubes, n_wavelengths), np.nan, dtype=np.float32))
    if "reference" not in method_group:
        method_group.create_dataset("reference", data=np.full((n_cubes, n_wavelengths), np.nan, dtype=np.float32))
    return method_group["sample"], method_group["reference"]


@dataclass(slots=True)
class FormulaSpectrumRowWrite:
    """One (cube) row to write for a single ROI, for `write_formula_
    spectrum_rows` below - the schema-7 counterpart of `measurement_export.
    py`'s `FormulaSpectrumBackupRow`, minus the fields schema 7 doesn't
    store (cube_index is implicit in `position`; there's no separate flat
    absorbance/sample_mean/reference_mean, only `reduced_values_by_method` -
    see this module's own top docstring for why)."""

    position: int
    timestamp_utc_ms: int
    signature_hash: str
    reduced_values_by_method: dict[str, tuple[np.ndarray, np.ndarray]]


def write_formula_spectrum_rows(
    spectra_group: h5py.Group,
    rows: list[FormulaSpectrumRowWrite],
    *,
    n_cubes: int,
    n_wavelengths: int,
) -> None:
    """Writes every row in `rows` (all for the same ROI - each a different
    cube, each already resolved to `reduced_values_by_method` with at least
    the row's own reduction_method) in place at each row's own fixed
    position - direct slice assignments, never a resize.

    Resolves every reduction method's `sample`/`reference` dataset pair
    (`ensure_reduced_method_arrays`) ONCE per call, up front, for the union
    of methods across the whole batch - not once per row. This matters: a
    real "Start analysis" run buffers several cubes per ROI before flushing
    (`measurement_backup_batch_size`), so `rows` is typically a small batch,
    not a single row - resolving the group/dataset objects per row instead
    of once per batch was a real, measured regression (2x slower than
    schema 6's own batched write at a realistic 30-ROI/4-method/batch-of-5
    scale - schema 6's batching amortizes its resize cost across the batch,
    and this fix is what lets schema 7's batching amortize its own,
    smaller-but-nonzero per-call HDF5 group/dataset-lookup cost the same
    way, instead of paying it fresh for every row)."""
    if not rows:
        return
    methods_present: set[str] = set()
    for row in rows:
        methods_present.update(row.reduced_values_by_method.keys())
    method_datasets = {
        method: ensure_reduced_method_arrays(spectra_group, method, n_cubes=n_cubes, n_wavelengths=n_wavelengths)
        for method in methods_present
    }
    timestamp_ds = spectra_group["timestamp_utc_ms"]
    hash_ds = spectra_group["signature_hash"]
    for row in rows:
        timestamp_ds[row.position] = int(row.timestamp_utc_ms)
        hash_ds[row.position] = str(row.signature_hash)
        for method, (sample_row, reference_row) in row.reduced_values_by_method.items():
            sample_ds, reference_ds = method_datasets[method]
            sample_ds[row.position] = np.asarray(sample_row, dtype=np.float32)
            reference_ds[row.position] = np.asarray(reference_row, dtype=np.float32)


@dataclass(slots=True)
class FormulaSpectrumRowV7:
    """Read-side result for one (ROI, cube) schema-7 row - shaped to match
    what `measurement_export.py`'s schema-6 `FormulaSpectrumTraceIndex.
    by_cube` entries already carry, so the calling code (`_formula_spectrum_
    result_from_disk_row` and friends) needs zero changes to consume either
    schema version."""

    wavelengths_nm: np.ndarray
    signature_hash: str
    reduced_values_by_method: dict[str, tuple[np.ndarray, np.ndarray]]


def read_formula_spectrum_row(
    handle: h5py.File, roi_id: str, cube_index: int, position_map: dict[int, int]
) -> FormulaSpectrumRowV7 | None:
    """Scoped single-cube read - the interactive preview's path. Reads only
    this one row's slice out of each method's array, not the whole history."""
    position = position_map.get(int(cube_index))
    if position is None:
        return None
    rois = handle.get("rois")
    if rois is None or roi_id not in rois:
        return None
    spectra = rois[roi_id].get("spectra")
    if spectra is None or "signature_hash" not in spectra:
        return None
    hash_value = spectra["signature_hash"][position]
    hash_text = hash_value.decode("utf-8") if isinstance(hash_value, bytes) else str(hash_value)
    if not hash_text:
        return None
    reduced_values_by_method = _read_reduced_methods_at(spectra, position)
    if not reduced_values_by_method:
        return None
    return FormulaSpectrumRowV7(
        wavelengths_nm=read_wavelength_axis(handle),
        signature_hash=hash_text,
        reduced_values_by_method=reduced_values_by_method,
    )


def read_formula_spectrum_index(
    handle: h5py.File, roi_id: str, position_map: dict[int, int]
) -> dict[int, FormulaSpectrumRowV7]:
    """Bulk read - the multi-cube sweep's path (loading everything up front
    is the right trade-off there, same as schema 6.x). Returns `cube_index ->
    FormulaSpectrumRowV7` for every cube that has ever actually been
    computed (a non-empty signature_hash) - unwritten slots are skipped
    entirely, same "nothing here yet" contract as a missing schema-6 row."""
    rois = handle.get("rois")
    if rois is None or roi_id not in rois:
        return {}
    spectra = rois[roi_id].get("spectra")
    if spectra is None or "signature_hash" not in spectra:
        return {}
    hashes = spectra["signature_hash"][...]
    wavelengths_nm = read_wavelength_axis(handle)
    reduced_group = spectra.get("reduced")
    method_arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    if reduced_group is not None:
        for method in reduced_group:
            method_group = reduced_group[method]
            if "sample" in method_group and "reference" in method_group:
                method_arrays[method] = (method_group["sample"][...], method_group["reference"][...])
    index_by_cube: dict[int, int] = {position: cube_index for cube_index, position in position_map.items()}
    result: dict[int, FormulaSpectrumRowV7] = {}
    for position, hash_value in enumerate(hashes):
        hash_text = hash_value.decode("utf-8") if isinstance(hash_value, bytes) else str(hash_value)
        if not hash_text:
            continue
        cube_index = index_by_cube.get(position)
        if cube_index is None:
            continue
        reduced_values_by_method = {
            method: (sample_arr[position], reference_arr[position])
            for method, (sample_arr, reference_arr) in method_arrays.items()
        }
        if not reduced_values_by_method:
            continue
        result[cube_index] = FormulaSpectrumRowV7(
            wavelengths_nm=wavelengths_nm, signature_hash=hash_text, reduced_values_by_method=reduced_values_by_method
        )
    return result


def _read_reduced_methods_at(spectra_group: h5py.Group, position: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    reduced_group = spectra_group.get("reduced")
    if reduced_group is None:
        return {}
    result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for method in reduced_group:
        method_group = reduced_group[method]
        if "sample" in method_group and "reference" in method_group:
            result[method] = (
                np.asarray(method_group["sample"][position], dtype=np.float64),
                np.asarray(method_group["reference"][position], dtype=np.float64),
            )
    return result


def roi_metrics_group(handle: h5py.File, roi_id: str, *, n_cubes: int) -> h5py.Group:
    """Lazily creates `/rois/<roi_id>/processed/metrics/` - the sensorgram
    trace's fixed-size counterpart to `roi_spectra_group` above. See that
    function's docstring for why no `create_roi_index_entry` soft-link call
    is needed here either."""
    rois = handle.require_group("rois")
    roi_group = rois.require_group(roi_id)
    metrics = roi_group.require_group("processed").require_group("metrics")
    if "value" not in metrics:
        metrics.create_dataset("value", data=np.full(n_cubes, np.nan, dtype=np.float64))
    if "signature_hash" not in metrics:
        metrics.create_dataset("signature_hash", data=np.full(n_cubes, _HASH_FILL, dtype=object), dtype=_STRING_DTYPE)
    return metrics


def set_sensorgram_metric_attrs(
    metrics_group: h5py.Group, *, metric_name: str, formula_key: str, combined_roi_ids: str = ""
) -> None:
    """Same fields schema 6.x's `set_sensorgram_metric` stamps, moved to this
    group path - see that method's docstring for why these are group-level
    attrs, not a per-row column."""
    metrics_group.attrs["metric_name"] = metric_name
    metrics_group.attrs["formula_key"] = formula_key
    if combined_roi_ids:
        metrics_group.attrs["combined_roi_ids"] = combined_roi_ids


def write_sensorgram_point(
    metrics_group: h5py.Group, position: int, *, metric_value: float, signature_hash: str
) -> None:
    metrics_group["value"][position] = float(metric_value)
    metrics_group["signature_hash"][position] = str(signature_hash)


def read_sensorgram_metric_index(
    handle: h5py.File, roi_id: str, position_map: dict[int, int]
) -> dict[int, tuple[str, float]]:
    """`cube_index -> (signature_hash, metric_value)` for every cube with a
    real (non-empty-hash) entry - the read-side counterpart of `write_
    sensorgram_point`, same contract as schema 6.x's `sensorgram_metric_
    index`."""
    rois = handle.get("rois")
    if rois is None or roi_id not in rois:
        return {}
    roi_group = rois[roi_id]
    if "processed" not in roi_group or "metrics" not in roi_group["processed"]:
        return {}
    metrics = roi_group["processed"]["metrics"]
    if "signature_hash" not in metrics or "value" not in metrics:
        return {}
    hashes = metrics["signature_hash"][...]
    values = metrics["value"][...]
    index_by_position: dict[int, int] = {position: cube_index for cube_index, position in position_map.items()}
    result: dict[int, tuple[str, float]] = {}
    for position, (hash_value, value) in enumerate(zip(hashes, values, strict=False)):
        hash_text = hash_value.decode("utf-8") if isinstance(hash_value, bytes) else str(hash_value)
        if not hash_text:
            continue
        cube_index = index_by_position.get(position)
        if cube_index is None:
            continue
        result[cube_index] = (hash_text, float(value))
    return result
