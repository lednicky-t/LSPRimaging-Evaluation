"""The ribbons' icon-button look, shared by every panel.

Moved out of `image/general_group.py` (2026-11-02), which still re-exports it: panels outside the Image panel (Sensorgram, ROI table, the
Live and unit toggles) imported it from there, which loaded the whole Image package and closed an import cycle
(`sensorgram` -> `live_toggle` -> `image` -> `analysis_tab` -> `live_toggle`).
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QToolButton

from lspr_ui import transparent_icon_button_stylesheet

from .ribbon_icon_size import BUTTON_SIZE, ICON_SIZE


def style_general_icon_button(button: QToolButton) -> None:
    """The ribbon's icon-button look (`BUTTON_SIZE`), for any icon button of a ribbon."""
    button.setAutoRaise(True)
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setStyleSheet(transparent_icon_button_stylesheet())
