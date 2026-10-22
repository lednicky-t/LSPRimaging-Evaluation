"""Scan a whole dataset for transients (2026-10-16): the orchestration around `transient_detection.py`, still Qt-free. It takes
the dataset as a plain `{cube: wavelengths}` map and a `load_plane(cube, wavelength)` callable, so it runs from a worker thread
of the app (`threading.Thread`, never `QThreadPool`: CLAUDE.md, zarr) or from a command line (`tools/scan_transients.py`) alike.
Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

Three stages, each one reporting progress and checking for Cancel (`progress.StageProgress`):

1. **screen**: every *checked* frame (every `stride`-th wavelength) is read, binned 4x4 and compared with the same wavelength
   in the previous cube (`screen_frame`). A passage lasts 3-15 frames (usually 5-8), so stride 3 saw all 35 passages of the
   test dataset and stride 5 saw 34-35 of them.
2. **expand**: around every hit, the neighbouring frames in acquisition order (which runs across a cube boundary) are screened
   too, and so on until a frame is clean. Only when `stride > 1`; with stride 1 everything is screened already.
3. **confirm**: the hit frames are read at full resolution with the brighter of the two neighbouring cubes as the reference
   (`confirmation_reference`), and each particle gets its half-depth contour.

Frames that cannot be read are skipped and listed in `ScanResult.failed_frames`, never silently dropped.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from lspr_core.perf import get_runtime

from ...applog import timed
from ...progress import ProgressCallback, StageProgress
from .transient_detection import (
    DetectionParams,
    FrameScreen,
    LinkParams,
    TransientEvent,
    bin_frame,
    confirmation_reference,
    contour_objects,
    global_events,
    link_passages,
    screen_frame,
)
from .transient_gaps import GapTarget, fill_event, find_gap_targets

logger = logging.getLogger(__name__)

Frame = tuple[int, float]  # (cube index, wavelength nm)
Binned = tuple[np.ndarray, tuple[float, float]]  # the binned frame and its full-resolution pixels per binned pixel (x, y)

# Stage shares of the progress bar, from the measured times on the 8164-frame test dataset (TIFF, stride 3): screening ~55 s
# (decoding), the neighbours of the hits ~5 s, the contours of ~800 hit frames (three full reads each) ~40 s.
_STAGE_WEIGHTS = {"screen": 0.50, "expand": 0.05, "confirm": 0.37, "fill": 0.08}


def _default_workers() -> int:
    """Plain threads for each of the two pools: all but two of the cores, at most 6, at least 2. Measured on the 8-core test machine
    (full scan, stride 3, 8164 frames): 4 workers 48 s, 6 workers 42 s, 8 workers 39 s - the last 3 s are not worth freezing the GUI
    for, and the result is identical for every count."""
    return max(2, min(6, (os.cpu_count() or 4) - 2))


@dataclass(frozen=True)
class ScanParams:
    detection: DetectionParams = field(default_factory=DetectionParams)
    link: LinkParams = field(default_factory=LinkParams)
    stride: int = 3  # check every n-th wavelength
    stride_offset: int = 0
    cube_range: tuple[int, int] | None = None  # (first, last) cube, inclusive; None = every cube
    workers: int | None = None
    """Threads per pool (reading, screening). None (the default) = automatic: `lspr_core.perf` measures the screening stage and tunes
    the number of reading threads, starting from `_default_workers()` (docs/performance_tuning.md). A number = exactly that many, as
    before (tests, the command-line tool). The scan finds the same events at any count."""
    expand: bool = True
    fill_gaps: bool = True  # search the frames a passage predicts at a lower threshold (`transient_gaps.py`)
    fill_extend_frames: int = 2  # how far beyond each end of a passage the filling may follow it


@dataclass
class ScanResult:
    events: list[TransientEvent]
    frames_in_range: int
    frames_screened: int
    frames_hit: int
    failed_frames: list[Frame]
    seconds: float
    stage_seconds: dict[str, float]
    params: ScanParams


def scan_transients(
    frame_index: Mapping[int, Sequence[float]],
    load_plane: Callable[[int, float], np.ndarray],
    params: ScanParams | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
    worker_init: Callable[[], None] | None = None,
) -> ScanResult:
    """Scan the frames of `frame_index` (cube -> wavelengths; a wavelength <= 0 is a dark frame and is never scanned).

    `load_plane` is called from several threads at once (`params.workers`, or the automatic count); `worker_init` runs once in each thread of both pools, the
    reading one and the screening one (the app opts the thread into the fast TIFF decoder and lowers its priority there, so the
    window keeps its turn). Raises `progress.Cancelled` when `cancelled()` turns true."""
    params = params or ScanParams()
    det = params.detection
    stride = max(int(params.stride), 1)
    report = StageProgress(_STAGE_WEIGHTS, progress, cancelled)
    started = time.perf_counter()

    first, last = params.cube_range if params.cube_range is not None else (None, None)
    cubes = sorted(c for c in frame_index if (first is None or c >= first) and (last is None or c <= last))
    order: list[Frame] = [(c, w) for c in cubes for w in sorted(float(w) for w in frame_index[c] if w > 0)]
    position = {frame: i for i, frame in enumerate(order)}
    cubes_with: dict[float, list[int]] = {}
    for c, w in order:
        cubes_with.setdefault(w, []).append(c)
    checked = sorted(cubes_with)[int(params.stride_offset) % stride :: stride]
    total = sum(len(cubes_with[w]) for w in checked)  # frames the screening stage looks at

    # How many reading threads work at once. Fixed: exactly `params.workers`. Automatic: both pools are created at the largest size
    # `lspr_core.perf` allows, a gate (a counting semaphore whose limit the tuner moves) lets only the tuned number of readers
    # run, and the screening stage reports each finished frame so the tuner can settle on the best count (2026-10-22). Only the
    # entry points submitted to the pool are gated; the calls inside them are not, or a task holding a slot would wait for a second.
    perf_run = None if params.workers is not None else get_runtime().begin("mask.scan.screen", total_units=total, start_hint=_default_workers())
    workers = max(int(params.workers), 1) if perf_run is None else perf_run.maximum
    gated: Callable[[Callable], Callable] = (lambda fn: fn) if perf_run is None else perf_run.gate.wrap

    failed: list[Frame] = []
    plane_shape: list[tuple[int, int]] = []  # (height, width) of the first plane read

    def neighbours(frame: Frame) -> tuple[int | None, int | None]:
        """The cubes before and after `frame`'s that also have its wavelength."""
        have = cubes_with[frame[1]]
        i = have.index(frame[0])
        return (have[i - 1] if i > 0 else None), (have[i + 1] if i + 1 < len(have) else None)

    def read_binned(frame: Frame) -> Binned | None:
        try:
            plane = load_plane(frame[0], frame[1])
            height, width = plane.shape
            if not plane_shape:
                plane_shape.append((height, width))
            binned = bin_frame(plane, det.bin_factor)
            return binned, (width / binned.shape[1], height / binned.shape[0])
        except Exception:
            logger.warning("Scan: could not read cube %s, %s nm - skipped", frame[0], frame[1], exc_info=True)
            failed.append(frame)
            return None

    def read_full(frame: Frame) -> np.ndarray | None:
        try:
            return np.asarray(load_plane(frame[0], frame[1]), dtype=np.float32)
        except Exception:
            logger.warning("Scan: could not read cube %s, %s nm - skipped", frame[0], frame[1], exc_info=True)
            failed.append(frame)
            return None

    compared: list[int] = []  # one entry per frame that was really compared with a reference (list.append is atomic)

    def screen(own: Binned | None, reference: Binned | None) -> FrameScreen | None:
        if own is None or reference is None:
            return None
        compared.append(1)
        found = screen_frame(own[0], reference[0], det, own[1])
        return found if found.weak_hit else None  # a weak hit makes no event, but its neighbours are looked at too

    hits: dict[Frame, FrameScreen] = {}
    screened: set[Frame] = set()
    stage_seconds: dict[str, float] = {}

    with timed(logger, "mask.transient_scan", frames=len(order), stride=stride, cubes=len(cubes)) as timing:
        # Two pools of plain threads: one reads (decoding is the floor), one screens (cv2 / numpy release the GIL). Screening on the
        # scan's own thread measured 3.4 ms a frame, as much as the reading itself (2026-10-17).
        with ThreadPoolExecutor(workers, initializer=worker_init) as pool, ThreadPoolExecutor(workers, initializer=worker_init) as compute:
            # -- 1. screen the checked frames, one wavelength at a time -----------------------------------------------
            stage_started = time.perf_counter()
            done = 0
            pending: list[tuple[Frame, Future]] = []

            def settle(keep: int) -> None:
                """Collect finished screenings until at most `keep` are outstanding (bounded memory: each holds two binned frames)."""
                while len(pending) > keep:
                    frame_done, future = pending.pop(0)
                    found_here = future.result()
                    if found_here is not None:
                        hits[frame_done] = found_here

            for w in checked:
                frames = [(c, w) for c in cubes_with[w]]
                previous: tuple[Frame, Binned | None] | None = None
                for frame, item in zip(frames, _ordered(pool, gated(read_binned), frames, ahead=workers * 3)):
                    report("screen", done / max(total, 1), f"cube {frame[0]}, {frame[1]:g} nm")
                    if perf_run is not None:
                        perf_run.unit_done(1)
                    done += 1
                    screened.add(frame)
                    if previous is None:
                        previous = (frame, item)  # the first cube is compared with the second, once that is read
                        continue
                    if previous[0] == frames[0]:
                        pending.append((previous[0], compute.submit(screen, previous[1], item)))
                    pending.append((frame, compute.submit(screen, item, previous[1])))
                    previous = (frame, item)
                    settle(workers * 8)
                settle(0)
            stage_seconds["screen"] = time.perf_counter() - stage_started
            report("screen", 1.0, "screened")
            tuning = None
            if perf_run is not None:  # the later stages are not tuned; they keep the count the screening settled on
                tuning = perf_run.finish()
                perf_run.gate.set_limit(tuning.workers)

            # -- 2. the neighbours of the hits ------------------------------------------------------------------------
            stage_started = time.perf_counter()
            if stride > 1 and params.expand and hits:
                frontier = list(hits)
                rounds = 0

                def screen_one(frame: Frame) -> tuple[Frame, FrameScreen | None]:
                    before, after = neighbours(frame)
                    reference_cube = before if before is not None else after
                    if reference_cube is None:
                        return frame, None
                    return frame, screen(read_binned(frame), read_binned((reference_cube, frame[1])))

                while frontier:
                    batch: list[Frame] = []
                    for frame in frontier:
                        for j in (position[frame] - 1, position[frame] + 1):
                            if 0 <= j < len(order) and order[j] not in screened:
                                screened.add(order[j])
                                batch.append(order[j])
                    rounds += 1
                    report("expand", 1.0 - 1.0 / (rounds + 1), f"neighbours of hits, round {rounds}")
                    frontier = []
                    for frame, found in pool.map(gated(screen_one), batch):
                        if found is not None:
                            hits[frame] = found
                            frontier.append(frame)
            stage_seconds["expand"] = time.perf_counter() - stage_started
            report("expand", 1.0, "neighbours screened")

            # -- 3. contours of the hit frames at full resolution -------------------------------------------------------
            stage_started = time.perf_counter()
            hit_frames = sorted(hits, key=lambda f: position[f])
            events: list[TransientEvent] = []

            def confirm(frame: Frame) -> list[TransientEvent]:
                found = global_events(hits[frame], det, frame)
                if not any(c.area_px <= det.max_particle_area_px for c in hits[frame].candidates):
                    return found
                before, after = neighbours(frame)
                own = read_full(frame)
                around = [read_full((c, frame[1])) if c is not None else None for c in (before, after)]
                if own is None or all(plane is None for plane in around):
                    return found
                return found + contour_objects(own, confirmation_reference(*around), hits[frame], det, frame)

            for index, found in enumerate(_ordered(pool, gated(confirm), hit_frames, ahead=workers * 2)):
                report("confirm", (index + 1) / max(len(hit_frames), 1), f"contours {index + 1}/{len(hit_frames)}")
                events.extend(found)
            stage_seconds["confirm"] = time.perf_counter() - stage_started
            report("confirm", 1.0, "contours done")

            # -- 4. gap filling along the passages ---------------------------------------------------------------------
            stage_started = time.perf_counter()
            shape = plane_shape[0] if plane_shape else None
            linked = link_passages(events, order, params.link, shape)
            filled = 0
            if params.fill_gaps and events:
                tried: set = set()

                def fill_one(target: GapTarget) -> TransientEvent | None:
                    before, after = neighbours(target.frame)
                    own = read_full(target.frame)
                    around = [read_full((c, target.frame[1])) if c is not None else None for c in (before, after)]
                    if own is None or all(plane is None for plane in around):
                        return None
                    return fill_event(own, confirmation_reference(*around), target, det)

                for round_index in range(max(int(params.fill_extend_frames), 0) + 1):
                    targets = [t for t in find_gap_targets(linked, order, det, params.link, shape) if t.key not in tried]
                    if not targets:
                        break
                    report("fill", round_index / (params.fill_extend_frames + 1), f"gap filling, round {round_index + 1}")
                    tried.update(t.key for t in targets)
                    added = [e for e in _ordered(pool, gated(fill_one), targets, ahead=workers * 2) if e is not None]
                    if not added:
                        break
                    events.extend(added)
                    filled += len(added)
                    linked = link_passages(events, order, params.link, shape)
            stage_seconds["fill"] = time.perf_counter() - stage_started
            report("fill", 1.0, "done")
            timing.set(screened=len(screened), compared=len(compared), hits=len(hits), events=len(events), filled=filled, failed=len(failed))
            if tuning is not None:
                timing.set(workers=tuning.workers, worker_mode=tuning.mode)

    linked.sort(key=lambda e: (position.get(e.frame, 0), -e.area_px))
    return ScanResult(
        events=linked,
        frames_in_range=len(order),
        frames_screened=len(compared),  # compared with a reference frame, not merely looked at (a lone cube has none to compare with)
        frames_hit=sum(1 for s in hits.values() if s.candidates),
        failed_frames=sorted(set(failed)),
        seconds=time.perf_counter() - started,
        stage_seconds=stage_seconds,
        params=params,
    )


def _ordered(pool: ThreadPoolExecutor, fn: Callable, items: Sequence, ahead: int) -> Iterator:
    """`fn(item)` for every item on the pool, results in order, never more than `ahead` of them submitted beyond the one being
    consumed (bounded memory), and the unstarted ones cancelled if the consumer stops early (a Cancel)."""
    futures: list[Future | None] = []
    submitted = 0
    try:
        for index in range(len(items)):
            while submitted < len(items) and submitted - index < ahead:
                futures.append(pool.submit(fn, items[submitted]))
                submitted += 1
            future = futures[index]
            assert future is not None
            futures[index] = None  # let the result go once it has been handed on
            yield future.result()
    finally:
        for future in futures:
            if future is not None:
                future.cancel()
