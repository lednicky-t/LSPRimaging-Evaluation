"""Image panel, ROIs tab, "Groups" section: three icon buttons.

- **Create group**: group the selected ROIs under a name (an empty group if none is selected). The same
  "plus" icon as the ROI table's group button.
- **Group by rows / by columns**: split the array (the selected ROIs, or all) into one group per row or per
  column, each with its own palette colour.

Display only: the buttons emit signals; `GroupActions` calls the ROI Toolbox.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from .general_group import ICON_SIZE, style_general_icon_button
from .group_label_menu import GroupLabelMenu

_RENDER_SIZE = ICON_SIZE * 2
_TIPS = {
    "create": "Create a group from the selected ROIs (an empty group if none are selected).",
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
    by_rows_requested = pyqtSignal()
    by_columns_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons = {key: QToolButton(self) for key in _TIPS}
        self.label_menu = GroupLabelMenu(self)  # show / hide and place the group labels
        self._buttons["create"].clicked.connect(lambda _checked=False: self.create_requested.emit())
        self._buttons["rows"].clicked.connect(lambda _checked=False: self.by_rows_requested.emit())
        self._buttons["columns"].clicked.connect(lambda _checked=False: self.by_columns_requested.emit())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for key, button in self._buttons.items():
            button.setToolTip(_TIPS[key])
            layout.addWidget(button)
        layout.addWidget(self.label_menu)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        for button in self._buttons.values():
            style_general_icon_button(button)
        self._buttons["create"].setIcon(
            load_tabler_icon("plus", color=get_active_theme().accent_blue, size=_RENDER_SIZE, stroke_width=2.1)
        )
        self._buttons["rows"].setIcon(_dot_grid_icon(by_rows=True))
        self._buttons["columns"].setIcon(_dot_grid_icon(by_rows=False))
        self.label_menu.refresh_theme()
