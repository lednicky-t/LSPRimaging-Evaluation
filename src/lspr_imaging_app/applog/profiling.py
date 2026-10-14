"""On-demand profiling of the GUI thread (2026-10-14).

`GuiProfile.start()` must be called **on the thread to profile** (the GUI thread, from the
Log panel button): `cProfile` only sees the thread that enabled it. `stop()` returns a
`ProfileResult` with a text table (for the detail pane / the log message), the same
rows as data (for machines) and, when a folder is given, a `.prof` file that `snakeviz`,
`pstats` or `gprof2dot` can open.

Why not always on: `cProfile` slows Python code 2-5x while it runs. Use it after the
stage timings (`playback.summary`, `render.frame`, ...) say *which* stage is slow but not
*why*. `py-spy` is the zero-overhead alternative when the app can be attached to from
outside (`py-spy record --pid PID --duration 20 -o play.svg`).
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PACKAGE_MARKERS = ("lspr_imaging_app", "lspr_ui", "lspr_core", "pyqtgraph")


@dataclass(frozen=True)
class ProfileResult:
    seconds: float
    text: str
    rows: list[dict[str, Any]]
    prof_path: Path | None


def _short_file(filename: str) -> str:
    normalised = filename.replace("\\", "/")
    for marker in _PACKAGE_MARKERS:
        position = normalised.rfind(marker + "/")
        if position >= 0:
            return normalised[position:]
    return normalised.rsplit("/", 1)[-1]


def summarize(profile: cProfile.Profile, limit: int = 25) -> tuple[str, list[dict[str, Any]]]:
    """The *limit* most expensive functions by cumulative time, as text and as rows."""
    stats = pstats.Stats(profile, stream=io.StringIO())
    rows = []
    for (filename, line, name), (_cc, calls, own, cumulative, _callers) in stats.stats.items():  # type: ignore[attr-defined]
        rows.append(
            {
                "function": f"{_short_file(filename)}:{line}({name})",
                "calls": int(calls),
                "own_ms": round(own * 1000, 2),
                "cum_ms": round(cumulative * 1000, 2),
            }
        )
    rows.sort(key=lambda row: row["cum_ms"], reverse=True)
    rows = rows[:limit]
    width = max((len(row["function"]) for row in rows), default=10)
    lines = [f"{'cum ms':>10} {'own ms':>10} {'calls':>8}  function"]
    lines.extend(f"{row['cum_ms']:>10.1f} {row['own_ms']:>10.1f} {row['calls']:>8}  {row['function']:<{width}}" for row in rows)
    return "\n".join(lines), rows


class GuiProfile:
    def __init__(self) -> None:
        self._profile: cProfile.Profile | None = None
        self._started = 0.0

    @property
    def running(self) -> bool:
        return self._profile is not None

    def start(self) -> None:
        if self._profile is not None:
            return
        self._profile = cProfile.Profile()
        self._started = time.perf_counter()
        self._profile.enable()

    def stop(self, save_dir: Path | None = None, name: str = "gui") -> ProfileResult | None:
        if self._profile is None:
            return None
        profile, self._profile = self._profile, None
        profile.disable()
        seconds = time.perf_counter() - self._started
        text, rows = summarize(profile)
        path = None
        if save_dir is not None:
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                path = save_dir / f"{name}.prof"
                profile.dump_stats(str(path))
            except OSError:
                path = None
        return ProfileResult(seconds=seconds, text=text, rows=rows, prof_path=path)
