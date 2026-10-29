"""The "Edit" group's seven per-tool control panels (five 2026-10-02, ROI features and Eraser 2026-10-29; maintainer
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
  immediately, matching the maintainer's own "4 icons" framing (not a +/-
  pair). **Preview (2026-10-29)**: resting the mouse on a button tints the
  pixels that operation would change (`panels/mask_edit_preview.py`), with
  the radius in the ribbon; the eye button at the left switches the preview.
- **Threshold / Local contrast**: the two tuning spinboxes each panel shows
  are real and push to `MaskModule.set_tool_settings` (the same fields
  `workflow/mask_settings.py`'s `MaskSettingsSection` already owns - both
  forms read/write the same `MaskSettings`, so editing either one updates the
  other). **Wired 2026-10-29**: Add/Subtract merge the pixels the tool selects
  (`panels/mask_candidate_editor.py`), and the same pixels are previewed on the
  image while the tool is picked (eye button). Both need the *raw* image plus
  `scipy` Gaussian filtering (0.2-4 s), so the candidate is made on a worker
  thread (`image_tools/mask_candidates.py`; `threading.Thread`, never
  `QThreadPool` - see the zarr-crash invariant) and shared by the preview and
  the buttons.
- **ROI features** (2026-10-29): `RoiFeaturesEditPanel`. Not a live preview but a Process button (3-16 s, worker processes, progress
  and Cancel in the status bar), a settings popup and an Apply button that adds the found pixels at the Persistent / Individual
  scope. Record: `docs/roi_feature_detection_2026-10-29.md`.
- **Eraser** (2026-10-29): `EraserEditPanel` is only a hint icon; the tool is a click on the image (`panels/mask_grain_eraser.py`,
  routed by `CanvasInteraction`), which removes the connected blob of mask pixels under it.
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

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from lspr_ui import get_active_theme, load_tabler_icon, make_compact_spinbox

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule, MaskModule, MaskScopeModule
from ...image_tools.mask.model import MaskSettings
from ...image_tools.mask.roi_feature_run import FeatureParams
from ...image_tools.roi_feature_task import FeatureSummary, RoiFeatureTask
from ...selection import HighlightRangeModule
from ..mask_candidate_editor import MaskCandidateEditor
from ..mask_edit_common import (
    ADD_COLOR,
    BUTTON_SIZE,
    ICON_SIZE,
    SUBTRACT_COLOR,
    action_button,
    disabled_safe_icon,
    resolve_mask_edit_base,
)
from ..mask_edit_preview import KIND_LOCAL_CONTRAST, KIND_ROI_FEATURES, KIND_THRESHOLD, MaskEditPreview
from ..mask_highlight_editor import HistogramHighlightMaskEditor
from ..ribbon_fields import style_ribbon_number_field
from .edit_preview_toggle import EditPreviewToggle

if TYPE_CHECKING:
    # Deferred - see mask_highlight_actions.py's own identical note for why
    # a plain top-level import here would close a real import cycle.
    from .panel import ImagePanel

_MORPHOLOGY_COLOR = "#f8fafc"  # GuiTheme.text_primary - 2026-10-02 maintainer request: white, since these are real, active actions
_DRAW_MODE_ACTIVE_COLOR = "#38bdf8"  # GuiTheme.accent_blue - matches MaskScopeToggle's own "selected" convention
_DRAW_MODE_INACTIVE_COLOR = "#8b95a3"  # GuiTheme.text_dim


_PAIR_BUTTON_WIDTH = ICON_SIZE + 2  # 1 px of air each side of the icon (the lone-icon buttons are BUTTON_SIZE wide)


def _add_icon_pair(layout: QHBoxLayout, first: QToolButton, second: QToolButton) -> None:
    """Adds a +/- (or +/erase) icon pair to `layout` as one tight unit:
    narrower buttons and no gap between them (2026-10-16, maintainer
    request: "reduce distance between icons plus and minus"). Centre to
    centre the pair went from 30 px (28 px buttons + 2 px layout spacing)
    to 24 px. A nested layout keeps the row's own spacing for everything
    else."""
    pair = QHBoxLayout()
    pair.setContentsMargins(0, 0, 0, 0)
    pair.setSpacing(0)
    for button in (first, second):
        button.setFixedWidth(_PAIR_BUTTON_WIDTH)
        pair.addWidget(button)
    layout.addLayout(pair)


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
        _add_icon_pair(layout, self._add_button, self._subtract_button)
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
    """Shared shape for Threshold/Local-contrast: an eye button (preview of
    the selected pixels on the image), two tuning spinboxes (pushed to
    `MaskModule`) and an Add/Subtract pair (2026-10-29: wired - they merge the
    same pixels the preview shows, computed on a worker thread; the buttons are
    off while that runs). Subclassed rather than parameterized
    with a dict of field specs - each subclass's spinbox ranges/suffixes
    differ enough (a percentage vs. a plain float vs. a pixel count) that a
    generic field-spec table would need as much code as just writing the
    two spinboxes out."""

    _kind = ""  # which candidate the buttons merge: `KIND_THRESHOLD` / `KIND_LOCAL_CONTRAST`

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        preview: MaskEditPreview,
        status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._syncing = False
        self._busy = False  # a candidate is being computed for a button press
        self._editor = MaskCandidateEditor(mask, chromatic, dataset, image_panel, mask_scope, preview, status)

        self._toggle = EditPreviewToggle(preview, self)
        self._spin_a, self._spin_b = self._build_spinboxes()
        self._add_button = action_button(self, "square-rounded-plus", ADD_COLOR, self._add_tooltip())
        self._add_button.clicked.connect(lambda: self._apply(subtract=False))
        self._subtract_button = action_button(self, "square-rounded-minus", SUBTRACT_COLOR, self._subtract_tooltip())
        self._subtract_button.clicked.connect(lambda: self._apply(subtract=True))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._spin_a)
        layout.addWidget(self._spin_b)
        layout.addSpacing(4)
        _add_icon_pair(layout, self._add_button, self._subtract_button)
        layout.addStretch(1)

        self._sync_from_settings(mask.settings())
        mask.cosmetic_changed.connect(self._on_model_changed)
        mask.mask_changed.connect(self._on_model_changed)
        image_panel.image_rendered.connect(self._refresh_enabled)
        image_panel.image_cleared.connect(self._refresh_enabled)
        self._refresh_enabled()

    def refresh_theme(self) -> None:
        self._toggle.refresh_theme()

    def _refresh_enabled(self, *_args: object) -> None:
        enabled = self._editor.is_ready() and not self._busy
        self._add_button.setEnabled(enabled)
        self._subtract_button.setEnabled(enabled)

    def _apply(self, *, subtract: bool) -> None:
        if self._busy:
            return
        self._busy = True
        self._refresh_enabled()
        self._editor.apply(self._kind, subtract=subtract, done=self._on_apply_done)

    def _on_apply_done(self) -> None:
        self._busy = False
        self._refresh_enabled()

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

    _kind = KIND_THRESHOLD

    def _build_spinboxes(self) -> tuple[QWidget, QWidget]:
        self._threshold_spin = make_compact_spinbox(QDoubleSpinBox(self))
        self._threshold_spin.setRange(0.1, 500.0)
        self._threshold_spin.setDecimals(1)
        self._threshold_spin.setSingleStep(1.0)
        self._threshold_spin.setSuffix(" %")
        self._threshold_spin.setToolTip("Relative threshold - how far a pixel must deviate from its local background.")
        self._threshold_spin.valueChanged.connect(self._on_spin_changed)
        style_ribbon_number_field(self._threshold_spin, "99.9 %")

        self._sigma_spin = make_compact_spinbox(QSpinBox(self))
        self._sigma_spin.setRange(3, 2000)
        self._sigma_spin.setSuffix(" px")
        self._sigma_spin.setKeyboardTracking(False)
        self._sigma_spin.setToolTip("Relative profile sigma - the local-background blur radius.")
        self._sigma_spin.valueChanged.connect(self._on_spin_changed)
        style_ribbon_number_field(self._sigma_spin, "99 px")
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

    _kind = KIND_LOCAL_CONTRAST

    def _build_spinboxes(self) -> tuple[QWidget, QWidget]:
        self._sigma_spin = make_compact_spinbox(QSpinBox(self))
        self._sigma_spin.setRange(1, 2000)
        self._sigma_spin.setSuffix(" px")
        self._sigma_spin.setKeyboardTracking(False)
        self._sigma_spin.setToolTip("Local contrast sigma - the local-neighborhood blur radius.")
        self._sigma_spin.valueChanged.connect(self._on_spin_changed)
        style_ribbon_number_field(self._sigma_spin, "99 px")

        self._z_spin = make_compact_spinbox(QDoubleSpinBox(self))
        self._z_spin.setRange(0.1, 20.0)
        self._z_spin.setDecimals(1)
        self._z_spin.setSingleStep(0.1)
        self._z_spin.setToolTip("Local contrast z-threshold - how many local standard deviations count as an outlier.")
        self._z_spin.valueChanged.connect(self._on_spin_changed)
        style_ribbon_number_field(self._z_spin, "19.9")
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


class _HoverWatcher(QObject):
    """Tells `on_enter(watched)` / `on_leave(watched)` when the mouse enters or leaves a widget it is installed on (a plain event
    filter: it never consumes the event, so the button behaves as before)."""

    def __init__(
        self,
        on_enter: Callable[[QObject], None],
        on_leave: Callable[[QObject], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_enter = on_enter
        self._on_leave = on_leave

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt's name
        if watched is not None and event is not None:
            if event.type() == QEvent.Type.Enter:
                self._on_enter(watched)
            elif event.type() == QEvent.Type.Leave:
                self._on_leave(watched)
        return False


class MorphologyEditPanel(QWidget):
    """Eye button + radius (px) + four operation buttons (erode/dilate/open/close), each
    an instant action against the mask already in effect at the current
    frame - see module docstring for why there's no +/- pair here. Resting the mouse on
    a button previews the pixels it would change (2026-10-29)."""

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        preview: MaskEditPreview,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mask = mask
        self._chromatic = chromatic
        self._dataset = dataset
        self._mask_scope = mask_scope
        self._image_panel = image_panel
        self._preview = preview
        self._syncing = False
        self._last_frame: tuple[int, float] | None = None

        self._toggle = EditPreviewToggle(preview, self)

        self._radius_spin = make_compact_spinbox(QSpinBox(self))
        self._radius_spin.setRange(1, 100)
        self._radius_spin.setSuffix(" px")
        self._radius_spin.setToolTip("Morphology radius.")
        self._radius_spin.valueChanged.connect(self._on_radius_changed)
        style_ribbon_number_field(self._radius_spin, "99 px")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._radius_spin)
        layout.addSpacing(4)

        self._operation_buttons: dict[str, QToolButton] = {}
        self._hover = _HoverWatcher(self._on_button_entered, self._on_button_left, self)
        for operation, icon_name, tooltip in _MORPHOLOGY_OPERATIONS:
            button = action_button(self, icon_name, _MORPHOLOGY_COLOR, tooltip)
            button.clicked.connect(lambda _checked=False, op=operation: self._apply(op))
            button.installEventFilter(self._hover)
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

    def refresh_theme(self) -> None:
        self._toggle.refresh_theme()

    def _refresh_enabled(self) -> None:
        enabled = self._last_frame is not None
        for button in self._operation_buttons.values():
            button.setEnabled(enabled)

    def _on_button_entered(self, watched: QObject) -> None:
        for operation, button in self._operation_buttons.items():
            if button is watched:
                self._preview.set_morphology_hover(operation)
                return

    def _on_button_left(self, watched: QObject) -> None:
        if watched in self._operation_buttons.values():
            self._preview.set_morphology_hover(None)

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


_PLAIN_ICON_COLOR = "#f8fafc"  # GuiTheme.text_primary, like the other real, active actions of this ribbon


class _FeatureParamsPopover(QWidget):
    """The finder's numbers, in the popup of the settings button (the ribbon row has no room for five fields). The widgets are the
    panel's own (it reads and remembers them); `changed` fires when any of them does."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        defaults = FeatureParams()
        self.strength_spin = QDoubleSpinBox(self)
        self.strength_spin.setRange(2.0, 20.0)
        self.strength_spin.setDecimals(1)
        self.strength_spin.setSingleStep(0.5)
        self.strength_spin.setToolTip(
            "How strongly a feature must stand out from what the other ROIs of the array look like (a score; about 1 is the ordinary "
            "pixel-to-pixel scatter, 5 is clearly different). Lower finds fainter features and more false alarms."
        )
        self.extent_spin = QDoubleSpinBox(self)
        self.extent_spin.setRange(1.0, 10.0)
        self.extent_spin.setDecimals(1)
        self.extent_spin.setSingleStep(0.5)
        self.extent_spin.setToolTip(
            "Where a feature ends: the pixels next to a found feature that reach this score belong to it. Lower makes the features "
            "bigger (and lets neighbouring ones merge). Never higher than the strength."
        )
        self.width_spin = QSpinBox(self)
        self.width_spin.setRange(4, 60)
        self.width_spin.setSuffix(" px")
        self.width_spin.setToolTip("Features wider than this are not small and are left alone (the sample disk is about 34 px across).")
        self.margin_spin = QSpinBox(self)
        self.margin_spin.setRange(0, 10)
        self.margin_spin.setSuffix(" px")
        self.margin_spin.setToolTip("The mask is the feature grown by this much (the edges of a feature are blurred by the optics).")
        self.share_spin = QSpinBox(self)
        self.share_spin.setRange(5, 100)
        self.share_spin.setSuffix(" %")
        self.share_spin.setToolTip(
            "A ROI whose features would cover more than this share of its sample disk or its reference ring is left unmasked (too "
            "little would be left to measure with). The status line lists it."
        )
        self.reset_button = QPushButton("Defaults", self)
        self.reset_button.setToolTip("Put the five numbers back to their starting values.")
        for spin in (self.strength_spin, self.extent_spin, self.width_spin, self.margin_spin, self.share_spin):
            spin.setKeyboardTracking(False)
        self._set_defaults(defaults)
        form = QFormLayout(self)
        form.setContentsMargins(10, 8, 10, 8)
        form.setSpacing(6)
        form.addRow("Strength", self.strength_spin)
        form.addRow("Extent", self.extent_spin)
        form.addRow("Largest feature", self.width_spin)
        form.addRow("Margin", self.margin_spin)
        form.addRow("Skip ROIs above", self.share_spin)
        form.addRow(self.reset_button)
        for spin in (self.strength_spin, self.extent_spin):
            spin.valueChanged.connect(lambda _v: self.changed.emit())
        for spin in (self.width_spin, self.margin_spin, self.share_spin):
            spin.valueChanged.connect(lambda _v: self.changed.emit())
        self.reset_button.clicked.connect(lambda: self._set_defaults(FeatureParams()))

    def _set_defaults(self, defaults: FeatureParams) -> None:
        self.strength_spin.setValue(defaults.strength)
        self.extent_spin.setValue(defaults.extent)
        self.width_spin.setValue(int(defaults.max_width_px))
        self.margin_spin.setValue(int(defaults.margin_px))
        self.share_spin.setValue(int(round(defaults.max_share * 100.0)))

    def params(self) -> FeatureParams:
        return FeatureParams(
            strength=float(self.strength_spin.value()),
            extent=float(self.extent_spin.value()),
            max_width_px=float(self.width_spin.value()),
            margin_px=int(self.margin_spin.value()),
            max_share=self.share_spin.value() / 100.0,
        )


def _ids_text(ids: tuple[int, ...], limit: int = 8) -> str:
    shown = ", ".join(str(i) for i in ids[:limit])
    return shown + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


class RoiFeaturesEditPanel(QWidget):
    """The ROI features tool: eye, settings, Process, Apply (2026-10-29).

    Screen purpose: find the small features (dust, scratches, notches at the rim) that lie on the sample disks or reference rings of
    the ROIs, look at them on the image, and add them to the mask. Layout (one row, like the other tools):

        [eye] [settings v] [process] [apply +]

    - **eye**: show or hide the found pixels on the image (the one switch of the three other tools too).
    - **settings**: strength, extent, largest feature, margin and the share of a ROI above which it is left out (a popup; its tooltip
      always shows the numbers). Remembered.
    - **process**: look at the image on screen. Off the GUI thread (`RoiFeatureTask`); progress and Cancel are in the status bar.
      A second run with other numbers on the same image takes a fraction of the first (the fit is kept).
    - **apply**: add the found pixels to the mask in effect at this frame, at the scope the State group says (Persistent or
      Individual), in one mask change, cut to the area selection if there is one. Like the other tools' Add it is not on the undo
      stack (mask edits are not, see `MaskModule`'s docstring). Enabled only while the image on screen is the one the result was made
      for; the tooltip says why it is not.

    The finder looks at the picture only, never at the mask, so what it finds does not change when the mask is edited."""

    def __init__(
        self,
        mask: MaskModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        image_panel: ImagePanel,
        mask_scope: MaskScopeModule,
        preview: MaskEditPreview,
        features: RoiFeatureTask,
        status: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._features = features
        self._dataset = dataset
        self._status = status
        self._editor = MaskCandidateEditor(mask, chromatic, dataset, image_panel, mask_scope, preview, status)

        self._toggle = EditPreviewToggle(preview, self)

        # The numbers are in a popup: the Mask ribbon row is already ~800 px wide.
        self._pop = _FeatureParamsPopover(self)
        self._settings_button = QToolButton(self)
        self._settings_button.setAutoRaise(True)
        self._process_button = action_button(self, "player-play", _PLAIN_ICON_COLOR, "")
        self._process_button.clicked.connect(self.start)
        self._settings_button.setFixedSize(self._process_button.size())
        self._settings_button.setIconSize(self._process_button.iconSize())
        self._settings_button.setStyleSheet(self._process_button.styleSheet())
        self._settings_button.setIcon(disabled_safe_icon("settings", _PLAIN_ICON_COLOR))
        self._settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._settings_button)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self._pop)
        menu.addAction(action)
        self._settings_button.setMenu(menu)
        self._pop.changed.connect(self._refresh_state)

        self._apply_button = action_button(self, "square-rounded-plus", ADD_COLOR, "")
        self._apply_button.clicked.connect(self.apply)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._settings_button)
        layout.addWidget(self._process_button)
        layout.addWidget(self._apply_button)
        layout.addStretch(1)

        features.running_changed.connect(lambda _running: self._refresh_state())
        features.result_changed.connect(self._refresh_state)
        features.completed.connect(self._on_completed)
        features.failed.connect(status)
        features.cancelled.connect(lambda: status("ROI features cancelled; nothing was changed."))
        self._frame_state: tuple[bool, bool] = (False, False)  # (an image is shown, the result belongs to it) at the last full refresh
        image_panel.image_rendered.connect(self._on_frame_changed)
        image_panel.image_cleared.connect(self._on_frame_changed)
        dataset.dataset_loaded.connect(lambda _ds: self._refresh_state())
        dataset.dataset_cleared.connect(self._refresh_state)
        self._refresh_state()

    # -- remembered state ---------------------------------------------------------------------------------

    def bind_ui_state(self, store) -> None:
        """Remember the five numbers across restarts (`panels/ui_state.py`)."""
        store.bind("image/mask_features/strength", self._pop.strength_spin)
        store.bind("image/mask_features/extent", self._pop.extent_spin)
        store.bind("image/mask_features/max_width_px", self._pop.width_spin)
        store.bind("image/mask_features/margin_px", self._pop.margin_spin)
        store.bind("image/mask_features/max_share_percent", self._pop.share_spin)

    def refresh_theme(self) -> None:
        self._toggle.refresh_theme()

    def params(self) -> FeatureParams:
        """The numbers the popup holds now."""
        return self._pop.params()

    # -- the buttons ----------------------------------------------------------------------------------------

    def start(self) -> None:
        """Process the image on screen with the numbers shown (the button's action; also callable by tests)."""
        frame = self._editor.frame()
        if frame is None or self._features.is_running():
            return
        self._status("Looking for small features on the ROIs of this image... (progress and Cancel in the status bar)")
        self._features.start(frame, self.params())

    def apply(self) -> None:
        """Add the found pixels to the mask (the button's action; also callable by tests)."""
        if self._features.pixels_for(self._editor.frame()) is None:
            return
        self._editor.apply(KIND_ROI_FEATURES, subtract=False, done=self._refresh_state)

    # -- state ------------------------------------------------------------------------------------------------

    def _on_frame_changed(self, *_args: object) -> None:
        """An image was rendered (every step of Play): O(1) unless the answer to "is there an image, and is the result its own" changed
        - the tooltips and button states are rebuilt only then."""
        frame = self._editor.frame()
        if (frame is not None, self._features.pixels_for(frame) is not None) != self._frame_state:
            self._refresh_state()

    def _refresh_state(self, *_args: object) -> None:
        frame = self._editor.frame()
        running = self._features.is_running()
        loaded = frame is not None
        result = self._features.result()
        shown = self._features.pixels_for(frame)
        self._frame_state = (loaded, shown is not None)
        self._process_button.setEnabled(loaded and not running)
        self._settings_button.setEnabled(not running)
        self._apply_button.setEnabled(shown is not None and not running)
        params = self.params()
        self._settings_button.setToolTip(
            f"ROI feature settings: strength {params.strength:.1f}, extent {params.extent:.1f}, largest {params.max_width_px:.0f} px, "
            f"margin {params.margin_px} px, skip ROIs above {params.max_share * 100:.0f} %. Click to change."
        )
        process_tip = (
            "Find the small features (dust, scratches, notches at the rim) on the sample disks and reference rings of all ROIs of "
            "this image. It looks at the picture only, takes a few seconds and changes no mask; progress and Cancel are in the status bar."
        )
        if not loaded:
            process_tip += "\nShow an image first."
        if running:
            process_tip += "\nIt is running."
        elif result is not None and result.params != params:
            process_tip += "\nThe settings changed since the last run: press to update the result."
        self._process_button.setToolTip(process_tip)
        if shown is not None:
            count = int(shown.sum())
            self._apply_button.setToolTip(
                f"Add the {count:,} pixels found ({sum(f.features for f in result.findings)} features on {len(result.findings)} ROIs) "
                "to the mask, at the scope chosen under State (Persistent or Individual)."
            )
        elif result is not None:
            cube, wavelength = result.frame
            self._apply_button.setToolTip(
                f"The features were found on cube {cube}, {wavelength:g} nm. Go back to that image to add them, or press Process here."
            )
        else:
            self._apply_button.setToolTip("Nothing to add yet: press Process first.")

    def _on_completed(self, summary: FeatureSummary) -> None:
        if summary.features == 0:
            text = f"ROI features: no features found on the {summary.rois_judged} ROIs compared ({summary.seconds:.1f} s)."
        else:
            text = (
                f"ROI features: {summary.features} features on {summary.rois_with_features} of {summary.rois_judged} ROIs "
                f"({summary.pixels:,} px, {summary.seconds:.1f} s{', fit reused' if summary.fit_kept else ''}). "
            )
            if summary.left_out:
                text += f"ROIs {_ids_text(summary.left_out)} left out (more than the set share of the disk or ring would go). "
            text += "The eye shows them; the + button adds them to the mask."
        self._status(text)


class EraserEditPanel(QWidget):
    """The Eraser's row (2026-10-29): nothing to set, so one passive "click on the image" icon whose tooltip says what the tool does.

    The tool itself is not here: while it is picked and the Mask tab is open, a left click on the image goes to
    `MaskGrainEraser.erase_at` (`CanvasInteraction`, which also shows a cross cursor). Screen purpose: remove one blob of the mask by
    clicking it. Main action: click a masked pixel. States: armed (this row is shown and the Mask tab is open) / not. Error messages
    (status line): no mask pixel under the click; the click is outside the area selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hint = QLabel(self)
        self._hint.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setToolTip(
            "Eraser: click a mask pixel on the image and the whole blob of connected mask pixels (a grain; touching at a corner "
            "counts) leaves the mask. It acts at the scope chosen under State (Persistent or Individual) and stays inside the area "
            "selection, if there is one."
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._hint)
        layout.addStretch(1)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        icon = load_tabler_icon("pointer", color=get_active_theme().text_dim, size=ICON_SIZE * 2, stroke_width=2.1)
        self._hint.setPixmap(icon.pixmap(ICON_SIZE, ICON_SIZE))


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
        style_ribbon_number_field(self._size_spin, "99 px")

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
        _add_icon_pair(layout, self._add_mode_button, self._erase_mode_button)
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
