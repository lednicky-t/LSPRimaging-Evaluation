from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QApplication

from lspr_imaging_app.domain.models import CropDefinition


class ImageToolsController:
    """Owns the rotate/crop/flip spatial-tool toggles and the crop rectangle
    overlay's sync with window._state.preprocessing.

    The crop rectangle is always stored/interpreted in "post rotate+flip"
    coordinates (see processing/preprocess.py: rotate -> flip -> crop). While
    a spatial tool is open, `unlink_for_preview` skips only the crop slice
    (keeping rotate/flip applied) so the box is always drawn on the same
    canvas its numbers are defined against - getting this wrong is what
    previously caused crops to compound into an over-cropped image on
    session restore (see the `image_tools_enabled` pitfall in CLAUDE.md).
    """

    def __init__(self, window) -> None:
        self.window = window

    def unlink_for_preview(self) -> None:
        self.window._image_tools_preview_only = True

    def relink_after_preview(self) -> None:
        window = self.window
        window._image_tools_preview_only = not bool(window.image_tools_section.is_applied())

    def ensure_reference_image_for_image_tools(self) -> bool:
        window = self.window
        if window._current_record_path is None or window._is_current_reference_image():
            return True
        window._set_status_text("Switch to the reference image to edit image tools.")
        return False

    def handle_image_tool_settings_changed(self, status: str, *, preserve_view: bool = False) -> None:
        window = self.window
        editing_spatial_tool = window._active_tool in ("rotate", "crop", "measure")
        linked = bool(window.image_tools_section.is_applied())
        window._image_tools_preview_only = editing_spatial_tool or not linked
        window._capture_pending_image_view_ranges(preserve_view=preserve_view)
        window._invalidate_image_analysis_caches()
        window._invalidate_background_profile_cache()
        if window._state.area_rois:
            window._update_roi_overlays()
            window._update_roi_summary()
            window._sync_roi_detection_controls()
        window._save_processing_state_for_dataset()
        window._current_image_key = None
        window._refresh_image()
        window.status_label.setText(status)

    def apply_image_transform_change(self, status: str) -> None:
        self.window._push_undo_point(status)
        self.handle_image_tool_settings_changed(status, preserve_view=True)

    def refresh_image_tool_action_icons(self) -> None:
        window = self.window
        window.rotate_action.setIcon(window._make_rotate_icon(window.rotate_action.isChecked()))
        window.rotation_fill_dark_button.setIcon(window._make_rotation_fill_icon(window.rotation_fill_dark_button.isChecked()))
        window.crop_action.setIcon(window._make_crop_icon(window.crop_action.isChecked()))
        window.flip_horizontal_action.setIcon(window._make_flip_horizontal_icon(window.flip_horizontal_action.isChecked()))
        window.flip_vertical_action.setIcon(window._make_flip_vertical_icon(window.flip_vertical_action.isChecked()))
        window.measure_action.setIcon(window._make_measure_icon(window.measure_action.isChecked()))

    def on_rotate_tool_toggled(self, checked: bool) -> None:
        window = self.window
        if checked:
            if not self.ensure_reference_image_for_image_tools():
                window.rotate_action.blockSignals(True)
                window.rotate_action.setChecked(False)
                window.rotate_action.blockSignals(False)
                window._sync_rotation_visibility()
                window._sync_crop_visibility()
                return
            self.unlink_for_preview()
            window.mask_pencil_check.blockSignals(True)
            window.mask_pencil_check.setChecked(False)
            window.mask_pencil_check.blockSignals(False)
            window.crop_action.blockSignals(True)
            window.crop_action.setChecked(False)
            window.crop_action.blockSignals(False)
            window.measure_action.blockSignals(True)
            window.measure_action.setChecked(False)
            window.measure_action.blockSignals(False)
            window.spot_edit_action.blockSignals(True)
            window.spot_edit_action.setChecked(False)
            window.spot_edit_action.blockSignals(False)
            window.roi_array_action.blockSignals(True)
            window.roi_array_action.setChecked(False)
            window.roi_array_action.blockSignals(False)
            window.chromatic_grid_button.blockSignals(True)
            window.chromatic_grid_button.setChecked(False)
            window.chromatic_grid_button.blockSignals(False)
            window._active_tool = "rotate"
        elif window._active_tool == "rotate":
            window._active_tool = None
            self.relink_after_preview()
        self.refresh_image_tool_action_icons()
        window._sync_rotation_visibility()
        window._sync_crop_visibility()
        window._sync_measurement_visibility()
        window._chromatic_controller.sync_grid_visibility()

    def on_crop_tool_toggled(self, checked: bool) -> None:
        window = self.window
        if checked:
            if not self.ensure_reference_image_for_image_tools():
                window.crop_action.blockSignals(True)
                window.crop_action.setChecked(False)
                window.crop_action.blockSignals(False)
                window._sync_rotation_visibility()
                window._sync_crop_visibility()
                return
            self.unlink_for_preview()
            window.mask_pencil_check.blockSignals(True)
            window.mask_pencil_check.setChecked(False)
            window.mask_pencil_check.blockSignals(False)
            window.rotate_action.blockSignals(True)
            window.rotate_action.setChecked(False)
            window.rotate_action.blockSignals(False)
            window.measure_action.blockSignals(True)
            window.measure_action.setChecked(False)
            window.measure_action.blockSignals(False)
            window.spot_edit_action.blockSignals(True)
            window.spot_edit_action.setChecked(False)
            window.spot_edit_action.blockSignals(False)
            window.roi_array_action.blockSignals(True)
            window.roi_array_action.setChecked(False)
            window.roi_array_action.blockSignals(False)
            window.chromatic_grid_button.blockSignals(True)
            window.chromatic_grid_button.setChecked(False)
            window.chromatic_grid_button.blockSignals(False)
            window._active_tool = "crop"
            window._state.preprocessing.crop.enabled = True
            window._save_processing_state_for_dataset()
        elif window._active_tool == "crop":
            window._active_tool = None
            self.relink_after_preview()
        self.refresh_image_tool_action_icons()
        window._sync_rotation_visibility()
        window._sync_crop_visibility()
        window._sync_measurement_visibility()
        window._chromatic_controller.sync_grid_visibility()
        window._current_image_key = None
        window._refresh_image()

    def on_measure_tool_toggled(self, checked: bool) -> None:
        window = self.window
        if checked:
            if not self.ensure_reference_image_for_image_tools():
                window.measure_action.blockSignals(True)
                window.measure_action.setChecked(False)
                window.measure_action.blockSignals(False)
                window._sync_measurement_visibility()
                return
            self.unlink_for_preview()
            window.mask_pencil_check.blockSignals(True)
            window.mask_pencil_check.setChecked(False)
            window.mask_pencil_check.blockSignals(False)
            window.rotate_action.blockSignals(True)
            window.rotate_action.setChecked(False)
            window.rotate_action.blockSignals(False)
            window.crop_action.blockSignals(True)
            window.crop_action.setChecked(False)
            window.crop_action.blockSignals(False)
            window.spot_edit_action.blockSignals(True)
            window.spot_edit_action.setChecked(False)
            window.spot_edit_action.blockSignals(False)
            window.roi_array_action.blockSignals(True)
            window.roi_array_action.setChecked(False)
            window.roi_array_action.blockSignals(False)
            window.chromatic_grid_button.blockSignals(True)
            window.chromatic_grid_button.setChecked(False)
            window.chromatic_grid_button.blockSignals(False)
            window._active_tool = "measure"
            window._set_status_text("Measurement tool active. Drag the two crosses, enter the real Δx/Δy in µm, then apply calibration.")
        elif window._active_tool == "measure":
            window._active_tool = None
            self.relink_after_preview()
        self.refresh_image_tool_action_icons()
        window._sync_rotation_visibility()
        window._sync_crop_visibility()
        window._sync_measurement_visibility()
        window._chromatic_controller.sync_grid_visibility()

    def on_flip_horizontal_toggled(self, checked: bool) -> None:
        window = self.window
        if not self.ensure_reference_image_for_image_tools():
            window.flip_horizontal_action.blockSignals(True)
            window.flip_horizontal_action.setChecked(window._state.preprocessing.flip_horizontal)
            window.flip_horizontal_action.blockSignals(False)
            return
        if checked:
            self.unlink_for_preview()
        window._state.preprocessing.flip_horizontal = checked
        self.refresh_image_tool_action_icons()
        self.apply_image_transform_change("Applied horizontal flip." if checked else "Removed horizontal flip.")

    def on_flip_vertical_toggled(self, checked: bool) -> None:
        window = self.window
        if not self.ensure_reference_image_for_image_tools():
            window.flip_vertical_action.blockSignals(True)
            window.flip_vertical_action.setChecked(window._state.preprocessing.flip_vertical)
            window.flip_vertical_action.blockSignals(False)
            return
        if checked:
            self.unlink_for_preview()
        window._state.preprocessing.flip_vertical = checked
        self.refresh_image_tool_action_icons()
        self.apply_image_transform_change("Applied vertical flip." if checked else "Removed vertical flip.")

    def update_rotation_fill_button_tooltip(self) -> None:
        window = self.window
        if bool(window._state.preprocessing.rotation_fill_dark):
            tooltip = "Rotation fill: dark (0). New corner pixels created by rotation are set to 0 intensity instead of copying the nearest edge pixel. Click to switch to edge-stretch fill."
        else:
            tooltip = "Rotation fill: edge-stretch. New corner pixels created by rotation copy the nearest edge pixel. Click to switch to dark (0 intensity) fill."
        window.rotation_fill_dark_button.setToolTip(tooltip)

    def on_rotation_fill_dark_toggled(self, checked: bool) -> None:
        window = self.window
        if not self.ensure_reference_image_for_image_tools():
            window.rotation_fill_dark_button.blockSignals(True)
            window.rotation_fill_dark_button.setChecked(window._state.preprocessing.rotation_fill_dark)
            window.rotation_fill_dark_button.blockSignals(False)
            return
        window._state.preprocessing.rotation_fill_dark = checked
        self.refresh_image_tool_action_icons()
        self.update_rotation_fill_button_tooltip()
        self.apply_image_transform_change(
            "Rotation fill set to dark (0)." if checked else "Rotation fill set to edge-stretch."
        )

    def reset_rotation(self) -> None:
        window = self.window
        if not self.ensure_reference_image_for_image_tools():
            return
        window._push_undo_point("Reset rotation")
        window._state.preprocessing.rotation_angle_deg = 0.0
        self.handle_image_tool_settings_changed("Rotation reset to 0 deg.", preserve_view=True)

    def reset_crop(self) -> None:
        window = self.window
        if not self.ensure_reference_image_for_image_tools():
            return
        window._push_undo_point("Reset crop")
        window._state.preprocessing.crop = CropDefinition()
        self.handle_image_tool_settings_changed("Crop reset.", preserve_view=True)

    def adjust_rotation(self, delta_deg: float) -> None:
        window = self.window
        if not self.ensure_reference_image_for_image_tools():
            return
        window._push_undo_point("Adjust rotation")
        window._state.preprocessing.rotation_angle_deg += float(delta_deg)
        self.handle_image_tool_settings_changed(
            f"Rotation adjusted to {window._state.preprocessing.rotation_angle_deg:.2f} deg",
            preserve_view=True,
        )

    def sync_rotation_tool(self) -> None:
        window = self.window
        window._ensure_image_tool_guide()
        window._sync_rotation_visibility()

    def sync_rotation_visibility(self) -> None:
        self.window._update_guide_overlays()

    def sync_crop_tool(self, image_shape: tuple[int, int]) -> None:
        window = self.window
        crop = window._state.preprocessing.crop
        image_height, image_width = image_shape[:2]
        window._ensure_image_tool_guide()
        # Guard the whole construction-and-position sync, not just the final
        # setPos/setSize: building a fresh RectROI's scale handles below fires
        # pyqtgraph's sigRegionChangeFinished once per addScaleHandle() call
        # (9 times), and crop_roi_changed() is already connected by then. With
        # the guard only wrapping the later setPos/setSize, those construction
        # -time signals ran unguarded and wrote the ROI's fallback size (half
        # the image, used when crop.width/height are 0) straight back into
        # window._state.preprocessing.crop with enabled=True -- silently
        # resurrecting a "cleared" crop on every fresh session restart, since
        # window._crop_roi only gets (re)built once per session.
        window._suspend_crop_sync = True
        try:
            if window._crop_roi is None:
                width = crop.width if crop.width > 0 else max(image_width // 2, 1)
                height = crop.height if crop.height > 0 else max(image_height // 2, 1)
                window._crop_roi = pg.RectROI(
                    [crop.x, crop.y],
                    [width, height],
                    pen=pg.mkPen("#38bdf8", width=2),
                    movable=False,
                    resizable=True,
                    rotatable=False,
                    sideScalers=False,
                )
                window._crop_roi.handleSize = 10
                window._crop_roi.sigRegionChangeFinished.connect(self.crop_roi_changed)
                window._crop_roi.sigRegionChanged.connect(lambda *_args: window._update_crop_overlay())
                window._crop_roi.addScaleHandle([0.0, 0.0], [1.0, 1.0], name="top_left")
                window._crop_roi.addScaleHandle([1.0, 0.0], [0.0, 1.0], name="top_right")
                window._crop_roi.addScaleHandle([0.0, 1.0], [1.0, 0.0], name="bottom_left")
                window._crop_roi.addScaleHandle([1.0, 1.0], [0.0, 0.0], name="bottom_right")
                for fraction in (0.25, 0.5, 0.75):
                    window._crop_roi.addScaleHandle([0.0, fraction], [1.0, fraction], name=f"left_{fraction:g}")
                    window._crop_roi.addScaleHandle([1.0, fraction], [0.0, fraction], name=f"right_{fraction:g}")
                    window._crop_roi.addScaleHandle([fraction, 0.0], [fraction, 1.0], name=f"top_{fraction:g}")
                    window._crop_roi.addScaleHandle([fraction, 1.0], [fraction, 0.0], name=f"bottom_{fraction:g}")
                window.image_plot.addItem(window._crop_roi)
                window._crop_roi.setZValue(1.0)

            width = crop.width if crop.width > 0 else max(image_width // 2, 1)
            height = crop.height if crop.height > 0 else max(image_height // 2, 1)
            x = min(max(crop.x, 0), max(image_width - width, 0))
            y = min(max(crop.y, 0), max(image_height - height, 0))

            window._crop_roi.setPos((x, y))
            window._crop_roi.setSize((min(width, image_width), min(height, image_height)))
        finally:
            window._suspend_crop_sync = False
        window._update_crop_overlay()
        self.sync_crop_visibility()

    def sync_crop_visibility(self) -> None:
        window = self.window
        if window._crop_roi is not None:
            window._crop_roi.setVisible(window._active_tool == "crop" and not window._showing_background_profile_main)
        window._update_crop_overlay()
        window._update_guide_overlays()

    def crop_roi_changed(self) -> None:
        window = self.window
        if window._suspend_crop_sync or window._crop_roi is None:
            return
        window._push_undo_point("Crop")
        pos = window._crop_roi.pos()
        size = window._crop_roi.size()
        window._state.preprocessing.crop.x = max(int(round(pos.x())), 0)
        window._state.preprocessing.crop.y = max(int(round(pos.y())), 0)
        window._state.preprocessing.crop.width = max(int(round(size.x())), 1)
        window._state.preprocessing.crop.height = max(int(round(size.y())), 1)
        window._state.preprocessing.crop.enabled = True
        window._update_crop_overlay()
        self.handle_image_tool_settings_changed("Crop updated.", preserve_view=True)

    def move_crop_roi_to(self, x: float, y: float) -> None:
        window = self.window
        if window._crop_roi is None or window._current_processed_image is None:
            return
        image_height, image_width = window._current_processed_image.shape[:2]
        size = window._crop_roi.size()
        crop_width = max(float(size.x()), 1.0)
        crop_height = max(float(size.y()), 1.0)
        x = float(np.clip(float(x), 0.0, max(float(image_width) - crop_width, 0.0)))
        y = float(np.clip(float(y), 0.0, max(float(image_height) - crop_height, 0.0)))
        window._suspend_crop_sync = True
        window._crop_roi.setPos((x, y))
        window._suspend_crop_sync = False
        window._state.preprocessing.crop.x = max(int(round(x)), 0)
        window._state.preprocessing.crop.y = max(int(round(y)), 0)
        window._state.preprocessing.crop.width = max(int(round(crop_width)), 1)
        window._state.preprocessing.crop.height = max(int(round(crop_height)), 1)
        window._state.preprocessing.crop.enabled = True
        window._update_crop_overlay()

    def on_image_tools_section_applied_changed(self, applied: bool) -> None:
        window = self.window
        applied = bool(applied)
        window._append_workflow_log(f"Image tools link | {applied}", level="debug")
        if bool(getattr(window._state.preprocessing, "image_tools_enabled", True)) == applied:
            return
        window._push_undo_point("Image tools")
        window._state.preprocessing.image_tools_enabled = applied
        window._image_tools_preview_only = not applied
        status = (
            "Image tools linked. Recalculating downstream views."
            if applied
            else "Image tools link disabled. Preview only mode is active."
        )
        window._begin_busy("Applying image tools...")
        QApplication.processEvents()
        try:
            self.handle_image_tool_settings_changed(status, preserve_view=True)
        finally:
            window._end_busy(status)
