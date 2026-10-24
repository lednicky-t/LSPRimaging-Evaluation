"""The ROI labels' text size, a number with "pt" (ROIs tab and View tab, in the ROI display pick-up menu).

Same look as the group-label menu's number boxes (`group_label_menu.py`): no frame, no fill, just wide enough
for the widest value. The ROIs tab owns one and the View tab a second, kept equal to it (`view_tab.py`).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QAbstractSpinBox, QSpinBox, QWidget

from .roi_label_overlay import DEFAULT_POINT_SIZE, POINT_SIZE_RANGE

_SPIN_STYLE = "QSpinBox { border: none; background: transparent; padding: 1px 2px; }"
_EXTRA_PX = 10  # beyond the widest text: padding and the edit's own margin (same as the group menu's pt box)


class RoiLabelSizeBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(*POINT_SIZE_RANGE)
        self.setSuffix(" pt")
        self.setValue(round(DEFAULT_POINT_SIZE))
        self.setToolTip("Text size of the ROI labels, in points. The labels keep this size while you zoom.")
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self.setStyleSheet(_SPIN_STYLE)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ensurePolished()
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance(f"{POINT_SIZE_RANGE[1]} pt") + _EXTRA_PX)

    def sync_from(self, other: RoiLabelSizeBox) -> None:
        if self.value() != other.value():
            self.setValue(other.value())
