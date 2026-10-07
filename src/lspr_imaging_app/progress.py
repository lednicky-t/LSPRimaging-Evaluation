"""Progress reporting and cancellation for heavy work (rule: CLAUDE.md,
"Heavy work reports progress and can be cancelled"). Qt-free on purpose, so
the pure computation modules that use it stay testable without a
`QApplication`.

A heavy function takes ``progress: ProgressCallback | None`` and
``cancelled: Callable[[], bool] | None`` and reports through a
`StageProgress`: each named stage owns a share of the overall 0..1 bar,
weighted by how long that stage really takes (measure it, don't guess).
`StageProgress.__call__` is also the single place cancellation is checked,
so a stage cannot forget to be cancellable: every report is a checkpoint.

The Qt side (a module re-emitting this as a typed signal, a panel showing it)
lives with the feature that owns the task - see
`image_tools/chromatic_auto_task.py` for the first one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

ProgressCallback = Callable[[float, str], None]
"""``callback(overall_fraction_0_to_1, short_message)``."""


class Cancelled(Exception):
    """Raised at a progress checkpoint when the caller's `cancelled()` is
    true. Not an error: callers catch it and report "cancelled"."""


class StageProgress:
    """Maps ``(stage, fraction within stage)`` to one overall fraction.

    `weights` is an ordered mapping stage name -> share of the bar; shares
    are normalised, so they need not sum to exactly 1."""

    def __init__(
        self,
        weights: Mapping[str, float],
        callback: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        total = float(sum(weights.values())) or 1.0
        self._callback = callback
        self._cancelled = cancelled
        self._share: dict[str, float] = {}
        self._start: dict[str, float] = {}
        running = 0.0
        for name, weight in weights.items():
            self._start[name] = running
            self._share[name] = float(weight) / total
            running += self._share[name]

    def __call__(self, stage: str, fraction: float, message: str = "") -> None:
        if self._cancelled is not None and self._cancelled():
            raise Cancelled()
        if self._callback is not None:
            clipped = min(max(float(fraction), 0.0), 1.0)
            self._callback(self._start[stage] + self._share[stage] * clipped, message or stage)
