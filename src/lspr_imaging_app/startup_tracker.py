"""``StartupTracker`` - tells the status bar when the app has finished loading after launch (2026-10-24, maintainer request: "it is
clear when the app is fully loaded, and datasets are loaded with everything").

When the app reopens the last dataset, "loaded" is not one moment: the folder is scanned, the session (ROIs, masks, settings) is read,
the first image is drawn, and the stored scan results are read and checked against the current setup. Each of those already runs on its
own thread and reports (or does not report) separately, so none of them can say "everything is ready". This class does: it is given a
list of **steps**, each a plain ``done() -> bool`` question about the state of the app, and watches them until all are true.

It follows the app-wide task-indicator shapes (``panels/task_indicator.py``): ``task_progress(task_id, label, fraction, message)`` and
``task_finished(task_id, outcome, message)``, the latter exactly once per run. The fraction is always ``-1`` (unknown): the steps differ
hugely in length, so a percentage or an ETA from "3 of 5 steps" would mislead; the message names the step being waited for instead.

How it decides, and why it polls: a step is *done* when its question answers True, and steps are judged **in order** (a later step is not
asked about until the earlier ones are done, because e.g. "scan results settled" is trivially true before the session that owns the scan
file has been opened). The questions are cheap attribute reads, so the tracker asks every ``poll_ms`` on the GUI thread instead of
subscribing to each module's signals: a missed signal can then never leave the spinner running forever. To not announce "ready" in the
gap between two steps (a module starts its follow-up work from a queued signal), all steps must be done on ``settle_polls`` polls in a
row. A run that is still not done after ``timeout_s`` ends as *failed* and names the step it is stuck on, so a step that can never become
true shows up as a message instead of an endless spinner.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import NamedTuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .applog import log_event

logger = logging.getLogger(__name__)

STARTUP_TASK_ID = "app.startup"
STARTUP_TASK_LABEL = "Starting up"

POLL_MS = 200
SETTLE_POLLS = 3  # all steps done this many polls in a row (0.6 s with the default poll) before "ready"
TIMEOUT_S = 600.0


class StartupStep(NamedTuple):
    name: str  # short, for the log and the final summary ("session")
    waiting_for: str  # shown while this is the step being waited for ("reading the session")
    done: Callable[[], bool]  # cheap, GUI thread, no side effects


class StartupTracker(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # task_id, label, fraction (always -1: unknown), message
    task_finished = pyqtSignal(str, str, str)  # task_id, outcome ("completed" | "failed" | "cancelled"), message; once per run

    def __init__(
        self,
        steps: list[StartupStep],
        parent: QObject | None = None,
        *,
        poll_ms: int = POLL_MS,
        settle_polls: int = SETTLE_POLLS,
        timeout_s: float = TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self._steps = list(steps)
        self._settle_polls = max(1, int(settle_polls))
        self._timeout_s = float(timeout_s)
        self._clock = clock
        self._running = False
        self._started = 0.0
        self._quiet_polls = 0
        self._last_message = ""
        self._timer = QTimer(self)
        self._timer.setInterval(int(poll_ms))
        self._timer.timeout.connect(self._poll)

    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Begin watching (call when the work that makes the app "loaded" has been started). Does nothing if already running."""
        if self._running:
            return
        self._running = True
        self._started = self._clock()
        self._quiet_polls = 0
        self._last_message = ""
        self._poll()
        if self._running:  # (the first poll may already have ended the run)
            self._timer.start()

    @pyqtSlot(str)
    def fail(self, message: str) -> None:
        """The work cannot complete (e.g. the dataset could not be opened): end the run as failed with `message`."""
        self._finish("failed", message)

    @pyqtSlot(str)
    def end_early(self, message: str) -> None:
        """The app is ready but waiting for the user (e.g. a folder holding several datasets needs a choice): end as completed."""
        self._finish("completed", message)

    @pyqtSlot(object)
    def on_choice_needed(self, _choice: object) -> None:
        """The folder holds several datasets and the user must pick one: nothing more to wait for."""
        self.end_early("choose which dataset to open")

    @pyqtSlot()
    def cancel(self) -> None:
        """Stop watching. The loading itself is not interrupted; only the status-bar entry ends."""
        self._finish("cancelled", "stopped watching; loading continues in the background")

    # -- internals -----------------------------------------------------------------------------------------------------------

    def _finish(self, outcome: str, message: str) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        log_event(logger, "app.startup_done", logging.INFO if outcome != "failed" else logging.WARNING,
                  outcome=outcome, seconds=round(self._clock() - self._started, 2), detail=message)
        self.task_finished.emit(STARTUP_TASK_ID, outcome, message)

    def _poll(self) -> None:
        if not self._running:
            return
        waiting = self._first_pending()
        if isinstance(waiting, Exception):
            self._finish("failed", f"could not check the startup state: {waiting}")
            return
        if waiting is None:
            self._quiet_polls += 1
            if self._quiet_polls >= self._settle_polls:
                names = ", ".join(step.name for step in self._steps)
                self._finish("completed", f"ready ({names})" if names else "ready")
                return
            self._report(f"step {len(self._steps)} of {len(self._steps)}: finishing")
            return
        self._quiet_polls = 0
        if self._clock() - self._started > self._timeout_s:
            self._finish("failed", f"still waiting for the {waiting.name} after {self._timeout_s / 60:.0f} minutes")
            return
        index = self._steps.index(waiting) + 1
        self._report(f"step {index} of {len(self._steps)}: {waiting.waiting_for}")

    def _first_pending(self) -> StartupStep | Exception | None:
        for step in self._steps:
            try:
                if not step.done():
                    return step
            except Exception as exc:  # noqa: BLE001 - a broken question must end the run visibly, not leave a spinner
                logger.exception("Startup step %r could not be checked", step.name)
                return exc
        return None

    def _report(self, message: str) -> None:
        if message != self._last_message:  # only changes, not every poll
            self._last_message = message
            self.task_progress.emit(STARTUP_TASK_ID, STARTUP_TASK_LABEL, -1.0, message)
