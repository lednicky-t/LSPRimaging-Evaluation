"""``RibbonRow`` - the control row of a display panel (Spectra, Sensorgram), in the look of the Image panel's ribbon (2026-10-24).

Captioned groups (a field or two with a small muted caption underneath, `ribbon_group.labeled_icon_group`) separated by the
thin dividers of the tool ribbon, the fields in the frameless ribbon look (`ribbon_fields.py`). The row is put in a
`RibbonDrawer` (`ribbon_drawer.py`), which gives it what the Image ribbons have: a double-click on empty space rolls it up
to a "..." handle (the plot gets the height back) and a panel narrower than the row scrolls it with an arrow at each edge
instead of refusing to get narrower.

`rolled_ribbon` builds the pair and wires the double-click, so a panel needs three lines::

    row = RibbonRow(self)
    row.add_group("Formula", self._formula)
    drawer = rolled_ribbon(row, "Show the spectra controls.", self)
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from lspr_ui import get_active_theme

from .ribbon_drawer import RibbonDrawer
from .ribbon_group import group_label_style, labeled_icon_group, ribbon_separator

_GROUP_SPACING = 6
_FIELD_SPACING = 4  # between the fields of one group


class RibbonRow(QWidget):
    roll_up_requested = pyqtSignal()  # a double-click on empty space of the row (a field takes its own double-click)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(_GROUP_SPACING)
        self._separators: list[QWidget] = []
        self._captions: list[QLabel] = []

    def add_group(self, caption: str, *fields: QWidget) -> None:
        """A group of one or more fields (left to right) with *caption* under them. A divider goes before every group but the first."""
        if self._layout.count():
            separator = ribbon_separator(self)
            self._separators.append(separator)
            self._layout.addWidget(separator)
        if len(fields) == 1:
            content = fields[0]
        else:
            content = QWidget(self)
            row = QHBoxLayout(content)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(_FIELD_SPACING)
            for field in fields:
                row.addWidget(field)
        group, label = labeled_icon_group(self, content, caption)
        self._captions.append(label)
        self._layout.addWidget(group)

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Fields and check boxes take their own clicks; one that reaches the row itself landed on empty space."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.roll_up_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def refresh_theme(self) -> None:
        for separator in self._separators:
            separator.setStyleSheet(f"color: {get_active_theme().control_border};")
        for label in self._captions:
            label.setStyleSheet(group_label_style())


def rolled_ribbon(row: RibbonRow, handle_tooltip: str, parent: QWidget) -> RibbonDrawer:
    """*row* inside a `RibbonDrawer` that a double-click on the row's empty space rolls up."""
    drawer = RibbonDrawer(row, handle_tooltip, parent, handle_alignment=Qt.AlignmentFlag.AlignTop)
    row.roll_up_requested.connect(lambda: drawer.set_expanded(False))
    return drawer
