"""The Array pop-out's content: every setting of a manual array, each with its pictogram (and its name), plus Apply / Cancel.

Screen purpose: set up a regular array from the selected ROI(s) while the canvas shows it live.

Layout (a panel inside the app window; `array.py` owns it, the preview and Apply), one row each:

    [type] [cell]                                         (preview opacity wedge, starts in the middle)
    Start point  (nine dots)    Numbering  (reading path over nine dots; double-click the picked corner: rows <-> columns)
    (o) Columns   [ 2 ]    (o) Rows    [ 2 ]       columns = x, so they stand over pitch x;
    (o) Pitch x   [ 50 px] (o) Pitch y [ 50 px]    rows = y, over pitch y
    Advanced v    (free-standing text, rolls down: increment x / y, angle, row offset)
    status line (what is wrong / what will be made)                 (cross = cancel) (tick = apply)

The lattice type is ONE icon: it shows the current type and a click switches to the other. Next to it the regular-cell switch is one icon too, showing the cell
you get (rectangular: square on / rectangle off; triangular: equilateral on / lopsided off). Neither has a button around it. Display only: this widget holds settings and reports them (`spec()` -> `roi.array_layout.ArraySpec`); it knows nothing of ROIs. Lengths are pixels
(`LengthField` shows px or um, the app-wide unit). **Regular cell** is one switch: while it is on, pitch y, increment y and the row offset follow x
(the rule is `ArraySpec.resolved`) and their fields are locked, showing the values in force.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import CompactWedgeSlider, get_active_theme, load_tabler_icon

from ...image_tools.geometry.module import GeometryModule
from ...roi.array_layout import LATTICE_KINDS, ArraySpec, lattice_kind
from .length_field import LengthField
from .array_pictograms import FlatIconButton, Pictogram, pictogram_icon
from .array_picker import CornerPicker, NumberingPicker
from .general_group import ICON_SIZE

OPACITY_DEFAULT = 0.5
"""The wedge starts in the middle: left of it the preview fades, right of it the spots get more solid (`array_ghosts.preview_alphas`)."""
FACTOR_MIN, FACTOR_MAX = 0.1, 10.0
"""Range of a pitch increment (a factor; 1 = constant pitch)."""
COUNT_WIDTH, NUMBER_WIDTH, LENGTH_WIDTH = 56, 84, 106
"""Fixed widths of the fields (px): a count, a plain number, a length with its px / um switch. No field needs to be wider."""
MAX_COUNT = 200
"""Rows and columns are limited to this each (40,000 nodes): the preview is drawn on the GUI thread."""

_TIPS = {
    "rows": "Rows: how many rows the array has (down the image).",
    "cols": "Columns: how many columns the array has (across the image).",
    "pitch_x": "Pitch x: centre-to-centre distance between neighbours along a row.",
    "pitch_y": "Pitch y: centre-to-centre distance between neighbours down a column.",
    "step_x": "Pitch increment x: every further step away from the selected ROI(s), along a row, has this many times the previous pitch. 1 = constant pitch; above 1 the gaps widen, below 1 they narrow.",
    "step_y": "Pitch increment y: every further step away from the selected ROI(s), down a column, has this many times the previous pitch. 1 = constant pitch.",
    "angle": "Angle: turns the whole array about the selected ROI(s), degrees, clockwise on the screen.",
    "row_offset": "Row offset: every second row is shifted along x by this fraction of the pitch x, away from the start point. Triangular: 0.5.",
}
_REGULAR_TIP = (
    "Regular cell (click to switch): one switch for the whole cell shape, driven by x. Rectangular: a square cell (pitch y and increment y equal x). "
    "Triangular: all triangle angles 60° (pitch y = x · √3/2, row offset 0.5). While on, those fields show the values in force and cannot be edited."
)


class ArrayPanel(QWidget):
    spec_changed = pyqtSignal()
    advanced_toggled = pyqtSignal(bool)
    opacity_changed = pyqtSignal(float)  # 0 - 1 (0.5 = the usual look): how strongly the preview of the new ROIs is drawn on the image
    apply_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, geometry: GeometryModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ArrayPanel")
        self._ready = False  # the controls are being built: their first values are not edits
        self._pictograms: list[Pictogram] = []
        self._kind = LATTICE_KINDS[0].key

        # -- type: one icon that switches, and the regular-cell icon beside it ----------------------------------------------
        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        self.kind_button = FlatIconButton("", self)
        self.kind_button.clicked.connect(lambda _checked=False: self.set_kind(self._next_kind(), emit=True))
        type_row.addWidget(self.kind_button)
        self.regular = FlatIconButton(_REGULAR_TIP, self)
        self.regular.setCheckable(True)
        self.regular.toggled.connect(self._on_edited)
        type_row.addWidget(self.regular)
        type_row.addStretch(1)
        self.opacity_slider = CompactWedgeSlider(parent=self)  # the same wedge the other views use for opacity
        self.opacity_slider.setFixedWidth(36)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(round(OPACITY_DEFAULT * 100))
        self.opacity_slider.setToolTip("Preview opacity: starts in the middle. Lower: the new ROIs fade, so you see the image under them. Higher: they get more solid (fill and outline), for when they are dim.")
        self.opacity_slider.valueChanged.connect(lambda value: self.opacity_changed.emit(float(value) / 100.0))
        type_row.addWidget(self.opacity_slider)

        # -- fields -----------------------------------------------------------------------------------------------
        self.rows = self._count_spin("rows")
        self.cols = self._count_spin("cols")
        self.pitch_x = self._length("pitch_x", geometry)
        self.pitch_y = self._length("pitch_y", geometry)
        self.step_x = self._factor("step_x")
        self.step_y = self._factor("step_y")
        self.angle = QDoubleSpinBox(self)
        self.angle.setRange(-180.0, 180.0)
        self.angle.setDecimals(2)
        self.angle.setSingleStep(0.1)
        self.angle.setSuffix(" °")
        self.angle.setToolTip(_TIPS["angle"])
        self.angle.setFixedWidth(NUMBER_WIDTH)
        self.angle.valueChanged.connect(self._on_edited)
        self.row_offset = QDoubleSpinBox(self)
        self.row_offset.setRange(-1.0, 1.0)
        self.row_offset.setDecimals(3)
        self.row_offset.setSingleStep(0.05)
        self.row_offset.setToolTip(_TIPS["row_offset"])
        self.row_offset.setFixedWidth(NUMBER_WIDTH)
        self.row_offset.valueChanged.connect(self._on_edited)
        self.pitch_x.set_value_px(50.0)
        self.pitch_y.set_value_px(50.0)
        self.rows.setValue(2)
        self.cols.setValue(2)

        self.picker = CornerPicker(self)
        self.picker.start_changed.connect(lambda _x, _y: self._on_edited())
        # Numbering of the ROIs: the corner it starts at (a picker drawn as a reading path); a double-click on the picked corner switches rows / columns
        self.numbering = NumberingPicker(self)
        self.numbering.changed.connect(self._on_numbering_edited)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self._place(grid, 0, 0, "cols", "Columns", self.cols)  # columns run along x: over pitch x
        self._place(grid, 0, 3, "rows", "Rows", self.rows)  # rows run along y: over pitch y
        self._place(grid, 1, 0, "pitch_x", "Pitch x", self.pitch_x)
        self._place(grid, 1, 3, "pitch_y", "Pitch y", self.pitch_y)
        grid.setColumnStretch(6, 1)

        caption = QLabel("Start point", self)
        caption.setToolTip("Where in the array the selected ROI(s) sit; the array grows away from them.")
        start_row = QHBoxLayout()  # the start point has a row of its own
        start_row.setSpacing(10)
        start_row.addWidget(caption)
        start_row.addWidget(self.picker)
        start_row.addSpacing(18)
        numbering_caption = QLabel("Numbering", self)
        numbering_caption.setToolTip(self.numbering.toolTip())
        start_row.addWidget(numbering_caption)
        start_row.addWidget(self.numbering)
        start_row.addStretch(1)

        # -- advanced (collapsible) --------------------------------------------------------------------------------
        self._advanced_toggle = QToolButton(self)
        self._advanced_toggle.setText("Advanced")
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setAutoRaise(True)
        self._advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.setToolTip("Show or hide the advanced settings: pitch increments, angle, row offset.")
        self._advanced = QFrame(self)
        self._advanced.setStyleSheet("QFrame { background: transparent; border: none; }")  # the panel's own background shows, so the rows match the rest
        advanced_grid = QGridLayout(self._advanced)
        advanced_grid.setContentsMargins(0, 0, 0, 0)
        advanced_grid.setHorizontalSpacing(6)
        advanced_grid.setVerticalSpacing(4)
        self._place(advanced_grid, 0, 0, "step_x", "Increment x", self.step_x)
        self._place(advanced_grid, 0, 3, "step_y", "Increment y", self.step_y)
        self._place(advanced_grid, 1, 0, "angle", "Angle", self.angle)
        self._place(advanced_grid, 1, 3, "row_offset", "Row offset", self.row_offset)
        advanced_grid.setColumnStretch(6, 1)
        self._advanced.setVisible(False)
        self._advanced_toggle.toggled.connect(self._on_advanced_toggled)

        # -- status and buttons ------------------------------------------------------------------------------------
        self._message = QLabel("", self)
        self._message.setWordWrap(True)
        self._count = QLabel("", self)
        # Free-standing icons, like the type and cell icons (no button around them): a cross closes without making anything, a tick makes the array.
        self.cancel_button = FlatIconButton("Cancel: close without making anything.", self)
        self.apply_button = FlatIconButton("Apply: make the array (one undo step; Ctrl+Z takes it back).", self)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        self.apply_button.clicked.connect(lambda _checked=False: self.apply_requested.emit())
        bottom = QHBoxLayout()
        bottom.addWidget(self._count)
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.apply_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addLayout(type_row)
        layout.addLayout(start_row)
        layout.addLayout(grid)
        layout.addWidget(self._advanced_toggle)
        layout.addWidget(self._advanced)
        layout.addWidget(self._message)
        layout.addLayout(bottom)

        self.set_kind(self._kind)
        self._ready = True
        self.refresh_theme()
        self._refresh_locks()

    def paintEvent(self, event) -> None:
        """Fill with the window colour, the one the fields (spin boxes) have. Painted here, not by a style sheet rule: a plain `QWidget` subclass only
        shows a style-sheet background with extra attributes, and the colour that showed through before was lighter than the fields (2026-11-25)."""
        QPainter(self).fillRect(self.rect(), QColor(get_active_theme().window_bg))

    # -- construction helpers ---------------------------------------------------------------------------------------

    def _count_spin(self, name: str) -> QSpinBox:
        box = QSpinBox(self)
        box.setRange(1, MAX_COUNT)
        box.setToolTip(_TIPS[name] + f" 1 to {MAX_COUNT}.")
        box.setFixedWidth(COUNT_WIDTH)
        box.valueChanged.connect(self._on_edited)
        return box

    def _length(self, name: str, geometry: GeometryModule) -> LengthField:
        field = LengthField(geometry, parent=self)
        field.setToolTip(_TIPS[name])
        field.setFixedWidth(LENGTH_WIDTH)
        field.value_changed.connect(lambda _v: self._on_edited())
        return field

    def _factor(self, name: str) -> QDoubleSpinBox:
        """A pitch increment: a plain number, 1 = no change."""
        box = QDoubleSpinBox(self)
        box.setRange(FACTOR_MIN, FACTOR_MAX)
        box.setDecimals(3)
        box.setSingleStep(0.01)
        box.setValue(1.0)
        box.setSuffix(" ×")
        box.setToolTip(_TIPS[name])
        box.setFixedWidth(NUMBER_WIDTH)
        box.valueChanged.connect(self._on_edited)
        return box

    def _place(self, grid: QGridLayout, row: int, column: int, kind: str, text: str, field: QWidget) -> None:
        """One setting: its pictogram, its name and its field, side by side."""
        picture = Pictogram(kind, _TIPS[kind], self)
        self._pictograms.append(picture)
        label = QLabel(text, self)
        label.setToolTip(_TIPS[kind])
        grid.addWidget(picture, row, column)
        grid.addWidget(label, row, column + 1)
        grid.addWidget(field, row, column + 2)

    # -- state ----------------------------------------------------------------------------------------------------

    def kind(self) -> str:
        return self._kind

    def _next_kind(self) -> str:
        """The lattice type after the current one (the type icon cycles through `LATTICE_KINDS`)."""
        keys = [kind.key for kind in LATTICE_KINDS]
        return keys[(keys.index(self._kind) + 1) % len(keys)]

    def set_kind(self, key: str, *, emit: bool = False) -> None:
        """Pick the lattice type. A change sets the row offset to that type's usual value (the field shows it)."""
        lattice_kind(key)
        changed = key != self._kind
        self._kind = key
        if changed:
            blocked = self.row_offset.blockSignals(True)
            self.row_offset.setValue(lattice_kind(key).default_row_offset)
            self.row_offset.blockSignals(blocked)
        self._refresh_kind_icons()
        self._refresh_regular_icon()
        if emit:
            self._on_edited()

    def spec(self) -> ArraySpec:
        fx, fy = self.picker.start()
        return ArraySpec(
            kind=self._kind,
            rows=self.rows.value(),
            cols=self.cols.value(),
            pitch_x_px=self.pitch_x.value_px(),
            pitch_y_px=self.pitch_y.value_px(),
            start_x=fx,
            start_y=fy,
            angle_deg=self.angle.value(),
            pitch_factor_x=self.step_x.value(),
            pitch_factor_y=self.step_y.value(),
            row_offset=self.row_offset.value(),
            regular_cell=self.regular.isChecked(),
            number_by=self.numbering.by(),
            number_start_x=self.numbering.start()[0],
            number_start_y=self.numbering.start()[1],
        )

    def set_spec(self, spec: ArraySpec) -> None:
        """Show ``spec`` on the controls without reporting each change (one `spec_changed` at the end, only if ``emit`` is wanted use `set_kind`)."""
        self.set_kind(spec.kind)
        widgets = (self.rows, self.cols, self.angle, self.row_offset, self.regular, self.step_x, self.step_y)
        blocked = [w.blockSignals(True) for w in widgets]
        try:
            self.rows.setValue(int(spec.rows))
            self.cols.setValue(int(spec.cols))
            self.angle.setValue(float(spec.angle_deg))
            self.row_offset.setValue(lattice_kind(spec.kind).default_row_offset if spec.row_offset is None else float(spec.row_offset))
            self.regular.setChecked(bool(spec.regular_cell))
            self.step_x.setValue(float(spec.pitch_factor_x))
            self.step_y.setValue(float(spec.pitch_factor_y))
        finally:
            for widget, was in zip(widgets, blocked, strict=True):
                widget.blockSignals(was)
        self.pitch_x.set_value_px(spec.pitch_x_px)
        self.pitch_y.set_value_px(spec.pitch_y_px)
        self.picker.set_start(spec.start_x, spec.start_y)
        self.numbering.set_numbering(spec.number_by, spec.number_start_x, spec.number_start_y)
        self._refresh_locks()

    def set_message(self, text: str, *, problem: bool = False) -> None:
        theme = get_active_theme()
        self._message.setText(text)
        self._message.setStyleSheet(f"color: {'#f59e0b' if problem else theme.text_muted};")

    def message(self) -> str:
        return self._message.text()

    def set_count(self, text: str) -> None:
        self._count.setText(text)

    def set_apply_enabled(self, enabled: bool) -> None:
        self.apply_button.setEnabled(bool(enabled))

    def is_advanced_open(self) -> bool:
        return self._advanced_toggle.isChecked()

    def set_advanced_open(self, opened: bool) -> None:
        self._advanced_toggle.setChecked(bool(opened))

    # -- reactions ------------------------------------------------------------------------------------------------

    def _on_edited(self, *_args: object) -> None:
        if not self._ready:
            return
        self._refresh_regular_icon()
        self._refresh_locks()
        self.spec_changed.emit()

    def _on_numbering_edited(self) -> None:
        self._on_edited()

    def set_opacity(self, opacity: float) -> None:
        """Show ``opacity`` (0 - 1) on the wedge without reporting it (restoring the saved value)."""
        blocked = self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(round(max(min(float(opacity), 1.0), 0.0) * 100))
        self.opacity_slider.blockSignals(blocked)

    def opacity(self) -> float:
        return self.opacity_slider.value() / 100.0

    def _on_advanced_toggled(self, opened: bool) -> None:
        self._advanced.setVisible(bool(opened))
        self._refresh_advanced_icon()
        self.advanced_toggled.emit(bool(opened))  # the popup holding this panel resizes to fit (`ArrayController`)

    def _refresh_locks(self) -> None:
        """While the regular cell is on, show the values in force in the y fields and the row offset and lock them."""
        on = self.regular.isChecked()
        if on:
            resolved = self.spec().resolved()
            self.pitch_y.set_value_px(resolved.pitch_y_px)
            blocked = [w.blockSignals(True) for w in (self.step_y, self.row_offset)]
            self.step_y.setValue(float(resolved.pitch_factor_y))
            self.row_offset.setValue(float(resolved.row_offset or 0.0))
            for widget, was in zip((self.step_y, self.row_offset), blocked, strict=True):
                widget.blockSignals(was)
        for widget in (self.pitch_y, self.step_y, self.row_offset):
            widget.setEnabled(not on)

    # -- look -----------------------------------------------------------------------------------------------------

    def _refresh_kind_icons(self) -> None:
        """The icon of the current lattice type; its tip names the other one."""
        theme = get_active_theme()
        self.kind_button.setIcon(pictogram_icon(f"type_{self._kind}", dim=theme.accent_blue, accent=theme.accent_blue))
        current, other = lattice_kind(self._kind).label, lattice_kind(self._next_kind()).label
        self.kind_button.setToolTip(f"Lattice type: {current}. Click for {other}.")

    def _refresh_action_icons(self) -> None:
        theme = get_active_theme()
        self.cancel_button.setIcon(pictogram_icon("cancel", accent=theme.text_secondary))
        self.apply_button.setIcon(pictogram_icon("apply", accent=theme.accent_green))  # (greyed by Qt while there is nothing to apply)

    def _refresh_regular_icon(self) -> None:
        """The cell the lattice has: rectangular, a square (regular cell on) or a rectangle; triangular, an equilateral triangle or a lopsided one."""
        theme = get_active_theme()
        on = self.regular.isChecked()
        names = {"rectangular": ("cell_square", "cell_rectangle"), "triangular": ("cell_equilateral", "cell_triangle")}[self._kind]
        colour = theme.accent_blue if on else theme.text_dim
        self.regular.setIcon(pictogram_icon(names[0] if on else names[1], dim=colour, accent=colour))

    def _refresh_advanced_icon(self) -> None:
        theme = get_active_theme()
        name = "chevron-down" if self._advanced_toggle.isChecked() else "chevron-right"
        self._advanced_toggle.setIcon(load_tabler_icon(name, color=theme.text_secondary, size=ICON_SIZE * 2, stroke_width=2.1))
        # Free-standing text, like a roll-down heading: no box in any state (checked, hovered, pressed).
        self._advanced_toggle.setStyleSheet(
            "QToolButton, QToolButton:checked, QToolButton:pressed { border: none; background: transparent; padding: 2px 0px; "
            f"color: {theme.text_secondary}; font-weight: 600; text-align: left; }} QToolButton:hover {{ color: {theme.text_primary}; background: transparent; }}"
        )

    def refresh_theme(self) -> None:
        # One background for the whole panel (`paintEvent`), the colour the fields have; the labels on it draw none of their own.
        self.setStyleSheet("QLabel { background: transparent; }")
        self.update()
        for picture in self._pictograms:
            picture.refresh_theme()
        self._refresh_kind_icons()
        self._refresh_regular_icon()
        self._refresh_advanced_icon()
        self._refresh_action_icons()
        self.picker.refresh_theme()
        self.numbering.refresh_theme()
