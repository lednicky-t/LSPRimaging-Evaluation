"""Which mask-editing tool is picked in the Image panel's "Mask" tab ->
"Edit" group (2026-10-02, maintainer request: "a dropdown menu but without
scroll feature... pickup menu" offering Histogram selection / Threshold /
Local contrast / Morphology / Draw - the five tool kinds the stable app
exposes as separate always-visible sections in its own Mask panel, here
picked one at a time instead).

**Owned privately by `ImagePanel` for now, not threaded through
`app_rewrite.py` like `MaskScopeModule`/`HighlightRangeModule`** - unlike
those two, nothing else needs to read or drive this selection yet: the
Workflow panel's own Mask section (`MaskSettingsSection`/
`MaskHighlightActions`) is untouched by this picker and keeps showing every
tool's settings stacked together, per the maintainer's explicit "maybe the
workflow panel would be removed later on... much better organized" - a
*maybe*, not a decision, so this rewrite doesn't yet assume the Workflow
panel's Mask section goes away. If the Workflow panel ever needs to mirror
which tool is picked, promote this module to `app_rewrite.py`-level
construction (same move already made for `MaskScopeModule`/
`HighlightRangeModule`) rather than letting a second copy exist.

Mirrors `mask_scope.py`'s `MaskScope`/`MaskScopeModule` shape exactly - same
"one tiny shared object" pattern, same "transient interaction state, not
undoable/persisted" category as `ActiveToolModule`/`MaskScopeModule` (see
either's own docstring): which tool is *picked* is not part of the document,
only an edit actually committed through one of `MaskModule`'s command
methods is.
"""

from __future__ import annotations

import enum

from PyQt6.QtCore import QObject, pyqtSignal


class MaskEditTool(enum.Enum):
    HISTOGRAM_SELECTION = "histogram_selection"
    THRESHOLD = "threshold"
    LOCAL_CONTRAST = "local_contrast"
    MORPHOLOGY = "morphology"
    DRAW = "draw"


class MaskEditToolModule(QObject):
    """Holds the currently-picked `MaskEditTool` and announces changes.
    Defaults to `HISTOGRAM_SELECTION` - the one tool kind with a fully wired
    Add/Subtract action (`HistogramHighlightMaskEditor`) at the time this was
    built, so the picker opens on something that actually works rather than
    on a tool whose panel is still settings-only."""

    tool_changed = pyqtSignal(object)  # emits the new MaskEditTool

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tool = MaskEditTool.HISTOGRAM_SELECTION

    def tool(self) -> MaskEditTool:
        return self._tool

    def set_tool(self, tool: MaskEditTool) -> None:
        if tool is self._tool:
            return
        self._tool = tool
        self.tool_changed.emit(tool)
