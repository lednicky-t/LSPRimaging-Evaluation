# Bulk analysis performance investigation: "Start analysis" slowness

2026-09-06. Full writeup of a multi-round investigation into "Start analysis"
taking multiple seconds per spectral cube on real datasets (specifically
`04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, an
OME-Zarr dataset, 66-160 ROIs depending on selection, chromatic correction
active). Three fixes landed in commit `176bde9` (submodule `apps/LSPRi/eva`);
one further fix is identified, measured, and recommended but **not yet
applied** because it requires re-exporting the maintainer's real data rather
than a code change. This doc exists so that last step doesn't need
re-deriving in a future session.

**Status at time of writing**: ~10-13s/cube → **~3.2s/cube** (measured live,
66 ROIs, steady state) from the three landed fixes. Re-exporting with a
larger OME-Zarr chunk size (below) is projected to bring that to roughly
**0.5-0.65s/cube**, based on directly measured I/O speedups on the same file.

**Update, same day**: a fourth fix (the `zarrs` Rust codec pipeline, see
"Follow-up" section near the end) got a large chunk of that same win
**without re-exporting** - measured ~5-7.5x on the I/O layer itself.

**Update, 2026-09-07 - `zarrs` is currently DISABLED**: it crashed a real
launch with `STATUS_HEAP_CORRUPTION` (see "Follow-up #7") - not yet
root-caused, so switched off rather than shipped with a known native crash.
The chunk-size re-export is, for now, the only remaining path to the
~0.5-0.65s/cube range; everything else in this doc (the three original
fixes, the mask reach-limiting fix, the always-scoped-path unification, the
busy-progress speed window) is unaffected and still active.

**Update, 2026-09-07 - Follow-up #10**: a fresh, report-independent audit of
the whole pipeline found and fixed three more small issues (worker-count cap
too high, redundant mask rasterization, an unrelated global event-filter
leak found in passing) and quantified a real remaining gap
(`flatten_background_enabled` bypasses every scoped-read win in this doc,
14x measured) that was deliberately left unfixed - see "Follow-up #10" near
the end for why.

## The three fixes already landed (summary - see submodule commit `176bde9` for full detail)

1. **Reduction-method over-computation.** `reduce_sample_and_reference_all_methods`
   (mean/median/trimmed_mean/plane_fit, the last needing an `np.where` mask
   scan + `lstsq`) was being computed unconditionally for every ROI at every
   wavelength during a bulk multi-cube sweep, even though only the active
   method is ever used for a historical cube (switching Reduction later just
   re-reads that one cube's pixels). Introduced by commit `0902fae`. Fixed
   with a `compute_all_reduction_methods` flag: `False` for sweeps of more
   than one cube, `True` (unchanged behavior) for a single-cube live preview.
2. **zarr 3.2.1 → 3.3.0.** See the chunk-size section below - this is the
   same root cause as the pending re-export, just the part of it that's a
   pure dependency bump instead of a data change.
3. **cv2 resample fast path.** Full writeup:
   [roi_scoped_resample_cv2_fast_path.md](roi_scoped_resample_cv2_fast_path.md).

## The pending fix: OME-Zarr chunk size (requires re-export, not yet done)

### Root cause, precisely

This dataset's OME-Zarr array uses a **50x50px inner chunk size** (sharding
is already correctly configured - one shard file per spectral cube,
containing all wavelengths - so this is not a "too many small files on disk"
problem). The problem is zarr 3.x's **local-store partial-shard-read path**:
reading a region that spans N inner chunks issues N separate `open()` +
thread-pool-dispatch + async-future-resolution round trips, *even though
all N chunks live in one already-open shard file*. Confirmed directly via
`cProfile` on 5 full-plane reads of the real file:

| Chunk config | Function calls | Time |
|---|---|---|
| 50px inner chunks (this dataset, zarr 3.2.1) | 1,965,746 | 3.548s |
| one chunk per plane (test array, zarr 3.2.1) | 16,348 | 0.066s |

**120x fewer calls, 54x faster**, doing the exact same read. The dominant
cost (`zarr/storage/_local.py:_get`, `_io.open`, `asyncio`/thread-pool
plumbing) is per-chunk dispatch overhead (~1.5-2ms/chunk), not actual
decompression - a real 5KB lz4-compressed chunk decompresses in
microseconds.

### Read cost scales with chunks touched, not bytes needed

Measured directly on the real file (`images.ome.zarr/0`), reading various
box sizes from the *same* cube/wavelength (so the only variable is how many
of the 50px chunks each box spans):

| Region | Chunks touched | Time (zarr 3.2.1) |
|---|---|---|
| 60x60px | 4 | 27.7ms |
| 150x150px | 9 | 23.7ms |
| 400x400px | 64 | 116ms |
| 800x1288px | 416 | 618ms |
| full plane (806x1288px) | 442 | 690ms |

Fits `22ms fixed + 1.5ms x chunk_count` almost exactly. **Re-reading the
identical region twice back to back costs the same both times** (588ms,
then 584ms) - there is no chunk-level cache, so this cost is paid on every
single wavelength read of every single cube, for the life of a run.

### This is a genuine OME-Zarr disadvantage *at this chunk size* - not a
### fundamental one

Direct comparison, same plane, same machine:

| Format | Full-plane read |
|---|---|
| TIFF, uncompressed | 2.54ms |
| TIFF, LZW | 16.31ms |
| **OME-Zarr, 50px chunks (current)** | **393ms** |
| OME-Zarr, one chunk per plane | 9.54ms |

With the current chunk size, OME-Zarr is 24-150x *slower* than TIFF for the
exact same read. With a chunk size matched to how this app actually reads
its own data (see "why fine chunking doesn't pay off here" below), OME-Zarr
is as fast as or faster than TIFF.

### Why small chunks don't pay off for this app specifically

The textbook argument for small chunks ("don't decompress data you don't
need") only wins when decompression is the expensive part. Here it isn't -
a whole plane is ~2MB and lz4 decompresses that in a few ms; the ~1.5ms/chunk
dispatch overhead dominates instead. Measured the crossover directly:

| Read size | Chunks touched | 50px chunks | One chunk/plane |
|---|---|---|---|
| 20x20 (single small ROI, best case for small chunks) | 1 | **8.4ms** (wins) | 14.1ms |
| 60x60 | ~4 | 27.7ms | **21.3ms** (wins) |
| 400x600 (a real multi-ROI analysis box) | ~96 | 145.8ms | **8.4ms** (17x) |
| full plane | 442 | 690ms | **9.5-20ms** (34-72x) |

The crossover is around 3-5 touched chunks. Below that, small chunks win by
a few milliseconds (imperceptible either way). Above it, big chunks win by
increasing margins. **Every real read pattern in this app** - the main
image viewer and wavelength switching (always full-plane), and any bulk
"Start analysis" sweep with more than a handful of ROIs - sits well past
that crossover. The only case that favors small chunks (viewing one tiny
ROI) is both rare and already fast either way. There is currently no known
use of these files by any *other* tool that would want small tiles for
progressive/tiled viewing; if that ever becomes a requirement, revisit this
recommendation specifically for that use case.

### zarr 3.3.0 already mitigates this (already applied, no re-export needed)

zarr-python PR #3004 ("Optimize partial shard reads", merged into the 3.3.0
release) replaces the serial per-chunk `Store.get()` calls with coalesced
`Store.get_ranges()` + added concurrency. Verified directly against this
same real file, no re-export:

| | zarr 3.2.1 | zarr 3.3.0 |
|---|---|---|
| Full-plane read | 393ms | 148.7ms (2.6x) |
| 26x sequential 400x600 reads | 2915ms total | 957ms total (3x) |

This is why the pin was bumped to `zarr>=3.3,<4` (was `>=3.2,<4` - already
technically allowed 3.3.0, but the floor was raised specifically so a future
fresh install can't silently resolve back to a 3.2.x that reintroduces this).
Note zarr 3.3.0 gives **no additional benefit** once chunks are already
large (both zarr versions measured ~9.4-9.5ms on the one-chunk-per-plane
test array) - the two fixes address the same symptom from different angles
and mostly overlap once chunks are fixed, rather than compounding.

### Recommended re-export settings

Via the app's existing **Export as OME-Zarr** dialog:

- **Chunk size ≥ 1300px** (covers the full 1288px image width, so each
  wavelength plane is exactly one inner chunk). Any value ≥ the image's
  longer dimension works; there is no benefit to going larger than that,
  and no meaningful downside either (a whole plane is only ~2MB).
- Sharding mode: leave as-is (`per_spectral_cube`, i.e. one shard per cube)
  - this part was already correctly configured and is unrelated to the
    chunk-size problem.
- Export to a **new** destination folder, don't overwrite the original,
  in case anything about the re-exported file needs comparing back.

### How to verify after re-exporting

1. Load the re-exported dataset, select a realistic ROI set (the original
   66-ROI selection if reproducing this exact investigation), run "Start
   analysis" for a few cubes, then Stop.
2. Check `apps/LSPRi/eva/logs/lspr_imaging_<timestamp>.log` for `SG cube
   compute timing` and `SG ome-zarr task stage timing` lines (DEBUG level -
   always written to the log file regardless of the in-app Debug/Normal
   toggle, see `workflow_log_controller.py`'s `setup_workflow_logging`).
3. Expect: `io=` in the stage-timing line should drop from several seconds
   (accumulated across ~26 wavelengths) to well under 1 second; total
   `SG cube compute timing` should land in the 500-700ms range once past the
   first cube (which always pays a one-time ROI-mask-geometry cache warmup
   cost in the current code - see "known noise in these measurements"
   below).
4. If it's not close to the projection, re-run the same profiling approach
   used here (`cProfile` on a raw `zarr.open_array(...)[...]` read of the
   new file, compared chunk-count-controlled) before assuming a new root
   cause - the technique in this doc generalizes directly.

## Known noise in these measurements (read before comparing new numbers)

- **The very first cube of any run pays a one-time cost** (measured at
  27-137 seconds depending on ROI count/chunk size) building the per-ROI
  circle/annulus mask cache and, when chromatic correction is active,
  potentially the `[λ]` shared-wavelength-geometry precompute. This is
  in-RAM (`roi_mask_cache` / `_formula_spectrum_roi_mask_cache`, a plain
  dict, cleared on every app restart) and amortizes to ~0 for every
  subsequent cube in the same run/session. Always compare *steady-state*
  (cube 2+ within one continuous run), never the first cube after a
  restart, when judging whether a fix helped.
- **Reduction-method choice matters.** All measurements above used
  `plane_fit` as the active method for some tests and `mean`/`median` for
  others - the `where` stage (np.where + coordinate lookup) only runs when
  the active method is `plane_fit`; for `mean`/`median`/`trimmed_mean` it's
  skipped entirely (`where=0.0ms` in the logs is expected and correct in
  that case, not a bug).
- **ROI selection width used to change which code path runs at all - no
  longer true, see the second follow-up section below.** A selection spread
  across most of the image (e.g. all 160 ROIs on this dataset) used to
  disqualify the scoped-read path and fall back to a separate full-image
  path with different performance characteristics; that ROI-count/area gate
  was removed (2026-09-06) because it no longer reflected reality once the
  `zarrs` fix (first follow-up section) made scoped reads cheap regardless of
  size. For an OME-Zarr dataset, the scoped path (`_ome_zarr_formula_spectrum_task`)
  now always runs, at any ROI count - the full-image path
  (`_tiff_stack_formula_spectrum_task`) is TIFF-only. The "SG ome-zarr task
  stage timing" debug line only appears for the OME-Zarr path; the TIFF path
  logs "ROI cache built/hit | shape=<full image>" instead. Check which one is
  actually running (via that log line) before interpreting stage-by-stage
  numbers on an older log capture from before this fix.

## Debug tooling added for this investigation (still in the codebase)

- **`Ctrl+Shift+F9`** (`main_window.py`) triggers Start/Stop analysis via
  the same code path as the button, auto-enabling the Analysis section and
  selecting every ROI table row if none are already selected. Added because
  `analysis_run_button` is a bare `ClickableIconLabel` (`QLabel`) with no
  accessible Invoke pattern - unreachable by UI-automation tooling or
  screen readers. Useful for any future scripted reproduction of a
  performance report without driving the GUI by hand.
- **`SG ome-zarr task stage timing`** debug log line (`analysis_tasks.py`,
  `_ome_zarr_formula_spectrum_task`) - per-cube accumulated `io=`/`resample=`/
  `mask=`/`where=`/`reduce=` breakdown, zero-cost when not read (a few
  `time.perf_counter()` calls + one `logger.debug()` per cube). This is
  what made isolating the dominant stage possible instead of guessing; it's
  cheap enough to leave in permanently for the next "X feels slow" report,
  matching the existing pattern documented in
  [sensorgram_reentrancy_and_cube_slider_cache_indicator.md](sensorgram_reentrancy_and_cube_slider_cache_indicator.md)
  and CLAUDE.md's own guidance on this.

## Follow-up (2026-09-06, same day): the chunk-size problem is now solved without re-exporting

A deeper look at zarr-python's own internals, prompted by the question "does
parallelism actually fix this, or does it just move the cost around" (asked
before any re-export had happened), found that the picture above is only
half the story on zarr 3.3.0, and found a fix that gets ~5-7.5x **without
touching the dataset at all**.

### Correction: the disk-I/O side is already coalesced on 3.3.0

Directly instrumenting `ShardingCodec._load_partial_shard_maybe` and
`LocalStore.get_ranges` against the real file confirmed: a 442-chunk
full-plane read now issues **exactly one** `get_ranges()` call covering all
442 byte ranges, not 442 separate opens. PR #3004 ("Optimize partial shard
reads") is working as designed for this file's read pattern. The "N separate
opens" framing earlier in this doc described the pre-3.3 behavior; don't
carry it forward into future reasoning about this codebase's zarr version.

### The actual remaining cost: decode dispatch, not fetch

cProfile on a full-plane read (zarr 3.3.0, defaults) showed ~450 separate
thread-pool dispatches - not for file reads, but for **decompressing** the
442 already-fetched, already-in-memory chunk buffers, one at a time. Root
cause: `codec_pipeline.batch_size` defaults to `1`, so even a single
`decode_partial_batch()` call carrying all 442 chunks gets split into 442
single-chunk batches internally (`zarr/core/codec_pipeline.py`, `batched(...,
self.batch_size)`), each paying its own asyncio-task/thread-pool round trip
(~1.5-2ms) regardless of how little work is actually inside it. This
generalizes: any zarr sharded read with many small chunks pays this twice
over on 3.3.0 - once (fixed, since #3004) for the coalesced fetch, and once
(still per-chunk) for decode dispatch.

### Fix landed: the `zarrs` Rust codec pipeline

[zarrs-python](https://github.com/zarrs/zarrs-python) is a drop-in
replacement codec pipeline (`zarr.config.set({"codec_pipeline.path":
"zarrs.ZarrsCodecPipeline"})`) backed by the Rust `zarrs` crate - it does the
same per-chunk iterate-and-decode loop natively instead of through Python's
asyncio, which is exactly the layer paying the dispatch tax above. Measured
directly against the real file, output verified byte-identical
(`np.array_equal`) to the default pipeline in every case tested:

| Read | Default | `batch_size=512`+`concurrency=64` (config-only) | `zarrs` pipeline |
|---|---|---|---|
| 1 full plane | 175ms | 89-100ms (~2x) | **23ms (~7.5x)** |
| 1 cube, 27 wavelengths, 400x600 ROI box (raw zarr calls) | ~1000ms | ~600ms (~1.7x) | **~195ms (~5x)** |
| Same, through the real `dataset_load_plane_roi()` app function | - | - | **~187-324ms** (confirms the win survives the app's own code path, not just an isolated benchmark) |

This is now **wired in**: `apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py`
(`_configure_zarr_codec_pipeline`, called from `_require_ome_zarr_support`)
enables `zarrs` if importable, silently falling back to zarr's default
pipeline otherwise - mirroring the existing `tifffile` optional-import
pattern in the same file. Dependency pinned in `apps/LSPRi/eva/pyproject.toml`
as `zarrs>=0.2.3,<0.3` (pre-1.0, so next-minor ceiling per this repo's
pinning policy).

**Not yet done**: an in-app measurement of the full `SG fast task stage
timing` log line (mask/reduce/chromatic-correction stages on top of I/O) to
convert this into an updated "X s/cube" figure for real "Start analysis"
runs - what's confirmed so far is the I/O layer itself and the real
`dataset_load_plane_roi()` function it feeds; the full task-level number
still needs a live run. Given `io=` was the dominant term in the original
~3.2s/cube figure, expect a substantial drop, but verify before quoting a
number.

**This does not replace the re-export recommendation** - it just makes it
much less urgent. The re-export (chunk size >= 1300px) removes the many-small-
chunks problem at its root (fewer dispatches needed in the first place);
`zarrs` makes each dispatch nearly free instead. Either alone gets most of
the win; doing both would not meaningfully compound (see the zarr 3.3.0
section above for why: once one side of the N-chunks-per-read equation is
fixed, the other stops mattering much).

### Relevant upstream issues (for future reference, don't re-search blindly)

- [PR #3004](https://github.com/zarr-developers/zarr-python/pull/3004) - the
  coalesced-fetch fix, merged into 3.3.0, already covers this codebase.
- [Issue #3953](https://github.com/zarr-developers/zarr-python/issues/3953) -
  a *different*, still-open concurrency gap (sequential fetch in one
  specific branch) - confirmed via direct tracing that this file's reads do
  **not** hit that branch, so it doesn't apply here. Don't assume it's the
  same bug as the one this doc describes if re-checking zarr's issue tracker
  later.
- [Issue #1398](https://github.com/zarr-developers/zarr-python/issues/1398) -
  "Allow batched/concurrent (de)compression support," open, unresolved
  upstream as a real per-codec API. This is the actual remaining bottleneck;
  `codec_pipeline.batch_size` and `zarrs` are both workarounds for it, not a
  proper upstream fix. Worth re-checking if this issue closes in a future
  zarr-python release - might obsolete the need for `zarrs` entirely.

## Follow-up #2 (2026-09-06, same day): the 160-ROI real-world run was still slow - here's why

After the `zarrs` fix (first follow-up) landed, a real "Start analysis" run
on the actual dataset with all 160 ROIs selected stayed slow (15-65s/cube).
Reading the run's own log line-by-line found this had nothing to do with the
I/O work above - it was hitting the full-image path this whole time, for
reasons unrelated to the fixes already made.

### The ROI mask cache is not a bug - confirmed from the log

`Spec cache summary | roi hit=%s build=%s` (per-cube) showed `build=26 hit=0`
on the very first cube processed (expected: cold cache) and `hit=26 build=0`
on every cube after that, for the entire rest of the run. Chromatic
correction is not cube-dependent here - one affine transform per wavelength,
computed once from the first cube's landmarks and reused identically for
every other cube (confirmed against `chromatic_controller.py`:
`chromatic_models` is keyed `(spectral_cube_index, wavelength)`, but the
maintainer confirmed the *values* don't vary by cube in practice) - so the
cache reuses correctly. This ruled out "the mask cache never hits" as a
hypothesis entirely.

### The real cost: full-image-sized array ops per ROI, even on a cache hit

Timestamps between consecutive `ROI cache hit` log lines within one cube
still showed ~2 seconds *per wavelength* - i.e. real work happens after the
(now-instant) mask lookup. Tracing `_tiff_stack_formula_spectrum_task`'s
per-ROI loop (the one that runs after `_build_roi_mask_cache`) found it
executes, per ROI, per wavelength: two `np.array(..., copy=True)` calls, two
boolean `&=` operations, and two boolean fancy-index extractions
(`processed[roi_mask]`) - all against the **full 806x1288 image array**
(~1.04M elements), regardless of how small the ROI's actual circle/annulus
is. With 160 ROIs x 26 wavelengths, that's ~4,160 iterations/cube each
paying full-image-scale numpy cost - the arithmetic (roughly 1-3ms x 6
operations x 160 ROIs ~ 1-2.9s/wavelength) matches the observed ~2s/wavelength
closely.

The scoped/fast path's equivalent loop (`_ome_zarr_formula_spectrum_task`,
via `_means_for`) does the identical calculation against a small local
`patch` array instead - confirmed by reading its `_selected_roi_masks_for_spectrum`
call, which is scoped to `(patch_h, patch_w)`, not the full image. This is
the second, independent reason (beyond raw I/O) the scoped path is so much
cheaper - it isn't just fewer bytes read from disk, it's also far less array
math per ROI.

### Root cause of landing on the slow path at all: an obsolete "worth it" gate

`_ome_zarr_spectrum_path_eligible` (was `_fast_spectrum_path_eligible`)
required the selected ROIs' union bounding box to cover no more than 60% of
the image (`roi_union_box_is_worth_scoping`, now removed), on the theory
that a near-full-image scoped read wasn't worth its own overhead compared to
just reading the plane directly. That theory predates the `zarrs` fix. With
160 ROIs scattered across the frame, the union box covered most of the
image, so this dataset's real 160-ROI runs *always* fell back to the
full-image path - the very case (many ROIs) where the full-image path's
per-ROI full-image-array cost above hurts most.

### Fix landed: always use the scoped path for OME-Zarr, rename both paths

Since (a) scoped reads are now cheap regardless of box size (`zarrs`), (b)
the scoped path's per-ROI loop is *also* cheaper regardless of box size (small
patch, not full image), and (c) chromatic correction here isn't cube-dependent
(so there's no per-cube reason to prefer the full-image path), the 60%-area
gate no longer protects anything and only hurts the common case of a large or
scattered ROI selection. `_ome_zarr_spectrum_path_eligible` was simplified to
just "dataset is OME-Zarr and at least one ROI is selected" - no box
computation at all. TIFF stacks are unaffected (they were never eligible for
the scoped path in the first place; no partial-read mechanism to exploit).

Renamed to reflect what each path actually specializes in (format), not a
relative "fast/slow" label that stops meaning anything once one of them is
always used for its format:

| Old name | New name |
|---|---|
| `_formula_spectrum_task` | `_tiff_stack_formula_spectrum_task` |
| `_formula_spectrum_fast_task` | `_ome_zarr_formula_spectrum_task` |
| `_fast_spectrum_path_eligible` | `_ome_zarr_spectrum_path_eligible` |
| `_prepare_fast_spectrum_payload_for_spectral_cube` | `_prepare_ome_zarr_spectrum_payload_for_spectral_cube` |
| `"SG fast task stage timing"` log line | `"SG ome-zarr task stage timing"` |
| status-bar `" [fast]"` label | `" [ome-zarr]"` |

`roi_union_box_is_worth_scoping` was deleted (its only caller is gone).
`compute_roi_union_bounding_box` stays - still used to compute the actual
scoped-read region, independent of the eligibility decision.

### Verification

Correctness (not just performance) needed its own check here, since this
changes which of two implementations computes real analysis numbers for a
case - many ROIs on OME-Zarr - that previously always used the other one.
A standalone script (not committed) built 12 ROIs scattered across a
240x360 synthetic image, 3 wavelengths with per-wavelength translation
affines (including two non-identity transforms, to stress the patch-offset
math `_ome_zarr_formula_spectrum_task`'s own docstring flags as the riskiest
spot - `np.where` coordinates on the local patch must be shifted back by the
patch's origin before a plane fit sees them in the same coordinate frame as
`roi.center_x/center_y`), and ran both `_tiff_stack_formula_spectrum_task`
and `_ome_zarr_formula_spectrum_task` on the identical input. Compared across
all three reduction methods (`mean`, `median`, `plane_fit` - the last being
the one that actually exercises the coordinate-shift code):

```
reduction=mean       worst |sample diff|=0.000e+00 worst |reference diff|=0.000e+00 worst |formula diff|=0.000e+00
reduction=median     worst |sample diff|=0.000e+00 worst |reference diff|=0.000e+00 worst |formula diff|=0.000e+00
reduction=plane_fit  worst |sample diff|=0.000e+00 worst |reference diff|=0.000e+00 worst |formula diff|=0.000e+00
```

Bit-for-bit identical (not just within tolerance) for every ROI, every
wavelength, every reduction method, plus exactly matching sample/reference
pixel counts. Expected, since this change never touches either task
function's own math - only which one gets called and when - but confirmed
rather than assumed, per this repo's rule that a change touching which code
produces real analysis numbers needs its own before/after check.

Unit tests: `tests/unit/test_lspri_roi_absorbance_multi_roi_isolation.py`
(directly exercises `_tiff_stack_formula_spectrum_task`) and
`tests/unit/test_lspri_analysis.py`, plus the broader LSPRi sensorgram/
chromatic test subset (`test_lspri_sensorgram_disk_metric_shortcut.py`,
`test_lspri_sensorgram_stop_preserves_prepared_cubes.py`,
`test_lspri_chromatic.py`, `test_lspri_busy_progress_speed_text.py`,
`test_lspri_sensorgram_cache.py`, `test_lspri_sensorgram_start_reentrancy.py`,
`test_lspri_sensorgram_time_axis.py`) - all pass (112 tests total).

Not yet done: a live "Start analysis" run on the real 160-ROI dataset to
confirm the real-world speedup. Expected: cubes should now log `SG ome-zarr
task stage timing` (not `ROI cache built/hit | shape=<full image>`), and
per-cube time should drop from the observed 15-65s down toward the
sub-second range the first follow-up section projected for the scoped path.

## Follow-up #3 (2026-09-06, same day): Follow-up #2's own fix uncovered a worse latent bug

After Follow-up #2 landed (always use the OME-Zarr scoped path, regardless of
ROI count), a real run got *worse*, not better: one cube logged **322.75
seconds** (`SG cube compute timing`), with the stage breakdown
`mask=1,268,522.2ms` dwarfing everything else (`io=2331.4ms
resample=195.0ms where=0.0ms reduce=1697.0ms`) - accumulated mask-build time
alone was ~4x the wall-clock cube time, consistent with ~4 worker threads
each independently paying a huge mask cost per wavelength.

### Root cause: `transformed_annulus_mask_for_patch` never reach-limited

Traced to `chromatic.py`'s `transformed_annulus_mask_for_patch` (used by
`_selected_roi_masks_for_spectrum` whenever `patch_origin_xy != (0, 0)` -
i.e. always, on the OME-Zarr scoped path). Unlike its full-image sibling
`transformed_annulus_mask` (which already computes a tight `annulus_reach_box`
around the ROI and only touches that small region), the patch variant's
docstring said outright: "this does not do its own reach-based shrinking
beyond the given patch... trusting the caller's box." It computed the full
annulus-distance formula over the **entire given `patch_shape`**, regardless
of how small the actual ROI circle/annulus was.

This was always somewhat wasteful, but cheap in practice before Follow-up #2,
because the 60%-area gate that fix removed used to guarantee `patch_shape`
stayed small (a "compact" ROI selection). With 160 ROIs scattered across the
frame, the union-box patch is now nearly the full 806x1288 image - and
`_fast_roi_mask_cache_entry` calls this function once per ROI *individually*
(160 times) plus once more inside the "combined" mask's own internal
per-ROI loop (another 160) = ~320 full-patch-sized computations **per
wavelength**. Measured: ~166ms per call on an 806x1288 patch for one small
ROI -> ~48.8s/wavelength -> the observed 322s cube. Follow-up #2's own fix
(justified and still correct - see that section) is what exposed this
already-existing bug at production scale; it did not introduce the bug
itself.

This is the exact class of regression [the "one fix attempted and reverted" section below](#one-fix-attempted-and-reverted-during-this-investigation-for-context)
already warned about for this file - a fix in this specific neighborhood
needs its own before/after measurement, every time, not an assumption that
"the caller already has the right box."

### Fix: teach the patch variant the same reach-limiting the full-image variant already has

`transformed_annulus_mask_for_patch` now computes `annulus_reach_box` (the
same bound `transformed_annulus_mask` and `compute_roi_union_bounding_box`
already trust elsewhere in this codebase), clips it to the given patch's own
bounds, computes the annulus formula only in that small region, and embeds
it into the zero-initialized patch-shaped array via slicing - mirroring
`transformed_annulus_mask`'s existing pattern exactly, just with a patch
offset. `transformed_disk_mask_for_patch` needed no separate change; it
already delegates to this function.

**Correctness** (standalone script, not committed): 300 randomized cases
(random patch size/origin, ROI center inside/outside/straddling the patch
edge, random inner/outer radii including 0, random similarity-transform and
identity affines) comparing the old (full-patch) computation against the new
(reach-limited) one - **0 pixel mismatches** across every case, plus the
disk-mask delegation (50 more cases) and two explicit edge cases
(`outer_radius=0`, ROI far outside the patch). Also re-ran the Follow-up #2
TIFF-vs-OME-Zarr parity script (12 ROIs, non-identity affines, all three
reduction methods) - still bit-for-bit identical after this change.

**Performance** (same script): one small ROI on an 806x1288 patch with a
real (non-identity) affine - **165.95ms -> 1.164ms per call, ~143x**.
Applied to the observed regression, that projects the 322s cube back down to
roughly 2-5 seconds (mask stage ~48.8s/wavelength -> ~0.34s/wavelength),
consistent with what Follow-up #2 was expected to deliver before this latent
bug ate the gain.

Tests: `test_lspri_chromatic.py`, `test_lspri_roi_absorbance_multi_roi_isolation.py`,
`test_lspri_analysis.py`, plus the broader LSPRi sensorgram/chromatic/roi
subset used for Follow-up #2 - 120 tests, all pass.

**Not yet done**: a live "Start analysis" run to confirm the real in-app
number matches this projection - see Follow-up #2's own "not yet done" note,
now doubly relevant.

### A related, not-yet-fixed latent issue (flagged, not touched)

`_selected_roi_masks_for_spectrum`'s **non-affine** branch (chromatic
correction off, or that wavelength's transform is identity) has the same
shape of inefficiency: it allocates and computes over the *entire* given
`image_shape` via `np.indices(...)` for every ROI, regardless of ROI size.
This is currently unmeasured and not the confirmed cause of anything (this
investigation's real dataset has chromatic correction on with non-identity
transforms, so it never hit this branch) - flagging it rather than fixing it
blindly, per this doc's own repeated lesson about not touching this
neighborhood without a real measurement in hand first. Worth a look if a
future "X is slow" report turns out to have chromatic correction off.

## Follow-up #4 (2026-09-07): after the mask fix, `io=` is now the dominant cost - and confirms the re-export is still the real remaining lever

With Follow-up #3 landed, a real run dropped from 322.75s/cube to **~5-6s/cube**
(cube 33: 5404.3ms, cube 34: 5679.2ms, cube 40: 5932.5ms - `SG cube compute
timing`). `mask=` dropped to ~30ms/cube (confirms the reach-limiting fix
works in production, not just in the standalone script). The new dominant
term is `io=` (~4000-5700ms accumulated/cube), with `reduce=` a distant
second (~1600-2000ms accumulated/cube).

### Ruled out: disk bandwidth

Raw multi-threaded file reads of the actual shard files (bypassing zarr
entirely - plain `open().read()`) scale cleanly with concurrency: 8 reads of
2MB each took 37.9ms at 1 worker and 7.1ms at 8 workers. The disk/OS layer
has plenty of headroom; whatever is costing ~5s/cube is happening inside the
zarr/zarrs read path itself, not the storage device.

### Ruled out: the app's own outer thread pool actually helping

`_load_wl` (the per-wavelength worker inside `_ome_zarr_formula_spectrum_task`)
runs under a `ThreadPoolExecutor(max_workers=4)`. Measured wall-clock for 26
scoped reads of a near-full-image box (795x1275px, matching what a
160-scattered-ROI union box looks like) on **fresh, never-before-read cubes**
(to avoid OS file-cache warmth skewing repeat reads):

| Workers | Per-call avg | Wall for 26 reads |
|---|---|---|
| 1 | 74.9ms | 1949ms |
| 2 | 153.4ms | 1996ms |
| 4 | 302.5ms | 2049ms |
| 8 | 805.9ms | 2855ms |

Wall-clock is essentially **flat from 1 to 4 workers** (~1950-2050ms) and
gets *worse* at 8. Per-call latency scales up almost exactly with worker
count (2x workers -> ~2x slower per call), meaning total throughput is
capped regardless of how many Python threads request reads concurrently -
adding more doesn't help, and oversubscribing (8) actively hurts. A single
batched call for all 26 wavelengths at once
(`arr[cube, 0:26, y0:y1, x0:x1]`) was *slower* still (~2450-2500ms) than
either the sequential or 4-worker loop. **No worker-count or batching tweak
found here beats the current default** - this isn't a tuning problem.

### Why: chunk count, still - just at a much smaller constant

The scoped box (795x1275px at 50px chunks) touches ~16x26 = 416 chunks per
wavelength; 416 x 26 wavelengths = **~10,800 chunks touched per cube**. Back
in the very first follow-up section, `zarrs` cut per-chunk dispatch cost by
roughly two orders of magnitude (measured ~1.5-2ms/chunk under the default
pipeline vs a small fraction of a millisecond under `zarrs`) - but it did not
make dispatch cost *zero*. At ~10,800 chunks, even a much smaller per-chunk
constant still adds up to seconds. This is the same root fact the original
investigation opened with (read cost scales with chunks touched, not bytes
needed) - `zarrs` and the mask fix each closed a different multiplier on top
of it, but neither one changes the chunk *count* a near-full-image scoped
read has to touch at this dataset's current 50px chunk size.

**This directly confirms the pending re-export recommendation (top of this
doc) is still the one lever that closes the remaining gap** - it wasn't
"replaced" by the zarrs/mask fixes, those just made everything else fast
enough that chunk count is now the only thing left. At chunk size >=1300px,
each wavelength's read becomes exactly 1 chunk (26 total instead of ~10,800),
which the very first measurements in this doc project to bring `io=` down to
roughly the 20-30ms/cube range the one-chunk-per-plane benchmarks already
demonstrated on this same file.

**Not yet done**: the re-export itself. Everything else identified in this
investigation (three original fixes, the `zarrs` pipeline, the OME-Zarr-
always-scoped-path gating fix, the patch-mask reach-limiting fix) is now
landed; this is the only remaining item.

## Follow-up #5 (2026-09-07): the "s/cube" speed readout was itself misleading

Separately from the compute-time bottleneck above: the status bar's
`X.XX s/cube` readout during "Start analysis" is a **whole-run cumulative
average** (`elapsed_since_start / cubes_done_so_far`). A run that opens with
a burst of near-instant disk-cache-hit cubes (see the "clean the cache"
question this investigation also answered - `<dataset>/analysis/
measurement_backup.h5` persists results across app restarts) reports a
falsely fast average for a long time afterward, even once every subsequent
cube is a slow, freshly-computed one - exactly what made this session's
"still slow, but the counter says otherwise" report hard to read at a
glance.

Fixed in `main_window.py`: `_update_busy_progress` now maintains a trailing
`BUSY_PROGRESS_SPEED_WINDOW_SECONDS` (10s) window of `(elapsed, items_done)`
samples (`_recent_busy_progress_rate`) and uses that windowed rate for the
speed readout specifically - elapsed/ETA still reflect the whole run (that's
correct for those). Falls back to the old whole-run-average behavior until
the window has at least ~1 second of span to compute from, so it degrades
gracefully at the very start of a run. `_format_busy_detail_text` (the
existing pure-function test seam) gained an optional
`recent_seconds_per_item` parameter for this - unset, it behaves exactly as
before, which is why every pre-existing test in
`test_lspri_busy_progress_speed_text.py` passed unmodified. New tests added
for the windowed-rate function itself, including the specific "fast burst
followed by a slow stretch" scenario this fix targets.

## Follow-up #6 (2026-09-07): one spectrum-compute method for both formats

Follow-up #4 asked whether TIFF stacks - having no chunking at all - are
effectively "one chunk per plane" already, and whether that means the
TIFF-specific full-image task (`_tiff_stack_formula_spectrum_task`) and the
OME-Zarr-specific scoped task (what was `_ome_zarr_formula_spectrum_task`)
should really be two separate implementations at all.

### Measured before proposing anything

- A real TIFF plane read (`tifffile.imread`, 806x1288 uint16) is **~2.2ms** -
  as fast as the best-case one-chunk-per-plane OME-Zarr benchmark from the
  very first section of this doc. TIFF's only real "chunk" genuinely is the
  whole plane.
- On identical TIFF-backed data (806x1288, 160 scattered ROIs, real
  chromatic affines), the scoped task took **15,148ms** vs the full-image
  task's **30,979ms** for the same 26-wavelength/160-ROI workload - **2x
  faster**, even at 100% box coverage (no I/O advantage at all in this
  case - `dataset_load_plane_roi`'s TIFF fallback always reads the whole
  file regardless of requested region). The win is purely the scoped task's
  per-ROI masking/reduction loop operating on a small `patch` array instead
  of full-image-sized arrays - the same inefficiency Follow-up #2 found and
  left unfixed for the full-image task specifically. Bit-for-bit identical
  output (`0.000e+00` diff) with `flatten_background_enabled` off.
- One real wrinkle, found and root-caused before shipping: with
  `flatten_background_enabled=True` and binning>1, the scoped task's
  background baseline goes through `_background_baseline`'s binned-median
  shortcut (`preprocess.py`) instead of an exact full-resolution median - a
  pre-existing, already-documented, deliberate approximation, true for
  every current OME-Zarr + background-flatten user today, unrelated to this
  change. On a small 120x160 synthetic image it measured a ~0.46 raw-
  intensity offset; **re-measured at realistic scale (806x1288, the real
  default `sigma_px=48.0`/`binning=2`, 160 ROIs)** it's **~0.005 out of a
  background level of ~361 - about 0.001% relative error** (0.003% for a
  more typical compact 8-ROI selection). The approximation genuinely is
  negligible at real settings, matching `_background_baseline`'s own
  docstring claim ("no meaningful variation at scales finer than one binned
  cell"). **Decision: extend this existing approximation to TIFF too**
  rather than special-case around it.

### Design: one method, chunk size decided by format in one small place

New function `spectrum_read_region` (`analysis_tasks.py`) is the **only**
place format enters the picture anywhere in this change:

```python
def spectrum_read_region(dataset, image_height, image_width, selected_rois,
                          reference_outer_radius_px, affine_matrices):
    if not dataset.is_ome_zarr:
        return (0, 0, image_width, image_height)          # TIFF: whole plane
    return compute_roi_union_bounding_box(                  # OME-Zarr: ROI box
        selected_rois, reference_outer_radius_px, affine_matrices,
        image_height, image_width,
    )
```

Both places that used to duplicate the "shape -> ROI union box" computation
now call this instead: `_prepare_scoped_spectrum_payload_for_spectral_cube`
(the payload builder) and `_build_shared_wavelength_geometry` ([λ] mode,
`analysis_chromatic_geometry_mixin.py`). Everything downstream of the
returned box - mask building, pixel extraction, reduction - is unchanged
and was already 100% format-agnostic.

### Removed (not kept as a fallback - genuinely unreachable once
### `spectrum_read_region` always returns a usable region for both formats)

- `_tiff_stack_formula_spectrum_task` (`analysis_tasks.py`)
- `_prepare_formula_spectrum_payload_for_spectral_cube` and
  `_cached_sensorgram_spectral_cube_payload` (`analysis_worker_mixin.py`),
  plus their now-orphaned cache (`_sensorgram_spectral_cube_payload_cache`,
  `SENSORGRAM_SPECTRAL_CUBE_PAYLOAD_CACHE_SIZE`) and both `main_window.py`
  delegators
- The `if use_ome_zarr_path: ... else: ...` branch in
  `_start_sensorgram_worker`, and the "fast payload is None -> fall through
  to slow path" branch in `_prepare_formula_spectrum_payload` - the latter
  was analytically redundant: `compute_roi_union_bounding_box` can only
  return `None` with a non-empty ROI selection when every selected ROI's
  reach falls entirely outside the image, a case where the removed
  full-image path would equally have produced empty/NaN results

Renamed for the same reason as Follow-up #2/#3's table - "ome_zarr" in a
name now used for every format is misleading:

| Old | New |
|---|---|
| `_ome_zarr_formula_spectrum_task` | `_scoped_formula_spectrum_task` |
| `_ome_zarr_spectrum_path_eligible` | removed - replaced by a plain `bool(selected_source_rois) and dataset is not None` guard |
| `_prepare_ome_zarr_spectrum_payload_for_spectral_cube` | `_prepare_scoped_spectrum_payload_for_spectral_cube` |
| `"SG ome-zarr task stage timing"` log line | `"SG scoped task stage timing"` |
| status label `" [ome-zarr]"` | removed (no longer format-conditional) |

A side effect worth knowing about: [λ] mode (precompute the shared
box/affine once per run instead of per-cube) was gated on the same removed
branch purely because it was only ever wired into the scoped payload
builder, not because it's OME-Zarr-specific in principle. It's now
available for TIFF runs too when "time independent" is on - verified via
`spectrum_read_region`'s own unit coverage (below), not yet via a live
TIFF+[λ]-mode run; worth a quick manual check if that combination gets used.

### Verification

- `spectrum_read_region` unit-checked directly: TIFF dataset -> whole
  plane, OME-Zarr dataset -> matches `compute_roi_union_bounding_box`
  exactly, degenerate image size -> `None`.
- Re-ran the end-to-end parity check post-refactor: `_scoped_formula_
  spectrum_task` fed a full-plane box vs an ROI-scoped box, same 160-ROI/
  26-wavelength TIFF-backed data, same real chromatic affines - **still
  bit-for-bit identical** (`0.000e+00`), confirming the collapse/rename
  didn't change any actual computation.
- Rewrote `tests/unit/test_lspri_roi_absorbance_multi_roi_isolation.py`
  (previously called the now-deleted TIFF task directly) to call
  `_scoped_formula_spectrum_task` - passes unchanged otherwise.
- Full touched-test run: 139 tests across the multi-ROI isolation,
  analysis, chromatic, sensorgram, and busy-progress suites - all pass.
- Repo-wide grep swept clean of every removed/renamed identifier (including
  several already-stale comment references to even older names from before
  this investigation, cleaned up while in the neighborhood).

## Follow-up #7 (2026-09-07): the `zarrs` pipeline crashed the whole process - disabled

A real launch of the app (via the Suite Launcher, real dataset from this
investigation, a restored previous session with 160 ROIs/chromatic
transforms) crashed the entire process ~22 seconds after startup, exit code
3221226356 = `0xC0000374` = Windows `STATUS_HEAP_CORRUPTION`. No Python
traceback - a hard native-level crash, not a normal exception. The crash log
shows the very first interactive image load completing successfully
("Image raw load | queue=0ms read=98ms", then "Image process stages"
finishing normally) with nothing logged afterward - the crash happened
somewhere past that point, during ordinary startup, not during a bulk
"Start analysis" sweep.

### Why `zarrs` is the prime suspect

This is the first crash of this kind seen at any point in this
investigation, appearing immediately after `zarrs` was wired in (Follow-up
#1) as the process-wide default zarr codec pipeline. `zarrs`' own docs
confirm its Rust side runs on a global Rayon thread pool (`threading.
max_workers` config controls its size, defaulting to the number of logical
CPUs) - a second, independent thread pool layered underneath this app's own
threading (a `QThreadPool`-driven interactive image loader, for the
"Image raw load | queue=..." line seen right before the crash, plus the
separate `ThreadPoolExecutor` used during "Start analysis"). `zarrs-python`
has at least one other documented concurrency bug in this same area -
[issue #171](https://github.com/zarrs/zarrs-python/issues/171), a deadlock
when a forked child process reads through `ZarrsCodecPipeline` after the
parent already decoded a chunk. Different platform (Linux forking vs.
Windows heap corruption) and different symptom, but the same underlying
theme: this library's interaction with a host application's own concurrency
model is a real, active source of bugs, not a hypothetical one - not
surprising as a risk, in hindsight, for a pre-1.0 native-code bridge
(exactly the class of dependency this repo's own pinning policy already
treats as higher-churn/higher-risk).

Not yet root-caused to a specific trigger - the exact code path or thread
interleaving that corrupted the heap here isn't confirmed, only the strong
circumstantial case above (timing, the only new native dependency, a known
sibling bug in the same library's threading model).

### Mitigation: disabled, not removed

`_configure_zarr_codec_pipeline` (`io/dataset.py`) no longer calls
`zarr.config.set(...)` - the activation line is commented out with a dated
explanation, `zarrs` stays imported (so the availability check and the
dependency itself are unaffected) and pinned in `pyproject.toml` (comment
updated to match), so re-enabling later is a one-line change once this is
actually root-caused. Every other fix from this investigation (the mask
reach-limiting fix, the always-scoped-path gating, the one-method
unification, the busy-progress speed window) is untouched and still active
- none of them depend on `zarrs` specifically, only on the OME-Zarr chunk
read machinery generally, which still works correctly (just back to
zarr-python's own default, slower, but stable pipeline).

**Practical effect**: OME-Zarr reads go back to the (still `zarr>=3.3`,
still benefiting from Follow-up #1's other fix - the `codec_pipeline.
batch_size`/`async.concurrency` config tuning was *not* part of what got
disabled) default pipeline - full-plane and scoped reads are slower than
the `zarrs`-accelerated numbers quoted earlier in this doc, but the app
should be stable again. The chunk-size re-export (top of this doc) remains
unaffected and is now, temporarily, the *only* lever left for closing the
`io=` gap until `zarrs` can be safely re-enabled.

### Before re-enabling `zarrs`

1. Reproduce the crash with more diagnostic capability - Windows'
   `HeapEnableTerminationOnCorruption` fires close to the actual corrupting
   write, but a genuinely useful stack trace likely needs a native debugger
   attached (e.g. WinDbg) at crash time, or Application Verifier's heap
   checks enabled for the process - beyond what's practical to set up
   blind; consider asking the maintainer to reproduce with one of these
   attached if the crash is reproducible on demand.
2. Check `zarrs-python`'s issue tracker for anything closer to this
   specific symptom (Windows, heap corruption, PyQt/Qt threading) - only
   issue #171 was found this session, and it's a different symptom on a
   different platform; a fix may already exist for a version newer than
   the `0.2.3` pinned here.
3. If re-enabled, specifically test the exact concurrent access pattern
   that was active here - an interactive image load (QThreadPool) racing
   the app's own background work - not just the single-threaded/sequential
   benchmarks that validated the original ~5-7.5x numbers, since those
   never exercised this failure mode at all.

## Follow-up #8 (2026-09-07): a live chunk-size read estimate in the Export section, replacing a wrong-place Summary version

### The Summary-section version (Phase 8) was a misread of "dataset/export section"

The original request for a read-performance hint literally said "in the
dataset/export section" - misread as "the Dataset panel's Summary
sub-section" and built there instead (a new "Read performance" line next
to the existing Chunk/Shard row, sourced from the array's actual on-disk
`.chunks`). The follow-up correction was explicit: the ask was always for
the **Export** sub-section, tied live to the chunk-size spinner a user is
about to commit to for a *new* export - not a static description of
whatever's already on disk for the *currently open* dataset.

Removed rather than kept alongside the new version: the two numbers answer
different questions ("how is my open dataset chunked, right now" vs. "what
would this chunk-size setting produce") and could disagree in a confusing
way - e.g. loading an old 50px-chunked export while the Export spinner
still holds a leftover 1300px value from a previous session would show "1
chunk/plane (optimal)" in Summary right next to a spinner implying
something totally different for the *next* export. One number, in the
place actually asked for, beats two that can contradict each other.
`OmeZarrExportSummary.chunk_shape_px`, `ome_zarr_read_performance_hint()`,
and `summary_read_perf_label` were all removed (`io/dataset.py`,
`gui/main_window.py`) along with their test file; nothing else from this
investigation's other fixes depended on them.

### What replaced it: `ome_zarr_chunk_estimate_label`, next to the chunk-size spinner

New label (`main_window.py`, `dataset_ome_zarr_options_layout`) sits
directly after `ome_zarr_chunk_spin` and stays current two ways: on every
spinner change (`ui_state_manager.sync_ome_zarr_chunk_controls` ->
`_sync_ome_zarr_chunk_estimate_label`), and for free on every image refresh
(`image_render_manager.py`'s per-wavelength refresh already called
`_sync_ome_zarr_chunk_controls()` for the existing chunk-size guide
overlay, so dataset load, wavelength switching, and cube navigation all
keep the new label in sync with no separate wiring needed).

Two-stage text, so something useful shows up instantly and gets more
precise a moment later:
1. **Immediately** (pure geometry, no I/O): `"-> {chunks_per_plane}
   chunks/plane"`, from `ceil(height/chunk) * ceil(width/chunk)` against
   the currently displayed image's actual dimensions.
2. **After a one-time background calibration finishes** (typically well
   under a second): upgraded to `"-> {chunks_per_plane} chunks/plane,
   ~{estimated_ms:.0f}ms/plane read (estimated)"`.

### The calibration: synthetic, linear, backgrounded

Per this app's own maintainer's steer ("simple estimate... they scale
linearly with chunk size... do some synthetic ones... several values and
then linear interpolate"): `calibrate_zarr_read_overhead_ms()` builds a
throwaway 400x400px sharded zarr array (50px inner chunks, one shard
covering the whole array - matching a real export's shard shape, since
Follow-up #1's root-cause finding was that the overhead is *per-inner-
chunk-within-an-already-open-shard*, not per-file) in a temp directory,
times a warm read at `{1, 2, 4, 8}` chunks-per-side (one untimed warm read
first, per probe, so the timed read isn't paying for the OS's first-touch
page-cache miss), and fits `elapsed_ms = fixed_ms + per_chunk_ms *
chunk_count` via `np.polyfit`. On the development machine this produced
`fixed_ms ~= 2.4`, `per_chunk_ms ~= 0.36` - consistent with the per-chunk-
dispatch-dominated cost model this whole investigation has been measuring
since Follow-up #1 (a few chunks costs about the same as one; it's the
marginal cost of each *additional* chunk that drives the total).

Runs through a background `FunctionWorker`
(`DatasetController._ensure_zarr_read_overhead_calibration`), never
blocking the GUI thread, attempted at most once per session (a separate
`_zarr_read_overhead_calibration_attempted` flag distinguishes "haven't
tried yet" from "tried and failed" - `calibration is not None` alone would
have retried forever after a legitimate failure). Trimmed from an initial
1.7s version (800px array, 5 probes) down to ~156ms warm (400px, 4 probes)
after profiling each stage separately - array creation and the random-data
fill turned out to dominate, not the reads themselves, so the fix was
shrinking the synthetic array, not the probe count.

### Verified against the real dataset

For the maintainer's actual 806x1288px dataset at the 50px chunk size
already on disk: `442 chunks/plane` (exact, matches the measured figure
used throughout this doc), with the calibration above upgrading it to
`~162ms/plane read (estimated)`. That's in the right neighborhood of this
doc's own directly-measured **690ms** full-plane number, but the two aren't
expected to match exactly - the 690ms figure was measured with a specific
pipeline at a specific point in this investigation, while the estimate
reflects whatever pipeline is active on the calibrating machine *right
now* (currently the default zarr-python pipeline, since `zarrs` is
disabled per Follow-up #7) - it's a live, per-machine calibration, not a
lookup against a fixed historical measurement.

### Is 442 chunks/plane, and ~120k chunks dataset-wide, "that bad"?

Asked directly, with a back-of-envelope: `442 x ~331 planes ~= 120,000`
chunks total in the dataset. Order of magnitude:
- **If every plane were read in full** (the interactive viewer and
  wavelength switching always do this; a bulk "Start analysis" sweep only
  approaches this when nearly every ROI is selected, since Follow-up #6's
  scoped path otherwise touches just the ROI-union bounding box, not the
  whole plane): ~120,000 chunk dispatches x ~0.36ms marginal cost ~= **~43s**
  of pure per-chunk overhead spread across the *whole* dataset - not
  nothing, but a small fraction of a full evaluation session, and it's
  overhead *on top of* actually-necessary decompression/compute time, not
  instead of it.
- **A chunk size >= the image's longer dimension (>=1288px)** collapses
  every full-plane read to exactly 1 chunk - 331 chunks dataset-wide
  instead of ~120,000, a ~360x reduction in dispatch count - and remains
  this doc's standing top recommendation (see "The pending fix" section
  near the top) for the next re-export.
- Bottom line: 442/plane is worse than necessary, and re-exporting at a
  larger chunk size is clearly still worth doing, but it is not a
  catastrophic value on its own. The new label exists precisely so this
  judgment call is visible *before* an export commits to a chunk size,
  instead of only discoverable after the fact via a slow real run.

### Addendum (2026-09-07, same day): a second field for the dataset-wide total

The back-of-envelope multiplication above (`442 x ~331 planes`) is exactly
the calculation a user would otherwise have to do by hand every time they
consider a chunk size - so it's now automatic. A second label,
`ome_zarr_chunk_total_label`, sits in its own row directly under the
per-plane one (`dataset_ome_zarr_chunk_total_row`, right below
`dataset_ome_zarr_options_row` in the Export section's layout) and shows
the same two-stage text scaled across every image in the *currently loaded*
dataset: `"-> {total_chunks:,} chunks total ({image_count:,} images)"`,
upgraded to add `", ~{total_seconds:.1f}s read (estimated)"` once
calibration finishes. "Image" here means one (spectral cube, wavelength)
plane - the same unit `ImageDataset.records`/`image_count` already use
elsewhere in this module (see `shard_mode="per_image"`'s "1 image per
file" meaning exactly one such plane) - so this total is exactly the
"chunks in total" half of the question above, computed from the real
dataset instead of a hand-typed guess. The total time estimate is a
straight sum of `image_count` independent per-plane estimates (each plane
read pays its own fixed dispatch cost - no assumed discount for reading
many back to back), via `estimate_ome_zarr_export_dataset_total_read` in
`io/dataset.py`, sharing `calibrate_zarr_read_overhead_ms`'s calibration
with the per-plane label rather than running a second one.

## Follow-up #9 (2026-09-07): a TIFF-stack run was slow too - two unrelated bugs, neither is chunking

A real 12-cube "Start analysis" run on a **TIFF stack** (no OME-Zarr, no
chunking at all) still took over 3 minutes - proof that everything above in
this doc, however real, was never the whole story. Diagnosed directly from
the run's own stage-timing log (`logs/lspr_imaging_<session>.log`, already
emitted by the "SG scoped task stage timing" line - see "Debug tooling"
above) rather than fresh instrumentation:

| Cube | io= | mask= |
|---|---|---|
| 0 | 80ms | **78,608ms** |
| 1-11 (avg) | **5,688ms** | 30-45ms |

Total ~153s, matching the reported "over 3 min" once GUI/dispatch overhead
is added. Two distinct, previously-uninvestigated bugs, confirmed by direct
profiling against the real dataset's files and a synthetic 160-ROI
reproduction - not guessed at from the log alone:

### Bug A: nested thread pools for TIFF reads

`analysis_tasks.py`'s per-wavelength read already runs on a
`ThreadPoolExecutor(max_workers=4)` (one worker per concurrent wavelength).
For a TIFF file, each of those 4 workers landed in `io/dataset.py`'s
`_load_image_array_uncached`, which called `tifffile.imread(path,
maxworkers=os.cpu_count())` - spinning up its *own* internal decode thread
pool per file, on top of the outer one. Up to 4 x 8 = 32 threads contending
for 8 cores for what should be a ~2.5ms plane read. Measured directly on
the maintainer's real dataset files: wrapping 27 real per-wavelength reads
in the app's own `ThreadPoolExecutor(max_workers=4)` took ~1.1s with the
nested `maxworkers=8` inner pool, vs ~0.6s with a small fixed inner worker
count - and the real run's ~5.7s/cube average suggests the gap widens
further under the full app's concurrent load (GIL/scheduling contention
from other threads' resample/mask/reduce work happening at the same time).
Mirrors a fix already applied to the sibling export path
(`_load_image_array_native`'s `maxworkers=2`, with the same "N workers x
cpu_count threads pile up" reasoning already in its comment) that was never
carried over to the analysis-read path. Fixed: `_load_image_array_uncached`
now also uses a fixed `maxworkers=2` instead of `os.cpu_count()`.

### Bug B: an unscoped full-image mask grid, once per ROI, only when chromatic correction is off

The much bigger of the two. `analysis_tasks.py`'s
`_selected_roi_masks_for_spectrum` had two implementations depending on
whether chromatic correction was active:
- **Chromatic on**: `transformed_annulus_mask`/`_for_patch`
  (`processing/chromatic.py`) - already reach-limited by Follow-up #3
  earlier this session (`annulus_reach_box` computes a tight local window
  around each ROI's own center, not the whole image/patch).
- **Chromatic off** (the common case, and what this run used): a hand-rolled
  branch built directly in `analysis_tasks.py` that computed a full
  `image_height x image_width` `np.indices` distance grid **per ROI** when
  building the per-ROI mask cache (`_fast_roi_mask_cache_entry`'s
  `per_roi_masks` loop) - O(patch area x ROI count) instead of O(ROI count
  x ROI area). Never got the same reach-limiting treatment as the
  chromatic-on path.

Profiled directly (cProfile) against a synthetic 160-ROI/806x1288
reproduction: 84% of the time was the raw elementwise arithmetic over the
full patch, repeated 160x, at ~30ms/ROI (~4.8s for one single-threaded
mask-cache build) - consistent with a 27-wavelength cube (each wavelength
its own cache entry when chromatic is on; here, with chromatic off, all 27
wavelengths' worth of `_load_wl` calls raced to build the *same* cache
entry under concurrent access before the first one finished and populated
it) producing the observed 78.6s one-time stall on cube 0 and near-zero
cost on every cube after (cache hit).

**Fix**: deleted the separate "chromatic off" branch entirely and route it
through the same reach-limited `transformed_disk_mask`/`transformed_
annulus_mask` functions the chromatic-on path already used, passing
`identity_affine_matrix()` when there's no real transform - one
implementation instead of two, and the fast one wins for both cases.
`annulus_reach_box`'s per-ROI cost (a 2x2 SVD + a handful of point
transforms) is negligible next to the full-grid cost it replaces even for
an identity matrix.

**Correctness verification** (required before touching this - it decides
which pixels feed into every computed sample/reference mean): compared old
vs. new output pixel-for-pixel against a 164-ROI synthetic set (random
placement/radii plus deliberate edge cases - a zero-inner-radius reference,
`reference_geometry_type="none"`, ROI centers at the image corners) for
both a full-image call and an offset-patch call. `roi_mask` matched exactly
in both cases; `reference_mask` matched exactly for the offset-patch case
and differed by **one pixel** (out of ~1.038M) in the full-image case.
Traced to its exact cause: that pixel sits at distance `9.99999129px` from
its nearest ROI's center against an inner radius of exactly `10.0px` - off
by `0.0000087px`, i.e. mathematically *on* the boundary. The old code
computed distance in `float32`; the new path (reusing the chromatic-on
math) computes in `float64` - more precise, not less, but landing on the
opposite side of the `<` comparison at a boundary this exact. A pixel this
established to be genuinely ambiguous, in an annulus that normally spans
hundreds of pixels, changes any reduced mean by an amount several orders of
magnitude below normal shot noise - flagging per this repo's own rule for
any measurable change to computed values, but not a reason to hold up the
fix.

**Measured speedup** (same synthetic 164-ROI reproduction, single-threaded,
matching the profiling methodology above): **26.70ms/ROI -> 1.83ms/ROI**,
~14.6x. For 160 ROIs, roughly 4.3s -> 0.29s per mask-cache build - the
dominant chunk of the observed 78.6s cube-0 stall.

### Verification pending

Both fixes are mechanical/isolated enough to ship without a check-in for
the threading one (a concurrency parameter, no computed-value change) and
with the pixel-level correctness check above for the mask one. Full
`pytest tests/unit tests/integration -k lspri` re-run to confirm no
regressions, and the maintainer re-running the same real 12-cube TIFF
analysis for a genuine before/after from real data (not just the synthetic
reproduction above) - the actual point of this exercise per this doc's own
"Verify a fix with a real before/after measurement" rule.

## Follow-up #10 (2026-09-07): fresh audit after Follow-up #9 - three more fixes, one gap quantified and deliberately left unfixed

A broader "recheck the whole pipeline for bottlenecks/architecture issues"
pass, independent of any specific slowness report, done after Follow-up #9
landed. Three small, low-risk fixes shipped; one real gap was measured and
explicitly *not* turned into a code change, because fixing it would mean
trading off scientific correctness for speed - a decision for the maintainer,
not something to bury in a "performance fix."

### Fix: worker_count cap lowered from 8 to 4

`_scoped_formula_spectrum_task`'s per-wavelength `ThreadPoolExecutor` used
`min(cpu_count // 2, 8)`, reachable on any 16+ logical-core machine. This
directly contradicts Follow-up #4's own measurement earlier in this doc: wall
time for 26 real reads was flat from 1-4 workers (~1950-2050ms) and *worse*
at 8 (2855ms) - the zarr dispatch layer is the bottleneck, not disk
bandwidth, and more threads just contend for it harder. That finding is still
current (the `zarrs` pipeline that might change it is disabled per
Follow-up #7). `_sensorgram_metric_task`'s own prep-phase pool a few hundred
lines below already used `min(4, cpu_count)` - this brings the main
per-wavelength loop in line with it. Re-measure if `zarrs` is ever
re-enabled; its different I/O characteristics might change the optimum.

### Fix: redundant mask rasterization in `_fast_roi_mask_cache_entry`

The mask-cache build computed the "combined" selection mask via a *second*,
separate `_selected_roi_masks_for_spectrum` call over every selected ROI at
once, in addition to the per-ROI loop that was already computing each ROI's
own mask individually - rasterizing every ROI's sample circle twice per
cache build. Worse: the combined call's *reference*-mask half was never read
by any caller (the one call site unpacks it as `_unused_reference_mask`) -
pure wasted work.

Fixed: `combined_roi_mask` is now built by OR-ing the already-computed
per-ROI sample masks together; the combined reference mask isn't computed at
all (the cache entry's `"combined"` value is now `(combined_roi_mask, None)`).
Correctness verified directly: a 25-ROI synthetic case comparing the old
"recompute over all ROIs" mask against the new "OR of independently-computed
per-ROI masks" - `np.array_equal` true (bit-identical, as expected: both are
the same union of the same per-ROI `transformed_disk_mask` calls, just
computed once instead of twice). Measured ~20-30% avoidable overhead in the
mask-cache-build step on a 160-ROI/806x1288 synthetic case. Since this build
is cached per unique (patch, ROI-set, affine) combination - once per run when
chromatic correction is off, once per wavelength when it's on - the real
saving is a one-time few-hundred-ms-to-low-seconds amount per run, not a
per-cube recurring cost.

### Fix: MainWindow's global event-filter leak (not part of "Start analysis" specifically, found in passing)

`main_window.py`'s `__init__` calls `app.installEventFilter(self)`
(registering the window as a filter for *every* Qt event in the whole
process), with no matching `app.removeEventFilter(self)` anywhere, including
`closeEvent`. Harmless for real single-window usage (the filter just lives as
long as the process does). Real impact: every test file in this repo's suite
does `QApplication.instance() or QApplication([])`, meaning all 19+ test
files that construct a `MainWindow` share one process-wide `QApplication` -
so across a full test session, every `MainWindow` instance ever constructed
stays registered as a permanent filter, still receiving every subsequent Qt
event anywhere in the whole session. This was actually observed: running
`pytest tests/unit tests/integration -k lspri` threw repeated
`AttributeError: 'MainWindow' object has no attribute '_image_interaction'`
from inside `eventFilter`, silently - PyQt6 swallows exceptions raised inside
an event filter rather than crashing, so pytest still reported the run as
516 passed even while this fired dozens of times. Root cause: a torn-down
MainWindow instance from an earlier test still receiving events via its
zombie filter registration. Fixed with one line in `closeEvent`:
`app.removeEventFilter(self)`. Full `-k lspri` suite re-run afterward to
confirm no regressions and that the AttributeError spam is gone.

### Measured, not fixed: `flatten_background_enabled` bypasses every OME-Zarr scoped-read optimization in this entire doc

`_scoped_formula_spectrum_task`'s `_load_wl` reads the *full plane*
unconditionally whenever `preprocessing.flatten_background_enabled` is on,
regardless of how small the ROI selection's own scoped box would otherwise
be - already known qualitatively ("same cost as the slow path", Follow-up #6
above), now quantified: for a compact ROI selection (~180x180px union box,
e.g. 8 clustered ROIs), a synthetic-zarr benchmark measured the scoped box
read at **14x faster** than the full-plane read. Every `io=` win from every
earlier follow-up in this doc is unavailable the moment this setting is on.

**Investigated as a possible fix, deliberately not implemented.** The
"thread the region through" half is already done (Follow-up #6's
`region=box` plumbing into `apply_preprocessing`/`flatten_background`). What
remains is a genuine algorithmic requirement, not an oversight:
`flatten_background`'s `baseline` value
(`float(np.median(background_full[valid_mask]))`) is a whole-image median
statistic, added back into every returned pixel - not a quantity that decays
with distance the way the Gaussian-blurred background estimate itself does.
Scoping the raw read to a padded box around the ROI selection would change
that baseline for any image whose ROI-cluster region and the rest of the
frame differ in average brightness - a real, image-dependent shift in
computed absorbance values, not something boundable with a fixed safety
margin the way Follow-up #6's binned-median resolution shortcut was. This is
a correctness/scientific-validity trade-off (this repo's priority #1),
deliberately left as a flagged gap rather than a silent performance fix - if
this is worth pursuing, it needs the same "measure the actual worst-case
discrepancy on real data" treatment Follow-up #6 gave its own approximation,
which is a dedicated task, not a quick patch.

## One fix attempted and reverted during this investigation (for context)

An earlier attempt mirrored the historical `PlotManager.roi_area_masks()`
fix (per-ROI bounding box instead of a full-image distance grid) into
`_formula_spectrum_task`'s `_build_roi_mask_cache`. Measured directly before
shipping: **28 seconds** for one call that should take ~130ms - 100x worse,
not better. Root cause: the affine-correction code path
(`transformed_disk_mask`/`transformed_annulus_mask` in `chromatic.py`)
already does its own per-ROI reach-based box shrinking internally
(`annulus_reach_box`); wrapping it in another outer box forced it through a
different internal branch (`transformed_annulus_mask_for_patch`, which
explicitly skips its own reach-shrinking, trusting the caller's box) with a
box sized for the *whole selection* instead of one ROI. Reverted
immediately; the lesson (measure before trusting a "this pattern worked
before" plausibility argument) is in this doc's sibling discussion about
CLAUDE.md process rules.
