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
   compute timing` and `SG fast task stage timing` lines (DEBUG level -
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
- **ROI selection width changes which code path runs at all.** A selection
  spread across most of the image (e.g. all 160 ROIs on this dataset)
  disqualifies the "fast" scoped-read path (`_fast_spectrum_path_eligible`'s
  `roi_union_box_is_worth_scoping`, >60% of image area) and falls back to
  the full-image path (`_formula_spectrum_task`, not
  `_formula_spectrum_fast_task`) - a *different* function with different
  performance characteristics. All the OME-Zarr chunk-size findings above
  apply to both paths (both ultimately call the same zarr read machinery),
  but the "SG fast task stage timing" debug line only appears for the fast
  path - the full-image path logs "ROI cache built/hit | shape=<full image>"
  instead. Check which one is actually running (via that log line) before
  interpreting stage-by-stage numbers.

## Debug tooling added for this investigation (still in the codebase)

- **`Ctrl+Shift+F9`** (`main_window.py`) triggers Start/Stop analysis via
  the same code path as the button, auto-enabling the Analysis section and
  selecting every ROI table row if none are already selected. Added because
  `analysis_run_button` is a bare `ClickableIconLabel` (`QLabel`) with no
  accessible Invoke pattern - unreachable by UI-automation tooling or
  screen readers. Useful for any future scripted reproduction of a
  performance report without driving the GUI by hand.
- **`SG fast task stage timing`** debug log line (`analysis_tasks.py`,
  `_formula_spectrum_fast_task`) - per-cube accumulated `io=`/`resample=`/
  `mask=`/`where=`/`reduce=` breakdown, zero-cost when not read (a few
  `time.perf_counter()` calls + one `logger.debug()` per cube). This is
  what made isolating the dominant stage possible instead of guessing; it's
  cheap enough to leave in permanently for the next "X feels slow" report,
  matching the existing pattern documented in
  [sensorgram_reentrancy_and_cube_slider_cache_indicator.md](sensorgram_reentrancy_and_cube_slider_cache_indicator.md)
  and CLAUDE.md's own guidance on this.

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
