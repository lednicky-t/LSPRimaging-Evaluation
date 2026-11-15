"""A second show / hide button for the histogram highlight overlay, for the Histogram range tool's pop-up (2026-11-16, maintainer request:
"in the left ribbon, the histogram selection, add to the right-click menu an icon of histogram (copied) that shows / hides the histogram
overview mask from the View tab, Masks menu").

The View tab's Masks menu owns the real control (`HistogramHighlightOverlayControls`, which `ImagePanel` listens to and which is remembered across
restarts). A widget can sit in one place only, so this is a copy: same icon (`histogram_selection_icon`: coloured bars when shown, flat grey when
hidden), a click is forwarded to the real control, and the real control's changes (the View tab, Show all / Hide all, a restore) come back here. It
holds no state of its own beyond its checked look, so the two can never disagree.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QToolButton, QWidget

from lspr_ui import transparent_icon_button_stylesheet

from ..ribbon_icon_size import BUTTON_SIZE
from .general_group import ICON_SIZE
from .histogram_highlight_overlay_controls import HistogramHighlightOverlayControls
from .mask_edit_tool_icons import histogram_selection_icon


class HistogramOverlayToggle(QToolButton):
    """Checked = the highlighted range is tinted on the image. Follows `controls` both ways."""

    def __init__(self, controls: HistogramHighlightOverlayControls, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controls = controls
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setStyleSheet(transparent_icon_button_stylesheet())
        self.setToolTip("Show or hide the histogram highlight overlay on the image (the same switch as View tab -> Masks -> Histogram).")
        self._follow(controls.is_shown())
        self.clicked.connect(lambda checked=False: controls.set_visible(bool(checked)))  # the real control reports it to the panel
        controls.visibility_changed.connect(self._follow)

    def _follow(self, shown: bool) -> None:
        """Look like the real control (no signal: the change came from there)."""
        blocked = self.blockSignals(True)
        try:
            self.setChecked(bool(shown))
        finally:
            self.blockSignals(blocked)
        self.setIcon(histogram_selection_icon(bool(shown)))
