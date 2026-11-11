"""The display-only decorations over the Image panel that need no new render: the scale bar, the chromatic landmarks, the crop outline
and the two chunk grids.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 1). The drawing code is the code that was in `ImagePanel`.
The panel still creates the canvas items (their order in the plot decides what paints over what); this class draws into them and owns
the landmark overlay's own state (shown, every wavelength, what was last drawn). What it needs from the panel comes in as small functions.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QGraphicsView

from ...dataset import DatasetModule
from ...image_tools import ChromaticModule, GeometryModule
from ...image_tools.geometry.transform import raw_to_processed_affine_xy
from .landmark_overlay import draw_landmarks
from .scale_bar_overlay import ScaleBarItem


def _same_lines(a: tuple[list[float], list[float]] | None, b: tuple[list[float], list[float]] | None) -> bool:
    """Two polylines are the same (NaN, the break between grid lines, counts as equal to NaN)."""
    if a is None or b is None:
        return a is b
    return all(np.array_equal(np.asarray(p, dtype=float), np.asarray(q, dtype=float), equal_nan=True) for p, q in zip(a, b))


class DecorationOverlays:
    def __init__(
        self,
        dataset: DatasetModule,
        geometry: GeometryModule,
        chromatic: ChromaticModule,
        *,
        view: QGraphicsView,
        chunk_grid_curve: pg.PlotDataItem,
        source_grid_curve: pg.PlotDataItem,
        crop_outline_curve: pg.PlotDataItem,
        landmark_line_item: pg.PlotDataItem,
        landmark_observed_item: pg.ScatterPlotItem,
        landmark_fitted_item: pg.ScatterPlotItem,
        scale_bar_item: ScaleBarItem,
        preview_tool_active: Callable[[], bool],
        last_image_shape: Callable[[], tuple[int, int] | None],
        current_cube: Callable[[], int],
        current_wavelength: Callable[[], float],
        schedule_redraw: Callable[[], None],
        view_changed: Callable[[bool, bool], None],
        landmarks_visible: bool,
        landmarks_all_wavelengths: bool,
    ) -> None:
        """``preview_tool_active()``: a tool that works on the uncropped image is active (Rotate, Crop; the panel's `_PREVIEW_TOOLS`).
        ``last_image_shape()``: (height, width) of the last rendered plane, or ``None``. ``schedule_redraw()``: redraw the image
        (coalesced). ``view_changed(visible, all_wavelengths)``: the landmark overlay's two view toggles changed; the app settings
        remember them."""
        self._dataset = dataset
        self._geometry = geometry
        self._chromatic = chromatic
        self._view = view
        self._chunk_grid_curve = chunk_grid_curve
        self._source_grid_curve = source_grid_curve
        self._crop_outline_curve = crop_outline_curve
        # What the three line curves show now (None: nothing), so an unchanged frame leaves them alone (`draw_crop_outline`, `_show_grid`).
        self._crop_outline_drawn: tuple[list[float], list[float]] | None = None
        self._grid_drawn: dict[str, tuple[list[float], list[float]] | None] = {"chunk": None, "source": None}
        self._landmark_line_item = landmark_line_item
        self._landmark_observed_item = landmark_observed_item
        self._landmark_fitted_item = landmark_fitted_item
        self._scale_bar_item = scale_bar_item
        self._preview_tool_active = preview_tool_active
        self._last_image_shape = last_image_shape
        self._current_cube = current_cube
        self._current_wavelength = current_wavelength
        self._schedule_redraw = schedule_redraw
        self._view_changed = view_changed
        self.landmarks_visible = bool(landmarks_visible)
        self.landmarks_all_wavelengths = bool(landmarks_all_wavelengths)  # False = current wavelength only
        # What the landmark overlay currently shows (see `draw_landmarks`):
        # drawing it costs 45-160 ms in all-wavelengths mode, so it is redrawn
        # only when this key changes. `_landmark_version` is bumped by anything
        # that changes the landmarks themselves (chromatic model, dataset).
        self._landmark_version = 0
        self._landmark_drawn_key: tuple | None = None

    def on_show_landmarks_changed(self, visible: bool) -> None:
        self.landmarks_visible = bool(visible)
        self._schedule_redraw()
        self._view_changed(self.landmarks_visible, self.landmarks_all_wavelengths)

    def on_landmark_scope_changed(self, all_wavelengths: bool) -> None:
        self.landmarks_all_wavelengths = bool(all_wavelengths)
        self._schedule_redraw()
        self._view_changed(self.landmarks_visible, self.landmarks_all_wavelengths)

    def invalidate_landmarks(self, *_args: object) -> None:
        self._landmark_version += 1

    def draw_scale_bar(self, *_args: object) -> None:
        """Lay the scale bar out for the current view and units (display only)."""
        settings = self._geometry.settings()
        um_per_px = (
            self._geometry.microns_per_pixel_scalar()
            if settings.display_units == "um" and self._geometry.can_display_micrometers()
            else None
        )
        shown = bool(settings.scale_bar_visible) and not self._preview_tool_active()
        self._scale_bar_item.update_view(shown, um_per_px)

    def draw_landmarks(self) -> None:
        """Chromatic landmarks (display only); drawing rules in
        `landmark_overlay.draw_landmarks`."""
        shown = self.landmarks_visible and not self._preview_tool_active()
        # The all-wavelengths view does not depend on the viewed frame at all,
        # so slider moves leave it alone; the current-wavelength view depends
        # on the frame. Anything else that changes the picture bumps
        # `_landmark_version` (chromatic model, dataset).
        frame = None if self.landmarks_all_wavelengths else (self._current_cube(), self._current_wavelength())
        key = (shown, self.landmarks_all_wavelengths, frame, self._landmark_version)
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
            all_wavelengths=self.landmarks_all_wavelengths,
        )

    def clear_lines(self) -> None:
        """Remove the crop outline and both chunk grids (the dataset went away)."""
        for curve in (self._chunk_grid_curve, self._source_grid_curve, self._crop_outline_curve):
            curve.clear()
        self._crop_outline_drawn = None
        self._grid_drawn = {"chunk": None, "source": None}

    def draw_crop_outline(self) -> None:
        """The existing crop as a dashed rectangle over the uncropped
        preview - only while a preview tool is active. Crop coordinates are
        in the rotated+flipped canvas, which is exactly what the uncropped
        preview shows (transform order: rotate -> flip -> crop)."""
        crop = self._geometry.settings().crop
        if not self._preview_tool_active() or not crop.enabled or crop.width <= 0 or crop.height <= 0:
            outline = None
        else:
            x0, y0 = float(crop.x), float(crop.y)
            x1, y1 = x0 + float(crop.width), y0 + float(crop.height)
            outline = ([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])
        if outline == self._crop_outline_drawn:
            return  # unchanged (every Play frame): touching the curve made Qt repaint the whole Image view again (2026-11-11)
        self._crop_outline_drawn = outline
        if outline is None:
            self._crop_outline_curve.clear()
        else:
            self._crop_outline_curve.setData(*outline)

    def update_chunk_grid(self) -> None:
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
            hidden = self._preview_tool_active()
            raw_shape = None if hidden else self._last_image_shape()
        else:
            raw_shape = self._dataset.raw_plane_shape() if preview.enabled else None
        if not preview.enabled or raw_shape is None or preview.chunk_w <= 0 or preview.chunk_h <= 0:
            lines = None
        else:
            to_display = (
                np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
                if preview.processed
                else raw_to_processed_affine_xy((int(raw_shape[0]), int(raw_shape[1])), self._geometry.settings())
            )
            lines = self.grid_polyline(raw_shape, to_display, preview.chunk_w, preview.chunk_h, preview.offset_x, preview.offset_y)
        self._show_grid(self._chunk_grid_curve, "chunk", lines)

    def _show_grid(self, curve, which: str, lines: tuple[list[float], list[float]] | None) -> None:
        """Put `lines` (None: nothing) on a grid curve and repaint the view - only when they differ from what it shows. Both grids are
        updated at every frame shown; an unconditional clear + repaint made Qt repaint the whole Image view a second time per Play
        frame, grids off or not (2026-11-11)."""
        if _same_lines(lines, self._grid_drawn[which]):
            return
        self._grid_drawn[which] = lines
        if lines is None:
            curve.clear()
        else:
            curve.setData(*lines)
        # Nothing else re-renders on a toggle, so without this the removed / moved lines stay painted until the mouse moves over the image.
        self._view.viewport().update()

    def update_source_grid(self) -> None:
        """Draw (or clear) the loaded OME-Zarr's own chunk grid (the View tab's blue toggle): lines at every stored chunk
        boundary from pixel (0, 0), in raw pixels and mapped onto the displayed image like the export preview's raw
        branch (tilted under a rotation, shifted by a crop). A TIFF stack has no chunks, so nothing is drawn."""
        module = self._dataset
        chunk = module.source_chunk_shape() if module.source_chunk_grid_visible() else None
        raw_shape = module.raw_plane_shape() if chunk is not None else None
        if chunk is None or raw_shape is None or chunk[0] <= 0 or chunk[1] <= 0:
            lines = None
        else:
            to_display = raw_to_processed_affine_xy((int(raw_shape[0]), int(raw_shape[1])), self._geometry.settings())
            lines = self.grid_polyline(raw_shape, to_display, chunk[1], chunk[0], 0, 0)
        self._show_grid(self._source_grid_curve, "source", lines)

    @staticmethod
    def grid_polyline(
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


def _append_polyline(xs: list[float], ys: list[float], new_x: np.ndarray, new_y: np.ndarray) -> None:
    """Append one closed curve, separated from whatever came before by a
    NaN. ``PlotDataItem(connect="finite")`` breaks the line at non-finite
    points, which is what lets a single item draw many disjoint circles."""
    if xs:
        xs.append(float("nan"))
        ys.append(float("nan"))
    xs.extend(new_x.tolist())
    ys.extend(new_y.tolist())
