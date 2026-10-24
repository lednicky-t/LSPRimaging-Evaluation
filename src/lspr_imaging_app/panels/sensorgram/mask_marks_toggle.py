"""``MaskMarksToggle``: the Sensorgram's View-section button that shows or hides the mask marks (2026-10-24).

On = the mask icon in the theme's blue, as the Image ribbon's mask toggle; off = the crossed-out mask icon, dimmed. The marks themselves are drawn by ``SensorgramPlot``:
a triangle where a persistent mask touched the ROIs of a line, a circle where an individual mask did. Display only.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ..image.general_group import style_general_icon_button
from ..ribbon_icon_size import ICON_SIZE

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
TIP = (
    "Mark the points an ignore mask touched. A triangle: a persistent mask removed pixels of the ROIs of that line at that cube. "
    "A circle: an individual mask did. Read from the scan, so a ROI the mask never reached gets no mark."
)


class MaskMarksToggle(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setToolTip(TIP)
        self.toggled.connect(lambda _checked: self._refresh())
        self.refresh_theme()

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._refresh()

    def _refresh(self) -> None:
        on = self.isChecked()
        theme = get_active_theme()
        name, color = ("mask", theme.accent_blue) if on else ("mask-off", theme.text_dim)  # as the Image ribbon's mask toggle
        self.setIcon(load_tabler_icon(name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
