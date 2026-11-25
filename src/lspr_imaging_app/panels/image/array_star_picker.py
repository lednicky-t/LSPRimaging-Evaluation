"""Start-point picker of the Array* pop-out: nine dots, no buttons.

One painted widget (not nine buttons): a faint rectangle stands for the array, a dot sits at each of the nine places the selected ROI(s) can have in it (corners,
middle of the edges, middle). The picked dot is large and in the accent colour; the dot under the pointer grows a little. A click anywhere in a dot's third of
the square picks it. Reports ``(start_x, start_y)`` as `array_layout.START_FRACTIONS` (0 / 0.5 / 1).

Display only: no domain state, no ROI access.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from lspr_ui import get_active_theme

from ...roi.array_layout import NUMBER_BY, START_FRACTIONS, ArraySpec, numbering_order

SIZE = 54
_PAD = 9.0  # between the widget's edge and the outer dots (the dots keep their size; only the distances between them are small)

_VERTICAL = {0.0: "top", 0.5: "middle", 1.0: "bottom"}
_HORIZONTAL = {0.0: "left", 0.5: "middle", 1.0: "right"}


def _where(fx: float, fy: float) -> str:
    if (fx, fy) == (0.5, 0.5):
        return "the middle"
    return "the " + " ".join(part for part in (_VERTICAL[fy], _HORIZONTAL[fx]) if part != "middle")


class NumberingPicker(QWidget):
    """Where the ROI numbering starts, drawn as the reading path over nine dots: the start dot is big and in the accent colour, the line of the first row (or
    column) is an arrow, and the dashed lines jump to the next row (or column), like a text being read. A click in a quarter of the square picks that corner
    as the start; a **double-click on the corner that is already picked** switches between rows and columns (the path turns). Reports through `changed`;
    `by()` and `start()` say what is picked.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._by = NUMBER_BY[0]
        self._start = (0.0, 0.0)
        self._hover: tuple[float, float] | None = None
        self._refresh_tip()

    def by(self) -> str:
        return self._by

    def start(self) -> tuple[float, float]:
        """``(x, y)``, each 0 or 1: the side (left / right) and the end (top / bottom) the numbering starts at."""
        return self._start

    def set_numbering(self, by: str, start_x: float, start_y: float, *, emit: bool = False) -> None:
        if by not in NUMBER_BY or start_x not in (0.0, 1.0) or start_y not in (0.0, 1.0):
            raise ValueError(f"numbering is by one of {NUMBER_BY} from a corner (0 / 1 each), got {(by, start_x, start_y)}")
        self._by, self._start = by, (float(start_x), float(start_y))
        self._refresh_tip()
        self.update()
        if emit:
            self.changed.emit()

    def corner_at(self, x: float, y: float) -> tuple[float, float]:
        """The corner of the quarter of the square that the widget point ``(x, y)`` is in."""
        return (0.0 if x < SIZE / 2.0 else 1.0), (0.0 if y < SIZE / 2.0 else 1.0)

    def _refresh_tip(self) -> None:
        side = "right" if self._start[0] else "left"
        end = "bottom" if self._start[1] else "top"
        along = "row by row, across from the " + side if self._by == "rows" else "column by column, down from the " + end
        self.setToolTip(
            f"Numbering of the array's ROIs: {along}, starting at the {end} {side}. Click a corner to start there; "
            "double-click the picked corner to switch between rows and columns."
        )

    def _dot(self, gx: int, gy: int) -> QPointF:
        step = (SIZE - 2 * _PAD) / 2.0
        return QPointF(_PAD + gx * step, _PAD + gy * step)

    # -- pointer ------------------------------------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        corner = self.corner_at(event.position().x(), event.position().y())
        if corner != self._hover:
            self._hover = corner
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        corner = self.corner_at(event.position().x(), event.position().y())
        if corner != self._start:
            self.set_numbering(self._by, *corner, emit=True)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        """On the picked corner: rows <-> columns. On another corner: just pick it (a fast second click somewhere else is a click, not a switch)."""
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        corner = self.corner_at(event.position().x(), event.position().y())
        if corner == self._start:
            self.set_numbering(NUMBER_BY[(NUMBER_BY.index(self._by) + 1) % len(NUMBER_BY)], *self._start, emit=True)
        else:
            self.set_numbering(self._by, *corner, emit=True)

    # -- look ---------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        theme = get_active_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        spec = ArraySpec(number_by=self._by, number_start_x=self._start[0], number_start_y=self._start[1])
        path = [divmod(index, 3) for index in numbering_order(spec, 3, 3)]  # (row, column) of the nine dots, in reading order
        accent = QColor(theme.accent_blue)
        faint = QColor(theme.text_dim)
        faint.setAlphaF(0.55)
        dashed = QPen(faint, 1.0, Qt.PenStyle.DashLine)
        solid = QPen(accent, 1.6)
        for (r0, c0), (r1, c1) in zip(path, path[1:], strict=False):
            same_line = (r0 == r1) if self._by == "rows" else (c0 == c1)
            painter.setPen(solid if same_line else dashed)
            painter.drawLine(self._dot(c0, r0), self._dot(c1, r1))
        # the head of the first arrow: at the end of the first line, pointing the way it runs
        (r0, c0), (r1, c1) = path[1], path[2]
        a, b = self._dot(c0, r0), self._dot(c1, r1)
        direction = QPointF(b.x() - a.x(), b.y() - a.y())
        length = max((direction.x() ** 2 + direction.y() ** 2) ** 0.5, 1e-9)
        ux, uy = direction.x() / length, direction.y() / length
        painter.setPen(solid)
        for turn in (1, -1):
            painter.drawLine(b, QPointF(b.x() - 5 * ux + turn * 3 * -uy, b.y() - 5 * uy + turn * 3 * ux))
        painter.setPen(Qt.PenStyle.NoPen)
        for gy in range(3):
            for gx in range(3):
                is_start = (gx == 2 * self._start[0] and gy == 2 * self._start[1])
                is_hover = self._hover is not None and (gx, gy) == (2 * self._hover[0], 2 * self._hover[1])
                radius = 5.0 if is_start else 4.0 if is_hover else 2.4
                painter.setBrush(accent if is_start else QColor(theme.text_dim))
                painter.drawEllipse(self._dot(gx, gy), radius, radius)


class CornerPicker(QWidget):
    start_changed = pyqtSignal(float, float)  # (start_x, start_y), each 0 / 0.5 / 1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(SIZE, SIZE)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start = (0.0, 0.0)
        self._hover: tuple[float, float] | None = None
        self._refresh_tip()

    def start(self) -> tuple[float, float]:
        return self._start

    def set_start(self, fx: float, fy: float, *, emit: bool = False) -> None:
        if fx not in START_FRACTIONS or fy not in START_FRACTIONS:
            raise ValueError(f"the start point is one of {START_FRACTIONS} on each axis, got {(fx, fy)}")
        self._start = (float(fx), float(fy))
        self._refresh_tip()
        self.update()
        if emit:
            self.start_changed.emit(*self._start)

    def place_at(self, x: float, y: float) -> tuple[float, float]:
        """Which of the nine places the widget point ``(x, y)`` belongs to (the square is cut into thirds)."""
        third = SIZE / 3.0
        column = min(max(int(x // third), 0), 2)
        row = min(max(int(y // third), 0), 2)
        return START_FRACTIONS[column], START_FRACTIONS[row]

    def _dot_centre(self, fx: float, fy: float) -> QPointF:
        span = SIZE - 2 * _PAD
        return QPointF(_PAD + fx * span, _PAD + fy * span)

    def _refresh_tip(self) -> None:
        fx, fy = self._start
        self.setToolTip(
            f"Start point: where in the array the selected ROI(s) sit; the array grows away from them. Now: {_where(fx, fy)}. Click a dot to change it."
        )

    # -- pointer ------------------------------------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        place = self.place_at(event.position().x(), event.position().y())
        if place != self._hover:
            self._hover = place
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        place = self.place_at(event.position().x(), event.position().y())
        if place != self._start:
            self.set_start(*place, emit=True)

    # -- look ---------------------------------------------------------------------------------------------------

    def refresh_theme(self) -> None:
        self.update()

    def paintEvent(self, event) -> None:
        theme = get_active_theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QColor(theme.text_dim)
        frame.setAlphaF(0.45)
        painter.setPen(QPen(frame, 1.0))
        top_left, bottom_right = self._dot_centre(0.0, 0.0), self._dot_centre(1.0, 1.0)
        painter.drawRect(QRectF(top_left, bottom_right))
        painter.setPen(Qt.PenStyle.NoPen)
        for fy in START_FRACTIONS:
            for fx in START_FRACTIONS:
                picked = (fx, fy) == self._start
                radius = 5.0 if picked else 4.0 if (fx, fy) == self._hover else 2.8
                painter.setBrush(QColor(theme.accent_blue if picked else theme.text_dim))
                painter.drawEllipse(self._dot_centre(fx, fy), radius, radius)
