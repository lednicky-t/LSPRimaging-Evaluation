"""Edit -> Preferences "Draw the image with the graphics card" (2026-11-02): the Image panel's canvas painted through an OpenGL viewport.

Measured on the real display of the dev laptop (``docs/spectra_sensorgram_draw_cost_2026-11-02.md``, a standalone benchmark with an
800 x 1275 picture swapped every frame): the frame cycle 17.4 -> 14.8 ms with 160 ROI circles, 11.3 -> 8.7 ms for the picture alone. Qt's
own drawing (QPainter) runs on the graphics card instead of the processor; it is the same drawing, so nothing on screen changes in content.
On by default from 2026-11-02 to 2026-11-11; **off by default since**: once Play repainted the window only once per frame, the window's
hand-over to the screen was the cost left, and an OpenGL widget makes Qt hand over the whole window through OpenGL for every change.
At 4K Play ran at 30 fps with the processor drawing, 24.6 with the graphics card (`docs/play_diagnosis_2026-11-11.md` 6c-6d). Switching
it off while the app runs leaves the window an OpenGL window (Qt keeps it), so the full gain needs a restart.

If no OpenGL context can be made (remote desktop, a driver without OpenGL 2), the switch stays off and a WARNING says why: never a black view.
Without a screen (Qt's "offscreen" platform: the tests) there is no OpenGL either; it stays off there without a warning.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
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
    possible). The view keeps its scene, items and update mode.

    (A Preferences choice giving this viewport a window handle of its own, `WA_NativeWindow`, existed 2026-11-11 and was removed the same
    day: no reliable gain in the app; `docs/play_diagnosis_2026-11-11.md` section 6b.)"""
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

            gl_viewport = QOpenGLWidget()
            mark_opaque(gl_viewport)
            replace_viewport(view, gl_viewport)
        else:
            plain = QWidget()
            mark_opaque(plain)
            replace_viewport(view, plain)
        view.setViewportUpdateMode(mode)  # a new viewport keeps the view's mode, set again to be sure
        log_event(logger, "image.gpu_drawing", logging.INFO, on=on)
    return on


def mark_opaque(viewport: QWidget) -> None:
    """The view paints every pixel of its viewport (its solid background brush first), so tell Qt. Without this Qt treats it as
    see-through and paints every widget underneath at each repaint, all hidden afterwards: the dock's card, the panel and the main
    window, ~1.25 M logical px each per Play frame (`playback.paints`). Found for the OpenGL viewport 2026-11-11; the plain one
    (processor drawing, the default since that evening) had the same problem, seen in the same census later that day."""
    viewport.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)


def replace_viewport(view: QGraphicsView, new_viewport: QWidget) -> None:
    """`view.setViewport(new_viewport)`, keeping the widgets that float on the old viewport (crop size fields, measure calibration,
    cursor readout). Qt deletes the old viewport together with its children, so they are moved to the new one first, with their
    place and visibility (found 2026-11-11: with GPU drawing on, those three widgets were gone and every pan/zoom logged a
    "wrapped C/C++ object ... has been deleted" error)."""
    old = view.viewport()
    kept = []
    if old is not None:
        for child in old.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            kept.append((child, child.isVisibleTo(old), child.geometry()))
            child.setParent(new_viewport)
    view.setViewport(new_viewport)
    for child, visible, geometry in kept:
        child.setGeometry(geometry)
        child.setVisible(visible)


def _offscreen() -> bool:
    return QGuiApplication.platformName() == "offscreen"


def _is_gpu(view: QGraphicsView) -> bool:
    return type(view.viewport()).__name__ == "QOpenGLWidget"
