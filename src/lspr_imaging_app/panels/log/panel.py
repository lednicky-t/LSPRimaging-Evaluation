"""``LogPanel`` - the Log console (Help -> Log Console, Ctrl+Shift+L), 2026-10-14.

Display only. The records live in `applog` (the ring buffer and the JSON Lines
file); this panel pulls new entries on a 100 ms timer while it is visible, filters
them, and shows them in a table, with a detail pane and a Timings tab (per-event
count / mean / p50 / p95 / max / total, from the same data). Screen description
and design: `docs/logging_design_2026-10-14.md`.

- **Level / search / Timed**: display filters only (the file still has everything).
  Search terms are ANDed, ``-word`` excludes, the event name is searched too.
- **Capture** (toolbar): the real *record-creation* level (root logger), so
  Info or Warning removes the DEBUG cost everywhere, not just from the view.
- **Follow**: auto-scroll to the newest row. **Mark**: log a ``ui.mark`` line, to
  bracket an experiment ("Mark 3, then press Play"). **Clear**: empties the *view*
  only (the file and the Timings are not touched).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ...applog import EntryFilter, LogEntry, LogSystem, RingBufferHandler, log_event
from ...applog.sampling import StackSampler
from ...applog.export import entries_to_csv, entries_to_jsonl, entries_to_text
from ..ui_state import UiStateStore
from .table_model import COL_LEVEL, COL_LOGGER, COL_MESSAGE, COL_MS, COL_TIME, LogTableModel

logger = logging.getLogger(__name__)

PROFILE_SECONDS = 10
_POLL_MS = 100
_POLL_BUSY_MS = 1000  # while the slideshow plays: each refresh repaints the table (~16 ms)
_TIMINGS_EVERY_TICKS = 10  # the Timings tab refreshes once a second
_ICON = 16
_LEVELS = (("Debug", logging.DEBUG), ("Info", logging.INFO), ("Warning", logging.WARNING), ("Error", logging.ERROR))
_CAPTURE_LEVELS = _LEVELS[:3]
_TIMING_COLUMNS = ("Event", "Count", "Mean ms", "p50 ms", "p95 ms", "Max ms", "Total ms")


class _NumberItem(QTableWidgetItem):
    """A table cell that shows text but sorts by its number."""

    def __init__(self, value: float, text: str) -> None:
        super().__init__(text)
        self._value = value
        self.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return self._value < getattr(other, "_value", 0.0)


class LogPanel(QWidget):
    def __init__(
        self,
        ring: RingBufferHandler,
        system: LogSystem | None = None,
        ui_state: UiStateStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ring = ring
        self._system = system
        self._last_seq = 0
        self._since_seq = 0  # "Clear" hides everything up to here from the view
        self._marks = 0
        self._ticks = 0
        self._profile = StackSampler()  # samples the GUI thread's stack; cProfile (applog/profiling.py) is all-thread and slow
        self._profile_count = 0
        self._filter = EntryFilter()
        self._model = LogTableModel(parent=self)
        self._buttons: dict[str, QToolButton] = {}

        # -- toolbar -------------------------------------------------------------------
        self._level_combo = QComboBox(self)
        for label, level in _LEVELS:
            self._level_combo.addItem(label, level)
        self._level_combo.setToolTip("Show this level and above (the file always keeps everything captured).")
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Filter: words are ANDed, -word excludes, event names work too")
        self._search.setClearButtonEnabled(True)
        self._timed = self._tool_button("clock", "Only entries with a duration (timings).", checkable=True)
        self._follow = self._tool_button("arrow-bar-to-down", "Follow: scroll to the newest entry.", checkable=True)
        self._follow.setChecked(True)
        self._mark = self._tool_button("pin", "Mark: add a marker line to the log (to bracket an experiment).")
        self._clear = self._tool_button("trash", "Clear the view (the log file and the timings are kept).")
        self._copy = self._tool_button("copy", "Copy the shown entries (or the selected ones) as text.")
        self._save = self._tool_button("device-floppy", "Save the shown entries as .jsonl, .csv or .txt.")
        self._open_folder = self._tool_button("folder-open", "Open the folder with the session log files.")
        self._profile_button = self._tool_button(
            "chart-histogram",
            f"Sample the GUI thread for {PROFILE_SECONDS} s (its stack every 10 ms; a small slowdown), then list where "
            "its time went and how much of it was the idle event loop, here and in the log. "
            "Start it, then do the slow thing (e.g. press Play).",
        )
        self._capture = QComboBox(self)
        for label, level in _CAPTURE_LEVELS:
            self._capture.addItem(f"Capture {label}", level)
        self._capture.setToolTip(
            "Record-creation level for the whole app. Info or Warning switches the DEBUG work off everywhere."
        )

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 4, 6, 2)
        toolbar.setSpacing(4)
        toolbar.addWidget(self._level_combo)
        toolbar.addWidget(self._search, 1)
        for button in (
            self._timed, self._follow, self._mark, self._clear, self._copy, self._save, self._open_folder,
            self._profile_button,
        ):
            toolbar.addWidget(button)
        toolbar.addWidget(self._capture)

        # -- Log tab -------------------------------------------------------------------
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        self._table.verticalHeader().hide()
        self._table.verticalHeader().setDefaultSectionSize(18)
        self._table.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_MESSAGE, QHeaderView.ResizeMode.Stretch)
        for column, width in ((COL_TIME, 96), (COL_LEVEL, 70), (COL_LOGGER, 190), (COL_MS, 64)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, width)
        self._detail = QPlainTextEdit(self)
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select an entry to see its fields and traceback.")
        self._detail.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self._table)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        # -- Timings tab -----------------------------------------------------------------
        self._timings = QTableWidget(0, len(_TIMING_COLUMNS), self)
        self._timings.setHorizontalHeaderLabels(_TIMING_COLUMNS)
        self._timings.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._timings.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._timings.verticalHeader().hide()
        self._timings.setSortingEnabled(True)
        self._timings.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._timings.setToolTip("Double-click an event to filter the Log tab to it.")
        self._timings.cellDoubleClicked.connect(self._on_timing_double_clicked)
        self._timings_reset = QPushButton("Reset timings", self)
        self._timings_reset.setToolTip("Forget the collected statistics (the log file is not touched).")
        self._timings_reset.clicked.connect(self._reset_timings)
        timings_page = QWidget(self)
        timings_layout = QVBoxLayout(timings_page)
        timings_layout.setContentsMargins(0, 0, 0, 0)
        timings_layout.addWidget(self._timings, 1)
        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(6, 2, 6, 2)
        reset_row.addStretch(1)
        reset_row.addWidget(self._timings_reset)
        timings_layout.addLayout(reset_row)

        self._tabs = QTabWidget(self)
        self._tabs.addTab(splitter, "Log")
        self._tabs.addTab(timings_page, "Timings")
        self._status = QLabel(self)
        self._status.setContentsMargins(6, 0, 6, 2)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(toolbar)
        layout.addWidget(self._tabs, 1)
        layout.addWidget(self._status)

        # Remembered across restarts. Bound before the change handlers are connected, so
        # restoring a saved value does not run them; they are run once by hand below.
        if ui_state is not None:
            ui_state.bind("log/level", self._level_combo)
            ui_state.bind("log/timed_only", self._timed)
            ui_state.bind("log/follow", self._follow)
            ui_state.bind("log/capture", self._capture)
        self._level_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._search.textChanged.connect(self._on_filter_changed)
        self._timed.toggled.connect(self._on_filter_changed)
        self._follow.toggled.connect(self._on_follow_toggled)
        self._capture.currentIndexChanged.connect(self._on_capture_changed)
        self._mark.clicked.connect(self.add_mark)
        self._clear.clicked.connect(self.clear_view)
        self._copy.clicked.connect(self.copy_text)
        self._save.clicked.connect(self._on_save_clicked)
        self._open_folder.clicked.connect(self.open_log_folder)
        self._profile_button.clicked.connect(lambda _checked=False: self.start_profile())  # not the bool as the duration
        self._table.selectionModel().selectionChanged.connect(lambda *_: self._show_detail())
        self._tabs.currentChanged.connect(lambda _i: self._refresh_timings())
        self._open_folder.setEnabled(system is not None)
        self._capture.setEnabled(system is not None)  # without a configured system there is nothing to switch

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._on_capture_changed()
        self._apply_filter()
        self._rebuild()
        self.refresh_theme()

    # -- building helpers -----------------------------------------------------------------

    def _tool_button(self, icon: str, tooltip: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        button.setFixedSize(24, 24)
        button.setIconSize(QSize(_ICON, _ICON))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setProperty("lspr_icon", icon)
        self._buttons[icon] = button
        return button

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        for icon, button in self._buttons.items():
            button.setIcon(load_tabler_icon(icon, color=theme.text_primary, size=_ICON * 2))
            button.setStyleSheet(
                transparent_icon_button_stylesheet(hover=theme.control_bg_hover, checked=theme.control_bg_hover)
            )
        self._status.setStyleSheet(f"color: {theme.text_dim};")
        self._model.refresh_theme()

    # -- live updates -----------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._poll()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._timer.stop()
        super().hideEvent(event)

    def _poll(self) -> None:
        self._ticks += 1
        new = self._ring.entries_since(self._last_seq)
        if new:
            self._last_seq = new[-1].seq
            self._model.append_entries(self._filter.apply(new))
            if self._follow.isChecked():
                self._table.scrollToBottom()
            self._update_status()
        if self._ticks % _TIMINGS_EVERY_TICKS == 0 and self._tabs.currentIndex() == 1:
            self._refresh_timings()

    def _rebuild(self) -> None:
        entries, self._last_seq = self._ring.snapshot_with_seq()
        self._model.set_entries(self._filter.apply(entries))
        if self._follow.isChecked():
            self._table.scrollToBottom()
        self._update_status()

    def _apply_filter(self) -> None:
        self._filter = EntryFilter(
            min_level=int(self._level_combo.currentData()),
            text=self._search.text(),
            timed_only=self._timed.isChecked(),
            since_seq=self._since_seq,
        )

    def _on_filter_changed(self, *_args: object) -> None:
        self._apply_filter()
        self._rebuild()

    def _on_follow_toggled(self, checked: bool) -> None:
        if checked:
            self._table.scrollToBottom()

    def _on_capture_changed(self, *_args: object) -> None:
        level = int(self._capture.currentData())
        if self._system is not None:
            self._system.set_capture_level(level)

    def _update_status(self) -> None:
        total = len(self._ring)
        where = f" | {self._system.log_path}" if self._system is not None else " | no log file (logging not configured)"
        self._status.setText(f"Showing {self._model.rowCount()} of {total} entries{where}")
        self._status.setToolTip(str(self._system.log_path) if self._system is not None else "")

    # -- commands ------------------------------------------------------------------------------

    def add_mark(self) -> None:
        self._marks += 1
        log_event(logging.getLogger("lspr_imaging_app.ui"), "ui.mark", logging.INFO, n=self._marks)
        self._poll()

    def clear_view(self) -> None:
        self._since_seq = self._ring.latest_seq()
        self._on_filter_changed()

    def shown_entries(self) -> list[LogEntry]:
        return self._model.entries()

    def selected_entries(self) -> list[LogEntry]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        return [entry for row in rows if (entry := self._model.entry_at(row)) is not None]

    def text_for_clipboard(self) -> str:
        return entries_to_text(self.selected_entries() or self.shown_entries())

    def copy_text(self) -> None:
        QGuiApplication.clipboard().setText(self.text_for_clipboard())

    def export_to(self, path: Path) -> int:
        """Write the shown entries to *path* (.jsonl, .csv, anything else = text). Returns the row count."""
        entries = self.shown_entries()
        renderer = {".jsonl": entries_to_jsonl, ".csv": entries_to_csv}.get(path.suffix.lower(), entries_to_text)
        path.write_text(renderer(entries) + "\n", encoding="utf-8")
        log_event(logger, "log.export", logging.INFO, file=path.name, rows=len(entries))
        return len(entries)

    def _on_save_clicked(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        start = str(self._system.log_dir) if self._system is not None else ""
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save log entries", start, "JSON Lines (*.jsonl);;CSV (*.csv);;Text (*.txt)"
        )
        if not path:
            return
        try:
            count = self.export_to(Path(path))
        except OSError as exc:
            logger.error("Could not save the log entries to %s: %s", path, exc)
            self._status.setText(f"Could not save: {exc}")
            return
        self._status.setText(f"Saved {count} entries to {path}")

    def set_busy(self, busy: bool) -> None:
        """Play started / stopped: refresh the table once a second instead of 10 times a second. Playing
        adds ~13 records per frame, so every refresh repaints the table; measured 2026-10-14, an open Log
        panel cost Play about 40 % of its frame rate. The records are collected either way."""
        self._timer.setInterval(_POLL_BUSY_MS if busy else _POLL_MS)
        if not busy and self.isVisible():
            self._poll()

    def start_profile(self, seconds: float = PROFILE_SECONDS) -> None:
        """Profile the GUI thread (this one) for *seconds*, then log and show the result."""
        if self._profile.running:
            return
        self._profile_count += 1
        self._profile.start()
        self._profile_button.setEnabled(False)
        self._status.setText(f"Profiling the GUI thread for {seconds:g} s - do the slow thing now ...")
        QTimer.singleShot(int(seconds * 1000), self.finish_profile)

    def finish_profile(self) -> None:
        save_dir = self._system.log_dir if self._system is not None else None
        session = self._system.session_id if self._system is not None else "nolog"
        result = self._profile.stop(save_dir, f"lspri-{session}.gui-{self._profile_count}")
        self._profile_button.setEnabled(True)
        if result is None:
            return
        logger.info(
            "GUI thread profile over %.1f s%s\n%s", result.seconds,
            f", saved {result.prof_path.name}" if result.prof_path else "", result.text,
            extra={
                "lspr_event": "profile.gui",
                "lspr_fields": {"seconds": round(result.seconds, 2), "file": result.prof_path.name if result.prof_path else None, "top": result.rows},
            },
        )
        self._poll()
        self._tabs.setCurrentIndex(0)
        self._table.selectRow(self._model.rowCount() - 1)
        self._detail.setPlainText(result.text)
        self._update_status()

    def open_log_folder(self) -> None:
        if self._system is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._system.log_dir)))

    # -- detail and timings --------------------------------------------------------------------

    def _show_detail(self) -> None:
        selected = self.selected_entries()
        if not selected:
            self._detail.clear()
            return
        entry = selected[0]
        lines = [f"{entry.level_name}  {entry.logger}  [{entry.thread}]", entry.message]
        if entry.event:
            lines.append(f"event: {entry.event}" + (f"   duration: {entry.dur_ms:.3f} ms" if entry.dur_ms is not None else ""))
        if entry.fields:
            lines.append(json.dumps(entry.fields, indent=2, default=repr))
        if entry.span is not None:
            lines.append(f"span {entry.span}" + (f", inside span {entry.parent}" if entry.parent is not None else ""))
        if entry.exc:
            lines.append(entry.exc)
        self._detail.setPlainText("\n".join(lines))

    def _refresh_timings(self) -> None:
        rows = self._ring.timing_rows()
        table = self._timings
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_number, row in enumerate(rows):
            table.setItem(row_number, 0, QTableWidgetItem(row.event))
            values = (row.count, row.mean_ms, row.p50_ms, row.p95_ms, row.max_ms, row.total_ms)
            for column, value in enumerate(values, start=1):
                text = f"{value}" if column == 1 else f"{value:.2f}"
                table.setItem(row_number, column, _NumberItem(float(value), text))
        table.setSortingEnabled(True)

    def _reset_timings(self) -> None:
        self._ring.reset_timings()
        self._refresh_timings()

    def _on_timing_double_clicked(self, row: int, _column: int) -> None:
        item = self._timings.item(row, 0)
        if item is not None:
            self._search.setText(item.text())
            self._tabs.setCurrentIndex(0)
