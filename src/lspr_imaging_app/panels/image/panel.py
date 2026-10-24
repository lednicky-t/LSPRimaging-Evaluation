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

import html

import itertools
import math
import time
import logging
from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics
from PyQt6.QtWidgets import (
    QMenu,
    QApplication,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
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
    MaskEditTool,
    MaskModule,
    MaskScopeModule,
)
from ...image_tools.geometry.model import CropDefinition, GeometrySettings
from ...image_tools.geometry.transform import raw_to_processed_affine_xy
from ...diagnostics.thread_times import thread_cpu_times
from ...image_tools.preprocess import area_selection_to_raw, resolve_external_mask
from ...roi import RoiToolbox
from ...roi.rasterize import effective_reference_diameters
from .no_data import format_pixel_value, no_data_overlay_rgba
from ...selection import (
    AreaSelectionModule,
    CubeDisplayModule,
    HighlightRangeModule,
    ReferenceFrameModule,
    SelectionModule,
)
from ...selection.cube_display_module import MODE_CUBE, MODE_TIME, format_elapsed_seconds
from ...selection.frame_roles_module import ROLE_DARK, ROLE_IGNORED, ROLE_REFERENCE, FrameRolesModule
from ..cursor_overlay import CursorOverlay
from ..ribbon_drawer import RibbonDrawer
from ..subtitle_column import SubtitleColumn
from .landmark_overlay import draw_landmarks
from .slider_ticks import cube_slider_major_ticks, wavelength_slider_major_ticks
from .transforms_settings import TransformsSection
from ...image_tools.chromatic_auto_task import ChromaticAutoDetect
from ...image_tools.mask.mask_pointers import MaskPointersModule
from ...image_tools.mask_auto_task import MaskAutoTask
from ...image_tools.mask_export_task import MaskExportTask
from ...image_tools.mask_scan_task import TransientScanTask
from ...storage.session_carry_task import SessionCarry
from ...roi.array_task import ArrayAction
from ...roi.display_style import RoiDisplayStyle
from ...roi.model import SCOPE_PERSISTENT
from ...roi.scope import RoiEditTarget, RoiScopeModule
from .area_selection_tool import AreaSelectionTool
from .background_tab import BACKGROUND_INFO_HTML, BackgroundTab
from .chromatic_tab import ChromaticCorrectionTab, ChromaticUiValues
from .canvas_tools import _ICON_SIZE, style_bar_icon_button
from .crop_size_controls import CropSizeControls
from .crop_tool import CropTool
from .data_axis_slider import DataAxisSlider
from .frame_tools import FrameToolsState, FrameToolsWidget
from .general_group import AreaSelectionPicker, style_general_icon_button
from .image_controls import ImageViewBox, controls_text
from .interaction import CanvasInteraction
from .mask_tab import MaskTab
from .measure_controls import MeasureCalibrationControls
from .measure_line_tool import MeasureLineTool
from .overlay_cache import environment_budget_mb
from .overlay_style import OverlayStyle
from .overlay_tint import OverlayTint
from .prefetch import prefetch_order
from .render import ImageRenderer, RenderRequest, RenderResult
from .roi_gestures import RoiGestures, SelectedApertures
from .roi_overlay import SAMPLE_WIDTH, SAMPLE_WIDTH_FAST, RoiOverlay, add_curve
from .array_actions import AutoCropReadiness
from .roi_tab import RoiTab
from .rotate_line_tool import RotateLineTool
from .display_range_controls import FULL_16BIT_RANGE
from .view_tab import ViewTab
from .export_tab import ExportTab
from ...storage.ui_state_keys import (
    IMAGE_CURSOR_READOUT,
    IMAGE_MASK_EDIT_TOOL,
    IMAGE_OVERLAY_CACHE_MB,
    IMAGE_ROI_LABELS,
    IMAGE_SCALE_BAR_COLOR,
    MASK_ROWS_VISIBLE,
    ROI_OVERLAY_KEYS,
    ROI_ROW_SELECTED_ONLY,
    ROI_ROW_VISIBLE,
    ROI_SCOPE,
    SLIDER_COLOR_KEYS,
    SLIDER_RIBBON_OPEN,
    TOP_RIBBON_OPEN,
    SLIDESHOW_END,
    SLIDESHOW_SKIP_DARK,
    SLIDESHOW_SKIP_IGNORED,
    SLIDESHOW_THIN_ROI_OUTLINE,
    SLIDESHOW_CALM_HISTOGRAM,
    SLIDESHOW_MODE,
    SLIDESHOW_REPEAT,
    SLIDESHOW_SPEED,
    read,
)
from ..ui_state import UiStateStore
from .scale_bar_overlay import ScaleBarItem
from . import roi_layout_menu
from .mark_colors import cube_color, roi_color, wavelength_color
from .mask_rows import MaskRows
from .timeline_strip import TimelineStrip, StripMark
from .slider_colors import SliderColors
from ...applog.events import log_event, timed
from .playback_stats import PlaybackStats
from .timed_canvas import TimedImageItem, TimedLayoutWidget
from .slider_ribbon import SliderRibbon, SliderRibbonDrawer
from .slideshow import (
    END_CUBE,
    END_DATASET,
    MODE_ALL,
    MODE_EVENTS,
    MODE_MASK_STATES,
    MODE_ONE_WAVELENGTH,
    MODE_ROI_STATES,
    MODE_ROW_MARKS,
    SPEEDS,
    SlideshowController,
    SlideshowSettings,
)
from .tool_ribbon import ROI_TAB, VIEW_TAB, ImageToolRibbon

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_MS = 100  # sketch §8 - the already-validated coalescing window
_REDRAW_COALESCE_PLAYING_MS = 0  # while Play runs each step is one event: the redraw runs right after its handlers (0 ms still merges a cube + wavelength change into one)
_PLAY_READ_AHEAD = 3  # frames read ahead while Play runs
# Slower than the redraw coalesce above on purpose: this debounces a settings
# *write* (one JSON file per `AppSettings._persist` call - see
# `app_rewrite.py`), not a redraw, so there is no reason to pay disk I/O on
# every intermediate frame of a drag/zoom the way a 100ms redraw would.
_VIEW_RANGE_PERSIST_DEBOUNCE_MS = 600
_CHUNK_GRID_COLOR = "#84cc16"  # the lime of the Export tab's grid icon (`export_tab._GRID_ON_COLOR`)
_SOURCE_GRID_COLOR = "#38bdf8"  # the blue of the View tab's grid icon (the loaded OME-Zarr's own chunks)
_CROP_OUTLINE_COLOR = "#38bdf8"  # the crop button's active blue

# Cube/Wavelength navigation rows (ported from the stable app's
# docs/image_area_slider_redesign.md). The colours of the slider marks (reference,
# dark, ...) are the user's pick or the theme's: `slider_colors.py`.


def _slider_axis_title_style(color: str) -> str:
    """Shared 11px/600 style for the Cube/λ slider-title labels - ported
    from the stable app's `layout_builder._slider_axis_title_style` so both
    titles use identical numbers instead of two hand-copied versions that
    can drift."""
    return f"color: {color}; font-size: 11px; font-weight: 600;"


def _count_phrase(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


# The frame-role word of the dock subtitle: role -> (slider colour role, word), in the colour of its slider tick.
_ROLE_WORDS = {ROLE_DARK: ("dark", "Dark background"), ROLE_REFERENCE: ("reference", "Reference"), ROLE_IGNORED: ("ignored", "Ignored")}
_WIDEST_ROLE_WORD = max((word for _color, word in _ROLE_WORDS.values()), key=len)
_WIDEST_FRAME_TIME = "00:00:00.00"  # H:MM:SS.ss with a two-digit hour (the dock measures every digit as the widest)
CHROMATIC_TAB = "Chromatic"  # ribbon tab label (full name "Chromatic Corrections" is its tooltip)
BACKGROUND_TAB = "Background"  # ribbon tab label; green while background removal is applied
EXPORT_TAB = "Export"  # OME-Zarr export (moved here from the Workflow panel 2026-10-20); its "i" text is live (`info_html`)
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
    playback_changed = pyqtSignal(bool)  # the slideshow started (True) or stopped (False); other panels lighten their live updates
    calm_histogram_changed = pyqtSignal(bool)  # Play is running with "calm histogram" on (True), or not (False)
    display_range_changed = pyqtSignal(bool, float, float)  # the user changed the View tab's Display range: (fixed, black, white)
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
    # the dock title bar's subtitle - since 2026-10-16 as fixed-place fields,
    # see `frame_columns_changed`). Render *errors*
    # ("Cannot show this frame: ...") deliberately do not go through this
    # signal - they go through `tool_status_changed` instead (a transient
    # status-bar message, the same convention every other guarded command in
    # this panel already uses - see `_on_measure_apply_requested`), since an
    # error is not "what frame is displayed", it's "why nothing is".
    frame_status_changed = pyqtSignal(str, str)  # subtitle text, tooltip (the comment / why it is N/A)
    # The same status as fixed-place fields (2026-10-16, maintainer request: the dock title bar's
    # fields must not shift when a frame role or a comment appears): a tuple of `SubtitleColumn`
    # - frame role, cube, wavelength, time, comment. `app_rewrite.py` wires it to
    # `PanelContainer.set_subtitle_columns`; `frame_status_changed` stays the one-line text.
    frame_columns_changed = pyqtSignal(object)

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
        analysis_running: Callable[[], bool] | None = None,
        roi_scope: RoiScopeModule | None = None,
        roi_display: RoiDisplayStyle | None = None,
        frame_roles: FrameRolesModule | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_carry = session_carry  # handed to the Export tab, which asks it to take the session along
        self._frame_roles = frame_roles
        self._show_background = bool(initial_show_background)
        # Whether an analysis run is in flight (the ROI menu's Delete waits for it); tests omit it.
        self._analysis_running = analysis_running or (lambda: False)
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
        self._frame_key: tuple[int, float] | None = None  # the frame the dock subtitle describes
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
        self._frame_columns: tuple[SubtitleColumn, ...] = ()  # the same, as the dock's fixed-place fields
        # The widest cube and wavelength texts of the loaded dataset (sizes of two dock fields);
        # None = look them up on next use (dataset loaded / cleared, cube unit changed).
        self._frame_widest: tuple[str, str] | None = None

        self._serial = itertools.count(1)
        self._latest_serial = 0
        self._last_shown_serial = 0
        self._no_data_shown: np.ndarray | None = None  # the checker currently uploaded to `_no_data_item`
        self._calm_histogram = False
        self._roi_revision = 0  # bumped by every ROI-toolbox change; part of the ROI overlay's "unchanged" test
        self._roi_draw_key: tuple | None = None
        # What the landmark overlay currently shows (see `_draw_landmarks`):
        # drawing it costs 45-160 ms in all-wavelengths mode, so it is redrawn
        # only when this key changes. `_landmark_version` is bumped by anything
        # that changes the landmarks themselves (chromatic model, dataset).
        self._landmark_version = 0
        self._landmark_drawn_key: tuple | None = None
        # (resolved mask object, colour, alpha) last painted into the mask tint.
        self._mask_painted: tuple[np.ndarray, int, float] | None = None
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
            app.aboutToQuit.connect(self._transient_scan.shutdown)
            app.aboutToQuit.connect(self._mask_export.shutdown)
            app.aboutToQuit.connect(self._mask_auto.shutdown)
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
        self._cursor_overlay.set_enabled(read(store, IMAGE_CURSOR_READOUT) is True)
        self._cursor_overlay.toggled.connect(lambda on: store.set(IMAGE_CURSOR_READOUT.key, bool(on)))
        try:
            self._mask_tab.edit_tool.set_tool(MaskEditTool(store.get(IMAGE_MASK_EDIT_TOOL.key)))
        except ValueError:  # nothing saved yet, or a tool that no longer exists
            pass
        self._mask_tab.edit_tool.tool_changed.connect(lambda tool: store.set(IMAGE_MASK_EDIT_TOOL.key, tool.value))
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
        self._restore_slideshow_settings(store)
        self._set_roi_row(read(store, ROI_ROW_VISIBLE) is not False, read(store, ROI_ROW_SELECTED_ONLY) is True)
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
    def mask_export(self) -> MaskExportTask:
        """The export-all-masks task (the app shell connects it to the task indicator)."""
        return self._mask_export

    @property
    def mask_auto(self) -> MaskAutoTask:
        """The Auto mask task (the app shell connects it to the task indicator)."""
        return self._mask_auto

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
        self._view = TimedLayoutWidget(parent=self)
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

        self._image_item = TimedImageItem(axisOrder="row-major")
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

        # The ROI circles, reference rings, selection highlight and labels (roi_overlay.py).
        # How they are drawn is the "ROIs" ribbon tab (`roi_overlay_controls.py`); display-only:
        # this panel owns the values, no other module does.
        self._roi_overlay = RoiOverlay(self._plot, self._roi_display)
        self._roi_overlay_store: UiStateStore | None = None
        self._chunk_grid_curve = self._add_curve(_CHUNK_GRID_COLOR, width=1.0, dashed=True)
        self._source_grid_curve = self._add_curve(_SOURCE_GRID_COLOR, width=1.0, dashed=True)
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
        self._roi_overlay.attach_labels()
        self._roi_overlay.layers.set_refresher(self._refresh_overlay_items)
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
        roi_content = self._build_roi_tab()  # before the View tab, which carries copies of its controls
        view_content = self._build_view_tab()
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
        roi_gestures = RoiGestures(
            self._plot, self._roi_toolbox, self._selection,
            rois_in_rect=self.rois_in_rect, selected_apertures=self._selected_apertures,
            view_pixel_size=lambda: float(self._plot.vb.viewPixelSize()[0]),
            display_linear=self._display_linear, redraw_overlay=self._draw_roi_overlay,
            edit_target=self._edit_target, parent=self,
        )
        roi_gestures.refused.connect(self._on_tool_status)  # e.g. an individual change on a persistent point
        self._interaction = CanvasInteraction(
            plot=self._plot, view=self._view, menu_parent=self,
            active_tool=self._active_tool, roi_toolbox=self._roi_toolbox, selection=self._selection,
            area_selection=self._area_selection,
            rotate_tool=self._rotate_tool, crop_tool=self._crop_tool, measure_tool=self._measure_tool,
            area_tool=self._area_tool,
            image_shape=lambda: self._last_image_shape, roi_at=self.roi_at, apply_crop=self._on_crop_apply_requested,
            roi_gestures=roi_gestures,
            roi_tab_active=lambda: self._tool_ribbon.current_category() == ROI_TAB,
            to_reference=self._display_to_reference,
            analysis_running=self._analysis_running,
            notify=self._on_tool_status,
        )
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
        self._mask_tab = MaskTab(
            self._mask, self._geometry, self._chromatic, self._dataset, self._highlight_range,
            self._mask_scope, self._mask_tint, self, self,
            events=self._mask_pointers, scan=self._transient_scan, auto=self._mask_auto, export=self._mask_export,
            status=self._on_tool_status, visible_cubes=self._visible_cube_range,
        )
        self._mask_tab.overlay_visibility_changed.connect(self._on_mask_overlay_visibility_changed)
        self._mask_tab.overlay_color_changed.connect(self._on_mask_overlay_color_changed)
        self._mask_tab.overlay_alpha_changed.connect(self._on_mask_overlay_alpha_changed)
        return self._mask_tab

    def _build_roi_tab(self) -> QWidget:
        self._roi_tab = RoiTab(
            self._roi_overlay, self._roi_scope, self._geometry, self._background, self._chromatic, self._dataset,
            self._roi_toolbox, self._selection, self._array_action, self._edit_target,
            self._resolve_reference_frame, self._analysis_running, self,
        )
        self._roi_tab.overlay_changed.connect(self._on_roi_overlay_changed)
        self._roi_tab.labels_toggled.connect(self._on_roi_labels_toggled)
        self._roi_tab.label_placement_changed.connect(self._on_roi_label_placement_changed)
        self._roi_tab.label_size_changed.connect(self._roi_overlay.set_label_point_size)
        self._roi_tab.group_labels_changed.connect(self._on_group_labels_changed)
        self._roi_tab.status.connect(self._on_tool_status)
        # Auto rotate and Auto crop (Image tools tab) act through the ROI tab, which owns the array tools; that
        # tab is built before this one. Auto crop also shows which of its dependencies are still missing, and its
        # menu rows lead to the tab that fixes them.
        self._transforms_section.auto_rotate_requested.connect(self._roi_tab.auto_rotate)
        self._transforms_section.auto_crop_requested.connect(self._roi_tab.auto_crop)
        self._transforms_section.navigate_requested.connect(self._on_transforms_navigate)
        self._roi_tab.auto_crop_readiness_changed.connect(self._on_auto_crop_readiness)
        self._on_auto_crop_readiness(self._roi_tab.auto_crop_readiness())
        return self._roi_tab

    def _on_transforms_navigate(self, key: str) -> None:
        """A row of Auto crop's pick-up menu that lives in another ribbon tab: show that tab."""
        self._tool_ribbon.set_category(ROI_TAB if key == "rois" else CHROMATIC_TAB)

    def _on_auto_crop_readiness(self, readiness: AutoCropReadiness) -> None:
        self._transforms_section.set_auto_crop_state(
            levelled=readiness.levelled, rois=readiness.rois, chromatic=readiness.chromatic,
            level_note=readiness.level_note,
        )

    def _build_view_tab(self) -> QWidget:
        self._view_tab = ViewTab(
            self._geometry, self._highlight_tint, self._mask_tint, self._mask_tab.overlay_controls,
            self._chromatic_tab, self._background_tab, self._roi_tab, self._scale_bar_item.color(),
            self._dataset, self,
        )
        self._view_tab.highlight_visibility_changed.connect(self._on_highlight_overlay_visibility_changed)
        self._view_tab.highlight_color_changed.connect(self._on_highlight_overlay_color_changed)
        self._view_tab.highlight_alpha_changed.connect(self._on_highlight_overlay_alpha_changed)
        self._view_tab.mask_visibility_changed.connect(self._on_mask_overlay_visibility_changed)
        self._view_tab.mask_color_changed.connect(self._on_mask_overlay_color_changed)
        self._view_tab.mask_alpha_changed.connect(self._on_mask_overlay_alpha_changed)
        self._view_tab.roi_overlay_changed.connect(self._on_roi_overlay_changed)
        self._view_tab.scale_bar_color_changed.connect(self._on_scale_bar_color_changed)
        self._view_tab.display_range_changed.connect(self._on_display_range_edited)
        return self._view_tab

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
            is_spectral=None if self._frame_roles is None else self._frame_roles.is_spectral,
        )
        self._chromatic_tab.show_landmarks_changed.connect(self._on_show_landmarks_changed)
        self._chromatic_tab.landmark_scope_changed.connect(self._on_landmark_scope_changed)
        self._chromatic_tab.settings_applied.connect(self.chromatic_settings_applied)
        self._background_tab = BackgroundTab(
            self._background, show_background=self._show_background, parent=self
        )
        self._background_tab.show_background_changed.connect(self._on_show_background_changed)
        self._export_tab = ExportTab(
            self._dataset, self, geometry=self._geometry, roi_toolbox=self._roi_toolbox, chromatic=self._chromatic,
            status=self._on_tool_status, session_carry=self._session_carry,
        )
        self._export_tab.info_changed.connect(self._on_export_info_changed)

    def _build_top_bar(
        self, view_content: QWidget, image_tools_content: QWidget, mask_content: QWidget, roi_content: QWidget
    ) -> None:
        self._top_bar = QWidget(self)
        self._top_bar.setObjectName("imageTopBar")
        top_bar_layout = QHBoxLayout(self._top_bar)
        top_bar_layout.setContentsMargins(4, 2, 6, 2)
        top_bar_layout.setSpacing(6)
        # The always-visible "General" group is gone (2026-10-23): the cursor toggle is an icon of the View tab's
        # "Image" group, and the area-selection picker is the first group of the Mask and ROIs tabs (and an icon
        # of "Image"), one picker per place, all on the same `AreaSelectionModule`.
        self._area_pickers = [
            AreaSelectionPicker(self._area_selection, self._active_tool, parent) for parent in (mask_content, roi_content, view_content)
        ]
        self._area_picker = self._area_pickers[0]  # any of them; the Mask tab's
        self._mask_tab.add_select_picker(self._area_pickers[0])
        self._roi_tab.add_select_picker(self._area_pickers[1])

        self._tool_ribbon = ImageToolRibbon(
            [
                ("Image tools", image_tools_content),
                ("Mask", mask_content),
                (ROI_TAB, roi_content),
                # Empty on purpose (2026-10-03): filled directly here, not in
                # the Workflow panel (maintainer's plan for chromatic correction).
                (CHROMATIC_TAB, self._chromatic_tab),
                (BACKGROUND_TAB, self._background_tab),
                (VIEW_TAB, view_content),
                (EXPORT_TAB, self._export_tab),
            ],
            self,
        )
        if self._initial_ribbon_category:
            # "Histogram" was this tab's name before 2026-10-06 (settings saved then still say so).
            restored = VIEW_TAB if self._initial_ribbon_category == "Histogram" else self._initial_ribbon_category
            self._tool_ribbon.set_category(restored)  # before the connect: restoring is not a user change
        self._tool_ribbon.category_changed.connect(self.ribbon_category_changed)
        self._tool_ribbon.category_changed.connect(lambda _label: self._show_current_tab())  # a tab scrolled half out of view comes into view
        self._tool_ribbon.category_changed.connect(lambda _label: self._refresh_tool_info(self._active_tool.active()))
        # Short tab label, full name in the tooltip; the tab is green while the correction is applied.
        self._tool_ribbon.set_tab_tooltip(CHROMATIC_TAB, "Chromatic Corrections")
        # The ribbon rolls up on a double-click on the bar's empty space (`eventFilter`), leaving
        # a thin strip with a "..." handle, like the slider ribbon (2026-10-16). The "i" icon rolls
        # up with it (see below).
        self._top_drawer = RibbonDrawer(
            self._tool_ribbon, "Show the tool ribbon.", self._top_bar, handle_alignment=Qt.AlignmentFlag.AlignTop
        )
        self._top_bar.installEventFilter(self)
        top_bar_layout.addWidget(self._top_drawer)
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
        self._view_tab.add_image_icon(self._cursor_overlay.icon_label)  # first icon of the View tab's "Image" group (2026-10-23)
        self._view_tab.add_image_icon(self._area_pickers[2], 1)  # and the picker right after it

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
        self._top_drawer.expanded_changed.connect(self._tool_info.setVisible)  # rolled up: only the handle is left

        self._refresh_top_bar_theme()

    def _show_current_tab(self) -> None:
        """Scroll the tool ribbon (it scrolls when the panel is narrower than its row) so the chosen tab is fully visible."""
        self._top_drawer.ensure_visible(self._tool_ribbon.tab_button(self._tool_ribbon.current_category()))

    def _build_navigation_bar(self) -> None:
        # Cube and Wavelength navigation - ported from the stable app's
        # tick/axis-style redesign (docs/image_area_slider_redesign.md):
        # each axis gets its own full-width row (title -> slider) rather than
        # splitting one row, so each slider gets the full available width and
        # the two axes read as parallel strips. The number fields that used to
        # sit right of each slider (a typed cube / wavelength, with a
        # wavelength autocomplete) were removed 2026-10-18 (maintainer request):
        # the current cube, wavelength and time are in the dock title bar.
        # The Cube/Time toggle title-button and the
        # "exclude this image" icon button are *not* ported - see this
        # panel's module docstring "Not built here, deliberately" note for
        # why (no elapsed-time mapping / no exclusions subsystem exists on
        # this branch yet).
        self._cube_slider = DataAxisSlider(Qt.Orientation.Horizontal, self)
        self._cube_slider.setEnabled(False)
        self._cube_slider.set_accent_color(cube_color())
        self._cube_slider.valueChanged.connect(self._on_cube_slider_changed)

        self._cube_title = cube_title = QLabel(self._cube_display.axis_title(), self)
        cube_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
        cube_title.setToolTip("Double-click to switch between cube number and start time.")
        cube_title.installEventFilter(self)
        cube_row =QHBoxLayout()
        cube_row.setContentsMargins(0, 0, 0, 0)
        cube_row.setSpacing(5)
        cube_row.addWidget(cube_title)
        cube_row.addWidget(self._cube_slider, 1)

        self._wavelength_slider = DataAxisSlider(Qt.Orientation.Horizontal, self)
        self._wavelength_slider.setEnabled(False)
        self._wavelength_slider.set_accent_color(wavelength_color())
        self._wavelength_slider.valueChanged.connect(self._on_wavelength_slider_changed)

        wavelength_title = QLabel("λ (nm)", self)
        wavelength_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
        wavelength_title.setToolTip("Wavelength (nm)")
        wavelength_row = QHBoxLayout()
        wavelength_row.setContentsMargins(0, 0, 0, 0)
        wavelength_row.setSpacing(5)
        wavelength_row.addWidget(wavelength_title)
        wavelength_row.addWidget(self._wavelength_slider, 1)

        # The ROI row (2026-10-16): a thin strip under the Cube slider, on the Cube axis, with a mark wherever the
        # ROI layout changes (`timeline_strip.py`). Its title takes the width of the other rows' titles (below),
        # so the strip's ticks line up with the Cube slider's.
        self._roi_strip = TimelineStrip(self)
        self._roi_strip.index_requested.connect(self._cube_slider.setValue)
        # One zoom window for the Cube slider and the ROI row (their marks must stay under their ticks); λ has its own.
        self._roi_strip.set_window(self._cube_slider.axis_window())
        self._cube_slider.axis_window().changed.connect(self._refresh_cube_slider_labels)
        self._wavelength_slider.axis_window().changed.connect(self._refresh_wavelength_slider_labels)
        roi_title = QLabel("ROIs", self)
        roi_title.setStyleSheet(_slider_axis_title_style(get_active_theme().text_muted))
        roi_title.setToolTip(
            "ROI layout. A mark wherever the ROIs change (position or size): > holds from that cube on, "
            "a line is that cube only."
        )
        roi_row = QHBoxLayout()
        roi_row.setContentsMargins(0, 0, 0, 0)
        roi_row.setSpacing(5)
        roi_row.addWidget(roi_title)
        roi_row.addWidget(self._roi_strip, 1)
        self._roi_row = QWidget(self)
        self._roi_row.setLayout(roi_row)
        self._roi_row_selected_only = False
        self._roi_states: tuple[int, ...] = ()  # see `_roi_state_cubes`
        self._roi_states_key: tuple | None = None
        self._roi_marks_timer = QTimer(self)  # the ROI marks follow ROI edits, one update per burst (a drag)
        self._roi_marks_timer.setSingleShot(True)
        self._roi_marks_timer.setInterval(_REDRAW_COALESCE_MS)
        self._roi_marks_timer.timeout.connect(self._update_roi_marks)

        # The two Mask rows (2026-10-16): one under the Cube slider, one under the wavelength slider (`mask_rows.py`).
        # Each shares its slider's zoom window; titles are sized with the other rows' below.
        self._mask_rows = MaskRows(
            self, self._mask, self._dataset, self._selection, self._cube_display.label_for,
            cube_window=self._cube_slider.axis_window(),
            wavelength_window=self._wavelength_slider.axis_window(),
            title_style=_slider_axis_title_style(get_active_theme().text_muted),
            status=self._on_tool_status,
            events=self._mask_pointers,
            auto_mask=lambda records: self._mask_tab.auto_controls.start(records),
        )
        self._mask_rows.cube_strip.index_requested.connect(self._cube_slider.setValue)
        self._mask_rows.wavelength_strip.index_requested.connect(self._wavelength_slider.setValue)

        # All the rows' titles share one width (2026-09-30, maintainer request) -
        # without this, "Cube" and "λ (nm)" size to their own text, so the two
        # sliders started at slightly different x positions.
        # Wide enough for either unit ("Cube #" / "Cube time"), so toggling never shifts the sliders.
        title_metrics = QFontMetrics(cube_title.font())
        title_width = max(
            cube_title.sizeHint().width(),
            wavelength_title.sizeHint().width(),
            *(title_metrics.horizontalAdvance(text) + 4 for text in ("Cube (#)", "Cube (t)")),
        )
        cube_title.setFixedWidth(title_width)
        wavelength_title.setFixedWidth(title_width)
        roi_title.setFixedWidth(title_width)
        for title in self._mask_rows.titles:
            title.setFixedWidth(title_width)

        for slider in (self._cube_slider, self._wavelength_slider):
            slider.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            slider.customContextMenuRequested.connect(lambda pos, s=slider: self._show_slider_menu(s, pos))
        self._cube_display.mode_changed.connect(self._on_cube_display_changed)

        navigation = QVBoxLayout()
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(4)
        navigation.addLayout(cube_row)
        navigation.addWidget(self._roi_row)
        navigation.addWidget(self._mask_rows.cube_row)
        navigation.addLayout(wavelength_row)
        navigation.addWidget(self._mask_rows.wavelength_row)

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
        # flush against the dock's left edge. Top/bottom stay flush - nothing
        # else in this bar needs the room. No right margin since 2026-10-18
        # (maintainer: the gap between the sliders and the panel edge was too
        # big): a slider already keeps 14 px inside itself (`SIDE_INSET`, room
        # for half of an end tick label), and the dock's card gap adds 3 more.
        controls = QVBoxLayout()
        controls.setContentsMargins(10, 0, 0, 0)
        controls.setSpacing(4)
        # The slider ribbon (2026-10-13): frame icon controls, Play, slideshow settings, slider colours.
        self._slider_colors = SliderColors()
        self._slider_ribbon = SliderRibbon(self)
        self._slider_ribbon.slider_colors.set_colors(self._slider_colors)
        self._slider_ribbon.slider_colors.color_changed.connect(self._on_slider_color_changed)
        self._slideshow = SlideshowController(
            self._dataset, self._selection, self._frame_roles, self, state_cubes=self._state_cubes_for_mode
        )
        self._slider_ribbon.set_roi_row(True, False)  # the defaults; `restore_ui_state` puts back what was saved
        self._slider_ribbon.roi_row_toggled.connect(self._on_roi_row_toggled)
        self._slider_ribbon.roi_selected_only_toggled.connect(self._on_roi_selected_only_toggled)
        self._slider_ribbon.play_toggled.connect(self._slideshow.toggle)
        # The wheel over the ROI row always hops between its marks, whatever Play steps through.
        self._roi_strip.step_requested.connect(lambda direction: self._slideshow.step(direction, MODE_ROI_STATES))
        self._roi_strip.mark_menu_requested.connect(self._on_roi_mark_menu)
        # The wheel over the Mask row under the Cube slider hops between ALL its marks, masks and standing scan pointers.
        self._mask_rows.cube_strip.step_requested.connect(lambda direction: self._slideshow.step(direction, MODE_ROW_MARKS))
        # The wheel turned on past the last / first wavelength goes on into the next / previous cube, as Play does in its
        # "all wavelengths" order (first wavelength of the next cube / last of the previous), whatever mode Play is set to.
        self._wavelength_slider.edge_step_requested.connect(lambda direction: self._slideshow.step(direction, MODE_ALL))
        self._slider_ribbon.set_mask_rows(True)  # the default; `restore_ui_state` puts back what was saved
        self._slider_ribbon.mask_rows_toggled.connect(self._mask_rows.set_shown)
        self._slider_ribbon.settings_changed.connect(self._slideshow.set_settings)
        self._slider_ribbon.settings_changed.connect(self._apply_play_outline)
        self._slider_ribbon.settings_changed.connect(self._on_slideshow_speed_for_stats)  # after set_settings: new interval
        self._slideshow.playing_changed.connect(self._slider_ribbon.set_playing)
        # Where the time goes while Play runs (status bar + one log line at the end).
        self._playback_stats = PlaybackStats()
        self._slideshow.playing_changed.connect(self._on_playback_changed)
        self._slideshow.step_made.connect(lambda handlers_s: self._playback_stats.note_step(time.perf_counter(), handlers_s))
        self._slideshow.step_made.connect(self._flush_redraw_for_step)
        self._connect_frame_tools(self._slider_ribbon.frame_tools)
        self._ribbon_drawer = SliderRibbonDrawer(self._slider_ribbon, self)
        controls.addWidget(self._ribbon_drawer)
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
        self._dataset.chunk_grid_preview_changed.connect(self._update_chunk_grid)
        self._dataset.source_chunk_grid_changed.connect(self._update_source_grid)

        self._active_tool.active_tool_changed.connect(self._on_active_tool_changed)

        self._geometry.geometry_changed.connect(self._invalidate_landmark_overlay)
        self._geometry.geometry_changed.connect(self._schedule_redraw)
        self._geometry.cosmetic_changed.connect(self._schedule_redraw)
        self._mask.mask_changed.connect(self._schedule_redraw)
        self._mask.cosmetic_changed.connect(self._schedule_redraw)
        self._background.background_model_changed.connect(self._schedule_redraw)
        self._chromatic.chromatic_model_changed.connect(self._invalidate_landmark_overlay)
        self._chromatic.chromatic_model_changed.connect(self._schedule_redraw)
        self._chromatic.chromatic_model_changed.connect(self._update_chromatic_tab_state)
        self._update_chromatic_tab_state()
        self._background.background_model_changed.connect(self._update_background_tab_state)
        self._update_background_tab_state()

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
        self._roi_toolbox.geometry_changed.connect(self._schedule_roi_marks)
        self._roi_toolbox.roi_ids_renumbered.connect(self._schedule_roi_marks)
        self._selection.roi_selection_changed.connect(self._on_roi_selection_for_marks)
        self._selection.cube_changed.connect(self._schedule_redraw)
        self._selection.wavelength_changed.connect(self._schedule_redraw)
        # Which ROIs are selected changes their outline, never a pixel: the background estimate's exclusion uses
        # *every* ROI (`_redraw`'s request), not the selection, so this one is always overlay-only.
        self._selection.roi_selection_changed.connect(self._schedule_overlay_redraw)

        # Keeps the nav widgets truthful for *any* source of a selection
        # change, not just this panel's own spin/slider handlers - e.g. the
        # "jump to reference" button below, or a future panel that also
        # calls `SelectionModule.set_cube`/`set_wavelength`. Matches this
        # panel's own "redraw because the module emitted a change" rule
        # (module docstring) - these are the same rule applied to the nav
        # widgets, not just the canvas.
        self._selection.cube_changed.connect(self._on_selection_cube_changed)
        self._selection.wavelength_changed.connect(self._on_selection_wavelength_changed)
        self._reference_frame.reference_frame_changed.connect(self._on_reference_frame_changed)
        if self._frame_roles is not None:
            self._frame_roles.roles_changed.connect(self._on_frame_roles_changed)

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
        for tab in ("_mask_tab", "_roi_tab", "_view_tab"):
            if hasattr(self, tab):
                getattr(self, tab).refresh_theme()
        if hasattr(self, "_tool_ribbon"):
            self._tool_ribbon.refresh_theme(get_active_theme())
        if hasattr(self, "_top_drawer"):
            self._top_drawer.refresh_theme()
        for picker in getattr(self, "_area_pickers", ()):
            picker.refresh_theme(get_active_theme())
        if hasattr(self, "_background_tab"):
            self._background_tab.refresh_theme(get_active_theme())
        if hasattr(self, "_export_tab"):
            self._export_tab.refresh_theme()
        if hasattr(self, "_cursor_readout"):
            self._style_cursor_readout()
        if hasattr(self, "_controls_bar"):
            self._refresh_controls_bar_theme()
        if hasattr(self, "_slider_ribbon"):
            self._ribbon_drawer.refresh_theme()
            self._update_all_slider_marks()  # a default colour follows the theme
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
        """No border. A single-pixel seam along the bottom edge, where this bar
        meets the canvas below it, was added 2026-09-30 (maintainer request -
        "make there a bo[r]der on the bottom to separate it from the image
        area") and taken away again 2026-10-18 (maintainer request: remove the
        edge line under the top ribbon). Kept as the live-theme-switch hook
        (see `_refresh_controls_bar_theme`, which is in the same position)."""
        self._top_bar.setStyleSheet("#imageTopBar { border: none; }")

    # -- dataset lifecycle --------------------------------------------------

    def _invalidate_landmark_overlay(self, *_args: object) -> None:
        self._landmark_version += 1

    def _stop_dataset_bound_tasks(self) -> None:
        """A scan reads frames of the dataset and Auto mask makes masks for its frames: when the dataset is loaded or cleared under
        them they stop, and nothing they had is kept (the scan reads through the dataset module as it is at each read, so it would
        otherwise go on into the new dataset and install its result in the new session)."""
        self._transient_scan.invalidate()
        self._mask_auto.invalidate()

    def _on_dataset_loaded(self, _dataset: object) -> None:
        self._stop_dataset_bound_tasks()
        self._frame_widest = None
        self._renderer.clear_prefetch()
        self._invalidate_landmark_overlay()
        self._refresh_navigation_ranges()
        self._refresh_frame_tools()
        self._schedule_redraw()

    def _on_dataset_cleared(self) -> None:
        """Reset this panel's own view state. `DatasetModule.clear_dataset`
        deliberately clears only its own reference and expects every module
        and panel holding dataset-derived state to reset itself (see its
        module docstring) - this is the first subscriber to actually do so."""
        self._stop_dataset_bound_tasks()
        self._frame_widest = None
        self._refresh_frame_tools()
        self._renderer.clear_prefetch()
        self._invalidate_landmark_overlay()
        self._mask_painted = None
        self._image_item.clear()
        self._no_data_item.hide()
        self._no_data_shown = None
        self._mask_tint.item.hide()
        self._mask_overlay_state = None
        self._highlight_tint.item.hide()
        self._current_display_image = None
        self._roi_overlay.clear()
        self._chunk_grid_curve.clear()
        self._source_grid_curve.clear()
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
        self._cube_slider.axis_window().reset()  # a dataset was loaded or cleared: the whole Cube axis again
        with _blocked(self._cube_slider):
            self._cube_slider.setEnabled(bool(cubes))
            self._cube_slider.setMinimum(0)
            self._cube_slider.setMaximum(max(len(cubes) - 1, 0))
            self._cube_slider.setSingleStep(1)
            self._cube_slider.setPageStep(1)
            self._cube_slider.set_ticks(list(cubes), self._cube_slider_major_ticks(cubes))
            if cubes and self._current_cube() in cubes:
                self._cube_slider.setValue(cubes.index(self._current_cube()))
        self._roi_strip.set_axis_count(len(cubes))
        self._mask_rows.refresh_cube_row()
        self._roi_states_key = None  # another dataset: the ROI states are looked up again
        self._refresh_wavelength_range()
        self._update_all_slider_marks()

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
            # No caller-side wiring needed for the scale-break glyph -
            # `DataAxisSlider` detects an unusually large consecutive gap in
            # `wavelengths` itself (e.g. a real 0 nm dark/reference frame
            # followed by the first real spectral wavelength) and draws it
            # automatically. See that widget's module docstring for why an
            # earlier same-day version that invented a synthetic "0" tick
            # here was wrong.
            self._wavelength_slider.set_ticks(
                list(wavelengths),
                self._wavelength_slider_major_ticks(wavelengths, self._wavelength_slider.axis_window().view_for(len(wavelengths))),
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
        self._update_slider_marks()
        self._refresh_frame_tools()

    def _on_selection_wavelength_changed(self, _wavelength: float) -> None:
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        wavelength = self._current_wavelength(wavelengths)
        with _blocked(self._wavelength_slider):
            if wavelengths and wavelength in wavelengths:
                self._wavelength_slider.setValue(wavelengths.index(wavelength))
        self._mask_rows.set_wavelength_cursor(wavelength)
        self._update_slider_marks()
        self._refresh_frame_tools()

    # -- reference-frame highlight (ReferenceFrameModule) --------------------

    def _resolve_reference_frame(self) -> tuple[int, float] | None:
        """Same resolution rule `panels/workflow/reference_frame_row.py`
        uses: Set reference = the stored snapshot; Find reference = the viewed
        cube's best-contrast wavelength (None while it is still being found).
        `ReferenceFrameModule.resolve` does the work; it only needs the viewed cube."""
        return self._reference_frame.resolve(self._selection.current_cube())

    def _update_slider_marks(self) -> None:
        """What the sliders mark besides the handle (colours: `slider_colors.py`):
        the reference frame, the dark background and the ignored wavelengths.
        Run on every cube / wavelength step, so it only looks at the viewed
        cube's own wavelengths. The ignored *cubes* are marked by
        `_update_ignored_cube_ticks`, which only runs when they or the cube
        list change."""
        self._update_reference_highlight()
        self._update_wavelength_role_ticks()

    def _update_all_slider_marks(self) -> None:
        """Every mark, after a change that is not a step (roles, colours, theme, a new dataset)."""
        self._update_slider_marks()
        self._update_ignored_cube_ticks()
        self._update_roi_marks()

    # -- the ROI row (timeline_strip.py) ------------------------------------------------------------

    def _on_roi_row_toggled(self, shown: bool) -> None:
        self._roi_row.setVisible(bool(shown))
        if shown:
            self._update_roi_marks()

    def _on_roi_selected_only_toggled(self, only_selected: bool) -> None:
        self._roi_row_selected_only = bool(only_selected)
        self._update_roi_marks()

    def _set_roi_row(self, shown: bool, selected_only: bool) -> None:
        """Put the ROI row's two toggles back as saved (no signal, so nothing is saved again)."""
        self._roi_row_selected_only = bool(selected_only)
        self._roi_row.setVisible(bool(shown))
        self._slider_ribbon.set_roi_row(shown, selected_only)
        self._update_roi_marks()

    def _schedule_roi_marks(self, *_args: object) -> None:
        if not self._roi_row.isHidden():
            self._roi_marks_timer.start()

    def _on_roi_selection_for_marks(self, *_args: object) -> None:
        if self._roi_row_selected_only:
            self._schedule_roi_marks()

    def _cube_phrase(self, cube: int) -> str:
        label = self._cube_display.label_for(cube)
        return f"Cube {cube}" if label == str(cube) else f"Cube {cube} ({label})"

    def _on_roi_mark_menu(self, mark: StripMark, global_pos: object) -> None:
        """Right click on a mark of the ROI row: go to its cube, make it individual / persistent, or delete it. Each acts on
        the whole point (every ROI's change at that cube; the menu says how many) and is one undo step. The first cube's
        mark is the start layout: only "Go to this cube" is offered there."""
        cubes = self._dataset.spectral_cubes()
        if not 0 <= mark.index < len(cubes):
            return
        cube = cubes[mark.index]
        toolbox = self._roi_toolbox
        is_start = mark.index == 0
        point = toolbox.layout_points().get(cube)
        if is_start:
            roi_ids = frozenset(roi.area_roi_id for roi in toolbox.rois())
            kind = SCOPE_PERSISTENT
        elif point is None:
            return  # the mark was stale (its change was undone meanwhile)
        else:
            roi_ids, kind = frozenset(point.roi_ids), point.kind
        shown = (roi_ids & self._selection.selected_roi_ids()) if self._roi_row_selected_only else roi_ids
        phrase = self._cube_phrase(cube)
        choice = roi_layout_menu.show_layout_point_menu(
            self, cube_text=phrase, kind=kind, is_start=is_start,
            roi_count=len(roi_ids), hidden_count=len(roi_ids) - len(shown), pos=global_pos,
        )
        if choice is None:
            return
        what = roi_layout_menu.who(len(roi_ids))
        try:
            if choice == roi_layout_menu.GO_TO:
                self._cube_slider.setValue(mark.index)
            elif choice == roi_layout_menu.DELETE and not is_start:
                toolbox.delete_layout_point(cube)
                self._on_tool_status(f"{phrase}: layout change deleted ({what}).")
            elif choice == roi_layout_menu.MAKE_INDIVIDUAL and not is_start:
                toolbox.make_layout_point_individual(cube)
                self._on_tool_status(f"{phrase} is now an individual change ({what}); the cubes after it go back to the earlier layout.")
            elif choice == roi_layout_menu.MAKE_PERSISTENT and not is_start:
                toolbox.make_layout_point_persistent(cube)
                self._on_tool_status(f"{phrase} is now a persistent change ({what}); its layout carries on to the cubes after it.")
        except (ValueError, KeyError) as exc:
            self._on_tool_status(f"ROI row: {exc}")

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
        return self._mask_rows.state_cubes() if mode == MODE_MASK_STATES else self._roi_state_cubes()

    def _roi_state_cubes(self) -> tuple[int, ...]:
        """The cubes the ROI row marks, ascending: the first cube (the start layout) and every cube where the layout
        changes. What Previous / Next and Play step through in the "ROI states" mode, and what the wheel over the row hops
        between. Follows the "only selected ROIs" filter like the marks do (not whether the row is shown). Asked on every
        Play step in that mode, so it is cached on what it depends on."""
        cubes = self._dataset.spectral_cubes()
        selected = frozenset(self._selection.selected_roi_ids()) if self._roi_row_selected_only else None
        key = (self._roi_revision, selected, cubes[:1], len(cubes))
        if key != self._roi_states_key:
            if cubes and self._roi_toolbox.rois():
                known = set(cubes)
                states = {cubes[0], *(cube for cube in self._roi_toolbox.layout_points(selected) if cube in known)}
                self._roi_states = tuple(sorted(states))
            else:
                self._roi_states = ()
            self._roi_states_key = key
        return self._roi_states

    def _update_roi_marks(self) -> None:
        """The ROI row: a ``>`` at the first cube (the start layout, always persistent) and one mark per cube where the
        layout changes (`RoiToolbox.layout_points`: ``>`` persistent, a line individual), counting only the selected ROIs
        when that toggle is on. Cost grows with the number of ROI changes, not with the dataset; it runs when ROIs, the
        selection filter, the colour or the dataset change, never per cube step."""
        if self._roi_row.isHidden():
            return
        strip = self._roi_strip
        strip.set_color(roi_color())  # no colour control for the row (maintainer); follows the theme
        cubes = self._dataset.spectral_cubes()
        strip.setEnabled(bool(cubes))
        strip.set_axis_count(len(cubes))
        if not cubes:
            strip.set_marks(())
            strip.set_cursor(None)
            return
        toolbox = self._roi_toolbox
        existing = {roi.area_roi_id for roi in toolbox.rois()}
        only = (self._selection.selected_roi_ids() & existing) if self._roi_row_selected_only else None
        noun = "selected ROI" if only is not None else "ROI"
        marks: list[StripMark] = []
        if existing:
            count = len(existing) if only is None else len(only)
            marks.append(
                StripMark(0, SCOPE_PERSISTENT, f"{self._cube_phrase(cubes[0])} - start layout (persistent): {_count_phrase(count, noun)}")
            )
        position = {cube: index for index, cube in enumerate(cubes)}
        for cube, point in toolbox.layout_points(only).items():
            index = position.get(cube)
            if index is None or (index == 0 and point.kind == SCOPE_PERSISTENT):
                continue  # no such cube in this dataset; or the start layout, already marked
            how = "persistent, holds from here on" if point.kind == SCOPE_PERSISTENT else "individual, this cube only"
            marks.append(StripMark(index, point.kind, f"{self._cube_phrase(cube)} - {how}: {_count_phrase(len(point.roi_ids), noun)} changed"))
        strip.set_marks(marks)
        strip.set_cursor(position.get(self._current_cube()))

    def _update_wavelength_role_ticks(self) -> None:
        """The wavelength slider's dark and ignored ticks. The dark is a wavelength,
        not a cube, so it is marked only where the viewed cube has that wavelength,
        and the cube slider has no dark tick. An ignored wavelength is ignored in
        every cube, so its ticks are red on this slider whichever cube is viewed."""
        roles = self._frame_roles
        dark = roles.dark_wavelength() if roles is not None else None
        ignored = frozenset(float(wl) for wl in roles.ignored_wavelengths()) if roles is not None else frozenset()
        dark_index = None
        ignored_indices: frozenset[int] = frozenset()
        if (dark is not None or ignored) and self._dataset.spectral_cubes():
            wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
            if dark is not None:
                dark_index = next((i for i, wl in enumerate(wavelengths) if abs(float(wl) - float(dark)) < 1e-6), None)
            ignored_indices = frozenset(i for i, wl in enumerate(wavelengths) if float(wl) in ignored)
        colors = self._slider_colors
        self._wavelength_slider.set_dark_tick(dark_index, colors.color("dark") if dark_index is not None else None)
        self._wavelength_slider.set_ignored_ticks(ignored_indices, colors.color("ignored"))

    def _update_ignored_cube_ticks(self) -> None:
        """The cube slider's ticks of the ignored cubes (ignoring a cube drops every
        frame of that time point)."""
        roles = self._frame_roles
        cubes = self._dataset.spectral_cubes()
        ignored = roles.ignored_cubes() if roles is not None else ()
        indices = frozenset(cubes.index(cube) for cube in ignored if cube in cubes)
        self._cube_slider.set_ignored_ticks(indices, self._slider_colors.color("ignored"))

    def _on_slider_color_changed(self, role: str, color: str | None) -> None:
        """A swatch in the ribbon was picked (or reset): keep it, show it on the swatch and the sliders."""
        self._slider_colors.set(role, color)
        self._slider_ribbon.slider_colors.refresh()
        self._update_all_slider_marks()
        self._refresh_frame_status()  # the dock subtitle's Dark / Reference / Ignored word has the same colour

    def _restore_slider_colors(self, store: UiStateStore) -> None:
        """The slider colours back as last left, and keep saving them."""
        for role, key in SLIDER_COLOR_KEYS.items():
            saved = read(store, key)
            self._slider_colors.set(role, saved if isinstance(saved, str) else None)
        self._slider_ribbon.slider_colors.refresh()
        self._update_all_slider_marks()
        self._refresh_frame_status()
        self._slider_ribbon.slider_colors.color_changed.connect(
            lambda role, color: store.set(SLIDER_COLOR_KEYS[role].key, color)
        )

    def _update_reference_highlight(self) -> None:
        """Highlights the slider handle (in the reference colour) whenever the
        currently-displayed cube/wavelength is the
        reference - ported from the stable app's
        `_update_reference_navigation_styles` (the stable app also recolored
        the spin boxes next to the sliders; this panel has none since
        2026-10-18, and the slider handle color carries the information)."""
        reference = self._resolve_reference_frame()
        if reference is None:
            self._cube_slider.set_reference_highlight(None)
            self._wavelength_slider.set_reference_highlight(None)
            self._cube_slider.set_reference_tick(None)
            self._wavelength_slider.set_reference_tick(None)
            return
        reference_cube, reference_wavelength = reference
        # Mark the reference's own tick on each rail (not just the handle
        # when it happens to sit on it). The wavelength tick only exists if
        # the *current* cube has that wavelength.
        cubes = self._dataset.spectral_cubes()
        cube_tick = cubes.index(int(reference_cube)) if int(reference_cube) in cubes else None
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        wavelength_tick = next(
            (i for i, wl in enumerate(wavelengths) if abs(float(wl) - float(reference_wavelength)) < 1e-6), None
        )
        color = self._slider_colors.color("reference")
        self._cube_slider.set_reference_tick(cube_tick, color)
        self._wavelength_slider.set_reference_tick(wavelength_tick, color)
        cube_is_reference = int(self._current_cube()) == int(reference_cube)
        wavelength_is_reference = abs(float(self._current_wavelength()) - float(reference_wavelength)) < 1e-6
        self._cube_slider.set_reference_highlight(color if cube_is_reference else None)
        self._wavelength_slider.set_reference_highlight(color if wavelength_is_reference else None)

    # -- slider tick labels ---------------------------------------------------

    @staticmethod
    def _wavelength_slider_major_ticks(values: tuple[float, ...], view: tuple[float, float] | None = None) -> dict[int, str]:
        """See `slider_ticks.wavelength_slider_major_ticks`; `view`: the stretch on screen when the axis is zoomed."""
        return wavelength_slider_major_ticks(values, view)

    def _cube_slider_major_ticks(self, values: tuple[int, ...]) -> dict[int, str]:
        """See `slider_ticks.cube_slider_major_ticks`; labels follow the cube display unit and the stretch on screen."""
        return cube_slider_major_ticks(values, self._cube_display.label_for, self._cube_slider.axis_window().view_for(len(values)))

    def _refresh_cube_slider_labels(self) -> None:
        """The Cube axis was zoomed or panned: label the ticks that are on screen now."""
        cubes = self._dataset.spectral_cubes()
        if cubes and len(cubes) == self._cube_slider.axis_window().count():
            self._cube_slider.set_ticks(list(cubes), self._cube_slider_major_ticks(cubes))

    def _refresh_wavelength_slider_labels(self) -> None:
        """The wavelength axis was zoomed or panned: label the ticks that are on screen now."""
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        window = self._wavelength_slider.axis_window()
        if wavelengths and len(wavelengths) == window.count():
            self._wavelength_slider.set_ticks(
                list(wavelengths), self._wavelength_slider_major_ticks(wavelengths, window.view_for(len(wavelengths)))
            )

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
        self._update_mask_overlay(authored_mask, geometry, warp_affine, hidden=preview_active)
        self._playback_stats.note_detail("mask", time.perf_counter() - mask_started)

    def _play_config_fields(self) -> dict[str, float | int | bool]:
        """What is switched on right now, as flat log fields (``c_*``), so a log of a Play run says which
        overlays each second of it was drawn with (testing9: the stats alone could not tell). Read once per
        second with the Play stats window; cheap."""
        settings = self._slideshow.settings()
        mask_state = self._mask_overlay_state
        return {
            "c_target_fps": round(1.0 / max(self._slideshow.interval_seconds(), 1e-6), 1),
            "c_speed": settings.speed,
            "c_rois": len(self._roi_toolbox.rois_at(self._current_cube())),
            "c_sample": bool(self._roi_overlay.sample.visible),
            "c_reference": bool(self._roi_overlay.reference.visible),
            "c_labels": bool(self._roi_overlay.labels_visible),
            "c_group_labels": bool(self._roi_overlay.group_labels.visible),
            "c_landmarks": bool(self._landmark_overlay_visible),
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
        self.playback_changed.emit(playing)
        stats = self._playback_stats
        self._redraw_timer.setInterval(_REDRAW_COALESCE_PLAYING_MS if playing else _REDRAW_COALESCE_MS)
        if playing:
            stats.start(time.perf_counter(), self._slideshow.interval_seconds())
            self._view.paint_listener = lambda s: stats.note_detail("paint_view", s)
            self._image_item.paint_listener = lambda s: stats.note_detail("paint_image", s)
            return
        self._view.paint_listener = None
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

    def _on_rendered(self, result: RenderResult) -> None:
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
            self._view_tab.display_range_controls.show_frame_levels(frame_levels)
            self._playback_stats.note_detail("setImage", time.perf_counter() - stage)
            stage = time.perf_counter()
            if display is not None:
                self._last_data_range = display.data_range
            else:
                finite = image[np.isfinite(image)]
                self._last_data_range = (float(finite.min()), float(finite.max())) if finite.size else None
            self._playback_stats.note_detail("minmax", time.perf_counter() - stage)
            stage = time.perf_counter()
            checker = no_data_overlay_rgba(image)
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
            self._update_highlight_overlay()
            self._playback_stats.note_detail("highlight", time.perf_counter() - stage)
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
            listeners_started = time.perf_counter()
            self.image_rendered.emit(image, result.request.cube_index, result.request.wavelength_nm)
            listeners_s = time.perf_counter() - listeners_started
            self._playback_stats.note_detail("listeners", listeners_s)
            stage = time.perf_counter()
            self._show_frame_status(result.request.cube_index, result.request.wavelength_nm)
            self._playback_stats.note_detail("status", time.perf_counter() - stage)
        if not showing_estimate:
            stage = time.perf_counter()
            self._request_prefetch(result.request.cube_index, result.request.wavelength_nm)
            self._playback_stats.note_detail("prefetch_plan", time.perf_counter() - stage)
        self._last_image_shape = result.image.shape[:2]
        self._area_tool.set_image_shape(self._last_image_shape)
        # The crop tool's clamp bound - correct the instant Crop is active
        # (the frame it renders is the *uncropped* one, `_PREVIEW_TOOLS`),
        # briefly stale (the smaller, cropped shape) right as the tool is
        # first switched on, self-correcting once the next preview render
        # lands ~100ms later (`_schedule_redraw` already runs on every
        # active-tool change).
        self._crop_tool.set_frame_size(result.image.shape[1], result.image.shape[0])
        stage = time.perf_counter()
        self._update_chunk_grid()
        self._update_source_grid()
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
                log_event(
                    logger, "playback.window", logging.DEBUG,
                    **self._playback_stats.last_window_fields(), **self._play_config_fields(),
                )
                self._on_tool_status(line)
        if serial == self._latest_serial:
            # Only the newest request releases the brake: an older result shown while a newer request is
            # still in flight must not let the slideshow run further ahead. With a pipelined show this
            # call runs the next step right here (last, so this frame's stats are complete).
            self._slideshow.frame_displayed()

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
        self._frame_key = None  # a transient estimate, not the real frame: not re-described on a unit change
        cube_text = f"Cube {self._cube_display.label_for(request.cube_index)}"
        wavelength_text = f"{request.wavelength_nm:.0f} nm"
        self._set_frame_status(
            f"{cube_text}, {wavelength_text} - background estimate{spread}",
            columns=self._frame_fields(
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
            self._draw_crop_outline()
            self._draw_landmarks()
            self._draw_scale_bar()
            self._draw_roi_overlay(only_if_changed=True)
            return
        for name, draw in (
            ("ov_crop", self._draw_crop_outline), ("ov_landmarks", self._draw_landmarks),
            ("ov_scalebar", self._draw_scale_bar), ("ov_rois", lambda: self._draw_roi_overlay(only_if_changed=True)),
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
            self._roi_draw_key = None
            return

        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
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
        epoch = (self._roi_revision, frozenset(self._selection.selected_roi_ids()), self._roi_overlay.appearance_key())
        key = (state, epoch)
        if only_if_changed and key == self._roi_draw_key:
            return
        layers = self._roi_overlay.layers
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
            self._roi_toolbox.display_positions(frame, affine),
            self._selection.selected_roi_ids(),
            affine,
            detection.reference_inner_diameter_px,
            detection.reference_outer_diameter_px,
            self._roi_toolbox.groups(),
        )
        self._roi_overlay.layers.mark_drawn(state)

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
        """Slideshow speed / mode / repeat / end back as last left, and keep saving them."""
        saved = SlideshowSettings(
            speed=read(store, SLIDESHOW_SPEED),
            mode=read(store, SLIDESHOW_MODE),
            repeat=read(store, SLIDESHOW_REPEAT),
            end=read(store, SLIDESHOW_END),
            skip_ignored=read(store, SLIDESHOW_SKIP_IGNORED),
            skip_dark=read(store, SLIDESHOW_SKIP_DARK),
            thin_roi_outline=read(store, SLIDESHOW_THIN_ROI_OUTLINE),
            calm_histogram=read(store, SLIDESHOW_CALM_HISTOGRAM),
        )
        if (
            saved.speed not in SPEEDS
            or saved.mode not in (MODE_ALL, MODE_ONE_WAVELENGTH, MODE_ROI_STATES, MODE_MASK_STATES, MODE_EVENTS)
            or saved.end not in (END_CUBE, END_DATASET)
        ):
            saved = SlideshowSettings()  # a hand-edited settings file
        self._slider_ribbon.set_settings(saved)
        self._slideshow.set_settings(saved)

        def save(settings: SlideshowSettings) -> None:
            store.set(SLIDESHOW_SPEED.key, settings.speed)
            store.set(SLIDESHOW_MODE.key, settings.mode)
            store.set(SLIDESHOW_REPEAT.key, settings.repeat)
            store.set(SLIDESHOW_END.key, settings.end)
            store.set(SLIDESHOW_SKIP_IGNORED.key, settings.skip_ignored)
            store.set(SLIDESHOW_SKIP_DARK.key, settings.skip_dark)
            store.set(SLIDESHOW_THIN_ROI_OUTLINE.key, settings.thin_roi_outline)
            store.set(SLIDESHOW_CALM_HISTOGRAM.key, settings.calm_histogram)

        self._slider_ribbon.settings_changed.connect(save)

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
        shown = self._landmark_overlay_visible and self._active_tool.active() not in _PREVIEW_TOOLS
        # The all-wavelengths view does not depend on the viewed frame at all,
        # so slider moves leave it alone; the current-wavelength view depends
        # on the frame. Anything else that changes the picture bumps
        # `_landmark_version` (chromatic model, dataset).
        frame = None if self._landmark_all_wavelengths else (self._current_cube(), self._current_wavelength())
        key = (shown, self._landmark_all_wavelengths, frame, self._landmark_version)
        if key == self._landmark_drawn_key:
            return
        self._landmark_drawn_key = key
        self._landmark_observed_item.clear()
        self._landmark_fitted_item.clear()
        self._landmark_line_item.clear()
        if not shown:
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
            self._mask_painted = None
            return
        mask = resolve_external_mask(authored_mask, geometry, warp_affine)
        if mask is None or not np.any(mask):
            self._mask_tint.item.hide()
            self._mask_painted = None
            return
        # `resolve_external_mask` hands back the same object for the same
        # inputs, so a wavelength change that resolves to the same mask does not
        # rebuild and re-upload the tint image.
        painted = self._mask_painted
        style = (self._mask_tint.color.rgba(), self._mask_tint.alpha)
        if painted is not None and painted[0] is mask and (painted[1], painted[2]) == style:
            self._mask_tint.item.show()
            return
        self._mask_tint.paint(mask)
        self._mask_painted = (mask, style[0], style[1])

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
        data_range = self._last_data_range  # the displayed frame's own finite min / max, computed once in `_on_rendered`
        if data_range is not None and (upper < data_range[0] or lower > data_range[1] or (lower <= data_range[0] and upper >= data_range[1])):
            # Nothing in range, or every finite pixel in range: either way no tint is drawn (below). Decided from
            # the min / max alone: building the full-image mask for it cost ~6-10 ms of every Play frame.
            self._highlight_tint.item.hide()
            return
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
        if not np.any(selection_mask):
            self._highlight_tint.item.hide()
            return
        # "Every finite pixel is in range" was already decided from the frame's min / max above whenever those are
        # known (all finite pixels lie between them), so the full-image `selection_mask[np.isfinite(image)]` test
        # (a ~1 MP copy, 2-3 ms per Play frame) only runs for a frame without a known range.
        if data_range is None and bool(np.all(selection_mask[np.isfinite(image)])):
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

    def _update_chunk_grid(self) -> None:
        """Draw (or clear) the Export section's chunk-grid preview - lines
        at every `chunk_size_px` boundary over the last rendered plane, so
        the maintainer can see how the chosen chunk size will actually
        divide up the image before exporting. Reads `DatasetModule` only
        (see its `chunk_grid_preview` docstring) - never touches the Export
        section's widgets.

        The grid lives in raw pixels (the export writes raw pixels, 2026-10-20): its lines run from the grid offset
        across the raw frame and are mapped onto the displayed image, so with a rotation they appear tilted and with
        a crop shifted - exactly where the exported chunks will fall."""
        preview = self._dataset.chunk_grid_preview()
        if preview.processed:
            # The export applies rotation/flip/crop (2026-10-20): the grid lies on the processed image itself, drawn
            # straight. Hidden while a preview tool shows the uncropped canvas (another coordinate space, as the ROIs).
            hidden = self._active_tool.active() in _PREVIEW_TOOLS
            raw_shape = None if hidden else self._last_image_shape
        else:
            raw_shape = self._dataset.raw_plane_shape() if preview.enabled else None
        if not preview.enabled or raw_shape is None or preview.chunk_w <= 0 or preview.chunk_h <= 0:
            self._chunk_grid_curve.clear()
            # Nothing else re-renders on a toggle, so without this the removed lines stay painted until the mouse
            # moves over the image.
            self._view.viewport().update()
            return
        to_display = (
            np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            if preview.processed
            else raw_to_processed_affine_xy((int(raw_shape[0]), int(raw_shape[1])), self._geometry.settings())
        )
        xs, ys = self._grid_polyline(raw_shape, to_display, preview.chunk_w, preview.chunk_h, preview.offset_x, preview.offset_y)
        self._chunk_grid_curve.setData(xs, ys)
        self._view.viewport().update()

    def _update_source_grid(self) -> None:
        """Draw (or clear) the loaded OME-Zarr's own chunk grid (the View tab's blue toggle): lines at every stored chunk
        boundary from pixel (0, 0), in raw pixels and mapped onto the displayed image like the export preview's raw
        branch (tilted under a rotation, shifted by a crop). A TIFF stack has no chunks, so nothing is drawn."""
        module = self._dataset
        chunk = module.source_chunk_shape() if module.source_chunk_grid_visible() else None
        raw_shape = module.raw_plane_shape() if chunk is not None else None
        if chunk is None or raw_shape is None or chunk[0] <= 0 or chunk[1] <= 0:
            self._source_grid_curve.clear()
            self._view.viewport().update()  # nothing else re-renders on a toggle (see `_update_chunk_grid`)
            return
        to_display = raw_to_processed_affine_xy((int(raw_shape[0]), int(raw_shape[1])), self._geometry.settings())
        xs, ys = self._grid_polyline(raw_shape, to_display, chunk[1], chunk[0], 0, 0)
        self._source_grid_curve.setData(xs, ys)
        self._view.viewport().update()

    @staticmethod
    def _grid_polyline(
        raw_shape: tuple[int, int], to_display: np.ndarray, chunk_w: int, chunk_h: int, offset_x: int, offset_y: int
    ) -> tuple[list[float], list[float]]:
        """Grid lines at ``offset + k * chunk`` across the raw frame, mapped to the displayed image by ``to_display``."""
        raw_h, raw_w = float(raw_shape[0]), float(raw_shape[1])
        xs: list[float] = []
        ys: list[float] = []

        def segment(x0: float, y0: float, x1: float, y1: float) -> None:
            pts = to_display @ np.array([[x0, x1], [y0, y1], [1.0, 1.0]])
            _append_polyline(xs, ys, pts[0], pts[1])

        for x in np.arange(offset_x % chunk_w, raw_w + 0.5, chunk_w, dtype=np.float64):
            segment(x, 0.0, x, raw_h)
        for y in np.arange(offset_y % chunk_h, raw_h + 0.5, chunk_h, dtype=np.float64):
            segment(0.0, y, raw_w, y)
        return xs, ys

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

    def _set_frame_status(
        self, text: str, tooltip: str = "", columns: tuple[SubtitleColumn, ...] | None = None
    ) -> None:
        """Publish the frame status as one line (`frame_status_changed`) and as the dock's fixed-place
        fields (`frame_columns_changed`). A message with no frame in it ("No dataset loaded.") has no
        *columns*: it is shown in the comment field, the one that has room."""
        self._frame_status = text
        self._frame_columns = columns if columns is not None else self._frame_fields(comment=text, tooltip=tooltip)
        self.frame_status_changed.emit(text, tooltip)
        self.frame_columns_changed.emit(self._frame_columns)

    def frame_columns(self) -> tuple[SubtitleColumn, ...]:
        """The dock fields of the last frame status (to seed a dock that connected after the panel was built)."""
        return self._frame_columns

    def _frame_fields(
        self,
        *,
        role: tuple[str, str] | None = None,
        cube: str = "",
        wavelength: str = "",
        time: str = "",
        comment: str = "",
        tooltip: str = "",
    ) -> tuple[SubtitleColumn, ...]:
        """The dock fields, left to right: cube, wavelength, the frame's own time, comment, frame role
        (word, colour). The role word is empty on most frames and has a fixed width (the widest role word), so it
        comes last, at the right end: anywhere before the comment it would be a blank gap (2026-10-24). Every field but the comment has the width of the widest thing it can
        show, so an empty one keeps its place and the ones after it never move."""
        widest_cube, widest_wavelength = self._widest_frame_texts()
        return (
            SubtitleColumn(cube, widest_cube),
            SubtitleColumn(wavelength, widest_wavelength),
            SubtitleColumn(time, _WIDEST_FRAME_TIME),
            SubtitleColumn(comment, tooltip=tooltip),
            SubtitleColumn(role[0] if role else "", _WIDEST_ROLE_WORD, role[1] if role else None),
        )

    def _widest_frame_texts(self) -> tuple[str, str]:
        """The longest "Cube X" and "NNN nm" of the loaded dataset (the highest cube and wavelength: the
        most digits; the last cube has the longest start time). Looked up once per dataset / cube unit,
        not per frame, since the status is rebuilt on every Play step."""
        if self._frame_widest is None:
            cubes = self._dataset.spectral_cubes()
            wavelengths = self._dataset.wavelengths()
            self._frame_widest = (
                f"Cube {self._cube_display.label_for(cubes[-1])}" if cubes else "Cube 0",
                f"{max(wavelengths):.0f} nm" if wavelengths else "0 nm",
            )
        return self._frame_widest

    def _show_frame_status(self, cube_index: int, wavelength_nm: float) -> None:
        """The dock subtitle for a real frame: "Cube X, 550 nm | 1:23.45 | <comment>".
        The middle part is this frame's own experimental time (H:MM:SS.ss, or M:SS.ss
        under an hour, since the acquisition start - the same t = 0 as the
        cube's Time label, which is only the cube's *first* frame); it is left
        out when the frame has no timing. The comment is the experiment-record
        entry active when the frame was taken. "N/A" (with the reason in the
        tooltip) when there is none: either the frame is not linked to
        experiment data (no acquisition metadata or no timing for it), or no
        comment was recorded yet."""
        self._frame_key = (int(cube_index), float(wavelength_nm))
        linked, comment = self._dataset.frame_comment(cube_index, wavelength_nm)
        cube_text = f"Cube {self._cube_display.label_for(cube_index)}"
        wavelength_text = f"{wavelength_nm:.0f} nm"
        head = f"{cube_text}, {wavelength_text}"
        frame_seconds = self._dataset.frame_start_seconds(cube_index, wavelength_nm)
        time_text = "" if frame_seconds is None else format_elapsed_seconds(frame_seconds, decimals=2)  # tens of ms: frames of a cube are close
        if time_text:
            head = f"{head} | {time_text}"
        if comment:
            text, tooltip = f"{head} | {comment}", comment
        elif linked:
            text, tooltip = f"{head} | N/A", "No comment was recorded at the time this image was acquired."
        else:
            text, tooltip = (
                f"{head} | N/A",
                "This image is not linked to experiment data (no acquisition metadata, or no timing for it).",
            )
        role = self._role_word(cube_index, wavelength_nm)
        self._set_frame_status(
            self._with_role_prefix(text, cube_index, wavelength_nm),
            tooltip,
            self._frame_fields(
                role=role, cube=cube_text, wavelength=wavelength_text, time=time_text,
                comment=comment or "N/A", tooltip=tooltip,
            ),
        )

    def _role_word(self, cube_index: int, wavelength_nm: float) -> tuple[str, str] | None:
        """("Dark background" | "Reference" | "Ignored", its slider colour) for such a frame
        (`slider_colors.py`: the colour of its tick); None for any other frame."""
        if self._frame_roles is None:
            return None
        shown = _ROLE_WORDS.get(self._frame_roles.role_of(cube_index, wavelength_nm))
        if shown is None:
            return None
        color_role, word = shown
        return word, self._slider_colors.color(color_role)

    def _with_role_prefix(self, text: str, cube_index: int, wavelength_nm: float) -> str:
        """The one-line status: the role word, in its colour, before the Cube/wavelength text; other
        frames: the text unchanged (plain). A rich-text line, so the whole text is escaped."""
        role = self._role_word(cube_index, wavelength_nm)
        if role is None:
            return text
        return f'<span style="color:{role[1]}">{role[0]}</span> {html.escape(text)}'

    def _refresh_frame_status(self) -> None:
        """Show the viewed frame's dock subtitle again (a role or a slider colour changed)."""
        if self._frame_key is not None:
            self._show_frame_status(*self._frame_key)

    def _on_reference_frame_changed(self) -> None:
        self._update_slider_marks()
        self._refresh_frame_tools()  # Go to reference is enabled only while a reference exists
        self._refresh_frame_status()

    def _on_frame_roles_changed(self) -> None:
        self._update_all_slider_marks()
        self._refresh_frame_tools()
        self._refresh_frame_status()

    def _on_cube_display_changed(self, _mode: str) -> None:
        """The cube unit (number / start time) changed anywhere in the app."""
        self._frame_widest = None  # "Cube 314" and "Cube 1:23:45" are not equally wide
        self._cube_title.setText(self._cube_display.axis_title())
        cubes = self._dataset.spectral_cubes()
        self._cube_slider.set_ticks(list(cubes), self._cube_slider_major_ticks(cubes))
        self._update_roi_marks()  # the mark tooltips name cubes in the new unit
        self._refresh_frame_tools()
        if self._frame_key is not None:
            self._show_frame_status(*self._frame_key)

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
        return _TAB_INFO.get(category) or controls_text(tool)

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming
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
        self._interaction.on_scene_clicked(event)

    def _on_scene_moved(self, scene_pos: object) -> None:
        self._interaction.on_scene_moved(scene_pos)

    def _on_left_drag_event(self, ev: object) -> bool:
        return self._interaction.on_left_drag(ev)


    def _on_crop_drag_event(self, ev: object) -> bool:
        return self._interaction.on_crop_drag(ev)

    def _on_measure_drag_event(self, ev: object) -> bool:
        return self._interaction.on_measure_drag(ev)

    def _in_selection(self, x: float, y: float) -> bool:
        """Whether display point (x, y) may be edited under the current area
        selection (always True with none)."""
        return self._interaction.in_selection(x, y)

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
        rois = self._roi_toolbox.rois_at(frame[0])
        if not rois:
            return None
        centers = self._roi_toolbox.display_positions(frame, affine)
        distances = np.hypot(x - centers[:, 0], y - centers[:, 1])
        radii = np.fromiter((roi.sample_diameter_px for roi in rois), dtype=np.float64, count=len(rois)) / 2.0
        distances = np.where(distances <= radii, distances, np.inf)
        nearest = int(np.argmin(distances))  # first of equal distances wins, as before
        return rois[nearest].area_roi_id if np.isfinite(distances[nearest]) else None

    def rois_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> set[int]:
        """Ids of the ROIs whose display-space *centre* lies in the rectangle
        (the rubber-band select). Public for the same reason as `roi_at`."""
        rois = self._roi_toolbox.rois()
        if not rois:
            return set()
        frame = (self._current_cube(), self._current_wavelength())
        centers = self._roi_toolbox.display_positions(frame, self._chromatic.affine_for(frame))
        inside = (centers[:, 0] >= x0) & (centers[:, 0] <= x1) & (centers[:, 1] >= y0) & (centers[:, 1] <= y1)
        return {roi.area_roi_id for roi, hit in zip(rois, inside, strict=True) if hit}

    def _selected_apertures(self) -> SelectedApertures | None:
        """The selected ROIs' display centres and diameters, for the border/inside hit test; `None` with no selection."""
        selected = self._selection.selected_roi_ids()
        rois = self._roi_toolbox.rois_at(self._current_cube())
        if not selected or not rois:
            return None
        keep = np.fromiter((roi.area_roi_id in selected for roi in rois), dtype=bool, count=len(rois))
        if not keep.any():
            return None
        frame = (self._current_cube(), self._current_wavelength())
        centers = self._roi_toolbox.display_positions(frame, self._chromatic.affine_for(frame))[keep]
        chosen = [roi for roi, hit in zip(rois, keep, strict=True) if hit]
        detection = self._roi_toolbox.detection_settings()
        rings = np.array(
            [
                effective_reference_diameters(roi, detection.reference_inner_diameter_px, detection.reference_outer_diameter_px)
                for roi in chosen
            ],
            dtype=np.float64,
        ).reshape(-1, 2)
        resizable = np.array([roi.sample_geometry_type != "mask" for roi in chosen], dtype=bool)
        return SelectedApertures(
            ids=np.array([roi.area_roi_id for roi in chosen], dtype=np.int64),
            centers=centers,
            diameters=np.array([roi.sample_diameter_px for roi in chosen], dtype=np.float64),
            resizable=resizable,
            ring_inner=rings[:, 0],
            ring_outer=rings[:, 1],
            ring_resizable=resizable & bool(self._roi_overlay.reference.visible) & (rings[:, 1] > rings[:, 0]),
        )

    def _current_affine(self) -> np.ndarray:
        frame = (self._current_cube(), self._current_wavelength())
        return np.asarray(self._chromatic.affine_for(frame), dtype=np.float64)

    def _display_linear(self) -> np.ndarray:
        """The linear (2x2) part of the shown frame's affine: how a drag in display space maps to the stored ROI centres."""
        return self._current_affine()[:2, :2]

    def _display_to_reference(self, x: float, y: float) -> tuple[float, float]:
        """Display-space point -> reference-frame point (what `RoiToolbox` stores), by inverting the affine."""
        matrix = self._current_affine()
        linear, shift = matrix[:2, :2], matrix[:2, 2]
        try:
            reference = np.linalg.solve(linear, np.array([x, y]) - shift)
        except np.linalg.LinAlgError:
            return x, y
        return float(reference[0]), float(reference[1])

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


def _append_polyline(xs: list[float], ys: list[float], new_x: np.ndarray, new_y: np.ndarray) -> None:
    """Append one closed curve, separated from whatever came before by a
    NaN. ``PlotDataItem(connect="finite")`` breaks the line at non-finite
    points, which is what lets a single item draw many disjoint circles."""
    if xs:
        xs.append(float("nan"))
        ys.append(float("nan"))
    xs.extend(new_x.tolist())
    ys.extend(new_y.tolist())
