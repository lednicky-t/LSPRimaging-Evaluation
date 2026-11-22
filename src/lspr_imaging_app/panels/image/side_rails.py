"""The Image panel's two **side ribbons** (the selection toolbar and the View column) as movable, hideable rails (2026-11-22, maintainer request:
"retire the View tab in the top ribbon, keep it only on the right ribbon; make the left and right ribbons hideable like the top ribbon; maybe dockable,
so both can be on the same side").

Screen purpose: every rail sits beside the image, on its left or its right, and can be rolled up out of the way, so the person decides where the
buttons are and how much room the image gets.
Main user actions (the **grip**, a thin strip with three dots at the top of each rail):
- drag the grip to the other half of the image: the rail docks on that side (a frame shows where it would go while the mouse is down);
- double-click the grip: the rail rolls up, leaving only the grip as a narrow handle; a click on the handle rolls it down again (the top ribbon does
  the same with a double-click on its empty space and its "..." handle); the height eases over 160 ms, as the top ribbon's does;
- right-click the grip: a menu with the same two commands as words (Hide / Show, Move to the left / right of the image), for a person who does not
  find the drag.
Layout: left of the image the rails stand in a fixed order, the selection toolbar at the window's edge and the View column next to the image;
on the right side the order is mirrored (the View column next to the image, the selection toolbar at the edge). Both on one side is allowed.
Important states: a rail's side and whether it is rolled up (both remembered: `bind_ui_state`); the View column's roll-up is what the Histogram reads
as "the View controls are in front of the person" (`ImagePanel.view_controls_open`).
Validation / error messages: none of their own; a saved side that is not "left" or "right" falls back to the rail's default side.

A rail knows nothing about what is in it. Its content only has to say, through `on_side`, which way its own pop-ups open (to the right of a rail on
the left, to the left of a rail on the right: `beside_point`).

**The animation (2026-11-22, maintainer request; the first version rolled at once)** is only the picture: `set_expanded` changes the state, saves it and
reports `expanded_changed` at once, and the rail's height follows (`QPropertyAnimation` on `maximumHeight`, the top ribbon's way, `ribbon_drawer.py`).
The width changes at once, while the rail is only a strip high (a handle of 14 px, or the column's width). Restoring a saved state and the tests roll
without it (`animate=False`); a rail that is not on the screen does too.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMenu,
    QRubberBand,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import get_active_theme

from ...storage.ui_state_keys import RAIL_KEYS, read
from ..menu_icons import give_icon
from ..ui_state import UiStateStore

LEFT = "left"
RIGHT = "right"
SIDES = (LEFT, RIGHT)

_GRIP_HEIGHT = 12
_HANDLE_WIDTH = 14  # a rolled-up rail is only this wide: the handle
_QWIDGETSIZE_MAX = 16777215
_ANIMATION_MS = 160  # the top ribbon's (`ribbon_drawer.py`)
_DOT = 2


def valid_side(value: object, default: str) -> str:
    """`value` if it is "left" or "right", else `default` (a settings file a person may have edited)."""
    return value if value in SIDES else default  # type: ignore[return-value]


def beside_point(button: QWidget, side: str, width: int = 0) -> QPoint:
    """Where a pop-up of `button` begins, in the button's own coordinates: against its right edge for a rail on the left of the image (the pop-up
    opens over the image), `width` to the left of its left edge for a rail on the right."""
    return QPoint(button.width(), 0) if side == LEFT else QPoint(-int(width), 0)


def tooltip_point(button: QWidget, side: str, text: str) -> QPoint:
    """The same for a tool tip said beside a button (a wheel turn); its width is measured, as a tip has no size hint before it is shown."""
    width = QFontMetrics(QToolTip.font()).horizontalAdvance(text) + 14
    return button.mapToGlobal(beside_point(button, side, width))


class RailGrip(QWidget):
    """The strip at the top of a rail: a drag moves the rail, a click on a rolled-up rail's handle opens it, a double-click rolls it up, a right-click
    opens the menu. A real widget with `click()`, so a test needs no screen coordinates."""

    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    drag_moved = pyqtSignal(QPoint)  # the global position of the pointer, at every step of a drag
    dropped = pyqtSignal(QPoint)  # ... and where the button was let go
    menu_requested = pyqtSignal(QPoint)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedHeight(_GRIP_HEIGHT)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._press_at: QPoint | None = None
        self._dragging = False
        self._hover = False

    def click(self) -> None:
        self.clicked.emit()

    def is_dragging(self) -> bool:
        return self._dragging

    def sizeHint(self) -> QSize:
        return QSize(_HANDLE_WIDTH, _GRIP_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return QSize(_HANDLE_WIDTH, _GRIP_HEIGHT)

    # -- mouse --------------------------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_at is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        here = event.globalPosition().toPoint()
        if not self._dragging and (here - self._press_at).manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self._dragging:
            self.drag_moved.emit(here)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press_at is None:
            super().mouseReleaseEvent(event)
            return
        dragged, self._dragging, self._press_at = self._dragging, False, None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if dragged:
            self.dropped.emit(event.globalPosition().toPoint())
        elif self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = None  # the release that follows is the end of the double-click, not a click
            self.double_clicked.emit()

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.menu_requested.emit(event.globalPos())
        event.accept()

    def enterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        theme = get_active_theme()
        painter = QPainter(self)
        if self._hover:
            painter.fillRect(self.rect(), QColor(theme.control_bg_hover))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.text_primary if self._hover else theme.text_muted))
        gap = _DOT + 2
        left = (self.width() - (3 * _DOT + 2 * 2)) // 2
        top = (self.height() - _DOT) // 2
        for index in range(3):
            painter.drawRect(QRect(left + index * gap, top, _DOT, _DOT))


class SideRail(QFrame):
    """One column beside the image (`content`) under its grip. Rolled up, the content is hidden and the rail is only a handle wide and tall."""

    expanded_changed = pyqtSignal(bool)
    side_changed = pyqtSignal(str)
    side_asked = pyqtSignal(str)  # the grip's menu asked to move the rail to this side (the host does it)
    drag_moved = pyqtSignal(QPoint)
    dropped = pyqtSignal(QPoint)

    def __init__(self, key: str, title: str, content: QWidget, parent: QWidget, *, side: str = LEFT, on_side: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._title = title
        self.content = content
        self._on_side = on_side
        self._side = side
        self._expanded = True
        self._expanded_at = float("-inf")  # when a click last rolled it down: the second click of a double-click must not roll it up again
        self.setObjectName(f"{key}Rail")
        self._grip = RailGrip(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._grip)
        layout.addWidget(content, 1)
        self._width = content.maximumWidth() if content.maximumWidth() < _QWIDGETSIZE_MAX else content.sizeHint().width()
        self.setFixedWidth(self._width)
        self._animation = QPropertyAnimation(self, b"maximumHeight", self)
        self._animation.setDuration(_ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._animation.finished.connect(self._on_animation_finished)
        self._grip.clicked.connect(self._on_clicked)
        self._grip.double_clicked.connect(self._on_double_clicked)
        self._grip.drag_moved.connect(self.drag_moved)
        self._grip.dropped.connect(self.dropped)
        self._grip.menu_requested.connect(self._open_menu)
        self._refresh_grip()
        if on_side is not None:
            on_side(side)

    # -- state --------------------------------------------------------------------------------------------

    @property
    def grip(self) -> RailGrip:
        return self._grip

    @property
    def title(self) -> str:
        return self._title

    def side(self) -> str:
        return self._side

    def set_side(self, side: str) -> bool:
        """The rail now sits on `side` of the image (the host moves the widget); its content learns it for its pop-ups. False when it did."""
        side = valid_side(side, self._side)
        if side == self._side:
            return False
        self._side = side
        if self._on_side is not None:
            self._on_side(side)
        self._refresh_grip()
        self.side_changed.emit(side)
        return True

    def is_expanded(self) -> bool:
        return self._expanded

    def is_animating(self) -> bool:
        return self._animation.state() == QAbstractAnimation.State.Running

    def set_expanded(self, expanded: bool, *, animate: bool = True) -> bool:
        """Roll the rail down (the content shows) or up (only the handle is left). The height eases over 160 ms when `animate` and the rail is on the
        screen; the state, its saving and `expanded_changed` do not wait for it. False when the rail was in that state already."""
        expanded = bool(expanded)
        if expanded == self._expanded:
            return False
        self._expanded = expanded
        self._animation.stop()
        if animate and self.isVisible():
            self._roll(expanded)
        else:
            self._settle()
        self._refresh_grip()
        self.updateGeometry()
        self.expanded_changed.emit(expanded)
        return True

    def _settle(self) -> None:
        """The rail in its resting shape for the state it is in: the whole column, or only the handle."""
        self.content.setVisible(self._expanded)
        if self._expanded:
            self.setFixedWidth(self._width)
            self.setMinimumHeight(0)
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
        else:
            self.setFixedSize(_HANDLE_WIDTH, _GRIP_HEIGHT)

    def _roll(self, expanded: bool) -> None:
        """Ease the height from where it is now: down to the whole height the row gives the rail, or up to the handle's. The width follows at
        once, while the rail is only a strip high; the content stays shown while the rail rolls up and shows before it rolls down."""
        row = self.parentWidget()
        full = max(row.height() if row is not None else self.sizeHint().height(), _GRIP_HEIGHT)
        start = max(self.height(), _GRIP_HEIGHT)  # the handle's height, or where an interrupted roll got to
        if expanded:
            self.content.show()
            self.setFixedWidth(self._width)
        self.setMinimumHeight(0)
        self._animation.setStartValue(start)
        self._animation.setEndValue(full if expanded else _GRIP_HEIGHT)
        self._animation.start()

    def _on_animation_finished(self) -> None:
        if self._animation.currentTime() < self._animation.duration():
            return  # stopped half way by the next click, which sets the state up itself
        self._settle()

    def _refresh_grip(self) -> None:
        if self._expanded:
            tip = (
                f"{self._title}. Drag this strip to the other half of the image to move it there; double-click it to roll the ribbon up; "
                "right-click it for a menu."
            )
        else:
            tip = f"Show the {self._title.lower()}. (Drag this handle to move it to the other side of the image; right-click for a menu.)"
        self._grip.setToolTip(tip)

    # -- the grip ------------------------------------------------------------------------------------------

    def _on_clicked(self) -> None:
        if not self._expanded:
            self._expanded_at = time.monotonic()
            self.set_expanded(True)

    def _on_double_clicked(self) -> None:
        if time.monotonic() - self._expanded_at < QApplication.doubleClickInterval() / 1000.0:
            return  # this double-click began with the click that just rolled the ribbon down
        self.set_expanded(False)

    def _open_menu(self, at: QPoint) -> None:
        """Hide / Show and Move to the other side, as words (only what can be done now: the other side, never the one it is on)."""
        menu = QMenu(self)
        name = self._title.lower()
        if self._expanded:
            give_icon(menu.addAction(f"Hide the {name}", lambda: self.set_expanded(False)), "hide_ribbon")
        else:
            give_icon(menu.addAction(f"Show the {name}", lambda: self.set_expanded(True)), "show_ribbon")
        if self._side == LEFT:
            give_icon(menu.addAction("Move to the right of the image", lambda: self.side_asked.emit(RIGHT)), "move_right")
        else:
            give_icon(menu.addAction("Move to the left of the image", lambda: self.side_asked.emit(LEFT)), "move_left")
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(at)


class SideRails(QWidget):
    """The row under the ribbon: [rails on the left] [the image] [rails on the right]. It owns the order, the drag frame and the remembering."""

    arranged = pyqtSignal()  # a rail went to the other side

    def __init__(self, center: QWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._rails: dict[str, SideRail] = {}
        self._order: list[str] = []  # the order of adding = the order left to right on the left side (the edge first); mirrored on the right
        self._left = QHBoxLayout()
        self._right = QHBoxLayout()
        for box in (self._left, self._right):
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(0)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addLayout(self._left)
        row.addWidget(center, 1)
        row.addLayout(self._right)
        self._hint = QRubberBand(QRubberBand.Shape.Rectangle, self)  # where a dragged rail would dock
        self._hint.hide()

    # -- the rails ----------------------------------------------------------------------------------------

    def add_rail(self, rail: SideRail) -> None:
        self._rails[rail.key] = rail
        self._order.append(rail.key)
        rail.drag_moved.connect(lambda at, r=rail: self._show_hint(r, at))
        rail.dropped.connect(lambda at, r=rail: self._drop(r, at))
        rail.side_asked.connect(lambda side, r=rail: self.set_side(r.key, side))
        self._arrange()

    def rail(self, key: str) -> SideRail:
        return self._rails[key]

    def side_of(self, key: str) -> str:
        return self._rails[key].side()

    def sides(self) -> dict[str, str]:
        return {key: rail.side() for key, rail in self._rails.items()}

    def set_side(self, key: str, side: str) -> bool:
        """Dock the rail `key` on `side` of the image (an unknown side changes nothing). False when it was there already."""
        rail = self._rails[key]
        if not rail.set_side(side):
            return False
        self._arrange()
        self.arranged.emit()
        return True

    def _arrange(self) -> None:
        """Put every rail in the layout of its side: on the left in the order added (the edge first), on the right mirrored (the first added is
        the outermost, so each new one goes in front of it)."""
        for rail in self._rails.values():
            self._left.removeWidget(rail)
            self._right.removeWidget(rail)
        for key in self._order:
            rail = self._rails[key]
            # No alignment: an aligned item is given only its size hint, and the rail must take the image's whole height as the toolbar always did.
            # A rolled-up rail has a fixed height, so it stays at the top.
            if rail.side() == LEFT:
                self._left.addWidget(rail)
            else:
                self._right.insertWidget(0, rail)
            rail.show()

    # -- the drag -----------------------------------------------------------------------------------------

    def side_at(self, global_pos: QPoint) -> str:
        """The half of the row a pointer is over: the side a dropped rail docks on."""
        return LEFT if self.mapFromGlobal(global_pos).x() < self.width() / 2 else RIGHT

    def _show_hint(self, rail: SideRail, global_pos: QPoint) -> None:
        width = max(rail.width(), _HANDLE_WIDTH)
        x = 0 if self.side_at(global_pos) == LEFT else self.width() - width
        self._hint.setGeometry(x, 0, width, self.height())
        self._hint.show()
        self._hint.raise_()

    def _drop(self, rail: SideRail, global_pos: QPoint) -> None:
        self._hint.hide()
        self.set_side(rail.key, self.side_at(global_pos))

    # -- remembered state ----------------------------------------------------------------------------------

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Put each rail's side and roll-up back as saved, then save every change after. Restoring saves nothing (the connects come after)."""
        for key, rail in self._rails.items():
            side_key, open_key = RAIL_KEYS[key]
            self.set_side(key, valid_side(read(store, side_key), str(side_key.default)))
            rail.set_expanded(read(store, open_key) is not False, animate=False)
            rail.side_changed.connect(lambda side, k=side_key.key: store.set(k, side))
            rail.expanded_changed.connect(lambda open_, k=open_key.key: store.set(k, bool(open_)))
