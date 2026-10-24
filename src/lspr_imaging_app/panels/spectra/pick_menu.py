"""Pick-up menus for the Spectra ribbon: a button showing the current choice's picture, a menu listing every choice with its picture.

Replaces the plain drop-downs for Formula, Fit and Metric. The pictures are drawn here (equations with real subscripts; small curve sketches),
not vendored icon files: an equation cannot be an icon file, and the curve sketches are a few lines of painter code. Display only: it holds
the pick and reports it. It copies the little of `QComboBox` the panel and its tests use (`currentData`, `findData`, `setCurrentIndex`,
`currentIndexChanged`), so the panel's logic did not change when the drop-downs became pick-up menus.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextDocument
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme

from ..ribbon_icon_size import BUTTON_SIZE

_SCALE = 2  # pictures are drawn at twice the size they are shown, so they stay sharp on a high-DPI screen
_CURVE_SIZE = 20
_EQUATION_HEIGHT = 22
_FACE_PADDING = 6 + 12  # left padding + the menu-indicator arrow on the right
_ROW_HEIGHT = 30

PictureFor = Callable[[str], QPixmap]  # colour (hex) -> a pixmap with its devicePixelRatio set


# -- pictures ----------------------------------------------------------------------------------------------------------------------

def equation_picture(html: str) -> PictureFor:
    """An equation written as HTML (``<sub>`` for subscripts), drawn in the given colour on a transparent background."""

    def draw(color: str) -> QPixmap:
        document = QTextDocument()
        font = QFont()
        font.setPointSizeF(10.5)
        document.setDefaultFont(font)
        document.setDocumentMargin(0)
        document.setHtml(f'<span style="color:{color}; white-space:nowrap;">{html}</span>')
        width = int(document.idealWidth()) + 2
        height = max(_EQUATION_HEIGHT, int(document.size().height()))
        pixmap = QPixmap(width * _SCALE, height * _SCALE)
        pixmap.setDevicePixelRatio(_SCALE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.translate(0, (height - document.size().height()) / 2)
        document.drawContents(painter)
        painter.end()
        return pixmap

    return draw


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(_CURVE_SIZE * _SCALE, _CURVE_SIZE * _SCALE)
    pixmap.setDevicePixelRatio(_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


_DOTS = ((2.5, 16.5), (6.5, 11.5), (10.0, 5.5), (13.5, 10.5), (17.5, 16.0))  # a peak-shaped scatter of measured points


def _bell_path() -> QPainterPath:
    path = QPainterPath(QPointF(1.5, 17.0))
    path.cubicTo(7.0, 17.0, 7.5, 3.0, 10.0, 3.0)
    path.cubicTo(12.5, 3.0, 13.0, 17.0, 18.5, 17.0)
    return path


def _dots(painter: QPainter, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for x, y in _DOTS:
        painter.drawEllipse(QPointF(x, y), 1.4, 1.4)


def fit_picture(kind: str) -> PictureFor:
    """``none``: the measured points alone; ``polynomial``: a wavy line through them; ``gaussian``: a bell through them."""

    def draw(color: str) -> QPixmap:
        pixmap, painter = _canvas()
        ink = QColor(color)
        _dots(painter, QColor(get_active_theme().accent_blue))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if kind == "gaussian":
            painter.drawPath(_bell_path())
        elif kind == "polynomial":
            wave = QPainterPath(QPointF(1.5, 15.5))
            wave.cubicTo(5.0, 3.0, 8.5, 19.0, 11.0, 9.0)
            wave.cubicTo(12.5, 3.5, 15.5, 17.0, 18.5, 14.0)
            painter.drawPath(wave)
        painter.end()
        return pixmap

    return draw


def metric_picture(kind: str) -> PictureFor:
    """``maximum``: a bell with a diamond on its top; ``centroid``: a bell with a line through its centre of mass, ending on a dot."""

    def draw(color: str) -> QPixmap:
        pixmap, painter = _canvas()
        ink = QColor(color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ink, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(_bell_path())
        marker = QColor(get_active_theme().accent_gold)
        if kind == "maximum":
            diamond = QPainterPath(QPointF(10.0, 0.8))
            for x, y in ((12.2, 3.0), (10.0, 5.2), (7.8, 3.0)):
                diamond.lineTo(x, y)
            diamond.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(marker)
            painter.drawPath(diamond)
        else:
            painter.setPen(QPen(marker, 1.4, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(10.0, 2.0), QPointF(10.0, 17.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(marker)
            painter.drawEllipse(QPointF(10.0, 17.0), 1.8, 1.8)
        painter.end()
        return pixmap

    return draw


# -- the control -------------------------------------------------------------------------------------------------------------------


@dataclass
class _Choice:
    key: str
    name: str
    tip: str
    picture: PictureFor


class PickMenu(QToolButton):
    """Button face = the current choice; instant-popup menu = every choice, each as ``picture + name`` with the current one highlighted."""

    currentIndexChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, *, name_on_face: bool = True) -> None:
        super().__init__(parent)
        self._choices: list[_Choice] = []
        self._index = 0
        self._name_on_face = name_on_face  # False where the picture says everything (an equation)
        self._base_tip = ""
        self._rows: list[QToolButton] = []
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setAutoRaise(True)
        self.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon if name_on_face else Qt.ToolButtonStyle.ToolButtonIconOnly
        )

    # -- the part of QComboBox the panel uses ----------------------------------------------------------------------------------

    def add_choice(self, key: str, name: str, tip: str, picture: PictureFor) -> None:
        """Add a choice. Call ``finish()`` after the last one."""
        self._choices.append(_Choice(key, name, tip, picture))

    def count(self) -> int:
        return len(self._choices)

    def currentIndex(self) -> int:
        return self._index

    def currentData(self) -> str | None:
        return self._choices[self._index].key if self._choices else None

    def findData(self, key: object) -> int:
        return next((i for i, choice in enumerate(self._choices) if choice.key == key), -1)

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._choices) or index == self._index:
            return
        self._index = index
        self._refresh()
        self.currentIndexChanged.emit(index)

    def setToolTip(self, text: str) -> None:  # the tip of the whole control; the current choice's own tip is added to it
        self._base_tip = text
        self._refresh_tip()

    # -- building ------------------------------------------------------------------------------------------------------------------

    def finish(self) -> None:
        """Build the menu rows and the button once every choice is added."""
        for choice_index, choice in enumerate(self._choices):
            row = QToolButton(self._menu)
            row.setAutoRaise(True)
            row.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            row.setText(" " + choice.name)
            row.setToolTip(choice.tip)
            row.setCheckable(True)
            row.setFixedHeight(_ROW_HEIGHT)
            row.clicked.connect(lambda _checked=False, picked=choice_index: self._pick(picked))
            action = QWidgetAction(self._menu)
            action.setDefaultWidget(row)
            self._menu.addAction(action)
            self._rows.append(row)
        self.refresh_theme()

    def _pick(self, index: int) -> None:
        self._menu.close()
        self.setCurrentIndex(index)
        self._refresh()  # a click on the current row un-checks it: put the mark back

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; padding: 0 12px 0 6px; color: {theme.text_primary}; }}"
            f"QToolButton:hover {{ background: {theme.control_bg_hover}; }}"
            "QToolButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; right: 2px; }"
        )
        row_style = (
            f"QToolButton {{ background: transparent; border: none; padding: 0 12px 0 8px; text-align: left; color: {theme.text_primary}; }}"
            f"QToolButton:hover {{ background: {theme.control_bg_hover}; }}"
            f"QToolButton:checked {{ background: {theme.control_bg}; }}"
        )
        for row, choice in zip(self._rows, self._choices, strict=True):
            picture = choice.picture(theme.text_primary)
            row.setIcon(QIcon(picture))
            row.setIconSize(QSize(round(picture.width() / _SCALE), round(picture.height() / _SCALE)))
            row.setStyleSheet(row_style)
            row.adjustSize()
        widest = max((row.sizeHint().width() for row in self._rows), default=0)
        for row in self._rows:
            row.setMinimumWidth(widest)
        self._fix_size()
        self._refresh()

    def _fix_size(self) -> None:
        """Wide enough for the widest choice, so the ribbon does not shift when the pick changes."""
        if not self._choices:
            return
        color = get_active_theme().text_primary
        metrics = self.fontMetrics()
        widest = 0
        for choice in self._choices:
            picture_width = round(choice.picture(color).width() / _SCALE)
            text_width = metrics.horizontalAdvance(" " + choice.name) if self._name_on_face else 0
            widest = max(widest, picture_width + text_width)
        self.setFixedSize(widest + _FACE_PADDING, BUTTON_SIZE)

    def _refresh(self) -> None:
        if not self._choices:
            return
        choice = self._choices[self._index]
        picture = choice.picture(get_active_theme().text_primary)
        self.setIcon(QIcon(picture))
        self.setIconSize(QSize(round(picture.width() / _SCALE), round(picture.height() / _SCALE)))
        self.setText(" " + choice.name if self._name_on_face else "")
        for i, row in enumerate(self._rows):
            row.setChecked(i == self._index)
        self._refresh_tip()

    def _refresh_tip(self) -> None:
        current = self._choices[self._index].tip if self._choices else ""
        super().setToolTip(f"{self._base_tip}\n{current}".strip())


__all__ = ["PickMenu", "equation_picture", "fit_picture", "metric_picture"]
