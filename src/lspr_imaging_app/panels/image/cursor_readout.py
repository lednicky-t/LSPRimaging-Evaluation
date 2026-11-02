"""The cursor readout of the Image panel: the "(col, row) = value" text on the canvas's top-left corner, and the pixel lookup behind it.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 2). The code is the code that was in `ImagePanel`; this class now
also owns the label. `CursorOverlay` (panels/cursor_overlay.py, shared with the Histogram plot) calls `value_at` and `set_text`.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from lspr_ui import get_active_theme
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from .no_data import format_pixel_value


class CursorReadout:
    def __init__(self, viewport: QWidget, image_item: pg.ImageItem, *, value_changed: Callable[[float | None], None]) -> None:
        """``viewport``: the canvas viewport the label is drawn on. ``image_item``: the item holding the displayed pixels.
        ``value_changed(value)``: the intensity under the cursor, or ``None`` once the readout is off or the pixel has no value."""
        self._image_item = image_item
        self._value_changed = value_changed
        # Cursor readout text lives on the canvas, top-left (maintainer,
        # 2026-10-03) - the toggle itself sits in the "General" group.
        self.label = QLabel(viewport)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label.move(6, 6)
        self.label.hide()

    def restyle(self) -> None:
        theme = get_active_theme()
        self.label.setStyleSheet(
            f"color: {theme.text_primary}; background: rgba(0, 0, 0, 140); padding: 1px 4px; border-radius: 2px;"
        )

    def set_text(self, text: str) -> None:
        """Sink for `CursorOverlay`: shows *text* in the canvas's top-left
        corner, hides the label for an empty string."""
        self.restyle()
        self.label.setText(text)
        self.label.adjustSize()
        self.label.setVisible(bool(text))
        self.label.raise_()
        if not text:
            self._value_changed(None)

    def value_at(self, view_x: float, view_y: float) -> tuple[float, float, str] | None:
        """Floors to the pixel under the cursor and reads its displayed
        intensity - `_image_item.image` is exactly what is on screen
        (post crop/rotate/mask/CC/background, `_on_rendered`'s own float32
        array), so this reads no state this panel doesn't already own.
        `None` off the image entirely, matching `CursorOverlay`'s "show
        nothing" contract for an out-of-data cursor position."""
        image = self._image_item.image
        if image is None:
            return None
        col = int(np.floor(view_x))
        row = int(np.floor(view_y))
        height, width = image.shape[:2]
        if not (0 <= row < height and 0 <= col < width):
            return None
        value = float(image[row, col])
        self._value_changed(value if np.isfinite(value) else None)
        return col + 0.5, row + 0.5, f"({col}, {row}) = {format_pixel_value(value)}"
