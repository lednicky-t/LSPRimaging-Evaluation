from __future__ import annotations

from copy import deepcopy

import numpy as np

from lspr_imaging_app.gui.analysis_tasks import _background_profile_task
from lspr_imaging_app.gui.worker import FunctionWorker
from lspr_imaging_app.processing.chromatic import warp_boolean_mask_affine
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
            int(getattr(self.window._state.preprocessing, "flatten_background_exclusion_dilation_px", 0)),
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
        if external_mask is not None:
            affine_matrix = self.window._chromatic_affine_for_image_key(self.window._current_image_key)
            if affine_matrix is not None:
                external_mask = warp_boolean_mask_affine(np.asarray(external_mask, dtype=bool), affine_matrix)
            external_mask = self.window._apply_mask_wavelength_diff(external_mask, self.window._current_image_key)
        return _background_profile_task(
            str(self.window._current_record_path),
            (preprocessing, mask_settings, external_mask_processed),
            float(self.window._state.preprocessing.flatten_background_sigma_px),
            rois,
            external_mask,
        )


    def request_background_profile_image(self, on_ready) -> None:
        """Resolve the background-profile image for the current view, off the GUI thread
        if it isn't already cached and current. `on_ready(image)` is called with the
        result: synchronously, in this call, on a cache hit; otherwise once the background
        computation finishes (`None` if it can't be computed or the job fails). Concurrent
        requests for the same signature (e.g. the live preview and a "Create new
        background" click both wanting the current view) share a single computation.

        The single-slot cache (`_background_profile_cache_signature`/`_image`) is only
        updated if the finished result still matches the *current* signature, so an
        out-of-order/superseded result can't clobber it with stale data - but `on_ready`
        still always fires with the (correctly computed, just possibly no-longer-current)
        result, since callers like "Save background" want that answer regardless of
        whether the view has since moved on.
        """
        window = self.window
        signature = self._background_profile_signature()
        if signature is None:
            on_ready(None)
            return
        if (
            window._background_profile_cache_signature == signature
            and window._background_profile_cache_image is not None
        ):
            on_ready(window._background_profile_cache_image)
            return
        window._background_profile_pending.setdefault(signature, []).append(on_ready)
        if signature in window._background_profile_in_flight:
            return
        window._background_profile_in_flight.add(signature)
        rois = (
            deepcopy(window._rois_for_preprocessing(window._current_image_key))
            if window._state.preprocessing.flatten_background_exclude_area_rois
            else None
        )
        preprocessing = deepcopy(window._state.preprocessing)
        mask_settings = deepcopy(window._state.area_roi_settings) if window._state.preprocessing.flatten_background_exclude_mask else None
        external_mask, external_mask_processed = window._effective_external_mask_for_record(
            window._current_record_path,
            processed_space=True,
        )
        if external_mask is not None:
            affine_matrix = window._chromatic_affine_for_image_key(window._current_image_key)
            if affine_matrix is not None:
                external_mask = warp_boolean_mask_affine(np.asarray(external_mask, dtype=bool), affine_matrix)
            external_mask = window._apply_mask_wavelength_diff(external_mask, window._current_image_key)
        worker = FunctionWorker(
            _background_profile_task,
            str(window._current_record_path),
            (preprocessing, mask_settings, external_mask_processed),
            float(window._state.preprocessing.flatten_background_sigma_px),
            rois,
            external_mask,
            supports_progress=True,
        )
        window._begin_busy("Computing background profile...")
        window._append_workflow_log("Background profile computation start", level="info")
        worker.signals.progress.connect(window._update_busy_progress)
        worker.signals.result.connect(
            lambda profile, signature=signature: self._on_background_profile_ready(signature, profile)
        )
        worker.signals.error.connect(
            lambda message, signature=signature: self._on_background_profile_failed(signature, message)
        )
        window._thread_pool.start(worker)


    def _update_background_profile_preview(self) -> None:
        self.request_background_profile_image(self._on_background_profile_preview_ready)


    def _on_background_profile_preview_ready(self, profile: np.ndarray | None) -> None:
        window = self.window
        if profile is None or not window._showing_background_profile_main:
            return
        if window._background_profile_cache_signature != self._background_profile_signature():
            return  # A newer request has since superseded this one.
        self._apply_main_image_content()


    def _on_background_profile_ready(self, signature: tuple[object, ...], profile: np.ndarray) -> None:
        window = self.window
        window._background_profile_in_flight.discard(signature)
        window._end_busy()
        if signature == self._background_profile_signature():
            window._background_profile_cache_signature = signature
            window._background_profile_cache_image = profile
        finite_profile = profile[np.isfinite(profile)]
        if finite_profile.size:
            pmin, pmax = float(finite_profile.min()), float(finite_profile.max())
            spread_note = f" | range {pmin:.4g}-{pmax:.4g} (Δ{pmax - pmin:.4g})"
        else:
            spread_note = ""
        window._append_workflow_log(f"Background profile computation done{spread_note}", level="success")
        for callback in window._background_profile_pending.pop(signature, []):
            callback(profile)


    def _on_background_profile_failed(self, signature: tuple[object, ...], message: str) -> None:
        window = self.window
        window._background_profile_in_flight.discard(signature)
        window._end_busy()
        window._append_workflow_log(f"Background profile computation failed | {message}", level="error")
        window._background_error("Background profile", message)
        for callback in window._background_profile_pending.pop(signature, []):
            callback(None)


    def _invalidate_background_profile_cache(self) -> None:
        self.window._background_profile_cache_signature = None
        self.window._background_profile_cache_image = None


    def _apply_main_image_content(self, *, skip_roi_overlay_refresh: bool = False) -> None:
        if self.window._showing_background_profile_main and self.window._background_profile_cache_image is not None:
            levels = self._background_profile_display_levels()
            if levels is not None:
                self.window.image_item.setImage(self.window._background_profile_cache_image.T, levels=levels)
            else:
                self.window.image_item.setImage(self.window._background_profile_cache_image.T, autoLevels=True)
        elif self.window._current_processed_image is not None:
            self.window.image_item.setImage(self.window._current_processed_image.T, autoLevels=True)
        self._sync_main_view_mode(skip_roi_overlay_refresh=skip_roi_overlay_refresh)
        self.window._update_reference_star_overlay()


    def _background_profile_display_levels(self) -> tuple[float, float] | None:
        # Match the data image's own intensity range so small profile variation isn't stretched to full black-white contrast.
        image = self.window._current_processed_image
        if image is None:
            return None
        finite = image[np.isfinite(image)]
        if finite.size == 0:
            return None
        vmin = float(finite.min())
        vmax = float(finite.max())
        if vmax <= vmin:
            return None
        return (vmin, vmax)


    def _sync_main_view_mode(self, *, skip_roi_overlay_refresh: bool = False) -> None:
        showing_profile = self.window._showing_background_profile_main and self.window._background_profile_cache_image is not None
        if showing_profile:
            self.window.intensity_highlight_item.hide()
            self.window.ignore_mask_item.hide()
            if self.window._crop_roi is not None:
                self.window._crop_roi.setVisible(False)
            self.window._update_crop_overlay()
            for bundle in self.window._roi_overlay_items.values():
                bundle.curve.setVisible(False)
                if bundle.reference_fill is not None:
                    bundle.reference_fill.setVisible(False)
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
        if not skip_roi_overlay_refresh:
            # image_render_manager.apply_loaded_image() already did a full
            # _update_roi_overlays() rebuild moments earlier as part of
            # switching to the new wavelength - reaching here right after
            # that (rather than via a standalone background-profile
            # view toggle, which needs this to restore overlays hidden by
            # the `showing_profile` branch above) would otherwise redo the
            # exact same 160-ROI rebuild a second time for nothing. See
            # apply_loaded_image's call to _apply_main_image_content.
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
        if hasattr(window, "background_ignore_roi_button"):
            window.background_ignore_roi_button.setIcon(
                window._background_exclusion_icon(
                    "current-location-off",
                    bool(window.background_ignore_roi_button.isChecked()),
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
