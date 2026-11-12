"""File -> Dataset Manager... (2026-11-11): the dataset's details in one non-modal window.

Replaces the Workflow panel (retired 2026-11-11, maintainer decision: a dataset is not looked at often enough to
keep a dock open for it). The actions moved to the File menu (`panels/file_menu.py`); what is left to *look at* or
*manage* is here, one tab per subject:

- **Overview**: folder, type, size, counts, OME-Zarr details (`summary.py`).
- **Experiment plan**: the acquisition metadata and pump-plan steps, with Import / Export (`experimental_plan.py`), and
  the refractive indices of the steps' solutions (`refractive_indices.py`, 2026-11-12). An editable step manager is
  meant to grow here.
- **Sessions**: list, open, new (`sessions.py`).

Non-modal (`show()`, not `exec()`): the app keeps working while it is open. Closing only hides it; the widgets stay,
so they keep following the dataset's signals. It is built with the main window, before any dataset opens, so it hears
the first `dataset_loaded` too. The size and the tab shown are remembered (`ui_state_keys.DATASET_MANAGER_*`).
"""

from __future__ import annotations

from PyQt6.QtGui import QHideEvent
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from ...dataset import DatasetModule
from ...selection import CubeDisplayModule, SelectionModule
from ...selection.experiment_plan_module import ExperimentPlanModule
from ...storage.session_coordinator import SessionCoordinator
from ...storage.ui_state_keys import DATASET_MANAGER_PAGE, DATASET_MANAGER_SIZE
from ..ui_state import UiStateStore
from .experimental_plan import ExperimentalPlanSection
from .refractive_indices import RefractiveIndexView
from .sessions import SessionsPage
from .summary import DatasetSummarySection

PAGE_OVERVIEW = "overview"
PAGE_PLAN = "plan"
PAGE_SESSIONS = "sessions"
_PAGES = (PAGE_OVERVIEW, PAGE_PLAN, PAGE_SESSIONS)

DEFAULT_SIZE = (560, 460)
_MIN_SIZE = (380, 280)


def _scrolled(content: QWidget, parent: QWidget) -> QScrollArea:
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll


class DatasetManagerDialog(QDialog):
    def __init__(
        self,
        dataset: DatasetModule,
        selection: SelectionModule,
        session_coordinator: SessionCoordinator,
        *,
        cube_display: CubeDisplayModule | None = None,
        ui_state: UiStateStore | None = None,
        parent: QWidget | None = None,
        experiment_plan: ExperimentPlanModule | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = ui_state
        self.setWindowTitle("Dataset Manager")
        self.setModal(False)
        self.setMinimumSize(*_MIN_SIZE)
        self.resize(*self._saved_size())

        self.tabs = QTabWidget(self)
        self.summary = DatasetSummarySection(dataset, self)
        self.plan = ExperimentalPlanSection(dataset, selection, self, cube_display=cube_display)
        self.sessions = SessionsPage(session_coordinator, self)
        # The refractive indices of the steps' solutions sit under the plan (2026-11-12; None in a test without the plan module).
        self.refractive = None if experiment_plan is None else RefractiveIndexView(experiment_plan, self)
        plan_page = QWidget(self)
        plan_layout = QVBoxLayout(plan_page)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.addWidget(self.plan)
        if self.refractive is not None:
            plan_layout.addWidget(self.refractive)
        plan_layout.addStretch(1)
        self.tabs.addTab(_scrolled(self.summary, self.tabs), "Overview")
        self.tabs.addTab(_scrolled(plan_page, self.tabs), "Experiment plan")
        self.tabs.addTab(self.sessions, "Sessions")
        self.show_page(self._saved_page())
        self.tabs.currentChanged.connect(self._remember_page)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.hide)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(buttons)

    def present(self, page: str | None = None) -> None:
        """Show the window (or bring it forward when it is already open), optionally on one page."""
        if page is not None:
            self.show_page(page)
        self.show()
        self.raise_()
        self.activateWindow()

    def show_page(self, page: str) -> None:
        if page in _PAGES:
            self.tabs.setCurrentIndex(_PAGES.index(page))

    def current_page(self) -> str:
        return _PAGES[self.tabs.currentIndex()]

    def _remember_page(self, _index: int) -> None:
        if self._store is not None:
            self._store.set(DATASET_MANAGER_PAGE, self.current_page())

    def _saved_page(self) -> str:
        saved = self._store.get(DATASET_MANAGER_PAGE) if self._store is not None else None
        return saved if saved in _PAGES else PAGE_OVERVIEW

    def _saved_size(self) -> tuple[int, int]:
        saved = self._store.get(DATASET_MANAGER_SIZE) if self._store is not None else None
        if (
            isinstance(saved, list) and len(saved) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) for value in saved)
            and saved[0] >= _MIN_SIZE[0] and saved[1] >= _MIN_SIZE[1]
        ):
            return saved[0], saved[1]
        return DEFAULT_SIZE

    def hideEvent(self, event: QHideEvent | None) -> None:  # Qt override
        if self._store is not None:
            self._store.set(DATASET_MANAGER_SIZE, [self.width(), self.height()])
        super().hideEvent(event)
