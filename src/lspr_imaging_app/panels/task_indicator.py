"""App-wide task indicator: one status-bar row that shows every running
background task (CLAUDE.md, "Heavy work reports progress and can be
cancelled").

Feeding it is deliberately tiny. A task's owning module already emits

    task_progress(task_id, label, fraction, message)    # fraction 0..1, or < 0 = unknown
    task_finished(task_id, outcome, message)             # outcome: completed | failed | cancelled

so wiring a new task is two `connect` calls (`TaskIndicator.report` and
`TaskIndicator.finish`) plus one for Cancel (`cancel_requested(task_id)` ->
the module's own `cancel()`). The indicator never computes anything; it owns
no task state beyond "what is on screen" and a history of finished runs.

While a task runs the row shows: busy spinner, label + current message,
progress bar (a sliding "busy" bar while the fraction is unknown), start
time, elapsed, ETA and estimated total, and Cancel. With several tasks the
oldest is shown in full plus "+N more".

When the last task ends the row does **not** disappear: it keeps showing the
outcome ("done in 0:29", "failed: ...", "cancelled") with start and finish
times until the next task starts or the user clicks Dismiss, so a result is
never lost because nobody was watching. Every finished run is also kept in
`history()` and written to the standard `logging` stream (INFO, or WARNING
for a failure), which is where a future log panel will pick it up. The
"History" button opens a small popup of running and recent tasks.

ETA is a straight-line extrapolation (`estimate_total`), so it is only
trustworthy when the producer's progress is roughly proportional to time -
which is what the "stage weights from measured stage times" rule in CLAUDE.md
is for. It is withheld until there is enough signal to not be nonsense.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from lspr_ui import get_active_theme, load_tabler_icon
from PyQt6.QtCore import QPoint, QPointF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# ETA is withheld below these: an extrapolation from 1 % / 0.3 s is noise.
_MIN_FRACTION_FOR_ETA = 0.02
_MIN_ELAPSED_FOR_ETA_S = 1.5
_TICK_MS = 500  # elapsed/ETA refresh, independent of how often progress is reported
_SPINNER_MS = 80
_HISTORY_LIMIT = 200


def format_duration(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` from one hour up. Negative/NaN-safe (-> `0:00`)."""
    if not seconds > 0:  # also catches NaN
        return "0:00"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def estimate_total(elapsed_s: float, fraction: float) -> float | None:
    """Estimated total run time, or None while it cannot be told yet.

    Straight line: if `fraction` of the work took `elapsed_s`, all of it takes
    `elapsed_s / fraction`."""
    if fraction < _MIN_FRACTION_FOR_ETA or elapsed_s < _MIN_ELAPSED_FOR_ETA_S:
        return None
    return elapsed_s / min(fraction, 1.0)


def _clock(moment: datetime) -> str:
    return moment.strftime("%H:%M:%S")


class SpinnerWidget(QWidget):
    """Twelve short spokes in a circle; the brightest one steps clockwise and
    the ones behind it fade out (the macOS/iOS style busy indicator). Only
    ticks while `start()`ed, so an idle app has no timer running."""

    _SPOKES = 12

    def __init__(self, parent: QWidget | None = None, size: int = 14) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._head = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_SPINNER_MS)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _advance(self) -> None:
        self._head = (self._head + 1) % self._SPOKES
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(get_active_theme().accent_blue)
        side = min(self.width(), self.height())
        painter.translate(self.width() / 2, self.height() / 2)
        outer, inner = side / 2 - 1, side / 2 - 1 - side * 0.26
        for spoke in range(self._SPOKES):
            # How many steps this spoke is behind the head: 0 = the head
            # (opaque), larger = more faded. Spokes are laid out clockwise,
            # the head advances clockwise, so the tail trails behind it.
            behind = (self._head - spoke) % self._SPOKES
            shade = QColor(color)
            shade.setAlphaF(max(0.12, 1.0 - behind / self._SPOKES))
            pen = QPen(shade, max(1.5, side * 0.11))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.save()
            painter.rotate(spoke * 360 / self._SPOKES)  # Qt's positive rotation is clockwise on screen
            painter.drawLine(QPointF(0, -outer), QPointF(0, -inner))
            painter.restore()


@dataclass
class _Running:
    label: str
    started: float  # time.monotonic(), for durations
    started_at: datetime  # wall clock, for display
    fraction: float = -1.0
    message: str = ""


@dataclass(frozen=True)
class TaskRecord:
    """One finished run."""

    task_id: str
    label: str
    outcome: str  # "completed" | "failed" | "cancelled"
    message: str
    started_at: datetime
    duration_s: float

    @property
    def finished_at(self) -> datetime:
        return self.started_at + timedelta(seconds=self.duration_s)

    def summary(self) -> str:
        """One line for the row and the history popup."""
        verb = {"completed": "done", "failed": "FAILED", "cancelled": "cancelled"}.get(self.outcome, self.outcome)
        text = f"{self.label}: {verb} after {format_duration(self.duration_s)}"
        return f"{text} - {self.message}" if self.message else text


class _HistoryPopup(QFrame):
    """Read-only list of running and recent tasks, newest first."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setFont(QFont("Consolas", 8))
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._view)
        self.resize(640, 220)

    def set_lines(self, lines: list[str]) -> None:
        self._view.setPlainText("\n".join(lines) if lines else "No tasks have run yet.")


class TaskIndicator(QWidget):
    """The status-bar row. Hidden until the first task has run."""

    cancel_requested = pyqtSignal(str)  # task_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        theme = get_active_theme()
        self._theme = theme
        self._running: dict[str, _Running] = {}  # insertion order = start order
        self._history: list[TaskRecord] = []
        self._last: TaskRecord | None = None  # shown while nothing runs

        self._spinner = SpinnerWidget(self)
        self._text = QLabel(self)
        self._text.setMinimumWidth(60)
        self._text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # clipped, never widens the bar
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setFixedWidth(120)
        self._bar.setFixedHeight(12)
        self._bar.setStyleSheet("QProgressBar { font-size: 8pt; }")
        self._bar.setTextVisible(True)
        self._detail = QLabel(self)
        self._detail.setFont(QFont("Consolas", 8))
        self._detail.setStyleSheet(f"color: {theme.text_dim};")
        # Fixed room for the widest realistic text, so the row does not jitter as digits change.
        self._detail.setMinimumWidth(
            self._detail.fontMetrics().horizontalAdvance("started 00:00:00 | 00:00 elapsed | ~00:00 left | total ~00:00")
        )
        self._more = QLabel(self)
        self._more.setStyleSheet(f"color: {theme.text_dim};")
        self._cancel = self._icon_button("player-stop", "Cancel the running task", theme.accent_red)
        self._cancel.clicked.connect(self._on_cancel_clicked)
        self._dismiss = self._icon_button("x", "Dismiss this result (it stays in History)", theme.text_secondary)
        self._dismiss.clicked.connect(self._on_dismiss_clicked)
        self._history_button = self._icon_button("list", "Show running and recent tasks", theme.text_secondary)
        self._history_button.clicked.connect(self._show_history)
        self._popup: _HistoryPopup | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)
        self.setFixedHeight(20)
        self.setStyleSheet("QLabel { font-size: 8pt; }")
        for widget in (
            self._spinner, self._text, self._bar, self._detail, self._more,
            self._cancel, self._dismiss, self._history_button,
        ):
            row.addWidget(widget)

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._refresh)
        self.setVisible(False)

    def _icon_button(self, icon_name: str, tooltip: str, color: str) -> QToolButton:
        """A small free-standing icon: no frame or fill until hovered."""
        button = QToolButton(self)
        button.setIcon(load_tabler_icon(icon_name, color=color, size=16))
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(18, 18)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 0; }"
            f"QToolButton:hover {{ background: {self._theme.control_bg_hover}; border-radius: 3px; }}"
        )
        return button

    # -- the producer-facing interface -----------------------------------

    def report(self, task_id: str, label: str, fraction: float, message: str) -> None:
        """Slot for a module's `task_progress`. The first report of an unknown
        `task_id` starts its clock and replaces any remembered result."""
        state = self._running.get(task_id)
        if state is None:
            state = self._running[task_id] = _Running(label=label, started=time.monotonic(), started_at=datetime.now())
            self._last = None
        state.label = label
        state.fraction = float(fraction)
        state.message = message
        self._refresh()

    def finish(self, task_id: str, outcome: str = "completed", message: str = "") -> None:
        """Slot for a module's `task_finished`. Records the run (history, log)
        and leaves its result on the row. Unknown ids are ignored."""
        state = self._running.pop(task_id, None)
        if state is None:
            return
        record = TaskRecord(
            task_id=task_id,
            label=state.label,
            outcome=outcome,
            message=message,
            started_at=state.started_at,
            duration_s=time.monotonic() - state.started,
        )
        self._history.append(record)
        del self._history[:-_HISTORY_LIMIT]
        self._last = record
        logger.log(
            logging.WARNING if outcome == "failed" else logging.INFO,
            "Task %s (started %s): %s", task_id, _clock(record.started_at), record.summary(),
        )
        self._refresh()

    def running_task_ids(self) -> list[str]:
        return list(self._running)

    def history(self) -> list[TaskRecord]:
        """Finished runs, oldest first (capped)."""
        return list(self._history)

    # -- display -----------------------------------------------------------

    def _refresh(self) -> None:
        if self._running:
            self._show_running()
        elif self._last is not None:
            self._show_result(self._last)
        else:
            self._spinner.stop()
            self._timer.stop()
            self.setVisible(False)

    def _show_running(self) -> None:
        _task_id, state = next(iter(self._running.items()))
        elapsed = time.monotonic() - state.started
        known = state.fraction >= 0.0
        fraction = min(state.fraction, 1.0) if known else 0.0

        self._text.setStyleSheet("")
        self._text.setText(f"{state.label}: {state.message}" if state.message else state.label)
        self._text.setToolTip(self._text.text())
        if known:
            self._bar.setRange(0, 100)
            self._bar.setValue(int(round(fraction * 100)))
            self._bar.setFormat("%p %")
        else:
            self._bar.setRange(0, 0)  # Qt's own sliding "busy" bar
            self._bar.setFormat("")
        total = estimate_total(elapsed, fraction) if known else None
        parts = [f"started {_clock(state.started_at)}", f"{format_duration(elapsed)} elapsed"]
        if total is not None:
            parts.append(f"~{format_duration(max(total - elapsed, 0.0))} left")
            parts.append(f"total ~{format_duration(total)}")
        self._detail.setText("  |  ".join(parts))
        extra = len(self._running) - 1
        self._more.setText(f"+{extra} more" if extra > 0 else "")

        self._spinner.setVisible(True)
        self._spinner.start()
        self._bar.setVisible(True)
        self._cancel.setVisible(True)
        self._dismiss.setVisible(False)
        if not self._timer.isActive():
            self._timer.start()
        self.setVisible(True)

    def _show_result(self, record: TaskRecord) -> None:
        self._spinner.stop()
        self._timer.stop()
        self._spinner.setVisible(False)
        self._bar.setVisible(False)
        self._cancel.setVisible(False)
        self._dismiss.setVisible(True)
        color = {
            "completed": self._theme.accent_green,
            "failed": self._theme.accent_red,
        }.get(record.outcome, self._theme.accent_gold)
        self._text.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 8pt;")
        self._text.setText(record.summary())
        self._text.setToolTip(record.summary())
        self._detail.setText(f"started {_clock(record.started_at)}  |  finished {_clock(record.finished_at)}")
        self._more.setText("")
        self.setVisible(True)

    def _on_cancel_clicked(self) -> None:
        if self._running:
            self.cancel_requested.emit(next(iter(self._running)))

    def _on_dismiss_clicked(self) -> None:
        self._last = None
        self._refresh()

    def history_lines(self) -> list[str]:
        """Text of the History popup: running tasks first, then finished ones, newest first."""
        lines = []
        for task_id, state in self._running.items():
            percent = f"{state.fraction * 100:.0f} %" if state.fraction >= 0 else "..."
            elapsed = format_duration(time.monotonic() - state.started)
            lines.append(f"{_clock(state.started_at)}  RUNNING  {state.label} ({task_id})  {percent}  {elapsed} elapsed")
        for record in reversed(self._history):
            lines.append(f"{_clock(record.started_at)}  {record.outcome.upper():<9}  {record.summary()}")
        return lines

    def _show_history(self) -> None:
        if self._popup is None:
            self._popup = _HistoryPopup(self)
        self._popup.set_lines(self.history_lines())
        # Popups open above the bar: the status bar is at the bottom of the window.
        self._popup.move(self._history_button.mapToGlobal(QPoint(self._history_button.width() - self._popup.width(), -self._popup.height())))
        self._popup.show()
