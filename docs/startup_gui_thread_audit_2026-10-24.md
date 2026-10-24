# Startup: what runs on the GUI thread (2026-10-24)

Question: which launch steps block the GUI thread, and which can move to a background thread? Evidence: log
`logs/lspri-20261024-134353-12784.jsonl` (real dataset, 314 cubes x 27 wavelengths, 8478 planes, OME-Zarr on the Desktop), plus two
read-only measurements. Nothing was changed.

## Timeline of one real launch (seconds since logging started; imports happen before that and are not in the log)

| t (s) | what | thread |
|---|---|---|
| 0.09 -> 4.39 | `build_main_window`: every panel, ribbon, dock, menu is built | GUI (cannot move: Qt widgets must be created on the GUI thread) |
| 4.4 -> 5.6 | folder scan (`dataset.scan`, 1.2 s) | worker (already) |
| 5.67 -> 9.79 | **nothing is logged on the GUI thread: it is blocked ~4.1 s** (session-load task reports 3 ms only because its first signal waited in the queue) | GUI |
| 6.5 | scan.h5 read (0.8 s) | worker (already) |
| 9.8 -> 10 | first image (`render.frame` 100 ms) | render thread (already) |
| 10.2 -> 15.7 | Spectra (3.7 s, waiting on a 3.2 s cold plane read) and Sensorgram (5.5 s) | workers (already) |
| ~10 -> 18.1 | `analysis.scan_checked` 8.0 s (staleness hashes of 314 cubes x 160 ROIs) | worker (already), slowed by GIL competition |

## The one real offender

`dataset_file_stats` (`dataset/io.py`), called from `DatasetModule.dataset_file_stats` by `DatasetInfoBar._on_loaded` (and the Workflow
dataset summary) in the `dataset_loaded` handler, **on the GUI thread**. It walks every file under the OME-Zarr (8905 files, 10.5 GB) to
get the size shown in the menu-bar row ("OME-Zarr · 10.5 GB · C/WL ..."). Measured read-only: 3.25 s and 3.69 s on two repeats (so it is
not only cold disk; thousands of tiny directories cost ~0.4 ms each on Windows). That accounts for ~3.5 of the 4.1 s stall; the
remaining ~0.6 s is not attributed (other `dataset_loaded` handlers; not instrumented). It is cached per dataset, so it is paid once per load.

## Not movable / not worth it

- `build_main_window` (4.3 s real, ~1.8 s in a headless profile): widget construction. Breakdown (headless cProfile): Image panel
  `_build_ui` 0.77 s (ribbons/toolbar), dock containers 0.6 s (`_apply_frame_style` 0.43 s), Log console 0.14 s. Could be *shortened*
  (lazy-build ribbon tabs not shown yet), not moved.
- Python imports before `main()` (~4 s with `-X importtime`; `analysis.engine` 1.3 s, `panels.histogram` 0.7 s, `analysis.scan_jobs`
  0.6 s, `lspr_core.perf` 0.4 s). Could be moved by showing a window/splash first and importing the heavy analysis modules on a thread;
  needs care (imports must finish before first use).

## Second step (2026-10-24): launch screen + stage timing of the window build

- `launch.py` `LaunchSplash`: a small dark card with the app name and one line ("Loading modules...", "Building the Image panel...",
  "Opening the last dataset..."). `src/main_rewrite.py` creates the `QApplication` and the card **before** importing `app_rewrite` (that import
  is ~3.5 s), `app_rewrite.main(app, splash)` closes it when the window is shown. Does not shorten the launch, only the empty wait (~8 s
  with nothing on screen before). Never raises (a courtesy, not a requirement). It uses fixed dark colours: it is shown before the theme is read.
- `launch.py` `StageClock`: `build_main_window` now logs one `app.build_stage` DEBUG event per step (`stage`, `ms`) plus `stage=total`.
  **This is the measurement for the unexplained 4.3 s** (a headless profile only reproduced ~1.8 s): read it after the next real launch with
  `python -m lspr_imaging_app.applog grep logs/<newest>.jsonl app.build_stage`. Decide what to shorten (lazy ribbon tabs, one stylesheet
  instead of many `setStyleSheet` calls, icon cache) from those numbers, not before.
- Not done yet: "image first" ordering (start Spectra / Sensorgram / scan check only after the restored cube is drawn; hypothesis: the 3.2 s read
  of cube 244 was starved by those threads, not slow disk since its neighbours read in 30-40 ms), the 8 s scan staleness check, lazy imports.
  Tests: `tests/integration/test_lspri_launch_screen.py`.

## Third step: the first launch with the fixes (log `lspri-20261024-141548-29540.jsonl`) and "image first"

Same dataset, same machine, but a warm file cache (so some of the gain is not ours: compare with care).

| | before | after |
|---|---|---|
| `build_main_window` | 4.3 s | 1.24 s (Image panel 0.52, dock layout 0.35, Log console 0.12, ROI/Spectra/Sensorgram 0.11, rest < 0.06 each) |
| dataset found -> GUI free | 4.1 s stall | none (`dataset.file_stats` now runs on its thread) |
| first image on screen | 9.9 s | 2.9 s |
| ready (`app.startup_done`) | 18.6 s | 11.0 s |
| restored cube 244 drawn | 13.7 s | 6.0 s |
| `dataset.file_stats` | 3.5 s on the GUI thread | 10.3 s on its thread (shared disk and GIL; "..." shows meanwhile, not on the ready path) |

**What still looks wrong.** Plane reads of 4 MB took 1.4 s, 1.9 s, 2.5 s, 2.9 s, 2.4 s between 3.0 and 7.6 s and 110-160 ms from 7.6 s on, i.e.
exactly when `sensorgram.compute` (4.3 s) ended. The scan check (until 10.4 s) and the file count (until 12.5 s) kept running and did not
slow the reads: so the Sensorgram fit is the thread that starves the reads (GIL hand-overs), not disk or the other two. Same pattern in the
first log (3.2 s read).

**Change made ("image first").** `ImagePanel.render_pending()` (a frame requested and not yet drawn, including the 100 ms coalescing pause);
`panels/image_first.py` `ImageFirst` (a head start, never a lock: gives up after 5 s so Play cannot hold a computation back for good);
`SensorgramPanel(image_busy=...)` asks it before starting a fit and retries on its own coalescing timer. Only the Sensorgram is gated: the
Spectra compute is 35 ms. Tests: `tests/unit/test_lspri_image_first.py`, `test_the_fit_waits_while_the_image_is_being_drawn_and_then_starts` in
`tests/integration/test_lspri_sensorgram_panel.py`. Expected: cube 244 drawn at ~3.2 s instead of 6.0 s; the Sensorgram then takes its 4 s after
that, so "ready" is bounded by the 7 s scan check (~10 s). To confirm in the next log: `dataset.plane_read` of `244.*` back to ~100 ms.

**Still open.** The 7 s scan staleness check is now the longest step on the ready path (correctness-sensitive: check in before changing).
A global `sys.setswitchinterval` below the 5 ms default would also help reads under any fit (affects every thread: measure first).
Spectra task entries lasted 3.4 s although `spectra.compute` took 35 ms; its `_on_ready` seems to run late (GUI thread starved by the same fit?):
not investigated.

## Outcome of the first step (done 2026-10-24, maintainer's go-ahead)

`DatasetModule.dataset_file_stats(dataset)` no longer blocks: the first ask starts the count on a plain `threading.Thread`
(`dataset-file-stats`) and returns **None until it is in**; `file_stats_ready(dataset)` is then emitted on the GUI thread and every later
ask returns the cached value. A count that outlives its dataset (cleared or replaced) is dropped; a failing count ends as `(0, None)` and is
logged. `DatasetInfoBar` and `DatasetSummary` show "..." for size / date until the signal arrives (`_show_size_and_date`). Log event
`dataset.file_stats` (ms) records the count. Tests: `tests/integration/test_lspri_dataset_file_stats_async.py`.

Not yet confirmed on the real launch (needs the maintainer's next start): the log should show the GUI-thread gap after `dataset.loaded`
shrink from ~4 s to well under 1 s. If a gap remains, it is the unattributed ~0.6 s in the other `dataset_loaded` handlers
(`DatasetInfoBar.refresh` -> `timed_image_count` -> `compact_dataset_image_timings` is the next suspect; not measured).
