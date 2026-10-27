"""Performance mode of the analysis: worker processes that stay ready between scans (2026-10-27, maintainer request).

**Why.** A scan runs in worker processes, and starting them is most of the time of a short one: each worker imports ~1,000 modules (zarr, scipy,
cv2, PIL, ...) in 2.1-2.5 s on an idle machine, and six or seven start at once on four cores, so the first result of a one-ROI refresh of all
314 cubes arrives after 5.5-6 s and the other cubes within about a second (docs/roi_nudge_partial_scan_proposal_2026-10-26.md section 10). A
pool that is already running removes that wait: move a ROI, press Fast scan, the workers start reading at once.

**The two modes** (the Analysis tab's Mode pick-up; remembered like every control, ``ANALYSIS_MODE``):

* ``normal``: a scan starts its workers and closes them at the end. Nothing is held while the app is idle. The default.
* ``performance``: the workers are started when the mode is switched on (or, for a remembered mode, once the application has finished
  starting up) and stay alive until the mode is switched off or the application quits. Costs: about 150 MB of memory per idle worker (the
  imports; the caches of a scan are dropped after it, :meth:`WarmPool.trim`), and the start-up burst of processor time when the pool starts.

The mode is a name, not a flag, so that more settings can be tied to it later (maintainer: "maybe later we will associate more settings");
:func:`uses_warm_pool` is the one place that says what a mode means today.

**What is shared between scans and what is not.** Only the processes. Every scan has a run number; a worker that gets a task of a new run
drops everything it built for the old one (layouts, read plans, background caches), so a result never depends on an earlier scan, and settings
travel with each task. A pool is created for a worker count and a priority (Edit -> Preferences) and replaced when a scan needs more or the
priority changed.

**Safety.** Workers end themselves when the application that started them is gone (``scan._start_parent_watch``; Windows does not stop child
processes with their parent, and an idle warm pool would otherwise survive a crash). The pool is also closed by ``atexit`` and by
``ScanTask.shutdown`` (``aboutToQuit``). A pool whose worker died is discarded and replaced; the scan that met the broken pool fails as a scan with
a crashed worker always did, the next one starts clean. No Qt in this file; parent process only (workers never import it).
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from concurrent.futures import ProcessPoolExecutor, wait

from .scan import PARENT_WATCH_SECONDS, _init_process, _parent_info, _ping_process, _trim_process

logger = logging.getLogger(__name__)

MODE_NORMAL = "normal"
MODE_PERFORMANCE = "performance"
MODES = (MODE_NORMAL, MODE_PERFORMANCE)

IDLE_WORKER_MB = 150
"""Memory one idle worker of a warm pool holds (the imports; measured 2026-10-27 in docs/roi_nudge_partial_scan_proposal_2026-10-26.md section 11),
for the text of the Mode pick-up."""


def uses_warm_pool(mode: str) -> bool:
    """What the analysis mode means for the worker processes today."""
    return mode == MODE_PERFORMANCE


class WarmPool:
    """A pool of worker processes that outlives a scan. Thread-safe. Hand it to ``scan.scan_cubes(warm=...)``."""

    def __init__(self, *, watch_interval: float = PARENT_WATCH_SECONDS) -> None:
        self._lock = threading.RLock()
        self._executor: ProcessPoolExecutor | None = None
        self._size = 0
        self._low_priority = False
        self._watch_interval = float(watch_interval)
        self._closed = False
        atexit.register(self.shutdown)

    # -- state -------------------------------------------------------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Workers the pool was made for (0 = none yet)."""
        with self._lock:
            return self._size if self._executor is not None else 0

    def is_ready(self) -> bool:
        """A pool exists and has not broken."""
        with self._lock:
            return self._executor is not None and not getattr(self._executor, "_broken", False)

    # -- use ---------------------------------------------------------------------------------------------------------------------

    def acquire(self, size: int, low_priority: bool) -> ProcessPoolExecutor:
        """The executor to run a scan on: the pool that is already there when it has at least ``size`` workers and the same priority and is not
        broken, else a new one (the old one is closed). Workers are started by their first tasks, or all at once by :meth:`start`."""
        size = max(int(size), 1)
        with self._lock:
            if self._closed:
                raise RuntimeError("the warm pool has been shut down")
            executor = self._executor
            if executor is not None and not getattr(executor, "_broken", False) and self._size >= size and self._low_priority == bool(low_priority):
                return executor
            if executor is not None:
                self._close(executor, wait_for_workers=True)
            self._executor = ProcessPoolExecutor(
                max_workers=size, initializer=_init_process, initargs=(bool(low_priority), _parent_info(), self._watch_interval),
            )
            self._size, self._low_priority = size, bool(low_priority)
            return self._executor

    def start(self, size: int, low_priority: bool, *, timeout: float = 180.0) -> set[int]:
        """Create the pool and start **all** its workers now, then return their pids. Blocks for the 5-6 s it takes: call from a thread of its own,
        never from the GUI thread."""
        executor = self.acquire(size, low_priority)
        # The executor starts a worker per submitted task while none is idle; a pause in each ping keeps the first workers busy until the last
        # task is submitted, so every worker is started exactly once.
        futures = [executor.submit(_ping_process, 0.3) for _ in range(self.size)]
        done, _not_done = wait(futures, timeout=timeout)
        return {f.result() for f in done if f.exception() is None}

    def worker_pids(self, *, timeout: float = 60.0) -> set[int]:
        """The pids of the workers that are running (asks each one; tests and diagnostics)."""
        with self._lock:
            executor, size = self._executor, self._size
        if executor is None:
            return set()
        futures = [executor.submit(_ping_process, 0.3) for _ in range(size)]
        done, _not_done = wait(futures, timeout=timeout)
        return {f.result() for f in done if f.exception() is None}

    def trim(self) -> None:
        """Ask every worker to drop the caches of the last scan (best effort, returns at once): an idle worker should hold the imports only."""
        with self._lock:
            executor, size = self._executor, self._size
            if executor is None or getattr(executor, "_broken", False):
                return
            try:
                for _ in range(size):
                    executor.submit(_trim_process, 0.1)
            except Exception:  # noqa: BLE001 - a pool that cannot take tasks is replaced by the next scan; nothing to report here
                logger.debug("could not ask the warm pool to trim its caches", exc_info=True)

    # -- end ---------------------------------------------------------------------------------------------------------------------

    def discard(self) -> None:
        """Forget the current pool without waiting for it (it is broken); the next ``acquire`` makes a new one."""
        with self._lock:
            executor, self._executor, self._size = self._executor, None, 0
        if executor is not None:
            self._close(executor, wait_for_workers=False)

    def shutdown(self) -> None:
        """Close the pool and wait for the workers to end. Safe to call twice; the pool cannot be used afterwards."""
        with self._lock:
            self._closed = True
            executor, self._executor, self._size = self._executor, None, 0
        if executor is not None:
            started = time.perf_counter()
            self._close(executor, wait_for_workers=True)
            logger.info("warm worker pool closed in %.1f s", time.perf_counter() - started)
        try:
            atexit.unregister(self.shutdown)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _close(executor: ProcessPoolExecutor, *, wait_for_workers: bool) -> None:
        try:
            executor.shutdown(wait=wait_for_workers, cancel_futures=True)
        except Exception:  # noqa: BLE001 - closing must never raise into a scan or the quit path
            logger.debug("warm pool shutdown raised", exc_info=True)
