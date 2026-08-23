from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from lspr_ui import APP_THEME


class UIStateManager:
    def __init__(self, window) -> None:
        self._window = window

    def sync_image_processing_controls(self) -> None:
        window = self._window
        settings = window._state.preprocessing
        window.reference_mode_combo.blockSignals(True)
        window.flatten_background_check.blockSignals(True)
        window.flatten_background_sigma_spin.blockSignals(True)
        window.flatten_background_binning_combo.blockSignals(True)
        window.background_ignore_roi_button.blockSignals(True)
        window.background_ignore_mask_button.blockSignals(True)
        window.background_exclusion_dilation_spin.blockSignals(True)
        window.background_local_reference_check.blockSignals(True)
        window.chromatic_apply_check.blockSignals(True)
        window.chromatic_sample_count_spin.blockSignals(True)
        window.chromatic_feature_count_spin.blockSignals(True)
        window.chromatic_subpixel_precision_combo.blockSignals(True)
        window.chromatic_landmark_kind_combo.blockSignals(True)
        window.chromatic_landmark_model_combo.blockSignals(True)
        window.chromatic_landmark_id_spin.blockSignals(True)
        window.hist_region.blockSignals(True)
        reference_index = max(window.reference_mode_combo.findData(str(settings.reference_mode or "auto")), 0)
        window.reference_mode_combo.setCurrentIndex(reference_index)
        window.background_removal_link.setChecked(settings.flatten_background_enabled)
        window.background_smoothing_sigma_spin.setValue(max(int(round(settings.flatten_background_sigma_px)), 3))
        binning_value = max(int(getattr(settings, "flatten_background_binning", 2)), 1)
        binning_index = max(window.background_smoothing_binning_combo.findData(binning_value), 0)
        window.background_smoothing_binning_combo.setCurrentIndex(binning_index)
        window.background_ignore_roi_button.setChecked(settings.flatten_background_exclude_area_rois)
        window.background_ignore_mask_button.setChecked(settings.flatten_background_exclude_mask)
        window.background_exclusion_dilation_spin.setValue(
            max(int(getattr(settings, "flatten_background_exclusion_dilation_px", 0)), 0)
        )
        window.background_local_reference_check.setChecked(bool(getattr(settings, "local_reference_normalization_enabled", False)))
        window.chromatic_apply_check.setChecked(bool(settings.chromatic_correction_enabled))
        sample_minimum = 1 if len(window._wavelength_values) <= 1 else 3
        sample_maximum = min(max(len(window._wavelength_values), sample_minimum), 7)
        window.chromatic_sample_count_spin.setRange(sample_minimum, max(sample_maximum, sample_minimum))
        window.chromatic_sample_count_spin.setValue(
            window._normalized_odd_count(int(settings.chromatic_sample_image_count), sample_minimum, sample_maximum)
        )
        feature_count_value = int(settings.chromatic_feature_count)
        feature_count_index = window.chromatic_feature_count_spin.findData(feature_count_value)
        if feature_count_index < 0:
            feature_count_index = window.chromatic_feature_count_spin.findData(15)
        if feature_count_index < 0:
            feature_count_index = 0
        window.chromatic_feature_count_spin.setCurrentIndex(feature_count_index)
        subpixel_precision_value = int(getattr(settings, "chromatic_subpixel_precision", 4))
        subpixel_precision_index = max(window.chromatic_subpixel_precision_combo.findData(subpixel_precision_value), 0)
        window.chromatic_subpixel_precision_combo.setCurrentIndex(subpixel_precision_index)
        window._state.preprocessing.chromatic_subpixel_precision = int(
            window.chromatic_subpixel_precision_combo.currentData() or 4
        )
        landmark_kind_value = str(getattr(settings, "chromatic_landmark_kind", "corner") or "corner")
        landmark_kind_index = max(window.chromatic_landmark_kind_combo.findData(landmark_kind_value), 0)
        window.chromatic_landmark_kind_combo.setCurrentIndex(landmark_kind_index)
        window._state.preprocessing.chromatic_landmark_kind = str(
            window.chromatic_landmark_kind_combo.currentData() or "corner"
        )
        landmark_model_value = str(getattr(settings, "chromatic_landmark_model", "similarity") or "similarity")
        landmark_model_index = max(window.chromatic_landmark_model_combo.findData(landmark_model_value), 0)
        window.chromatic_landmark_model_combo.setCurrentIndex(landmark_model_index)
        window._state.preprocessing.chromatic_landmark_model = str(
            window.chromatic_landmark_model_combo.currentData() or "similarity"
        )
        window.chromatic_landmark_id_spin.setMaximum(max(int(window.chromatic_feature_count_spin.currentData() or 15), 1))
        window._state.preprocessing.chromatic_feature_count = int(window.chromatic_feature_count_spin.currentData() or 15)
        window.chromatic_landmark_id_spin.setValue(max(int(window._chromatic_landmark_marker_id), 1))
        window._set_section_applied(window.transforms_section, bool(getattr(settings, "image_tools_enabled", True)))
        window._set_section_applied(window.roi_editor_section, bool(window._read_bool_setting("controls/live_geometry", False)))
        window._set_section_applied(window.background_section, bool(settings.flatten_background_enabled))
        window._set_section_applied(window.chromatic_section, bool(settings.chromatic_correction_enabled))
        highlight_min = getattr(settings, "histogram_highlight_min_value", None)
        highlight_max = getattr(settings, "histogram_highlight_max_value", None)
        if highlight_min is not None and highlight_max is not None:
            lower = float(highlight_min)
            upper = float(highlight_max)
            if lower > upper:
                lower, upper = upper, lower
            window.hist_region.setRegion((lower, upper))
        window.reference_mode_combo.blockSignals(False)
        window.background_removal_link.blockSignals(False)
        window.background_smoothing_sigma_spin.blockSignals(False)
        window.background_smoothing_binning_combo.blockSignals(False)
        window.background_ignore_roi_button.blockSignals(False)
        window.background_ignore_mask_button.blockSignals(False)
        window.background_exclusion_dilation_spin.blockSignals(False)
        window.background_local_reference_check.blockSignals(False)
        window._sync_background_exclusion_buttons()
        window.chromatic_apply_check.blockSignals(False)
        window.chromatic_sample_count_spin.blockSignals(False)
        window.chromatic_feature_count_spin.blockSignals(False)
        window.chromatic_subpixel_precision_combo.blockSignals(False)
        window.chromatic_landmark_kind_combo.blockSignals(False)
        window.chromatic_landmark_model_combo.blockSignals(False)
        window.chromatic_landmark_id_spin.blockSignals(False)
        window.hist_region.blockSignals(False)
        detection = window._state.area_roi_settings
        window._normalize_mask_application_state()
        window.ignore_marked_check.blockSignals(True)
        window.mask_mode_combo.blockSignals(True)
        window.mask_profile_sigma_spin.blockSignals(True)
        window.mask_relative_profile_sigma_spin.blockSignals(True)
        window.mask_relative_threshold_spin.blockSignals(True)
        window.mask_local_sigma_spin.blockSignals(True)
        window.mask_local_z_spin.blockSignals(True)
        window.mask_local_contrast_sigma_spin.blockSignals(True)
        window.mask_local_contrast_z_spin.blockSignals(True)
        window.ignore_marked_check.setChecked(detection.ignore_marked_pixels)
        combo_index = max(window.mask_mode_combo.findData(detection.mask_mode), 0)
        window.mask_mode_combo.setCurrentIndex(combo_index)
        window.mask_profile_sigma_spin.setValue(max(int(round(detection.mask_profile_sigma_px)), 3))
        window.mask_relative_profile_sigma_spin.setValue(max(int(round(detection.mask_profile_sigma_px)), 3))
        window.mask_relative_threshold_spin.setValue(max(float(detection.mask_relative_threshold_fraction) * 100.0, 0.1))
        window.mask_local_sigma_spin.setValue(max(int(round(detection.mask_local_contrast_sigma_px)), 1))
        window.mask_local_contrast_sigma_spin.setValue(max(int(round(detection.mask_local_contrast_sigma_px)), 1))
        window.mask_local_z_spin.setValue(max(float(detection.mask_local_contrast_z_threshold), 0.1))
        window.mask_local_contrast_z_spin.setValue(max(float(detection.mask_local_contrast_z_threshold), 0.1))
        window._set_section_applied(window.mask_section, bool(detection.ignore_marked_pixels))
        window.ignore_marked_check.blockSignals(False)
        window.mask_mode_combo.blockSignals(False)
        window.mask_profile_sigma_spin.blockSignals(False)
        window.mask_relative_profile_sigma_spin.blockSignals(False)
        window.mask_relative_threshold_spin.blockSignals(False)
        window.mask_local_sigma_spin.blockSignals(False)
        window.mask_local_z_spin.blockSignals(False)
        window.mask_local_contrast_sigma_spin.blockSignals(False)
        window.mask_local_contrast_z_spin.blockSignals(False)
        window._update_mask_control_state()
        window._update_reference_controls()
        window._update_reference_summary()
        window._update_apply_button_labels()
        window._update_chromatic_summary()
        window._update_chromatic_control_state()
        window._update_analysis_control_state()

    def restore_visual_preferences(self) -> None:
        window = self._window
        mask_color = QColor(str(window._settings.value("visual/mask_color", window._mask_visual_color.name())))
        sample_color = QColor(str(window._settings.value("visual/sample_color", window._sample_visual_color.name())))
        reference_color = QColor(str(window._settings.value("visual/reference_color", window._reference_visual_color.name())))
        highlight_color = QColor(str(window._settings.value("visual/highlight_color", window._highlight_visual_color.name())))
        scale_bar_color = QColor(str(window._settings.value("visual/scale_bar_color", window._scale_bar_visual_color.name())))
        if mask_color.isValid():
            window._mask_visual_color = mask_color
        if sample_color.isValid():
            window._sample_visual_color = sample_color
        if reference_color.isValid():
            window._reference_visual_color = reference_color
        if highlight_color.isValid():
            window._highlight_visual_color = highlight_color
        if scale_bar_color.isValid():
            window._scale_bar_visual_color = scale_bar_color
        window._mask_alpha = window._alpha01(window._read_float_setting("visual/mask_alpha", window._mask_alpha))
        window._roi_alpha = window._alpha01(window._read_float_setting("visual/spot_alpha", window._roi_alpha))
        window._reference_alpha = window._alpha01(window._read_float_setting("visual/ring_alpha", window._reference_alpha))
        window._highlight_alpha = window._alpha01(window._read_float_setting("visual/highlight_alpha", window._highlight_alpha))
        window._rois_visible = window._read_bool_setting("visual/rois_visible", window._rois_visible)
        window._roi_labels_visible = window._read_bool_setting("visual/spot_labels_visible", window._roi_labels_visible)
        window._mask_visible = window._read_bool_setting("visual/mask_visible", window._mask_visible)
        window._reference_visible = window._read_bool_setting("visual/reference_rois_visible", window._reference_visible)
        window._highlight_visible = window._read_bool_setting("visual/highlight_visible", window._highlight_visible)
        window._reference_points_visible = window._read_bool_setting("visual/reference_points_visible", window._reference_points_visible)
        window._chromatic_reference_points_all_visible = window._read_bool_setting(
            "visual/chromatic_reference_points_all_visible",
            window._chromatic_reference_points_all_visible,
        )
        window._showing_background_profile_main = window._read_bool_setting(
            "visual/background_profile_visible",
            window._showing_background_profile_main,
        )
        window._sync_background_profile_buttons(window._showing_background_profile_main)
        window._restore_saved_image_view_after_load()

        window._spectrum_fit_line_width_px = window._read_float_setting(
            "plot_style/spectrum_fit_line_width_px", window._spectrum_fit_line_width_px
        )
        window._spectrum_fit_line_style = Qt.PenStyle(
            window._settings_int("plot_style/spectrum_fit_line_style", int(window._spectrum_fit_line_style.value))
        )
        window._spectrum_symbol_size_px = window._read_float_setting(
            "plot_style/spectrum_symbol_size_px", window._spectrum_symbol_size_px
        )
        window._sensorgram_line_width_px = window._read_float_setting(
            "plot_style/sensorgram_line_width_px", window._sensorgram_line_width_px
        )
        window._sensorgram_line_style = Qt.PenStyle(
            window._settings_int("plot_style/sensorgram_line_style", int(window._sensorgram_line_style.value))
        )
        window._sensorgram_processed_line_width_px = window._read_float_setting(
            "plot_style/sensorgram_processed_line_width_px", window._sensorgram_processed_line_width_px
        )
        window._sensorgram_processed_line_style = Qt.PenStyle(
            window._settings_int(
                "plot_style/sensorgram_processed_line_style", int(window._sensorgram_processed_line_style.value)
            )
        )
        processed_color = QColor(
            str(window._settings.value("plot_style/sensorgram_processed_color", window._sensorgram_processed_color.name()))
        )
        if processed_color.isValid():
            window._sensorgram_processed_color = processed_color
        window._sensorgram_group_line_width_px = window._read_float_setting(
            "plot_style/sensorgram_group_line_width_px", window._sensorgram_group_line_width_px
        )
        window._sensorgram_group_line_style = Qt.PenStyle(
            window._settings_int("plot_style/sensorgram_group_line_style", int(window._sensorgram_group_line_style.value))
        )
        group_color = QColor(
            str(window._settings.value("plot_style/sensorgram_group_color", window._sensorgram_group_color.name()))
        )
        if group_color.isValid():
            window._sensorgram_group_color = group_color

    def save_visual_preferences(self) -> None:
        window = self._window
        window._settings.setValue("visual/mask_color", window._mask_visual_color.name())
        window._settings.setValue("visual/sample_color", window._sample_visual_color.name())
        window._settings.setValue("visual/reference_color", window._reference_visual_color.name())
        window._settings.setValue("visual/highlight_color", window._highlight_visual_color.name())
        window._settings.setValue("visual/scale_bar_color", window._scale_bar_visual_color.name())
        window._settings.setValue("visual/mask_alpha", float(window._mask_alpha))
        window._settings.setValue("visual/spot_alpha", float(window._roi_alpha))
        window._settings.setValue("visual/ring_alpha", float(window._reference_alpha))
        window._settings.setValue("visual/highlight_alpha", float(window._highlight_alpha))
        window._settings.setValue("visual/rois_visible", window._rois_visible)
        window._settings.setValue("visual/spot_labels_visible", window._roi_labels_visible)
        window._settings.setValue("visual/mask_visible", window._mask_visible)
        window._settings.setValue("visual/reference_rois_visible", window._reference_visible)
        window._settings.setValue("visual/highlight_visible", window._highlight_visible)
        window._settings.setValue("visual/reference_points_visible", window._reference_points_visible)
        window._settings.setValue("visual/chromatic_reference_points_all_visible", window._chromatic_reference_points_all_visible)
        window._settings.setValue("visual/background_profile_visible", window._showing_background_profile_main)
        if window._current_processed_image is not None and not window._showing_background_profile_main:
            x_range, y_range = window.image_plot.vb.viewRange()
            window._settings.setValue("visual/image_view_x_min", float(x_range[0]))
            window._settings.setValue("visual/image_view_x_max", float(x_range[1]))
            window._settings.setValue("visual/image_view_y_min", float(y_range[0]))
            window._settings.setValue("visual/image_view_y_max", float(y_range[1]))
        window._settings.setValue("plot_style/spectrum_fit_line_width_px", float(window._spectrum_fit_line_width_px))
        window._settings.setValue("plot_style/spectrum_fit_line_style", int(window._spectrum_fit_line_style.value))
        window._settings.setValue("plot_style/spectrum_symbol_size_px", float(window._spectrum_symbol_size_px))
        window._settings.setValue("plot_style/sensorgram_line_width_px", float(window._sensorgram_line_width_px))
        window._settings.setValue("plot_style/sensorgram_line_style", int(window._sensorgram_line_style.value))
        window._settings.setValue(
            "plot_style/sensorgram_processed_line_width_px", float(window._sensorgram_processed_line_width_px)
        )
        window._settings.setValue(
            "plot_style/sensorgram_processed_line_style", int(window._sensorgram_processed_line_style.value)
        )
        window._settings.setValue("plot_style/sensorgram_processed_color", window._sensorgram_processed_color.name())
        window._settings.setValue(
            "plot_style/sensorgram_group_line_width_px", float(window._sensorgram_group_line_width_px)
        )
        window._settings.setValue("plot_style/sensorgram_group_line_style", int(window._sensorgram_group_line_style.value))
        window._settings.setValue("plot_style/sensorgram_group_color", window._sensorgram_group_color.name())

    def save_control_preferences(self) -> None:
        window = self._window
        window._settings.setValue("controls/live_geometry", bool(window.roi_editor_section.is_applied()))
        window._settings.setValue("histogram_bin_size", int(window.histogram_bins_spin.value()))
        window._settings.setValue("analysis/fit_method", str(window.analysis_fit_method_combo.currentData() or "none"))
        window._settings.setValue("analysis/poly_order", int(window.analysis_poly_order_spin.value()))
        window._settings.setValue("analysis/metric", str(window.analysis_metric_combo.currentData() or "centroid"))
        window._settings.setValue("analysis/spectral_cube_start", int(window.analysis_start_spectral_cube_spin.value()))
        window._settings.setValue("analysis/spectral_cube_end", int(window.analysis_end_spectral_cube_spin.value()))
        window._settings.setValue("analysis/wavelength_min_nm", float(window.analysis_wavelength_min_spin.value()))
        window._settings.setValue("analysis/wavelength_max_nm", float(window.analysis_wavelength_max_spin.value()))
        window._settings.setValue("analysis/live_preview", bool(window._analysis_live_preview_enabled))
        window._settings.setValue("histogram/log_y", bool(window._histogram_log_y_enabled))
        lower, upper = window.hist_region.getRegion()
        if lower > upper:
            lower, upper = upper, lower
        window._settings.setValue("histogram/highlight_min", float(lower))
        window._settings.setValue("histogram/highlight_max", float(upper))
        window._settings.setValue("selection/area_roi_ids", json.dumps(sorted(int(area_roi_id) for area_roi_id in window._selected_roi_ids)))
        window._settings.setValue("mask_tools/relative_sigma_px", int(window.mask_relative_profile_sigma_spin.value()))
        window._settings.setValue("mask_tools/relative_threshold_percent", float(window.mask_relative_threshold_spin.value()))
        window._settings.setValue("mask_tools/local_sigma_px", int(window.mask_local_contrast_sigma_spin.value()))
        window._settings.setValue("mask_tools/local_z", float(window.mask_local_contrast_z_spin.value()))

    def restore_control_preferences(self) -> None:
        window = self._window
        window._histogram_log_y_enabled = window._settings_bool("histogram/log_y", False)
        window._apply_histogram_log_mode(refresh=not window._startup_restore_in_progress)
        window._sync_reference_selection_from_settings()
        window._sync_image_processing_controls()
        window._sync_roi_detection_controls()

    def update_reference_controls(self) -> None:
        window = self._window
        settings = window._state.preprocessing
        has_dataset = window._state.dataset is not None
        manual_enabled = has_dataset and bool(window._spectral_cube_values) and bool(window._wavelength_values)
        auto_enabled = has_dataset and bool(window._spectral_cube_values)
        window.reference_auto_button.setEnabled(auto_enabled)
        window.reference_manual_button.setEnabled(manual_enabled)
        if window.reference_auto_button.isChecked() != (str(settings.reference_mode or "auto") != "manual"):
            window.reference_auto_button.blockSignals(True)
            window.reference_manual_button.blockSignals(True)
            window.reference_auto_button.setChecked(str(settings.reference_mode or "auto") != "manual")
            window.reference_manual_button.setChecked(str(settings.reference_mode or "auto") == "manual")
            window.reference_auto_button.blockSignals(False)
            window.reference_manual_button.blockSignals(False)

    def update_analysis_control_state(self) -> None:
        window = self._window
        enabled = bool(window._analysis_enabled)
        has_dataset = window._state.dataset is not None and bool(window._spectral_cube_values) and bool(window._wavelength_values)
        interactive = enabled and has_dataset and not window._sensorgram_running
        window.analysis_refresh_button.setEnabled(interactive)
        window.analysis_refresh_button.setPixmap(
            window._make_analysis_spectrum_icon(enabled and has_dataset).pixmap(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner)
        )
        preview_enabled = enabled and has_dataset
        window.analysis_preview_button.setEnabled(preview_enabled)
        window.analysis_preview_button.setPixmap(
            window._make_analysis_preview_icon(preview_enabled and window._analysis_live_preview_enabled).pixmap(
                APP_THEME.compact_icon_inner,
                APP_THEME.compact_icon_inner,
            )
        )
        window.analysis_calculate_all_button.setEnabled(interactive)
        window.analysis_calculate_all_button.setPixmap(
            window._make_analysis_all_spectral_cubes_icon(enabled and has_dataset).pixmap(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner)
        )
        window.analysis_stop_button.setEnabled(enabled and window._sensorgram_running)
        window.analysis_stop_button.setPixmap(
            window._make_analysis_stop_icon(enabled and window._sensorgram_running).pixmap(APP_THEME.compact_icon_inner, APP_THEME.compact_icon_inner)
        )

    def update_chromatic_control_state(self) -> None:
        window = self._window
        has_dataset = window._state.dataset is not None
        sample_keys = window._chromatic_sample_image_keys() if has_dataset else []
        current_index = window._current_chromatic_sample_index() if has_dataset else None
        feature_ids = window._expected_chromatic_feature_ids() if has_dataset else []
        has_samples = bool(sample_keys)
        can_navigate = has_samples and current_index is not None and len(sample_keys) > 1
        can_edit = has_samples
        controls_enabled = has_dataset and not window._chromatic_auto_running

        window.chromatic_sample_count_spin.setEnabled(controls_enabled)
        window.chromatic_feature_count_spin.setEnabled(controls_enabled)
        window.chromatic_subpixel_precision_combo.setEnabled(controls_enabled)
        window.chromatic_landmark_kind_combo.setEnabled(controls_enabled)
        window.chromatic_landmark_model_combo.setEnabled(controls_enabled)
        window.chromatic_reference_points_all_button.setEnabled(has_dataset and bool(feature_ids))
        window.chromatic_start_button.setEnabled(can_edit and controls_enabled)
        window.chromatic_grid_button.setEnabled(has_dataset and controls_enabled)
        window.chromatic_grid_reset_button.setEnabled(has_dataset and controls_enabled)
        window.chromatic_auto_button.setEnabled(has_dataset and controls_enabled and has_samples)
        window.chromatic_prev_button.setEnabled(can_navigate and controls_enabled)
        window.chromatic_next_button.setEnabled(can_navigate and controls_enabled)
        window.chromatic_landmark_clear_button.setEnabled(has_dataset and bool(window._state.chromatic_landmarks) and controls_enabled)
        window.chromatic_landmark_id_spin.setEnabled(can_edit and controls_enabled)
        window.chromatic_transform_button.setEnabled(has_dataset and controls_enabled)
        window.chromatic_reset_transforms_button.setEnabled(has_dataset and controls_enabled and bool(window._state.chromatic_models))
        window.chromatic_apply_check.setEnabled(has_dataset and controls_enabled)
        window.chromatic_apply_check.setChecked(bool(window._state.preprocessing.chromatic_correction_enabled))
        window.chromatic_apply_check.setIcon(window._make_link_toggle_icon(bool(window.chromatic_apply_check.isChecked())))
        window.chromatic_start_button.setChecked(bool(window._active_tool == "chromatic_landmark"))
        window._update_chromatic_summary()

    def sync_roi_detection_controls(self) -> None:
        window = self._window
        settings = window._state.area_roi_settings
        window._update_geometry_control_ranges(window._current_processed_image.shape if window._current_processed_image is not None else None)
        window.sample_diameter_spin.blockSignals(True)
        window.reference_inner_diameter_spin.blockSignals(True)
        window.reference_outer_diameter_spin.blockSignals(True)
        window.array_rows_spin.blockSignals(True)
        window.array_cols_spin.blockSignals(True)
        window.array_spacing_spin.blockSignals(True)
        window.ignore_marked_check.blockSignals(True)
        window.ignore_region.blockSignals(True)
        window.sample_diameter_spin.setValue(max(0.0, window._format_length_display_value(2 * settings.sample_radius_px)))
        window.reference_inner_diameter_spin.setValue(max(0.0, window._format_length_display_value(2 * settings.reference_inner_radius_px)))
        window.reference_outer_diameter_spin.setValue(max(0.0, window._format_length_display_value(2 * settings.reference_outer_radius_px)))
        window.array_rows_spin.setValue(max(0, settings.array_rows))
        window.array_cols_spin.setValue(max(0, settings.array_cols))
        window.array_spacing_spin.setValue(max(0.0, window._length_px_to_display(settings.array_spacing_px)))
        window.ignore_marked_check.setChecked(settings.ignore_marked_pixels)
        window._set_section_applied(window.mask_section, bool(settings.ignore_marked_pixels))
        if window._current_processed_image is not None:
            default_lower, default_upper = window._default_ignored_intensity_range(window._current_processed_image)
            lower = default_lower if settings.ignored_intensity_min_value is None else float(settings.ignored_intensity_min_value)
            upper = default_upper if settings.ignored_intensity_max_value is None else float(settings.ignored_intensity_max_value)
            if lower > upper:
                lower, upper = upper, lower
            window.ignore_region.setRegion((lower, upper))
        window.sample_diameter_spin.blockSignals(False)
        window.reference_inner_diameter_spin.blockSignals(False)
        window.reference_outer_diameter_spin.blockSignals(False)
        window.array_rows_spin.blockSignals(False)
        window.array_cols_spin.blockSignals(False)
        window.array_spacing_spin.blockSignals(False)
        window.ignore_marked_check.blockSignals(False)
        window.ignore_region.blockSignals(False)
        window._update_mask_file_button_state()
        window._update_display_unit_controls()
        window._update_roi_detection_labels()
        window._update_roi_summary()
        window._update_ignore_mask_overlay()
        window._update_histogram_region_labels()
        window._update_apply_button_labels()

    def update_mask_control_state(self) -> None:
        window = self._window
        mode = str(window.mask_mode_combo.currentData() or "absolute")
        is_absolute = mode == "absolute"
        window.mask_profile_sigma_spin.setEnabled(False)
        window.mask_local_sigma_spin.setEnabled(False)
        window.mask_local_z_spin.setEnabled(False)
        window.mask_relative_profile_sigma_spin.setEnabled(True)
        window.mask_relative_threshold_spin.setEnabled(True)
        window.mask_local_contrast_sigma_spin.setEnabled(True)
        window.mask_local_contrast_z_spin.setEnabled(True)
        window.mask_draw_add_button.setEnabled(window.mask_pencil_check.isChecked())
        window.mask_draw_remove_button.setEnabled(window.mask_pencil_check.isChecked())
        window.mask_mode_hint.setVisible(is_absolute)

    def sync_ome_zarr_chunk_controls(self) -> None:
        window = self._window
        if window._ome_zarr_chunk_controls_syncing:
            return
        window._ome_zarr_chunk_controls_syncing = True
        try:
            size = window._dataset_controller._current_ome_zarr_chunk_size()
            window._settings.setValue("ome_zarr/chunk_size_px", int(size))
            window.ome_zarr_chunk_guide_button.setIcon(window._ome_zarr_grid_icon(window.ome_zarr_chunk_guide_button.isChecked()))
            compression_enabled = window._dataset_controller._current_ome_zarr_compression_enabled()
            window._settings.setValue("ome_zarr/compression_enabled", bool(compression_enabled))
            window.ome_zarr_compression_label.setText(f"Compression: {'on' if compression_enabled else 'off'}")
            window.ome_zarr_compression_button.setIcon(window._ome_zarr_compression_icon(compression_enabled))
            window._update_ome_zarr_chunk_guide_overlay()
        finally:
            window._ome_zarr_chunk_controls_syncing = False
