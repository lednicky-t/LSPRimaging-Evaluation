"""How quickly Python hands its interpreter lock to a waiting thread (2026-10-14, testing7 analysis).

**What this means.** Only one thread at a time may run Python code: it holds the *GIL* (global
interpreter lock). A thread that wants it waits, and Python asks the holder to let go only after the
*switch interval* has passed (default 5 ms). A thread doing native work (decoding a TIFF, an OpenCV or
numpy call) gives the lock up while it works and has to take it back afterwards, so every "come back"
can cost up to one interval while the GUI thread is busy.

**What was measured.** The render thread reads a plane in 3-4 such round trips: 21 ms per plane with an
idle GUI, 36 ms while the slideshow played (testing7). With 0.5 ms the read fell to 23 ms in the app
(first run after the change) and the render thread's wait for a free thread fell from 29 to ~5 ms.

**Why it is OFF by default (2026-10-14, same evening).** (1) The frame rate did not move (17.5 -> 18.3 fps):
the GUI thread, not the render thread, is the limit now. (2) The first run with 0.5 ms ended in a native
access violation in Qt (`QRasterPaintEngine::penChanged`, GUI thread, inside a paint, 1 s after Play started;
`logs/lspri-20261014-182741-60288.fault.log`). A shorter interval lets other Python threads run between two
Qt calls of a GUI-thread paint far more often; if one of them frees a Qt object (Python's cyclic garbage
collector runs on whichever thread allocates), Qt can crash. The app has no guard against that yet
(`docs/testing7_playback_analysis_2026-10-14.md`, "The crash"). Not proven to be the cause, so it stays opt-in.

``LSPR_GIL_SWITCH_MS=<milliseconds>`` (for example ``0.5``) switches it on for a run, so the effect can be
compared without editing code. Unset = Python's own default (5 ms), untouched. A value that is not a
positive number is reported and ignored.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from collections.abc import Mapping

from .applog import log_event

logger = logging.getLogger(__name__)

ENV_VAR = "LSPR_GIL_SWITCH_MS"


def apply_switch_interval(environ: Mapping[str, str] | None = None) -> float:
    """Apply the switch interval asked for in `LSPR_GIL_SWITCH_MS`, if any, and return the one now in effect
    (seconds). Call once at start-up, before worker threads exist. `environ` is for tests (default: `os.environ`)."""
    environ = os.environ if environ is None else environ
    previous = sys.getswitchinterval()
    source = "default"
    raw = environ.get(ENV_VAR, "").strip()
    if raw:
        try:
            requested_ms = float(raw)
        except ValueError:
            requested_ms = 0.0
        if math.isfinite(requested_ms) and requested_ms > 0.0:
            sys.setswitchinterval(requested_ms / 1000.0)
            source = "env"
        else:
            logger.warning("%s=%r is not a positive number of milliseconds: ignored, Python's default stays", ENV_VAR, raw)
    interval = sys.getswitchinterval()
    log_event(
        logger, "app.gil_switch_interval", logging.INFO,
        ms=round(interval * 1000.0, 3), previous_ms=round(previous * 1000.0, 3), source=source,
    )
    return interval
