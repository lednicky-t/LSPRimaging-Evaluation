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

from ..version_rewrite import APP_VERSION, REWRITE_APP_NAME
from .frame_reduce import RoiSet
from .scan_arrays import ScanArrays
from .scan_fingerprint import recipe_digest, recipe_json, recipe_of
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
    positions = stale_cube_positions(stored.cell_hash[rows], hashes)
    return frozenset(int(plan.cube_indices[p]) for p in positions), ""


class ScanStore:
    """The scan file of one session. ``prepare`` once per run, then ``write_cube`` per finished cube."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cube_row: dict[int, int] = {}
        self._recipe_id = 0
        self._layout_ids: dict[str, int] = {}

    # -- preparing a run -------------------------------------------------------------------------------------------------------

    def prepare(self, plan: ScanPlan, *, dataset_label: str = "") -> np.ndarray:
        """Make the file ready for a run of ``plan`` and return the cell hashes it holds for the plan's cubes (``uint64 [cube, roi]``, 0 =
        never computed). Creates the file (or replaces an incompatible one, see the module docstring) and records the plan's recipe and ROI
        layouts. Call on the task thread, never while another writer has the file open."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self._compatible(plan):
            backup = self.path.with_name(BACKUP_NAME)
            os.replace(self.path, backup)
            logger.warning("scan file does not fit this dataset or session any more; the old one is kept as %s", backup.name)
        if not self.path.exists():
            self._create(plan, dataset_label)
        self._cube_row = {int(c): i for i, c in enumerate(plan.dataset_cubes)}
        recipe = recipe_of(plan.settings)
        recipe_text, recipe_hex = recipe_json(recipe), recipe_digest(recipe).hex()
        layouts = {job.rois.key: layout_json(job.rois) for job in plan.jobs}
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
        n_cube, n_roi, n_wl = len(plan.dataset_cubes), len(plan.roi_ids), len(plan.wavelength_axis_nm)
        shape = (n_cube, n_roi, n_wl, 2)
        chunks = (1, n_roi, n_wl, 2)  # one cube per chunk: the unit that is written
        tmp = self.path.with_name(self.path.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        now = _utc_now()
        with h5py.File(tmp, "w") as f:
            f.attrs.update({
                "schema_name": SCHEMA_NAME, "schema_version": SCHEMA_VERSION, "schema_major": SCHEMA_MAJOR, "schema_minor": SCHEMA_MINOR,
                "format_name": FORMAT_NAME, "format_version": FORMAT_VERSION, "app_name": REWRITE_APP_NAME, "app_version": APP_VERSION,
                "created_by": f"{REWRITE_APP_NAME} analysis scan", "created_at_utc": now, "started_at_utc": now,
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
            axes.create_dataset("acquired_at_unix_ms", shape=(n_cube, n_wl), dtype=np.int64, fillvalue=0, chunks=(1, n_wl), compression="gzip")
            scan = f.create_group("processed/scan")

            def array(group: h5py.Group, name: str, dtype, fill) -> None:
                ds = group.create_dataset(name, shape=shape, dtype=dtype, fillvalue=fill, chunks=chunks, compression="gzip", compression_opts=4, shuffle=True)
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
        os.replace(tmp, self.path)

    # -- writing ---------------------------------------------------------------------------------------------------------------

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

        def put(dataset: h5py.Dataset, row: int, fresh: np.ndarray, idx: np.ndarray | None) -> None:
            if idx is None:
                dataset[row] = fresh
                return
            block = dataset[row]  # the cube's rows are one chunk: read it, replace the rows, write it back
            block[idx] = fresh[idx]
            dataset[row] = block

        with h5py.File(self.path, "r+") as f:
            state, scan = f["state"], f["processed/scan"]
            hashes = {}  # row -> the hashes of the cube as they stand while its values are being written
            for _, row, idx, _ in batch:
                current = state["cell_hash"][row, :]  # h5py hands over a copy
                if idx is None:
                    current[:] = 0
                else:
                    current[idx] = 0
                hashes[row] = current
                state["cell_hash"][row, :] = current  # first: the old hash must not sit on values that are being replaced
            f.flush()
            for cube_pos, row, idx, _ in batch:
                for method, values in arrays.values.items():
                    put(scan["value"][method], row, values[cube_pos], idx)
                put(scan["std"], row, arrays.std[cube_pos], idx)
                put(scan["n_valid"], row, arrays.n_valid[cube_pos], idx)
                put(scan["n_nominal"], row, arrays.n_nominal[cube_pos], idx)
                put(scan["n_masked"], row, arrays.n_masked[cube_pos], idx)
                if plan.acquired_at_unix_ms is not None:
                    f["axes/acquired_at_unix_ms"][row] = plan.acquired_at_unix_ms[cube_pos]
            f.flush()
            for cube_pos, row, idx, new_hashes in batch:
                recipe = state["recipe_id"][row, :]
                final = hashes[row]
                if idx is None:
                    recipe[:] = self._recipe_id
                    final[:] = new_hashes
                else:
                    recipe[idx] = self._recipe_id
                    final[idx] = new_hashes[idx]
                state["recipe_id"][row, :] = recipe
                state["layout_id"][row] = self._layout_ids[plan.jobs[cube_pos].rois.key]
                state["cell_hash"][row, :] = final  # last: from here these cells count as computed
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
