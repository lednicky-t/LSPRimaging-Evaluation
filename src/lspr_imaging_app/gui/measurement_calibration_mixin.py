from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtGui import QColor

from lspr_imaging_app.gui.worker import MeasurementOverlayBundle, ScaleBarOverlayBundle


class MeasurementCalibrationMixin:
    """The measurement (ruler) tool and the scale-bar overlay: drawing them,
    and converting a ruler measurement into a px<->um calibration. Mixed
    into MainWindow (same pattern as MainWindowIcons): `self` here is the
    MainWindow instance, so these methods use the same window state/widgets
    as the rest of the class.
    """

    def _hide_measurement_overlay(self) -> None:
        self._overlay_manager._hide_measurement_overlay()

    def _ensure_measurement_overlay(self) -> MeasurementOverlayBundle:
        return self._overlay_manager._ensure_measurement_overlay()

    def _update_measurement_overlay(self) -> None:
        self._overlay_manager._update_measurement_overlay()

    def _on_measurement_marker_moved(self, *_args) -> None:
        self._overlay_manager._on_measurement_marker_moved(*_args)

    def _update_measurement_status_label(
        self,
        *,
        dx_px: float | None = None,
        dy_px: float | None = None,
        distance_px: float | None = None,
    ) -> None:
        self._overlay_manager._update_measurement_status_label(dx_px=dx_px, dy_px=dy_px, distance_px=distance_px)

    def _apply_measurement_calibration(self) -> None:
        dx_px, dy_px, _distance_px = self._measurement_delta_components_px()
        dx_um = float(self.measurement_um_x_spin.value())
        dy_um = float(self.measurement_um_y_spin.value())
        if dx_um <= 0.0 and dy_um <= 0.0:
            self._set_status_text("Enter a real Δx and/or Δy in µm before applying calibration.")
            return
        if dx_um > 0.0 and abs(dx_px) < 1e-6:
            self._set_status_text("Δx between the ruler guides is zero, so Δx calibration cannot be applied.")
            return
        if dy_um > 0.0 and abs(dy_px) < 1e-6:
            self._set_status_text("Δy between the ruler guides is zero, so Δy calibration cannot be applied.")
            return
        self._push_undo_point("Measurement calibration")
        if dx_um > 0.0:
            self._state.preprocessing.microns_per_pixel_x = abs(dx_um / dx_px)
        if dy_um > 0.0:
            self._state.preprocessing.microns_per_pixel_y = abs(dy_um / dy_px)
        if dx_um > 0.0 and dy_um <= 0.0:
            self._state.preprocessing.microns_per_pixel_y = self._state.preprocessing.microns_per_pixel_x
        if dy_um > 0.0 and dx_um <= 0.0:
            self._state.preprocessing.microns_per_pixel_x = self._state.preprocessing.microns_per_pixel_y
        self._state.preprocessing.calibration_enabled = True
        self._state.preprocessing.display_units = "um"
        self._update_display_unit_controls()
        self._sync_roi_detection_controls()
        self._save_processing_state_for_dataset()
        self._set_status_text("Measurement calibration applied in memory. Display units switched to micrometers.")

    def _sync_measurement_visibility(self) -> None:
        if hasattr(self, "measurement_info_row"):
            self.measurement_info_row.setVisible(self._active_tool == "measure")
        self._update_measurement_overlay()
        self._refresh_scale_bar_overlay()

    def _ensure_scale_bar_overlay(self) -> ScaleBarOverlayBundle:
        if self._scale_bar_overlay is not None:
            return self._scale_bar_overlay
        # Each bar/tick is drawn as three stacked strokes (dark halo under a light
        # halo under the color line). A single light halo disappears on a bright
        # background, so the dark ring guarantees contrast on light images while
        # the light ring keeps doing the same job on dark images.
        dark_outline_line = pg.PlotCurveItem(pen=pg.mkPen(QColor(0, 0, 0, 200), width=6.6))
        outline_line = pg.PlotCurveItem(pen=pg.mkPen(QColor(255, 255, 255, 220), width=5.0))
        line = pg.PlotCurveItem(pen=pg.mkPen(self._scale_bar_visual_color, width=2.4))
        dark_outline_left_tick = pg.PlotCurveItem(pen=pg.mkPen(QColor(0, 0, 0, 200), width=5.8))
        outline_left_tick = pg.PlotCurveItem(pen=pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
        left_tick = pg.PlotCurveItem(pen=pg.mkPen(self._scale_bar_visual_color, width=2.0))
        dark_outline_right_tick = pg.PlotCurveItem(pen=pg.mkPen(QColor(0, 0, 0, 200), width=5.8))
        outline_right_tick = pg.PlotCurveItem(pen=pg.mkPen(QColor(255, 255, 255, 220), width=4.2))
        right_tick = pg.PlotCurveItem(pen=pg.mkPen(self._scale_bar_visual_color, width=2.0))
        # Label uses a small solid chip (same convention as the ROI/landmark tags)
        # instead of a halo, since overlapping two same-size text items can't
        # produce a real outline - the top layer fully covers the one beneath it.
        label = pg.TextItem(anchor=(0.5, 1.0))
        self.image_plot.addItem(dark_outline_line, ignoreBounds=True)
        self.image_plot.addItem(outline_line, ignoreBounds=True)
        self.image_plot.addItem(line, ignoreBounds=True)
        self.image_plot.addItem(dark_outline_left_tick, ignoreBounds=True)
        self.image_plot.addItem(outline_left_tick, ignoreBounds=True)
        self.image_plot.addItem(left_tick, ignoreBounds=True)
        self.image_plot.addItem(dark_outline_right_tick, ignoreBounds=True)
        self.image_plot.addItem(outline_right_tick, ignoreBounds=True)
        self.image_plot.addItem(right_tick, ignoreBounds=True)
        self.image_plot.addItem(label, ignoreBounds=True)
        self._scale_bar_overlay = ScaleBarOverlayBundle(
            dark_outline_line=dark_outline_line,
            outline_line=outline_line,
            line=line,
            dark_outline_left_tick=dark_outline_left_tick,
            outline_left_tick=outline_left_tick,
            left_tick=left_tick,
            dark_outline_right_tick=dark_outline_right_tick,
            outline_right_tick=outline_right_tick,
            right_tick=right_tick,
            label=label,
        )
        return self._scale_bar_overlay

    @staticmethod
    def _nice_scale_bar_value(target: float) -> float:
        if target <= 0.0:
            return 1.0
        exponent = np.floor(np.log10(target))
        base = target / (10.0 ** exponent)
        if base < 1.5:
            nice_base = 1.0
        elif base < 3.5:
            nice_base = 2.0
        elif base < 7.5:
            nice_base = 5.0
        else:
            nice_base = 10.0
        return float(nice_base * (10.0 ** exponent))

    def _refresh_scale_bar_overlay(self) -> None:
        self._overlay_manager._refresh_scale_bar_overlay()
