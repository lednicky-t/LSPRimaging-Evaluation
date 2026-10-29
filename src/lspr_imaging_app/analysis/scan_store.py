"""The scan result file ``analysis/scan.h5`` (schema ``lspri_scan`` 1.1). Layout and decisions: plan doc section 15. No Qt.

Holds only what step 1 produces: per cube, ROI, wavelength and side the reduced pixel **mean**, **STD**, ``n_valid``, ``n_nominal`` and ``n_masked``
(absorbance, fits and metrics are recomputed from these, never stored). Readable with plain ``h5py`` / ``xarray`` / pandas::

    /processed/scan/value/mean  float64 [cube, roi, wavelength, side]    side 0 = sample, 1 = reference; NaN = no value
    /processed/scan/std         float64 [same]      spread of the valid pixels (SEM = std / sqrt(n_valid))
    /processed/scan/n_valid     uint32  [same]      pixels with a value
    /processed/scan/n_nominal   uint32  [same]      pixels in the aperture after the exclusions
    /processed/scan/n_masked    uint32  [same]      aperture pixels the ignore mask removed (> 0: a mask touched this ROI side; the Sensorgram marks it)
    /axes/   cube_index, roi_id, wavelength_nm, side (HDF5 dimension scales), acquired_at_unix_ms int64 [cube, wavelength] (0 = unknown)
    /metadata/recipes/<n>, /metadata/roi_layouts/<n>   readable JSON (what produced the numbers; see scan_fingerprint)
    /state/  cell_hash uint64 [cube, roi] (0 = never computed), recipe_id int32 [cube, roi], layout_id int32 [cube]

The axes are the whole dataset's (every spectral cube, the wavelength union, the ROI ids), so a partial run and a full run share one file.

**Crash safety.** A cube is written in this order: its ``cell_hash`` row is set to 0, the values are written and flushed, then the real
hash, ``recipe_id`` and ``layout_id`` are written. A crash at any point leaves a cube either complete or marked "never computed"; a stale
hash can never sit on half-written values. The file is created under a temporary name and renamed, so a half-made file is never taken for a
result. One writer at a time (the scan task's thread); readers open it read-only.

``write_batch`` (2026-10-27) writes several cubes with one opening of the file, in the same order for the whole batch (all their hashes to 0,
all values, all real hashes); ``ScanWriter`` runs it on a thread of its own so the collector of results never waits for the disk.
``write_cells`` (2026-10-27) refreshes only some ROI rows of a cube, in the same order (those hashes to 0, values flushed, real hashes last);
the other rows of the cube are not touched. ``state/layout_id`` of such a cube then names the layout of the latest write: still true for every
cell, because a cell that was not rewritten depends only on geometry that is identical in the old and the new layout (``scan_fingerprint``).

**No compression (2026-10-28, maintainer's choice).** The big arrays are stored plain, in one chunk per cube. A refresh that rewrites some ROI
rows of every cube made each gzip chunk a different size, HDF5 put the new chunk at the end and lost the old space when the file was closed: a
one-ROI refresh of 314 cubes added 3-7 MB (the test session's file was 96 MB where the same numbers written fresh take 38.5 MB), and the writer spent
half its time compressing. Plain chunks never change size and are rewritten in place: constant file size (74 MB for 314 cubes x 160 ROIs x 26
wavelengths, 28 bytes per value slot), a one-ROI refresh writes in 1.3 s instead of 3.3-3.8 s. A file written before this change (gzip) is converted
once by ``prepare`` (:meth:`ScanStore._convert_to_uncompressed`): copied into a new file, compared array for array, and only then swapped in.

**When the file does not fit** (other ROI ids, wavelength axis, cube list or reduction methods; or an unknown schema) the old file is
moved to ``scan.previous.h5`` (one backup slot) and a new one is made: every stored cell is stale then anyway (decision 2026-10-23), and
nothing is deleted without a copy.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from ..version import APP_NAME, APP_VERSION
from .frame_reduce import RoiSet
from .scan_arrays import ScanArrays
from .scan_fingerprint import recipe_digest, recipe_json, recipe_of, stamped_by_previous_formula
from .scan_jobs import WAVELENGTH_KEY_DECIMALS, ScanPlan

logger = logging.getLogger(__name__)

FILE_NAME = "scan.h5"
BACKUP_NAME = "scan.previous.h5"
SCHEMA_NAME = "lspri_scan"
SCHEMA_VERSION = "1.1"  # 1.1 (2026-10-24): + n_masked; a 1.0 file has none and is replaced like any file that does not fit
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 1
FORMAT_NAME = "lspri_scan_hdf5"
FORMAT_VERSION = 1
SIDE_LABELS = ("sample", "reference")
DIM_LABELS = ("cube", "roi", "wavelength", "side")
_STRING = h5py.string_dtype("utf-8")


class ScanFileError(Exception):
    """The file is not a scan file this version can read (unknown schema, newer major version, damaged)."""


@dataclass
class StoredScan:
    """A scan file read into memory."""

    path: Path
    attrs: dict[str, object]
    cube_indices: tuple[int, ...]
    roi_ids: tuple[int, ...]
    wavelength_nm: tuple[float, ...]
    acquired_at_unix_ms: np.ndarray
    values: dict[str, np.ndarray]
    std: np.ndarray
    n_valid: np.ndarray
    n_nominal: np.ndarray
    n_masked: np.ndarray
    cell_hash: np.ndarray
    recipe_id: np.ndarray
    layout_id: np.ndarray
    recipes: dict[int, str] = field(default_factory=dict)
    layouts: dict[int, str] = field(default_factory=dict)

    def computed(self) -> np.ndarray:
        """bool [cube, roi]: the cell was written completely."""
        return self.cell_hash != 0


def layout_json(rs: RoiSet) -> str:
    """The ROI table of one layout, readable (ids and geometry as the scan used them)."""
    rows = [
        {
            "roi_id": int(r.area_roi_id), "center_x": float(r.center_x), "center_y": float(r.center_y),
            "sample_diameter_px": float(r.sample_diameter_px), "sample_geometry_type": r.sample_geometry_type,
            "reference_geometry_type": r.reference_geometry_type, "reference_inner_diameter_px": r.reference_inner_diameter_px,
            "reference_outer_diameter_px": r.reference_outer_diameter_px,
        }
        for r in rs.rois
    ]
    return json.dumps(
        {"default_reference_inner_diameter_px": rs.inner_default_px, "default_reference_outer_diameter_px": rs.outer_default_px, "rois": rows},
        indent=1, default=str,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _wl_keys(values) -> tuple[float, ...]:
    return tuple(round(float(v), WAVELENGTH_KEY_DECIMALS) for v in values)


def _read_identity(handle: h5py.File) -> None:
    name = handle.attrs.get("schema_name")
    if name != SCHEMA_NAME:
        raise ScanFileError(f"not a scan file (schema_name {name!r}, expected {SCHEMA_NAME!r})")
    major = int(handle.attrs.get("schema_major", -1))
    if major != SCHEMA_MAJOR:
        raise ScanFileError(f"scan file schema {handle.attrs.get('schema_version')!r} is not supported (this version reads {SCHEMA_MAJOR}.x)")


def _strings(group: h5py.Group) -> dict[int, str]:
    return {int(name): group[name].asstr()[()] for name in group}


def read_scan(path: Path) -> StoredScan:
    """Read a whole scan file into memory (a few array reads; ~40 MB for 314 cubes x 160 ROIs)."""
    try:
        with h5py.File(path, "r") as f:
            _read_identity(f)
            scan = f["processed/scan"]
            if "n_masked" not in scan:
                raise ScanFileError(f"{Path(path).name} is from an older version (no mask counts): scan again to replace it")
            return StoredScan(
                path=Path(path),
                attrs={k: (v.item() if hasattr(v, "item") else v) for k, v in f.attrs.items()},
                cube_indices=tuple(int(c) for c in f["axes/cube_index"][()]),
                roi_ids=tuple(int(r) for r in f["axes/roi_id"][()]),
                wavelength_nm=tuple(float(w) for w in f["axes/wavelength_nm"][()]),
                acquired_at_unix_ms=f["axes/acquired_at_unix_ms"][()],
                values={name: scan["value"][name][()] for name in scan["value"]},
                std=scan["std"][()], n_valid=scan["n_valid"][()], n_nominal=scan["n_nominal"][()],
                n_masked=scan["n_masked"][()],
                cell_hash=f["state/cell_hash"][()], recipe_id=f["state/recipe_id"][()], layout_id=f["state/layout_id"][()],
                recipes=_strings(f["metadata/recipes"]), layouts=_strings(f["metadata/roi_layouts"]),
            )
    except (OSError, KeyError) as exc:
        raise ScanFileError(f"cannot read {path}: {exc}") from exc


def describe_file(path: Path) -> str | None:
    """One line about an existing scan file for the Workflow panel; None when there is no file. Opens it read-only and reads only the
    small ``cell_hash`` table, so it is cheap enough to call when a session is opened."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with h5py.File(path, "r") as f:
            _read_identity(f)
            done = int((f["state/cell_hash"][()] != 0).all(axis=1).sum())
            n_cube, n_roi = f["state/cell_hash"].shape
            n_wl = f["axes/wavelength_nm"].shape[0]
    except (OSError, KeyError, ScanFileError) as exc:
        return f"{path.name} cannot be read: {exc}"
    return f"{path.name}: {done} of {n_cube} cubes saved ({n_roi} ROIs, {n_wl} wavelengths, {path.stat().st_size / 1e6:.0f} MB)"


def stale_cell_matrix(stored: np.ndarray, current: np.ndarray) -> np.ndarray:
    """``bool [cube, roi]``: True where the stored cell differs from what the current inputs would give (never computed counts: its hash is 0).
    Both arguments are ``[cube, roi]`` uint64. Shapes that differ: everything is stale."""
    if stored.shape != current.shape:
        return np.ones(current.shape, dtype=bool)
    return stored != current


def stale_cube_positions(stored: np.ndarray, current: np.ndarray) -> list[int]:
    """Positions (rows) of the cubes where any cell differs from what the current inputs would give. Both ``[cube, roi]`` uint64."""
    return [int(p) for p in np.flatnonzero(stale_cell_matrix(stored, current).any(axis=1))]


def stale_cube_indices(stored: StoredScan, plan: ScanPlan, hashes: np.ndarray) -> tuple[frozenset[int], str]:
    """Which cubes of ``plan`` hold results that are not current, as dataset cube indices, and why when the whole file is affected.

    ``hashes`` is ``scan_fingerprint.cell_hashes(plan)``. If the file's axes (ROI ids, wavelengths, cubes) differ from the plan's, every cube
    of the plan is stale and the second value says which axis changed; otherwise it is empty and only cubes with a differing or missing cell
    are returned. Pure comparison, milliseconds."""
    everything = frozenset(int(c) for c in plan.cube_indices)
    if tuple(plan.roi_ids) != stored.roi_ids:
        return everything, "the ROIs changed (added, deleted or renumbered)"
    if _wl_keys(plan.wavelength_axis_nm) != _wl_keys(stored.wavelength_nm):
        return everything, "the wavelengths changed"
    if tuple(plan.dataset_cubes) != stored.cube_indices:
        return everything, "the cubes changed"
    rows = [stored.cube_indices.index(int(c)) for c in plan.cube_indices]
    stored_hashes = stored.cell_hash[rows]
    stale = stale_cell_matrix(stored_hashes, hashes)
    if stale.any():
        stale &= ~stamped_by_previous_formula(stored_hashes, hashes, plan)  # an old stamp on unchanged inputs is current
    return frozenset(int(plan.cube_indices[p]) for p in np.flatnonzero(stale.any(axis=1))), ""


_ROWS_PER_COPY = 16
_STATE_PATHS = ("state/cell_hash", "state/recipe_id", "state/layout_id")
_METADATA_GROUPS = ("metadata/recipes", "metadata/roi_layouts")


def _array_paths(f: h5py.File) -> list[str]:
    """The large datasets of a scan file (everything that used to be compressed)."""
    return [f"processed/scan/value/{m}" for m in f["processed/scan/value"]] + [
        "processed/scan/std", "processed/scan/n_valid", "processed/scan/n_nominal", "processed/scan/n_masked", "axes/acquired_at_unix_ms",
    ]


def _copy_scan_content(old: h5py.File, new: h5py.File) -> None:
    """Everything an ``old`` scan file holds into the empty skeleton ``new`` (same axes): attributes, arrays, state and metadata."""
    for key, value in old.attrs.items():
        new.attrs[key] = value
    for path in _array_paths(old) + list(_STATE_PATHS):
        source, target = old[path], new[path]
        if source.ndim == 0 or source.shape[0] == 0:
            continue
        for start in range(0, source.shape[0], _ROWS_PER_COPY):
            target[start:start + _ROWS_PER_COPY] = source[start:start + _ROWS_PER_COPY]
    for group in _METADATA_GROUPS:
        for name in old[group]:
            old.copy(f"{group}/{name}", new[group], name=name)


def _same_scan_content(old: h5py.File, new: h5py.File) -> bool:
    """True when ``new`` holds exactly what ``old`` holds: every array (NaN equal to NaN), the state, the metadata entries and their attributes."""
    for path in _array_paths(old) + list(_STATE_PATHS):
        source, target = old[path], new[path]
        if source.shape != target.shape or source.dtype != target.dtype:
            return False
        if source.ndim == 0 or source.shape[0] == 0:
            continue
        for start in range(0, source.shape[0], _ROWS_PER_COPY):
            if not np.array_equal(source[start:start + _ROWS_PER_COPY], target[start:start + _ROWS_PER_COPY], equal_nan=source.dtype.kind == "f"):
                return False
    for group in _METADATA_GROUPS:
        if set(old[group]) != set(new[group]):
            return False
        for name in old[group]:
            a, b = old[f"{group}/{name}"], new[f"{group}/{name}"]
            if a[()] != b[()] or dict(a.attrs) != dict(b.attrs):
                return False
    return {k: _plain(v) for k, v in old.attrs.items()} == {k: _plain(v) for k, v in new.attrs.items()}


def _plain(value):
    return value.tolist() if isinstance(value, np.ndarray) else value


class ScanStore:
    """The scan file of one session. ``prepare`` once per run, then ``write_cube`` per finished cube."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cube_row: dict[int, int] = {}
        self._recipe_id = 0
        self._layout_ids: dict[str, int] = {}

    # -- preparing a run -------------------------------------------------------------------------------------------------------

    def prepare(self, plan: ScanPlan, *, dataset_label: str = "", create: bool = True) -> np.ndarray:
        """Make the file ready for a run of ``plan`` and return the cell hashes it holds for the plan's cubes (``uint64 [cube, roi]``, 0 =
        never computed). Creates the file (or replaces an incompatible one, see the module docstring) and records the plan's recipe and ROI
        layouts. Call on the task thread, never while another writer has the file open.

        ``create=False`` is for a caller that only wants to add to a file that is already there and fits (ROI position tuning seeding the cells of the ROIs it
        moved): a missing or incompatible file raises :class:`ScanFileError` and is left exactly as it is (it is never replaced, backed up or created)."""
        if not create and not (self.path.exists() and self._compatible(plan)):
            raise ScanFileError(
                "there is no scan file that fits this dataset and these ROIs" if not self.path.exists() else "the scan file does not fit these ROIs or this dataset"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self._compatible(plan):
            backup = self.path.with_name(BACKUP_NAME)
            os.replace(self.path, backup)
            logger.warning("scan file does not fit this dataset or session any more; the old one is kept as %s", backup.name)
        if not self.path.exists():
            self._create(plan, dataset_label)
        elif self._is_compressed():
            self._convert_to_uncompressed(plan, dataset_label)
        self._cube_row = {int(c): i for i, c in enumerate(plan.dataset_cubes)}
        recipe = recipe_of(plan.settings)
        recipe_text, recipe_hex = recipe_json(recipe), recipe_digest(recipe).hex()
        layouts: dict[str, str] = {}
        for job in plan.jobs:  # most plans share one ROI set between all their cubes: describe each distinct layout once (314 x 1 ms before)
            if job.rois.key not in layouts:
                layouts[job.rois.key] = layout_json(job.rois)
        rows = [self._cube_row[int(c)] for c in plan.cube_indices]
        with h5py.File(self.path, "r") as f:  # read-only first: a run with nothing to do must not touch the file
            self._recipe_id = self._find(f["metadata/recipes"], "digest", recipe_hex)
            self._layout_ids = {key: self._find(f["metadata/roi_layouts"], "key", key) for key in layouts}
            stored = f["state/cell_hash"][()][rows]
        if self._recipe_id is None or any(v is None for v in self._layout_ids.values()):
            with h5py.File(self.path, "r+") as f:
                if self._recipe_id is None:
                    self._recipe_id = self._add(f["metadata/recipes"], recipe_text, "digest", recipe_hex)
                for key, rid in self._layout_ids.items():
                    if rid is None:
                        self._layout_ids[key] = self._add(f["metadata/roi_layouts"], layouts[key], "key", key)
        return stored

    def _compatible(self, plan: ScanPlan) -> bool:
        try:
            with h5py.File(self.path, "r") as f:
                _read_identity(f)
                return (
                    tuple(int(c) for c in f["axes/cube_index"][()]) == tuple(plan.dataset_cubes)
                    and tuple(int(r) for r in f["axes/roi_id"][()]) == tuple(plan.roi_ids)
                    and _wl_keys(f["axes/wavelength_nm"][()]) == _wl_keys(plan.wavelength_axis_nm)
                    and set(f["processed/scan/value"]) == set(plan.settings.methods)
                    and "n_masked" in f["processed/scan"]
                )
        except (OSError, KeyError, ScanFileError) as exc:
            logger.warning("existing scan file cannot be used: %s", exc)
            return False

    @staticmethod
    def _find(group: h5py.Group, attr: str, value: str) -> int | None:
        for name in group:
            if group[name].attrs.get(attr) == value:
                return int(name)
        return None

    @staticmethod
    def _add(group: h5py.Group, text: str, attr: str, value: str) -> int:
        """Store ``text`` as the next numbered entry (readable JSON) and return its id."""
        new_id = max((int(name) for name in group), default=0) + 1
        entry = group.create_dataset(str(new_id), data=text, dtype=_STRING)
        entry.attrs[attr] = value
        entry.attrs["added_at_utc"] = _utc_now()
        return new_id

    def _create(self, plan: ScanPlan, dataset_label: str) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        self._write_skeleton(plan, dataset_label, tmp)
        os.replace(tmp, self.path)

    @staticmethod
    def _write_skeleton(plan: ScanPlan, dataset_label: str, tmp: Path) -> None:
        """A new, empty scan file (every cell "never computed") at ``tmp``."""
        n_cube, n_roi, n_wl = len(plan.dataset_cubes), len(plan.roi_ids), len(plan.wavelength_axis_nm)
        shape = (n_cube, n_roi, n_wl, 2)
        chunks = (1, n_roi, n_wl, 2)  # one cube per chunk: the unit that is written
        if tmp.exists():
            tmp.unlink()
        now = _utc_now()
        with h5py.File(tmp, "w") as f:
            f.attrs.update({
                "schema_name": SCHEMA_NAME, "schema_version": SCHEMA_VERSION, "schema_major": SCHEMA_MAJOR, "schema_minor": SCHEMA_MINOR,
                "format_name": FORMAT_NAME, "format_version": FORMAT_VERSION, "app_name": APP_NAME, "app_version": APP_VERSION,
                "created_by": f"{APP_NAME} analysis scan", "created_at_utc": now, "started_at_utc": now,
                "dataset": dataset_label, "dims": ",".join(DIM_LABELS), "side_labels": json.dumps(list(SIDE_LABELS)),
                "methods": json.dumps(list(plan.settings.methods)),
            })
            axes = f.create_group("axes")
            scales = [
                axes.create_dataset("cube_index", data=np.asarray(plan.dataset_cubes, dtype=np.int32)),
                axes.create_dataset("roi_id", data=np.asarray(plan.roi_ids, dtype=np.int32)),
                axes.create_dataset("wavelength_nm", data=np.asarray(plan.wavelength_axis_nm, dtype=np.float64)),
                axes.create_dataset("side", data=np.asarray(SIDE_LABELS, dtype=object), dtype=_STRING),
            ]
            for scale, name in zip(scales, DIM_LABELS):
                scale.make_scale(name)
            axes.create_dataset("acquired_at_unix_ms", shape=(n_cube, n_wl), dtype=np.int64, fillvalue=0, chunks=(1, n_wl))
            scan = f.create_group("processed/scan")

            def array(group: h5py.Group, name: str, dtype, fill) -> None:
                ds = group.create_dataset(name, shape=shape, dtype=dtype, fillvalue=fill, chunks=chunks)  # plain: see the module docstring
                for i, (scale, label) in enumerate(zip(scales, DIM_LABELS)):
                    ds.dims[i].attach_scale(scale)
                    ds.dims[i].label = label

            values = scan.create_group("value")
            for method in plan.settings.methods:
                array(values, method, np.float64, np.nan)
            array(scan, "std", np.float64, np.nan)
            array(scan, "n_valid", np.uint32, 0)
            array(scan, "n_nominal", np.uint32, 0)
            array(scan, "n_masked", np.uint32, 0)
            f.create_group("metadata/recipes")
            f.create_group("metadata/roi_layouts")
            state = f.create_group("state")
            state.create_dataset("cell_hash", shape=(n_cube, n_roi), dtype=np.uint64, fillvalue=0, chunks=(1, n_roi))
            state.create_dataset("recipe_id", shape=(n_cube, n_roi), dtype=np.int32, fillvalue=0, chunks=(1, n_roi))
            state.create_dataset("layout_id", shape=(n_cube,), dtype=np.int32, fillvalue=0)

    # -- a file written with compression (before 2026-10-28) -------------------------------------------------------------------

    def _is_compressed(self) -> bool:
        try:
            with h5py.File(self.path, "r") as f:
                return any(f[path].compression is not None for path in _array_paths(f))
        except (OSError, KeyError):
            return False  # unreadable: the other checks of `prepare` have already dealt with it

    def _convert_to_uncompressed(self, plan: ScanPlan, dataset_label: str) -> None:
        """Replace a gzip file by an uncompressed one with exactly the same content. The copy is compared array for array before it is swapped
        in; on any problem (a reader holds the file open, the disk is full, a difference) the original stays as it is and the run goes on with it."""
        tmp = self.path.with_name(self.path.name + ".convert.tmp")
        started = time.perf_counter()
        try:
            self._write_skeleton(plan, dataset_label, tmp)
            with h5py.File(self.path, "r") as old, h5py.File(tmp, "r+") as new:
                _copy_scan_content(old, new)
                same = _same_scan_content(old, new)
            if not same:
                raise ScanFileError("the converted copy differs from the original")
            os.replace(tmp, self.path)
        except (OSError, KeyError, ScanFileError) as exc:
            logger.warning("could not convert the scan file to the uncompressed layout (%s); it stays as it is", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return
        logger.info("scan file converted to the uncompressed layout in %.1f s", time.perf_counter() - started)

    # -- writing ---------------------------------------------------------------------------------------------------------------

    def restamp(self, plan: ScanPlan, cells: np.ndarray, hashes: np.ndarray) -> int:
        """Give the cells flagged in ``cells`` (``bool [cube of plan, roi]``) the hashes ``hashes`` (``uint64``, the same shape), leaving their numbers
        alone: for cells whose inputs are known to be unchanged but whose stamp was made with an earlier hash formula
        (`scan_fingerprint.stamped_by_previous_formula`). Only the hash dataset is written. A torn write could at worst leave a wrong hash, which reads as
        "out of date" and is scanned again: never a wrong number shown as current. Returns the number of cells."""
        positions = [int(p) for p in np.flatnonzero(cells.any(axis=1))]
        if not positions:
            return 0
        entries = sorted((self._cube_row[int(plan.cube_indices[p])], p) for p in positions)
        with h5py.File(self.path, "r+") as f:
            dataset = f["state/cell_hash"]
            start = 0
            while start < len(entries):
                stop = start + 1
                while stop < len(entries) and entries[stop][0] == entries[stop - 1][0] + 1:
                    stop += 1
                lo, hi = entries[start][0], entries[stop - 1][0] + 1
                slab = dataset[lo:hi]
                for row, p in entries[start:stop]:
                    slab[row - lo, cells[p]] = hashes[p][cells[p]]
                dataset[lo:hi] = slab
                start = stop
            f.flush()
        return int(cells.sum())

    def write_cube(self, plan: ScanPlan, arrays: ScanArrays, cube_pos: int, cell_hash_row: np.ndarray) -> None:
        """Write one finished cube (position ``cube_pos`` in ``plan`` and ``arrays``) in the crash-safe order of the module docstring."""
        self.write_batch(plan, arrays, [(cube_pos, None, cell_hash_row)])

    def write_cells(self, plan: ScanPlan, arrays: ScanArrays, cube_pos: int, roi_rows, cell_hash_row: np.ndarray) -> None:
        """Write only the ROI rows ``roi_rows`` (positions on the ROI axis) of one finished cube, in the crash-safe order of the module docstring.
        ``arrays`` holds those rows at ``[cube_pos, roi_rows]`` (``scan_arrays.place``); ``cell_hash_row`` is the whole row of hashes of the cube,
        of which only ``roi_rows`` are written. Every other ROI row of the cube stays exactly as it is in the file."""
        self.write_batch(plan, arrays, [(cube_pos, roi_rows, cell_hash_row)])

    def write_batch(self, plan: ScanPlan, arrays: ScanArrays, items) -> None:
        """Write several finished cubes with one opening of the file. ``items``: ``(cube_pos, roi_rows, cell_hash_row)`` per cube, ``roi_rows``
        None for a whole cube (see :meth:`write_cube`) or the ROI rows to refresh (see :meth:`write_cells`). The crash-safe order holds for
        the whole batch: first the hashes of everything about to be replaced are set to 0 for **all** its cubes, then the values of all of
        them are written, then the real hashes; a crash leaves every cube of the batch complete or marked "never computed" (a cube is never
        half of each). Opening the file is 45 % of the cost of writing one cube (7.8 of 17 ms measured 2026-10-27), so batches are cheaper."""
        batch = []
        for cube_pos, roi_rows, cell_hash_row in items:
            idx = None if roi_rows is None else np.asarray(tuple(roi_rows), dtype=np.intp)
            batch.append((int(cube_pos), self._cube_row[int(plan.cube_indices[cube_pos])], idx, np.asarray(cell_hash_row, dtype=np.uint64)))
        if not batch:
            return
        if len({row for _, row, _, _ in batch}) != len(batch):
            raise ValueError("a cube appears twice in one batch")

        # One read-patch-write per dataset and per run of neighbouring cubes (the results of a task are four neighbours), not twenty small h5py calls per
        # cube (2026-10-28: 4.5 s of writer time for 314 cubes in the app). Only rows that are in the batch are ever rewritten, never the gaps between
        # runs: those cubes are current and a torn write must not be able to reach them.
        batch.sort(key=lambda item: item[1])
        runs: list[tuple[int, int]] = []  # [first, stop) positions in ``batch`` whose file rows are consecutive
        first = 0
        for k in range(1, len(batch) + 1):
            if k == len(batch) or batch[k][1] != batch[k - 1][1] + 1:
                runs.append((first, k))
                first = k

        def patch(dataset: h5py.Dataset, fresh_of) -> None:
            """Replace the batch's rows in ``dataset``: the whole cube, or only the ROI rows of ``idx`` (``fresh_of(cube_pos)`` gives the new numbers)."""
            for a, b in runs:
                lo, hi = batch[a][1], batch[b - 1][1] + 1
                slab = dataset[lo:hi]  # a copy
                for cube_pos, row, idx, _ in batch[a:b]:
                    fresh = fresh_of(cube_pos)
                    if idx is None:
                        slab[row - lo] = fresh
                    else:
                        slab[row - lo, idx] = fresh[idx]
                dataset[lo:hi] = slab

        with h5py.File(self.path, "r+") as f:
            state, scan = f["state"], f["processed/scan"]
            hashes = {}  # row -> the hashes of the cube as they stand while its values are being written
            for a, b in runs:
                lo, hi = batch[a][1], batch[b - 1][1] + 1
                block = state["cell_hash"][lo:hi]  # h5py hands over a copy
                for _, row, idx, _ in batch[a:b]:
                    if idx is None:
                        block[row - lo] = 0
                    else:
                        block[row - lo, idx] = 0
                    hashes[row] = block[row - lo].copy()
                state["cell_hash"][lo:hi] = block  # first: the old hash must not sit on values that are being replaced
            f.flush()
            for method, values in arrays.values.items():
                patch(scan["value"][method], lambda cube_pos, values=values: values[cube_pos])
            patch(scan["std"], lambda cube_pos: arrays.std[cube_pos])
            patch(scan["n_valid"], lambda cube_pos: arrays.n_valid[cube_pos])
            patch(scan["n_nominal"], lambda cube_pos: arrays.n_nominal[cube_pos])
            patch(scan["n_masked"], lambda cube_pos: arrays.n_masked[cube_pos])
            if plan.acquired_at_unix_ms is not None:
                for a, b in runs:
                    lo, hi = batch[a][1], batch[b - 1][1] + 1
                    f["axes/acquired_at_unix_ms"][lo:hi] = np.stack([plan.acquired_at_unix_ms[cube_pos] for cube_pos, _, _, _ in batch[a:b]])
            f.flush()
            for a, b in runs:
                lo, hi = batch[a][1], batch[b - 1][1] + 1
                recipe = state["recipe_id"][lo:hi]
                final = np.stack([hashes[row] for _, row, _, _ in batch[a:b]])
                for k, (cube_pos, row, idx, new_hashes) in enumerate(batch[a:b]):
                    if idx is None:
                        recipe[k] = self._recipe_id
                        final[k] = new_hashes
                    else:
                        recipe[k, idx] = self._recipe_id
                        final[k, idx] = new_hashes[idx]
                state["recipe_id"][lo:hi] = recipe
                state["layout_id"][lo:hi] = [self._layout_ids[plan.jobs[cube_pos].rois.key] for cube_pos, _, _, _ in batch[a:b]]
                state["cell_hash"][lo:hi] = final  # last: from here these cells count as computed
            f.flush()


class ScanWriter:
    """Writes finished cubes to the scan file on a thread of its own (2026-10-27).

    Why: the thread that collects results from the worker processes used to write each cube itself, and ``scan_cubes`` hands the pool new work
    only when its consumer comes back, so every write (17 ms idle, ~50 ms with the workers busy) left the workers waiting; in the 63-cube
    one-ROI refresh of the test export the writes were 3.2 s of 12.5 s. Now the collector only ``submit``s (a queue put) and carries on; the
    writer takes whatever is waiting, up to ``MAX_BATCH`` cubes, and writes it with one opening of the file (:meth:`ScanStore.write_batch`).

    Rules: the cubes of ``arrays`` handed to ``submit`` must be complete (``place`` done) and are not touched again; ``finish`` waits until
    everything submitted is on disk, also after a Cancel (finished cubes stay saved); an error of the writer (a disk that is full) is raised by
    the next ``submit`` or by ``finish``, in the collector's thread, and stops the writer. One writer per file at a time.
    """

    MAX_BATCH = 16

    def __init__(self, store: ScanStore, plan: ScanPlan, arrays: ScanArrays) -> None:
        self._store, self._plan, self._arrays = store, plan, arrays
        self._queue: queue.Queue = queue.Queue()
        self._error: BaseException | None = None
        self.busy_seconds = 0.0  # time spent inside the file, summed over the batches
        self.batches = 0
        self.cubes = 0
        self._thread = threading.Thread(target=self._run, name="analysis-scan-writer", daemon=True)
        self._thread.start()

    @property
    def error(self) -> BaseException | None:
        return self._error

    def submit(self, cube_pos: int, roi_rows, cell_hash_row: np.ndarray) -> None:
        """Queue a finished cube (``roi_rows`` None = the whole cube). Returns at once; raises the writer's error if it has failed."""
        if self._error is not None:
            raise self._error
        self._queue.put((int(cube_pos), None if roi_rows is None else tuple(roi_rows), cell_hash_row))

    def finish(self) -> None:
        """Wait until everything submitted is written and the thread has ended; raise the writer's error, if it had one."""
        self._queue.put(None)
        self._thread.join()
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        stop = False
        while not stop:
            item = self._queue.get()
            if item is None:
                return
            batch = [item]
            while len(batch) < self.MAX_BATCH:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:  # write this batch, then stop
                    stop = True
                    break
                batch.append(more)
            started = time.perf_counter()
            try:
                self._store.write_batch(self._plan, self._arrays, batch)
            except BaseException as exc:  # noqa: BLE001 - handed to the collector's thread, which decides what to do
                self._error = exc
                return
            self.busy_seconds += time.perf_counter() - started
            self.batches += 1
            self.cubes += len(batch)
