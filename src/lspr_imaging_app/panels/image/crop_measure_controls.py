"""The floating size / calibration controls of the Crop and Measure tools, and what the two tools' signals do to them.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 2). The code is the code that was in `ImagePanel`. The tools
(`CropTool`, `MeasureLineTool`) and their control widgets are still created by the panel; this class keeps the widgets next to the tool's
rectangle / ruler point, pushes the tool's values into them, and applies the result.
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QGraphicsView

from ...image_tools import ActiveToolModule, GeometryModule, ImageTool
from .crop_size_controls import CropSizeControls
from .crop_tool import CropTool
from .measure_controls import MeasureCalibrationControls
from .measure_line_tool import MeasureLineTool


class CropMeasureControls:
    def __init__(
        self,
        plot: pg.PlotItem,
        view: QGraphicsView,
        geometry: GeometryModule,
        active_tool: ActiveToolModule,
        *,
        crop_tool: CropTool,
        crop_controls: CropSizeControls,
        measure_tool: MeasureLineTool,
        measure_controls: MeasureCalibrationControls,
        status: Callable[[str], None],
    ) -> None:
        """``status(text)``: a transient message for the status bar (the panel's `_on_tool_status`)."""
        self._plot = plot
        self._view = view
        self._geometry = geometry
        self._active_tool = active_tool
        self._crop_tool = crop_tool
        self._crop_controls = crop_controls
        self._measure_tool = measure_tool
        self._measure_controls = measure_controls
        self._status = status

    def on_crop_apply_requested(self) -> None:
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
            self.reposition_crop()

    def on_crop_tool_changed(self) -> None:
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
        self.reposition_crop()

    def reposition_crop(self) -> None:
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

    def on_measure_measured(self, dx_px: float, dy_px: float, is_fresh_placement: bool) -> None:
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
        self.reposition_measure()

    def reposition_measure(self) -> None:
        """Moves the floating calibration controls so the Apply button (not
        the widget's top-left corner) sits under the ruler's second point -
        or, while point 2 hasn't been clicked yet, under the live cursor
        (`current_anchor_point()`, maintainer's "live measurement" request,
        2026-09-29) - with the px/um fields to its left (maintainer's spec,
        2026-09-29) - same idea as `reposition_crop`, just
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

    def on_measure_apply_requested(self, dx_um: float, dy_um: float) -> None:
        """`MeasureCalibrationControls.apply_requested`. A `ValueError`
        (no real dx/dy entered, or a zero pixel delta on the requested
        axis - `GeometryModule.apply_measurement_calibration`'s own three
        guards) is reported as a status message rather than raised, the
        same convention `RotateLineTool`/`CropTool` callers already use for
        this module's other guarded commands. A *successful* apply exits
        Measure mode (maintainer's spec, 2026-09-29 - "apply, the tool will
        cancel and settings will [be] applied"), the same shape as
        `on_crop_apply_requested`: a failed attempt leaves the session
        exactly as it was, still in the tool, so a mistyped value doesn't
        also cost the placed ruler."""
        try:
            self._geometry.apply_measurement_calibration(dx_um, dy_um)
        except ValueError as exc:
            self._status(str(exc))
            return
        self._status("Measurement calibration applied - display units switched to micrometers.")
        self._active_tool.set_active(ImageTool.MEASURE, False)
