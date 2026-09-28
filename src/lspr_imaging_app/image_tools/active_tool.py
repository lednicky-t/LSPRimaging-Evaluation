"""Which Image Tools canvas tool is on right now (rotate / crop / measure).

One tiny shared object, so the Workflow panel's tool buttons and the Image
panel's click handling agree on the mode without either knowing about the
other - the rewrite's replacement for the stable app's
``window._active_tool`` string, which every part of the main window read and
wrote directly.

**At most one tool is active.** Activating a tool switches the previous one
off (and emits once, with the new state), which is what keeps e.g. the
rotate and crop buttons mutually exclusive without them referencing each
other.

**Deliberately not undoable, not persisted, and not a "change event".** It is
transient interaction state - like which widget has keyboard focus - not part
of the document. Ctrl+Z must not switch a tool off, and a saved session must
not reopen with the crop tool half-active. (Contrast ``GeometryModule``,
whose settings are all three of those things.)

Only the panels' *behavior* changes with the active tool; nothing computed
depends on it, so it never triggers analysis.
"""

from __future__ import annotations

import enum

from PyQt6.QtCore import QObject, pyqtSignal


class ImageTool(enum.Enum):
    ROTATE = "rotate"
    CROP = "crop"
    MEASURE = "measure"


class ActiveToolModule(QObject):
    """Holds the active `ImageTool` (or `None`) and announces changes."""

    # Emits the new active tool, or None when the last one was switched off.
    active_tool_changed = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active: ImageTool | None = None

    def active(self) -> ImageTool | None:
        return self._active

    def is_active(self, tool: ImageTool) -> bool:
        return self._active is tool

    def set_active(self, tool: ImageTool, active: bool) -> None:
        """Switch `tool` on or off. Switching a tool *off* only has an effect
        if it is the active one - a stale "off" from a button that was already
        replaced by another tool must not switch the new tool off."""
        if active:
            self._set(tool)
        elif self._active is tool:
            self._set(None)

    def clear(self) -> None:
        self._set(None)

    def _set(self, tool: ImageTool | None) -> None:
        if tool is self._active:
            return
        self._active = tool
        self.active_tool_changed.emit(tool)
