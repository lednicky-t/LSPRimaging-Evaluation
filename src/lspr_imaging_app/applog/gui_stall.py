"""What is the GUI thread doing while the window does not answer? (2026-11-28)

**Why.** A launch log of 2026-11-28 showed the GUI thread busy for ~2.4 s (`analysis.scan_checked handover_ms=2407`) with *nothing logged*
in between: the work was Python code on the GUI thread that nobody had put a timer around. Looking for it one suspect at a time is slow;
this records it whatever it is.

**How it works.** The GUI thread proves it is alive by calling :meth:`GuiStallWatchdog.beat` from a 50 ms `QTimer` (the app wires the
timer, as it does for `gc_trace.flush`). A plain thread wakes every 100 ms and compares the clock with the last beat. When the beat is
more than ``threshold_s`` late (300 ms), the GUI thread is *stalled*: the thread looks at the Python stack of the GUI thread
(``sys._current_frames()``) at every wake-up while the stall lasts, and when the beat comes back it writes **one** DEBUG record,
``gui.stall``: how long it lasted, the application function where most samples were taken (``where``), the next most common ones, and the
call stack of one sample in it. The stack names Python functions only; a stall inside a native call (Qt painting, a numpy call) shows the
Python line that made the call.

**Cost.** Ten wake-ups per second on a thread that does a dictionary lookup; no wake-up needs more than a few microseconds of the
interpreter lock. A stall is logged after it ends, so a freeze that never ends is not reported here (`faulthandler` is for that).
Installed by the app only (`app_rewrite.main`); tests never get it.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from types import FrameType

from .events import log_event

logger = logging.getLogger(__name__)

BEAT_MS = 50  # how often the GUI thread is asked to beat (the app's QTimer)
POLL_S = 0.1  # how often the watching thread looks
THRESHOLD_S = 0.3  # a beat later than this is a stall
_PACKAGE = "lspr_imaging_app"
_MAX_DEPTH = 60  # frames walked per sample
_STACK_FRAMES = 10  # application frames in the logged stack
_TOP_PLACES = 3


def _label(frame: FrameType) -> str:
    code = frame.f_code
    module = frame.f_globals.get("__name__", "?")
    short = module.split(f"{_PACKAGE}.", 1)[-1] if _PACKAGE in module else module
    return f"{short}.{getattr(code, 'co_qualname', code.co_name)}:{frame.f_lineno}"


def describe_stack(frame: FrameType | None) -> tuple[str, list[str]]:
    """(where, stack) of one sample. ``stack`` lists the application's own frames, innermost first (the standard library, Qt
    glue and numpy are left out so the line is short); ``where`` is its first entry, or the innermost frame of any kind when the
    thread is outside the application (an event loop, a library call)."""
    own: list[str] = []
    first_any = ""
    depth = 0
    while frame is not None and depth < _MAX_DEPTH:
        if not first_any:
            first_any = _label(frame)
        if _PACKAGE in str(frame.f_globals.get("__name__", "")):
            own.append(_label(frame))
        frame = frame.f_back
        depth += 1
    where = own[0] if own else first_any or "?"
    return where, own[:_STACK_FRAMES]


@dataclass
class _Stall:
    started: float  # the last beat before the stall: the stall began somewhere after it
    places: Counter = field(default_factory=Counter)
    stacks: dict[str, list[str]] = field(default_factory=dict)  # one sample's stack per place

    def sample(self, where: str, stack: list[str]) -> None:
        self.places[where] += 1
        self.stacks[where] = stack


class GuiStallWatchdog:
    def __init__(
        self,
        main_ident: int | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
        frames: Callable[[], dict[int, FrameType]] = sys._current_frames,
        threshold_s: float = THRESHOLD_S,
        poll_s: float = POLL_S,
    ) -> None:
        self._main_ident = main_ident if main_ident is not None else threading.main_thread().ident
        self._clock = clock
        self._frames = frames
        self._threshold = threshold_s
        self._poll = poll_s
        self._last_beat = clock()
        self._stall: _Stall | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- GUI thread --------------------------------------------------------------------------------------------------------
    def beat(self) -> None:
        """Called from the GUI thread's timer: "the event loop is running"."""
        self._last_beat = self._clock()

    # -- watching thread ---------------------------------------------------------------------------------------------------
    def check(self) -> None:
        """One look (the watching thread calls it every ``poll_s``; tests call it directly with a fake clock)."""
        last_beat = self._last_beat
        late = self._clock() - last_beat
        if late >= self._threshold:
            if self._stall is None:
                self._stall = _Stall(started=last_beat)
            where, stack = describe_stack(self._frames().get(self._main_ident))
            self._stall.sample(where, stack)
        elif self._stall is not None:
            stall, self._stall = self._stall, None
            self._report(stall, ended=last_beat)

    def _report(self, stall: _Stall, *, ended: float) -> None:
        top = stall.places.most_common(_TOP_PLACES)
        where = top[0][0]
        total = sum(stall.places.values())
        log_event(
            logger, "gui.stall", logging.DEBUG,
            dur_ms=round((ended - stall.started) * 1000.0), samples=total, where=where,
            others=" | ".join(f"{place} x{count}" for place, count in top[1:]) or "-",
            stack=" < ".join(stall.stacks[where]),
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._last_beat = self._clock()
        self._thread = threading.Thread(target=self._run, name="gui-stall-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._poll):
            try:
                self.check()
            except Exception:  # noqa: BLE001 - a diagnostic must never take the app down
                logger.debug("gui stall watchdog check failed", exc_info=True)
