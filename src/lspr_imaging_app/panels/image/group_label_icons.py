"""The two drawn pictures of the Groups label menu (`group_label_menu.py`), painted rather than loaded because
they are text: the ordinal "1st" / "nth" with raised letters (which ROI of the group carries the label), and the
word "label" turned to the current text angle.

Each is a 30 x 30 logical-pixel icon drawn at 2x so the text stays crisp. ``color`` is the pen colour (the
picked / dim colour is the caller's choice).
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPixmap

SIZE = 30  # logical px
_SCALE = 2
_ORDINALS = {"first": ("1", "st"), "last": ("n", "th")}
_LABEL_WORD = "label"


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(SIZE * _SCALE, SIZE * _SCALE)
    pixmap.setDevicePixelRatio(float(_SCALE))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    return pixmap, painter


def ordinal_icon(kind: str, color: str) -> QIcon:
    """"1st" for ``"first"``, "nth" for ``"last"``: the figure big, its two letters smaller and raised."""
    base, raised = _ORDINALS[kind]
    big = QFont()
    big.setBold(True)
    big.setPointSizeF(11.0)
    small = QFont(big)
    small.setPointSizeF(7.0)
    big_metrics, small_metrics = QFontMetricsF(big), QFontMetricsF(small)
    left = (SIZE - big_metrics.horizontalAdvance(base) - small_metrics.horizontalAdvance(raised)) / 2.0
    lift = 0.42 * big_metrics.ascent()
    # Centre the block from the top of the raised letters to the baseline.
    baseline = SIZE / 2.0 + (lift + small_metrics.ascent()) / 2.0
    pixmap, painter = _canvas()
    painter.setPen(QColor(color))
    painter.setFont(big)
    painter.drawText(QPointF(left, baseline), base)
    painter.setFont(small)
    painter.drawText(QPointF(left + big_metrics.horizontalAdvance(base), baseline - lift), raised)
    painter.end()
    return QIcon(pixmap)


def orientation_pixmap(angle: int, color: str) -> QPixmap:
    """The word "label" turned ``angle`` degrees counter-clockwise (0 = horizontal, 45 = rising, 90 = reading
    upwards), the way the group labels are drawn on the image."""
    font = QFont()
    font.setBold(True)
    font.setPointSizeF(8.5)
    metrics = QFontMetricsF(font)
    pixmap, painter = _canvas()
    painter.setPen(QColor(color))
    painter.setFont(font)
    painter.translate(SIZE / 2.0, SIZE / 2.0)
    painter.rotate(-float(angle))
    painter.drawText(QPointF(-metrics.horizontalAdvance(_LABEL_WORD) / 2.0, (metrics.ascent() - metrics.descent()) / 2.0), _LABEL_WORD)
    painter.end()
    return pixmap
