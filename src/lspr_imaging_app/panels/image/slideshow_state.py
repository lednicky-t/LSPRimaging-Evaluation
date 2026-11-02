"""The slideshow settings (speed, mode, repeat, end, skips, Play options) put back as last left, and saved when they change.

Moved out of `panel.py` 2026-11-02 (maintainability audit item D, batch 3). The code is the code that was in
`ImagePanel._restore_slideshow_settings`; it needs only the settings store, the ribbon that shows the settings and the controller that
uses them.
"""

from __future__ import annotations

from ...storage.ui_state_keys import (
    SLIDESHOW_CALM_HISTOGRAM,
    SLIDESHOW_END,
    SLIDESHOW_MODE,
    SLIDESHOW_REPEAT,
    SLIDESHOW_SKIP_DARK,
    SLIDESHOW_SKIP_IGNORED,
    SLIDESHOW_SPEED,
    SLIDESHOW_THIN_ROI_OUTLINE,
    read,
)
from ..ui_state import UiStateStore
from .slider_ribbon import SliderRibbon
from .slideshow import (
    END_CUBE,
    END_DATASET,
    MODE_ALL,
    MODE_EVENTS,
    MODE_MASK_STATES,
    MODE_ONE_WAVELENGTH,
    MODE_ROI_STATES,
    SPEEDS,
    SlideshowController,
    SlideshowSettings,
)


def restore_slideshow_settings(store: UiStateStore, slider_ribbon: SliderRibbon, slideshow: SlideshowController) -> None:
    """Slideshow speed / mode / repeat / end back as last left, and keep saving them."""
    saved = SlideshowSettings(
        speed=read(store, SLIDESHOW_SPEED),
        mode=read(store, SLIDESHOW_MODE),
        repeat=read(store, SLIDESHOW_REPEAT),
        end=read(store, SLIDESHOW_END),
        skip_ignored=read(store, SLIDESHOW_SKIP_IGNORED),
        skip_dark=read(store, SLIDESHOW_SKIP_DARK),
        thin_roi_outline=read(store, SLIDESHOW_THIN_ROI_OUTLINE),
        calm_histogram=read(store, SLIDESHOW_CALM_HISTOGRAM),
    )
    if (
        saved.speed not in SPEEDS
        or saved.mode not in (MODE_ALL, MODE_ONE_WAVELENGTH, MODE_ROI_STATES, MODE_MASK_STATES, MODE_EVENTS)
        or saved.end not in (END_CUBE, END_DATASET)
    ):
        saved = SlideshowSettings()  # a hand-edited settings file
    slider_ribbon.set_settings(saved)
    slideshow.set_settings(saved)

    def save(settings: SlideshowSettings) -> None:
        store.set(SLIDESHOW_SPEED.key, settings.speed)
        store.set(SLIDESHOW_MODE.key, settings.mode)
        store.set(SLIDESHOW_REPEAT.key, settings.repeat)
        store.set(SLIDESHOW_END.key, settings.end)
        store.set(SLIDESHOW_SKIP_IGNORED.key, settings.skip_ignored)
        store.set(SLIDESHOW_SKIP_DARK.key, settings.skip_dark)
        store.set(SLIDESHOW_THIN_ROI_OUTLINE.key, settings.thin_roi_outline)
        store.set(SLIDESHOW_CALM_HISTOGRAM.key, settings.calm_histogram)

    slider_ribbon.settings_changed.connect(save)
