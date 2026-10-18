"""One place for the colour of each kind of slider and row (2026-10-18, maintainer request:
"link all colours together").

The slideshow mode icons on the slider ribbon (`slideshow_ribbon_controls.py`) wear the colour of the thing they
step through, so a colour is defined once and both the slider / row and the icon read it from here:

- **Cube**: the Cube slider's handle (gold).
- **Wavelength**: the wavelength slider's handle (the theme's blue).
- **ROI**: the ROIs row's marks (the theme's blue).
- **Mask**: the Mask rows' marks (a fixed orange, `mask_rows.MASK_COLOR`).
- **Pointers**: the Mask scan's pointers (a fixed pink, `mask_rows.EVENT_COLOR`).

Theme colours are functions, not constants, so a theme switch recolours them.
"""

from __future__ import annotations

from lspr_ui import get_active_theme

from .mask_rows import EVENT_COLOR, MASK_COLOR

__all__ = ["EVENT_COLOR", "MASK_COLOR", "cube_color", "roi_color", "wavelength_color"]


def cube_color() -> str:
    return get_active_theme().accent_gold


def wavelength_color() -> str:
    return get_active_theme().accent_blue


def roi_color() -> str:
    return get_active_theme().accent_blue
