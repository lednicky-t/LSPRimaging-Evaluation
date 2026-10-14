# Slideshow preview test `testing7` - analysis (2026-10-14)

Log: `logs/testing7.jsonl` (13 294 records, 10 min, dataset: 8478 planes, 314 cubes x 27 wavelengths,
LZW TIFFs read as float32, 900x1300 raw / 769x1239 shown). Analysed with `python -m lspr_imaging_app.applog summary`
plus ad-hoc scripts over the JSONL. Benchmarks below are standalone scripts on the real data (not the GUI); they
lived in the session scratchpad and were not kept (re-create from the descriptions here if needed).
Anything marked *estimate* has not been measured in the app yet.

## 1. What the log says

Four Play runs. Only the **free-running** stretches say anything about speed; in 233-313 s the cycle is a
constant ~169 ms with `blocked=0` - that is the speed setting (interval), the machine kept up. (The log does
not record the speed setting - see section 5.)

| Stretch | fps | cycle | what limits it |
|---|---|---|---|
| 181-233 s, no background | 14-18 | 55-72 ms | render thread (below) |
| 313-320 s, flatten background on | 10-11 | 90-105 ms | render thread: `prep_ms` 35 ms |
| 320-350 s, flatten off, chromatic toggled | 16-19 | 55-65 ms | render thread (below) |
| 583-611 s (3rd run) | 15-17 | 58-65 ms | render thread (below) |

### The free-running cycle (~57 ms) is fully serial. Where the time goes (run 1 summary)

wait 2.6 + redraw 6.3 (overlays, before submit) + **thread busy 29.1** + render ~10 (load 1 + prep 1.7 + picture ~8)
+ deliver 0.4 + gui 5.2 = ~54 of 57 ms.

**`thread_ms` (29 ms, half the cycle) = the request waits for the render thread to finish a read-ahead plane
read.** `blocked == shown` in every free-running window: *every* request arrives while the thread is in the middle
of reading a plane for a later frame (a read cannot be interrupted). One plane read per frame (2015 reads for
1995 frames), one thread: read + render >= ~49 ms per frame, so ~20 fps is the ceiling of the design.

### Why a read costs 36 ms (median 35.6; p95 44) when decoding is ~12-14 ms

- Same loader, same files, idle app (first 31 reads of the session): **median 21.3 ms**. During Play: **35.6 ms**.
- Standalone: raw `imagecodecs.tiff_decode` 11.5-13.9 ms; the app loader (open + decode + float32) 20 ms idle.
- `bench_switch_interval.py` (scratchpad; GUI-like thread = 26 ms of GIL-holding work per 57 ms cycle): the
  loader takes **34.6-36 ms** at Python's default GIL switch interval (5 ms) - the log's number - and
  **~20 ms at 0.5 ms**. The GUI-like thread's wake-up lateness did not change (0.3 ms median, 0.6 p90).
- Mechanism: each time the read thread comes back from native code (file read, decode, astype) it must take the
  GIL; while the GUI thread runs Python or a long Qt paint it asks for a hand-over only after 5 ms. Three or four
  re-acquisitions per read = +15 ms. It is not disk (0.46 MB per file; OS-warm 12.3 ms vs cold 11.5 ms).
- The same inflation shows on the **histogram worker**: `compute.py` documents ~24 ms standalone, the log has
  58-60 ms (306 computes). And in the **Handler lock** in the GUI profile (`logging/__init__.py:handle` self 1.4 %).

### Decoding scales across threads (`bench_parallel_read.py`, disjoint files per trial)

serial 11.5 ms/plane; 2 threads 6.6 ms/plane (1.75x); 3 and 4 threads 6.6-6.8 (no further gain).
Playback needs ~17-35 planes/s, i.e. one or two reader threads are plenty.

### GUI thread (10 s profile during run 3; 44 % in Python, 56 % event loop / Qt)

Not the bottleneck at 17 fps, but it becomes it after the render-thread fixes (~27 ms per frame:
paint 14 + redraw 6.3 + gui 5.2 + step ~1).

| Item | per frame (est. from profile) | note |
|---|---|---|
| `group_label_overlay.paint` | ~5.7 ms (9.4 %) | per paint event: `_fit` (QFont + QFontMetricsF loop) + wrap + halo `drawText` per entry; nothing changes between frames |
| `circle_points` + `circle_outlines` + `sample_circles_item.paint` | ~2.8 + 1.2 + 3.3 ms | redrawn every frame because the chromatic affine is part of the draw key (`panel.py:1855`); paint = 2 Python->Qt calls per ROI |
| `_update_highlight_overlay` | ~3.3 ms (5.3 %) | `(image>=lo)&(image<=hi)` + `np.any` + `np.all(mask[np.isfinite(image)])`: ~6 passes over 1.2 MP on the GUI thread |
| `no_data_overlay_rgba` | ~1 ms | `~np.isfinite` + `.any()` + `np.array_equal` each frame; the render thread already computes `has_value` |
| log records on the GUI thread | ~1.4 ms (3.2 %) | 1.4 records/frame, ~1 ms each (handler lock waits, see above) |

### Log noise

`dataset.plane source=cache` = 6 097 of 13 294 records (46 %, 3.06 per frame: the render read + 2-3 read-ahead
hits). It is produced by comparing `cache_info().hits` before/after (two lock takes; racy once two threads read).

### Flatten background (`bench_background_prep.py`, 160 ROIs, real frames)

Idle process, sigma 48 / bin 8: **34.1 ms** (log: 34.9 ms in 313-320 s - consistent). Of that: ROI exclusion loop
7.6 ms, whole exclusion mask 11.5 ms, blur+resize 13.8 ms. The ROI part is identical for every frame while the
ROIs do not change. The log also has windows at 46-48 ms and 73-84 ms prep: other settings (not logged); a large
`exclusion_dilation_px` (`ndimage.binary_dilation(iterations=n)` on a 1.2 MP mask) is the first suspect for the
76 ms one. Bin 1 costs 222 ms (the UI limits bin to sigma/6).

## 2. Recommended order (all priority 5 "performance"; none changes a pixel value)

1. **`sys.setswitchinterval(0.0005)` at startup** (one line in `app_rewrite.main`, env override for A/B).
   Reads 36 -> ~21 ms, histogram worker and logging also faster. *Estimate:* cycle 57 -> ~44 ms (17.5 -> ~22 fps).
   Risk: slightly more GIL hand-offs (a few % CPU overhead with two busy Python threads); measure in the app.
2. **Read-ahead off the render thread** (a process-wide pool of 1-2 plain `threading` readers in `dataset/io.py`,
   not owned by the panel). The render request then finds its plane in the cache: the 29 ms wait disappears.
   *Estimate:* cycle -> ~30 ms (GUI-bound, ~35 fps). Restrict to TIFF stacks first (zarr threading history:
   `qthreadpool_zarr_crash_investigation.md`). **Trap to respect:** a separate prefetch thread class made
   `test_lspri_rewrite_histogram_panel.py` segfault (memory `lspri-image-refresh-speedups-2026-10-11`); run that
   file 4x after the change. A global pool avoids panel teardown involvement, but the cause was never found.
3. **In `_redraw`, submit the render request before drawing overlays** (`panel.py:1544-1599`): the request
   currently waits ~6 ms for the ROI/crop/scale-bar drawing. Free, *estimate* -6 ms.
4. GUI thread, each independent: highlight via `cv2.inRange` + `countNonZero` (or on the render thread);
   no-data flag from the render thread; group-label layout/pixmap cache; one `QPainterPath` per colour for the
   circles (or translate instead of recompute when only the chromatic shift changed); drop the per-hit
   `dataset.plane` record.
5. Background: cache the ROI exclusion mask keyed on (shape, ROI centres/diameters); *estimate* 34 -> ~26 ms.
   Bit-identical by construction, still gets a before/after check (`.claude/rules/numerics.md`).

### Status (2026-10-14, same day)

- **Step 1 done, uncommitted:** `gil_tuning.py` (`apply_switch_interval`, called from `app_rewrite.main`), 0.5 ms by
  default, `LSPR_GIL_SWITCH_MS=5` restores Python's default for an A/B run. Logs `app.gil_switch_interval` at start.
  Tests: `tests/unit/test_lspri_rewrite_gil_tuning.py` (umbrella repo).
- **Step 3 done, uncommitted:** `ImagePanel._redraw` sends the render request first, then draws overlays and the mask
  tint. `PlaybackStats` "redraw" now ends at the submit (it will drop by about the old `overlays` time, ~6 ms);
  the overlay drawing is still reported as the `overlays` / `mask` details.
- Tests run: gil_tuning, slideshow, image_refresh_speedups, image_panel (104), crop, rotate, roi_overlay_controls,
  histogram_panel x3: all pass. **Not yet measured in the app** - needs the same slideshow test.
- Steps 2, 4, 5: not started.

### First run after steps 1 + 3 (session `lspri-20261014-182741-60288`, 10x speed = 33.7 ms asked; 1x = 337 ms here)

| | testing7 | after 1 + 3 |
|---|---|---|
| `dataset.plane_read` median during Play | 35.6 ms | **23.4 ms** |
| `thread_ms` (request waits for the render thread) | 29 ms | **~5 ms** |
| `redraw_ms` | 6.3 ms | **0.5 ms** |
| `deliver_ms` (finished frame waits for the GUI thread) | 0.4 ms | **~25 ms** |
| fps / cycle | 17.5 / 57 ms | **18.3 / 54 ms** |

The render side got much faster and the frame rate did not move: **the GUI thread is now the limit** (per frame: paint
~17 ms, overlays ~8.8 ms (was 6: they now overlap the render thread), gui ~5.7 ms, step handlers ~1.2 ms, plus Qt
work with no Python on top: the profile says 56 % of GUI time is "event loop / native"). Steps 2 and 3 only pay off
once the GUI-thread items (section 1 table, step 4) are cut. Step 4 is therefore the next one, not step 2.

### The crash (same run, 62.5 s after launch, exit code 0xC0000005)

- `logs/lspri-20261014-182741-60288.fault.log`: access violation on the **GUI thread**, innermost Python frame
  `sample_circles_item.py:168` (`painter.drawPolyline`), called from `GraphicsView.paintEvent`. Windows event:
  `Qt6Gui.dll` 6.11.1, offset 0x1bf71c = **`QRasterPaintEngine::penChanged` + 0x1c** (nearest export): the first
  instructions of `penChanged` read the engine's state, so the painter / paint engine was already invalid.
- It happened 23 frames into the second Play (~51 s), while the Log panel's GUI profiler (`StackSampler`) thread was
  running (it is in the fault dump) and the console DEBUG output was on. No Python exception, nothing in the log.
- testing7's session (57 min, 3 Plays, a profile run, 5 ms interval) had no crash. Evidence is one crash, so **not
  proven** to be caused by steps 1/3 - but both are new, and step 1 changes thread interleaving.
- Suspected mechanism (a known PyQt hazard; pyqtgraph ships `pyqtgraph.util.garbage_collector.GarbageCollector` for it):
  Python's cyclic garbage collector runs on *whichever thread allocates*; if it frees a Qt object (a graphics item, a
  pen, a pixmap wrapper in a reference cycle) on the render / histogram / log thread while the GUI thread is between
  two Qt calls in a paint, Qt crashes. The app has no such guard (`grep` finds `gc.disable` only in the old
  analysis code). A 0.5 ms switch interval lets other threads run between two Qt calls of a paint ~10x more often.
- Done: step 1 is **opt-in again** (`LSPR_GIL_SWITCH_MS=0.5`), default = Python's 5 ms, unchanged from testing7.
  Step 3 (reorder) kept; if a crash recurs at the default interval, revert step 3 first.
- Proposed (needs a go-ahead, threading): (a) log every GC pass with the thread it ran on and its duration
  (`gc.callbacks`, DEBUG, gen 2 only) to see whether GC really runs on worker threads; (b) if so, run the collector
  only on the GUI thread (pyqtgraph's `GarbageCollector`, 1 s timer).

## 3. Diagnostics to add (so the next log answers more)

- `playback.summary` / `playback.window`: add the target interval (`_target_interval_s` is already held) and the
  slideshow settings (speed, mode, thin outline, calm histogram).
- Log the settings in `BackgroundModule.set_flatten_background_settings`, `ChromaticModule.set_correction_enabled`
  (`@instrumented` records only the duration, not the arguments), and whether the Histogram panel is visible/calm.
- Qt warnings: attach `traceback.format_stack()` in the Qt message handler so
  `QFont::setPointSize: Point size <= 0 (-1)` (once, at 48.65 s, no pointSize() read in the code base - the source is
  not obvious) can be traced to a Python line.

## 4. Not a problem in this log

Histogram (off-thread, 5.4 ms draw on the GUI thread, ~1.3 ms redraw); `prepare_frame_display` (OpenCV,
~9-10 ms, GIL-inflated but off-thread); `next_frame` planning (0.1 ms); chromatic toggle (11 ms, once);
`dataset.scan` 2.35 s (once, off-thread).

## 5. How to verify after a change

Same slideshow test, free-running speed, no background: compare `playback.summary` `thread_ms`, `cycle_ms`,
`fps` and the median `dataset.plane_read` (expect ~21 ms instead of ~36 ms for step 1). Baseline: fps 17.5,
cycle 57 ms, thread_ms 29, read 35.6 ms.

### Step 4, first part (done 2026-10-14, uncommitted; awaiting testing8)

- **Highlight tint** (`panel._update_highlight_overlay`, `overlay_tint.rgba_layer`): dropped the redundant
  `selection_mask[np.isfinite(image)]` test (a ~1 MP copy; already decided from the frame's min / max whenever those
  are known: all finite pixels lie between them) and build the layer with `np.where` when more than 10 % of the
  pixels are selected (indexed write below that). Output is byte-identical (88-case comparison on real frames incl.
  NaN, +-inf, values exactly on the limits; `test_lspri_rewrite_overlay_tint_layer.py`). Standalone, real 1.2 MP
  frame: 6.1 -> 3.8 ms (5 % selected), 13.5 -> 7.5 ms (40-60 %), 15.3 -> 7.2 ms (60-95 %). My earlier OpenCV idea
  (`cv2.inRange`) was *not* faster: on this machine every pass over ~1 MP costs 1-2 ms, mostly fresh-memory
  allocation, so fewer passes / fewer temporaries is what counts.
- **No more `dataset.plane source=cache` records** (io.load_image_array): 46 % of the log, also removes two cache-lock
  takes per read.
- **GC trace** (`applog/gc_trace.py`, installed by `configure_logging`, flushed every 5 s from a GUI timer in
  `app_rewrite.main`): DEBUG record `gc.summary` with `collections, full, off_main, off_main_threads, total_ms,
  max_ms, max_thread, collected`. Never logs from inside the collector's callback (deadlock risk). **What to read in
  the next log:** `off_main > 0` means the collector ran on a worker thread (the crash hypothesis is then
  plausible); `max_ms` is the longest everything-stops pause.
- **Not done: no-data flag from the render thread.** Measured ~0.7 ms per frame (isfinite + invert); it needs a new
  field through `FrameDisplay` / `_on_rendered` for 1-2 % of a frame. Skipped on purpose (complexity vs gain).
- Still open from step 4: group-label paint cache (~5 ms), ROI overlay (`d_ov_rois_ms` 8.6 ms: needs a stage
  breakdown first).

## testing8 (session `lspri-20261014-185753-30336`, default 5 ms interval, 10x = 29.6/s asked, 85 s, no crash)

**Results of the step-4 first part**
- `d_highlight_ms` 3.3-4.1 (testing7) -> 1.9-3.4. Log: 3.2 records per Play frame (testing7: 6.6).
- **GC trace: the crash hypothesis is not supported.** During Play the collector ran once per ~10 s, 0.4-0.5 ms, always
  on `MainThread`; `off_main` = 0 in every Play window. The only off-main collections (43) are the dataset loader thread
  at start-up (`Thread-2 (_run)`), plus one 54.9 ms full collection on the main thread at start-up. So Python's garbage
  collector is neither a freeze source nor a cross-thread Qt-destruction source here. What stays unexplained is the
  crash itself: it happened with the 0.5 ms interval and a running profiler; the default interval has not crashed in
  testing8 or in the maintainer's limited tests since. Keep 5 ms as the default.

**New regime: the mask overlay (switched on at ~15 s)**
- Before: fps 18-19, `prep_ms` 1.5-4, `d_mask_ms` 0, cycle 53-57. After: fps ~15, `prep_ms` 12-16, `d_mask_ms` 9, cycle 62-70.
- Render thread: `render._render_inner` resolved the ignore mask (`resolve_external_mask`) for **every** frame, although it
  only matters to the background estimate with "exclude mask" on (and, for the processed image, flattening on): ~10 ms of
  `prep_ms` for a value nothing read. Since step 3 (submit first) it also computed the same mask at the same time as the
  GUI thread's mask tint instead of finding it in the cache.
- GUI thread: `_update_mask_overlay` resolves the mask per frame: a new chromatic warp for every wavelength = a cache miss
  (7.5 ms standalone: hash 0.35, rotate/crop 2.6, warp 3.8). The rotate/crop part is the same for all wavelengths.
- Fixed (uncommitted): (1) `render.request_uses_ignore_mask` gates the render-thread resolve; (2) `_redraw` resolves
  first, before submitting, only when the render thread will read it; (3) `preprocess.resolve_external_mask` keeps the
  rotated mask in a second small cache, so only the warp is redone per wavelength; (4) `warp_boolean_mask_affine` warps a
  bool mask as uint8 (3.8 -> 2.2 ms; identical output on 24 affine/seed combinations; other dtypes unchanged).
  *Estimate for the same setup:* `prep_ms` 13 -> ~2 ms, `d_mask_ms` 9 -> ~4.5 ms, cycle ~65 -> ~50 ms. Numerically
  nothing changes (masks are bit-identical: `tests/unit/test_lspri_rewrite_mask_resolve_cache.py`).
- Still on the GUI thread per frame with a mask: warp + hash + tint layer ~4.5 ms. Moving the mask resolve and tint layer to
  the render thread (delivered in `RenderResult`) would take that off the GUI thread; not done.

**Unchanged**: `thread_ms` ~25 ms and 100 % `blocked` (the request waits behind a read-ahead plane read: step 2, or the
opt-in 0.5 ms interval); `d_ov_rois_ms` ~5 ms and `d_paint_view_ms` ~17 ms (group labels, circles, image).

**One-off**: 29.8-32 s every stage 10x slower for ~3 s (`paint_view` 109 ms, `d_ov_rois` 43 ms): a pause of the whole
process, followed at 32.7 s by the `QFont::setPointSize: Point size <= 0 (-1)` warning (once per session in every log we
have: 48.7 s, 46.8 s, 32.7 s). The source is still unknown (no `setPointSize(` in the Log panel; not in `panels/`); a
Python stack attached to Qt warnings in `applog/setup.py` would show it.

## Step 2 done: read-ahead on its own reader threads (2026-10-14, uncommitted)

- `render.ImageRenderer`: the render thread only renders. `READER_THREADS = 2` plain `threading.Thread`s ("ImageReader-n")
  read the planes `prefetch` lists. They start on demand (first `prefetch`), leave after `READER_IDLE_SECONDS = 3` without
  work (so an idle session or a test process building many panels keeps no sleeping threads), and are joined by `stop()`
  together with the render thread. A render that needs the plane a reader has in hand *waits for that read* (an Event per
  plane in flight) instead of decoding it again; a plane listed twice is read once. The quiet period (0.25 s for idle
  exploring, 0 while Play runs) and "a new list replaces the old one" are unchanged. `use_fast_tiff_decoder(True)` is set
  on every reader thread (it is thread-local).
- Meaning of the Play stats changed: `blocked` / "% behind a read-ahead" = the request's *own* plane was still being read
  when it arrived (before: any read-ahead was running, i.e. always). `thread_ms` should now be small.
- **Measured** (standalone, real planes, fresh files, simulated GUI thread burning 25 ms of Python per frame, default 5 ms
  GIL interval, 120 frames): old renderer (HEAD) **20.4 / 19.8 frames/s, cycle 49-51 ms, request waits 46-48 ms for the
  render thread** (it reproduces testing7's ~50-57 ms); new: **32.7 / 34.6 frames/s, cycle 29-31 ms**, wait ~14 ms (GIL
  hand-offs with the busy "GUI"); 1 reader thread instead of 2: 30.8 frames/s. In the real app the GUI thread's own
  ~35-40 ms per frame becomes the limit, so expect the cycle to land there (~40 ms, ~25 fps), not at 30 ms.
- Tests: `test_lspri_rewrite_renderer_readers.py` (8: two reads overlap; duplicate not read twice; missing plane survives; a
  render of another plane is not held up by a stuck read; a render of the plane being read waits and does not decode it
  again; readers start on demand / leave when idle / come back; stop joins them; quiet period holds). The older prefetch
  tests use one reader to pin the order. Histogram panel tests 5/5 clean runs (this file crashed ~80 % of runs with the
  earlier separate-thread-class attempt, see memory `lspri-image-refresh-speedups-2026-10-11`).
- Verify in testing9: `thread_ms` ~25 -> small, `blocked` share low, `cycle_ms` down, `dataset.plane_read` now logged from
  `ImageReader-0/1` (the `thr` field), and no crash.

## testing9 (session `lspri-20261014-202410-49232`, default 5 ms interval, mask overlay on, 65 s, 2 Plays, no crash)

**Step 2 worked on the render side**: `thread_ms` 25-29 -> **1.8**, `blocked` 100 % -> **0**, `load_ms` 0.7, `prep_ms` 1.8 (mask fixes: was 12-16),
`d_mask_ms` 9 -> 3.3. Reads now come from `ImageReader-0` (319) and `ImageReader-1` (415), ~41 ms each (GIL-inflated), 1 read per frame:
capacity ~47 planes/s against ~17 needed. GC: nothing off the main thread during Play; one collection on `ImageReader-0` at start-up.

**The frame rate did not move: 17.6 / 16.3 fps, cycle 57 / 61 ms** (testing8 with mask: 15 fps, 62-70 ms; testing8 without mask and testing7: 17.5-18.5 fps, 54-57 ms).

**Why: the GUI thread has been the limit all along.** Across three very different render paths the cycle stayed at ~55 ms:

| session | render path (thread wait + load + prep + picture) | cycle |
|---|---|---|
| testing7 | 29 + 1 + 2 + 9 = ~41 ms | 57 ms |
| testing8 (before mask) | ~26 + 1 + 2 + 9 = ~38 ms | 54 ms |
| testing9 | 1.8 + 0.7 + 1.9 + 24 = ~28 ms | 57 ms |

The render fixes only mattered where the render path was *longer* than the GUI loop (testing8 with the mask: 12-16 ms prep). Now a finished frame waits
`deliver_ms` ~17 ms for the GUI thread.

**GUI thread per frame (testing9)**: step 1.2 + redraw 0.4 + overlays 8.5 (of which `d_ov_rois` 8.3) + mask tint 3.3 + paint of the view 17.7
(`paint_image` only 1.1 ms, so ~16 ms of the paint is the overlay items) + `_on_rendered` 4.2 (highlight 2.2, no-data 0.9, listeners 0.5) = **~35 ms measured
in Python**; cycle 57 ms, so **~20 ms per frame is unattributed** = GUI-thread work with no Python on top (testing8's profile said the same: 41 % "event loop /
Qt native" = ~26 ms per frame). Candidates: window flush to the screen, Qt's own scene bookkeeping, event dispatch / GIL re-acquisition. **Not measured yet.**
The ROI overlay (circles, rings, labels, selection) is ~21 ms of the 35 measured (draw 8.3 + most of the paint).

**Also seen**: the render thread's picture step (`pic_ms`) is 24 ms now (9 ms in testing7/8): it competes with the busy GUI thread and two readers for the
GIL; harmless while it is not the limit, but it is why the render path is still ~28 ms.

**Next (needs measurements before code)**
1. Cheap experiments by the maintainer, same 10x Play: (a) hide the ROI overlay + group labels (View/ROI options); (b) Slideshow settings "thin ROI outline" on; (c) a much
   smaller Image panel. If (a) gives most of the gain, the overlay is the lever (cache or simplify it while Play runs); if (c) does, it is Qt's raster / flush cost.
2. Diagnostics to add: GUI-thread CPU time per frame (`time.thread_time()`: CPU busy vs waiting), the slideshow settings and which panels are visible in `playback.summary`,
   and a per-event-type dispatch timer (QApplication.notify) during the profile run, to split the ~20 ms.

## Maintainer's overlay experiments after testing9 (10x Play, fps from the stats row)

| switched on | fps | ms/frame | extra |
|---|---|---|---|
| everything off | 23-25 | ~42 | floor |
| sample ROI | 22-23 | ~44 | +3 |
| sample + reference ROI | 18-20 | ~52 | +8 |
| sample ROI + CC (chromatic correction) | same as without | | 0 |
| all labels on | 19-22 | ~48 | ~+3-4 (unstable) |
| sample ROI + CC landmarks + ignore mask + histogram overlay | 17-18 | ~57 | +13 |
| everything on | 14 | ~71 | +14 more |
| everything + background removal | 9 | ~111 | +40 |

Reading: (1) **the floor is the render path, not the GUI**: with all overlays off the GUI thread is nearly idle, and the cycle is step 1.6 + render path
~30 (picture step 24 ms) + hand-off + `_on_rendered` ~4 = ~38-42 ms. (2) Overlays add GUI work on top; once the GUI chain (~45-70 ms) is longer than the render
path they are the limit. (3) Background removal adds its ~35 ms *to the render path* and the cycle grows by more than that (+40): it is render-bound and
GIL/CPU-inflated. (4) CC costs nothing.

**Benchmark `bench_pic_contention.py` (real frame with NaN corners)**: picture step 14.9 ms idle -> 25.6 ms with 2 reader threads + a GUI-like Python thread
(testing9 in-app: 24.4 ms). `cv2.setNumThreads(1/2/4/8` changes nothing useful (picture 24-31 ms, geometry warp gets slower with fewer threads): the inflation is
GIL / CPU contention, not OpenCV's worker pool. A 0.5 ms switch interval would help it (opt-in `LSPR_GIL_SWITCH_MS`), the crash question is open.

**Possible levers, by what the experiments say**
- Light configs (floor): render two frames ahead (a 2-deep pipeline: submit N+2 when N+1's render ends, not when N is shown; needs a render queue and a looser
  brake in `frame_displayed`), or fewer / cheaper passes in the picture step (it must stay byte-identical to pyqtgraph: `test_lspri_display_picture.py`).
- Reference ROI (+8 ms, the largest single overlay): rings + fill are still a `PlotDataItem` + a `QPainterPath` rebuilt per frame; the sample circles were moved to one
  painted item (`sample_circles_item.py`) for the same reason. Same treatment is the obvious candidate.
- Landmarks / ignore mask / histogram overlay (+13 together): mask tint 3.3 + highlight 2.2 + landmarks (unmeasured) + their paint.
- Background removal: cache the ROI exclusion mask (7.6 of 34 ms), render ahead.

**Diagnostics added for the next run (uncommitted)**: every `playback.window` / `playback.summary` record carries `c_*` tags (target fps, speed, number of ROIs,
sample / reference / labels / group labels / landmarks / mask / highlight / background / background-estimate / thin outline / calm histogram) and
`gui_cpu_ms` + `gui_busy_pct` (GUI-thread CPU time per frame and as a share of wall time: ~100 % = the GUI thread is the bottleneck; low = it waits for the
render thread, the GIL or the screen). Tests: `test_lspri_rewrite_slideshow.py` (2), `test_lspri_rewrite_image_panel.py::test_play_config_fields_...`.
Suggested run for testing10: one Play at 10x, ~10 s per step, adding one thing at a time: all off, sample ROI, reference ROI, ROI labels, group labels,
CC landmarks, ignore mask, histogram overlay, background removal; press the Log panel's profile button once during "everything on".

## testing10 (`lspri-20261014-205601-34504`): one overlay added at a time, 160 ROIs, thin outline ON, 10x Play, 66 s of windows, no crash

Per configuration (S sample ROI, R reference, L labels, G group labels, CC = chromatic *landmarks* overlay, H histogram overlay, BG background removal):

| on | fps | cycle | GUI CPU/frame | GUI busy | paint of view | deliver | render path (load+prep+pic) |
|---|---|---|---|---|---|---|---|
| none | 24.1 | 41.6 | 27.7 | 67 % | 3.9 | 1.6 | 18.7 |
| H | 22.5 | 44.5 | 31.3 | 70 % | 5.2 | 1.5 | 18.7 |
| CC+H | 21.6 | 47.4 | 32.0 | 69 % | 6.9 | 1.6 | |
| S+CC+H | 20.5 | 49.0 | 38.5 | 78 % | 12.6 | 2.7 | 29.3 |
| S+R+CC+H | 18.8 | 53.3 | 44.0 | 83 % | 20.2 | 3.5 | |
| S+R+L+CC+H | 17.6 | 56.9 | 44.5 | 78 % | 22.9 | 5.1 | |
| S+R+L+G+CC+H | 15.1 | 66.8 | 55.2 | 83 % | 30.5 | 13.7 | 38.3 |
| ... + BG | 10.0 | 100.2 | 56.0 | 56 % | 31.9 | 1.0 | 88.8 (prep 80) |

Findings
1. **Overlays cost paint, not drawing.** GUI CPU grows 27.7 -> 55.2 ms, the paint of the view 3.9 -> 30.5 ms. The overlay *draw* (`d_ov_rois`) is 0.1 ms: in this run
   the ROI positions do not change from frame to frame (no per-frame chromatic shift), so only group labels (3.7 ms) re-draw. Every item is simply **repainted every frame**
   because the image under it changes. In heavy configs the cycle grows 1:1 with the paint (+25 ms of cycle for +26 ms of paint).
2. Marginal cost in cycle: G +9.9 (paint +7.6, draw +3.7), R +4.3 (paint +7.6), L +3.6, CC landmarks +2.9, H +3.0, S +1.6 (paint +5.7, hidden behind the render path).
3. **A floor of ~20 ms of GUI-thread CPU per frame is unattributed even with everything off** (27.7 ms CPU per frame vs ~8 ms the Python stages account for). Added: kernel/user split.
4. The GUI thread is never 100 % busy (56-83 %): it still waits for the render thread / GIL / screen part of the time.
5. **The render thread gets slower as the GUI gets busier**: picture step 12.2 -> 25.8 -> 35.2 ms (GIL / CPU contention). Background removal: prep 80 ms (34 ms standalone, 2.3x inflated), the cycle is then render-bound (100 ms).
6. Reader threads / GC fine: `plane_read` median 35 ms, no collections off the main thread.

**Benchmark `bench_overlay_paint.py`** (real `RoiOverlay`, 160 ROIs in 10 groups, offscreen, image changes each iteration, static overlay): paint 9.2 ms image only;
27.3 ms with all overlay items; **11.6 ms with every overlay item on `QGraphicsItem.DeviceCoordinateCache` and `QPixmapCache` limit raised from the default 10 MB to 512 MB** (overlay cost 18.1 -> 2.4 ms).
With the default 10 MB limit it is *worse* (36.1 ms): the full-view cache pixmaps (~5 MB each) evict each other. Caching only some items helps only those items.
Caveats: only helps while the overlay is static (a per-frame chromatic shift redraws it every frame, and then a cache costs a little extra); a cached item is blitted at an
integer device pixel offset (up to 0.5 px off the image while panning); memory = one pixmap per full-view item (5 MB at 1500x850, ~33 MB on a 4K view); the offscreen platform
paints text differently from Windows, so group-label gains in the app may be larger than the 1 ms seen here. Needs the maintainer's visual check.

**Added (uncommitted)**: `diagnostics/thread_times.py` (`GetThreadTimes`: GUI-thread user vs kernel CPU), `gui_kernel_ms` in the Play records. If kernel time is large, the unattributed
floor is the window system / graphics driver (screen update), not Qt painting.

## Battery-mode log (`lspri-20261014-222237-75476`) - what it adds

Absolute speed is not comparable (battery mode: 8-14 fps), ratios are.
- **Kernel time is small**: GUI-thread CPU per frame 56-107 ms, of which kernel 10-20 ms (14-19 %). The ~20 ms "unattributed" floor is mostly *user-mode* work (Qt C++ and
  Python), not the window system. (Fresh-memory page faults from per-frame array allocations count as kernel time too.)
- **A GUI profile with everything on** (65 % Python / 35 % native): `paintEvent` 38.5 %, of which the **chromatic landmarks overlay** (`ScatterPlotItem.paint` 11.6 %,
  `_maskAt` 9.7 %, `PlotCurveItem.boundingRect` 10.7 %) ~22 %; `slideshow.interval_seconds` -> `mean_frame_interval_seconds` **4.4 %** (it re-summed all 8478 frame timings on every
  step: fixed, see below); `logging StreamHandler.emit` **5.6 %** (the console handler writing every DEBUG record on the GUI thread; with the Suite Launcher's Debug console on this is a
  blocking pipe write: run speed tests with the console on Normal); sample circles paint 4.3 %; `_draw_roi_overlay` 6.2 %.
- **With every overlay hidden the ROI overlay still re-drew its geometry on each wavelength step** (`d_ov_rois` 15 ms on battery, ~7 ms normal; CC makes the draw key change per
  frame) - for items that were hidden. Fixed: a hidden part is not computed.

## Overlay layer cache + two small fixes (implemented 2026-10-14, uncommitted)

**What was asked** (maintainer): the overlay is static for one wavelength; the same wavelengths repeat in every cube; cache it per wavelength, and watch when it changes.

**`panels/image/overlay_cache.py`** (`OverlayLayers`, `LayerCacheEffect`): the overlay items are children of two empty layer items (data space: circles, rings, ring fill, selection;
screen space: labels, group labels). A pass-through `QGraphicsEffect` on each layer renders it into a device-coordinate pixmap the first time a state is shown and afterwards only blits.
The panel (`_draw_roi_overlay`, per-frame path) asks `layers.begin(epoch, state)`: on a hit the geometry is **not re-drawn at all** (saves the 5-8 ms draw as well as the paint).
- `state` = cube (only with a ROI timeline) + chromatic affine bytes. `epoch` = ROI revision + selection + `RoiOverlay.appearance_key()` (style, visibility, labels, placement, fill opacity, line
  width, group-label settings): a new epoch drops all pictures. The view (range, size, pixel ratio) is part of the key inside the effect, read live; a zoom/pan/resize drops the pictures.
- A picture is kept only if the epoch was stable for a frame (a drag caches nothing) **and the items hold the state being shown**. After a hit the items still hold the previous state's geometry;
  a repaint for another reason (zoom) would have drawn and *cached* that wrong geometry (found by the first test run). Fix: the layers track `items_state`; `sigRangeChanged` runs a refresher
  (`ImagePanel._refresh_overlay_items`) that brings the items up to date before the repaint; nothing is kept from non-current items.
- Budget: `LSPR_OVERLAY_CACHE_MB` (default 256, 0 = off). Admit until full, **never evict** (LRU smaller than the 27-state cycle = 0 hits, measured).
- Forced redraws (style / label / group-label changes, previews) call `invalidate()`; every `RoiOverlay` mutator that changes the look (`apply_style`, `set_sample_width`, `set_label_placement`, `clear`) does too.
- **Measured** (offscreen, real classes, 160 ROIs / 10 groups, 1500x850 view, image changes every frame, 27 wavelength states x 6 cubes, draw + full paint per frame): **~70 ms without the cache; first cube
  ~70 ms (no penalty), then 16.5-18.7 ms** (hits 134/162; 27 states = 213 MB). Pixel difference to direct painting: max 1/255, 0.003 % of pixels.
- Costs / caveats: ~8 MB per cached state at 1500x850 (more on a bigger panel: a smaller share of the 27 states then fits; see `c_ov_states` / `c_ov_mb` in the Play log); text in the labels is rendered
  grayscale-antialiased into the layer (no sub-pixel/ClearType) and the cached layer sits on the device pixel grid (<= 0.5 px vs the image while panning); to be confirmed visually by the maintainer.
  Not covered: the chromatic landmarks overlay (scatter/curves), scale bar, mask / highlight tints, crop outline.

**Hidden parts are not computed** (`RoiOverlay.draw`): sample circles + selection ring when the sample overlay is hidden, rings + fill when the reference overlay is hidden. Showing them again goes through
`apply_style` + forced redraw; the panel's "unchanged" test now includes `appearance_key()`.

**`DatasetModule.mean_frame_interval_seconds`** is computed once per dataset (identity of the dataset and its metadata, and the record count), not on every slideshow step.

**New Play-log fields**: `c_ov_hits`, `c_ov_misses` (cumulative), `c_ov_states`, `c_ov_mb`. In the next log expect: `d_ov_rois_ms` ~0 on hits, `d_paint_view_ms` and the cycle clearly lower in the heavy configurations, `c_ov_hits` >> `c_ov_misses` after the first cube.
Tests: `test_lspri_rewrite_overlay_layer_cache.py` (16), 3 panel tests in `test_lspri_rewrite_image_panel.py`, the interval test in `test_lspri_rewrite_slideshow.py`.

## Options -> "ROI Overlay Cache" (2026-10-14, uncommitted)

The cache size is now a menu choice instead of an environment variable. **Where:** the Options menu, next to "ROI Fill Maximum Opacity": it is the app's stand-in for a Preferences dialog (its own
docstring says so), it already holds the other saved view/performance choices, and a Preferences dialog for one setting would be more machinery than the setting is worth.
- Submenu: first line = what the cache holds right now (refreshed when the menu opens: "27 wavelengths cached: 213 of 256 MB", or "Off: the overlay is painted directly"), then the exclusive choices
  **Off, 128 MB, 256 MB (default), 512 MB, 1024 MB**. Tooltip explains what it does and the memory cost (~8 MB per wavelength at 1500 x 850).
- Applied at once: `ImagePanel.set_overlay_cache_mb` -> `OverlayLayers.set_budget_bytes` (0 = off, drops the pictures) and a forced overlay redraw for the frame on screen (after hits the items hold an earlier
  wavelength; without the redraw switching the cache off would leave that stale overlay visible - pinned by a test that fails when the redraw is removed).
- Saved with the other UI state (`UiKey` `image/overlay_cache_mb`, default 256) and restored in `restore_ui_state`. `LSPR_OVERLAY_CACHE_MB` still exists as a start-up override for comparing runs: it wins over the saved
  value for that run and does not overwrite it.
- Tests: `test_lspri_rewrite_overlay_cache_option.py` (11: budget change, environment parsing, panel property / save / restore / override / stale-overlay, the real Options menu in the real main window).
- Edge: the cache is keyed on the view range; if pyqtgraph's auto-range were to move the view every frame (ROIs outside the image bounds, no saved view, no zoom yet), nothing would be reusable - the Play log's
  `c_ov_misses` would show it.

## testing12 (`lspri-20261014-233828-42960`): the first run with the overlay cache - it did not help, and why

Windows 88-176 s (Play at 10x, 160 ROIs, S+R+L+G(+CC)+H, thin outline): 13.4-17.6 fps, cycle 58-76 ms, `gui_busy_pct` 84-86 %. **Cache: 0-2 hits in 1468 frames, 14-18 misses per second, 45 pictures
held = the whole 256 MB budget**, and the profile shows `overlay_cache.draw` 31 % of the GUI thread (rendering a picture on every miss) and `overlay_cache.boundingRect` 8.7 % self. The heavy configuration
was ~9 ms *slower* than testing10's without the cache (15.1 -> 13.4 fps). So: every frame was a new state, nothing was ever reused.

**Cause (checked on the maintainer's saved session, 8164 chromatic models):**
- The chromatic affines are **identical across cubes**: 26 distinct (wavelength, affine) states for all 8164 (cube, wavelength) frames, at full precision (translation range e.g. -8.15..-8.15 over 314 cubes).
  The maintainer's model (the overlay depends on the wavelength only) is right.
- **My state key was wrong**: it contained the cube number whenever *any* ROI has a timeline (`has_timeline()`). 3 of the 160 ROIs have one persistent change each, at cube 79. Result: 26 x 314 distinct
  states, none repeating. A timeline only changes the geometry at its change cubes (persistent: from that cube on; individual: that cube only).
- Also: the data layer's `boundingRect` walked the children's bounding rectangles on every call, and after the budget was full every miss still rendered a picture it could not keep.

**Fixes (uncommitted):**
1. `RoiToolbox.geometry_segment(cube)`: a label such that two cubes with the same label have the same geometry for every ROI (`()` without timeline; `("segment", n)` between persistent changes; `("single", cube)` for
   an individual change). The overlay state uses it instead of the cube: with the maintainer's timelines the overlay has **2 segments x 26 wavelengths**, and within one segment every cube reuses the pictures. It also avoids
   the full overlay re-draw that every cube change used to cost whenever a timeline existed. Tests: `test_lspri_rewrite_roi_geometry_segment.py` (same label => same geometry for every ROI; one edit => two segments),
   and a panel test that fails with the old key (6 draws instead of 4).
2. **Guards**: after 60 misses in a row without a single hit the cache pauses itself for that epoch (no more pictures, the ones made are dropped; status "Paused: the overlay never repeated"); once the budget is
   full a miss is painted directly instead of being rendered into a picture that cannot be kept.
3. The layers' rectangles are cheap now (data layer: the visible view; screen layer: the view box; `ItemClipsChildrenToShape`), pictures are view-sized (~9 MB per state at 1500x850: 27 states ~250 MB).
4. **Console log output is written by the log listener thread** (`build_file_pipeline(..., *extra_handlers)`, `respect_handler_level=True`), not by the thread that logs: `StreamHandler.emit` was 8 % of the GUI thread
   (5.6 % in the battery log) whenever the console was on Debug. Tests in `test_lspri_applog_core.py`.
- Benchmark of the shipped code (160 ROIs, 27 states x 6 cubes, unloaded machine): **~34 ms per frame without the cache, 9.5-13 ms with it** (hits 134/162, first cube no penalty).
- Expect in the next log: `c_ov_hits` >> `c_ov_misses` after the first ~30 frames, `c_ov_states` ~26-52, `d_ov_rois_ms` ~0, `d_paint_view_ms` much lower, and no `overlay_cache.draw` in the profile. If it says Paused,
  look at what differs between consecutive frames.
