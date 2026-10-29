"""ROI position tuning, step E (2026-10-29): what Accept does with a finished trial. Pure apart from the scan file it writes. No Qt.

Accepting moves the ROIs whose best centre is not their current one (:func:`accepted_moves`; the ROI toolbox does the move, as one undo step) and, so that the cubes of
the two steps are not scanned again, **copies the numbers of the best position from the trial's matrix into the session's ``scan.h5``** (:func:`seed_scan_file`).
The maintainer's rule (2026-10-28): "there is no recomputation".

Why that is exact, and when it is refused (priority 1, correctness, and 2, data integrity: a seeded cell must be the cell a real scan would have written, so the
function writes nothing unless it can show that):

* the trial scanned every position with the ROIs at exactly the centres the toolbox stores after the move (``roi_tuning.moved_centre``: 0.1 px rounding);
* a ROI's numbers depend on its own pixels and on the sample discs of the ROIs near its reference ring and nothing else, so they are the same whether the other tuned
  ROIs are at their old or their new centres **when no other ROI is within reach** of it over the lattice (``TuningMatrix.interacting_pairs``): the ROIs of such a pair
  are moved but not seeded, they are scanned the normal way;
* what the trial was made from still holds: the cell hashes of the *unmoved* layout, recomputed now from a snapshot taken before the move, equal the ones stored in the
  matrix (``TuningMatrix.base_hashes``). Any difference (a mask, the chromatic correction, the background, a diameter ... changed after the trial) and nothing is written;
* the file is only added to when it fits the dataset and the ROIs (``ScanStore.prepare(create=False)``): a missing or incompatible file is never created, replaced or backed up.

Each written cell carries the hash a real scan of the moved layout gives (``scan_fingerprint.cell_hashes`` of a snapshot taken after the move), written in the file's
crash-safe order (``ScanStore.write_batch``), so a later Fast scan finds those cells current. The cubes outside the two steps are stale after any ROI move, as always,
until the next Fast scan. The raw data is never touched; undoing the move makes the seeded cells stale (they were made for the new centres), which costs a scan, not a number.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..applog import log_event
from .roi_tuning import moved_centre
from .roi_tuning_objectives import Ranking
from .roi_tuning_run import TuningMatrix
from .scan_arrays import new_arrays
from .scan_fingerprint import cell_hashes
from .scan_jobs import ScanPlan
from .scan_store import ScanFileError, ScanStore

logger = logging.getLogger(__name__)


def _check_same_lattice(matrix: TuningMatrix, ranking: Ranking) -> None:
    if ranking.roi_ids != matrix.roi_ids or ranking.offsets.shape != matrix.offsets.shape or not np.array_equal(ranking.offsets, matrix.offsets):
        raise ValueError("the ranking was not made from this matrix")


def accepted_moves(matrix: TuningMatrix, ranking: Ranking) -> dict[int, tuple[float, float]]:
    """ROI id -> new centre (processed px, as the toolbox will store it) for every ROI whose best position is not its current centre. Empty if nothing moves."""
    _check_same_lattice(matrix, ranking)
    moves: dict[int, tuple[float, float]] = {}
    for j, roi_id in enumerate(matrix.roi_ids):
        if not ranking.moves[j]:
            continue
        dx, dy = ranking.best_offsets[j]  # the best lattice point, or the position interpolated next to it
        x, y = moved_centre(matrix.roi_xy[j, 0], matrix.roi_xy[j, 1], float(dx), float(dy))
        if (x, y) != (float(matrix.roi_xy[j, 0]), float(matrix.roi_xy[j, 1])):
            moves[int(roi_id)] = (x, y)
    return moves


def seedable_columns(matrix: TuningMatrix, ranking: Ranking) -> list[int]:
    """Columns (positions in ``matrix.roi_ids``) of the moved ROIs whose numbers can be copied into the scan file: those that are in no interacting pair and whose new
    centre is a lattice point. A position interpolated between lattice points (``Ranking.refined``) was never scanned, so there are no numbers to copy: that ROI is
    moved, and the next scan computes it."""
    interacting = {int(r) for pair in matrix.interacting_pairs for r in pair}
    moved = set(accepted_moves(matrix, ranking))
    refined = ranking.refined
    return [j for j, roi_id in enumerate(matrix.roi_ids) if int(roi_id) in moved and int(roi_id) not in interacting and not refined[j]]


@dataclass(frozen=True)
class SeedReport:
    """What seeding the scan file did. ``written`` False: nothing was written and ``reason`` says why, in a sentence for the status bar."""

    written: bool
    reason: str = ""
    roi_ids: tuple[int, ...] = ()  # the ROIs whose numbers were written
    cubes: int = 0
    not_saved: tuple[int, ...] = field(default=())  # moved ROIs left to the next scan because a neighbour is within reach
    seconds: float = 0.0


def seed_scan_file(
    path: Path,
    matrix: TuningMatrix,
    ranking: Ranking,
    plan_before: ScanPlan,
    plan_after: ScanPlan,
    *,
    dataset_label: str = "",
) -> SeedReport:
    """Write the best position's numbers of every seedable moved ROI into the scan file at ``path`` (rules: module docstring).

    ``plan_before`` / ``plan_after``: ``build_scan_plan(snapshot, cubes=matrix.cube_indices, resolve_masks=False)`` of the app **before** and **after** the move
    (masks need not be resolved: only hashes and the cube / layout bookkeeping are read from them). Never raises for a file problem: the report says what happened."""
    started = time.perf_counter()
    _check_same_lattice(matrix, ranking)

    def refused(reason: str, skipped: tuple[int, ...] = ()) -> SeedReport:
        log_event(logger, "tuning.seed_refused", logging.INFO, reason=reason)
        return SeedReport(False, reason, not_saved=skipped, seconds=time.perf_counter() - started)

    moved = accepted_moves(matrix, ranking)
    columns = seedable_columns(matrix, ranking)
    skipped = tuple(int(r) for r in matrix.roi_ids if int(r) in moved and int(r) not in {matrix.roi_ids[j] for j in columns})
    if not columns:
        return refused("No moved ROI can have its numbers saved (none moved, or their neighbours are too close); the next scan computes them.", skipped)
    if tuple(plan_before.roi_ids) != tuple(plan_after.roi_ids) or tuple(plan_after.cube_indices) != tuple(matrix.cube_indices):
        return refused("The ROIs or the cubes are not those of the trial; the next scan computes the moved ROIs.", skipped)
    if tuple(plan_after.wavelength_axis_nm) != tuple(matrix.wavelength_nm):
        return refused("The wavelengths are not those of the trial; the next scan computes the moved ROIs.", skipped)
    try:
        rows = [list(plan_after.roi_ids).index(int(r)) for r in matrix.roi_ids]
    except ValueError:
        return refused("A ROI of the trial is no longer in the scan; the next scan computes the moved ROIs.", skipped)

    before = np.asarray(cell_hashes(plan_before), dtype=np.uint64)[:, rows]
    if before.shape != matrix.base_hashes.shape or not np.array_equal(before, matrix.base_hashes):
        return refused(
            "Something the trial was made from changed (a mask, the chromatic correction, the background, a ROI setting ...), so its numbers were not saved; "
            "a Fast scan computes the moved ROIs.", skipped,
        )
    after = np.asarray(cell_hashes(plan_after), dtype=np.uint64)

    try:
        store = ScanStore(path)
        store.prepare(plan_after, dataset_label=dataset_label, create=False)
    except ScanFileError as exc:
        return refused(f"The numbers were not saved: {exc}. Run a scan to compute the moved ROIs.", skipped)

    arrays = new_arrays(plan_after)
    seeded_rows = [rows[j] for j in columns]
    for j, row in zip(columns, seeded_rows, strict=True):
        best = int(ranking.best[j])
        arrays.values["mean"][:, row] = matrix.mean[best, :, j]
        arrays.std[:, row] = matrix.std[best, :, j]
        arrays.n_valid[:, row] = matrix.n_valid[best, :, j]
        arrays.n_nominal[:, row] = matrix.n_nominal[best, :, j]
        arrays.n_masked[:, row] = matrix.n_masked[best, :, j]
    arrays.done[:] = True
    items = [(p, seeded_rows, after[p]) for p in range(len(plan_after.jobs))]
    try:
        store.write_batch(plan_after, arrays, items)
    except OSError as exc:
        return refused(f"The scan file could not be written ({exc}); the next scan computes the moved ROIs.", skipped)
    seconds = time.perf_counter() - started
    roi_ids = tuple(int(matrix.roi_ids[j]) for j in columns)
    log_event(logger, "tuning.seeded", logging.INFO, rois=len(roi_ids), cubes=len(plan_after.jobs), skipped=len(skipped), seconds=round(seconds, 2))
    return SeedReport(True, "", roi_ids, len(plan_after.jobs), skipped, seconds)
