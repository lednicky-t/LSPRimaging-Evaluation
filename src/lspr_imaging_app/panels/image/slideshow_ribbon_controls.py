"""The two slideshow controls that sit next to Play on the slider ribbon (2026-10-18, maintainer request;
before, both lived in the settings pop-out):

- ``SlideshowModeButton``: what the show steps through, a menu of five icons (no cube drawn in them any more: every mode
  steps through cubes) - a cube, a lambda, the ROI display icon, a mask and a warning triangle (the Mask scan's pointers).
  The button's face is the icon of the chosen mode. Each icon wears the colour of what it steps through
  (`mark_colors.py`: the same colour as the slider handle / row marks).
- ``SlideshowSpeedButton``: the speed as plain text (``1x``, ``2x``, ``5x``, ``10x``). A click goes to the next speed
  and from the fastest back to 1x; the wheel goes faster (up) or slower (down) and stops at both ends.

Display only: a choice leaves as a signal; the ribbon stores it in its ``SlideshowSettings`` and announces it.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QMenu, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from .canvas_tools import _BUTTON_SIZE, _ICON_SIZE
from .mark_colors import EVENT_COLOR, MASK_COLOR, cube_color, roi_color, wavelength_color
from .roi_icons import display_icon
from .roi_overlay_controls import SAMPLE_ON_COLOR
from .slideshow import (
    MODE_ALL,
    MODE_EVENTS,
    MODE_MASK_STATES,
    MODE_ONE_WAVELENGTH,
    MODE_ROI_STATES,
    SPEEDS,
)

_RENDER = _ICON_SIZE * 2
_WHEEL_NOTCH = 120  # angleDelta units of one wheel notch

# (mode, menu text, what it does) in the menu's order.
MODES = (
    (MODE_ONE_WAVELENGTH, "Cubes", "Step through the cubes on the wavelength shown now."),
    (MODE_ALL, "Wavelengths", "Step through every wavelength of a cube, then the next cube."),
    (
        MODE_ROI_STATES,
        "ROI layout changes",
        "Step through the ROI row's marks (where the ROI layout changes) on the wavelength shown now.",
    ),
    (
        MODE_MASK_STATES,
        "Mask changes",
        "Step through the cubes with a mark on the Mask row (a mask of their own) on the wavelength shown now.",
    ),
    (
        MODE_EVENTS,
        "Scan pointers",
        "Step through the cubes with a pointer of the Mask scan (something dark that crossed the field) on the wavelength shown now.",
    ),
)
_TEXT = {mode: (label, description) for mode, label, description in MODES}


def mode_icon(mode: str) -> QIcon:
    """The icon of a slideshow mode, in the colour of what it steps through."""
    if mode == MODE_ROI_STATES:  # the ROI display menu's icon: a pink circle in a ring, the ring in the ROI row's colour
        return display_icon(SAMPLE_ON_COLOR, roi_color())
    name, color = {
        MODE_ONE_WAVELENGTH: ("cube", cube_color()),
        MODE_ALL: ("lambda", wavelength_color()),
        MODE_MASK_STATES: ("mask", MASK_COLOR),
        MODE_EVENTS: ("alert-triangle", EVENT_COLOR),
    }[mode]
    return load_tabler_icon(name, color=color, size=_RENDER)


class SlideshowModeButton(QToolButton):
    mode_chosen = pyqtSignal(str)  # a click on a menu entry (not a programmatic set_mode)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._mode = MODE_ALL
        self._menu = QMenu(self)
        self._menu.setToolTipsVisible(True)
        group = QActionGroup(self)
        group.setExclusive(True)
        self._actions: dict[str, QAction] = {}
        for mode, label, description in MODES:
            action = self._menu.addAction(label)
            action.setCheckable(True)
            action.setToolTip(description)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, m=mode: self.mode_chosen.emit(m))
            self._actions[mode] = action
        self.setMenu(self._menu)
        self.set_mode(self._mode)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Show *mode* (does not emit)."""
        if mode not in self._actions:
            return
        self._mode = mode
        self._actions[mode].setChecked(True)
        self.setIcon(mode_icon(mode))
        label, description = _TEXT[mode]
        self.setToolTip(f"Play and the wheel step through: {label}. {description} Click to choose.")

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.setStyleSheet(
            transparent_icon_button_stylesheet(hover=theme.control_bg_hover)
            + "QToolButton::menu-indicator { image: none; width: 0px; }"
        )
        for mode, action in self._actions.items():
            action.setIcon(mode_icon(mode))
        self.set_mode(self._mode)


class SlideshowSpeedButton(QToolButton):
    speed_chosen = pyqtSignal(int)  # a click or a wheel turn (not a programmatic set_speed)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setFixedHeight(_BUTTON_SIZE)
        self.setFixedWidth(max(_BUTTON_SIZE, self.fontMetrics().horizontalAdvance("10x") + 14))
        self.setToolTip(
            "Speed. Click: the next speed, from the fastest back to 1x. Scroll: faster (up) or slower (down), "
            "it stops at both ends. 1x is the average time between two wavelengths in the experiment data, or 0.5 s without it."
        )
        self._speed = SPEEDS[0]
        self._wheel_remainder = 0
        self.clicked.connect(self._cycle)
        self.set_speed(self._speed)

    def speed(self) -> int:
        return self._speed

    def set_speed(self, speed: int) -> None:
        """Show *speed* (does not emit)."""
        self._speed = speed
        self.setText(f"{speed}x")

    def _index(self) -> int:
        return SPEEDS.index(self._speed) if self._speed in SPEEDS else 0

    def _choose(self, index: int) -> None:
        speed = SPEEDS[index]
        if speed != self._speed:
            self.set_speed(speed)
            self.speed_chosen.emit(speed)

    def _cycle(self) -> None:
        self._choose((self._index() + 1) % len(SPEEDS))

    def wheelEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 - Qt naming
        self._wheel_remainder += event.angleDelta().y()
        notches = int(self._wheel_remainder / _WHEEL_NOTCH)  # towards zero: 119 units is not a notch yet
        if notches:
            self._wheel_remainder -= notches * _WHEEL_NOTCH
            self._choose(min(max(self._index() + notches, 0), len(SPEEDS) - 1))  # stops at both ends
        event.accept()

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.setStyleSheet(
            transparent_icon_button_stylesheet(hover=theme.control_bg_hover)
            + f"QToolButton {{ color: {theme.text_primary}; }}"
        )
