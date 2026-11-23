"""The "super highlight" (2026-11-23, maintainer's name): a yellow frame round a chosen icon button, for a choice that the icon colour alone does not
make obvious enough (first use: the ROIs tab's Persistent / Individual scope). A thin yellow line with rounded corners and no fill (a soft fill and
then a radial glow were tried the same day and dropped); the icon keeps its own colour. Use it on any checkable, fixed-size `QToolButton`:

    button.setStyleSheet(super_highlight_stylesheet())

Re-apply it on a theme switch the way the button's plain style sheet is (it does not read the theme: the yellow is the same on every theme).
"""

from __future__ import annotations

from lspr_ui import transparent_icon_button_stylesheet

SUPER_HIGHLIGHT_COLOR = "#facc15"  # amber-400 / yellow


def super_highlight_stylesheet(width: int = 2, radius: int = 5) -> str:
    """The transparent icon-button style sheet plus the yellow frame for the checked state."""
    return (
        transparent_icon_button_stylesheet()
        + f"\nQToolButton:checked {{ background: transparent; border: {width}px solid {SUPER_HIGHLIGHT_COLOR}; border-radius: {radius}px; }}"
    )
