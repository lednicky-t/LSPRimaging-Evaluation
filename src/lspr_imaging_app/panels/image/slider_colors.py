"""Slider colours (2026-10-15, maintainer request): the colour of each kind of mark on
the Cube / Wavelength sliders - Dark background, Reference frame, Ignored, Mask, ROI -
and the swatches where the user changes them.

``SliderColors`` is the state (one optional override per role; ``None`` = the theme's
colour, so a theme switch still recolours what was never changed). A ``ColorSwatch`` is
the display: one coloured button; a click opens a popup with the hue-only slider the
Histogram's curve popup uses (`panels/hue_slider.py`) and a Reset button. A swatch holds
no state of its own: a pick leaves as a signal, the panel stores it and pushes the result
back through `refresh()`. ``SliderColorControls`` makes the five swatches and is what the
panel talks to; the ribbon puts each swatch next to the icons it belongs to (Reference,
Ignore, Dark background), and Mask and ROI in a "Slider colors" section.

Dark, Reference and Ignored colour ticks on the sliders today. Mask and ROI are stored and
remembered already, but nothing draws with them yet: their marks come with the
timeline rows planned under the sliders.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, transparent_icon_button_stylesheet

from ..hue_slider import color_for_hue, hue_of, make_hue_slider
from .canvas_tools import _BUTTON_SIZE

ROLES = ("dark", "reference", "ignored", "mask", "roi")


def default_color(role: str) -> str:
    """The theme colour a role has until the user picks one."""
    theme = get_active_theme()
    return {
        "dark": theme.accent_purple,
        "reference": theme.accent_green,
        "ignored": theme.accent_red,
        "mask": theme.accent_red,
        "roi": theme.accent_blue,
    }[role]


class SliderColors:
    """The colour per role: the user's pick, else the theme's."""

    def __init__(self) -> None:
        self._overrides: dict[str, str] = {}

    def color(self, role: str) -> str:
        return self._overrides.get(role) or default_color(role)

    def override(self, role: str) -> str | None:
        """The user's pick, or ``None`` while the theme's colour is in use."""
        return self._overrides.get(role)

    def set(self, role: str, color: str | None) -> None:
        """Pick `color` for `role`; ``None`` (or an invalid colour) goes back to the theme's."""
        if role not in ROLES:
            raise ValueError(f"unknown slider colour role {role!r}")
        if color is not None and QColor(color).isValid():
            self._overrides[role] = QColor(color).name()
        else:
            self._overrides.pop(role, None)


_ROLE_TEXT = {
    "dark": ("Dark background", "the dark background tick (wavelength slider)"),
    "reference": ("Reference", "the reference frame tick (both sliders)"),
    "ignored": ("Ignored", "the ignored cube / wavelength ticks"),
    "mask": ("Mask", "mask marks"),
    "roi": ("ROI", "ROI-change marks"),
}
_SWATCH_SIZE = 16
_swatch_cache: dict[tuple[str, str], QIcon] = {}


def _swatch_icon(color: str, border: str) -> QIcon:
    key = (color, border)
    icon = _swatch_cache.get(key)
    if icon is None:
        pixmap = QPixmap(_SWATCH_SIZE * 2, _SWATCH_SIZE * 2)  # drawn at 2x, so it stays sharp on a scaled screen
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(border), 2.0))
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(2, 2, _SWATCH_SIZE * 2 - 4, _SWATCH_SIZE * 2 - 4), 6, 6)
        painter.end()
        icon = _swatch_cache[key] = QIcon(pixmap)
    return icon


class _HuePopup(QWidget):
    """The popup's content: a hue slider and Reset. `color_picked(None)` = back to the default."""

    color_picked = pyqtSignal(object)  # "#rrggbb" or None

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hue_slider = make_hue_slider(self, default_color(role))
        self.hue_slider.valueChanged.connect(lambda hue: self.color_picked.emit(color_for_hue(hue)))
        self.reset_button = QToolButton(self)
        self.reset_button.setText("Reset")
        self.reset_button.setToolTip("Back to the default colour")
        self.reset_button.clicked.connect(lambda: self.color_picked.emit(None))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self.hue_slider)
        layout.addWidget(self.reset_button)

    def show_color(self, color: str) -> None:
        """Put the slider on `color`'s hue without reporting it as a pick."""
        self.hue_slider.blockSignals(True)
        self.hue_slider.setValue(hue_of(color))
        self.hue_slider.blockSignals(False)


class ColorSwatch(QToolButton):
    """One coloured swatch; a click opens the hue popup. Reports a pick, keeps no state."""

    color_changed = pyqtSignal(str, object)  # role, "#rrggbb" or None (back to the default)

    def __init__(self, role: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.role = role
        self._colors: SliderColors | None = None
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setIconSize(QSize(_SWATCH_SIZE, _SWATCH_SIZE))
        self.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        self.popup = _HuePopup(role, menu)
        self.popup.color_picked.connect(lambda color: self.color_changed.emit(self.role, color))
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(self.popup)
        menu.addAction(holder)
        menu.aboutToShow.connect(lambda: self.popup.show_color(self._color()))
        self.setMenu(menu)

    def set_colors(self, colors: SliderColors) -> None:
        self._colors = colors
        self.refresh()

    def _color(self) -> str:
        return self._colors.color(self.role) if self._colors is not None else default_color(self.role)

    def refresh(self) -> None:
        """Show the current colour (and the theme's border / hover)."""
        theme = get_active_theme()
        self.setStyleSheet(
            transparent_icon_button_stylesheet(hover=theme.control_bg_hover)
            + "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        self.setIcon(_swatch_icon(self._color(), theme.text_muted))
        title, what = _ROLE_TEXT[self.role]
        self.setToolTip(f"{title}: the colour of {what}. Click to change it.")


class SliderColorControls(QObject):
    """The five swatches, made together (parented to `parent`; the ribbon places each
    where it belongs) and spoken to as one: `color_changed`, `set_colors`, `refresh`."""

    color_changed = pyqtSignal(str, object)  # role, "#rrggbb" or None (back to the default)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._swatches = {role: ColorSwatch(role, parent) for role in ROLES}
        for swatch in self._swatches.values():
            swatch.color_changed.connect(self.color_changed)

    def swatch(self, role: str) -> ColorSwatch:
        return self._swatches[role]

    def button(self, role: str) -> QToolButton:
        return self._swatches[role]

    def popup(self, role: str) -> _HuePopup:
        return self._swatches[role].popup

    def set_colors(self, colors: SliderColors) -> None:
        for swatch in self._swatches.values():
            swatch.set_colors(colors)

    def refresh(self) -> None:
        for swatch in self._swatches.values():
            swatch.refresh()
