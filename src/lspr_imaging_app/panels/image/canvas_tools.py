"""Shared look and size of the Image panel's top-bar icon buttons, and a flyout-group button.

History (2026-09-30): this module held the Image panel's own on-canvas tool bar, `CanvasToolsBar` (Select, Add ROI). The bar was taken off the
screen on 2026-10-07 (commit fa61b11) and **deleted 2026-11-24** with the Shape picker that only fed it and `ImageTool.ADD_ROI`: nothing could
turn the click-to-add tool on any more. A ROI is added from the right-click menu, Add ROI (`roi_creation.py`). Pan and zoom need no tool at all
(`image_controls.py`: middle-drag pans, wheel zooms, always).

What is left, still used elsewhere:

- **`style_bar_icon_button()`**: the one place the top bar's icon size and hover look live (maintainer, 2026-09-30: every icon sharing the row must
  match), used by `panel.py`'s cursor-readout and "i" icons, the Histogram plot and the ribbons. `_BUTTON_SIZE`, `_ICON_SIZE` and `_BAR_MARGIN` are
  the numbers other modules import.
- **`_ToolGroupButton` / `ToolVariant`**: a reusable flyout-group button (Photoshop style: click-and-hold or the small arrow shows the group's
  other members; Qt's `MenuButtonPopup`). It has no member on screen today; it is kept ready on the maintainer's explicit direction (2026-09-30)
  for a future multi-member tool group.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

_BUTTON_SIZE = 22
_ICON_SIZE = 16
_RENDER_SIZE = _ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # same blue as Crop/Measure - the maintainer's preferred tool color
_BAR_MARGIN = 2


def style_bar_icon_button(button: QToolButton, *, fixed_width: bool = True) -> None:
    """Applies this bar's shared icon-button look - size, hover chrome - to
    *button*. The single place that look lives (2026-09-30, maintainer
    request: "make cursor and i icon same as other icons in the bar...
    this apply for all icons later applied, they should have same style"),
    so `panel.py`'s cursor-readout/"i" icons, `_ToolGroupButton` below, and
    any future icon added to this row all go through one function instead
    of each copying the same four numbers and risking drift.

    `fixed_width=False` for a button that must grow to show live text
    (the cursor-readout toggle, whose label becomes e.g. "(38, 30) = 123.4"
    while enabled) - height and icon size still match every other button
    here, only the width stays free to grow."""
    button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
    button.setFixedHeight(_BUTTON_SIZE)
    if fixed_width:
        button.setFixedWidth(_BUTTON_SIZE)
    button.setAutoRaise(True)
    button.setStyleSheet(transparent_icon_button_stylesheet())


@dataclass(frozen=True)
class ToolVariant:
    """One flyout-group member: an icon, a tooltip, whether it is the
    currently-active tool, and what to do when picked."""

    icon_name: str
    tooltip: str
    is_active: Callable[[], bool]
    activate: Callable[[], None]


class _ToolGroupButton(QToolButton):
    """One toolbar slot. A single `ToolVariant` renders as a plain checkable
    icon button, identical in shape to the Transforms row's own buttons. More
    than one switches to `MenuButtonPopup` mode: the main click re-runs
    whichever variant was picked last (defaulting to the first), and the
    small arrow (or a press-and-hold) opens a dropdown of the others - the
    flyout mechanism, built once here rather than per-group."""

    def __init__(self, variants: Sequence[ToolVariant], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not variants:
            raise ValueError("_ToolGroupButton needs at least one variant")
        self._variants = tuple(variants)
        self._current = self._variants[0]

        self.setCheckable(True)
        style_bar_icon_button(self)
        self.clicked.connect(self._on_clicked)

        if len(self._variants) > 1:
            self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            menu = QMenu(self)
            for variant in self._variants:
                action = menu.addAction(
                    load_tabler_icon(variant.icon_name, color=get_active_theme().text_primary, size=_RENDER_SIZE),
                    variant.tooltip,
                )
                action.triggered.connect(lambda _checked=False, v=variant: self._select(v))
            self.setMenu(menu)

        self.refresh()

    def _on_clicked(self, _checked: bool = False) -> None:
        self._current.activate()

    def _select(self, variant: ToolVariant) -> None:
        self._current = variant
        variant.activate()
        self.refresh()

    def refresh(self) -> None:
        """Re-read every variant's `is_active()` - called after
        `ActiveToolModule` changes from any source (this button, the other
        toolbar, or a session restore), never assumed from the click that
        triggered it."""
        active_variant = next((v for v in self._variants if v.is_active()), None)
        if active_variant is not None:
            self._current = active_variant
        self.setChecked(active_variant is not None)
        self.setToolTip(self._current.tooltip)
        color = _ACTIVE_COLOR if self.isChecked() else get_active_theme().text_primary
        self.setIcon(load_tabler_icon(self._current.icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
