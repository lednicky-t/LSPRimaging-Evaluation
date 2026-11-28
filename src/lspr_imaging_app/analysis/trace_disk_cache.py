"""A disk copy of the fitted Sensorgram traces, so a relaunch does not fit them again (2026-11-28). Pure: numpy, hashlib and a file; no Qt of its own, no h5py (the log helper it imports brings Qt in, as for ``scan_trace``).

**Why.** After a launch the Sensorgram fits every selected ROI at every cube (160 ROIs x 2655 cubes: 7.7 s of the ~13 s start-up, in the helper
processes). The result is only ``160 x 2655`` numbers (3.4 MB), and it is the same on the next launch as long as nothing it was made from changed.

**What is stored.** One file per session, ``sensorgram_traces.npz``, next to ``scan.h5``: the trace of each ROI (nm, one value per cube), the
**fingerprint of the scan** the traces were fitted from, and the **setup**. It holds one scan and one setup only: a run under another scan or setup
replaces the file's content. It is a speed-up and nothing else: delete it and the next run fits again; ``scan.h5`` (the stored scan) is not touched.

**When a stored trace is used.** Only when both are equal to what this run would fit from:

- the scan: ``scan_trace.scan_fingerprint`` hashes every array a trace is read from (the paired scan's mean values, valid counts, cell hashes, ROI ids, cube
  numbers, and the wavelength axis / band widths / passband moments of a spectral correction). A rescan, a moved ROI, a changed reference pairing or
  spectral correction changes it, and then every ROI is fitted again (only the ROIs asked for; the file is replaced);
- the setup (``setup_key``): formula, fit method and order, fit window, metric and metric range, template cubes, ``CACHE_VERSION`` and a hash of the
  source of the fit modules (``fit_code_fingerprint``), so a changed fit never shows an old number, even when nobody remembered to bump a version.

Why the scan as a whole and not each ROI's arrays: hashing the arrays of 160 ROIs one by one is ~1600 numpy / hash calls, and each one lets go of
the interpreter lock and waits to get it back (up to 5 ms while the GUI thread runs Python): 1.2 s in the app, 23 s beside a busy thread, against
0.35 s quiet. The scan's fingerprint is ~10 big calls, made once per scan object. The price: a rescan of one ROI fits all of them again (a scan
takes minutes; the fits 8 s).

Anything wrong with the file (missing, unreadable, other format, other cube count) means "no cache" with a WARNING when it was damaged; never a wrong number.

**Threads.** ``TraceDiskCache.run`` is called on the Sensorgram's compute thread (the ``TraceRunner`` slot of ``scan_trace.compute_traces``): it asks the
pool only for the ROIs that miss, and writes the file on its own short-lived thread, so neither the window nor the result waits for the disk.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..applog.events import log_event
from .settings import MetricSettings
from .trace_fit import TraceInput

logger = logging.getLogger(__name__)

FILE_NAME = "sensorgram_traces.npz"
FORMAT_NAME = "lspri_sensorgram_traces"
CACHE_VERSION = 2
"""Bump when the cache file's layout changes, or when a change outside ``_FIT_MODULES`` can alter a fitted trace (the modules listed there are covered by their source hash)."""

_FIT_MODULES = (
    "trace_fit", "gaussian_fit", "voigt_fit", "metric_range", "query", "fit_window", "spectral_passband", "template_shift", "settings",
)
"""Every module whose code decides a metric trace (``trace_fit.traces_from_inputs`` and what it calls)."""

_DIGEST_BYTES = 16

Runner = Callable[..., tuple[dict[int, np.ndarray], bool]]


@lru_cache(maxsize=1)
def fit_code_fingerprint() -> str:
    """A hash of the source text of the fit modules (read once per run of the program). A source that cannot be read (a zipped install) stands for itself by name only."""
    h = hashlib.blake2b(digest_size=_DIGEST_BYTES)
    for name in _FIT_MODULES:
        module = importlib.import_module(f"{__package__}.{name}")
        h.update(name.encode())
        try:
            h.update(Path(str(module.__file__)).read_bytes())
        except (OSError, TypeError):
            h.update(b"?")
    return h.hexdigest()


def setup_key(formula_key: str, metric: MetricSettings) -> str:
    """What the fits of a run have in common, as text (the file keeps one). The same fields as ``scan_trace.trace_key`` (the in-memory cache's key) apart from the scan itself."""
    return json.dumps(
        {
            "version": CACHE_VERSION, "code": fit_code_fingerprint(), "formula": str(formula_key),
            "fit": str(metric.fit_method), "order": int(metric.poly_order), "window": list(metric.window_key()), "metric": str(metric.metric_key),
            "range": list(metric.metric_range_key()), "template": list(metric.template_key()),
        },
        default=str, sort_keys=True,
    )


def fingerprint_arrays(parts: Sequence[tuple[str, object]]) -> str:
    """A hash of named arrays (and plain values): equal fingerprint = equal names, dtypes, shapes and bytes. None stands for "absent"."""
    h = hashlib.blake2b(digest_size=_DIGEST_BYTES)
    for name, value in parts:
        if value is None or isinstance(value, (str, int, float, tuple)):
            h.update(f"{name}={value!r};".encode())
            continue
        a = np.ascontiguousarray(value)  # (a copy only for a strided view; the stored arrays are contiguous)
        h.update(f"{name}:{a.dtype.str}{a.shape};".encode())
        h.update(memoryview(a).cast("B"))
    return h.hexdigest()


def read_entries(path: Path, setup: str, scan: str) -> dict[int, np.ndarray]:
    """``{roi id: trace}`` of the file when it was written for this ``setup`` and ``scan`` fingerprint; empty for no file, another setup, scan or format, or a damaged file (WARNING)."""
    if not path.is_file():
        return {}
    try:
        with np.load(path, allow_pickle=False) as f:
            if str(f["format"]) != FORMAT_NAME or int(f["version"]) != CACHE_VERSION:
                return {}
            if str(f["setup"]) != setup or str(f["scan"]) != scan:
                return {}
            roi_ids, traces = f["roi_ids"], f["traces"]
            if traces.ndim != 2 or len(roi_ids) != len(traces):
                raise ValueError("array shapes do not agree")
            return {int(r): traces[i] for i, r in enumerate(roi_ids)}
    except Exception as exc:  # noqa: BLE001 - a damaged cache must never stop the Sensorgram: it is fitted again, and the file replaced
        log_event(logger, "sensorgram.trace_cache_unreadable", logging.WARNING, path=str(path), reason=f"{type(exc).__name__}: {exc}")
        return {}


def write_entries(path: Path, setup: str, scan: str, entries: Mapping[int, np.ndarray]) -> None:
    """Write the file whole and put it in place in one step (a crash or a full disk leaves the old file)."""
    ids = sorted(entries)
    n_cube = len(entries[ids[0]])
    traces = np.vstack([np.asarray(entries[r], dtype=np.float64).reshape(1, n_cube) for r in ids])
    tmp = path.with_name(path.name + ".tmp.npz")
    try:
        np.savez(tmp, format=np.array(FORMAT_NAME), version=np.array(CACHE_VERSION), setup=np.array(setup), scan=np.array(scan), roi_ids=np.asarray(ids, dtype=np.int64), traces=traces)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class TraceDiskCache:
    """The disk copy for the traces of one scan. ``set_path`` says where (None: no cache); ``run`` wraps the pool's ``run``."""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._write_lock = threading.Lock()
        self._writers: list[threading.Thread] = []
        self._latest = 0  # numbers the writes; one that is no longer the newest is skipped (the file only ever needs the last state)

    def set_path(self, path: Path | None) -> None:
        self._path = path

    def path(self) -> Path | None:
        return self._path

    def run(
        self,
        pool_run: Runner,
        jobs: Sequence[tuple[int, TraceInput]],
        formula_key: str,
        metric: MetricSettings,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[dict[int, np.ndarray], bool]:
        """Same contract as ``TracePool.run`` / ``scan_trace.TraceRunner``: the traces of ``jobs`` and whether the run was cancelled. Stored traces for the ROIs
        in the file; ``pool_run`` fits the rest, and the file is brought up to date afterwards. Jobs without a scan fingerprint (``TraceInput.scan_digest``,
        set by ``scan_trace.trace_input``) are not cached."""
        path = self._path
        scans = {data.scan_digest for _, data in jobs}
        if path is None or not jobs or len(scans) != 1 or None in scans:
            return pool_run(list(jobs), formula_key, metric, cancelled, progress)
        scan = next(iter(scans))
        t0 = time.perf_counter()
        setup = setup_key(formula_key, metric)
        stored = read_entries(path, setup, scan)
        n_cube = int(jobs[0][1].sample.shape[0])
        hits = {r: stored[r] for r, _ in jobs if r in stored and len(stored[r]) == n_cube}
        misses = [job for job in jobs if job[0] not in hits]
        t_read = time.perf_counter()
        total = len(jobs)
        if progress is not None and hits:
            progress(len(hits), total)

        def shifted(done: int, _total: int) -> None:  # the pool counts only its own ROIs
            if progress is not None:
                progress(len(hits) + done, total)

        fitted: dict[int, np.ndarray] = {}
        was_cancelled = False
        if misses:
            fitted, was_cancelled = pool_run(misses, formula_key, metric, cancelled, shifted)
        log_event(
            logger, "sensorgram.trace_cache", logging.DEBUG, rois=total, hits=len(hits), fitted=len(fitted), cancelled=bool(was_cancelled),
            read_ms=round((t_read - t0) * 1e3, 1), file_matched=bool(stored),
        )
        if fitted:
            # The file's other ROIs stay (same scan and setup, if their length still fits); a ROI fitted now replaces its old entry.
            kept = {r: v for r, v in stored.items() if len(v) == n_cube}
            kept.update({r: np.asarray(t, dtype=np.float64) for r, t in fitted.items()})
            self._write_later(path, setup, scan, kept)
        return {**hits, **fitted}, was_cancelled

    def _write_later(self, path: Path, setup: str, scan: str, entries: dict[int, np.ndarray]) -> None:
        self._latest += 1
        number = self._latest
        writer = threading.Thread(target=self._write, args=(number, path, setup, scan, entries), name="trace-cache-write", daemon=True)
        self._writers = [w for w in self._writers if w.is_alive()] + [writer]
        writer.start()

    def flush(self, timeout: float = 10.0) -> None:
        """Wait until the writes started so far are done (tests, and anything that must see the file)."""
        for writer in list(self._writers):
            writer.join(timeout)

    def _write(self, number: int, path: Path, setup: str, scan: str, entries: dict[int, np.ndarray]) -> None:
        with self._write_lock:
            if number != self._latest or not entries:
                return
            t0 = time.perf_counter()
            try:
                write_entries(path, setup, scan, entries)
            except Exception as exc:  # noqa: BLE001 - only a speed-up: a failed write is said and the Sensorgram carries on
                log_event(logger, "sensorgram.trace_cache_write_failed", logging.WARNING, path=str(path), reason=f"{type(exc).__name__}: {exc}")
                return
            log_event(logger, "sensorgram.trace_cache_written", logging.DEBUG, rois=len(entries), write_ms=round((time.perf_counter() - t0) * 1e3, 1))
