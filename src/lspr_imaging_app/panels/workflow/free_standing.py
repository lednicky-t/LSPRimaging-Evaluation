"""Chrome-less clickable icon widgets - the stable app's own "free
standing" naming (`gui/main_window_icons.py`), ported 2026-09-25 for the
Workflow panel's Dataset section (design doc §4a).

``ClickableIconLabel``: a plain ``QLabel`` plus a ``clicked`` signal fired
from ``mousePressEvent`` - no border, no background, no button chrome,
just an icon with a pointing-hand cursor. This is what the source uses for
``browse_button``/``open_explorer_button`` instead of a ``QToolButton`` -
ported verbatim (it's a five-line class).

Only the non-toggling base is ported so far - the source's checkable
variants (``FreeStandingToggleIconLabel``, whose icon swaps on click;
``FreeStandingToggleTextLabel``, whose *text* swaps on click) will follow
once a section that actually needs toggle state is built (e.g. Metadata's
Cube/Time display toggle)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel, QWidget


class ClickableIconLabel(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


def make_free_standing_icon_label(
    icon: QIcon,
    tooltip: str,
    *,
    size: int = 24,
    parent: QWidget | None = None,
) -> ClickableIconLabel:
    """Ported from the source's ``_free_standing_icon_label`` factory
    (``gui/main_window_icons.py``)."""
    label = ClickableIconLabel(parent)
    label.setPixmap(icon.pixmap(size, size))
    label.setToolTip(tooltip)
    label.setFixedSize(size + 4, size + 4)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setCursor(Qt.CursorShape.PointingHandCursor)
    return label
