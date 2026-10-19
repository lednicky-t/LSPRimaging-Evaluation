"""Image panel ribbon, ROIs tab, "Groups" section: the group-label pick-up menu.

One icon button (a label over three dots) that opens a one-row strip, no captions, no drop-downs. Left to
right, with a thin line between the parts:

    [label on/off] | [1st | nth] | [3 x 3 place grid] | [label  (rotate)] | [90 %] | [6 pt]

- the label toggle: show or hide the group labels;
- 1st / nth (raised letters): which ROI of the group, by number, carries the label (first or last);
- the 3 x 3 grid: where around that ROI (centred on it, or one of eight places outside its circle);
- the word "label" at the current text angle, and a rotate arrow beside it that turns the text 45 degrees
  further on every press (eight presses make a full turn);
- the box size, a number with %: the longest a label may be, as a percent of the distance between
  neighbouring ROIs of the group, so labels of neighbouring groups do not run into each other;
- the smallest text size, in pt, a long name is shrunk to before it is broken into lines.

Every part says what it is in its tooltip. The drawing is `group_label_overlay.py`.

Holds only the controls; `ImagePanel` applies `settings()` to the overlay on every `changed`. Every control
is remembered across restarts (`bind_ui_state`, same keys and values as when the choices were drop-downs). The
View tab carries a second instance, kept equal to this one with `sync_from` (see `view_tab.py`).
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractSpinBox, QHBoxLayout, QMenu, QSpinBox, QToolButton, QWidget, QWidgetAction

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from ..ribbon_group import vertical_separator
from ..ui_state import UiStateStore
from .choice_picker import ChoicePicker
from .general_group import ICON_SIZE, style_general_icon_button
from .group_label_icons import ordinal_icon
from .group_label_overlay import (
    DEFAULT_BOX_PERCENT,
    DEFAULT_MIN_POINT_SIZE,
    MAX_BOX_PERCENT,
    MIN_BOX_PERCENT,
    MIN_POINT_SIZE_RANGE,
    GroupLabelSettings,
)
from .label_placement import DEFAULT_GROUP_PLACEMENT
from .label_placement_picker import LabelPlacementPicker
from .text_angle_control import TextAngleControl

_RENDER_SIZE = ICON_SIZE * 2
# No frame, no fill (cleaner than a box in a strip of icons) and a slimmer padding than the theme's 2px 5px.
_SPIN_STYLE = "QSpinBox { border: none; background: transparent; padding: 1px 2px; }"
_PERCENT_EXTRA_PX = 14  # beyond the widest text of the % box: padding and the edit's own margin; the "300 %" needs the room
_POINT_EXTRA_PX = 10  # same for the pt box
_BOX_TIP = (
    "The longest a group label may be, as a percent of the distance between neighbouring ROIs of the group. "
    "A longer name is shrunk, then cut. Lower it if labels of neighbouring groups touch."
)
_MIN_SIZE_TIP = (
    "The smallest text size you still find readable. A name too long for its box is shrunk down to this "
    "size first, and only then broken into lines."
)
_POSITION_CHOICES = (
    ("first", "Label each group at its first ROI (by ROI number)."),
    ("last", "Label each group at its last ROI (by ROI number)."),
)


def label_menu_icon(color: str) -> QIcon:
    """The ROI-label icon over three dots in a row. Its visible part fills the same 30 x 30 of the 36 x 36
    canvas that a Tabler icon does (measured 2026-10-19: it was 24 wide, the dots spanning only 0.26-0.74 of
    the canvas and the label drawn at 62 %, so it read smaller than the icons beside it)."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    label_size = round(_RENDER_SIZE * 0.72)
    label = load_tabler_icon("label-important", color=color, size=label_size, stroke_width=2.1).pixmap(label_size, label_size)
    painter.drawPixmap((_RENDER_SIZE - label_size) // 2, round(_RENDER_SIZE * 0.028), label)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    radius = _RENDER_SIZE * 0.07
    y = _RENDER_SIZE * 0.845
    for x_fraction in (0.16, 0.5, 0.84):
        x = _RENDER_SIZE * x_fraction
        painter.drawEllipse(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
    painter.end()
    return QIcon(pixmap)


def _ordinal_for(value: str, picked: bool, theme: GuiTheme) -> QIcon:
    return ordinal_icon(value, theme.accent_blue if picked else theme.text_dim)


class GroupLabelMenu(QToolButton):
    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        panel = QWidget(self)
        self.show_button = QToolButton(panel)
        self.show_button.setCheckable(True)
        self.show_button.setToolTip("Show or hide the group labels (each group's name).")
        style_general_icon_button(self.show_button)
        self.position = ChoicePicker(_POSITION_CHOICES, "first", _ordinal_for, parent=panel)
        self.placement = LabelPlacementPicker(DEFAULT_GROUP_PLACEMENT, panel)
        self.placement.setToolTip("Where the label sits around that ROI: the middle centres it on the ROI, the others put it just outside.")
        self.angle = TextAngleControl(0, panel)
        self.box = QSpinBox(panel)
        self.box.setRange(MIN_BOX_PERCENT, MAX_BOX_PERCENT)
        self.box.setSingleStep(5)
        self.box.setSuffix(" %")
        self.box.setValue(DEFAULT_BOX_PERCENT)
        self.box.setToolTip(_BOX_TIP)
        self.min_size = QSpinBox(panel)
        self.min_size.setRange(*MIN_POINT_SIZE_RANGE)
        self.min_size.setSuffix(" pt")
        self.min_size.setValue(DEFAULT_MIN_POINT_SIZE)
        self.min_size.setToolTip(_MIN_SIZE_TIP)

        row = QHBoxLayout(panel)
        row.setContentsMargins(5, 3, 5, 3)  # was 8, 6: a slimmer border round the strip
        row.setSpacing(4)  # was 6, also either side of each thin line
        self._separators: list[QWidget] = []
        for index, part in enumerate((self.show_button, self.position, self.placement, self.angle, self.box, self.min_size)):
            if index:
                separator = vertical_separator(panel)
                self._separators.append(separator)
                row.addWidget(separator)
            row.addWidget(part, 0, Qt.AlignmentFlag.AlignVCenter)

        self._menu = QMenu(self)
        self._menu.setStyleSheet("QMenu { padding: 2px; }")  # the theme's 4 px, slimmer: only this menu
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)

        self.show_button.toggled.connect(self._on_changed)
        self.position.value_changed.connect(self._on_changed)
        self.placement.placement_changed.connect(self._on_changed)
        self.angle.angle_changed.connect(self._on_changed)
        self.box.valueChanged.connect(self._on_changed)
        self.min_size.valueChanged.connect(self._on_changed)
        self.refresh_theme()

    def _fit_spin_widths(self) -> None:
        """Both number boxes just wide enough for the widest value they can show (300 %, 14 pt), then fixed so
        the strip does not stretch them. The theme pads every field 5 px a side (plus a border and the inner
        edit's margin: the text starts 9 px in), which is more than half the width of three digits, so these two
        boxes get a slim padding of their own, and no frame or fill."""
        for spin, widest, extra in (
            (self.box, f"{MAX_BOX_PERCENT} %", _PERCENT_EXTRA_PX),
            (self.min_size, f"{MIN_POINT_SIZE_RANGE[1]} pt", _POINT_EXTRA_PX),
        ):
            spin.setStyleSheet(_SPIN_STYLE)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            spin.ensurePolished()
            spin.setFixedWidth(QFontMetrics(spin.font()).horizontalAdvance(widest) + extra)

    def settings(self) -> GroupLabelSettings:
        return GroupLabelSettings(
            visible=self.show_button.isChecked(),
            angle=self.angle.angle(),
            position=self.position.value(),
            side=self.placement.placement(),
            box_percent=float(self.box.value()),
            min_point_size=float(self.min_size.value()),
        )

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Put every control back as last left, then save each later change."""
        store.bind("image/group_labels/visible", self.show_button)
        # The key held a word (horizontal / vertical / diagonal) before it became an angle; both are read.
        self.angle.bind_ui_state(store, "image/group_labels/angle", legacy_key="image/group_labels/direction")
        self.position.bind_ui_state(store, "image/group_labels/position")
        # The key predates the 3 x 3 grid: its old values (top, left, bottom, right) are still placements.
        self.placement.bind_ui_state(store, "image/group_labels/side")
        store.bind("image/group_labels/box_percent", self.box)
        store.bind("image/group_labels/min_point_size", self.min_size)

    def sync_from(self, other: GroupLabelMenu, *, silent: bool = True) -> None:
        """Make every control equal to ``other``'s (the same menu sits on the ROIs and View tabs). Only a
        control that differs is touched, so one change on a copy is one change here. ``silent=False`` lets the
        controls report as if the user had changed them (the panel redraws, the value is saved); ``True``
        shows the values without any report."""
        was_blocked = self.blockSignals(True) if silent else False  # blocks this menu's `changed`, nothing else
        try:
            if self.show_button.isChecked() != other.show_button.isChecked():
                self.show_button.setChecked(other.show_button.isChecked())
            if self.position.value() != other.position.value():
                self.position.set_value(other.position.value())
            if self.angle.angle() != other.angle.angle():
                self.angle.set_angle(other.angle.angle())
            if self.placement.placement() != other.placement.placement():
                self.placement.set_placement(other.placement.placement())
            for spin, theirs in ((self.box, other.box), (self.min_size, other.min_size)):
                if spin.value() != theirs.value():
                    spin.setValue(theirs.value())
        finally:
            if silent:
                self.blockSignals(was_blocked)

    def _on_changed(self, *_args: object) -> None:
        self._refresh_icons()
        self.changed.emit()

    def _refresh_icons(self) -> None:
        theme = get_active_theme()
        on = self.show_button.isChecked()
        self.setIcon(label_menu_icon(theme.accent_blue if on else theme.text_dim))
        self.setToolTip("Group labels: show or hide, which ROI, where, text angle and size." + ("" if on else " (hidden)"))
        self.show_button.setIcon(
            load_tabler_icon(
                "label-important" if on else "label-off",
                color=theme.accent_blue if on else theme.text_dim,
                size=_RENDER_SIZE,
                stroke_width=2.1,
            )
        )

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style_general_icon_button(self)
        style_general_icon_button(self.show_button)
        self.position.refresh_theme(theme)
        self.placement.refresh_theme(theme)
        self.angle.refresh_theme(theme)
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        self._fit_spin_widths()
        self._refresh_icons()
