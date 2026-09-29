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

A tool that needs a drag gesture claims it for itself, in its own row of
`_TOOL_CONTROLS` - it does not re-enable the view's default drags. The Crop
tool (2026-09-29, `crop_tool.py`) is the first: `ImageViewBox.set_left_drag_
handler` lets it intercept left-button drags while it is active, everything
else still ignored exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable
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
        Control("Right-click", "menu with Cancel rotation - exits Rotate mode (Esc only cancels point 1)"),
        Control("Arrow keys", "rotate by 0.1 deg (Ctrl 1 deg, Shift 5 deg)"),
    ),
    ImageTool.CROP: (
        Control("Left-drag (empty area)", "draw a new crop rectangle"),
        Control("Left-drag (edge/corner)", "resize that side, or both sides at a corner"),
        Control("Left-drag (inside)", "move the rectangle"),
        Control("x: / y: fields", "type an exact width/height"),
        Control("checkmark", "apply the crop"),
        Control("Right-click", "menu with Apply crop / Cancel crop - Cancel exits Crop mode"),
        Control("Esc", "discard an unapplied resize/move, stay in Crop mode"),
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
    (pans), unless a tool has claimed the left button for itself (see
    `set_left_drag_handler`). Wheel zoom is inherited unchanged; clicks are
    untouched (they reach the scene's ``sigMouseClicked`` as before)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._left_drag_handler: Callable[[object], bool] | None = None

    def set_left_drag_handler(self, handler: Callable[[object], bool] | None) -> None:
        """*handler* gets every left-button drag event first and returns
        whether it claimed it. Returning `False` (or `None`/no handler set)
        falls back to the default "left drag does nothing" rule - a tool
        that isn't currently active just declines every event, rather than
        this class needing to know which tool, if any, is on."""
        self._left_drag_handler = handler

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802 - Qt/pyqtgraph naming
        if ev.button() == Qt.MouseButton.LeftButton and self._left_drag_handler is not None:
            if self._left_drag_handler(ev):
                ev.accept()
                return
        if ev.button() != Qt.MouseButton.MiddleButton:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis)
