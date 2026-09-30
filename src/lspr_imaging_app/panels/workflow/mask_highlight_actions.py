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

import numpy as np
from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule, MaskModule
from ...image_tools.preprocess import histogram_highlight_mask_to_raw
from ...selection import HighlightRangeModule

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

_BUTTON_SIZE = 28
_ICON_SIZE = 22
_RENDER_SIZE = _ICON_SIZE * 2  # rendered at 2x, scaled down - crisper than a native bitmap
_STROKE_WIDTH = 2.1
_ADD_COLOR = "#22c55e"  # the stable app's own literal for this action
_SUBTRACT_COLOR = "#ef4444"
_SCOPE_ACTIVE_COLOR = "#38bdf8"  # Crop/Measure's own "tool active" blue, reused for "scope selected"


def _action_button(parent: QWidget, icon_name: str, color: str, tooltip: str) -> QToolButton:
    button = QToolButton(parent)
    button.setAutoRaise(True)
    button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
    button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())
    button.setToolTip(tooltip)
    button.setIcon(load_tabler_icon(icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
    return button


def _scope_toggle_button(parent: QWidget, icon_name: str, tooltip: str) -> QToolButton:
    """Icon-only, no text label - same width-budget fix already applied to
    Transforms' rotation-fill control (`transforms_settings.py`'s own
    docstring: a text label there read clearly but pushed the row 20px past
    the Workflow panel's 320px budget). The tooltip carries the words a
    label would have."""
    button = QToolButton(parent)
    button.setCheckable(True)
    button.setAutoRaise(True)
    button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
    button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())
    button.setToolTip(tooltip)
    button.setIcon(load_tabler_icon(icon_name, color=get_active_theme().text_dim, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
    return button


class MaskHighlightActions(QWidget):
    """Persistent/Individual scope toggle + Add/Subtract-highlighted-pixels
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._geometry = geometry
        self._chromatic = chromatic
        self._dataset = dataset
        self._highlight_range = highlight_range
        # The last image ImagePanel actually rendered, and the exact frame
        # it belongs to - same "read from the one panel that already has
        # it" convention the Histogram panel uses (`image_rendered`'s own
        # `(cube_index, wavelength_nm)` payload, not `SelectionModule`
        # re-read fresh): `ImagePanel._current_wavelength()` snaps the
        # selected wavelength to the nearest one the current cube actually
        # has, so `SelectionModule.current_wavelength()` alone can disagree
        # with what is actually on screen - real bug caught by this file's
        # own individual-scope test, which failed when `_apply` used
        # `self._selection.current_wavelength()` directly.
        self._last_image: np.ndarray | None = None
        self._last_frame: tuple[int, float] | None = None

        # "stack-3" (a layered stack - this cube and every one after it) /
        # "focus-2" (one exact frame) - icon-only, see `_scope_toggle_button`.
        self._persistent_button = _scope_toggle_button(
            self, "stack-3", "Persistent scope: new mask edits apply to this cube and every cube after it."
        )
        self._persistent_button.setChecked(True)
        self._persistent_button.toggled.connect(self._refresh_scope_icons)

        self._individual_button = _scope_toggle_button(
            self, "focus-2", "Individual scope: new mask edits apply to this exact (cube, wavelength) frame only."
        )
        self._individual_button.toggled.connect(self._refresh_scope_icons)

        self._scope_group = QButtonGroup(self)
        self._scope_group.setExclusive(True)
        self._scope_group.addButton(self._persistent_button)
        self._scope_group.addButton(self._individual_button)

        self._add_button = _action_button(
            self, "square-rounded-plus", _ADD_COLOR, "Add the highlighted histogram pixels to the mask."
        )
        self._add_button.clicked.connect(lambda: self._apply(subtract=False))

        self._subtract_button = _action_button(
            self, "square-rounded-minus", _SUBTRACT_COLOR, "Remove the highlighted histogram pixels from the mask."
        )
        self._subtract_button.clicked.connect(lambda: self._apply(subtract=True))

        # One row: scope toggle pair, a little extra gap, then the two
        # actions - keeps this section's height to what Transforms' own row
        # already costs (see `transforms_settings.py`'s `_GROUP_GAP`
        # convention, matched here with a plain `addSpacing`).
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._persistent_button)
        layout.addWidget(self._individual_button)
        layout.addSpacing(6)
        layout.addWidget(self._add_button)
        layout.addWidget(self._subtract_button)
        layout.addStretch(1)

        self._refresh_scope_icons()
        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)
        self._highlight_range.range_changed.connect(self._refresh_enabled)
        self._refresh_enabled()

    def _on_image_rendered(self, image: np.ndarray, cube_index: int, wavelength_nm: float) -> None:
        self._last_image = image
        self._last_frame = (cube_index, wavelength_nm)
        self._refresh_enabled()

    def _on_image_cleared(self) -> None:
        self._last_image = None
        self._last_frame = None
        self._refresh_enabled()

    def _refresh_enabled(self, *_args: object) -> None:
        enabled = self._last_image is not None and self._highlight_range.current_range() is not None
        self._add_button.setEnabled(enabled)
        self._subtract_button.setEnabled(enabled)

    def _refresh_scope_icons(self, *_args: object) -> None:
        theme = get_active_theme()
        for button, icon_name in ((self._persistent_button, "stack-3"), (self._individual_button, "focus-2")):
            color = _SCOPE_ACTIVE_COLOR if button.isChecked() else theme.text_dim
            button.setIcon(load_tabler_icon(icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))

    def _current_scope(self) -> str:
        return "persistent" if self._persistent_button.isChecked() else "individual"

    def _resolve_base_mask(self, target_frame: tuple[int, float], raw_shape: tuple[int, int]) -> np.ndarray:
        """`apply_candidate`'s own docstring: the caller resolves `base_mask`
        - typically `resolve_mask_source(target_frame)`, warped into
        `target_frame`'s geometry via `ChromaticModule.warp_mask_between` if
        it came from a different frame. No existing change yet means an
        all-clear raw canvas, not an error - the common "first edit" case."""
        resolution = self._mask.resolve_mask_source(target_frame)
        if resolution is None:
            return np.zeros(raw_shape, dtype=bool)
        authored_frame, authored_mask, _scope = resolution
        if authored_frame == target_frame:
            return authored_mask
        return self._chromatic.warp_mask_between(authored_mask, authored_frame, target_frame)

    def _apply(self, *, subtract: bool) -> None:
        if self._last_image is None or self._last_frame is None:
            return
        highlight = self._highlight_range.current_range()
        if highlight is None:
            return
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            return
        min_value, max_value = highlight
        candidate = histogram_highlight_mask_to_raw(
            self._last_image, min_value, max_value, raw_shape, self._geometry.settings()
        )
        # The exact frame the candidate was built for - `self._last_frame`,
        # not `SelectionModule` re-read fresh (see `_last_frame`'s own
        # comment above for why those can disagree).
        target_frame = self._last_frame
        base_mask = self._resolve_base_mask(target_frame, raw_shape)
        self._mask.apply_candidate(
            base_mask, candidate, target_frame=target_frame, scope=self._current_scope(), subtract=subtract
        )
