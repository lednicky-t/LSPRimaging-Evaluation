"""Recolor the native Windows title bar to match the active theme
(2026-09-27, VS Code style pass).

**Why this exists.** Qt draws every pixel of this app's own UI, but the
title bar (with the OS minimize/maximize/close buttons) is drawn by
Windows itself, entirely outside Qt's rendering - `setTitleBarWidget()`
(``dock_container.py``) replaces a *dock panel's* title bar, not the main
*window's* OS title bar. Confirmed by sampling a real screenshot pixel by
pixel: a solid, visibly brown-tinted `#23201a` band running exactly the
height of the title bar, transitioning cleanly to this app's actual theme
color right where the menu bar starts - not a color this app's `GuiTheme`
sets anywhere. The most likely cause is Windows' own "show accent color on
title bars" setting picking up whatever the maintainer's system accent
color is. Real VS Code never hits this at all because it draws a fully
custom title bar by default; doing the same here would be a much bigger
change (losing the OS title bar also means reimplementing Snap Layouts,
system move/resize, etc.). This is the lighter fix: keep the native title
bar, just tell Windows what color to paint it via DWM's
`DWMWA_CAPTION_COLOR`/`DWMWA_TEXT_COLOR` window attributes (Windows 11
22H2+ only - a documented, public Win32 API, not an undocumented hack).

Silently does nothing on an older Windows build or a non-Windows platform -
the title bar just stays whatever the OS default is, same as before this
existed.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(hex_color: str) -> "ctypes.wintypes.DWORD":
    """Windows COLORREF packing: 0x00BBGGRR - blue in the highest byte,
    red in the lowest - the reverse of a normal #RRGGBB hex string."""
    import ctypes.wintypes as wintypes

    value = hex_color.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return wintypes.DWORD(red | (green << 8) | (blue << 16))


def apply_windows_titlebar_color(window, theme) -> None:
    """Set *window*'s native title bar caption/text color from *theme*.

    Call once after the window has a real native handle (safe any time
    after construction - ``winId()`` forces one to exist if needed), and
    again after every live theme switch so the title bar keeps tracking
    the active theme instead of freezing at whatever was active on
    launch."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        dwmapi = ctypes.windll.dwmapi
        caption = _colorref(theme.toolbar_section_bg)
        text = _colorref(theme.text_primary)
        dwmapi.DwmSetWindowAttribute(hwnd, _DWMWA_CAPTION_COLOR, ctypes.byref(caption), ctypes.sizeof(caption))
        dwmapi.DwmSetWindowAttribute(hwnd, _DWMWA_TEXT_COLOR, ctypes.byref(text), ctypes.sizeof(text))
    except OSError:
        # DwmSetWindowAttribute rejects DWMWA_CAPTION_COLOR/TEXT_COLOR on
        # Windows builds older than 11 22H2 - not this app's problem to
        # solve, the OS default title bar is a perfectly fine fallback.
        logger.debug("Could not set native title bar color (needs Windows 11 22H2+)", exc_info=True)
    except Exception:
        logger.debug("Could not set native title bar color", exc_info=True)
