from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from lspr_ui import get_active_theme

from lspr_imaging_app.domain.models import AbsorbanceSpectrumResult, FitResult
from lspr_imaging_app.processing.analysis import fit_curve_for_method
from lspr_imaging_app.processing.chromatic import transformed_annulus_mask, transformed_disk_mask
from lspr_imaging_app.processing.roi_detection import ignored_pixel_mask
from lspr_imaging_app.gui.roi_overlay_helpers import resolved_roi_color


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
            getattr(window, "roi_histogram_curve", None),
            getattr(window, "reference_histogram_curve", None),
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
        self._window.spectrum_plot.setTitle(text, color=get_active_theme().text_muted, size="9pt")

    def clear_spectrum_summary_text(self) -> None:
        self._window.spectrum_plot.setTitle(None)

    def clear_absorbance_spectrum(self) -> None:
        self.clear_spectrum_series_items()
        self._window.spectrum_current_point.setData([], [])
        self._window.spectrum_metric_point.setData([], [])
        self._window._absorbance_spectrum_dirty = True
        self._window.spectrum_plot.setTitle(None)

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

    def roi_spectrum_color(self, roi_id: int) -> QColor:
        window = self._window
        roi = next((roi for roi in window._state.area_rois if int(roi.area_roi_id) == int(roi_id)), None)
        group = window._group_for_roi(int(roi_id)) if roi is not None else None
        return QColor(resolved_roi_color(roi, group, window._sample_visual_color) if roi is not None else window._sample_visual_color)

    def analysis_fit_result_from_spectrum(self, result: AbsorbanceSpectrumResult) -> FitResult | None:
        if self._window._analysis_fit_method_key() == "none":
            return None
        wavelength_range = self._window._analysis_wavelength_range()
        fit = fit_curve_for_method(
            result.wavelengths_nm,
            result.formula_values,
            self._window._analysis_fit_method_key(),
            poly_order=self._window._analysis_poly_order(),
            wl_min=wavelength_range[0] if wavelength_range is not None else None,
            wl_max=wavelength_range[1] if wavelength_range is not None else None,
        )
        if fit.fitted_wavelengths_nm.size == 0 or fit.fitted_values.size == 0:
            return None
        return fit

    def add_spectrum_series(
        self,
        *,
        roi_id: int,
        result: AbsorbanceSpectrumResult,
        label: str | None = None,
        highlighted: bool = False,
        dimmed: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None] | None:
        window = self._window
        wavelengths = np.asarray(result.wavelengths_nm, dtype=np.float64)
        formula_values = np.asarray(result.formula_values, dtype=np.float64)
        valid_mask = np.isfinite(wavelengths) & np.isfinite(formula_values)
        if not np.any(valid_mask):
            return None
        x_values = wavelengths[valid_mask]
        y_values = formula_values[valid_mask]
        order = np.argsort(x_values)
        x_values = x_values[order]
        y_values = y_values[order]
        color = self.roi_spectrum_color(roi_id)
        fit_color = QColor(color).darker(125)
        base_symbol_size = float(getattr(window, "_spectrum_symbol_size_px", 6.0))
        base_fit_width = float(getattr(window, "_spectrum_fit_line_width_px", 2.0))
        fit_style = getattr(window, "_spectrum_fit_line_style", Qt.PenStyle.SolidLine)
        symbol_size = base_symbol_size + 1.5 if highlighted else base_symbol_size
        fit_width = base_fit_width + 1.0 if highlighted else base_fit_width
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
        if fit is not None and fit.fitted_wavelengths_nm.size and fit.fitted_values.size:
            fit_item = window.spectrum_plot.plot(
                fit.fitted_wavelengths_nm,
                fit.fitted_values,
                pen=pg.mkPen(
                    fit_color if not highlighted else fit_symbol_color,
                    width=fit_width,
                    style=fit_style,
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
                        style=fit_style,
                    ) if fit_item is not None else None,
                    symbol="o",
                    symbolSize=symbol_size,
                    symbolBrush=pg.mkBrush(symbol_color),
                    symbolPen=pg.mkPen(color, width=symbol_pen_width),
                )
                window.spectrum_legend.addItem(legend_sample, f"{label or f'ROI {int(roi_id)}'}")
            except Exception:
                pass
        return (
            x_values,
            y_values,
            fit.fitted_wavelengths_nm if fit is not None else None,
            fit.fitted_values if fit is not None else None,
        )

    def set_sensorgram_series(
        self,
        spectral_cube_indices,
        metric_values,
        *,
        summary_text: str | None = None,
    ) -> None:
        self._window._analysis_controller.set_sensorgram_series(spectral_cube_indices, metric_values, summary_text=summary_text)

    def update_sensorgram_current_point(self) -> None:
        self._window._analysis_controller.update_current_point()

    def update_single_spectral_cube_sensorgram(self, metric_value: float | None, metric_signal: float | None) -> None:
        window = self._window
        if window._sensorgram_running or window._sensorgram_spectral_cube_indices.size > 1:
            self.update_sensorgram_current_point()
            return
        spectral_cube_index = window._current_spectral_cube()
        if spectral_cube_index is None or metric_value is None or not np.isfinite(metric_value):
            self.clear_sensorgram("Calculate all spectral cubes to build the fitted sensorgram.")
            return
        signal_value = float(metric_signal) if metric_signal is not None and np.isfinite(metric_signal) else float("nan")
        window._sensorgram_metric_signal = np.asarray([signal_value], dtype=np.float64)
        self.set_sensorgram_series(
            [int(spectral_cube_index)],
            [float(metric_value)],
            summary_text=(
                f"{window._analysis_metric_label()} | Spectral cube {int(spectral_cube_index)} = {float(metric_value):.3f} nm"
                f"{window._poly_order_summary_suffix()}"
            ),
        )

    def clear_sensorgram(self, summary_text: str) -> None:
        self._window._analysis_controller.clear_sensorgram(summary_text)

    def update_histogram(self, image: np.ndarray) -> None:
        window = self._window
        values, sample_values, reference_values, mask_values = self.histogram_source_values(image)
        y_floor = window.HISTOGRAM_LOG_Y_FLOOR if window._histogram_log_y_enabled else 0.0
        view_box = window.histogram_plot.getViewBox()
        if window._histogram_log_y_enabled:
            view_box.disableAutoRange(axis=view_box.YAxis)
        if values.size == 0:
            window.histogram_curve.setData([], [])
            window.roi_histogram_curve.setData([], [])
            window.reference_histogram_curve.setData([], [])
            window.mask_histogram_curve.setData([], [])
            window.residual_histogram_curve.setData([], [])
            window.hist_region_label.hide()
            window.ignore_region_label.hide()
            window.roi_histogram_label.hide()
            window.reference_histogram_label.hide()
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
                sample_values=sample_values,
                reference_values=reference_values,
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
        min_value = window.HISTOGRAM_MIN_INTENSITY if window._state.area_roi_settings.intensity_min_value is None else float(window._state.area_roi_settings.intensity_min_value)
        max_value = window.HISTOGRAM_MAX_INTENSITY if window._state.area_roi_settings.intensity_max_value is None else float(window._state.area_roi_settings.intensity_max_value)
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
        roi_signature_for_preprocessing = window._roi_signature(window._display_rois())
        return (
            window._current_image_key,
            id(image),
            image.shape,
            roi_signature_for_preprocessing,
            round(float(window._state.area_roi_settings.reference_inner_radius_px), 3),
            round(float(window._state.area_roi_settings.reference_outer_radius_px), 3),
            bool(window._state.area_roi_settings.ignore_marked_pixels),
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
            window._state.area_roi_settings,
            external_mask=window._current_external_mask(),
            rotation_fill_mask=window._rotation_fill_mask(),
        )
        window._ignored_mask_cache_signature = signature
        window._ignored_mask_cache_value = ignored_mask
        return ignored_mask

    def histogram_source_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        window = self._window
        signature = self.histogram_source_signature(image)
        if window._histogram_source_cache_signature == signature and window._histogram_source_cache_values is not None:
            return window._histogram_source_cache_values
        image_f32 = image.astype(np.float32, copy=False)
        # Excludes rotation-fill padding unconditionally, plus any painted/
        # histogram/relative/local-contrast mask when "ignore marked pixels"
        # is on (see ignored_pixel_mask) - previously this curve ravel()'d
        # the whole image regardless, so rotation-fill corners (and, more
        # generally, anything the user had masked) always showed up here
        # even though ROI statistics already excluded them.
        excluded_mask = self.ignored_mask(image_f32)
        source_values = image_f32[~excluded_mask] if excluded_mask.shape == image_f32.shape[:2] else image_f32.ravel()
        image_values = np.clip(
            source_values,
            window.HISTOGRAM_MIN_INTENSITY,
            window.HISTOGRAM_MAX_INTENSITY,
        )
        sample_values, reference_values, mask_values = self.roi_intensity_values(image)
        cached = (image_values, sample_values, reference_values, mask_values)
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
        sample_values: np.ndarray,
        reference_values: np.ndarray,
        mask_values: np.ndarray,
        total_pixels: float,
        total_counts_display: np.ndarray,
    ) -> float:
        window = self._window
        y_floor = window.HISTOGRAM_LOG_Y_FLOOR if window._histogram_log_y_enabled else 0.0
        if window._histogram_log_y_enabled:
            sample_counts_percent = self.histogram_counts_from_values(sample_values, edges)
            reference_counts_percent = self.histogram_counts_from_values(reference_values, edges)
            mask_counts_percent = self.histogram_counts_from_values(mask_values, edges)
        else:
            sample_counts_percent = self.histogram_percent_from_values(sample_values, edges, total_pixels)
            reference_counts_percent = self.histogram_percent_from_values(reference_values, edges, total_pixels)
            mask_counts_percent = self.histogram_percent_from_values(mask_values, edges, total_pixels)
        if window._histogram_log_y_enabled:
            sample_counts_percent = np.clip(sample_counts_percent, y_floor, None)
            reference_counts_percent = np.clip(reference_counts_percent, y_floor, None)
            mask_counts_percent = np.clip(mask_counts_percent, y_floor, None)

        self.set_histogram_curve_data(window.roi_histogram_curve, edges, sample_counts_percent, window._sample_visual_color, window._roi_alpha)
        self.set_histogram_curve_data(window.reference_histogram_curve, edges, reference_counts_percent, window._reference_visual_color, window._reference_alpha)
        self.set_histogram_curve_data(window.mask_histogram_curve, edges, mask_counts_percent, window._mask_visual_color, window._mask_alpha)

        component_counts_percent = np.clip(
            sample_counts_percent + reference_counts_percent + mask_counts_percent,
            0.0,
            total_counts_display,
        )
        residual_counts_percent = np.clip(total_counts_display - component_counts_percent, 0.0, None)
        if window._histogram_log_y_enabled:
            residual_counts_percent = np.clip(residual_counts_percent, y_floor, None)
        top_baseline = float(np.max(total_counts_display)) if total_counts_display.size else 0.0
        residual_pen = QColor(get_active_theme().text_dim)
        residual_pen.setAlphaF(0.95)
        residual_fill = QColor(get_active_theme().text_dim)
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
                np.max(sample_counts_percent) if sample_counts_percent.size else 0.0,
                np.max(reference_counts_percent) if reference_counts_percent.size else 0.0,
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
        self.restyle_histogram_curve(window.roi_histogram_curve, color=window._sample_visual_color, alpha=window._roi_alpha)
        self.restyle_histogram_curve(window.reference_histogram_curve, color=window._reference_visual_color, alpha=window._reference_alpha)
        self.restyle_histogram_curve(window.mask_histogram_curve, color=window._mask_visual_color, alpha=window._mask_alpha)
        self.update_area_histogram_peak_labels()

    def update_area_histogram_peak_labels(self) -> None:
        window = self._window
        self.update_area_histogram_peak_label(window.roi_histogram_curve, window.roi_histogram_label, "ROIs", window._sample_visual_color)
        self.update_area_histogram_peak_label(window.reference_histogram_curve, window.reference_histogram_label, "Reference ROIs", window._reference_visual_color)

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
        sample_mask = np.zeros((image_height, image_width), dtype=bool)
        reference_mask = np.zeros((image_height, image_width), dtype=bool)
        display_rois = window._display_rois()
        if display_rois:
            affine_matrix = window._chromatic_affine_for_image_key(window._current_image_key)
            reference_inner_radius = float(max(window._state.area_roi_settings.reference_inner_radius_px, 0.0))
            reference_outer_radius = float(max(window._state.area_roi_settings.reference_outer_radius_px, reference_inner_radius))
            if affine_matrix is None or window._is_current_reference_image():
                # Per-ROI bounding box, not a full-image np.indices() grid
                # evaluated 160 times: nothing outside `radius_ceil` of a
                # ROI's center can ever satisfy either distance test below,
                # so restricting the float32 distance computation (same
                # dtype/arithmetic as the old full-grid version, so results
                # are unchanged bit-for-bit) to that box - typically a few
                # thousand pixels instead of the image's ~1.17M - produces an
                # identical mask far faster. Same technique as
                # preprocess._roi_exclusion_mask. This branch alone was
                # measured costing ~1.2s of every wavelength switch's ~1.5s
                # total (see PlotManager.update_histogram -> roi_area_masks).
                for roi in display_rois:
                    box_radius = max(float(roi.sample_radius_px), reference_outer_radius)
                    radius_ceil = int(np.ceil(box_radius)) + 1
                    center_x, center_y = float(roi.center_x), float(roi.center_y)
                    x0 = max(int(np.floor(center_x)) - radius_ceil, 0)
                    x1 = min(int(np.floor(center_x)) + radius_ceil + 1, image_width)
                    y0 = max(int(np.floor(center_y)) - radius_ceil, 0)
                    y1 = min(int(np.floor(center_y)) + radius_ceil + 1, image_height)
                    if x0 >= x1 or y0 >= y1:
                        continue
                    local_yy, local_xx = np.ogrid[y0:y1, x0:x1]
                    local_xx = local_xx.astype(np.float32)
                    local_yy = local_yy.astype(np.float32)
                    distance_sq = (local_xx - center_x) ** 2 + (local_yy - center_y) ** 2
                    sample_mask[y0:y1, x0:x1] |= distance_sq <= float(roi.sample_radius_px) ** 2
                    if reference_outer_radius > 0.0:
                        outer_mask = distance_sq <= reference_outer_radius ** 2
                        inner_mask = distance_sq < reference_inner_radius ** 2 if reference_inner_radius > 0.0 else np.zeros_like(outer_mask)
                        reference_mask[y0:y1, x0:x1] |= outer_mask & ~inner_mask
            else:
                source_roi_map = {roi.area_roi_id: roi for roi in window._state.area_rois}
                for roi in display_rois:
                    source_roi = source_roi_map.get(roi.area_roi_id, roi)
                    sample_mask |= transformed_disk_mask(
                        (image_height, image_width),
                        (float(source_roi.center_x), float(source_roi.center_y)),
                        float(source_roi.sample_radius_px),
                        affine_matrix,
                    )
                    if reference_outer_radius > 0.0:
                        reference_mask |= transformed_annulus_mask(
                            (image_height, image_width),
                            (float(source_roi.center_x), float(source_roi.center_y)),
                            float(reference_inner_radius),
                            float(reference_outer_radius),
                            affine_matrix,
                        )
        sample_mask &= ~ignored_mask
        reference_mask &= ~ignored_mask
        reference_mask &= ~sample_mask
        residual_mask = ~(sample_mask | reference_mask | ignored_mask)
        cached = (sample_mask, reference_mask, ignored_mask, residual_mask)
        window._roi_mask_cache_signature = signature
        window._roi_mask_cache_values = cached
        return cached

    def roi_intensity_values(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        image_f32 = image.astype(np.float32, copy=False)
        sample_mask, reference_mask, ignored_mask, _residual_mask = self.roi_area_masks(image_f32)
        return (
            image_f32[sample_mask],
            image_f32[reference_mask],
            image_f32[ignored_mask],
        )
