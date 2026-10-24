"""What makes a stored scan cell *current*: one combined hash per (cube, ROI) cell (maintainer's choice, plan doc section 15).

A cell is the scan of one ROI on one cube (all wavelengths, both sides). Its hash covers everything that decides the numbers:

* the **recipe**: the settings that reach the pixels (geometry, background, detection settings, reduction methods, exclusion mode,
  trimmed fraction) and :data:`SCAN_ALGORITHM_VERSION`;
* the cube's **ROI layout** (``RoiSet.key``: every ROI's geometry and the ring defaults; the reference ring of a ROI excludes the sample
  apertures of *all* ROIs, so one ROI moving changes every cell of that cube);
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
from dataclasses import asdict

import numpy as np

from .scan import ScanSettings
from .scan_jobs import ScanPlan

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


def cell_hashes(plan: ScanPlan) -> np.ndarray:
    """``uint64 [cube, roi]`` for the cubes of ``plan`` (0 is never returned: it means "never computed" in the file)."""
    base = recipe_digest(recipe_of(plan.settings))
    ignore_cache: dict[int, bytes] = {}  # one digest per distinct mask array (they are shared between frames)
    out = np.zeros((len(plan.jobs), len(plan.roi_ids)), dtype=np.uint64)
    for p, job in enumerate(plan.jobs):
        cube = hashlib.blake2b(digest_size=16)
        cube.update(base)
        cube.update(job.rois.key.encode("ascii"))
        for column, frame in zip(plan.axis_positions[p], job.frames):
            cube.update(np.int64(column).tobytes())
            cube.update(frame.path.replace("\\", "/").rsplit("/", 1)[-1].encode("utf-8"))
            cube.update(np.ascontiguousarray(frame.affine, dtype=np.float64).tobytes())
            if frame.ignore_key is not None:
                cube.update(frame.ignore_key)
            elif frame.ignore is None:
                cube.update(b"-")
            else:  # a hand-built job without a key: hash the mask itself
                key = id(frame.ignore)
                if key not in ignore_cache:
                    ignore_cache[key] = _ignore_digest(frame.ignore)
                cube.update(ignore_cache[key])
        digest = cube.digest()
        for r, roi_id in enumerate(plan.roi_ids):
            h = hashlib.blake2b(digest + np.int64(roi_id).tobytes() + np.int64(r).tobytes(), digest_size=8).digest()
            value = int.from_bytes(h, "little") or 1
            out[p, r] = value
    return out
