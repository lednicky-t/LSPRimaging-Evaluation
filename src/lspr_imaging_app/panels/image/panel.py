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
  mask-overlay tint's own shape (`TintOverlays.update_highlight`,
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
import math
import time
import logging
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QMenu,
    QApplication,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...image_tools import (
    ActiveToolModule,
    BackgroundModule,
    ChromaticModule,
    GeometryModule,
    ImageTool,
    MaskModule,
    MaskScopeModule,
)
from ...analysis.roi_tuning_module import TuningView
from ...image_tools.chromatic.affine import apply_affine_to_points
from ...image_tools.geometry.model import CropDefinition
from ...diagnostics.thread_times import thread_cpu_times
from ...image_tools.preprocess import resolve_external_mask
from ...roi import RoiToolbox
from .no_data import no_data_overlay_rgba
from ...selection import (
    AreaSelectionModule,
    CubeDisplayModule,
    HighlightRangeModule,
    ReferenceFrameModule,
    SelectionModule,
)
from ...selection.cube_display_module import MODE_CUBE, MODE_TIME
from ...selection.frame_roles_module import FrameRolesModule
from ..subtitle_column import SubtitleColumn
from ...image_tools.chromatic_auto_task import ChromaticAutoDetect
from ...image_tools.mask.mask_pointers import MaskPointersModule
from ...image_tools.mask_auto_task import MaskAutoTask
from ...image_tools.mask_export_task import MaskExportTask
from ...image_tools.mask_scan_task import TransientScanTask
from ..feature_map_task import FeatureMapTask
from ...storage.session_carry_task import SessionCarry
from ...roi.array_task import ArrayAction
from ...roi.display_style import RoiDisplayStyle
from ...roi.scope import RoiEditTarget, RoiScopeModule
from .background_tab import BACKGROUND_INFO_HTML
from .chromatic_tab import ChromaticUiValues
from .canvas_tools import _ICON_SIZE
from .data_axis_slider import DataAxisSlider
from .frame_tools import FrameToolsState, FrameToolsWidget
from .selection_regions import SelectionRegions
from .layers_build import build_layers
from .selection_tools import SelectionTools
from .canvas_build import build_canvas
from .navigation_build import build_navigation_bar
from .ribbon_build import (
    build_chromatic_and_background_tabs,
    build_image_tools_tab,
    build_mask_tab,
    build_roi_tab,
    build_side_rails,
    build_top_bar,
    build_view_tab,
)
from .gpu_view import apply_gpu_drawing
from .image_controls import controls_text
from .interaction import CanvasInteraction
from .overlay_cache import environment_budget_mb
from .overlay_style import OverlayStyle
from .overlay_tint import OverlayTint
from .tint_overlays import TintOverlays
from .frame_status_text import FrameStatusText
from .roi_creation import RoiCreation
from .roi_queries import RoiQueries
from .slideshow_state import restore_slideshow_settings
from .prefetch import prefetch_order
from .render import ImageRenderer, RenderRequest, RenderResult
from .roi_gestures import RoiGestures
from .roi_overlay import SAMPLE_WIDTH, SAMPLE_WIDTH_FAST, add_curve
from .array_actions import AutoCropReadiness
from .display_range_controls import FULL_16BIT_RANGE
from .analysis_tab import AnalysisTab
from .tuning_tab import TuningTab
from ...storage.ui_state_keys import (
    IMAGE_CURSOR_READOUT,
    IMAGE_MASK_EDIT_PREVIEW,
    IMAGE_GPU_DRAWING,
    IMAGE_OVERLAY_CACHE_MB,
    IMAGE_ROI_LABELS,
    IMAGE_SCALE_BAR_COLOR,
    MASK_ROWS_VISIBLE,
    ROI_OVERLAY_KEYS,
    ROI_ROW_SELECTED_ONLY,
    ROI_ROW_VISIBLE,
    ROI_SCOPE,
    SLIDER_RIBBON_OPEN,
    TOP_RIBBON_OPEN,
    read,
)
from ..ui_state import UiStateStore
from ...applog.events import log_event, timed
from .slideshow import (
    MODE_EVENTS,
    MODE_MASK_STATES,
    MODE_ROW_MARKS,
)
from .tool_escape import ToolEscape
from .tool_ribbon import ROI_TAB
from .panel_constants import (
    ANALYSIS_TAB,
    BACKGROUND_TAB,
    CHROMATIC_TAB,
    EXPORT_TAB,
    _PREVIEW_TOOLS,
    _REDRAW_COALESCE_MS,
)

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_PLAYING_MS = 0  # while Play runs each step is one event: the redraw runs right after its handlers (0 ms still merges a cube + wavelength change into one)
_PLAY_READ_AHEAD = 3  # frames read ahead while Play runs
# Slower than the redraw coalesce above on purpose: this debounces a settings
# *write* (one JSON file per `AppSettings._persist` call - see
# `app_rewrite.py`), not a redraw, so there is no reason to pay disk I/O on
# every intermediate frame of a drag/zoom the way a 100ms redraw would.
_VIEW_RANGE_PERSIST_DEBOUNCE_MS = 600

# The "i" icon's text per ribbon tab ("Image tools" and "ROIs" show the active tool's canvas controls instead).
_TAB_INFO: dict[str, str] = {
    "Mask": (
        "Mask: pixels to ignore (kept out of the background estimate and ROI sampling). "
        "State: edit scope (persistent / individual). "
        "Selection: add / subtract / replace the mask by the selection, or make the mask the selection (the selection tools are beside the image). "
        "Feature map: find the small features of the image and add them to the mask. Scan, Auto mask: mark and mask frames where something crossed the field. PNG: load/save the mask."
    ),
    CHROMATIC_TAB: (
        "Chromatic correction: Run finds landmarks on the reference frame and follows them through the "
        "wavelengths (gear: settings). Eye/stack: show landmarks for one or all wavelengths. "
        "Wand: switch the correction on/off. Trash: clear landmarks and correction."
    ),
    BACKGROUND_TAB: BACKGROUND_INFO_HTML,
}
class ImagePanel(QWidget):
    """Renders the current processed image with ROI overlays. Owns no
    computation and no ROI/group state (CLAUDE.md)."""

    # A tool's *live* status while a gesture runs (angle readout, ...), relayed to the app's status bar (`app_rewrite.py`) like every other panel's transient status.
    # The tool's *static* "what do the buttons do" text is not a signal: it is the tooltip of the permanent info icon (`_refresh_tool_info`).
    tool_status_changed = pyqtSignal(str)

    # The plane exactly as shown (after crop / rotate / mask / chromatic correction / background, before the overlays) with the (cube, wavelength) it belongs to:
    # the one narrow read the Histogram panel needs (sketch §7), added 2026-09-29 so the Histogram does not run its own second render of the frame. The frame
    # travels with the array instead of being re-read from `SelectionModule`: by the time a render completes the selection may have moved on (a fast wavelength
    # drag queues renders faster than they complete), and matching ROI / mask geometry to the wrong frame would be a correctness bug, not a cosmetic lag.
    # `RenderResult.request` carries the frame the array was really requested for (`_on_rendered`); this only forwards it.
    playback_changed = pyqtSignal(bool)  # the slideshow started (True) or stopped (False); other panels lighten their live updates
    roi_edit_running = pyqtSignal(bool)  # a drag that edits ROIs (move, resize, handle, border edge) started (True) or ended (False): the ROI table holds its rebuild in between
    calm_histogram_changed = pyqtSignal(bool)  # Play is running with "calm histogram" on (True), or not (False)
    display_range_changed = pyqtSignal(bool, float, float)  # the user changed the View tab's Display range: (fixed, black, white)
    play_frame_showing = pyqtSignal(int)
    """While Play runs: the cube of the frame about to be put on screen, emitted FIRST, before anything of it is drawn (2026-11-11).
    A plot that marks the cube (the Sensorgram cursor) moves its mark here: its plot scene then queues its repaint ahead of the
    window's, and the whole frame reaches the screen in one repaint instead of two (~12 ms of GUI time per frame)."""
    play_frame_shown = pyqtSignal()
    """While Play runs: a frame and the step it released are set, emitted just before this panel repaints its own view (`play_repaint.py`).
    A plot with a moved mark (the Sensorgram cursor) marks its dirty strip now, so one window repaint draws the whole frame."""
    image_rendered = pyqtSignal(np.ndarray, int, float)
    # Companion to `image_rendered` - the Histogram panel's other half of
    # "read Image, don't read Dataset directly" (sketch §7): without this,
    # Histogram would need its own `DatasetModule` reference just to learn
    # "nothing is displayed anymore", widening its dependency list for one
    # narrow case `ImagePanel` already knows about.
    image_cleared = pyqtSignal()

    # What frame is on screen ("Cube 0, 550 nm"): shown as the dock title bar's subtitle (it replaced the old bottom status row and its "- WxH px" text, 2026-09-30),
    # since 2026-10-16 as fixed-place fields (`frame_columns_changed`). Render *errors* ("Cannot show this frame: ...") deliberately do not go through this signal but
    # through `tool_status_changed`, a transient status-bar message like every other guarded command of this panel (`CropMeasureControls.on_measure_apply_requested`):
    # an error is not "what frame is displayed", it is "why nothing is".
    frame_status_changed = pyqtSignal(str, str)  # subtitle text, tooltip (the comment / why it is N/A)
    # The same status as fixed-place fields (2026-10-16, maintainer request: the dock title bar's
    # fields must not shift when a frame role or a comment appears): a tuple of `SubtitleColumn`
    # - frame role, cube, wavelength, time, comment. `app_rewrite.py` wires it to
    # `PanelContainer.set_subtitle_columns`; `frame_status_changed` stays the one-line text.
    frame_columns_changed = pyqtSignal(object)

    # The viewport's pan / zoom as (x_min, x_max, y_min, y_max) in image pixels: `app_rewrite.py`'s other half of `initial_view_range`, written back to `AppSettings`
    # debounced like every display panel's redraw, but on its own slower timer (a settings write, not a redraw: `_view_range_persist_timer`).
    view_range_changed = pyqtSignal(float, float, float, float)
    # Label of the ribbon tab now shown ("Image tools"/"Mask"/"Histogram"/"ROIs") -
    # lets the Histogram plot honour the area selection only while its own tab is open.
    ribbon_category_changed = pyqtSignal(str)
    view_controls_open_changed = pyqtSignal(bool)  # the View ribbon was rolled down (True) or up (`side_rails.py`; the Histogram reads `view_controls_open`)
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
        mask_pointers: MaskPointersModule | None = None,
        transient_scan: TransientScanTask | None = None,
        session_carry: SessionCarry | None = None,
        cube_display: CubeDisplayModule | None = None,
        initial_chromatic_values: ChromaticUiValues | None = None,
        initial_chromatic_view: tuple[bool, bool] = (True, False),
        initial_view_range: tuple[tuple[float, float], tuple[float, float]] | None = None,
        initial_mask_overlay: OverlayStyle | None = None,
        initial_highlight_overlay: OverlayStyle | None = None,
        initial_ribbon_category: str | None = None,
        initial_show_background: bool = False,
        roi_scope: RoiScopeModule | None = None,
        roi_display: RoiDisplayStyle | None = None,
        frame_roles: FrameRolesModule | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_carry = session_carry  # handed to the Export tab, which asks it to take the session along
        self._frame_roles = frame_roles
        self._show_background = bool(initial_show_background)
        self._last_data_range: tuple[float, float] | None = None  # display levels reused for the background view
        # (fixed, black point, white point): the View tab's Display range. Automatic per frame unless `fixed`;
        # the range itself starts at the whole 16-bit scale (what the pin switches on).
        self._display_range: tuple[bool, float, float] = (False, *FULL_16BIT_RANGE)
        self._initial_ribbon_category = initial_ribbon_category
        # Applied once, the first time a real image lands (`_on_rendered`) -
        # not here, since no plane exists yet to set a view against. `None`
        # (first launch, or a settings file with no saved range yet) just
        # means "let pyqtgraph's own default auto-range fit the first image",
        # exactly like every launch before this feature existed.
        self._initial_view_range = initial_view_range
        self._view_range_restored = False
        self._dataset = dataset
        # How a cube is named (number or start time); optional so tests build the panel alone.
        self._cube_display = cube_display if cube_display is not None else CubeDisplayModule(dataset, self)
        self._geometry = geometry
        self._mask = mask
        self._mask_scope = mask_scope
        # Optional so a panel built without the app shell (tests) still works;
        # `app_rewrite.py` passes the one shared instance.
        self._area_selection = area_selection if area_selection is not None else AreaSelectionModule(self)
        self._chromatic = chromatic
        # Optional like `area_selection`: tests build the panel without the app shell.
        self._chromatic_auto = chromatic_auto if chromatic_auto is not None else ChromaticAutoDetect(chromatic, self)
        # The pointers of the Mask-tab scan and the task that makes them; optional like the ones above (tests build the panel alone).
        self._mask_pointers = mask_pointers if mask_pointers is not None else MaskPointersModule(self)
        self._transient_scan = transient_scan if transient_scan is not None else TransientScanTask(self._mask_pointers, self)
        self._mask_auto = MaskAutoTask(self._mask, self)  # Mask tab, Auto mask group (pointers -> masks, one undo step)
        self._mask_export = MaskExportTask(self)  # Mask tab, PNG group: export every mask
        # Mask tab, Manual edit: Feature map (dark and ignored frames are left out of its mean through the frame roles)
        self._feature_map = FeatureMapTask(
            geometry, chromatic, dataset, None if frame_roles is None else frame_roles.is_spectral, self
        )
        self._array_action = ArrayAction(self)  # ROIs tab, Array group (panels/image/array_actions.py)
        # Persistent / Individual ROI edit scope, shared with the ROI table (optional so tests build the panel alone).
        self._roi_scope = roi_scope if roi_scope is not None else RoiScopeModule(self)
        # The one place the ROI display style (Sample / Reference colour, transparency, visibility) lives.
        self._roi_display = roi_display if roi_display is not None else RoiDisplayStyle(self)
        self._edit_target = RoiEditTarget(
            self._roi_scope, lambda: int(self._selection.current_cube()), lambda: min(self._dataset.spectral_cubes(), default=None)
        )
        self._initial_chromatic_values = initial_chromatic_values
        self._initial_chromatic_view = initial_chromatic_view
        self._background = background
        self._roi_toolbox = roi_toolbox
        self._selection = selection
        self._reference_frame = reference_frame
        self._highlight_range = highlight_range
        self._active_tool = active_tool
        self._tool_status = ""

        self._init_display_state(highlight_range, frame_roles, initial_mask_overlay, initial_highlight_overlay)

        self._build_ui()

        self._start_render_thread_and_timers(dataset)

        self._connect_modules()
        self._refresh_navigation_ranges()
        # No dataset yet at construction - same text `_on_dataset_cleared`
        # emits later, so the dock title bar's subtitle never sits blank
        # while genuinely nothing is loaded.
        self._frame_text.set_status("No dataset loaded.")

    def _init_display_state(
        self,
        highlight_range: HighlightRangeModule,
        frame_roles: FrameRolesModule | None,
        initial_mask_overlay: OverlayStyle | None,
        initial_highlight_overlay: OverlayStyle | None,
    ) -> None:
        """What is tinted over the image, what the dock title says about the frame, and the render bookkeeping (a step of `__init__`, same order as
        before it was split out)."""
        dataset = self._dataset
        # Mask-overlay display state (cosmetic only - see
        # mask_overlay_controls.py's module docstring for why this lives
        # here and not on MaskModule); what is cached and drawn is in `TintOverlays` (tint_overlays.py).
        self._mask_tint = OverlayTint(QColor(get_active_theme().mask_color), 0.5, initial_mask_overlay)

        # Histogram highlight-overlay display state (cosmetic only; `histogram_highlight_overlay_controls.py` says why it lives here and not on
        # `HighlightRangeModule`). Unlike the mask overlay there is nothing to "resolve": the tint is the already-displayed pixel array thresholded against
        # `HighlightRangeModule.current_range()`, so `_current_display_image` caches that array. 0.42 = the stable app's own alpha literal.
        self._highlight_tint = OverlayTint(
            QColor(get_active_theme().highlight_color), 0.42, initial_highlight_overlay
        )
        self._current_display_image: np.ndarray | None = None
        # What is tinted over the image and when (tint_overlays.py); the three functions are what it reads from this panel.
        self._tints = TintOverlays(
            self._mask_tint,
            self._highlight_tint,
            highlight_range,
            display_image=lambda: self._current_display_image,
            data_range=lambda: self._last_data_range,
            show_background=lambda: self._show_background,
            style_changed=self.overlay_style_changed.emit,
        )
        # What the dock title bar's subtitle says about the frame on screen (frame_status_text.py); `status` mirrors the last
        # `frame_status_changed` emission, as `_tool_status` does for the tool status above.
        self._frame_text = FrameStatusText(
            dataset,
            self._cube_display,
            frame_roles,
            slider_color=lambda role: self._slider_colors.color(role),
            status_changed=self.frame_status_changed.emit,
            columns_changed=self.frame_columns_changed.emit,
        )

        self._serial = itertools.count(1)
        self._latest_serial = 0
        self._last_shown_serial = 0
        self._no_data_shown: np.ndarray | None = None  # the checker currently uploaded to `_no_data_item`
        self._calm_histogram = False
        self._roi_revision = 0  # bumped by every ROI-toolbox change; part of the ROI overlay's "unchanged" test
        self._roi_draw_key: tuple | None = None
        # (height, width) of the last successfully rendered plane - the
        # chunk-grid preview needs real pixel dimensions and only this
        # panel's own render result has them (`DatasetModule` holds no
        # per-frame shape query - see its query-surface docstring).
        self._last_image_shape: tuple[int, int] | None = None
        # The selection / lock as raw-space regions for the mask tools, and the Lock / Unlock commands (`selection_regions.py`).
        self.selection_regions = SelectionRegions(self._area_selection, self._geometry, lambda: self._last_image_shape, self._on_tool_status)

    def _start_render_thread_and_timers(self, dataset: DatasetModule) -> None:
        """The render thread, its shutdown hooks and the redraw timers (a step of `__init__`, after the widgets exist)."""
        self._renderer = ImageRenderer(dataset.load_plane, parent=self)
        self._renderer.rendered.connect(self._on_rendered)
        # `closeEvent` reaches only top-level windows and this panel lives in a tab strip, so on a normal quit it would never fire and the render thread would emit into
        # a widget Qt is tearing down (this app's documented PyQt6-sip crash on close): the shutdown hook is on the application, where it does fire.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._renderer.stop)
            app.aboutToQuit.connect(self._chromatic_auto.shutdown)
            app.aboutToQuit.connect(self._transient_scan.shutdown)
            app.aboutToQuit.connect(self._mask_export.shutdown)
            app.aboutToQuit.connect(self._mask_auto.shutdown)
            app.aboutToQuit.connect(self._feature_map.shutdown)
            app.aboutToQuit.connect(self._array_action.shutdown)

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

        # Overlay-only redraws (a ROI moved, the selection changed): the same coalescing, none of the renderer.
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(_REDRAW_COALESCE_MS)
        self._overlay_timer.timeout.connect(self._redraw_overlay_only)

        self._view_range_persist_timer = QTimer(self)
        self._view_range_persist_timer.setSingleShot(True)
        self._view_range_persist_timer.setInterval(_VIEW_RANGE_PERSIST_DEBOUNCE_MS)
        self._view_range_persist_timer.timeout.connect(self._emit_view_range_changed)
        self._plot.vb.sigRangeChanged.connect(self._on_view_range_changed)

    def restore_ui_state(self, store: UiStateStore) -> None:
        """Put the cursor readout toggle and the Feature map preview's eye back as
        last left, and keep saving them (see `panels/ui_state.py`). Called
        once by the app shell after construction."""
        self._cursor_overlay.set_enabled(read(store, IMAGE_CURSOR_READOUT) is True)
        self._cursor_overlay.toggled.connect(lambda on: store.set(IMAGE_CURSOR_READOUT.key, bool(on)))
        edit_preview = self._mask_tab.edit_preview
        edit_preview.set_shown(read(store, IMAGE_MASK_EDIT_PREVIEW) is not False)
        edit_preview.shown_changed.connect(lambda on: store.set(IMAGE_MASK_EDIT_PREVIEW.key, bool(on)))
        saved_color = QColor(str(store.get(IMAGE_SCALE_BAR_COLOR.key) or ""))
        if saved_color.isValid():
            self._scale_bar_item.set_color(saved_color)
            self._view_tab.scale_bar_controls.set_color(saved_color)
        self._scale_bar_color_store = store
        for kind in ("sample", "reference"):
            keys = ROI_OVERLAY_KEYS[kind]
            visible = store.get(keys["visible"].key)
            style = self._roi_overlay.style(kind)
            if isinstance(visible, bool):
                style.visible = visible
            color = QColor(str(store.get(keys["color"].key) or ""))
            if color.isValid():
                style.color = color.name()
            alpha = store.get(keys["alpha"].key)
            if isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and 0.0 <= alpha <= 1.0:
                style.alpha = float(alpha)
        labels = store.get(IMAGE_ROI_LABELS.key)
        if isinstance(labels, bool):
            self._roi_overlay.labels_visible = labels
        env_mb = environment_budget_mb()  # LSPR_OVERLAY_CACHE_MB, if set, wins over the saved choice for this run
        cache_mb = env_mb if env_mb is not None else int(read(store, IMAGE_OVERLAY_CACHE_MB))
        self._roi_overlay.layers.set_budget_bytes(int(max(cache_mb, 0) * 1024 * 1024))
        self.side_rails.bind_ui_state(store)  # the two side ribbons' sides and roll-ups; before the store is kept below: a restore saves nothing
        if read(store, IMAGE_GPU_DRAWING) is True:  # off unless turned on in Preferences (default off since 2026-11-11, gpu_view.py)
            apply_gpu_drawing(self._view, True)
        self._restore_slideshow_settings(store)
        self._slider_marks.set_roi_row(read(store, ROI_ROW_VISIBLE) is not False, read(store, ROI_ROW_SELECTED_ONLY) is True)
        self._slider_ribbon.roi_row_toggled.connect(lambda on: store.set(ROI_ROW_VISIBLE.key, bool(on)))
        self._slider_ribbon.roi_selected_only_toggled.connect(lambda on: store.set(ROI_ROW_SELECTED_ONLY.key, bool(on)))
        mask_rows_shown = read(store, MASK_ROWS_VISIBLE) is not False
        self._slider_ribbon.set_mask_rows(mask_rows_shown)
        self._mask_rows.set_shown(mask_rows_shown)
        self._slider_ribbon.mask_rows_toggled.connect(lambda on: store.set(MASK_ROWS_VISIBLE.key, bool(on)))
        self._restore_slider_colors(store)
        self._ribbon_drawer.set_expanded(read(store, SLIDER_RIBBON_OPEN) is not False, animate=False)
        self._ribbon_drawer.expanded_changed.connect(lambda open_: store.set(SLIDER_RIBBON_OPEN.key, bool(open_)))
        self._top_drawer.set_expanded(read(store, TOP_RIBBON_OPEN) is not False, animate=False)
        self._top_drawer.expanded_changed.connect(lambda open_: store.set(TOP_RIBBON_OPEN.key, bool(open_)))
        self._apply_roi_overlay_style()
        self._sync_roi_overlay_controls()
        self._roi_tab.bind_ui_state(store)
        self._export_tab.bind_ui_state(store)
        store.bind("view/source_chunk_grid", self._view_tab.chunk_grid_controls)
        self._mask_tab.scan_controls.bind_ui_state(store)
        self._mask_tab.auto_controls.bind_ui_state(store)
        self._mask_tab.features_panel.bind_ui_state(store)
        self._selection_tools.toolbar.bind_ui_state(store)
        self._on_group_labels_changed()
        saved_scope = store.get(ROI_SCOPE.key)
        if saved_scope in ("persistent", "individual"):
            self._roi_scope.set_scope(saved_scope)
        self._roi_scope.scope_changed.connect(lambda scope: store.set(ROI_SCOPE.key, scope))
        self._roi_overlay_store = store  # only now: restoring must not save what it just read

    @property
    def roi_display(self) -> RoiDisplayStyle:
        """The one place the ROI display style (Sample / Reference colour, transparency, visibility) lives."""
        return self._roi_display

    @property
    def edit_target(self) -> RoiEditTarget:
        """Where a ROI geometry edit lands (shared with the ROI table)."""
        return self._edit_target

    @property
    def analysis_tab(self) -> AnalysisTab:
        """The Analysis ribbon tab: Scan / Fast scan / Scan file (the app shell wires it to the scan task)."""
        return self._analysis_tab

    @property
    def tuning_tab(self) -> TuningTab:
        """The Tuning ribbon tab: try other ROI centres, rank them, show the best (the app shell wires it to `RoiTuningModule`)."""
        return self._tuning_tab

    @property
    def mask_export(self) -> MaskExportTask:
        """The export-all-masks task (the app shell connects it to the task indicator)."""
        return self._mask_export

    @property
    def mask_auto(self) -> MaskAutoTask:
        """The Auto mask task (the app shell connects it to the task indicator)."""
        return self._mask_auto

    @property
    def feature_map(self) -> FeatureMapTask:
        """The Feature map task (the app shell connects it to the task indicator)."""
        return self._feature_map

    @property
    def array_action(self) -> ArrayAction:
        """The Array tools' background action (the app shell connects it to the task indicator)."""
        return self._array_action

    # -- display range (2026-10-14) -----------------------------------------------------

    def display_range(self) -> tuple[bool, float, float]:
        """``(fixed, black point, white point)``: with `fixed` every frame is shown with the same black and white
        point (so brightness is comparable between frames), otherwise each at its own automatic range."""
        return self._display_range

    def set_display_range(self, fixed: bool, low: float, high: float) -> None:
        """Put the Display range back (dataset load / session restore). Does not emit `display_range_changed`,
        so restoring never saves anything."""
        self._display_range = (bool(fixed), float(low), float(high))
        self._view_tab.display_range_controls.set_state(*self._display_range)
        self._schedule_redraw()

    def _fixed_levels(self) -> tuple[float, float] | None:
        fixed, low, high = self._display_range
        return (low, high) if fixed and high > low else None

    def _on_display_range_edited(self, fixed: bool, low: float, high: float) -> None:
        self._display_range = (bool(fixed), float(low), float(high))
        if fixed and not high > low:
            self._on_tool_status("Display range: max must be above min - each frame is shown at its own range until it is.")
        self.display_range_changed.emit(bool(fixed), float(low), float(high))
        self._schedule_redraw()

    def _on_scale_bar_color_changed(self, color: QColor) -> None:
        self._scale_bar_item.set_color(color)
        store = getattr(self, "_scale_bar_color_store", None)
        if store is not None:
            store.set(IMAGE_SCALE_BAR_COLOR.key, color.name())

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        build_canvas(self)
        self._build_toolbar_and_layout()

    def _build_toolbar_and_layout(self) -> None:
        # The top bar holds the category ribbon (`ImageToolRibbon`: the "Image tools" / "Mask" / ... tabs over a fixed-height row; a horizontal bar since 2026-09-30
        # because frames are usually landscapes, so a left rail wastes more width than a top bar wastes height) and, on the right, the cursor-readout and "i" info
        # icons, ordinary `QHBoxLayout` items (they were once `.move()`d overlays on the canvas). Each tab page is built by its own function in `ribbon_build.py`, in the
        # order the tabs depend on each other: the View tab mirrors buttons of the Mask, Chromatic and Background tabs, so those come first (split by tab 2026-10-07).
        image_tools_content = build_image_tools_tab(self)
        mask_content = build_mask_tab(self)
        build_chromatic_and_background_tabs(self)
        roi_content = build_roi_tab(self)  # before the View tab, which carries copies of its controls
        self._roi_tab.add_tuning(self._tuning_tab)  # ROI position tuning is the last section of the ROIs tab
        build_view_tab(self)  # not a tab since 2026-11-22: the View ribbon beside the image
        build_top_bar(self, image_tools_content, mask_content, roi_content)
        build_navigation_bar(self)

        # The selection toolbar beside the image, its tools and their right-drag gestures (`selection_tools.py`).
        self.layers, self._image_cover = build_layers(self)  # Image / ROI / Ignore mask layers, the active one is what the wand reads (layers.py)
        display = self._roi_display
        self._selection_tools = SelectionTools(
            self, mask=self._mask, geometry=self._geometry, highlight_range=self._highlight_range, preview=self._mask_tab.edit_preview,
            area_selection=self._area_selection, active_tool=self._active_tool, area_tool=self._area_tool, plot=self._plot, view=self._view,
            regions=self.selection_regions, image_shape=lambda: self._last_image_shape, status=self._on_tool_status,
            highlight_controls=self._view_tab.highlight_controls, layers=self.layers, displayed_mask=self._tints.displayed_mask,
            roi_region=lambda x, y, shape: self._roi_queries.region_at(
                x, y, shape, sample=display.sample.visible, reference=display.reference.visible
            ),
        )

        # Canvas column: top bar (built above), then the pyqtgraph view with the selection toolbar and the View ribbon beside it, each on the side
        # of the image the person docked it on (`side_rails.py`; the toolbar on the left and the View ribbon on the right until moved).
        self.side_rails = build_side_rails(self)
        canvas_column = QVBoxLayout()
        canvas_column.setContentsMargins(0, 0, 0, 0)
        canvas_column.setSpacing(0)
        canvas_column.addWidget(self._top_bar)
        canvas_column.addWidget(self.side_rails, 1)

        # The navigation bar sits below the canvas (2026-09-30, maintainer): `_refresh_controls_bar_theme` draws its border on the edge that touches the canvas.
        #
        # Zero margins and spacing on this outermost layout (2026-09-30, a real bug found with a headless geometry probe: "wide borders around the image area, biggest
        # from the top"): every other layout here zeroes its margins, this one was left at Qt's style default of about 11 px on all four sides. It is invisible as a line
        # (the panel's and the canvas's background are both `toolbar_bg`) but reads as extra dark padding, worst at the top where it stacked on the dock's own title
        # bar. (A 10 px left / right margin was tried here and moved to the navigation bar's own `controls` layout: the canvas and the toolbar stay flush.)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(canvas_column, 1)
        layout.addWidget(self._controls_bar)

        scene = self._image_item.scene()
        # Which ROI is under a point, which are inside a rectangle, where the selected ones are (roi_queries.py).
        self._roi_queries = RoiQueries(
            self._selection,
            self._roi_toolbox,
            self._chromatic,
            current_cube=self._current_cube,
            current_wavelength=self._current_wavelength,
            reference_visible=lambda: bool(self._roi_overlay.reference.visible),
        )
        roi_gestures = RoiGestures(
            self._plot, self._roi_toolbox, self._selection,
            rois_in_rect=self._roi_queries.rois_in_rect, selected_apertures=self._roi_queries.selected_apertures,
            view_pixel_size=lambda: float(self._plot.vb.viewPixelSize()[0]),
            display_linear=self._roi_queries.display_linear, redraw_overlay=self._draw_roi_overlay,
            edit_target=self._edit_target, parent=self,
        )
        roi_gestures.refused.connect(self._on_tool_status)  # e.g. an individual change on a persistent point
        roi_gestures.edit_running.connect(self.roi_edit_running)
        self._roi_creation = RoiCreation(
            self._roi_toolbox, self._selection, self._area_selection, image_shape=lambda: self._last_image_shape,
            current_affine=self._roi_queries.current_affine, to_reference=self._roi_queries.display_to_reference, action_parent=self,
        )
        self._roi_creation.status.connect(self._on_tool_status)
        self._roi_tab.link_requested.connect(self._roi_creation.link)
        self._roi_tab.unlink_requested.connect(self._roi_creation.unlink)
        self._roi_tab.shape_changed.connect(self._roi_creation.set_picked_shape)
        self._roi_creation.set_picked_shape(self._roi_tab.shape_picker.shape())
        self._interaction = CanvasInteraction(
            plot=self._plot, view=self._view, menu_parent=self,
            active_tool=self._active_tool, roi_toolbox=self._roi_toolbox, selection=self._selection,
            area_selection=self._area_selection,
            rotate_tool=self._rotate_tool, crop_tool=self._crop_tool, measure_tool=self._measure_tool,
            area_tool=self._area_tool,
            image_shape=lambda: self._last_image_shape, roi_at=self._roi_queries.roi_at, apply_crop=self._crop_measure.on_crop_apply_requested,
            roi_gestures=roi_gestures,
            roi_tab_active=lambda: self._tool_ribbon.current_category() == ROI_TAB,
            to_reference=self._roi_queries.display_to_reference,
            notify=self._on_tool_status,
            wand_click=self._selection_tools.layer_wand.click, rois_touching=self._roi_queries.rois_touching,
            drag_adjusters=self._selection_tools.drag_adjusters, value_click=self._selection_tools.run_value_tool,
            combine_badge=self._selection_tools.combine_badge,
            lock_to_selection=self.selection_regions.lock_to_selection, unlock=self.selection_regions.unlock_selection,
            roi_creation=self._roi_creation,
        )
        scene.sigMouseClicked.connect(self._on_scene_clicked)
        scene.sigMouseMoved.connect(self._on_scene_moved)
        # Arrow keys for the active tool. An event filter on the view
        # (not `keyPressEvent` here) because the graphics view would
        # otherwise consume arrow keys itself to scroll.
        self._view.installEventFilter(self)
        self._tool_escape = ToolEscape(self._active_tool, self._interaction.handle_key, action_parent=self)  # Esc leaves the tool that is on (2026-11-19)

    def _on_transforms_navigate(self, key: str) -> None:
        """A row of Auto crop's pick-up menu that lives in another ribbon tab: show that tab."""
        self._tool_ribbon.set_category(ROI_TAB if key == "rois" else CHROMATIC_TAB)

    def _on_auto_crop_readiness(self, readiness: AutoCropReadiness) -> None:
        self._transforms_section.set_auto_crop_state(
            levelled=readiness.levelled, rois=readiness.rois, chromatic=readiness.chromatic,
            level_note=readiness.level_note,
        )

    def _show_current_tab(self) -> None:
        """Scroll the tool ribbon (it scrolls when the panel is narrower than its row) so the chosen tab is fully visible."""
        self._top_drawer.ensure_visible(self._tool_ribbon.tab_button(self._tool_ribbon.current_category()))

    def _add_curve(self, color_hex: str, *, width: float, dashed: bool = False) -> pg.PlotDataItem:
        return add_curve(self._plot, color_hex, width=width, dashed=dashed)

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
        self._dataset.chunk_grid_preview_changed.connect(self._decorations.update_chunk_grid)
        self._dataset.source_chunk_grid_changed.connect(self._decorations.update_source_grid)

        self._active_tool.active_tool_changed.connect(self._on_active_tool_changed)

        self._geometry.geometry_changed.connect(self._decorations.invalidate_landmarks)
        self._geometry.geometry_changed.connect(self._schedule_redraw)
        self._geometry.cosmetic_changed.connect(self._schedule_redraw)
        self._mask.mask_changed.connect(self._schedule_redraw)
        self._mask.cosmetic_changed.connect(self._schedule_redraw)
        self._background.background_model_changed.connect(self._schedule_redraw)
        self._chromatic.chromatic_model_changed.connect(self._decorations.invalidate_landmarks)
        self._chromatic.chromatic_model_changed.connect(self._schedule_redraw)
        self._chromatic.chromatic_model_changed.connect(self._update_chromatic_tab_state)
        self._update_chromatic_tab_state()
        self._background.background_model_changed.connect(self._update_background_tab_state)
        self._update_background_tab_state()
        # The Feature map describes the picture as it was (not the ROIs: moving ROIs keeps it): a new rotation / crop or chromatic
        # model drops it (O(1)). Background flattening does not touch it (the map takes the shading out itself).
        self._geometry.geometry_changed.connect(self._feature_map.forget_result)
        self._chromatic.chromatic_model_changed.connect(self._feature_map.forget_result)

        # ROI/selection changes normally only move the overlay, never the pixels, so they take the overlay-only
        # redraw (`_schedule_roi_redraw`, 2026-10-21 - the optimization this comment used to defer "until there is
        # a dataset big enough to measure it against"; that dataset arrived: 314 cubes, 160 ROIs, and a full
        # re-render of 50-180 ms per ROI edit for pixels that had not changed).
        self._roi_toolbox.geometry_changed.connect(self._bump_roi_revision)
        self._roi_toolbox.cosmetic_changed.connect(self._bump_roi_revision)
        self._roi_toolbox.geometry_changed.connect(self._schedule_roi_redraw)
        self._roi_toolbox.cosmetic_changed.connect(self._schedule_roi_redraw)
        # The ROI row's marks depend on the ROI timelines only, never on the cube shown: they are not part of the
        # redraw above (which runs on every Play step) and wait for quiet after an edit.
        self._roi_toolbox.geometry_changed.connect(self._slider_marks.schedule_roi_marks)
        self._roi_toolbox.roi_ids_renumbered.connect(self._slider_marks.schedule_roi_marks)
        self._selection.roi_selection_changed.connect(self._slider_marks.on_roi_selection_for_marks)
        self._selection.cube_changed.connect(self._schedule_redraw)
        self._selection.wavelength_changed.connect(self._schedule_redraw)
        # Which ROIs are selected changes their outline, never a pixel: the background estimate's exclusion uses
        # *every* ROI (`_redraw`'s request), not the selection, so this one is always overlay-only.
        self._selection.roi_selection_changed.connect(self._schedule_overlay_redraw)

        # These keep the navigation widgets truthful for *any* source of a selection change, not just this panel's own slider handlers (the "jump to reference" button,
        # another panel calling `SelectionModule.set_cube` / `set_wavelength`): the panel's rule "redraw because the module emitted a change", applied to the nav widgets.
        self._selection.cube_changed.connect(self._on_selection_cube_changed)
        self._selection.wavelength_changed.connect(self._on_selection_wavelength_changed)
        self._reference_frame.reference_frame_changed.connect(self._on_reference_frame_changed)
        if self._frame_roles is not None:
            self._frame_roles.roles_changed.connect(self._on_frame_roles_changed)

        # The highlight overlay tints the already-displayed pixel array
        # (`_current_display_image`, set in `_on_rendered`) - a range change
        # never needs a new render, so this goes straight to
        # `TintOverlays.update_highlight`, not `_schedule_redraw`.
        self._highlight_range.range_changed.connect(self._tints.on_highlight_range_changed)

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
        for tab in ("_mask_tab", "_roi_tab", "_view_tab", "_view_ribbon"):
            if hasattr(self, tab):
                getattr(self, tab).refresh_theme()
        if hasattr(self, "_tool_ribbon"):
            self._tool_ribbon.refresh_theme(get_active_theme())
        if hasattr(self, "_top_drawer"):
            self._top_drawer.refresh_theme()
        if hasattr(self, "_selection_tools"):
            self._selection_tools.toolbar.refresh_theme(get_active_theme())
        if hasattr(self, "_background_tab"):
            self._background_tab.refresh_theme(get_active_theme())
        if hasattr(self, "_analysis_tab"):
            self._analysis_tab.refresh_theme()
        if hasattr(self, "_tuning_tab"):
            self._tuning_tab.refresh_theme()
        if hasattr(self, "_export_tab"):
            self._export_tab.refresh_theme()
        if hasattr(self, "_cursor_readout"):
            self._cursor_readout.restyle()
        if hasattr(self, "_controls_bar"):
            self._refresh_controls_bar_theme()
        if hasattr(self, "_slider_ribbon"):
            self._ribbon_drawer.refresh_theme()
            self._slider_marks.update_all()  # a default colour follows the theme
        if hasattr(self, "_top_bar"):
            self._refresh_top_bar_theme()

    def area_selection(self) -> AreaSelectionModule:
        """The shared area selection - editors read it from here so each
        front door needs no extra constructor argument."""
        return self._area_selection

    def active_ribbon_category(self) -> str:
        return self._tool_ribbon.current_category()

    def view_controls_open(self) -> bool:
        """The View controls are in front of the person: the View ribbon is rolled down (on either side of the image; 2026-11-22: it was "the View
        tab is the open one" while the controls were a tab of the top ribbon)."""
        return self.side_rails.rail("view").is_expanded()

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
        """No border. A single-pixel seam along the bottom edge, where this bar
        meets the canvas below it, was added 2026-09-30 (maintainer request -
        "make there a bo[r]der on the bottom to separate it from the image
        area") and taken away again 2026-10-18 (maintainer request: remove the
        edge line under the top ribbon). Kept as the live-theme-switch hook
        (see `_refresh_controls_bar_theme`, which is in the same position)."""
        self._top_bar.setStyleSheet("#imageTopBar { border: none; }")

    # -- dataset lifecycle --------------------------------------------------

    def _stop_dataset_bound_tasks(self) -> None:
        """A scan reads frames of the dataset and Auto mask makes masks for its frames: when the dataset is loaded or cleared under
        them they stop, and nothing they had is kept (the scan reads through the dataset module as it is at each read, so it would
        otherwise go on into the new dataset and install its result in the new session)."""
        self._transient_scan.invalidate()
        self._mask_auto.invalidate()
        self._feature_map.invalidate()

    def _on_dataset_loaded(self, _dataset: object) -> None:
        self._stop_dataset_bound_tasks()
        self._frame_text.forget_widest()
        self._renderer.clear_prefetch()
        self._decorations.invalidate_landmarks()
        self._refresh_navigation_ranges()
        self._refresh_frame_tools()
        self._schedule_redraw()

    def _on_dataset_cleared(self) -> None:
        """Reset this panel's own view state. `DatasetModule.clear_dataset`
        deliberately clears only its own reference and expects every module
        and panel holding dataset-derived state to reset itself (see its
        module docstring) - this is the first subscriber to actually do so."""
        self._stop_dataset_bound_tasks()
        self._frame_text.forget_widest()
        self._refresh_frame_tools()
        self._renderer.clear_prefetch()
        self._decorations.invalidate_landmarks()
        self._image_item.clear()
        self._no_data_item.hide()
        self._no_data_shown = None
        self._tints.clear()
        self._current_display_image = None
        self._roi_overlay.clear()
        self._decorations.clear_lines()
        self._last_image_shape = None
        self._area_tool.set_image_shape(None)
        self._refresh_navigation_ranges()
        self._frame_text.set_status("No dataset loaded.")
        self.image_cleared.emit()
        # A tool with no image under it has nothing to do; the Workflow
        # panel's buttons follow this signal.
        self._active_tool.clear()

    def _refresh_navigation_ranges(self) -> None:
        """Point the cube/wavelength controls at what the dataset actually
        has. Blocked while being reprogrammed so that re-ranging them
        doesn't emit a spurious "the user changed the wavelength"."""
        cubes = self._dataset.spectral_cubes()
        self._cube_slider.axis_window().reset()  # a dataset was loaded or cleared: the whole Cube axis again
        with _blocked(self._cube_slider):
            self._cube_slider.setEnabled(bool(cubes))
            self._cube_slider.setMinimum(0)
            self._cube_slider.setMaximum(max(len(cubes) - 1, 0))
            self._cube_slider.setSingleStep(1)
            self._cube_slider.setPageStep(1)
            self._cube_slider.set_ticks(list(cubes), self._slider_marks.cube_slider_major_ticks(cubes))
            if cubes and self._current_cube() in cubes:
                self._cube_slider.setValue(cubes.index(self._current_cube()))
        self._roi_strip.set_axis_count(len(cubes))
        self._mask_rows.refresh_cube_row()
        self._slider_marks.forget_roi_states()  # another dataset: the ROI states are looked up again
        self._refresh_wavelength_range()
        self._slider_marks.update_all()

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
        with _blocked(self._wavelength_slider):
            self._wavelength_slider.setEnabled(bool(wavelengths))
            self._wavelength_slider.setMinimum(0)
            self._wavelength_slider.setMaximum(max(len(wavelengths) - 1, 0))
            self._wavelength_slider.setSingleStep(1)
            self._wavelength_slider.setPageStep(1)
            # Nothing to wire for the scale-break glyph: `DataAxisSlider` finds an unusually large gap in `wavelengths` itself (a real 0 nm dark frame followed by the
            # first spectral wavelength) and draws it (its module docstring says why a synthetic "0" tick here was wrong).
            self._wavelength_slider.set_ticks(
                list(wavelengths),
                self._slider_marks.wavelength_slider_major_ticks(wavelengths, self._wavelength_slider.axis_window().view_for(len(wavelengths))),
            )
            if wavelengths and wavelength in wavelengths:
                self._wavelength_slider.setValue(wavelengths.index(wavelength))
        self._mask_rows.update_wavelength_row(wavelengths, wavelength)

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
    # The sliders change the value through `SelectionModule.set_cube`/
    # `set_wavelength` - never straight to anything else - so the module stays
    # the single source of truth (module docstring's one-way flow) and
    # `_on_selection_*_changed` below is the only place a slider's *displayed*
    # value is set.

    def _on_cube_slider_changed(self, index: int) -> None:
        cubes = self._dataset.spectral_cubes()
        if not cubes or index >= len(cubes):
            return
        self._selection.set_cube(int(cubes[index]))

    def _on_wavelength_slider_changed(self, index: int) -> None:
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        if not wavelengths or index >= len(wavelengths):
            return
        self._selection.set_wavelength(float(wavelengths[index]))

    def _on_selection_cube_changed(self, _cube_index: int) -> None:
        # The wavelength set can differ per cube, so a cube change must
        # re-range the wavelength controls too, not just re-display the
        # cube controls' new value.
        cubes = self._dataset.spectral_cubes()
        with _blocked(self._cube_slider):
            if self._current_cube() in cubes:
                self._cube_slider.setValue(cubes.index(self._current_cube()))
        cursor = cubes.index(self._current_cube()) if self._current_cube() in cubes else None
        self._roi_strip.set_cursor(cursor)
        self._mask_rows.set_cube_cursor(cursor)
        self._refresh_wavelength_range()
        self._slider_marks.update_marks()
        self._refresh_frame_tools()

    def _on_selection_wavelength_changed(self, _wavelength: float) -> None:
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        wavelength = self._current_wavelength(wavelengths)
        with _blocked(self._wavelength_slider):
            if wavelengths and wavelength in wavelengths:
                self._wavelength_slider.setValue(wavelengths.index(wavelength))
        self._mask_rows.set_wavelength_cursor(wavelength)
        self._slider_marks.update_marks()
        self._refresh_frame_tools()

    # -- reference-frame highlight (ReferenceFrameModule) --------------------

    def _resolve_reference_frame(self) -> tuple[int, float] | None:
        """Same resolution rule `panels/workflow/reference_frame_row.py`
        uses: Set reference = the stored snapshot; Find reference = the viewed
        cube's best-contrast wavelength (None while it is still being found).
        `ReferenceFrameModule.resolve` does the work; it only needs the viewed cube."""
        return self._reference_frame.resolve(self._selection.current_cube())

    def _visible_cube_range(self) -> tuple[int, int] | None:
        """The first and last cube number in view on the Cube slider, or ``None`` when it shows every cube (what the Scan group's
        "only the cubes in view" switch scans)."""
        cubes = self._dataset.spectral_cubes()
        window = self._cube_slider.axis_window()
        if len(cubes) < 2 or not window.is_zoomed():
            return None
        low, high = window.view_for(len(cubes))
        first, last = max(math.ceil(low - 1e-9), 0), min(math.floor(high + 1e-9), len(cubes) - 1)
        return (cubes[first], cubes[last]) if first <= last else None

    def _state_cubes_for_mode(self, mode: str) -> tuple[int, ...]:
        """The cubes a "states" slideshow mode steps through: the marks of the ROIs row or of the Mask row under the Cube slider,
        or the cubes with a scan pointer."""
        if mode == MODE_EVENTS:
            return self._mask_rows.event_state_cubes()
        if mode == MODE_ROW_MARKS:
            return self._mask_rows.marked_cubes()
        return self._mask_rows.state_cubes() if mode == MODE_MASK_STATES else self._slider_marks.roi_state_cubes()

    def _on_slider_color_changed(self, role: str, color: str | None) -> None:
        """A swatch in the ribbon was picked (or reset): the sliders take it (`SliderMarks`), and so does the dock subtitle."""
        self._slider_marks.on_slider_color_changed(role, color)
        self._frame_text.refresh()  # the dock subtitle's Dark / Reference / Ignored word has the same colour

    def _restore_slider_colors(self, store: UiStateStore) -> None:
        """The slider colours back as last left, and keep saving them."""
        self._slider_marks.restore_colors(store)
        self._frame_text.refresh()

    # -- rendering ----------------------------------------------------------

    def _schedule_redraw(self, *_args: object) -> None:
        """Coalesce many events into one redraw ~100ms later (sketch §8).
        Takes ``*_args`` so it can be connected directly to signals carrying
        a payload without a lambda per connection.

        `start()` on a running timer restarts it (a debounce: the redraw waits for
        quiet). While the slideshow plays, steps arrive faster than 100 ms at
        2x and above, so the timer never ran out and no frame was drawn until Play
        stopped (measured 2026-10-14: 4 of 52 steps drawn at 5x, 1.4 s lag). Then
        it is a throttle instead: a running timer is left alone and `_redraw` reads
        whatever the newest selection is when it fires."""
        if self._redraw_timer.isActive() and hasattr(self, "_slideshow") and self._slideshow.is_playing():
            return
        self._redraw_timer.start()

    def _rois_change_pixels(self) -> bool:
        """Whether moving a ROI changes the **rendered pixels**, not just the overlay.

        It does in exactly one case: the background estimate leaves the ROIs out
        (`flatten_background_exclude_area_rois`) and that estimate is being used - either flattening is on, or the
        estimate itself is what is shown. Nothing else in `RenderRequest` depends on a ROI (see the `rois=` note
        in `_redraw`), and the exclusion uses every ROI, so the *selection* never matters.
        """
        background = self._background.settings()
        return bool(background.flatten_background_exclude_area_rois) and (
            bool(background.flatten_background_enabled) or bool(self._show_background)
        )

    def _schedule_roi_redraw(self, *_args: object) -> None:
        """A ROI edit: redraw the overlay, and the pixels only if the ROIs feed them."""
        if self._rois_change_pixels():
            self._schedule_redraw()
        else:
            self._schedule_overlay_redraw()

    def _schedule_overlay_redraw(self, *_args: object) -> None:
        """Coalesce overlay-only redraws the way `_schedule_redraw` coalesces full ones (2026-10-21).

        Skipped while a full redraw is already queued: `_redraw` draws the overlay itself (and stops this
        timer), so queueing both would draw it twice. During a drag the gesture draws the circles directly every
        sample (`roi_gestures._redraw_now`); this is what catches up afterwards, and what every non-drag edit
        (the ROI table, Undo, Detect) goes through."""
        if self._redraw_timer.isActive():
            return
        self._overlay_timer.start()

    def _redraw_overlay_only(self) -> None:
        """The ROI overlay, with the rendered frame left alone. 2-6 ms on 160 ROIs, against 50-180 ms for a
        re-render of pixels that cannot have changed."""
        with timed(logger, "render.overlay_only"):
            self._draw_roi_overlay()

    def _redraw(self) -> None:
        """Snapshot every module's current state and hand it to the renderer.

        Only the *pixels* are rendered off-thread. Overlays are drawn here,
        synchronously, because they are a few thousand points of pure numpy
        and because they must track a drag with no perceptible lag - a
        coalesced 100ms round trip to a worker would make dragging an ROI
        feel broken.

        **The render request goes out first, the overlays are drawn after it** (2026-10-14, testing7): while
        Play runs the render thread is the busy one, and drawing the overlays first (~6 ms on a ROI-heavy
        frame) made every request wait for work that does not feed it. Nothing below the submit changes what
        the request contains."""
        redraw_started = time.perf_counter()
        self._overlay_timer.stop()  # this draws the overlay too: no need for the overlay-only pass as well
        cubes = self._dataset.spectral_cubes()
        if not cubes:
            self._draw_overlays()
            self._tints.update_mask(None, None, None, hidden=True)
            self._current_display_image = None
            self._highlight_tint.item.hide()
            self._edit_preview_layer.refresh()
            self._lock_layer.refresh()
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
        request_geometry = geometry
        if preview_active and geometry.crop.enabled:
            # Uncropped preview - the crop is drawn as an outline instead.
            request_geometry = replace(geometry, crop=CropDefinition())

        background = self._background.settings()
        if authored_mask is not None and background.flatten_background_exclude_mask and (
            self._show_background or background.flatten_background_enabled
        ):
            # The render thread will need this resolved mask (background estimate with "exclude mask"): resolve it
            # here first, as before the 2026-10-14 reorder, so it finds it in the cache instead of both threads
            # computing the same thing at the same time.
            resolve_external_mask(authored_mask, request_geometry, warp_affine)

        submit_started = time.perf_counter()
        self._latest_serial = next(self._serial)
        self._renderer.submit(
            RenderRequest(
                cube_index=cube_index,
                wavelength_nm=wavelength_nm,
                geometry=request_geometry,
                background=background,
                authored_mask=authored_mask,
                mask_warp_affine=warp_affine,
                # Every ROI, not the selection - these feed the background
                # estimate's exclusion, not the overlay (see RenderRequest).
                # `_redraw` already re-runs on every ROI edit, so this adds
                # no re-render that wasn't happening anyway.
                rois=tuple(self._roi_toolbox.rois_at(self._current_cube())),
                detection=self._roi_toolbox.detection_settings(),
                serial=self._latest_serial,
                show_background=self._show_background,
                # Fast while moving, exact at rest: `_on_playback_changed` redraws exactly when Play stops.
                fast_rotation=hasattr(self, "_slideshow") and self._slideshow.is_playing(),
                display_levels=self._fixed_levels(),
            )
        )
        submitted = time.perf_counter()
        self._playback_stats.note_detail("submit", submitted - submit_started)
        # The stats' "redraw" is now only what happens before the request leaves; the overlay drawing below
        # overlaps with the render thread's work and is reported on its own ("overlays", "mask").
        self._playback_stats.note_redraw(self._latest_serial, redraw_started, submitted)

        overlays_started = time.perf_counter()
        self._draw_overlays()
        self._playback_stats.note_detail("overlays", time.perf_counter() - overlays_started)
        mask_started = time.perf_counter()
        # The overlay tint must match the *displayed* geometry exactly - a
        # preview tool shows the image uncropped (see above), a different
        # coordinate space than `authored_mask`/`warp_affine` were resolved
        # for, so it hides then, the same "wrong coordinate space" reasoning
        # `_draw_overlays` already applies to the ROI overlay.
        self._tints.update_mask(authored_mask, geometry, warp_affine, hidden=preview_active)
        self._edit_preview_layer.refresh()  # a geometry change moves where the previewed raw pixels land
        self._lock_layer.refresh()
        self._playback_stats.note_detail("mask", time.perf_counter() - mask_started)

    def _play_config_fields(self) -> dict[str, float | int | bool]:
        """What is switched on right now, as flat log fields (``c_*``), so a log of a Play run says which
        overlays each second of it was drawn with (testing9: the stats alone could not tell). Read once per
        second with the Play stats window; cheap."""
        settings = self._slideshow.settings()
        mask_state = self._tints.mask_state
        return {
            "c_target_fps": round(1.0 / max(self._slideshow.interval_seconds(), 1e-6), 1),
            "c_speed": settings.speed,
            "c_rois": len(self._roi_toolbox.rois_at(self._current_cube())),
            "c_sample": bool(self._roi_overlay.sample.visible),
            "c_reference": bool(self._roi_overlay.reference.visible),
            "c_labels": bool(self._roi_overlay.labels_visible),
            "c_group_labels": bool(self._roi_overlay.group_labels.visible),
            "c_landmarks": bool(self._decorations.landmarks_visible),
            "c_mask": bool(self._mask_tint.visible and mask_state is not None and mask_state[0] is not None),
            "c_highlight": bool(self._highlight_tint.item.isVisible()),
            "c_background": bool(self._background.settings().flatten_background_enabled),
            "c_bg_estimate": bool(self._show_background),
            "c_thin": bool(settings.thin_roi_outline),
            "c_calm": bool(settings.calm_histogram),
            "c_ov_hits": self._roi_overlay.layers.hits,
            "c_ov_misses": self._roi_overlay.layers.misses,
            "c_ov_states": self._roi_overlay.layers.cached_states,
            "c_ov_mb": round(self._roi_overlay.layers.cached_megabytes, 1),
        }

    def _flush_redraw_for_step(self, _handlers_s: float) -> None:
        """Play made a step: send the render request now instead of 0 ms later through the redraw timer.
        The timer only fires after Qt has handled its pending events, i.e. after painting the previous frame,
        and the request would then wait for that paint; sent now it is rendered meanwhile."""
        if self._slideshow.pipelined and self._redraw_timer.isActive():
            self._redraw_timer.stop()
            self._redraw()

    def _apply_play_outline(self, *_args: object) -> None:
        """The options that only apply while Play runs: ROI outlines 1.0 px thin (instead of 1.5 px), and the
        Histogram updating at most once a second. Called when Play starts or stops and when an option changes."""
        playing = self._slideshow.is_playing()
        settings = self._slideshow.settings()
        self._roi_overlay.set_sample_width(SAMPLE_WIDTH_FAST if playing and settings.thin_roi_outline else SAMPLE_WIDTH)
        calm = playing and settings.calm_histogram
        if calm != self._calm_histogram:
            self._calm_histogram = calm
            self.calm_histogram_changed.emit(calm)

    def _on_slideshow_speed_for_stats(self, _settings: object = None) -> None:
        """A speed change while Play runs: the "of N fps" in the status bar follows it (it was fixed at Play start)."""
        self._playback_stats.set_target(time.perf_counter(), self._slideshow.interval_seconds())

    def _on_playback_changed(self, playing: bool) -> None:
        self._apply_play_outline()
        self._mask_tab.edit_preview.set_playing(playing)  # no preview is computed for frames that fly past
        self.playback_changed.emit(playing)
        stats = self._playback_stats
        self._redraw_timer.setInterval(_REDRAW_COALESCE_PLAYING_MS if playing else _REDRAW_COALESCE_MS)
        if playing:
            stats.start(time.perf_counter(), self._slideshow.interval_seconds())
            self._paint_census.start()
            self._play_repaint.start()
            self._view.paint_listener = lambda s: stats.note_detail("paint_view", s)
            self._view.paint_cpu_listener = lambda s: stats.note_detail("paint_view_cpu", s)  # paint_view minus this = waiting
            self._image_item.paint_listener = lambda s: stats.note_detail("paint_image", s)
            return
        self._paint_census.stop()
        self._play_repaint.stop()
        self._held_result = None  # the frame Play stopped on is rendered again, exactly, just below
        self._view.paint_listener = None
        self._view.paint_cpu_listener = None
        self._image_item.paint_listener = None
        # The frames shown while playing were built the fast way (`RenderRequest.fast_rotation`: bilinear rotation,
        # one-pass grey picture): show the frame Play stopped on again, exactly, so the Histogram, Mask tools and
        # readout work on the exact image. Always, not only with a rotation: the fast grey picture is used for any
        # frame with no-data pixels (rotation corners, chromatic edges).
        self._schedule_redraw()
        if stats.active:
            stats.stop()
            summary = stats.summary(time.perf_counter())
            log_event(logger, "playback.summary", logging.INFO, **stats.total_fields(), **self._play_config_fields())
            self._on_tool_status(summary)

    def render_pending(self) -> bool:
        """True from a request for a new frame (including the short coalescing pause before it is sent) until that frame has been
        drawn. Other panels use it to let the image come first (`panels/image_first.py`). Always False without a dataset."""
        return self._redraw_timer.isActive() or self._latest_serial != self._last_shown_serial

    def _show_held_frame(self) -> None:
        """The slideshow's next step is due: show the frame held for it (if any); its `frame_displayed()` steps right after."""
        held, self._held_result = self._held_result, None
        if held is not None:
            self._on_rendered(held, held_for_step=True)

    def _hold_for_next_step(self, result: RenderResult) -> bool:
        """Hold a Play frame that arrived shortly before the next step (`SlideshowController.hold_frame_s`), to be shown with it."""
        if result.error is not None or result.request.serial != self._latest_serial or not self._slideshow.pipelined:
            return False
        due = self._slideshow.seconds_to_next_step()
        if due is None or due <= 0.0 or due > self._slideshow.hold_frame_s:
            return False
        self._held_result = result
        return True

    def _on_rendered(self, result: RenderResult, held_for_step: bool = False) -> None:
        if not held_for_step and self._hold_for_next_step(result):
            return
        arrived = time.perf_counter()
        serial = result.request.serial
        if serial != self._latest_serial and not (
            # While Play runs a new request goes out every few ms, so a render that takes
            # longer is *always* superseded on arrival and nothing would ever be shown.
            # Showing the newest result that has arrived is the right preview behaviour.
            self._slideshow.is_playing() and serial > self._last_shown_serial
        ):
            # Superseded while in flight - dropping it is the point (see
            # render.py's "latest request wins"), not an error.
            return
        self._last_shown_serial = serial
        times = thread_cpu_times()  # (user, kernel) seconds of the GUI thread; None off Windows
        if times is None:
            self._playback_stats.note_gui_clock(arrived, time.thread_time())
        else:
            self._playback_stats.note_gui_clock(arrived, times[0] + times[1], times[1])
        if result.error is not None or result.image is None:
            # A render error, not "what frame is displayed" - goes to the
            # status bar (`tool_status_changed`), not the dock title bar
            # subtitle (see `frame_status_changed`'s docstring).
            self._on_tool_status(f"Cannot show this frame: {result.error}")
            self._image_item.clear()
            self._no_data_item.hide()
            if serial == self._latest_serial:
                self._slideshow.frame_displayed()
            return
        if self._slideshow.is_playing():
            self.play_frame_showing.emit(int(result.request.cube_index))  # before anything else is drawn: see the signal
        image = np.asarray(result.image, dtype=np.float32)
        listeners_s = 0.0
        showing_estimate = result.request.show_background
        if showing_estimate:
            self._show_background_estimate(image, result.request)
        else:
            stage = time.perf_counter()
            display = result.display  # worked out on the render thread (display_picture.py); None = do it here
            fixed = result.request.display_levels  # the fixed Display range this frame was rendered with, or None
            if display is not None and display.picture is not None:
                self._image_item.show_prepared(image, display.picture, display.levels)
            elif fixed is not None:
                self._image_item.setImage(image, autoLevels=False, levels=np.asarray(fixed, dtype=np.float32))
            else:
                self._image_item.setImage(image, autoLevels=True)
            if display is not None:
                frame_levels = display.auto_levels
            elif fixed is None and self._image_item.levels is not None:
                frame_levels = (float(self._image_item.levels[0]), float(self._image_item.levels[1]))
            else:
                frame_levels = None
            self._view_tab.display_range_controls.show_frame_levels(frame_levels, playing=result.request.fast_rotation)
            self._playback_stats.note_detail("setImage", time.perf_counter() - stage)
            stage = time.perf_counter()
            if display is not None:
                self._last_data_range = display.data_range
            else:
                finite = image[np.isfinite(image)]
                self._last_data_range = (float(finite.min()), float(finite.max())) if finite.size else None
            self._playback_stats.note_detail("minmax", time.perf_counter() - stage)
            stage = time.perf_counter()
            # Worked out on the render thread when it could (display_picture.py); otherwise here.
            checker = display.no_data if display is not None and display.no_data_known else no_data_overlay_rgba(image)
            if checker is None:
                self._no_data_item.hide()
                self._no_data_shown = None
            elif checker is not self._no_data_shown:  # same mask as the last frame: nothing to upload
                self._no_data_item.setImage(checker, autoLevels=False)
                self._no_data_item.show()
                self._no_data_shown = checker
            self._playback_stats.note_detail("nodata", time.perf_counter() - stage)
            self._current_display_image = image
            stage = time.perf_counter()
            self._tints.update_highlight()
            self._playback_stats.note_detail("highlight", time.perf_counter() - stage)
        if not self._view_range_restored:
            # Once, on this panel's first successful render: a later navigation must never snap the view back to the saved position (it would fight the user's own
            # panning and zooming). `None` (first launch, nothing saved) leaves pyqtgraph's auto-range in charge, as before this existed.
            self._view_range_restored = True
            if self._initial_view_range is not None:
                x_range, y_range = self._initial_view_range
                self._plot.vb.setRange(xRange=x_range, yRange=y_range, padding=0.0)
        if not showing_estimate:
            # The estimate is not data: the Histogram etc. keep the real frame.
            listeners_started = time.perf_counter()
            self.image_rendered.emit(image, result.request.cube_index, result.request.wavelength_nm)
            listeners_s = time.perf_counter() - listeners_started
            self._playback_stats.note_detail("listeners", listeners_s)
            stage = time.perf_counter()
            self._frame_text.show_frame(result.request.cube_index, result.request.wavelength_nm)
            self._playback_stats.note_detail("status", time.perf_counter() - stage)
        if not showing_estimate:
            stage = time.perf_counter()
            self._request_prefetch(result.request.cube_index, result.request.wavelength_nm)
            self._playback_stats.note_detail("prefetch_plan", time.perf_counter() - stage)
        self._last_image_shape = result.image.shape[:2]
        self._area_tool.set_image_shape(self._last_image_shape)
        # The crop tool's clamp bound: right the moment Crop is active (it renders the *uncropped* frame, `_PREVIEW_TOOLS`), briefly the smaller cropped shape as
        # the tool is first switched on, corrected by the next preview render ~100 ms later (`_schedule_redraw` runs on every active-tool change).
        self._crop_tool.set_frame_size(result.image.shape[1], result.image.shape[0])
        stage = time.perf_counter()
        self._decorations.update_chunk_grid()
        self._decorations.update_source_grid()
        self._playback_stats.note_detail("chunkgrid", time.perf_counter() - stage)
        if self._playback_stats.active:
            line = self._playback_stats.note_rendered(
                result.request.serial, arrived, time.perf_counter(),
                load_s=result.load_seconds, prep_s=result.preprocess_seconds, listeners_s=listeners_s,
                picture_s=result.display_seconds,
                submitted_at=result.request.submitted_at, started_at=result.started_at,
                finished_at=result.finished_at, blocked_by_prefetch=result.request.submitted_while_prefetching,
            )
            if line is not None:
                window = self._playback_stats.last_window_fields()
                log_event(logger, "playback.window", logging.DEBUG, **window, **self._play_config_fields())
                log_event(logger, "playback.paints", logging.DEBUG, **self._paint_census.window_fields(int(window["shown"])))
                self._on_tool_status(line)
        if serial == self._latest_serial:
            # Only the newest request releases the brake: an older result shown while a newer request is
            # still in flight must not let the slideshow run further ahead. With a pipelined show this
            # call runs the next step right here (last, so this frame's stats are complete).
            self._slideshow.frame_displayed()
        if self._play_repaint.active:
            self.play_frame_shown.emit()  # the Sensorgram marks its cursor strip ...
            self._play_repaint.frame_shown()  # ... then this view repaints, and one window repaint draws everything
    def _request_prefetch(self, cube_index: int, wavelength_nm: float) -> None:
        """Have the render thread read the planes the user is likely to open
        next into the plane cache (`prefetch.py`) once it is idle."""
        if self._slideshow.is_playing():
            # While Play runs, read the frames it will show next, in its own order, and start at once
            # (the usual quiet period is longer than a frame, so read-ahead never ran: every frame was
            # a first read from disk, ~60 ms of each ~150 ms cycle on the maintainer's data).
            upcoming = self._slideshow.upcoming_frames(_PLAY_READ_AHEAD)
            if upcoming:
                self._renderer.prefetch(upcoming, quiet_seconds=0.0)
                return
        self._renderer.prefetch(
            prefetch_order(
                cube_index,
                wavelength_nm,
                self._dataset.spectral_cubes(),
                self._dataset.wavelengths_for_cube(cube_index),
            )
        )

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
        self._frame_text.forget_frame()  # a transient estimate, not the real frame: not re-described on a unit change
        cube_text = f"Cube {self._cube_display.label_for(request.cube_index)}"
        wavelength_text = f"{request.wavelength_nm:.0f} nm"
        self._frame_text.set_status(
            f"{cube_text}, {wavelength_text} - background estimate{spread}",
            columns=self._frame_text.fields(
                cube=cube_text, wavelength=wavelength_text, comment=f"Background estimate{spread}"
            ),
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

    def _bump_roi_revision(self, *_args: object) -> None:
        self._roi_revision += 1

    def _draw_overlays(self) -> None:
        stats = self._playback_stats
        if not stats.active:
            self._decorations.draw_crop_outline()
            self._decorations.draw_landmarks()
            self._decorations.draw_scale_bar()
            self._draw_roi_overlay(only_if_changed=True)
            return
        for name, draw in (
            ("ov_crop", self._decorations.draw_crop_outline), ("ov_landmarks", self._decorations.draw_landmarks),
            ("ov_scalebar", self._decorations.draw_scale_bar), ("ov_rois", lambda: self._draw_roi_overlay(only_if_changed=True)),
        ):
            started = time.perf_counter()
            draw()
            stats.note_detail(name, time.perf_counter() - started)

    def _draw_roi_overlay(self, *, only_if_changed: bool = False) -> None:
        """The ROI circles, reference rings and labels (drawn by `RoiOverlay`).
        Also called alone when a ROI display option changes, so that needs no
        new image render."""
        if self._active_tool.active() in _PREVIEW_TOOLS:
            # ROI positions are in cropped/processed space; over the
            # uncropped preview they would be drawn in the wrong place.
            self._roi_overlay.clear()
            self._tuning_overlay.clear()
            self._roi_draw_key = None
            return

        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        self._tuning_overlay.update(self._tuning_view, affine)  # the tuning crosses: not part of the ROI layer cache, a comparison when nothing changed
        # `only_if_changed` (the per-frame redraw, i.e. every wavelength step): ~40 ms of the GUI thread
        # per frame on 100+ ROIs (measured 2026-10-14), and nothing the overlay draws changes between two
        # wavelengths unless the ROIs, the selection, the cube or this frame's chromatic shift did. Every
        # other caller (a style, label or group-label change) forces the redraw.
        # `state` = what changes from one frame to the next (the chromatic shift; the cube only with a ROI timeline);
        # `epoch` = everything else that decides how the overlay looks. The overlay layer cache (`overlay_cache.py`)
        # keeps one picture per state while the epoch holds: the same wavelength shows the same overlay in every cube.
        state = (
            self._roi_toolbox.geometry_segment(frame[0]),  # the cubes between two ROI-timeline changes look the same
            np.asarray(affine, dtype=np.float64).tobytes(),
        )
        epoch = (
            self._roi_revision, frozenset(self._selection.selected_roi_ids()), self._roi_overlay.appearance_key(), self._tuning_best_key(),
            self.signal_map.revision(),
        )
        key = (state, epoch)
        layers = self._roi_overlay.layers
        if only_if_changed and key == self._roi_draw_key:
            layers.repeat(epoch, state)  # the same overlay again (Play through cubes): the cache may keep its picture now
            return
        if only_if_changed:
            if layers.begin(epoch, state):  # a picture of this very state exists: the items need not be re-drawn
                self._roi_draw_key = key
                return
        else:
            layers.invalidate()  # a setting or a gesture asked for this: start from scratch
        rois = self._roi_toolbox.rois_at(self._current_cube())
        if not rois:
            self._roi_overlay.clear()
            self._roi_draw_key = key
            return

        self._roi_draw_key = key
        self._draw_overlay_items(rois, frame, affine, state)

    def _draw_overlay_items(self, rois, frame: tuple[int, float], affine: np.ndarray, state: object) -> None:
        """Compute and set the overlay items for `frame` (the expensive part), and tell the layer cache which state they hold."""
        detection = self._roi_toolbox.detection_settings()
        self._roi_overlay.draw(
            rois,
            self._with_tuning_best(rois, self._roi_queries.view_centers(frame, affine), affine),
            self._selection.selected_roi_ids(),
            affine,
            detection.default_border(),
            self._roi_toolbox.groups(),
            color_override=self.signal_map.colors(),
        )
        self._roi_overlay.layers.mark_drawn(state)

    # -- ROI position tuning (2026-10-29) --------------------------------------------------------------------------------

    def set_tuning_view(self, view: TuningView | None) -> None:
        """Show the trial in hand (None: none): the crosses of the tried centres and, when the view says so, the ROIs at their best centres. A drawing only:
        no ROI moves. The app shell hands over `RoiTuningModule.view()` whenever it changes."""
        before = self._tuning_best_key()
        self._tuning_view = view
        if self._last_image_shape is None:
            return
        if self._tuning_best_key() != before:
            self._draw_roi_overlay()  # the ROIs move on the screen (forced: the layer cache holds the old picture)
        else:
            self._tuning_overlay.update(view, self._chromatic.affine_for((self._current_cube(), self._current_wavelength())))

    def _tuning_best_key(self) -> int | None:
        """What the ROI overlay must know about the tuning view: None while the ROIs are drawn where they are, else the view's revision."""
        view = self._tuning_view
        return view.revision if view is not None and view.show_best and view.moves_any else None

    def _with_tuning_best(self, rois, centers: np.ndarray, affine: np.ndarray) -> np.ndarray:
        """``centers`` (view centres of ``rois``, `RoiQueries.view_centers`) with the tuned ROIs moved to their best centres when the view shows them there. A shift of ``(dx, dy)`` in
        processed pixels moves a display centre by the affine's image of the shift, as the chromatic correction carries a shifted ROI to every wavelength."""
        view = self._tuning_view
        if self._tuning_best_key() is None or view is None:
            return centers
        column_of = {roi_id: column for column, roi_id in enumerate(view.roi_ids)}
        rows = [row for row, roi in enumerate(rois) if roi.area_roi_id in column_of and view.best_offsets[column_of[roi.area_roi_id]].any()]
        if not rows:
            return centers
        columns = [column_of[rois[row].area_roi_id] for row in rows]
        start = view.roi_xy[columns]
        moved = centers.copy()
        moved[rows] += apply_affine_to_points(start + view.best_offsets[columns], affine) - apply_affine_to_points(start, affine)
        return moved

    def _refresh_overlay_items(self) -> None:
        """The layer cache's refresher (`OverlayLayers.set_refresher`): the view changed while the items still hold an earlier
        wavelength's geometry (a cache hit skipped their re-draw); bring them up to date before the repaint."""
        if self._active_tool.active() in _PREVIEW_TOOLS:
            return
        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        rois = self._roi_toolbox.rois_at(self._current_cube())
        if not rois:
            return
        state = (
            self._roi_toolbox.geometry_segment(frame[0]),
            np.asarray(affine, dtype=np.float64).tobytes(),
        )
        self._draw_overlay_items(rois, frame, affine, state)

    def _on_group_labels_changed(self) -> None:
        """The group-label menu changed: show its settings on the overlay and redraw (no image render)."""
        self._roi_overlay.group_labels = self._roi_tab.group_label_settings()
        if self._roi_overlay.group_label_item is not None:
            self._roi_overlay.group_label_item.set_settings(self._roi_overlay.group_labels)
        if self._last_image_shape is not None:
            self._draw_roi_overlay()

    def _apply_roi_overlay_style(self) -> None:
        """Put the ROI display options on the curves (`RoiOverlay.apply_style`).
        The default colour decides which curve a colourless ROI is on, so the
        circles are redrawn as well."""
        self._roi_overlay.apply_style()
        if self._last_image_shape is not None:
            self._draw_roi_overlay()

    def _on_roi_overlay_changed(self, kind: str, field: str, value: object) -> None:
        """A ROI display control changed: ``kind`` is "sample" or "reference",
        ``field`` "visible", "color" (a ``#rrggbb`` string) or "alpha"."""
        self._roi_display.set(kind, field, value)  # the one shared style; the overlay and the ROI table both read it
        self._apply_roi_overlay_style()
        if self._roi_overlay_store is not None:
            self._roi_overlay_store.set(ROI_OVERLAY_KEYS[kind][field].key, value)

    @property
    def overlay_cache_mb(self) -> int:
        """Size of the ROI overlay cache in MB (0 = off). See `overlay_cache.py`."""
        return int(round(self._roi_overlay.layers.budget_megabytes))

    def set_overlay_cache_mb(self, megabytes: int) -> None:
        """Preferences: how much memory the ROI overlay cache may use (0 = off; remembered). Takes effect at once: the
        overlay is re-drawn for the frame on screen, so nothing out of date stays visible."""
        megabytes = max(int(megabytes), 0)
        changed = megabytes != self.overlay_cache_mb
        self._roi_overlay.layers.set_budget_bytes(megabytes * 1024 * 1024)
        if changed and self._last_image_shape is not None:
            self._draw_roi_overlay()
        if self._roi_overlay_store is not None:
            self._roi_overlay_store.set(IMAGE_OVERLAY_CACHE_MB.key, megabytes)

    @property
    def gpu_drawing(self) -> bool:
        """The canvas is painted through OpenGL (Preferences; `gpu_view.py`)."""
        return type(self._view.viewport()).__name__ == "QOpenGLWidget"

    def set_gpu_drawing(self, on: bool) -> bool:
        """Preferences: draw the canvas with the graphics card (remembered). Returns what is in effect (False if OpenGL is not available)."""
        applied = apply_gpu_drawing(self._view, bool(on))
        if self._roi_overlay_store is not None:
            self._roi_overlay_store.set(IMAGE_GPU_DRAWING.key, applied)
        return applied

    def overlay_cache_status(self) -> str:
        return self._roi_overlay.layers.status_text()

    def _on_roi_labels_toggled(self, shown: bool) -> None:
        self._roi_overlay.labels_visible = bool(shown)
        if self._last_image_shape is not None:
            self._draw_roi_overlay()
        if self._roi_overlay_store is not None:
            self._roi_overlay_store.set(IMAGE_ROI_LABELS.key, bool(shown))

    def _on_roi_label_placement_changed(self, placement: str) -> None:
        """The ROI-label place grid changed: only the label item repaints (nothing is redrawn or recomputed).
        The grid saves its own pick (`RoiTab.bind_ui_state`)."""
        self._roi_overlay.set_label_placement(placement)

    def _restore_slideshow_settings(self, store: UiStateStore) -> None:
        """Slideshow speed / mode / repeat / end back as last left, and keep saving them (`slideshow_state.py`)."""
        restore_slideshow_settings(store, self._slider_ribbon, self._slideshow)

    def _sync_roi_overlay_controls(self) -> None:
        """Show the panel's ROI display options on the ribbon controls (after
        a restore) without those controls reporting them back."""
        self._roi_tab.show_style(self._roi_overlay)
        self._view_tab.sync_roi_copies()
        self._apply_roi_overlay_style()

    def _update_chromatic_tab_state(self, *_args: object) -> None:
        """Green "Chromatic" tab while a fitted correction is switched on, so
        it is visible even when another tab is open."""
        settings = self._chromatic.settings()
        applied = bool(settings.chromatic_correction_enabled) and self._chromatic.has_fitted_models
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

    # -- interaction --------------------------------------------------------

    def _on_active_tool_changed(self, tool: ImageTool | None) -> None:
        self._lock_layer.refresh()  # hidden over Rotate's / Crop's uncropped canvas
        self._rotate_tool.set_active(tool is ImageTool.ROTATE)
        self._crop_tool.set_active(tool is ImageTool.CROP)
        self._measure_tool.set_active(tool is ImageTool.MEASURE)
        self._crop_measure.reposition_measure()
        self._view.viewport().unsetCursor()  # drop any resize/move cursor left over from Crop
        if tool is ImageTool.SELECT_AREA:
            self._view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._area_tool.cancel_gesture()
        self._tool_status = ""
        self.tool_status_changed.emit("")  # drop a stale status from the tool just switched away from
        self._refresh_tool_info(tool)
        if tool is not None:
            # So Esc (and Rotate's arrow keys) reach the tool without an extra click: the tools are armed from buttons that do not take the keyboard.
            self._view.setFocus()
        # Preview tools swap cropped <-> uncropped and show/hide the ROI overlay.
        self._schedule_redraw()

    def _on_tool_status(self, text: str) -> None:
        self._tool_status = text
        self.tool_status_changed.emit(text)

    def frame_columns(self) -> tuple[SubtitleColumn, ...]:
        """The dock fields of the last frame status (to seed a dock that connected after the panel was built)."""
        return self._frame_text.columns()

    def _on_reference_frame_changed(self) -> None:
        self._slider_marks.update_marks()
        self._refresh_frame_tools()  # Go to reference is enabled only while a reference exists
        self._frame_text.refresh()

    def _on_frame_roles_changed(self) -> None:
        self._slider_marks.update_all()
        self._refresh_frame_tools()
        self._frame_text.refresh()

    def _on_cube_display_changed(self, _mode: str) -> None:
        """The cube unit (number / start time) changed anywhere in the app."""
        self._frame_text.forget_widest()  # "Cube 314" and "Cube 1:23:45" are not equally wide
        self._cube_title.setText(self._cube_display.axis_title())
        cubes = self._dataset.spectral_cubes()
        self._cube_slider.set_ticks(list(cubes), self._slider_marks.cube_slider_major_ticks(cubes))
        self._slider_marks.update_roi_marks()  # the mark tooltips name cubes in the new unit
        self._refresh_frame_tools()
        self._frame_text.refresh()

    def _frame_tools_state(self) -> FrameToolsState:
        """What the frame icons show now (ribbon and right-click menu alike)."""
        if not self._dataset.spectral_cubes():
            return FrameToolsState(enabled=False)
        cube, wavelength = self._current_cube(), self._current_wavelength()
        roles = self._frame_roles
        return FrameToolsState(
            time_mode=self._cube_display.effective_mode() == MODE_TIME,
            time_available=self._cube_display.time_available(),
            wavelength_nm=wavelength,
            cube_label=self._cube_display.label_for(cube),
            cube_ignored=roles is not None and cube in roles.ignored_cubes(),
            wavelength_ignored=roles is not None and wavelength in roles.ignored_wavelengths(),
            dark_is_this_wavelength=roles is not None and roles.dark_wavelength() == wavelength,
            has_dark=roles is not None and roles.dark_wavelength() is not None,
            any_ignored_anywhere=roles is not None and bool(roles.ignored_cubes() or roles.ignored_wavelengths()),
            has_reference=self._resolve_reference_frame() is not None,
        )

    def _refresh_frame_tools(self) -> None:
        self._slider_ribbon.frame_tools.refresh(self._frame_tools_state())
        self._slider_ribbon.set_enabled_for_dataset(bool(self._dataset.spectral_cubes()))

    def _connect_frame_tools(self, tools: FrameToolsWidget) -> None:
        """Turn the icon clicks into commands on the owning modules."""
        roles = self._frame_roles
        tools.unit_toggle_requested.connect(self._toggle_cube_unit)
        tools.go_to_reference_requested.connect(self._go_to_reference)
        tools.set_reference_requested.connect(
            lambda: self._reference_frame.set_manual_frame(self._current_cube(), self._current_wavelength())
        )
        tools.find_reference_requested.connect(self._reference_frame.request_find)
        if roles is None:
            return
        tools.ignore_cube_requested.connect(lambda ignore: roles.set_cube_ignored(self._current_cube(), ignore))
        tools.ignore_wavelength_requested.connect(lambda ignore: roles.set_wavelength_ignored(self._current_wavelength(), ignore))
        tools.clear_ignores_requested.connect(self._clear_current_ignores)
        tools.clear_all_ignores_requested.connect(roles.clear_all_ignores)
        tools.pick_dark_requested.connect(
            lambda pick: roles.set_dark(self._current_wavelength()) if pick else roles.clear_dark()
        )
        tools.clear_dark_requested.connect(roles.clear_dark)

    def _go_to_reference(self) -> None:
        """Move both sliders to the reference frame (nothing while there is none)."""
        frame = self._resolve_reference_frame()
        if frame is None:
            return
        self._selection.set_cube(frame[0])
        self._selection.set_wavelength(frame[1])

    def _toggle_cube_unit(self) -> None:
        if self._cube_display.time_available():
            self._cube_display.set_mode(MODE_CUBE if self._cube_display.effective_mode() == MODE_TIME else MODE_TIME)

    def _clear_current_ignores(self) -> None:
        """Stop ignoring the cube and the wavelength shown now (two commands, so two undo steps)."""
        roles = self._frame_roles
        if roles is None:
            return
        if self._current_cube() in roles.ignored_cubes():
            roles.set_cube_ignored(self._current_cube(), False)
        if self._current_wavelength() in roles.ignored_wavelengths():
            roles.set_wavelength_ignored(self._current_wavelength(), False)

    def _show_slider_menu(self, slider: DataAxisSlider, pos) -> None:
        """Right-click menu of either navigation slider: the same icon controls
        as the slider ribbon (`FrameToolsWidget`, stacked with titles). Any
        click runs its command and closes the menu."""
        if not slider.isEnabled():
            return
        menu = QMenu(slider)
        tools = FrameToolsWidget(stacked=True, parent=menu)
        self._connect_frame_tools(tools)
        tools.refresh(self._frame_tools_state())
        tools.used.connect(menu.close)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(tools)
        menu.addAction(holder)
        menu.exec(slider.mapToGlobal(pos))

    def _info_text(self, tool: ImageTool | None) -> str:
        """The info icon's tooltip for the open ribbon tab (2026-10-06): the
        canvas controls of the active tool on the tool tabs ("Image tools",
        "ROIs"), a short explanation of the tab on the others."""
        category = self._tool_ribbon.current_category() if hasattr(self, "_tool_ribbon") else None
        if category == EXPORT_TAB:
            return self._export_tab.info_html()
        if category == ANALYSIS_TAB:
            return self._analysis_tab.info_html()
        return _TAB_INFO.get(category) or controls_text(tool)

    def _on_analysis_info_changed(self) -> None:
        """The Analysis tab's "i" text carries what the last scan did: re-read it while that tab is open."""
        if hasattr(self, "_tool_ribbon") and self._tool_ribbon.current_category() == ANALYSIS_TAB:
            self._refresh_tool_info(self._active_tool.active())

    def _on_export_info_changed(self) -> None:
        """The Export tab's "i" text carries live chunk counts: re-read it while that tab is open."""
        if hasattr(self, "_tool_ribbon") and self._tool_ribbon.current_category() == EXPORT_TAB:
            self._refresh_tool_info(self._active_tool.active())

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._view and event.type() == QEvent.Type.KeyPress:
            if self._interaction.handle_key(event.key(), event.modifiers()):
                event.accept()
                return True
        if watched is self._top_bar:  # sees every event of the bar, also while the panel is still being built
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                # Buttons and fields take their own double-clicks; one that reaches the bar (or the
                # ribbon, a group caption) landed on empty space.
                self._top_drawer.set_expanded(False)
                return True
            return super().eventFilter(watched, event)
        if watched is self._cube_title and event.type() == QEvent.Type.MouseButtonDblClick:
            self._toggle_cube_unit()  # same as the unit icon in the ribbon and the right-click menu
            return True
        return super().eventFilter(watched, event)

    # -- canvas interaction: routed by `CanvasInteraction` (interaction.py) ---------------

    def _on_scene_clicked(self, event: object) -> None:
        if self.signal_map.owns_click(event):  # a click on the signal map's legend opens its menus, nothing else
            return
        self._interaction.on_scene_clicked(event)

    def _on_scene_moved(self, scene_pos: object) -> None:
        self._interaction.on_scene_moved(scene_pos)
        self.signal_map.hover(scene_pos)  # the value of the ROI under the cursor, while the map is on

    def _on_left_drag_event(self, ev: object) -> bool:
        # (the view box is built before `_interaction` exists, so it is handed this late-binding method)
        return self._interaction.on_left_drag(ev)

    def _in_selection(self, x: float, y: float) -> bool:
        """Whether display point (x, y) may be edited under the current area
        selection (always True with none)."""
        return self._interaction.in_selection(x, y)

    def roi_at(self, x: float, y: float) -> int | None:
        """The ROI whose sample aperture contains display-space point (x, y), or `None` (`RoiQueries.roi_at`). Public: the one piece of
        this panel's hit-testing worth driving directly from a test, so a test needs no screen coordinates."""
        return self._roi_queries.roi_at(x, y)

    def rois_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> set[int]:
        """Ids of the ROIs whose display-space *centre* lies in the rectangle (`RoiQueries.rois_in_rect`). Public for the same reason as `roi_at`."""
        return self._roi_queries.rois_in_rect(x0, y0, x1, y1)

    def _on_drag(self, roi_id: int, x: float, y: float) -> None:
        """Forwards a drag gesture to the owning module - never mutates ROI
        state directly."""
        if not self._in_selection(x, y):
            return  # the area selection also limits where an ROI may be moved
        self._roi_toolbox.request_move(roi_id, x, y)

    # -- teardown -----------------------------------------------------------

    def closeEvent(self, event) -> None:
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
