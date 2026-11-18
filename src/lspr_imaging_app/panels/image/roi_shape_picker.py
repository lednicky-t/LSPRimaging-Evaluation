"""Image panel ribbon, "ROIs" tab: the "Shape" pick-up menu (circle or rectangle).

It sets the shape the **Add ROI tool** places when the image is clicked (`shape_changed`, which `RoiCreation.set_picked_shape` follows). Polygon and freeform
entries were listed here, greyed, until 2026-11-18 and were removed (maintainer's choice): a free-form region is made with the selection toolbar's Lasso, Wand or
brush and the right-click menu's Add ROI > Current selection, which gives a pixel-mask ROI. The right-click menu's Add ROI > Circle / Rectangle place that shape
whatever the picker says (a control does what it says). The pick is remembered (`bind_ui_state`).

The icons are filled shapes drawn with `QPainter` (the Tabler set has outlines only).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPixmap, QPolygonF
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme

from .general_group import ICON_SIZE, style_general_icon_button

_RENDER_SIZE = ICON_SIZE * 2
_MARGIN = _RENDER_SIZE * 0.14

# key, menu text, available now
SHAPES = (
    ("circle", "Circle", True),
    ("rectangle", "Rectangle", True),
)


def _shape_path(shape: str) -> QPainterPath:
    lo, hi = _MARGIN, _RENDER_SIZE - _MARGIN
    span = hi - lo
    path = QPainterPath()
    if shape == "circle":
        path.addEllipse(QRectF(lo, lo, span, span))
    elif shape == "rectangle":
        path.addRect(QRectF(lo, lo + span * 0.12, span, span * 0.76))
    elif shape == "polygon":  # an irregular, straight-sided outline
        points = ((0.50, 0.00), (1.00, 0.38), (0.82, 1.00), (0.22, 0.92), (0.00, 0.35))
        path.addPolygon(QPolygonF([QPointF(lo + x * span, lo + y * span) for x, y in points]))
        path.closeSubpath()
    else:  # freeform: a smooth, uneven blob
        def at(x: float, y: float) -> QPointF:
            return QPointF(lo + x * span, lo + y * span)

        path.moveTo(at(0.10, 0.45))
        path.cubicTo(at(0.00, 0.10), at(0.45, 0.00), at(0.62, 0.12))
        path.cubicTo(at(0.85, 0.25), at(1.00, 0.30), at(0.95, 0.60))
        path.cubicTo(at(0.90, 0.95), at(0.60, 0.85), at(0.45, 1.00))
        path.cubicTo(at(0.20, 1.05), at(0.20, 0.75), at(0.12, 0.65))
        path.cubicTo(at(0.05, 0.58), at(0.12, 0.50), at(0.10, 0.45))
        path.closeSubpath()
    return path


def shape_icon(shape: str, color: str) -> QIcon:
    pixmap = QPixmap(_RENDER_SIZE, _RENDER_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPath(_shape_path(shape))
    painter.end()
    return QIcon(pixmap)


class RoiShapePicker(QToolButton):
    shape_changed = pyqtSignal(str)  # "circle" or "rectangle"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shape = "circle"
        style_general_icon_button(self)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        group = QActionGroup(self)
        self._actions: dict[str, QAction] = {}
        for key, text, available in SHAPES:
            action = QAction(text if available else f"{text} (not available yet)", group)
            action.setCheckable(True)
            action.setEnabled(available)
            action.triggered.connect(lambda _checked=False, picked=key: self.set_shape(picked))
            self._menu.addAction(action)
            self._actions[key] = action
        self.setMenu(self._menu)
        self.refresh_theme()

    def shape(self) -> str:
        return self._shape

    def set_shape(self, shape: str) -> None:
        """Pick a shape; ignored if unknown or not available yet."""
        action = self._actions.get(shape)
        changed = False
        if action is not None and action.isEnabled():
            changed = shape != self._shape
            self._shape = shape
        self._refresh()
        if changed:
            self.shape_changed.emit(shape)

    def bind_ui_state(self, store, key: str) -> None:
        """Put the pick back as last left (a saved value that is not a shape of this menu is ignored), then save each change."""
        saved = store.get(key)
        if isinstance(saved, str) and saved in self._actions:
            self.set_shape(saved)
        self.shape_changed.connect(lambda shape: store.set(key, shape))

    def refresh_theme(self) -> None:
        style_general_icon_button(self)
        color = get_active_theme().text_primary
        for key, action in self._actions.items():
            action.setIcon(shape_icon(key, color))
        self._refresh()

    def _refresh(self) -> None:
        self._actions[self._shape].setChecked(True)
        self.setIcon(shape_icon(self._shape, get_active_theme().accent_blue))
        label = next(text for key, text, _available in SHAPES if key == self._shape)
        self.setToolTip(f"ROI shape the Add ROI tool places: {label}. (The right-click menu's Add ROI offers every shape.)")
