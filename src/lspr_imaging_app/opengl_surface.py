"""OpenGL surface format for the app: no wait for the screen refresh (2026-11-11).

With "Draw the image with the graphics card" on (`panels/image/gpu_view.py`), Qt hands the whole window to the screen through
OpenGL, and by default every hand-over (a buffer swap) waits for the monitor's next refresh: up to 16.7 ms at 60 Hz, and Play
made two per frame. Measured on the maintainer's dataset and layout (Play, 160 ROIs, Sensorgram shown): 15.3 -> 24.1 fps, frame
cycle 65 -> 41.5 ms (`docs/play_pixmap_cache_2026-11-11.md`).

No tearing: on Windows a windowed app does not draw to the screen directly; the desktop compositor (DWM) assembles the windows
and keeps its own refresh timing.

Must run before the QApplication is created (Qt reads the default format when it sets up OpenGL). Cheap: imports only QtGui.
"""

from __future__ import annotations

from PyQt6.QtGui import QSurfaceFormat


def configure_opengl_surface() -> None:
    """Make swap interval 0 (do not wait for the screen refresh) the default for every OpenGL surface of the app."""
    surface_format = QSurfaceFormat.defaultFormat()
    surface_format.setSwapInterval(0)
    QSurfaceFormat.setDefaultFormat(surface_format)
