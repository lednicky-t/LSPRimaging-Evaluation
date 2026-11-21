"""Where the Image panel's View controls sit: the ribbon's View tab, or the side ribbon right of the image (2026-11-21; Edit -> Preferences ->
"View controls").

One `ViewTab` object holds the controls; this class moves it. `TAB` (the default, what the app always did): it is the page of the tool ribbon's "View"
tab, inside `host`. `RIGHT`: it is a column in a `ViewRibbon` (`view_ribbon.py`) beside the image and the tool ribbon's View tab is hidden
(`ImageToolRibbon.set_category_visible`). The change is made at once and is remembered by the panel (`ImagePanel.set_view_placement`); nothing here
touches a control's state, a data value or a result.

`view_controls_open()` is what the Histogram asks (it narrows its plot to the area selection only while the View controls are in front of the person,
maintainer's spec 2026-10-03: "the View tab is the open one"). With the side ribbon they are always in front, so it says yes then.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from .tool_ribbon import VIEW_TAB, ImageToolRibbon
from .view_ribbon import ViewRibbon
from .view_tab import ViewTab

TAB = "tab"
RIGHT = "right"
PLACEMENTS = (TAB, RIGHT)


def valid_placement(value: object) -> str:
    """`value` if it is one of the two placements, else the default (a settings file a person may have edited)."""
    return value if value in PLACEMENTS else TAB  # type: ignore[return-value]


class ViewPlacement(QObject):
    changed = pyqtSignal(str)  # the new placement, after the controls were moved

    def __init__(self, view_tab: ViewTab, parent: QWidget) -> None:
        super().__init__(parent)
        self._view_tab = view_tab
        self._where = TAB
        self._tool_ribbon: ImageToolRibbon | None = None
        self.host = QWidget(parent)  # the page of the tool ribbon's View tab
        self._host_layout = QHBoxLayout(self.host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.addWidget(view_tab)
        self.ribbon = ViewRibbon(parent)  # added beside the image by the panel; hidden until `RIGHT`

    def bind_tool_ribbon(self, tool_ribbon: ImageToolRibbon) -> None:
        """The tool ribbon whose View tab goes away while the controls are on the right (it is built after this object)."""
        self._tool_ribbon = tool_ribbon
        tool_ribbon.set_category_visible(VIEW_TAB, self._where == TAB)

    def where(self) -> str:
        return self._where

    def set_where(self, where: str) -> bool:
        """Put the View controls in `where`. Returns False (and does nothing) when they are there already."""
        where = valid_placement(where)
        if where == self._where:
            return False
        if where == RIGHT:
            self._host_layout.removeWidget(self._view_tab)
            self.ribbon.hold(self._view_tab)
        else:
            self.ribbon.release(self._view_tab)
            self._host_layout.addWidget(self._view_tab)
        self._where = where
        if self._tool_ribbon is not None:
            self._tool_ribbon.set_category_visible(VIEW_TAB, where == TAB)
        self.changed.emit(where)
        return True

    def view_controls_open(self) -> bool:
        """True when the View controls are in front of the person: the side ribbon, or the View tab when it is the open one."""
        if self._where == RIGHT:
            return True
        return self._tool_ribbon is not None and self._tool_ribbon.current_category() == VIEW_TAB

    def refresh_theme(self) -> None:
        self.ribbon.refresh_theme()
