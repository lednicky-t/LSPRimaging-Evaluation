"""Image panel, ROIs tab, "Groups" section: three separate icon buttons (2026-11-25, maintainer: the Group pick-up menu is split up).

- **Group / Ungroup** (one place, as in the ROI table): Group (the selected ROIs under a name, an empty group if none is
  selected). When the selection holds grouped ROIs the same place shows Ungroup, which takes the selected ROIs out of
  their groups (they stay).
- **Group by rows** and **Group by columns**: split the array (the selected ROIs, or all) into one group per row / column,
  each in its own palette colour.

The group-label menu is no longer shown here (the View ribbon's Groups icon has it; maintainer, 2026-11-25). It stays built, hidden,
because it holds the original controls the View ribbon's copy mirrors and that are remembered (`RoiTab.group_label_menu`), as the
Display section does.

Display only: the buttons emit signals; `GroupActions` calls the ROI Toolbox.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...shortcuts import display
from .general_group import ICON_SIZE, style_general_icon_button
from .group_label_menu import GroupLabelMenu

_RENDER_SIZE = ICON_SIZE * 2


def _tips() -> dict[str, str]:
    """The buttons' tool tips, with their keys (read when the controls are built: the keys come from `shortcuts.py`)."""
    return {
        "create": f"Group the selected ROIs ({display('groups.group')}; an empty group if none are selected).",
        "ungroup": f"Ungroup ({display('groups.ungroup')}): take the selected ROIs out of their groups (the ROIs stay; a group left empty goes away).",
        "rows": "Group by rows: one group per row of the array, each in its own colour. Works on the selected ROIs, or on all if none are selected.",
        "columns": "Group by columns: one group per column of the array, each in its own colour. Works on the selected ROIs, or on all if none are selected.",
    }


_LINE_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c")  # three groups, as the palette hands them out


def _dot_grid_icon(by_rows: bool) -> QIcon:
    """3 x 3 dots; each row (or, for columns, each column) in its own colour: one group per line."""
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    radius = _RENDER_SIZE * 0.1
    for row in range(3):
        for col in range(3):
            painter.setBrush(QColor(_LINE_COLORS[row if by_rows else col]))
            x, y = _RENDER_SIZE * (0.2 + 0.3 * col), _RENDER_SIZE * (0.2 + 0.3 * row)
            painter.drawEllipse(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
    painter.end()
    return QIcon(pixmap)


class GroupControls(QWidget):
    create_requested = pyqtSignal()
    ungroup_requested = pyqtSignal()
    by_rows_requested = pyqtSignal()
    by_columns_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        tips = _tips()
        self._buttons = {key: QToolButton(self) for key in tips}
        for key, signal in (
            ("create", self.create_requested),
            ("ungroup", self.ungroup_requested),
            ("rows", self.by_rows_requested),
            ("columns", self.by_columns_requested),
        ):
            self._buttons[key].setToolTip(tips[key])
            self._buttons[key].clicked.connect(lambda _checked=False, sig=signal: sig.emit())
        # Hidden, not removed: the original of the group-label controls (the View ribbon mirrors it, the settings are saved from it).
        self.label_menu = GroupLabelMenu(self)
        self.label_menu.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._slot = QStackedWidget(self)  # Group and Ungroup share one place: whichever fits the selection
        self._slot.addWidget(self._buttons["create"])
        self._slot.addWidget(self._buttons["ungroup"])
        layout.addWidget(self._slot)
        layout.addWidget(self._buttons["rows"])
        layout.addWidget(self._buttons["columns"])
        self.refresh_theme()

    def refresh_theme(self) -> None:
        for button in self._buttons.values():
            style_general_icon_button(button)
        color = get_active_theme().text_primary
        self._buttons["create"].setIcon(load_tabler_icon("group-rois", color=color, size=_RENDER_SIZE, stroke_width=2.1))
        self._buttons["ungroup"].setIcon(load_tabler_icon("ungroup-rois", color=color, size=_RENDER_SIZE, stroke_width=2.1))
        self._buttons["rows"].setIcon(_dot_grid_icon(by_rows=True))
        self._buttons["columns"].setIcon(_dot_grid_icon(by_rows=False))
        self.label_menu.refresh_theme()

    def set_ungroup_mode(self, ungroup: bool) -> None:
        """Show Ungroup (the selection holds grouped ROIs) instead of Group."""
        self._slot.setCurrentWidget(self._buttons["ungroup" if ungroup else "create"])
