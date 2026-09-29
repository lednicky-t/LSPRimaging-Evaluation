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

- Mask painting/preview overlays, the intensity-highlight overlay, and the
  histogram-driven highlight (`MaskModule`'s async candidate machinery
  isn't built either - see its module docstring).
- The cursor readout, scale bar, and ruler overlay - ``GeometryModule``
  already owns the calibration state they would draw from.
- ROI creation by click and ROI resize by handle. ``add_roi``/``resize_roi``
  are real commands; this panel currently only moves and selects, which is
  what makes the overlay worth looking at in the first place.
  (**Rotation is built** - 2026-09-28, ``rotate_line_tool.py``. **Crop is
  built** - 2026-09-29, ``crop_tool.py`` - deliberately *not* a port of the
  old app's ``gui/image_tools_controller.py``/``pg.RectROI``: no separate
  handle widgets, the rectangle's own border is the grab zone.)

**Active tool** (2026-09-28): which canvas tool is on comes from the shared
``ActiveToolModule`` - the panel never decides it. While a *preview* tool
(rotate or crop) is active, the panel (a) renders the image **uncropped**,
with the existing crop drawn as a fixed outline, so the user sees what is in
and out of the crop while aligning - the crop stays put in pixel terms and is
re-applied when the tool is switched off; (b) hides the ROI overlay, because
ROI positions live in *processed* (cropped) space and would sit at the wrong
place over an uncropped image (CLAUDE.md: mixing the spaces silently gives
wrong results); and (c) routes left/right clicks and keys to the tool - Crop
additionally claims left-button *drags* (``ImageViewBox.set_left_drag_
handler``, ``image_controls.py``), the one tool so far that needs one. With
no tool active, a left click selects ROIs as before. Mouse/keyboard rules:
``image_controls.py``.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import replace

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...image_tools import ActiveToolModule, BackgroundModule, ChromaticModule, GeometryModule, ImageTool, MaskModule
from ...image_tools.geometry.model import CropDefinition
from ...roi import RoiToolbox
from ...roi.model import AreaRoi
from ...roi.rasterize import effective_reference_radii, transformed_circle_points
from ...selection import SelectionModule
from .context_menu import show_tool_context_menu
from .crop_size_controls import CropSizeControls
from .crop_tool import CropTool
from .image_controls import ImageViewBox, controls_text
from .render import ImageRenderer, RenderRequest, RenderResult
from .rotate_line_tool import RotateLineTool

logger = logging.getLogger(__name__)

_REDRAW_COALESCE_MS = 100  # sketch §8 - the already-validated coalescing window
_CIRCLE_POINTS = 48
"""Vertices per drawn circle. 48 is smooth at any zoom a screen can show
while keeping the whole overlay to a few thousand points for a few hundred
ROIs - the overlay is redrawn on every selection change, so its cost is
paid far more often than the image's."""

_DEFAULT_SAMPLE_COLOR = "#f59e0b"
_DEFAULT_REFERENCE_COLOR = "#38bdf8"
_SELECTED_COLOR = "#f8fafc"
_CHUNK_GRID_COLOR = "#a3a3a3"
_CROP_OUTLINE_COLOR = "#38bdf8"  # the crop button's active blue

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
    computation and no ROI/group state (AGENTS.md)."""

    # A tool's *live* status while a gesture is in progress (angle readout
    # etc.) - relayed to the app's status bar (app_rewrite.py), the same
    # place every other panel's transient status already goes. The tool's
    # *static* "what do the buttons do" text is not a signal at all; it
    # lives on the permanent info icon's tooltip instead (see
    # `_refresh_tool_info`), because it only needs to be re-read, not pushed.
    tool_status_changed = pyqtSignal(str)

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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dataset = dataset
        self._geometry = geometry
        self._mask = mask
        self._chromatic = chromatic
        self._background = background
        self._roi_toolbox = roi_toolbox
        self._selection = selection
        self._active_tool = active_tool
        self._tool_status = ""

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

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(_REDRAW_COALESCE_MS)
        self._redraw_timer.timeout.connect(self._redraw)

        self._connect_modules()
        self._refresh_navigation_ranges()

    # -- construction -------------------------------------------------------

    def _build_ui(self) -> None:
        # Parent passed at construction, never set later via a layout that is
        # not itself attached yet - see CLAUDE.md's phantom-top-level-window
        # pitfall, which cost ~6 rounds of screen-recording analysis to find
        # the last time it was hit.
        self._view = pg.GraphicsLayoutWidget(parent=self)
        self.refresh_theme()
        # ImageViewBox: middle-drag pans, wheel zooms, left/right drags do
        # nothing (image_controls.py).
        self._plot = self._view.addPlot(viewBox=ImageViewBox())
        self._plot.invertY(True)  # image row 0 at the top, like the old app
        self._plot.setAspectLocked(True)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.setMenuEnabled(False)

        self._image_item = pg.ImageItem(axisOrder="row-major")
        self._plot.addItem(self._image_item)

        # Three curve items for the whole overlay rather than per-ROI items:
        # with NaN separators between ROIs, one PlotDataItem draws any number
        # of disjoint circles, so adding an ROI costs array append, not a new
        # QGraphicsItem. Matters because the overlay is rebuilt on every
        # selection change.
        self._sample_curve = self._add_curve(_DEFAULT_SAMPLE_COLOR, width=1.5)
        self._reference_curve = self._add_curve(_DEFAULT_REFERENCE_COLOR, width=1.0)
        self._selection_curve = self._add_curve(_SELECTED_COLOR, width=2.5)
        self._chunk_grid_curve = self._add_curve(_CHUNK_GRID_COLOR, width=1.0, dashed=True)
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
        self._plot.vb.set_left_drag_handler(self._on_crop_drag_event)
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

        self._status = QLabel("No dataset loaded.", self)
        self._status.setWordWrap(True)

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
        self._tool_info = QLabel(self)
        self._tool_info.setFixedSize(18, 18)
        self._refresh_tool_info(None)

        self._cube_spin = QSpinBox(self)
        self._cube_spin.setPrefix("Cube ")
        self._cube_spin.setEnabled(False)
        self._cube_spin.valueChanged.connect(self._on_cube_spin_changed)

        self._wavelength_spin = QDoubleSpinBox(self)
        self._wavelength_spin.setSuffix(" nm")
        self._wavelength_spin.setDecimals(1)
        self._wavelength_spin.setSingleStep(1.0)
        self._wavelength_spin.setEnabled(False)
        self._wavelength_spin.valueChanged.connect(self._on_wavelength_spin_changed)

        controls = QHBoxLayout()
        controls.addWidget(self._cube_spin)
        controls.addWidget(self._wavelength_spin)
        controls.addStretch(1)
        controls.addWidget(self._status, 2)
        controls.addSpacing(6)
        controls.addWidget(self._tool_info)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._view, 1)

        scene = self._image_item.scene()
        scene.sigMouseClicked.connect(self._on_scene_clicked)
        scene.sigMouseMoved.connect(self._on_scene_moved)
        # Arrow keys/Esc for the active tool. An event filter on the view
        # (not `keyPressEvent` here) because the graphics view would
        # otherwise consume arrow keys itself to scroll.
        self._view.installEventFilter(self)

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

        # ROI/selection changes only move the overlay, never the pixels - but
        # they still go through the same path. Splitting "redraw overlay only"
        # out is a real optimization once there is a dataset big enough to
        # measure it against; guessing at it now would be the premature kind
        # AGENTS.md's performance rules warn about.
        self._roi_toolbox.geometry_changed.connect(self._schedule_redraw)
        self._roi_toolbox.cosmetic_changed.connect(self._schedule_redraw)
        self._selection.cube_changed.connect(self._schedule_redraw)
        self._selection.wavelength_changed.connect(self._schedule_redraw)
        self._selection.roi_selection_changed.connect(self._schedule_redraw)

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
        self._sample_curve.clear()
        self._reference_curve.clear()
        self._selection_curve.clear()
        self._chunk_grid_curve.clear()
        self._crop_outline_curve.clear()
        self._last_image_shape = None
        self._refresh_navigation_ranges()
        self._status.setText("No dataset loaded.")
        # A tool with no image under it has nothing to do; the Workflow
        # panel's buttons follow this signal.
        self._active_tool.clear()

    def _refresh_navigation_ranges(self) -> None:
        """Point the cube/wavelength controls at what the dataset actually
        has. Both are blocked while being reprogrammed so that re-ranging
        them doesn't emit a spurious "the user changed the wavelength"."""
        cubes = self._dataset.spectral_cubes()
        with _blocked(self._cube_spin):
            self._cube_spin.setEnabled(bool(cubes))
            self._cube_spin.setRange(min(cubes) if cubes else 0, max(cubes) if cubes else 0)
            if cubes:
                self._cube_spin.setValue(self._current_cube())
        self._refresh_wavelength_range()

    def _refresh_wavelength_range(self) -> None:
        wavelengths = self._dataset.wavelengths_for_cube(self._current_cube())
        with _blocked(self._wavelength_spin):
            self._wavelength_spin.setEnabled(bool(wavelengths))
            if wavelengths:
                self._wavelength_spin.setRange(min(wavelengths), max(wavelengths))
                self._wavelength_spin.setValue(self._current_wavelength(wavelengths))
            else:
                self._wavelength_spin.setRange(0.0, 0.0)

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

    def _on_cube_spin_changed(self, value: int) -> None:
        self._selection.set_cube(int(value))
        self._refresh_wavelength_range()

    def _on_wavelength_spin_changed(self, value: float) -> None:
        self._selection.set_wavelength(float(value))

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
        if self._active_tool.active() in _PREVIEW_TOOLS and geometry.crop.enabled:
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
            )
        )

    def _on_rendered(self, result: RenderResult) -> None:
        if result.request.serial != self._latest_serial:
            # Superseded while in flight - dropping it is the point (see
            # render.py's "latest request wins"), not an error.
            return
        if result.error is not None or result.image is None:
            self._status.setText(f"Cannot show this frame: {result.error}")
            self._image_item.clear()
            return
        self._image_item.setImage(np.asarray(result.image, dtype=np.float32), autoLevels=True)
        self._status.setText(
            f"Cube {result.request.cube_index}, {result.request.wavelength_nm:g} nm "
            f"- {result.image.shape[1]}x{result.image.shape[0]} px"
        )
        self._last_image_shape = result.image.shape[:2]
        # The crop tool's clamp bound - correct the instant Crop is active
        # (the frame it renders is the *uncropped* one, `_PREVIEW_TOOLS`),
        # briefly stale (the smaller, cropped shape) right as the tool is
        # first switched on, self-correcting once the next preview render
        # lands ~100ms later (`_schedule_redraw` already runs on every
        # active-tool change).
        self._crop_tool.set_frame_size(result.image.shape[1], result.image.shape[0])
        self._update_chunk_grid()

    # -- overlays -----------------------------------------------------------

    def _draw_overlays(self) -> None:
        self._draw_crop_outline()
        if self._active_tool.active() in _PREVIEW_TOOLS:
            # ROI positions are in cropped/processed space; over the
            # uncropped preview they would be drawn in the wrong place.
            self._sample_curve.clear()
            self._reference_curve.clear()
            self._selection_curve.clear()
            return

        rois = self._roi_toolbox.rois()
        if not rois:
            self._sample_curve.clear()
            self._reference_curve.clear()
            self._selection_curve.clear()
            return

        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        detection = self._roi_toolbox.detection_settings()
        selected = self._selection.selected_roi_ids()
        theta = np.linspace(0.0, 2.0 * np.pi, _CIRCLE_POINTS, endpoint=True)

        sample_x, sample_y = [], []
        reference_x, reference_y = [], []
        selection_x, selection_y = [], []

        for roi in rois:
            center = self._roi_toolbox.display_position(roi.area_roi_id, frame, affine)
            xs, ys = transformed_circle_points(center, self._sample_radius(roi), affine, theta)
            target = (selection_x, selection_y) if roi.area_roi_id in selected else (sample_x, sample_y)
            _append_polyline(target[0], target[1], xs, ys)

            inner, outer = effective_reference_radii(
                roi, detection.reference_inner_radius_px, detection.reference_outer_radius_px
            )
            for radius in (inner, outer):
                rx, ry = transformed_circle_points(center, radius, affine, theta)
                _append_polyline(reference_x, reference_y, rx, ry)

        self._sample_curve.setData(sample_x, sample_y)
        self._reference_curve.setData(reference_x, reference_y)
        self._selection_curve.setData(selection_x, selection_y)

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

    @staticmethod
    def _sample_radius(roi: AreaRoi) -> float:
        """An ROI's own sample-diameter override wins over its radius field,
        matching `roi/rasterize.py`'s own resolution order."""
        if roi.sample_diameter_px is not None:
            return float(roi.sample_diameter_px) / 2.0
        return float(roi.sample_radius_px)

    # -- interaction --------------------------------------------------------

    def _on_active_tool_changed(self, tool: ImageTool | None) -> None:
        self._rotate_tool.set_active(tool is ImageTool.ROTATE)
        self._crop_tool.set_active(tool is ImageTool.CROP)
        self._view.viewport().unsetCursor()  # drop any resize/move cursor left over from Crop
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

    def _refresh_tool_info(self, tool: ImageTool | None) -> None:
        """Point the info icon's tooltip at *tool*'s controls. Always shows
        something - a tool with no row of its own (crop, measure) and no
        tool at all both fall back to `image_controls.py`'s plain-image
        row (left-click selects, plus the always-available drag/zoom)."""
        theme = get_active_theme()
        self._tool_info.setPixmap(load_tabler_icon("info-circle", color=theme.text_muted, size=16).pixmap(16, 16))
        self._tool_info.setToolTip(controls_text(tool))

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
        if self._rotate_tool.first_point() is None or not self._in_view(scene_pos):
            return
        point = self._plot.vb.mapSceneToView(scene_pos)
        self._rotate_tool.on_mouse_moved(float(point.x()), float(point.y()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming
        if watched is self._view and event.type() == QEvent.Type.KeyPress:
            if self._rotate_tool.handle_key(event.key(), event.modifiers()):
                event.accept()
                return True
            if self._crop_tool.handle_key(event.key()):
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
                self._show_rotate_context_menu()
            return
        if self._active_tool.active() is ImageTool.CROP:
            # A plain (non-drag) left-click has nothing to do - dragging is
            # handled separately, by ImageViewBox's left-drag handler
            # (`_on_crop_drag_event`), since a real drag never reaches
            # `sigMouseClicked` at all (pyqtgraph routes it as a drag
            # event once the mouse has moved past its click threshold).
            if button == Qt.MouseButton.RightButton and self._in_view(scene_pos):
                self._show_crop_context_menu()
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

    def _show_rotate_context_menu(self) -> None:
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
        if show_tool_context_menu(self, [("Cancel rotation", True)]) == "Cancel rotation":
            self._active_tool.set_active(ImageTool.ROTATE, False)

    def _show_crop_context_menu(self) -> None:
        """Right-click while Crop is active: "Apply crop" (enabled only
        with something pending, `CropTool.has_pending_changes`) and
        "Cancel crop", always enabled - like Rotate's menu, it exits Crop
        mode entirely (`ActiveToolModule.set_active(CROP, False)`), the
        same as clicking the Workflow panel's Crop button again, dropping
        any not-yet-applied edit along the way. "Cancel" always being
        clickable is what keeps this menu from ever having nothing
        clickable in it, even with "Apply" grayed out - see
        `context_menu.py`'s docstring."""
        chosen = show_tool_context_menu(self, [("Apply crop", self._crop_tool.has_pending_changes()), ("Cancel crop", True)])
        if chosen == "Apply crop":
            self._on_crop_apply_requested()
        elif chosen == "Cancel crop":
            self._active_tool.set_active(ImageTool.CROP, False)

    def _on_crop_drag_event(self, ev: object) -> bool:
        """`ImageViewBox`'s left-drag handler (image_controls.py). Declines
        (returns `False`) whenever Crop isn't the active tool, so it has no
        effect on any other tool or on plain ROI dragging - only one thing
        in the whole panel claims left-button drags at a time."""
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

    def roi_at(self, x: float, y: float) -> int | None:
        """The ROI whose sample aperture contains display-space point
        (x, y), or `None`. Public because it is the one piece of this
        panel's hit-testing worth driving directly from a test - clicking by
        screen coordinate is exactly what AGENTS.md's testability rule says
        to avoid needing.

        Nearest-center wins when apertures overlap, so a small ROI sitting
        inside a large one stays reachable."""
        frame = (self._current_cube(), self._current_wavelength())
        affine = self._chromatic.affine_for(frame)
        best: tuple[float, int] | None = None
        for roi in self._roi_toolbox.rois():
            cx, cy = self._roi_toolbox.display_position(roi.area_roi_id, frame, affine)
            distance = float(np.hypot(x - cx, y - cy))
            if distance <= self._sample_radius(roi) and (best is None or distance < best[0]):
                best = (distance, roi.area_roi_id)
        return None if best is None else best[1]

    def _on_drag(self, roi_id: int, x: float, y: float) -> None:
        """Forwards a drag gesture to the owning module - never mutates ROI
        state directly."""
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
