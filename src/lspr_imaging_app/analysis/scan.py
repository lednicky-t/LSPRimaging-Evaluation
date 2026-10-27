"""Analysis step 1 over a whole run: for every frame, read -> prepare -> reduce every ROI, in worker processes.

Design record: ``docs/analysis_redesign_plan_2026-10-19.md`` (sections 11-14). No Qt import allowed in this file, nor in
anything it imports: worker processes start from it (Windows "spawn" start), and a Qt import costs seconds per worker.

Everything a worker needs arrives as plain data (:class:`ScanSettings`, :class:`CubeJob`); workers never touch an app
module. Per worker process, three caches make repeated work free without changing any result:

* :class:`~.frame_reduce.LayoutCache`: which pixels every ROI reads, per (ROI layout, chromatic affine). A real run has
  one affine per wavelength, the same in every cube.
* :class:`~..image_tools.background.estimate.BackgroundWeightsCache`: the valid-mask half of the background estimate,
  the same for every frame without an ignore mask.
* the ROI part of the background exclusion mask, per ROI layout.

The final divide by the background is done only at the pixels the ROIs read (``apply_preprocessing_at``).

Why processes, not threads: the per-ROI and bookkeeping work is many small numpy calls that hold the GIL; 4 threads gave
about 1.3x, 4 processes about 2x on a 4-core laptop (plan doc section 11). Never ``QThreadPool`` (zarr crash rule).
"""

from __future__ import annotations

import gc
import itertools
import os
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field

import numpy as np
from lspr_core.perf import RunReport, get_runtime, lower_process_priority

from ..dataset import io
from ..image_tools.background.estimate import BackgroundWeightsCache, roi_exclusion_mask
from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.geometry.transform import spatial_transform_is_identity
from ..image_tools.preprocess import apply_preprocessing_at, apply_spatial_preprocessing
from ..progress import Cancelled
from ..roi.model import AreaRoiDetectionSettings
from .frame_reduce import LayoutCache, RoiSet, reduce_frame
from .provenance import DEFAULT_REFERENCE_EXCLUSION_MODE
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION


PARTIAL_READ_MAX_SHARE = 1.0
"""A frame is read as chunks only when the pixels it needs lie in at most this share of the plane's chunks; otherwise the whole plane is read.
1.0 (maintainer, 2026-10-27): always, when chunk reads apply at all, so a full scan of an OME-Zarr uses the chunk reader too. On three real
exports (50, 128 and 256 px chunks) it was faster than the whole-plane reader at every share up to 94 %, mostly because it checks a shard's
index once per shard instead of once per plane. It was 0.3 for the first day; the cost of 1.0 is memory (a read plan for 160 ROIs holds
~4.6 MB per layout and wavelength, measured in docs/roi_nudge_partial_scan_proposal_2026-10-26.md section 8), so lower it again if a
machine runs short. It never changes a result."""


@dataclass(frozen=True)
class ScanSettings:
    """The run-wide inputs (one copy per run, sent to every worker once)."""

    geometry: GeometrySettings
    background: BackgroundSettings
    detection: AreaRoiDetectionSettings
    methods: tuple[str, ...] = ("mean",)
    exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE
    trimmed_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION
    fast_tiff_decoder: bool = True  # native decoder, verified against tifffile at runtime by dataset.io (maintainer OK 2026-10-19)
    partial_reads: bool = True
    """Read only the chunks around the ROIs of an OME-Zarr plane when that is possible (background removal off, no rotation / flip /
    crop, an export of this app, few enough chunks). Same numbers either way (pinned by tests), so like ``fast_tiff_decoder`` it is not
    in the recipe and never makes a stored cell stale; False reads every plane whole."""


@dataclass(frozen=True)
class FrameJob:
    path: str  # the plane's record path, as dataset.io.load_image_array_uncached takes it
    affine: np.ndarray  # the frame's 2x3 chromatic matrix (identity when there is none)
    ignore: np.ndarray | None = None  # ignore mask in processed space, already warped to this frame; None = none
    ignore_key: bytes | None = None
    """What the ignore mask is made from, for the staleness fingerprint (`scan_fingerprint.cell_hashes`): a digest of the *authored* mask pixels
    and of the chromatic warp that carries it to this frame (the geometry that rotates / crops it is in the recipe). The processed-space mask is
    a function of exactly these, so a plan made only to be checked (``build_scan_plan(resolve_masks=False)``) has the key and no array. None =
    no key was made (a hand-built job): the fingerprint then hashes ``ignore`` itself."""


@dataclass(frozen=True)
class CubeJob:
    """One cube: its ROIs (as they are in this cube) and its frames in wavelength order.

    ``roi_subset``: positions (in ``rois``, increasing) of the only ROIs to compute; None = all. The ROIs that are left out still count where
    they must: the reference rings leave out every ROI's sample disk. The numbers of a ROI are the same whether or not others are computed
    with it (pinned by a test), so a cube whose other cells are current can be refreshed for just the stale ROIs."""

    cube_pos: int
    rois: RoiSet
    frames: tuple[FrameJob, ...]
    roi_subset: tuple[int, ...] | None = None


@dataclass
class CubeResult:
    """Arrays are ``(n_rois, n_frames_of_the_cube, 2)``, last axis [sample, reference]. For a job with a ``roi_subset`` the first axis has one row
    per subset ROI, in the subset's order (``roi_subset`` echoes it)."""

    cube_pos: int
    values: dict[str, np.ndarray]
    std: np.ndarray
    n_valid: np.ndarray
    n_nominal: np.ndarray
    n_masked: np.ndarray
    seconds: dict[str, float] = field(default_factory=dict)  # busy time per stage, this cube
    roi_subset: tuple[int, ...] | None = None
    partial_frames: int = 0  # frames of this cube that were read as chunks instead of as whole planes
    index_seconds: float = 0.0  # time this cube spent reading and checking shard indexes (part of ``seconds["read"]``); KNOWN BOTTLENECK, see dataset/io.py
    crc_seconds: float = 0.0  # of which the CRC32C


class _Worker:
    """One worker's settings and caches."""

    def __init__(self, settings: ScanSettings) -> None:
        self.settings = settings
        self.layouts = LayoutCache()
        self.weights = BackgroundWeightsCache()
        self._exclusion: dict[tuple[str, tuple[int, int]], np.ndarray] = {}
        self._processed_shape: dict[tuple[int, ...], tuple[int, int]] = {}
        self._subset_rois: dict[tuple[str, tuple[int, ...]], RoiSet] = {}
        # Chunk reads need the plane to be the processed frame as it is (no rotation / flip / crop) and no background estimate, which
        # looks at the whole frame. Layout -> read plan (None: too many chunks, read whole planes); the layout is kept so its id stays valid.
        self._chunk_reads = bool(
            settings.partial_reads and not settings.background.flatten_background_enabled and spatial_transform_is_identity(settings.geometry)
        )
        self._read_plans: dict[tuple, tuple[object, io.PartialReadPlan | None]] = {}

    def _read_plan(self, layout, grid: io.ZarrPlaneGrid) -> io.PartialReadPlan | None:
        key = (id(layout), grid)
        cached = self._read_plans.get(key)
        if cached is None:
            cached = self._read_plans[key] = (layout, io.partial_read_plan(grid, layout.needed_flat, PARTIAL_READ_MAX_SHARE))
        return cached[1]

    def processed_shape(self, raw: np.ndarray) -> tuple[int, int]:
        key = raw.shape
        if key not in self._processed_shape:
            self._processed_shape[key] = tuple(apply_spatial_preprocessing(raw, self.settings.geometry).shape[:2])
        return self._processed_shape[key]

    def exclusion(self, rs: RoiSet, shape: tuple[int, int]) -> np.ndarray:
        key = (rs.key, shape)
        if key not in self._exclusion:
            self._exclusion[key] = roi_exclusion_mask(shape, list(rs.rois))
        return self._exclusion[key]

    def subset_rois(self, rs: RoiSet, subset: tuple[int, ...]) -> RoiSet:
        key = (rs.key, subset)
        if key not in self._subset_rois:
            self._subset_rois[key] = rs.subset(subset)
        return self._subset_rois[key]

    def run_cube(self, job: CubeJob) -> CubeResult:
        s = self.settings
        rs = job.rois
        subset = None if job.roi_subset is None else tuple(int(i) for i in job.roi_subset)
        run_rois = rs if subset is None else self.subset_rois(rs, subset)  # the ROIs this call computes
        n_frames = len(job.frames)
        values = {m: np.full((run_rois.n, n_frames, 2), np.nan) for m in s.methods}
        std = np.full((run_rois.n, n_frames, 2), np.nan)
        n_valid = np.zeros((run_rois.n, n_frames, 2), dtype=np.int32)
        n_nominal = np.zeros((run_rois.n, n_frames, 2), dtype=np.int32)
        n_masked = np.zeros((run_rois.n, n_frames, 2), dtype=np.int32)
        seconds = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}
        clock = time.perf_counter
        partial_frames = 0
        index_before = io.shard_index_seconds()
        for k, frame in enumerate(job.frames):
            t0 = clock()
            at_rois = None
            grid = io.ome_zarr_plane_grid(frame.path) if self._chunk_reads else None
            if grid is not None:
                # The processed frame is the stored plane, so its shape is the grid's and the layout needs no pixels.
                layout = self.layouts.get(rs, frame.affine, (grid.height, grid.width), s.exclusion_mode, subset=subset)
                plan = self._read_plan(layout, grid)
                if plan is not None:
                    at_rois = io.read_plane_pixels(frame.path, plan)
            if at_rois is not None:
                partial_frames += 1
                t1 = t2 = clock()
            else:
                raw = io.load_image_array_uncached(frame.path)
                t1 = clock()
                shape = self.processed_shape(raw)
                layout = self.layouts.get(rs, frame.affine, shape, s.exclusion_mode, subset=subset)
                at_rois = apply_preprocessing_at(
                    raw, layout.needed_flat, s.geometry, s.background, mask_settings=s.detection, external_mask=frame.ignore,
                    roi_exclusion=self.exclusion(rs, shape), weights_cache=self.weights,
                )
                t2 = clock()
            result = reduce_frame(
                run_rois, None, frame.ignore, frame.affine, s.methods, trimmed_fraction=s.trimmed_fraction,
                exclusion_mode=s.exclusion_mode, layout=layout, layout_values=at_rois,
            )
            t3 = clock()
            for m in s.methods:
                values[m][:, k, :] = result.values[m]
            std[:, k, :] = result.std
            n_valid[:, k, :] = result.n_valid
            n_nominal[:, k, :] = result.n_nominal
            n_masked[:, k, :] = result.n_masked
            seconds["read"] += t1 - t0
            seconds["prepare"] += t2 - t1
            seconds["reduce"] += t3 - t2
        index_after = io.shard_index_seconds()
        return CubeResult(
            job.cube_pos, values, std, n_valid, n_nominal, n_masked, seconds, subset, partial_frames,
            index_after[0] - index_before[0], index_after[1] - index_before[1],
        )


_WORKER: _Worker | None = None  # the worker of this process (multiprocessing path)
_WORKER_RUN: int | None = None  # the run `_WORKER` belongs to: a task of another run gets a fresh worker (caches never leak between scans)
_SCAN_WORKER_BYTES = 600 * 1024 * 1024  # memory one scan worker process may need, for the automatic worker limit
PARENT_WATCH_SECONDS = 5.0
"""How often a worker process checks that the application that started it is still alive (see `_start_parent_watch`)."""

_run_numbers = itertools.count(1)


def _parent_info() -> tuple[int, float] | None:
    """(pid, start time) of this process, for the workers it starts to watch; None when psutil is not installed (no watch then)."""
    try:
        import psutil

        return os.getpid(), psutil.Process().create_time()
    except Exception:  # noqa: BLE001 - the watch is a safety net, never a reason for a scan to fail
        return None


def _start_parent_watch(pid: int, created: float, interval: float) -> None:
    """In a worker: end this process when the application that started it is gone. A scan's own pool is shut down by the scan, but a pool that
    stays warm between scans (performance mode) would otherwise live on as idle processes after a crash or a kill of the application, because
    on Windows child processes are not stopped with their parent. The start time is compared too, so a new process that got the old pid is
    not mistaken for the application."""
    try:
        import psutil
    except ImportError:
        return

    def watch() -> None:
        while True:
            time.sleep(interval)
            try:
                alive = psutil.pid_exists(pid) and abs(psutil.Process(pid).create_time() - created) < 1.0
            except psutil.Error:
                alive = False
            if not alive:
                os._exit(0)

    threading.Thread(target=watch, name="scan-parent-watch", daemon=True).start()


def _init_process(low_priority: bool = False, parent: tuple[int, float] | None = None, watch_interval: float = PARENT_WATCH_SECONDS) -> None:
    if low_priority:
        lower_process_priority()  # the window and the rest of the computer keep their turn (Preferences, default on)
    if parent is not None:
        _start_parent_watch(parent[0], parent[1], watch_interval)


def _run_in_process(run_number: int, settings: ScanSettings, jobs: tuple[CubeJob, ...]) -> list[CubeResult]:
    """Run some cubes of scan number ``run_number`` in this worker process. Settings travel with the task (a pool that stays warm serves
    several scans); the first task of a new scan makes a fresh :class:`_Worker`, which drops the caches of the previous one."""
    global _WORKER, _WORKER_RUN
    if _WORKER is None or _WORKER_RUN != run_number:
        _WORKER = None  # free the old caches before the new ones are built
        io.use_fast_tiff_decoder(settings.fast_tiff_decoder)
        _WORKER, _WORKER_RUN = _Worker(settings), run_number
    return [_WORKER.run_cube(job) for job in jobs]


def _ping_process(pause: float = 0.0) -> int:
    """Start-up check of a warm pool: returns this worker's pid. ``pause`` keeps the worker busy so that a batch of pings reaches every worker."""
    time.sleep(pause)
    return os.getpid()


def _trim_process(pause: float = 0.0) -> int:
    """Between scans of a warm pool: drop this worker's caches (layouts, read plans, ...) so that an idle worker holds the imports only.
    Best effort: ``pause`` makes a batch of these reach each worker once; a worker that misses one drops its caches at its next scan."""
    global _WORKER, _WORKER_RUN
    _WORKER, _WORKER_RUN = None, None
    gc.collect()
    time.sleep(pause)
    return os.getpid()


def scan_cubes(
    jobs: Sequence[CubeJob],
    settings: ScanSettings,
    *,
    processes: int | None = None,
    cubes_per_task: int = 4,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_finished: Callable[[RunReport | None], None] | None = None,
    warm: object | None = None,
) -> Iterator[CubeResult]:
    """Run step 1 over ``jobs``, yielding one :class:`CubeResult` per cube **in completion order** (use ``cube_pos``).

    ``processes``: None (the default) = automatic: ``lspr_core.perf`` picks the number of worker processes and tunes it while the
    scan runs (the pool is created once at the largest size; the number of tasks in flight is the tuned count). A number = exactly
    that many (<= 1 runs in this process: tests and debugging). The results are identical at any count. Workers get ``cubes_per_task`` neighbouring
    cubes at a time: neighbours mostly share a ROI layout, so each worker builds a pixel layout once and keeps reusing it
    (handing out single cubes in any order made workers rebuild layouts). ``progress(fraction, message)`` is called
    once per finished cube; ``cancelled()`` is checked as often, and on True the remaining cubes are dropped and
    :class:`~..progress.Cancelled` is raised (cubes already yielded stay valid: each is complete). ``on_finished`` is called once after the
    last cube of a run that completed, with the ``lspr_core.perf`` report (workers, mode, rate) or None when ``processes`` was given.

    ``warm`` (performance mode, ``scan_pool.WarmPool``): run on worker processes that were started before and stay alive afterwards, instead of
    starting a pool for this scan and closing it at the end. Starting the workers costs 5-6 s (each imports ~1,000 modules), which is most of the
    time of a short refresh. The numbers are the same; a pool that breaks is discarded and the scan fails as it always did."""
    total = len(jobs)
    if total == 0:
        return
    if any(f.ignore_key is not None and f.ignore is None for job in jobs for f in job.frames):
        raise ValueError("a frame has an ignore-mask key but no mask: this plan was built to be checked (resolve_masks=False), not to be scanned")
    if processes is not None and processes <= 1:
        io.use_fast_tiff_decoder(settings.fast_tiff_decoder)
        worker = _Worker(settings)
        for done, job in enumerate(jobs, start=1):
            if cancelled is not None and cancelled():
                raise Cancelled()
            yield worker.run_cube(job)
            if progress is not None:
                progress(done / total, f"cube {done} of {total}")
        if on_finished is not None:
            on_finished(None)
        return
    step = max(int(cubes_per_task), 1)
    tasks = [tuple(jobs[i:i + step]) for i in range(0, total, step)]
    # Per-process memory: the interpreter with numpy/cv2 and the three caches, plus a few float32 planes (a guess to bound the pool, not a
    # promise; checked in docs/performance_tuning.md).
    run = None if processes is not None else get_runtime().begin(
        "analysis.scan", total_units=len(tasks), per_worker_bytes=_SCAN_WORKER_BYTES, max_useful=len(tasks),
    )
    pool_size = max(int(processes), 1) if run is None else run.maximum
    in_flight = (lambda: pool_size) if run is None else (lambda: run.workers)
    low_priority = True if run is None else run.low_priority
    run_number = next(_run_numbers)
    if warm is not None:
        pool = warm.acquire(pool_size, low_priority)
    else:
        pool = ProcessPoolExecutor(max_workers=pool_size, initializer=_init_process, initargs=(low_priority, _parent_info()))
    try:
        queue = iter(tasks)
        pending: set = set()

        def top_up() -> None:
            while len(pending) < in_flight():
                chunk = next(queue, None)
                if chunk is None:
                    return
                pending.add(pool.submit(_run_in_process, run_number, settings, chunk))

        done = 0
        try:
            try:
                top_up()
            except BrokenProcessPool:
                if warm is None or pending:
                    raise
                # A worker of the warm pool died while it was idle (killed, out of memory): start a new pool and try once more.
                warm.discard()
                pool = warm.acquire(pool_size, low_priority)
                top_up()
            while pending:
                finished, still = wait(pending, return_when=FIRST_COMPLETED)
                pending.clear()
                pending.update(still)
                for future in finished:
                    results = future.result()
                    for result in results:
                        done += 1
                        yield result
                        if progress is not None:
                            progress(done / total, f"cube {done} of {total}")
                    if run is not None:
                        run.unit_done(1)
                if cancelled is not None and cancelled():
                    raise Cancelled()
                top_up()
        except BaseException as exc:
            for future in pending:
                future.cancel()
            if warm is not None and isinstance(exc, BrokenProcessPool):
                warm.discard()  # never hand the next scan a broken pool
            raise
    finally:
        if warm is None:
            pool.shutdown(wait=True)  # a pool made for this scan: wait for the workers that are still running a task, then close it
    report = run.finish() if run is not None else None
    if on_finished is not None:
        on_finished(report)
