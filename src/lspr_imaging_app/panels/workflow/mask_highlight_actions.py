"""Image Tools stage - "Mask" section: histogram-highlight -> mask actions
(2026-09-30, maintainer request: "creating mask from histogram selection
(highlight)... two free standing icons one for adding selected pixels to
mask, other to remove").

Separate from `mask_settings.py`'s tuning form - that file's own docstring
already flagged this as "separate, larger work" needing `ChromaticModule`
coordination and the currently-displayed image, neither of which the
settings form has. This widget owns that orchestration (read the current
image/range/frame, call the pure math, call the command); the actual
raw-space remap math itself lives in `image_tools/preprocess.py`'s
`histogram_highlight_mask_to_raw` - AGENTS.md's module-boundary rule keeps
Mask and Geometry each independent, so the cross-module math can't live in
either one (the same reason `resolve_external_mask` lives there too).

**Persistent vs individual is a real per-click choice, not a fixed
default** (maintainer, 2026-09-30: "built in some toggle between canonical
edits (masks) vs individual, and base on what it is actually on it will do
the edit") - a two-way toggle selects the scope; +/- always act on
whichever is selected, at the current (cube, wavelength) frame.
`MaskModule.resolve_mask_source`'s own resolution order already treats a
persistent change as applying to its whole cube and every cube after it
(keyed by cube index only - see that method's docstring) - confirmed by
reading the code rather than assumed, matching the maintainer's stated
expectation exactly ("canonical should be whole cube and cubes onwards"),
so no `MaskModule` change was needed for that part.

**The toggle itself moved out to `MaskScopeModule`/`MaskScopeToggle`**
(2026-10-02, maintainer request: copy the Persistent/Individual icons into
the Image panel's own "Mask" tab too) - this widget used to own the toggle
as a private `QButtonGroup`; a second copy elsewhere could only have
duplicated that, not stayed in sync with it, which would have made "which
scope does Add/Subtract actually use" ambiguous. This widget now takes a
`MaskScopeModule` it shares with whoever else shows the toggle, and reads
`scope_module.scope()` instead of its own buttons' checked state - see
`image_tools/mask_scope.py`'s module docstring for the full reasoning.

**The apply/resolve logic itself moved out to `HistogramHighlightMaskEditor`**
(`panels/mask_highlight_editor.py`, 2026-10-02, maintainer request: add the
same Add/Subtract action to the Image panel's new "Edit" tool picker) - same
"one backend, several front doors" reasoning as the scope toggle above, one
level deeper: this widget is now just an Add/Subtract button pair plus the
scope toggle, both driving one shared, widget-free editor object a second
widget (`panels/image/mask_edit_panels.py`'s `HistogramSelectionEditPanel`)
also holds. See that module's own docstring for the full extraction story.

Cube-to-cube consistency for a persistent mask still relies on the
existing "chromatic models don't vary by cube" simplifying assumption
(`ChromaticModule`'s own docstring) - no per-cube sample-drift registration
exists yet, matching the architecture vision's "static per spectral
cube/time point" scope decision for this rewrite pass, not a gap
introduced here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QHBoxLayout, QWidget

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ...selection import HighlightRangeModule
from ..image.mask_scope_toggle import MaskScopeToggle
from ..mask_edit_common import ADD_COLOR, SUBTRACT_COLOR, action_button
from ..mask_highlight_editor import HistogramHighlightMaskEditor

if TYPE_CHECKING:
    # Deferred, not a plain import: `panels.image.panel` imports
    # `workflow.transforms_settings`, and this module is imported by
    # `workflow/panel.py` (itself pulled in whenever anything imports the
    # `workflow` package, via `workflow/__init__.py`) - a plain top-level
    # `from ..image.panel import ImagePanel` here closes that into a real
    # cycle: workflow package init -> workflow/panel.py -> this file ->
    # image/panel.py -> workflow.transforms_settings -> workflow package
    # init again, now mid-import. `ImagePanel` is only ever used here as a
    # type hint (duck-typed at runtime - `__init__` just connects to two
    # signals), and `from __future__ import annotations` already makes
    # every annotation in this file a string, so the class is never needed
    # at runtime, only by a type checker.
    from ..image.panel import ImagePanel

logger = logging.getLogger(__name__)


class MaskHighlightActions(QWidget):
    """Persistent/Individual scope toggle (`MaskScopeToggle`, shared state -
    see this file's module docstring) + Add/Subtract-highlighted-pixels
    buttons, acting on the currently-displayed image and the Histogram
    panel's highlight-range selection (`HighlightRangeModule`)."""

    def __init__(
        self,
        mask: MaskModule,
        geometry: GeometryModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        highlight_range: HighlightRangeModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = HistogramHighlightMaskEditor(
            mask, geometry, chromatic, dataset, highlight_range, image_panel, mask_scope
        )
        self._highlight_range = highlight_range

        self._scope_toggle = MaskScopeToggle(mask_scope, self)

        self._add_button = action_button(
            self, "square-rounded-plus", ADD_COLOR, "Add the highlighted histogram pixels to the mask."
        )
        self._add_button.clicked.connect(lambda: self._editor.apply(subtract=False))

        self._subtract_button = action_button(
            self, "square-rounded-minus", SUBTRACT_COLOR, "Remove the highlighted histogram pixels from the mask."
        )
        self._subtract_button.clicked.connect(lambda: self._editor.apply(subtract=True))

        # One row: scope toggle pair, a little extra gap, then the two
        # actions - keeps this section's height to what Transforms' own row
        # already costs (see `transforms_settings.py`'s `_GROUP_GAP`
        # convention, matched here with a plain `addSpacing`).
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._scope_toggle)
        layout.addSpacing(6)
        layout.addWidget(self._add_button)
        layout.addWidget(self._subtract_button)
        layout.addStretch(1)

        image_panel.image_rendered.connect(self._refresh_enabled)
        image_panel.image_cleared.connect(self._refresh_enabled)
        self._highlight_range.range_changed.connect(self._refresh_enabled)
        self._refresh_enabled()

    def _refresh_enabled(self, *_args: object) -> None:
        enabled = self._editor.is_ready()
        self._add_button.setEnabled(enabled)
        self._subtract_button.setEnabled(enabled)
