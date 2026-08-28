from __future__ import annotations

import time
import numpy as np
from copy import deepcopy
from datetime import datetime
from math import ceil, floor
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
import pyqtgraph as pg

from lspr_ui import get_active_theme

from lspr_imaging_app.domain.exclusions import is_cube_fully_excluded
from lspr_imaging_app.domain.models import AreaRoi
from lspr_imaging_app.gui.worker import SensorgramComputationResult
from lspr_imaging_app.gui.analysis_result_serialization import (
    serialize_formula_spectrum_result,
    deserialize_formula_spectrum_result,
    serialize_sensorgram_result,
    deserialize_sensorgram_result,
)
from lspr_imaging_app.gui.analysis_cache_signature import (
    analysis_cache_signature_to_json,
    analysis_cache_signature_from_json,
    signature_hash,
    formula_spectral_cube_signature,
    formula_spectrum_result_covers_roi_ids,
)
from lspr_imaging_app.processing.trace_statistics import (
    aggregate_group_traces,
    normalize_to_baseline_window,
    reject_spikes_hampel,
    reject_spikes_running_median,
    smooth_moving_average,
    smooth_savgol,
)
from lspr_imaging_app.gui.analysis_chromatic_geometry_mixin import AnalysisChromaticGeometryMixin
from lspr_imaging_app.gui.analysis_worker_mixin import AnalysisWorkerMixin


_FORMULA_AXIS_LABELS: dict[str, str] = {
    "absorbance": "Absorbance (A = -log10(Is/Ir))",
    "ratio": "Ratio (Is/Ir)",
    "relative_change": "Relative change ((Ir-Is)/Ir)",
    "mod_absorbance": "mOD Absorbance (-1000 x log10(Is/Ir))",
}


class AnalysisController(AnalysisWorkerMixin, AnalysisChromaticGeometryMixin):
    def __init__(self, window) -> None:
        self.window = window

    def _sensorgram_selection_color(self) -> QColor:
        selected_roi_ids = tuple(self.window._selected_spectrum_roi_ids())
        if len(selected_roi_ids) == 1:
            return QColor(self.window._roi_spectrum_color(int(selected_roi_ids[0])))
        if selected_roi_ids:
            return QColor("#38bdf8")
        return QColor("#22c55e")

    def update_selection_highlight(self, *, force: bool = False) -> None:
        selected_signature = tuple(self.window._selected_spectrum_roi_ids())
        if not force and selected_signature == getattr(self.window, "_sensorgram_selection_highlight_signature", None):
            return
        self.window._sensorgram_selection_highlight_signature = selected_signature
        has_data = self.window._sensorgram_spectral_cube_indices.size > 0
        selected = bool(selected_signature) and has_data
        color = self._sensorgram_selection_color()
        curve_color = color if selected else QColor("#22c55e")
        base_width = float(getattr(self.window, "_sensorgram_line_width_px", 2.2))
        curve_width = base_width + 1.0 if selected else base_width
        curve_style = getattr(self.window, "_sensorgram_line_style", Qt.PenStyle.SolidLine)
        curve_symbol_size = 7.5 if selected else 6
        point_size = 11 if selected else 9
        point_brush = pg.mkBrush(color.lighter(138) if selected else get_active_theme().text_primary)
        point_pen = pg.mkPen(color.darker(125) if selected else "#22c55e", width=2.0)
        self.window.sensorgram_curve.setPen(pg.mkPen(curve_color, width=curve_width, style=curve_style))
        self.window.sensorgram_curve.setSymbolSize(curve_symbol_size)
        self.window.sensorgram_curve.setSymbolBrush(pg.mkBrush(color.lighter(130) if selected else "#22c55e"))
        self.window.sensorgram_curve.setSymbolPen(pg.mkPen(curve_color.darker(120) if selected else "#bbf7d0", width=1.4))
        self.window.sensorgram_current_point.setSymbolSize(point_size)
        self.window.sensorgram_current_point.setSymbolBrush(point_brush)
        self.window.sensorgram_current_point.setSymbolPen(point_pen)

    def apply_sensorgram_style_settings(self) -> None:
        """Re-pen the sensogram's statistics overlays (processed/group
        traces) from window._sensorgram_processed_*/_sensorgram_group_*, and
        refresh the raw trace via update_selection_highlight so its width/
        style pick up the new _sensorgram_line_width_px/_line_style. Called
        after the plot settings dialog's Apply/OK - see
        plot_style_settings_dialog.SensorgramPlotSettingsDialog."""
        window = self.window
        window.sensorgram_processed_curve.setPen(
            pg.mkPen(
                window._sensorgram_processed_color,
                width=window._sensorgram_processed_line_width_px,
                style=window._sensorgram_processed_line_style,
            )
        )
        group_color = QColor(window._sensorgram_group_color)
        window.sensorgram_group_curve.setPen(
            pg.mkPen(group_color, width=window._sensorgram_group_line_width_px, style=window._sensorgram_group_line_style)
        )
        window.sensorgram_group_curve.setSymbolBrush(pg.mkBrush(group_color))
        window.sensorgram_group_curve.setSymbolPen(pg.mkPen(group_color.lighter(150), width=1.0))
        band_color = QColor(group_color)
        band_color.setAlpha(50)
        window.sensorgram_group_band_fill_item.setBrush(pg.mkBrush(band_color))
        self.update_selection_highlight(force=True)

    def on_fit_settings_changed(self, *_args) -> None:
        self.window._on_analysis_fit_settings_changed(*_args)

    def on_spectral_cube_range_changed(self, *_args) -> None:
        self.window._on_analysis_spectral_cube_range_changed(*_args)

    def on_wavelength_range_changed(self, *_args) -> None:
        self._on_analysis_wavelength_range_changed(*_args)

    def update_plot_labels(self) -> None:
        self.window._update_sensorgram_plot_labels()

    def _sensorgram_time_mode_metadata(self):
        """The loaded `ImagingAcquisitionMetadata` if it has per-image timing
        data (enough to plot the sensorgram against real elapsed time),
        otherwise None - falls back to plotting by raw spectral_cube_index,
        the behavior for every dataset without acquisition metadata loaded."""
        dataset = self.window._state.dataset
        metadata = getattr(dataset, "acquisition_metadata", None) if dataset is not None else None
        if metadata is None or not metadata.image_timings:
            return None
        return metadata

    @staticmethod
    def _sensorgram_time_anchor_ms(metadata) -> int:
        """t=0 for the elapsed-time axis: the dataset's recorded start time
        if parseable (consistent with how comment-event/image timestamps are
        already anchored), otherwise the earliest recorded image timing."""
        if metadata.started_at_utc:
            try:
                return int(datetime.fromisoformat(metadata.started_at_utc.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                pass
        return min(timing.acquired_at_unix_ms for timing in metadata.image_timings)

    def _acquisition_timing_index(
        self, metadata
    ) -> tuple[dict[int, object], dict[int, object], dict[tuple[int, float], object]]:
        """(per_cube_earliest, per_cube_latest, per_frame) built together in
        one pass over `metadata.image_timings`, cached on the window keyed by
        id(metadata) - a freshly loaded/reloaded dataset always gets a new
        metadata object, so this self-invalidates without needing an
        explicit clear call.

        `per_cube_earliest`/`per_cube_latest`: {spectral_cube_index: first/
        last ImagingCubeTiming for that cube} - one representative timestamp
        per cube (when its sweep began/ended), the raw material for
        resolving the Cube/Time toggle's timestamp rule (see
        `_cube_timestamp_ms_by_cube_index`).

        `per_frame`: {(spectral_cube_index, wavelength_nm): ImagingCubeTiming}
        - the full per-image timing, for anything asking "when was this
        exact frame taken" (the "This image acquired" status label).

        These replace linear scans that used to run on every wavelength
        switch: `ImagingCubeTiming.earliest_timing_for_cube()` and
        `.timing_for()` (packages/lspr_core) each re-scan the *entire*
        `image_timings` list - O(wavelengths x cubes) - per call, since
        `image_timings` is flat per-(cube, wavelength), not indexed by
        either key. `_sensorgram_axis_range` used to call
        `earliest_timing_for_cube()` once per spectral cube in the whole
        dataset every switch, making that one call O(cubes^2 x wavelengths) -
        measured at ~190ms on a 200-cube x 65-wavelength dataset (13,000
        timing entries), matching almost exactly a "chromatic"-labeled stage
        cost seen on a real slow-switch log (mislabeled - the actual cost was
        here, not chromatic sync). This builds all three dicts with one
        O(cubes x wavelengths) pass instead, without touching lspr_core (a
        separate, shared package the acquisition app also depends on -
        changing its model wasn't necessary here)."""
        window = self.window
        cache = getattr(window, "_acquisition_timing_index_cache", None)
        if cache is not None and cache[0] == id(metadata):
            return cache[1], cache[2], cache[3]
        per_cube_earliest: dict[int, object] = {}
        per_cube_latest: dict[int, object] = {}
        per_frame: dict[tuple[int, float], object] = {}
        for timing in metadata.image_timings:
            cube_index = int(timing.spectral_cube_index)
            wavelength_nm = float(timing.wavelength_nm)
            per_frame[(cube_index, wavelength_nm)] = timing
            earliest = per_cube_earliest.get(cube_index)
            if earliest is None or timing.acquired_at_unix_ms < earliest.acquired_at_unix_ms:
                per_cube_earliest[cube_index] = timing
            latest = per_cube_latest.get(cube_index)
            if latest is None or timing.acquired_at_unix_ms > latest.acquired_at_unix_ms:
                per_cube_latest[cube_index] = timing
        window._acquisition_timing_index_cache = (id(metadata), per_cube_earliest, per_cube_latest, per_frame)
        return per_cube_earliest, per_cube_latest, per_frame

    def _earliest_timing_by_cube_index(self, metadata) -> dict[int, object]:
        per_cube_earliest, _per_cube_latest, _per_frame = self._acquisition_timing_index(metadata)
        return per_cube_earliest

    def _latest_timing_by_cube_index(self, metadata) -> dict[int, object]:
        _per_cube_earliest, per_cube_latest, _per_frame = self._acquisition_timing_index(metadata)
        return per_cube_latest

    def _cube_timestamp_ms_by_cube_index(self, metadata) -> dict[int, int]:
        """Resolves each cube's single representative timestamp (ms) per the
        active Cube/Time rule (`window._cube_time_timestamp_rule`): the
        first frame acquired in the cube's sweep, the last, or the midpoint
        between them. Used by both the Cube/Time spinbox display and the
        sensorgram x-axis so they stay consistent with whichever rule is
        selected. This is a display/plotting concern only - the timestamp
        actually written to the measurement-export backup
        (`_acquisition_timestamp_ms_for_cube`) intentionally always uses the
        earliest frame regardless of this rule, so switching a display
        preference never changes what gets persisted."""
        rule = getattr(self.window, "_cube_time_timestamp_rule", "first")
        per_cube_earliest, per_cube_latest, _per_frame = self._acquisition_timing_index(metadata)
        if rule == "last":
            return {cube_index: timing.acquired_at_unix_ms for cube_index, timing in per_cube_latest.items()}
        if rule == "midpoint":
            result: dict[int, int] = {}
            for cube_index, earliest in per_cube_earliest.items():
                latest = per_cube_latest.get(cube_index, earliest)
                result[cube_index] = int((earliest.acquired_at_unix_ms + latest.acquired_at_unix_ms) / 2)
            return result
        return {cube_index: timing.acquired_at_unix_ms for cube_index, timing in per_cube_earliest.items()}

    def _timing_for_frame(self, metadata, spectral_cube_index: int, wavelength_nm: float):
        """The exact per-image timing for one (cube, wavelength) frame, or
        None if this metadata has no entry for it - the indexed equivalent of
        `ImagingAcquisitionMetadata.timing_for()`, see
        `_acquisition_timing_index`'s docstring for why."""
        _per_cube_earliest, _per_cube_latest, per_frame = self._acquisition_timing_index(metadata)
        return per_frame.get((int(spectral_cube_index), float(wavelength_nm)))

    def _sensorgram_x_values(self, spectral_cube_indices) -> np.ndarray:
        """Map spectral cube indices to sensorgram x-axis display values:
        elapsed seconds since the dataset's start when acquisition metadata
        with per-image timing is loaded (NaN for any cube with no timing
        entry, so it's dropped rather than plotted at a misleading raw-index
        position), otherwise the raw cube indices themselves, unchanged from
        today's behavior."""
        indices = np.asarray(list(spectral_cube_indices), dtype=np.int64)
        metadata = self._sensorgram_time_mode_metadata()
        if metadata is None:
            return indices.astype(np.float64)
        anchor_ms = self._sensorgram_time_anchor_ms(metadata)
        timestamp_by_cube = self._cube_timestamp_ms_by_cube_index(metadata)
        values = np.full(indices.shape, np.nan, dtype=np.float64)
        for position, cube_index in enumerate(indices):
            timestamp_ms = timestamp_by_cube.get(int(cube_index))
            if timestamp_ms is not None:
                values[position] = (timestamp_ms - anchor_ms) / 1000.0
        return values

    def available_analysis_spectral_cubes(self) -> list[int]:
        return self.window._available_analysis_spectral_cubes()

    # ------------------------------------------------------------------
    # Cache / signature / serialization / payload-builder methods
    # (moved from MainWindow)
    # ------------------------------------------------------------------

    # Pure dict/dataclass<->JSON transforms - moved to analysis_result_serialization.py
    # (no self.window/Qt dependency). Kept as staticmethod attributes under
    # their original names since main_window.py already references them as
    # AnalysisController._method(...).
    _serialize_formula_spectrum_result = staticmethod(serialize_formula_spectrum_result)
    _deserialize_formula_spectrum_result = staticmethod(deserialize_formula_spectrum_result)
    _serialize_sensorgram_result = staticmethod(serialize_sensorgram_result)
    _deserialize_sensorgram_result = staticmethod(deserialize_sensorgram_result)

    # ------------------------------------------------------------------
    # Result / event handler methods (moved from MainWindow)
    # ------------------------------------------------------------------

    def _on_analysis_fit_settings_changed(self, *_args) -> None:
        start_time = time.perf_counter()
        self.window._save_control_preferences()
        if self.window._analysis_live_preview_enabled:
            self.window._schedule_sensorgram_refresh()
        else:
            self.window._mark_sensorgram_stale(
                f"{self.window._analysis_metric_label()} sensorgram is out of date | Press Start analysis"
            )
        selected_source_rois = self.window._selected_source_rois_snapshot()
        if len(selected_source_rois) == 1:
            roi_signature = self.window._roi_formula_spectrum_signature(selected_source_rois[0])
            if roi_signature is not None and roi_signature in self.window._roi_formula_spectrum_cache and not self.window._formula_spectrum_dirty:
                self.window._formula_spectrum_dirty = False
                self._apply_formula_spectrum_result(self.window._roi_formula_spectrum_cache[roi_signature])
                self.window._roi_formula_spectrum_cache.move_to_end(roi_signature)
                elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
                self.window._set_status_text(f"Spec | cache {elapsed}")
                return
        signature = self._formula_spectrum_signature()
        if signature is not None and signature in self.window._formula_spectrum_cache and not self.window._formula_spectrum_dirty:
            self._apply_formula_spectrum_result(self.window._formula_spectrum_cache[signature])
            self.window._formula_spectrum_cache.move_to_end(signature)
            elapsed = self.window._format_elapsed_seconds(time.perf_counter() - start_time)
            self.window._append_workflow_log(f"Spec cache hit | {elapsed}", level="debug")
            self.window._set_status_text(f"Spec | cache {elapsed}")
        elif self.window._analysis_live_preview_enabled:
            self.window._schedule_formula_spectrum_refresh()

    def _on_analysis_spectral_cube_range_changed(self, *_args) -> None:
        self.window._save_control_preferences()
        if self.window.analysis_start_spectral_cube_spin.value() > self.window.analysis_end_spectral_cube_spin.value():
            self.window.analysis_start_spectral_cube_spin.blockSignals(True)
            self.window.analysis_end_spectral_cube_spin.blockSignals(True)
            start = min(self.window.analysis_start_spectral_cube_spin.value(), self.window.analysis_end_spectral_cube_spin.value())
            end = max(self.window.analysis_start_spectral_cube_spin.value(), self.window.analysis_end_spectral_cube_spin.value())
            self.window.analysis_start_spectral_cube_spin.setValue(start)
            self.window.analysis_end_spectral_cube_spin.setValue(end)
            self.window.analysis_start_spectral_cube_spin.blockSignals(False)
            self.window.analysis_end_spectral_cube_spin.blockSignals(False)
        low = int(self.window.analysis_start_spectral_cube_spin.value())
        high = int(self.window.analysis_end_spectral_cube_spin.value())
        if self.window.analysis_spectral_cube_range_slider.values() != (low, high):
            self.window.analysis_spectral_cube_range_slider.setValues(low, high)
        if self.window._analysis_live_preview_enabled and not self.preview_sensorgram_from_cache():
            self.mark_stale(
                f"{self.window._analysis_metric_label()} sensorgram is out of date | Press Start analysis"
            )
        elif not self.window._analysis_live_preview_enabled:
            self.window._mark_sensorgram_stale()

    def on_spectral_cube_range_slider_changed(self, low: int, high: int) -> None:
        start_spin = self.window.analysis_start_spectral_cube_spin
        end_spin = self.window.analysis_end_spectral_cube_spin
        if start_spin.value() != low:
            start_spin.setValue(low)
        if end_spin.value() != high:
            end_spin.setValue(high)

    def select_all_spectral_cube_range(self) -> None:
        if not self.window._spectral_cube_values:
            return
        self.window.analysis_start_spectral_cube_spin.setValue(int(min(self.window._spectral_cube_values)))
        self.window.analysis_end_spectral_cube_spin.setValue(int(max(self.window._spectral_cube_values)))

    def select_first_spectral_cube_range(self) -> None:
        if not self.window._spectral_cube_values:
            return
        first = int(min(self.window._spectral_cube_values))
        self.window.analysis_start_spectral_cube_spin.setValue(first)
        self.window.analysis_end_spectral_cube_spin.setValue(first)

    def _on_analysis_wavelength_range_changed(self, *_args) -> None:
        min_spin = self.window.analysis_wavelength_min_spin
        max_spin = self.window.analysis_wavelength_max_spin
        if min_spin.value() > max_spin.value():
            min_spin.blockSignals(True)
            max_spin.blockSignals(True)
            low = min(min_spin.value(), max_spin.value())
            high = max(min_spin.value(), max_spin.value())
            min_spin.setValue(low)
            max_spin.setValue(high)
            min_spin.blockSignals(False)
            max_spin.blockSignals(False)
        if self.window._wavelength_values:
            low_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - min_spin.value()))
            high_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - max_spin.value()))
            if self.window.analysis_wavelength_range_slider.values() != (low_index, high_index):
                self.window.analysis_wavelength_range_slider.setValues(low_index, high_index)
        self.on_fit_settings_changed()

    def on_wavelength_range_slider_changed(self, low_index: int, high_index: int) -> None:
        if not self.window._wavelength_values:
            return
        low_value = float(self.window._wavelength_values[low_index])
        high_value = float(self.window._wavelength_values[high_index])
        min_spin = self.window.analysis_wavelength_min_spin
        max_spin = self.window.analysis_wavelength_max_spin
        if min_spin.value() != low_value:
            min_spin.setValue(low_value)
        if max_spin.value() != high_value:
            max_spin.setValue(high_value)

    def select_all_wavelength_range(self) -> None:
        if not self.window._wavelength_values:
            return
        self.window.analysis_wavelength_min_spin.setValue(float(min(self.window._wavelength_values)))
        self.window.analysis_wavelength_max_spin.setValue(float(max(self.window._wavelength_values)))

    def _on_analysis_section_applied_changed(self, applied: bool) -> None:
        applied = bool(applied)
        self.window._append_workflow_log(f"Analysis linked state changed: {applied}", level="debug")
        if self.window._analysis_enabled == applied:
            self.window._update_analysis_control_state()
            return
        self.window._analysis_enabled = applied
        self.window._settings.setValue("analysis_section_applied", self.window._analysis_enabled)
        if not self.window._analysis_enabled and self.window._analysis_live_preview_enabled:
            self.window._analysis_live_preview_enabled = False
            self.window._settings.setValue("analysis/live_preview", False)
        self.window._update_analysis_control_state()
        if self.window._analysis_enabled:
            self.window._mark_formula_spectrum_dirty()
            self.window._set_status_text("Analysis calculations enabled.")
            return
        self.window._stop_sensorgram_calculation()
        self.window._pending_sensorgram_payload = None
        self.window._clear_formula_spectrum()
        self.window._clear_sensorgram("Analysis calculations are disabled for this panel.")
        self.window._set_status_text("Analysis calculations disabled.")

    def _analysis_fit_method_key(self) -> str:
        return str(self.window.analysis_fit_method_combo.currentData() or "none")

    def _analysis_metric_key(self) -> str:
        return str(self.window.analysis_metric_combo.currentData() or "centroid")

    def _analysis_metric_label(self) -> str:
        return str(self.window.analysis_metric_combo.currentText() or "Metric")

    def _analysis_metric_axis_label(self) -> str:
        metric_key = self._analysis_metric_key()
        if metric_key in {"maximum", "centroid"}:
            return "Wavelength (nm)"
        return "Metric"

    def _analysis_formula_axis_label(self) -> str:
        """Spectra plot's y-axis label - shows the actual formula (see
        processing/analysis.py:formula_value) matching whatever the ROI's
        math Formula combo (analysis_formula_combo) is currently set to, so
        the axis never silently mislabels a Ratio/Relative change/mOD trace
        as "Absorbance"."""
        formula_key = str(self.window.analysis_formula_combo.currentData() or "absorbance")
        return _FORMULA_AXIS_LABELS.get(formula_key, _FORMULA_AXIS_LABELS["absorbance"])

    def _update_spectrum_plot_label(self) -> None:
        self.window.spectrum_plot.setLabel("left", self._analysis_formula_axis_label())

    def _analysis_poly_order(self) -> int:
        return int(self.window.analysis_poly_order_spin.value())

    def _poly_order_summary_suffix(self) -> str:
        """' | Polynomial order N' for status/summary text - only when a poly
        fit actually produced the metric, matching Order's own visibility
        (sync_analysis_fitting_controls). Fitting = None reads the metric off
        the raw spectrum, so mentioning a polynomial order there would be
        misleading."""
        if self._analysis_fit_method_key() != "poly":
            return ""
        return f" | Polynomial order {self._analysis_poly_order()}"

    def _current_analysis_spectral_cube_range(self) -> tuple[int, int] | None:
        if not self.window._spectral_cube_values:
            return None
        start = int(self.window.analysis_start_spectral_cube_spin.value())
        end = int(self.window.analysis_end_spectral_cube_spin.value())
        if start > end:
            start, end = end, start
        return start, end

    def _sync_analysis_spectral_cube_range_controls(self) -> None:
        spectral_cube_enabled = bool(self.window._spectral_cube_values)
        self.window.analysis_start_spectral_cube_spin.setEnabled(spectral_cube_enabled)
        self.window.analysis_end_spectral_cube_spin.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_range_slider.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_select_all_button.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_select_first_button.setEnabled(spectral_cube_enabled)
        self.window.analysis_spectral_cube_axis_label.setText(self.window._spectral_cube_axis_label())
        if not spectral_cube_enabled:
            return

        spectral_cube_min = int(min(self.window._spectral_cube_values))
        spectral_cube_max = int(max(self.window._spectral_cube_values))
        stored_start = self.window._settings_int("analysis/spectral_cube_start", spectral_cube_min, minimum=spectral_cube_min, maximum=spectral_cube_max)
        stored_end = self.window._settings_int("analysis/spectral_cube_end", spectral_cube_max, minimum=spectral_cube_min, maximum=spectral_cube_max)
        if stored_start > stored_end:
            stored_start, stored_end = stored_end, stored_start

        self.window.analysis_start_spectral_cube_spin.blockSignals(True)
        self.window.analysis_end_spectral_cube_spin.blockSignals(True)
        self.window.analysis_start_spectral_cube_spin.setRange(spectral_cube_min, spectral_cube_max)
        self.window.analysis_end_spectral_cube_spin.setRange(spectral_cube_min, spectral_cube_max)
        self.window.analysis_start_spectral_cube_spin.setValue(stored_start)
        self.window.analysis_end_spectral_cube_spin.setValue(stored_end)
        self.window.analysis_start_spectral_cube_spin.blockSignals(False)
        self.window.analysis_end_spectral_cube_spin.blockSignals(False)
        self.window.analysis_spectral_cube_range_slider.setRange(spectral_cube_min, spectral_cube_max)
        self.window.analysis_spectral_cube_range_slider.setValues(stored_start, stored_end)

    def _analysis_wavelength_range(self) -> tuple[float, float] | None:
        if not self.window._wavelength_values:
            return None
        return float(self.window.analysis_wavelength_min_spin.value()), float(self.window.analysis_wavelength_max_spin.value())

    def _sync_analysis_wavelength_range_controls(self) -> None:
        wavelength_enabled = bool(self.window._wavelength_values)
        self.window.analysis_wavelength_min_spin.setEnabled(wavelength_enabled)
        self.window.analysis_wavelength_max_spin.setEnabled(wavelength_enabled)
        self.window.analysis_wavelength_range_slider.setEnabled(wavelength_enabled)
        self.window.analysis_wavelength_select_all_button.setEnabled(wavelength_enabled)
        if not wavelength_enabled:
            return

        wavelength_min = float(min(self.window._wavelength_values))
        wavelength_max = float(max(self.window._wavelength_values))
        decimals = max((self.window._decimal_places(value) for value in self.window._wavelength_values), default=0)
        decimals = min(max(decimals, 0), 4)
        stored_min = self.window._settings_float("analysis/wavelength_min_nm", wavelength_min, minimum=wavelength_min, maximum=wavelength_max)
        stored_max = self.window._settings_float("analysis/wavelength_max_nm", wavelength_max, minimum=wavelength_min, maximum=wavelength_max)
        if stored_min > stored_max:
            stored_min, stored_max = stored_max, stored_min

        self.window.analysis_wavelength_min_spin.blockSignals(True)
        self.window.analysis_wavelength_max_spin.blockSignals(True)
        self.window.analysis_wavelength_min_spin.setDecimals(decimals)
        self.window.analysis_wavelength_max_spin.setDecimals(decimals)
        self.window.analysis_wavelength_min_spin.setRange(wavelength_min, wavelength_max)
        self.window.analysis_wavelength_max_spin.setRange(wavelength_min, wavelength_max)
        self.window.analysis_wavelength_min_spin.setValue(stored_min)
        self.window.analysis_wavelength_max_spin.setValue(stored_max)
        self.window.analysis_wavelength_min_spin.blockSignals(False)
        self.window.analysis_wavelength_max_spin.blockSignals(False)
        low_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - stored_min))
        high_index = min(range(len(self.window._wavelength_values)), key=lambda idx: abs(self.window._wavelength_values[idx] - stored_max))
        self.window.analysis_wavelength_range_slider.setRange(0, max(len(self.window._wavelength_values) - 1, 0))
        self.window.analysis_wavelength_range_slider.setValues(low_index, high_index)

    def sync_analysis_fitting_controls(self) -> None:
        """Show/hide Order to match the chosen Fitting method - mirrors sLSPR
        acq's own sync_processing_crop_parameter_widget
        (main_window_processing.py): Order is poly-specific, same as acq's
        own poly_widgets_visible. Metric stays visible regardless of Fitting:
        Maximum/Centroid are computed straight off the raw absorbance
        spectrum when Fitting = None (metric_value_from_spectrum), so unlike
        Order there is no state where Metric has nothing to show."""
        fit_method = self._analysis_fit_method_key()
        order_visible = fit_method == "poly"
        self.window.analysis_poly_order_spin.setVisible(order_visible)
        order_title = getattr(self.window, "_analysis_fit_order_title_widget", None)
        if order_title is not None:
            order_title.setVisible(order_visible)

    def sync_analysis_roi_math_controls(self) -> None:
        """Show/hide Trim % to match the chosen Reduction method - only
        Trimmed mean uses it, same visibility-gating pattern as
        sync_analysis_fitting_controls's Order widget above."""
        is_trimmed_mean = str(self.window.analysis_reduction_method_combo.currentData() or "mean") == "trimmed_mean"
        self.window.analysis_trimmed_mean_spin.setVisible(is_trimmed_mean)
        trim_title = getattr(self.window, "_analysis_trim_fraction_title_widget", None)
        if trim_title is not None:
            trim_title.setVisible(is_trimmed_mean)

    def on_roi_math_settings_changed(self, *_args) -> None:
        """Reduction method / Trim % / Formula changed - these are session-
        scoped (area_roi_settings, not QSettings, see storage/workspace.py)
        since they change what a cached absorbance value means, unlike
        fit_method/metric/poly_order. Written back into state here, then the
        same cache-check-or-refresh logic as a fit-settings change applies
        (the changed reduction/formula are now part of the cache signature -
        see AnalysisController._roi_math_signature_elements - so a stale
        result will already miss and trigger a real recompute)."""
        settings = self.window._state.area_roi_settings
        settings.reduction_method = str(self.window.analysis_reduction_method_combo.currentData() or "mean")
        settings.trimmed_mean_fraction = float(self.window.analysis_trimmed_mean_spin.value()) / 100.0
        settings.formula_key = str(self.window.analysis_formula_combo.currentData() or "absorbance")
        self.sync_analysis_roi_math_controls()
        self._update_spectrum_plot_label()
        self.window._schedule_processing_state_save()
        self._on_analysis_fit_settings_changed(*_args)

    def refresh_roi_math_controls_from_state(self) -> None:
        """Push area_roi_settings' reduction/trim/formula into the ROI's-math
        widgets without re-triggering on_roi_math_settings_changed - called
        whenever a session/profile load replaces area_roi_settings wholesale,
        so the panel doesn't keep showing stale (default) values after
        loading a session that used a non-default Reduction or Formula."""
        window = self.window
        settings = window._state.area_roi_settings
        window.analysis_reduction_method_combo.blockSignals(True)
        window.analysis_trimmed_mean_spin.blockSignals(True)
        window.analysis_formula_combo.blockSignals(True)
        try:
            reduction_index = max(window.analysis_reduction_method_combo.findData(str(settings.reduction_method or "mean")), 0)
            window.analysis_reduction_method_combo.setCurrentIndex(reduction_index)
            window.analysis_trimmed_mean_spin.setValue(int(round(float(settings.trimmed_mean_fraction) * 100.0)))
            formula_index = max(window.analysis_formula_combo.findData(str(settings.formula_key or "absorbance")), 0)
            window.analysis_formula_combo.setCurrentIndex(formula_index)
        finally:
            window.analysis_reduction_method_combo.blockSignals(False)
            window.analysis_trimmed_mean_spin.blockSignals(False)
            window.analysis_formula_combo.blockSignals(False)
        self.sync_analysis_roi_math_controls()
        self._update_spectrum_plot_label()

    # ------------------------------------------------------------------
    # Statistics: post-processing applied to the already-computed
    # sensorgram trace (smoothing/spike-rejection/baseline/group
    # averaging) - never raw pixels, so no cache-signature changes are
    # needed anywhere here (see processing/trace_statistics.py).
    # ------------------------------------------------------------------

    def sync_statistics_controls(self) -> None:
        """Show/enable only the controls that matter for the current
        settings - Order only for Savitzky-Golay smoothing, Threshold only
        for Hampel spike rejection, and the method/param controls for each
        block only while its own toggle is on. Same visibility-gating idea
        as sync_analysis_fitting_controls/sync_analysis_roi_math_controls."""
        window = self.window
        is_savgol = str(window.analysis_smoothing_method_combo.currentData() or "none") == "savgol"
        window.analysis_smoothing_polyorder_spin.setVisible(is_savgol)
        order_title = getattr(window, "_analysis_smoothing_order_title_widget", None)
        if order_title is not None:
            order_title.setVisible(is_savgol)
        window.analysis_smoothing_window_spin.setEnabled(
            str(window.analysis_smoothing_method_combo.currentData() or "none") != "none"
        )

        spike_enabled = bool(window.analysis_spike_rejection_check.isChecked())
        window.analysis_spike_rejection_method_combo.setEnabled(spike_enabled)
        window.analysis_spike_rejection_window_spin.setEnabled(spike_enabled)
        is_hampel = str(window.analysis_spike_rejection_method_combo.currentData() or "hampel") == "hampel"
        window.analysis_spike_rejection_threshold_spin.setVisible(is_hampel)
        threshold_title = getattr(window, "_analysis_spike_threshold_title_widget", None)
        if threshold_title is not None:
            threshold_title.setVisible(is_hampel)
        window.analysis_spike_rejection_threshold_spin.setEnabled(spike_enabled)

        baseline_enabled = bool(window.analysis_baseline_check.isChecked())
        window.analysis_baseline_start_spin.setEnabled(baseline_enabled)
        window.analysis_baseline_end_spin.setEnabled(baseline_enabled)

        group_enabled = bool(window.analysis_group_stats_check.isChecked())
        window.analysis_group_stats_center_combo.setEnabled(group_enabled)
        window.analysis_group_stats_band_combo.setEnabled(group_enabled)
        window.analysis_calculate_group_button.setEnabled(group_enabled)

    def on_statistics_settings_changed(self, *_args) -> None:
        """Any Statistics control changed - write back to state (session-
        scoped, same reasoning as ROI's math), then just recompute the
        overlays from the already-stored raw sensorgram arrays. No cache
        invalidation is needed: this never touches the pixel-level or
        fitted-trace caches, only how the already-computed trace is drawn."""
        window = self.window
        settings = window._state.statistics_settings
        settings.smoothing_method = str(window.analysis_smoothing_method_combo.currentData() or "none")
        settings.smoothing_window = int(window.analysis_smoothing_window_spin.value())
        settings.smoothing_polyorder = int(window.analysis_smoothing_polyorder_spin.value())
        settings.spike_rejection_enabled = bool(window.analysis_spike_rejection_check.isChecked())
        settings.spike_rejection_method = str(window.analysis_spike_rejection_method_combo.currentData() or "hampel")
        settings.spike_rejection_window = int(window.analysis_spike_rejection_window_spin.value())
        settings.spike_rejection_threshold = float(window.analysis_spike_rejection_threshold_spin.value())
        settings.baseline_enabled = bool(window.analysis_baseline_check.isChecked())
        settings.baseline_window_start = float(window.analysis_baseline_start_spin.value())
        settings.baseline_window_end = float(window.analysis_baseline_end_spin.value())
        settings.group_stats_enabled = bool(window.analysis_group_stats_check.isChecked())
        settings.group_stats_center = str(window.analysis_group_stats_center_combo.currentData() or "mean")
        settings.group_stats_band = str(window.analysis_group_stats_band_combo.currentData() or "sd")
        self.sync_statistics_controls()
        window._schedule_processing_state_save()
        self._update_statistics_overlays()

    def refresh_statistics_controls_from_state(self) -> None:
        """Push statistics_settings into the Statistics widgets without
        re-triggering on_statistics_settings_changed - called whenever a
        session/profile load replaces statistics_settings wholesale, same
        pattern as refresh_roi_math_controls_from_state."""
        window = self.window
        settings = window._state.statistics_settings
        widgets = (
            window.analysis_smoothing_method_combo,
            window.analysis_smoothing_window_spin,
            window.analysis_smoothing_polyorder_spin,
            window.analysis_spike_rejection_check,
            window.analysis_spike_rejection_method_combo,
            window.analysis_spike_rejection_window_spin,
            window.analysis_spike_rejection_threshold_spin,
            window.analysis_baseline_check,
            window.analysis_baseline_start_spin,
            window.analysis_baseline_end_spin,
            window.analysis_group_stats_check,
            window.analysis_group_stats_center_combo,
            window.analysis_group_stats_band_combo,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
            smoothing_index = max(window.analysis_smoothing_method_combo.findData(str(settings.smoothing_method or "none")), 0)
            window.analysis_smoothing_method_combo.setCurrentIndex(smoothing_index)
            window.analysis_smoothing_window_spin.setValue(int(settings.smoothing_window))
            window.analysis_smoothing_polyorder_spin.setValue(int(settings.smoothing_polyorder))
            window.analysis_spike_rejection_check.setChecked(bool(settings.spike_rejection_enabled))
            spike_index = max(
                window.analysis_spike_rejection_method_combo.findData(str(settings.spike_rejection_method or "hampel")), 0
            )
            window.analysis_spike_rejection_method_combo.setCurrentIndex(spike_index)
            window.analysis_spike_rejection_window_spin.setValue(int(settings.spike_rejection_window))
            window.analysis_spike_rejection_threshold_spin.setValue(float(settings.spike_rejection_threshold))
            window.analysis_baseline_check.setChecked(bool(settings.baseline_enabled))
            window.analysis_baseline_start_spin.setValue(float(settings.baseline_window_start or 0.0))
            window.analysis_baseline_end_spin.setValue(float(settings.baseline_window_end or 0.0))
            window.analysis_group_stats_check.setChecked(bool(settings.group_stats_enabled))
            group_center_index = max(
                window.analysis_group_stats_center_combo.findData(str(settings.group_stats_center or "mean")), 0
            )
            window.analysis_group_stats_center_combo.setCurrentIndex(group_center_index)
            group_band_index = max(window.analysis_group_stats_band_combo.findData(str(settings.group_stats_band or "sd")), 0)
            window.analysis_group_stats_band_combo.setCurrentIndex(group_band_index)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self.sync_statistics_controls()
        self._update_statistics_overlays()

    def _update_statistics_overlays(self) -> None:
        self._update_processed_trace_overlay()
        self._update_group_overlay()

    def _update_processed_trace_overlay(self) -> None:
        """Spike-rejection -> smoothing -> baseline, in that order (clean
        transients before smoothing blends them into neighbors; baseline is
        a simple offset applied last), drawn on sensorgram_processed_curve.
        Hidden whenever nothing is actually active, so an unused overlay
        doesn't sit on top of the raw trace."""
        window = self.window
        settings = window._state.statistics_settings
        spectral_cubes = window._sensorgram_spectral_cube_indices
        values = window._sensorgram_metric_values
        if spectral_cubes.size == 0:
            window.sensorgram_processed_curve.hide()
            return
        x_values = self._sensorgram_x_values(spectral_cubes)
        valid = np.isfinite(x_values) & np.isfinite(values)
        if not np.any(valid):
            window.sensorgram_processed_curve.hide()
            return
        order = np.argsort(x_values[valid])
        x = x_values[valid][order]
        y = values[valid][order].astype(np.float64, copy=True)

        active = False
        if settings.spike_rejection_enabled:
            active = True
            if settings.spike_rejection_method == "running_median":
                y = reject_spikes_running_median(y, window=settings.spike_rejection_window)
            else:
                y = reject_spikes_hampel(y, window=settings.spike_rejection_window, threshold=settings.spike_rejection_threshold)
        if settings.smoothing_method == "savgol":
            active = True
            y = smooth_savgol(y, window=settings.smoothing_window, polyorder=settings.smoothing_polyorder)
        elif settings.smoothing_method == "moving_average":
            active = True
            y = smooth_moving_average(y, window=settings.smoothing_window)
        if settings.baseline_enabled:
            active = True
            y, _baseline = normalize_to_baseline_window(x, y, settings.baseline_window_start, settings.baseline_window_end)

        if not active:
            window.sensorgram_processed_curve.hide()
            return
        finite = np.isfinite(y)
        if not np.any(finite):
            window.sensorgram_processed_curve.hide()
            return
        window.sensorgram_processed_curve.setData(x[finite], y[finite])
        window.sensorgram_processed_curve.show()

    # ------------------------------------------------------------------
    # Group statistics: aggregates each member ROI pair's own already-
    # computed sensorgram trace across an AreaRoiGroup, shown alongside
    # (not replacing) the individually-selected trace above.
    # ------------------------------------------------------------------

    def _group_members_for_current_selection(self) -> tuple[str, list[AreaRoi]] | None:
        """(group_name, member_area_rois) if the current selection is
        exactly one ROI pair belonging to a group with >=2 members, else
        None - group stats only make sense for a genuine multi-member group,
        not a lone ROI or an already-multi-selected combined view."""
        selected_roi_ids = self.window._selected_spectrum_roi_ids()
        if len(selected_roi_ids) != 1:
            return None
        group = self.window._group_for_roi(int(selected_roi_ids[0]))
        if group is None or len(group.area_roi_ids) < 2:
            return None
        member_ids = {int(roi_id) for roi_id in group.area_roi_ids}
        members = [roi for roi in self.window._state.area_rois if int(roi.area_roi_id) in member_ids]
        if len(members) < 2:
            return None
        return group.name, members

    @staticmethod
    def _member_trace_aligned(result: SensorgramComputationResult, spectral_cubes: list[int]) -> np.ndarray:
        """Reindex a member's own (spectral_cube_indices, metric_signal)
        onto the group's full requested spectral-cube list, NaN where that
        member has no value for a given cube - keeps every member's array
        the same length/order for aggregate_group_traces regardless of
        whether one member happened to skip/fail a different frame."""
        value_by_cube = {
            int(index): float(value) for index, value in zip(result.spectral_cube_indices, result.metric_signal)
        }
        return np.asarray([value_by_cube.get(int(cube), float("nan")) for cube in spectral_cubes], dtype=np.float64)

    def _update_group_overlay(self) -> None:
        """Draws whatever is already available from cache - never triggers a
        computation itself (see calculate_group_sensorgram for that). Called
        after every sensorgram update and after a group calculation
        finishes, so the band reflects whatever member traces exist right
        now, live."""
        window = self.window
        settings = window._state.statistics_settings
        if not settings.group_stats_enabled:
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        group_info = self._group_members_for_current_selection()
        spectral_cubes = self.available_analysis_spectral_cubes()
        if group_info is None or not spectral_cubes:
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        _group_name, members = group_info
        member_traces: dict[int, np.ndarray] = {}
        for member_roi in members:
            member_id = int(member_roi.area_roi_id)
            signature = self._sensorgram_signature_for_selection(spectral_cubes, (member_id,), [member_roi])
            result = None if signature is None else window._sensorgram_cache.get(signature)
            if result is None:
                continue
            member_traces[member_id] = self._member_trace_aligned(result, spectral_cubes)
        if len(member_traces) < 2:
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        x_values = self._sensorgram_x_values(spectral_cubes)
        center, low, high = aggregate_group_traces(
            member_traces, center=settings.group_stats_center, band=settings.group_stats_band
        )
        valid = np.isfinite(x_values) & np.isfinite(center) & np.isfinite(low) & np.isfinite(high)
        if not np.any(valid):
            window.sensorgram_group_curve.hide()
            window.sensorgram_group_band_fill_item.hide()
            return
        window.sensorgram_group_curve.setData(x_values[valid], center[valid])
        window.sensorgram_group_band_low_curve.setData(x_values[valid], low[valid])
        window.sensorgram_group_band_high_curve.setData(x_values[valid], high[valid])
        window.sensorgram_group_curve.show()
        window.sensorgram_group_band_fill_item.show()

    def calculate_group_sensorgram(self) -> None:
        """"Calculate group" button: computes (or reuses already-cached)
        every member's own single-ROI sensorgram, one at a time - reusing
        the normal single-ROI worker path (_start_sensorgram_worker) rather
        than a separate parallel implementation, so each member's result
        lands in the same window._sensorgram_cache the individual view
        already reads from. The visible primary trace flickers through each
        member while this runs (the same worker that updates the display is
        what's being reused per member) and is restored to the actual
        current selection once the whole group is done, see
        _finish_group_calculation."""
        group_info = self._group_members_for_current_selection()
        if group_info is None:
            self.window._set_status_text("Select a ROI pair that belongs to a multi-member group first.")
            return
        spectral_cubes = self.available_analysis_spectral_cubes()
        if not spectral_cubes:
            self.window._set_status_text("No spectral cubes are available in the selected range.")
            return
        _group_name, members = group_info
        self._group_calculation_spectral_cubes = spectral_cubes
        self._group_calculation_members_by_id = {int(m.area_roi_id): m for m in members}
        self._group_calculation_pending_member_ids = list(self._group_calculation_members_by_id.keys())
        self._group_calculation_active = True
        self.window._set_status_text(f"Calculating group ({len(members)} members)...")
        self._advance_group_calculation()

    def _advance_group_calculation(self) -> None:
        spectral_cubes = getattr(self, "_group_calculation_spectral_cubes", None)
        pending = getattr(self, "_group_calculation_pending_member_ids", None)
        members_by_id = getattr(self, "_group_calculation_members_by_id", None)
        if not spectral_cubes or pending is None or members_by_id is None:
            self._group_calculation_active = False
            return
        while pending:
            member_id = pending[0]
            member_roi = members_by_id[member_id]
            signature = self._sensorgram_signature_for_selection(spectral_cubes, (member_id,), [member_roi])
            if signature is None:
                pending.pop(0)
                continue
            if self.window._sensorgram_cache.get(signature) is not None:
                pending.pop(0)
                continue
            self._start_sensorgram_worker(signature, spectral_cubes, (member_id,), [member_roi])
            return
        self._finish_group_calculation()

    def _on_group_member_sensorgram_ready(self) -> None:
        pending = getattr(self, "_group_calculation_pending_member_ids", None)
        if pending:
            pending.pop(0)
        self._advance_group_calculation()

    def _finish_group_calculation(self) -> None:
        self._group_calculation_active = False
        member_count = len(getattr(self, "_group_calculation_members_by_id", {}) or {})
        self.window._set_status_text(f"Group calculated ({member_count} members).")
        # Restore the actual current selection's own trace/view - group
        # member calculations reused the same worker/display path, so the
        # plot currently shows whichever member finished last.
        self.calculate_sensorgram_for_range()

    def _set_sensorgram_summary_text(self, text: str) -> None:
        self.window.sensorgram_summary_label.setText(text)

    def _update_sensorgram_plot_labels(self) -> None:
        self.window.sensorgram_plot.setLabel("left", self._analysis_metric_axis_label())
        self.window.sensorgram_plot.setLabel("bottom", self.window._spectral_cube_axis_label())

    def _analysis_plot_spectral_cube_range(self) -> tuple[int, int] | None:
        if not self.window._spectral_cube_values:
            return None
        return int(min(self.window._spectral_cube_values)), int(max(self.window._spectral_cube_values))

    def _spectrum_plot_wavelength_values(self) -> list[float]:
        """0 nm is the broadband/dark reference frame, not a target
        wavelength (see ChromaticController.candidate_chromatic_wavelengths
        for the same exclusion elsewhere) - it can feed analysis as a
        correction factor but is never itself a result, so the spectra
        plot's x-axis always drops it regardless of the separate "treat 0 nm
        as a dark reference frame" display preference that only governs the
        wavelength slider/spin/dropdown (_filtered_wavelength_values)."""
        return sorted(float(w) for w in self.window._wavelength_values if float(w) != 0.0)

    def _analysis_plot_wavelength_range(self) -> tuple[float, float] | None:
        values = self._spectrum_plot_wavelength_values()
        if not values:
            return None
        return values[0], values[-1]

    def _analysis_plot_wavelength_ticks(self) -> list[list[tuple[float, str]]] | None:
        """Major ticks every ~100 nm (snapped to the nearest available
        wavelength, mirroring MainWindow._wavelength_slider_major_ticks) plus
        a minor tick at every remaining available wavelength, so the axis
        grid (showGrid draws lines at each AxisItem tick) shows a labeled
        line every ~100 nm and a faint line at every acquired wavelength."""
        values = self._spectrum_plot_wavelength_values()
        if not values:
            return None
        step = 100.0
        lo = ceil(values[0] / step) * step
        hi = floor(values[-1] / step) * step
        major_values: set[float] = set()
        boundary = lo
        while boundary <= hi + 1e-6:
            major_values.add(min(values, key=lambda v: abs(v - boundary)))
            boundary += step
        if not major_values:
            major_values = {values[0], values[-1]}
        majors = [(v, f"{v:.0f}") for v in sorted(major_values)]
        minors = [(v, "") for v in values if v not in major_values]
        return [majors, minors]

    def _sensorgram_axis_range(self) -> tuple[float, float] | None:
        """The sensorgram plot's x-axis limits: elapsed-time range when
        acquisition metadata with per-image timing is loaded, otherwise the
        raw spectral-cube-index range (today's behavior, unchanged).

        Cached on the window: this result depends only on the dataset's full
        spectral-cube set and its acquisition metadata, neither of which
        changes when the user switches wavelength - yet this used to get
        recomputed from scratch on every single switch anyway, as part of
        apply_loaded_image's general "resync everything to the new image"
        step, even though nothing about a wavelength switch actually
        invalidates it. The cache key (metadata identity + the cube list
        itself) naturally recomputes only when either really changes (a new
        dataset load, or edits to which cubes exist)."""
        window = self.window
        if not window._spectral_cube_values:
            return None
        metadata = self._sensorgram_time_mode_metadata()
        cache_key = (id(metadata) if metadata is not None else None, tuple(window._spectral_cube_values))
        cache = getattr(window, "_sensorgram_axis_range_cache", None)
        if cache is not None and cache[0] == cache_key:
            return cache[1]
        index_range = (float(min(window._spectral_cube_values)), float(max(window._spectral_cube_values)))
        if metadata is None:
            result = index_range
        else:
            x_values = self._sensorgram_x_values(window._spectral_cube_values)
            finite = x_values[np.isfinite(x_values)]
            result = index_range if finite.size == 0 else (float(np.min(finite)), float(np.max(finite)))
        window._sensorgram_axis_range_cache = (cache_key, result)
        return result

    def _sync_analysis_plot_axes(self) -> None:
        wavelength_range = self._analysis_plot_wavelength_range()
        if wavelength_range is not None:
            self.window.spectrum_plot.setLimits(xMin=wavelength_range[0], xMax=wavelength_range[1])
            self.window.spectrum_plot.setXRange(wavelength_range[0], wavelength_range[1], padding=0.03)
        self.window.spectrum_plot.getAxis("bottom").setTicks(self._analysis_plot_wavelength_ticks())
        sensorgram_range = self._sensorgram_axis_range()
        if sensorgram_range is not None:
            self.window.sensorgram_plot.setLimits(xMin=sensorgram_range[0], xMax=sensorgram_range[1])
            self.window.sensorgram_plot.setXRange(sensorgram_range[0], sensorgram_range[1], padding=0.03)

    def _sync_analysis_plot_cursors(self) -> None:
        has_dataset = bool(self.window._spectral_cube_values) and bool(self.window._wavelength_values)
        if not has_dataset:
            self.window.spectrum_cursor_line.hide()
            self.window.sensorgram_cursor_line.hide()
            return
        self.window.spectrum_cursor_line.show()
        self.window.sensorgram_cursor_line.show()
        current_spectral_cube = self.window._current_spectral_cube()
        current_wavelength = self.window._current_wavelength()
        if current_spectral_cube is None:
            current_spectral_cube = int(self.window._spectral_cube_values[0])
        if current_wavelength is None:
            current_wavelength = float(self.window._wavelength_values[0])
        cursor_color = self.window._chromatic_wavelength_color(float(current_wavelength))
        sensorgram_cursor_x = float(self._sensorgram_x_values([int(current_spectral_cube)])[0])
        if not np.isfinite(sensorgram_cursor_x):
            sensorgram_cursor_x = float(current_spectral_cube)
        self.window.spectrum_cursor_line.blockSignals(True)
        self.window.sensorgram_cursor_line.blockSignals(True)
        self.window.spectrum_cursor_line.setValue(float(current_wavelength))
        self.window.sensorgram_cursor_line.setValue(sensorgram_cursor_x)
        self.window.spectrum_cursor_line.setPen(pg.mkPen(cursor_color, width=2.2))
        self.window.spectrum_cursor_line.blockSignals(False)
        self.window.sensorgram_cursor_line.blockSignals(False)
        self.window._update_sensorgram_current_point()

    def _sync_analysis_plots(self) -> None:
        self._sync_analysis_plot_axes()
        self._sync_analysis_plot_cursors()

    def _on_spectrum_cursor_moved(self) -> None:
        if not self.window._wavelength_values:
            return
        wavelength = float(self.window.spectrum_cursor_line.value())
        nearest_index = min(
            range(len(self.window._wavelength_values)),
            key=lambda idx: abs(float(self.window._wavelength_values[idx]) - wavelength),
        )
        current_spectral_cube = self.window._current_spectral_cube()
        if current_spectral_cube is None and self.window._spectral_cube_values:
            current_spectral_cube = int(self.window._spectral_cube_values[self.window.spectral_cube_slider.value()])
        if current_spectral_cube is None:
            return
        target_wavelength = float(self.window._wavelength_values[nearest_index])
        self.window._set_current_spectral_cube_and_wavelength(int(current_spectral_cube), target_wavelength)

    def _on_sensorgram_cursor_moved(self) -> None:
        if not self.window._spectral_cube_values:
            return
        cursor_x = float(self.window.sensorgram_cursor_line.value())
        # Match against display x-values (elapsed time when in time mode,
        # same values _sync_analysis_plot_cursors placed the cursor with) -
        # any cube missing a timing entry falls back to its raw index so it
        # can still be matched against, consistent with the cursor-set side.
        display_x_values = self._sensorgram_x_values(self.window._spectral_cube_values)
        missing = ~np.isfinite(display_x_values)
        if np.any(missing):
            display_x_values = display_x_values.copy()
            display_x_values[missing] = np.asarray(self.window._spectral_cube_values, dtype=np.float64)[missing]
        nearest_index = int(np.argmin(np.abs(display_x_values - cursor_x)))
        current_wavelength = self.window._current_wavelength()
        if current_wavelength is None and self.window._wavelength_values:
            current_wavelength = float(self.window._wavelength_values[self.window.wavelength_slider.value()])
        if current_wavelength is None:
            return
        target_spectral_cube = int(self.window._spectral_cube_values[nearest_index])
        self.window._set_current_spectral_cube_and_wavelength(target_spectral_cube, float(current_wavelength))

    def _mark_sensorgram_stale(self, reason: str | None = None) -> None:
        if self.window._analysis_live_preview_enabled and self.window._analysis_enabled and self.window._state.dataset is not None:
            if reason is not None:
                self._set_sensorgram_summary_text(reason)
            self._schedule_sensorgram_refresh()
            return
        self.mark_stale(reason)

    def _invalidate_formula_spectrum_cache(self) -> None:
        self.window._formula_spectrum_cache.clear()
        self.window._formula_spectral_cube_cache.clear()
        self.window._roi_formula_spectrum_cache.clear()
        self.window._formula_spectrum_dirty = True
        self.window._cached_roi_ids.clear()

    def _invalidate_caches_for_exclusion_change(self) -> None:
        """No longer bulk-clears every cache: `_absorbance_spectrum_signature_
        for_source_rois`, `_roi_absorbance_signature`, `_sensorgram_signature_
        for_selection`, and `_sensorgram_spectral_cube_payload_signature` all
        now fold in `_exclusion_signature_for_cube()` per (cube, wavelength),
        so a rule being added/removed already produces a signature miss for
        exactly the affected cube/ROI entries - everything else the app
        already computed stays a valid, reusable cache hit instead of being
        thrown away wholesale (see docs/analysis_pipeline_redesign.md §2c).
        Still refreshes the "already calculated" ROI-table indicator (its
        cached snapshot, unlike the caches themselves, doesn't self-correct
        on the next read) and marks the sensorgram stale, matching the same
        "press Calculate again" UX every other analysis-affecting setting
        change already uses.
        """
        self._refresh_cached_roi_ids_snapshot()
        self.mark_stale("Exclusion rules changed")

    def _schedule_sensorgram_refresh(self) -> None:
        if not self.window._startup_ready or self.window._startup_restore_in_progress or not self.window._analysis_live_preview_enabled:
            return
        if self.window._sensorgram_refresh_timer.isActive():
            self.window._sensorgram_refresh_timer.stop()
        self.window._sensorgram_refresh_timer.start()

    def _refresh_sensorgram(self) -> None:
        if not self.window._startup_ready or self.window._startup_restore_in_progress or not self.window._analysis_live_preview_enabled:
            return
        self.calculate_sensorgram()

    def _mark_formula_spectrum_dirty(self) -> None:
        self.window._formula_spectrum_dirty = True
        blocked = self._sensorgram_prerequisite_blocked()
        if blocked == "disabled":
            self.window._clear_spectrum_summary_text()
            self.window._clear_sensorgram("Analysis calculations are disabled for this panel.")
            return
        if blocked == "no_dataset":
            self.window._clear_spectrum_summary_text()
            self.window._clear_sensorgram("Load a dataset to build the fitted sensorgram.")
            return
        if blocked == "chromatic_active":
            self.window._clear_spectrum_summary_text()
            self.window._clear_sensorgram("Sensorgram is hidden during chromatic setup.")
            return
        if not self._selected_spectrum_roi_ids():
            self.window._clear_spectrum_summary_text()
            self.window._clear_sensorgram("Select ROIs before calculating the sensorgram.")
            return
        self.window._set_spectrum_summary_text(
            f"{self._spectrum_selection_label()} | Spectrum is out of date | Enable live preview, or run Start analysis"
        )
        if not self.window._analysis_live_preview_enabled:
            self._mark_sensorgram_stale()

    def _selected_spectrum_roi_ids(self) -> tuple[int, ...]:
        return tuple(sorted(int(roi_id) for roi_id in self.window._selected_roi_ids))

    def _selected_source_rois_snapshot(self) -> list[AreaRoi]:
        selected_ids = self._selected_spectrum_roi_ids()
        if not selected_ids:
            self.window._selected_source_rois_cache_signature = None
            self.window._selected_source_rois_cache_value = tuple()
            return []
        signature_parts: list[object] = [selected_ids]
        source_rois: list[AreaRoi] = []
        roi_by_id = {int(roi.area_roi_id): roi for roi in self.window._state.area_rois}
        for roi_id in selected_ids:
            roi = roi_by_id.get(int(roi_id))
            if roi is None:
                self.window._selected_source_rois_cache_signature = None
                self.window._selected_source_rois_cache_value = tuple()
                return []
            source_rois.append(roi)
            signature_parts.append(
                (
                    int(roi.area_roi_id),
                    round(float(roi.center_x), 3),
                    round(float(roi.center_y), 3),
                    round(float(roi.sample_radius_px), 3),
                    round(float(roi.reference_inner_diameter_px or 0.0), 3),
                    round(float(roi.reference_outer_diameter_px or 0.0), 3),
                    roi.sample_color_hex or "",
                    roi.reference_color_hex or "",
                )
            )
        signature = tuple(signature_parts)
        if self.window._selected_source_rois_cache_signature == signature and self.window._selected_source_rois_cache_value:
            return list(self.window._selected_source_rois_cache_value)
        copied = tuple(deepcopy(roi) for roi in source_rois)
        self.window._selected_source_rois_cache_signature = signature
        self.window._selected_source_rois_cache_value = copied
        return list(copied)

    def _spectrum_selection_label(self) -> str:
        selected_ids = self._selected_spectrum_roi_ids()
        if not selected_ids:
            return "No ROIs"
        if self.window._selected_roi_ids:
            noun = "ROI" if len(selected_ids) == 1 else "ROIs"
            return f"{len(selected_ids)} selected {noun}"
        noun = "ROI" if len(selected_ids) == 1 else "ROIs"
        return f"All {len(selected_ids)} {noun}"

    def _schedule_formula_spectrum_refresh(self) -> None:
        if not self.window._startup_ready or self.window._startup_restore_in_progress:
            return
        self._mark_formula_spectrum_dirty()
        if self.window._analysis_live_preview_enabled:
            if self.window._formula_spectrum_timer.isActive():
                self.window._formula_spectrum_timer.stop()
            self.window._formula_spectrum_timer.start()

    def _refresh_cached_roi_ids_snapshot(self) -> None:
        """Recompute the "which ROIs are already calculated" snapshot the ROI
        table/overlay read for their blue/white indicator.

        This is the only place that calls `_roi_has_cached_formula_spectrum` for the
        whole ROI list. Call it after `_roi_formula_spectrum_cache` actually changes
        (populated or cleared) - never from ROI selection/editing code, which
        should only ever read `self.window._cached_roi_ids`.
        """
        self.window._cached_roi_ids = {
            int(roi.area_roi_id) for roi in self.window._state.area_rois if self._roi_has_cached_formula_spectrum(roi)
        }
        self.schedule_cube_slider_cache_refresh()

    def schedule_cube_slider_cache_refresh(self) -> None:
        """Debounced (150ms, see the timer set up in MainWindow.__init__)
        recompute of which Cube/Time slider ticks are already cached for the
        current ROI selection - call after anything that can change either
        side of that: the ROI selection itself, or `_roi_formula_spectrum_cache`
        (already covered by `_refresh_cached_roi_ids_snapshot` above, the
        established single choke point for cache-changed)."""
        self.window._cube_slider_cache_refresh_timer.start()

    def _refresh_cube_slider_cache_indicators(self) -> None:
        """Recomputes, on a background thread, which cubes on the Cube/Time
        slider already have every currently-selected ROI's formula
        spectrum cached in RAM (`window._roi_formula_spectrum_cache`), then colors
        those ticks via `DataAxisSlider.set_tick_cache_state`. A cube only
        counts as "cached" if ALL selected ROIs have it - selecting one
        ROI that was never computed pulls ticks back to gray even if every
        other selected ROI already has that cube, matching the maintainer's
        expected semantics (adding an uncalculated spot should only ever
        lower the cached count, never hide that it's missing).

        Runs off the main thread because the cost is real: for N selected
        ROIs and M cubes this is N*M signature computations, each folding in
        a per-wavelength chromatic signature - see
        `_roi_absorbance_signature_for_cube`. Cheap per call, but with the
        several dozen ROIs and few hundred cubes this app routinely handles
        (see docs/analysis_pipeline_redesign.md), the total can run into
        hundreds of milliseconds, which is a real, visible stall if paid on
        the GUI thread (the same "must not block the GUI thread" rule the
        sensorgram/spectrum workers already follow).
        """
        window = self.window
        if not window._analysis_enabled or window._state.dataset is None:
            window.spectral_cube_slider.set_tick_cache_state(None)
            return
        selected_roi_ids = window._selected_spectrum_roi_ids()
        spectral_cube_values = list(window._spectral_cube_values)
        if not selected_roi_ids or not spectral_cube_values:
            window.spectral_cube_slider.set_tick_cache_state(None)
            return
        selected_roi_id_set = set(int(roi_id) for roi_id in selected_roi_ids)
        selected_source_rois = [
            deepcopy(roi) for roi in window._state.area_rois if int(roi.area_roi_id) in selected_roi_id_set
        ]
        if not selected_source_rois:
            window.spectral_cube_slider.set_tick_cache_state(None)
            return

        from lspr_imaging_app.gui.worker import FunctionWorker

        window._cube_slider_cache_request_id += 1
        request_id = window._cube_slider_cache_request_id
        cache_lock = window._analysis_cache_lock
        roi_formula_spectrum_cache = window._roi_formula_spectrum_cache

        def _compute(rois=selected_source_rois, cubes=spectral_cube_values) -> set[int]:
            cached_tick_positions: set[int] = set()
            for position, spectral_cube_index in enumerate(cubes):
                all_cached = True
                for roi in rois:
                    signature = self._roi_formula_spectrum_signature_for_cube(roi, int(spectral_cube_index))
                    if signature is None:
                        all_cached = False
                        break
                    with cache_lock:
                        if roi_formula_spectrum_cache.get(signature) is None:
                            all_cached = False
                            break
                if all_cached:
                    cached_tick_positions.add(position)
            return cached_tick_positions

        worker = FunctionWorker(_compute)
        worker.signals.result.connect(
            lambda cached_tick_positions, request_id=request_id: self._apply_cube_slider_cache_indicators(
                request_id, cached_tick_positions
            )
        )
        window._thread_pool.start(worker)

    def _apply_cube_slider_cache_indicators(self, request_id: int, cached_tick_positions: set[int]) -> None:
        if request_id != self.window._cube_slider_cache_request_id:
            return
        self.window.spectral_cube_slider.set_tick_cache_state(frozenset(cached_tick_positions))

    # Pure JSON-canonicalization/hash helpers - moved to analysis_cache_signature.py
    # (no self.window/Qt dependency). Kept as staticmethod attributes under
    # their original names since main_window.py already references them as
    # AnalysisController._method(...).
    _analysis_cache_signature_to_json = staticmethod(analysis_cache_signature_to_json)
    _analysis_cache_signature_from_json = staticmethod(analysis_cache_signature_from_json)
    _signature_hash = staticmethod(signature_hash)
    _formula_spectral_cube_signature = staticmethod(formula_spectral_cube_signature)
    _formula_spectrum_result_covers_roi_ids = staticmethod(formula_spectrum_result_covers_roi_ids)

    def _analysis_cache_payload(self) -> dict:
        payload: dict[str, list[dict[str, object]]] = {
            "formula_spectrum_cache": [],
            "formula_spectral_cube_cache": [],
            "roi_absorbance_cache": [],
            "sensorgram_cache": [],
        }
        for signature, result in self.window._formula_spectrum_cache.items():
            payload["formula_spectrum_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_formula_spectrum_result(result),
                }
            )
        for signature, result in self.window._formula_spectral_cube_cache.items():
            payload["formula_spectral_cube_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_formula_spectrum_result(result),
                }
            )
        for signature, result in self.window._roi_formula_spectrum_cache.items():
            payload["roi_absorbance_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_formula_spectrum_result(result),
                }
            )
        for signature, result in self.window._sensorgram_cache.items():
            payload["sensorgram_cache"].append(
                {
                    "signature": self._analysis_cache_signature_to_json(signature),
                    "result": self._serialize_sensorgram_result(result),
                }
            )
        return payload

    def _restore_analysis_caches(self, payload: dict | None) -> None:
        self.window._formula_spectrum_cache.clear()
        self.window._formula_spectral_cube_cache.clear()
        self.window._roi_formula_spectrum_cache.clear()
        self.window._sensorgram_cache.clear()
        if not isinstance(payload, dict):
            return
        raw_formula_spectrum = payload.get("formula_spectrum_cache", payload.get("absorbance_spectrum_cache", []))
        if isinstance(raw_formula_spectrum, list):
            for entry in raw_formula_spectrum:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_formula_spectrum_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._formula_spectrum_cache[signature] = result
                spectral_cube_signature = self._formula_spectral_cube_signature(signature)
                if spectral_cube_signature is not None:
                    self._store_in_lru_cache(
                        self.window._formula_spectral_cube_cache, spectral_cube_signature, result,
                        self.window.FORMULA_SPECTRAL_CUBE_CACHE_SIZE,
                    )
        raw_formula_spectral_cubes = payload.get(
            "formula_spectral_cube_cache", payload.get("absorbance_spectral_cube_cache", [])
        )
        if isinstance(raw_formula_spectral_cubes, list):
            for entry in raw_formula_spectral_cubes:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_formula_spectrum_result(entry.get("result"))
                if signature is None:
                    continue
                self._store_in_lru_cache(
                    self.window._formula_spectral_cube_cache, signature, result,
                    self.window.FORMULA_SPECTRAL_CUBE_CACHE_SIZE,
                )
        raw_roi_formula_spectrum = payload.get("roi_absorbance_cache", payload.get("spot_absorbance_cache", []))
        if isinstance(raw_roi_formula_spectrum, list):
            for entry in raw_roi_formula_spectrum:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_formula_spectrum_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._roi_formula_spectrum_cache[signature] = result
        raw_sensorgram = payload.get("sensorgram_cache", [])
        if isinstance(raw_sensorgram, list):
            for entry in raw_sensorgram:
                if not isinstance(entry, dict):
                    continue
                signature = self._analysis_cache_signature_from_json(entry.get("signature"))
                result = self._deserialize_sensorgram_result(entry.get("result"))
                if signature is None:
                    continue
                self.window._sensorgram_cache[signature] = result
        self._refresh_cached_roi_ids_snapshot()

    def _toggle_analysis_live_preview(self) -> None:
        self.window._analysis_live_preview_enabled = not self.window._analysis_live_preview_enabled
        if not self.window._analysis_enabled and self.window._analysis_live_preview_enabled:
            self.window._analysis_live_preview_enabled = False
            self.window._settings.setValue("analysis/live_preview", False)
            self.window._update_analysis_control_state()
            self.window._set_status_text("Enable Analysis to use live preview.")
            return
        self.window._settings.setValue("analysis/live_preview", bool(self.window._analysis_live_preview_enabled))
        self.window._update_analysis_control_state()
        if self.window._analysis_live_preview_enabled:
            self._refresh_visible_spectrum_from_cache()
            self.preview_sensorgram_from_cache()
            self.window._set_status_text("Analysis live preview enabled.")
        else:
            self.window._set_status_text("Analysis live preview disabled.")

    def _toggle_analysis_time_independent(self) -> None:
        """Switches "Start analysis" between the default [λ,t] mode (every
        per-cube quantity - ROI read geometry, marked-pixel masks - is
        recomputed per cube, always correct, handles any future per-cube
        drift) and [λ] mode (each of those is computed once, from a
        reference cube, and reused for every cube - faster, valid because
        none of them actually vary cube to cube in this app by construction;
        see SharedWavelengthGeometry's docstring and
        _build_shared_wavelength_mask). Purely a performance choice: the
        scientific result is identical either way when the underlying
        assumption holds. _diagnose_shared_wavelength_geometry still checks
        and logs whether the geometry assumption actually holds for each
        run, but no longer blocks [λ] mode on a mismatch - this toggle is
        trusted directly. No cache needs invalidating when this flips either
        way.
        """
        self.window._analysis_time_independent = not self.window._analysis_time_independent
        self.window._settings.setValue("analysis/time_independent", bool(self.window._analysis_time_independent))
        self.window._update_analysis_control_state()
        if self.window._analysis_time_independent:
            self.window._set_status_text("[λ]: per-cube read geometry/masks computed once per run, not per cube.")
        else:
            self.window._set_status_text("[λ,t]: per-cube read geometry/masks recomputed for every cube.")

    def _available_analysis_spectral_cubes(self) -> list[int]:
        spectral_cube_range = self._current_analysis_spectral_cube_range()
        if spectral_cube_range is None:
            return []
        start, end = spectral_cube_range
        return [
            int(spectral_cube_index)
            for spectral_cube_index in self.window._spectral_cube_values
            if start <= int(spectral_cube_index) <= end
            and not is_cube_fully_excluded(self.window._state.image_exclusions, spectral_cube_index)
        ]
