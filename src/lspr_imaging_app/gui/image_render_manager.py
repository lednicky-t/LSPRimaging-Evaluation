from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time

import numpy as np

from lspr_imaging_app.processing.chromatic import (
    apply_affine_to_points,
    transformed_circle_points,
    transform_rois_affine,
    warp_boolean_mask_affine,
)


def _apply_dense_overrides(transformed: list, original: list, image_key: tuple[int, float] | None) -> list:
    """Overwrite each transformed ROI's center with its persisted per-wavelength
    override (AreaRoi.per_wavelength), where one exists for `image_key`. Leaves
    ROIs without an override exactly as the affine-derived transform produced
    them, so this is a no-op until an ROI actually has a manual per-wavelength
    edit or a dense-populated entry for this wavelength."""
    if image_key is None:
        return transformed
    for transformed_roi, original_roi in zip(transformed, original):
        if not original_roi.per_wavelength:
            continue
        override = original_roi.per_wavelength.get(image_key)
        if override is not None:
            transformed_roi.center_x, transformed_roi.center_y = override
    return transformed


class ImageRenderManager:
    def __init__(self, window) -> None:
        self.window = window

    def refresh_image(self) -> None:
        window = self.window
        dataset = window._state.dataset
        if dataset is None:
            return
        spectral_cube_index = window._current_spectral_cube()
        wavelength = window._current_wavelength()
        if spectral_cube_index is None or wavelength is None:
            return
        if str(window._state.preprocessing.reference_mode or "auto") != "manual":
            auto_key = window._auto_reference_image_key_for_spectral_cube(spectral_cube_index)
            if auto_key is not None:
                auto_spectral_cube, auto_wavelength = auto_key
                window._state.preprocessing.reference_mode = "auto"
                window._state.preprocessing.reference_spectral_cube_index = int(auto_spectral_cube)
                window._state.preprocessing.reference_wavelength_nm = float(auto_wavelength)
        window.spectral_cube_spin.blockSignals(True)
        window.spectral_cube_spin.setValue(int(spectral_cube_index))
        window.spectral_cube_spin.blockSignals(False)
        window.wavelength_spin.blockSignals(True)
        window.wavelength_spin.setValue(float(wavelength))
        window.wavelength_spin.blockSignals(False)
        window._update_reference_controls()
        window._update_reference_summary()
        window._update_metadata_status_label()
        window._update_sensorgram_current_point()
        record = window._record_map.get((spectral_cube_index, wavelength))
        if record is None:
            window.status_label.setText("Selected spectral cube/wavelength combination is missing.")
            return
        image_key = (spectral_cube_index, wavelength)
        if window._current_image_key == image_key:
            return
        preprocessing = deepcopy(window._state.preprocessing)
        mask_settings = deepcopy(window._state.area_roi_settings) if preprocessing.flatten_background_exclude_mask else None
        rois = deepcopy(window._rois_for_preprocessing(image_key))
        _external_mask, external_mask_processed = window._effective_external_mask_for_record(record.path, processed_space=True)
        cache_key = window._processed_image_cache_key(record.path, image_key)
        signature = cache_key
        window._latest_image_refresh_signature = signature
        cached = window._get_processed_image_from_cache(cache_key)
        if cached is not None:
            started_at = time.perf_counter()
            window._apply_loaded_image(cached, record.path, image_key, spectral_cube_index, wavelength, record.path.name)
            elapsed = window._format_elapsed_seconds(time.perf_counter() - started_at)
            window._set_status_text(f"Img spectral cube {spectral_cube_index} {wavelength:g}nm | cache {elapsed}")
            return
        window._pending_image_refresh_payload = (
            signature,
            cache_key,
            str(record.path),
            record.path,
            image_key,
            spectral_cube_index,
            wavelength,
            record.path.name,
            (preprocessing, mask_settings, external_mask_processed),
            rois,
        )
        if not window._image_refresh_running:
            self.start_pending_image_refresh()
        window.status_label.setText(f"Loading {record.path.name}...")

    def start_pending_image_refresh(self) -> None:
        window = self.window
        if window._pending_image_refresh_payload is None:
            return
        (
            signature,
            cache_key,
            path_str,
            record_path,
            image_key,
            spectral_cube_index,
            wavelength,
            record_name,
            preprocessing,
            rois,
        ) = window._pending_image_refresh_payload
        from lspr_imaging_app.gui.main_window import FunctionWorker, _process_image_task

        external_mask, external_mask_processed = window._effective_external_mask_for_record(record_path, processed_space=True)
        if external_mask is not None:
            # Ignore-mask geometry is authored once, independent of wavelength; carry
            # it into this image_key's corrected geometry so background flattening's
            # "exclude mask" region stays aligned with the feature it was drawn over,
            # the same way the absorbance/sensorgram payload builders in
            # analysis_controller.py already do for the final ROI calculation.
            affine_matrix = window._chromatic_affine_for_image_key(image_key)
            if affine_matrix is not None:
                external_mask = warp_boolean_mask_affine(np.asarray(external_mask, dtype=bool), affine_matrix)
        skip_crop = bool(getattr(window, "_image_tools_preview_only", False))
        window._pending_image_refresh_payload = None
        window._image_refresh_running = True
        window._image_refresh_started_at = time.perf_counter()
        window._begin_busy(f"Loading {record_name}...")
        if isinstance(preprocessing, tuple):
            preprocessing = (preprocessing[0], preprocessing[1], external_mask_processed, skip_crop)
        worker = FunctionWorker(
            _process_image_task,
            path_str,
            preprocessing,
            rois,
            external_mask,
            window._state.mask if window._mask_section_applied() else None,
            window._image_refresh_started_at,
        )
        worker.signals.result.connect(
            lambda result,
            signature=signature,
            cache_key=cache_key,
            record_path=record_path,
            image_key=image_key,
            spectral_cube_index=spectral_cube_index,
            wavelength=wavelength,
            record_name=record_name: self.on_image_refresh_ready(
                signature,
                cache_key,
                record_path,
                image_key,
                spectral_cube_index,
                wavelength,
                record_name,
                result[0],
                result[1],
            )
        )
        worker.signals.error.connect(lambda message: self.on_image_refresh_failed(message))
        window._thread_pool.start(worker)

    def on_image_refresh_ready(
        self,
        signature: tuple[object, ...],
        cache_key: tuple[object, ...],
        record_path: Path,
        image_key: tuple[int, float],
        spectral_cube_index: int,
        wavelength: float,
        record_name: str,
        processed: np.ndarray,
        background_total_ms: float | None,
    ) -> None:
        window = self.window
        window._image_refresh_running = False
        started_at = window._image_refresh_started_at
        window._image_refresh_started_at = None
        window._end_busy()
        window._sync_busy_cursor_state()
        window._store_processed_image_in_cache(cache_key, processed)
        # Measured and logged *before* apply_loaded_image runs below - this
        # used to be computed after it (a bug: "Image load" silently included
        # the entirety of "Image apply" inside it, despite the comment here
        # claiming otherwise). background_total_ms (reported by
        # _process_image_task itself: queue + raw-TIFF-read + apply_preprocessing,
        # already broken down further by the "Image raw load" and "Image
        # process stages" lines) vs. gui_wait_ms (everything else in this
        # total - i.e. how long the finished result sat waiting for the GUI
        # thread to actually become free and process the queued cross-thread
        # signal) are two very different problems with different causes, so
        # collapsing them into one "Image load" number was hiding which one
        # actually mattered on a slow switch.
        if started_at is not None:
            total_since_dispatch_ms = (time.perf_counter() - started_at) * 1000.0
            elapsed = window._format_elapsed_seconds(total_since_dispatch_ms / 1000.0)
            if elapsed:
                window._set_status_text(f"Img spectral cube {spectral_cube_index} {wavelength:g}nm | load {elapsed}")
                if background_total_ms is None:
                    window._append_workflow_log_throttled(
                        "image_load", f"Image load | total={total_since_dispatch_ms:.0f}ms", level="debug", min_interval=2.0
                    )
                else:
                    gui_wait_ms = max(total_since_dispatch_ms - background_total_ms, 0.0)
                    window._append_workflow_log_throttled(
                        "image_load",
                        f"Image load | background={background_total_ms:.0f}ms gui_wait={gui_wait_ms:.0f}ms total={total_since_dispatch_ms:.0f}ms",
                        level="debug",
                        min_interval=2.0,
                    )
        apply_started_at = time.perf_counter()
        if signature == window._latest_image_refresh_signature:
            self.apply_loaded_image(processed, record_path, image_key, spectral_cube_index, wavelength, record_name)
            apply_elapsed = window._format_elapsed_seconds(time.perf_counter() - apply_started_at)
            if apply_elapsed:
                window._append_workflow_log_throttled("image_apply", f"Image apply | {apply_elapsed}", level="debug", min_interval=2.0)
        if window._pending_image_refresh_payload is not None:
            self.start_pending_image_refresh()

    def on_image_refresh_failed(self, message: str) -> None:
        window = self.window
        window._image_refresh_running = False
        window._image_refresh_started_at = None
        window._end_busy()
        window._sync_busy_cursor_state()
        window._background_error("Image refresh", message)
        if window._pending_image_refresh_payload is not None:
            self.start_pending_image_refresh()

    def apply_loaded_image(
        self,
        processed: np.ndarray,
        record_path: Path,
        image_key: tuple[int, float],
        spectral_cube_index: int,
        wavelength: float,
        record_name: str,
    ) -> None:
        window = self.window
        # Stage timing, one line at the end - see apply_preprocessing's
        # log_stage_timing docstring for why this is safe to always compute
        # (one call per wavelength switch, negligible perf_counter overhead)
        # and only visible in the console when Debug mode is on. "Image
        # apply" (on_image_refresh_ready) already reports this function's
        # total; this breaks that total down by what it's actually spending
        # time on, since the total alone couldn't say whether a slow apply
        # was overlays, chromatic sync, histogram, or something else.
        stage_started_at = time.perf_counter()
        stage_timings: list[tuple[str, float]] = []

        def _mark_stage(label: str) -> None:
            nonlocal stage_started_at
            now = time.perf_counter()
            stage_timings.append((label, (now - stage_started_at) * 1000.0))
            stage_started_at = now

        window._current_processed_image = processed
        window._current_record_path = record_path
        window._current_image_key = image_key
        window._auto_load_mask_for_current_record()
        window._invalidate_per_frame_display_caches()
        window._invalidate_background_profile_cache()
        _mark_stage("setup")
        # Circle/annulus ROI overlays are drawn at a per-wavelength position
        # (via the chromatic affine, see display_rois/roi_curve_points above)
        # but that transformed position is only ever pushed to the on-screen
        # curve items by an explicit _update_roi_overlays() call - without
        # this, the overlay stays wherever it was last drawn and never
        # visibly follows the wavelength, even though ROI pixel sampling for
        # the actual calculation already uses the correct per-wavelength
        # position. (Its own cost is reported separately by
        # OverlayManager._update_roi_overlays as "ROI overlay rebuild" -
        # excluded from this function's stage breakdown below so it isn't
        # counted twice; re-baseline stage_started_at past it.)
        window._update_roi_overlays()
        stage_started_at = time.perf_counter()
        window._update_geometry_control_ranges(processed.shape)
        window._update_mask_file_button_state()
        window._refresh_mask_previews()
        _mark_stage("geom_mask")
        is_reference_view = window._is_current_reference_image()
        window.detect_rois_button.setEnabled(is_reference_view)
        window.roi_detection_full_auto_button.setEnabled(is_reference_view)
        window.roi_auto_histogram_action.setEnabled(is_reference_view)
        if not is_reference_view and not window._state.preprocessing.chromatic_correction_enabled:
            window.mask_pencil_check.blockSignals(True)
            window.mask_pencil_check.setChecked(False)
            window.mask_pencil_check.blockSignals(False)
        window._sync_roi_edit_capabilities()
        if hasattr(window, "rotation_fill_dark_button"):
            window.rotation_fill_dark_button.blockSignals(True)
            window.rotation_fill_dark_button.setChecked(window._state.preprocessing.rotation_fill_dark)
            window.rotation_fill_dark_button.blockSignals(False)
            window.rotation_fill_dark_button.setIcon(
                window._make_rotation_fill_icon(window.rotation_fill_dark_button.isChecked())
            )
            window._update_rotation_fill_button_tooltip()
        if hasattr(window, "flip_horizontal_action"):
            window.flip_horizontal_action.blockSignals(True)
            window.flip_horizontal_action.setChecked(window._state.preprocessing.flip_horizontal)
            window.flip_horizontal_action.blockSignals(False)
        if hasattr(window, "flip_vertical_action"):
            window.flip_vertical_action.blockSignals(True)
            window.flip_vertical_action.setChecked(window._state.preprocessing.flip_vertical)
            window.flip_vertical_action.blockSignals(False)
        _mark_stage("roi_edit_ui")
        # Split into 4 sub-stages rather than one "chromatic" bucket - this
        # bucket measured ~170ms even with chromatic correction OFF, which
        # was unexpected enough (none of these four look individually
        # expensive by inspection) that guessing which one was responsible
        # wasn't good enough; this settles it directly instead of needing
        # another round-trip.
        window._seed_chromatic_landmarks_for_current_image()
        _mark_stage("chrom_seed")
        window._sync_current_chromatic_feature_selection()
        _mark_stage("chrom_feature_sel")
        window._update_chromatic_control_state()
        _mark_stage("chrom_state")
        window._sync_analysis_plots()
        if window._activate_chromatic_tool_after_refresh and window._is_chromatic_sample_image_key(window._current_image_key):
            window._activate_chromatic_tool_after_refresh = False
            window.chromatic_landmark_mark_button.setChecked(True)
        _mark_stage("analysis_plots")
        # skip_roi_overlay_refresh=True: the explicit _update_roi_overlays()
        # call above already rebuilt every ROI overlay for this image_key -
        # nothing between there and here repositions an ROI or changes the
        # chromatic/crop signature display_rois() caches on, so
        # _apply_main_image_content -> _sync_main_view_mode's own overlay
        # rebuild would just redo the identical 160-ROI work a second time
        # (measured ~90-115ms wasted per wavelength switch).
        window._apply_main_image_content(skip_roi_overlay_refresh=True)
        window._update_reference_star_overlay()
        _mark_stage("content")
        window._update_image_exclusion_indicator(spectral_cube_index, wavelength)
        window._dataset_controller._sync_ome_zarr_chunk_controls()
        window._update_ome_zarr_chunk_guide_overlay()
        _mark_stage("exclusion")
        restored_view = window._restore_chromatic_view_after_load()
        if not restored_view:
            restored_view = self.restore_pending_image_view_after_load()
        if not restored_view:
            restored_view = self.restore_saved_image_view_after_load()
        if bool(getattr(window, "_force_image_autorange_after_load", False)):
            window._force_image_autorange_after_load = False
            window.image_plot.autoRange()
            restored_view = True
        if not restored_view:
            window.image_plot.autoRange()
        _mark_stage("view")
        window._update_image_name_overlay(None)
        if not window._chromatic_setup_active and not bool(getattr(window, "_image_tools_preview_only", False)):
            window._update_histogram(processed)
        _mark_stage("histogram")
        window._sync_rotation_tool()
        window._sync_crop_tool(processed.shape)
        window._sync_chromatic_grid_tool(processed.shape)
        window._update_landmark_overlays()
        _mark_stage("tools")
        window._set_status_text(f"Img spectral cube {spectral_cube_index} {wavelength:g}nm")
        if not window._chromatic_setup_active and not bool(getattr(window, "_image_tools_preview_only", False)):
            window._schedule_processing_state_save()
            window._refresh_visible_spectrum_from_cache()
            window._analysis_controller.preview_sensorgram_from_cache()
        _mark_stage("status_and_preview")
        total_ms = sum(elapsed for _label, elapsed in stage_timings)
        window._workflow_logger.debug(
            "Image apply stages | "
            + " ".join(f"{label}={elapsed:.0f}ms" for label, elapsed in stage_timings)
            + f" total={total_ms:.0f}ms"
        )
        # Used to also call _request_roi_metrics_refresh() here to recompute
        # AreaRoi.score against the newly-shown reference image - removed
        # since nothing reads a placed ROI's score outside of Detect ROIs
        # itself, which computes its own fresh scores rather than reusing
        # this one (see _apply_roi_geometry_preview for the same reasoning).

    def on_image_view_range_changed(self, *_args) -> None:
        window = self.window
        if window._current_processed_image is None or window._showing_background_profile_main:
            return
        if window._image_view_save_timer.isActive():
            window._image_view_save_timer.stop()
        window._image_view_save_timer.start()

    def display_rois(self, image_key: tuple[int, float] | None = None) -> list:
        window = self.window
        target_key = image_key if image_key is not None else window._current_image_key
        if target_key is None:
            return window._state.area_rois
        if window._is_reference_image_key(target_key):
            return window._state.area_rois
        if window._current_processed_image is None and image_key is None:
            return window._state.area_rois
        signature = (
            target_key,
            window._roi_signature(window._state.area_rois),
            window._roi_override_signature(window._state.area_rois, target_key),
            window._chromatic_signature_for_image_key(target_key),
            None if window._current_processed_image is None else window._current_processed_image.shape[:2],
        )
        if window._display_roi_cache_signature == signature and window._display_roi_cache_value is not None:
            return window._display_roi_cache_value
        affine_matrix = window._chromatic_affine_for_image_key(target_key)
        if affine_matrix is None:
            transformed = window._state.area_rois
        else:
            clamp_shape = window._current_processed_image.shape[:2] if window._current_processed_image is not None else None
            transformed = transform_rois_affine(window._state.area_rois, affine_matrix, clamp_shape=clamp_shape)
            transformed = _apply_dense_overrides(transformed, window._state.area_rois, target_key)
        window._display_roi_cache_signature = signature
        window._display_roi_cache_value = transformed
        return transformed

    def rois_for_preprocessing(self, image_key: tuple[int, float] | None) -> list:
        window = self.window
        if image_key is None or window._is_reference_image_key(image_key):
            return window._state.area_rois
        if not window._state.preprocessing.chromatic_correction_enabled:
            return window._state.area_rois
        affine_matrix = window._chromatic_affine_for_image_key(image_key)
        if affine_matrix is None:
            return window._state.area_rois
        transformed = transform_rois_affine(window._state.area_rois, affine_matrix)
        return _apply_dense_overrides(transformed, window._state.area_rois, image_key)

    def roi_curve_points(
        self,
        source_roi,
        display_roi,
        radius_px: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        window = self.window
        affine_matrix = window._chromatic_affine_for_image_key(window._current_image_key)
        if affine_matrix is None or window._is_current_reference_image():
            theta = window._roi_overlay_theta
            xs = display_roi.center_x + float(radius_px) * np.cos(theta)
            ys = display_roi.center_y + float(radius_px) * np.sin(theta)
            return xs, ys
        xs, ys = transformed_circle_points(
            (float(source_roi.center_x), float(source_roi.center_y)),
            float(radius_px),
            affine_matrix,
            window._roi_overlay_theta,
        )
        # display_roi.center may carry a persisted per-wavelength override
        # (AreaRoi.per_wavelength) that differs from the plain affine-derived
        # position above - shift the warped outline to match it so the drawn
        # circle/ellipse tracks a manual nudge instead of silently reverting
        # to the CC-derived spot. A no-op when there's no override, since
        # display_roi.center then equals the affine-derived point exactly.
        derived_center = apply_affine_to_points(
            np.asarray([[float(source_roi.center_x), float(source_roi.center_y)]], dtype=np.float64),
            affine_matrix,
        )[0]
        dx = float(display_roi.center_x) - float(derived_center[0])
        dy = float(display_roi.center_y) - float(derived_center[1])
        return xs + dx, ys + dy

    def set_view_overlay_visibility(
        self,
        *,
        rois_visible: bool,
        reference_rois_visible: bool,
        mask_visible: bool,
        reference_points_visible: bool,
        highlight_visible: bool,
    ) -> None:
        window = self.window
        window._rois_visible = bool(rois_visible)
        window._reference_visible = bool(reference_rois_visible)
        window._mask_visible = bool(mask_visible)
        window._reference_points_visible = bool(reference_points_visible)
        window._highlight_visible = bool(highlight_visible)
        window.show_rois_check.blockSignals(True)
        window.show_reference_check.blockSignals(True)
        window.show_mask_check.blockSignals(True)
        window.show_reference_points_check.blockSignals(True)
        window.show_highlight_check.blockSignals(True)
        window.show_rois_check.setChecked(window._rois_visible)
        window.show_reference_check.setChecked(window._reference_visible)
        window.show_mask_check.setChecked(window._mask_visible)
        window.show_reference_points_check.setChecked(window._reference_points_visible)
        window.show_highlight_check.setChecked(window._highlight_visible)
        window.show_rois_check.blockSignals(False)
        window.show_reference_check.blockSignals(False)
        window.show_mask_check.blockSignals(False)
        window.show_reference_points_check.blockSignals(False)
        window.show_highlight_check.blockSignals(False)
        window._refresh_view_toggle_icons()
        window._update_roi_overlays()
        window._update_ignore_mask_overlay()
        window._update_selected_intensity_overlay()
        window._update_landmark_overlays()

    def capture_pending_image_view_ranges(self, *, preserve_view: bool = False) -> None:
        window = self.window
        if window._current_processed_image is None:
            window._pending_image_view_ranges = None
            window._pending_image_view_crop_offset = None
            window._pending_image_view_preserve = False
            return
        x_range, y_range = window.image_plot.vb.viewRange()
        window._pending_image_view_ranges = (
            (float(x_range[0]), float(x_range[1])),
            (float(y_range[0]), float(y_range[1])),
        )
        window._pending_image_view_preserve = bool(preserve_view)
        crop = window._state.preprocessing.crop
        if window._active_tool == "crop" and crop.enabled and bool(window._state.preprocessing.image_tools_enabled):
            window._pending_image_view_crop_offset = (float(crop.x), float(crop.y))
        else:
            window._pending_image_view_crop_offset = None

    def set_clamped_image_view_ranges(
        self,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> bool:
        window = self.window
        if window._current_processed_image is None:
            return False
        image_height, image_width = window._current_processed_image.shape[:2]
        view_width = max(float(x_range[1] - x_range[0]), 1.0)
        view_height = max(float(y_range[1] - y_range[0]), 1.0)
        if view_width >= float(image_width):
            target_x_range = (0.0, float(image_width))
        else:
            shift_x = 0.0
            if x_range[0] < 0.0:
                shift_x = -float(x_range[0])
            elif x_range[1] > float(image_width):
                shift_x = float(image_width) - float(x_range[1])
            target_x_range = (float(x_range[0] + shift_x), float(x_range[1] + shift_x))
        if view_height >= float(image_height):
            target_y_range = (0.0, float(image_height))
        else:
            shift_y = 0.0
            if y_range[0] < 0.0:
                shift_y = -float(y_range[0])
            elif y_range[1] > float(image_height):
                shift_y = float(image_height) - float(y_range[1])
            target_y_range = (float(y_range[0] + shift_y), float(y_range[1] + shift_y))
        window.image_plot.vb.setRange(xRange=target_x_range, yRange=target_y_range, padding=0.0)
        return True

    def set_unclamped_image_view_ranges(
        self,
        x_range: tuple[float, float],
        y_range: tuple[float, float],
    ) -> bool:
        window = self.window
        if window._current_processed_image is None:
            return False
        window.image_plot.vb.setRange(
            xRange=(float(x_range[0]), float(x_range[1])),
            yRange=(float(y_range[0]), float(y_range[1])),
            padding=0.0,
        )
        return True

    def restore_pending_image_view_after_load(self) -> bool:
        window = self.window
        previous_ranges = window._pending_image_view_ranges
        crop_offset = window._pending_image_view_crop_offset
        preserve_view = window._pending_image_view_preserve
        window._pending_image_view_ranges = None
        window._pending_image_view_crop_offset = None
        window._pending_image_view_preserve = False
        if previous_ranges is None:
            return False
        if preserve_view:
            return self.set_unclamped_image_view_ranges(previous_ranges[0], previous_ranges[1])
        if crop_offset is not None:
            return self.set_unclamped_image_view_ranges(
                (previous_ranges[0][0] - crop_offset[0], previous_ranges[0][1] - crop_offset[0]),
                (previous_ranges[1][0] - crop_offset[1], previous_ranges[1][1] - crop_offset[1]),
            )
        return self.set_clamped_image_view_ranges(previous_ranges[0], previous_ranges[1])

    def restore_saved_image_view_after_load(self) -> bool:
        window = self.window
        if window._current_processed_image is None or window._showing_background_profile_main:
            return False
        x_min_raw = window._settings.value("visual/image_view_x_min", None)
        x_max_raw = window._settings.value("visual/image_view_x_max", None)
        y_min_raw = window._settings.value("visual/image_view_y_min", None)
        y_max_raw = window._settings.value("visual/image_view_y_max", None)
        if None in (x_min_raw, x_max_raw, y_min_raw, y_max_raw):
            return False
        try:
            x_range = (float(x_min_raw), float(x_max_raw))
            y_range = (float(y_min_raw), float(y_max_raw))
        except (TypeError, ValueError):
            return False
        return self.set_clamped_image_view_ranges(x_range, y_range)
