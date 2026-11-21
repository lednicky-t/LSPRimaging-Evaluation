"""The Image panel's **View ribbon**: the View controls as a column of icon buttons right of the image (2026-11-21, maintainer request:
"move the View tab out of the image ribbon into a ribbon of its own, like the left one, on the right side"; Edit -> Preferences chooses which of the two shows).

Screen purpose: reach every display option (show / hide and colour of the overlays, the display range, the scale bar, ...) from any ribbon tab, without
opening the View tab.
Main user actions: the buttons are the View tab's own widgets, not copies (`ViewTab.set_vertical`), so a click does what it did on the tab: **an
overlay's icon is its show / hide switch (a left click), and a right-click on it opens a menu with the rest of its controls** (`right_click_menu.py`,
`view_tab.py`). In the way of the selection toolbar on the other side of the image, the menu opens beside the button, to the left (the column is at the
edge of the image, so the menu opens over the image and covers none of the column's other buttons).
Layout:
- Left panel: the image (the selection toolbar is left of it).
- Right panel: this column, `BUTTON_SIZE` wide, in sections divided by thin horizontal lines, top to bottom: Image (cursor, AR / MR, "..."), All
  (show all / hide all), Masks (histogram highlight, mask), Chromatic (landmarks) and BG, ROIs (sample, reference, labels) and Groups, Scale bar
  (show, units, colour), Chunks (only with an OME-Zarr). It scrolls (arrows at its ends) when the panel is shorter than the column
  (`scrolling_column.py`).
- Bottom / status area: unchanged.
Important states: the column is in the layout only while Preferences says "right"; then the ribbon's View tab is gone. The state of every control is
the one it already has (they are remembered, `bind`).
Validation / error messages: none of its own.

The moving between tab and column is `view_placement.py`; this file is only the column.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget

from ..ribbon_icon_size import BUTTON_SIZE
from ..scrolling_column import ScrollingColumn
from .view_tab import ViewTab

_WIDTH = BUTTON_SIZE + 2  # a pixel of room each side of the 24 px buttons


class ViewRibbon(QFrame):
    """The column. It holds the `ViewTab` while the controls are on the right (`hold` / `release`, called by `ViewPlacement`)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("viewRibbon")
        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 4)
        self._content_layout.setSpacing(0)
        self._column = ScrollingColumn(self._content, self)  # 24 px buttons, ~17 of them: scrolls when the panel is short
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._column, 1)
        self.setFixedWidth(_WIDTH)
        self.hide()
        self.refresh_theme()

    @property
    def column(self) -> ScrollingColumn:
        return self._column

    def hold(self, view_tab: ViewTab) -> None:
        """Take the View controls into the column (as a column: their menus now open beside the buttons) and show it."""
        view_tab.set_vertical(True)
        self._content_layout.addWidget(view_tab)
        self.show()

    def release(self, view_tab: ViewTab) -> None:
        """Let the View controls go (they go back to the ribbon's tab, as a row) and hide the column."""
        self._content_layout.removeWidget(view_tab)
        view_tab.set_vertical(False)
        self.hide()

    def refresh_theme(self) -> None:
        self._column.refresh_theme()
        self.setStyleSheet("#viewRibbon { border: none; }")
