"""Linking the legend's text size and the step stats' text size (2026-11-13). Display only: two sizes of text, nothing of the analysis.

Screen purpose: the stats numbers line up with the legend's rows, so they usually look best at one size. A **link** button (a chain) beside the text-size box of both menus, the Legend
menu's "Text" and the Stats menu's "Text size", makes the two sizes one: while it is on, changing either box changes the other. Off, they are as independent as ever (the default).

Rules:
- Turning the link on gives both boxes the size of the box in the menu where the button was pressed (the one the user is looking at).
- While it is on, the two boxes have the same range (the narrower of the two ranges, so that "the same size" is always possible; today both are 6 - 24 pt). The ranges come back when it is off.
- The two buttons show one state: pressing either presses both. It is remembered (`sensorgram/text_size_linked`, bound to the Legend menu's button by `SensorgramViewControls`).
- Restoring the remembered state is a pair of calls around the two menus' restores: `begin_restore()` (the buttons still follow each other, but no size is copied) and `end_restore()`
  (then, if linked, the two sizes are made equal, the Legend menu's winning). So a saved file whose two sizes disagree cannot show a linked pair that differs, and which size wins does
  not depend on the order in which the menus happen to restore.

`make_link_toggle` makes the button (it is built by each menu, the link by the panel, which owns both); `TextSizeLink` holds the rule.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSpinBox, QWidget

from lspr_ui import load_tabler_icon

from ..spectra.view_menu import IconToggle

LINK_TIP = (
    "Link the text size of the legend and of the stats numbers: while this is on, changing either size changes both, so the numbers and the legend they line up with stay the same size. "
    "Turning it on gives both the size of the menu you are in."
)
LINK_KEY = "sensorgram/text_size_linked"


def link_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A chain in the accent colour when the sizes are linked; a broken chain, dimmed, when they are not."""
    return load_tabler_icon("link" if on else "link-off", color=on_color if on else off_color, size=24)


def make_link_toggle(parent: QWidget) -> IconToggle:
    """The link button of one menu (not pressed at the start; `TextSizeLink` joins it to the other menu's)."""
    toggle = IconToggle(link_icon, LINK_TIP, parent)
    toggle.setObjectName("text_size_link")
    return toggle


class TextSizeLink(QObject):
    def __init__(self, legend_box: QSpinBox, legend_toggle: IconToggle, stats_box: QSpinBox, stats_toggle: IconToggle, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._boxes = (legend_box, stats_box)
        self._toggles = (legend_toggle, stats_toggle)
        self._ranges = [(box.minimum(), box.maximum()) for box in self._boxes]  # as the menus made them, put back when the link goes
        self._busy = False
        self._restoring = False  # between `begin_restore` and `end_restore`: sizes are not copied
        for index in (0, 1):
            self._toggles[index].toggled.connect(lambda on, source=index: self._on_toggled(source, on))
            self._boxes[index].valueChanged.connect(lambda value, source=index: self._on_value(source, value))

    def is_linked(self) -> bool:
        return self._toggles[0].isChecked()

    def begin_restore(self) -> None:
        """The remembered state is about to be restored into the two menus: until `end_restore` the buttons still follow each other but no size is copied."""
        self._restoring = True

    def end_restore(self) -> None:
        """Both menus are restored: if linked, the two sizes are made equal (the Legend menu's wins)."""
        self._restoring = False
        self.sync()

    def sync(self) -> None:
        """Make the two sizes equal if linked. The Legend menu's size wins."""
        if self.is_linked() and not self._busy:
            self._busy = True
            try:
                self._apply_ranges(True)
                self._boxes[1].setValue(self._boxes[0].value())
            finally:
                self._busy = False

    def _on_toggled(self, source: int, on: bool) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            other = 1 - source
            self._toggles[other].setChecked(on)  # one state in both menus
            self._apply_ranges(on)
            if on and not self._restoring:
                self._boxes[other].setValue(self._boxes[source].value())  # the menu you pressed it in decides
        finally:
            self._busy = False

    def _on_value(self, source: int, value: int) -> None:
        if self._busy or self._restoring or not self.is_linked():
            return
        self._busy = True
        try:
            self._boxes[1 - source].setValue(value)
        finally:
            self._busy = False

    def _apply_ranges(self, linked: bool) -> None:
        """Linked: both boxes take the narrower of the two ranges; not linked: each its own again."""
        if linked:
            low, high = max(r[0] for r in self._ranges), min(r[1] for r in self._ranges)
            for box in self._boxes:
                box.setRange(low, high)
        else:
            for box, (low, high) in zip(self._boxes, self._ranges):
                box.setRange(low, high)
