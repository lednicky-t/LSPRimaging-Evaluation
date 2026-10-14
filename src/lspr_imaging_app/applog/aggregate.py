"""``TimingAggregator`` - per-event timing statistics (2026-10-14).

Fed with every entry that carries ``dur_ms`` (the ring buffer does it as entries
arrive); read by the Log panel's Timings tab and by the ``summary`` command line
tool. Percentiles come from a bounded window of the most recent samples per
event, so memory stays flat however long the app runs; count / total / max are
exact over the whole run.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

_WINDOW = 2000


@dataclass(frozen=True, slots=True)
class TimingRow:
    event: str
    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    total_ms: float


class _Stat:
    __slots__ = ("count", "total", "maximum", "samples")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.maximum = 0.0
        self.samples: deque[float] = deque(maxlen=_WINDOW)


def _percentile(sorted_samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_samples:
        return 0.0
    rank = max(math.ceil(fraction * len(sorted_samples)), 1)
    return sorted_samples[min(rank, len(sorted_samples)) - 1]


class TimingAggregator:
    def __init__(self) -> None:
        self._stats: dict[str, _Stat] = {}

    def add(self, event: str, dur_ms: float) -> None:
        stat = self._stats.get(event)
        if stat is None:
            stat = self._stats[event] = _Stat()
        stat.count += 1
        stat.total += dur_ms
        if dur_ms > stat.maximum:
            stat.maximum = dur_ms
        stat.samples.append(dur_ms)

    def reset(self) -> None:
        self._stats.clear()

    def rows(self) -> list[TimingRow]:
        """One row per event, the biggest total time first."""
        out = []
        for event, stat in self._stats.items():
            ordered = sorted(stat.samples)
            out.append(
                TimingRow(
                    event=event,
                    count=stat.count,
                    mean_ms=stat.total / stat.count,
                    p50_ms=_percentile(ordered, 0.50),
                    p95_ms=_percentile(ordered, 0.95),
                    max_ms=stat.maximum,
                    total_ms=stat.total,
                )
            )
        out.sort(key=lambda row: row.total_ms, reverse=True)
        return out
