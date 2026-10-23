"""The scan result file ``analysis/scan.h5`` (schema ``lspri_scan`` 1.0). Layout and decisions: plan doc section 15. No Qt.

Holds only what step 1 produces: per cube, ROI, wavelength and side the reduced pixel **mean**, **STD**, ``n_valid`` and ``n_nominal``
(absorbance, fits and metrics are recomputed from these, never stored). Readable with plain ``h5py`` / ``xarray`` / pandas::

    /processed/scan/value/mean  float64 [cube, roi, wavelength, side]    side 0 = sample, 1 = reference; NaN = no value
    /processed/scan/std         float64 [same]      spread of the valid pixels (SEM = std / sqrt(n_valid))
    /processed/scan/n_valid     uint32  [same]      pixels with a value
    /processed/scan/n_nominal   uint32  [same]      pixels in the aperture after the exclusions
    /axes/   cube_index, roi_id, wavelength_nm, side (HDF5 dimension scales), acquired_at_unix_ms int64 [cube, wavelength] (0 = unknown)
    /metadata/recipes/<n>, /metadata/roi_layouts/<n>   readable JSON (what produced the numbers; see scan_fingerprint)
    /state/  cell_hash uint64 [cube, roi] (0 = never computed), recipe_id int32 [cube, roi], layout_id int32 [cube]

The axes are the whole dataset's (every spectral cube, the wavelength union, the ROI ids), so a partial run and a full run share one file.

**Crash safety.** A cube is written in this order: its ``cell_hash`` row is set to 0, the values are written and flushed, then the real
hash, ``recipe_id`` and ``layout_id`` are written. A crash at any point leaves a cube either complete or marked "never computed"; a stale
hash can never sit on half-written values. The file is created under a temporary name and renamed, so a half-made file is never taken for a
result. One writer at a time (the scan task's thread); readers open it read-only.

**When the file does not fit** (other ROI ids, wavelength axis, cube list or reduction methods; or an unknown schema) the old file is
moved to ``scan.previous.h5`` (one backup slot) and a new one is made: every stored cell is stale then anyway (decision 2026-10-23), and
nothing is deleted without a copy.
"""

from __future__ import annotations

import json
import logging
import os
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
SCHEMA_VERSION = "1.0"
SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
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
            return StoredScan(
                path=Path(path),
                attrs={k: (v.item() if hasattr(v, "item") else v) for k, v in f.attrs.items()},
                cube_indices=tuple(int(c) for c in f["axes/cube_index"][()]),
                roi_ids=tuple(int(r) for r in f["axes/roi_id"][()]),
                wavelength_nm=tuple(float(w) for w in f["axes/wavelength_nm"][()]),
                acquired_at_unix_ms=f["axes/acquired_at_unix_ms"][()],
                values={name: scan["value"][name][()] for name in scan["value"]},
                std=scan["std"][()], n_valid=scan["n_valid"][()], n_nominal=scan["n_nominal"][()],
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


def stale_cube_positions(stored: np.ndarray, current: np.ndarray) -> list[int]:
    """Positions (rows) of the cubes where any cell differs from what the current inputs would give. Both ``[cube, roi]`` uint64."""
    if stored.shape != current.shape:
        return list(range(current.shape[0]))
    return [int(p) for p in np.flatnonzero((stored != current).any(axis=1))]


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
        row = self._cube_row[int(plan.cube_indices[cube_pos])]
        with h5py.File(self.path, "r+") as f:
            state = f["state"]
            state["cell_hash"][row, :] = 0  # first: the old hash must not sit on values that are being replaced
            f.flush()
            scan = f["processed/scan"]
            for method, values in arrays.values.items():
                scan["value"][method][row] = values[cube_pos]
            scan["std"][row] = arrays.std[cube_pos]
            scan["n_valid"][row] = arrays.n_valid[cube_pos]
            scan["n_nominal"][row] = arrays.n_nominal[cube_pos]
            if plan.acquired_at_unix_ms is not None:
                f["axes/acquired_at_unix_ms"][row] = plan.acquired_at_unix_ms[cube_pos]
            f.flush()
            state["recipe_id"][row, :] = self._recipe_id
            state["layout_id"][row] = self._layout_ids[plan.jobs[cube_pos].rois.key]
            state["cell_hash"][row, :] = cell_hash_row  # last: from here the cube counts as computed
            f.flush()
