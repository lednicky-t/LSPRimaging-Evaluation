"""ROI position tuning, step C2 (2026-10-29): the heavy part, run once and objective-free. No Qt, nothing stored on disk.

:func:`run_lattice_scan` moves the selected ROIs over a :class:`~.roi_tuning.Lattice` of centres and scans the cubes of the baseline and the signal step at
every lattice position with the real ``scan_cubes``; the result is a :class:`TuningMatrix` holding **the scan-level numbers** (mean, std, pixel counts per
position, cube, ROI, wavelength and side: the same arrays a ``scan.h5`` cell has). :func:`compute_traces` turns a matrix into the Sensorgram's metric
traces; ``roi_tuning.lattice_objectives`` and ``roi_tuning_objectives.rank`` then give the numbers the user ranks by. Keeping the scan-level numbers (not just
traces) is what lets a change of fit method or formula re-rank without a rescan, and lets Accept copy the best position's numbers into ``scan.h5``.

Why a shifted copy is just a ROI at another place: nothing new enters the pixels. The scan, its staleness hash and the fits already do everything; the
position-0 numbers equal the normal scan's, cell for cell (tools/roi_tuning_lab ``--check`` and the tests of this step pin that). ROIs are shifted *together*,
one scan per lattice position for all of them, which equals shifting each alone while no two ROIs are close enough to touch each other's reference ring
(``roi_tuning.neighbours_within_reach``); a matrix lists the pairs that are (``interacting_pairs``) so the screen can say its numbers for those are approximate.

Units and spaces: offsets are in processed image pixels (see ``roi_tuning``); numbers are as stored in ``scan.h5``. Parallel work takes its worker count from
``lspr_core.perf`` through ``scan_cubes(processes=None)``; results are placed by ``cube_pos``, so the order in which workers finish cannot change them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from ..applog import log_event
from ..progress import Cancelled
from .roi_tuning import (
    Lattice,
    LatticeObjectives,
    TuningRegions,
    lattice_objectives,
    neighbours_within_reach,
    region_membership,
    shifted_roi_set,
)
from .scan import scan_cubes
from .scan_fingerprint import cell_hashes
from .scan_jobs import ScanPlan
from .settings import MetricSettings, StatisticsSettings
from .trace_fit import TraceInput, traces_from_inputs

logger = logging.getLogger(__name__)

SAMPLE, REFERENCE = 0, 1
BYTES_PER_ELEMENT = 8 + 8 + 4 + 4 + 4
"""mean + std (float64) and n_valid, n_nominal, n_masked (uint32) for one (position, cube, ROI, wavelength, side)."""

TraceRunner = Callable[
    [list[tuple[int, TraceInput]], str, MetricSettings, "Callable[[], bool] | None", "Callable[[int, int], None] | None"],
    "tuple[dict[int, np.ndarray], bool]",
]
"""The shape of ``TracePool.run``: ``(jobs, formula_key, metric, cancelled, progress) -> (traces by job key, cancelled before the end)``."""


class TuningError(ValueError):
    """A run that cannot be made. The message is a plain sentence for the status bar."""


def check_tunable(plan: ScanPlan, selected: Sequence[int]) -> None:
    """Raise :class:`TuningError` unless ``selected`` (positions in the plan's ROI set) can be tuned by moving the centre."""
    if not selected:
        raise TuningError("Select at least one ROI first.")
    if not plan.jobs:
        raise TuningError("There are no cubes to scan.")
    rs = plan.jobs[0].rois
    if any(job.rois.key != rs.key for job in plan.jobs):
        raise TuningError("The ROIs change over the experiment (a ROI geometry timeline); position tuning does not support that yet.")
    if len(set(selected)) != len(selected) or any(not 0 <= i < rs.n for i in selected):
        raise TuningError("The selected ROIs are not part of the scan.")
    free_form = [int(plan.roi_ids[i]) for i in selected if not rs.vectorised[i]]
    if free_form:
        shown = ", ".join(str(r) for r in free_form[:3]) + (" ..." if len(free_form) > 3 else "")
        raise TuningError(f"ROI {shown} {'is a free-form ROI' if len(free_form) == 1 else 'are free-form ROIs'} and cannot be tuned by moving its centre.")
    if "mean" not in plan.settings.methods:
        raise TuningError("Position tuning needs the 'mean' reduction in the scan settings.")


@dataclass(frozen=True)
class TuningCost:
    """What a run would do, before it starts (the screen shows it and asks before a long one)."""

    positions: int
    rois: int
    cubes: int
    frames: int  # frames read: every position reads the same frames again
    matrix_bytes: int  # RAM the finished matrix holds


def estimate_cost(plan: ScanPlan, n_selected: int, lattice: Lattice) -> TuningCost:
    """Frames and memory of a run of ``lattice`` over the cubes of ``plan`` for ``n_selected`` ROIs."""
    frames = lattice.n_points * plan.n_frames
    shape = lattice.n_points * len(plan.jobs) * n_selected * len(plan.wavelength_axis_nm) * 2
    return TuningCost(lattice.n_points, n_selected, len(plan.jobs), frames, shape * BYTES_PER_ELEMENT)


@dataclass
class TuningMatrix:
    """The scan-level numbers of every lattice position. Arrays are ``[position, cube, roi, wavelength, side]`` (``side``: 0 sample, 1 reference), the ROI
    axis holding only the tuned ROIs (``roi_ids``). ``base_hashes[cube, roi]``: the ordinary scan's cell hash of each tuned ROI at its **unmoved** place, for
    asking later whether what the matrix was made from has changed. A matrix is complete or does not exist (a cancelled run returns nothing)."""

    lattice: Lattice
    roi_ids: tuple[int, ...]
    roi_xy: np.ndarray  # float64 [roi, 2]: the centres (processed px) the offsets are relative to
    cube_indices: tuple[int, ...]
    wavelength_nm: tuple[float, ...]
    mean: np.ndarray
    std: np.ndarray
    n_valid: np.ndarray
    n_nominal: np.ndarray
    n_masked: np.ndarray
    base_hashes: np.ndarray  # uint64 [cube, roi]
    interacting_pairs: tuple[tuple[int, int], ...] = ()  # (ROI id, ROI id) that can touch each other's reference ring over this lattice
    seconds: dict[str, float] = field(default_factory=dict)

    @property
    def offsets(self) -> np.ndarray:
        return self.lattice.offsets()

    @property
    def nbytes(self) -> int:
        return int(sum(a.nbytes for a in (self.mean, self.std, self.n_valid, self.n_nominal, self.n_masked, self.base_hashes)))

    def trace_inputs(self) -> list[tuple[int, TraceInput]]:
        """One ``TraceInput`` per (position, ROI), keyed ``position * n_rois + roi``: the same inputs ``scan_trace.trace_input`` makes from a stored scan
        (every cube counts as scanned: the matrix is complete)."""
        n_points, n_cubes, n_rois = self.mean.shape[:3]
        axis = np.asarray(self.wavelength_nm, dtype=np.float64)
        cubes = np.asarray(self.cube_indices, dtype=np.int64)
        scanned = np.ones(n_cubes, dtype=bool)
        jobs: list[tuple[int, TraceInput]] = []
        for k in range(n_points):
            for j in range(n_rois):
                sample, reference = self.mean[k, :, j, :, SAMPLE], self.mean[k, :, j, :, REFERENCE]
                usable = (
                    np.isfinite(sample) & np.isfinite(reference) & (self.n_valid[k, :, j, :, SAMPLE] > 0) & (self.n_valid[k, :, j, :, REFERENCE] > 0)
                )
                jobs.append((k * n_rois + j, TraceInput(axis, sample, reference, usable, scanned, cubes)))
        return jobs


def run_lattice_scan(
    plan: ScanPlan,
    selected: Sequence[int],
    lattice: Lattice,
    *,
    processes: int | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    warm: object | None = None,
) -> TuningMatrix:
    """Scan every lattice position of the ROIs at ``selected`` (positions in the plan's ROI set, ``plan.roi_ids`` order) over the cubes of ``plan``.

    ``plan``: a scan plan of just the cubes of the two steps (``build_scan_plan(source, cubes=...)``) with its masks resolved. ``processes``: None = the
    tuned count of ``lspr_core.perf`` (the app), a number = exactly that many (<= 1 runs in this process: tests). ``progress(fraction, message)`` is called
    once per finished cube-and-position; ``cancelled()`` is checked as often and a True raises :class:`~..progress.Cancelled` (nothing is returned: a
    partial matrix would rank positions on unequal data). Raises :class:`TuningError` for a selection that cannot be tuned. No file is touched."""
    selected = [int(i) for i in selected]
    check_tunable(plan, selected)
    t_start = time.perf_counter()
    rs = plan.jobs[0].rois
    offsets = lattice.offsets()
    n_points, n_cubes, n_rois, n_wl = len(offsets), len(plan.jobs), len(selected), len(plan.wavelength_axis_nm)
    roi_ids = tuple(int(plan.roi_ids[i]) for i in selected)

    pairs = tuple((int(plan.roi_ids[a]), int(plan.roi_ids[k])) for a, k in neighbours_within_reach(rs, selected, lattice))
    sets = [shifted_roi_set(rs, selected, float(dx), float(dy)) for dx, dy in offsets]
    base_hashes = np.asarray(cell_hashes(plan), dtype=np.uint64)[:, selected]
    t_prepared = time.perf_counter()

    # Point-major job order: the cubes of one position are neighbours, so a worker keeps reusing the pixel layout of that position's ROI set.
    jobs = [
        replace(plan.jobs[p], rois=sets[k], roi_subset=tuple(selected), cube_pos=k * n_cubes + p) for k in range(n_points) for p in range(n_cubes)
    ]
    shape = (n_points, n_cubes, n_rois, n_wl, 2)
    mean = np.full(shape, np.nan)
    std = np.full(shape, np.nan)
    n_valid = np.zeros(shape, dtype=np.uint32)
    n_nominal = np.zeros(shape, dtype=np.uint32)
    n_masked = np.zeros(shape, dtype=np.uint32)
    placed = np.zeros((n_points, n_cubes), dtype=bool)
    busy: dict[str, float] = {}
    frames = partial = 0
    for result in scan_cubes(jobs, plan.settings, processes=processes, progress=progress, cancelled=cancelled, warm=warm):
        k, p = divmod(result.cube_pos, n_cubes)
        if placed[k, p]:
            raise RuntimeError(f"position {k}, cube position {p} was returned twice")
        columns = np.asarray(plan.axis_positions[p], dtype=np.intp)
        mean[k, p][:, columns, :] = result.values["mean"]
        std[k, p][:, columns, :] = result.std
        n_valid[k, p][:, columns, :] = result.n_valid
        n_nominal[k, p][:, columns, :] = result.n_nominal
        n_masked[k, p][:, columns, :] = result.n_masked
        placed[k, p] = True
        frames += result.values["mean"].shape[1]
        partial += result.partial_frames
        for name, seconds in result.seconds.items():
            busy[name] = busy.get(name, 0.0) + seconds
    if cancelled is not None and cancelled():
        raise Cancelled()
    if not placed.all():
        raise RuntimeError(f"{int((~placed).sum())} of {placed.size} (position, cube) results are missing")
    wall = time.perf_counter() - t_start
    log_event(
        logger, "tuning.lattice_scan", logging.INFO, positions=n_points, rois=n_rois, cubes=n_cubes, frames=frames, partial_frames=partial,
        wall_s=round(wall, 2), prepare_s=round(t_prepared - t_start, 2), frames_per_s=round(frames / wall, 1) if wall > 0 else 0.0,
        interacting_pairs=len(pairs), processes=processes if processes is not None else "tuned", **{f"busy_{k}_s": round(v, 2) for k, v in busy.items()},
    )
    return TuningMatrix(
        lattice, roi_ids, np.column_stack([rs.cx[selected], rs.cy[selected]]).astype(np.float64), tuple(plan.cube_indices),
        tuple(plan.wavelength_axis_nm), mean, std, n_valid, n_nominal, n_masked, base_hashes, pairs, {"wall_s": wall, **busy},
    )


def compute_traces(
    matrix: TuningMatrix,
    formula_key: str,
    metric: MetricSettings,
    *,
    runner: TraceRunner | None = None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> np.ndarray:
    """The Sensorgram's metric trace of every (position, ROI): ``float64 [position, roi, cube]`` in nm, NaN where a fit has no value.

    ``runner`` (the app passes ``TracePool.run``) does the fits in a helper process, so they never compete with the window for the interpreter lock; None fits in
    **this** thread (tests, the lab, a command line - never next to the GUI). Raises :class:`~..progress.Cancelled` if ``runner`` reports a cancel.

    A template-shift metric needs its reference cubes among the cubes of the matrix; where they are not, its traces are gaps."""
    n_points, n_cubes, n_rois = matrix.mean.shape[:3]
    jobs = matrix.trace_inputs()
    out = np.full((n_points, n_rois, n_cubes), np.nan)
    if runner is not None:
        done, was_cancelled = runner(jobs, formula_key, metric, cancelled, progress)
        if was_cancelled:
            raise Cancelled()
        for key, trace in done.items():
            out[divmod(key, n_rois)] = trace
        return out
    fitted = traces_from_inputs([data for _, data in jobs], formula_key, metric)
    for (key, _), trace in zip(jobs, fitted, strict=True):
        out[divmod(key, n_rois)] = trace
    if progress is not None:
        progress(len(jobs), len(jobs))
    return out


def objectives_from_traces(
    matrix: TuningMatrix,
    traces: np.ndarray,
    regions: TuningRegions,
    first_unix_ms: Sequence[float] | np.ndarray,
    last_unix_ms: Sequence[float] | np.ndarray,
    statistics: StatisticsSettings,
) -> LatticeObjectives:
    """The statistical numbers of every (position, ROI) from the metric ``traces`` of ``matrix``: the Sensorgram's own post-processing (``statistics``), then the
    baseline and signal step statistics (``roi_tuning.lattice_objectives``). ``first_unix_ms`` / ``last_unix_ms``: acquisition span of each cube of the matrix
    (same order as ``matrix.cube_indices``). The baseline switch of ``statistics`` is turned off: subtracting a constant per ROI changes neither the delta nor the
    noise. Cheap (no scan, no fit): the step that runs again when the user changes the fit, formula or smoothing."""
    membership = region_membership(regions, matrix.cube_indices, first_unix_ms, last_unix_ms)
    x = np.asarray(matrix.cube_indices, dtype=np.float64)  # the Sensorgram's x is the cube number
    return lattice_objectives(matrix.roi_ids, matrix.offsets, traces, membership, x, replace(statistics, baseline_enabled=False))
