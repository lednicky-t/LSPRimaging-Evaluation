"""``SliderRibbon`` - the small tool ribbon above the Cube / Wavelength sliders
(2026-10-13, maintainer request; also called the dataset preview ribbon).

Left to right: the frame icon controls (`FrameToolsWidget`, inline: unit,
Reference, Ignore, Dark background), Play / Pause, and a settings pop-out for
the slideshow (speed, what to step through, repeat, where the end is), and the
"Slider colors" section (Mask, ROI). The colour swatches of Reference, Ignore and
Dark background sit inside those groups (`slider_colors.py`).

Display only: Play/Pause and every settings change leave as a signal; the panel
hands them to `SlideshowController` (`slideshow.py`), which does the work.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from .canvas_tools import _BUTTON_SIZE, _ICON_SIZE
from .frame_tools import FrameToolsWidget, captioned_group
from .ribbon_drawer import RibbonDrawer
from .slideshow import SlideshowSettings
from .slider_colors import SliderColorControls
from .slideshow_settings_panel import SlideshowSettingsPanel


class SliderRibbon(QWidget):
    play_toggled = pyqtSignal()
    roll_up_requested = pyqtSignal()  # double-click on empty space in the ribbon
    settings_changed = pyqtSignal(object)  # a SlideshowSettings

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

        self._settings_button = QToolButton(self)
        self._settings_button.setAutoRaise(True)
        self._settings_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._settings_button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self._settings_button.setFixedSize(_BUTTON_SIZE, _BUTTON_SIZE)
        self._settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._settings_button.setToolTip("Slideshow settings: speed, what to step through, repeat.")
        self._menu = QMenu(self._settings_button)
        self._settings_panel = SlideshowSettingsPanel(self._menu)
        holder = QWidgetAction(self._menu)
        holder.setDefaultWidget(self._settings_panel)
        self._menu.addAction(holder)
        self._settings_button.setMenu(self._menu)
        self._settings_panel.changed.connect(self._on_panel_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.frame_tools)
        layout.addWidget(self.frame_tools._separator())
        layout.addWidget(captioned_group(self, "Slideshow", (self._play, self._settings_button)))
        layout.addWidget(self.frame_tools._separator())
        layout.addWidget(
            captioned_group(self, "Slider colors", (self.slider_colors.swatch("mask"), self.slider_colors.swatch("roi")))
        )
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

    # -- settings pop-out ---------------------------------------------------------

    def _on_panel_changed(self, settings: SlideshowSettings) -> None:
        self._settings = settings
        self.settings_changed.emit(settings)

    def settings(self) -> SlideshowSettings:
        return self._settings

    def set_settings(self, settings: SlideshowSettings, *, emit: bool = False) -> None:
        self._settings = settings
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
        self._settings_button.setIcon(load_tabler_icon("settings", color=theme.text_primary, size=_ICON_SIZE * 2))
        self._refresh_play()
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
        self.ribbon.refresh_theme()
