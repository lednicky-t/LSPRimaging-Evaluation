"""The thread / signal / Cancel wrapper that the long-running tasks share (2026-10-22).

`TransientScanTask`, `MaskAutoTask` and `MaskExportTask` each used to carry their own copy of this machinery; a fix to cancellation
or to the stale-result rule had to be made three times. A task now only says *what* to compute and *what to do with the result*:

- ``start()`` (the subclass's own signature) checks its inputs, calls ``_begin_run()`` and then ``_start_thread(compute)``;
  ``compute()`` runs on a **plain ``threading.Thread``** (never ``QThreadPool``: it may read OME-Zarr, see ``CLAUDE.md``) and
  touches no module.
- ``_on_result(result)`` runs back on the GUI thread, installs the result and returns ``(message, summary)``; the summary goes out
  as ``completed``. It may raise `TaskRefused` to end the run as a failure without installing anything.

The signals are the app-wide task-indicator shapes (``panels/task_indicator.py``): ``task_progress(task_id, label, fraction,
message)`` and ``task_finished(task_id, outcome, message)``, the latter exactly once per run, after success, failure or cancel alike.

**A run belongs to the dataset and session it started in** (scan audit A1): ``invalidate()`` cancels a running task and makes a
result that still arrives be dropped.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from .progress import Cancelled


class TaskRefused(Exception):
    """Raised by ``_on_result`` when the finished work must not be installed; the text is shown to the user as the failure."""


class ThreadedTask(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction 0..1, message
    completed = pyqtSignal(object)  # the summary `_on_result` returned
    failed = pyqtSignal(str)  # readable message
    cancelled = pyqtSignal()
    running_changed = pyqtSignal(bool)
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome ("completed" | "failed" | "cancelled"), message; once per run

    # Internal: thread -> GUI thread hand-off (the result or the error, never both).
    _finished = pyqtSignal(object, object)

    task_id = ""
    task_label = ""
    thread_name = "task"
    crash_log = "The task crashed"
    stopped_message = "stopped: the dataset changed"  # shown for a run that `invalidate` made stale

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._log = logging.getLogger(type(self).__module__)
        self._running = False
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._generation = 0  # bumped by `invalidate`; a run whose number is not the current one has lost its place
        self._run_generation = 0
        self._finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self._cancel_event.set()

    def invalidate(self) -> None:
        """The dataset or the session changed under a running task: whatever it finds no longer belongs here. Cancels it and makes
        sure that a result which still arrives (it may finish before it notices) is dropped, not installed."""
        self._generation += 1
        if self._running:
            self._log.info("%s is stopped: the dataset or the session changed", self.task_label)
            self._cancel_event.set()

    def shutdown(self) -> None:
        """Application is quitting: stop the thread and wait briefly, so it never emits into a widget Qt is tearing down."""
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    # -- for subclasses ------------------------------------------------------------------------------------------------------

    def _emit_progress(self, fraction: float, message: str) -> None:
        self.task_progress.emit(self.task_id, self.task_label, fraction, message)

    def _begin_run(self) -> None:
        """Call from ``start()`` once the inputs are accepted (GUI thread)."""
        self._cancel_event.clear()
        self._run_generation = self._generation
        self._running = True
        self.running_changed.emit(True)

    def _start_thread(self, compute: Callable[[], object]) -> None:
        def work() -> None:
            result: object = None
            error: BaseException | None = None
            try:
                result = compute()
            except BaseException as exc:  # noqa: BLE001 - nothing may escape a thread silently
                error = exc
            self._finished.emit(result, error)

        self._thread = threading.Thread(target=work, name=self.thread_name, daemon=True)
        self._thread.start()

    def _on_result(self, result: object) -> tuple[str, object]:
        """GUI thread: install ``result``; return ``(message for the task indicator, summary for ``completed``)``."""
        raise NotImplementedError

    def _failure_message(self, error: BaseException) -> str:
        return f"Unexpected error: {error}"

    def _release(self) -> None:
        """Called when the run is over, whatever it ended in; drop what only this run needed."""

    def _stop_running(self) -> None:
        self._running = False
        self._thread = None
        self._release()
        self.running_changed.emit(False)

    def _report(self, outcome: str, message: str) -> None:
        self.task_finished.emit(self.task_id, outcome, message)
        if outcome == "cancelled":
            self.cancelled.emit()
        elif outcome == "failed":
            self.failed.emit(message)

    def _report_crash(self, error: BaseException, message: str) -> None:
        self._log.error("%s", self.crash_log, exc_info=error)
        self._report("failed", message)

    # -- back on the GUI thread ----------------------------------------------------------------------------------------------

    def _after_run(self) -> None:
        """Called last, once everything about a finished run has been reported (so a task may start its next run here)."""

    def _on_finished(self, result: object, error: object) -> None:
        try:
            self._handle_finished(result, error)
        finally:
            self._after_run()

    def _handle_finished(self, result: object, error: object) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._stop_running()
        if self._run_generation != self._generation:  # the dataset or session changed: drop it, whatever it ended in
            self._report("cancelled", self.stopped_message)
            return
        if isinstance(error, Cancelled):
            self._report("cancelled", "")
            return
        if isinstance(error, BaseException):
            self._report_crash(error, self._failure_message(error))
            return
        try:
            message, summary = self._on_result(result)
        except TaskRefused as refused:
            self._report("failed", str(refused))
            return
        self._report("completed", message)
        self.completed.emit(summary)
