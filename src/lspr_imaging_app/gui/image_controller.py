from __future__ import annotations

from lspr_imaging_app.gui.image_render_manager import ImageRenderManager


class ImageController:
    def __init__(self, window) -> None:
        self.window = window
        self._render_manager = ImageRenderManager(window)

    def refresh_image(self) -> None:
        self._render_manager.refresh_image()

    def start_pending_image_refresh(self) -> None:
        self._render_manager.start_pending_image_refresh()

    def on_image_refresh_ready(self, *args, **kwargs) -> None:
        self._render_manager.on_image_refresh_ready(*args, **kwargs)

    def on_image_refresh_failed(self, message: str) -> None:
        self._render_manager.on_image_refresh_failed(message)

    def apply_loaded_image(self, *args, **kwargs) -> None:
        self._render_manager.apply_loaded_image(*args, **kwargs)

    def on_image_view_range_changed(self, *args) -> None:
        self._render_manager.on_image_view_range_changed(*args)

    def display_rois(self, *args, **kwargs):
        return self._render_manager.display_rois(*args, **kwargs)

    def rois_for_preprocessing(self, *args, **kwargs):
        return self._render_manager.rois_for_preprocessing(*args, **kwargs)

    def roi_curve_points(self, *args, **kwargs):
        return self._render_manager.roi_curve_points(*args, **kwargs)

    def set_view_overlay_visibility(self, *args, **kwargs) -> None:
        self._render_manager.set_view_overlay_visibility(*args, **kwargs)

    def capture_pending_image_view_ranges(self, *args, **kwargs) -> None:
        self._render_manager.capture_pending_image_view_ranges(*args, **kwargs)

    def set_clamped_image_view_ranges(self, *args, **kwargs) -> bool:
        return self._render_manager.set_clamped_image_view_ranges(*args, **kwargs)

    def restore_pending_image_view_after_load(self) -> bool:
        return self._render_manager.restore_pending_image_view_after_load()

    def restore_saved_image_view_after_load(self) -> bool:
        return self._render_manager.restore_saved_image_view_after_load()
