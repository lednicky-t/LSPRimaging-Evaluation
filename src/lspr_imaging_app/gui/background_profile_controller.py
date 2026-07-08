from __future__ import annotations

from copy import deepcopy

import numpy as np

from lspr_imaging_app.gui.analysis_tasks import _background_profile_task
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_ui import APP_THEME


class BackgroundProfileController:
    def __init__(self, window) -> None:
        self.window = window

    def _background_profile_signature(self) -> tuple[object, ...] | None:
        if self.window._current_record_path is None or self.window._current_image_key is None:
            return None
        crop = self.window._state.preprocessing.crop
        return (
            str(self.window._current_record_path),
            self.window._current_image_key,
            bool(getattr(self.window._state.preprocessing, "image_tools_enabled", True)),
            round(float(self.window._state.preprocessing.rotation_angle_deg), 6),
            bool(getattr(self.window._state.preprocessing, "rotation_fill_dark", False)),
            bool(self.window._state.preprocessing.flip_horizontal),
            bool(self.window._state.preprocessing.flip_vertical),
            self.window._chromatic_signature_for_image_key(self.window._current_image_key),
            bool(crop.enabled),
            int(crop.x),
            int(crop.y),
            int(crop.width),
            int(crop.height),
            round(float(self.window._state.preprocessing.flatten_background_sigma_px), 3),
            int(max(getattr(self.window._state.preprocessing, "flatten_background_binning", 2), 1)),
            bool(self.window._state.preprocessing.flatten_background_exclude_area_rois),
            bool(self.window._state.preprocessing.flatten_background_exclude_mask),
            self.window._roi_signature(self.window._rois_for_preprocessing(self.window._current_image_key))
            if self.window._state.preprocessing.flatten_background_exclude_area_rois
            else None,
            self.window._mask_preview_signature() if self.window._state.preprocessing.flatten_background_exclude_mask else None,
        )


    def _calculate_background_profile_image(self) -> np.ndarray | None:
        if self.window._current_record_path is None or self.window._current_image_key is None:
            return None
        rois = (
            deepcopy(self.window._rois_for_preprocessing(self.window._current_image_key))
            if self.window._state.preprocessing.flatten_background_exclude_area_rois
            else None
        )
        preprocessing = deepcopy(self.window._state.preprocessing)
        mask_settings = deepcopy(self.window._state.area_roi_settings) if self.window._state.preprocessing.flatten_background_exclude_mask else None
        external_mask, external_mask_processed = self.window._effective_external_mask_for_record(
            self.window._current_record_path,
            processed_space=True,
        )
        return _background_profile_task(
            str(self.window._current_record_path),
            (preprocessing, mask_settings, external_mask_processed),
            float(self.window._state.preprocessing.flatten_background_sigma_px),
            rois,
            external_mask,
        )


    def _update_background_profile_preview(self) -> None:
        signature = self._background_profile_signature()
        if signature is None:
            return
        if (
            self.window._background_profile_cache_signature == signature
            and self.window._background_profile_cache_image is not None
        ):
            if self.window._showing_background_profile_main:
                self._apply_main_image_content()
            return
        rois = (
            deepcopy(self.window._rois_for_preprocessing(self.window._current_image_key))
            if self.window._state.preprocessing.flatten_background_exclude_area_rois
            else None
        )
        preprocessing = deepcopy(self.window._state.preprocessing)
        mask_settings = deepcopy(self.window._state.area_roi_settings) if self.window._state.preprocessing.flatten_background_exclude_mask else None
        external_mask, external_mask_processed = self.window._effective_external_mask_for_record(
            self.window._current_record_path,
            processed_space=True,
        )
        request_id = self.window._background_profile_request_id + 1
        self.window._background_profile_request_id = request_id
        worker = FunctionWorker(
            _background_profile_task,
            str(self.window._current_record_path),
            (preprocessing, mask_settings, external_mask_processed),
            float(self.window._state.preprocessing.flatten_background_sigma_px),
            rois,
            external_mask,
            supports_progress=True,
        )
        self.window._begin_busy("Updating background profile preview...")
        self.window._append_workflow_log("Background profile preview start", level="info")
        worker.signals.progress.connect(self.window._update_busy_progress)
        worker.signals.result.connect(
            lambda profile,
            request_id=request_id,
            signature=signature: self._on_background_profile_ready(request_id, signature, profile)
        )
        worker.signals.error.connect(lambda message: self._on_background_profile_failed(message))
        self.window._thread_pool.start(worker)


    def _on_background_profile_ready(
        self,
        request_id: int,
        signature: tuple[object, ...],
        profile: np.ndarray,
    ) -> None:
        self.window._end_busy()
        if request_id != self.window._background_profile_request_id:
            return
        if signature != self._background_profile_signature():
            return
        self.window._background_profile_cache_signature = signature
        self.window._background_profile_cache_image = profile
        self.window._append_workflow_log("Background profile preview done", level="success")
        if self.window._showing_background_profile_main:
            self._apply_main_image_content()


    def _on_background_profile_failed(self, message: str) -> None:
        self.window._end_busy()
        self.window._append_workflow_log(f"Background profile preview failed | {message}", level="error")
        self.window._background_error("Background profile preview", message)


    def _invalidate_background_profile_cache(self) -> None:
        self.window._background_profile_cache_signature = None
        self.window._background_profile_cache_image = None


    def _apply_main_image_content(self) -> None:
        if self.window._showing_background_profile_main and self.window._background_profile_cache_image is not None:
            self.window.image_item.setImage(self.window._background_profile_cache_image.T, autoLevels=True)
        elif self.window._current_processed_image is not None:
            self.window.image_item.setImage(self.window._current_processed_image.T, autoLevels=True)
        self._sync_main_view_mode()
        self.window._update_reference_star_overlay()


    def _sync_main_view_mode(self) -> None:
        showing_profile = self.window._showing_background_profile_main and self.window._background_profile_cache_image is not None
        if showing_profile:
            self.window.intensity_highlight_item.hide()
            self.window.ignore_mask_item.hide()
            if self.window._crop_roi is not None:
                self.window._crop_roi.setVisible(False)
            self.window._update_crop_overlay()
            for bundle in self.window._roi_overlay_items.values():
                bundle.curve.setVisible(False)
                if bundle.ring_fill is not None:
                    bundle.ring_fill.setVisible(False)
                if bundle.inner_curve is not None:
                    bundle.inner_curve.setVisible(False)
                if bundle.outer_curve is not None:
                    bundle.outer_curve.setVisible(False)
                if bundle.label is not None:
                    bundle.label.setVisible(False)
            for bundle in self.window._guide_overlay_items.values():
                bundle.vertical.setVisible(False)
                bundle.horizontal.setVisible(False)
                bundle.marker.setVisible(False)
            for bundle in self.window._landmark_overlay_items.values():
                bundle.curve.setVisible(False)
                bundle.label.setVisible(False)
            self.window._hide_measurement_overlay()
            self.window._refresh_scale_bar_overlay()
            return
        self.window._update_selected_intensity_overlay()
        self.window._update_ignore_mask_overlay()
        self.window._sync_rotation_visibility()
        self.window._sync_crop_visibility()
        self.window._update_roi_overlays()
        self.window._update_landmark_overlays()
        self.window._update_guide_overlays()
        self.window._sync_measurement_visibility()
        self.window._update_reference_star_overlay()


    def _sync_background_profile_buttons(self, checked: bool) -> None:
        window = self.window
        for button in (getattr(window, "background_profile_hold_button", None), getattr(window, "background_profile_button", None)):
            if button is None:
                continue
            button.blockSignals(True)
            button.setChecked(bool(checked))
            button.setIcon(window._make_background_profile_icon(bool(checked), size=APP_THEME.compact_icon_inner))
            button.blockSignals(False)

    def _sync_background_exclusion_buttons(self) -> None:
        window = self.window
        if hasattr(window, "background_ignore_spot_button"):
            window.background_ignore_spot_button.setIcon(
                window._background_exclusion_icon(
                    "current-location-off",
                    bool(window.background_ignore_spot_button.isChecked()),
                    size=APP_THEME.compact_icon_inner,
                )
            )
        if hasattr(window, "background_ignore_mask_button"):
            window.background_ignore_mask_button.setIcon(
                window._background_exclusion_icon(
                    "mask-off",
                    bool(window.background_ignore_mask_button.isChecked()),
                    size=APP_THEME.compact_icon_inner,
                )
            )


    def _on_background_profile_toggled(self, checked: bool) -> None:
        self.window._showing_background_profile_main = bool(checked)
        self._sync_background_profile_buttons(bool(checked))
        if checked:
            self._update_background_profile_preview()
            if self.window._background_profile_cache_image is not None:
                self._apply_main_image_content()
            else:
                self._sync_main_view_mode()
        else:
            self._apply_main_image_content()
        self.window._save_visual_preferences()
