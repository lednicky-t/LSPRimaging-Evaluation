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

import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field

import numpy as np
from lspr_core.perf import RunReport, get_runtime, lower_process_priority

from ..dataset import io
from ..image_tools.background.estimate import BackgroundWeightsCache, roi_exclusion_mask
from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.preprocess import apply_preprocessing_at, apply_spatial_preprocessing
from ..progress import Cancelled
from ..roi.model import AreaRoiDetectionSettings
from .frame_reduce import LayoutCache, RoiSet, reduce_frame
from .provenance import DEFAULT_REFERENCE_EXCLUSION_MODE
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION


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


@dataclass(frozen=True)
class FrameJob:
    path: str  # the plane's record path, as dataset.io.load_image_array_uncached takes it
    affine: np.ndarray  # the frame's 2x3 chromatic matrix (identity when there is none)
    ignore: np.ndarray | None = None  # ignore mask in processed space, already warped to this frame; None = none


@dataclass(frozen=True)
class CubeJob:
    """One cube: its ROIs (as they are in this cube) and its frames in wavelength order."""

    cube_pos: int
    rois: RoiSet
    frames: tuple[FrameJob, ...]


@dataclass
class CubeResult:
    """Arrays are ``(n_rois, n_frames_of_the_cube, 2)``, last axis [sample, reference]."""

    cube_pos: int
    values: dict[str, np.ndarray]
    std: np.ndarray
    n_valid: np.ndarray
    n_nominal: np.ndarray
    seconds: dict[str, float] = field(default_factory=dict)  # busy time per stage, this cube


class _Worker:
    """One worker's settings and caches."""

    def __init__(self, settings: ScanSettings) -> None:
        self.settings = settings
        self.layouts = LayoutCache()
        self.weights = BackgroundWeightsCache()
        self._exclusion: dict[tuple[str, tuple[int, int]], np.ndarray] = {}
        self._processed_shape: dict[tuple[int, ...], tuple[int, int]] = {}

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

    def run_cube(self, job: CubeJob) -> CubeResult:
        s = self.settings
        rs = job.rois
        n_frames = len(job.frames)
        values = {m: np.full((rs.n, n_frames, 2), np.nan) for m in s.methods}
        std = np.full((rs.n, n_frames, 2), np.nan)
        n_valid = np.zeros((rs.n, n_frames, 2), dtype=np.int32)
        n_nominal = np.zeros((rs.n, n_frames, 2), dtype=np.int32)
        seconds = {"read": 0.0, "prepare": 0.0, "reduce": 0.0}
        clock = time.perf_counter
        for k, frame in enumerate(job.frames):
            t0 = clock()
            raw = io.load_image_array_uncached(frame.path)
            t1 = clock()
            shape = self.processed_shape(raw)
            layout = self.layouts.get(rs, frame.affine, shape, s.exclusion_mode)
            at_rois = apply_preprocessing_at(
                raw, layout.needed_flat, s.geometry, s.background, mask_settings=s.detection, external_mask=frame.ignore,
                roi_exclusion=self.exclusion(rs, shape), weights_cache=self.weights,
            )
            t2 = clock()
            result = reduce_frame(
                rs, None, frame.ignore, frame.affine, s.methods, trimmed_fraction=s.trimmed_fraction,
                exclusion_mode=s.exclusion_mode, layout=layout, layout_values=at_rois,
            )
            t3 = clock()
            for m in s.methods:
                values[m][:, k, :] = result.values[m]
            std[:, k, :] = result.std
            n_valid[:, k, :] = result.n_valid
            n_nominal[:, k, :] = result.n_nominal
            seconds["read"] += t1 - t0
            seconds["prepare"] += t2 - t1
            seconds["reduce"] += t3 - t2
        return CubeResult(job.cube_pos, values, std, n_valid, n_nominal, seconds)


_WORKER: _Worker | None = None  # the worker of this process (multiprocessing path)
_SCAN_WORKER_BYTES = 600 * 1024 * 1024  # memory one scan worker process may need, for the automatic worker limit


def _init_process(settings: ScanSettings, low_priority: bool = False) -> None:
    global _WORKER
    if low_priority:
        lower_process_priority()  # the window and the rest of the computer keep their turn (Preferences, default on)
    io.use_fast_tiff_decoder(settings.fast_tiff_decoder)
    _WORKER = _Worker(settings)


def _run_in_process(jobs: tuple[CubeJob, ...]) -> list[CubeResult]:
    assert _WORKER is not None, "worker process not initialised"
    return [_WORKER.run_cube(job) for job in jobs]


def scan_cubes(
    jobs: Sequence[CubeJob],
    settings: ScanSettings,
    *,
    processes: int | None = None,
    cubes_per_task: int = 4,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    on_finished: Callable[[RunReport | None], None] | None = None,
) -> Iterator[CubeResult]:
    """Run step 1 over ``jobs``, yielding one :class:`CubeResult` per cube **in completion order** (use ``cube_pos``).

    ``processes``: None (the default) = automatic: ``lspr_core.perf`` picks the number of worker processes and tunes it while the
    scan runs (the pool is created once at the largest size; the number of tasks in flight is the tuned count). A number = exactly
    that many (<= 1 runs in this process: tests and debugging). The results are identical at any count. Workers get ``cubes_per_task`` neighbouring
    cubes at a time: neighbours mostly share a ROI layout, so each worker builds a pixel layout once and keeps reusing it
    (handing out single cubes in any order made workers rebuild layouts). ``progress(fraction, message)`` is called
    once per finished cube; ``cancelled()`` is checked as often, and on True the remaining cubes are dropped and
    :class:`~..progress.Cancelled` is raised (cubes already yielded stay valid: each is complete). ``on_finished`` is called once after the
    last cube of a run that completed, with the ``lspr_core.perf`` report (workers, mode, rate) or None when ``processes`` was given."""
    total = len(jobs)
    if total == 0:
        return
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
    with ProcessPoolExecutor(max_workers=pool_size, initializer=_init_process, initargs=(settings, low_priority)) as pool:
        queue = iter(tasks)
        pending: set = set()

        def top_up() -> None:
            while len(pending) < in_flight():
                chunk = next(queue, None)
                if chunk is None:
                    return
                pending.add(pool.submit(_run_in_process, chunk))

        done = 0
        try:
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
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    report = run.finish() if run is not None else None
    if on_finished is not None:
        on_finished(report)
