# Audit: transient scan, Auto mask, mask export, sliders (2026-10-17)

Scope: commits `877898f` (Scan + Auto mask + export), `76f1c5f` / `f73fb2a` (Mask rows, ROI row), `9e7a7fb` (centred zoomed sliders), `c02a58b` / `39395fe` (wheel crossing cubes), and the session autosave only where it touches the scan's data. The audit itself was read-only; the fixes are listed under "Outcome" at the end (2026-10-18). Not audited: ROI Array detect (2026-10-08), the analysis engine's planning loop.

Method: read every new file in full, then timed the pure parts on synthetic 900 x 1300 frames (script was in the session scratchpad; numbers below are medians of 15 runs on the 8-core test machine). Real-dataset numbers (48 s scan at 4 workers etc.) are the plan doc's, not re-measured. Tests were not run (no code changed).

Engineering priorities (docs/engineering_policy.md): 1 correctness, 2 data integrity, 3 readability, 4 testability, 5 performance, 6 GUI polish.

## Verdict

The design is sound and well documented: the scan only points, masking is a separate undoable act, every event records the parameters that made it, the batch commit is one undo step, compressed masks need no migration, the session change is additive (1.4). The slider code (`AxisWindow`, `DataAxisSlider`, `TimelineStrip`) has no bug I could find by reading; its per-step cost is far below 1 ms. The real findings are about **lifecycle** (what happens when the dataset changes while a task runs) and **what still runs on the GUI thread**.

## A. Correctness and data integrity (priorities 1-2)

**A1. A running scan is not stopped when the dataset or session changes.** `MaskScanControls.start_scan` hands the task the bound method `self._dataset.load_plane_uncached` (`panels/image/mask_scan_panels.py:230-236`). That method looks up `DatasetModule._dataset` *when it is called* (`dataset/module.py:149-151, 173-178`), i.e. on every read from the worker threads. Nothing cancels the scan on `dataset_loaded` / `dataset_cleared` (`panels/image/panel.py:1445-1480` does not touch it) or on a session switch. If a different dataset is opened mid-scan, later reads come from the new dataset (or fail and are counted as unreadable), and `_on_finished` (`image_tools/mask_scan_task.py:121-137`) installs the mixed result into whichever session is active. From reading the code; not reproduced. Fix (small): cancel scan and Auto mask on those signals, and give each run a token so a late result for an old token is dropped. Same for Auto mask (below).

**A2. Auto mask can overwrite a mask painted while it runs.** The run takes 1-2 s in 30 ms slices; each slice reads the mask in effect *at that moment* (`mask_auto_task.py:119`), and the commit writes the whole batch at the end (`:133`). The version-token guard only protects *undo*, not this commit. A brush stroke on a frame that is also being auto-masked, during those seconds, is replaced (undoable, but silent). Fix (small): connect `mask.mask_changed` for the duration of the run; if it fires, refuse the commit with a message ("the mask changed while Auto mask ran, nothing was changed, run it again"). Also disable the mask tools while it runs.

**A3. "Scan done: 0 particles" with a single cube, or when nothing could be compared.** Screening needs a reference cube. With one cube the loop never pairs anything (`transient_scan.py:181-187`) but `frames_screened` still counts the frames, and the status text (`mask_scan_panels.py:246-250`) does not show it. The result reads as "clean". Fix: say "needs at least 2 cubes" up front, and show screened / unreadable counts in the done message.

**A4. For you to decide: the `artefact` class is "did not move", not "not a particle".** `link_passages` marks an object that sits at one place for 3+ consecutive wavelengths as `artefact` (`transient_detection.py:398-404`), and Auto mask never masks artefacts. A real particle that rests in the field for one whole cube (about 27 frames, absent in the cube before) is new against the previous cube, so it is detected, then stays put, so it is classed `artefact` and is not masked. The pointer is still drawn, so you would see it, but the wand skips it. The rule was tuned on the test dataset (corner/edge artefacts in cube 0 and cubes 175-185) and I do not know whether such a rest happens in your measurements. If it can, the class needs a different name or a separate "mask these too" switch.

## B. What runs where (threads), with measurements

| Piece | Thread now | Measured / claimed cost | Verdict |
|---|---|---|---|
| Scan (read, screen, contour, gap fill) | plain worker threads, 2 pools | 42-48 s whole dataset (plan doc) | Good. |
| Scan progress | worker emits one queued signal per frame | about 110 per second for 24 s | Throttle (B3). |
| Install scan result | GUI | milliseconds | Good. |
| **Auto mask** | **GUI, 30 ms slices** | **3.9 ms per frame** (warp 3.2, OR 0.6, compress 0.24); 300 frames about 1.2 s | **Can move off (B1).** |
| Export all masks | worker | PNG per mask | Good; one GUI-side stat loop (B5). |
| Session autosave | capture on GUI, write on worker | events payload 3.9 ms on GUI (850 events) | Small (B6). |
| Mask rows refresh | GUI, coalesced 100 ms | tiny | Good. |
| Slider relabel / repaint per step | GUI | well under 1 ms | Good. |

**B1. Auto mask can run on a worker; the stated reason does not hold.** `mask_auto_task.py:7-13` says the modules are "read on the GUI thread only" and the per-frame work is ~5 ms (confirmed: 3.9 ms). But what needs the modules is only (a) the dict lookup `MaskModule.resolve_mask_source` and (b) the 2x3 matrix `ChromaticModule.affine_between(from, to)`. The expensive parts, `warp_boolean_mask_affine` (3.2 ms), the contour OR (0.6 ms) and the compression (0.24 ms), are pure functions of arrays and a matrix. Design: on the GUI thread, in one quick loop (about 0.05 ms per frame, 15 ms for 300), collect per frame `(authored MaskChange, matrix)`; a plain `threading.Thread` does the rest and hands back the finished `MaskChange` list; the GUI thread commits. Needs one small public addition on `MaskModule` (a lookup that returns the stored `MaskChange` undecoded, so the decode happens on the worker too). Side benefits: all bases are resolved at one instant (narrows A2), and no 30 ms stalls. This matches the standing rule (design the off-thread version by default). Priority served: 6 (GUI smoothness), with a small gain for 2.

**B2. Does the scan make the window sluggish? Unmeasured; one cheap mitigation.** The pools are 6 reader + 6 compute threads (`transient_scan.py:56-60, 159`; the docstring says "at most 6" but that is per pool) on 8 cores, all at normal priority, and each native call (decode, cv2, numpy) gives the Python lock (GIL) back and takes it again, which the GUI thread feels (see `gil_tuning.py`: the same mechanism cost the render thread 21 -> 36 ms). The scan's `worker_init` hook already exists; setting the worker threads to below-normal priority there (Windows `SetThreadPriority`, a few lines, in both pools; the compute pool has no initializer yet) is the standard way to keep the GUI first. Needs your eyes: scrub or Play while a scan runs and say whether it is smooth. The existing `diagnostics/thread_times.py` could give a number.

**B3. Throttle progress.** `mask_scan_task.py:99` emits per frame, the task indicator rebuilds its text each time (`panels/task_indicator.py:264-274`). Emit at most every ~50 ms (always emit the last). Trivial, safe.

**B4. Bulk decoders flush the GUI's mask cache.** `MaskChange.mask` decodes through a shared 32-entry `lru_cache` (`image_tools/mask/model.py:72-77`). Export (`mask/export.py:70`), the session writer (`storage/session.py:523`) and Auto mask each decode hundreds of masks through it, pushing out the masks the Image panel is showing; each costs 1.75 ms to decode again (measured, cold). Give bulk callers a decode that bypasses the cache.

**B5. Export pre-check on the GUI thread.** `panels/image/mask_file_actions.py:219` calls `.exists()` once per planned file. On a network or cloud-synced folder with hundreds of files this can stall; one directory listing and a set intersection does the same.

**B6. Events payload on the GUI thread.** `prepare_session` (`storage/session.py:616`) encodes every event and contour on each autosave (3.9 ms for 850 events, measured; `json.dumps` of it 7 ms is already on the writer). The state is immutable, so `PreparedSession` could carry the `TransientEventsState` and the writer could encode it. Small.

**B7. Scan compute hotspot (optional).** In `contour_objects` (26 ms per hit frame) the full-frame `ratio_image` costs 12.8 ms (`transient_detection.py:165-172`), though only a 16 px window around each object is used afterwards. Computing the gain on the 1-in-2 grid directly and the ratio and blur only inside padded windows should give identical numbers (same element-wise operations; the blur window is padded beyond its 4 px reach). Estimated gain about 2-3 s of a 45 s scan, because the scan is decode-bound: the stages together decode about one plane per dataset frame (screen ~2.7k reads, expand ~3k, confirm ~2.4k). Not worth doing unless the scan gets another reason to be touched; would need the before/after comparison the numerics rule asks for.

**B8. Every cube change is two selection signals.** `SlideshowController._show` (`panels/image/slideshow.py:326-330`) and `MaskRows._hop_to_other_cube` (`panels/image/mask_rows.py:354-355`) call `set_cube` then `set_wavelength`; every subscriber runs twice (a cube change is about 20 ms on the big dataset per the slideshow notes). A `SelectionModule.set_frame(cube, wavelength)` that emits once would halve it. Existing pattern, shared module, so: your decision first.

## C. Sliders

Read `AxisWindow`, `DataAxisSlider`, `TimelineStrip`, `slider_ticks`, and the wheel changes in `MaskRows`. Checked: hold/release of the shared window (also on `hideEvent`), zoom reset only when the tick count changes, `follow` not recursing through the relabel hook, the wheel crossing only on a notch against the end, wheel hops being synchronous (each hop sees the new selection). No defect found.

Small, optional: `_large_gap_boundaries()` (`panels/image/data_axis_slider.py:451`) rebuilds the gap list and a median on every paint (about 0.1 ms at 300 ticks); it only depends on the ticks, so compute it in `set_ticks`. Tooltips of the event marks are built eagerly for every cube on each rows refresh; fine at the current size.

## D. Adjacent, not verified

The analysis engine's planning loop (`analysis/engine.py:605-631`) resolves a mask for every frame and, through `resolve_mask_snapshot_ref`, reads existing mask PNGs per frame (its own docstring says so). Auto mask adds up to hundreds of distinct individual masks, so "Start analysis" planning on an auto-masked dataset is worth timing in the next performance pass. Nothing subscribes the engine to `mask_changed` yet, so the batch emission itself triggers no recompute.

## Suggested order

1. A1 + A2 + A3 (small, integrity). 2. B3, B4, B5 (trivial). 3. B1 together with A2's guard. 4. B2 after you have judged by feel. 5. B6-B8 only if you want them.

## Outcome (2026-10-18): what was done

Maintainer: "you can do all of them (A/B)". Source in `apps/LSPRi/eva` (uncommitted), tests in the umbrella repo `tests/`.

| Item | Result |
|---|---|
| A1 | **Done.** `TransientScanTask.invalidate()` and `MaskAutoTask.invalidate()`; the Image panel calls both when the dataset is loaded or cleared (`panel.py` `_stop_dataset_bound_tasks`), and the scan also stops when the events module announces a restored session. A run carries a generation number: a result of an older run is dropped even if the run finished first (outcome `cancelled`, "stopped: the dataset or the session changed"). |
| A2 | **Done.** Auto mask listens to `mask_changed` while it runs and refuses to write if it fires ("the mask was changed while it ran, so nothing was written. Run it again."). Not done: disabling the mask tools while it runs (the guard makes it safe, and the run is about a second). |
| A3 | **Done.** A scan of fewer than two cubes (whole dataset or chosen range, dark-only cubes do not count) is refused with a sentence. `frames_screened` now counts frames really compared with a reference; the done message says "N of M frames compared". |
| A4 | **Done (maintainer, 2026-10-18: a particle that arrives and rests must be labelled and masked).** Stationary runs away from the image border are now particles at rest, not artefacts; see plan doc section 12, including a known limit (found for at most one cube's worth of frames after arrival) and the 13 shadow-cube pieces that become particles. |
| B1 | **Done.** Auto mask runs on a worker thread. `start()` does the lookups on the GUI thread (`MaskModule.resolve_mask_change`, new; `panels/mask_edit_common.plan_mask_edit_base`, new; ~40 microseconds a frame), the worker decodes, warps (skipped for an identity matrix: verified to give the same picture), ORs the contour and compresses; the GUI thread commits. |
| B2 | **Done as a hint, effect unmeasured.** `thread_priority.lower_current_thread_priority()` (Windows, below normal) runs in both scan pools through `worker_init`. Whether the window now feels smoother during a scan needs your eyes. |
| B3 | **Done.** `progress.throttled`: at most one report per 50 ms, the end always goes through, Cancel unaffected. Used by the scan and by Auto mask. |
| B4 | **Done.** `MaskChange.plane_uncached()`; export, session writer and Auto mask use it, so they no longer push the Image panel's masks out of the 32-entry decode cache. |
| B5 | **Done.** `mask/export.existing_clashes`: one directory listing instead of one `exists()` per file. |
| B6 | **Done.** `PreparedSession.transient_events` carries the immutable state; `SessionWriter.write` encodes it (on the writer's thread). |
| B7 | **Done, with a numeric note.** See below. |
| B8 | **Not done: measured, not worth it.** On a 120-cube x 27-wavelength dataset (offscreen, the Image panel's own handlers) `set_cube` costs 0.60 ms and `set_wavelength` 0.25 ms, so a combined setter would save at most ~0.25 ms per cube change. The audit's "halve the work" was an over-claim: the two signals would still both be emitted. The ~20 ms per cube change in the slideshow notes is in the rendering, not in these handlers. |
| C (slider) | **Done, then reverted (2026-10-18).** Caching `DataAxisSlider`'s scale-break positions in `set_ticks` (instead of per paint, ~0.1 ms) made `tests/integration/test_lspri_rewrite_chromatic_tab.py` crash natively in about half of its runs (7 of 16; 0 of 10 without it, 8 of 8 clean after the revert). See "The test crash" below. Not worth a crash-prone test file for 0.1 ms, so `data_axis_slider.py` is as it was. |

**B7 numeric note (numerics rule).** The contour step used to compute the ratio image and its blur over the whole frame and then cut out the windows; it now computes them inside the windows (padded by the blur's reach) and the gain from the 1-in-2 grid.
- Real dataset, stride 3, 8164 frames, old (git HEAD) vs new code: **871 events, every field of every event identical, contour pixels included.**
- Per pixel, the depth image inside a window differs from the whole-frame one by at most one float32 rounding step (1.2e-7 on a depth of order 1; about 0.6 % of the pixels), because OpenCV's blur adds its terms in a slightly different order at a different position in the row. The thresholds are 0.05 and above, so only a pixel exactly on a threshold could ever flip; none did here. Pinned by `tests/unit/test_lspri_scan_audit_fixes.py` (tolerance two steps; the test fails if the window padding is removed).
- Speed: `contour_objects` 28.6 -> 17.1 ms per hit frame (synthetic 900 x 1300). Whole scan, warm file cache, same machine and load: **73.5 s -> 68.9 s** (confirm 23.9 -> 20.9 s, fill 2.7 -> 2.2 s). A first pair of runs showed 119 s vs 79 s, but the first read the files cold: not a measurement of this change. Both are slower than the 42-48 s of the plan doc; the machine was busier today.

**Tests.** New: `tests/unit/test_lspri_scan_audit_fixes.py` (20). Rewritten for the worker thread and extended: `tests/integration/test_lspri_mask_auto_task.py` (17: off the GUI thread, mask changed meanwhile, dataset change, warped base, failing worker). Added to `test_lspri_transient_scan_task.py` (invalidate, session restore, one cube, compared frames, both pools initialised) and `test_lspri_rewrite_mask_scan_ui.py` (dataset cleared / loaded under a scan and under an Auto mask).

**Full LSPRi run (one pytest process per file, 190 files):** 2632 passed; the only failures are the four already known to fail on the committed source (`frame_roles` 1, `roi_table_panel` 1, `ui_state` 2: `docs`/memory "known failing tests"), plus the chromatic-tab crash below, which is what led to reverting item C.

## The test crash (found while verifying, 2026-10-18)

`test_lspri_rewrite_chromatic_tab.py` (17 tests) died with a native crash ("Fatal Python error: Aborted", "access violation" or a segmentation fault) at its 17th test, inside `processEvents`, while the slider change was in. Facts:
- HEAD source: 0 crashes in 5 runs. HEAD + only the `data_axis_slider.py` change: 3 of 6 and 1 of 4. Everything else of today's work without it: 0 of 10. After reverting it: 8 of 8 clean, but over all later runs 2 of 26 crashed (HEAD source: 0 of 15), so a small residual shift remains; it is the test, not the app.
- With the cyclic garbage collector switched off the crash disappears (0 of 4). A GC callback shows it dies at the **first full (generation 2) collection**, which is where the 16 earlier tests' panels are destroyed at once (the file's `tearDown` stops the renderer but never closes or deletes the panel).
- So this is the already documented crash class (`memory: lspri_rewrite_image_panel_test_crash`, "many panels GC'd at once"), not a fault of the slider code: the slider change only moved the odds, probably through a different allocation pattern. A first bisection counted import errors as crashes and pointed at the wrong files; the corrected one tells `CRASH(code)` from `ok`.
- The fix is in the test, not in the app: its `tearDown` now destroys the panel in order (`hide(); deleteLater()`, `sendPostedEvents(DeferredDelete)`, `processEvents()`, `gc.collect()`), done 2026-10-18 after a later, unrelated one-line change in the app moved the crash rate to 5 of 6 runs; with it 12 of 12 runs are clean.
