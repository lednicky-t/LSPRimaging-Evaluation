"""Linking the text sizes of the step names and the step stats numbers to the legend's (2026-11-13). Display only: sizes of text, nothing of the analysis.

Screen purpose: the stats numbers line up with the legend's rows and the step names sit on the same bar, so they usually look best at the legend's size. The **Steps** menu ("Size") and
the **Stats** menu ("Text size") each have a **link** button (a chain) beside their size box. Each button is its own switch: it links *that* menu's size to the **Legend's** "Text" size
(the Legend menu has no button; its size is the one the others follow). Off, the sizes are as independent as ever (the default). (Built first as one chain state shared by three menus;
the maintainer wanted two independent buttons, one in Steps and one in Stats, each linking to the Legend's size.)

Rules:
- Turning a button on gives that menu the Legend's size. The other menu's button is not touched.
- While linked, the two boxes show one size, whichever is changed (the linked menu's box moves the Legend's, and so also anything else linked to the Legend). Unlinked, they are independent.
- The Legend, and every linked menu, share one range (the narrowest of theirs, so that "the same size" is always possible; today all are 6 - 24 pt). Each box's own range comes back when it
  is unlinked.
- Each button is remembered on its own (`sensorgram/plan_text_size_linked`, `sensorgram/stats_text_size_linked`, bound by the two menus' `restore_ui_state`).
- Restoring the remembered state is a pair of calls around the menus' restores: `begin_restore()` (no size is copied) and `end_restore()` (then every linked menu takes the Legend's
  size). So a saved file whose sizes disagree cannot show a linked set that differs, and the Legend's size always wins, whatever the order in which the menus happen to restore.

`make_link_toggle` makes a button (each menu builds its own); `TextSizeLink` holds the rule and is made by the panel, which owns all three menus.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSpinBox, QWidget

from lspr_ui import load_tabler_icon

from ..spectra.view_menu import IconToggle

LINK_TIP = (
    "Link the size of {what} to the legend's text size: while this is on, it is always the legend's size, and changing either one changes the other. "
    "Turning it on gives it the legend's size."
)


def link_icon(on: bool, off_color: str, on_color: str) -> QIcon:
    """A chain in the accent colour when the size is linked; a broken chain, dimmed, when it is not."""
    return load_tabler_icon("link" if on else "link-off", color=on_color if on else off_color, size=24)


def make_link_toggle(parent: QWidget, what: str) -> IconToggle:
    """The link button of one menu (not pressed at the start; `TextSizeLink` joins its menu's size box to the legend's). `what` is for the tool tip: "the step names", "the stats numbers"."""
    toggle = IconToggle(link_icon, LINK_TIP.format(what=what), parent)
    toggle.setObjectName("text_size_link")
    return toggle


class TextSizeLink(QObject):
    def __init__(self, legend_box: QSpinBox, followers: Sequence[tuple[QSpinBox, IconToggle]], parent: QObject | None = None) -> None:
        """`legend_box`: the Legend menu's size box, the one the others follow. `followers`: (size box, link button) of each menu that can be linked to it."""
        super().__init__(parent)
        self._legend = legend_box
        self._boxes = tuple(box for box, _toggle in followers)
        self._toggles = tuple(toggle for _box, toggle in followers)
        self._legend_range = (legend_box.minimum(), legend_box.maximum())  # as the menus made them, put back when the link goes
        self._ranges = [(box.minimum(), box.maximum()) for box in self._boxes]
        self._busy = False
        self._restoring = False  # between `begin_restore` and `end_restore`: sizes are not copied
        for index in range(len(followers)):
            self._toggles[index].toggled.connect(lambda on, source=index: self._on_toggled(source, on))
            self._boxes[index].valueChanged.connect(lambda value, source=index: self._on_follower_value(source, value))
        legend_box.valueChanged.connect(self._on_legend_value)

    def is_linked(self, index: int) -> bool:
        """Is the `index`-th follower (in the order given) linked to the legend's size?"""
        return self._toggles[index].isChecked()

    def begin_restore(self) -> None:
        """The remembered state is about to be restored into the menus: until `end_restore` no size is copied."""
        self._restoring = True

    def end_restore(self) -> None:
        """All the menus are restored: every linked menu takes the legend's size."""
        self._restoring = False
        self.sync()

    def sync(self) -> None:
        """Give every linked menu the legend's size."""
        if self._busy:
            return
        self._busy = True
        try:
            self._apply_ranges()
            for box, toggle in zip(self._boxes, self._toggles):
                if toggle.isChecked():
                    box.setValue(self._legend.value())
        finally:
            self._busy = False

    def _on_toggled(self, source: int, on: bool) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._apply_ranges()
            if on and not self._restoring:
                self._boxes[source].setValue(self._legend.value())  # the legend's size decides
        finally:
            self._busy = False

    def _on_legend_value(self, value: int) -> None:
        self._spread(value, source=None)

    def _on_follower_value(self, source: int, value: int) -> None:
        if self._toggles[source].isChecked():  # an unlinked menu's size is its own
            self._spread(value, source=source)

    def _spread(self, value: int, source: int | None) -> None:
        """One of the linked sizes changed: the legend and every other linked menu take it."""
        if self._busy or self._restoring:
            return
        self._busy = True
        try:
            if source is not None:
                self._legend.setValue(value)
            for index, (box, toggle) in enumerate(zip(self._boxes, self._toggles)):
                if index != source and toggle.isChecked():
                    box.setValue(value)
        finally:
            self._busy = False

    def _apply_ranges(self) -> None:
        """The legend and every linked menu take the narrowest of their ranges; an unlinked menu has its own again."""
        members = [self._legend_range] + [r for r, toggle in zip(self._ranges, self._toggles) if toggle.isChecked()]
        low, high = max(r[0] for r in members), min(r[1] for r in members)
        self._legend.setRange(low, high)
        for box, toggle, (own_low, own_high) in zip(self._boxes, self._toggles, self._ranges):
            if toggle.isChecked():
                box.setRange(low, high)
            else:
                box.setRange(own_low, own_high)
