"""``SliderRibbon`` - the small tool ribbon above the Cube / Wavelength sliders
(2026-10-13, maintainer request; also called the dataset preview ribbon).

Left to right: the frame icon controls (`FrameToolsWidget`, inline: unit,
Reference, Ignore, Dark background), Play-Pause, the mode menu (what the show steps through: cubes,
wavelengths, ROI changes, Mask changes, scan pointers; the wheel at a slider's end steps through the same thing), the
speed as text (1x ... 20x; both in `slideshow_ribbon_controls.py`), a settings pop-out for the rest of the slideshow
(end, skip, faster, repeat), the "ROIs"
section (show the ROIs row under the Cube slider, only the selected ROIs; the row's
marks are always the theme's blue) and the "Mask" section (show the two Mask rows, under the
Cube and the wavelength slider; their marks are a fixed orange). The colour swatches of
Reference, Ignore and Dark background sit inside their groups (`slider_colors.py`); the rows
under the sliders have no colour control.

Display only: Play/Pause, the ROI and Mask toggles and every settings change leave as a
signal; the panel hands them to `SlideshowController` (`slideshow.py`) or the rows
(`timeline_strip.py`, `mask_rows.py`), which do the work.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ..ribbon_drawer import RibbonDrawer
from .canvas_tools import _BUTTON_SIZE, _ICON_SIZE
from .frame_tools import FrameToolsWidget, captioned_group
from .mask_rows import MASK_COLOR
from .slideshow import SlideshowSettings
from .slideshow_ribbon_controls import SlideshowModeButton, SlideshowSpeedButton
from .slider_colors import SliderColorControls
from .slideshow_settings_panel import SlideshowSettingsPanel


class SliderRibbon(QWidget):
    play_toggled = pyqtSignal()
    roll_up_requested = pyqtSignal()  # double-click on empty space in the ribbon
    settings_changed = pyqtSignal(object)  # a SlideshowSettings
    mask_rows_toggled = pyqtSignal(bool)  # show / hide the two Mask rows (a click, not a programmatic set)
    roi_row_toggled = pyqtSignal(bool)  # show / hide the ROIs row under the Cube slider (a click, not a programmatic set)
    roi_selected_only_toggled = pyqtSignal(bool)  # the ROI row counts only the selected ROIs

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SlideshowSettings()
        self._playing = False
        self.slider_colors = SliderColorControls(self)
        self.frame_tools = FrameToolsWidget(
            stacked=False,
            parent=self,
            color_swatches={role: self.slider_colors.swatch(role) for role in ("reference", "ignored", "dark")},
        )

        self._play = QToolButton(self)
        self._play.setAutoRaise(True)
        self._play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._play.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._play.clicked.connect(self.play_toggled)

        self._mode_button = SlideshowModeButton(self)
        self._mode_button.mode_chosen.connect(lambda mode: self._choose(mode=mode))
        self._speed_button = SlideshowSpeedButton(self)
        self._speed_button.speed_chosen.connect(lambda speed: self._choose(speed=speed))
        self._mode_button.set_mode(self._settings.mode)
        self._speed_button.set_speed(self._settings.speed)

        self._settings_button = QToolButton(self)
        self._settings_button.setAutoRaise(True)
        self._settings_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._settings_button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._settings_button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._settings_button.setToolTip("Slideshow settings: where the show ends, what to skip, faster drawing, repeat.")
        self._menu = QMenu(self._settings_button)
        self._settings_panel = SlideshowSettingsPanel(self._menu)
        holder = QWidgetAction(self._menu)
        holder.setDefaultWidget(self._settings_panel)
        self._menu.addAction(holder)
        self._settings_button.setMenu(self._menu)
        self._settings_panel.changed.connect(self._on_panel_changed)

        self.roi_row_button = self._make_toggle(
            "Show the ROIs row under the Cube slider: a mark wherever the ROI layout changes."
        )
        self.mask_rows_button = self._make_toggle(
            "Show the Mask rows under the Cube and the wavelength slider: a mark wherever a mask was drawn "
            "(> persistent, a line individual, a filled > both)."
        )
        self.mask_rows_button.clicked.connect(self.mask_rows_toggled)
        self.mask_rows_button.clicked.connect(self._refresh_roi_icons)
        self.roi_selected_button = self._make_toggle(
            "Only selected ROIs: the ROI row marks (and counts) only the changes of the ROIs selected now."
        )
        self.roi_row_button.clicked.connect(self.roi_row_toggled)
        self.roi_selected_button.clicked.connect(self.roi_selected_only_toggled)
        for button in (self.roi_row_button, self.roi_selected_button):
            button.clicked.connect(self._refresh_roi_icons)  # the icon colour shows the on / off state

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.frame_tools)
        self.frame_tools.add_separator(layout)
        layout.addWidget(captioned_group(self, "Slideshow", (self._play, self._mode_button, self._speed_button, self._settings_button)))
        self.frame_tools.add_separator(layout)
        layout.addWidget(
            captioned_group(self, "ROIs", (self.roi_row_button, self.roi_selected_button))
        )
        self.frame_tools.add_separator(layout)
        layout.addWidget(captioned_group(self, "Mask", (self.mask_rows_button,)))
        layout.addStretch(1)
        self.refresh_theme()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Buttons take their own clicks; a double-click that reaches the ribbon
        itself (or a caption) landed on empty space."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.roll_up_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # -- ROI toggles ----------------------------------------------------------------

    def _make_toggle(self, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        button.setToolTip(tooltip)
        return button

    def set_roi_row(self, shown: bool, selected_only: bool) -> None:
        """Show the two toggles' state without announcing it (restoring what was saved)."""
        for button, checked in ((self.roi_row_button, shown), (self.roi_selected_button, selected_only)):
            button.setChecked(bool(checked))
        self._refresh_roi_icons()

    def set_mask_rows(self, shown: bool) -> None:
        """Show the Mask toggle's state without announcing it (restoring what was saved)."""
        self.mask_rows_button.setChecked(bool(shown))
        self._refresh_roi_icons()

    def _refresh_roi_icons(self) -> None:
        theme = get_active_theme()
        masks = self.mask_rows_button.isChecked()
        self.mask_rows_button.setIcon(
            load_tabler_icon("mask" if masks else "mask-off", color=MASK_COLOR if masks else theme.text_primary, size=_ICON_SIZE * 2)
        )
        shown = self.roi_row_button.isChecked()
        self.roi_row_button.setIcon(
            load_tabler_icon("eye" if shown else "eye-off", color=theme.accent_blue if shown else theme.text_primary, size=_ICON_SIZE * 2)
        )
        selected = self.roi_selected_button.isChecked()
        self.roi_selected_button.setIcon(
            load_tabler_icon("select-rectangle", color=theme.accent_blue if selected else theme.text_primary, size=_ICON_SIZE * 2)
        )

    # -- settings pop-out ---------------------------------------------------------

    def _on_panel_changed(self, settings: SlideshowSettings) -> None:
        self._settings = settings
        self.settings_changed.emit(settings)

    def _choose(self, **fields: object) -> None:
        """A pick on the mode menu or the speed text: change those fields, tell the pop-out (it works on its own copy of the
        settings) and announce the result."""
        new = replace(self._settings, **fields)
        if new != self._settings:
            self.set_settings(new, emit=True)

    def settings(self) -> SlideshowSettings:
        return self._settings

    def set_settings(self, settings: SlideshowSettings, *, emit: bool = False) -> None:
        self._settings = settings
        self._mode_button.set_mode(settings.mode)
        self._speed_button.set_speed(settings.speed)
        self._settings_panel.set_settings(settings)
        if emit:
            self.settings_changed.emit(settings)

    # -- play button --------------------------------------------------------------

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self._refresh_play()

    def set_enabled_for_dataset(self, enabled: bool) -> None:
        self._play.setEnabled(enabled)

    def _refresh_play(self) -> None:
        theme = get_active_theme()
        name = "player-pause" if self._playing else "player-play"
        color = theme.accent_gold if self._playing else theme.accent_green  # pause = yellow, play = green
        self._play.setIcon(load_tabler_icon(name, color=color, size=_ICON_SIZE * 2))
        self._play.setToolTip("Pause the slideshow." if self._playing else "Play: step through the images one by one.")

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        for button in (self._play, self._settings_button):
            button.setStyleSheet(
                transparent_icon_button_stylesheet(hover=theme.control_bg_hover)
                + "QToolButton::menu-indicator { image: none; width: 0px; }"
            )
        for button in (self.roi_row_button, self.roi_selected_button, self.mask_rows_button):
            button.setStyleSheet(transparent_icon_button_stylesheet(hover=theme.control_bg_hover))
        self._refresh_roi_icons()
        self._settings_button.setIcon(load_tabler_icon("settings", color=theme.text_primary, size=_ICON_SIZE * 2))
        self._refresh_play()
        self._mode_button.refresh_theme()
        self._speed_button.refresh_theme()
        self.frame_tools.refresh_theme()
        self.slider_colors.refresh()
        self._settings_panel.set_settings(self._settings)


class SliderRibbonDrawer(RibbonDrawer):
    """The slider ribbon plus a small handle on its left (2026-10-13). The ribbon
    rolls up on a double-click on its empty space; rolled up, only the handle
    (three dots) is left, as a thin strip above the sliders, and a click on it
    rolls the ribbon down again. The rolling is `RibbonDrawer`'s."""

    def __init__(self, ribbon: SliderRibbon, parent: QWidget | None = None) -> None:
        super().__init__(ribbon, "Show the slider ribbon.", parent)
        ribbon.roll_up_requested.connect(lambda: self.set_expanded(False))

    def refresh_theme(self) -> None:
        super().refresh_theme()
        self.content.refresh_theme()
