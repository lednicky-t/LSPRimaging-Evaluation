# QThreadPool + zarr: root-causing the STATUS_HEAP_CORRUPTION crash, and the fix

2026-09-08. Follows on from `bulk_analysis_performance_investigation.md`'s
Follow-up #7, which disabled the `zarrs` Rust codec pipeline after a real
launch crashed with Windows `STATUS_HEAP_CORRUPTION`, suspecting `zarrs`'
Rust-side Rayon thread pool colliding with this app's own threading. This
doc root-causes that crash properly: the suspicion was half right, but
pointed at the wrong library. The actual trigger is `QThreadPool` itself
(any codec pipeline), and the fix removes `QThreadPool` from this app's
dataset-touching background dispatch entirely.

## Summary

- **Not `zarrs`-specific.** The crash reproduces identically with zarr-python's
  own default codec pipeline. Disabling `zarrs` alone was not full protection.
- **Root cause, precisely isolated**: a `QThreadPool` worker thread that
  becomes part of a wait/read chain touching a zarr array - either calling
  zarr directly, or merely blocking on a `Future.result()` whose work (on a
  *different*, real thread) touches zarr - reliably crashes the whole
  process with native memory corruption. Nothing short of that combination
  crashes (see the isolation matrix below).
- **Fix**: `gui/worker.py`'s `FunctionWorker` now dispatches via a plain
  `threading.Thread` (`.start()`) instead of being submitted to a
  `QThreadPool`. Every call site updated except one deliberate exception
  (`_measurement_backup_flush_pool`, HDF5-only, kept on `QThreadPool` for
  its serialization guarantee - see "What was deliberately left on
  QThreadPool" below).
- **`zarrs` re-enabled** (`io/dataset.py`'s `_configure_zarr_codec_pipeline`)
  now that the actual trigger is fixed - re-verified clean against the real,
  patched `FunctionWorker` class under the same stress pattern that reliably
  crashed the old `QThreadPool`-based version.
- Full `-k lspri` suite (516 tests) passes both before and after this
  change, plus a standalone `zarrs`-enabled run of every OME-Zarr-touching
  test (56 tests) - see "Verification" below.

## How this was found

A standalone, headless (`QT_QPA_PLATFORM=offscreen`) stress harness - real
`QThreadPool`/`QRunnable`, a real pumped Qt event loop, synthetic zarr data,
no GUI window, no real dataset - run as isolated subprocesses so a native
crash couldn't take anything else down. `echo $LASTEXITCODE` (PowerShell)
was essential: Git Bash mangles native Windows exit codes, and a native
crash raises no Python exception at all, so the only signal is the process's
own exit code (`0xC0000409` / `STATUS_STACK_BUFFER_OVERRUN`, a close relative
of the original `STATUS_HEAP_CORRUPTION` - both are Windows' generic
memory-corruption detectors, not two different bugs).

## The isolation matrix

Each row is a distinct stress-test variant, 15-40s duration, ~200
dispatches/sec, run 2-5 times each:

| Setup | Crashed? |
|---|---|
| `QThreadPool` + zarr read, `zarrs` enabled | Yes, reliably |
| `QThreadPool` + zarr read, `zarrs` **disabled** (zarr-python default pipeline) | **Yes** - not zarrs-specific |
| `QThreadPool` + zarr read, only that one pool running (no second pool at all) | Yes - two racing pools not required |
| Plain Python `ThreadPoolExecutor` + zarr read (no `QThreadPool` anywhere) | No |
| `QThreadPool` + trivial CPU work, no numpy/zarr at all | No |
| `QThreadPool` + real numpy work (big in-memory array, no zarr/disk I/O) | No |
| `QThreadPool` worker **blocks on a `Future.result()`** whose zarr read happens on a *separate* real Python thread (candidate mitigation - didn't work) | **Yes** |
| `QThreadPool` dispatching **trivial** work, while a fully independent `ThreadPoolExecutor` reads zarr concurrently with **no synchronization** between them | No |
| `QThreadPool` worker blocks on a cross-thread `Future` for **trivial** work (`time.sleep`), no zarr anywhere in the process | No - rules out "generic QThreadPool + blocking wait" |
| Plain `threading.Thread` (not `QThreadPool`) doing the zarr read, emitting a real PyQt6 signal back to a GUI-thread-affinity `QObject`, under a live pumped event loop | **No** - the actual fix pattern, confirmed clean |

Reading down that table: the crash needs (a) a `QThreadPool` worker thread,
and (b) that specific thread to be a party - directly or via a blocking
cross-thread wait - to zarr's own execution, regardless of which codec
pipeline. Neither condition alone is sufficient; both together are.

## Why: best available explanation (not fully proven at the C level)

zarr-python 3.x executes synchronous calls (`z[...]`) through an internal
asyncio bridge - a persistent background event-loop thread, with
`run_coroutine_threadsafe`-style dispatch and a blocking wait on the calling
thread until the coroutine resolves. The isolation matrix shows this
specifically needs a `QThreadPool` worker thread on the *calling* side of
that bridge (directly reading, or blocking on a future for a read happening
elsewhere) - not the *presence* of zarr activity elsewhere in the process,
and not blocking waits in general. The likely mechanism: a `QThreadPool`
worker is a native Qt-created OS thread, not a `threading.Thread`-created
one; something about how such a thread interacts with CPython's GIL-state
handling when woken by a *callback from a different thread* (zarr's
background loop thread signaling completion) - as opposed to a natural
timeout or simple polling - plausibly corrupts memory. This was not
confirmed with a native debugger (WinDbg/Application Verifier would be
needed for that); the isolation matrix is strong circumstantial evidence,
not a C-level proof.

## What changed

### `gui/worker.py`: `FunctionWorker` dispatches via `threading.Thread`

`FunctionWorker` is still technically a `QRunnable` (see below for why), but
gained a `.start()` method that spawns a plain `threading.Thread` running
`self.run()` instead of going through `QThreadPool`. `.signals` (a
`WorkerSignals(QObject)`) is unchanged - a cross-thread `.emit()` from a
`threading.Thread` queues safely onto the GUI thread via Qt's own
`AutoConnection`, exactly as it already did from a `QThreadPool` thread, so
every existing `worker.signals.result.connect(...)` callsite needed no
changes.

Two new classmethods replace what callers used to get from
`QThreadPool.activeThreadCount()`/`waitForDone()`:
`FunctionWorker.active_count()` and `FunctionWorker.wait_for_all(timeout_ms)`
- both operate on a class-level tracked set of in-flight threads, added on
`start()` and removed when `run()` finishes.

### Every call site updated except one

All ~15 `window._thread_pool.start(worker)` / `self._thread_pool.start(worker)`
call sites (`image_render_manager.py`, `dataset_controller.py`,
`chromatic_controller.py`, `analysis_controller.py`,
`analysis_worker_mixin.py`, `background_profile_controller.py`,
`mask_controller.py`, `roi_table_controller.py`, `session_state_manager.py`,
`main_window.py`) became `worker.start()`. `main_window.py`'s
`self._thread_pool = QThreadPool(self)` was removed entirely - nothing uses
it anymore - and `_wait_for_background_tasks_before_close` now calls
`FunctionWorker.active_count()`/`.wait_for_all()` instead.

Checked which of these actually touch the dataset (directly, or via a
helper function they call): roughly 10 of 13 original call sites do -
interactive image load, dataset load, the zarr read-time calibration probe,
OME-Zarr export path setup, background-profile estimation, both
chromatic-landmark workers, mask-candidate generation, and both
single-cube/bulk analysis paths. Critically, this includes
`_sensorgram_metric_task` - the outer dispatch for **"Start analysis"
itself** - which blocks on its own internal `ThreadPoolExecutor.future.result()`
for the actual per-wavelength reads, exactly the "candidate mitigation"
pattern the isolation matrix proved still crashes. Bulk analysis runs were
just as exposed as the interactive image loader that actually crashed in
the original incident, not a separate concern. Given `QThreadPool` reuses
its fixed set of worker threads across every submission, and a thread that
ran a zarr-touching task once could later run an unrelated one, there was
no safe way to leave *some* call sites on the shared pool while fixing only
others - the fix had to be all-or-nothing for that shared pool.

### What was deliberately left on `QThreadPool`

`analysis_worker_mixin.py`'s `_flush_measurement_backup_buffers_async`
still dispatches through `window._measurement_backup_flush_pool.start(worker)`
- a *separate*, dedicated, `max_thread_count=1` `QThreadPool`, unchanged.
Two reasons this one call site is safe to leave as-is:

1. It only ever runs HDF5 writes (h5py) - never a zarr read. Not implicated
   by anything in the isolation matrix above; h5py has no comparable
   internal async-execution bridge.
2. Its `max_thread_count=1` exists for a real correctness reason unrelated
   to this crash: serializing backup flushes against each other (so two
   concurrent writes to the same HDF5 dataset can never happen) and
   preserving submission order (cube ordering within each ROI's on-disk
   trace stays chronological). `FunctionWorker.start()`'s unordered raw
   threads don't provide that guarantee - switching this one call site
   would have traded a native-crash risk it never had for a real ordering
   bug it would then have.

`dataset_controller.py`'s OME-Zarr export path was *already* safe before
this investigation, by coincidence: it dispatches via
`threading.Thread(target=worker.run, daemon=True, name="ome-zarr-export")`
directly, for an unrelated reason ("so the export is fully independent of
the GUI thread pool... Qt tasks like image loading keep their pool slots" -
existing comment, predates this investigation). No change needed there;
it's the same pattern `FunctionWorker.start()` now generalizes into the
class itself.

### `io/dataset.py`: `zarrs` re-enabled

`_configure_zarr_codec_pipeline`'s `zarr.config.set({"codec_pipeline.path":
"zarrs.ZarrsCodecPipeline"})` call, commented out since Follow-up #7 of the
bulk-analysis doc, is uncommented again. Re-verified clean (see
"Verification" below) against the real, patched `FunctionWorker` class under
the exact stress pattern that reliably crashed the old `QThreadPool`-based
dispatch, with `zarrs` active.

## Verification

- **Correctness (no `FunctionWorker` behavior change beyond dispatch
  mechanism)**: full `pytest tests/unit tests/integration -k lspri` (516
  tests) passes identically before and after this change.
- **`zarrs` re-enable didn't break anything**: every OME-Zarr-touching test
  (56 tests, `-k "lspri and (zarr or ome)"`) passes with `zarrs` now live by
  default.
- **The actual fix, stress-tested against the real class**: a script
  importing the real, patched `lspr_imaging_app.gui.worker.FunctionWorker`
  (not a mock), `zarrs` enabled, dispatching ~2,500 zarr-reading tasks via
  `FunctionWorker(...).start()` under a live pumped Qt event loop - clean
  across 3 runs, 0 native crashes, 0 Python-level failures. The old
  `QThreadPool`-based dispatch crashed 5/5 times under the equivalent
  synthetic pattern before this fix.

## Checked for the same pattern elsewhere in the repo

Grepped every app for `QThreadPool`/`QRunnable` and checked what each one
actually touches:

- **`apps/sLSPR/acq`**: `QThreadPool` usage is (a) a dedicated
  single-worker "device I/O" lane serializing hardware access (Ocean
  spectrometer, AMF M-Switch SDK, Arduino) - a deliberate design for vendor
  SDK thread-safety, unrelated to this bug and already correctly isolated
  the same way `_measurement_backup_flush_pool` is here; (b) HDF5/archive
  reload tasks (h5py, no async-execution bridge); (c) pure CPU signal
  processing. No library with zarr-python's specific
  persistent-background-event-loop architecture is involved anywhere in
  this app - no evidence of the same risk, though not independently
  stress-tested (no Ocean Insight hardware available in this environment).
- **`apps/LSPRi/acq`** (early-stage, writes OME-Zarr from live acquisition):
  already uses plain `threading.Thread` + `queue.Queue` for its whole
  acquisition pipeline (`sweep_pipeline.py`, comment: "Deliberately
  same-process threading.Thread + queue.Queue, not [QThreadPool]") - already
  clear of this bug by an earlier, independent design decision. Its own
  `QThreadPool` usage (`experiment_control_window.py`) is limited to
  experiment-plan JSON export/import, never the zarr image writer.

The crash's specific trigger (a library with its own internal
asyncio-based background-thread execution bridge, invoked from a
`QThreadPool` worker thread) is architecturally narrow - `zarr-python` is
the only dependency in this repo with that shape. This is not a blanket
"QThreadPool is unsafe" finding; the isolation matrix's clean rows (trivial
work, real numpy work, generic cross-thread waits) all still ran on
`QThreadPool` without incident.

## Worth reporting upstream

This looks like a genuine, previously-unreported bug - no existing
zarr-python or PyQt6/sip issue was found describing this specific symptom
(zarrs-python issue #171 is a related-but-different Linux fork/deadlock
bug, not this one). The isolation matrix above is a clean, minimal,
reproducible bug report: a `QThreadPool` worker thread reading a zarr array
(any codec pipeline) reliably corrupts memory on Windows, while the
identical read from a plain `threading.Thread` does not. Filing this
requires the maintainer's own decision (an external report, potentially
naming this app) - the standalone repro scripts used for this investigation
are not committed to this repo but can be reconstructed directly from the
isolation matrix and this doc.
