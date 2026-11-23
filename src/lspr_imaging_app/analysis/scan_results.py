"""``ScanResultsModule``: the scan file of the active session, in memory, and which of its cubes are out of date (2026-10-23).

What the display panels (Spectra now, Sensorgram later) read. It owns two facts and nothing else:

* ``stored()``: the scan **as the display should see it** (or None): the :class:`~.scan_store.StoredScan` of ``analysis/scan.h5`` with each
  sample's reference side replaced by its effective reference, linked reference ROIs pooled by pixel (``reference_pairing.pair_scan``; plan
  ``docs/roi_shapes_and_linking_plan_2026-11-18.md`` phase 1.2). A session with no reference ROI and no link gets the file's own object, so
  nothing is copied. The file is loaded on a plain thread when the session opens and again when a scan finishes (~0.5 s for 314 cubes x 160
  ROIs); the GUI thread never reads it. The pairing is **rebuilt from the stored numbers, never from the file and never by a scan**, when the
  file arrives and when :meth:`request_pairing` is called (a link, an unlink, a role change, a restored session); a rebuild that has work to do
  (a link exists: ~0.1-0.2 s for a full scan) runs on a thread of its own, and the old view stays until the new one is ready. The arrays are
  shared, read-only. The raw file's own numbers are what the staleness check compares with (``_stored``).
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
import copy
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..applog.events import log_event
from ..domain.roi_links import is_reference
from ..domain.roi_model import AreaRoi
from .reference_pairing import PairedScan, pair_scan
from .spectral_passband import Passbands, correction_axis
from .scan_fingerprint import cell_hashes
from .scan_jobs import ScanSource, build_scan_plan, snapshot_source
from .scan_store import ScanFileError, StoredScan, read_scan, stale_cube_indices

logger = logging.getLogger(__name__)

CHECK_DELAY_MS = 1500  # after the last change: a drag of a ROI or a slider makes dozens of signals


class ScanResultsModule(QObject):
    results_changed = pyqtSignal()  # a different StoredScan (or none) is in place; a panel redraws and drops what it cached
    staleness_changed = pyqtSignal()  # stale_cubes() / is_checking() / the message changed; cosmetic for the data, panels redraw

    _loaded = pyqtSignal(int, object, object)  # generation, StoredScan | None, error text | None (from the loader thread)
    _paired_ready = pyqtSignal(int, object, object)  # pairing generation, PairedScan | None, error text | None (from the pairing thread)
    _checked = pyqtSignal(int, object, object)  # generation, (stale set, reason) | None, error text | None

    def __init__(
        self,
        source_factory: Callable[[], ScanSource],
        scan_running: Callable[[], bool] = lambda: False,
        parent: QObject | None = None,
        rois: Callable[[], Sequence[AreaRoi]] | None = None,
    ) -> None:
        """``rois``: the session's ROIs as they are now (roles and links), asked on the GUI thread; None = no pairing (every ROI is a sample with its own
        reference), which is what a caller that has no ROI toolbox, such as a test, wants."""
        super().__init__(parent)
        self._source_factory = source_factory
        self._scan_running = scan_running
        self._rois_source = rois
        self._path: Path | None = None
        self._stored: StoredScan | None = None  # the file as it is: the staleness check compares with this
        self._paired: PairedScan | None = None  # what the display sees (see the module docstring); None until the first pairing of a loaded file is done
        self._passbands: Passbands | None = None  # the spectral correction (2026-11-20, spectral_passband.py): the measured LCTF bands and the mode the user picked
        self._spectral_mode = "none"
        self._shown: tuple[StoredScan, str, StoredScan] | None = None  # (paired scan, correction key, what the display sees): kept so the same object is handed out until one of them changes
        self._pair_generation = 0  # bumped by every pairing request and every invalidate: a pairing from an older one is dropped
        self._publish_after_pairing = False  # a new file waits for its pairing before the panels and the check hear of it (kept across a pairing that is replaced)
        self._stale: frozenset[int] | None = None
        self._reason = ""
        self._message = ""
        self._loading = False
        self._checking = False
        self._generation = 0  # bumped by every set_scan_file / invalidate: a load or check from an older one is dropped
        self._recheck = False  # a change arrived while a check was running
        self._paused = False  # `set_paused`: the user's Live / Paused choice. No check starts; one runs when live again if one was wanted
        self._displays_hidden = False  # `set_displays_hidden`: nothing that shows the check's answer is on screen. A second, separate reason to hold,
        # so the user's choice above is never overwritten (it is "remembered" by not being touched)
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
        self._paired_ready.connect(self._on_paired)

    # -- query interface -----------------------------------------------------------------------------------------------------

    def stored(self) -> StoredScan | None:
        """The scan as the display sees it: one row per sample ROI, each with its effective reference (module docstring). None: no scan, or the file
        is still being read and paired.

        Under a spectral correction this is a copy of the paired scan that carries the correction (``StoredScan.spectral``); the arrays are shared. It is the same
        object until the paired scan, the table or the mode changes, so the caches that key on its identity stay valid exactly that long."""
        if self._paired is None:
            return None
        base = self._paired.stored
        if self._passbands is None or self._spectral_mode == "none":
            return base
        axis = correction_axis(self._passbands, base.wavelength_nm, self._spectral_mode)
        if self._shown is not None and self._shown[0] is base and self._shown[1] == axis.key:
            return self._shown[2]
        shown = replace(base, spectral=axis)
        self._shown = (base, axis.key, shown)  # holds `base`, so its identity cannot be reused while this is cached
        return shown

    def set_spectral_correction(self, passbands: Passbands | None, mode: str) -> None:
        """The spectral correction the display applies: the derived bands (None: no table) and the mode (``spectral_passband.MODES``). A change that alters what ``stored()``
        returns publishes ``results_changed`` (every panel redraws and every cache of fitted traces is dropped); nothing is scanned and the file is untouched."""
        mode = str(mode).strip().lower()

        def in_force(bands: Passbands | None, picked: str) -> tuple[str, str, str]:  # what the display would show: no correction at all is one state whatever the table
            return ("", "", "none") if bands is None or picked == "none" else (bands.sha256, bands.camera_sha256, picked)

        before = in_force(self._passbands, self._spectral_mode)
        self._passbands, self._spectral_mode = passbands, mode
        if before != in_force(passbands, mode) and self._paired is not None:
            self.results_changed.emit()

    def spectral_correction(self) -> tuple[Passbands | None, str]:
        """The bands and the mode as set (what the Spectra panel's "i" text describes)."""
        return self._passbands, self._spectral_mode

    def reference_note(self, roi_id: int) -> str:
        """Why a sample ROI has no reference numbers ("No reference: ..."; a linked reference that is not in the file yet), or ''."""
        return self._paired.problems.get(int(roi_id), "") if self._paired is not None else ""

    def reference_notes(self) -> dict[int, str]:
        """``reference_note`` for every ROI that has one, as a plain dict (safe to hand to a worker)."""
        return dict(self._paired.problems) if self._paired is not None else {}

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
        self._pair_generation += 1  # a pairing still running is dropped when it arrives
        self._publish_after_pairing = False
        had = self._stored is not None
        self._stored, self._paired, self._stale, self._reason, self._message = None, None, None, "", ""
        if had:
            self.results_changed.emit()

    def reload(self) -> None:
        """Read the file again (a scan just wrote it). Runs on a plain thread; the old data stays until the new arrives."""
        path = self._path
        if path is None or not path.exists():
            if self._stored is not None:
                self._pair_generation += 1
                self._publish_after_pairing = False
                self._stored, self._paired, self._stale, self._reason = None, None, None, ""
                self.results_changed.emit()
            return
        self._generation += 1
        generation = self._generation
        self._loading = True
        self._pair_generation += 1  # a pairing of the old file is dropped
        self._checking = self._recheck = False  # a check of the old file is dropped when it arrives (older generation)
        self._start_thread(lambda: self._load(generation, path), "scan-results-load")

    def request_check(self) -> None:
        """Something that can alter a stored number changed: look again at what is out of date, after a short pause."""
        if self._stored is None:
            return
        if self._held():
            self._check_wanted = True
            return
        self._timer.start()

    def _held(self) -> bool:
        """No check may start now: the user paused the analysis displays, or none of the displays that show the answer is on screen."""
        return self._paused or self._displays_hidden

    def check_is_waiting(self) -> bool:
        """A check is wanted but held back (paused or nothing on screen to show it): ``stale_cubes()`` is as of the last check, and one runs
        when the hold ends. For code that waits for the first check (the start-up tracker) and must not wait for one that will not come."""
        return self._check_wanted and self._held()

    def set_paused(self, paused: bool) -> None:
        """Paused (the analysis displays are paused): no check starts, a wanted one waits; live again: it runs once. A running check finishes."""
        paused = bool(paused)
        if paused == self._paused:
            return
        was_held = self._held()
        self._paused = paused
        self._hold_changed(was_held)

    def set_displays_hidden(self, hidden: bool) -> None:
        """Nothing that shows the check's answer is on screen (the Spectra and Sensorgram docks are closed, behind another tab or the window is
        minimised): no check starts, since nobody could see it, and the freeze it costs (the snapshot of the app's inputs is on the GUI thread) buys
        nothing. One runs when a display is shown again. Independent of :meth:`set_paused`: the user's Live / Paused choice is not changed by it
        and holds again when the displays come back."""
        hidden = bool(hidden)
        if hidden == self._displays_hidden:
            return
        was_held = self._held()
        self._displays_hidden = hidden
        self._hold_changed(was_held)

    def _hold_changed(self, was_held: bool) -> None:
        now_held = self._held()
        if now_held == was_held:
            return
        if now_held:
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
        except BaseException as exc:  # nothing may escape a thread silently
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
        self._message = str(error) if error else ""
        self._stored = stored if isinstance(stored, StoredScan) else None
        self._stale = None  # unknown until the check below has run against this file
        self._reason = ""
        if self._stored is None:
            self._loading = False
            self._paired = None
            self._publish_after_pairing = False
            self.results_changed.emit()
            self.staleness_changed.emit()
            return
        self._publish_after_pairing = True
        self._pair()  # publishes (results_changed, staleness_changed, the check) when the pairing is in place

    # -- pairing samples with their references -------------------------------------------------------------------------------------

    def request_pairing(self) -> None:
        """The roles or links of the ROIs changed (a link, an unlink, a role change, a session restored): make the display's view of the scan again from
        the numbers already stored. Nothing is read from the file and nothing is scanned. Without a loaded file there is nothing to do: the pairing is
        made when one arrives."""
        if self._stored is not None:
            self._pair()

    def _snapshot_rois(self) -> list[AreaRoi]:
        """The ROIs as they are now, copied (GUI thread: the toolbox hands out live objects, and the pairing runs on another thread)."""
        return [copy.copy(roi) for roi in self._rois_source()] if self._rois_source is not None else []

    def _pair(self) -> None:
        stored = self._stored
        rois = self._snapshot_rois()
        self._pair_generation += 1
        generation = self._pair_generation
        if not any(is_reference(roi) or roi.reference_roi_ids for roi in rois):  # nothing to pair: the file's own object comes back, no copy, no thread
            self._on_paired(generation, pair_scan(stored, rois), None)
            return
        self._start_thread(lambda: self._pair_work(generation, stored, rois), "scan-results-pair")

    def _pair_work(self, generation: int, stored: StoredScan, rois: list[AreaRoi]) -> None:
        started, cpu_started = time.perf_counter(), time.thread_time()
        try:
            paired = pair_scan(stored, rois)
        except BaseException as exc:  # nothing may escape a thread silently
            logger.exception("Pairing the samples with their references failed")
            self._paired_ready.emit(generation, None, f"Could not pair the samples with their references: {exc}")
            return
        log_event(logger, "analysis.scan_paired", logging.INFO, rois=len(rois), rows=len(paired.stored.roi_ids),
                  linked=sum(1 for s in paired.sources.values() if s == "linked"), ms=round((time.perf_counter() - started) * 1000),
                  cpu_ms=round((time.thread_time() - cpu_started) * 1000))
        self._paired_ready.emit(generation, paired, None)

    def _on_paired(self, generation: int, paired: object, error: object) -> None:
        if generation != self._pair_generation:
            return  # another pairing was asked for, or the file changed meanwhile
        self._loading = False
        if isinstance(paired, PairedScan):
            self._paired = paired
            if self._publish_after_pairing:  # a new file: its message starts empty (a rebuild after a link keeps what the check said)
                self._message = ""
        else:  # said, never hidden: the panels show the message instead of numbers made without the pairing
            self._paired = None
            self._message = str(error or "Could not pair the samples with their references.")
        self.results_changed.emit()
        if self._publish_after_pairing:
            self._publish_after_pairing = False
            self.staleness_changed.emit()
            if self._stored is not None:
                QTimer.singleShot(0, self._start_check)  # (not self._timer.start(0): that would change the debounce interval for good)

    # -- staleness check ----------------------------------------------------------------------------------------------------------

    def _start_check(self) -> None:
        if self._stored is None or self._path is None:
            return
        if self._held():
            self._check_wanted = True
            return
        if self._checking:
            self._recheck = True
            return
        if self._scan_running():
            return  # the scan reloads the file and asks again when it is done
        t_start = time.perf_counter()
        try:
            snapshot = snapshot_source(self._source_factory())  # GUI thread: reads the modules, ~1.9 s on the 71,685-frame dataset (4.1 s before 2026-11-23: docs/snapshot_source_cost_2026-11-23.md)
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
        except BaseException as exc:
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
