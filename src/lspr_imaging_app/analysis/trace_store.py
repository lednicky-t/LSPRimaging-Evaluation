"""``TraceStore``: the fitted metric traces and the helper process that fits them, shared by every panel that needs them (2026-11-05).

The metric trace of a ROI (the fitted position of its peak at every cube, in nm) is the expensive part of the Sensorgram and the cheap part of everything
that comes after it (smoothing, baseline, the numbers of an experiment step). It depends only on the scan, the formula and the fit / metric settings
(`scan_trace.trace_key`), so any panel can use a trace any other panel has already fitted. Before this module the Sensorgram kept the traces in a private
dictionary; the ROI / Groups table's step columns needed the same traces for *every* ROI, and two private caches would have fitted everything twice.

**What it holds.** One dictionary ``{roi id: trace}`` for the one key that is current (`scan_trace.trace_key`: scan, formula, fit and metric settings), and
the `TracePool` (the helper process, `trace_pool.py`; None = fit in the calling thread, for tests). The key is the only thing that decides which traces
are valid: asking with another key (`known`) drops the old traces, exactly as the Sensorgram always did, and a result is added only while its key is still
the current one (`add`), so a computation that finished after a setting changed cannot put stale traces back. A new scan file (``results_changed``) empties it.

**Threads.** `known` and `add` are called on the GUI thread only. A client takes a copy with `known`, hands the copy to its worker thread, and calls `add`
with what the worker found when the result is back on the GUI thread. Two clients may fit the same missing ROI at the same time (the user asks for the
table while the Sensorgram is fitting its selection); both get the same numbers and the second `add` just overwrites with equal values, so the only cost is
the duplicated fit. The store keeps no in-flight list: at a few ms per ROI it would add more code than it saves.

**Ownership.** The app makes one store and passes it to the Sensorgram and the step table. A Sensorgram made without one (tests, stand-alone use) makes its own
and shuts it down with itself (`owns`).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping

import numpy as np
from PyQt6.QtCore import QObject

from .scan_results import ScanResultsModule
from .trace_pool import TracePool


class TraceStore(QObject):
    def __init__(self, scan_results: ScanResultsModule | None = None, parent: QObject | None = None, *, in_process: bool = True) -> None:
        """`in_process`: the fits run in the helper process (`TracePool`); False fits in the calling thread (tests that spy on the fit, debugging)."""
        super().__init__(parent)
        self._pool: TracePool | None = TracePool() if in_process else None
        self._key: tuple | None = None
        self._traces: dict[int, np.ndarray] = {}
        self._warm_started = False
        if scan_results is not None:
            scan_results.results_changed.connect(self.clear)  # a different scan: nothing fitted from the old one applies

    # -- the helper process -----------------------------------------------------------------------------------------------

    def pool(self) -> TracePool | None:
        """The helper process the fits run in (None: in the worker thread). Lent to the Spectra panel, which fits its spectra in the same process."""
        return self._pool

    def runner(self):
        """What `scan_trace.compute_traces` takes as ``runner``: the pool's `run`, or None to fit in the calling thread."""
        return None if self._pool is None else self._pool.run

    def warm_up(self) -> None:
        """Start the helper process now (it imports numpy / scipy, a few seconds) instead of at the first fit. Off the GUI thread; safe to call at any time and more than once."""
        if self._pool is None or self._warm_started:
            return
        self._warm_started = True
        threading.Thread(target=self._pool.warm_up, name="trace-pool-warmup", daemon=True).start()

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown()

    # -- the traces -------------------------------------------------------------------------------------------------------------

    def known(self, key: tuple) -> dict[int, np.ndarray]:
        """A copy of the traces valid for `key` (empty after a change of key: the old ones are dropped). GUI thread."""
        if key != self._key:
            self._key, self._traces = key, {}
        return dict(self._traces)

    def add(self, key: tuple, new: Mapping[int, np.ndarray]) -> None:
        """Keep traces a worker finished for `key`; ignored when `key` is no longer the current one. GUI thread."""
        if key == self._key:
            self._traces.update(new)

    def cached(self) -> Mapping[int, np.ndarray]:
        """The traces held now (read-only use: tests, diagnostics)."""
        return self._traces

    def clear(self) -> None:
        self._key, self._traces = None, {}
