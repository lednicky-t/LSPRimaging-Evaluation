"""``AnalysisEngine`` (sketch §7 "Analysis Engine", §10).

Owns the store (§5), the recompute planner (§6), background workers.
Confirmed (2026-09-20, sketch §7): analysis is only ever run by explicit
user action - ``run_analysis(scope)`` is the *only* entry point that
triggers real computation. Selecting/deselecting ROIs or navigating between
panels never implicitly triggers computation.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..diagnostics import instrumented
from .planner import AnalysisScope
from .worker import AnalysisWorker


class AnalysisStatus:
    """What's actually in the store, for the proposed "what's actually in
    the HDF5" indicator. Placeholder shape."""


class AnalysisEngine(QObject):
    """Owns the analysis store and drives (re)computation. Never reacts to
    a ``ComputationalChange`` signal by launching computation itself - it
    only tracks that inputs are stale *for the next run the user asks for*
    (sketch §7)."""

    analysis_progress = pyqtSignal(float)  # batched/coalesced (§8), not per-cube
    store_updated = pyqtSignal()
    analysis_complete = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker = AnalysisWorker()

    # -- query interface ------------------------------------------------

    def get_metric(self, roi_id: int, cube_index: int) -> float | None:
        """Returns ``None`` if not yet analyzed - never computes on read
        (sketch §7). Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def get_spectrum(self, roi_id: int, cube_index: int) -> np.ndarray | None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    def status_summary(self) -> AnalysisStatus:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    # -- the only entry point that triggers computation ---------------------

    @instrumented("AnalysisEngine.run_analysis")
    def run_analysis(self, scope: AnalysisScope = AnalysisScope.ALL_ROIS) -> None:
        """Plan and dispatch recompute for ``scope``. The only method in
        this module (or anywhere else) allowed to trigger real computation
        (AGENTS.md, "What NOT to do without checking in again first"). Not
        yet implemented - scaffolding only."""
        raise NotImplementedError
