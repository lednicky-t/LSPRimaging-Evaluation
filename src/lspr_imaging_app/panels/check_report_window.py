"""The report window of the data checks (2026-11-07): one non-modal window that shows a `CheckReport` (`analysis/check_report.py`). Used twice, with different content:
the Sensorgram's "Checks" (``with_pairs=True``) and the Image panel's "Motion".

Screen purpose: tell the user, in plain sentences, when the data behave systematically wrong, and give the numbers behind each sentence so they can judge it.

Layout (top to bottom)::

    [Pair: B1 = 1 · H2O, S1 = 8 · 10perEG  v]                    [Run again]     <- the pair picker only for the Sensorgram, shown when there are several pairs
    status line  (running / out of date / failed, else what the report was made from)
    findings     (a dot per finding: amber = warning, green = ok, grey = note; warnings first; the title in bold, then the sentence)
    > Numbers per step   (folded; opens the table, medians over the ROIs)
    [Copy] [Save report...]                                                [Close]

States: *not run yet* (an explanation, Run), *running* (the status line shows progress; the button is Cancel; the last report stays visible), *current*, *out of date* (the
report is kept and marked; Run again), *failed* (the reason; the last report stays). Copy puts the whole report on the clipboard as text; Save writes JSON, CSV (the table) or
text by the extension chosen, on a thread of its own. The window changes no result and holds no state of the checks: its owner (`sensorgram/checks_presenter.py`,
`image/motion_presenter.py`) tells it what to show with `set_state` and `set_progress`, and hears `run_requested`, `cancel_requested` and `pair_chosen`.

The size and whether the table is unfolded are remembered (``ui_state``).
"""

from __future__ import annotations

import html
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QHideEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme

from ..analysis.check_report import CheckReport, format_cell, to_csv, to_json, to_text
from ..analysis.step_diagnostics import INFO, OK, WARN
from .ui_state import UiStateStore

logger = logging.getLogger(__name__)

DEFAULT_SIZE = (860, 620)
_MIN_SIZE = (460, 300)
_COPIED_MS = 1500
_ORDER = {WARN: 0, INFO: 1, OK: 2}  # warnings first
RUN_TIP = "Check the data now. The checks run in the background; progress is in the status bar."
CANCEL_TIP = "Stop the checks."
COPY_TIP = "Copy the whole report (the findings and the table) to the clipboard as text."
SAVE_TIP = "Save the report: a .json file (findings and table), a .csv file (the table only) or a .txt file (as copied)."

PathChooser = Callable[[QWidget, str], "Path | None"]


def _ask_save_path(parent: QWidget, default_name: str) -> Path | None:
    chosen, _filter = QFileDialog.getSaveFileName(
        parent, "Save the report", default_name, "JSON report (*.json);;Table of numbers (*.csv);;Text (*.txt)"
    )
    return Path(chosen) if chosen else None


class CheckReportWindow(QDialog):
    run_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    pair_chosen = pyqtSignal(object)  # the pair's number
    _saved = pyqtSignal(str, str)  # (path, error text or "") - from the writing thread

    def __init__(
        self,
        title: str,
        *,
        size_key: str,
        numbers_key: str,
        ui_state: UiStateStore | None = None,
        with_pairs: bool = False,
        choose_path: PathChooser = _ask_save_path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self._store = ui_state
        self._size_key, self._numbers_key = size_key, numbers_key
        self._choose_path = choose_path
        self._report: CheckReport | None = None
        self._running = False
        self._stale = False
        self._failure: str | None = None
        self._empty_text = ""
        self._progress = ""
        self._note = ""  # the status line when nothing else is going on: what the report was made from

        self.pair_label = QLabel("", self)
        self.pair_label.setWordWrap(True)
        self.pair_combo = QComboBox(self)
        self.pair_combo.setToolTip("Which evaluation pair (a baseline step and a signal step of the Experimental plan) the checks compare.")
        self.run_button = QPushButton("Run", self)
        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.numbers_button = QToolButton(self)
        self.numbers_button.setCheckable(True)
        self.numbers_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.numbers_button.setAutoRaise(True)
        self.table = QTableWidget(self)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.hide()
        self.copy_button = QPushButton("Copy", self)
        self.copy_button.setToolTip(COPY_TIP)
        self.save_button = QPushButton("Save report…", self)
        self.save_button.setToolTip(SAVE_TIP)
        self.close_button = QPushButton("Close", self)

        top = QHBoxLayout()
        top.addWidget(self.pair_label, 1)
        top.addWidget(self.pair_combo, 1)
        top.addWidget(self.run_button)
        bottom = QHBoxLayout()
        bottom.addWidget(self.copy_button)
        bottom.addWidget(self.save_button)
        bottom.addStretch(1)
        bottom.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.status_label)
        layout.addWidget(self.browser, 2)
        layout.addWidget(self.numbers_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.table, 1)
        layout.addLayout(bottom)

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.timeout.connect(lambda: self.copy_button.setText("Copy"))
        self.pair_combo.hide()
        self.pair_label.setVisible(with_pairs)
        self._with_pairs = with_pairs
        self.run_button.clicked.connect(self._on_run_clicked)
        self.copy_button.clicked.connect(self._copy)
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.close)
        self.numbers_button.toggled.connect(self._on_fold_toggled)
        self.pair_combo.activated.connect(lambda _index: self.pair_chosen.emit(self.pair_combo.currentData()))
        self._saved.connect(self._on_saved)
        self.resize(*self._saved_size())
        self.numbers_button.setChecked(self._saved_unfolded())
        self.set_state(report=None, running=False, stale=False, failure=None, empty_text="Not run yet.")

    # -- what to show ----------------------------------------------------------------------------------------------------------

    def set_pairs(self, choices: list[tuple[int, str]], current: int | None) -> None:
        """The evaluation pairs the checks can compare (number, text) and the one in use. Several: a picker; one: its text; none: a sentence saying only the step checks run."""
        if not self._with_pairs:
            return
        self.pair_combo.blockSignals(True)
        self.pair_combo.clear()
        for number, text in choices:
            self.pair_combo.addItem(text, number)
        index = self.pair_combo.findData(current) if current is not None else -1
        self.pair_combo.setCurrentIndex(max(index, 0) if choices else -1)
        self.pair_combo.blockSignals(False)
        self.pair_combo.setVisible(len(choices) > 1)
        self.pair_label.setVisible(len(choices) <= 1)
        self.pair_label.setText(
            choices[0][1] if choices else "No evaluation pair is set (Experimental plan > Evaluation): only the step checks are made."
        )

    def set_state(self, *, report: CheckReport | None, running: bool, stale: bool, failure: str | None, empty_text: str = "") -> None:
        """Show ``report`` (None: none yet) with the state it is in. ``stale``: something it was made from changed. ``failure``: why the last run failed."""
        self._report, self._running, self._stale, self._failure = report, running, stale, failure
        if empty_text:
            self._empty_text = empty_text
        if not running:
            self._progress = ""
        self._render()

    def set_progress(self, fraction: float, message: str) -> None:
        self._progress = f"{message} ({round(100 * fraction)} %)" if fraction >= 0 else message
        self._render_status()

    def show_notice(self, text: str) -> None:
        """One sentence on the status line (why nothing was run); the next change of state replaces it."""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {get_active_theme().accent_gold};")

    def report(self) -> CheckReport | None:
        return self._report

    def findings_text(self) -> str:
        return self.browser.toPlainText()

    def status_text(self) -> str:
        return self.status_label.text()

    def table_visible(self) -> bool:
        return self.table.isVisibleTo(self)

    # -- drawing ---------------------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        self._render()

    def _render(self) -> None:
        report = self._report
        self.run_button.setText("Cancel" if self._running else "Run" if report is None else "Run again")
        self.run_button.setToolTip(CANCEL_TIP if self._running else RUN_TIP)
        self.copy_button.setEnabled(report is not None)
        self.save_button.setEnabled(report is not None)
        self.browser.setHtml(self._findings_html(report))
        table = None if report is None else report.table
        self.numbers_button.setVisible(table is not None)
        self.numbers_button.setText(table.title if table is not None else "")
        self._apply_fold()
        self._fill_table(table)
        self._render_status()

    def _render_status(self) -> None:
        theme = get_active_theme()
        if self._running:
            text, colour = (self._progress or "Running…"), theme.accent_blue
        elif self._failure is not None:
            text, colour = f"The checks failed: {self._failure}", theme.accent_red
        elif self._stale:
            text, colour = "Out of date: something the checks use has changed since this run. Run again to check the data as they are now.", theme.accent_gold
        else:
            text, colour = (self._report.subtitle if self._report is not None else ""), theme.text_dim
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {colour};")

    def _findings_html(self, report: CheckReport | None) -> str:
        theme = get_active_theme()
        dim = theme.text_dim
        if report is None:
            return f"<p style='color:{dim}'>{html.escape(self._empty_text)}</p>"
        colour = {OK: theme.accent_green, WARN: theme.accent_gold, INFO: dim}
        counts = [(report.warnings, "warning"), (sum(1 for f in report.findings if f.level == INFO), "note"), (sum(1 for f in report.findings if f.level == OK), "ok")]
        summary = " · ".join(f"{n} {word}{'s' if n != 1 and word != 'ok' else ''}" for n, word in counts if n)
        parts = [f"<p style='color:{dim}'>{html.escape(summary or 'No findings.')}</p>"]
        if self._stale:
            parts[0] = f"<p style='color:{dim}'><i>{html.escape(summary)} (out of date)</i></p>"
        for f in sorted(report.findings, key=lambda f: _ORDER.get(f.level, 3)):
            dot = f"<span style='color:{colour.get(f.level, dim)}'>●</span>"
            parts.append(f"<p>{dot} <b>{html.escape(f.title)}</b><br>{html.escape(f.detail)}</p>")
        return "".join(parts)

    def _fill_table(self, table) -> None:
        self.table.clear()
        if table is None:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        self.table.setColumnCount(len(table.headers))
        self.table.setHorizontalHeaderLabels(list(table.headers))
        self.table.setRowCount(len(table.rows))
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row):
                item = QTableWidgetItem(format_cell(cell))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if not isinstance(cell, str):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

    def _apply_fold(self) -> None:
        unfolded = self.numbers_button.isChecked()
        self.numbers_button.setArrowType(Qt.ArrowType.DownArrow if unfolded else Qt.ArrowType.RightArrow)
        has_table = self._report is not None and self._report.table is not None
        self.table.setVisible(unfolded and has_table)

    def _on_fold_toggled(self, unfolded: bool) -> None:
        self._apply_fold()
        if self._store is not None:
            self._store.set(self._numbers_key, bool(unfolded))

    # -- buttons ---------------------------------------------------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        (self.cancel_requested if self._running else self.run_requested).emit()

    def _copy(self) -> None:
        if self._report is None:
            return
        QGuiApplication.clipboard().setText(to_text(self._report))
        self.copy_button.setText("Copied")
        self._copied_timer.start(_COPIED_MS)

    def _save(self) -> None:
        if self._report is None:
            return
        path = self._choose_path(self, f"{self._report.title.lower().replace(' ', '_')}.json")
        if path is None:
            return
        suffix = path.suffix.lower()
        text = to_csv(self._report) if suffix == ".csv" else to_text(self._report) if suffix == ".txt" else to_json(self._report)
        if suffix == ".csv" and not text:
            self.status_label.setText("This report has no table to save as CSV: choose .json or .txt.")
            return
        self.save_button.setEnabled(False)

        def write() -> None:
            try:
                path.write_text(text, encoding="utf-8", newline="\n")
                self._saved.emit(str(path), "")
            except OSError as exc:
                logger.warning("Could not save the report to %s: %s", path, exc)
                self._saved.emit(str(path), str(exc))

        threading.Thread(target=write, name="check-report-save", daemon=True).start()  # a file write never blocks the window

    def _on_saved(self, path: str, error: str) -> None:
        self.save_button.setEnabled(self._report is not None)
        self.status_label.setText(f"Could not save the report: {error}" if error else f"Saved {path}")

    # -- remembered state ------------------------------------------------------------------------------------------------------

    def _saved_unfolded(self) -> bool:
        return self._store.get(self._numbers_key) is True if self._store is not None else False

    def _saved_size(self) -> tuple[int, int]:
        saved = self._store.get(self._size_key) if self._store is not None else None
        if (
            isinstance(saved, list) and len(saved) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in saved)
            and saved[0] >= _MIN_SIZE[0] and saved[1] >= _MIN_SIZE[1]
        ):
            return int(saved[0]), int(saved[1])
        return DEFAULT_SIZE

    def hideEvent(self, event: QHideEvent) -> None:
        if self._store is not None:
            self._store.set(self._size_key, [self.width(), self.height()])
        super().hideEvent(event)
