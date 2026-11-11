"""Edit -> Preferences -> Performance: "Screen resolution" (2026-11-11, `docs/play_diagnosis_2026-11-11.md` section 6).

Why it exists: on a 4K monitor at 150 % scaling the main window is 3840 x 2080 real pixels (8 M). With the image drawn through OpenGL,
every screen update hands the **whole window** to the graphics driver, and that costs time in proportion to the window's pixels (a 3-pixel
cursor line cost ~14 ms there; Play ran at 20 fps). Launched as a 96-dpi program, Windows gets a 2560 x 1387 window (3.5 M pixels) and
stretches it to the monitor on the graphics card: Play measured 28-29 fps (the speed setting's cap) on the same layout. The price: the
whole window, text included, is softer, as in an old program on a high-resolution monitor.

Two choices:

* ``full`` (default): the window is drawn at the monitor's real resolution. Sharp.
* ``standard``: Windows draws the window at 100 % scale and stretches it. Faster on a high-resolution monitor, softer.

The two words are only the saved values. Preferences shows the **real pixel sizes** instead (maintainer, 2026-11-12), e.g. "3840 x 2160" and
"2560 x 1440" (`resolution_sizes`, `monitor_pixels`). The reduced size is Qt's logical size of the screen (the same in both modes, measured on
the laptop screen and the external 4K monitor); the real size comes from Windows by device name (`GetDeviceCaps`, also right when the run is
96-dpi: Qt itself then only knows the reduced size).

The choice can only be made **before the QApplication exists** (it is how Qt asks Windows to treat the process), so it is read here from the
saved settings file with no Qt, set as Qt's own platform option (``QT_QPA_PLATFORM=windows:dpiawareness=0``), and takes effect at the **next
start**. `src/main.py` and `app_rewrite.main` call `apply_saved_resolution()` right before they create the QApplication. Never raises: a
settings file that cannot be read means the full resolution.

This module imports nothing from Qt or from the rest of the app at module level (it runs before the splash screen).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class ResolutionSizes:
    """The two sizes the choice gives on one screen, in pixels (width, height)."""

    full: tuple[int, int]
    """The monitor's real pixels: what "full" draws."""
    reduced: tuple[int, int]
    """What "standard" draws (the screen at Windows' 100 % scale); Windows stretches it to `full`."""

    @property
    def differ(self) -> bool:
        """False when Windows' scale of this screen is 100 %: both choices give the same window, so there is nothing to choose."""
        return self.full != self.reduced


def format_size(size: tuple[int, int]) -> str:
    """``(3840, 2160)`` -> ``"3840 × 2160"``."""
    return f"{size[0]} × {size[1]}"


def resolution_sizes(
    logical: tuple[int, int], running: str, device_ratio: float, real: tuple[int, int] | None = None
) -> ResolutionSizes | None:
    """The sizes of one screen, from what Qt says about it. Pure.

    `logical`: Qt's size of the screen (the same in a full and a standard run). `running`: ``RESOLUTION_FULL`` or ``RESOLUTION_STANDARD``.
    `device_ratio`: Qt's pixel ratio of the screen (1.5 at 150 %; always 1.0 in a standard run). `real`: the monitor's real pixels from
    Windows (`monitor_pixels`), or None. A full run knows the real size itself (logical x ratio); a standard run needs `real` and
    gives None without it (the caller then shows words instead of sizes).
    """
    if real is None and running == RESOLUTION_FULL:
        real = (round(logical[0] * device_ratio), round(logical[1] * device_ratio))
    if real is None:
        return None
    return ResolutionSizes(full=real, reduced=(int(logical[0]), int(logical[1])))


def monitor_pixels(window_handle: int) -> tuple[int, int] | None:
    """Windows only: the real pixel size of the monitor the window (an HWND) is on. None elsewhere, and on any failure (never raises).

    Asked by the monitor's device name (``\\\\.\\DISPLAY2``) with ``GetDeviceCaps(DESKTOPHORZRES / DESKTOPVERTRES)``, which is the real size
    whether or not the process is DPI aware (checked on the laptop screen and a 4K monitor in both modes, 2026-11-12).
    """
    if sys.platform != "win32" or not window_handle:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
        gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        gdi32.CreateDCW.restype = ctypes.c_void_p
        gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

        monitor = user32.MonitorFromWindow(window_handle, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return None
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        dc = gdi32.CreateDCW("DISPLAY", info.szDevice, None, None)
        if not dc:
            return None
        try:
            size = (int(gdi32.GetDeviceCaps(dc, 118)), int(gdi32.GetDeviceCaps(dc, 117)))  # DESKTOPHORZRES, DESKTOPVERTRES
        finally:
            gdi32.DeleteDC(dc)
        return size if size[0] > 0 and size[1] > 0 else None
    except Exception:  # noqa: BLE001 - only a label depends on this
        logger.debug("The monitor's real pixel size could not be read", exc_info=True)
        return None


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


_SETTINGS_FILENAME = "lspri_eva_rewrite_settings.json"
"""The app settings file's name, as in `storage/app_settings.py` (a test pins that the two agree)."""


def settings_file() -> Path:
    """Where the app settings file is (the same place as `storage.app_settings.default_settings_path`, pinned by a test)."""
    from platformdirs import user_config_dir

    return Path(user_config_dir("lspr-suite", appauthor=False)) / _SETTINGS_FILENAME


def saved_resolution() -> str:
    """The choice saved by Preferences ("full" when there is none or the file cannot be read).

    Reads the one value straight from the settings JSON instead of `storage.app_settings.load_app_settings`: that module imports the session
    code (numpy, OpenCV, the analysis), which delayed the **launch screen** by ~0.22 s on every start (measured 2026-11-12 in `src/main.py`'s
    import order: old way 209-252 ms, this way ~19 ms). The app's own loader still decides everything else about the file; this only looks at
    ``ui_state["display/resolution"]``.
    """
    try:
        from .storage.ui_state_keys import DISPLAY_RESOLUTION  # a light module (standard library only): the key stays in one place

        path = settings_file()
        if not path.is_file():
            return RESOLUTION_FULL
        payload = json.loads(path.read_text(encoding="utf-8"))
        ui_state = payload.get("ui_state") if isinstance(payload, dict) else None
        value = ui_state.get(DISPLAY_RESOLUTION.key) if isinstance(ui_state, dict) else None
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
