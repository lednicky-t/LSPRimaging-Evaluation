"""The Array section's Reorder button (2026-11-25): renumber the selected ROIs like an array, with the numbering picker of the Array / Find* pop-outs.

Screen purpose: one icon (the Reorder icon of the retired Array group). Clicking it opens a small pick-up with the same `NumberingPicker` the Array and Find* pop-outs have
(click a corner to start there, double-click the picked corner to switch rows / columns) and a tick. The tick renumbers and closes the pick-up; clicking away closes
it and does nothing. Works on the selected ROIs, or on all if none are selected (as the old Reorder menu did): select an array first with the ROI menu's
"Select whole array".

**Live preview** (`array_reorder_preview.py`): while the pick-up is open the image shows the numbers the ROIs would get (accent colour, in the place of their
labels, also with the labels switched off); every click on the picker redraws it. Only the numbers the chosen ROIs already hold are shuffled among them (`RoiToolbox.reorder_subset`, one undo step); every other ROI keeps its number. Positions are
the stored (reference-frame) centres on the cube on screen. Display only here: `reorder_roi_numbers` does the work through the toolbox.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon

from ...domain.roi_shapes import nominal_diameter
from ...roi import RoiToolbox
from ...roi.ordering import grid_band_px, order_by_grid_from_corner
from ...selection import SelectionModule
from .array_pictograms import FlatIconButton, pictogram_icon
from .array_picker import NumberingPicker
from .general_group import ICON_SIZE, style_general_icon_button

TIP = (
    "Reorder: renumber the selected ROIs like an array. Pick where the numbering starts and whether it runs by rows or by columns, then tick. "
    "Works on the selected ROIs, or on all if none are selected (right-click a ROI > Select whole array)."
)
APPLY_TIP = "Reorder: renumber the ROIs in the picked order (one undo step; Ctrl+Z takes it back)."


def reorder_icon(color: str) -> QIcon:
    """The Reorder icon: Tabler "sort-ascending-numbers" (the stable app's, and the one the retired Array group's Reorder menu wore)."""
    return load_tabler_icon("sort-ascending-numbers", color=color, size=ICON_SIZE * 2, stroke_width=2.1)


def plan_reorder(toolbox: RoiToolbox, selection: SelectionModule, by: str, start_x: float, start_y: float) -> list[int] | None:
    """The ids of the ROIs to renumber (the selected ones, all if none is selected) in the order the numbering visits them, or ``None`` when there are fewer than two.
    ``start_x`` / ``start_y``: 0 or 1, the corner it starts at. The ROI first in the list gets the smallest of their numbers, and so on (`RoiToolbox.reorder_subset`).
    Positions are the stored (reference-frame) centres on the cube on screen. Pure reading: used by the preview and by the command."""
    selected = {int(i) for i in selection.selected_roi_ids()}
    rois = [roi for roi in toolbox.rois_at(int(selection.current_cube())) if not selected or roi.area_roi_id in selected]
    if len(rois) < 2:
        return None
    return order_by_grid_from_corner(
        [roi.area_roi_id for roi in rois],
        [(float(roi.center_x), float(roi.center_y)) for roi in rois],
        grid_band_px([nominal_diameter(roi.shape, roi.center_x, roi.center_y) for roi in rois]),
        by=by,
        from_right=bool(start_x),
        from_bottom=bool(start_y),
    )


def reorder_roi_numbers(toolbox: RoiToolbox, selection: SelectionModule, by: str, start_x: float, start_y: float) -> str:
    """Renumber the selected ROIs (all if none selected): the numbers they hold are dealt out again in the order of the numbering (`plan_reorder`). Returns the line
    for the status bar (also when nothing was done, and why)."""
    order = plan_reorder(toolbox, selection, by, start_x, start_y)
    if order is None:
        return "Reorder: nothing to reorder (fewer than two ROIs)."
    toolbox.reorder_subset(order)
    scope = "the selected ROIs" if selection.selected_roi_ids() else "all ROIs"
    corner = f"{'bottom' if start_y else 'top'}-{'right' if start_x else 'left'}"
    return f"Reorder: numbered {scope} {'column by column' if by == 'columns' else 'row by row'}, starting {corner} (Ctrl+Z undoes it)."


class ArrayReorderButton(QToolButton):
    """The icon and its pick-up. Reports the pick through `reorder_requested(by, start_x, start_y)`; knows nothing of ROIs."""

    reorder_requested = pyqtSignal(str, float, float)
    choice_changed = pyqtSignal(str, float, float)  # the picker was clicked while the pick-up is open: (by, start_x, start_y)
    popup_opened = pyqtSignal()
    popup_closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip(TIP)
        panel = QWidget(self)
        self.picker = NumberingPicker(panel)
        self.apply_button = FlatIconButton(APPLY_TIP, panel)
        self.apply_button.clicked.connect(self._on_apply)
        self._caption = QLabel("Numbering", panel)
        self._caption.setToolTip(self.picker.toolTip())
        row = QHBoxLayout(panel)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(10)
        row.addWidget(self._caption)
        row.addWidget(self.picker)
        row.addWidget(self.apply_button)
        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self.picker.changed.connect(lambda: self.choice_changed.emit(self.picker.by(), *self.picker.start()))
        self._menu.aboutToShow.connect(self.popup_opened)
        self._menu.aboutToHide.connect(self.popup_closed)
        self.refresh_theme()

    def choice(self) -> tuple[str, float, float]:
        """``(by, start_x, start_y)`` as picked now."""
        return (self.picker.by(), *self.picker.start())

    @property
    def popup_menu(self) -> QMenu:
        return self._menu

    def _on_apply(self) -> None:
        self._menu.hide()  # (closing it clears the preview; the renumbering below redraws the real labels)
        self.reorder_requested.emit(*self.choice())

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        style_general_icon_button(self)
        self.setStyleSheet(self.styleSheet() + " QToolButton::menu-indicator { image: none; width: 0px; }")  # an icon that opens a pick-up needs no arrow
        self.setIcon(reorder_icon(theme.text_primary))
        self.apply_button.setIcon(pictogram_icon("apply", accent=theme.accent_green))
        self._caption.setStyleSheet("QLabel { background: transparent; }")
        self.picker.refresh_theme()
