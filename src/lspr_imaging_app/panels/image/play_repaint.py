"""One repaint of the Image view per frame while Play runs (2026-11-11).

**Why.** A QGraphicsView repaints itself when its scene reports a change. The scene reports it in a queued call, after the window's own
repaint request, so each Play frame cost two whole-window repaints: one for the sliders and the Sensorgram cursor, one for the image.
With the OpenGL image view a whole-window repaint hands the whole window to the screen (the Intel driver's buffer swap and copies:
~20 ms at 3840 x 2080 pixels, about two thirds of the frame). Measured on the maintainer's 4K monitor (Image + ROI table +
Sensorgram, `docs/play_diagnosis_2026-11-11.md`): 14.5 fps -> 19.6 fps with one repaint per frame, the same as with the Sensorgram hidden.

**How.** While Play runs the view does not repaint on scene changes (`NoViewportUpdate`); the panel calls `frame_shown()` once per
frame, last (after the Sensorgram has marked its cursor strip: `ImagePanel.play_frame_shown`). With the OpenGL view that repaint is
immediate and paints everything that is dirty, so the whole frame reaches the screen in one pass. The view's normal mode comes back
(and the view is repainted) when Play stops.

**Nothing may stay unpainted.** Zoom, pan, a tool's overlay or a ROI drag during Play change the scene without a new frame: a guard timer
repaints the view when no frame has been shown for `GUARD_MS` (a slow frame, a paused feed), so such changes still show.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QGraphicsView

GUARD_MS = 100  # no frame for this long: repaint anyway (interactions during a stalled Play)


class PlayRepaint(QObject):
    def __init__(self, view: QGraphicsView, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._saved_mode: QGraphicsView.ViewportUpdateMode | None = None
        self._last_repaint = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(GUARD_MS)
        self._timer.timeout.connect(self._guard)

    @property
    def active(self) -> bool:
        return self._saved_mode is not None

    def start(self) -> None:
        """Play started: the view repaints only when `frame_shown()` (or the guard) says so."""
        if self.active:
            return
        self._saved_mode = self._view.viewportUpdateMode()
        self._view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
        self._last_repaint = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        """Play stopped: the view's own mode again, and one repaint for whatever changed since the last frame."""
        if not self.active:
            return
        self._timer.stop()
        self._view.setViewportUpdateMode(self._saved_mode)
        self._saved_mode = None
        self._view.viewport().update()

    def frame_shown(self) -> None:
        """The frame and the step it released are set: repaint the view once. With the OpenGL view this runs the whole-window repaint now."""
        if not self.active:
            return
        self._last_repaint = time.perf_counter()
        self._view.viewport().update()

    def _guard(self) -> None:
        if time.perf_counter() - self._last_repaint >= GUARD_MS / 1000.0:
            self._last_repaint = time.perf_counter()
            self._view.viewport().update()
