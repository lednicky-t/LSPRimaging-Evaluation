"""``PanelTask`` - how a display panel reports a recompute that takes a while to the app's task indicator (2026-10-24, maintainer request:
"all recalculating functions ... in Spectra / Sensorgram should have output of status in the status bar").

The panel calls ``begin`` when it hands a computation to its worker thread, ``update`` when the worker reports progress and ``end`` when the
result is in (or the run was dropped). The two signals have the shape ``TaskIndicator.report`` / ``finish`` take, so the owner connects them in
``app_rewrite._build_status_bar`` like any other task (CLAUDE.md, "Heavy work reports progress and can be cancelled").

**Only a run that lasts is shown.** A panel recomputes on every cube step of the slideshow, most in a few milliseconds; an entry for each would
flash the status bar and fill the history. So nothing is emitted until the run has lasted ``show_after_ms``; a run that ends sooner leaves no
trace. Runs that follow each other without a gap (a newer request replaced the running one) count as one: ``begin`` while a run is under way only
updates its message.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

SHOW_AFTER_MS = 400


class PanelTask(QObject):
    task_progress = pyqtSignal(str, str, float, str)  # (task_id, label, fraction (< 0: unknown), message)
    task_finished = pyqtSignal(str, str, str)  # (task_id, "completed" | "failed" | "cancelled", message)

    def __init__(self, task_id: str, label: str, parent: QObject | None = None, show_after_ms: int = SHOW_AFTER_MS) -> None:
        super().__init__(parent)
        self.task_id = task_id
        self._label = label
        self._active = False
        self._shown = False
        self._fraction = -1.0
        self._message = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(show_after_ms))
        self._timer.timeout.connect(self._show)

    def is_active(self) -> bool:
        return self._active

    def is_shown(self) -> bool:
        return self._shown

    def begin(self, message: str) -> None:
        """A computation was started (or replaced by a newer one: the clock keeps running)."""
        self._message = message
        if not self._active:
            self._active, self._fraction = True, -1.0
            self._timer.start()
        elif self._shown:
            self._emit()

    def update(self, fraction: float, message: str) -> None:
        """Progress of the running computation (`fraction` 0..1, or < 0 if unknown)."""
        if not self._active:
            return
        self._fraction, self._message = float(fraction), message
        if self._shown:
            self._emit()

    def end(self, outcome: str = "completed", message: str = "") -> None:
        """The run is over. Does nothing for a run that never became visible, or that was ended already."""
        was_shown = self._shown
        self._timer.stop()
        self._active = self._shown = False
        if was_shown:
            self.task_finished.emit(self.task_id, outcome, message)

    def _show(self) -> None:
        if self._active:
            self._shown = True
            self._emit()

    def _emit(self) -> None:
        self.task_progress.emit(self.task_id, self._label, self._fraction, self._message)
