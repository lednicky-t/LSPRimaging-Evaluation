"""Image panel tool ribbon - "Histogram" tab display controls (2026-10-02): a
show/hide toggle, a color swatch, and a transparency slider for the
intensity-range ("Highlight") selection drawn over the image, ported from
the stable app's highlight-overlay controls (`gui/main_window.py`'s
`show_highlight_check`/`highlight_color_button`/`highlight_alpha_slider`,
wired in `gui/overlay_manager.py`'s `_update_selected_intensity_overlay`).
Same shape as `mask_overlay_controls.py`'s `MaskOverlayControls` - see that
module's docstring for the general pattern this follows - but for the
"Histogram" ribbon tab instead of "Mask" (that tab was a seeded placeholder
until this).

This is display-only cosmetic state, not part of `HighlightRangeModule`.
`HighlightRangeModule` owns the selected intensity range itself (the
``(min, max)`` pair driving *what* pixels are selected) - shared,
cross-cutting state several modules read (see that module's docstring).
Whether/how the selected pixels are tinted on screen is a different kind of
thing and belongs to whoever draws it; `ImagePanel` is this widget's only
listener and owns the visible/color/alpha state itself, same as it already
does for the mask-overlay tint.

Deliberately keeps one icon shape rather than swapping between a
visible/hidden pair the way `MaskOverlayControls` swaps "mask"/"mask-off" -
this matches the stable app's own `_make_view_toggle_icon(kind="highlight")`,
which only recolors (green when visible, dim when not) rather than drawing a
different glyph; the stable app reserves the on/off icon-swap for "mask" and
"reference_points" specifically, not every toggle.

**Icon updated 2026-10-02** (maintainer request: "change to this icon the
icon in histogram widget, on is colored, off is all white/gray") to use the
same bespoke pictogram the Mask tab's "Edit" picker uses for its own
"Histogram selection" tool
(`mask_edit_tool_icons.histogram_selection_icon`) - a 5-bar glyph with the
middle three bars colored when visible, collapsing to one flat muted tone
when hidden, via that function's own `active` flag. Replaces the plain
vendored `chart-histogram` Tabler icon this toggle used until now (still
vendored at `packages/lspr_ui/src/lspr_ui/icon_assets/chart-histogram.svg`,
and still the uncolored base `histogram_selection_icon` itself draws from -
see that function's docstring).
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QHBoxLayout, QToolButton, QWidget

from lspr_ui import CompactWedgeSlider, GuiTheme, get_active_theme, transparent_icon_button_stylesheet

from .mask_edit_tool_icons import histogram_selection_icon

_BUTTON_SIZE = 28  # matches MaskOverlayControls's own icon buttons, same row shape
_ICON_SIZE = 22
_SWATCH_SIZE = 14
_SLIDER_WIDTH = 18  # matches MaskOverlayControls's own narrowed wedge - see that module's docstring


class HistogramHighlightOverlayControls(QWidget):
    """Show/hide toggle + color swatch + transparency slider for the
    histogram intensity-selection tint. Emits on every user change; carries
    no state of its own beyond what it needs to repaint itself (the swatch
    color, the toggle's checked state) - `ImagePanel` is the source of
    truth, same shape as `MaskOverlayControls`."""

    visibility_changed = pyqtSignal(bool)
    color_changed = pyqtSignal(QColor)
    alpha_changed = pyqtSignal(float)  # 0.0-1.0

    def __init__(self, *, visible: bool, color: QColor, alpha: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)

        self._toggle_button = QToolButton(self)
        self._toggle_button.setCheckable(True)
        self._toggle_button.setChecked(bool(visible))
        self._toggle_button.setAutoRaise(True)
        self._toggle_button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._toggle_button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._toggle_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._toggle_button.setToolTip("Show or hide the histogram highlight overlay.")
        self._toggle_button.toggled.connect(self._on_toggled)

        self._color_button = QToolButton(self)
        self._color_button.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        self._color_button.setToolTip("Choose the histogram-highlight overlay color.")
        self._color_button.clicked.connect(self._on_choose_color)

        self._alpha_slider = CompactWedgeSlider(parent=self)
        self._alpha_slider.setFixedWidth(_SLIDER_WIDTH)
        self._alpha_slider.setRange(0, 100)
        self._alpha_slider.setValue(int(round(float(alpha) * 100.0)))
        self._alpha_slider.setToolTip("Histogram highlight transparency.")
        self._alpha_slider.valueChanged.connect(self._on_alpha_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._toggle_button)
        layout.addWidget(self._color_button)
        layout.addWidget(self._alpha_slider)

        self._refresh_toggle_icon()
        self._refresh_swatch()

    def _on_toggled(self, checked: bool) -> None:
        self._refresh_toggle_icon()
        self.visibility_changed.emit(bool(checked))

    def _on_choose_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "Choose histogram highlight overlay color")
        if not color.isValid():
            return
        self._color = color
        self._refresh_swatch()
        self.color_changed.emit(QColor(color))

    def _on_alpha_changed(self, value: int) -> None:
        self.alpha_changed.emit(float(value) / 100.0)

    def _refresh_toggle_icon(self) -> None:
        visible = self._toggle_button.isChecked()
        self._toggle_button.setIcon(histogram_selection_icon(visible))

    def _refresh_swatch(self) -> None:
        theme = get_active_theme()
        self._color_button.setStyleSheet(
            f"QToolButton {{ background-color: {self._color.name()}; min-width: {_SWATCH_SIZE}px; "
            f"max-width: {_SWATCH_SIZE}px; min-height: {_SWATCH_SIZE}px; max-height: {_SWATCH_SIZE}px; "
            f"border: 1px solid {theme.control_border}; border-radius: 3px; padding: 0; }}"
        )

    def refresh_theme(self, _theme: GuiTheme) -> None:
        """Called by `panel.py` on every live theme switch - re-renders the
        toggle icon and swatch border against the new theme's colors."""
        self._toggle_button.setStyleSheet(transparent_icon_button_stylesheet())
        self._refresh_toggle_icon()
        self._refresh_swatch()
