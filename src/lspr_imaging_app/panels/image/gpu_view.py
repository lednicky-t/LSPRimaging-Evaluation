"""Edit -> Preferences "Draw the image with the graphics card" (2026-11-02): the Image panel's canvas painted through an OpenGL viewport.

Measured on the real display of the dev laptop (``docs/spectra_sensorgram_draw_cost_2026-11-02.md``, a standalone benchmark with an
800 x 1275 picture swapped every frame): the frame cycle 17.4 -> 14.8 ms with 160 ROI circles, 11.3 -> 8.7 ms for the picture alone. Qt's
own drawing (QPainter) runs on the graphics card instead of the processor; it is the same drawing, so nothing on screen changes in content.
On by default (maintainer, 2026-11-02); an old or virtual graphics driver can draw wrongly or slowly through OpenGL, so it can be
turned off in Preferences.

If no OpenGL context can be made (remote desktop, a driver without OpenGL 2), the switch stays off and a WARNING says why: never a black view.
Without a screen (Qt's "offscreen" platform: the tests) there is no OpenGL either; it stays off there without a warning.
"""

from __future__ import annotations

import logging

from PyQt6.QtGui import QGuiApplication, QOpenGLContext
from PyQt6.QtWidgets import QGraphicsView, QWidget

from ...applog import log_event

logger = logging.getLogger(__name__)


def opengl_available() -> bool:
    """Whether an OpenGL context can be created on this computer (checked once per call; cheap)."""
    try:
        context = QOpenGLContext()
        return bool(context.create())
    except Exception:  # noqa: BLE001 - any failure here means: not available
        return False


def apply_gpu_drawing(view: QGraphicsView, on: bool) -> bool:
    """Give `view` an OpenGL viewport (`on`) or a plain one. Returns whether it now draws with the graphics card (False when asked but not
    possible). The view keeps its scene, items and update mode."""
    mode = view.viewportUpdateMode()
    if on and _offscreen():
        on = False  # no screen, no OpenGL: the tests (nothing to warn about)
    elif on and not opengl_available():
        log_event(logger, "image.gpu_drawing_unavailable", logging.WARNING, reason="no OpenGL context could be created")
        on = False
    current = _is_gpu(view)
    if on != current:
        if on:
            from PyQt6.QtOpenGLWidgets import QOpenGLWidget  # imported only when used

            view.setViewport(QOpenGLWidget())
        else:
            view.setViewport(QWidget())
        view.setViewportUpdateMode(mode)  # a new viewport keeps the view's mode, set again to be sure
        log_event(logger, "image.gpu_drawing", logging.INFO, on=on)
    return on


def _offscreen() -> bool:
    return QGuiApplication.platformName() == "offscreen"


def _is_gpu(view: QGraphicsView) -> bool:
    return type(view.viewport()).__name__ == "QOpenGLWidget"
