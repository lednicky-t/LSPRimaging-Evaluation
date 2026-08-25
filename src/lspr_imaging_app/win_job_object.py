from __future__ import annotations

import logging
import sys

_LOGGER = logging.getLogger("lspr_imaging_app.startup")

# Kept alive for the process lifetime - deliberately never closed. Windows
# always closes every open handle a process holds when that process exits,
# whether the exit is normal, a crash, or a forceful Task Manager kill; it's
# that automatic handle-close (not any Python code) which fires the job
# object's "kill everything attached" behavior below. If we closed this
# handle ourselves (e.g. in an atexit hook), we'd disarm the safety net for
# the exact moment - a hard kill - it exists to cover.
_job_object_handle: int | None = None


def enable_kill_children_on_exit() -> None:
    """Ensure every child process this app spawns (OME-Zarr export's
    ProcessPoolExecutor workers) is force-terminated by Windows itself the
    moment this process exits, however it exits.

    Normal export completion/cancellation already cleans its own workers up
    via a `finally: executor.shutdown(wait=True)` in dataset.py - that part
    was never broken. The gap this closes is a hard kill of the whole app
    (Task Manager "End Task", or being forced to kill a hang) while an
    export's worker pool is alive: a forceful kill runs no Python code at
    all, so that `finally` block never gets a chance to run, and the
    already-spawned worker processes are orphaned - left running forever,
    idle, waiting on a pipe to a parent that no longer exists (observed:
    dozens of stray `--multiprocessing-fork` python.exe processes
    accumulating in Task Manager across repeated crash/kill cycles).

    A Windows "job object" is an OS-level group a process can be placed in;
    flagging it JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE means Windows guarantees
    every process in the group dies when the group's last handle closes -
    and every process automatically inherits membership in any job object
    its parent belongs to. Since Windows itself (not this app) enforces
    that, it covers every way this process can end, including a hard kill.

    No-op on non-Windows, and silently gives up on any failure (e.g. this
    process is already in a job object that doesn't allow nesting on very
    old Windows versions) - this is a hardening measure, not something
    startup should ever fail over.
    """
    global _job_object_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # restype/argtypes must be set explicitly: HANDLE is pointer-sized
        # (8 bytes on 64-bit Windows), and ctypes defaults an unconfigured
        # function to a 32-bit int return - which would silently truncate
        # every handle here into garbage on 64-bit without this.
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []

        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            return

        limit_info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limit_info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job_handle, JobObjectExtendedLimitInformation,
            ctypes.byref(limit_info), ctypes.sizeof(limit_info),
        ):
            return

        if not kernel32.AssignProcessToJobObject(job_handle, kernel32.GetCurrentProcess()):
            return

        _job_object_handle = job_handle
        _LOGGER.debug("Job object armed: child processes will be killed if this process exits unexpectedly.")
    except Exception:
        _LOGGER.debug("Could not arm kill-children-on-exit job object; continuing without it.", exc_info=True)
