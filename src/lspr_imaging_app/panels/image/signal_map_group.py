"""The "Signal map" group of the Image panel's Analysis tab (2026-11-10; module: ``selection/signal_map_module.py``, drawing: ``signal_map_display.py``).

Screen purpose: colour every ROI on the image by how much it changed between two experiment steps, instead of reading the Sensorgram line by line.

Layout (one captioned group, six icons):

    [map]  [gear v]  [refresh]  [legend]  [hover]  [export v]
                         Signal map

* **Map** (colour picker, a toggle): the ROIs take the map's colours instead of their own. Switching it on calculates the numbers that are missing or out of date
  (the ROI / Groups table's step numbers, shared with the Sensorgram: a ROI fitted there costs nothing here).
* **Settings** (gear, a pop-up like the Tuning one): *Show* (shift S − B of an evaluation pair, S/N, or one step's mean), *Pair* (which B / S, set in the Sensorgram's
  Experimental plan tab), *Step* (for "one step's mean"), *Per* (each ROI, or every ROI in its group's colour), *Colours* (the scheme), *Range* (Auto / around zero /
  2-98 % / fixed, with the fixed minimum and maximum).
* **Refresh**: lit (amber) when the map is on and some numbers are missing (grey ROIs) or out of date (faded colours); a press calculates them.
* **Legend** / **Hover**: the colour bar on the image, and the value of the ROI under the cursor as a tooltip. Neither changes the colours.
* **Export**: the shared plot export (``plot_export_button.py``): the Image plot with the coloured ROIs and the legend as PNG / SVG, or the values as CSV.

Every control is remembered except the step (a step belongs to one dataset). The group touches no module: it emits what the user chose, the owner
(``app_rewrite._wire_signal_map``) answers with `set_state`.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QWidget, QWidgetAction,
)

from lspr_ui import get_active_theme, load_tabler_icon

from ...analysis.signal_map import (
    DEFAULT_PER,
    DEFAULT_RANGE,
    DEFAULT_SCHEME,
    DEFAULT_SHOW,
    PERS,
    RANGE_AUTO,
    RANGE_FIXED,
    RANGES,
    SCHEMES,
    SHOW_STEP,
    SHOWS,
    Scheme,
)
from ..step_picker import StepPicker
from ..ribbon_group import labeled_icon_group
from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ON_COLOR = "#38bdf8"  # the ribbon's tool blue
_REFRESH_COLOR = "#f59e0b"  # amber: something to do
MAX_PAIRS = 6
CAPTION = "Signal map"
_MAP_TIP = (
    "Signal map: colour every ROI by how much it changed (Settings: what is shown, which steps, colours). The numbers are the ROI / Groups table's step numbers, "
    "i.e. the Sensorgram's lines as drawn. Switching it on calculates the ones that are missing or out of date."
)
_SETTINGS_TIP = "Signal map settings: what is shown, the steps, each ROI or by group, the colour scheme and the range."
_REFRESH_TIP = "Refresh: calculate the numbers that are missing (grey ROIs) or out of date (faded colours)."
_LEGEND_TIP = "Legend: show the colour bar with its range on the image (also in a saved figure)."
_HOVER_TIP = "Hover values: show the value of the ROI under the cursor."


_SWATCH_SIZE = QSize(110, 14)


def _scheme_swatch(s: Scheme) -> QIcon:
    """A colour scheme drawn as a small horizontal colour bar (the Colours list shows these instead of names)."""
    pixmap = QPixmap(_SWATCH_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    gradient = QLinearGradient(QPointF(0.0, 0.0), QPointF(float(_SWATCH_SIZE.width()), 0.0))
    for position, (r, g, b) in s.stops:
        gradient.setColorAt(position, QColor(int(r), int(g), int(b)))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor(128, 128, 128), 1.0))
    painter.drawRect(QRectF(0.5, 0.5, _SWATCH_SIZE.width() - 1.0, _SWATCH_SIZE.height() - 1.0))
    painter.end()
    return QIcon(pixmap)


def _combo(parent: QWidget, entries: tuple[tuple[str, str, str], ...], default: str) -> QComboBox:
    combo = QComboBox(parent)
    for key, label, tip in entries:
        combo.addItem(label, key)
        combo.setItemData(combo.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
    combo.setCurrentIndex(max(0, combo.findData(default)))
    return combo


class SignalMapGroup(QWidget):
    enabled_toggled = pyqtSignal(bool)
    show_changed = pyqtSignal(str)
    pair_changed = pyqtSignal(int)
    step_changed = pyqtSignal(object)  # a step reference, or None
    per_changed = pyqtSignal(str)
    scheme_changed = pyqtSignal(str)
    range_changed = pyqtSignal(str, float, float)  # mode, fixed low, fixed high
    refresh_requested = pyqtSignal()
    legend_toggled = pyqtSignal(bool)
    hover_toggled = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        self._status = "The signal map is off."
        self._needs_refresh = False
        self._quiet = False  # `set_fixed_bound` is changing several range controls at once
        self._open_menu: QMenu | None = None

        self.map_button = self._icon_button(_MAP_TIP, checkable=True)
        self.settings_button = self._icon_button(_SETTINGS_TIP)
        self.refresh_button = self._icon_button(_REFRESH_TIP)
        self.legend_button = self._icon_button(_LEGEND_TIP, checkable=True)
        self.legend_button.setChecked(True)
        self.hover_button = self._icon_button(_HOVER_TIP, checkable=True)
        self.hover_button.setChecked(True)
        self.export_button: QToolButton | None = None  # the owner's `PlotExportButton` (it needs the Image plot), see `set_export_button`

        self.show_combo = _combo(self, SHOWS, DEFAULT_SHOW)
        self.pair_combo = QComboBox(self)
        for pair in range(1, MAX_PAIRS + 1):
            self.pair_combo.addItem(f"B{pair} / S{pair}", pair)
        self.pair_combo.setToolTip("The evaluation pair: its baseline (B) and signal (S) steps are chosen in the Sensorgram's Experimental plan tab (Evaluation).")
        self.step_picker = StepPicker("Step: not set", self, ribbon=False)
        self.step_picker.setToolTip("The step whose mean is shown (Show: one step's mean).")
        self.per_combo = _combo(self, PERS, DEFAULT_PER)
        self.scheme_combo = QComboBox(self)
        self.scheme_combo.setIconSize(_SWATCH_SIZE)
        for s in SCHEMES:
            self.scheme_combo.addItem(_scheme_swatch(s), "", s.key)  # the colour bar itself, no text; the name is in the tooltip
            kind = "Diverging: white in the middle, for values around zero." if s.diverging else "Sequential, perceptually even."
            self.scheme_combo.setItemData(self.scheme_combo.count() - 1, f"{s.label}\n{kind}", Qt.ItemDataRole.ToolTipRole)
        self.scheme_combo.setCurrentIndex(max(0, self.scheme_combo.findData(DEFAULT_SCHEME)))
        self.range_combo = _combo(self, RANGES, DEFAULT_RANGE)
        self.low_spin = self._spin(-1.0, "Fixed range: the value at the low end of the colours.")
        self.high_spin = self._spin(1.0, "Fixed range: the value at the high end of the colours.")

        self._menu = QMenu(self.settings_button)
        self._menu.setToolTipsVisible(True)
        panel = QWidget(self._menu)
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        fixed = QWidget(panel)
        fixed_layout = QHBoxLayout(fixed)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        fixed_layout.setSpacing(6)
        fixed_layout.addWidget(self.low_spin)
        fixed_layout.addWidget(QLabel("to", fixed))
        fixed_layout.addWidget(self.high_spin)
        for row, (text, control) in enumerate((
            ("Show", self.show_combo), ("Pair", self.pair_combo), ("Step", self.step_picker), ("Per", self.per_combo),
            ("Colours", self.scheme_combo), ("Range", self.range_combo), ("Fixed", fixed),
        )):
            grid.addWidget(QLabel(text, panel), row, 0)
            grid.addWidget(control, row, 1)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.settings_button.setMenu(self._menu)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self._content = QWidget(self)
        self._content_layout = QHBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(2)
        for button in (self.map_button, self.settings_button, self.refresh_button, self.legend_button, self.hover_button):
            self._content_layout.addWidget(button)
        group, _caption = labeled_icon_group(self, self._content, CAPTION)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(group)

        self.map_button.toggled.connect(self.enabled_toggled)
        self.legend_button.toggled.connect(self.legend_toggled)
        self.hover_button.toggled.connect(self.hover_toggled)
        for button in (self.map_button, self.legend_button, self.hover_button):
            button.toggled.connect(lambda _checked: self._refresh())
        self.refresh_button.clicked.connect(lambda _checked=False: self.refresh_requested.emit())
        self.show_combo.currentIndexChanged.connect(lambda _i: self._on_show())
        self.pair_combo.currentIndexChanged.connect(lambda _i: self.pair_changed.emit(self.pair()))
        self.step_picker.step_chosen.connect(self.step_changed)
        self.per_combo.currentIndexChanged.connect(lambda _i: self.per_changed.emit(self.per()))
        self.scheme_combo.currentIndexChanged.connect(lambda _i: self.scheme_changed.emit(self.scheme()))
        self.range_combo.currentIndexChanged.connect(lambda _i: self._on_range())
        self.low_spin.valueChanged.connect(lambda _v: self._on_range())
        self.high_spin.valueChanged.connect(lambda _v: self._on_range())
        self._sync_enabled()
        self._refresh()

    # -- what the controls say ------------------------------------------------------------------------------------------------------

    def show_key(self) -> str:
        return str(self.show_combo.currentData())

    def pair(self) -> int:
        return int(self.pair_combo.currentData())

    def per(self) -> str:
        return str(self.per_combo.currentData())

    def scheme(self) -> str:
        return str(self.scheme_combo.currentData())

    def range_mode(self) -> str:
        return str(self.range_combo.currentData())

    def fixed_range(self) -> tuple[float, float]:
        return float(self.low_spin.value()), float(self.high_spin.value())

    # -- driven by the owner --------------------------------------------------------------------------------------------------------

    def set_export_button(self, button: QToolButton) -> None:
        """The plot export button for the Image plot (made by the owner, which has the plot), placed after the hover toggle."""
        self.export_button = button
        button.setParent(self._content)
        self._content_layout.addWidget(button)

    def set_state(self, status: str, needs_refresh: bool) -> None:
        """What the map shows now (the tooltip of the map button) and whether a Refresh would change it."""
        self._status, self._needs_refresh = status, bool(needs_refresh)
        self._refresh()

    def status_text(self) -> str:
        return self._status

    # -- the legend's menus (2026-11-10): the legend on the image is a shortcut to these same controls, so a choice made there is remembered and shown here --

    def scheme_menu(self, parent: QWidget | None = None) -> QMenu:
        """The colour schemes, each drawn as its colour bar with its name (left click on the legend)."""
        menu = QMenu(parent or self)
        self._add_scheme_entries(menu)
        return menu

    def context_menu(self, parent: QWidget | None = None) -> QMenu:
        """Right click on the legend: auto scale, range, colours, what is shown, and the switches."""
        menu = QMenu(parent or self)
        menu.setToolTipsVisible(True)
        auto = menu.addAction("Auto scale")
        auto.setToolTip("Range: Auto (symmetric around zero when the values have both signs, else 2-98 % of the values).")
        auto.triggered.connect(lambda _c=False: self._choose(self.range_combo, self.range_combo.findData(RANGE_AUTO)))
        self._add_combo_entries(menu.addMenu("Range"), self.range_combo)
        self._add_scheme_entries(menu.addMenu("Colours"))
        menu.addSeparator()
        self._add_combo_entries(menu.addMenu("Show"), self.show_combo)
        self._add_combo_entries(menu.addMenu("Pair"), self.pair_combo)
        self._add_combo_entries(menu.addMenu("Step"), self.step_picker)
        self._add_combo_entries(menu.addMenu("Per"), self.per_combo)
        menu.addSeparator()
        refresh = menu.addAction("Refresh")
        refresh.setEnabled(self.map_button.isChecked() and self._needs_refresh)
        refresh.triggered.connect(lambda _c=False: self.refresh_requested.emit())
        for button, text in ((self.legend_button, "Legend"), (self.hover_button, "Hover values")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(button.isChecked())
            action.triggered.connect(lambda checked, b=button: b.setChecked(checked))
        off = menu.addAction("Turn the signal map off")
        off.triggered.connect(lambda _c=False: self.map_button.setChecked(False))
        return menu

    def bound_editor(self, which: str, low: float, high: float, decimals: int = 3, parent: QWidget | None = None) -> tuple[QMenu, QDoubleSpinBox]:
        """A small pop-up to type the low or high end of the colours (click on a legend number). Enter (or Set) applies it and makes the range Fixed; the other
        end keeps the value shown now. Escape closes it unchanged."""
        menu = QMenu(parent or self)
        box = QWidget(menu)
        row = QHBoxLayout(box)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        row.addWidget(QLabel("Low end" if which == "low" else "High end", box))
        start = low if which == "low" else high
        spin = self._spin(start if math.isfinite(start) else 0.0, "Type a value and press Enter: the range becomes Fixed.")
        spin.setParent(box)
        spin.setDecimals(max(3, int(decimals)))
        row.addWidget(spin)
        set_button = QToolButton(box)
        set_button.setText("Set")
        row.addWidget(set_button)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)

        def apply() -> None:
            self.set_fixed_bound(which, float(spin.value()), low, high)
            menu.close()

        spin.lineEdit().returnPressed.connect(apply)
        set_button.clicked.connect(lambda _c=False: apply())
        menu.aboutToShow.connect(lambda: QTimer.singleShot(0, lambda: (spin.setFocus(), spin.selectAll())))
        return menu, spin

    def set_fixed_bound(self, which: str, value: float, low: float, high: float) -> None:
        """The range becomes Fixed at (`low`, `high`) as shown now, with `which` end ("low" / "high") set to `value`. One `range_changed` at the end."""
        if which == "low":
            low = value
        else:
            high = value
        self._quiet = True  # each control still announces its own change (so it is remembered), but the map hears about it once
        try:
            if math.isfinite(low):
                self.low_spin.setValue(low)
            if math.isfinite(high):
                self.high_spin.setValue(high)
            self.range_combo.setCurrentIndex(self.range_combo.findData(RANGE_FIXED))
        finally:
            self._quiet = False
        self._on_range()

    def popup_scheme_menu(self, where: QPoint) -> None:
        self._popup(self.scheme_menu(), where)

    def popup_context_menu(self, where: QPoint) -> None:
        self._popup(self.context_menu(), where)

    def popup_bound_editor(self, which: str, low: float, high: float, decimals: int, where: QPoint) -> None:
        self._popup(self.bound_editor(which, low, high, decimals)[0], where)

    def refresh_theme(self) -> None:
        for button in (self.map_button, self.settings_button, self.refresh_button, self.legend_button, self.hover_button):
            style_general_icon_button(button)
        if self.export_button is not None and hasattr(self.export_button, "refresh_theme"):
            self.export_button.refresh_theme()
        self.step_picker.refresh_theme()
        self._refresh()

    # -- internals ------------------------------------------------------------------------------------------------------------------

    def _icon_button(self, tooltip: str, *, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setToolTip(tooltip)
        style_general_icon_button(button)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        return button

    def _spin(self, value: float, tip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(-1e6, 1e6)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        spin.setToolTip(tip)
        return spin

    def _icon(self, name: str, color: str) -> QIcon:
        key = (name, color)
        icon = self._icon_cache.get(key)
        if icon is None:
            icon = self._icon_cache[key] = load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        return icon

    def _on_show(self) -> None:
        self._sync_enabled()
        self.show_changed.emit(self.show_key())

    def _popup(self, menu: QMenu, where: QPoint) -> None:
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._open_menu = menu  # kept until the next one: a menu without a reference would be collected while open
        menu.popup(where)

    def _choose(self, combo: QComboBox, index: int) -> None:
        """Pick an entry as if the user had picked it in the drop-down (`activated` is what a StepPicker listens to)."""
        if index < 0:
            return
        combo.setCurrentIndex(index)
        combo.activated.emit(index)

    def _add_combo_entries(self, menu: QMenu, combo: QComboBox) -> None:
        """One check-marked entry per drop-down entry. Greyed when the drop-down is (for example Pair while one step's mean is shown)."""
        menu.setToolTipsVisible(True)
        for i in range(combo.count()):
            action = menu.addAction(combo.itemIcon(i), combo.itemText(i))
            action.setCheckable(True)
            action.setChecked(i == combo.currentIndex())
            tip = combo.itemData(i, Qt.ItemDataRole.ToolTipRole)
            if tip:
                action.setToolTip(str(tip))
            action.triggered.connect(lambda _c=False, i=i: self._choose(combo, i))
        menu.setEnabled(combo.isEnabled() and combo.count() > 0)

    def _add_scheme_entries(self, menu: QMenu) -> None:
        """The colour schemes as buttons showing the colour bar and the name (a menu's own icons are small squares: the bars would be squashed)."""
        combo = self.scheme_combo
        for i in range(combo.count()):
            tip = str(combo.itemData(i, Qt.ItemDataRole.ToolTipRole) or "")
            button = QToolButton(menu)
            button.setIcon(combo.itemIcon(i))
            button.setIconSize(_SWATCH_SIZE)
            button.setText(tip.split("\n")[0])
            button.setToolTip(tip)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setAutoRaise(True)
            button.setCheckable(True)
            button.setChecked(i == combo.currentIndex())
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setStyleSheet("QToolButton { text-align: left; padding: 3px 8px; }")
            button.clicked.connect(lambda _c=False, i=i: (self._choose(combo, i), menu.close()))
            action = QWidgetAction(menu)
            action.setDefaultWidget(button)
            menu.addAction(action)

    def _on_range(self) -> None:
        if self._quiet:
            return
        self._sync_enabled()
        low, high = self.fixed_range()
        self.range_changed.emit(self.range_mode(), low, high)

    def _sync_enabled(self) -> None:
        """Only the controls that mean something for the choices made are live (a greyed control still says what it is in its tooltip)."""
        step_mode = self.show_key() == SHOW_STEP
        self.pair_combo.setEnabled(not step_mode)
        self.step_picker.setEnabled(step_mode)
        fixed = self.range_mode() == RANGE_FIXED
        self.low_spin.setEnabled(fixed)
        self.high_spin.setEnabled(fixed)

    def _refresh(self) -> None:
        theme = get_active_theme()
        on = self.map_button.isChecked()
        self.map_button.setIcon(self._icon("color-picker", _ON_COLOR if on else theme.text_dim))
        self.map_button.setToolTip(f"{_MAP_TIP}\n\n{self._status}" if self._status else _MAP_TIP)
        self.settings_button.setIcon(self._icon("settings", theme.text_dim))
        live = on and self._needs_refresh
        self.refresh_button.setIcon(self._icon("refresh", _REFRESH_COLOR if live else theme.text_dim))
        self.refresh_button.setEnabled(on)
        self.refresh_button.setToolTip(f"{_REFRESH_TIP}\n\n{self._status}" if on else _REFRESH_TIP)
        self.legend_button.setIcon(self._icon("ruler-measure", _ON_COLOR if self.legend_button.isChecked() else theme.text_dim))
        self.hover_button.setIcon(self._icon("pointer", _ON_COLOR if self.hover_button.isChecked() else theme.text_dim))
