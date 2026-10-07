"""``ImagePanel`` (sketch §7 "Display panels" > Image, §10).

Reads Dataset + Image Tools (to render the processed image) + ROI Toolbox
(``display_position``, for overlays) + Selection. Forwards drag/click as
``RoiToolbox.request_move(...)`` etc. - never touches ROI state directly
(resolving the current app's "lots of connection between Image panel and
ROI section" complaint from the feature inventory).

**Built 2026-09-23**, replacing the scaffold's stub - the first real panel
on this branch. What it does and, more importantly, what it deliberately
does not:

**It owns no state that another module owns.** It holds no ROI list, no
selection set, no settings copy; every draw re-reads from the modules. That
is the single rule the old app broke hardest (`docs/rewrite_feature_
inventory_2026-09.md`: a god object with its own shadow copies of
everything), and it is cheap to keep here because the modules' query methods
are all in-memory reads.

**Every user gesture becomes a command call, never a mutation.** A drag
calls ``RoiToolbox.request_move``; a click calls
``SelectionModule.set_roi_selection``; the navigation controls call
``SelectionModule.set_cube``/``set_wavelength``. The panel then redraws
because the module emitted a change - not because it changed something and
redrew itself. That one-way flow is what makes undo/redo work without the
panel knowing undo exists: a Ctrl+Z emits the same signals a fresh edit
does, and the panel reacts identically.

**Redraws are coalesced** into one render ~100ms after the last change
(sketch §8), and rendering happens off the GUI thread (`render.py`).

**Not built here, deliberately** - each is its own piece of work, not
something this panel should invent an answer for:

- Mask painting/preview overlays (`MaskModule`'s async candidate machinery
  isn't built either - see its module docstring). **The histogram
  intensity-highlight overlay is built** - 2026-10-02, mirrors the
  mask-overlay tint's own shape (`_update_highlight_overlay`,
  `histogram_highlight_overlay_controls.py`), reading
  `HighlightRangeModule.current_range()` against the already-displayed
  pixel array instead of a resolved mask.
- The cursor readout and scale bar - ``GeometryModule`` already owns the
  calibration state they would draw from. (The ruler itself is built -
  2026-09-29, ``measure_line_tool.py`` - two-click placement, drag either
  placed point to fine-tune, and its floating fields; a persistent
  always-visible scale bar is a separate, still-unbuilt overlay.)
- ROI creation by click and ROI resize by handle. ``add_roi``/``resize_roi``
  are real commands; this panel currently only moves and selects, which is
  what makes the overlay worth looking at in the first place.
  (**Rotation is built** - 2026-09-28, ``rotate_line_tool.py``. **Crop is
  built** - 2026-09-29, ``crop_tool.py`` - deliberately *not* a port of the
  old app's ``gui/image_tools_controller.py``/``pg.RectROI``: no separate
  handle widgets, the rectangle's own border is the grab zone. **Measure is
  built** - 2026-09-29, ``measure_line_tool.py``/``measure_controls.py`` -
  a two-click ruler, not the old app's draggable crosses.)

**Active tool** (2026-09-28): which canvas tool is on comes from the shared
``ActiveToolModule`` - the panel never decides it. While a *preview* tool
(rotate or crop) is active, the panel (a) renders the image **uncropped**,
with the existing crop drawn as a fixed outline, so the user sees what is in
and out of the crop while aligning - the crop stays put in pixel terms and is
re-applied when the tool is switched off; (b) hides the ROI overlay, because
ROI positions live in *processed* (cropped) space and would sit at the wrong
place over an uncropped image (CLAUDE.md: mixing the spaces silently gives
wrong results); and (c) routes left/right clicks and keys to the tool - Crop
and Measure additionally claim left-button *drags* (``ImageViewBox.set_
left_drag_handler``, dispatched by ``_on_left_drag_event`` to whichever of
the two is active - Crop for drawing/resizing the rectangle, Measure for
repositioning an already-placed point). With no tool active, a left click
selects ROIs as before. Measure is deliberately **not** a preview tool -
calibration is measured against whatever is currently displayed
(already-cropped/rotated), not the raw frame, so the ROI overlay stays
visible while measuring too. Mouse/keyboard rules: ``image_controls.py``.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, QPointF, QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
    QCompleter,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...image_tools import (
    ActiveToolModule,
    BackgroundModule,
    ChromaticModule,
    GeometryModule,
    ImageTool,
    MaskEditTool,
    MaskEditToolModule,
    MaskModule,
    MaskScopeModule,
)
from ...image_tools.geometry.model import CropDefinition, GeometrySettings
from ...image_tools.preprocess import area_selection_to_raw, resolve_external_mask
from ...roi import RoiToolbox
from .no_data import format_pixel_value, no_data_overlay_rgba
from ...roi.overlay_geometry import circle_outlines
from ...roi.rasterize import effective_reference_diameters
from ...selection import AreaSelectionModule, HighlightRangeModule, ReferenceFrameModule, SelectionModule
from ...selection.reference_frame_module import MODE_AUTO
from ..cursor_overlay import CursorOverlay
from ..ribbon_group import group_label_style, labeled_icon_group, vertical_separator
from .landmark_overlay import draw_landmarks
from .slider_ticks import cube_slider_major_ticks, wavelength_slider_major_ticks
from .transforms_settings import TransformsSection
from ...image_tools.chromatic_auto_task import ChromaticAutoDetect
from .area_selection_tool import AreaSelectionTool
from .background_tab import BACKGROUND_INFO_HTML, BackgroundTab
from .chromatic_tab import ChromaticCorrectionTab, ChromaticUiValues
from .canvas_tools import _ICON_SIZE, CanvasToolsBar, style_bar_icon_button
from .context_menu import show_tool_context_menu
from .crop_size_controls import CropSizeControls
from .crop_tool import CropTool
from .data_axis_slider import DataAxisSlider
from .general_group import ICON_SIZE as _RIBBON_ICON_SIZE
from .general_group import AreaSelectionPicker, mirror_icon_button, style_general_icon_button
from .guided_value_spinbox import GuidedValueSpinBox
from .histogram_highlight_overlay_controls import HistogramHighlightOverlayControls
from .image_controls import ImageViewBox, controls_text
from .mask_edit_panels import (
    DrawEditPanel,
    HistogramSelectionEditPanel,
    LocalContrastEditPanel,
    MorphologyEditPanel,
    ThresholdEditPanel,
)
from .mask_edit_tool_picker import MaskEditToolPicker
from .mask_file_actions import MaskClearAction, MaskPngActions
from .mask_overlay_controls import MaskOverlayControls
from .mask_scope_toggle import MaskScopeToggle
from .measure_controls import MeasureCalibrationControls
from .measure_line_tool import MeasureLineTool
from .overlay_style import OverlayStyle
from .overlay_tint import OverlayTint
from .render import ImageRenderer, RenderRequest, RenderResult
from .roi_label_overlay import RoiLabelItem, label_text
from .roi_overlay_controls import RoiOverlayControls
from .rotate_line_tool import RotateLineTool
from ..ui_state import UiStateStore
from .scale_bar_controls import ScaleBarControls
from .scale_bar_overlay import ScaleBarItem
from .tool_ribbon import VIEW_TAB, ImageToolRibbon

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_MS = 100  # sketch §8 - the already-validated coalescing window
# Slower than the redraw coalesce above on purpose: this debounces a settings
# *write* (one JSON file per `AppSettings._persist` call - see
# `app_rewrite.py`), not a redraw, so there is no reason to pay disk I/O on
# every intermediate frame of a drag/zoom the way a 100ms redraw would.
_VIEW_RANGE_PERSIST_DEBOUNCE_MS = 600
_CIRCLE_POINTS = 48
"""Vertices per drawn circle. 48 is smooth at any zoom a screen can show
while keeping the whole overlay to a few thousand points for a few hundred
ROIs - the overlay is redrawn on every selection change, so its cost is
paid far more often than the image's."""

_DEFAULT_SAMPLE_COLOR = "#f59e0b"
_DEFAULT_REFERENCE_COLOR = "#38bdf8"
_SAMPLE_WIDTH = 1.5
_REFERENCE_WIDTH = 1.0
_SELECTED_COLOR = "#f8fafc"
_MAX_IDLE_SAMPLE_CURVES = 24
"""How many emptied per-colour sample curves are kept for reuse."""
_CHUNK_GRID_COLOR = "#a3a3a3"
_CROP_OUTLINE_COLOR = "#38bdf8"  # the crop button's active blue

# Cube/Wavelength navigation rows (ported from the stable app's
# docs/image_area_slider_redesign.md). The reference-highlight colors are
# the stable app's own hardcoded literals, not lspr_ui theme tokens - same
# convention `reference_frame_row.py`'s `_ACTIVE_COLOR` already uses for the
# same feature's Auto/Manual buttons.
_REFERENCE_CUBE_HIGHLIGHT = "#facc15"
_REFERENCE_WAVELENGTH_HIGHLIGHT = "#84cc16"


def _slider_axis_title_style(color: str) -> str:
    """Shared 11px/600 style for the Cube/λ slider-title labels - ported
    from the stable app's `layout_builder._slider_axis_title_style` so both
    titles use identical numbers instead of two hand-copied versions that
    can drift."""
    return f"color: {color}; font-size: 11px; font-weight: 600;"


CHROMATIC_TAB = "Chromatic"  # ribbon tab label (full name "Chromatic Corrections" is its tooltip)
BACKGROUND_TAB = "Background"  # ribbon tab label; green while background removal is applied
# The "i" icon's text per ribbon tab ("Image tools" and "ROIs" show the active tool's canvas controls instead).
_TAB_INFO: dict[str, str] = {
    VIEW_TAB: (
        "View: display options only - show/hide, colour and transparency of the histogram-selection tint "
        "and of the mask tint. Nothing here changes data or results."
    ),
    "Mask": (
        "Mask: pixels to ignore (kept out of the background estimate and ROI sampling). "
        "State: edit scope (persistent / individual). Visibility: tint on/off, colour, transparency. "
        "Manual edit: histogram selection, threshold, local contrast, morphology, draw. PNG: load/save the mask."
    ),
    CHROMATIC_TAB: (
        "Chromatic correction: Run finds landmarks on the reference frame and follows them through the "
        "wavelengths (gear: settings). Eye/stack: show landmarks for one or all wavelengths. "
        "Wand: switch the correction on/off. Trash: clear landmarks and correction."
    ),
    BACKGROUND_TAB: BACKGROUND_INFO_HTML,
}
_PREVIEW_TOOLS = frozenset({ImageTool.ROTATE, ImageTool.CROP})
"""Tools that work on the *uncropped* image: while one is active the image is
rendered without its crop, the crop is drawn as an outline, and the ROI
overlay (cropped-space coordinates) is hidden. See the module docstring.
Crop joined 2026-09-29: the whole point of the tool is choosing a new crop
from the full available frame, not just the region an old crop already
kept."""

_CURSOR_FOR_CROP_HANDLE: dict[str | None, Qt.CursorShape] = {
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}
"""Cursor feedback for `CropTool.hover_handle`'s result - the tool draws no
separate handle graphics (see crop_tool.py's docstring), so the cursor
shape is the only hint that an edge/corner/interior is grabbable."""


class ImagePanel(QWidget):
    """Renders the current processed image with ROI overlays. Owns no
    computation and no ROI/group state (CLAUDE.md)."""

    # A tool's *live* status while a gesture is in progress (angle readout
    # etc.) - relayed to the app's status bar (app_rewrite.py), the same
    # place every other panel's transient status already goes. The tool's
    # *static* "what do the buttons do" text is not a signal at all; it
    # lives on the permanent info icon's tooltip instead (see
    # `_refresh_tool_info`), because it only needs to be re-read, not pushed.
    tool_status_changed = pyqtSignal(str)

    # The currently displayed plane, exactly as shown (post crop/rotate/
    # mask/CC/background, pre-overlay), plus the (cube_index, wavelength_nm)
    # it belongs to - the one narrow read the Histogram panel needs (sketch
    # §7's "pulls 'current displayed image' from Image panel"), added
    # 2026-09-29 rather than having Histogram run its own second render of
    # the same frame. The frame is carried alongside the array rather than
    # left for the receiver to re-read from `SelectionModule` - by the time
    # a render completes, `SelectionModule` may already have moved on to a
    # different frame (a fast wavelength drag queues renders faster than
    # they complete), and matching ROI/mask geometry against the wrong frame
    # would be a real correctness bug, not just a cosmetic lag. `RenderResult
    # .request` already carries the frame the array was actually requested
    # for (see `_on_rendered` below), so this only forwards it.
    image_rendered = pyqtSignal(np.ndarray, int, float)
    # Companion to `image_rendered` - the Histogram panel's other half of
    # "read Image, don't read Dataset directly" (sketch §7): without this,
    # Histogram would need its own `DatasetModule` reference just to learn
    # "nothing is displayed anymore", widening its dependency list for one
    # narrow case `ImagePanel` already knows about.
    image_cleared = pyqtSignal()

    # What frame is on screen ("Cube 0, 550 nm") - the old bottom status
    # row's replacement (2026-09-30, maintainer request: that row, plus its
    # trailing "- WxH px" resolution text, is gone; this is shown instead as
    # the dock title bar's centered subtitle, via `app_rewrite.py` wiring
    # this straight to `PanelContainer.set_subtitle`). Render *errors*
    # ("Cannot show this frame: ...") deliberately do not go through this
    # signal - they go through `tool_status_changed` instead (a transient
    # status-bar message, the same convention every other guarded command in
    # this panel already uses - see `_on_measure_apply_requested`), since an
    # error is not "what frame is displayed", it's "why nothing is".
    frame_status_changed = pyqtSignal(str)

    # The viewport's pan/zoom, as (x_min, x_max, y_min, y_max) in image pixel
    # coordinates - `app_rewrite.py`'s other half of `initial_view_range`
    # below, written back to `AppSettings` the same debounced-then-persist
    # shape every other display panel already uses for its redraw (this is a
    # settings write, not a redraw, so it uses its own slower timer - see
    # `_view_range_debounce_timer`).
    view_range_changed = pyqtSignal(float, float, float, float)
    # Label of the ribbon tab now shown ("Image tools"/"Mask"/"Histogram"/"ROIs") -
    # lets the Histogram plot honour the area selection only while its own tab is open.
    ribbon_category_changed = pyqtSignal(str)
    # The Chromatic tab's popover values, after a run that used them succeeded (app settings persist them).
    chromatic_settings_applied = pyqtSignal(object)  # ChromaticUiValues
    # A tint overlay's look changed ("mask" or "highlight", new `OverlayStyle`); app settings persist it.
    overlay_style_changed = pyqtSignal(str, object)
    # The landmark overlay's two view toggles (shown, every wavelength) - remembered by the app settings.
    chromatic_view_changed = pyqtSignal(bool, bool)
    # The Background tab's "show the estimate instead of the image" toggle; app settings remember it.
    background_view_changed = pyqtSignal(bool)
    # Intensity under the Image cursor (2026-10-03), or None once the cursor
    # is switched off / the pixel has no value. The Histogram marks it as a
    # tick on its x axis. Display only - carries no state anyone must keep.
    cursor_value_changed = pyqtSignal(object)

    def __init__(
        self,
        dataset: DatasetModule,
        geometry: GeometryModule,
        mask: MaskModule,
        chromatic: ChromaticModule,
        background: BackgroundModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        active_tool: ActiveToolModule,
        reference_frame: ReferenceFrameModule,
        highlight_range: HighlightRangeModule,
        parent: QWidget | None = None,
        *,
        mask_scope: MaskScopeModule,
        area_selection: AreaSelectionModule | None = None,
        chromatic_auto: ChromaticAutoDetect | None = None,
        initial_chromatic_values: ChromaticUiValues | None = None,
        initial_chromatic_view: tuple[bool, bool] = (True, False),
        initial_view_range: tuple[tuple[float, float], tuple[float, float]] | None = None,
        initial_mask_overlay: OverlayStyle | None = None,
        initial_highlight_overlay: OverlayStyle | None = None,
        initial_ribbon_category: str | None = None,
        initial_show_background: bool = False,
    ) -> None:
        super().__init__(parent)
        self._show_background = bool(initial_show_background)
        self._last_data_range: tuple[float, float] | None = None  # display levels reused for the background view
        self._initial_ribbon_category = initial_ribbon_category
        # Applied once, the first time a real image lands (`_on_rendered`) -
        # not here, since no plane exists yet to set a view against. `None`
        # (first launch, or a settings file with no saved range yet) just
        # means "let pyqtgraph's own default auto-range fit the first image",
        # exactly like every launch before this feature existed.
        self._initial_view_range = initial_view_range
        self._view_range_restored = False
        self._dataset = dataset
        self._geometry = geometry
        self._mask = mask
        self._mask_scope = mask_scope
        # Optional so a panel built without the app shell (tests) still works;
        # `app_rewrite.py` passes the one shared instance.
        self._area_selection = area_selection if area_selection is not None else AreaSelectionModule(self)
        self._chromatic = chromatic
        # Optional like `area_selection`: tests build the panel without the app shell.
        self._chromatic_auto = chromatic_auto if chromatic_auto is not None else ChromaticAutoDetect(chromatic, self)
        self._initial_chromatic_values = initial_chromatic_values
        self._initial_chromatic_view = initial_chromatic_view
        self._landmark_overlay_visible = bool(initial_chromatic_view[0])
        self._landmark_all_wavelengths = bool(initial_chromatic_view[1])  # False = current wavelength only
        self._background = background
        self._roi_toolbox = roi_toolbox
        self._selection = selection
        self._reference_frame = reference_frame
        self._highlight_range = highlight_range
        self._active_tool = active_tool
        self._tool_status = ""

        # Mask-overlay display state (cosmetic only - see
        # mask_overlay_controls.py's module docstring for why this lives
        # here and not on MaskModule). `_mask_overlay_state` caches the last
        # resolved (authored_mask, geometry, warp_affine, hidden) tuple so a
        # pure visibility/color/alpha change can redraw the tint without
        # re-resolving the mask or touching the async pixel-render pipeline.
        self._mask_tint = OverlayTint(QColor(get_active_theme().mask_color), 0.5, initial_mask_overlay)
        self._mask_overlay_state: tuple[np.ndarray | None, GeometrySettings | None, np.ndarray | None, bool] | None = None

        # Histogram highlight-overlay display state (cosmetic only - see
        # histogram_highlight_overlay_controls.py's module docstring for why
        # this lives here and not on `HighlightRangeModule`). Unlike the mask
        # overlay above, there is nothing to "resolve" - the tint is just the
        # already-displayed pixel array thresholded against
        # `HighlightRangeModule.current_range()` - so `_current_display_image`
        # caches that array instead of a resolved-mask tuple.
        # 0.42 = the stable app's own literal (`_highlight_alpha`).
        self._highlight_tint = OverlayTint(
            QColor(get_active_theme().highlight_color), 0.42, initial_highlight_overlay
        )
        self._current_display_image: np.ndarray | None = None
        # Mirrors the last `frame_status_changed` emission (same "cached
        # alongside the signal" shape as `_tool_status` above) - lets a test
        # (or any other direct caller) read the current frame-status text
        # without needing a dock title bar in the loop to observe it.
        self._frame_status = ""

        self._serial = itertools.count(1)
        self._latest_serial = 0
        self._drag_roi_id: int | None = None
        # (height, width) of the last successfully rendered plane - the
        # chunk-grid preview needs real pixel dimensions and only this
        # panel's own render result has them (`DatasetModule` holds no
        # per-frame shape query - see its query-surface docstring).
        self._last_image_shape: tuple[int, int] | None = None

        self._build_ui()

        self._renderer = ImageRenderer(dataset.load_plane, parent=self)
        self._renderer.rendered.connect(self._on_rendered)
        # `closeEvent` only reaches top-level windows, and this panel lives
        # inside a tab strip - so on a normal application quit it would
        # never fire, leaving the render thread emitting into a widget Qt is
        # tearing down. That is the shape of this app's documented
        # PyQt6-sip crash-on-close, so the shutdown hook is on the
        # application, where it actually fires.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._renderer.stop)
            app.aboutToQuit.connect(self._chromatic_auto.shutdown)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

        self._view_range_persist_timer = QTimer(self)
        self._view_range_persist_timer.setSingleShot(True)
        self._view_range_persist_timer.setInterval(_VIEW_RANGE_PERSIST_DEBOUNCE_MS)
        self._view_range_persist_timer.timeout.connect(self._emit_view_range_changed)
        self._plot.vb.sigRangeChanged.connect(self._on_view_range_changed)

        self._connect_modules()
        self._refresh_navigation_ranges()
        # No dataset yet at construction - same text `_on_dataset_cleared`
        # emits later, so the dock title bar's subtitle never sits blank
        # while genuinely nothing is loaded.
        self._set_frame_status("No dataset loaded.")

    def restore_ui_state(self, store: UiStateStore) -> None:
        """Put the cursor readout toggle and the Mask edit tool pick back as
        last left, and keep saving them (see `panels/ui_state.py`). Called
        once by the app shell after construction."""
        self._cursor_overlay.set_enabled(store.get("image/cursor_readout") is True)
        self._cursor_overlay.toggled.connect(lambda on: store.set("image/cursor_readout", bool(on)))
        try:
            self._mask_edit_tool.set_tool(MaskEditTool(store.get("image/mask_edit_tool")))
        except ValueError:  # nothing saved yet, or a tool that no longer exists
            pass
        self._mask_edit_tool.tool_changed.connect(lambda tool: store.set("image/mask_edit_tool", tool.value))
        saved_color = QColor(str(store.get("image/scale_bar_color") or ""))
        if saved_color.isValid():
            self._scale_bar_item.set_color(saved_color)
            self._scale_bar_controls.set_color(saved_color)
        self._scale_bar_color_store = store
        for kind in ("sample", "reference"):
            visible = store.get(f"image/roi_{kind}_visible")
            if isinstance(visible, bool):
                setattr(self, f"_roi_{kind}_visible", visible)
            color = QColor(str(store.get(f"image/roi_{kind}_color") or ""))
            if color.isValid():
                setattr(self, f"_roi_{kind}_color", color.name())
            alpha = store.get(f"image/roi_{kind}_alpha")
            if isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and 0.0 <= alpha <= 1.0:
                setattr(self, f"_roi_{kind}_alpha", float(alpha))
        labels = store.get("image/roi_labels")
        if isinstance(labels, bool):
            self._roi_labels_visible = labels
        self._sync_roi_overlay_controls()
        self._roi_overlay_store = store  # only now: restoring must not save what it just read

    def _on_scale_bar_color_changed(self, color: QColor) -> None:
        self._scale_bar_item.set_color(color)
        store = getattr(self, "_scale_bar_color_store", None)
        if store is not None:
            store.set("image/scale_bar_color", color.name())

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_canvas()
        self._build_toolbar_and_layout()

    def _build_canvas(self) -> None:
        """The pyqtgraph canvas: view, plot, image/overlay items, canvas tools and
        the cursor readout. Only creates `self._*` attributes, so the toolbar and
        layout code in `_build_toolbar_and_layout` can rely on them."""
        # Parent passed at construction, never set later via a layout that is
        # not itself attached yet - see CLAUDE.md's phantom-top-level-window
        # pitfall, which cost ~6 rounds of screen-recording analysis to find
        # the last time it was hit.
        self._view = pg.GraphicsLayoutWidget(parent=self)
        # Repaint the whole canvas on every change: the cursor crosshair's
        # InfiniteLines span the full view, and Qt's partial-update rectangles
        # left stale copies of them behind as ghost lines (2026-10-03).
        self._view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.refresh_theme()
        # ImageViewBox: middle-drag pans, wheel zooms, left/right drags do
        # nothing (image_controls.py).
        self._plot = self._view.addPlot(viewBox=ImageViewBox())
        self._plot.invertY(True)  # image row 0 at the top, like the old app
        self._plot.setAspectLocked(True)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.setMenuEnabled(False)

        # Pixels without a value (NaN, e.g. rotation corners) are transparent
        # in `_image_item`; this checker sits underneath so they read as
        # "no data" and never as black or a colormap colour.
        self._no_data_item = pg.ImageItem(axisOrder="row-major")
        self._no_data_item.setZValue(-1)
        self._no_data_item.hide()
        self._plot.addItem(self._no_data_item)

        self._image_item = pg.ImageItem(axisOrder="row-major")
        self._plot.addItem(self._image_item)

        # Mask-overlay tint, drawn between the image and the ROI curves
        # below (so ROI markers stay legible over it). Row-major, matching
        # `_image_item` - no transpose needed, unlike the stable app's
        # `ignore_mask_item` (col-major `pg.ImageItem`, hence its transpose
        # in `overlay_manager._update_ignore_mask_overlay`).
        self._mask_tint.attach(self._plot)

        # Histogram highlight-overlay tint (2026-10-02) - same row-major
        # shape as `_mask_tint.item` just above, a separate item so the
        # two tints can be shown/hidden/recolored independently.
        self._highlight_tint.attach(self._plot)

        # Three curve items for the whole overlay rather than per-ROI items:
        # with NaN separators between ROIs, one PlotDataItem draws any number
        # of disjoint circles, so adding an ROI costs array append, not a new
        # QGraphicsItem. Matters because the overlay is rebuilt on every
        # selection change.
        self._sample_curve = self._add_curve(_DEFAULT_SAMPLE_COLOR, width=_SAMPLE_WIDTH)
        # One curve per distinct ROI colour (a group's members each have their
        # own tint); `_sample_curve` stays the one for ROIs with no colour of
        # their own. See `_set_sample_curves`.
        self._sample_curves: dict[str, pg.PlotDataItem] = {_DEFAULT_SAMPLE_COLOR: self._sample_curve}
        self._reference_curve = self._add_curve(_DEFAULT_REFERENCE_COLOR, width=_REFERENCE_WIDTH)
        # How the ROI circles are drawn (the "ROIs" ribbon tab; see `roi_overlay_controls.py`).
        # Display-only: this panel owns the values, no other module does.
        self._roi_sample_visible = True
        self._roi_sample_color = _DEFAULT_SAMPLE_COLOR  # for ROIs with no colour of their own
        self._roi_sample_alpha = 1.0
        self._roi_reference_visible = True
        self._roi_reference_color = _DEFAULT_REFERENCE_COLOR
        self._roi_reference_alpha = 1.0
        self._roi_labels_visible = False
        self._roi_overlay_store: UiStateStore | None = None
        self._selection_curve = self._add_curve(_SELECTED_COLOR, width=2.5)
        self._chunk_grid_curve = self._add_curve(_CHUNK_GRID_COLOR, width=1.0, dashed=True)
        # Chromatic landmark overlay (2026-10-04): crosses = *estimated* (tracked
        # on the image) positions, dots = *fitted* positions (what the correction
        # model says), lines connect them, each in its wavelength's own colour
        # (`wavelength_color.py`). Symbol sizes are screen pixels; styling is
        # set per draw (`_draw_landmarks`).
        self._landmark_line_item = pg.PlotDataItem(connect="finite")
        self._plot.addItem(self._landmark_line_item)
        self._landmark_observed_item = pg.ScatterPlotItem(pxMode=True)
        self._landmark_fitted_item = pg.ScatterPlotItem(pxMode=True)
        self._plot.addItem(self._landmark_observed_item)
        self._plot.addItem(self._landmark_fitted_item)
        # Scale bar (2026-10-06): a screen-space child of the ViewBox, see
        # `scale_bar_overlay.py`; laid out by `_draw_scale_bar`.
        self._scale_bar_item = ScaleBarItem(self._plot.vb)
        self._plot.vb.sigRangeChanged.connect(self._draw_scale_bar)
        self._plot.vb.sigResized.connect(self._draw_scale_bar)
        # ROI labels (the ROIs tab's toggle): the same kind of item, see `roi_label_overlay.py`.
        self._roi_label_item = RoiLabelItem(self._plot.vb)
        self._plot.vb.sigRangeChanged.connect(lambda *_args: self._roi_label_item.update())
        self._plot.vb.sigResized.connect(lambda *_args: self._roi_label_item.update())
        # Drawn only while a preview tool is active (see `_draw_overlays`).
        self._crop_outline_curve = self._add_curve(_CROP_OUTLINE_COLOR, width=1.5, dashed=True)

        self._rotate_tool = RotateLineTool(self._plot, self._geometry, parent=self)
        self._rotate_tool.status_changed.connect(self._on_tool_status)

        self._crop_tool = CropTool(self._plot, self._geometry, parent=self)
        self._crop_tool.status_changed.connect(self._on_tool_status)
        self._crop_tool.changed.connect(self._on_crop_tool_changed)
        # Left-button drags do nothing by default (image_controls.py); Crop
        # is the first tool to claim them - declines (returns False) unless
        # it is the active tool, so every other case is untouched.
        self._plot.vb.set_left_drag_handler(self._on_left_drag_event)
        self._plot.vb.sigTransformChanged.connect(self._reposition_crop_controls)

        # A real QWidget (QSpinBox/QToolButton need actual input, unlike a
        # painted overlay item - see crop_size_controls.py), parented to the
        # viewport so it draws on top of the graphics content. Positioned in
        # screen pixels by `_reposition_crop_controls`; hidden until Crop is
        # active and has a rectangle to show a size for.
        self._crop_controls = CropSizeControls(self._view.viewport(), get_active_theme())
        self._crop_controls.setVisible(False)
        self._crop_controls.size_edited.connect(self._crop_tool.set_size)
        self._crop_controls.apply_requested.connect(self._on_crop_apply_requested)

        self._measure_tool = MeasureLineTool(self._plot, self._geometry, parent=self)
        self._measure_tool.status_changed.connect(self._on_tool_status)
        self._measure_tool.measured.connect(self._on_measure_tool_measured)

        self._measure_controls = MeasureCalibrationControls(self._view.viewport(), get_active_theme())
        self._measure_controls.setVisible(False)
        self._measure_controls.apply_requested.connect(self._on_measure_apply_requested)
        self._plot.vb.sigTransformChanged.connect(self._reposition_measure_controls)

        # Area selection (2026-10-03): drag gesture + marching-ants outline.
        # The state is `AreaSelectionModule`'s; this only draws/forwards.
        self._area_tool = AreaSelectionTool(self._plot, self._area_selection, parent=self)

        # Cursor readout text lives on the canvas, top-left (maintainer,
        # 2026-10-03) - the toggle itself sits in the "General" group.
        self._cursor_readout = QLabel(self._view.viewport())
        self._cursor_readout.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._cursor_readout.move(6, 6)
        self._cursor_readout.hide()

    def _build_toolbar_and_layout(self) -> None:
        # Top toolbar (2026-09-30, flipped from a vertical strip along the
        # canvas's left edge to a horizontal bar across its top - maintainer
        # request, "since frames are usually landscapes" - a left rail
        # wastes more of a landscape frame's width than a top bar wastes of
        # its height). A category ribbon on the left (`ImageToolRibbon`,
        # added 2026-09-30 - "Image tools"/"Mask"/"Histogram"/"ROIs" tabs over
        # a fixed-height tool row, ribbon-style; "Histogram" is still a seeded
        # placeholder - see `tool_ribbon.py`'s module docstring for why
        # Select/Add ROI (`CanvasToolsBar`) landed under "ROIs" rather than
        # "Image tools"); the cursor-readout and "i" info icons on the right
        # - both used to float as manually `.move()`d overlays on the canvas
        # itself (`_reposition_cursor_overlay`/`_reposition_tool_info`, both
        # removed with an earlier change); now they're ordinary widgets in a
        # real `QHBoxLayout`, so Qt repositions them on any resize for free.
        #
        # Split by ribbon tab on 2026-10-07 (one 554-line method before; no behaviour
        # change). Each `_build_*_tab` returns the page the ribbon shows, in the order the
        # tabs depend on each other: the View tab mirrors buttons of the Mask, Chromatic and
        # Background tabs, so those are built first.
        image_tools_content = self._build_image_tools_tab()
        mask_content = self._build_mask_tab()
        self._build_chromatic_and_background_tabs()
        view_content = self._build_view_tab()
        roi_content = self._build_roi_tab()
        self._build_top_bar(view_content, image_tools_content, mask_content, roi_content)
        self._build_navigation_bar()

        # Canvas column: top bar (Select/Add ROI + cursor/info icons, both
        # built above), then the pyqtgraph view itself.
        canvas_column = QVBoxLayout()
        canvas_column.setContentsMargins(0, 0, 0, 0)
        canvas_column.setSpacing(0)
        canvas_column.addWidget(self._top_bar)
        canvas_column.addWidget(self._view, 1)

        # Navigation now sits below the canvas (2026-09-30, maintainer
        # request) - `_refresh_controls_bar_theme` draws its border on
        # whichever edge actually touches the canvas, so moving this bar
        # means flipping that edge too (border-top now, was border-bottom).
        #
        # Zero margins/spacing (2026-09-30, real bug found via headless
        # geometry probe, maintainer report of "wide borders around the
        # image area, biggest from the top") - every *other* layout in this
        # method explicitly zeroes its margins; this one, the outermost, was
        # the one left at Qt's style-default ~11px on all four sides. That
        # is invisible as a distinct line (this panel's own background and
        # the canvas's are the same `toolbar_bg`), but it reads as extra
        # dark space padding out the canvas, the toolbar strip, and the
        # nav bar equally - worst at the top because it stacked on top of
        # the dock's own title bar, which the other three sides have
        # nothing equivalent to. (A 10px left/right margin was briefly
        # added here too, then moved to just the nav bar's own `controls`
        # layout above - the canvas/toolbar were meant to stay flush.)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(canvas_column, 1)
        layout.addWidget(self._controls_bar)

        scene = self._image_item.scene()
        scene.sigMouseClicked.connect(self._on_scene_clicked)
        scene.sigMouseMoved.connect(self._on_scene_moved)
        # Arrow keys/Esc for the active tool. An event filter on the view
        # (not `keyPressEvent` here) because the graphics view would
        # otherwise consume arrow keys itself to scroll.
        self._view.installEventFilter(self)

    def _build_image_tools_tab(self) -> QWidget:
        # A second `TransformsSection` instance (2026-09-30, maintainer
        # request - "duplicate transform tools and put them in image tools
        # of image panel"), wired to the same `GeometryModule`/
        # `ActiveToolModule` the Workflow panel's own Transforms row uses -
        # see that class's module docstring for why this is a second front
        # door onto the same backend, not a copy that can drift out of sync.
        self._transforms_section = TransformsSection(self._geometry, self._active_tool, self)

        image_tools_content = QWidget(self)
        image_tools_content_layout = QHBoxLayout(image_tools_content)
        image_tools_content_layout.setContentsMargins(0, 0, 0, 0)
        image_tools_content_layout.setSpacing(6)
        image_tools_content_layout.addWidget(self._transforms_section)
        image_tools_content_layout.addStretch(1)
        return image_tools_content

    def _build_mask_tab(self) -> QWidget:
        # Persistent/Individual mask-edit scope toggle (2026-10-02,
        # maintainer request - copy these icons into the Image panel's own
        # "Mask" tab too). Reads/drives the same `MaskScopeModule` the
        # Workflow panel's `MaskHighlightActions` uses, so the two toggles
        # can never disagree about where a new mask edit lands - see
        # `mask_scope_toggle.py`'s module docstring.
        self._mask_scope_toggle = MaskScopeToggle(self._mask_scope, self)

        # Mask-overlay show/hide + color + transparency (2026-09-30,
        # maintainer request - "implement the mask overlay features" ported
        # from the stable app). Moved into its own "Mask" ribbon tab
        # (2026-10-01, maintainer request - keep the mask icons out of
        # "Image tools" so that tab stays Transforms-only) - see
        # mask_overlay_controls.py's module docstring for why this state
        # lives on the panel rather than on `MaskModule`.
        self._mask_overlay_controls = MaskOverlayControls(
            visible=self._mask_tint.visible,
            color=self._mask_tint.color,
            alpha=self._mask_tint.alpha,
            parent=self,
        )
        # Second copy for the "View" tab (2026-10-06, maintainer request -
        # View collects every display option in one place). Same handlers,
        # and each copy mirrors the other so they never disagree.
        self._view_mask_overlay_controls = MaskOverlayControls(
            visible=self._mask_tint.visible,
            color=self._mask_tint.color,
            alpha=self._mask_tint.alpha,
            parent=self,
        )
        for controls, other in (
            (self._mask_overlay_controls, self._view_mask_overlay_controls),
            (self._view_mask_overlay_controls, self._mask_overlay_controls),
        ):
            controls.visibility_changed.connect(self._on_mask_overlay_visibility_changed)
            controls.color_changed.connect(self._on_mask_overlay_color_changed)
            controls.alpha_changed.connect(self._on_mask_overlay_alpha_changed)
            for signal in (controls.visibility_changed, controls.color_changed, controls.alpha_changed):
                signal.connect(lambda _value, c=controls, o=other: o.sync_from(c))
        self._mask_scope_separator = vertical_separator(self)

        # Mask "Edit" group (2026-10-02, maintainer request - "next to the
        # visibility section in Mask, add Edit section... a pickup menu"
        # offering Histogram selection/Threshold/Local contrast/Morphology/
        # Draw, each with its own small control row swapped in underneath).
        # `MaskEditToolModule` is owned privately here, not threaded through
        # `app_rewrite.py` like `MaskScopeModule`/`HighlightRangeModule` -
        # see that module's own docstring for why nothing else needs to
        # share it yet.
        self._mask_edit_tool = MaskEditToolModule(self)
        self._mask_edit_picker = MaskEditToolPicker(self._mask_edit_tool, self)

        self._mask_edit_stack = QStackedWidget(self)
        self._mask_edit_panel_order: tuple[MaskEditTool, ...] = (
            MaskEditTool.HISTOGRAM_SELECTION,
            MaskEditTool.THRESHOLD,
            MaskEditTool.LOCAL_CONTRAST,
            MaskEditTool.MORPHOLOGY,
            MaskEditTool.DRAW,
        )
        self._mask_edit_stack.addWidget(
            HistogramSelectionEditPanel(
                self._mask, self._geometry, self._chromatic, self._dataset, self._highlight_range, self, self._mask_scope
            )
        )
        self._mask_edit_stack.addWidget(ThresholdEditPanel(self._mask))
        self._mask_edit_stack.addWidget(LocalContrastEditPanel(self._mask))
        self._mask_edit_stack.addWidget(
            MorphologyEditPanel(self._mask, self._chromatic, self._dataset, self, self._mask_scope)
        )
        self._mask_edit_stack.addWidget(DrawEditPanel(self._mask))
        self._on_mask_edit_tool_changed(self._mask_edit_tool.tool())
        self._mask_edit_tool.tool_changed.connect(self._on_mask_edit_tool_changed)

        self._mask_visibility_separator = vertical_separator(self)

        # "General" (Clear) and "PNG" (Load/Save) groups (2026-10-02,
        # maintainer request - split from an earlier single "Automatic
        # edit" group into these two: "these two icons [load/save] should
        # be in 'PNG' section... put [Clear] in solo section 'General'").
        # Neither is part of the `MaskEditToolModule` picker/stack - both
        # are plain standalone action rows, same shape as
        # `MaskOverlayControls`.
        self._mask_clear_action = MaskClearAction(self._mask, self, self)
        self._mask_general_separator = vertical_separator(self)
        self._mask_png_actions = MaskPngActions(self._mask, self._dataset, self._mask_scope, self, self)
        self._mask_edit_separator = vertical_separator(self)

        # Scope toggle on the left, a vertical divider, then the overlay
        # display controls (maintainer request, 2026-10-02: "put them on the
        # left side and separate from rest by | line"), each group captioned
        # ("State"/"Visibility") below its icons - same request, "some
        # non-intrusive labels... smaller fonts, more darker" - see
        # `_labeled_icon_group`'s own docstring for the style reasoning.
        # "General" (Clear) is the Mask tab's own leftmost group (2026-10-02,
        # maintainer request: "put section the most left").
        mask_general_group, self._mask_general_label = labeled_icon_group(self, self._mask_clear_action, "General")
        mask_state_group, self._mask_state_label = labeled_icon_group(self, self._mask_scope_toggle, "State")
        mask_visibility_group, self._mask_visibility_label = labeled_icon_group(
            self, self._mask_overlay_controls, "Visibility"
        )
        # "Edit" renamed "Manual edit" (2026-10-02, maintainer request) now
        # that a sibling group ("PNG" - Load/Save) sits next to it.
        #
        # **The caption wraps only the picker, not the picker+stack pair**
        # (2026-10-02, maintainer report: the label was centering under the
        # *reserved* stack width - as wide as Morphology's own widest
        # panel, 1 spinbox + 4 buttons - rather than under whatever's
        # actually visible, so it visually floated away from the picker
        # whenever a narrower panel like Histogram selection's was shown).
        # The stack itself sits as a plain, uncaptioned sibling to the
        # group's right (top-aligned, so its icon row lines up with every
        # other group's icon row rather than the group's full 42px caption
        # height) - the same "label only where it has one fixed thing to
        # sit under" rule every other group here already follows.
        mask_edit_group, self._mask_edit_label = labeled_icon_group(self, self._mask_edit_picker, "Manual edit")
        mask_png_group, self._mask_png_label = labeled_icon_group(self, self._mask_png_actions, "PNG")

        mask_content = QWidget(self)
        mask_content_layout = QHBoxLayout(mask_content)
        mask_content_layout.setContentsMargins(0, 0, 0, 0)
        mask_content_layout.setSpacing(6)
        mask_content_layout.addWidget(mask_general_group)
        mask_content_layout.addWidget(self._mask_general_separator)
        mask_content_layout.addWidget(mask_state_group)
        mask_content_layout.addWidget(self._mask_scope_separator)
        mask_content_layout.addWidget(mask_visibility_group)
        mask_content_layout.addWidget(self._mask_visibility_separator)
        mask_content_layout.addWidget(mask_edit_group)
        mask_content_layout.addWidget(self._mask_edit_stack, 0, Qt.AlignmentFlag.AlignTop)
        mask_content_layout.addWidget(self._mask_edit_separator)
        mask_content_layout.addWidget(mask_png_group)
        mask_content_layout.addStretch(1)
        return mask_content

    def _build_chromatic_and_background_tabs(self) -> None:
        self._chromatic_tab = ChromaticCorrectionTab(
            self._chromatic,
            self._chromatic_auto,
            self._dataset,
            self._geometry,
            self._resolve_reference_frame,
            self._image_aspect,
            self,
            initial_values=self._initial_chromatic_values,
            initial_show_landmarks=self._initial_chromatic_view[0],
            initial_all_wavelengths=self._initial_chromatic_view[1],
        )
        self._chromatic_tab.show_landmarks_changed.connect(self._on_show_landmarks_changed)
        self._chromatic_tab.landmark_scope_changed.connect(self._on_landmark_scope_changed)
        self._chromatic_tab.settings_applied.connect(self.chromatic_settings_applied)
        self._background_tab = BackgroundTab(
            self._background, show_background=self._show_background, parent=self
        )
        self._background_tab.show_background_changed.connect(self._on_show_background_changed)

    def _build_view_tab(self) -> QWidget:
        # Histogram highlight-overlay show/hide + color + transparency
        # (2026-10-02, maintainer request - "add the toggle to show/hide
        # histogram selection in image... add color selection and
        # transparency control (similar to mask icons)"), ported from the
        # stable app the same way `MaskOverlayControls` was. Fills the
        # "Histogram" ribbon tab, previously a seeded placeholder (see
        # tool_ribbon.py's module docstring).
        self._highlight_overlay_controls = HistogramHighlightOverlayControls(
            visible=self._highlight_tint.visible,
            color=self._highlight_tint.color,
            alpha=self._highlight_tint.alpha,
            parent=self,
        )
        self._highlight_overlay_controls.visibility_changed.connect(self._on_highlight_overlay_visibility_changed)
        self._highlight_overlay_controls.color_changed.connect(self._on_highlight_overlay_color_changed)
        self._highlight_overlay_controls.alpha_changed.connect(self._on_highlight_overlay_alpha_changed)

        # "View" tab (2026-10-06, maintainer request - the old "Histogram"
        # tab renamed and moved second; a one-stop place for display
        # options): a "Histogram" group (the highlight-overlay controls)
        # and a "Mask" group (a mirrored copy of the Mask tab's Visibility
        # icons).
        highlight_visibility_group, self._highlight_visibility_label = labeled_icon_group(
            self, self._highlight_overlay_controls, "Histogram"
        )
        view_mask_group, self._view_mask_label = labeled_icon_group(self, self._view_mask_overlay_controls, "Mask")
        self._view_separator = vertical_separator(self)
        view_content = QWidget(self)
        view_content_layout = QHBoxLayout(view_content)
        view_content_layout.setContentsMargins(0, 0, 0, 0)
        view_content_layout.setSpacing(6)
        view_content_layout.addWidget(highlight_visibility_group)
        view_content_layout.addWidget(self._view_separator)
        view_content_layout.addWidget(view_mask_group)
        view_content_layout.addStretch(1)
        self._view_content_layout = view_content_layout
        # View tab "Chromatic" and "Background" groups: mirrored copies of the
        # Chromatic tab's View buttons (landmarks on/off, scope) and the
        # Background tab's View button (maintainer request 2026-10-06).
        chromatic_mirror_row = QWidget(self)
        chromatic_mirror_layout = QHBoxLayout(chromatic_mirror_row)
        chromatic_mirror_layout.setContentsMargins(0, 0, 0, 0)
        chromatic_mirror_layout.setSpacing(2)
        chromatic_mirrors = [
            mirror_icon_button(button, chromatic_mirror_row)
            for button in (self._chromatic_tab.show_button(), self._chromatic_tab.scope_button())
        ]
        for mirror in chromatic_mirrors:
            chromatic_mirror_layout.addWidget(mirror)
        background_mirror = mirror_icon_button(self._background_tab.view_button(), self)
        self._view_mirrors = [*chromatic_mirrors, background_mirror]
        self._chromatic_tab.view_buttons_refreshed.connect(lambda: [m.sync() for m in chromatic_mirrors])
        self._background_tab.view_buttons_refreshed.connect(background_mirror.sync)
        self._scale_bar_controls = ScaleBarControls(self._geometry, self._scale_bar_item.color(), self)
        self._scale_bar_controls.color_changed.connect(self._on_scale_bar_color_changed)
        view_scale_bar_group, self._view_scale_bar_label = labeled_icon_group(
            self, self._scale_bar_controls, "Scale bar"
        )
        view_chromatic_group, self._view_chromatic_label = labeled_icon_group(
            self, chromatic_mirror_row, "Chromatic"
        )
        view_background_group, self._view_background_label = labeled_icon_group(
            self, background_mirror, "Background"
        )
        self._view_separator_2 = vertical_separator(self)
        self._view_separator_3 = vertical_separator(self)
        self._view_separator_4 = vertical_separator(self)
        stretch_index = self._view_content_layout.count() - 1
        for offset, widget in enumerate(
            (
                self._view_separator_2,
                view_chromatic_group,
                self._view_separator_3,
                view_background_group,
                self._view_separator_4,
                view_scale_bar_group,
            )
        ):
            self._view_content_layout.insertWidget(stretch_index + offset, widget)
        return view_content

    def _build_roi_tab(self) -> QWidget:
        self._canvas_tools = CanvasToolsBar(self._roi_toolbox, self._active_tool, self)
        self._roi_sample_controls = RoiOverlayControls(
            "sample", visible=self._roi_sample_visible, color=QColor(self._roi_sample_color),
            alpha=self._roi_sample_alpha, parent=self,
        )
        self._roi_reference_controls = RoiOverlayControls(
            "reference", visible=self._roi_reference_visible, color=QColor(self._roi_reference_color),
            alpha=self._roi_reference_alpha, parent=self,
        )
        for kind, controls in (("sample", self._roi_sample_controls), ("reference", self._roi_reference_controls)):
            controls.visibility_changed.connect(lambda shown, k=kind: self._on_roi_overlay_changed(k, "visible", bool(shown)))
            controls.color_changed.connect(lambda color, k=kind: self._on_roi_overlay_changed(k, "color", color.name()))
            controls.alpha_changed.connect(lambda alpha, k=kind: self._on_roi_overlay_changed(k, "alpha", float(alpha)))
        self._roi_labels_button = QToolButton(self)
        self._roi_labels_button.setCheckable(True)
        self._roi_labels_button.setChecked(self._roi_labels_visible)
        self._roi_labels_button.setToolTip("Show or hide the ROI labels: each ROI's number, and its name if it has one.")
        style_general_icon_button(self._roi_labels_button)
        self._roi_labels_button.toggled.connect(self._on_roi_labels_toggled)
        self._refresh_roi_labels_icon()
        roi_sample_group, self._roi_sample_label = labeled_icon_group(self, self._roi_sample_controls, "Sample")
        roi_reference_group, self._roi_reference_label = labeled_icon_group(self, self._roi_reference_controls, "Reference")
        roi_labels_group, self._roi_labels_caption = labeled_icon_group(self, self._roi_labels_button, "Labels")
        self._roi_separator = vertical_separator(self)
        self._roi_separator_2 = vertical_separator(self)
        self._roi_separator_3 = vertical_separator(self)
        roi_content = QWidget(self)
        roi_content_layout = QHBoxLayout(roi_content)
        roi_content_layout.setContentsMargins(0, 0, 0, 0)
        roi_content_layout.setSpacing(6)
        roi_content_layout.addWidget(self._canvas_tools)
        roi_content_layout.addWidget(self._roi_separator)
        roi_content_layout.addWidget(roi_sample_group)
        roi_content_layout.addWidget(self._roi_separator_2)
        roi_content_layout.addWidget(roi_reference_group)
        roi_content_layout.addWidget(self._roi_separator_3)
        roi_content_layout.addWidget(roi_labels_group)
        roi_content_layout.addStretch(1)
        return roi_content

    def _build_top_bar(
        self, view_content: QWidget, image_tools_content: QWidget, mask_content: QWidget, roi_content: QWidget
    ) -> None:
        self._top_bar = QWidget(self)
        self._top_bar.setObjectName("imageTopBar")
        top_bar_layout = QHBoxLayout(self._top_bar)
        top_bar_layout.setContentsMargins(4, 2, 6, 2)
        top_bar_layout.setSpacing(6)
        # Always-visible leading tab, no caption (2026-10-03). Filled with
        # the cursor toggle and the area-selection picker just below, once
        # the cursor overlay exists.
        self._general_row = QWidget(self)
        general_row_layout = QHBoxLayout(self._general_row)
        general_row_layout.setContentsMargins(0, 0, 0, 0)
        general_row_layout.setSpacing(2)
        self._area_picker = AreaSelectionPicker(self._area_selection, self._active_tool, self._general_row)

        self._tool_ribbon = ImageToolRibbon(
            [
                (VIEW_TAB, view_content),
                ("Image tools", image_tools_content),
                ("Mask", mask_content),
                ("ROIs", roi_content),
                # Empty on purpose (2026-10-03): filled directly here, not in
                # the Workflow panel (maintainer's plan for chromatic correction).
                (CHROMATIC_TAB, self._chromatic_tab),
                (BACKGROUND_TAB, self._background_tab),
            ],
            self,
            pinned=self._general_row,
        )
        if self._initial_ribbon_category:
            # "Histogram" was this tab's name before 2026-10-06 (settings saved then still say so).
            restored = VIEW_TAB if self._initial_ribbon_category == "Histogram" else self._initial_ribbon_category
            self._tool_ribbon.set_category(restored)  # before the connect: restoring is not a user change
        self._tool_ribbon.category_changed.connect(self.ribbon_category_changed)
        self._tool_ribbon.category_changed.connect(lambda _label: self._refresh_tool_info(self._active_tool.active()))
        # Short tab label, full name in the tooltip; the tab is green while the correction is applied.
        self._tool_ribbon.set_tab_tooltip(CHROMATIC_TAB, "Chromatic Corrections")
        top_bar_layout.addWidget(self._tool_ribbon)
        top_bar_layout.addStretch(1)

        # Cursor-crosshair toggle (maintainer's request, 2026-09-29 - "copy
        # all functions, as hiding, showing values" from the stable app's
        # cursor-toggle overlay). Shares `cursor_overlay.CursorOverlay` with
        # the Histogram plot; only `_cursor_value_at` (a pixel lookup here,
        # a nearest-bin lookup there) differs. No `on_changed` callback
        # (2026-09-30) - its icon used to need manual repositioning when its
        # width changed (icon vs. live text); now that it is a normal
        # `QHBoxLayout` item, Qt already re-lays-out this row for free
        # whenever a child's size hint changes.
        self._cursor_overlay = CursorOverlay(
            scene_view=self._view,
            plot_item=self._plot,
            overlay_parent=self._top_bar,
            value_at=self._cursor_value_at,
            theme=get_active_theme(),
            readout_sink=self._set_cursor_readout,
        )
        # Restyled to match Select/Add ROI (2026-09-30, maintainer request -
        # "make cursor and i icon same as other icons in the bar... this
        # apply for all icons later applied, they should have same style"):
        # overrides the class's own default sizing (`theme.compact_icon_
        # inner`, tuned instead for Histogram's settings-gear button, a
        # different icon this class knows nothing about) with this bar's
        # shared look. `fixed_width=False` - unlike a plain toggle, this
        # button must still grow to show live text ("(38, 30) = 123.4")
        # while enabled; only the height and icon size need to match.
        # Now a fixed-size ribbon icon in "General" (2026-10-03): its live
        # text goes to the canvas corner, so the button never changes width.
        style_general_icon_button(self._cursor_overlay.icon_label)
        self._cursor_overlay.icon_label.setParent(self._general_row)
        general_row_layout.addWidget(self._cursor_overlay.icon_label)
        general_row_layout.addWidget(self._area_picker)

        # A permanent "i" icon (2026-09-29, replacing a text row that only
        # appeared while a tool with canvas behavior was active): hovering
        # it shows the active tool's controls - image_controls.py's single
        # source of truth, so this can never drift from what is actually
        # wired. Always visible, so the help is reachable with no tool
        # active too (it then shows the plain-image controls). A live
        # in-progress status (e.g. the rotate tool's angle readout) is not
        # part of it - nobody is hovering a corner icon mid-gesture, so that
        # goes to the status bar instead (`tool_status_changed`, wired in
        # app_rewrite.py).
        #
        # A real `QToolButton` now, not a `QLabel` (2026-09-30, maintainer
        # request - same size/chrome as every other icon in this bar, via
        # `style_bar_icon_button`) - it does nothing on click (there is
        # nothing to toggle, only a tooltip to show on hover), but a plain
        # `QLabel` had no hover affordance at all and would have stood out
        # as visually inconsistent next to Select/Add ROI/the cursor icon.
        self._tool_info = QToolButton(self._top_bar)
        style_bar_icon_button(self._tool_info)
        self._refresh_tool_info(None)
        top_bar_layout.addWidget(self._tool_info)

        self._refresh_top_bar_theme()

    def _build_navigation_bar(self) -> None:
        # Cube and Wavelength navigation - ported from the stable app's
        # tick/axis-style redesign (docs/image_area_slider_redesign.md):
        # each axis gets its own full-width row (title -> slider -> spin ->
        # one trailing icon button) rather than splitting one row, so each
        # slider gets the full available width and the two axes read as
        # parallel strips. The Cube/Time toggle title-button and the
        # "exclude this image" icon button are *not* ported - see this
        # panel's module docstring "Not built here, deliberately" note for
        # why (no elapsed-time mapping / no exclusions subsystem exists on
        # this branch yet).
        self._cube_spin = QSpinBox(self)
        self._cube_spin.setEnabled(False)
        self._cube_spin.valueChanged.connect(self._on_cube_spin_changed)

        self._cube_slider = DataAxisSlider(Qt.Orientation.Horizontal, self)
        self._cube_slider.setEnabled(False)
        self._cube_slider.set_accent_color(get_active_theme().accent_gold)
        self._cube_slider.valueChanged.connect(self._on_cube_slider_changed)

        cube_title = QLabel("Cube", self)
        cube_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
        cube_row = QHBoxLayout()
        cube_row.setContentsMargins(0, 0, 0, 0)
        cube_row.setSpacing(5)
        cube_row.addWidget(cube_title)
        cube_row.addWidget(self._cube_slider, 1)
        cube_row.addWidget(self._cube_spin)

        self._wavelength_spin = GuidedValueSpinBox(self)
        # No decimal point (2026-09-30, maintainer request) - the dataset's
        # wavelengths are whole nanometers in practice, and a bare integer
        # reads faster in a field this narrow.
        self._wavelength_spin.setDecimals(0)
        self._wavelength_spin.setSingleStep(1.0)
        self._wavelength_spin.setEnabled(False)
        self._wavelength_spin.valueChanged.connect(self._on_wavelength_spin_changed)

        self._wavelength_completer = QCompleter(self)
        self._wavelength_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._wavelength_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._wavelength_completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._wavelength_completer.setModel(QStringListModel([], self))
        self._wavelength_spin.lineEdit().setCompleter(self._wavelength_completer)
        self._wavelength_completer.activated[str].connect(self._on_wavelength_completion_activated)
        self._wavelength_spin.lineEdit().textEdited.connect(self._on_wavelength_text_edited)

        self._wavelength_slider = DataAxisSlider(Qt.Orientation.Horizontal, self)
        self._wavelength_slider.setEnabled(False)
        self._wavelength_slider.set_accent_color(get_active_theme().accent_blue)
        self._wavelength_slider.valueChanged.connect(self._on_wavelength_slider_changed)

        wavelength_title = QLabel("λ (nm)", self)
        wavelength_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
        wavelength_title.setToolTip("Wavelength (nm)")
        wavelength_row = QHBoxLayout()
        wavelength_row.setContentsMargins(0, 0, 0, 0)
        wavelength_row.setSpacing(5)
        wavelength_row.addWidget(wavelength_title)
        wavelength_row.addWidget(self._wavelength_slider, 1)
        wavelength_row.addWidget(self._wavelength_spin)

        # Both rows' titles and number fields share one width each (2026-09-
        # 30, maintainer request) - without this, "Cube" and "λ (nm)" size
        # to their own text and "197"/"470.0" size to their own digits, so
        # the two sliders started at slightly different x positions and the
        # two number fields didn't line up on the right either.
        title_width = max(cube_title.sizeHint().width(), wavelength_title.sizeHint().width())
        cube_title.setFixedWidth(title_width)
        wavelength_title.setFixedWidth(title_width)
        # Narrowed and sized off "00:00:00" (2026-09-30, maintainer request),
        # not the widgets' own `sizeHint()` - a plain cube index needs far
        # less room, but the cube field is meant to grow into an HH:MM:SS
        # elapsed-time display later (see this panel's module docstring,
        # "Not built here, deliberately" - no elapsed-time mapping exists on
        # this branch yet), and re-widening every dependent layout calc when
        # that lands would be needless churn. The wavelength field shares
        # the same width so the two number fields line up on the right,
        # same as the two titles above.
        metrics = QFontMetrics(self._cube_spin.font())
        spin_width = metrics.horizontalAdvance("00:00:00") + 28  # + spin-arrow/frame padding
        self._cube_spin.setFixedWidth(spin_width)
        self._wavelength_spin.setFixedWidth(spin_width)

        navigation = QVBoxLayout()
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(4)
        navigation.addLayout(cube_row)
        navigation.addLayout(wavelength_row)

        # No status row here anymore (2026-09-30, maintainer request) - the
        # "Cube X, wl nm" text it used to show now lives in the dock title
        # bar (`frame_status_changed`, wired to `PanelContainer.set_subtitle`
        # in app_rewrite.py); the trailing "- WxH px" resolution text is not
        # shown anywhere anymore.
        #
        # Left/right margin only, no top/bottom (2026-09-30, maintainer
        # request, "small stylish" follow-up - scoped to just this nav bar
        # after an earlier pass put it on the whole panel by mistake): a
        # bare 10px breathing room so the "Cube"/"λ (nm)" titles don't start
        # flush against the dock's left edge and the number fields don't
        # end flush against its right edge. Top/bottom stay flush - nothing
        # else in this bar needs the room.
        controls = QVBoxLayout()
        controls.setContentsMargins(10, 0, 10, 0)
        controls.setSpacing(4)
        controls.addLayout(navigation)

        # A real QWidget, not a bare layout - QSS `border` only applies to
        # widgets, and this bar needs one (see `_refresh_controls_bar_
        # theme`) for the same reason the top bar does: its background
        # and the canvas's are otherwise visually identical, so the seam
        # where this bar ends and the canvas begins needs a boundary drawn
        # explicitly, not left to two adjacent flat colors that happen to
        # differ.
        self._controls_bar = QWidget(self)
        self._controls_bar.setObjectName("imageNavigationBar")
        self._controls_bar.setLayout(controls)
        self._refresh_controls_bar_theme()

    def _add_curve(self, color_hex: str, *, width: float, dashed: bool = False) -> pg.PlotDataItem:
        pen = pg.mkPen(QColor(color_hex), width=width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        curve = pg.PlotDataItem(pen=pen, connect="finite")
        self._plot.addItem(curve)
        return curve

    def _connect_modules(self) -> None:
        """Every signal that can change what is on screen lands on the same
        coalescing timer. Deliberately flat: the panel does not try to work
        out *which* part of the drawing a given change affects. A geometry
        change and a recolor both just mean "redraw", and one 100ms-coalesced
        redraw is cheaper than the bookkeeping to distinguish them."""
        self._dataset.dataset_loaded.connect(self._on_dataset_loaded)
        self._dataset.dataset_cleared.connect(self._on_dataset_cleared)
        # Direct update, not `_schedule_redraw` - the preview only needs the
        # already-cached last render shape, so redrawing it shouldn't wait on
        # (or trigger) a full coalesced image re-render.
        self._dataset.chunk_grid_preview_changed.connect(self._update_chunk_grid)

        self._active_tool.active_tool_changed.connect(self._on_active_tool_changed)

        self._geometry.geometry_changed.connect(self._schedule_redraw)
        self._geometry.cosmetic_changed.connect(self._schedule_redraw)
        self._mask.mask_changed.connect(self._schedule_redraw)
        self._mask.cosmetic_changed.connect(self._schedule_redraw)
        self._background.background_model_changed.connect(self._schedule_redraw)
        self._chromatic.chromatic_model_changed.connect(self._schedule_redraw)
        self._chromatic.chromatic_model_changed.connect(self._update_chromatic_tab_state)
        self._update_chromatic_tab_state()
        self._background.background_model_changed.connect(self._update_background_tab_state)
        self._update_background_tab_state()

        # ROI/selection changes only move the overlay, never the pixels - but
        # they still go through the same path. Splitting "redraw overlay only"
        # out is a real optimization once there is a dataset big enough to
        # measure it against; guessing at it now would be the premature kind
        # CLAUDE.md's performance rules warn about.
        self._roi_toolbox.geometry_changed.connect(self._schedule_redraw)
        self._roi_toolbox.cosmetic_changed.connect(self._schedule_redraw)
        self._selection.cube_changed.connect(self._schedule_redraw)
        self._selection.wavelength_changed.connect(self._schedule_redraw)
        self._selection.roi_selection_changed.connect(self._schedule_redraw)

        # Keeps the nav widgets truthful for *any* source of a selection
        # change, not just this panel's own spin/slider handlers - e.g. the
        # "jump to reference" button below, or a future panel that also
        # calls `SelectionModule.set_cube`/`set_wavelength`. Matches this
        # panel's own "redraw because the module emitted a change" rule
        # (module docstring) - these are the same rule applied to the nav
        # widgets, not just the canvas.
        self._selection.cube_changed.connect(self._on_selection_cube_changed)
        self._selection.wavelength_changed.connect(self._on_selection_wavelength_changed)
        self._reference_frame.reference_frame_changed.connect(self._update_reference_highlight)

        # The highlight overlay tints the already-displayed pixel array
        # (`_current_display_image`, set in `_on_rendered`) - a range change
        # never needs a new render, so this goes straight to
        # `_update_highlight_overlay`, not `_schedule_redraw`.
        self._highlight_range.range_changed.connect(self._on_highlight_range_changed)

    # -- theming --------------------------------------------------------------

    def refresh_theme(self) -> None:
        """Re-applies the active theme's canvas background.

        Pyqtgraph's ``GraphicsLayoutWidget`` draws its own canvas and does
        not respond to Qt stylesheets/palette at all - unlike the rest of
        this app's chrome, it needs to be told about a theme switch
        explicitly (design doc §6, ``docs/rewrite_gui_shell_design_
        2026-09.md``). Called once at construction (see ``_build_ui``) and
        again by the shell on every live theme switch - the overlay curve
        colors are deliberately theme-invariant (see ``lspr_ui``'s
        ``GuiTheme`` docstring) so only the background changes here.

        Also re-renders the tool info icon, whose color is baked into a
        themed pixmap - but only once that widget exists: this method's
        first call happens mid-``_build_ui``, before it does."""
        self._view.setBackground(get_active_theme().toolbar_bg)
        if hasattr(self, "_tool_info"):
            self._refresh_tool_info(self._active_tool.active())
        if hasattr(self, "_crop_controls"):
            self._crop_controls.refresh_theme(get_active_theme())
        if hasattr(self, "_measure_controls"):
            self._measure_controls.refresh_theme(get_active_theme())
        if hasattr(self, "_cursor_overlay"):
            self._cursor_overlay.refresh_theme(get_active_theme())
        if hasattr(self, "_canvas_tools"):
            self._canvas_tools.refresh_theme(get_active_theme())
        if hasattr(self, "_mask_overlay_controls"):
            self._mask_overlay_controls.refresh_theme(get_active_theme())
        if hasattr(self, "_view_mask_overlay_controls"):
            self._view_mask_overlay_controls.refresh_theme(get_active_theme())
        for name in ("_roi_sample_controls", "_roi_reference_controls"):
            if hasattr(self, name):
                getattr(self, name).refresh_theme(get_active_theme())
        if hasattr(self, "_roi_labels_button"):
            style_general_icon_button(self._roi_labels_button)
            self._refresh_roi_labels_icon()
        for name in (
            "_view_separator", "_view_separator_2", "_view_separator_3", "_view_separator_4",
            "_roi_separator", "_roi_separator_2", "_roi_separator_3",
        ):
            if hasattr(self, name):
                getattr(self, name).setStyleSheet(f"color: {get_active_theme().control_border};")
        if hasattr(self, "_mask_scope_toggle"):
            self._mask_scope_toggle.refresh_theme(get_active_theme())
        if hasattr(self, "_mask_scope_separator"):
            self._mask_scope_separator.setStyleSheet(f"color: {get_active_theme().control_border};")
        if hasattr(self, "_highlight_overlay_controls"):
            self._highlight_overlay_controls.refresh_theme(get_active_theme())
        if hasattr(self, "_mask_visibility_separator"):
            self._mask_visibility_separator.setStyleSheet(f"color: {get_active_theme().control_border};")
        if hasattr(self, "_mask_edit_separator"):
            self._mask_edit_separator.setStyleSheet(f"color: {get_active_theme().control_border};")
        if hasattr(self, "_mask_general_separator"):
            self._mask_general_separator.setStyleSheet(f"color: {get_active_theme().control_border};")
        if hasattr(self, "_mask_edit_picker"):
            self._mask_edit_picker.refresh_theme(get_active_theme())
        for label_attr in (
            "_mask_general_label",
            "_mask_state_label",
            "_mask_visibility_label",
            "_highlight_visibility_label",
            "_view_mask_label",
            "_view_chromatic_label",
            "_view_background_label",
            "_view_scale_bar_label",
            "_mask_edit_label",
            "_mask_png_label",
            "_roi_sample_label",
            "_roi_reference_label",
            "_roi_labels_caption",
        ):
            if hasattr(self, label_attr):
                getattr(self, label_attr).setStyleSheet(group_label_style())
        if hasattr(self, "_tool_ribbon"):
            self._tool_ribbon.refresh_theme(get_active_theme())
        if hasattr(self, "_area_picker"):
            self._area_picker.refresh_theme(get_active_theme())
        if hasattr(self, "_background_tab"):
            self._background_tab.refresh_theme(get_active_theme())
        if hasattr(self, "_cursor_readout"):
            self._style_cursor_readout()
        if hasattr(self, "_controls_bar"):
            self._refresh_controls_bar_theme()
        if hasattr(self, "_top_bar"):
            self._refresh_top_bar_theme()

    def _style_cursor_readout(self) -> None:
        theme = get_active_theme()
        self._cursor_readout.setStyleSheet(
            f"color: {theme.text_primary}; background: rgba(0, 0, 0, 140); padding: 1px 4px; border-radius: 2px;"
        )

    def _set_cursor_readout(self, text: str) -> None:
        """Sink for `CursorOverlay`: shows *text* in the canvas's top-left
        corner, hides the label for an empty string."""
        self._style_cursor_readout()
        self._cursor_readout.setText(text)
        self._cursor_readout.adjustSize()
        self._cursor_readout.setVisible(bool(text))
        self._cursor_readout.raise_()
        if not text:
            self.cursor_value_changed.emit(None)

    def area_selection(self) -> AreaSelectionModule:
        """The shared area selection - editors read it from here so each
        front door needs no extra constructor argument."""
        return self._area_selection

    def selection_raw_mask(self, raw_shape: tuple[int, int]) -> np.ndarray | None:
        """The area selection as a raw-space editable mask (the space mask
        edits are authored in), or `None` when nothing is selected."""
        if self._last_image_shape is None:
            return None
        region = self._area_selection.mask(self._last_image_shape)
        if region is None:
            return None
        return area_selection_to_raw(region, raw_shape, self._geometry.settings())

    def active_ribbon_category(self) -> str:
        return self._tool_ribbon.current_category()

    def _refresh_controls_bar_theme(self) -> None:
        """No border (2026-09-30, maintainer request - reverses the subtle
        seam line added 2026-09-27/30) - the bar now sits flush against the
        canvas above it (zero spacing, see `_build_ui`'s outer layout), and
        the maintainer found the line distracting rather than clarifying.
        Kept as a live-theme-switch hook (called from `refresh_theme`) even
        though it sets no border today, since some future styling here may
        still need to react to a theme switch."""
        self._controls_bar.setStyleSheet("#imageNavigationBar { border: none; }")

    def _refresh_top_bar_theme(self) -> None:
        """A single-pixel seam along the bottom edge, where this bar meets
        the canvas below it (2026-09-30, maintainer request - "make there a
        bo[r]der on the bottom to separate it from the image area") - same
        convention as `_refresh_controls_bar_theme`'s old border (only the
        edge that actually touches the canvas is bordered)."""
        theme = get_active_theme()
        self._top_bar.setStyleSheet(f"#imageTopBar {{ border: none; border-bottom: 1px solid {theme.toolbar_border}; }}")

    # -- dataset lifecycle --------------------------------------------------

    def _on_dataset_loaded(self, _dataset: object) -> None:
        self._refresh_navigation_ranges()
        self._schedule_redraw()

    def _on_dataset_cleared(self) -> None:
        """Reset this panel's own view state. `DatasetModule.clear_dataset`
        deliberately clears only its own reference and expects every module
        and panel holding dataset-derived state to reset itself (see its
        module docstring) - this is the first subscriber to actually do so."""
        self._image_item.clear()
        self._no_data_item.hide()
        self._mask_tint.item.hide()
        self._mask_overlay_state = None
        self._highlight_tint.item.hide()
        self._current_display_image = None
        self._clear_roi_curves()
        self._chunk_grid_curve.clear()
        self._crop_outline_curve.clear()
        self._last_image_shape = None
        self._area_tool.set_image_shape(None)
        self._refresh_navigation_ranges()
        self._set_frame_status("No dataset loaded.")
        self.image_cleared.emit()
        # A tool with no image under it has nothing to do; the Workflow
        # panel's buttons follow this signal.
        self._active_tool.clear()

    def _refresh_navigation_ranges(self) -> None:
        """Point the cube/wavelength controls at what the dataset actually
        has. Blocked while being reprogrammed so that re-ranging them
        doesn't emit a spurious "the user changed the wavelength"."""
        cubes = self._dataset.spectral_cubes()
        with _blocked(self._cube_spin):
            self._cube_spin.setEnabled(bool(cubes))
            self._cube_spin.setRange(min(cubes) if cubes else 0, max(cubes) if cubes else 0)
            if cubes:
                self._cube_spin.setValue(self._current_cube())
        with _blocked(self._cube_slider):
            self._cube_slider.setEnabled(bool(cubes))
            self._cube_slider.setMinimum(0)
            self._cube_slider.setMaximum(max(len(cubes) - 1, 0))
            self._cube_slider.setSingleStep(1)
            self._cube_slider.setPageStep(1)
            self._cube_slider.set_ticks(list(cubes), self._cube_slider_major_ticks(cubes))
            if cubes and self._current_cube() in cubes:
                self._cube_slider.setValue(cubes.index(self._current_cube()))
        self._refresh_wavelength_range()
        self._update_reference_highlight()

    def _refresh_wavelength_range(self) -> None:
        """Re-fetches the *current cube's* wavelength set, not the dataset's
        whole/union list - a cube can be short a wavelength another cube
        has (`DatasetModule.wavelengths_for_cube`'s own docstring), so this
        must be called on every cube change, not just once per dataset load
        (the stable app assumes one fixed wavelength list for the whole
        dataset - see docs/image_area_slider_redesign.md - which does not
        hold here)."""
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        wavelength = self._current_wavelength(wavelengths)
        with _blocked(self._wavelength_spin):
            self._wavelength_spin.setEnabled(bool(wavelengths))
            if wavelengths:
                self._wavelength_spin.setRange(min(wavelengths), max(wavelengths))
                self._wavelength_spin.setValue(wavelength)
            else:
                self._wavelength_spin.setRange(0.0, 0.0)
        with _blocked(self._wavelength_slider):
            self._wavelength_slider.setEnabled(bool(wavelengths))
            self._wavelength_slider.setMinimum(0)
            self._wavelength_slider.setMaximum(max(len(wavelengths) - 1, 0))
            self._wavelength_slider.setSingleStep(1)
            self._wavelength_slider.setPageStep(1)
            # No caller-side wiring needed for the scale-break glyph -
            # `DataAxisSlider` detects an unusually large consecutive gap in
            # `wavelengths` itself (e.g. a real 0 nm dark/reference frame
            # followed by the first real spectral wavelength) and draws it
            # automatically. See that widget's module docstring for why an
            # earlier same-day version that invented a synthetic "0" tick
            # here was wrong.
            self._wavelength_slider.set_ticks(list(wavelengths), self._wavelength_slider_major_ticks(wavelengths))
            if wavelengths and wavelength in wavelengths:
                self._wavelength_slider.setValue(wavelengths.index(wavelength))
        self._refresh_wavelength_completer_model(wavelengths)

    def _current_cube(self) -> int:
        return int(self._selection.current_cube())

    def _current_wavelength(self, wavelengths: tuple[float, ...] | None = None) -> float:
        """The selected wavelength, snapped to one the current cube actually
        has. A cube can be short a wavelength another cube has (see
        `DatasetModule.wavelengths_for_cube`), so "the selected wavelength"
        is not always available here - showing the nearest one it does have
        beats showing an error for an ordinary dataset."""
        if wavelengths is None:
            wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        selected = self._selection.current_wavelength()
        if not wavelengths:
            return float(selected)
        if selected in wavelengths:
            return float(selected)
        return float(min(wavelengths, key=lambda wl: abs(wl - selected)))

    # -- navigation ---------------------------------------------------------
    # Two ways to change the same value (slider drag, spin box edit) both go
    # through `SelectionModule.set_cube`/`set_wavelength` - never straight to
    # the other widget - so the module stays the single source of truth
    # (module docstring's one-way flow) and `_on_selection_*_changed` below
    # is the only place either widget's *displayed* value is set.

    def _on_cube_spin_changed(self, value: int) -> None:
        self._selection.set_cube(int(value))

    def _on_cube_slider_changed(self, index: int) -> None:
        cubes = self._dataset.spectral_cubes()
        if not cubes or index >= len(cubes):
            return
        self._selection.set_cube(int(cubes[index]))

    def _on_wavelength_spin_changed(self, value: float) -> None:
        self._selection.set_wavelength(float(value))

    def _on_wavelength_slider_changed(self, index: int) -> None:
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        if not wavelengths or index >= len(wavelengths):
            return
        self._selection.set_wavelength(float(wavelengths[index]))

    def _on_selection_cube_changed(self, _cube_index: int) -> None:
        # The wavelength set can differ per cube, so a cube change must
        # re-range the wavelength controls too, not just re-display the
        # cube controls' new value.
        with _blocked(self._cube_spin):
            self._cube_spin.setValue(self._current_cube())
        cubes = self._dataset.spectral_cubes()
        with _blocked(self._cube_slider):
            if self._current_cube() in cubes:
                self._cube_slider.setValue(cubes.index(self._current_cube()))
        self._refresh_wavelength_range()
        self._update_reference_highlight()

    def _on_selection_wavelength_changed(self, _wavelength: float) -> None:
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        wavelength = self._current_wavelength(wavelengths)
        with _blocked(self._wavelength_spin):
            if wavelengths:
                self._wavelength_spin.setValue(wavelength)
        with _blocked(self._wavelength_slider):
            if wavelengths and wavelength in wavelengths:
                self._wavelength_slider.setValue(wavelengths.index(wavelength))
        self._update_reference_highlight()

    # -- wavelength jump field (QCompleter) ----------------------------------

    def _refresh_wavelength_completer_model(self, wavelengths: tuple[float, ...]) -> None:
        decimals = self._wavelength_spin.decimals()
        texts = [f"{value:.{decimals}f}" for value in wavelengths]
        self._wavelength_completer.setModel(QStringListModel(texts, self))

    def _on_wavelength_completion_activated(self, text: str) -> None:
        line_edit = self._wavelength_spin.lineEdit()
        line_edit.setStyleSheet("")
        line_edit.setText(text)
        self._wavelength_spin.interpretText()

    def _on_wavelength_text_edited(self, text: str) -> None:
        line_edit = self._wavelength_spin.lineEdit()
        stripped = text.strip()
        if not stripped or not self._dataset.wavelengths_for_cube(self._current_cube()):
            line_edit.setStyleSheet("")
            return
        self._wavelength_completer.setCompletionPrefix(stripped)
        if self._wavelength_completer.completionCount() == 0:
            line_edit.setStyleSheet(f"border: 1px solid {get_active_theme().accent_red};")
        else:
            line_edit.setStyleSheet("")

    # -- reference-frame highlight (ReferenceFrameModule) --------------------

    def _resolve_reference_frame(self) -> tuple[int, float] | None:
        """Same resolution rule `panels/workflow/reference_frame_row.py`
        uses: Auto mirrors whatever is currently being viewed live; Manual
        reads the stored snapshot. Duplicated here rather than shared,
        matching this codebase's "one backend, several front doors"
        convention - `ReferenceFrameModule` deliberately holds no
        `SelectionModule` reference (see its own docstring), so every
        front door resolves Auto mode itself."""
        if self._reference_frame.mode() == MODE_AUTO:
            return (self._selection.current_cube(), self._selection.current_wavelength())
        return self._reference_frame.manual_frame()

    def _update_reference_highlight(self) -> None:
        """Highlights the slider handle (gold for cube, green for
        wavelength) whenever the currently-displayed cube/wavelength is the
        reference - ported from the stable app's
        `_update_reference_navigation_styles` (spin-box background recolor
        omitted: this panel's spin boxes are plain `QSpinBox`/
        `GuidedValueSpinBox`, and the slider handle color already carries
        the same information without a second, redundant cue)."""
        reference = self._resolve_reference_frame()
        if reference is None:
            self._cube_slider.set_reference_highlight(None)
            self._wavelength_slider.set_reference_highlight(None)
            return
        reference_cube, reference_wavelength = reference
        cube_is_reference = int(self._current_cube()) == int(reference_cube)
        wavelength_is_reference = abs(float(self._current_wavelength()) - float(reference_wavelength)) < 1e-6
        self._cube_slider.set_reference_highlight(_REFERENCE_CUBE_HIGHLIGHT if cube_is_reference else None)
        self._wavelength_slider.set_reference_highlight(
            _REFERENCE_WAVELENGTH_HIGHLIGHT if wavelength_is_reference else None
        )

    # -- slider tick labels ---------------------------------------------------

    @staticmethod
    def _wavelength_slider_major_ticks(values: tuple[float, ...]) -> dict[int, str]:
        """See `slider_ticks.wavelength_slider_major_ticks`."""
        return wavelength_slider_major_ticks(values)

    @staticmethod
    def _cube_slider_major_ticks(values: tuple[int, ...]) -> dict[int, str]:
        """See `slider_ticks.cube_slider_major_ticks`."""
        return cube_slider_major_ticks(values)

    # -- rendering ----------------------------------------------------------

    def _schedule_redraw(self, *_args: object) -> None:
        """Coalesce many events into one redraw ~100ms later (sketch §8).
        Takes ``*_args`` so it can be connected directly to signals carrying
        a payload without a lambda per connection."""
        self._redraw_timer.start()

    def _redraw(self) -> None:
        """Snapshot every module's current state and hand it to the renderer.

        Only the *pixels* are rendered off-thread. Overlays are drawn here,
        synchronously, because they are a few thousand points of pure numpy
        and because they must track a drag with no perceptible lag - a
        coalesced 100ms round trip to a worker would make dragging an ROI
        feel broken."""
        self._draw_overlays()

        cubes = self._dataset.spectral_cubes()
        if not cubes:
            self._update_mask_overlay(None, None, None, hidden=True)
            self._current_display_image = None
            self._highlight_tint.item.hide()
            return
        cube_index = self._current_cube()
        wavelength_nm = self._current_wavelength()
        frame = (cube_index, wavelength_nm)

        authored_mask = None
        warp_affine = None
        resolution = self._mask.resolve_mask_source(frame)
        if resolution is not None:
            authored_frame, authored_mask, _scope = resolution
            if authored_frame != frame:
                warp_affine = self._chromatic.affine_between(authored_frame, frame)

        geometry = self._geometry.settings()
        preview_active = self._active_tool.active() in _PREVIEW_TOOLS
        # The overlay tint must match the *displayed* geometry exactly - a
        # preview tool shows the image uncropped (see below), a different
        # coordinate space than `authored_mask`/`warp_affine` were resolved
        # for, so it hides then, the same "wrong coordinate space" reasoning
        # `_draw_overlays` already applies to the ROI overlay above.
        self._update_mask_overlay(authored_mask, geometry, warp_affine, hidden=preview_active)
        if preview_active and geometry.crop.enabled:
            # Uncropped preview - the crop is drawn as an outline instead.
            geometry = replace(geometry, crop=CropDefinition())

        self._latest_serial = next(self._serial)
        self._renderer.submit(
            RenderRequest(
                cube_index=cube_index,
                wavelength_nm=wavelength_nm,
                geometry=geometry,
                background=self._background.settings(),
                authored_mask=authored_mask,
                mask_warp_affine=warp_affine,
                # Every ROI, not the selection - these feed the background
                # estimate's exclusion, not the overlay (see RenderRequest).
                # `_redraw` already re-runs on every ROI edit, so this adds
                # no re-render that wasn't happening anyway.
                rois=tuple(self._roi_toolbox.rois()),
                detection=self._roi_toolbox.detection_settings(),
                serial=self._latest_serial,
                show_background=self._show_background,
            )
        )

    def _on_rendered(self, result: RenderResult) -> None:
        if result.request.serial != self._latest_serial:
            # Superseded while in flight - dropping it is the point (see
            # render.py's "latest request wins"), not an error.
            return
        if result.error is not None or result.image is None:
            # A render error, not "what frame is displayed" - goes to the
            # status bar (`tool_status_changed`), not the dock title bar
            # subtitle (see `frame_status_changed`'s docstring).
            self._on_tool_status(f"Cannot show this frame: {result.error}")
            self._image_item.clear()
            self._no_data_item.hide()
            return
        image = np.asarray(result.image, dtype=np.float32)
        showing_estimate = result.request.show_background
        if showing_estimate:
            self._show_background_estimate(image, result.request)
        else:
            self._image_item.setImage(image, autoLevels=True)
            finite = image[np.isfinite(image)]
            self._last_data_range = (float(finite.min()), float(finite.max())) if finite.size else None
            checker = no_data_overlay_rgba(image)
            if checker is None:
                self._no_data_item.hide()
            else:
                self._no_data_item.setImage(checker, autoLevels=False)
                self._no_data_item.show()
            self._current_display_image = image
            self._update_highlight_overlay()
        if not self._view_range_restored:
            # Once only, ever, on this panel's first successful render - a
            # later frame navigation must never snap the view back to this
            # saved position (that would fight the user's own panning/
            # zooming for the rest of the session). `None` (first-ever
            # launch, or a settings file with nothing saved yet) leaves
            # pyqtgraph's own auto-range in charge, same as before this
            # existed.
            self._view_range_restored = True
            if self._initial_view_range is not None:
                x_range, y_range = self._initial_view_range
                self._plot.vb.setRange(xRange=x_range, yRange=y_range, padding=0.0)
        if not showing_estimate:
            # The estimate is not data: the Histogram etc. keep the real frame.
            self.image_rendered.emit(image, result.request.cube_index, result.request.wavelength_nm)
            self._set_frame_status(f"Cube {result.request.cube_index}, {result.request.wavelength_nm:.0f} nm")
        self._last_image_shape = result.image.shape[:2]
        self._area_tool.set_image_shape(self._last_image_shape)
        # The crop tool's clamp bound - correct the instant Crop is active
        # (the frame it renders is the *uncropped* one, `_PREVIEW_TOOLS`),
        # briefly stale (the smaller, cropped shape) right as the tool is
        # first switched on, self-correcting once the next preview render
        # lands ~100ms later (`_schedule_redraw` already runs on every
        # active-tool change).
        self._crop_tool.set_frame_size(result.image.shape[1], result.image.shape[0])
        self._update_chunk_grid()

    def _show_background_estimate(self, estimate: np.ndarray, request: RenderRequest) -> None:
        """Display the background estimate in place of the image. Only the
        picture changes: `_current_display_image` (the cursor readout, the
        Histogram, the highlight tint) keeps describing the real data, and
        the levels follow the data's own range when known - an auto-stretched
        near-flat background would look far more uneven than it is."""
        levels = self._last_data_range
        if levels is not None and levels[1] > levels[0]:
            self._image_item.setImage(estimate, autoLevels=False, levels=levels)
        else:
            self._image_item.setImage(estimate, autoLevels=True)
        self._no_data_item.hide()
        self._highlight_tint.item.hide()
        finite = estimate[np.isfinite(estimate)]
        spread = f", range {finite.min():.0f}-{finite.max():.0f}" if finite.size else ""
        self._set_frame_status(
            f"Cube {request.cube_index}, {request.wavelength_nm:.0f} nm - background estimate{spread}"
        )

    # -- viewport persistence -------------------------------------------------

    def _on_view_range_changed(self, _vb: object, _ranges: object) -> None:
        """`ViewBox.sigRangeChanged` fires on every pan/zoom step and on
        every auto-range refit after a new render - both are real "this is
        where the view is now" moments, so both restart the debounce rather
        than trying to tell them apart. Reads the range back from the
        `ViewBox` itself when the timer fires (`_emit_view_range_changed`),
        not from this signal's own arguments, so a burst of these only ever
        reports the final, settled range."""
        self._view_range_persist_timer.start()

    def _emit_view_range_changed(self) -> None:
        x_range, y_range = self._plot.vb.viewRange()
        self.view_range_changed.emit(float(x_range[0]), float(x_range[1]), float(y_range[0]), float(y_range[1]))

    # -- overlays -----------------------------------------------------------

    def _draw_overlays(self) -> None:
        self._draw_crop_outline()
        self._draw_landmarks()
        self._draw_scale_bar()
        self._draw_roi_overlay()

    def _draw_roi_overlay(self) -> None:
        """The ROI circles, reference rings and labels. Also called alone when
        a ROI display option changes, so that needs no new image render."""
        if self._active_tool.active() in _PREVIEW_TOOLS:
            # ROI positions are in cropped/processed space; over the
            # uncropped preview they would be drawn in the wrong place.
            self._clear_roi_curves()
            return

        rois = self._roi_toolbox.rois()
        if not rois:
            self._clear_roi_curves()
            return

        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        detection = self._roi_toolbox.detection_settings()
        selected = self._selection.selected_roi_ids()

        # Everything below is vectorized over the ROIs (roi/overlay_geometry.py):
        # the old per-ROI loop cost ~0.27 ms per ROI per redraw. The circles
        # are drawn around each ROI's *display* centre, bent by the affine's
        # linear part only - the translation is already in the centre.
        centers = self._roi_toolbox.display_positions(frame, affine)
        sample_diameters = np.fromiter((roi.sample_diameter_px for roi in rois), dtype=np.float64, count=len(rois))
        is_selected = np.fromiter((roi.area_roi_id in selected for roi in rois), dtype=bool, count=len(rois))

        # One curve per colour. Checking a colour string builds a QColor, so
        # each distinct string is resolved once, not once per ROI.
        resolved: dict[str | None, str] = {}
        indices_by_color: dict[str, list[int]] = {}
        for index, roi in enumerate(rois):
            if is_selected[index]:
                continue  # drawn in the highlight colour below, whatever its own
            key = roi.sample_color_hex
            if key not in resolved:
                resolved[key] = _roi_overlay_color(key, self._roi_sample_color)
            indices_by_color.setdefault(resolved[key], []).append(index)
        by_color = {
            color: circle_outlines(centers[indices], sample_diameters[indices], affine, n_points=_CIRCLE_POINTS)
            for color, indices in indices_by_color.items()
        }

        ring_diameters = np.asarray(
            [
                effective_reference_diameters(
                    roi, detection.reference_inner_diameter_px, detection.reference_outer_diameter_px
                )
                for roi in rois
            ],
            dtype=np.float64,
        )  # (N, 2): inner, outer
        reference_x, reference_y = circle_outlines(
            np.vstack((centers, centers)),
            np.concatenate((ring_diameters[:, 0], ring_diameters[:, 1])),
            affine,
            n_points=_CIRCLE_POINTS,
        )
        # Selected ROIs are all drawn in the highlight colour, whatever their own, so the selection reads at a glance.
        selection_x, selection_y = circle_outlines(
            centers[is_selected], sample_diameters[is_selected], affine, n_points=_CIRCLE_POINTS
        )
        labels: list[tuple[float, float, float, str]] = []
        if self._roi_labels_visible:
            labels = [
                (float(cx), float(cy), float(roi.sample_diameter_px) / 2.0, label_text(roi.area_roi_id, roi.label))
                for roi, (cx, cy) in zip(rois, centers, strict=True)
            ]

        self._set_sample_curves(by_color)
        self._reference_curve.setData(reference_x, reference_y)
        self._selection_curve.setData(selection_x, selection_y)
        self._roi_label_item.set_labels(labels)

    def _clear_roi_curves(self) -> None:
        self._set_sample_curves({})
        self._reference_curve.clear()
        self._selection_curve.clear()
        self._roi_label_item.set_labels([])

    def _style_roi_curve(self, curve: pg.PlotDataItem, color_hex: str, alpha: float, width: float) -> None:
        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        curve.setPen(pg.mkPen(color, width=width))

    def _apply_roi_overlay_style(self) -> None:
        """Put the ROI display options on the curves: colour with its
        transparency, and shown or hidden. A hidden sample overlay hides the
        selection highlight too (they are the same circles). The default
        colour decides which curve a colourless ROI is on, so the circles are
        redrawn as well."""
        for color, curve in self._sample_curves.items():
            self._style_roi_curve(curve, color, self._roi_sample_alpha, _SAMPLE_WIDTH)
            curve.setVisible(self._roi_sample_visible)
        self._selection_curve.setVisible(self._roi_sample_visible)
        self._style_roi_curve(self._reference_curve, self._roi_reference_color, self._roi_reference_alpha, _REFERENCE_WIDTH)
        self._reference_curve.setVisible(self._roi_reference_visible)
        if self._last_image_shape is not None:
            self._draw_roi_overlay()

    def _on_roi_overlay_changed(self, kind: str, field: str, value: object) -> None:
        """A ROI display control changed: ``kind`` is "sample" or "reference",
        ``field`` "visible", "color" (a ``#rrggbb`` string) or "alpha"."""
        setattr(self, f"_roi_{kind}_{field}", value)
        self._apply_roi_overlay_style()
        if self._roi_overlay_store is not None:
            self._roi_overlay_store.set(f"image/roi_{kind}_{field}", value)

    def _on_roi_labels_toggled(self, shown: bool) -> None:
        self._roi_labels_visible = bool(shown)
        self._refresh_roi_labels_icon()
        if self._last_image_shape is not None:
            self._draw_roi_overlay()
        if self._roi_overlay_store is not None:
            self._roi_overlay_store.set("image/roi_labels", bool(shown))

    def _refresh_roi_labels_icon(self) -> None:
        theme = get_active_theme()
        on = self._roi_labels_button.isChecked()
        self._roi_labels_button.setIcon(
            load_tabler_icon(
                "label-important" if on else "label-off",
                color=theme.accent_blue if on else theme.text_dim,
                size=_RIBBON_ICON_SIZE * 2,
                stroke_width=2.1,
            )
        )

    def _sync_roi_overlay_controls(self) -> None:
        """Show the panel's ROI display options on the ribbon controls (after
        a restore) without those controls reporting them back."""
        self._roi_sample_controls.set_state(
            visible=self._roi_sample_visible, color=QColor(self._roi_sample_color), alpha=self._roi_sample_alpha
        )
        self._roi_reference_controls.set_state(
            visible=self._roi_reference_visible, color=QColor(self._roi_reference_color), alpha=self._roi_reference_alpha
        )
        blocked = self._roi_labels_button.blockSignals(True)
        self._roi_labels_button.setChecked(self._roi_labels_visible)
        self._roi_labels_button.blockSignals(blocked)
        self._refresh_roi_labels_icon()
        self._apply_roi_overlay_style()

    def _set_sample_curves(self, by_color: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
        """Draw each colour's circles on that colour's curve (one
        `PlotDataItem` per colour, circles joined by NaN separators, the same
        trick the single curve used). A curve is created the first time a
        colour appears and reused after, because making and removing graphics
        items is the expensive part of a redraw. Curves whose colour is no
        longer in use are emptied, and the oldest idle ones removed beyond
        `_MAX_IDLE_SAMPLE_CURVES`, so recolouring a large group over and over
        cannot pile up items. The default-colour curve is never removed."""
        for color, (xs, ys) in by_color.items():
            self._sample_curve_for(color).setData(xs, ys)
        idle = [color for color in self._sample_curves if color not in by_color]
        for color in idle:
            self._sample_curves[color].clear()
        removable = [color for color in idle if color != _DEFAULT_SAMPLE_COLOR]  # oldest first
        for color in removable[: max(0, len(removable) - _MAX_IDLE_SAMPLE_CURVES)]:
            self._plot.removeItem(self._sample_curves.pop(color))

    def _sample_curve_for(self, color_hex: str) -> pg.PlotDataItem:
        curve = self._sample_curves.get(color_hex)
        if curve is None:
            curve = self._add_curve(color_hex, width=_SAMPLE_WIDTH)
            self._style_roi_curve(curve, color_hex, self._roi_sample_alpha, _SAMPLE_WIDTH)
            curve.setVisible(self._roi_sample_visible)
            # Drawn where the single sample curve used to be: under the
            # reference rings and the selection highlight, whenever it was made.
            curve.stackBefore(self._reference_curve)
            self._sample_curves[color_hex] = curve
        return curve

    def _update_chromatic_tab_state(self, *_args: object) -> None:
        """Green "Chromatic" tab while a fitted correction is switched on, so
        it is visible even when another tab is open."""
        settings = self._chromatic.settings()
        applied = bool(settings.chromatic_correction_enabled and self._chromatic.models())
        self._tool_ribbon.set_tab_applied(CHROMATIC_TAB, applied)

    def _update_background_tab_state(self, *_args: object) -> None:
        """Green "Background" tab while background removal is applied."""
        self._tool_ribbon.set_tab_applied(BACKGROUND_TAB, self._background.settings().flatten_background_enabled)

    def _on_show_background_changed(self, shown: bool) -> None:
        self._show_background = bool(shown)
        self._schedule_redraw()
        self.background_view_changed.emit(self._show_background)

    def _image_aspect(self) -> float:
        """Width / height of the processed image, for shaping the landmark grid."""
        if self._last_image_shape is not None:
            height, width = self._last_image_shape
            return float(width) / max(float(height), 1.0)
        raw = self._dataset.raw_plane_shape()
        return float(raw[1]) / max(float(raw[0]), 1.0) if raw else 1.5

    def _on_show_landmarks_changed(self, visible: bool) -> None:
        self._landmark_overlay_visible = bool(visible)
        self._schedule_redraw()
        self.chromatic_view_changed.emit(self._landmark_overlay_visible, self._landmark_all_wavelengths)

    def _on_landmark_scope_changed(self, all_wavelengths: bool) -> None:
        self._landmark_all_wavelengths = bool(all_wavelengths)
        self._schedule_redraw()
        self.chromatic_view_changed.emit(self._landmark_overlay_visible, self._landmark_all_wavelengths)

    def _draw_scale_bar(self, *_args: object) -> None:
        """Lay the scale bar out for the current view and units (display only)."""
        settings = self._geometry.settings()
        um_per_px = (
            self._geometry.microns_per_pixel_scalar()
            if settings.display_units == "um" and self._geometry.can_display_micrometers()
            else None
        )
        shown = bool(settings.scale_bar_visible) and self._active_tool.active() not in _PREVIEW_TOOLS
        self._scale_bar_item.update_view(shown, um_per_px)

    def _draw_landmarks(self) -> None:
        """Chromatic landmarks (display only); drawing rules in
        `landmark_overlay.draw_landmarks`."""
        self._landmark_observed_item.clear()
        self._landmark_fitted_item.clear()
        self._landmark_line_item.clear()
        if not self._landmark_overlay_visible or self._active_tool.active() in _PREVIEW_TOOLS:
            return
        draw_landmarks(
            self._chromatic,
            self._dataset,
            self._landmark_observed_item,
            self._landmark_fitted_item,
            self._landmark_line_item,
            current_cube=self._current_cube(),
            current_wavelength=self._current_wavelength(),
            all_wavelengths=self._landmark_all_wavelengths,
        )

    def _draw_crop_outline(self) -> None:
        """The existing crop as a dashed rectangle over the uncropped
        preview - only while a preview tool is active. Crop coordinates are
        in the rotated+flipped canvas, which is exactly what the uncropped
        preview shows (transform order: rotate -> flip -> crop)."""
        crop = self._geometry.settings().crop
        if self._active_tool.active() not in _PREVIEW_TOOLS or not crop.enabled or crop.width <= 0 or crop.height <= 0:
            self._crop_outline_curve.clear()
            return
        x0, y0 = float(crop.x), float(crop.y)
        x1, y1 = x0 + float(crop.width), y0 + float(crop.height)
        self._crop_outline_curve.setData([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])

    # -- mask overlay (display-only, see mask_overlay_controls.py) ----------

    def _update_mask_overlay(
        self,
        authored_mask: np.ndarray | None,
        geometry: GeometrySettings | None,
        warp_affine: np.ndarray | None,
        *,
        hidden: bool,
    ) -> None:
        """Redraw the mask-overlay tint from an already-resolved mask -
        synchronous GUI-thread work, same as `_draw_overlays` (a few
        thousand boolean pixels, cheap next to a full image re-render).

        Caches its arguments as `self._mask_overlay_state` so a pure
        visibility/color/alpha change (`_on_mask_overlay_*` below) can
        redraw without re-resolving the mask or touching the async
        pixel-render pipeline at all - none of those three controls change
        what is *computed*, only how the already-resolved mask is drawn."""
        self._mask_overlay_state = (authored_mask, geometry, warp_affine, hidden)
        if hidden or not self._mask_tint.visible or authored_mask is None or geometry is None:
            self._mask_tint.item.hide()
            return
        mask = resolve_external_mask(authored_mask, geometry, warp_affine)
        if mask is None or not np.any(mask):
            self._mask_tint.item.hide()
            return
        self._mask_tint.paint(mask)

    def _redraw_mask_overlay_from_cache(self) -> None:
        if self._mask_overlay_state is not None:
            authored_mask, geometry, warp_affine, hidden = self._mask_overlay_state
            self._update_mask_overlay(authored_mask, geometry, warp_affine, hidden=hidden)

    def _emit_overlay_style(self, kind: str) -> None:
        tint = self._mask_tint if kind == "mask" else self._highlight_tint
        self.overlay_style_changed.emit(kind, tint.style())

    def _on_mask_overlay_visibility_changed(self, visible: bool) -> None:
        self._mask_tint.visible = bool(visible)
        self._redraw_mask_overlay_from_cache()
        self._emit_overlay_style("mask")

    def _on_mask_overlay_color_changed(self, color: QColor) -> None:
        self._mask_tint.color = QColor(color)
        self._redraw_mask_overlay_from_cache()
        self._emit_overlay_style("mask")

    def _on_mask_overlay_alpha_changed(self, alpha: float) -> None:
        self._mask_tint.alpha = float(alpha)
        self._redraw_mask_overlay_from_cache()
        self._emit_overlay_style("mask")

    # -- histogram highlight overlay (display-only, see histogram_highlight_overlay_controls.py) --

    def _update_highlight_overlay(self) -> None:
        """Redraw the histogram highlight-overlay tint from the cached,
        already-displayed pixel array (`_current_display_image`, set in
        `_on_rendered`) - synchronous GUI-thread work, same cost reasoning
        as `_update_mask_overlay`. A pure visibility/color/alpha change
        (`_on_highlight_overlay_*` below) and a range change
        (`_on_highlight_range_changed`) both land here; neither needs a new
        render, since the tint is a threshold over pixels already on
        screen - matches the stable app's own
        `_update_selected_intensity_overlay`."""
        if not self._highlight_tint.visible or self._current_display_image is None or self._show_background:
            self._highlight_tint.item.hide()
            return
        range_ = self._highlight_range.current_range()
        if range_ is None:
            self._highlight_tint.item.hide()
            return
        lower, upper = range_
        image = self._current_display_image
        selection_mask = (image >= lower) & (image <= upper)
        # Hide rather than tint when nothing is excluded (the whole image
        # falls in range) as well as when nothing is included - a full-image
        # tint carries no information, same reasoning the stable app applies
        # against its own fixed sensor-range bounds (here, against the
        # actually-displayed image's own range, since `HighlightRangeModule`
        # seeds to that, not a fixed 16-bit constant - see that module's
        # docstring).
        # Pixels without a value (NaN) are never selected and do not count as
        # "excluded" either: "everything in range" is judged on finite pixels.
        if not np.any(selection_mask) or bool(np.all(selection_mask[np.isfinite(image)])):
            self._highlight_tint.item.hide()
            return
        self._highlight_tint.paint(selection_mask)

    def _on_highlight_range_changed(self, _range: tuple[float, float] | None) -> None:
        self._update_highlight_overlay()

    def _on_highlight_overlay_visibility_changed(self, visible: bool) -> None:
        self._highlight_tint.visible = bool(visible)
        self._update_highlight_overlay()
        self._emit_overlay_style("highlight")

    def _on_highlight_overlay_color_changed(self, color: QColor) -> None:
        self._highlight_tint.color = QColor(color)
        self._update_highlight_overlay()
        self._emit_overlay_style("highlight")

    def _on_highlight_overlay_alpha_changed(self, alpha: float) -> None:
        self._highlight_tint.alpha = float(alpha)
        self._update_highlight_overlay()
        self._emit_overlay_style("highlight")

    # -- mask edit tool picker (mask_edit_tool_picker.py/mask_edit_panels.py) --

    def _on_mask_edit_tool_changed(self, tool: MaskEditTool) -> None:
        self._mask_edit_stack.setCurrentIndex(self._mask_edit_panel_order.index(tool))

    def _update_chunk_grid(self) -> None:
        """Draw (or clear) the Export section's chunk-grid preview - lines
        at every `chunk_size_px` boundary over the last rendered plane, so
        the maintainer can see how the chosen chunk size will actually
        divide up the image before exporting. Reads `DatasetModule` only
        (see its `chunk_grid_preview` docstring) - never touches the Export
        section's widgets."""
        enabled, chunk_px = self._dataset.chunk_grid_preview()
        if not enabled or self._last_image_shape is None or chunk_px <= 0:
            self._chunk_grid_curve.clear()
            return
        height, width = self._last_image_shape
        xs: list[float] = []
        ys: list[float] = []
        x = 0.0
        while x <= width:
            _append_polyline(xs, ys, np.array([x, x]), np.array([0.0, float(height)]))
            x += chunk_px
        y = 0.0
        while y <= height:
            _append_polyline(xs, ys, np.array([0.0, float(width)]), np.array([y, y]))
            y += chunk_px
        self._chunk_grid_curve.setData(xs, ys)

    # -- interaction --------------------------------------------------------

    def _on_active_tool_changed(self, tool: ImageTool | None) -> None:
        self._rotate_tool.set_active(tool is ImageTool.ROTATE)
        self._crop_tool.set_active(tool is ImageTool.CROP)
        self._measure_tool.set_active(tool is ImageTool.MEASURE)
        self._reposition_measure_controls()
        self._view.viewport().unsetCursor()  # drop any resize/move cursor left over from Crop
        if tool is ImageTool.SELECT_AREA:
            self._view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._area_tool.cancel_gesture()
        self._tool_status = ""
        self.tool_status_changed.emit("")  # drop a stale status from the tool just switched away from
        self._refresh_tool_info(tool)
        if tool is ImageTool.ROTATE:
            # So the arrow keys reach the tool without an extra click.
            self._view.setFocus()
        # Preview tools swap cropped <-> uncropped and show/hide the ROI overlay.
        self._schedule_redraw()

    def _on_tool_status(self, text: str) -> None:
        self._tool_status = text
        self.tool_status_changed.emit(text)

    def _set_frame_status(self, text: str) -> None:
        self._frame_status = text
        self.frame_status_changed.emit(text)

    def _info_text(self, tool: ImageTool | None) -> str:
        """The info icon's tooltip for the open ribbon tab (2026-10-06): the
        canvas controls of the active tool on the tool tabs ("Image tools",
        "ROIs"), a short explanation of the tab on the others."""
        category = self._tool_ribbon.current_category() if hasattr(self, "_tool_ribbon") else None
        return _TAB_INFO.get(category) or controls_text(tool)

    def _refresh_tool_info(self, tool: ImageTool | None) -> None:
        """Point the info icon's tooltip at the open tab's help (see
        `_info_text`; on the tool tabs, *tool*'s controls). Always shows
        something - every real tool has its own row in `image_controls.py`'s
        `_TOOL_CONTROLS` now; only no tool at all falls back to its
        plain-image row (left-click selects, plus the always-available
        drag/zoom).

        Rendered at `_ICON_SIZE * 2` and displayed at `_ICON_SIZE`
        (`style_bar_icon_button`'s `setIconSize`) - the same 2x-render-for-
        crispness convention `_ToolGroupButton.refresh` already uses for
        Select/Add ROI's icons, not a fresh choice."""
        theme = get_active_theme()
        render_size = _ICON_SIZE * 2
        self._tool_info.setIcon(load_tabler_icon("info-circle", color=theme.text_muted, size=render_size))
        self._tool_info.setToolTip(self._info_text(tool))

    def _in_view(self, scene_pos: object) -> bool:
        return bool(self._plot.vb.sceneBoundingRect().contains(scene_pos))

    def _on_scene_moved(self, scene_pos: object) -> None:
        # Cheap early-out: this fires on every mouse move over the scene.
        if self._active_tool.active() is ImageTool.CROP and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            handle = self._crop_tool.hover_handle(float(point.x()), float(point.y()))
            cursor = _CURSOR_FOR_CROP_HANDLE.get(handle)
            if cursor is None:
                self._view.viewport().unsetCursor()
            else:
                self._view.viewport().setCursor(cursor)
        if self._active_tool.active() is ImageTool.MEASURE and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            handle = self._measure_tool.hover_handle(float(point.x()), float(point.y()))
            if handle is None:
                self._view.viewport().unsetCursor()
            else:
                # Same "move" cursor as dragging Crop's interior - both mean
                # "drag this to reposition it".
                self._view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        if self._rotate_tool.first_point() is not None and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            self._rotate_tool.on_mouse_moved(float(point.x()), float(point.y()))
        if self._measure_tool.first_point() is not None and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            self._measure_tool.on_mouse_moved(float(point.x()), float(point.y()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming
        if watched is self._view and event.type() == QEvent.Type.KeyPress:
            if self._rotate_tool.handle_key(event.key(), event.modifiers()):
                event.accept()
                return True
            if self._crop_tool.handle_key(event.key()):
                event.accept()
                return True
            if self._measure_tool.handle_key(event.key()):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _on_scene_clicked(self, event: object) -> None:
        """Route a click: to the active tool, or - with no tool active -
        select the ROI under the cursor (clear the selection when the click
        lands on empty image). Ctrl/Shift toggles instead of replacing,
        matching the platform convention for multi-select lists. Only the
        left button selects; the middle button is for panning."""
        try:
            scene_pos = event.scenePos()
        except AttributeError:  # pragma: no cover - defensive against pyqtgraph versions
            return
        button = getattr(event, "button", lambda: Qt.MouseButton.LeftButton)()
        if self._active_tool.active() is ImageTool.ROTATE:
            if not self._in_view(scene_pos):
                return
            if button == Qt.MouseButton.LeftButton:
                p = self._plot.vb.mapSceneToView(scene_pos)
                self._rotate_tool.on_left_click(float(p.x()), float(p.y()))
            elif button == Qt.MouseButton.RightButton:
                self._show_rotate_context_menu(scene_pos)
            return
        if self._active_tool.active() is ImageTool.MEASURE:
            if not self._in_view(scene_pos):
                return
            if button == Qt.MouseButton.LeftButton:
                p = self._plot.vb.mapSceneToView(scene_pos)
                self._measure_tool.on_left_click(float(p.x()), float(p.y()))
            elif button == Qt.MouseButton.RightButton:
                self._show_measure_context_menu(scene_pos)
            return
        if self._active_tool.active() is ImageTool.CROP:
            # A plain (non-drag) left-click has nothing to do - dragging is
            # handled separately, by ImageViewBox's left-drag handler
            # (`_on_crop_drag_event`), since a real drag never reaches
            # `sigMouseClicked` at all (pyqtgraph routes it as a drag
            # event once the mouse has moved past its click threshold).
            if button == Qt.MouseButton.RightButton and self._in_view(scene_pos):
                self._show_crop_context_menu(scene_pos)
            return
        if self._active_tool.active() is ImageTool.SELECT_AREA:
            # The drag draws (`_on_select_area_drag_event`); only the menu is a click.
            if button == Qt.MouseButton.RightButton and self._in_view(scene_pos):
                self._show_select_area_context_menu(scene_pos)
            return
        if self._active_tool.active() is ImageTool.ADD_ROI:
            if not self._in_view(scene_pos):
                return
            if button == Qt.MouseButton.LeftButton:
                p = self._plot.vb.mapSceneToView(scene_pos)
                if self._in_selection(float(p.x()), float(p.y())):
                    self._roi_toolbox.add_roi(float(p.x()), float(p.y()))
            elif button == Qt.MouseButton.RightButton:
                self._show_add_roi_context_menu(scene_pos)
            return
        if button == Qt.MouseButton.RightButton and self._point_in_selection(scene_pos):
            self._context_menu([], scene_pos)  # plain Invert/Deselect menu
            return
        if button != Qt.MouseButton.LeftButton:
            return  # not a select gesture
        point = self._plot.vb.mapSceneToView(scene_pos)
        roi_id = self.roi_at(float(point.x()), float(point.y()))
        modifiers = getattr(event, "modifiers", lambda: Qt.KeyboardModifier.NoModifier)()
        additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))

        current = set(self._selection.selected_roi_ids())
        if roi_id is None:
            self._selection.set_roi_selection(current if additive else set())
            return
        if additive:
            current.symmetric_difference_update({roi_id})
            self._selection.set_roi_selection(current)
        else:
            self._selection.set_roi_selection({roi_id})

    def _show_rotate_context_menu(self, scene_pos: object) -> None:
        """Right-click while Rotate is active: a menu with a single "Cancel
        rotation" action, always enabled (2026-09-29, maintainer's spec) -
        it exits Rotate mode entirely, the same as clicking the Workflow
        panel's Rotate button again (`ActiveToolModule.set_active(ROTATE,
        False)` already drops any in-progress point 1 as a side effect of
        deactivating - `RotateLineTool.set_active`'s own `_clear_first_
        point()` call - so there is nothing extra to do first). Always
        being enabled is also what keeps the menu from ever having nothing
        clickable in it - see `context_menu.py`'s docstring for why that
        matters."""
        if self._context_menu([("Cancel rotation", True)], scene_pos) == "Cancel rotation":
            self._active_tool.set_active(ImageTool.ROTATE, False)

    def _show_measure_context_menu(self, scene_pos: object) -> None:
        """Right-click while Measure is active: a menu with a single
        "Cancel measurement" action, always enabled - same shape and
        reasoning as `_show_rotate_context_menu` (exits Measure mode
        entirely, the same as clicking the Workflow panel's Measure button
        again; always-enabled is what keeps the menu from ever having
        nothing clickable in it, see `context_menu.py`)."""
        if self._context_menu([("Cancel measurement", True)], scene_pos) == "Cancel measurement":
            self._active_tool.set_active(ImageTool.MEASURE, False)

    def _show_crop_context_menu(self, scene_pos: object) -> None:
        """Right-click while Crop is active: "Apply crop" (enabled only
        with something pending, `CropTool.has_pending_changes`) and
        "Cancel crop", always enabled - like Rotate's menu, it exits Crop
        mode entirely (`ActiveToolModule.set_active(CROP, False)`), the
        same as clicking the Workflow panel's Crop button again, dropping
        any not-yet-applied edit along the way. "Cancel" always being
        clickable is what keeps this menu from ever having nothing
        clickable in it, even with "Apply" grayed out - see
        `context_menu.py`'s docstring."""
        chosen = self._context_menu(
            [("Apply crop", self._crop_tool.has_pending_changes()), ("Cancel crop", True)], scene_pos
        )
        if chosen == "Apply crop":
            self._on_crop_apply_requested()
        elif chosen == "Cancel crop":
            self._active_tool.set_active(ImageTool.CROP, False)

    def _show_add_roi_context_menu(self, scene_pos: object) -> None:
        """Right-click while Add ROI is active: a menu with a single "Exit
        tool" action, always enabled - same shape as Rotate/Measure's own
        "Cancel ..." menus (`_show_rotate_context_menu`/`_show_measure_
        context_menu`), just not labeled "Cancel" since there is no
        in-progress point to drop - each click here is already a complete,
        independent action."""
        if self._context_menu([("Exit tool", True)], scene_pos) == "Exit tool":
            self._active_tool.set_active(ImageTool.ADD_ROI, False)

    def _show_select_area_context_menu(self, scene_pos: object) -> None:
        """Right-click while a selection tool is armed: "Exit tool" (leaves the
        draw tool, keeps the selection), plus Invert/Deselect inside it."""
        if self._context_menu([("Exit tool", True)], scene_pos) == "Exit tool":
            self._active_tool.set_active(ImageTool.SELECT_AREA, False)

    def _point_in_selection(self, scene_pos: object) -> bool:
        """True when a selection exists and the scene point lies inside the
        *editable* region (inside the shape, or outside it once inverted)."""
        if not self._area_selection.has_selection() or self._last_image_shape is None or not self._in_view(scene_pos):
            return False
        point = self._plot.vb.mapSceneToView(scene_pos)
        return self._area_selection.contains(float(point.x()), float(point.y()), self._last_image_shape)

    def _context_menu(self, actions: list[tuple[str, bool]], scene_pos: object) -> str | None:
        """`show_tool_context_menu` plus, when the click is inside the
        selection, "Invert selection" and "Deselect" (handled here). Returns
        the chosen *tool* action, or `None` if nothing or a selection entry was
        chosen. Selection entries are always enabled, so the menu never opens
        with nothing clickable (see `context_menu.py`)."""
        entries = list(actions)
        if self._point_in_selection(scene_pos):
            entries += [("Invert selection", True), ("Deselect", True)]
        if not entries:
            return None
        chosen = show_tool_context_menu(self, entries)
        if chosen == "Invert selection":
            self._area_selection.invert()
            return None
        if chosen == "Deselect":
            self._area_selection.clear()
            return None
        return chosen

    def _on_left_drag_event(self, ev: object) -> bool:
        """`ImageViewBox`'s single left-drag handler slot - dispatches to
        whichever tool (if any) claims the drag. Crop and Measure each
        decline (return `False`) unless *they* are the active tool, so at
        most one of them ever claims a given drag, and neither has any
        effect on plain ROI dragging."""
        return self._on_crop_drag_event(ev) or self._on_measure_drag_event(ev) or self._on_select_area_drag_event(ev)

    def _on_select_area_drag_event(self, ev: object) -> bool:
        """Draws the rectangle/lasso while a selection tool is armed."""
        if self._active_tool.active() is not ImageTool.SELECT_AREA:
            return False
        point = self._plot.vb.mapSceneToView(ev.scenePos())
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            return self._area_tool.begin_gesture(x, y)
        if ev.isFinish():
            self._area_tool.end_gesture()
            return True
        self._area_tool.update_gesture(x, y)
        return True

    def _in_selection(self, x: float, y: float) -> bool:
        """Whether display point (x, y) may be edited under the current area
        selection (always True with none)."""
        if self._last_image_shape is None:
            return not self._area_selection.has_selection()
        return self._area_selection.contains(x, y, self._last_image_shape)

    def _on_crop_drag_event(self, ev: object) -> bool:
        if self._active_tool.active() is not ImageTool.CROP:
            return False
        scene_pos = ev.scenePos()
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            if not self._in_view(scene_pos):
                return False
            return self._crop_tool.begin_gesture(x, y)
        if ev.isFinish():
            self._crop_tool.end_gesture()
            return True
        self._crop_tool.update_gesture(x, y)
        return True

    def _on_measure_drag_event(self, ev: object) -> bool:
        """Drags an already-placed point (maintainer's request, 2026-09-29
        - added after the click-twice-only version shipped). A drag that
        doesn't start on an existing point is left unclaimed - a *new* pair
        is placed by ordinary clicks (`on_left_click`), not by dragging
        empty space."""
        if self._active_tool.active() is not ImageTool.MEASURE:
            return False
        scene_pos = ev.scenePos()
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            if not self._in_view(scene_pos):
                return False
            return self._measure_tool.begin_gesture(x, y)
        if ev.isFinish():
            self._measure_tool.end_gesture()
            return True
        self._measure_tool.update_gesture(x, y)
        return True

    def _on_crop_apply_requested(self) -> None:
        """Apply and exit crop mode (maintainer's spec, 2026-09-29): a
        successful apply switches Crop off, which is what makes the panel
        actually show the cropped result - while any preview tool
        (`_PREVIEW_TOOLS`) is active the panel always renders the
        *uncropped* frame, on purpose, so staying in Crop mode after
        applying would keep showing the full frame with no visible change.
        A failed apply (nothing to apply, or Image Tools switched off)
        leaves the session exactly as it was."""
        if self._crop_tool.apply():
            self._active_tool.set_active(ImageTool.CROP, False)
        else:
            self._reposition_crop_controls()

    def _on_crop_tool_changed(self) -> None:
        """`CropTool.changed`: refresh the floating size-controls widget
        it does not own (see crop_size_controls.py's docstring). No redraw
        here - the rendered pixels never change until `apply()`, which
        already triggers one via `GeometryModule.geometry_changed`."""
        # Max *before* size: QSpinBox.setValue() clamps to whatever range is
        # already set, so on the very first activation (spin boxes still at
        # their construction-time range of [1, 1]) setting a pre-filled
        # rectangle's real width/height first would silently clip it to 1.
        frame = self._crop_tool.frame_size()
        if frame is not None:
            self._crop_controls.set_max_size(*frame)
        rect = self._crop_tool.rect()
        if rect is not None:
            self._crop_controls.set_size(rect[2], rect[3])
        self._reposition_crop_controls()

    def _reposition_crop_controls(self) -> None:
        """Moves the (real QWidget) size controls just under the
        rectangle's bottom-right corner - the apply button flush with the
        crop's right edge, the fields packed tightly to its left
        (maintainer's spec, 2026-09-29) - in the view's current screen
        pixels. Called on every rectangle change and on every pan/zoom
        (`sigTransformChanged`), since a `QWidget` child, unlike the
        pyqtgraph overlay items `CropTool` itself owns, does not track the
        view transform on its own."""
        rect = self._crop_tool.rect()
        if not self._crop_tool.is_active() or rect is None:
            self._crop_controls.setVisible(False)
            return
        x, y, w, h = rect
        scene_pos = self._plot.vb.mapViewToScene(QPointF(float(x + w), float(y + h)))
        view_pos = self._view.mapFromScene(scene_pos)
        margin = 4
        self._crop_controls.move(view_pos.x() - self._crop_controls.width(), view_pos.y() + margin)
        self._crop_controls.set_apply_enabled(self._crop_tool.has_pending_changes())
        self._crop_controls.setVisible(True)
        self._crop_controls.raise_()

    def _on_measure_tool_measured(self, dx_px: float, dy_px: float, is_fresh_placement: bool) -> None:
        """`MeasureLineTool.measured`: the current measurement changed -
        point 1 just placed (a reset), the cursor moving toward point 2
        before it's clicked, point 2's click committing the pair, or a
        later drag of either point (`MeasureLineTool.measured`'s own
        docstring has the full list). Show the floating controls with the
        current px deltas either way.

        **If a calibration already exists**, the tool doubles as a plain
        ruler: the um fields are live-filled from the existing microns-per-
        pixel scale on *every* update (maintainer's spec, 2026-09-29 -
        "moving... should automatically use this coefficient"). Typing a
        different value still works afterward (to re-calibrate against a
        different reference), it just gets overwritten by the next
        placement/hover/drag the same way a plain placement's 0 used to.

        **Without a calibration yet**, the um fields reset to 0 only when
        `is_fresh_placement` is `True` - point 1 just being placed, i.e. a
        brand-new measurement session starting - not on every hover/drag
        update after that: fine-tuning where point 2 lands, before or after
        it's clicked, must not throw away a distance already typed in for
        the first-time calibration this measurement is building toward."""
        self._measure_controls.set_deltas(dx_px, dy_px)
        if self._geometry.can_display_micrometers():
            settings = self._geometry.settings()
            self._measure_controls.set_um_values(
                abs(dx_px) * settings.microns_per_pixel_x, abs(dy_px) * settings.microns_per_pixel_y
            )
        elif is_fresh_placement:
            self._measure_controls.reset_um_fields()
        self._reposition_measure_controls()

    def _reposition_measure_controls(self) -> None:
        """Moves the floating calibration controls so the Apply button (not
        the widget's top-left corner) sits under the ruler's second point -
        or, while point 2 hasn't been clicked yet, under the live cursor
        (`current_anchor_point()`, maintainer's "live measurement" request,
        2026-09-29) - with the px/um fields to its left (maintainer's spec,
        2026-09-29) - same idea as `_reposition_crop_controls`, just
        anchored at a point in the middle of the widget instead of at one
        edge. Called on every placement, every hover/drag update, and every
        pan/zoom. Hidden whenever Measure isn't active or nothing has been
        placed/hovered yet."""
        point = self._measure_tool.current_anchor_point()
        if not self._measure_tool.is_active() or point is None:
            self._measure_controls.setVisible(False)
            return
        x, y = point
        scene_pos = self._plot.vb.mapViewToScene(QPointF(float(x), float(y)))
        view_pos = self._view.mapFromScene(scene_pos)
        margin = 4
        apply_center_x = self._measure_controls.apply_button_center_x()
        self._measure_controls.move(view_pos.x() - apply_center_x, view_pos.y() + margin)
        self._measure_controls.setVisible(True)
        self._measure_controls.raise_()

    def _on_measure_apply_requested(self, dx_um: float, dy_um: float) -> None:
        """`MeasureCalibrationControls.apply_requested`. A `ValueError`
        (no real dx/dy entered, or a zero pixel delta on the requested
        axis - `GeometryModule.apply_measurement_calibration`'s own three
        guards) is reported as a status message rather than raised, the
        same convention `RotateLineTool`/`CropTool` callers already use for
        this module's other guarded commands. A *successful* apply exits
        Measure mode (maintainer's spec, 2026-09-29 - "apply, the tool will
        cancel and settings will [be] applied"), the same shape as
        `_on_crop_apply_requested`: a failed attempt leaves the session
        exactly as it was, still in the tool, so a mistyped value doesn't
        also cost the placed ruler."""
        try:
            self._geometry.apply_measurement_calibration(dx_um, dy_um)
        except ValueError as exc:
            self._on_tool_status(str(exc))
            return
        self._on_tool_status("Measurement calibration applied - display units switched to micrometers.")
        self._active_tool.set_active(ImageTool.MEASURE, False)

    # -- cursor readout -------------------------------------------------------

    def _cursor_value_at(self, view_x: float, view_y: float) -> tuple[float, float, str] | None:
        """Floors to the pixel under the cursor and reads its displayed
        intensity - `_image_item.image` is exactly what is on screen
        (post crop/rotate/mask/CC/background, `_on_rendered`'s own float32
        array), so this reads no state this panel doesn't already own.
        `None` off the image entirely, matching `CursorOverlay`'s "show
        nothing" contract for an out-of-data cursor position."""
        image = self._image_item.image
        if image is None:
            return None
        col = int(np.floor(view_x))
        row = int(np.floor(view_y))
        height, width = image.shape[:2]
        if not (0 <= row < height and 0 <= col < width):
            return None
        value = float(image[row, col])
        self.cursor_value_changed.emit(value if np.isfinite(value) else None)
        return col + 0.5, row + 0.5, f"({col}, {row}) = {format_pixel_value(value)}"

    def roi_at(self, x: float, y: float) -> int | None:
        """The ROI whose sample aperture contains display-space point
        (x, y), or `None`. Public because it is the one piece of this
        panel's hit-testing worth driving directly from a test - clicking by
        screen coordinate is exactly what CLAUDE.md's testability rule says
        to avoid needing.

        Nearest-center wins when apertures overlap, so a small ROI sitting
        inside a large one stays reachable."""
        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        rois = self._roi_toolbox.rois()
        if not rois:
            return None
        centers = self._roi_toolbox.display_positions(frame, affine)
        distances = np.hypot(x - centers[:, 0], y - centers[:, 1])
        radii = np.fromiter((roi.sample_diameter_px for roi in rois), dtype=np.float64, count=len(rois)) / 2.0
        distances = np.where(distances <= radii, distances, np.inf)
        nearest = int(np.argmin(distances))  # first of equal distances wins, as before
        return rois[nearest].area_roi_id if np.isfinite(distances[nearest]) else None

    def _on_drag(self, roi_id: int, x: float, y: float) -> None:
        """Forwards a drag gesture to the owning module - never mutates ROI
        state directly."""
        if not self._in_selection(x, y):
            return  # the area selection also limits where an ROI may be moved
        self._roi_toolbox.request_move(roi_id, x, y)

    # -- teardown -----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Covers the case where this panel *is* shown as its own window
        (a detached/floating panel). The application-level `aboutToQuit`
        hook in `__init__` covers the normal in-a-tab case, which this
        never sees - see that comment. `stop()` is safe to call twice."""
        self._renderer.stop()
        super().closeEvent(event)


class _blocked:
    """Suppress a widget's signals for the duration of a `with` block.

    Programmatically re-ranging a spin box emits ``valueChanged``, which
    would be indistinguishable from the user turning it - and would call
    back into ``SelectionModule``, which would emit, which would schedule
    another redraw. Qt's own ``blockSignals`` is the intended tool; this is
    just a context manager so the restore can't be forgotten."""

    def __init__(self, widget: QWidget) -> None:
        self._widget = widget
        self._previous = False

    def __enter__(self) -> QWidget:
        self._previous = self._widget.blockSignals(True)
        return self._widget

    def __exit__(self, *_exc: object) -> None:
        self._widget.blockSignals(self._previous)


def _roi_overlay_color(color_hex: str | None, default: str) -> str:
    """The colour a ROI's sample circle is drawn in: its own stored colour, or
    ``default`` for a ROI with none (or one that is not a valid colour)."""
    if color_hex and QColor(color_hex).isValid():
        return color_hex.lower()
    return default


def _append_polyline(xs: list[float], ys: list[float], new_x: np.ndarray, new_y: np.ndarray) -> None:
    """Append one closed curve, separated from whatever came before by a
    NaN. ``PlotDataItem(connect="finite")`` breaks the line at non-finite
    points, which is what lets a single item draw many disjoint circles."""
    if xs:
        xs.append(float("nan"))
        ys.append(float("nan"))
    xs.extend(new_x.tolist())
    ys.extend(new_y.tolist())
