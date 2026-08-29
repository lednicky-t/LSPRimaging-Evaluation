"""HDF5 spectra/sensorgram export and incremental backup for LSPRi eva.

Writes into the shared `lspr_measurement` schema (`packages/lspr_io`),
reusing the ROI-indexed groups introduced for LSPRimaging Acquisition in
schema 6.4 and extended in 6.5 - see
`apps/LSPRi/eva/docs/imaging_measurement_export_format.md` and
`packages/lspr_io/src/lspr_io/schema.py`'s 6.4/6.5 changelog entries.

No image pixel data is written here (images stay TIFF/OME-Zarr); this only
covers per-ROI derived spectra and sensorgram (metric-over-time) traces,
plus a descriptive mirror of the ROI definitions. The full ROI geometry -
including any freeform mask - stays canonical in `analysis/roi_table.json`
(see `storage/workspace.py`); the `roi_definitions` table written here is a
thin, self-describing mirror, not a second source of truth.

Datasets are created resizable and grown one row at a time, so a file this
writer is actively appending to stays valid and readable if the app closes
or crashes mid-run - the same property that makes sLSPR acq's HDF5 format
usable as a live backup, not just a final export.

Deliberately Qt-free (no imports from `gui/`): this module must be usable
without a running Qt application, per this repo's GUI/science separation
rule. Callers pass plain arrays/scalars, not `AbsorbanceSpectrumResult`/
`SensorgramComputationResult` (those live in `domain/models.py` and
`gui/worker.py` respectively, and the latter pulls in PyQt6 at import time)
- the GUI layer converts to/from those at the call site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from lspr_io import (
    LSPR_MEASUREMENT_ROI_DEFINITIONS_COLUMNS,
    LSPR_MEASUREMENT_ROI_DEFINITIONS_DATASET_NAME,
    LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME,
    LSPR_PROCESSED_SENSORGRAM_GROUP_NAME,
    create_roi_index_entry,
    read_absorbance_spectra,
    read_roi_definitions,
    read_sensorgram,
    standard_measurement_metadata,
    upsert_table,
    write_measurement_manifest_metadata,
    write_measurement_root_metadata,
)

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup, RoiArrayGroup
from lspr_imaging_app.processing.roi_math import DEFAULT_TRIMMED_MEAN_FRACTION
from lspr_imaging_app.version import APP_NAME, APP_VERSION


def _opt(value: object) -> str:
    """Empty string for None, str() otherwise - matches the `_opt` idiom
    already used by LSPRi acq's writer for the same string-table encoding."""
    return "" if value is None else str(value)


def _array_position(roi_id: int, arrays: list[RoiArrayGroup]) -> tuple[str, str, str]:
    """(array_group_id, row, col) for `roi_id`, derived from whichever
    RoiArrayGroup lists it as a member - empty strings if it isn't part of
    any array. Row/col come from the member's position in
    `member_area_roi_ids` (row-major, per `cols`); AreaRoi itself has no
    stored row/col field, so this is computed at export time only, not
    written back onto the domain model."""
    for array in arrays:
        if roi_id in array.member_area_roi_ids:
            index = array.member_area_roi_ids.index(roi_id)
            cols = max(int(array.cols), 1)
            return array.array_id, str(index // cols), str(index % cols)
    return "", "", ""


def _append_scalar(dataset: h5py.Dataset, value: object) -> None:
    index = dataset.shape[0]
    dataset.resize((index + 1,) + dataset.shape[1:])
    dataset[index] = value


def _append_row(dataset: h5py.Dataset, row: np.ndarray) -> None:
    index = dataset.shape[0]
    dataset.resize((index + 1,) + dataset.shape[1:])
    dataset[index, :] = row


_STRING_DTYPE = h5py.string_dtype(encoding="utf-8")

# Human-readable definitions for every Reduction/Formula key this writer can
# emit under processed/absorbance_spectra/<roi_id>/reduced_values/<method>/
# and the formula_key attr - stamped once per file (see
# _stamp_method_definitions) so the file is self-documenting: a scientist
# opening it years later without this app's source can see exactly how each
# column was computed and reproduce it, without having to reverse-engineer
# reduce_sample_and_reference_all_methods/formula_value from code. New keys
# only need an entry added here - the JSON-attr shape itself never needs a
# schema change to grow.
_REDUCTION_METHOD_DEFINITIONS: dict[str, str] = {
    "mean": "Arithmetic mean of every masked pixel in the ROI.",
    "median": "Median of every masked pixel in the ROI - robust to a single hot/dead pixel or cosmic-ray hit.",
    "trimmed_mean": (
        f"Mean after dropping the top/bottom {DEFAULT_TRIMMED_MEAN_FRACTION * 100:.0f}% of pixel values from "
        "each tail (a fixed fraction, not user-adjustable) - a middle ground between mean and median."
    ),
    "plane_fit": (
        "Reference side: least-squares plane fit (z = a*x + b*y + c) to the reference ROI's pixels, evaluated "
        "at the sample ROI's center - corrects a spatial illumination gradient between the sample and reference "
        "apertures. Sample side: plain mean (same as the 'mean' method). Falls back to plain mean when there "
        "are too few reference points to fit a plane."
    ),
}
_FORMULA_KEY_DEFINITIONS: dict[str, str] = {
    "absorbance": "A = -log10(sample / reference).",
    "ratio": "sample / reference.",
    "relative_change": "(reference - sample) / reference.",
    "mod_absorbance": "-1000 x log10(sample / reference), in milli-absorbance units.",
}


def _stamp_method_definitions(parent: h5py.Group) -> None:
    """Writes the Reduction/Formula definition catalogs above as JSON-string
    attrs on `parent` (processed/absorbance_spectra), once - subsequent
    calls are a no-op so re-opening an existing backup never overwrites an
    already-stamped catalog (e.g. with a version of this dict from a newer
    app release than actually computed the file's own data)."""
    if "reduction_method_definitions" not in parent.attrs:
        parent.attrs["reduction_method_definitions"] = json.dumps(_REDUCTION_METHOD_DEFINITIONS)
    if "formula_key_definitions" not in parent.attrs:
        parent.attrs["formula_key_definitions"] = json.dumps(_FORMULA_KEY_DEFINITIONS)


def _ensure_column(group: h5py.Group, name: str, *, dtype, fill_value, row_count: int) -> h5py.Dataset:
    """Creates a resizable 1-D `name` dataset if missing, backfilling it with
    `fill_value` up to `row_count` rows first. Needed whenever a schema
    change adds a new per-row column (see schema.py's 6.6 changelog entry):
    without backfilling, reopening a file written by an older version of
    this writer would create the new column at length 0 while its
    already-populated sibling columns (cube_index, timestamp_utc_ms, ...)
    stay at their existing length - a silent row-count mismatch across one
    "row" of a group, since every column in these groups is meant to stay
    the same length by construction."""
    if name in group:
        return group[name]
    dataset = group.create_dataset(name, shape=(row_count,), maxshape=(None,), dtype=dtype, chunks=True)
    if row_count:
        dataset[...] = fill_value
    return dataset


def _ensure_matrix_column(group: h5py.Group, name: str, *, n_wavelengths: int, fill_value: float, row_count: int) -> h5py.Dataset:
    """2-D analog of `_ensure_column`, for a per-row per-wavelength dataset
    (shape (rows, n_wavelengths)) - same backfill reasoning: without it, a
    subgroup created after a ROI group already has rows would start at
    length 0 while `cube_index` stays at its existing length. The backfilled
    rows are NaN placeholders standing in for "not actually computed for
    this row", not real data - see `reduced_values_start_row` (stamped in
    `_ensure_reduced_values_subgroup`), which is what a reader actually uses
    to tell backfilled rows apart from genuine ones, since NaN alone can't
    (a genuinely empty ROI mask also produces NaN)."""
    if name in group:
        return group[name]
    dataset = group.create_dataset(
        name, shape=(row_count, n_wavelengths), maxshape=(None, n_wavelengths), dtype=np.float32, chunks=True
    )
    if row_count:
        dataset[...] = fill_value
    return dataset


class ImagingMeasurementExportWriter:
    """Owns one open `lspr_measurement` HDF5 file used as LSPRi eva's
    spectra/sensorgram export - and, since its datasets are resizable and
    appended incrementally, also as a crash-safe backup during a long
    analysis run.

    Call `write_roi_definitions` whenever the ROI set is known or changes,
    and `append_formula_spectrum`/`append_sensorgram_point` as each new
    result becomes available.
    """

    def __init__(self, path: Path, *, experiment_name: str = "", started_at_utc: datetime | None = None) -> None:
        """Opens `path` for append if it already exists (a previous run's
        backup), or creates it fresh otherwise. Reopening an existing backup
        never truncates it - identity metadata (created_at_utc/started_at_utc/
        etc.) is preserved from the original creation rather than re-stamped,
        since those describe when the backup/run first started, not when it
        was last reopened."""
        self.path = Path(path)
        file_exists = self.path.exists()
        self._handle = h5py.File(self.path, "a" if file_exists else "w")
        if file_exists:
            self._processed = self._handle.require_group("processed")
        else:
            identity_kwargs = dict(
                created_by=APP_NAME,
                started_at_utc=started_at_utc or datetime.now(),
                app_name=APP_NAME,
                app_version=APP_VERSION,
                experiment_name=experiment_name,
            )
            write_measurement_root_metadata(self._handle, **standard_measurement_metadata(**identity_kwargs))
            manifest = self._handle.create_group("manifest")
            write_measurement_manifest_metadata(
                manifest,
                **standard_measurement_metadata(**identity_kwargs),
                extra_attrs={"manifest_kind": "measurement"},
            )
            self._processed = self._handle.create_group("processed")
        self._sensorgram_groups: dict[str, h5py.Group] = {}
        self._absorbance_groups: dict[str, h5py.Group] = {}

    # -- reopening an existing backup: recover what's already on disk --------

    def existing_formula_spectrum_keys(self) -> set[tuple[int, int, str]]:
        """(roi_id, cube_index, signature_hash) triples already backed up on
        disk, so a reopened writer's caller can skip re-appending a row for
        a (cube, signature) a previous run already wrote - and, conversely,
        still append a fresh row when the signature has since changed
        (e.g. an ROI moved), rather than mistaking a now-stale row for a
        duplicate. Reads only the small `cube_index`/`signature_hash`
        columns per ROI, never the bulk spectral arrays. Legacy rows
        backfilled with `signature_hash=""` (see `_ensure_column`) never
        match a real hash, so they're always treated as needing a fresh
        append rather than as an accidental duplicate."""
        keys: set[tuple[int, int, str]] = set()
        parent = self._processed.get(LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME)
        if parent is None:
            return keys
        for roi_id_text, group in parent.items():
            if "cube_index" not in group:
                continue
            try:
                roi_id = int(roi_id_text)
            except ValueError:
                continue
            cube_indices = group["cube_index"][...]
            hashes = group["signature_hash"][...] if "signature_hash" in group else [""] * len(cube_indices)
            for cube_index, signature_hash in zip(cube_indices, hashes, strict=False):
                hash_text = signature_hash.decode("utf-8") if isinstance(signature_hash, bytes) else str(signature_hash)
                keys.add((roi_id, int(cube_index), hash_text))
        return keys

    def existing_sensorgram_keys(self) -> set[tuple[str, int, str]]:
        """(roi_id, cube_index, signature_hash) triples already backed up on
        disk. `roi_id` stays a string - a combined-ROI-selection trace uses
        a synthetic `"combined_..."` key rather than a real ROI id. See
        `existing_absorbance_keys` for why `signature_hash` is part of the
        key rather than a separate freshness check."""
        keys: set[tuple[str, int, str]] = set()
        parent = self._processed.get(LSPR_PROCESSED_SENSORGRAM_GROUP_NAME)
        if parent is None:
            return keys
        for roi_id_text, group in parent.items():
            if "cube_index" not in group:
                continue
            cube_indices = group["cube_index"][...]
            hashes = group["signature_hash"][...] if "signature_hash" in group else [""] * len(cube_indices)
            for cube_index, signature_hash in zip(cube_indices, hashes, strict=False):
                hash_text = signature_hash.decode("utf-8") if isinstance(signature_hash, bytes) else str(signature_hash)
                keys.add((roi_id_text, int(cube_index), hash_text))
        return keys

    def sensorgram_metric_index(self, roi_id: str | int) -> dict[int, tuple[str, float]]:
        """`cube_index -> (signature_hash, metric_value)` for the most-
        recently-appended row per cube_index in this ROI's backed-up
        sensorgram trace. A parameter change makes an old row stale without
        removing it (§4d of `analysis_pipeline_redesign.md` - rows are
        appended, never rewritten in place), so a later row for the same
        cube_index supersedes an earlier one; iterating in on-disk (append)
        order and overwriting the dict per cube_index naturally keeps the
        latest. Reads from the already-open handle, so it's safe to call
        while this writer is still appending elsewhere. Rows backfilled with
        `signature_hash=""` (see `_ensure_column`) are included as-is - never
        a match for a real live signature, same convention as
        `existing_sensorgram_keys`. This is the read-side counterpart of
        `existing_sensorgram_keys`, giving a caller the actual `metric_value`
        instead of just a dedup key, so a disk hit can supply the final
        answer instead of only skipping a duplicate append."""
        index: dict[int, tuple[str, float]] = {}
        parent = self._processed.get(LSPR_PROCESSED_SENSORGRAM_GROUP_NAME)
        if parent is None:
            return index
        group = parent.get(str(roi_id))
        if group is None or "cube_index" not in group or "metric_value" not in group:
            return index
        cube_indices = group["cube_index"][...]
        values = group["metric_value"][...]
        hashes = group["signature_hash"][...] if "signature_hash" in group else [""] * len(cube_indices)
        for cube_index, value, signature_hash in zip(cube_indices, values, hashes, strict=False):
            hash_text = signature_hash.decode("utf-8") if isinstance(signature_hash, bytes) else str(signature_hash)
            index[int(cube_index)] = (hash_text, float(value))
        return index

    def formula_spectrum_index(self, roi_id: str | int) -> "FormulaSpectrumTraceIndex | None":
        """Read-side counterpart of `append_formula_spectrum`, mirroring
        `sensorgram_metric_index`'s contract exactly: reads from the
        already-open `self._processed` handle (safe to call while this writer
        is still appending elsewhere), and where a cube_index repeats (a
        cube recomputed under changed settings appends a new row rather than
        rewriting), the later row supersedes an earlier one for that cube.

        Returns None if this ROI has no backed-up spectrum trace at all, so a
        caller can distinguish "nothing on disk yet" from "on disk but every
        row's signature is stale" (the latter is a per-cube decision the
        caller makes by comparing `by_cube[cube_index][0]` against a live
        signature hash - as of schema 6.7, the reduction-independent disk
        signature, see AnalysisController._roi_disk_signature_for_cube; not
        `_sensorgram_point_signature_hash`, which is fit-method-dependent
        and wrong for a raw pre-fit spectrum).

        Each `by_cube[cube_index]` entry's `reduced_values_by_method` dict
        holds whichever reduction methods are actually available for that
        row: every method in `reduced_values/<method>/` (schema 6.7+, once
        `row_index >= reduced_values_start_row` - see
        `_ensure_reduced_values_subgroup`) plus, as a fallback baseline,
        the group's flat legacy `sample_mean`/`reference_mean` columns under
        whichever `reduction_method` the group attrs say was active for
        that write. A pre-6.7 file (or a row written before this group ever
        got a `reduced_values/` subgroup) therefore still yields exactly one
        method - the one it was actually saved under - never a fabricated
        multi-method entry."""
        parent = self._processed.get(LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME)
        if parent is None:
            return None
        group = parent.get(str(roi_id))
        if group is None or "cube_index" not in group or "wavelengths_nm" not in group:
            return None
        cube_indices = group["cube_index"][...]
        hashes = group["signature_hash"][...] if "signature_hash" in group else [""] * len(cube_indices)
        sample_rows = group["sample_mean"][...]
        reference_rows = group["reference_mean"][...]
        baseline_reduction_method = str(group.attrs.get("reduction_method", "mean"))

        # Per-method (rows, n_wavelengths) arrays, only for methods that
        # actually have a reduced_values/ subgroup in this file.
        reduced_arrays_by_method: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        reduced_values_group = group.get("reduced_values")
        if reduced_values_group is not None:
            for method_name in reduced_values_group:
                method_group = reduced_values_group[method_name]
                if "sample_mean" in method_group and "reference_mean" in method_group:
                    reduced_arrays_by_method[method_name] = (
                        method_group["sample_mean"][...],
                        method_group["reference_mean"][...],
                    )
        reduced_values_start_row = int(group.attrs.get("reduced_values_start_row", 0))

        by_cube: dict[int, tuple[str, dict[str, tuple[np.ndarray, np.ndarray]]]] = {}
        for row_index, (cube_index, signature_hash) in enumerate(zip(cube_indices, hashes, strict=False)):
            hash_text = signature_hash.decode("utf-8") if isinstance(signature_hash, bytes) else str(signature_hash)
            reduced_values_by_method: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            if row_index >= reduced_values_start_row:
                for method_name, (sample_arr, reference_arr) in reduced_arrays_by_method.items():
                    reduced_values_by_method[method_name] = (
                        np.asarray(sample_arr[row_index], dtype=np.float64),
                        np.asarray(reference_arr[row_index], dtype=np.float64),
                    )
            if baseline_reduction_method not in reduced_values_by_method:
                reduced_values_by_method[baseline_reduction_method] = (
                    np.asarray(sample_rows[row_index], dtype=np.float64),
                    np.asarray(reference_rows[row_index], dtype=np.float64),
                )
            by_cube[int(cube_index)] = (hash_text, reduced_values_by_method)
        return FormulaSpectrumTraceIndex(
            wavelengths_nm=np.asarray(group["wavelengths_nm"][...], dtype=np.float64),
            formula_key=str(group.attrs.get("formula_key", "absorbance")),
            reduction_method=baseline_reduction_method,
            by_cube=by_cube,
        )

    # -- ROI definitions: a thin, self-describing mirror ---------------------
    # (full geometry, including any freeform mask, stays canonical in
    # analysis/roi_table.json - see this module's docstring)

    def write_roi_definitions(
        self,
        rois: list[AreaRoi],
        groups: list[AreaRoiGroup] | None = None,
        arrays: list[RoiArrayGroup] | None = None,
    ) -> None:
        group_id_by_roi_id: dict[int, str] = {}
        for group in groups or []:
            for roi_id in group.area_roi_ids:
                group_id_by_roi_id[roi_id] = group.group_id
        arrays = arrays or []

        rows: list[list[str]] = []
        for roi in rois:
            array_group_id, array_row, array_col = _array_position(roi.area_roi_id, arrays)
            rows.append(
                [
                    _opt(roi.area_roi_id),
                    group_id_by_roi_id.get(roi.area_roi_id, ""),
                    _opt(roi.center_x),
                    _opt(roi.center_y),
                    _opt(roi.sample_radius_px),
                    _opt(roi.sample_diameter_px),
                    _opt(roi.reference_inner_diameter_px),
                    _opt(roi.reference_outer_diameter_px),
                    roi.sample_color_hex or "",
                    roi.reference_color_hex or "",
                    roi.sample_geometry_type,
                    roi.reference_geometry_type,
                    roi.label or "",
                    roi.notes or "",
                    roi.created_by,
                    array_group_id,
                    array_row,
                    array_col,
                ]
            )
        upsert_table(
            self._processed,
            LSPR_MEASUREMENT_ROI_DEFINITIONS_DATASET_NAME,
            rows,
            LSPR_MEASUREMENT_ROI_DEFINITIONS_COLUMNS,
        )

        for roi in rois:
            array_group_id, array_row, array_col = _array_position(roi.area_roi_id, arrays)
            create_roi_index_entry(
                self._handle,
                str(roi.area_roi_id),
                definition_attrs={
                    "name": roi.label or "",
                    "sample_geometry_type": roi.sample_geometry_type,
                    "reference_geometry_type": roi.reference_geometry_type,
                    "array_group_id": array_group_id,
                    "array_row": array_row,
                    "array_col": array_col,
                    "notes": roi.notes or "",
                    "created_by": roi.created_by,
                },
            )

    # -- sensorgram: one tracked metric over time, per ROI --------------------

    def _sensorgram_group(self, roi_id: str) -> h5py.Group:
        group = self._sensorgram_groups.get(roi_id)
        if group is not None:
            return group
        parent = self._processed.require_group(LSPR_PROCESSED_SENSORGRAM_GROUP_NAME)
        group = parent.require_group(roi_id)
        existing_row_count = int(group["timestamp_utc_ms"].shape[0]) if "timestamp_utc_ms" in group else 0
        if "timestamp_utc_ms" not in group:
            group.create_dataset("timestamp_utc_ms", shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True)
        if "metric_value" not in group:
            group.create_dataset("metric_value", shape=(0,), maxshape=(None,), dtype=np.float64, chunks=True)
        # Both added in schema 6.6, after the sensorgram group's original
        # columns above - backfilled via _ensure_column so a file written by
        # an earlier writer version reopens with all columns row-aligned.
        _ensure_column(group, "cube_index", dtype=np.int64, fill_value=-1, row_count=existing_row_count)
        _ensure_column(group, "signature_hash", dtype=_STRING_DTYPE, fill_value="", row_count=existing_row_count)
        self._sensorgram_groups[roi_id] = group
        create_roi_index_entry(
            self._handle,
            roi_id,
            links={"sensorgram": f"/processed/{LSPR_PROCESSED_SENSORGRAM_GROUP_NAME}/{roi_id}"},
        )
        return group

    def set_sensorgram_metric(
        self, roi_id: str | int, *, metric_name: str, formula_key: str, combined_roi_ids: str = ""
    ) -> None:
        """Record which metric/formula this ROI's sensorgram trace tracks.
        Written once as group-level attrs (not a per-row column) - this
        assumes the tracked metric/formula doesn't change mid-run for a
        given ROI's backup stream. Switching metrics mid-analysis should
        start a new roi_id/run rather than mixing metrics in one trace.

        `combined_roi_ids`, if given, is a comma-separated list of the real
        ROI ids a multi-ROI-selection trace is a combination of - written
        when `roi_id` is a synthetic "combined_..." key rather than a real
        ROI, so the trace stays self-describing without inventing a new
        top-level schema shape for it (see `_backup_sensorgram_point` in
        `gui/analysis_controller.py`).
        """
        group = self._sensorgram_group(str(roi_id))
        group.attrs["metric_name"] = metric_name
        group.attrs["formula_key"] = formula_key
        if combined_roi_ids:
            group.attrs["combined_roi_ids"] = combined_roi_ids

    def append_sensorgram_point(
        self,
        roi_id: str | int,
        *,
        cube_index: int,
        timestamp_utc_ms: int,
        metric_value: float,
        signature_hash: str = "",
    ) -> None:
        """`signature_hash` identifies the exact preprocessing/chromatic/ROI-
        geometry/exclusion/fit-parameter state this value was computed
        under (see `AnalysisController._sensorgram_point_signature_hash`) -
        empty string means "unknown" (a caller that hasn't wired hashing
        yet, or a legacy row backfilled by `_ensure_column`), which never
        matches a real hash, so it's always treated as unverifiable rather
        than accidentally trusted."""
        group = self._sensorgram_group(str(roi_id))
        _append_scalar(group["cube_index"], int(cube_index))
        _append_scalar(group["timestamp_utc_ms"], int(timestamp_utc_ms))
        _append_scalar(group["metric_value"], float(metric_value))
        _append_scalar(group["signature_hash"], str(signature_hash))

    # -- absorbance spectra: the full per-wavelength trace over time, per ROI -

    def _absorbance_group(self, roi_id: str, *, n_wavelengths: int) -> h5py.Group:
        group = self._absorbance_groups.get(roi_id)
        if group is not None:
            return group
        parent = self._processed.require_group(LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME)
        _stamp_method_definitions(parent)
        group = parent.require_group(roi_id)
        existing_row_count = int(group["cube_index"].shape[0]) if "cube_index" in group else 0
        for name in ("cube_index", "timestamp_utc_ms"):
            if name not in group:
                group.create_dataset(name, shape=(0,), maxshape=(None,), dtype=np.int64, chunks=True)
        for name in ("absorbance", "sample_mean", "reference_mean"):
            if name not in group:
                group.create_dataset(
                    name, shape=(0, n_wavelengths), maxshape=(None, n_wavelengths), dtype=np.float32, chunks=True
                )
        # Added in schema 6.6, after this group's original columns above -
        # see _sensorgram_group for why _ensure_column's backfill matters.
        _ensure_column(group, "signature_hash", dtype=_STRING_DTYPE, fill_value="", row_count=existing_row_count)
        self._absorbance_groups[roi_id] = group
        create_roi_index_entry(
            self._handle,
            roi_id,
            links={"absorbance_spectra": f"/processed/{LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME}/{roi_id}"},
        )
        return group

    def _ensure_reduced_values_subgroup(self, group: h5py.Group, reduction_method: str, *, n_wavelengths: int) -> h5py.Group:
        """`reduced_values/<reduction_method>/{sample_mean, reference_mean}`
        under a ROI's absorbance-spectrum group - schema 6.7+. Every
        reduction method actually computed alongside the row being appended
        (see `reduce_sample_and_reference_all_methods`) gets its own
        subgroup here, so any of them can be recovered later without
        re-reading pixels - see processing/analysis.py's project_reduction_
        result. `reduction_method`'s own pair also duplicates the flat
        `sample_mean`/`reference_mean` columns (small extra storage,
        simpler code than sharing via a link).

        `reduced_values_start_row`, stamped once on the FIRST call for this
        group (any method), is the row index before which no `reduced_values/`
        entry should be trusted for ANY method - rows before it predate this
        feature and get NaN-backfilled (`_ensure_matrix_column`) purely to
        keep every column in the group the same length, not because they
        were computed. See `formula_spectrum_index`'s read-side use of this.
        """
        existing_row_count = int(group["cube_index"].shape[0]) if "cube_index" in group else 0
        if "reduced_values_start_row" not in group.attrs:
            group.attrs["reduced_values_start_row"] = existing_row_count
        reduced_values_root = group.require_group("reduced_values")
        method_group = reduced_values_root.require_group(reduction_method)
        _ensure_matrix_column(method_group, "sample_mean", n_wavelengths=n_wavelengths, fill_value=np.nan, row_count=existing_row_count)
        _ensure_matrix_column(method_group, "reference_mean", n_wavelengths=n_wavelengths, fill_value=np.nan, row_count=existing_row_count)
        return method_group

    def append_formula_spectrum(
        self,
        roi_id: str | int,
        *,
        wavelengths_nm: np.ndarray,
        formula_values: np.ndarray,
        sample_mean: np.ndarray,
        reference_mean: np.ndarray,
        cube_index: int,
        timestamp_utc_ms: int,
        formula_key: str = "absorbance",
        reduction_method: str = "mean",
        signature_hash: str = "",
        reduced_values_by_method: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
    ) -> None:
        """Append one ROI's full per-wavelength spectrum at one point in
        time. `formula_values` is whatever `formula_key` actually computed
        (absorbance by default - see processing/analysis.py:formula_value);
        `sample_mean`/`reference_mean` are the pre-combination reduced pixel
        values (from whichever `reduction_method` was active - despite the
        param/dataset names, not necessarily an arithmetic mean; kept as-is
        for on-disk schema stability, see domain/models.py's
        FormulaSpectrumResult.sample_reduced_value), kept alongside it (not
        discarded) so the combination can be audited or recomputed later -
        mirroring this repo's "raw data is sacred" rule applied to this
        app's own reduced-per-wavelength data.
        `signature_hash`: see `append_sensorgram_point`'s docstring, with one
        difference as of schema 6.7 - callers now compute this from a
        reduction-INDEPENDENT signature (see AnalysisController._roi_disk_
        signature_for_cube), since one row can carry every reduction
        method's values (see `reduced_values_by_method` below) and its
        validity must not depend on which one happened to be active when it
        was written.
        `reduced_values_by_method`: schema 6.7+ - every reduction method's
        own (sample, reference) pair computed alongside `reduction_method`
        this call (see processing/roi_math.py's reduce_sample_and_reference_
        all_methods), written into per-method `reduced_values/<method>/`
        subgroups so any of them can be recovered later without re-reading
        pixels. Optional/None keeps this a purely additive parameter for any
        future caller that only ever computes one method.
        """
        wavelengths_nm = np.asarray(wavelengths_nm, dtype=np.float64)
        n_wavelengths = len(wavelengths_nm)
        group = self._absorbance_group(str(roi_id), n_wavelengths=n_wavelengths)
        if "wavelengths_nm" not in group:
            group.create_dataset("wavelengths_nm", data=wavelengths_nm)
        group.attrs["formula_key"] = formula_key
        group.attrs["reduction_method"] = reduction_method
        if reduced_values_by_method:
            for method, (method_sample, method_reference) in reduced_values_by_method.items():
                method_group = self._ensure_reduced_values_subgroup(group, method, n_wavelengths=n_wavelengths)
                _append_row(method_group["sample_mean"], np.asarray(method_sample, dtype=np.float32))
                _append_row(method_group["reference_mean"], np.asarray(method_reference, dtype=np.float32))
        _append_scalar(group["cube_index"], int(cube_index))
        _append_scalar(group["timestamp_utc_ms"], int(timestamp_utc_ms))
        _append_row(group["absorbance"], np.asarray(formula_values, dtype=np.float32))
        _append_row(group["sample_mean"], np.asarray(sample_mean, dtype=np.float32))
        _append_row(group["reference_mean"], np.asarray(reference_mean, dtype=np.float32))
        _append_scalar(group["signature_hash"], str(signature_hash))

    # -- export ------------------------------------------------------------

    def export_snapshot(self, destination: Path) -> None:
        """Write a standalone copy of everything recorded so far (ROI
        definitions, absorbance spectra, sensorgram traces) to a new file at
        `destination`, without closing or otherwise disturbing this writer's
        own open handle - this is what the "Export Results..." button uses,
        so analysis can keep appending to the live backup afterward.

        Copies via h5py's in-process `Group.copy` from the already-open
        `self._handle`, not a filesystem copy of `self.path` - the backup
        file is open for writing (this writer holds it), and a plain
        OS-level byte copy of a currently-open HDF5 file isn't guaranteed
        consistent or even readable while another handle holds it.
        """
        self._handle.flush()
        destination = Path(destination)
        with h5py.File(destination, "w") as dest:
            for key in self._handle.keys():
                self._handle.copy(key, dest)
            for attr_key, attr_value in self._handle.attrs.items():
                dest.attrs[attr_key] = attr_value

    # -- lifecycle -------------------------------------------------------------

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "ImagingMeasurementExportWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@dataclass(slots=True)
class FormulaSpectrumTraceIndex:
    """Read-side result of `ImagingMeasurementExportWriter.formula_spectrum_index`
    - one ROI's whole backed-up spectrum trace, indexed by cube_index for a
    caller to validate/reconstruct one cube's `AbsorbanceSpectrumResult` at a
    time without re-reading the file per cube.

    `formula_key`/`reduction_method` are the group-level attrs (whichever was
    active at the last append - see `append_formula_spectrum`), kept as a
    baseline/default, not the full story: since schema 6.7, each cube's own
    `by_cube` entry can carry more than one reduction method's (sample,
    reference) pair (see `reduced_values_by_method` below), and formula is
    never stored at all beyond that pair - both a specific formula's curve
    and any other reduction method's pair are cheap to derive from whichever
    pair IS present, see processing/analysis.py's project_formula_spectrum
    and project_reduction_result."""

    wavelengths_nm: np.ndarray
    formula_key: str
    reduction_method: str
    # (signature_hash, {reduction_method: (sample_reduced_value, reference_reduced_value)})
    # per cube_index. The reduction-independent signature_hash (see
    # AnalysisController._roi_disk_signature_for_cube) validates the WHOLE
    # entry - every method in the dict was computed from the same pixel
    # extraction, so they all share one hash rather than needing their own.
    by_cube: dict[int, tuple[str, dict[str, tuple[np.ndarray, np.ndarray]]]]


@dataclass(slots=True)
class RoiDefinitionRecord:
    """Plain read-side mirror of one `processed/roi_definitions` row - the
    descriptive subset written by `write_roi_definitions`, not the full
    `AreaRoi` geometry (which stays canonical in `analysis/roi_table.json`).
    """

    area_roi_id: int
    group_id: str
    center_x: float
    center_y: float
    sample_geometry_type: str
    reference_geometry_type: str
    label: str
    notes: str
    created_by: str
    array_group_id: str
    array_row: int | None
    array_col: int | None


def read_roi_definition_records(path: Path) -> list[RoiDefinitionRecord]:
    with h5py.File(path, "r") as handle:
        rows = read_roi_definitions(handle)
    records: list[RoiDefinitionRecord] = []
    for row in rows:
        records.append(
            RoiDefinitionRecord(
                area_roi_id=int(row.get("area_roi_id") or 0),
                group_id=row.get("group_id", ""),
                center_x=float(row.get("center_x") or 0.0),
                center_y=float(row.get("center_y") or 0.0),
                sample_geometry_type=row.get("sample_geometry_type") or "circle",
                reference_geometry_type=row.get("reference_geometry_type") or "annulus",
                label=row.get("label", ""),
                notes=row.get("notes", ""),
                created_by=row.get("created_by") or "user",
                array_group_id=row.get("array_group_id", ""),
                array_row=int(row["array_row"]) if row.get("array_row") else None,
                array_col=int(row["array_col"]) if row.get("array_col") else None,
            )
        )
    return records


def read_sensorgram_trace(path: Path, roi_id: str | int) -> dict[str, Any]:
    """Plain dict of arrays/attrs for one ROI's sensorgram trace
    (`timestamp_utc_ms`, `metric_value`, `metric_name`, `formula_key`) - the
    GUI layer wraps this into a `SensorgramComputationResult` if needed."""
    with h5py.File(path, "r") as handle:
        return read_sensorgram(handle, str(roi_id))


def read_formula_spectra_trace(path: Path, roi_id: str | int) -> dict[str, Any]:
    """Plain dict of arrays for one ROI's absorbance-spectrum-over-time
    (`wavelengths_nm`, `cube_index`, `timestamp_utc_ms`, `absorbance`,
    `sample_mean`, `reference_mean`, `formula_key`, `reduction_method`) - the
    GUI layer wraps this into `AbsorbanceSpectrumResult` entries if needed.

    `sample_mean`/`reference_mean` are LSPRi-eva-specific additions on top
    of the generic `read_absorbance_spectra` shape shared with LSPRi acq
    (which only has `cube_index`/`absorbance`/`wavelengths_nm`), so they're
    read directly here rather than through the shared `lspr_io` helper.
    """
    with h5py.File(path, "r") as handle:
        result = read_absorbance_spectra(handle, str(roi_id))
        processed_group = handle.get("processed")
        absorbance_root = processed_group.get(LSPR_PROCESSED_ABSORBANCE_SPECTRA_GROUP_NAME) if processed_group is not None else None
        group = absorbance_root.get(str(roi_id)) if absorbance_root is not None else None
        if group is not None:
            if "sample_mean" in group:
                result["sample_mean"] = group["sample_mean"][...]
            if "reference_mean" in group:
                result["reference_mean"] = group["reference_mean"][...]
            for attr_name in ("formula_key", "reduction_method"):
                if attr_name in group.attrs:
                    value = group.attrs[attr_name]
                    result[attr_name] = value.decode("utf-8") if isinstance(value, bytes) else value
        return result
