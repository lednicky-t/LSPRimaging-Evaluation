"""A lightweight sampling profiler for ONE thread (the GUI thread), 2026-10-14.

Why not `cProfile` (`profiling.py`): since Python 3.12 it records every thread, slows
Python code 2-5x, and mixes the render thread, the log writer and the Histogram worker into
the GUI numbers. This one looks at the target thread's stack every few milliseconds from a
helper thread (`sys._current_frames()`): the cost is a stack walk per sample plus a GIL hand-off
between the two threads (small, but not zero: expect a few percent at 10 ms), and it sees only that thread. Like py-spy it answers "where is the GUI thread right now?", which is
the question behind "wait" / "other" time in the Play stats.

What a sample means: the innermost *Python* function on the thread's stack. Time spent in
Qt's C++ (painting, layout) is attributed to the Python function that called into it
(`paintEvent`, a slot ...). A sample whose innermost frame is the function that called
``app.exec()`` (or the test pump) is "event loop": the thread is idle **or** doing Qt work
that no Python code sits on top of.

Limit: the sampler needs the GIL to run, so it can only sample when the target releases it
(every ~5 ms at the latest, or in any Qt call): fine for 100+ ms frames, coarse below ~10 ms.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .profiling import ProfileResult, _short_file

DEFAULT_INTERVAL_S = 0.010
MAX_DEPTH = 60
# Functions that sit under the event loop: a sample whose innermost frame is one of these is "event loop".
_LOOP_FUNCTIONS = {"main", "run", "_pump", "exec", "exec_"}


@dataclass(frozen=True)
class _Key:
    file: str
    function: str

    def label(self) -> str:
        return f"{self.file}:{self.function}"


class StackSampler:
    def __init__(self, thread_id: int | None = None, interval_s: float = DEFAULT_INTERVAL_S) -> None:
        self._thread_id = thread_id if thread_id is not None else threading.get_ident()
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._self: Counter[_Key] = Counter()
        self._inclusive: Counter[_Key] = Counter()
        self._samples = 0
        self._event_loop = 0
        self._started = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._started = time.perf_counter()
        self._thread = threading.Thread(target=self._run, name="StackSampler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            frame = sys._current_frames().get(self._thread_id)
            if frame is None:
                continue
            self._record(frame)

    def _record(self, frame: Any) -> None:
        keys: list[_Key] = []
        depth = 0
        while frame is not None and depth < MAX_DEPTH:
            code = frame.f_code
            keys.append(_Key(_short_file(code.co_filename), code.co_name))
            frame = frame.f_back
            depth += 1
        if not keys:
            return
        self._samples += 1
        innermost = keys[0]
        self._self[innermost] += 1
        if innermost.function in _LOOP_FUNCTIONS:
            self._event_loop += 1
        for key in set(keys):
            self._inclusive[key] += 1

    def stop(self, save_dir: Path | None = None, name: str = "gui") -> ProfileResult | None:
        """Stop and summarise. `save_dir` / `name` are accepted for the same call shape as `GuiProfile` (nothing is saved)."""
        thread, self._thread = self._thread, None
        if thread is None:
            return None
        self._stop.set()
        thread.join(timeout=1.0)
        seconds = time.perf_counter() - self._started
        total = max(self._samples, 1)
        rows = [
            {
                "function": key.label(),
                "incl_pct": round(100.0 * count / total, 1),
                "self_pct": round(100.0 * self._self.get(key, 0) / total, 1),
                "samples": count,
            }
            for key, count in self._inclusive.most_common(30)
        ]
        busy_pct = 100.0 * (1.0 - self._event_loop / total)
        lines = [
            f"GUI thread, {self._samples} samples over {seconds:.1f} s (every {self._interval * 1000:.0f} ms)",
            f"in Python code under an event handler: {busy_pct:.0f} %    event loop (idle, or Qt work with no Python on top): {100 - busy_pct:.0f} %",
            "",
            f"{'incl %':>7} {'self %':>7}  function  (incl = on the stack, self = innermost)",
        ]
        width = max((len(row["function"]) for row in rows), default=10)
        lines.extend(f"{row['incl_pct']:>7.1f} {row['self_pct']:>7.1f}  {row['function']:<{width}}" for row in rows)
        text = "\n".join(lines)
        result = ProfileResult(seconds=seconds, text=text, rows=rows, prof_path=None)
        return result
