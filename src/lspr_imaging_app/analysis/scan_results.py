"""``ScanResultsModule``: the scan file of the active session, in memory, and which of its cubes are out of date (2026-10-23).

What the display panels (Spectra now, Sensorgram later) read. It owns two facts and nothing else:

* ``stored()``: the :class:`~.scan_store.StoredScan` of ``analysis/scan.h5`` (or None). Loaded on a plain thread when the session opens and
  again when a scan finishes (~0.5 s for 314 cubes x 160 ROIs); the GUI thread never reads the file. The arrays are shared, read-only.
* ``stale_cubes()``: the dataset cubes whose stored cells are *not* current for the app as it is now, or None while that is not known. A
  check (``scan_fingerprint.cell_hashes`` of the current inputs against the hashes in the file) is started after a change that can alter
  a stored number (geometry, mask, chromatic, background, ROIs, frame roles; the owner calls :meth:`request_check` from those signals) and
  after a load. It is debounced, runs off the GUI thread (a snapshot of the app's inputs is taken on the GUI thread, ~0.1 s; decoding the
  masks and hashing happen on the thread, ~2-3 s on the real dataset) and never starts while a scan is running (a finished scan reloads
  the file and checks again). A check never changes the file.

Formula, fit and metric changes do not call :meth:`request_check`: they never make a stored cell stale (``scan_fingerprint``).

The module is the only owner of this state, as the CLAUDE.md module rule asks; panels connect to ``results_changed`` / ``staleness_changed``
and read through the query methods.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..applog import log_event
from .scan_fingerprint import cell_hashes
from .scan_jobs import ScanSource, build_scan_plan, snapshot_source
from .scan_store import ScanFileError, StoredScan, read_scan, stale_cube_indices

logger = logging.getLogger(__name__)

CHECK_DELAY_MS = 1500  # after the last change: a drag of a ROI or a slider makes dozens of signals


class ScanResultsModule(QObject):
    results_changed = pyqtSignal()  # a different StoredScan (or none) is in place; a panel redraws and drops what it cached
    staleness_changed = pyqtSignal()  # stale_cubes() / is_checking() / the message changed; cosmetic for the data, panels redraw

    _loaded = pyqtSignal(int, object, object)  # generation, StoredScan | None, error text | None (from the loader thread)
    _checked = pyqtSignal(int, object, object)  # generation, (stale set, reason) | None, error text | None

    def __init__(
        self,
        source_factory: Callable[[], ScanSource],
        scan_running: Callable[[], bool] = lambda: False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_factory = source_factory
        self._scan_running = scan_running
        self._path: Path | None = None
        self._stored: StoredScan | None = None
        self._stale: frozenset[int] | None = None
        self._reason = ""
        self._message = ""
        self._loading = False
        self._checking = False
        self._generation = 0  # bumped by every set_scan_file / invalidate: a load or check from an older one is dropped
        self._recheck = False  # a change arrived while a check was running
        self._paused = False  # `set_paused`: no check starts; one runs when live again if one was wanted
        self._check_wanted = False
        self._threads: list[threading.Thread] = []
        self._loaded_emitted_at = 0.0  # perf_counter when the loader thread handed the file over (for the "how long did the GUI thread take to get to it" figure)
        self._loaded_applied_at = 0.0  # ... when the GUI thread applied it
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CHECK_DELAY_MS)
        self._timer.timeout.connect(self._start_check)
        self._loaded.connect(self._on_loaded)
        self._checked.connect(self._on_checked)

    # -- query interface -----------------------------------------------------------------------------------------------------

    def stored(self) -> StoredScan | None:
        return self._stored

    def path(self) -> Path | None:
        return self._path

    def stale_cubes(self) -> frozenset[int] | None:
        """Dataset cubes whose results are not current; None = not known (no file, loading, or the first check has not finished)."""
        return self._stale

    def is_cube_current(self, cube: int) -> bool | None:
        """True / False when known, None when not."""
        if self._stored is None or self._stale is None:
            return None
        return int(cube) not in self._stale

    def stale_reason(self) -> str:
        """Why the whole file is stale ('the ROIs changed ...'), or '' when only some cubes are."""
        return self._reason

    def message(self) -> str:
        """A line for the panel: what is wrong with the file (cannot be read), or ''."""
        return self._message

    def is_loading(self) -> bool:
        return self._loading

    def is_checking(self) -> bool:
        return self._checking

    # -- commands --------------------------------------------------------------------------------------------------------------

    def set_scan_file(self, path: Path | None) -> None:
        """The active session's scan file (None: no dataset or no session). Drops what was held and loads the new file if it exists."""
        self.invalidate()
        self._path = path
        self.reload()

    def invalidate(self) -> None:
        """Drop the file and everything derived from it (dataset cleared, session switched)."""
        self._generation += 1
        self._timer.stop()
        self._loading = self._checking = self._recheck = self._check_wanted = False
        had = self._stored is not None
        self._stored, self._stale, self._reason, self._message = None, None, "", ""
        if had:
            self.results_changed.emit()

    def reload(self) -> None:
        """Read the file again (a scan just wrote it). Runs on a plain thread; the old data stays until the new arrives."""
        path = self._path
        if path is None or not path.exists():
            if self._stored is not None:
                self._stored, self._stale, self._reason = None, None, ""
                self.results_changed.emit()
            return
        self._generation += 1
        generation = self._generation
        self._loading = True
        self._checking = self._recheck = False  # a check of the old file is dropped when it arrives (older generation)
        self._start_thread(lambda: self._load(generation, path), "scan-results-load")

    def request_check(self) -> None:
        """Something that can alter a stored number changed: look again at what is out of date, after a short pause."""
        if self._stored is None:
            return
        if self._paused:
            self._check_wanted = True
            return
        self._timer.start()

    def set_paused(self, paused: bool) -> None:
        """Paused (the analysis displays are paused): no check starts, a wanted one waits; live again: it runs once. A running check finishes."""
        paused = bool(paused)
        if paused == self._paused:
            return
        self._paused = paused
        if paused:
            if self._timer.isActive():
                self._timer.stop()
                self._check_wanted = True
        elif self._check_wanted:
            self._check_wanted = False
            self._timer.start()

    def shutdown(self) -> None:
        self._generation += 1
        self._timer.stop()
        for thread in self._threads:
            thread.join(timeout=3.0)

    # -- loading ----------------------------------------------------------------------------------------------------------------

    def _start_thread(self, work: Callable[[], None], name: str) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=work, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _load(self, generation: int, path: Path) -> None:
        started, cpu_started = time.perf_counter(), time.thread_time()
        try:
            stored = read_scan(path)
        except ScanFileError as exc:
            self._loaded.emit(generation, None, str(exc))
            return
        except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
            logger.exception("Reading the scan file failed")
            self._loaded.emit(generation, None, f"Could not read {path.name}: {exc}")
            return
        # cpu_ms far below ms = the thread waited (disk, or the interpreter lock held by another thread), not computed
        log_event(logger, "analysis.scan_loaded", logging.INFO, file=path.name, cubes=len(stored.cube_indices), rois=len(stored.roi_ids),
                  ms=round((time.perf_counter() - started) * 1000), cpu_ms=round((time.thread_time() - cpu_started) * 1000))
        self._loaded_emitted_at = time.perf_counter()
        self._loaded.emit(generation, stored, None)

    def _on_loaded(self, generation: int, stored: object, error: object) -> None:
        if generation != self._generation:
            return  # the session changed meanwhile
        self._loaded_applied_at = time.perf_counter()
        self._loading = False
        self._message = str(error) if error else ""
        self._stored = stored if isinstance(stored, StoredScan) else None
        self._stale = None  # unknown until the check below has run against this file
        self._reason = ""
        self.results_changed.emit()
        self.staleness_changed.emit()
        if self._stored is not None:
            QTimer.singleShot(0, self._start_check)  # (not self._timer.start(0): that would change the debounce interval for good)

    # -- staleness check ----------------------------------------------------------------------------------------------------------

    def _start_check(self) -> None:
        if self._stored is None or self._path is None:
            return
        if self._paused:
            self._check_wanted = True
            return
        if self._checking:
            self._recheck = True
            return
        if self._scan_running():
            return  # the scan reloads the file and asks again when it is done
        t_start = time.perf_counter()
        try:
            snapshot = snapshot_source(self._source_factory())  # GUI thread: reads the modules, ~0.1 s on the real dataset
        except (ValueError, RuntimeError) as exc:  # no dataset, no ROIs...: nothing to compare
            self._stale, self._reason = None, ""
            self._message = str(exc)
            self.staleness_changed.emit()
            return
        stored, generation = self._stored, self._generation
        self._checking = True
        self.staleness_changed.emit()
        # GUI-thread part of a check, for the log: how long after the file was applied the check got its turn, and what the snapshot cost
        waits = {
            "handover_ms": round((self._loaded_applied_at - self._loaded_emitted_at) * 1000) if self._loaded_applied_at >= self._loaded_emitted_at > 0 else None,
            "start_wait_ms": round((t_start - self._loaded_applied_at) * 1000) if self._loaded_applied_at else None,
            "snapshot_ms": round((time.perf_counter() - t_start) * 1000),
        }
        self._loaded_applied_at = 0.0  # a later check (a ROI edit) is not "after the load"
        self._start_thread(lambda: self._check(generation, stored, snapshot, waits), "scan-results-check")

    def _check(self, generation: int, stored: StoredScan, snapshot: ScanSource, waits: dict[str, int | None] | None = None) -> None:
        started, cpu_started = time.perf_counter(), time.thread_time()
        try:
            plan = build_scan_plan(snapshot, resolve_masks=False)  # the check hashes what the masks are made from; it does not build them
            t_plan = time.perf_counter()
            hashes = cell_hashes(plan)
            t_hash = time.perf_counter()
            result = stale_cube_indices(stored, plan, hashes)
        except ValueError as exc:  # e.g. no ROIs: the whole file is out of date for the current setup
            self._checked.emit(generation, None, str(exc))
            return
        except BaseException as exc:  # noqa: BLE001
            logger.exception("Checking the scan file failed")
            self._checked.emit(generation, None, f"Could not check the scan: {exc}")
            return
        t_end = time.perf_counter()
        # plan_ms / hash_ms / compare_ms add up to ms; cpu_ms far below ms = the thread waited for the interpreter lock (or the disk), not computed
        log_event(logger, "analysis.scan_checked", logging.INFO, stale_cubes=len(result[0]), cubes=len(stored.cube_indices),
                  reason=result[1] or "-", ms=round((t_end - started) * 1000), cpu_ms=round((time.thread_time() - cpu_started) * 1000),
                  plan_ms=round((t_plan - started) * 1000), hash_ms=round((t_hash - t_plan) * 1000), compare_ms=round((t_end - t_hash) * 1000),
                  **(waits or {}))
        self._checked.emit(generation, result, None)

    def _on_checked(self, generation: int, result: object, error: object) -> None:
        if generation != self._generation:
            return
        self._checking = False
        if result is not None:
            self._stale, self._reason = result
            self._message = ""
        else:
            self._stale, self._reason = None, ""
            self._message = str(error or "")
        self.staleness_changed.emit()
        if self._recheck:
            self._recheck = False
            self._timer.start()
