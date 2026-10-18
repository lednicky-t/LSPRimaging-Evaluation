"""Let background worker threads yield to the window (2026-10-17, audit `docs/transient_scan_automask_slider_audit_2026-10-17.md`, B2).

**What this means.** Every thread of a program starts at the same priority, so when a scan keeps 12 worker threads busy on an
8-core machine, the thread that draws the window (the GUI thread) is just one more thread in the queue for a core. A thread set
to *below normal* priority only gets a core when no normal-priority thread wants it. The scan then takes the cores the window
is not using, and the window keeps its turn first. It costs the scan little (it is bound by reading the files) and is invisible
when nothing else is going on.

Windows only (that is where the app runs); a no-op elsewhere. A thread lowers *itself*: call `lower_current_thread_priority()`
as the first thing in a worker (for a `ThreadPoolExecutor`: its ``initializer``).
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

THREAD_PRIORITY_BELOW_NORMAL = -1


def lower_current_thread_priority() -> bool:
    """Set the calling thread to below-normal priority. Returns whether it was set (False: not Windows, or the call failed).
    Never raises: a priority hint must not be able to stop a worker from starting."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # a private handle on the DLL: no argtypes set on the shared `windll`
        kernel32.GetCurrentThread.restype = wintypes.HANDLE
        kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.SetThreadPriority.restype = wintypes.BOOL
        return bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL))
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("Could not lower the thread priority", exc_info=True)
        return False


def current_thread_priority() -> int | None:
    """The calling thread's Windows priority (0 = normal, -1 = below normal), or None when it cannot be read. For tests."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThread.restype = wintypes.HANDLE
        kernel32.GetThreadPriority.argtypes = [wintypes.HANDLE]
        kernel32.GetThreadPriority.restype = ctypes.c_int
        return int(kernel32.GetThreadPriority(kernel32.GetCurrentThread()))
    except Exception:  # noqa: BLE001
        return None
