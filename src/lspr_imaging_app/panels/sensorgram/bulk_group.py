"""The Sensorgram ribbon's S_bulk button (2026-11-12), in the "Evaluation" group next to the evaluation pairs: which steps make the bulk sensitivity fit.

Screen purpose: put the steps of a refractive index series (water, 10 % EG, 20 % EG ...) into the S_bulk set, see which ones are in it with their refractive indices, and
choose how the fit weights them. The numbers are shown in the ROI / Groups table's S_bulk column (`analysis/bulk_sensitivity.py`).

Layout: one icon button that opens a pop-up (a `QWidgetAction` in a `QMenu`, like the Evaluation button)::

    Step for S_bulk                        (bulk is a subscript; tool tip "Bulk sensitivity")
      1 · H2O          1.33300
      3 · 10perEG       1.34531
      5 · 20perEG       no RI          (a step whose solution has no imported RI: left out of the fit)
    ------------------------------
    Weighting  [1/σ² (weighted) v]
    [+] [-] [clear]                    add the steps selected on the plan bar / take them out / empty the set

The right-click menu of a step does the same ("Use for S_bulk"). Display only: the set lives in `ExperimentPlanModule`; this button reports ``add_requested`` /
``remove_requested`` / ``clear_requested``, and the weighting (remembered, ``sensorgram/bulk_weighting``) as ``weighting_changed``. It greys out when the dataset has no steps.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.bulk_sensitivity import WEIGHT_EQUAL, WEIGHT_INVERSE_VARIANCE
from ...storage.ui_state_keys import SENSORGRAM_BULK_WEIGHTING, read
from ..icon_button_style import style_general_icon_button
from ..ribbon_icon_size import ICON_SIZE
from ..ui_state import UiStateStore
from .plan_icons import bulk_picture

TITLE_HTML = "Step for S<sub>bulk</sub>"
TITLE_TIP = "Bulk sensitivity"
BUTTON_TIP = (
    "S_bulk: the bulk sensitivity, the slope of the step means against the refractive index of each step's solution (nm/RIU). "
    "Click to see the steps in the fit and to add the steps selected on the plan bar. The numbers are in the ROI / Groups table's S_bulk column."
)
NO_STEPS_TIP = "This dataset has no experiment steps. Import the measurement notes (and the pump plan) to get them."
ADD_TIP = "Add the steps selected on the plan bar to the S_bulk set (also: right-click a step > Use for S_bulk)."
ADD_NONE_TIP = "Select steps on the plan bar first (click, Ctrl-click or Shift-click)."
REMOVE_TIP = "Take the steps selected on the plan bar out of the S_bulk set."
CLEAR_TIP = "Empty the S_bulk set (Edit > Undo brings it back)."
WEIGHTING_TIP = (
    "How the steps count in the fit. 1/σ²: a step whose mean is known better (less noise, more cubes) counts more; the statistically best line when the uncertainties are right. "
    "Equal: every step counts the same; useful to compare when one step dominates."
)
EMPTY_TEXT = "No steps yet. Select steps on the plan bar and click +."
PLOT_TIP = (
    "A floating window with the step means against the refractive index: the fitted bulk line with its uncertainty, filled points for the steps in the set and hollow "
    "points for the other steps with a refractive index (how far they sit from the line is the ROI table's measured − predicted)."
)

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1


class BulkControls(QObject):
    add_requested = pyqtSignal()
    remove_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    weighting_changed = pyqtSignal(str)
    plot_toggled = pyqtSignal(bool)  # the floating S_bulk plot (2026-11-12, `bulk_plot.py`) shown / hidden

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._layout_tick = 0
        self._rows: list[QLabel] = []

        self._button = QToolButton(parent)
        self._button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._button)
        self._menu.setToolTipsVisible(True)
        self._panel = QWidget(self._menu)
        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        title = QLabel(TITLE_HTML, self._panel)
        title.setTextFormat(Qt.TextFormat.RichText)  # a real subscript: S<sub>bulk</sub>
        title.setToolTip(TITLE_TIP)
        title.setStyleSheet("font-weight: bold;")
        outer.addWidget(title)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(2)
        outer.addLayout(self._grid)
        self._empty = QLabel(EMPTY_TEXT, self._panel)
        outer.addWidget(self._empty)
        line = QFrame(self._panel)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line)
        weighting_row = QHBoxLayout()
        weighting_row.setContentsMargins(0, 0, 0, 0)
        weighting_row.addWidget(QLabel("Weighting", self._panel))
        self._weighting = QComboBox(self._panel)
        self._weighting.addItem("1/σ² (weighted)", WEIGHT_INVERSE_VARIANCE)
        self._weighting.addItem("Equal", WEIGHT_EQUAL)
        self._weighting.setToolTip(WEIGHTING_TIP)
        weighting_row.addWidget(self._weighting, 1)
        outer.addLayout(weighting_row)
        self.plot_toggle = QCheckBox("Show S_bulk plot", self._panel)  # a check box draws plain text: no subscript here
        self.plot_toggle.setToolTip(PLOT_TIP)
        outer.addWidget(self.plot_toggle)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(2)
        self._add = QToolButton(self._panel)
        self._remove = QToolButton(self._panel)
        self._clear = QToolButton(self._panel)
        for button in (self._add, self._remove, self._clear):
            style_general_icon_button(button)
            buttons.addWidget(button)
        buttons.addStretch(1)
        outer.addLayout(buttons)
        self._action = QWidgetAction(self._menu)
        self._action.setDefaultWidget(self._panel)
        self._menu.addAction(self._action)
        self._button.setMenu(self._menu)

        self._add.clicked.connect(lambda _checked=False: self.add_requested.emit())
        self._remove.clicked.connect(lambda _checked=False: self.remove_requested.emit())
        self._clear.clicked.connect(lambda _checked=False: self.clear_requested.emit())
        self._weighting.currentIndexChanged.connect(lambda _i: self.weighting_changed.emit(self.weighting()))
        self.plot_toggle.toggled.connect(self.plot_toggled.emit)
        self._remove.setToolTip(REMOVE_TIP)
        self._clear.setToolTip(CLEAR_TIP)
        self.set_state((), has_selection=False, selection_in_set=False)
        self.refresh_theme()
        self.set_available(False)

    # -- the group ------------------------------------------------------------------------------------------------------------

    def widgets(self) -> tuple[QWidget, ...]:
        return (self._button,)

    def weighting(self) -> str:
        return self._weighting.currentData() or WEIGHT_INVERSE_VARIANCE

    def set_plot_shown(self, shown: bool) -> None:
        """The window was closed by its own title bar (or opened elsewhere): the check box follows without asking again."""
        self.plot_toggle.blockSignals(True)
        self.plot_toggle.setChecked(shown)
        self.plot_toggle.blockSignals(False)

    def set_available(self, available: bool) -> None:
        self._button.setEnabled(bool(available))
        self._button.setToolTip(BUTTON_TIP if available else NO_STEPS_TIP)

    def set_state(self, rows: Sequence[tuple[str, str]], *, has_selection: bool, selection_in_set: bool) -> None:
        """Show the set: one ``(step text, refractive index text)`` per step in time order; and what the buttons can do with the bar's selection now. Emits nothing."""
        while self._rows:
            label = self._rows.pop()
            self._grid.removeWidget(label)
            label.hide()  # still parented to the panel: not a window of its own
            label.deleteLater()
        for row, (step_text, ri_text) in enumerate(rows):
            for column, text in enumerate((step_text, ri_text)):
                label = QLabel(text, self._panel)
                self._grid.addWidget(label, row, column)
                self._rows.append(label)
                if self._panel.isVisible():
                    label.show()
        self._empty.setVisible(not rows)
        self._add.setEnabled(has_selection)
        self._add.setToolTip(ADD_TIP if has_selection else ADD_NONE_TIP)
        self._remove.setEnabled(selection_in_set)
        self._clear.setEnabled(bool(rows))
        self._panel.adjustSize()
        self._layout_tick += 1
        self._action.setData(self._layout_tick)  # an open QMenu lays itself out again

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style_general_icon_button(self._button)
        self._button.setIcon(QIcon(bulk_picture()(theme.text_primary)))
        for button, name in ((self._add, "plus"), (self._remove, "minus"), (self._clear, "clear-all")):
            style_general_icon_button(button)
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
            button.setIcon(load_tabler_icon(name, color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))

    # -- remembered state -----------------------------------------------------------------------------------------------------

    def restore_ui_state(self, store: UiStateStore) -> None:
        index = self._weighting.findData(read(store, SENSORGRAM_BULK_WEIGHTING))
        if index >= 0:
            self._weighting.setCurrentIndex(index)
        self._weighting.currentIndexChanged.connect(lambda _i: store.set(SENSORGRAM_BULK_WEIGHTING.key, self.weighting()))
        self.weighting_changed.emit(self.weighting())
