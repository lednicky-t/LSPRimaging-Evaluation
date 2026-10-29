"""ROI position tuning's ribbon row: one group, "Position tuning", the last section of the Image panel's ROIs tab (2026-10-29, step D1; design:
``docs/roi_position_tuning_plan_2026-10-28.md``). It was a ribbon tab of its own with five groups; regrouped the same day at the maintainer's request.

Screen purpose: try other centres for the selected ROIs, see which centre is best by a criterion of the user's choice, and keep or drop the answer.

Main user actions: select ROIs, choose the baseline (B1) and signal (S1) steps on the Sensorgram's Experimental plan tab, press Run tuning, look at the crosses
and at the ROIs drawn at their best centres, then accept the new centres. Everything that is set up once, or rarely, is in the settings pop-up.

Layout (one captioned group, four icons):

    [play / stop]  [gear v]  [star]  [check]
                Position tuning

* **Run tuning** (play): scans every lattice position of the selected ROIs over the baseline and the signal step; while it runs the same button is a red Stop (like
  the Analysis tab's). Its tooltip says what would be run ("25 positions x 20 ROIs x 57 cubes ...") or why nothing can be: the button is never greyed out for that
  reason, because a greyed button cannot say why.
* **Settings** (gear, a pop-up panel like the ROI display menu): *Lattice* (a square of size x size centres, pitch px apart, the current centre in the middle; 5 x 5 at
  1 px is the normal choice, a bigger or coarser lattice costs many times the time and is rarely better; disabled while a trial runs), *Rank by* (what the crosses and
  the best centres are judged by; changing it re-ranks at once, nothing is scanned again), *Show crosses* (every tried centre, coloured worst to best; **on by
  default**), *Interpolate* (off by default: place each ROI's best centre between the lattice points, from a quadratic fitted around its best point; changing it
  re-ranks at once; an interpolated centre was never scanned, so Accept cannot save its numbers), and *Result*: clear the trial, export the numbers behind it as a CSV.
* **Best** (star): the ROIs are drawn at their best centres instead of where they are (a view only; the real ROIs do not move).
* **Accept** (check mark; live only for a current result in which a ROI would move): moves the ROIs to their best centres (one step in Edit > Undo) and saves their
  numbers for the two steps to the scan file.

States the row shows: no result yet; running (Run is Stop, lattice disabled); result ready (Best, Clear and Export enabled, Accept when a ROI would move); result out of
date (the ROIs or image settings changed after the trial: shown, the crosses are dimmed by the overlay). Text: what the last trial did is the Run tooltip; a message
that must be seen goes to the status bar. Progress and Cancel are the task indicator's.

Every control is remembered (the owner binds them). The row touches no module and no file: buttons emit requests, the owner (``app_rewrite._wire_roi_tuning``)
answers and tells the row what to show.
"""

from __future__ import annotations

import html

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.roi_tuning_module import IDLE, LATTICE_SIZES, READY, RUNNING, STALE
from ...analysis.roi_tuning_objectives import DEFAULT_OBJECTIVE_KEY, OBJECTIVES
from ..ribbon_group import labeled_icon_group
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_STOP_COLOR = "#ef4444"  # as the Analysis and Export tabs' stop
_BEST_COLOR = "#f59e0b"  # amber: the star of "show the best centres"
_CROSS_COLOR = "#38bdf8"  # the ribbon's tool blue
PITCH_RANGE_PX = (0.25, 10.0)
DEFAULT_SIZE, DEFAULT_PITCH_PX = 5, 1.0
CAPTION = "Position tuning"
_RUN_TIP = (
    "Run tuning: scan every lattice centre of the selected ROIs over the baseline step (B1) and the signal step (S1) of the experimental plan, and rank the centres."
)
_STOP_TIP = "Stop the trial. Nothing is kept from a stopped trial."
_SETTINGS_TIP = "Tuning settings: the lattice, what to rank by, whether to show the crosses, and clearing or exporting the result."
_CROSSES_TIP = "Show crosses: mark every centre that was tried, coloured from the worst (dark) to the best (bright) for the chosen criterion."
_BEST_TIP = (
    "Best: draw the ROIs at their best centres instead of where they are. This is only a view: the real ROIs do not move."
)
_ACCEPT_TIP = (
    "Accept: move the ROIs to their best centres (one step in Edit > Undo) and save their numbers for the baseline and signal steps to the scan file, so those "
    "cubes are not scanned again. The other cubes are out of date until the next Fast scan."
)
_INTERPOLATE_TIP = (
    "Interpolate: place each ROI's best centre between the lattice points, from a quadratic fitted to the 3 x 3 points around its best one, instead of on a lattice "
    "point. Only inside that 3 x 3 window and inside the lattice; a ROI whose surface has no top (a ramp: the peak is probably beyond the lattice) stays on its "
    "lattice point. An interpolated centre was never scanned, so Accept moves the ROI there but does not save its numbers: the next scan computes it."
)
_CLEAR_TIP = "Clear the trial: forget the numbers and remove the crosses."
_EXPORT_TIP = "Export the numbers behind the crosses (every ROI and centre) as a CSV table."


class TuningTab(QWidget):
    run_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    lattice_changed = pyqtSignal(int, float)  # size, pitch in px
    objective_changed = pyqtSignal(str)  # key of the "Rank by" entry
    crosses_toggled = pyqtSignal(bool)
    interpolate_toggled = pyqtSignal(bool)
    best_toggled = pyqtSignal(bool)
    accept_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    export_requested = pyqtSignal()
    status_message = pyqtSignal(str)  # a message to show on the status bar (see `set_status`)
    info_changed = pyqtSignal()  # the text changed (the Run tooltip is already current; kept for an owner that wants to show it elsewhere)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = IDLE
        self._can_accept = False  # the owner says: a current result in which at least one ROI would move
        self._hint = ""
        self._status = "No trial yet."
        self._icon_cache: dict[tuple[str, str], QIcon] = {}

        self.run_button = self._icon_button()
        self.settings_button = self._icon_button(_SETTINGS_TIP)
        self.best_button = self._icon_button(_BEST_TIP, checkable=True)
        self.accept_button = self._icon_button(_ACCEPT_TIP)

        # The controls of the settings pop-up. They are the row's attributes (the owner binds and reads them) and live in the pop-up panel.
        self.size_combo = QComboBox(self)
        for size in LATTICE_SIZES:
            self.size_combo.addItem(f"{size} x {size}", size)
        self.size_combo.setCurrentIndex(self.size_combo.findData(DEFAULT_SIZE))
        self.size_combo.setToolTip("Lattice size: how many centres are tried along each side (the current centre is the middle one).")
        self.pitch_spin = QDoubleSpinBox(self)
        self.pitch_spin.setRange(*PITCH_RANGE_PX)
        self.pitch_spin.setSingleStep(0.25)
        self.pitch_spin.setDecimals(2)
        self.pitch_spin.setSuffix(" px")
        self.pitch_spin.setValue(DEFAULT_PITCH_PX)
        self.pitch_spin.setToolTip("Pitch: the distance between neighbouring centres, in image pixels.")
        self.objective_combo = QComboBox(self)
        for objective in OBJECTIVES:
            self.objective_combo.addItem(objective.label, objective.key)
            self.objective_combo.setItemData(self.objective_combo.count() - 1, objective.tooltip, Qt.ItemDataRole.ToolTipRole)
        self.objective_combo.setCurrentIndex(self.objective_combo.findData(DEFAULT_OBJECTIVE_KEY))
        self.crosses_button = self._icon_button(_CROSSES_TIP, checkable=True)
        self.crosses_button.setChecked(True)  # on by default
        self.interpolate_button = self._icon_button(_INTERPOLATE_TIP, checkable=True)  # off by default: the measured position is the safe one
        self.clear_button = self._icon_button(_CLEAR_TIP)
        self.export_button = self._icon_button(_EXPORT_TIP)

        self._menu = QMenu(self.settings_button)
        self._menu.setToolTipsVisible(True)
        panel = QWidget(self._menu)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        lattice = self._row_widget(panel, self.size_combo, self.pitch_spin)
        result = self._row_widget(panel, self.clear_button, self.export_button)
        for row, (text, control) in enumerate((
            ("Lattice", lattice), ("Rank by", self.objective_combo), ("Show crosses", self.crosses_button), ("Interpolate", self.interpolate_button),
            ("Result", result),
        )):
            grid.addWidget(QLabel(text, panel), row, 0)
            grid.addWidget(control, row, 1)  # reparents the control into the pop-up panel
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.settings_button.setMenu(self._menu)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        content = QWidget(self)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(2)
        for button in (self.run_button, self.settings_button, self.best_button, self.accept_button):
            content_layout.addWidget(button)
        group, _caption = labeled_icon_group(self, content, CAPTION)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(group)
        layout.addStretch(1)

        self.run_button.clicked.connect(lambda _checked=False: self._run_clicked())
        self.size_combo.currentIndexChanged.connect(lambda _index: self._lattice_edited())
        self.pitch_spin.valueChanged.connect(lambda _value: self._lattice_edited())
        self.objective_combo.currentIndexChanged.connect(lambda _index: self._objective_edited())
        self.crosses_button.toggled.connect(self.crosses_toggled)
        self.best_button.toggled.connect(self.best_toggled)
        self.interpolate_button.toggled.connect(self.interpolate_toggled)
        self.crosses_button.toggled.connect(lambda _checked: self._refresh())  # the icon's colour says on / off
        self.best_button.toggled.connect(lambda _checked: self._refresh())
        self.interpolate_button.toggled.connect(lambda _checked: self._refresh())
        self.accept_button.clicked.connect(lambda _checked=False: self.accept_requested.emit())
        self.clear_button.clicked.connect(self._menu.close)  # a pop-up that stays over the image after its button was used is in the way
        self.clear_button.clicked.connect(lambda _checked=False: self.clear_requested.emit())
        self.export_button.clicked.connect(self._menu.close)  # the file dialog follows
        self.export_button.clicked.connect(lambda _checked=False: self.export_requested.emit())
        self._refresh()

    @staticmethod
    def _row_widget(parent: QWidget, *widgets: QWidget) -> QWidget:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        return row

    # -- what the controls say ------------------------------------------------------------------------------------------------

    def lattice_size(self) -> int:
        return int(self.size_combo.currentData())

    def pitch_px(self) -> float:
        return float(self.pitch_spin.value())

    def objective_key(self) -> str:
        return str(self.objective_combo.currentData())

    # -- driven by the owner --------------------------------------------------------------------------------------------------

    def set_state(self, state: str, hint: str, can_accept: bool = False) -> None:
        """``state``: the module's ``idle`` / ``running`` / ``ready`` / ``stale``; ``hint``: what a press of Run tuning would do (or why it cannot);
        ``can_accept``: a current result in which at least one ROI would move (Accept is live only then)."""
        self._state, self._hint, self._can_accept = state, hint, bool(can_accept)
        self._refresh()
        self.info_changed.emit()

    def state(self) -> str:
        return self._state

    def is_running(self) -> bool:
        return self._state == RUNNING

    def set_status(self, text: str, *, announce: bool = False) -> None:
        """What the last trial did. ``announce``: also show it on the status bar (a refusal or an outcome)."""
        self._status = text
        self._refresh()
        self.info_changed.emit()
        if announce and text:
            self.status_message.emit(text)

    def status_text(self) -> str:
        return self._status

    def info_html(self) -> str:
        return (
            "<b>Position tuning</b><br>Choose a baseline step (B1) and a signal step (S1) on the Sensorgram's Experimental plan tab, select the ROIs, and press Run "
            "tuning (play): every ROI is moved over a lattice of centres and the Sensorgram is computed at each. In the settings (gear): the lattice, <i>Rank by</i> "
            "(what counts as best; changing it never scans again), the crosses that mark the tried centres, Interpolate (best centre between the lattice points), and "
            "Clear / Export. The star draws the ROIs at their best centres (a view only). Nothing moves until you press Accept (the check mark): then the ROIs move to their best centres (one step in Edit > Undo) and "
            "their numbers for the two steps are saved to the scan file."
            f"<br><br><b>Now</b><br>{html.escape(self._hint)}"
            f"<br><br>{html.escape(self._status).replace(chr(10), '<br>')}"
        )

    def refresh_theme(self) -> None:
        for button in (
            self.run_button, self.settings_button, self.best_button, self.accept_button, self.crosses_button, self.interpolate_button, self.clear_button,
            self.export_button,
        ):
            style_general_icon_button(button)
        self._refresh()

    # -- internals ------------------------------------------------------------------------------------------------------------

    def _icon_button(self, tooltip: str = "", *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        style_general_icon_button(button)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        return button

    def _icon(self, name: str, color: str) -> QIcon:
        key = (name, color)
        icon = self._icon_cache.get(key)
        if icon is None:
            icon = self._icon_cache[key] = load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        return icon

    def _run_clicked(self) -> None:
        if self._state == RUNNING:  # the button is the Stop while a trial runs
            self.stop_requested.emit()
        else:
            self.run_requested.emit()

    def _lattice_edited(self) -> None:
        self.lattice_changed.emit(self.lattice_size(), self.pitch_px())

    def _objective_edited(self) -> None:
        self.objective_changed.emit(self.objective_key())

    def _refresh(self) -> None:
        theme = get_active_theme()
        running = self._state == RUNNING
        has_result = self._state in (READY, STALE)
        if running:
            self.run_button.setIcon(self._icon("player-stop", _STOP_COLOR))
            self.run_button.setToolTip(_STOP_TIP)
        else:
            self.run_button.setIcon(self._icon("player-play", theme.accent_green))
            self.run_button.setToolTip("\n\n".join(part for part in (_RUN_TIP, self._hint, self._status) if part))
        self.settings_button.setIcon(self._icon("settings", theme.text_dim))  # always enabled: its pop-up holds the controls
        self.size_combo.setEnabled(not running)
        self.pitch_spin.setEnabled(not running)
        self.crosses_button.setIcon(self._icon("crosshair", _CROSS_COLOR if self.crosses_button.isChecked() else theme.text_dim))
        self.interpolate_button.setIcon(self._icon("math-function", _CROSS_COLOR if self.interpolate_button.isChecked() else theme.text_dim))
        self.best_button.setIcon(self._icon("star", _BEST_COLOR if self.best_button.isChecked() else theme.text_dim))
        self.best_button.setEnabled(has_result)
        self.accept_button.setIcon(self._icon("checkbox", theme.accent_green if self._can_accept else theme.text_dim))
        self.accept_button.setEnabled(self._can_accept and self._state == READY)
        self.clear_button.setIcon(self._icon("trash", theme.text_dim))
        self.clear_button.setEnabled(has_result)
        self.export_button.setIcon(self._icon("file-export", theme.text_dim))
        self.export_button.setEnabled(has_result)
