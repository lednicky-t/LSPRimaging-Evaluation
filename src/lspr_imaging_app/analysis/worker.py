"""Background dispatch for analysis compute (sketch §10: "ports
FunctionWorker, never QThreadPool near zarr").

AGENTS.md non-negotiable invariant: never let a ``QThreadPool`` worker
touch, even indirectly via a blocking wait, an OME-Zarr read - root-caused
to a native ``STATUS_HEAP_CORRUPTION`` crash. Use plain ``threading.Thread``
(or a small pool built on it) for anything dataset/zarr-adjacent; the one
safe exception is a single-thread ``QThreadPool`` for HDF5-only writes that
need strict ordering (sketch §4).

Not yet ported. Current implementation (``FunctionWorker``) lives in
``lspr_imaging_app/gui/`` on the ``develop``/``main`` branches.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class AnalysisWorker:
    """Runs queued per-cell compute on a plain ``threading.Thread``, never a
    ``QThreadPool``. Results cross back to the GUI thread via Qt signals
    only (sketch §4). Not yet implemented - scaffolding only."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None

    def submit(self, task: Callable[[], object]) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def cancel(self) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError
