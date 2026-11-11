"""The Sensorgram plot repaints only its cube cursor while Play runs (2026-11-11).

**Why.** The cursor moves at every Play frame. A QGraphicsView repaints for a scene change in a queued call that runs after the window's
own repaint request, so the cursor took a whole-window repaint of its own besides the image's (about 20 ms of a 69 ms frame on a 4K
window with the OpenGL image view: `docs/play_diagnosis_2026-11-11.md`). The plot's drawing itself is only ~2 ms.

**How.** While Play runs the view does not repaint on scene changes (`NoViewportUpdate`). The Image panel says when a frame is shown
(`ImagePanel.play_frame_shown`, before it repaints its own view) and `frame_shown()` marks the strip the cursor line and its handle
occupy (old and new place) as dirty at once; the window then repaints once, for everything. The strips of the last few frames are
remembered so a scene change that comes late from a cursor move is recognised as one.

**Nothing else may stay unpainted.** Every other change the scene reports during Play (a new sensorgram arriving from a computation, a
step edited, the baseline moved) is forwarded to the view's repaint as the view itself would have done: only changes that lie inside a
cursor strip are skipped. The normal mode comes back, with one full repaint, when Play stops.
"""

from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QObject, QRect
from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

_PAD_PX = 4  # around the cursor items: their pen width, the hover pen, anti-aliasing
_HISTORY = 8  # strips remembered (frames)


class PlayCursorRepaint(QObject):
    def __init__(self, view: QGraphicsView, scene: QGraphicsScene, cursor_items, parent: QObject | None = None) -> None:
        """`cursor_items`: the graphics items of the cube cursor (the dashed line and the handle on the axis)."""
        super().__init__(parent)
        self._view = view
        self._scene = scene
        self._items = tuple(cursor_items)
        self._saved_mode: QGraphicsView.ViewportUpdateMode | None = None
        self._strips: deque[QRect] = deque(maxlen=_HISTORY)

    @property
    def active(self) -> bool:
        return self._saved_mode is not None

    def start(self) -> None:
        if self.active:
            return
        self._saved_mode = self._view.viewportUpdateMode()
        self._view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
        self._strips.clear()
        self._scene.changed.connect(self._on_scene_changed)

    def stop(self) -> None:
        if not self.active:
            return
        self._scene.changed.disconnect(self._on_scene_changed)
        self._view.setViewportUpdateMode(self._saved_mode)
        self._saved_mode = None
        self._strips.clear()
        self._view.viewport().update()

    def _strip_now(self) -> QRect:
        rect = QRect()
        for item in self._items:
            rect = rect.united(self._view.mapFromScene(item.sceneBoundingRect()).boundingRect())
        return rect.adjusted(-_PAD_PX, -_PAD_PX, _PAD_PX, _PAD_PX)

    def frame_shown(self) -> None:
        """The cursor has moved for the frame about to be shown: mark where it was and where it is as dirty."""
        if not self.active:
            return
        now = self._strip_now()
        dirty = now.united(self._strips[-1]) if self._strips else now
        self._strips.append(now)
        self._view.viewport().update(dirty)

    def _on_scene_changed(self, regions) -> None:
        """What the view would have repainted for a scene change, except the cursor's own."""
        for region in regions:
            rect = self._view.mapFromScene(region).boundingRect()
            if any(strip.contains(rect) for strip in self._strips):
                continue
            self._view.viewport().update(rect.adjusted(-2, -2, 2, 2))
