"""``StaleMarksToggle``: the Sensorgram's View-section button that shows or hides the out-of-date marks (2026-11-03).

On = the clock icon in the marks' grey; off = the same icon dimmed. The marks themselves are drawn by ``SensorgramPlot``: a hollow grey diamond on every point of a cube whose
scan results no longer match the app (a ROI moved or resized, a mask, the chromatic correction, the background or the geometry changed). Display only: the points and lines are
drawn either way, and the "Save data" table keeps its out-of-date column. The panel hands the button a line with the current count (`set_note`), because the status row is hidden
and a hidden mark would otherwise leave no sign at all.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ..icon_button_style import style_general_icon_button
from ..image.mark_colors import STALE_COLOR
from ..ribbon_icon_size import ICON_SIZE

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
TIP = (
    "Mark the points whose results are out of date: a grey diamond on every point of a cube where the scan no longer matches the app as it is now "
    "(a ROI was moved or resized, or a mask, the chromatic correction, the background or the geometry changed). Run the scan again to refresh them. "
    "A cube counts as out of date when any ROI of it is, so the diamonds are not limited to the lines of the ROI that changed. "
    "Hiding the marks does not hide any point."
)


class StaleMarksToggle(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self._note = ""
        self._refresh_tip()
        self.toggled.connect(lambda _checked: self._refresh())
        self.refresh_theme()

    def set_note(self, note: str) -> None:
        """The current state in words ("3 of 314 cubes out of date: ..."), added under the explanation in the tool tip; '' = nothing known."""
        if note != self._note:
            self._note = note
            self._refresh_tip()

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._refresh()

    def _refresh_tip(self) -> None:
        self.setToolTip(TIP if not self._note else f"{TIP}\n\nNow: {self._note}")

    def _refresh(self) -> None:
        color = STALE_COLOR if self.isChecked() else get_active_theme().text_dim
        self.setIcon(load_tabler_icon("clock", color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
