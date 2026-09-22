"""Background dispatch for analysis compute (sketch §10: "ports
FunctionWorker, never QThreadPool near zarr").

AGENTS.md non-negotiable invariant: never let a ``QThreadPool`` worker
touch, even indirectly via a blocking wait, an OME-Zarr read - root-caused
to a native ``STATUS_HEAP_CORRUPTION`` crash. Use plain ``threading.Thread``
(or a small pool built on it) for anything dataset/zarr-adjacent; the one
safe exception is a single-thread ``QThreadPool`` for HDF5-only writes that
need strict ordering (sketch §4).

**Built 2026-09-22, deliberately minimal** - not a full port of the old
app's ``FunctionWorker`` (``gui/worker.py``), which is a much larger
QRunnable with progress/partial-result Qt signal plumbing built in. This
class is just the threading primitive: run one callable on a fresh
``threading.Thread``, expose a stable, cooperatively-checked cancel flag.
Progress/result reporting is the caller's job - the task callable itself
(built by ``AnalysisEngine``) can safely call `.emit()` on a Qt signal
directly from this background thread, since Qt queues a cross-thread
`.emit()` onto the receiver's own thread automatically (the same guarantee
``FunctionWorker``'s own docstring documents) - no extra machinery needed
here for that.

Cooperative cancellation only, checked **between cells, never mid-cell**
(the "beyond sketch" idea from this rewrite's `analysis/` design
discussion, 2026-09-22) - a cancelled run always leaves a valid, if
partial, store, since nothing is ever half-written for one cell. This
class only exposes the flag (`cancel_event`); the task callable is
responsible for actually checking it between units of work.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class AnalysisWorker:
    """Runs one task on a plain ``threading.Thread``, never a
    ``QThreadPool``. Only one task may be in flight at a time - matches
    ``AnalysisEngine`` owning a single ``AnalysisWorker`` instance and
    ``run_analysis`` being the only entry point that triggers computation
    (sketch §7)."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        """Public and stable across this worker's whole lifetime (not
        replaced per-``submit``) - a caller can capture it in a task
        closure *before* calling `submit()` and it stays the correct
        object to check across multiple runs. Cleared at the start of each
        `submit()`, not left set from a previous cancelled run."""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, task: Callable[[], object]) -> None:
        """Starts `task` on a fresh ``threading.Thread``. Raises
        ``RuntimeError`` if a previous task is still running - matching
        "only one task in flight," not silently queuing a second one
        against the same shared `cancel_event`."""
        if self.is_running():
            raise RuntimeError("AnalysisWorker already has a task in flight")
        self.cancel_event.clear()
        self._thread = threading.Thread(target=task, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Signals cooperative cancellation - sets `cancel_event`, nothing
        more. Does not block/join, does not kill the thread; the running
        task decides for itself when it's safe to actually stop (between
        cells, per this module's docstring)."""
        self.cancel_event.set()
