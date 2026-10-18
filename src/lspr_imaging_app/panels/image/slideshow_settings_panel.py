"""``SlideshowSettingsPanel`` - the content of the slider ribbon's settings
pop-out (2026-10-14, maintainer request): icons and one slider instead of a
text menu.

Speed and the mode (what the show steps through) used to be here; since 2026-10-18 they sit next to Play on the ribbon
(`slideshow_ribbon_controls.py`). The Prev / Next buttons were removed the same day. This pop-out still shows the current
mode, because the End row only applies to the wavelengths mode.

- **End**: two exclusive icons, a cube (the show stays in its cube) and a stack
  (the whole dataset). Greyed out unless the mode is "every wavelength", where the end is
  always the last cube.
- **Skip**: two toggles, ignored frames (eye-off) and the dark background frame (moon).
- **Repeat**: a repeat-arrows toggle.

An active icon takes the accent colour, like the Ignore / Dark icons in
`frame_tools.py`. Display only: every change leaves as ``changed(SlideshowSettings)``.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from .canvas_tools import _BUTTON_SIZE, _ICON_SIZE
from .slideshow import END_CUBE, END_DATASET, MODE_ALL, SlideshowSettings

_RENDER = _ICON_SIZE * 2


class SlideshowSettingsPanel(QWidget):
    changed = pyqtSignal(object)  # a SlideshowSettings

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SlideshowSettings()

        self._end_cube =self._button(True, "End of the show = the last frame of the cube it is in.")
        self._end_dataset = self._button(True, "End of the show = the last frame of the dataset.")
        self._repeat = self._button(True, "Repeat: start again at the beginning instead of stopping.")
        self._skip_ignored = self._button(True, "Skip ignored frames: leave them out of the show.")
        self._skip_dark = self._button(True, "Skip the dark background frame (LED off): leave it out of the show.")
        self._calm_histogram = self._button(
            True,
            "Calm histogram: update the Histogram at most once a second while playing "
            "(each update costs ~30 ms of the app's GUI time); it catches up when Play stops.",
        )
        self._thin_outline = self._button(
            True,
            "Fast ROI outlines: draw the ROI circles 1.0 px thin instead of 1.5 px while playing "
            "(about 5x faster to paint; the normal width returns when Play stops).",
        )
        end_group = QButtonGroup(self)
        end_group.setExclusive(True)
        for button in (self._end_cube, self._end_dataset):
            end_group.addButton(button)
        self._end_cube.clicked.connect(lambda: self._change(end=END_CUBE))
        self._end_dataset.clicked.connect(lambda: self._change(end=END_DATASET))
        self._repeat.clicked.connect(lambda checked: self._change(repeat=bool(checked)))
        self._skip_ignored.clicked.connect(lambda checked: self._change(skip_ignored=bool(checked)))
        self._skip_dark.clicked.connect(lambda checked: self._change(skip_dark=bool(checked)))
        self._thin_outline.clicked.connect(lambda checked: self._change(thin_roi_outline=bool(checked)))
        self._calm_histogram.clicked.connect(lambda checked: self._change(calm_histogram=bool(checked)))

        grid = QGridLayout(self)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        for row, (title, buttons) in enumerate(
            (
                ("End", (self._end_cube, self._end_dataset)),
                ("Skip", (self._skip_ignored, self._skip_dark)),
                ("Faster", (self._thin_outline, self._calm_histogram)),
                ("Repeat", (self._repeat,)),
            )
        ):
            grid.addWidget(QLabel(title, self), row, 0)
            holder = QHBoxLayout()
            holder.setContentsMargins(0, 0, 0, 0)
            holder.setSpacing(2)
            for button in buttons:
                holder.addWidget(button)
            holder.addStretch(1)
            grid.addLayout(holder, row, 1)
        self.set_settings(self._settings)

    def _button(self, checkable: bool, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(checkable)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setFixedHeight(_BUTTON_SIZE)
        button.setMinimumWidth(_BUTTON_SIZE)
        button.setToolTip(tooltip)
        return button

    def _all_widgets(self) -> tuple[QWidget, ...]:
        return (
            self._end_cube, self._end_dataset,
            self._repeat, self._skip_ignored, self._skip_dark, self._thin_outline, self._calm_histogram,
        )

    def settings(self) -> SlideshowSettings:
        return self._settings

    def _change(self, **fields: object) -> None:
        new = replace(self._settings, **fields)
        if new != self._settings:
            self.set_settings(new)
            self.changed.emit(new)

    def set_settings(self, settings: SlideshowSettings) -> None:
        """Show *settings* (does not emit)."""
        self._settings = settings
        theme = get_active_theme()
        plain, accent = theme.text_primary, theme.accent_blue
        for widget in self._all_widgets():
            widget.blockSignals(True)
        every = settings.mode == MODE_ALL
        self._end_cube.setChecked(settings.end == END_CUBE)
        self._end_dataset.setChecked(settings.end == END_DATASET)
        self._repeat.setChecked(settings.repeat)
        self._skip_ignored.setChecked(settings.skip_ignored)
        self._skip_dark.setChecked(settings.skip_dark)
        self._thin_outline.setChecked(settings.thin_roi_outline)
        self._calm_histogram.setChecked(settings.calm_histogram)
        for widget in self._all_widgets():
            widget.blockSignals(False)

        def colour(active: bool) -> str:
            return accent if active else plain

        end_applies = every
        self._end_cube.setIcon(load_tabler_icon("cube", color=colour(settings.end == END_CUBE and end_applies), size=_RENDER))
        self._end_dataset.setIcon(load_tabler_icon("stack-3", color=colour(settings.end == END_DATASET and end_applies), size=_RENDER))
        for button in (self._end_cube, self._end_dataset):
            button.setEnabled(end_applies)
        self._end_cube.setToolTip(
            "End of the show = the last frame of the cube it is in."
            if end_applies else "Only in the wavelengths mode (the λ next to Play): the other modes end at the last cube (or the last mark)."
        )
        self._end_dataset.setToolTip(
            "End of the show = the last frame of the dataset."
            if end_applies else "Only in the wavelengths mode (the λ next to Play): the other modes end at the last cube (or the last mark)."
        )
        self._repeat.setIcon(load_tabler_icon("repeat", color=colour(settings.repeat), size=_RENDER))
        self._skip_ignored.setIcon(load_tabler_icon("eye-off", color=theme.accent_red if settings.skip_ignored else plain, size=_RENDER))
        self._skip_dark.setIcon(load_tabler_icon("moon", color=theme.accent_gold if settings.skip_dark else plain, size=_RENDER))
        self._thin_outline.setIcon(load_tabler_icon("circle", color=theme.accent_blue if settings.thin_roi_outline else plain, size=_RENDER))
        self._calm_histogram.setIcon(
            load_tabler_icon("chart-histogram", color=theme.accent_blue if settings.calm_histogram else plain, size=_RENDER)
        )
        for button in (
            self._end_cube, self._end_dataset,
            self._repeat, self._skip_ignored, self._skip_dark, self._thin_outline, self._calm_histogram,
        ):
            button.setStyleSheet(transparent_icon_button_stylesheet(hover=theme.control_bg_hover))
