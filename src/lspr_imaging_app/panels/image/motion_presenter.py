"""``MotionPresenter``: connects the image-motion module, the Analysis tab's "Motion" group and the report window (2026-11-07).

The same arrangement as the Sensorgram's `checks_presenter.py`: the module (`selection/image_motion_module.py`) measures; the tab (`analysis_tab.py`) has a button and a wavelength
box; the window (`panels/check_report_window.py`) shows the report. This object carries what one tells the others and owns the `PanelTask` that puts a long measurement in the
status bar. The app builds one (`app_rewrite._wire_image_motion`).

**A click on Motion** starts the measurement at the wavelength in the box and opens the report window, which shows progress and then the report. While it runs the button is a Stop.
**Run again** in the window measures again at the box's wavelength. A refusal (no dataset, no steps) is written in the window.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject

from ...selection.image_motion_module import ImageMotionModule
from ..check_report_window import CheckReportWindow
from ..panel_task import PanelTask
from .analysis_tab import AnalysisTab

TASK_ID = "checks.motion"
NOT_RUN_TEXT = (
    "Not measured yet. The image motion is the shift of the image between the cubes of each experiment step, measured on one raw plane per cube. "
    "Set the wavelength in the Analysis tab's Motion box and press Run."
)


class MotionPresenter(QObject):
    def __init__(self, module: ImageMotionModule, tab: AnalysisTab, window: CheckReportWindow, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._module = module
        self._tab = tab
        self._window = window
        self.task = PanelTask(TASK_ID, "Image motion", self)
        tab.motion_requested.connect(self._on_requested)
        tab.motion_stop_requested.connect(module.cancel)
        window.run_requested.connect(self._run_again)
        window.cancel_requested.connect(module.cancel)
        module.state_changed.connect(self.refresh)
        module.notice.connect(self._on_notice)
        module.started.connect(self.task.begin)
        module.progress.connect(self.task.update)
        module.progress.connect(window.set_progress)
        module.finished.connect(self.task.end)
        self.refresh()

    def cancel(self) -> None:
        """Stop the measurement (the status bar's Cancel)."""
        self._module.cancel()

    def _on_requested(self, wavelength_nm: float) -> None:
        self._open()
        self._module.run(wavelength_nm)

    def _run_again(self) -> None:
        self._module.run(float(self._tab.motion_wavelength.value()))

    def _on_notice(self, text: str) -> None:
        self._open()
        self._window.show_notice(text)

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
