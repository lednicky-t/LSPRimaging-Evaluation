"""Image panel ribbon, ROIs tab, "Groups" section: the group-label pick-up menu.

One icon button (a label over three dots) that opens a small panel: show or hide the group labels, text
direction, which ROI of the group carries the label (first or last), where around that ROI (a 3 x 3 grid:
centred on it, or on one of eight places outside its circle), and the box size (the longest a label may be,
as a percent of the distance between neighbouring ROIs of the group, so labels of neighbouring groups do not
run into each other). The drawing is `group_label_overlay.py`.

Holds only the controls; `ImagePanel` applies `settings()` to the overlay on every `changed`. Every control
is remembered across restarts (`bind_ui_state`). The View tab carries a second instance, kept equal to this one
with `sync_from` (see `view_tab.py`).
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QComboBox, QGridLayout, QLabel, QMenu, QSpinBox, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ..ui_state import UiStateStore
from .general_group import ICON_SIZE, style_general_icon_button
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

_RENDER_SIZE = ICON_SIZE * 2
_BOX_TIP = (
    "The longest a group label may be, as a percent of the distance between neighbouring ROIs of the group. "
    "A longer name is shrunk, then cut. Lower it if labels of neighbouring groups touch."
)


def label_menu_icon(color: str) -> QIcon:
    """The ROI-label icon over three dots in a row."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    label_size = int(_RENDER_SIZE * 0.62)
    label = load_tabler_icon("label-important", color=color, size=label_size, stroke_width=2.1).pixmap(label_size, label_size)
    painter.drawPixmap((_RENDER_SIZE - label_size) // 2, 0, label)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    radius = _RENDER_SIZE * 0.075
    y = _RENDER_SIZE * 0.84
    for i in range(3):
        x = _RENDER_SIZE * (0.26 + 0.24 * i)
        painter.drawEllipse(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
    painter.end()
    return QIcon(pixmap)


def _combo(parent: QWidget, items: tuple[tuple[str, str], ...], tip: str) -> QComboBox:
    box = QComboBox(parent)
    for text, data in items:
        box.addItem(text, data)
    box.setToolTip(tip)
    return box


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
        self.direction = _combo(panel, (("Horizontal", "horizontal"), ("Vertical", "vertical"), ("Diagonal (45°)", "diagonal")),
            "Text direction (vertical and diagonal text read upwards).",
        )
        self.position = _combo(panel, (("First ROI", "first"), ("Last ROI", "last")), "Which ROI of the group (by number) carries the label.")
        self.placement = LabelPlacementPicker(DEFAULT_GROUP_PLACEMENT, panel)
        self.placement.setToolTip("Where the label sits around that ROI: the middle centres it on the ROI, the others put it just outside.")
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
        self.min_size.setToolTip(
            "The smallest text size you still find readable. A name too long for its box is shrunk down to this "
            "size first, and only then broken into lines."
        )

        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self._captions: list[QLabel] = []
        rows = (("Labels", self.show_button), ("Text", self.direction), ("At", self.position), ("Place", self.placement), ("Box size", self.box), ("Min text", self.min_size))
        for row, (text, widget) in enumerate(rows):
            caption = QLabel(text, panel)
            caption.setToolTip(widget.toolTip())
            self._captions.append(caption)
            grid.addWidget(caption, row, 0)
            grid.addWidget(widget, row, 1, alignment=Qt.AlignmentFlag.AlignLeft)  # the grid keeps its own small size

        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)

        self.show_button.toggled.connect(self._on_changed)
        self.direction.currentIndexChanged.connect(self._on_changed)
        self.position.currentIndexChanged.connect(self._on_changed)
        self.placement.placement_changed.connect(self._on_changed)
        self.box.valueChanged.connect(self._on_changed)
        self.min_size.valueChanged.connect(self._on_changed)
        self.refresh_theme()

    def settings(self) -> GroupLabelSettings:
        return GroupLabelSettings(
            visible=self.show_button.isChecked(),
            direction=str(self.direction.currentData()),
            position=str(self.position.currentData()),
            side=self.placement.placement(),
            box_percent=float(self.box.value()),
            min_point_size=float(self.min_size.value()),
        )

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Put every control back as last left, then save each later change."""
        store.bind("image/group_labels/visible", self.show_button)
        store.bind("image/group_labels/direction", self.direction)
        store.bind("image/group_labels/position", self.position)
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
            for mine, theirs in ((self.direction, other.direction), (self.position, other.position)):
                if mine.currentIndex() != theirs.currentIndex():
                    mine.setCurrentIndex(theirs.currentIndex())
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
        self.setToolTip("Group labels: show or hide, direction, position and size." + ("" if on else " (hidden)"))
        self.show_button.setIcon(
            load_tabler_icon(
                "label-important" if on else "label-off",
                color=theme.accent_blue if on else theme.text_dim,
                size=_RENDER_SIZE,
                stroke_width=2.1,
            )
        )

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        style_general_icon_button(self.show_button)
        self.placement.refresh_theme(get_active_theme())
        self._refresh_icons()
