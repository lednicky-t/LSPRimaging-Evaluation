"""The "Edit" group's five per-tool control panels (2026-10-02, maintainer
request) - one per `MaskEditTool`, swapped by `MaskEditToolPicker` via a
`QStackedWidget` (`panel.py`'s `_build_ui`). Reorganizes the stable app's
always-all-visible Mask section (Histogram masking / Relative threshold /
Local contrast / Morphology / Drawing, each its own always-shown row) into
"pick one tool, see only its controls" - the maintainer's own framing:
"this selection is first step in the editing... base on selection he will
get other tools like +/- icons... in the tool section will be toggle to
show the edits".

**What's real here and what isn't, honestly, not silently**:
- **Histogram selection**: fully wired - `HistogramSelectionEditPanel`
  builds its own `HistogramHighlightMaskEditor` (the same shared, widget-
  free logic `mask_highlight_actions.MaskHighlightActions` uses - see that
  module's own docstring), so this is a real second front door onto the
  same backend, not a copy. Its "live preview" is the already-built
  Histogram highlight overlay (`panel.py`'s `_update_highlight_overlay`) -
  no new overlay needed, since that overlay already shows exactly what
  Add/Subtract would act on, live, as of the earlier 2026-10-02 session.
- **Morphology**: fully wired - `MorphologyEditPanel`'s four operation
  buttons call the now-corrected `MaskModule.apply_morphology` (see that
  method's own docstring for the 2026-10-02 OR/AND-merge-vs-plain-replace
  correctness fix made alongside this file) directly against the resolved
  base mask (`mask_edit_common.resolve_mask_edit_base`). Each click commits
  immediately - no live preview before commit, since there is no drag/slider
  driving a continuous candidate the way the Histogram-range selection has;
  the four buttons are instant actions, matching the maintainer's own "4
  icons" framing (not a +/- pair).
- **Threshold / Local contrast**: settings-only. The two tuning spinboxes
  each panel shows are real and already push to `MaskModule.set_tool_
  settings` (the same fields `workflow/mask_settings.py`'s
  `MaskSettingsSection` already owns - both forms read/write the same
  `MaskSettings`, so editing either one updates the other). The Add/Subtract
  buttons are present but disabled, with a tooltip explaining why: both
  tools need a raw-plane reload plus `scipy` Gaussian filtering against the
  *raw* image (not the already-in-memory displayed one, unlike Histogram
  selection), explicitly flagged as "genuinely slow" and not-yet-worker-
  backed in `MaskModule`'s own module docstring ("Still not built"). Wiring
  these for real needs a background worker (this codebase's established
  `threading.Thread` pattern, never `QThreadPool` - see the zarr-crash
  invariant) plus a live candidate-preview overlay, each its own follow-up
  piece of work, not guessed at here.
- **Draw**: settings-only. The brush-size spinbox is real (same
  `MaskSettings.brush_size_px` field `MaskModule.paint_brush` already
  consumes). The Add/Erase mode toggle is plain local widget state, not yet
  connected to anything - there is no canvas brush *gesture* yet (no mouse-
  drag paint tool, unlike Rotate/Crop/Measure's `ActiveToolModule`-driven
  canvas tools). Building one is its own follow-up (a new canvas tool class
  alongside `rotate_line_tool.py`/`crop_tool.py`/`measure_line_tool.py`,
  wired into `ImagePanel`'s mouse-event dispatch) - `MaskModule.paint_brush`
  itself is already built and ready for it. The "maybe add pencil shape
  (circle/square)" idea from the maintainer's own spec isn't included either
  - `raster_tools.brush_stamp_bounds`/`apply_brush_stamp` only implement a
  circular stamp today, so a shape toggle would need a backend change too,
  not guessed at here.

Every panel keeps this ribbon's established sizing: 28px-tall icon buttons/
spinboxes (`make_compact_spinbox`, `_BUTTON_SIZE`), `transparent_icon_
button_stylesheet()` chrome, no inline text labels (tooltips only, matching
`CompactWedgeSlider`/the Mask tab's own icon rows) - these panels live inside
`ImageToolRibbon`'s fixed-height content row, same budget `MaskOverlayControls`
already fits inside.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QAbstractSpinBox, QButtonGroup, QDoubleSpinBox, QHBoxLayout, QSpinBox, QToolButton, QWidget

from lspr_ui import make_compact_spinbox

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ...image_tools.mask.model import MaskSettings
from ...selection import HighlightRangeModule
from ..mask_edit_common import ADD_COLOR, SUBTRACT_COLOR, action_button, disabled_safe_icon, resolve_mask_edit_base
from ..mask_highlight_editor import HistogramHighlightMaskEditor

if TYPE_CHECKING:
    # Deferred - see mask_highlight_actions.py's own identical note for why
    # a plain top-level import here would close a real import cycle.
    from .panel import ImagePanel

_DISABLED_TOOLTIP_SUFFIX = " Not wired yet - needs a background worker (see mask_edit_panels.py)."
_MORPHOLOGY_COLOR = "#f8fafc"  # GuiTheme.text_primary - 2026-10-02 maintainer request: white, since these are real, active actions
_DRAW_MODE_ACTIVE_COLOR = "#38bdf8"  # GuiTheme.accent_blue - matches MaskScopeToggle's own "selected" convention
_DRAW_MODE_INACTIVE_COLOR = "#8b95a3"  # GuiTheme.text_dim


def _narrow_spinbox(spinbox: QAbstractSpinBox, sample_text: str) -> None:
    """Narrows a `make_compact_spinbox`-prepared spinbox to fit
    `sample_text` (e.g. `"99 px"`) instead of the full numeric range -
    maintainer request, 2026-10-02: "make the boxes for the controls wide
    only as much as possible... there will be just 2 digit number". Without
    this, `make_compact_spinbox`'s own deferred auto-sizing reserves room
    for the *technical* max (e.g. `relative_profile_sigma_px`'s range goes
    to 2000, so "2000 px" worth of width) even though every realistic value
    here is 1-2 digits. Setting an explicit fixed width makes that deferred
    auto-sizing back off on its own (`_apply_content_based_minimum_width`
    only overrides a spinbox whose `minimumWidth()` is still at Qt's bare
    frame default - `setFixedWidth` already raises it past that threshold),
    so this must run after `make_compact_spinbox`, not before."""
    metrics = QFontMetrics(spinbox.font())
    spinbox.setFixedWidth(metrics.horizontalAdvance(sample_text) + 14)


def _push_tool_settings(mask: MaskModule, **overrides: object) -> None:
    """`MaskModule.set_tool_settings` takes every tunable at once (its own
    "first real owner, combined-Apply shape" docstring) - this reads the
    current settings and overrides just the caller's fields, the same
    "current + override" shape `workflow/mask_settings.py`'s own
    `_push_settings` uses, factored out since four panels in this file each
    only touch a subset."""
    current: MaskSettings = mask.settings()
    fields: dict[str, object] = {
        "histogram_min_value": current.histogram_min_value,
        "histogram_max_value": current.histogram_max_value,
        "relative_threshold_fraction": current.relative_threshold_fraction,
        "relative_profile_sigma_px": current.relative_profile_sigma_px,
        "local_contrast_sigma_px": current.local_contrast_sigma_px,
        "local_contrast_z_threshold": current.local_contrast_z_threshold,
        "morphology_radius_px": current.morphology_radius_px,
        "brush_size_px": current.brush_size_px,
    }
    fields.update(overrides)
    mask.set_tool_settings(**fields)


class HistogramSelectionEditPanel(QWidget):
    """Add/Subtract the Histogram panel's highlighted pixels - the Image
    panel's own front door onto `HistogramHighlightMaskEditor`. No scope
    toggle here (the Mask tab's own "State" group already shows one, shared
    across the whole tab, not per-tool)."""

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

        self._add_button = action_button(
            self, "square-rounded-plus", ADD_COLOR, "Add the highlighted histogram pixels to the mask."
        )
        self._add_button.clicked.connect(lambda: self._editor.apply(subtract=False))

        self._subtract_button = action_button(
            self, "square-rounded-minus", SUBTRACT_COLOR, "Remove the highlighted histogram pixels from the mask."
        )
        self._subtract_button.clicked.connect(lambda: self._editor.apply(subtract=True))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._add_button)
        layout.addWidget(self._subtract_button)
        layout.addStretch(1)

        image_panel.image_rendered.connect(self._refresh_enabled)
        image_panel.image_cleared.connect(self._refresh_enabled)
        highlight_range.range_changed.connect(self._refresh_enabled)
        self._refresh_enabled()

    def _refresh_enabled(self, *_args: object) -> None:
        enabled = self._editor.is_ready()
        self._add_button.setEnabled(enabled)
        self._subtract_button.setEnabled(enabled)


class _TwoSpinToolSettingsPanel(QWidget):
    """Shared shape for Threshold/Local-contrast: two tuning spinboxes
    (real, pushed to `MaskModule`) plus a disabled Add/Subtract pair (not
    wired yet - see module docstring). Subclassed rather than parameterized
    with a dict of field specs - each subclass's spinbox ranges/suffixes
    differ enough (a percentage vs. a plain float vs. a pixel count) that a
    generic field-spec table would need as much code as just writing the
    two spinboxes out."""

    def __init__(self, mask: MaskModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._syncing = False

        self._spin_a, self._spin_b = self._build_spinboxes()
        self._add_button = action_button(
            self, "square-rounded-plus", ADD_COLOR, self._add_tooltip(),
            enabled=False, disabled_tooltip_suffix=_DISABLED_TOOLTIP_SUFFIX,
        )
        self._subtract_button = action_button(
            self, "square-rounded-minus", SUBTRACT_COLOR, self._subtract_tooltip(),
            enabled=False, disabled_tooltip_suffix=_DISABLED_TOOLTIP_SUFFIX,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._spin_a)
        layout.addWidget(self._spin_b)
        layout.addSpacing(4)
        layout.addWidget(self._add_button)
        layout.addWidget(self._subtract_button)
        layout.addStretch(1)

        self._sync_from_settings(mask.settings())
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)

    # -- subclass hooks ---------------------------------------------------

    def _build_spinboxes(self) -> tuple[QWidget, QWidget]:
        raise NotImplementedError

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        raise NotImplementedError

    def _push_settings(self) -> None:
        raise NotImplementedError

    def _add_tooltip(self) -> str:
        raise NotImplementedError

    def _subtract_tooltip(self) -> str:
        raise NotImplementedError

    # -- shared -------------------------------------------------------------

    def _on_model_changed(self, _change: object) -> None:
        self._sync_from_settings(self._mask.settings())

    def _on_spin_changed(self, *_args: object) -> None:
        if self._syncing:
            return
        self._push_settings()


class ThresholdEditPanel(_TwoSpinToolSettingsPanel):
    """Relative threshold (%) + profile sigma (px) - same two fields
    `MaskSettingsSection` already owns, `create_relative_contrast_mask`'s
    own two parameters."""

    def _build_spinboxes(self) -> tuple[QWidget, QWidget]:
        self._threshold_spin = make_compact_spinbox(QDoubleSpinBox(self))
        self._threshold_spin.setRange(0.1, 500.0)
        self._threshold_spin.setDecimals(1)
        self._threshold_spin.setSingleStep(1.0)
        self._threshold_spin.setSuffix(" %")
        self._threshold_spin.setToolTip("Relative threshold - how far a pixel must deviate from its local background.")
        self._threshold_spin.valueChanged.connect(self._on_spin_changed)
        _narrow_spinbox(self._threshold_spin, "99.9 %")

        self._sigma_spin = make_compact_spinbox(QSpinBox(self))
        self._sigma_spin.setRange(3, 2000)
        self._sigma_spin.setSuffix(" px")
        self._sigma_spin.setKeyboardTracking(False)
        self._sigma_spin.setToolTip("Relative profile sigma - the local-background blur radius.")
        self._sigma_spin.valueChanged.connect(self._on_spin_changed)
        _narrow_spinbox(self._sigma_spin, "99 px")
        return self._threshold_spin, self._sigma_spin

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        self._syncing = True
        try:
            self._threshold_spin.setValue(settings.relative_threshold_fraction * 100.0)
            self._sigma_spin.setValue(int(round(settings.relative_profile_sigma_px)))
        finally:
            self._syncing = False

    def _push_settings(self) -> None:
        _push_tool_settings(
            self._mask,
            relative_threshold_fraction=self._threshold_spin.value() / 100.0,
            relative_profile_sigma_px=float(self._sigma_spin.value()),
        )

    def _add_tooltip(self) -> str:
        return "Add pixels over the relative threshold to the mask."

    def _subtract_tooltip(self) -> str:
        return "Remove pixels over the relative threshold from the mask."


class LocalContrastEditPanel(_TwoSpinToolSettingsPanel):
    """Local contrast sigma (px) + z-threshold - `create_local_contrast_
    mask`'s own two parameters."""

    def _build_spinboxes(self) -> tuple[QWidget, QWidget]:
        self._sigma_spin = make_compact_spinbox(QSpinBox(self))
        self._sigma_spin.setRange(1, 2000)
        self._sigma_spin.setSuffix(" px")
        self._sigma_spin.setKeyboardTracking(False)
        self._sigma_spin.setToolTip("Local contrast sigma - the local-neighborhood blur radius.")
        self._sigma_spin.valueChanged.connect(self._on_spin_changed)
        _narrow_spinbox(self._sigma_spin, "99 px")

        self._z_spin = make_compact_spinbox(QDoubleSpinBox(self))
        self._z_spin.setRange(0.1, 20.0)
        self._z_spin.setDecimals(1)
        self._z_spin.setSingleStep(0.1)
        self._z_spin.setToolTip("Local contrast z-threshold - how many local standard deviations count as an outlier.")
        self._z_spin.valueChanged.connect(self._on_spin_changed)
        _narrow_spinbox(self._z_spin, "19.9")
        return self._sigma_spin, self._z_spin

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        self._syncing = True
        try:
            self._sigma_spin.setValue(int(round(settings.local_contrast_sigma_px)))
            self._z_spin.setValue(settings.local_contrast_z_threshold)
        finally:
            self._syncing = False

    def _push_settings(self) -> None:
        _push_tool_settings(
            self._mask,
            local_contrast_sigma_px=float(self._sigma_spin.value()),
            local_contrast_z_threshold=self._z_spin.value(),
        )

    def _add_tooltip(self) -> str:
        return "Add local-contrast outlier pixels to the mask."

    def _subtract_tooltip(self) -> str:
        return "Remove local-contrast outlier pixels from the mask."


_MORPHOLOGY_OPERATIONS: tuple[tuple[str, str, str], ...] = (
    # (operation, icon name, tooltip) - icon names are plain vendored
    # Tabler shapes. "erode"/"dilate" read fine as arrows, matching
    # Tabler's own icon-naming for size-change actions. "open"/"close"
    # copied from the stable app's own choice (`main_window_icons.py`'s
    # `_make_mask_morphology_icon`, 2026-10-02 maintainer request: "copy
    # the icons from the stable app (open book, closed book)") - a pun on
    # the word, not a literal morphology-math glyph, but it's what the
    # maintainer already knows from the stable app.
    ("erode", "arrows-minimize", "Erode: shrink the mask by this radius."),
    ("dilate", "arrows-maximize", "Dilate: grow the mask by this radius."),
    ("open", "book", "Open: erode then dilate - removes small isolated specks."),
    ("close", "book-2", "Close: dilate then erode - fills small gaps and notches."),
)


class MorphologyEditPanel(QWidget):
    """Radius (px) + four operation buttons (erode/dilate/open/close), each
    an instant action against the mask already in effect at the current
    frame - see module docstring for why there's no +/- pair here."""

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._chromatic = chromatic
        self._dataset = dataset
        self._mask_scope = mask_scope
        self._image_panel = image_panel
        self._syncing = False
        self._last_frame: tuple[int, float] | None = None

        self._radius_spin = make_compact_spinbox(QSpinBox(self))
        self._radius_spin.setRange(1, 100)
        self._radius_spin.setSuffix(" px")
        self._radius_spin.setToolTip("Morphology radius.")
        self._radius_spin.valueChanged.connect(self._on_radius_changed)
        _narrow_spinbox(self._radius_spin, "99 px")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._radius_spin)
        layout.addSpacing(4)

        self._operation_buttons: dict[str, QToolButton] = {}
        for operation, icon_name, tooltip in _MORPHOLOGY_OPERATIONS:
            button = action_button(self, icon_name, _MORPHOLOGY_COLOR, tooltip)
            button.clicked.connect(lambda _checked=False, op=operation: self._apply(op))
            self._operation_buttons[operation] = button
            layout.addWidget(button)
        layout.addStretch(1)

        self._sync_from_settings(mask.settings())
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)

        image_panel.image_rendered.connect(self._on_image_rendered)
        image_panel.image_cleared.connect(self._on_image_cleared)
        self._refresh_enabled()

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        self._syncing = True
        try:
            self._radius_spin.setValue(settings.morphology_radius_px)
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: object) -> None:
        self._sync_from_settings(self._mask.settings())

    def _on_radius_changed(self, _value: int) -> None:
        if self._syncing:
            return
        _push_tool_settings(self._mask, morphology_radius_px=self._radius_spin.value())

    def _on_image_rendered(self, _image: object, cube_index: int, wavelength_nm: float) -> None:
        self._last_frame = (cube_index, wavelength_nm)
        self._refresh_enabled()

    def _on_image_cleared(self) -> None:
        self._last_frame = None
        self._refresh_enabled()

    def _refresh_enabled(self) -> None:
        enabled = self._last_frame is not None
        for button in self._operation_buttons.values():
            button.setEnabled(enabled)

    def _apply(self, operation: str) -> None:
        if self._last_frame is None:
            return
        raw_shape = self._dataset.raw_plane_shape()
        if raw_shape is None:
            return
        base_mask = resolve_mask_edit_base(self._mask, self._chromatic, self._last_frame, raw_shape)
        self._mask.apply_morphology(
            base_mask,
            operation,
            self._radius_spin.value(),
            target_frame=self._last_frame,
            scope=self._mask_scope.scope().value,
            restrict_to=self._image_panel.selection_raw_mask(raw_shape),
        )


class DrawEditPanel(QWidget):
    """Brush size (real, synced to `MaskSettings.brush_size_px`) + an
    Add/Erase mode *selector* - UI-only, see module docstring for what
    isn't wired yet (no canvas paint gesture exists).

    **A selector, not a +/- action pair** (2026-10-02, maintainer
    correction of their own earlier framing: "pencil is either adding or
    removing... this is more like a selector compare to others") - unlike
    Histogram selection's Add/Subtract (two independent instant actions,
    either of which can fire), exactly one of these two is always the
    active paint mode, the same "pick one, see it highlighted" shape
    `MaskScopeToggle`'s Persistent/Individual pair already uses (and
    already was, structurally, here too - a mutually-exclusive
    `QButtonGroup` - this change is about the *coloring* matching that
    meaning, not the underlying behavior). One shared active color for
    whichever is checked (`_DRAW_MODE_ACTIVE_COLOR`) instead of a fixed
    blue-for-add/orange-for-erase regardless of selection, which read as
    "two actions" rather than "one choice"."""

    def __init__(self, mask: MaskModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask = mask
        self._syncing = False

        self._size_spin = make_compact_spinbox(QSpinBox(self))
        self._size_spin.setRange(1, 200)
        self._size_spin.setSuffix(" px")
        self._size_spin.setToolTip("Brush size.")
        self._size_spin.valueChanged.connect(self._on_size_changed)
        _narrow_spinbox(self._size_spin, "99 px")

        _no_canvas_gesture = " Not wired yet - no canvas drawing gesture exists (see mask_edit_panels.py)."
        self._add_mode_button = action_button(
            self, "square-rounded-plus", _DRAW_MODE_ACTIVE_COLOR, "Paint mode: add." + _no_canvas_gesture
        )
        self._erase_mode_button = action_button(
            self, "square-rounded-minus", _DRAW_MODE_INACTIVE_COLOR, "Paint mode: erase." + _no_canvas_gesture
        )
        for button in (self._add_mode_button, self._erase_mode_button):
            button.setCheckable(True)
            button.setEnabled(False)  # no canvas gesture to drive yet - see module docstring
        self._add_mode_button.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._add_mode_button)
        self._mode_group.addButton(self._erase_mode_button)
        self._add_mode_button.toggled.connect(self._refresh_mode_icons)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._size_spin)
        layout.addSpacing(4)
        layout.addWidget(self._add_mode_button)
        layout.addWidget(self._erase_mode_button)
        layout.addStretch(1)

        self._sync_from_settings(mask.settings())
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)

    def _refresh_mode_icons(self, add_checked: bool) -> None:
        self._add_mode_button.setIcon(
            disabled_safe_icon("square-rounded-plus", _DRAW_MODE_ACTIVE_COLOR if add_checked else _DRAW_MODE_INACTIVE_COLOR)
        )
        self._erase_mode_button.setIcon(
            disabled_safe_icon(
                "square-rounded-minus", _DRAW_MODE_INACTIVE_COLOR if add_checked else _DRAW_MODE_ACTIVE_COLOR
            )
        )

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        self._syncing = True
        try:
            self._size_spin.setValue(settings.brush_size_px)
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: object) -> None:
        self._sync_from_settings(self._mask.settings())

    def _on_size_changed(self, _value: int) -> None:
        if self._syncing:
            return
        _push_tool_settings(self._mask, brush_size_px=self._size_spin.value())
