"""Linking the text sizes of the legend, the step names and the step stats numbers (2026-11-13). Display only: sizes of text, nothing of the analysis.

Screen purpose: the stats numbers line up with the legend's rows and the step names sit on the same bar, so they usually look best at one size. A **link** button (a chain) beside
the text-size box of each of the three menus (Legend "Text", Steps "Size", Stats "Text size") makes the three sizes one: while it is on, changing any box changes the others. Off,
they are as independent as ever (the default). (First built for two menus, the legend and the stats; the Steps menu joined the same day, maintainer.)

Rules:
- Turning the link on gives every box the size of the box in the menu where the button was pressed (the one the user is looking at).
- While it is on, the boxes have the same range (the narrowest of the ranges, so that "the same size" is always possible; today all are 6 - 24 pt). The ranges come back when it is off.
- The buttons show one state: pressing any of them presses all. It is remembered (`sensorgram/text_size_linked`, bound to the Legend menu's button by `SensorgramViewControls`).
- Restoring the remembered state is a pair of calls around the menus' restores: `begin_restore()` (the buttons still follow each other, but no size is copied) and `end_restore()`
  (then, if linked, the sizes are made equal, the first menu's - the Legend's - winning). So a saved file whose sizes disagree cannot show a linked set that differs, and which size
  wins does not depend on the order in which the menus happen to restore.

`make_link_toggle` makes the button (it is built by each menu, the link by the panel, which owns all of them); `TextSizeLink` holds the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSpinBox, QWidget

from lspr_ui import load_tabler_icon

from ..spectra.view_menu import IconToggle

LINK_TIP = (
    "Link the text sizes of the legend, the step names and the stats numbers: while this is on, changing any of the sizes changes all of them, so the three stay the same size. "
    "Turning it on gives all of them the size of the menu you are in."
)
LINK_KEY = "sensorgram/text_size_linked"


def link_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A chain in the accent colour when the sizes are linked; a broken chain, dimmed, when they are not."""
    return load_tabler_icon("link" if on else "link-off", color=on_color if on else off_color, size=24)


def make_link_toggle(parent: QWidget) -> IconToggle:
    """The link button of one menu (not pressed at the start; `TextSizeLink` joins it to the other menus')."""
    toggle = IconToggle(link_icon, LINK_TIP, parent)
    toggle.setObjectName("text_size_link")
    return toggle


class TextSizeLink(QObject):
    def __init__(self, members: Sequence[tuple[QSpinBox, IconToggle]], parent: QObject | None = None) -> None:
        """`members`: (size box, link button) of each menu, the Legend's first (its size wins when a restored set disagrees)."""
        super().__init__(parent)
        self._boxes = tuple(box for box, _toggle in members)
        self._toggles = tuple(toggle for _box, toggle in members)
        self._ranges = [(box.minimum(), box.maximum()) for box in self._boxes]  # as the menus made them, put back when the link goes
        self._busy = False
        self._restoring = False  # between `begin_restore` and `end_restore`: sizes are not copied
        for index in range(len(members)):
            self._toggles[index].toggled.connect(lambda on, source=index: self._on_toggled(source, on))
            self._boxes[index].valueChanged.connect(lambda value, source=index: self._on_value(source, value))

    def is_linked(self) -> bool:
        return self._toggles[0].isChecked()

    def begin_restore(self) -> None:
        """The remembered state is about to be restored into the menus: until `end_restore` the buttons still follow each other but no size is copied."""
        self._restoring = True

    def end_restore(self) -> None:
        """All the menus are restored: if linked, the sizes are made equal (the first menu's, the Legend's, wins)."""
        self._restoring = False
        self.sync()

    def sync(self) -> None:
        """Make the sizes equal if linked. The first menu's (the Legend's) size wins."""
        if self.is_linked() and not self._busy:
            self._busy = True
            try:
                self._apply_ranges(True)
                for box in self._boxes[1:]:
                    box.setValue(self._boxes[0].value())
            finally:
                self._busy = False

    def _on_toggled(self, source: int, on: bool) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            for index, toggle in enumerate(self._toggles):
                if index != source:
                    toggle.setChecked(on)  # one state in every menu
            self._apply_ranges(on)
            if on and not self._restoring:
                for index, box in enumerate(self._boxes):
                    if index != source:
                        box.setValue(self._boxes[source].value())  # the menu you pressed it in decides
        finally:
            self._busy = False

    def _on_value(self, source: int, value: int) -> None:
        if self._busy or self._restoring or not self.is_linked():
            return
        self._busy = True
        try:
            for index, box in enumerate(self._boxes):
                if index != source:
                    box.setValue(value)
        finally:
            self._busy = False

    def _apply_ranges(self, linked: bool) -> None:
        """Linked: every box takes the narrowest of the ranges; not linked: each its own again."""
        if linked:
            low, high = max(r[0] for r in self._ranges), min(r[1] for r in self._ranges)
            for box in self._boxes:
                box.setRange(low, high)
        else:
            for box, (low, high) in zip(self._boxes, self._ranges):
                box.setRange(low, high)
