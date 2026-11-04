"""The feature map's heavy loops on worker processes (2026-11-04): a `feature_map.Mapper` for the Mask tab's Feature map tool.

`feature_map.py` is the method; its slow part (`prepare`) runs two kinds of independent jobs through a mapper: one radial fit per disk
(twice: on the raw picture, then without its shading; ~27 s for 160 disks on one core) and one model per wavelength (1.45 s each, ~38 s
for 26). Here they run on **worker processes**, not threads: each job is many numpy calls that need the interpreter lock, and a thread
doing that for seconds starves the window of it (the Sensorgram lesson of 2026-10-24, `analysis/trace_pool.py`). The number of workers
comes from ``lspr_core.perf`` like every fan-out of this app (one run per stage, so each stage is tuned on its own throughput).

**Tuning changes scheduling, never results.** Every job is computed by exactly one worker from its own plain data, and the results are
handed back in job order, so the map is identical at 1, 2 or 7 workers and in any completion order. Pinned by
``tests/unit/test_lspri_feature_map_run.py``.

One pool serves a whole run (``with ProcessMapper(...) as mapper:``): a pool starts in ~1 s (Python, numpy, scipy), three stages would
pay it three times. Workers keep the nearest-disk map and the radial model of the picture between wavelengths (`feature_map._model_for`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any

from lspr_core.perf import get_runtime, lower_process_priority

from ...applog.events import log_event
from ...progress import Cancelled

logger = logging.getLogger(__name__)

TASK_KEYS = {  # one perf key per kind of job (their units cost very different times)
    "fitting the disks": "mask.feature_map.fits",
    "refining the disk centres": "mask.feature_map.fits",
    "modelling the wavelengths": "mask.feature_map.wavelengths",
}
# Share of `prepare`'s progress (0..1) each stage spans: measured on one core, 2026-11-04 (19 + 8 s of fits, 38 s of wavelengths)
STAGE_RANGES = {
    "fitting the disks": (0.0, 0.2),
    "refining the disk centres": (0.2, 0.3),
    "modelling the wavelengths": (0.35, 0.95),
}
_JOBS_PER_TASK = {"fitting the disks": 8, "refining the disk centres": 8, "modelling the wavelengths": 1}  # 8 fits ~0.8 s, a wavelength ~1.5 s
_WORKER_BYTES = 500 * 1024 * 1024  # memory one worker may need: numpy + scipy ~90 MB, the picture's model ~60 MB, a job ~40 MB; x2.5


def _init_process(low_priority: bool) -> None:
    if low_priority:
        lower_process_priority()  # the window and the rest of the computer keep their turn (Preferences, default on)


def _run_chunk(fn: Callable[[Any], Any], chunk: list[Any]) -> list[Any]:
    return [fn(job) for job in chunk]


class ProcessMapper:
    """A `feature_map.Mapper` on worker processes, for one run. ``processes=None`` lets ``lspr_core.perf`` choose (and tune when the
    user asked for that); a number is exact and ``<= 1`` runs the jobs in this process. ``progress(fraction, message)`` is `prepare`'s
    own 0..1 (each stage reports within its `STAGE_RANGES` share); ``cancelled()`` is looked at about every 100 ms."""

    def __init__(
        self,
        *,
        processes: int | None = None,
        progress: Callable[[float, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._processes = processes
        self._progress = progress
        self._cancelled = cancelled
        self._pool: ProcessPoolExecutor | None = None
        self._pool_size = 0

    def __enter__(self) -> ProcessMapper:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)  # a running job finishes (~1 s), then the workers close
            self._pool = None

    def _report(self, stage: str, done: int, total: int) -> None:
        if self._progress is None:
            return
        start, end = STAGE_RANGES.get(stage, (0.0, 1.0))
        self._progress(start + (end - start) * done / max(total, 1), f"{stage}: {done} of {total}")

    def _check(self) -> None:
        if self._cancelled is not None and self._cancelled():
            raise Cancelled()

    def __call__(self, fn: Callable[[Any], Any], jobs: Sequence[Any], stage: str) -> list[Any]:
        total = len(jobs)
        results: list[Any] = [None] * total
        if total == 0:
            return results
        if self._processes is not None and self._processes <= 1:
            for i, job in enumerate(jobs):
                self._check()
                results[i] = fn(job)
                self._report(stage, i + 1, total)
            return results
        size = _JOBS_PER_TASK.get(stage, 1)
        chunks = [(i, list(jobs[i : i + size])) for i in range(0, total, size)]
        run = None
        if self._processes is None:
            run = get_runtime().begin(
                TASK_KEYS.get(stage, "mask.feature_map"), total_units=len(chunks), per_worker_bytes=_WORKER_BYTES, max_useful=len(chunks)
            )
        if self._pool is None:
            self._pool_size = max(int(self._processes), 1) if run is None else run.maximum
            low_priority = True if run is None else run.low_priority
            self._pool = ProcessPoolExecutor(max_workers=self._pool_size, initializer=_init_process, initargs=(low_priority,))
        pool = self._pool
        in_flight = (lambda: self._pool_size) if run is None else (lambda: min(run.workers, self._pool_size))
        queue = iter(chunks)
        pending: dict = {}
        done = 0
        try:
            def top_up() -> None:
                while len(pending) < in_flight():
                    item = next(queue, None)
                    if item is None:
                        return
                    start, chunk = item
                    pending[pool.submit(_run_chunk, fn, chunk)] = start

            top_up()
            while pending:
                finished, _still = wait(list(pending), timeout=0.1, return_when=FIRST_COMPLETED)
                for future in finished:
                    start = pending.pop(future)
                    for offset, value in enumerate(future.result()):  # an error inside a job is raised here, to the caller
                        results[start + offset] = value
                        done += 1
                    if run is not None:
                        run.unit_done(1)
                    self._report(stage, done, total)
                self._check()
                top_up()
        except BaseException:
            for future in pending:
                future.cancel()
            raise
        if run is not None:
            log_event(logger, "perf.run", logging.INFO, **run.finish().as_fields())
        return results
