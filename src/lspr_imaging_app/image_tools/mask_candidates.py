"""Mask candidates computed off the GUI thread (2026-10-29).

The Mask tab's Threshold and Local contrast tools turn the **raw** image of a frame into a boolean "candidate" (which pixels the tool
selects). That is two to four Gaussian filters over a 1.2 MP plane: 0.2 s to 4 s depending on sigma, far too long for the GUI thread.
Both users of a candidate - the live preview on the image and the Add / Subtract buttons - ask here, so they can never disagree about
which pixels the tool selects, and a candidate the preview already computed is reused (cache hit) by a click on Add.

**Where it runs.** On a plain ``threading.Thread`` (never ``QThreadPool``: the compute may read an OME-Zarr plane, see ``CLAUDE.md``),
one thread per computation. scipy's filters release the interpreter lock, so the GUI thread keeps running (measured 2026-10-29:
longest main-thread stall 8 ms while a worker filtered a 1.2 MP plane). The result comes back through a queued signal and the
callbacks run on the GUI thread.

**Bounded work.** Previews are throw-away: at most one preview computation runs, and a newer preview request waits in a single
"pending" slot (a still newer one replaces it), so typing a sigma never stacks threads. A request that is not a preview (the user
pressed Add) always starts at once.

**Stale results.** ``invalidate()`` (the dataset or the session was replaced) clears the cache and makes every computation still
running deliver ``None`` to its callbacks. A failed computation also delivers ``None`` (logged as an ERROR with the traceback).

The key is a hashable tuple whose first element names the kind (``"relative"``, ``"local_contrast"``, ``"morphology"``): it is the
cache key and the label of the log event ``mask.candidate``. ``cache=False`` skips the cache (morphology: 20 ms, and its result
depends on the mask, which would need a version in the key).

No cancellation inside a computation: one scipy call cannot be interrupted. What can be done is done - a result nobody wants any
more is simply dropped when it arrives.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..applog.events import log_event

logger = logging.getLogger(__name__)

Compute = Callable[[], np.ndarray]
Ready = Callable[[np.ndarray | None], None]

CACHE_SIZE = 6  # one candidate is a full raw plane of booleans (1.3 MB at 1.2 MP)


class MaskCandidates(QObject):
    # Internal: worker thread -> GUI thread. key, generation, result or None, error text or "", seconds.
    _done = pyqtSignal(object, int, object, str, float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: OrderedDict[Hashable, np.ndarray] = OrderedDict()
        self._callbacks: dict[Hashable, list[Ready]] = {}  # computations running now
        self._cached_keys: set[Hashable] = set()  # of the running ones: the result may be cached
        self._preview_key: Hashable | None = None  # the running computation that is a preview (at most one)
        self._pending: tuple[Hashable, Compute, Ready, bool] | None = None  # the newest preview waiting for it
        self._generation = 0
        self._closed = False
        self._done.connect(self._on_done)

    # -- queries ------------------------------------------------------------------------------------------

    def cached(self, key: Hashable) -> np.ndarray | None:
        """The candidate for `key` if it was computed before (and moves it to the front of the cache)."""
        found = self._cache.get(key)
        if found is not None:
            self._cache.move_to_end(key)
        return found

    def is_busy(self) -> bool:
        return bool(self._callbacks) or self._pending is not None

    # -- requests -----------------------------------------------------------------------------------------

    def request(
        self, key: Hashable, compute: Compute, on_ready: Ready, *, preview: bool = False, cache: bool = True
    ) -> None:
        """Deliver the candidate for `key` to ``on_ready`` (on the calling thread at once for a cache hit, otherwise on the GUI thread
        when the computation ends; ``None`` = it failed or was made stale). `compute` runs on a worker thread and must touch no
        module: hand it values, not objects that change."""
        if cache:
            hit = self.cached(key)
            if hit is not None:
                on_ready(hit)
                return
        if key in self._callbacks:  # already being computed: wait for the same result
            self._callbacks[key].append(on_ready)
            return
        pending = self._pending
        if pending is not None and pending[0] == key:  # the same preview was waiting: it is wanted now
            self._pending = None
            self._start(key, pending[1], [pending[2], on_ready], preview=preview and pending[3], cache=cache)
            return
        if preview and self._preview_key is not None:
            self._pending = (key, compute, on_ready, cache)  # replaces an older waiting preview: nobody asks for it any more
            return
        self._start(key, compute, [on_ready], preview=preview, cache=cache)

    def invalidate(self) -> None:
        """The dataset or the session was replaced: nothing computed so far belongs to the new one."""
        self._generation += 1
        self._cache.clear()
        self._pending = None

    def shutdown(self) -> None:
        """Application is quitting: a thread that ends later must not emit into a window Qt is tearing down."""
        self._closed = True
        self._generation += 1
        self._pending = None

    # -- the worker ---------------------------------------------------------------------------------------

    def _start(self, key: Hashable, compute: Compute, callbacks: list[Ready], *, preview: bool, cache: bool) -> None:
        self._callbacks[key] = callbacks
        if cache:
            self._cached_keys.add(key)
        if preview:
            self._preview_key = key
        generation = self._generation

        def run() -> None:
            started = time.perf_counter()
            result: np.ndarray | None = None
            error = ""
            try:
                result = np.asarray(compute(), dtype=bool)
            except Exception as exc:  # noqa: BLE001 - nothing may escape a thread silently
                logger.exception("Computing the mask candidate %r failed", key[0] if isinstance(key, tuple) else key)
                error = f"{type(exc).__name__}: {exc}"
            seconds = time.perf_counter() - started
            if self._closed:
                return
            try:
                self._done.emit(key, generation, result, error, seconds)
            except RuntimeError:  # the object was deleted while the thread ran (the window is closing)
                pass

        threading.Thread(target=run, name="mask-candidate", daemon=True).start()

    def _on_done(self, key: object, generation: int, result: object, error: str, seconds: float) -> None:
        callbacks = self._callbacks.pop(key, [])  # type: ignore[arg-type]
        may_cache = key in self._cached_keys
        self._cached_keys.discard(key)  # type: ignore[arg-type]
        was_preview = key == self._preview_key
        if was_preview:
            self._preview_key = None
        delivered: np.ndarray | None = None
        if generation == self._generation and isinstance(result, np.ndarray) and not error:
            delivered = result
            if may_cache:
                self._cache[key] = result  # type: ignore[index]
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
        log_event(
            logger,
            "mask.candidate",
            logging.DEBUG,
            kind=str(key[0]) if isinstance(key, tuple) and key else str(key),
            ms=round(seconds * 1000.0),
            px=None if delivered is None else int(np.count_nonzero(delivered)),
            stale=bool(generation != self._generation),
            failed=bool(error),
        )
        for callback in callbacks:
            try:
                callback(delivered)
            except Exception:  # noqa: BLE001 - one bad listener must not keep the others (or the waiting preview) from running
                logger.exception("A mask-candidate listener failed")
        if was_preview and self._pending is not None:
            pending_key, compute, on_ready, cache = self._pending
            self._pending = None
            self.request(pending_key, compute, on_ready, preview=True, cache=cache)
