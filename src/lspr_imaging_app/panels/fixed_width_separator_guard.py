"""Suppress hover/drag on the one dock separator that can never actually do
anything - the boundary running along a fixed-width panel's edge (2026-09-27,
maintainer report: Workflow's own hover highlight shows on its right edge
even though Workflow can't resize there at all).

**Why not a custom-painted overlay** (the maintainer's own explicit ask):
`QMainWindow::separator` has exactly one style rule shared by every
separator in the window - there is no `:horizontal`/`:vertical` pseudo-
state, let alone a per-instance one, to make just this one segment behave
differently in QSS alone (same limitation already hit twice today - the
gradient-based border attempt and the dot-grip `background-image` attempt,
see `gui/app_theme.py`'s `dock_separator_stylesheet`). A painted overlay
tracking live panel geometry would work but is real added machinery for a
purely cosmetic (and, if a drag were attempted, purely inert) mismatch.

**What this does instead**: an event filter installed on the main window,
which sees every mouse event *before* `QMainWindowLayout`'s own handling
does (Qt delivers to installed event filters first, and a filter
returning `True` stops the event going any further). When the cursor is
within a small dead-zone rect hugging the fixed-width dock's far edge, the
filter swallows the event outright - `QMainWindowLayout` never learns the
mouse is there at all, so it never marks that separator as hovered (no
highlight) and never starts a drag from it (nothing to clamp to a no-op).
No painting, no geometry to keep in sync beyond re-reading the dock's own
current position on each event - which the filter already has to do
regardless, since a fixed *width* can still move up and down as the
window resizes.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect
from PyQt6.QtWidgets import QDockWidget, QMainWindow

_SUPPRESSED_EVENT_TYPES = (
    QEvent.Type.MouseMove,
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseButtonRelease,
    QEvent.Type.MouseButtonDblClick,
)
# Half-width of the dead zone around the fixed edge, in each direction -
# a little wider than the 4px separator itself (dock_separator_stylesheet)
# so no edge pixel of the real hit area is missed to rounding.
_DEAD_ZONE_HALF_WIDTH_PX = 4


class FixedWidthSeparatorGuard(QObject):
    """Install via ``window.installEventFilter(guard)`` - keep a reference
    alive on the caller's side (PyQt does not keep the Python wrapper of an
    installed filter alive on its own)."""

    def __init__(self, window: QMainWindow, fixed_width_dock: QDockWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._dock = fixed_width_dock

    def _dead_zone_rect(self) -> QRect | None:
        if not self._dock.isVisible() or self._dock.isFloating():
            return None
        top_left = self._dock.mapTo(self._window, QPoint(0, 0))
        edge_x = top_left.x() + self._dock.width()
        return QRect(
            edge_x - _DEAD_ZONE_HALF_WIDTH_PX,
            top_left.y(),
            2 * _DEAD_ZONE_HALF_WIDTH_PX,
            self._dock.height(),
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - overriding Qt's own camelCase API
        if watched is self._window and event.type() in _SUPPRESSED_EVENT_TYPES:
            dead_zone = self._dead_zone_rect()
            if dead_zone is not None:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                if dead_zone.contains(pos):
                    return True
        return super().eventFilter(watched, event)
