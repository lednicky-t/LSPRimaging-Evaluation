"""Debounced session autosave - the trigger layer `storage/session.py`
deliberately doesn't own (built 2026-09-23).

`save_session`/`load_session` were built the day before with nothing calling
them: no autosave, no save-on-close, no load-on-open. This is that wiring,
kept out of `session.py` because that file is Qt-free on purpose (it takes
and returns plain dataclasses so a test can drive it without a
`QApplication`), and a debounce needs a `QTimer`.

**The policy, ported from the old app** (`gui/main_window.py`'s
`_processing_state_save_timer` and `SessionStateManager
.save_processing_state_for_dataset`), including its interval and its
reasoning:

- **Coalesce edits, don't save per edit.** A single ROI drag, key-repeat
  nudge or brush stroke emits a change signal per step. Writing the session
  on each one puts a real disk write in the middle of an interaction; one
  write a couple of seconds after the user stops is indistinguishable in
  outcome and invisible in feel. This is an evaluation app working from data
  it never mutates, so a burst of edits collapsing into one write loses
  nothing - unlike the acquisition app, where CLAUDE.md's lossless rule
  forbids exactly this kind of coalescing for raw data.
- **2500 ms**, the same interval the old app settled on, for the same
  reason: seconds rather than milliseconds, because the timeout fires the
  real write.
- **Checkpoints bypass the debounce.** A dataset switch, a session load and
  application quit each need the file on disk *before* the next thing
  happens, so those call `flush()` rather than `schedule()`.

**Two things here that the old app does differently, both deliberate:**

1. **A plain dirty flag, not a content signature.** The old app rebuilds the
   whole payload and compares it against the last-written one to decide
   whether to skip a save - it has to, because its save triggers fire
   whether or not anything really changed, and rebuilding that payload is
   itself measured in seconds on a real dataset. Here a save is only ever
   scheduled by a module's own change signal, so "was anything scheduled
   since the last successful write" answers the same question for free. The
   trade-off is that a change signal that carries no real change still costs
   one write; that is a cheap wrong answer, where rebuilding a payload to
   find out is an expensive right one.
2. **The write runs on its own thread** (2026-10-17). It used to run on the
   GUI thread ("needs a real measurement first"); the measurement
   (`docs/session_autosave_blocking_analysis_2026-10-17.md`) showed 3-4 s
   freezes with ~400 masks, and the maintainer's rule is that saving has no
   reason to be on the GUI thread. The split:

   - **GUI thread** (when the debounce fires): read every module's state and
     `prepare_session` it into plain, copied data - milliseconds, and the only
     moment the modules are touched. Several module queries hand out live
     mutable objects, so this must not move to the worker.
   - **Worker thread** (`_WriteWorker`, a plain `threading.Thread`, never a
     `QThreadPool`): the PNG and JSON file work. One thread, so writes never
     overlap and the atomic JSON replace keeps its meaning. Requests are kept
     *per root*, newest wins: an edit made while a write runs replaces the
     request waiting behind it, but a request for another dataset's root is never
     replaced by this one's (the dataset-switch flush must still land in the old
     folder).
   - **Back on the GUI thread**: a failed write comes back through a queued
     signal and is reported exactly as before (`save_failed`, dirty flag kept).

   `flush()` (quit, tests) still means "on disk when I return" and waits.
   `set_root()` no longer waits: its flush is handed over and the root switches at
   once; whoever is about to *read* that root's session back calls
   `wait_until_written(root)` first (`app_rewrite._wire_session_coordinator`).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..analysis.provenance import FrameNamingScheme
from ..applog import timed
from .session import PreparedSession, SessionState, SessionWriter, prepare_session

logger = logging.getLogger(__name__)

AUTOSAVE_DEBOUNCE_MS = 2500
"""See the module docstring - the old app's own interval, kept."""

FLUSH_TIMEOUT_S = 120.0
"""How long `flush()` (quit) waits for a running write before giving up with a warning. Generous: a first save of
hundreds of masks takes seconds; this only bounds a write that is stuck."""


class _WriteWorker:
    """One background thread that runs session writes one after another (see the module docstring, point 2).

    Pending requests are kept per root, oldest root first; submitting for a root that already waits replaces that request
    (its newer state includes the older one's edits) and keeps its place in the queue. The thread starts with the first
    request and is a daemon, so a stuck write can never keep the process from exiting; `close()` stops it politely."""

    def __init__(
        self,
        write: Callable[[Path, PreparedSession], object],
        on_done: Callable[[Path, BaseException | None], None],
    ) -> None:
        self._write = write
        self._on_done = on_done
        self._cond = threading.Condition()
        self._pending: dict[Path, PreparedSession] = {}
        self._busy_root: Path | None = None
        self._closing = False
        self._thread: threading.Thread | None = None

    def submit(self, root: Path, prepared: PreparedSession) -> None:
        with self._cond:
            self._pending[root] = prepared
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="session-autosave", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def _has_work(self, root: Path | None) -> bool:
        if root is None:
            return bool(self._pending) or self._busy_root is not None
        return root in self._pending or self._busy_root == root

    def wait_idle(self, timeout_s: float | None = None, root: Path | None = None) -> bool:
        """Block until nothing is pending or running (for *root* only, when given). ``False`` on timeout."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._has_work(root), timeout_s)

    def close(self, timeout_s: float = 5.0) -> None:
        with self._cond:
            self._closing = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout_s)

    def _run(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._pending or self._closing)
                if not self._pending:
                    return  # closing, and nothing left to write
                root = next(iter(self._pending))
                prepared = self._pending.pop(root)
                self._busy_root = root
            error: BaseException | None = None
            try:
                self._write(root, prepared)
            except Exception as exc:  # reported on the GUI thread; the worker must survive it
                error = exc
            try:
                self._on_done(root, error)
            finally:
                with self._cond:
                    self._busy_root = None
                    self._cond.notify_all()


class SessionAutosave(QObject):
    """Turns "something changed" signals into at most one session write per
    `AUTOSAVE_DEBOUNCE_MS` of activity, and runs that write off the GUI thread.

    Takes `capture`/`naming` as callables rather than module references, the
    same convention `AnalysisEngine` and `ImageRenderer` follow: this class
    then holds no module state of its own and a test can drive it with two
    plain functions. Both are called on the GUI thread only.

    `write(root, prepared)` is what the worker thread runs (default: a `SessionWriter`).

    `save_failed(message)` is emitted (on the GUI thread) whenever a write fails (disk full, locked
    file, permissions) so the app can tell the user: a failure that only
    reaches the log leaves them believing their ROIs and masks are on disk.
    """

    save_failed = pyqtSignal(str)
    _failure_pending = pyqtSignal()  # emitted by the worker thread, so Qt queues the delivery to the GUI thread

    def __init__(
        self,
        capture: Callable[[], SessionState],
        naming: Callable[[], FrameNamingScheme],
        *,
        write: Callable[[Path, PreparedSession], object] | None = None,
        interval_ms: int = AUTOSAVE_DEBOUNCE_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._capture = capture
        self._naming = naming
        self._write = write if write is not None else SessionWriter().write
        self._root: Path | None = None
        self._enabled = True
        self._dirty = False
        self._suspended = False
        self._failures: deque[tuple[Path, BaseException]] = deque()  # appended by the worker, drained on the GUI thread
        self._worker = _WriteWorker(lambda root, prepared: self._write(root, prepared), self._on_write_done)
        self._failure_pending.connect(self._drain_failures)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self._save_now)

    # -- which dataset is being saved --------------------------------------

    def root(self) -> Path | None:
        """The dataset folder being autosaved into, or `None` when nothing
        is loaded and every `schedule()` is a no-op."""
        return self._root

    def set_root(self, root: Path | None, *, enabled: bool = True) -> None:
        """Point this at a dataset's folder, **handing over anything still
        pending for the previous one first** (it is written in the background, into the previous folder).

        Without that hand-over, switching datasets within the debounce window
        would either lose the last edits to the old dataset or - worse -
        write them into the new dataset's folder, since the pending timer
        holds no root of its own.

        `enabled=False` accepts the root but refuses to ever write to it.
        That is for one specific case: a session file that exists but could
        not be read (corrupt, or a schema this build doesn't know). The
        modules are then sitting at defaults, which is *not* what that file
        describes, so autosaving would overwrite a recoverable file with
        blank state - the one genuinely destructive thing this class could
        do."""
        self._timer.stop()
        self._save_now()
        self._root = None if root is None else Path(root)
        self._enabled = enabled
        self._dirty = False

    # -- the debounce ------------------------------------------------------

    def schedule(self, *_args: object) -> None:
        """Note that something changed and (re)start the debounce. Takes
        `*_args` so it can be connected straight to any module's change
        signal without a lambda per connection."""
        if self._suspended or self._root is None or not self._enabled:
            return
        self._dirty = True
        self._timer.start()

    def flush(self) -> None:
        """Write now, if anything is pending, and **return once it is on disk**. Safe to call when nothing is
        loaded or nothing changed - both are no-ops, which is what makes it
        safe to hang off `aboutToQuit` unconditionally."""
        self._timer.stop()
        self._save_now()
        self.wait_until_written()

    def wait_until_written(self, root: Path | None = None, timeout_s: float = FLUSH_TIMEOUT_S) -> bool:
        """Block until every write already handed over is finished (those for *root* only, when given) and report any
        failure. Returns at once when nothing is running, which is the usual case.

        For code that is about to read a session folder back (loading a session) or to end the process (quit)."""
        finished = self._worker.wait_idle(timeout_s, None if root is None else Path(root))
        if not finished:
            logger.warning("Session autosave: a write was still running after %.0f s", timeout_s)
        self._drain_failures()
        return finished

    def close(self) -> None:
        """Finish what is queued and stop the worker thread (tests; the app just exits - the thread is a daemon)."""
        self.flush()
        self._worker.close()

    def _save_now(self) -> None:
        """Debounce fired (or a checkpoint): capture on this (GUI) thread, hand the copy to the worker."""
        if not self._dirty or self._root is None or not self._enabled:
            return
        root = self._root
        try:
            with timed(logger, "session.capture", root=root.name) as t:
                prepared = prepare_session(self._capture(), self._naming())
                t.set(masks=len(prepared.mask_changes))
        except Exception as exc:
            # A failed capture must not take the application down, and must not be
            # silent either. `_dirty` stays set, so the next edit or the quit flush tries again.
            self._report_failure(root, exc)
            return
        self._dirty = False  # handed over; a failed write sets it again (`_report_failure`)
        self._worker.submit(root, prepared)

    # -- the worker's results ----------------------------------------------

    def _on_write_done(self, root: Path, error: BaseException | None) -> None:
        """Runs on the worker thread. A success needs no follow-up; a failure is queued for the GUI thread."""
        if error is None:
            return
        self._failures.append((root, error))
        try:
            self._failure_pending.emit()
        except RuntimeError:  # the Qt object is already gone (the app is closing): the failure was logged by nobody else, so log it
            logger.error("Session autosave failed for %s while closing", root, exc_info=(type(error), error, error.__traceback__))

    def _drain_failures(self) -> None:
        while True:
            try:
                root, error = self._failures.popleft()
            except IndexError:
                return
            self._report_failure(root, error)

    def _report_failure(self, root: Path, error: BaseException) -> None:
        """Log it *and* tell the user (`save_failed`). The change stays pending for the root being saved, so the
        next edit or the quit flush tries again rather than treating a failed write as a completed one."""
        logger.error("Session autosave failed for %s", root, exc_info=(type(error), error, error.__traceback__))
        if root == self._root:
            self._dirty = True
            self.save_failed.emit(f"Session not saved: {error} - will retry on the next change and when the app closes.")
        else:
            self.save_failed.emit(f"Session in {root.name} not saved: {error}")

    @contextmanager
    def suspended(self) -> Iterator[None]:
        """Ignore change signals for the duration - for a session *restore*,
        which necessarily emits from every module it touches.

        Leaves the state clean on exit rather than merely un-suspending:
        what is in memory after a restore is exactly what was just read off
        disk, so there is nothing to write back. Suppressing the writes but
        leaving the dirty flag set would make the very next quit re-save an
        identical file. Any edit pending from *before* the restore is
        dropped with it, which is correct - the restore replaced whatever
        that edit was made to."""
        previous_suspended = self._suspended
        self._suspended = True
        self._timer.stop()
        try:
            yield
        finally:
            self._suspended = previous_suspended
            self._dirty = False
