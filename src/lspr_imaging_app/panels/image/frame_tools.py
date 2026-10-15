"""``FrameToolsWidget`` - the icon controls for "what is this frame?" (2026-10-13,
maintainer request): the cube unit (# / time), Reference (set, find), Ignore
(cube, wavelength, clear) and Dark background (pick, clear).

One widget, two hosts, so the two can never drift apart:

- **stacked** (``stacked=True``): a titled row per group, shown inside the
  slider right-click menu (`panel.py` ``_show_slider_menu``);
- **inline** (``stacked=False``): one row of icon groups with thin separators and
  no titles, shown in the slider ribbon above the sliders.

Display only (CLAUDE.md "panels display, modules own state"): a click becomes a
signal, the panel turns it into a command on `FrameRolesModule`,
`ReferenceFrameModule` or `CubeDisplayModule`, and pushes the new look back
through `refresh(FrameToolsState)`. The buttons use ``clicked``, not
``toggled``, so a programmatic `refresh` never re-emits a command.

Every button explains itself in its tooltip; the tooltips that name a value
("Ignore 470 nm in all cubes") are rebuilt from the state on each `refresh`.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ..ribbon_group import group_label_style
from .canvas_tools import _BUTTON_SIZE, _ICON_SIZE

def captioned_group(parent: QWidget, caption: str, buttons: tuple[QWidget, ...]) -> QWidget:
    """A row of icon buttons with a small muted caption on top (ribbon sections)."""
    group = QWidget(parent)
    column = QVBoxLayout(group)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(1)
    label = QLabel(caption, group)
    label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    label.setStyleSheet(group_label_style())
    column.addWidget(label)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    for button in buttons:
        row.addWidget(button)
    column.addLayout(row)
    return group


_icon_cache: dict[tuple[str, str, int], QIcon] = {}


def _cached_icon(name: str, color: str, size: int) -> QIcon:
    """Rendering an SVG icon costs ~0.5 ms; the ribbon refreshes on every wavelength step while
    the slideshow plays (measured 2026-10-14: this was 90 % of a step), so each look is built once."""
    key = (name, color, size)
    icon = _icon_cache.get(key)
    if icon is None:
        icon = _icon_cache[key] = load_tabler_icon(name, color=color, size=size)
    return icon


_TIME_NEEDS_METADATA = "Needs acquisition metadata with per-image timing."


@dataclass(frozen=True)
class FrameToolsState:
    """Everything the icons need to show, read from the modules by the panel."""

    time_mode: bool = False  # cubes are currently labelled by start time
    time_available: bool = False
    wavelength_nm: float | None = None  # the wavelength shown now
    cube_label: str = ""  # the cube shown now, in the current unit
    cube_ignored: bool = False
    wavelength_ignored: bool = False
    dark_is_this_wavelength: bool = False
    has_dark: bool = False
    any_ignored_anywhere: bool = False  # some cube or wavelength is ignored, not necessarily the one shown
    enabled: bool = True  # False with no dataset loaded


class FrameToolsWidget(QWidget):
    unit_toggle_requested = pyqtSignal()
    set_reference_requested = pyqtSignal()
    find_reference_requested = pyqtSignal()
    ignore_cube_requested = pyqtSignal(bool)  # True = ignore, False = stop ignoring
    ignore_wavelength_requested = pyqtSignal(bool)
    clear_ignores_requested = pyqtSignal()
    clear_all_ignores_requested = pyqtSignal()
    pick_dark_requested = pyqtSignal(bool)  # True = make this wavelength the dark, False = clear it
    clear_dark_requested = pyqtSignal()
    used = pyqtSignal()  # after any click; the menu host closes itself on it

    def __init__(
        self, *, stacked: bool, parent: QWidget | None = None, color_swatches: dict[str, QWidget] | None = None
    ) -> None:
        """`color_swatches` ("reference", "ignored", "dark" -> a colour swatch, `slider_colors.py`)
        are shown at the end of their group in the inline (ribbon) layout; the stacked (menu) one has none."""
        super().__init__(parent)
        self._stacked = stacked
        self._state = FrameToolsState(enabled=False)
        self._last_shown: tuple[FrameToolsState, int] | None = None
        self._styled_theme = None
        self._icon_keys: dict[QToolButton, tuple[str, str]] = {}

        self._unit = self._button(checkable=False, with_text=True)
        self._set_ref = self._button()
        self._find_ref = self._button()
        self._ignore_cube = self._button(checkable=True)
        self._ignore_wl = self._button(checkable=True)
        self._ignore_clear = self._button()
        self._ignore_clear_all = self._button()
        self._dark_pick = self._button(checkable=True)
        self._dark_clear = self._button()

        self._unit.clicked.connect(self.unit_toggle_requested)
        self._set_ref.clicked.connect(self.set_reference_requested)
        self._find_ref.clicked.connect(self.find_reference_requested)
        self._ignore_cube.clicked.connect(self.ignore_cube_requested)
        self._ignore_wl.clicked.connect(self.ignore_wavelength_requested)
        self._ignore_clear.clicked.connect(self.clear_ignores_requested)
        self._ignore_clear_all.clicked.connect(self.clear_all_ignores_requested)
        self._dark_pick.clicked.connect(self.pick_dark_requested)
        self._dark_clear.clicked.connect(self.clear_dark_requested)
        for button in self._buttons():
            button.clicked.connect(self.used)

        reference = (self._set_ref, self._find_ref)
        ignore = (self._ignore_cube, self._ignore_wl, self._ignore_clear, self._ignore_clear_all)
        dark = (self._dark_pick, self._dark_clear)
        if stacked:
            grid = QGridLayout(self)
            grid.setContentsMargins(8, 6, 8, 6)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            grid.addWidget(self._unit, 0, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
            for row, (title, group) in enumerate((("Reference", reference), ("Ignore", ignore), ("Dark background", dark)), start=1):
                grid.addWidget(QLabel(title, self), row, 0)
                holder = QHBoxLayout()
                holder.setContentsMargins(0, 0, 0, 0)
                holder.setSpacing(2)
                for button in group:
                    holder.addWidget(button)
                holder.addStretch(1)
                grid.addLayout(holder, row, 1)
        else:
            row_layout = QHBoxLayout(self)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)
            self._unit.hide()  # the ribbon has no unit toggle (it stays in the right-click menu)
            swatches = color_swatches or {}
            for index, (caption, group, role) in enumerate(
                (("Reference", reference, "reference"), ("Ignore", ignore, "ignored"), ("Dark", dark, "dark"))
            ):
                if index:
                    row_layout.addWidget(self._separator())
                swatch = swatches.get(role)
                row_layout.addWidget(captioned_group(self, caption, group + ((swatch,) if swatch is not None else ())))
        self.refresh(self._state)

    # -- building ---------------------------------------------------------------

    def _button(self, *, checkable: bool = False, with_text: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setAutoRaise(True)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setFixedHeight(_BUTTON_SIZE)
        if with_text:
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setMinimumWidth(_BUTTON_SIZE)
        else:
            button.setFixedWidth(_BUTTON_SIZE)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedHeight(_BUTTON_SIZE - 6)
        line.setStyleSheet(f"color: {get_active_theme().toolbar_border};")
        return line

    def _buttons(self) -> tuple[QToolButton, ...]:
        return (
            self._unit,
            self._set_ref,
            self._find_ref,
            self._ignore_cube,
            self._ignore_wl,
            self._ignore_clear,
            self._ignore_clear_all,
            self._dark_pick,
            self._dark_clear,
        )

    # -- state --------------------------------------------------------------------

    def refresh_theme(self) -> None:
        self._styled_theme = None  # restyle, then redraw
        self._last_shown = None
        self.refresh(self._state)

    def _icon(self, button: QToolButton, name: str, color: str) -> None:
        key = (name, color)
        if self._icon_keys.get(button) != key:
            self._icon_keys[button] = key
            button.setIcon(_cached_icon(name, color, _ICON_SIZE * 2))

    @staticmethod
    def _tip(button: QToolButton, text: str) -> None:
        if button.toolTip() != text:
            button.setToolTip(text)

    def refresh(self, state: FrameToolsState) -> None:
        """Show *state*: checked buttons take an accent colour (red = ignored,
        gold = dark, the same colours as the "Dark" / "Ignored" frame prefix).

        Runs on every wavelength step during Play, so it only touches what changed:
        nothing at all for an identical state, stylesheets only on a theme change,
        icons from a cache, tooltips and enabled flags only when different."""
        self._state = state
        theme = get_active_theme()
        if self._last_shown == (state, id(theme)):
            return
        self._last_shown = (state, id(theme))
        plain = theme.text_primary
        if self._styled_theme is not theme:
            self._styled_theme = theme
            for button in self._buttons():
                button.setStyleSheet(transparent_icon_button_stylesheet(hover=theme.control_bg_hover))
        for button in self._buttons():
            if button.isEnabled() != state.enabled:
                button.setEnabled(state.enabled)

        wl = "" if state.wavelength_nm is None else f"{state.wavelength_nm:g} nm"
        # The unit button shows what a click switches *to*: "(t)" while numbers are shown.
        self._icon(self._unit, "cube", plain)
        self._unit.setText("(#)" if state.time_mode else "(t)")
        self._unit.setEnabled(state.enabled and state.time_available)
        self._tip(self._unit,
            ("Cubes are shown by start time. Click to show cube numbers (#)."
             if state.time_mode else
             "Cubes are shown by number. Click to show start time (t).")
            + ("" if state.time_available else f"\n{_TIME_NEEDS_METADATA}")
        )

        self._icon(self._set_ref, "pin", plain)
        self._tip(self._set_ref, "Set reference: use the cube and wavelength shown now as the reference frame.")
        self._icon(self._find_ref, "search", plain)
        self._tip(self._find_ref, "Find reference: score every wavelength of the cube shown now and keep the best-contrast one as the reference frame (runs only when clicked).")

        for button, icon, active, tip_off, tip_on in (
            (
                self._ignore_cube,
                "cube",
                state.cube_ignored,
                f"Ignore cube {state.cube_label}: leave it out of fits and analysis (the data is kept).",
                f"Cube {state.cube_label} is ignored. Click to stop ignoring it.",
            ),
            (
                self._ignore_wl,
                "lambda",
                state.wavelength_ignored,
                f"Ignore {wl} in all cubes: leave it out of fits and analysis (the data is kept).",
                f"{wl} is ignored in all cubes. Click to stop ignoring it.",
            ),
        ):
            if button.isChecked() != active:
                button.setChecked(active)
            self._icon(button, icon, theme.accent_red if active else plain)
            self._tip(button, tip_on if active else tip_off)
        any_ignored = state.cube_ignored or state.wavelength_ignored
        self._icon(self._ignore_clear, "eraser", plain)
        self._tip(self._ignore_clear, "Clear ignore: stop ignoring the cube and the wavelength shown now.")
        self._ignore_clear.setEnabled(state.enabled and any_ignored)
        self._icon(self._ignore_clear_all, "clear-all", plain)
        self._tip(self._ignore_clear_all, "Clear all ignored: stop ignoring every cube and every wavelength in the dataset.")
        self._ignore_clear_all.setEnabled(state.enabled and state.any_ignored_anywhere)

        if self._dark_pick.isChecked() != state.dark_is_this_wavelength:
            self._dark_pick.setChecked(state.dark_is_this_wavelength)
        self._icon(self._dark_pick, "color-picker", theme.accent_gold if state.dark_is_this_wavelength else plain)
        self._tip(self._dark_pick,
            f"{wl} is the dark background. Click to clear it."
            if state.dark_is_this_wavelength
            else f"Pick dark background: use {wl} (LED off) as the dark background in every cube."
        )
        self._icon(self._dark_clear, "eraser", plain)
        self._tip(self._dark_clear, "Clear dark background: no wavelength is used as the dark background.")
        self._dark_clear.setEnabled(state.enabled and state.has_dark)
