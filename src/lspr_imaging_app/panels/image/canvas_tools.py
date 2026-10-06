"""Image panel's own on-canvas tool bar (2026-09-30, flipped horizontal
2026-09-30 same day - see below).

A horizontal strip of icon buttons docked to the top edge of the Image
panel's canvas, on the left side of `panel.py`'s shared top bar (the
cursor-readout and "i" info icons live in the rest of that bar, not here -
see `panel.py`'s `_build_ui`) - deliberately separate from the Workflow
panel's Transforms row (`panels/image/transforms_settings.py`), per the
maintainer's own distinction: Rotate/Crop/Flip/Measure resample the actual
pixel grid (AGENTS.md's non-negotiable invariant on that), while the tools
that belong here are the "soft" ones - visualization/inspection and ROI
manipulation - that never touch pixel data. Pan and zoom need no tool or
icon at all (`image_controls.py`: middle-drag pans, wheel zooms, always).

**Flipped from a vertical left-edge strip to a horizontal top-edge one**
(2026-09-30, maintainer request - "since frames are usually landscapes"): a
left rail wastes more of a landscape frame's width than a top bar wastes of
its height. This widget only lays out its own two buttons horizontally now;
`panel.py`'s wrapping top bar is what actually borders the canvas (a single
bottom seam, not this widget's own edge) and owns the cursor/info icons
that sit to this widget's right.

**Still drives the same shared `ActiveToolModule`/`ImageTool` state machine
Rotate/Crop/Measure use** (see `active_tool.py`'s module docstring for why
one shared enum, not two) - this bar and the Transforms row are two front
doors onto one piece of state, the same "several front doors, one backend"
shape `RoiToolbox` itself already uses.

**Built on a reusable flyout-group button (`_ToolGroupButton`)**, even
though every group here has exactly one member today - the maintainer's
explicit direction (2026-09-30) was to have the grouping mechanism ready
now, Photoshop-style (click-and-hold, or the small arrow, reveals a group's
other members), rather than retrofitting it once a real multi-member group
(e.g. several ROI shapes, several mask brushes) actually exists. Qt's
native `QToolButton.ToolButtonPopupMode.MenuButtonPopup` is the mechanism -
idiomatic, keyboard-accessible, and testable via plain `QAction.trigger()`,
not a custom-painted popup.

Only "Select" and "Add ROI" exist today:

- **Select** - not really a "tool", the `ActiveToolModule` `None` state
  given its own icon so there is always an obvious way back to plain
  click-to-select-ROI/drag-to-move, from whichever tool (in either toolbar)
  happens to be active. Checked whenever no tool is active.
- **Add ROI** - the click-to-place gap `panels/image/panel.py`'s own
  docstring already flagged as a deliberate omission (`RoiToolbox.add_roi`
  is a real command; nothing called it). Stays active after placing one ROI
  so several can be placed in a row, matching Crop/Rotate's "stays on until
  explicitly exited" shape - exiting is the Select button, the tool's own
  right-click "Exit tool" menu entry, or clicking Add ROI again.

Where a click actually lands - the scene/view coordinate mapping, and the
`RoiToolbox.add_roi` call itself - stays in `panel.py`'s single gesture
dispatch table (`_on_scene_clicked`), exactly where Rotate/Crop/Measure's
own click handling already lives. This bar only ever toggles which tool is
active; it holds no canvas-interaction logic of its own.

**Sized to hug its icons** - `_BUTTON_SIZE`/`_ICON_SIZE` shrunk from the
first pass's `28`/`22` (borrowed from the Transforms row's own buttons,
sized for a horizontal row with room to spare) to `22`/`16`: this strip
should read as compact, not a second toolbar's worth of size. No border of
its own (dropped with the flip to horizontal, 2026-09-30) - it no longer
sits at the canvas's own edge, so `panel.py`'s wrapping top bar is what
draws the one seam that matters (its own bottom edge, against the canvas).

**`style_bar_icon_button()`** (2026-09-30, maintainer request - "make
cursor and i icon same as other icons in the bar... this apply for all
icons later applied") applies that same size/chrome to any `QToolButton`,
not just this bar's own two - `panel.py`'s cursor-readout and "i" icons go
through it too, so every icon sharing this row is guaranteed to match
rather than relying on each caller copying the same numbers correctly.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ...image_tools import ActiveToolModule, ImageTool
from ...roi import RoiToolbox

_BUTTON_SIZE = 22
_ICON_SIZE = 16
_RENDER_SIZE = _ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ADD_ROI_ACTIVE_COLOR = "#38bdf8"  # same blue as Crop/Measure - the maintainer's preferred tool color
_BAR_MARGIN = 2
_BAR_SPACING = 3


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
        color = _ADD_ROI_ACTIVE_COLOR if self.isChecked() else get_active_theme().text_primary
        self.setIcon(load_tabler_icon(self._current.icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))


class CanvasToolsBar(QWidget):
    """Vertical icon bar - Select, Add ROI, docked to the Image panel's
    canvas by `panel.py`."""

    def __init__(self, roi_toolbox: RoiToolbox, active_tool: ActiveToolModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._roi_toolbox = roi_toolbox
        self._active_tool = active_tool

        select_variant = ToolVariant(
            icon_name="pointer",
            tooltip="Select. Left-click an ROI to select it, drag to move it.",
            is_active=lambda: active_tool.active() is None,
            activate=active_tool.clear,
        )
        add_roi_variant = ToolVariant(
            icon_name="circle-plus",
            tooltip="Add ROI. Left-click the image to place one; stays on for placing several.",
            is_active=lambda: active_tool.active() is ImageTool.ADD_ROI,
            activate=lambda: active_tool.set_active(ImageTool.ADD_ROI, True),
        )

        self._select_button = _ToolGroupButton([select_variant], self)
        self._add_roi_button = _ToolGroupButton([add_roi_variant], self)

        # Horizontal, no trailing stretch (2026-09-30, flipped from a
        # vertical strip) - this widget now just hugs its two buttons
        # side by side; `panel.py`'s wrapping top bar is the one that
        # stretches to push the cursor/info icons to the far right.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(_BAR_MARGIN, _BAR_MARGIN, _BAR_MARGIN, _BAR_MARGIN)
        layout.setSpacing(_BAR_SPACING)
        layout.addWidget(self._select_button)
        layout.addWidget(self._add_roi_button)

        self.setObjectName("canvasToolsBar")
        self.refresh_theme(get_active_theme())

        active_tool.active_tool_changed.connect(self._on_active_tool_changed)

    def refresh_theme(self, theme: GuiTheme) -> None:
        """No border of its own (2026-09-30, flipped from a vertical strip -
        see module docstring) - it no longer sits directly against the
        canvas, so there's no seam here to draw. Still called at
        construction and on every live theme switch (`ImagePanel.
        refresh_theme`) in case a future revision needs to react to one."""
        self.setStyleSheet("#canvasToolsBar { border: none; }")

    def _on_active_tool_changed(self, _tool: object) -> None:
        self._select_button.refresh()
        self._add_roi_button.refresh()
