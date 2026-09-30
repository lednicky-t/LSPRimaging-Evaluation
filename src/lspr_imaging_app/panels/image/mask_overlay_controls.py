"""Image Tools ribbon - mask overlay display controls (2026-09-30): a
show/hide toggle, a color swatch, and a transparency slider, ported from
the stable app's mask-overlay controls (`gui/main_window.py`'s
`show_mask_check`/`mask_color_button`/`mask_alpha_slider`, wired in
`gui/overlay_manager.py`'s `_update_ignore_mask_overlay`).

This is display-only cosmetic state, not a `MaskModule` command.
`MaskModule` owns the ignore mask itself - which pixels are excluded,
computational, affects analysis results (see that module's docstring).
Whether/how that mask is tinted on screen is a different kind of thing, the
same cosmetic/computational split the rest of this rewrite already draws
(e.g. `GeometryCosmeticChange` vs `GeometryComputationalChange`) - nothing
here calls into `MaskModule` at all. `ImagePanel` is this widget's only
listener; it owns the visible/color/alpha state itself and does the actual
drawing (`panel.py`'s `_update_mask_overlay`), matching that panel's own
"owns no state that another module owns" rule - this state belongs to no
other module, so the panel is its rightful owner.

Uses `lspr_ui.CompactWedgeSlider` (the shared copy, not the stable app's
own local `gui/widgets.py` one - the rewrite tree never imports from the
old `gui` package, see AGENTS.md's module-boundary rule) for the
transparency control, and the vendored `mask`/`mask-off` Tabler icons for
the toggle, matching the stable app's own icon choice
(`MainWindowIcons._make_view_toggle_icon`, kind="mask")."""

from __future__ import annotations

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QHBoxLayout, QToolButton, QWidget

from lspr_ui import CompactWedgeSlider, GuiTheme, get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

_BUTTON_SIZE = 28  # matches TransformsSection's own icon buttons, same row
_ICON_SIZE = 22
_RENDER_SIZE = _ICON_SIZE * 2  # rendered at 2x, scaled down - crisper than a native 22px bitmap
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#22c55e"  # the stable app's literal for "overlay visible"
_SWATCH_SIZE = 14
_SLIDER_WIDTH = 28


class MaskOverlayControls(QWidget):
    """Show/hide toggle + color swatch + transparency slider for the mask
    overlay tint. Emits on every user change; carries no state of its own
    beyond what it needs to repaint itself (the swatch color, the toggle's
    checked state) - `ImagePanel` is the source of truth."""

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
        self._toggle_button.setToolTip("Show or hide the mask overlay.")
        self._toggle_button.toggled.connect(self._on_toggled)

        self._color_button = QToolButton(self)
        self._color_button.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        self._color_button.setToolTip("Choose the mask-overlay color.")
        self._color_button.clicked.connect(self._on_choose_color)

        self._alpha_slider = CompactWedgeSlider(parent=self)
        self._alpha_slider.setFixedWidth(_SLIDER_WIDTH)
        self._alpha_slider.setRange(0, 100)
        self._alpha_slider.setValue(int(round(float(alpha) * 100.0)))
        self._alpha_slider.setToolTip("Mask overlay transparency.")
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
        color = QColorDialog.getColor(self._color, self, "Choose mask overlay color")
        if not color.isValid():
            return
        self._color = color
        self._refresh_swatch()
        self.color_changed.emit(QColor(color))

    def _on_alpha_changed(self, value: int) -> None:
        self.alpha_changed.emit(float(value) / 100.0)

    def _refresh_toggle_icon(self) -> None:
        theme = get_active_theme()
        visible = self._toggle_button.isChecked()
        color = _ACTIVE_COLOR if visible else theme.text_dim
        icon_name = "mask" if visible else "mask-off"
        self._toggle_button.setIcon(load_tabler_icon(icon_name, color=color, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))

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
