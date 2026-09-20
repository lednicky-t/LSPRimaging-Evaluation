"""The ``@instrumented`` decorator and shared diagnostics signal hub.

Every rewritten module's public mutating methods go through this so
performance visibility is built in from the first commit rather than
retrofitted after a slowdown report (AGENTS.md, "Communication" section;
docs/rewrite_architecture_sketch_2026-09.md §3, "Built-in diagnosability,
for free"). This one is fully implemented (not a stub) - it's small,
self-contained infrastructure with no dependency on any other new module,
so there's nothing to leave as a placeholder.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, TypeVar

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., object])


class DiagnosticsHub(QObject):
    """Publishes one event per ``@instrumented`` call.

    A future built-in performance panel subscribes to this (sketch §3);
    nothing subscribes yet - this is the hub the panel will attach to.
    """

    event_recorded = pyqtSignal(str, str, float, float)  # module, method, duration_ms, timestamp

    def record(self, module: str, method: str, duration_ms: float, timestamp: float) -> None:
        self.event_recorded.emit(module, method, duration_ms, timestamp)


# One process-wide hub, matching the sketch's "lightweight Diagnostics.event_recorded
# signal" description - modules import this instance rather than constructing their own.
diagnostics_hub = DiagnosticsHub()


def instrumented(label: str) -> Callable[[_F], _F]:
    """Time ``func``, log one structured DEBUG line, and publish the result
    on :data:`diagnostics_hub`.

    ``label`` should be ``"ModuleName.method_name"`` (e.g.
    ``"RoiToolbox.move_roi"``, matching sketch §3's example) so the log line
    and the diagnostics event both carry an unambiguous origin.
    """

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000.0
                logger.debug("%s | %.3fms", label, duration_ms)
                module_name = label.split(".", 1)[0]
                diagnostics_hub.record(module_name, label, duration_ms, time.time())

        return wrapper  # type: ignore[return-value]

    return decorator
