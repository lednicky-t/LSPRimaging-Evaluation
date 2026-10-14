"""CPU time of the calling thread, split into *user* and *kernel* time (2026-10-14, testing10).

`time.thread_time()` gives the sum. The split matters for one question: when the GUI thread burns CPU per frame that
no Python code accounts for (testing10: ~20 ms per frame with every overlay off), is it Qt's own C++ painting and event
handling (user time), or the window system / graphics driver doing the screen update on its behalf (kernel time)?

Windows only (`GetThreadTimes`); elsewhere `thread_cpu_times()` returns None and callers fall back to
`time.thread_time()`. Windows charges thread time in scheduler ticks (~15.6 ms), so one reading is coarse; averaged over
a second of frames (what the Play stats do) it is fine.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetCurrentThread.restype = wintypes.HANDLE
    _kernel32.GetThreadTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
    _kernel32.GetThreadTimes.restype = wintypes.BOOL

    def _seconds(value: _FILETIME) -> float:
        return ((value.high << 32) | value.low) * 1e-7  # 100 ns units

    def thread_cpu_times() -> tuple[float, float] | None:
        """``(user_seconds, kernel_seconds)`` of the calling thread, or None if Windows refuses."""
        created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        ok = _kernel32.GetThreadTimes(
            _kernel32.GetCurrentThread(),
            ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user),
        )
        return (_seconds(user), _seconds(kernel)) if ok else None

else:

    def thread_cpu_times() -> tuple[float, float] | None:
        return None
