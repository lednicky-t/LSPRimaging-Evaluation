from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.models import AbsorbanceSpectrumResult, DetectedSpot, FitResult
from lspr_imaging_app.processing.analysis import fit_absorbance_curve
from lspr_imaging_app.processing.chromatic import transformed_annulus_mask, transformed_disk_mask
from lspr_imaging_app.processing.spot_detection import ignored_pixel_mask
from lspr_imaging_app.gui.spot_overlay_helpers import resolved_spot_color


class PlotManager:
    def __init__(self, window) -> None:
        self._window = window

    def refresh_histogram_if_available(self) -> None:
        if self._window._current_processed_image is None:
            return
        self._window._update_histogram(self._window._current_processed_image)

    def apply_histogram_log_mode(self, *, refresh: bool = True) -> None:
        window = self._window
        if not hasattr(window, "histogram_plot"):
            return
        enabled = bool(window._histogram_log_y_enabled)
        window.histogram_plot.setLogMode(y=enabled)
        window.histogram_plot.setLimits(yMin=window.HISTOGRAM_LOG_Y_FLOOR if enabled else 0.0)
        window.histogram_plot.setLabel("left", "Counts" if enabled else "Pixels (%)")
        view_box = window.histogram_plot.getViewBox()
        if enabled:
            view_box.disableAutoRange(axis=view_box.YAxis)
        else:
            view_box.enableAutoRange(axis=view_box.YAxis, enable=True)
        if hasattr(window, "residual_fill_item"):
            window.residual_fill_item.setVisible(not enabled)
        if hasattr(window, "residual_histogram_curve"):
            window.residual_histogram_curve.setVisible(not enabled)
        if hasattr(window, "histogram_y_scale_button"):
            window.histogram_y_scale_button.blockSignals(True)
            window.histogram_y_scale_button.setChecked(enabled)
            window.histogram_y_scale_button.blockSignals(False)
        if refresh and window._current_processed_image is not None and not window._startup_restore_in_progress:
            self.refresh_histogram_if_available()
        else:
            if enabled:
                self.clamp_histogram_log_range()
            else:
                window.histogram_plot.setYRange(0.0, 1.0, padding=0.0)

    def on_histogram_y_scale_toggled(self, checked: bool) -> None:
        self._window._histogram_log_y_enabled = bool(checked)
        self.apply_histogram_log_mode()
        self._window._save_control_preferences()

    def histogram_log_y_max(self) -> float:
        window = self._window
        log_floor = float(np.log10(window.HISTOGRAM_LOG_Y_FLOOR))
        candidates: list[float] = [log_floor]
        for curve in (
            getattr(window, "histogram_curve", None),
            getattr(window, "spot_histogram_curve", None),
            getattr(window, "ring_histogram_curve", None),
            getattr(window, "mask_histogram_curve", None),
        ):
            if curve is None:
                continue
            _x_data, y_data = curve.getData()
            if y_data is None or len(y_data) == 0:
                continue
            y_array = np.asarray(y_data, dtype=np.float64)
            y_array = y_array[np.isfinite(y_array)]
            if y_array.size:
                candidates.append(float(np.max(y_array)))
        return max(candidates)

    def clamp_histogram_log_range(self) -> None:
        window = self._window
        if not hasattr(window, "histogram_plot") or not window._histogram_log_y_enabled:
            return
        if getattr(window, "_histogram_log_range_guard", False):
            return
        window._histogram_log_range_guard = True
        try:
            y_min = float(np.log10(window.HISTOGRAM_LOG_Y_FLOOR))
            y_max = self.histogram_log_y_max()
            view_box = window.histogram_plot.getViewBox()
            view_box.disableAutoRange(axis=view_box.YAxis)
            window.histogram_plot.setLimits(yMin=y_min, yMax=y_max)
            view_box.setRange(
                yRange=(y_min, y_max),
                padding=0.0,
                disableAutoRange=True,
            )
        finally:
            window._histogram_log_range_guard = False

    def on_histogram_view_range_changed(self, *_args) -> None:
        window = self._window
        if not window._histogram_log_y_enabled or window._startup_restore_in_progress:
            return
        if getattr(window, "_histogram_log_range_guard", False):
            return
        view_box = window.histogram_plot.getViewBox()
        current_y = view_box.viewRange()[1] if view_box is not None else None
        if not current_y or len(current_y) < 2:
            return
        target_y_min = float(np.log10(window.HISTOGRAM_LOG_Y_FLOOR))
        target_y_max = self.histogram_log_y_max()
        if (
            abs(float(current_y[0]) - target_y_min) < 1e-9
            and abs(float(current_y[1]) - target_y_max) < 1e-9
        ):
            return
        self.clamp_histogram_log_range()

    def set_spectrum_summary_text(self, text: str) -> None:
        self._window.spectrum_summary_label.setText(text)
        self._window.analysis_summary_label.setText(text)

    def clear_absorbance_spectrum(self, summary_text: str) -> None:
        self.clear_spectrum_series_items()
        self._window.spectrum_current_point.setData([], [])
        self._window.spectrum_metric_point.setData([], [])
        self._window._absorbance_spectrum_dirty = True
        self.set_spectrum_summary_text(summary_text)

    def clear_spectrum_series_items(self) -> None:
        window = self._window
        if getattr(window, "_spectrum_series_items", None):
            for item in window._spectrum_series_items:
                try:
                    window.spectrum_plot.removeItem(item)
                except Exception:
                    continue
        window._spectrum_series_items = []
        window.spectrum_curve = None
        window.spectrum_fit_curve = None
        if hasattr(window, "spectrum_legend"):
            try:
                window.spectrum_legend.clear()
            except Exception:
                pass

    def spot_spectrum_color(self, spot_id: int) -> QColor:
        window = self._window
        spot = next((spot for spot in window._state.detected_spots if int(spot.spot_id) == int(spot_id)), None)
        group = window._group_for_spot(int(spot_id)) if spot is not None else None
        return QColor(resolved_spot_color(spot, group, window._spot_visual_color) if spot is not None else window._spot_visual_color)

    def analysis_fit_result_from_spectrum(self, result: AbsorbanceSpectrumResult) -> FitResult | None:
        fit = fit_absorbance_curve(
            result.wavelengths_nm,
            result.absorbance,
            poly_order=self._window._analysis_poly_order(),
        )
        if fit.fitted_wavelengths_nm.size == 0 or fit.fitted_absorbance.size == 0:
            return None
        return fit

    def add_spectrum_series(
        self,
        *,
        spot_id: int,
        result: AbsorbanceSpectrumResult,
        label: str | None = None,
        highlighted: bool = False,
        dimmed: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None] | None:
        window = self._window
        wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
        absorbance = np.asarray(result.absorbance, dtype=np.float64)
        valid_mask = np.isfinite(wavelengths) & np.isfinite(absorbance)
        if not np.any(valid_mask):
            return None
        x_values = wavelengths[valid_mask]
        y_values = absorbance[valid_mask]
        order = np.argsort(x_values)
        x_values = x_values[order]
        y_values = y_values[order]
        color = self.spot_spectrum_color(spot_id)
        fit_color = QColor(color).darker(125)
        line_width = 2.5 if highlighted else 1.8
        symbol_size = 7.5 if highlighted else 6
        fit_width = 3.0 if highlighted else 2.0
        symbol_pen_width = 1.8 if highlighted else 1.4
        symbol_color = QColor(color)
        if dimmed and not highlighted:
            symbol_color.setAlphaF(0.25)
            fit_color.setAlphaF(0.45)
        elif not highlighted:
            symbol_color.setAlphaF(0.8)
        fit_symbol_color = QColor(fit_color)
        if highlighted:
            fit_symbol_color = QColor(color)

        data_item = window.spectrum_plot.plot(
            x_values,
            y_values,
            pen=None,
            symbol="o",
            symbolSize=symbol_size,
            symbolBrush=pg.mkBrush(symbol_color),
            symbolPen=pg.mkPen(color, width=symbol_pen_width),
        )
        fit_item = None
        fit = self.analysis_fit_result_from_spectrum(result)
        if fit is not None and fit.fitted_wavelengths_nm.size and fit.fitted_absorbance.size:
            fit_item = window.spectrum_plot.plot(
                fit.fitted_wavelengths_nm,
                fit.fitted_absorbance,
                pen=pg.mkPen(
                    fit_color if not highlighted else fit_symbol_color,
                    width=fit_width,
                    style=Qt.PenStyle.SolidLine,
                ),
            )
        window._spectrum_series_items.extend([data_item, fit_item] if fit_item is not None else [data_item])
        if window.spectrum_curve is None:
            window.spectrum_curve = data_item
        if fit_item is not None and window.spectrum_fit_curve is None:
            window.spectrum_fit_curve = fit_item
        if hasattr(window, "spectrum_legend"):
            try:
                legend_sample = pg.PlotDataItem(
                    [],
                    [],
                    pen=pg.mkPen(
                        fit_color if not highlighted else fit_symbol_color,
                        width=fit_width,
                        style=Qt.PenStyle.SolidLine,
                    ) if fit_item is not None else None,
                    symbol="o",
                    symbolSize=symbol_size,
                    symbolBrush=pg.mkBrush(symbol_color),
                    symbolPen=pg.mkPen(color, width=symbol_pen_width),
                )
                window.spectrum_legend.addItem(legend_sample, f"{label or f'Spot {int(spot_id)}'}")
            except Exception:
                pass
        return (
            x_values,
            y_values,
            fit.fitted_wavelengths_nm if fit is not None else None,
            fit.fitted_absorbance if fit is not None else None,
        )

    def set_sensorgram_series(
        self,
        frame_indices,
        metric_values,
        *,
        summary_text: str | None = None,
    ) -> None:
        self._window._analysis_controller.set_sensorgram_series(frame_indices, metric_values, summary_text=summary_text)

    def update_sensorgram_current_point(self) -> None:
        self._window._analysis_controller.update_current_point()

    def update_single_frame_sensorgram(self, metric_value: float | None, metric_signal: float | None) -> None:
        window = self._window
        if window._sensorgram_running or window._sensorgram_frame_indices.size > 1:
            self.update_sensorgram_current_point()
            return
        frame = window._current_frame()
        if frame is None or metric_value is None or not np.isfinite(metric_value):
            self.clear_sensorgram("Calculate all frames to build the fitted sensorgram.")
            return
        signal_value = float(metric_signal) if metric_signal is not None and np.isfinite(metric_signal) else float("nan")
        window._sensorgram_metric_signal = np.asarray([signal_value], dtype=np.float64)
        self.set_sensorgram_series(
            [int(frame)],
            [float(metric_value)],
            summary_text=(
                f"{window._analysis_metric_label()} | Frame {int(frame)} = {float(metric_value):.3f} nm"
                f" | Polynomial order {window._analysis_poly_order()}"
            ),
        )

    def clear_sensorgram(self, summary_text: str) -> None:
        self._window._analysis_controller.clear_sensorgram(summary_text)

    def update_histogram(self, image: np.ndarray) -> None:
        window = self._window
        values, spot_values, ring_values, mask_values = self.histogram_source_values(image)
        y_floor = window.HISTOGRAM_LOG_Y_FLOOR if window._histogram_log_y_enabled else 0.0
        view_box = window.histogram_plot.getViewBox()
        if window._histogram_log_y_enabled:
            view_box.disableAutoRange(axis=view_box.YAxis)
        if values.size == 0:
            window.histogram_curve.setData([], [])
            window.spot_histogram_curve.setData([], [])
            window.ring_histogram_curve.setData([], [])
            window.mask_histogram_curve.setData([], [])
            window.residual_histogram_curve.setData([], [])
            window.hist_region_label.hide()
            window.ignore_region_label.hide()
            window.spot_histogram_label.hide()
            window.ring_histogram_label.hide()
            return
        bin_size = int(np.clip(window.histogram_bins_spin.value(), 1, 8192))
        data_lower = float(np.min(values))
        data_upper = float(np.max(values))
        lower_edge = float(np.floor(data_lower / float(bin_size)) * float(bin_size))
        upper_edge = float(np.ceil(data_upper / float(bin_size)) * float(bin_size))
        lower_edge = float(np.clip(lower_edge, window.HISTOGRAM_MIN_INTENSITY, window.HISTOGRAM_MAX_INTENSITY))
        upper_edge = float(np.clip(max(upper_edge, lower_edge + float(bin_size)), window.HISTOGRAM_MIN_INTENSITY, window.HISTOGRAM_MAX_INTENSITY + 1.0))
        edges = np.arange(lower_edge, upper_edge + float(bin_size), float(bin_size), dtype=np.float64)
        if edges.size < 2 or edges[-1] < upper_edge:
            edges = np.append(edges, upper_edge)
        counts, edges = np.histogram(values, bins=edges)
        centers = 0.5 * (edges[:-1] + edges[1:])
        total = float(np.sum(counts))
        total_counts_display = counts.astype(np.float64, copy=False)
        if not window._histogram_log_y_enabled and total > 0.0:
            total_counts_display = total_counts_display / total * 100.0
        if window._histogram_log_y_enabled:
            total_counts_display = np.clip(total_counts_display, y_floor, None)
        window.histogram_curve.setData(centers, total_counts_display)
        histogram_peak = float(np.max(total_counts_display)) if total_counts_display.size else 1.0
        histogram_peak = max(
            histogram_peak,
            self.update_area_histogram_curves(
                edges=edges,
                spot_values=spot_values,
                ring_values=ring_values,
                mask_values=mask_values,
                total_pixels=float(values.size),
                total_counts_display=total_counts_display,
            ),
        )
        if window._histogram_log_y_enabled:
            y_min = float(np.log10(window.HISTOGRAM_LOG_Y_FLOOR))
            y_max = max(float(np.log10(histogram_peak)), y_min)
            window.histogram_plot.setLimits(yMin=y_min, yMax=y_max)
            view_box.setRange(yRange=(y_min, y_max), padding=0.0, disableAutoRange=True)
        else:
            window.histogram_plot.setYRange(y_floor, max(histogram_peak * 1.05, 1.0), padding=0.0)
        window.histogram_plot.setXRange(float(edges[0]), float(edges[-1]), padding=0.0)
        min_value = window.HISTOGRAM_MIN_INTENSITY if window._state.spot_detection.intensity_min_value is None else float(window._state.spot_detection.intensity_min_value)
        max_value = window.HISTOGRAM_MAX_INTENSITY if window._state.spot_detection.intensity_max_value is None else float(window._state.spot_detection.intensity_max_value)
        if min_value > max_value:
            min_value, max_value = max_value, min_value
        min_value = float(np.clip(min_value, window.HISTOGRAM_MIN_INTENSITY, window.HISTOGRAM_MAX_INTENSITY))
        max_value = float(np.clip(max_value, window.HISTOGRAM_MIN_INTENSITY, window.HISTOGRAM_MAX_INTENSITY))
        window.hist_region.blockSignals(True)
        window.hist_region.setRegion((min_value, max_value))
        window.hist_region.blockSignals(False)
        window.ignore_region.hide()
        window._update_histogram_region_labels()
        window._update_selected_intensity_overlay()

    def histogram_source_signature(self, image: np.ndarray) -> tuple[object, ...]:
        window = self._window
        spot_signature = window._spot_signature(window._display_spots())
        return (
            window._current_image_key,
            id(image),
            image.shape,
            spot_signature,
            round(float(window._state.spot_detection.ring_inner_radius_px), 3),
            round(float(window._state.spot_detection.ring_outer_radius_px), 3),
            bool(window._state.spot_detection.ignore_marked_pixels),
            window._external_mask_signature(),
        )

    def ignored_mask_signature(self, image: np.ndarray) -> tuple[object, ...]:
        window = self._window
        return (
            window._current_image_key,
            id(image),
            image.shape,
            window._mask_preview_signature(),
        )

    def ignored_mask(self, image: np.ndarray) -> np.ndarray:
        window = self._window
        signature = self.ignored_mask_signature(image)
        if window._ignored_mask_cache_signature == signature and window._ignored_mask_cache_value is not None:
            return window._ignored_mask_cache_value
        image_f32 = image.astype(np.float32, copy=False)
        ignored_mask = ignored_pixel_mask(
            image_f32,
            window._state.spot_detection,
            external_mask=window._current_external_mask(),
        )
        window._ignored_mask_cache_signature = signature
        window._ignored_mask_cache_value = ignored_mask
        return ignored_mask

    def histogram_source_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        window = self._window
        signature = self.histogram_source_signature(image)
        if window._histogram_source_cache_signature == signature and window._histogram_source_cache_values is not None:
            return window._histogram_source_cache_values
        image_values = np.clip(
            image.astype(np.float32, copy=False).ravel(),
            window.HISTOGRAM_MIN_INTENSITY,
            window.HISTOGRAM_MAX_INTENSITY,
        )
        spot_values, ring_values, mask_values = self.roi_intensity_values(image)
        cached = (image_values, spot_values, ring_values, mask_values)
        window._histogram_source_cache_signature = signature
        window._histogram_source_cache_values = cached
        return cached

    def histogram_counts_from_values(self, values: np.ndarray, edges: np.ndarray) -> np.ndarray:
        if values.size == 0:
            return np.zeros(max(edges.size - 1, 0), dtype=np.float64)
        counts, _ = np.histogram(values, bins=edges)
        return counts.astype(np.float64, copy=False)

    def histogram_percent_from_values(
        self,
        values: np.ndarray,
        edges: np.ndarray,
        total_pixels: float,
    ) -> np.ndarray:
        window = self._window
        if values.size == 0:
            return np.zeros(max(edges.size - 1, 0), dtype=np.float64)
        clipped_values = np.clip(
            values,
            window.HISTOGRAM_MIN_INTENSITY,
            window.HISTOGRAM_MAX_INTENSITY,
        )
        counts, _ = np.histogram(clipped_values, bins=edges)
        counts_percent = counts.astype(np.float64, copy=False)
        if total_pixels > 0.0:
            counts_percent = counts_percent / total_pixels * 100.0
        return counts_percent

    def update_area_histogram_curves(
        self,
        *,
        edges: np.ndarray,
        spot_values: np.ndarray,
        ring_values: np.ndarray,
        mask_values: np.ndarray,
        total_pixels: float,
        total_counts_display: np.ndarray,
    ) -> float:
        window = self._window
        y_floor = window.HISTOGRAM_LOG_Y_FLOOR if window._histogram_log_y_enabled else 0.0
        if window._histogram_log_y_enabled:
            spot_counts_percent = self.histogram_counts_from_values(spot_values, edges)
            ring_counts_percent = self.histogram_counts_from_values(ring_values, edges)
            mask_counts_percent = self.histogram_counts_from_values(mask_values, edges)
        else:
            spot_counts_percent = self.histogram_percent_from_values(spot_values, edges, total_pixels)
            ring_counts_percent = self.histogram_percent_from_values(ring_values, edges, total_pixels)
            mask_counts_percent = self.histogram_percent_from_values(mask_values, edges, total_pixels)
        if window._histogram_log_y_enabled:
            spot_counts_percent = np.clip(spot_counts_percent, y_floor, None)
            ring_counts_percent = np.clip(ring_counts_percent, y_floor, None)
            mask_counts_percent = np.clip(mask_counts_percent, y_floor, None)

        self.set_histogram_curve_data(window.spot_histogram_curve, edges, spot_counts_percent, window._spot_visual_color, window._spot_alpha)
        self.set_histogram_curve_data(window.ring_histogram_curve, edges, ring_counts_percent, window._ring_visual_color, window._ring_alpha)
        self.set_histogram_curve_data(window.mask_histogram_curve, edges, mask_counts_percent, window._mask_visual_color, window._mask_alpha)

        component_counts_percent = np.clip(
            spot_counts_percent + ring_counts_percent + mask_counts_percent,
            0.0,
            total_counts_display,
        )
        residual_counts_percent = np.clip(total_counts_display - component_counts_percent, 0.0, None)
        if window._histogram_log_y_enabled:
            residual_counts_percent = np.clip(residual_counts_percent, y_floor, None)
        top_baseline = float(np.max(total_counts_display)) if total_counts_display.size else 0.0
        residual_pen = QColor("#9ca3af")
        residual_pen.setAlphaF(0.95)
        residual_fill = QColor("#9ca3af")
        residual_fill.setAlphaF(0.24)
        if window._histogram_log_y_enabled or residual_counts_percent.size == 0:
            window.residual_histogram_curve.setData([], [])
            if hasattr(window, "residual_fill_item"):
                window.residual_fill_item.setVisible(False)
        else:
            centers = 0.5 * (edges[:-1] + edges[1:])
            residual_display = top_baseline - residual_counts_percent
            window.residual_histogram_curve.setData(
                centers,
                residual_display,
                fillLevel=top_baseline,
                pen=pg.mkPen(residual_pen, width=2.2),
                brush=pg.mkBrush(residual_fill),
            )
            if hasattr(window, "residual_fill_item"):
                window.residual_fill_item.setVisible(True)
        return float(
            max(
                np.max(spot_counts_percent) if spot_counts_percent.size else 0.0,
                np.max(ring_counts_percent) if ring_counts_percent.size else 0.0,
                np.max(mask_counts_percent) if mask_counts_percent.size else 0.0,
                np.max(total_counts_display) if total_counts_display.size else 0.0,
            )
        )

    def set_histogram_curve_data(
        self,
        curve: pg.PlotDataItem,
        edges: np.ndarray,
        counts_percent: np.ndarray,
        color: QColor,
        alpha: float,
    ) -> None:
        window = self._window
        if counts_percent.size == 0:
            curve.setData([], [])
            return
        pen_color = QColor(color)
        pen_color.setAlphaF(window._alpha01(max(alpha, 0.85)))
        centers = 0.5 * (edges[:-1] + edges[1:])
        if window._histogram_log_y_enabled:
            curve.setData(centers, counts_percent, pen=pg.mkPen(pen_color, width=2.2))
        else:
            fill_color = QColor(color)
            fill_color.setAlphaF(window._alpha01(max(alpha * 0.32, 0.10)))
            curve.setData(centers, counts_percent, fillLevel=0.0, pen=pg.mkPen(pen_color, width=2.2), brush=pg.mkBrush(fill_color))
        self.update_area_histogram_peak_labels()

    def restyle_histogram_curve(
        self,
        curve: pg.PlotDataItem,
        *,
        color: QColor,
        alpha: float,
        fill_level: float = 0.0,
    ) -> None:
        window = self._window
        x_data, y_data = curve.getData()
        if x_data is None or y_data is None or len(x_data) == 0 or len(y_data) == 0:
            return
        pen_color = QColor(color)
        pen_color.setAlphaF(window._alpha01(max(alpha, 0.85)))
        if window._histogram_log_y_enabled:
            curve.setData(x_data, y_data, pen=pg.mkPen(pen_color, width=2.2))
        else:
            fill_color = QColor(color)
            fill_color.setAlphaF(window._alpha01(max(alpha * 0.32, 0.10)))
            curve.setData(
                x_data,
                y_data,
                fillLevel=fill_level,
                pen=pg.mkPen(pen_color, width=2.2),
                brush=pg.mkBrush(fill_color),
            )

    def restyle_area_histogram_curves(self) -> None:
        window = self._window
        self.restyle_histogram_curve(window.spot_histogram_curve, color=window._spot_visual_color, alpha=window._spot_alpha)
        self.restyle_histogram_curve(window.ring_histogram_curve, color=window._ring_visual_color, alpha=window._ring_alpha)
        self.restyle_histogram_curve(window.mask_histogram_curve, color=window._mask_visual_color, alpha=window._mask_alpha)
        self.update_area_histogram_peak_labels()

    def update_area_histogram_peak_labels(self) -> None:
        window = self._window
        self.update_area_histogram_peak_label(window.spot_histogram_curve, window.spot_histogram_label, "Spots", window._spot_visual_color)
        self.update_area_histogram_peak_label(window.ring_histogram_curve, window.ring_histogram_label, "Ref. rings", window._ring_visual_color)

    def update_area_histogram_peak_label(
        self,
        curve: pg.PlotDataItem,
        label: pg.TextItem,
        text: str,
        color: QColor,
    ) -> None:
        window = self._window
        x_data, y_data = curve.getData()
        if x_data is None or y_data is None or len(x_data) == 0 or len(y_data) == 0:
            label.hide()
            return
        y_values = np.asarray(y_data, dtype=np.float64)
        if y_values.size == 0:
            label.hide()
            return
        peak_index = int(np.argmax(y_values))
        peak_y = float(y_values[peak_index])
        if peak_y <= 0.0:
            label.hide()
            return
        x_values = np.asarray(x_data, dtype=np.float64)
        peak_x = float(x_values[peak_index])
        view_box = window.histogram_plot.getViewBox()
        _x_range, y_range = view_box.viewRange()
        y_span = max(float(y_range[1] - y_range[0]), 1.0)
        label.setText(text, color=color)
        label.setPos(peak_x, peak_y + max(y_span * 0.025, 0.08))
        label.show()

    def roi_area_masks(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        window = self._window
        signature = self.histogram_source_signature(image)
        if window._roi_mask_cache_signature == signature and window._roi_mask_cache_values is not None:
            return window._roi_mask_cache_values
        image_f32 = image.astype(np.float32, copy=False)
        image_height, image_width = image_f32.shape[:2]
        ignored_mask = self.ignored_mask(image_f32)
        spot_mask = np.zeros((image_height, image_width), dtype=bool)
        ring_mask = np.zeros((image_height, image_width), dtype=bool)
        display_spots = window._display_spots()
        if display_spots:
            affine_matrix = window._chromatic_affine_for_image_key(window._current_image_key)
            ring_inner_radius = float(max(window._state.spot_detection.ring_inner_radius_px, 0.0))
            ring_outer_radius = float(max(window._state.spot_detection.ring_outer_radius_px, ring_inner_radius))
            if affine_matrix is None or window._is_current_reference_image():
                yy, xx = np.indices((image_height, image_width), dtype=np.float32)
                for spot in display_spots:
                    distance_sq = (xx - float(spot.center_x)) ** 2 + (yy - float(spot.center_y)) ** 2
                    spot_mask |= distance_sq <= float(spot.radius_px) ** 2
                    if ring_outer_radius > 0.0:
                        outer_mask = distance_sq <= ring_outer_radius ** 2
                        inner_mask = distance_sq < ring_inner_radius ** 2 if ring_inner_radius > 0.0 else np.zeros_like(outer_mask)
                        ring_mask |= outer_mask & ~inner_mask
            else:
                source_spot_map = {spot.spot_id: spot for spot in window._state.detected_spots}
                for spot in display_spots:
                    source_spot = source_spot_map.get(spot.spot_id, spot)
                    spot_mask |= transformed_disk_mask(
                        (image_height, image_width),
                        (float(source_spot.center_x), float(source_spot.center_y)),
                        float(source_spot.radius_px),
                        affine_matrix,
                    )
                    if ring_outer_radius > 0.0:
                        ring_mask |= transformed_annulus_mask(
                            (image_height, image_width),
                            (float(source_spot.center_x), float(source_spot.center_y)),
                            float(ring_inner_radius),
                            float(ring_outer_radius),
                            affine_matrix,
                        )
        spot_mask &= ~ignored_mask
        ring_mask &= ~ignored_mask
        ring_mask &= ~spot_mask
        residual_mask = ~(spot_mask | ring_mask | ignored_mask)
        cached = (spot_mask, ring_mask, ignored_mask, residual_mask)
        window._roi_mask_cache_signature = signature
        window._roi_mask_cache_values = cached
        return cached

    def roi_intensity_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        image_f32 = image.astype(np.float32, copy=False)
        spot_mask, ring_mask, ignored_mask, _residual_mask = self.roi_area_masks(image_f32)
        return (
            image_f32[spot_mask],
            image_f32[ring_mask],
            image_f32[ignored_mask],
        )
