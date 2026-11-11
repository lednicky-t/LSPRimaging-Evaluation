"""Edit -> Preferences -> Performance: "Screen resolution" (2026-11-11, `docs/play_diagnosis_2026-11-11.md` section 6).

Why it exists: on a 4K monitor at 150 % scaling the main window is 3840 x 2080 real pixels (8 M). With the image drawn through OpenGL,
every screen update hands the **whole window** to the graphics driver, and that costs time in proportion to the window's pixels (a 3-pixel
cursor line cost ~14 ms there; Play ran at 20 fps). Launched as a 96-dpi program, Windows gets a 2560 x 1387 window (3.5 M pixels) and
stretches it to the monitor on the graphics card: Play measured 28-29 fps (the speed setting's cap) on the same layout. The price: the
whole window, text included, is softer, as in an old program on a high-resolution monitor.

Two choices:

* ``full`` (default): the window is drawn at the monitor's real resolution. Sharp.
* ``standard``: Windows draws the window at 100 % scale and stretches it. Faster on a high-resolution monitor, softer.

The choice can only be made **before the QApplication exists** (it is how Qt asks Windows to treat the process), so it is read here from the
saved settings file with no Qt, set as Qt's own platform option (``QT_QPA_PLATFORM=windows:dpiawareness=0``), and takes effect at the **next
start**. `src/main.py` and `app_rewrite.main` call `apply_saved_resolution()` right before they create the QApplication. Never raises: a
settings file that cannot be read means the full resolution.

This module imports nothing from Qt or from the rest of the app at module level (it runs before the splash screen).
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

RESOLUTION_FULL = "full"
RESOLUTION_STANDARD = "standard"
RESOLUTIONS = (RESOLUTION_FULL, RESOLUTION_STANDARD)

_PLATFORM_VARIABLE = "QT_QPA_PLATFORM"
_DPI_OPTION = "dpiawareness=0"  # Qt's windows platform: 0 = the program is not DPI aware (Windows scales the window), 2 = per monitor (default)

_running = RESOLUTION_FULL
"""What this run was started with (set by `apply_saved_resolution`; shown in Preferences next to the saved choice)."""


def running_resolution() -> str:
    """The resolution this run was started with: "full" or "standard"."""
    return _running


def platform_value(choice: str, current: str, platform: str) -> str | None:
    """The new value of ``QT_QPA_PLATFORM`` that gives the choice, or None to leave the variable alone.

    Pure (no environment access) so it can be tested. `current` is the variable's present value ("" when unset).

    - not Windows, or the full resolution: None (nothing to do);
    - unset: ``windows:dpiawareness=0``;
    - already names the windows platform: the option is added (or left, if a DPI option is already there: the person who set the variable
      knows what they want);
    - any other platform (``offscreen`` in the tests, ``minimal``...): None, never overridden.
    """
    if platform != "win32" or choice != RESOLUTION_STANDARD:
        return None
    if not current.strip():
        return f"windows:{_DPI_OPTION}"
    name, _, options = current.partition(":")
    if name.strip() != "windows":
        return None
    if "dpiawareness" in options:
        return None
    return f"{current},{_DPI_OPTION}" if options else f"{current.rstrip(':')}:{_DPI_OPTION}"


def _asks_for_96_dpi(platform_variable: str) -> bool:
    """Whether a ``QT_QPA_PLATFORM`` value names the windows platform with the not-DPI-aware option, e.g. ``windows:dpiawareness=0``."""
    name, _, options = platform_variable.partition(":")
    return name.strip() == "windows" and _DPI_OPTION in [option.strip() for option in options.split(",")]


def saved_resolution() -> str:
    """The choice saved by Preferences ("full" when there is none or the file cannot be read)."""
    try:
        from .storage.app_settings import load_app_settings
        from .storage.ui_state_keys import DISPLAY_RESOLUTION, read

        value = read(load_app_settings().ui_state, DISPLAY_RESOLUTION)
    except Exception:  # noqa: BLE001 - nothing here may stop the app from starting
        logger.debug("The saved screen resolution could not be read", exc_info=True)
        return RESOLUTION_FULL
    return value if value in RESOLUTIONS else RESOLUTION_FULL


def apply_saved_resolution() -> str:
    """Before the QApplication: set Qt's platform option for the saved choice. Returns the resolution this run will have."""
    global _running
    choice = saved_resolution()
    new_value = platform_value(choice, os.environ.get(_PLATFORM_VARIABLE, ""), sys.platform)
    if new_value is not None:
        os.environ[_PLATFORM_VARIABLE] = new_value
    final = os.environ.get(_PLATFORM_VARIABLE, "")
    # What the run really gets: also "standard" when the variable already asked for 96 dpi (set by hand), whatever was saved.
    _running = RESOLUTION_STANDARD if sys.platform == "win32" and _asks_for_96_dpi(final) else RESOLUTION_FULL
    if choice == RESOLUTION_STANDARD and _running != RESOLUTION_STANDARD:  # asked for but not possible (another platform, or the variable says otherwise)
        logger.info("Standard screen resolution is saved but not applied (QT_QPA_PLATFORM=%r)", final)
    return _running
