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

- Crop/rotate interaction (the draggable crop rectangle, the rotation
  handle). ``GeometryModule``'s commands are real; a tool UI to drive them
  is not, and the old app's version (`gui/image_tools_controller.py`) is
  tangled with pyqtgraph ``RectROI`` sync that needs its own port.
- Mask painting/preview overlays, the intensity-highlight overlay, and the
  histogram-driven highlight (`MaskModule`'s async candidate machinery
  isn't built either - see its module docstring).
- The cursor readout, scale bar, and ruler overlay - ``GeometryModule``
  already owns the calibration state they would draw from.
- ROI creation by click and ROI resize by handle. ``add_roi``/``resize_roi``
  are real commands; this panel currently only moves and selects, which is
  what makes the overlay worth looking at in the first place.
"""

from __future__ import annotations

import itertools
import logging

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
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

from ...dataset import DatasetModule
from ...image_tools import BackgroundModule, ChromaticModule, GeometryModule, MaskModule
from ...roi import RoiToolbox
from ...roi.model import AreaRoi
from ...roi.rasterize import effective_reference_radii, transformed_circle_points
from ...selection import SelectionModule
from .render import ImageRenderer, RenderRequest, RenderResult

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


class ImagePanel(QWidget):
    """Renders the current processed image with ROI overlays. Owns no
    computation and no ROI/group state (AGENTS.md)."""

    def __init__(
        self,
        dataset: DatasetModule,
        geometry: GeometryModule,
        mask: MaskModule,
        chromatic: ChromaticModule,
        background: BackgroundModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
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

        self._serial = itertools.count(1)
        self._latest_serial = 0
        self._drag_roi_id: int | None = None

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
        self._plot = self._view.addPlot()
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

        self._status = QLabel("No dataset loaded.", self)
        self._status.setWordWrap(True)

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

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._view, 1)

        self._image_item.scene().sigMouseClicked.connect(self._on_scene_clicked)

    def _add_curve(self, color_hex: str, *, width: float) -> pg.PlotDataItem:
        curve = pg.PlotDataItem(pen=pg.mkPen(QColor(color_hex), width=width), connect="finite")
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
        self._refresh_navigation_ranges()
        self._status.setText("No dataset loaded.")

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

        self._latest_serial = next(self._serial)
        self._renderer.submit(
            RenderRequest(
                cube_index=cube_index,
                wavelength_nm=wavelength_nm,
                geometry=self._geometry.settings(),
                background=self._background.settings(),
                authored_mask=authored_mask,
                mask_warp_affine=warp_affine,
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

    # -- overlays -----------------------------------------------------------

    def _draw_overlays(self) -> None:
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

    @staticmethod
    def _sample_radius(roi: AreaRoi) -> float:
        """An ROI's own sample-diameter override wins over its radius field,
        matching `roi/rasterize.py`'s own resolution order."""
        if roi.sample_diameter_px is not None:
            return float(roi.sample_diameter_px) / 2.0
        return float(roi.sample_radius_px)

    # -- interaction --------------------------------------------------------

    def _on_scene_clicked(self, event: object) -> None:
        """Select the ROI under the cursor, or clear the selection when the
        click lands on empty image. Ctrl/Shift toggles instead of replacing,
        matching the platform convention for multi-select lists."""
        try:
            scene_pos = event.scenePos()
        except AttributeError:  # pragma: no cover - defensive against pyqtgraph versions
            return
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
