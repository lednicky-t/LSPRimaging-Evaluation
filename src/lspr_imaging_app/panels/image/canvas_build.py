"""The Image panel's canvas, built (2026-11-15; moved out of `ImagePanel._build_canvas`, which was 173 lines of the 2,700-line `panel.py`).

The pyqtgraph view and plot, the image and "no data" items, every tint and overlay item **in the order that decides what paints over what**, the
ROI / tuning / signal-map overlays, the decorations object, the Rotate / Crop / Measure / area-selection tools and the cursor readout.

**What this is, honestly:** the construction code moved as it was (`self` is `panel`): a function that reaches into the panel's private fields, listed in
the function's docstring. It is a first step: a file of its own, with its dependencies visible, instead of one more block inside the biggest class. The next
step, replacing the reach-in by an object that owns what it creates, is described in `docs/image_panel_split_investigation_2026-11-15.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QGraphicsView,
)
from lspr_ui import get_active_theme
from ...analysis.roi_tuning_module import TuningView
from .area_selection_tool import AreaSelectionTool
from .crop_size_controls import CropSizeControls
from .crop_tool import CropTool
from .gpu_view import mark_opaque
from .image_controls import ImageViewBox
from .edit_preview_layer import EditPreviewLayer
from .lock_layer import LockLayer
from .measure_controls import MeasureCalibrationControls
from .measure_line_tool import MeasureLineTool
from .decoration_overlays import DecorationOverlays
from .cursor_readout import CursorReadout
from .crop_measure_controls import CropMeasureControls
from .roi_overlay import RoiOverlay
from .rotate_line_tool import RotateLineTool
from .signal_map_display import SignalMapDisplay
from .tuning_overlay import TuningOverlay
from ..ui_state import UiStateStore
from .scale_bar_overlay import ScaleBarItem
from .timed_canvas import TimedImageItem, TimedLayoutWidget
from .panel_constants import (
    _CHUNK_GRID_COLOR,
    _CROP_OUTLINE_COLOR,
    _PREVIEW_TOOLS,
    _SOURCE_GRID_COLOR,
)

if TYPE_CHECKING:
    from .panel import ImagePanel


def build_canvas(panel: ImagePanel) -> None:
    """The pyqtgraph canvas: view, plot, image/overlay items, canvas tools and
    the cursor readout. Only creates `self._*` attributes, so the toolbar and
    layout code in `_build_toolbar_and_layout` can rely on them.

    Moved out of `ImagePanel._build_canvas` (2026-11-15). On the panel it
    creates: `_area_tool`, `_chunk_grid_curve`, `_crop_controls`, `_crop_measure`, `_crop_outline_curve`, `_crop_tool`, `_cursor_readout`,
    `_decorations`, `_edit_preview_layer`, `_image_item`, `_landmark_fitted_item`, `_landmark_line_item`, `_landmark_observed_item`,
    `_lock_layer`, `_measure_controls`, `_measure_tool`, `_no_data_item`, `_plot`, `_roi_overlay`, `_roi_overlay_store`, `_rotate_tool`,
    `_scale_bar_item`, `_source_grid_curve`, `_tuning_overlay`, `_tuning_view`, `_view`, `signal_map`
    reads: `_active_tool`, `_area_selection`, `_chromatic`, `_dataset`, `_geometry`, `_highlight_tint`, `_initial_chromatic_view`,
    `_interaction`, `_last_image_shape`, `_mask`, `_mask_tint`, `_roi_display`, `chromatic_view_changed`, `cursor_value_changed`
    calls its methods: `_add_curve`, `_draw_roi_overlay`, `refresh_theme`
    """
    # Parent passed at construction, never set later via a layout that is
    # not itself attached yet - see CLAUDE.md's phantom-top-level-window
    # pitfall, which cost ~6 rounds of screen-recording analysis to find
    # the last time it was hit.
    panel._view = TimedLayoutWidget(parent=panel)
    # Repaint the whole canvas on every change: the cursor crosshair's
    # InfiniteLines span the full view, and Qt's partial-update rectangles
    # left stale copies of them behind as ghost lines (2026-10-03).
    panel._view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    mark_opaque(panel._view.viewport())  # its background brush fills it: the widgets behind need no repaint (gpu_view.py)
    panel.refresh_theme()
    # ImageViewBox: middle-drag pans, wheel zooms, left/right drags do
    # nothing (image_controls.py).
    panel._plot = panel._view.addPlot(viewBox=ImageViewBox())
    panel._plot.invertY(True)  # image row 0 at the top, like the old app
    panel._plot.setAspectLocked(True)
    panel._plot.hideAxis("left")
    panel._plot.hideAxis("bottom")
    panel._plot.setMenuEnabled(False)

    # Pixels without a value (NaN, e.g. rotation corners) are transparent
    # in `_image_item`; this checker sits underneath so they read as
    # "no data" and never as black or a colormap colour.
    panel._no_data_item = pg.ImageItem(axisOrder="row-major")
    panel._no_data_item.setZValue(-1)
    panel._no_data_item.hide()
    panel._plot.addItem(panel._no_data_item)

    panel._image_item = TimedImageItem(axisOrder="row-major")
    panel._plot.addItem(panel._image_item)

    # Mask-overlay tint, drawn between the image and the ROI curves
    # below (so ROI markers stay legible over it). Row-major, matching
    # `_image_item` - no transpose needed, unlike the stable app's
    # `ignore_mask_item` (col-major `pg.ImageItem`, hence its transpose
    # in `overlay_manager._update_ignore_mask_overlay`).
    panel._mask_tint.attach(panel._plot)

    # Histogram highlight-overlay tint (2026-10-02) - same row-major
    # shape as `_mask_tint.item` just above, a separate item so the
    # two tints can be shown/hidden/recolored independently.
    panel._highlight_tint.attach(panel._plot)

    # The Mask tab's edit preview (2026-10-29): the pixels the picked tool would select or change, one more tint above
    # the two above and below the ROI curves. Fed by `MaskTab.edit_preview`, which exists once the tab is built.
    panel._edit_preview_layer = EditPreviewLayer(
        panel._plot, panel._geometry, panel._area_selection,
        hidden=lambda: panel._active_tool.active() in _PREVIEW_TOOLS,
    )
    # The selection lock (2026-11-15): everything outside it dimmed, its border a solid line. Above the tints, below the ROI curves.
    panel._lock_layer = LockLayer(
        panel._plot, panel._area_selection,
        hidden=lambda: panel._active_tool.active() in _PREVIEW_TOOLS,
        image_shape=lambda: panel._last_image_shape,
    )

    # The ROI circles, reference rings, selection highlight and labels (roi_overlay.py).
    # How they are drawn is the "ROIs" ribbon tab (`roi_overlay_controls.py`); display-only:
    # this panel owns the values, no other module does.
    panel._roi_overlay = RoiOverlay(panel._plot, panel._roi_display)
    panel._roi_overlay_store: UiStateStore | None = None
    # ROI position tuning (2026-10-29): the crosses of the tried centres, and the ROIs drawn at their best centres (`set_tuning_view`).
    panel._tuning_overlay = TuningOverlay(panel._plot)
    panel._tuning_view: TuningView | None = None
    # The signal map (2026-11-10): ROIs coloured by their response, its legend and hover text (`signal_map_display.py`); the owner calls `signal_map.set_view`.
    panel.signal_map = SignalMapDisplay(panel._plot, panel, lambda: panel._draw_roi_overlay() if panel._last_image_shape is not None else None, panel.roi_at)
    panel._chunk_grid_curve = panel._add_curve(_CHUNK_GRID_COLOR, width=1.0, dashed=True)
    panel._source_grid_curve = panel._add_curve(_SOURCE_GRID_COLOR, width=1.0, dashed=True)
    # Chromatic landmark overlay (2026-10-04): crosses = *estimated* (tracked
    # on the image) positions, dots = *fitted* positions (what the correction
    # model says), lines connect them, each in its wavelength's own colour
    # (`wavelength_color.py`). Symbol sizes are screen pixels; styling is
    # set per draw (`DecorationOverlays.draw_landmarks`).
    panel._landmark_line_item = pg.PlotDataItem(connect="finite")
    panel._plot.addItem(panel._landmark_line_item)
    panel._landmark_observed_item = pg.ScatterPlotItem(pxMode=True)
    panel._landmark_fitted_item = pg.ScatterPlotItem(pxMode=True)
    panel._plot.addItem(panel._landmark_observed_item)
    panel._plot.addItem(panel._landmark_fitted_item)
    # Scale bar (2026-10-06): a screen-space child of the ViewBox, see
    # `scale_bar_overlay.py`; laid out by `DecorationOverlays.draw_scale_bar`.
    panel._scale_bar_item = ScaleBarItem(panel._plot.vb)
    # ROI labels (the ROIs tab's toggle): the same kind of item, see `roi_label_overlay.py`.
    panel._roi_overlay.attach_labels()
    panel._roi_overlay.layers.set_refresher(panel._refresh_overlay_items)
    # Drawn only while a preview tool is active (see `_draw_overlays`).
    panel._crop_outline_curve = panel._add_curve(_CROP_OUTLINE_COLOR, width=1.5, dashed=True)
    # The scale bar, landmarks, crop outline and chunk grids above are drawn by one object (decoration_overlays.py); the items stay
    # created here, in this order, because it decides what paints over what.
    panel._decorations = DecorationOverlays(
        panel._dataset,
        panel._geometry,
        panel._chromatic,
        view=panel._view,
        chunk_grid_curve=panel._chunk_grid_curve,
        source_grid_curve=panel._source_grid_curve,
        crop_outline_curve=panel._crop_outline_curve,
        landmark_line_item=panel._landmark_line_item,
        landmark_observed_item=panel._landmark_observed_item,
        landmark_fitted_item=panel._landmark_fitted_item,
        scale_bar_item=panel._scale_bar_item,
        preview_tool_active=lambda: panel._active_tool.active() in _PREVIEW_TOOLS,
        last_image_shape=lambda: panel._last_image_shape,
        current_cube=panel._current_cube,
        current_wavelength=panel._current_wavelength,
        schedule_redraw=panel._schedule_redraw,
        view_changed=panel.chromatic_view_changed.emit,
        landmarks_visible=panel._initial_chromatic_view[0],
        landmarks_all_wavelengths=panel._initial_chromatic_view[1],
    )
    panel._plot.vb.sigRangeChanged.connect(panel._decorations.draw_scale_bar)
    panel._plot.vb.sigResized.connect(panel._decorations.draw_scale_bar)

    panel._rotate_tool = RotateLineTool(panel._plot, panel._geometry, parent=panel)
    panel._rotate_tool.status_changed.connect(panel._on_tool_status)

    panel._crop_tool = CropTool(panel._plot, panel._geometry, parent=panel)
    panel._crop_tool.status_changed.connect(panel._on_tool_status)
    # Left-button drags do nothing by default (image_controls.py); Crop
    # is the first tool to claim them - declines (returns False) unless
    # it is the active tool, so every other case is untouched.
    panel._plot.vb.set_left_drag_handler(panel._on_left_drag_event)
    panel._plot.vb.set_right_drag_handler(lambda ev: panel._interaction.on_right_drag(ev))  # the armed tool's numbers (tool_drag.py)

    # A real QWidget (QSpinBox/QToolButton need actual input, unlike a
    # painted overlay item - see crop_size_controls.py), parented to the
    # viewport so it draws on top of the graphics content. Positioned in
    # screen pixels by `CropMeasureControls.reposition_crop`; hidden until Crop is
    # active and has a rectangle to show a size for.
    panel._crop_controls = CropSizeControls(panel._view.viewport(), get_active_theme())
    panel._crop_controls.setVisible(False)
    panel._crop_controls.size_edited.connect(panel._crop_tool.set_size)

    panel._measure_tool = MeasureLineTool(panel._plot, panel._geometry, parent=panel)
    panel._measure_tool.status_changed.connect(panel._on_tool_status)

    panel._measure_controls = MeasureCalibrationControls(panel._view.viewport(), get_active_theme())
    panel._measure_controls.setVisible(False)
    # Where the two floating control widgets sit and what the tools' results do to them (crop_measure_controls.py).
    panel._crop_measure = CropMeasureControls(
        panel._plot,
        panel._view,
        panel._geometry,
        panel._active_tool,
        crop_tool=panel._crop_tool,
        crop_controls=panel._crop_controls,
        measure_tool=panel._measure_tool,
        measure_controls=panel._measure_controls,
        status=panel._on_tool_status,
    )
    panel._crop_tool.changed.connect(panel._crop_measure.on_crop_tool_changed)
    panel._plot.vb.sigTransformChanged.connect(panel._crop_measure.reposition_crop)
    panel._crop_controls.apply_requested.connect(panel._crop_measure.on_crop_apply_requested)
    panel._measure_tool.measured.connect(panel._crop_measure.on_measure_measured)
    panel._measure_controls.apply_requested.connect(panel._crop_measure.on_measure_apply_requested)
    panel._plot.vb.sigTransformChanged.connect(panel._crop_measure.reposition_measure)

    # Area selection (2026-10-03): drag gesture + marching-ants outline.
    # The state is `AreaSelectionModule`'s; this only draws/forwards.
    panel._area_tool = AreaSelectionTool(
        panel._plot, panel._area_selection, parent=panel, notify=panel._on_tool_status,
        brush_diameter=lambda: float(panel._mask.settings().brush_size_px),
    )

    # Cursor readout text lives on the canvas, top-left (maintainer,
    # 2026-10-03) - the toggle itself sits in the "General" group (cursor_readout.py).
    panel._cursor_readout = CursorReadout(
        panel._view.viewport(), panel._image_item, value_changed=panel.cursor_value_changed.emit
    )
