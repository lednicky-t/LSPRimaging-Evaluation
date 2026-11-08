"""``ChecksPresenter``: connects the data-checks module, the ribbon's "Checks" button and the report window (2026-11-07).

Each of the three knows nothing of the others: the module (`selection/data_checks_module.py`) runs the checks and knows whether its report is current; the button
(`checks_group.py`) shows a dot; the window (`panels/check_report_window.py`) shows the report. This object carries what one tells the others, and owns the `PanelTask` that puts a
long run in the status bar (the module sits below the panels and cannot import it). The app builds one (`app_rewrite._wire_data_checks`).

**A click on the button** opens the window and, unless the report is current, runs the checks (a run is in the background; the window shows progress and a Cancel). **Run again** in the
window always runs. **Choosing another pair** in the window changes the module's pair and runs the checks on it. The window is drawn only while it is shown (a settings change would
otherwise rebuild its text for nobody); opening it draws the current state.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject

from ...selection.data_checks_module import FAILED, NOT_RUN, RUNNING, STALE, DataChecksModule
from ..check_report_window import CheckReportWindow
from ..panel_task import PanelTask
from .checks_group import ChecksControls

TASK_ID = "checks.data"
NOT_RUN_TEXT = (
    "Not run yet. The checks look at every ROI of the scan: they find steps that are noisier than the others, noise that does not average out, steps that have not settled, "
    "methods that read less of the signal than another, ROIs that change pixels or read a different signal, and uneven signal across the image. Press Run."
)


def summary_of(report) -> str:
    n = report.warnings
    return f"{n} warning{'s' if n != 1 else ''}" if n else "nothing to warn about"


class ChecksPresenter(QObject):
    def __init__(self, module: DataChecksModule, controls: ChecksControls, window: CheckReportWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._controls = controls
        self._window = window
        self.task = PanelTask(TASK_ID, "Data checks", self)
        controls.open_requested.connect(self.open_window)
        window.run_requested.connect(self._run)
        window.cancel_requested.connect(module.cancel)
        window.pair_chosen.connect(self._on_pair_chosen)
        module.state_changed.connect(self.refresh)
        module.notice.connect(window.show_notice)
        module.started.connect(self.task.begin)
        module.progress.connect(self.task.update)
        module.progress.connect(window.set_progress)
        module.finished.connect(self.task.end)
        self.refresh()

    def cancel(self) -> None:
        """Stop the run (the status bar's Cancel)."""
        self._module.cancel()

    def open_window(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        self.refresh()
        if self._module.status() in (NOT_RUN, STALE, FAILED):
            self._module.run()

    def refresh(self) -> None:
        """Tell the button, and the window if it is shown, what the module's state is now."""
        status = self._module.status()
        report = self._module.report()
        self._controls.set_available(self._module.has_steps())
        self._controls.set_state(status, None if report is None else report.level, "" if report is None else summary_of(report))
        if not self._window.isVisible():
            return
        choices = self._module.pair_choices()
        self._window.set_pairs(choices, self._module.pair())
        self._window.set_state(
            report=report, running=status == RUNNING, stale=status == STALE, failure=self._module.failure(), empty_text=NOT_RUN_TEXT,
        )

    def _run(self) -> None:
        self._module.run()

    def _on_pair_chosen(self, number: object) -> None:
        self._module.set_pair(number if isinstance(number, int) else None)
        self._module.run()
