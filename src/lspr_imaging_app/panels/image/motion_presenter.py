"""``MotionPresenter``: connects the image-motion module, the Analysis tab's "Motion" button and the report window (2026-11-07; reworked 2026-11-10).

The same arrangement as the Sensorgram's `checks_presenter.py`: the module (`selection/image_motion_module.py`) measures; the tab (`analysis_tab.py`) has the button; the window
(`panels/check_report_window.py`, compact) shows a report. This object carries what one tells the others and owns the `PanelTask` that puts a long measurement in the status bar.
The app builds one (`app_rewrite._wire_image_motion`).

**A click on Motion** starts the measurement in the background at the wavelength of the image on screen (`wavelength_now`, the Selection's current wavelength, read at the click;
no box any more). Nothing opens. While it runs the button is a Stop and the status bar shows the progress.

**The result** is one sentence on the status bar (`CheckReport.summary`). Only when a step moves over the limits does the compact report window open, with the steps that move and
the table of numbers. A refusal (no dataset, no steps), a failure and a stop are also written on the status bar, never in a window.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject

from ...selection.image_motion_module import ImageMotionModule
from ..check_report_window import CheckReportWindow
from ..panel_task import PanelTask
from .analysis_tab import AnalysisTab

TASK_ID = "checks.motion"
NOT_RUN_TEXT = (
    "Not measured yet. The image motion is the shift of the image between the cubes of each experiment step, measured on one raw plane per cube. "
    "Press Motion in the Analysis tab: it measures the plane of the image on screen."
)


class MotionPresenter(QObject):
    def __init__(
        self, module: ImageMotionModule, tab: AnalysisTab, window: CheckReportWindow, wavelength_now: Callable[[], float], parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._module = module
        self._tab = tab
        self._window = window
        self._wavelength_now = wavelength_now
        self.task = PanelTask(TASK_ID, "Image motion", self)
        tab.motion_requested.connect(self._on_requested)
        tab.motion_stop_requested.connect(module.cancel)
        window.cancel_requested.connect(module.cancel)
        module.state_changed.connect(self.refresh)
        module.notice.connect(tab.announce)
        module.started.connect(self.task.begin)
        module.progress.connect(self.task.update)
        module.progress.connect(window.set_progress)
        module.finished.connect(self.task.end)
        module.finished.connect(self._on_finished)
        self.refresh()

    def cancel(self) -> None:
        """Stop the measurement (the status bar's Cancel)."""
        self._module.cancel()

    def _on_requested(self) -> None:
        self._module.run(float(self._wavelength_now()))

    def _on_finished(self, outcome: str, message: str) -> None:
        if outcome == "failed":
            self._tab.announce(f"Image motion failed: {message}")
        elif outcome == "cancelled":
            self._tab.announce("Image motion: stopped.")
        else:
            report = self._module.report()
            if report is None:
                return
            self._tab.announce(report.summary)
            if report.warnings:  # something moves: the numbers are worth a window
                self._open()

    def _open(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        self.refresh()

    def refresh(self) -> None:
        self._tab.set_motion_running(self._module.is_running())
        if not self._window.isVisible():
            return
        self._window.set_state(
            report=self._module.report(), running=self._module.is_running(), stale=False, failure=self._module.failure(), empty_text=NOT_RUN_TEXT,
        )
