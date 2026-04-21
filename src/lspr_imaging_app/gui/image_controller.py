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
