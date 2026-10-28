"""What makes a stored scan cell *current*: one combined hash per (cube, ROI) cell (maintainer's choice, plan doc section 15).

A cell is the scan of one ROI on one cube (all wavelengths, both sides). Its hash covers everything that decides the numbers:

* the **recipe**: the settings that reach the pixels (geometry, background, detection settings, reduction methods, exclusion mode,
  trimmed fraction) and :data:`SCAN_ALGORITHM_VERSION`;
* the **ROI geometry that decides the cell's pixels** (since 2026-10-27, ``frame_reduce.local_roi_keys``): the ROI's own geometry and that of
  the other ROIs whose sample disk can touch its reference ring (the ring leaves out the sample apertures of all ROIs). Moving one ROI changes
  its own cells, and a neighbour's only if the two came within reach; on a lattice with a ring smaller than the pitch, one ROI. **Exception:**
  when background removal is on *and* excludes the ROIs from its estimate (``flatten_background_exclude_area_rois``), the background of the
  whole frame depends on every ROI's position, so the hash covers the whole layout (``RoiSet.key``) exactly as before 2026-10-27 and a moved
  ROI makes every cell stale. Changing from the whole-layout hash to the local one changed every hash once: files written before read as out
  of date until a Fast scan refreshes them (same numbers, new hashes); sessions under the exception above kept their hashes;
* every frame of the cube: its wavelength column, the file *name* (not the folder, so moving a dataset does not invalidate anything), the
  chromatic affine and the **content** of the ignore mask (not a version number). The mask enters as what the processed-space mask is made
  from: a digest of the *authored* pixels plus the chromatic warp to this frame (``FrameJob.ignore_key``; the rotation and crop are in the
  recipe). Since 2026-10-24 the processed-space mask itself is not built to be hashed: that was 3 s of a 3.9 s check (411 masks decoded, rotated
  and warped) and gave the same answer, because the processed mask is a function of exactly those inputs. Changing from the old form (a digest of
  the resolved pixels) changed the hash of every cube that has a masked frame once (49 of 314 on the test dataset): those cubes read as out of
  date until a Fast scan refreshes them; the numbers they hold are unchanged, and cubes without a mask kept their hash;
* the ROI itself (id and position in the ROI list).

Known cost of a single combined hash: it says *that* a cell is stale, not *why*. ``recipe_id`` and the readable recipe / layout JSON in the
file (``scan_store``) are there so that a reader can compare by eye or by script.

Not in the hash, on purpose: the app version, the TIFF decoder choice (verified identical at run time), the worker count, and the detection
settings that only matter to other steps (``formula_key``, ``reduction_method``, the array-detection fields): changing the formula must never
make a stored scan stale (CLAUDE.md: formula, fit and metric are never stored). **Bump** :data:`SCAN_ALGORITHM_VERSION` whenever a change to
``frame_reduce``, the background estimate, the rotation or the reduction can change a stored number; that makes every cell stale.

Pure, no Qt. Cost on the real dataset: 8,164 frames and 411 ignore masks, a fraction of a second.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import asdict

import numpy as np

from ..image_tools.geometry.transform import geometry_cache_key
from .frame_reduce import local_roi_keys, roi_window_boxes
from .scan import ScanSettings
from .scan_jobs import ScanPlan

logger = logging.getLogger(__name__)

SCAN_ALGORITHM_VERSION = 1
"""1 = analysis step 1 as built 2026-10-19..23: float64 mean via bincount, STD of the valid pixels, Lanczos4 rotation, divide-by-background
at the ROI pixels, reference-ring exclusion of all sample apertures."""

DETECTION_FIELDS_NOT_IN_THE_SCAN = frozenset({"reduction_method", "formula_key", "array_rows", "array_cols", "array_spacing_px"})


def recipe_of(settings: ScanSettings) -> dict:
    """The settings that decide the numbers, as plain JSON-ready data (this dict is stored in the file as readable JSON)."""
    return {
        "algorithm_version": SCAN_ALGORITHM_VERSION,
        "geometry": asdict(settings.geometry),
        "background": asdict(settings.background),
        "detection": {k: v for k, v in asdict(settings.detection).items() if k not in DETECTION_FIELDS_NOT_IN_THE_SCAN},
        "methods": list(settings.methods),
        "exclusion_mode": settings.exclusion_mode,
        "trimmed_fraction": float(settings.trimmed_fraction),
    }


def recipe_json(recipe: dict) -> str:
    """Canonical text of a recipe: sorted keys, so the same recipe is always the same bytes; readable."""
    return json.dumps(recipe, sort_keys=True, indent=1, default=str)


def recipe_digest(recipe: dict) -> bytes:
    return hashlib.blake2b(recipe_json(recipe).encode("utf-8"), digest_size=16).digest()


def _ignore_digest(mask: np.ndarray) -> bytes:
    return hashlib.blake2b(np.packbits(np.ascontiguousarray(mask, dtype=bool)).tobytes() + str(mask.shape).encode(), digest_size=16).digest()


def layout_is_global(settings: ScanSettings) -> bool:
    """True when the numbers of a ROI depend on where *every* ROI is, whatever the distances: background removal on, with the ROIs cut out of
    its estimate. Then a moved ROI changes the background of the whole frame (the estimate and its whole-frame baseline), so no cell can be
    called current after any ROI moved."""
    return bool(settings.background.flatten_background_enabled and settings.background.flatten_background_exclude_area_rois)


MASK_BLOCK_PX = 16
"""Side of the blocks of the coarse occupancy grid of a mask (`_MaskFootprints`): a ROI whose window meets no occupied block needs no look at the pixels."""


def masks_are_global(settings: ScanSettings) -> bool:
    """True when the numbers of a ROI depend on the whole ignore mask of its frame: background removal on, with the masked pixels cut out of its
    estimate (the background of the whole frame then depends on every masked pixel). Then the mask enters every cell as a whole, as it always did."""
    return bool(settings.background.flatten_background_enabled and settings.background.flatten_background_exclude_mask)


class _MaskFootprints:
    """What a mask looks like to a ROI, remembered for the life of the process (2026-10-28).

    Per mask (its ``ignore_key`` and the geometry it was made under): a coarse occupancy grid of 16 px blocks as a summed-area table, so "does this
    mask touch this ROI's window?" is four lookups for every ROI at once; and, only for the few (mask, window) pairs that do touch, the exact digest of
    the mask pixels in the window. Keys are *content* (``ignore_key`` is a digest of the authored pixels and the chromatic warp; the geometry key
    covers rotation, flip and crop), so nothing here can go out of date: a changed mask is a different key. Bounded; a forgotten entry is only
    made again. Thread-safe (the scan task and the results check run in different threads)."""

    GRIDS_MAX = 4096
    CROPS_MAX = 300_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tables: OrderedDict[tuple, tuple[np.ndarray, tuple[int, int]]] = OrderedDict()
        self._crops: dict[tuple, bytes] = {}

    def table(self, key: bytes, geometry_key: tuple, mask_of) -> tuple[np.ndarray, tuple[int, int]]:
        """(summed-area table of the occupied blocks ``int32 [rows + 1, cols + 1]``, (height, width) of the mask) for a mask."""
        entry_key = (key, geometry_key)
        with self._lock:
            hit = self._tables.get(entry_key)
            if hit is not None:
                self._tables.move_to_end(entry_key)
                return hit
        mask = mask_of()
        height, width = mask.shape
        rows, cols = -(-height // MASK_BLOCK_PX), -(-width // MASK_BLOCK_PX)
        padded = np.zeros((rows * MASK_BLOCK_PX, cols * MASK_BLOCK_PX), dtype=bool)
        padded[:height, :width] = mask
        # two reductions over contiguous axes: 0.6 ms for a 1 MP mask, where any(axis=(1, 3)) over the 4-D view takes 2.5 ms (measured)
        grid = padded.reshape(rows, MASK_BLOCK_PX, cols * MASK_BLOCK_PX).any(axis=1).reshape(rows, cols, MASK_BLOCK_PX).any(axis=2)
        table = np.zeros((rows + 1, cols + 1), dtype=np.int32)
        table[1:, 1:] = grid.cumsum(axis=0).cumsum(axis=1)
        entry = (table, (int(height), int(width)))
        with self._lock:
            self._tables[entry_key] = entry
            while len(self._tables) > self.GRIDS_MAX:
                self._tables.popitem(last=False)
        return entry

    @staticmethod
    def touched(table: np.ndarray, boxes: tuple[np.ndarray, ...]) -> np.ndarray:
        """``bool [roi]``: some occupied block lies in the ROI's window (the blocks the box overlaps: a superset of the box, so never a miss)."""
        y0, y1, x0, x1 = boxes[:4]
        by0, by1 = y0 // MASK_BLOCK_PX, -(-y1 // MASK_BLOCK_PX)
        bx0, bx1 = x0 // MASK_BLOCK_PX, -(-x1 // MASK_BLOCK_PX)
        count = table[by1, bx1] - table[by0, bx1] - table[by1, bx0] + table[by0, bx0]
        return (count > 0) & (y1 > y0) & (x1 > x0)

    def crop_digest(self, key: bytes, geometry_key: tuple, box: tuple[int, int, int, int], mask_of) -> bytes:
        """Digest of the mask pixels in ``box`` (y0, y1, x0, x1); ``b"-"`` when there are none, which is what no mask at all would give."""
        entry_key = (key, geometry_key, *box)
        with self._lock:
            hit = self._crops.get(entry_key)
        if hit is not None:
            return hit
        y0, y1, x0, x1 = box
        crop = mask_of()[y0:y1, x0:x1]
        digest = hashlib.blake2b(np.packbits(crop).tobytes() + np.array(box, dtype=np.int64).tobytes(), digest_size=16).digest() if crop.any() else b"-"
        with self._lock:
            if len(self._crops) >= self.CROPS_MAX:
                self._crops.clear()
            self._crops[entry_key] = digest
        return digest


_FOOTPRINTS = _MaskFootprints()


def cell_hashes(plan: ScanPlan, *, local_masks: bool = True) -> np.ndarray:
    """``uint64 [cube, roi]`` for the cubes of ``plan`` (0 is never returned: it means "never computed" in the file).

    **The mask part is local since 2026-10-28** (``local_masks=True``): a cell's numbers depend on an ignore mask only through the mask pixels the
    ROI can read, and those lie in the ROI's window (`frame_reduce.roi_window_boxes`, in the frame's own geometry), so a cell is hashed with the mask
    pixels *in its window* and not with the whole mask. A mask edit then makes stale only the ROIs it touches, instead of every ROI of every cube it
    applies to. A frame whose mask does not meet a ROI's window contributes nothing to that ROI's hash: it hashes as if the frame had no mask, which
    is also what the numbers are. Whole-mask hashing remains for the cases that cannot be local: background removal that cuts the masked pixels out
    of its estimate (`masks_are_global`), a plan that cannot make its masks (no ``mask_resolver``), a frame whose mask cannot be made, and a free-form
    ROI (no bound). ``local_masks=False`` gives the hashes as they were before (cubes without masks never changed): used to recognise cells stamped
    with the previous formula, whose inputs are then known to be unchanged (`stamped_by_previous_formula`)."""
    base = recipe_digest(recipe_of(plan.settings))
    local = bool(local_masks) and not masks_are_global(plan.settings)
    geometry_key = geometry_cache_key(plan.settings.geometry) if local else None
    box_cache: dict[tuple, tuple[np.ndarray, ...]] = {}
    ignore_cache: dict[int, bytes] = {}  # one digest per distinct mask array (they are shared between frames)
    global_layout = layout_is_global(plan.settings)
    # Per ROI layout (cubes share one unless there is a ROI timeline): the bytes that follow a cube's digest in every one of its cells. Built once,
    # not per cell: the same bytes as ``own + int64(roi_id) + int64(position)`` made cell by cell (50,000 cells took 0.25 s that way, 0.1 s now).
    roi_bytes = [np.int64(roi_id).tobytes() + np.int64(r).tobytes() for r, roi_id in enumerate(plan.roi_ids)]
    tails: dict[str, list[bytes]] = {}
    column_bytes: dict[int, bytes] = {}
    out = np.zeros((len(plan.jobs), len(plan.roi_ids)), dtype=np.uint64)
    for p, job in enumerate(plan.jobs):
        cube = hashlib.blake2b(digest_size=16)
        cube.update(base)
        masked: list[tuple] = []  # (column, frame, mask key, column bytes) of the frames whose mask enters per ROI
        if global_layout:
            cube.update(job.rois.key.encode("ascii"))
            if "" not in tails:
                tails[""] = roi_bytes  # no per-ROI part: the whole layout is in the cube's digest
        elif job.rois.key not in tails:
            tails[job.rois.key] = [own + tail for own, tail in zip(local_roi_keys(job.rois, plan.settings.exclusion_mode), roi_bytes)]
        for column, frame in zip(plan.axis_positions[p], job.frames):
            packed_column = column_bytes.get(column)
            if packed_column is None:
                packed_column = column_bytes[column] = np.int64(column).tobytes()
            cube.update(packed_column)
            cube.update(frame.path.replace("\\", "/").rsplit("/", 1)[-1].encode("utf-8"))
            affine = frame.affine
            if isinstance(affine, np.ndarray) and affine.dtype == np.float64 and affine.flags.c_contiguous:
                cube.update(affine.tobytes())  # the common case, without the conversion call
            else:
                cube.update(np.ascontiguousarray(affine, dtype=np.float64).tobytes())
            if frame.ignore_key is not None:
                mask_key = frame.ignore_key
            elif frame.ignore is None:
                mask_key = None
            else:  # a hand-built job without a key: hash the mask itself
                key = id(frame.ignore)
                if key not in ignore_cache:
                    ignore_cache[key] = _ignore_digest(frame.ignore)
                mask_key = ignore_cache[key]
            if mask_key is None:
                cube.update(b"-")
            elif local and (frame.ignore is not None or plan.mask_resolver is not None):
                cube.update(b"-")  # the mask enters per ROI below, only where the ROI can see it
                masked.append((column, frame, mask_key, packed_column))
            else:
                cube.update(mask_key)  # the whole mask, as before
        parts: dict[int, list[bytes]] = {}
        for column, frame, mask_key, packed_column in masked:
            if frame.ignore is not None:
                mask_of = lambda array=frame.ignore: np.asarray(array, dtype=bool)  # noqa: E731
            else:
                mask_of = lambda key=mask_key: np.asarray(plan.mask_resolver(key), dtype=bool)  # noqa: E731
            try:
                table, shape = _FOOTPRINTS.table(mask_key, geometry_key, mask_of)
                if table[-1, -1] == 0:
                    continue  # an empty mask touches nobody
                box_key = (job.rois.key, np.ascontiguousarray(frame.affine, dtype=np.float64).tobytes(), shape)
                boxes = box_cache.get(box_key)
                if boxes is None:
                    boxes = box_cache[box_key] = roi_window_boxes(job.rois, np.asarray(frame.affine, dtype=np.float64), shape)
                for j in np.flatnonzero(_MaskFootprints.touched(table, boxes)):
                    box = (int(boxes[0][j]), int(boxes[1][j]), int(boxes[2][j]), int(boxes[3][j]))
                    window = _FOOTPRINTS.crop_digest(mask_key, geometry_key, box, mask_of)
                    if window != b"-":
                        parts.setdefault(int(j), []).append(packed_column + window)
            except Exception:  # noqa: BLE001 - a mask that cannot be looked at counts as a whole, for every ROI of the cube: stale rather than wrong
                logger.warning("could not look at an ignore mask for the cell hash; it counts for every ROI of its cube", exc_info=True)
                cube.update(b"!" + mask_key)
        digest = cube.digest()
        row_tails = tails["" if global_layout else job.rois.key]
        if parts:
            row_tails = [tail + b"".join(parts.get(r, ())) for r, tail in enumerate(row_tails)]
        out[p] = [
            int.from_bytes(hashlib.blake2b(digest + tail, digest_size=8).digest(), "little") or 1
            for tail in row_tails
        ]
    return out


def stamped_by_previous_formula(stored: np.ndarray, current: np.ndarray, plan: ScanPlan) -> np.ndarray:
    """``bool [cube, roi]``: cells whose stored hash is not the current one but **is** what the formula before 2026-10-28 (whole ignore mask in every
    cell, ``cell_hashes(plan, local_masks=False)``) gives for the current inputs. Their inputs have not changed since they were computed, so the
    numbers are current and only the stamp is old: they are re-stamped, not scanned again. Without this, the first Fast scan after the change
    would redo every cube that has an ignore mask. Costs one more hash pass, and only when something differs."""
    differs = (stored != current) & (stored != 0)
    if stored.shape != current.shape or not differs.any():
        return np.zeros(current.shape, dtype=bool)
    return differs & (stored == cell_hashes(plan, local_masks=False))
