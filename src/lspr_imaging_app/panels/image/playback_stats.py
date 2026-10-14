"""``PlaybackStats`` - where the time goes while the slideshow plays (2026-10-14,
maintainer request: previewed images do not update fast enough, find the bottleneck).

Pure (no Qt): the panel feeds it timestamps from five points of one frame's trip
and it turns them into a one-line summary for the status bar, plus a final
summary when the show stops.

One frame's trip, and the stage each gap measures:

1. **step**: the slideshow moves the selection (`step_s` = the time the selection
   signal handlers took on the GUI thread: sliders, spin boxes, icon refresh...).
2. **wait**: from that step to the Image panel's redraw starting. This is the
   redraw coalescing timer (100 ms, restarted by every step), so it is the first
   suspect at fast speeds.
3. **redraw**: the GUI-thread snapshot of the module state and overlay drawing.
4. **queue**: the render thread was busy (or prefetching) when the request arrived.
5. **load** / **prep**: reading the plane (disk, or the plane cache) / geometry,
   background flattening on the render thread.
6. **gui**: applying the result on the GUI thread (`setImage`, levels, overlays,
   and `listeners` = the `image_rendered` subscribers such as the Histogram).

Steps that happened before a redraw could start are *merged* into one frame (the
redraw only ever shows the newest selection); `merged` counts them: a high number
means the screen is skipping frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _Frame:
    step_t: float  # the earliest step this frame stands for
    redraw_start: float = 0.0
    submit_t: float = 0.0
    step_s: float = 0.0
    redraw_s: float = 0.0


@dataclass
class _Window:
    steps: int = 0
    shown: int = 0
    step_s: float = 0.0
    wait_s: float = 0.0
    redraw_s: float = 0.0
    queue_s: float = 0.0
    load_s: float = 0.0
    prep_s: float = 0.0
    gui_s: float = 0.0
    listeners_s: float = 0.0
    lag_s: float = 0.0
    stale: int = 0  # rendered but superseded by a newer request before it arrived
    thread_s: float = 0.0  # queue part 1: request waited for the render thread to be free
    deliver_s: float = 0.0  # queue part 2: finished result waited for the GUI thread
    blocked: int = 0  # requests that arrived while a read-ahead plane read was running
    detail: dict[str, float] = field(default_factory=dict)  # finer GUI-thread stages, seconds summed
    cycle_s: float = 0.0  # time between the starts of consecutive steps, summed
    cycles: int = 0


@dataclass
class PlaybackStats:
    """Collects one slideshow run. `start()` .. `stop()`; every `note_*` is a no-op while inactive."""

    active: bool = False
    _pending_step_t: float | None = None
    _pending_step_s: float = 0.0
    _in_flight: dict[int, _Frame] = field(default_factory=dict)
    _window: _Window = field(default_factory=_Window)
    _total: _Window = field(default_factory=_Window)
    _window_start: float = 0.0
    _run_start: float = 0.0
    _target_interval_s: float = 0.0
    _last_done: float = 0.0
    _last_step_start: float | None = None
    _last_window: _Window = field(default_factory=_Window)
    _last_window_seconds: float = 1.0

    def start(self, now: float, target_interval_s: float) -> None:
        self.active = True
        self._pending_step_t = None
        self._pending_step_s = 0.0
        self._last_step_start = None
        self._in_flight.clear()
        self._window = _Window()
        self._total = _Window()
        self._window_start = self._run_start = now
        self._target_interval_s = target_interval_s

    def stop(self) -> None:
        self.active = False

    # -- the five points of a frame's trip --------------------------------------

    def note_step(self, now: float, handlers_s: float) -> None:
        """The slideshow moved the selection; `handlers_s` = time its signal handlers took."""
        if not self.active:
            return
        started = now - handlers_s
        if self._last_step_start is not None:
            for window in (self._window, self._total):
                window.cycle_s += started - self._last_step_start
                window.cycles += 1
        self._last_step_start = started
        self._window.steps += 1
        self._total.steps += 1
        self._window.step_s += handlers_s
        self._total.step_s += handlers_s
        if self._pending_step_t is None:
            self._pending_step_t = now - handlers_s  # the step began before its handlers finished
        self._pending_step_s += handlers_s

    def note_redraw(self, serial: int, start: float, end: float) -> None:
        """The panel's redraw ran and submitted request `serial`."""
        if not self.active or self._pending_step_t is None:
            return
        frame = _Frame(
            step_t=self._pending_step_t, redraw_start=start, submit_t=end,
            step_s=self._pending_step_s, redraw_s=end - start,
        )
        self._in_flight[serial] = frame
        self._pending_step_t = None
        self._pending_step_s = 0.0

    def note_rendered(
        self,
        serial: int,
        arrived: float,
        done: float,
        *,
        load_s: float,
        prep_s: float,
        listeners_s: float,
        submitted_at: float = 0.0,
        started_at: float = 0.0,
        finished_at: float = 0.0,
        blocked_by_prefetch: bool = False,
    ) -> str | None:
        """The GUI thread applied result `serial` (`arrived` .. `done`). Returns a
        new status line when a one-second window just closed, else None."""
        if not self.active:
            return None
        frame = self._in_flight.pop(serial, None)
        # Results older than this one will never be shown: they were superseded in flight.
        for old in [key for key in self._in_flight if key < serial]:
            del self._in_flight[old]
            self._window.stale += 1
            self._total.stale += 1
        if frame is None:
            return None
        parts = {
            "wait": frame.redraw_start - frame.step_t,
            "redraw": frame.redraw_s,
            "load": load_s,
            "prep": prep_s,
            "gui": done - arrived,
            "listeners": listeners_s,
            "queue": max((arrived - frame.submit_t) - load_s - prep_s, 0.0),
            "lag": done - frame.step_t,
        }
        # The queue time, split: the request waiting for the render thread, and the finished
        # result waiting for the GUI thread (zero when the caller did not pass the stamps).
        thread_s = max(started_at - submitted_at, 0.0) if started_at and submitted_at else 0.0
        deliver_s = max(arrived - finished_at, 0.0) if finished_at else 0.0
        self._last_done = done
        for window in (self._window, self._total):
            window.thread_s += thread_s
            window.deliver_s += deliver_s
            window.blocked += int(blocked_by_prefetch)
            window.shown += 1
            window.wait_s += parts["wait"]
            window.redraw_s += parts["redraw"]
            window.load_s += load_s
            window.prep_s += prep_s
            window.gui_s += parts["gui"]
            window.listeners_s += listeners_s
            window.queue_s += parts["queue"]
            window.lag_s += parts["lag"]
        if done - self._window_start >= 1.0:
            line = self._format(self._window, done - self._window_start, prefix="Play")
            self._last_window, self._last_window_seconds = self._window, done - self._window_start
            self._window = _Window()
            self._window_start = done
            return line
        return None

    def note_detail(self, name: str, seconds: float) -> None:
        """Add *seconds* to a finer stage (overlays, setImage, paint ...). Reported per shown
        frame, so a stage that runs more than once per frame (paint) shows its full cost."""
        if not self.active:
            return
        for window in (self._window, self._total):
            window.detail[name] = window.detail.get(name, 0.0) + seconds

    # -- structured numbers (for the log) -------------------------------------------------

    def total_fields(self) -> dict[str, float | int]:
        """The whole run as event fields (means in ms), for the `playback.summary` log event."""
        return self._fields_of(self._total, max(self._last_done - self._run_start, 1e-9))

    def last_window_fields(self) -> dict[str, float | int]:
        return self._fields_of(self._last_window, max(self._last_window_seconds, 1e-9))

    @staticmethod
    def _fields_of(w: _Window, seconds: float) -> dict[str, float | int]:
        n = max(w.shown, 1)
        return {
            "shown": w.shown, "steps": w.steps, "stale": w.stale, "fps": round(w.shown / seconds, 2),
            "lag_ms": round(w.lag_s / n * 1000, 1), "wait_ms": round(w.wait_s / n * 1000, 1),
            "redraw_ms": round(w.redraw_s / n * 1000, 1), "queue_ms": round(w.queue_s / n * 1000, 1),
            "thread_ms": round(w.thread_s / n * 1000, 1), "deliver_ms": round(w.deliver_s / n * 1000, 1),
            "load_ms": round(w.load_s / n * 1000, 1), "prep_ms": round(w.prep_s / n * 1000, 1),
            "gui_ms": round(w.gui_s / n * 1000, 1), "listeners_ms": round(w.listeners_s / n * 1000, 1),
            "blocked": w.blocked,
            "cycle_ms": round(w.cycle_s / max(w.cycles, 1) * 1000, 1),
            **{f"d_{name}_ms": round(total / n * 1000, 1) for name, total in sorted(w.detail.items())},
        }

    # -- text ---------------------------------------------------------------------

    def summary(self, now: float) -> str:
        """The whole run, for when the show stops."""
        return self._format(self._total, max(now - self._run_start, 1e-9), prefix="Play finished")

    @staticmethod
    def _detail_text(w: _Window, n: int) -> str:
        """' | detail (ms per frame): paint_view 120, overlays 41, ...' - the biggest first."""
        if not w.detail:
            return ""
        parts = sorted(w.detail.items(), key=lambda item: item[1], reverse=True)[:9]
        return " | per frame: " + ", ".join(f"{name} {total / n * 1000:.0f}" for name, total in parts)

    def _format(self, w: _Window, seconds: float, *, prefix: str) -> str:
        if w.shown == 0:
            return f"{prefix}: no frame was shown ({w.steps} steps)"
        n = w.shown

        def ms(total: float) -> str:
            return f"{total / n * 1000:.0f}"

        merged = max(w.steps - w.shown, 0)
        target = f"{1.0 / self._target_interval_s:.1f}" if self._target_interval_s > 0 else "?"
        return (
            f"{prefix}: shown {w.shown / seconds:.1f}/s of {target}/s asked"
            f" | skipped {merged} of {w.steps} steps"
            f" | lag {ms(w.lag_s)} ms = wait {ms(w.wait_s)} + redraw {ms(w.redraw_s)} + queue {ms(w.queue_s)}"
            f"({ms(w.thread_s)} thread + {ms(w.deliver_s)} deliver, {w.blocked * 100 // n}% behind a read-ahead)"
            f" + load {ms(w.load_s)} + prep {ms(w.prep_s)} + gui {ms(w.gui_s)} (of which listeners {ms(w.listeners_s)})"
            f" | step handlers {w.step_s / max(w.steps, 1) * 1000:.0f} ms"
            f" | cycle {w.cycle_s / max(w.cycles, 1) * 1000:.0f} ms (lag {ms(w.lag_s)} + other"
            f" {max(w.cycle_s / max(w.cycles, 1) - w.lag_s / n, 0.0) * 1000:.0f})"
            + self._detail_text(w, n)
        )
