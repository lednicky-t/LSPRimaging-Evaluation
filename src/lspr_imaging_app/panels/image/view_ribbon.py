"""The Image panel's **View ribbon**: the View controls as a column of icon buttons beside the image (2026-11-21, maintainer request: "move the View tab
out of the image ribbon into a ribbon of its own, like the left one"). Since 2026-11-22 it is the only place the View controls are: the View tab of the top
ribbon and the Preferences choice between the two are gone.

Screen purpose: reach every display option (show / hide and colour of the overlays, the display range, the scale bar, ...) from any ribbon tab.
Main user actions: the buttons are the `ViewTab`'s widgets: **an overlay's icon is its show / hide switch (a left click),
and a right-click on it opens a menu with the rest of its controls** (`right_click_menu.py`, `view_tab.py`). The menu opens beside the button, over
the image, whichever side the column stands on. The column can be docked on either side of the image, and rolled up, by its grip (`side_rails.py`).
Layout:
- Left or right panel: this column, `BUTTON_SIZE` wide, in sections divided by thin horizontal lines, top to bottom: All (show all / hide all), Image
  (cursor, scale bar, AR / MR, "..."), Masks (histogram highlight, mask, landmarks, Background), ROIs (sample, reference, labels) and Groups, Chunks
  (greyed out without an OME-Zarr). It scrolls (arrows at its ends) when the panel is shorter than the column (`scrolling_column.py`).
- Bottom / status area: unchanged.
Important states: the state of every control is the one it already has (they are remembered, `bind`).
Validation / error messages: none of its own.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ..ribbon_icon_size import BUTTON_SIZE
from ..scrolling_column import ScrollingColumn
from .view_tab import ViewTab

_WIDTH = BUTTON_SIZE + 2  # a pixel of room each side of the 24 px buttons


class ViewRibbon(QFrame):
    """The column: it holds the `ViewTab` (itself a column of icon buttons) for as long as the panel lives."""

    def __init__(self, view_tab: ViewTab, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("viewRibbon")
        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 4)
        self._content_layout.setSpacing(0)
        self.view_tab = view_tab
        self._content_layout.addWidget(view_tab)
        self._column = ScrollingColumn(self._content, self)  # 24 px buttons, ~17 of them: scrolls when the panel is short
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._column, 1)
        self.setFixedWidth(_WIDTH)
        self.refresh_theme()

    @property
    def column(self) -> ScrollingColumn:
        return self._column

    def refresh_theme(self) -> None:
        self._column.refresh_theme()
        self.setStyleSheet("#viewRibbon { border: none; }")
