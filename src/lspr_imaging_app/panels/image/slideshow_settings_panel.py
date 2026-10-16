"""``SlideshowSettingsPanel`` - the content of the slider ribbon's settings
pop-out (2026-10-14, maintainer request): icons and one slider instead of a
text menu.

- **Speed**: a four-tick slider (1x, 2x, 5x, 10x) with a small "Nx" readout.
- **Show**: four exclusive icons, a cube (stay on one wavelength, step through
  the cubes), a cube + lambda (every wavelength of every cube), a cube + `>`
  (stay on one wavelength, step through the ROIs row's marks) and a cube + mask
  (the same over the Mask row's marks; both 2026-10-16). Prev / Next next to Play
  follow the same choice.
- **End**: two exclusive icons, a cube (the show stays in its cube) and a stack
  (the whole dataset). Greyed out in the one-wavelength mode, where the end is
  always the last cube.
- **Skip**: two toggles, ignored frames (eye-off) and the dark background frame (moon).
- **Repeat**: a repeat-arrows toggle.

An active icon takes the accent colour, like the Ignore / Dark icons in
`frame_tools.py`. Display only: every change leaves as ``changed(SlideshowSettings)``.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from .canvas_tools import _BUTTON_SIZE, _ICON_SIZE
from .slideshow import (
    END_CUBE,
    END_DATASET,
    MODE_ALL,
    MODE_MASK_STATES,
    MODE_ONE_WAVELENGTH,
    MODE_ROI_STATES,
    SPEEDS,
    SlideshowSettings,
)

_RENDER = _ICON_SIZE * 2


def _pair_icon(first: str, second: str, color: str) -> QIcon:
    """Two icons side by side."""
    pixmap = QPixmap(_RENDER * 2, _RENDER)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    for slot, name in enumerate((first, second)):
        painter.drawPixmap(slot * _RENDER, 0, load_tabler_icon(name, color=color, size=_RENDER).pixmap(_RENDER, _RENDER))
    painter.end()
    return QIcon(pixmap)


def _cube_lambda_icon(color: str) -> QIcon:
    """A cube and a lambda side by side (every wavelength of each cube)."""
    return _pair_icon("cube", "lambda", color)


class SlideshowSettingsPanel(QWidget):
    changed = pyqtSignal(object)  # a SlideshowSettings

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = SlideshowSettings()

        self._speed = QSlider(Qt.Orientation.Horizontal, self)
        self._speed.setRange(0, len(SPEEDS) - 1)
        self._speed.setPageStep(1)
        self._speed.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._speed.setTickInterval(1)
        self._speed.setFixedWidth(84)
        self._speed.setToolTip("Speed. 1x is the average time between two wavelengths in the experiment data, or 0.5 s without it.")
        self._speed.valueChanged.connect(lambda index: self._change(speed=SPEEDS[index]))
        self._speed_label = QLabel(self)
        self._speed_label.setMinimumWidth(self._speed_label.fontMetrics().horizontalAdvance("10x") + 4)

        self._mode_one = self._button(True, "Step through the cubes on the wavelength shown now.")
        self._mode_all = self._button(True, "Step through every wavelength of a cube, then the next cube.")
        self._mode_all.setIconSize(QSize(_ICON_SIZE * 2, _ICON_SIZE))
        self._mode_roi = self._button(
            True,
            "Step through the ROI row's marks (where the ROI layout changes) on the wavelength shown now.",
        )
        self._mode_roi.setIconSize(QSize(_ICON_SIZE * 2, _ICON_SIZE))
        self._mode_mask = self._button(
            True,
            "Step through the cubes with a mark on the Mask row (a mask of their own) on the wavelength shown now.",
        )
        self._mode_mask.setIconSize(QSize(_ICON_SIZE * 2, _ICON_SIZE))
        self._end_cube = self._button(True, "End of the show = the last frame of the cube it is in.")
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
        for group, buttons in (
            (QButtonGroup(self), (self._mode_one, self._mode_all, self._mode_roi, self._mode_mask)),
            (QButtonGroup(self), (self._end_cube, self._end_dataset)),
        ):
            group.setExclusive(True)
            for button in buttons:
                group.addButton(button)
        self._mode_one.clicked.connect(lambda: self._change(mode=MODE_ONE_WAVELENGTH))
        self._mode_all.clicked.connect(lambda: self._change(mode=MODE_ALL))
        self._mode_roi.clicked.connect(lambda: self._change(mode=MODE_ROI_STATES))
        self._mode_mask.clicked.connect(lambda: self._change(mode=MODE_MASK_STATES))
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
        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.setSpacing(6)
        speed_row.addWidget(self._speed)
        speed_row.addWidget(self._speed_label)
        speed_row.addStretch(1)
        grid.addWidget(QLabel("Speed", self), 0, 0)
        grid.addLayout(speed_row, 0, 1)
        for row, (title, buttons) in enumerate(
            (
                ("Show", (self._mode_one, self._mode_all, self._mode_roi, self._mode_mask)),
                ("End", (self._end_cube, self._end_dataset)),
                ("Skip", (self._skip_ignored, self._skip_dark)),
                ("Faster", (self._thin_outline, self._calm_histogram)),
                ("Repeat", (self._repeat,)),
            ),
            start=1,
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
            self._speed, self._mode_one, self._mode_all, self._mode_roi, self._mode_mask, self._end_cube, self._end_dataset,
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
        self._speed.setValue(SPEEDS.index(settings.speed) if settings.speed in SPEEDS else 0)
        self._speed_label.setText(f"{settings.speed}x")
        one, every, states = (settings.mode == MODE_ONE_WAVELENGTH, settings.mode == MODE_ALL, settings.mode == MODE_ROI_STATES)
        self._mode_one.setChecked(one)
        self._mode_all.setChecked(every)
        self._mode_roi.setChecked(states)
        masks = settings.mode == MODE_MASK_STATES
        self._mode_mask.setChecked(masks)
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

        self._mode_one.setIcon(load_tabler_icon("cube", color=colour(one), size=_RENDER))
        self._mode_all.setIcon(_cube_lambda_icon(colour(every)))
        self._mode_roi.setIcon(_pair_icon("cube", "chevron-right", colour(states)))
        self._mode_mask.setIcon(_pair_icon("cube", "mask", colour(masks)))
        end_applies = every
        self._end_cube.setIcon(load_tabler_icon("cube", color=colour(settings.end == END_CUBE and end_applies), size=_RENDER))
        self._end_dataset.setIcon(load_tabler_icon("stack-3", color=colour(settings.end == END_DATASET and end_applies), size=_RENDER))
        for button in (self._end_cube, self._end_dataset):
            button.setEnabled(end_applies)
        self._end_cube.setToolTip(
            "End of the show = the last frame of the cube it is in."
            if end_applies else "Only with every wavelength shown: the other modes end at the last cube (or the last ROI mark)."
        )
        self._end_dataset.setToolTip(
            "End of the show = the last frame of the dataset."
            if end_applies else "Only with every wavelength shown: the other modes end at the last cube (or the last ROI mark)."
        )
        self._repeat.setIcon(load_tabler_icon("repeat", color=colour(settings.repeat), size=_RENDER))
        self._skip_ignored.setIcon(load_tabler_icon("eye-off", color=theme.accent_red if settings.skip_ignored else plain, size=_RENDER))
        self._skip_dark.setIcon(load_tabler_icon("moon", color=theme.accent_gold if settings.skip_dark else plain, size=_RENDER))
        self._thin_outline.setIcon(load_tabler_icon("circle", color=theme.accent_blue if settings.thin_roi_outline else plain, size=_RENDER))
        self._calm_histogram.setIcon(
            load_tabler_icon("chart-histogram", color=theme.accent_blue if settings.calm_histogram else plain, size=_RENDER)
        )
        for button in (
            self._mode_one, self._mode_all, self._mode_roi, self._mode_mask, self._end_cube, self._end_dataset, self._repeat,
            self._skip_ignored, self._skip_dark, self._thin_outline, self._calm_histogram,
        ):
            button.setStyleSheet(transparent_icon_button_stylesheet(hover=theme.control_bg_hover))
