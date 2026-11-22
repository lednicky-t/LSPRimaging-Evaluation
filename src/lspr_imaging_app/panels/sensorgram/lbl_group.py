"""The Sensorgram ribbon's LbL button (2026-11-22), in the "Evaluation" group after S_bulk: which steps make a layer-by-layer series.

Screen purpose: a layer-by-layer (LbL) deposition repeats one cycle (water - PAH - water - PSS - water ...). Name the starting step and the number of steps in a cycle once;
the series then takes the start step and every n-th step after it (steps 1, 7, 13 ... for start 1, 6 per cycle), numbers them 0, 1, 2 ... on the plan bar, and the LbL plot shows
the shift against that number (`lbl_plot.py`, `analysis/lbl_series.py`).

Layout: one icon button that opens a pop-up (a `QWidgetAction` in a `QMenu`, like the S_bulk button)::

    LbL series  (layer by layer)
    Start step      1 · H2O                 (or "not set")
    [Use selected step as start]            the one step selected on the plan bar
    Steps per cycle [ 6 ]
    Uses steps 1, 7, 13, 19  (cycles 0 to 3)
    ------------------------------
    [ ] Show LbL plot
    [clear]

Display only: the series lives in `ExperimentPlanModule`; this button reports ``start_requested(period)`` (the Use button), ``period_changed`` (the spin box, by the user only: a
value set from the session does not signal), ``clear_requested`` and ``plot_toggled``. The spin box's value is remembered as the default for the next series
(``sensorgram/lbl_period``); the series itself is saved in the session. It greys out when the dataset has no steps.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ..icon_button_style import style_general_icon_button
from ..ribbon_icon_size import ICON_SIZE
from ..ui_state import UiStateStore
from .plan_icons import lbl_picture

PERIOD_KEY = "sensorgram/lbl_period"
DEFAULT_PERIOD = 6  # the maintainer's own cycle: water, PAH, water, PSS, water, ... (steps per bilayer); only the first value of the box
BUTTON_TIP = (
    "LbL: a layer-by-layer series. Name the starting step and the steps in one cycle; the steps 0, 1, 2 ... are marked on the plan bar and the LbL plot shows their shift "
    "against the cycle number."
)
NO_STEPS_TIP = "This dataset has no experiment steps. Import the measurement notes (and the pump plan) to get them."
USE_TIP = "Make the step selected on the plan bar the start of the series (cycle 0: usually the rinse before the first layer)."
USE_NONE_TIP = "Select one step on the plan bar first (click it)."
PERIOD_TIP = "How many steps one cycle has, from one rinse to the next (e.g. water, PAH, water, PSS, water, water = 6). The next cycle's step is this many steps further on."
CLEAR_TIP = "Remove the LbL series (Edit > Undo brings it back)."
PLOT_TIP = "A floating window with the shift of each line against the cycle number: accumulated from the start step, or per cycle."
NOT_SET_TEXT = "not set"
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1


class LblControls(QObject):
    start_requested = pyqtSignal(int)  # the steps per cycle in the box now
    period_changed = pyqtSignal(int)
    clear_requested = pyqtSignal()
    plot_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._layout_tick = 0
        self._button = QToolButton(parent)
        self._button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._button)
        self._menu.setToolTipsVisible(True)
        self._panel = QWidget(self._menu)
        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        title = QLabel("LbL series (layer by layer)", self._panel)
        title.setStyleSheet("font-weight: bold;")
        outer.addWidget(title)
        start_row = QHBoxLayout()
        start_row.setContentsMargins(0, 0, 0, 0)
        start_row.addWidget(QLabel("Start step", self._panel))
        self._start = QLabel(NOT_SET_TEXT, self._panel)
        start_row.addWidget(self._start, 1)
        outer.addLayout(start_row)
        self._use = QPushButton("Use selected step as start", self._panel)
        outer.addWidget(self._use)
        period_row = QHBoxLayout()
        period_row.setContentsMargins(0, 0, 0, 0)
        period_row.addWidget(QLabel("Steps per cycle", self._panel))
        self._period = QSpinBox(self._panel)
        self._period.setRange(1, 999)
        self._period.setValue(DEFAULT_PERIOD)
        self._period.setToolTip(PERIOD_TIP)
        period_row.addWidget(self._period)
        period_row.addStretch(1)
        outer.addLayout(period_row)
        self._uses = QLabel("", self._panel)
        self._uses.setWordWrap(True)
        self._uses.setMaximumWidth(300)
        outer.addWidget(self._uses)
        line = QFrame(self._panel)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line)
        self.plot_toggle = QCheckBox("Show LbL plot", self._panel)
        self.plot_toggle.setToolTip(PLOT_TIP)
        outer.addWidget(self.plot_toggle)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self._clear = QToolButton(self._panel)
        self._clear.setToolTip(CLEAR_TIP)
        style_general_icon_button(self._clear)
        buttons.addWidget(self._clear)
        buttons.addStretch(1)
        outer.addLayout(buttons)
        self._action = QWidgetAction(self._menu)
        self._action.setDefaultWidget(self._panel)
        self._menu.addAction(self._action)
        self._button.setMenu(self._menu)

        self._use.clicked.connect(lambda _checked=False: self.start_requested.emit(self._period.value()))
        self._clear.clicked.connect(lambda _checked=False: self.clear_requested.emit())
        self.plot_toggle.toggled.connect(self.plot_toggled.emit)
        self.set_state(None, "", has_single_selection=False)
        self.refresh_theme()
        self.set_available(False)

    # -- the group ------------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._button,)

    def period(self) -> int:
        return self._period.value()

    def set_plot_shown(self, shown: bool) -> None:
        """The window was closed by its own title bar: the check box follows without asking again."""
        self.plot_toggle.blockSignals(True)
        self.plot_toggle.setChecked(shown)
        self.plot_toggle.blockSignals(False)

    def set_available(self, available: bool) -> None:
        self._button.setEnabled(bool(available))
        self._button.setToolTip(BUTTON_TIP if available else NO_STEPS_TIP)

    def set_state(self, start_text: str | None, uses_text: str, *, has_single_selection: bool, period: int | None = None) -> None:
        """Show the series: the start step's text (None: not set), which steps it uses, and what the Use button can do with the bar's selection now. `period`: the series' own
        steps per cycle, shown in the box without signalling. Emits nothing."""
        self._start.setText(start_text if start_text else NOT_SET_TEXT)
        self._uses.setText(uses_text)
        self._uses.setVisible(bool(uses_text))
        if period is not None and period != self._period.value():
            self._period.blockSignals(True)
            self._period.setValue(period)
            self._period.blockSignals(False)
        self._use.setEnabled(has_single_selection)
        self._use.setToolTip(USE_TIP if has_single_selection else USE_NONE_TIP)
        self._clear.setEnabled(start_text is not None)
        self._panel.adjustSize()
        self._layout_tick += 1
        self._action.setData(self._layout_tick)  # an open QMenu lays itself out again

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style_general_icon_button(self._button)
        self._button.setIcon(QIcon(lbl_picture()(theme.text_primary)))
        style_general_icon_button(self._clear)
        self._clear.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._clear.setIcon(load_tabler_icon("clear-all", color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))

    # -- remembered state -----------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        """The box's last value is the default for the next series. Wired after the restore, so a remembered value never edits a series (``period_changed`` is the user's)."""
        saved = store.get(PERIOD_KEY)
        if isinstance(saved, int) and not isinstance(saved, bool) and self._period.minimum() <= saved <= self._period.maximum():
            self._period.setValue(saved)
        self._period.valueChanged.connect(lambda value: store.set(PERIOD_KEY, int(value)))
        self._period.valueChanged.connect(self.period_changed.emit)
