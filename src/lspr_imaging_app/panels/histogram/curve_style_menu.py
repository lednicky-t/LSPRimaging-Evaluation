"""Compact per-curve style popup for the Histogram legend (right-click a
legend entry): colour, line width, line style, and an optional translucent fill
under the line. Pure widget + a style dict; `plot.py` applies the dict.

A style is a plain dict holding only what the user changed (missing key = the
curve's default), so it saves to JSON as-is:
``{"color": "#rrggbb", "width": 2.0, "line": "dash", "fill": True, "alpha": 30}``
(`line` is one of `LINE_STYLES`, `alpha` is the fill opacity in percent).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMenu, QSlider, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from ..hue_slider import color_for_hue, hue_of, make_hue_slider

LINE_STYLES = ("solid", "dash", "dot")
LINE_WIDTHS = (1.0, 2.0, 3.5)
DEFAULT_FILL_ALPHA = 30  # percent
_PEN_STYLES = {"solid": Qt.PenStyle.SolidLine, "dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}

def pen_style(name: str | None) -> Qt.PenStyle:
    return _PEN_STYLES.get(name or "solid", Qt.PenStyle.SolidLine)


def _line_icon(color: QColor, width: float, style: Qt.PenStyle) -> QIcon:
    pixmap = QPixmap(26, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    pen = QPen(color, width)
    pen.setStyle(style)
    painter.setPen(pen)
    painter.drawLine(2, 7, 24, 7)
    painter.end()
    return QIcon(pixmap)


class CurveStyleEditor(QWidget):
    """The popup's content. `changed(style)` fires on every edit, carrying the
    whole override dict (empty after Reset)."""

    changed = pyqtSignal(dict)

    def __init__(
        self, style: dict, default_color: str, default_width: float, default_line: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._style = dict(style)
        self._default_color = default_color
        self._default_width = default_width
        self._default_line = default_line
        ink = self.palette().color(self.foregroundRole())

        self._hue = make_hue_slider(self, self._style.get("color", default_color))
        self._hue.valueChanged.connect(self._on_hue)
        reset = QToolButton(self)
        reset.setText("Reset")
        reset.setToolTip("Back to this line's default style")
        reset.clicked.connect(self._reset)

        self._width_buttons = self._button_group(
            [(w, _line_icon(ink, w, Qt.PenStyle.SolidLine), f"Width {w:g}") for w in LINE_WIDTHS], self._on_width
        )
        self._line_buttons = self._button_group(
            [(n, _line_icon(ink, 2.0, _PEN_STYLES[n]), n.capitalize()) for n in LINE_STYLES], self._on_line
        )

        self._fill = QCheckBox("Fill", self)
        self._fill.toggled.connect(self._on_fill)
        self._alpha = QSlider(Qt.Orientation.Horizontal, self)
        self._alpha.setRange(0, 100)
        self._alpha.setFixedWidth(90)
        self._alpha.setToolTip("Fill opacity (%)")
        self._alpha.valueChanged.connect(self._on_alpha)

        row_color = QHBoxLayout()
        row_color.addWidget(QLabel("Color", self))
        row_color.addWidget(self._hue)
        row_color.addWidget(reset)
        row_line = QHBoxLayout()
        row_line.setSpacing(2)
        for button in self._width_buttons.values():
            row_line.addWidget(button)
        row_line.addSpacing(8)
        for button in self._line_buttons.values():
            row_line.addWidget(button)
        row_fill = QHBoxLayout()
        row_fill.addWidget(self._fill)
        row_fill.addWidget(self._alpha)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addLayout(row_color)
        layout.addLayout(row_line)
        layout.addLayout(row_fill)
        self._sync()

    def _button_group(self, entries: list, handler: Callable) -> dict:
        buttons: dict = {}
        for value, icon, tip in entries:
            button = QToolButton(self)
            button.setIcon(icon)
            button.setCheckable(True)
            # Borderless; the chosen option shows as a soft highlight instead.
            button.setStyleSheet(
                "QToolButton { border: none; background: transparent; border-radius: 3px; }"
                "QToolButton:hover { background: rgba(128,128,128,40); }"
                "QToolButton:checked { background: rgba(128,128,128,90); }"
            )
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, v=value: handler(v))
            buttons[value] = button
        return buttons

    # -- edits --------------------------------------------------------------

    def _emit(self) -> None:
        self._sync()
        self.changed.emit(dict(self._style))

    def _on_hue(self, hue: int) -> None:
        self._style["color"] = color_for_hue(hue)
        self._emit()

    def _on_width(self, width: float) -> None:
        self._style["width"] = float(width)
        self._emit()

    def _on_line(self, name: str) -> None:
        self._style["line"] = name
        self._emit()

    def _on_fill(self, on: bool) -> None:
        self._style["fill"] = bool(on)
        self._style.setdefault("alpha", DEFAULT_FILL_ALPHA)
        self._emit()

    def _on_alpha(self, alpha: int) -> None:
        self._style["alpha"] = int(alpha)
        self._emit()

    def _reset(self) -> None:
        self._style = {}
        self._hue.blockSignals(True)
        self._hue.setValue(hue_of(self._default_color))
        self._hue.blockSignals(False)
        self._emit()

    def _sync(self) -> None:
        width = self._style.get("width", self._default_width)
        for value, button in self._width_buttons.items():
            button.setChecked(abs(value - width) < 1e-6)
        line = self._style.get("line", self._default_line)
        for value, button in self._line_buttons.items():
            button.setChecked(value == line)
        self._fill.blockSignals(True)
        self._fill.setChecked(bool(self._style.get("fill", False)))
        self._fill.blockSignals(False)
        self._alpha.blockSignals(True)
        self._alpha.setValue(int(self._style.get("alpha", DEFAULT_FILL_ALPHA)))
        self._alpha.blockSignals(False)
        self._alpha.setEnabled(self._fill.isChecked())


def show_curve_style_menu(
    global_pos: QPoint,
    parent: QWidget,
    style: dict,
    default_color: str,
    default_width: float,
    default_line: str,
    on_changed: Callable[[dict], None],
) -> None:
    """Opens the popup at `global_pos`; `on_changed(style)` is called live."""
    menu = QMenu(parent)
    editor = CurveStyleEditor(style, default_color, default_width, default_line, menu)
    editor.changed.connect(on_changed)
    action = QWidgetAction(menu)
    action.setDefaultWidget(editor)
    menu.addAction(action)
    menu.exec(global_pos)
