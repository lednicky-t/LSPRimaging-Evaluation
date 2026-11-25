"""The "find the array" icon of the Find* button (moved here 2026-11-25 from the retired Array group's `array_controls.py`)."""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap

from lspr_ui import load_tabler_icon

from .general_group import ICON_SIZE


def find_array_icon(dots_color: str, glass_color: str) -> QIcon:
    """3x3 dots (an array) with a magnifying glass over the bottom-right corner: "find the array"."""
    size = ICON_SIZE * 2
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.drawPixmap(0, 0, load_tabler_icon("grid-dots", color=dots_color, size=size, stroke_width=2.6).pixmap(size, size))
    outline = load_tabler_icon("search", color="#000000", size=size, stroke_width=4.6).pixmap(size, size)  # thin black border
    glass = load_tabler_icon("search", color=glass_color, size=size, stroke_width=3.4).pixmap(size, size)
    big = size * 7 // 10
    target = QRect(size - big + size // 10, size - big + size // 10, big, big)
    painter.drawPixmap(target, outline)
    painter.drawPixmap(target, glass)
    painter.end()
    return QIcon(pixmap)
