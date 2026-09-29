"""The Image panel's mouse/keyboard controls - one table, one view box.

Single source of truth for "which input does what on the image", so the
help text shown to the user and the behavior actually wired cannot drift
apart (the stable app documents its controls in several unrelated places -
e.g. "Middle-drag: Pan" appears only under chromatic editing).

**Always available** (any tool, or none): middle-button drag pans, the wheel
zooms. **Left- and right-button drags do nothing** - pyqtgraph's defaults
(left-drag pans, right-drag zooms) are switched off in `ImageViewBox`, so a
slightly shaky click can never turn into an accidental pan, and each button
keeps exactly one meaning: clicks belong to the active tool (or ROI
selection), and panning is the middle button's job.

A tool that needs a drag gesture later (e.g. dragging a crop box) claims it
for itself, in its own row of `_TOOL_CONTROLS` - it does not re-enable the
view's default drags.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PyQt6.QtCore import Qt

from ...image_tools import ImageTool


@dataclass(frozen=True)
class Control:
    input: str
    action: str


_ALWAYS: tuple[Control, ...] = (
    Control("Middle-drag", "pan"),
    Control("Wheel", "zoom"),
)

_TOOL_CONTROLS: dict[ImageTool | None, tuple[Control, ...]] = {
    None: (Control("Left-click", "select the ROI under the cursor (Ctrl/Shift adds to the selection)"),),
    ImageTool.ROTATE: (
        Control("Left-click", "place point 1, then point 2 - the image rotates so the two points are level"),
        Control("Right-click", "menu with Cancel rotation (Esc also cancels point 1 directly)"),
        Control("Arrow keys", "rotate by 0.1 deg (Ctrl 1 deg, Shift 5 deg)"),
    ),
}


def controls_for(tool: ImageTool | None) -> tuple[Control, ...]:
    """The tool's own controls first, then the always-available ones. A tool
    with no row of its own falls back to the no-tool row (plain image)."""
    return _TOOL_CONTROLS.get(tool, _TOOL_CONTROLS[None]) + _ALWAYS


def controls_text(tool: ImageTool | None) -> str:
    return "  |  ".join(f"{c.input}: {c.action}" for c in controls_for(tool))


class ImageViewBox(pg.ViewBox):
    """`pg.ViewBox` with the drag rules above: only the middle button drags
    (pans). Wheel zoom is inherited unchanged; clicks are untouched (they
    reach the scene's ``sigMouseClicked`` as before)."""

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802 - Qt/pyqtgraph naming
        if ev.button() != Qt.MouseButton.MiddleButton:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis)
