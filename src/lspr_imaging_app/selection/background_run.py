"""``BackgroundRun``: run one job on a thread of its own and hand the result back on the GUI thread (2026-11-07).

Used by the data checks (`data_checks_module.py`) and the image-motion check (`image_motion_module.py`). It is the pattern of `StepTableModule`, kept in one place:
a plain ``threading.Thread`` (never ``QThreadPool``: the jobs touch the dataset and the scan), a cancel flag the job polls, progress and the result sent back as queued signals,
and "the newest request wins": starting a run cancels the one before it.

``start(work)`` takes ``work(cancelled, report)``: ``cancelled()`` is True once the run was cancelled or replaced, ``report(fraction, message)`` sends progress (fraction 0..1,
or < 0 when unknown). Whatever ``work`` returns arrives in ``done(token, result, error)`` **for every run, also a replaced one** (its ``token`` is no longer ``current``), so a
module can keep what a superseded run finished (the fitted traces) and drop the rest. An exception in ``work`` arrives as ``error``; nothing escapes a thread silently.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

JOIN_SECONDS = 5.0


class BackgroundRun(QObject):
    progress = pyqtSignal(float, str)
    """(fraction, message) of the current run (a replaced run's progress is dropped)."""
    done = pyqtSignal(object, object, object)
    """(token, result | None, error | None) of every run that ends, on the GUI thread."""
    _from_worker = pyqtSignal(object, object, object)
    _progress_from_worker = pyqtSignal(object, float, str)

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._token = 0
        self._cancel: threading.Event | None = None
        self._running = False
        self._threads: list[threading.Thread] = []
        self._from_worker.connect(self._on_done)
        self._progress_from_worker.connect(self._on_progress)

    # -- state ------------------------------------------------------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def is_current(self, token: object) -> bool:
        """True while ``token`` names the newest run (not replaced, not shut down)."""
        return token == self._token

    # -- running ----------------------------------------------------------------------------------------------------------------

    def start(self, work: Callable[[Callable[[], bool], Callable[[float, str], None]], object]) -> int:
        """Start ``work`` on a new thread (cancelling the run before it) and return the run's token. GUI thread."""
        if self._cancel is not None:
            self._cancel.set()
        self._token += 1
        token = self._token
        cancel = threading.Event()
        self._cancel = cancel
        self._running = True

        def report(fraction: float, message: str) -> None:
            self._progress_from_worker.emit(token, float(fraction), str(message))

        def run() -> None:
            try:
                result = work(cancel.is_set, report)
            except BaseException as exc:  # nothing may escape a thread silently
                logger.exception("%s failed", self._name)
                self._from_worker.emit(token, None, exc)
                return
            self._from_worker.emit(token, result, None)

        self._threads = [t for t in self._threads if t.is_alive()]
        thread = threading.Thread(target=run, name=self._name, daemon=True)
        self._threads.append(thread)
        thread.start()
        return token

    def cancel(self) -> None:
        """Stop the current run at its next check; its ``done`` still arrives (``cancelled`` is up to the job to report)."""
        if self._cancel is not None:
            self._cancel.set()

    def shutdown(self) -> None:
        """The application is quitting: cancel, wait briefly, and never emit into an object Qt is tearing down."""
        self._token += 1
        self.cancel()
        self._running = False
        for thread in self._threads:
            thread.join(timeout=JOIN_SECONDS)

    # -- from the worker, on the GUI thread -------------------------------------------------------------------------------------

    def _on_progress(self, token: object, fraction: float, message: str) -> None:
        if self.is_current(token):
            self.progress.emit(fraction, message)

    def _on_done(self, token: object, result: object, error: object) -> None:
        if self.is_current(token):
            self._running = False
        self.done.emit(token, result, error)
