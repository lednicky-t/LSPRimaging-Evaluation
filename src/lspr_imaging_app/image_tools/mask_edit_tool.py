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

**2026-11-15: reduced to two tools.** The Histogram selection, Threshold, Local contrast, Morphology and Draw tools moved into the
selection toolbar (they make / modify a *selection* now: `panels/image/selection_by_value.py`, `selection_modify.py`, the brush;
record `docs/selection_lock_and_tool_move_2026-11-15.md`). What stays in the Mask tab's picker acts on the mask itself or is a
two-stage process: the Feature map and the Eraser. A saved choice of a removed tool is ignored at start-up (`ImagePanel.restore_ui_state`).

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
    # 2026-11-04: the Feature map (every small feature of the image, no ROIs; docs/roi_feature_detection_2026-10-29.md s.10-11). It
    # replaced the ROI features tool of 2026-10-29; the stored value stays "roi_features" so a remembered tool choice still loads.
    FEATURE_MAP = "roi_features"
    ERASER = "eraser"  # added 2026-10-29: a click on the image removes the connected blob of mask pixels under it


class MaskEditToolModule(QObject):
    """Holds the currently-picked `MaskEditTool` and announces changes. Defaults to `FEATURE_MAP` (the first entry of the picker)."""

    tool_changed = pyqtSignal(object)  # emits the new MaskEditTool

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tool = MaskEditTool.FEATURE_MAP

    def tool(self) -> MaskEditTool:
        return self._tool

    def set_tool(self, tool: MaskEditTool) -> None:
        if tool is self._tool:
            return
        self._tool = tool
        self.tool_changed.emit(tool)
