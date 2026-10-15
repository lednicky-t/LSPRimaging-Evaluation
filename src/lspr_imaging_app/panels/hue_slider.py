"""The app's "pick a colour with one slider" control (2026-10-15): a rainbow slider that
moves only the *hue*; saturation and brightness stay fixed, so every pick is a clear,
clean colour that reads on both a dark and a light theme.

Shared by the Histogram's curve-style popup (`histogram/curve_style_menu.py`) and the
Image panel's slider-colour swatches (`image/slider_colors.py`), so the two can never
disagree about what "pick a colour" means. A hue-only slider cannot produce white,
grey or black; a control that needs those must offer a different picker.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QSlider, QWidget

HUE_SATURATION = 200  # of 255
HUE_VALUE = 240  # of 255
_HUE_GRADIENT = (
    "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #f00,stop:0.17 #ff0,stop:0.33 #0f0,"
    "stop:0.5 #0ff,stop:0.67 #00f,stop:0.83 #f0f,stop:1 #f00)"
)


def color_for_hue(hue: int) -> str:
    """The colour (``#rrggbb``) the slider gives at `hue` (0-359)."""
    return QColor.fromHsv(int(hue) % 360, HUE_SATURATION, HUE_VALUE).name()


def hue_of(color: str) -> int:
    """The hue (0-359) to show on the slider for `color` (a grey has no hue: 0)."""
    return max(QColor(color).hue(), 0) % 360


def make_hue_slider(parent: QWidget, color: str, *, width: int = 120) -> QSlider:
    """A rainbow slider (0-359) set to `color`'s hue; the caller connects `valueChanged`."""
    slider = QSlider(Qt.Orientation.Horizontal, parent)
    slider.setRange(0, 359)
    slider.setFixedWidth(width)
    slider.setToolTip("Colour (hue)")
    slider.setStyleSheet(
        f"QSlider::groove:horizontal {{ height: 8px; border-radius: 4px; background: {_HUE_GRADIENT}; }}"
        "QSlider::handle:horizontal { width: 8px; margin: -3px 0; border-radius: 4px;"
        " background: white; border: 1px solid #555; }"
    )
    slider.setValue(hue_of(color))
    return slider
