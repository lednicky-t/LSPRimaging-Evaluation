"""``ImageFirst`` - lets the Image panel draw its frame before a display panel starts a long computation (2026-10-24).

Why: in the launch log of 2026-10-24, reading one 4 MB plane for the restored cube took 1.4-2.9 s while the Sensorgram was fitting, and
110-160 ms again the moment the fit ended (`docs/startup_gui_thread_audit_2026-10-24.md`). Both are Python threads sharing the GIL (the lock
that lets one thread run Python at a time), and a number-crunching thread slows down a thread that needs many small GIL hand-overs, such as
a zarr read. The image is what the user is waiting for, so the long computation starts after it.

How: a panel asks `should_wait()` just before it starts a computation. While the image panel has a frame in flight (`image_busy()` is True)
the answer is True and the panel tries again a moment later. It is a head start, not a lock: after `max_wait_s` the answer is False, so a
computation is never held back for good (e.g. while Play keeps the image panel busy). With no `image_busy` callable it never waits.
"""

from __future__ import annotations

import time
from collections.abc import Callable

MAX_WAIT_S = 5.0


class ImageFirst:
    def __init__(
        self,
        image_busy: Callable[[], bool] | None = None,
        max_wait_s: float = MAX_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._image_busy = image_busy
        self._max_wait_s = float(max_wait_s)
        self._clock = clock
        self._waiting_since: float | None = None

    def should_wait(self) -> bool:
        """True: postpone the computation and ask again shortly. False: start it now (image drawn, or waited long enough)."""
        if self._image_busy is None or not self._image_busy():
            self._waiting_since = None
            return False
        now = self._clock()
        if self._waiting_since is None:
            self._waiting_since = now
        if now - self._waiting_since >= self._max_wait_s:
            self._waiting_since = None  # go; the next computation gets a fresh head start
            return False
        return True
