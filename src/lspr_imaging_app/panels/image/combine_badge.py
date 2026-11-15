"""The little ``+`` / ``-`` / ``x`` beside the pointer that says how the next selection will combine with the current one
(2026-11-16, maintainer: "showing - symbol near the tool, like in Adobe Photoshop"; then: "in a transparent circle, and smaller (2x)"), and the
source of the *held mode* that the toolbar's mode button shows while Shift / Alt is down.

The keys themselves were already there (`how_for_keys` in `selection/area_selection_module.py`: Shift adds, Alt takes away,
Shift+Alt intersects, no key = the mode picker's sticky mode); this only *shows* the result, so you see it before you click.

* ``+`` add (Shift, or the picker on Add), ``-`` take away (Alt, or Subtract), ``x`` intersect (Shift+Alt, or Intersect).
* Replace shows nothing: it is the plain, classic selection.
* The brush is a tool like the others: the same symbols (Shift+Alt = ``x``: keep only what the stroke covers of the selection), nothing for a
  plain Replace. (Until 2026-11-16 it showed no ``+``, and treated Intersect and Replace as Add: fixed, maintainer: "it will always jump to
  adding mode", "I select replace, and when I start drawing it switches to add".)
* During a drag the symbol is the one the drag began with (the combine mode is fixed at the press), whatever keys are held now.
* An open ring (no fill) with the symbol, a white line under a coloured one so it reads on any image: blue for add and intersect, orange for
  take away (the colours of the mode icons, `selection_palette.py`).

**Held mode.** While Shift or Alt is down (or a drag that began with one), `held_mode_changed(how)` says which mode that is, ``None`` when no key
overrides the picked mode. `SelectionToolbar.show_held_mode` shows that mode's picture on the mode button, so the button follows the keys. The
picked (sticky) mode itself never changes.

**Why a timer.** A key press changes nothing in the canvas, and a pressed key goes to the focused widget, which is not the canvas
unless it was clicked. So while the pointer is over the image with a selection tool armed, a 60 ms timer asks the system which
modifier keys are down (`QGuiApplication.queryKeyboardModifiers`, which needs no focus) and whether the pointer is still over the image.
The timer stops as soon as the badge hides, so an idle panel pays nothing.

The badge is a graphics item that ignores the view's zoom (it is always a few screen pixels), placed at the pointer's view position.
Pure display: no selection logic, no domain state.
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsView

from ...selection import AreaSelectionModule
from ...selection.area_selection_module import ADD, INTERSECT, SUBTRACT, how_for_keys
from .selection_palette import REMOVED_COLOR, RESULT_COLOR

PLUS, MINUS, CROSS = "plus", "minus", "cross"

_POLL_MS = 60
_Z_VALUE = 62  # above the outlines (60 .. 60.2) and the brush ring (61)
_SIZE = 8.0  # the ring's diameter, screen pixels (half of the first version's 15)
_OFFSET = 10.0  # from the pointer to the ring's top-left corner, screen pixels (right of and below the cross cursor)
_LINE = 1.2  # the coloured line's width; under it a white one of _HALO (thicker would fill the 8 px ring: it must stay open)
_HALO = 2.2
_COLOR = {PLUS: RESULT_COLOR, MINUS: REMOVED_COLOR, CROSS: RESULT_COLOR}


def symbol_for(how: str) -> str | None:
    """The symbol for a combine mode: ``PLUS`` / ``MINUS`` / ``CROSS``, or ``None`` (Replace: nothing to say).
    Every tool has the same three, the brush too (Shift+Alt keeps what the stroke covers); a plain Replace stays unmarked. Pure (no Qt)."""
    return {ADD: PLUS, SUBTRACT: MINUS, INTERSECT: CROSS}.get(how)


class _BadgeItem(QGraphicsItem):
    """An open ring with the symbol inside: a white line under a coloured one, no fill (the image shows through)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)  # item units are screen pixels
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # purely visual, never eats clicks
        self.setZValue(_Z_VALUE)
        self._symbol: str | None = None

    def symbol(self) -> str | None:
        return self._symbol

    def set_symbol(self, symbol: str | None) -> None:
        if symbol != self._symbol:
            self._symbol = symbol
            self.update()

    def boundingRect(self) -> QRectF:
        pad = _HALO
        return QRectF(_OFFSET - pad, _OFFSET - pad, _SIZE + 2.0 * pad, _SIZE + 2.0 * pad)

    def paint(self, painter: QPainter | None, option: object = None, widget: object = None) -> None:
        if painter is None or self._symbol is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ring = QRectF(_OFFSET, _OFFSET, _SIZE, _SIZE)
        centre, reach = ring.center(), _SIZE * 0.25
        strokes = []
        if self._symbol in (PLUS, MINUS):
            strokes.append((QPointF(centre.x() - reach, centre.y()), QPointF(centre.x() + reach, centre.y())))
        if self._symbol == PLUS:
            strokes.append((QPointF(centre.x(), centre.y() - reach), QPointF(centre.x(), centre.y() + reach)))
        if self._symbol == CROSS:
            strokes.append((QPointF(centre.x() - reach, centre.y() - reach), QPointF(centre.x() + reach, centre.y() + reach)))
            strokes.append((QPointF(centre.x() - reach, centre.y() + reach), QPointF(centre.x() + reach, centre.y() - reach)))
        for color, width in (("#ffffff", _HALO), (_COLOR[self._symbol], _LINE)):
            painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.drawEllipse(ring)
            for start, end in strokes:
                painter.drawLine(start, end)


class CombineBadge(QObject):
    """Shows `symbol_for` beside the pointer while a selection tool is armed and the pointer is over the image, and says which mode the held
    keys give (`held_mode_changed`)."""

    held_mode_changed = pyqtSignal(object)  # str (REPLACE / ADD / SUBTRACT / INTERSECT) while a key overrides the picked mode, else None

    def __init__(
        self,
        plot: pg.PlotItem,
        view: QGraphicsView,
        selection: AreaSelectionModule,
        armed: Callable[[], bool],
        gesture_how: Callable[[], str | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._plot = plot
        self._view = view
        self._selection = selection
        self._armed = armed  # a selection tool is the active tool (and a picker mode is chosen)
        self._gesture_how = gesture_how  # the combine mode of the drag in progress, or None
        self._held: str | None = None
        self._item = _BadgeItem()
        self._item.hide()
        plot.addItem(self._item, ignoreBounds=True)
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)

    def symbol(self) -> str | None:
        """The symbol now shown (``None`` when the badge is hidden or empty)."""
        return self._item.symbol() if self._item.isVisible() else None

    def held_mode(self) -> str | None:
        """The mode a held key (or the drag in progress) gives, ``None`` when the picked mode applies."""
        return self._held

    def follow(self, scene_pos: object) -> None:
        """The pointer moved to *scene_pos*: put the badge beside it, or hide it when no selection tool is armed or the pointer is
        off the image."""
        if not self._armed() or not self._plot.vb.sceneBoundingRect().contains(scene_pos):  # type: ignore[arg-type]
            self.hide()
            return
        self._item.setPos(self._plot.vb.mapSceneToView(scene_pos))  # type: ignore[arg-type]
        self._refresh()
        if not self._timer.isActive():
            self._timer.start()

    def hide(self) -> None:
        self._timer.stop()
        if self._item.isVisible():
            self._item.hide()
        self._set_held(None)

    def _set_held(self, how: str | None) -> None:
        if how != self._held:
            self._held = how
            self.held_mode_changed.emit(how)

    def _refresh(self) -> None:
        sticky = self._selection.combine_mode()
        fixed = self._gesture_how()
        if fixed is not None:  # a drag keeps the mode it began with
            how = fixed
            held = fixed if fixed != sticky else None
        else:
            modifiers = QGuiApplication.queryKeyboardModifiers()
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
            how = how_for_keys(shift, alt, sticky)
            held = how if (shift or alt) else None
        self._set_held(held)
        symbol = symbol_for(how)
        self._item.set_symbol(symbol)
        self._item.setVisible(symbol is not None)

    def _tick(self) -> None:
        """Keys can change with the pointer standing still, and the pointer can leave the image without a scene event."""
        viewport = self._view.viewport()
        if not self._armed() or viewport is None or not viewport.rect().contains(viewport.mapFromGlobal(QCursor.pos())):
            self.hide()
            return
        self._refresh()
