# TIFF stack vs. OME-Zarr read benchmark: is chunking really the dominant cost?

2026-09-07. A direct, controlled follow-up to Follow-up #9 in
`bulk_analysis_performance_investigation.md` - after finding and fixing two
real bugs (nested TIFF thread pools, an unscoped ROI mask grid), the
question this doc answers is separate: **given the fixes are in, how much
does OME-Zarr's chunk size/compression/shard-mode configuration actually
matter for total per-cube analysis time, and is a real TIFF stack still
faster overall?**

## Methodology

Every number below comes from calling the app's own real, currently-shipped
`_scoped_formula_spectrum_task` (`gui/analysis_tasks.py`) directly - the
exact function the GUI's bulk "Start analysis" sensorgram worker calls per
cube - not a reimplementation. `compute_all_reduction_methods=False` is set
explicitly, matching `analysis_worker_mixin.py`'s own real rule
(`len(spectral_cubes) <= 1`) for any real multi-cube run. `zarrs` is left
disabled, matching its actual current state (Follow-up #7 disabled it after
a crash). cv2 is used for the resample stage automatically - it's the
current shipped fast path (`roi_scoped_resample_cv2_fast_path.md`), nothing
special was done to enable it.

**ROI set**: 160 `AreaRoi`s in a 16x10 square lattice across the real
806x1288px image, 80px spacing. 40px-diameter sample circles (20px radius),
50-65px-diameter reference rings (25-32.5px radius) - matching the
maintainer's real ROI count and roughly its scale.

**Dataset variants**: an 8-spectral-cube subset (216 files, 27 wavelengths
each) of the maintainer's real dataset
(`04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`),
used as-is for the TIFF case and re-exported (via the app's own
`export_ome_zarr_dataset`) into 6 OME-Zarr variants spanning chunk size,
compression, and shard mode:

| Variant | Chunk | Compression | Shard mode |
|---|---|---|---|
| `zarr_c50_compT_perimage` | 50px (matches the real dataset's current export) | lz4+bitshuffle | 1 image/file |
| `zarr_c128_compT_perimage` | 128px | lz4+bitshuffle | 1 image/file |
| `zarr_c1300_compT_perimage` | 1300px (>= longer image dimension, 1 chunk/plane) | lz4+bitshuffle | 1 image/file |
| `zarr_c1300_compF_perimage` | 1300px | none | 1 image/file |
| `zarr_c50_compF_perimage` | 50px | none | 1 image/file |
| `zarr_c1300_compT_percube` | 1300px | lz4+bitshuffle | 1 spectral cube/file |

Each of 7 datasets (TIFF + 6 zarr variants) ran the same 8 cubes with the
same ROI-mask cache lifetime a real run has (built once, reused across
cubes) - cube 0 pays the one-time mask-cache-build cost, cubes 1-7 are
steady-state and are what's averaged below.

## Results: total time per cube

| Variant | avg s/cube (cubes 1-7) | vs. best |
|---|---:|---:|
| `zarr_c1300_compT_percube` | **2.94** | 1.00x (best) |
| `zarr_c1300_compF_perimage` | 3.06 | 1.04x |
| `zarr_c1300_compT_perimage` | 3.24 | 1.10x |
| TIFF stack (real data) | 4.21 | 1.43x |
| `zarr_c128_compT_perimage` | 4.03 | 1.37x |
| `zarr_c50_compF_perimage` | 4.65 | 1.58x |
| `zarr_c50_compT_perimage` (current real dataset's own settings) | 6.72 | **2.29x (worst)** |

Best-to-worst spread: **2.27x**.

## Results: stage breakdown (`io=` is summed across 4 concurrent worker threads - see note below)

| Variant | io (summed) | io / ~4 (wall-clock est.) | resample | mask (steady) | reduce |
|---|---:|---:|---:|---:|---:|
| `zarr_c1300_compF_perimage` | 644ms | ~161ms | 109ms | 44ms | 648ms |
| `zarr_c1300_compT_percube` | 956ms | ~239ms | 105ms | 42ms | 590ms |
| `zarr_c1300_compT_perimage` | 982ms | ~246ms | 109ms | 47ms | 640ms |
| TIFF stack | 4722ms | ~1181ms | 189ms | 37ms | 635ms |
| `zarr_c128_compT_perimage` | 4056ms | ~1014ms | 105ms | 39ms | 646ms |
| `zarr_c50_compF_perimage` | 5874ms | ~1468ms | 98ms | 34ms | 604ms |
| `zarr_c50_compT_perimage` | 14391ms | ~3598ms | 82ms | 30ms | 584ms |

**Why divide by ~4**: `_scoped_formula_spectrum_task` reads all 27
wavelengths of a cube through a `ThreadPoolExecutor(max_workers=4)` - the
logged `io=` total is the *sum* of each wavelength's own read time added to
a shared accumulator (see the stage-timing lock comment in
`analysis_tasks.py`), not wall-clock time. With near-ideal 4-way
parallelism, wall-clock cost is roughly `io / 4`; real contention (GIL,
scheduling) makes the true value somewhat higher than that estimate, so
treat the `/4` column as a lower bound, not exact.

io-stage summed best/worst ratio: **22.3x** (`zarr_c50_compT_perimage`
14391ms vs. `zarr_c1300_compF_perimage` 644ms).

## The answer: chunking is real, but total-time impact is diluted by everything else

Both things are true at once, and neither cancels the other:

1. **At the pure I/O level, chunk size (and to a lesser extent
   compression) is exactly as dramatic as this investigation's earlier,
   narrower measurements claimed** - a 22x spread between the best and
   worst OME-Zarr configuration here, consistent with the "34-72x for a
   full-plane read alone" figures measured earlier in
   `bulk_analysis_performance_investigation.md`. This part of the original
   claim is not exaggerated.
2. **At the total per-cube wall-clock level - what "Start analysis"
   actually spends - the spread shrinks to 2.27x**, for two structural
   reasons that have nothing to do with whether the chunking claim was
   right:
   - The 4-way thread pool already hides a good chunk of the io cost
     behind itself; a 22x difference in *summed* io time becomes roughly a
     5-6x difference in *wall-clock* io time once parallelism is accounted
     for.
   - `resample` (~100-190ms), `mask` (~30-50ms steady-state), and `reduce`
     (~585-650ms) are all essentially **format-independent** - they cost
     the same regardless of chunk size, compression, or shard mode, because
     none of them touch storage. Once io shrinks far enough (the three
     1300px variants), these fixed costs - dominated by `reduce`, at
     ~600ms - become the *floor* nothing about chunking can go below.
     That floor is why the three well-configured zarr variants (2.94-3.24s)
     cluster so closely despite a real ~1.5x io difference between them.

So: **not negligible** (2.27x is a real, worthwhile difference for a bulk
run over hundreds of cubes), but **not the whole story either** - past a
certain chunk size, further chunk-size tuning has rapidly diminishing
returns because the fixed per-cube cost (mostly `reduce`, largely
independent of ROI pixel count at this scale) dominates instead.

## Format comparison: TIFF vs. OME-Zarr

- **TIFF vs. the real dataset's current OME-Zarr export (50px chunks,
  compressed)**: TIFF is **1.60x faster** (4.21s vs. 6.72s/cube) - this
  matches the maintainer's own suspicion from earlier in this
  investigation ("would tiff stack give the best performance?"). Yes,
  *right now*, given the existing export's chunk size.
- **TIFF vs. a well-configured OME-Zarr export (1300px chunks, either
  shard mode)**: OME-Zarr is **1.35-1.43x *faster*** than TIFF (2.94-3.24s
  vs. 4.21s/cube). OME-Zarr is not inherently slower than TIFF - the
  format itself was never the problem; the chunk size chosen at export
  time was.

## Secondary effects

- **Compression**, isolated at the *good* chunk size (1300px, per-image
  shard): off is ~6% faster than on (3.06s vs 3.24s) - small, as expected,
  since lz4 decompression of already-few, large chunks is cheap.
- **Compression**, isolated at the *bad* chunk size (50px): off is ~31%
  faster than on (4.65s vs 6.72s) - compression's cost is not fixed, it
  scales with how many chunks there are to decompress-call-overhead for,
  same underlying mechanism as the chunk-count dispatch cost itself.
- **Shard mode**, isolated at the good chunk size (1300px, compressed):
  one-shard-per-spectral-cube is ~9% faster than one-shard-per-image (2.94s
  vs 3.24s) - fewer file opens per cube (27 wavelengths share one shard
  file instead of each opening its own).

## Caveats

- Single machine, one real dataset, an 8-cube subset (not the full 314-cube
  dataset) - absolute numbers will vary machine to machine; the *relative*
  comparisons (same machine, same ROI set, same code path, only the
  dataset variant changes) are the reliable part.
- The 160-ROI square lattice is synthetic geometry (evenly spaced), not the
  maintainer's actual ROI placement - chosen to match ROI *count* and
  *size* exactly as specified, not exact positions. Given the lattice
  spans most of the image, `spectrum_read_region`'s ROI-union box ends up
  close to full-plane for every OME-Zarr variant here too, same as TIFF's
  always-full-plane box - this benchmark is effectively measuring
  full-plane-equivalent reads for every format, which is the right
  comparison for a "most ROIs selected" bulk run (see the "why two paths"
  discussion earlier in this investigation for when a small ROI subset
  would scope the read down further for OME-Zarr specifically, TIFF never
  benefiting from that either way).
- OS file cache was warm for the TIFF source files (touched earlier in
  this same investigation session) - a genuinely cold-cache first run
  could show TIFF somewhat slower than measured here; the freshly-exported
  zarr variants started cold and still won at the good chunk sizes.

## Part 2 (2026-09-07, same day): isolating every stage - "3s for 60MB is still too slow"

The maintainer's direct pushback on the numbers above: a cube is 27
wavelength frames at roughly 2MB each (~54-60MB total) - on any real SSD
that should read in well under a second, so why does even the *fastest*
measured configuration still take ~2.9s? This section isolates every stage
on its own, using real code and real data throughout, to find out.
Three separate, previously-invisible costs turned up - two of them bigger
than either bug fixed in Part 1/Follow-up #9.

### The disk was never the bottleneck

Pure `open()+read()` of 27 real TIFF files (42.1MB total, no decoding at
all): **26.7-35.2ms**, ~1.2-1.6 GB/s. The maintainer's intuition was
correct - raw disk bandwidth was never in question. Every millisecond
beyond this is decode, compute, or write overhead, not I/O.

### Finding 1: the source TIFF files are LZW-compressed with 300 strips/image - not the "uncompressed" case measured earlier

Inspecting a real file directly: `compression=5` (LZW), `predictor=2`,
`rowsperstrip=3` on a 900-row image - **300 separate compressed strips per
file**, each decompressed independently by tifffile. This is a property of
however the source acquisition software wrote these files; nothing in
LSPR-Suite's own code produces or controls this layout (LSPRi doesn't have
its own Acquisition app yet - see CLAUDE.md), so it isn't fixable from the
read side.

Re-encoding the same pixel data three ways isolates exactly what each
factor costs:

| Encoding | Decode time |
|---|---:|
| Uncompressed, 1 strip | 2.29ms |
| LZW, 1 strip | 20.36ms (+18.1ms for LZW itself) |
| LZW, 300 strips (matches the real files) | 31.02ms (+10.7ms more for fragmentation) |

The earlier "TIFF, uncompressed \| 2.54ms" figure elsewhere in this
investigation was accurate for what it measured - it just wasn't measuring
*these* files. At ~27 files/cube, LZW+fragmentation overhead alone adds
roughly **730-780ms/cube** (single-threaded-equivalent) beyond what an
uncompressed, sanely-strided TIFF would cost. This is a genuine, quantified
argument for OME-Zarr conversion: it's a one-time cost that removes this
overhead from every future read, not just the current chunk-size problem.

### Finding 2 (the big one): ROI masks are built efficiently now, but *used* inefficiently - 55% of total cube time

cProfile on a real cube call (best zarr variant, post both Follow-up #9
fixes) found the single largest cost in the entire pipeline:

```
ncalls  tottime  cumtime  function
4320/46  1.224s   0.024s   analysis_tasks.py:785(_means_for)
```

**`_means_for`'s own code - not any function it calls - accounts for 1.224
of 2.206 total seconds (55.5%).** The cause: `sample_pixels = patch[roi_mask]`
and `reference_pixels = patch[reference_mask]` index a *full-patch-sized*
boolean mask (e.g. 806x1288 ~= 1.04M elements) for every one of 160 ROIs,
even though each ROI's own True region is a tiny fraction of that - the
same "full-image work standing in for a small local operation" pattern
Follow-up #3 already fixed once for mask *construction*, this time in mask
*use*, downstream of it. It was invisible in every stage-timing log so far
because the indexing lines sit in the untimed gap between the `mask=` timer
(wraps only `_selected_roi_masks_for_spectrum`) and the `reduce=` timer
(starts *after* indexing finishes) - the existing instrumentation simply
never had a bucket for it.

Isolated, reproduced directly (160 ROIs, 806x1288 patch, single-threaded):

| Approach | Time (160 ROIs) | Per ROI |
|---|---:|---:|
| Current: `patch[full_patch_mask]` | 123.4ms | 0.771ms |
| Reach-limited: crop to a local box first, then index | 10.9ms | 0.068ms |

**11.3x speedup**, confirmed both in isolation and as the dominant term in
a real cProfile run. This affects **every** run regardless of chromatic
correction - the mask itself is always full-patch-shaped by the time it
reaches `_means_for`, whether it came from the chromatic-off path or the
already-reach-limited chromatic-on construction.

**Fixed (2026-09-07)**: `_means_for` now computes a local reach box from
the ROI's own radius (extended to cover a "mask"-geometry bitmap's own
extent when either side uses one) and crops `patch`/`roi_mask`/
`reference_mask`/`extra_exclude_mask`/`ignored_patch` to it before any
indexing - "mask"-geometry ROIs still fall back to the exact old full-patch
behavior (their extent isn't bounded by any radius), mirroring the same
split `_selected_roi_masks_for_spectrum` already uses. Verified
pixel-for-pixel identical (not just close - zero floating-point difference
possible, since this changes nothing about how a value is computed, only
which subset of an already-identical array gets gathered) against 164 real
ROIs including edge cases. **Measured real-world result**: the same cube
that profiled at 2.206s now runs in **0.943s (2.34x)**.

### Finding 3: a real, synchronous, completely unlogged GUI-thread stall every 5th cube

Tracing `on_sensorgram_partial_result` fully (not just its own "SG backup
timing" log line): during a real bulk run, `_backup_sensorgram_point`/
`_backup_formula_spectrum_series` only buffer rows in RAM
(confirmed: 0.103ms for 160 ROIs - genuinely negligible). The actual HDF5
write is deferred to `_flush_measurement_backup_buffers`, called
synchronously, directly in the same GUI-thread code path, every
`measurement_backup_batch_size` cubes (**default: 5**) - and this call sits
*after* the point where "SG backup timing" already finished logging, so
it has never appeared in any log this entire investigation.

Measured directly via the writer's own real batch-append methods (160
ROIs, batch of 5 cubes, matching the real default):

| Flush | sensorgram batch | formula batch | total | per-cube-equivalent |
|---|---:|---:|---:|---:|
| 1st (cold) | 616.7ms | 1139.0ms | 1755.7ms | 351.1ms |
| 2nd-4th (steady) | ~330-360ms | ~600-630ms | ~930-985ms | ~186-197ms |

This is a real, synchronous stall on the GUI thread every 5 cubes, sized
similarly to the earlier-diagnosed HDF5 resize-history cost
(`measurement_backup_performance_and_crash_recovery.md`) but for a
*different* reason (batch-write cost, not resize-count growth) and
currently has zero visibility in any log. Very plausibly what "occasional
hiccup" reports have actually been describing.

**Fixed (2026-09-07)**: the periodic flush (`on_sensorgram_partial_result`)
now calls `_flush_measurement_backup_buffers_async` instead of the
synchronous version - it swaps the buffer dicts for fresh empty ones (a
plain attribute reassignment, not a lock; buffering the next cube starts
immediately into the new dict) and hands the swapped-out data to
`_write_measurement_backup_buffers` (a new module-level, `self`/`window`-free
function so it's safe to call from any thread) running on a dedicated,
single-worker `QThreadPool` (`_measurement_backup_flush_pool`) -
`setMaxThreadCount(1)` both serializes flushes against each other and
preserves cube-ordering-on-disk. Every caller that needs the write to have
*actually finished* first (run end, dataset switch, app close, compact)
still calls the synchronous `_flush_measurement_backup_buffers`, which now
calls `waitForDone()` on that same pool before writing whatever's left -
guaranteeing no two writers ever touch the HDF5 file concurrently and that
a background flush already queued is always written before a later
synchronous one. `_backup_sensorgram_point`/`_backup_formula_spectrum_
series` now resolve `timestamp_utc_ms` at buffer time (cheap - already
memoized, see `_acquisition_timing_index`) instead of at flush time, so the
background write never needs to call back into GUI-owned state at all.
Verified with a real `ImagingMeasurementExportWriter` against a temp file
(`tests/integration/test_lspri_measurement_backup_async_flush.py`): the
swap itself measured **0.458ms** for 160 ROIs x 5 cubes (down from
~930-1755ms synchronous) - a ~2000-3800x reduction in GUI-thread blocking
time for this specific call.

### Fit stage: confirmed negligible

`fit_curve_for_method` (poly, order 4, 27 points), isolated: **1.33ms/call**
- once per cube, not per wavelength or per ROI. Ruled out as a contributor.

### Repeated-trial results (20 cubes, checking for instability) - CORRECTED, see below

| Run | avg/cube | min | max | spread |
|---|---:|---:|---:|---:|
| Best zarr (1300px, per-cube shard), no chromatic | 2.941s | 2.267s | 3.456s | 1.52x |
| Same, **with** realistic per-wavelength chromatic correction | 4.119s | 3.213s | 5.159s | 1.61x |
| TIFF stack | 4.493s | 4.253s | 4.850s | **1.14x** (most stable) |

The first pass at this table concluded chromatic correction cost a real
~40% and flagged the zarr run's gradual climb (cubes 1-3 ~2.3-2.5s, cubes
4-19 ~2.8-3.5s) as an open, un-root-caused question. **Both conclusions
were wrong** - not because the numbers above are fabricated, but because
the benchmark script producing them had a bug: it never called
`gc.disable()`, unlike the real `_sensorgram_metric_task` this whole
investigation has been comparing against (see that function's own comment:
"Cyclic GC is disabled for the run's duration ... a standard, safe
technique for a batch job like this").

**Root cause, confirmed directly**: rerunning the exact same 20-cube
repeat test with `gc.disable()` added:

| Run (gc disabled) | avg/cube | min | max | spread |
|---|---:|---:|---:|---:|
| Best zarr, no chromatic | ~0.90s | 0.854s | 0.943s | **1.10x** - drift gone entirely |

And rerunning the chromatic on/off comparison each in its own **fresh
process** (eliminating same-process ordering entirely) with `gc.disable()`,
steady-state stage timings landed in the same range either way - io
658-941ms, resample 59-91ms, mask 28-42ms, reduce 301-349ms, chromatic on
or off. No 2-5x gap anywhere. The original same-process, GC-enabled
comparison had chromatic-on running *second*, after chromatic-off had
already allocated tens of thousands of temporary arrays across three
cubes' worth of per-wavelength reads - exactly the condition under which
Python's cyclic GC starts running noticeably more often, and exactly why
it disappeared once GC was disabled or the comparison was run fresh either
way (a "gc.disable() fixes it" result and a "fresh process either way
matches" result independently, both pointing at the same mechanism, is
stronger confirmation than either alone).

**Corrected conclusion**: chromatic correction's true recurring per-cube
cost is **negligible**, not +40% - once measured under the same GC-disabled
condition the real app already runs under, it disappears into noise. The
only *real*, measurable cost of chromatic correction is a **one-time
mask-cache-build delay on the first cube of a run**: 27 distinct
per-wavelength affine-keyed cache entries need building instead of 1 -
measured directly (GC disabled, fresh process): cube 0's `mask=` stage went
from 6.28s (chromatic off) to 41.0s (chromatic on, summed across 4 worker
threads - roughly 8-9s wall-clock-equivalent once divided by ~4). That is
a real, perceptible several-second pause before a chromatically-corrected
run's *first* result appears - but it happens once per run, not once per
cube, so it becomes proportionally negligible over a run of any real
length. Nothing here currently needs fixing: not the "+40%" that turned
out not to exist, and the one real cost (a few seconds, once, at run
start) isn't worth the complexity of optimizing away for what it currently
buys.

This also means every earlier finding in this document that came from a
GC-enabled benchmark script (all of them) is a slight *over-estimate* of
real production timing, in this specific, now-understood direction - the
*relative* comparisons (chunk size, compression, shard mode, TIFF vs zarr)
are unaffected, since GC pressure applies equally across every variant
tested with the same script; only comparisons *between two separate runs
in the same long-lived process* were at risk, which is why this only
surfaced here and not in Part 1's variant matrix (each variant there ran
its own full 8-cube sequence back to back within one script too, for what
it's worth - not re-verified with gc.disable() after the fact, since nothing
about the chunk/compression/shard *relative* ordering would plausibly
change from an effect that hits every variant equally).

### What this section changes about the "2.27x" conclusion above

Nothing about the *relative* chunk-size/compression/shard-mode comparisons
changes - those all still apply, backup-write and mask-indexing costs are
equally present (or equally absent) across every variant. What changes is
the absolute floor: Finding 2 and Finding 3 (both fixed 2026-09-07, see
their own sections above) mean the *true* achievable per-cube time is
meaningfully lower than anything reported in Part 1 - confirmed directly,
not just estimated: the same real cube profiled at 2.206s pre-fix now runs
in 0.943s post-fix (2.34x), and with `gc.disable()` also applied (matching
the real app, see the chromatic-correction correction above) a steady-state
cube on the best zarr variant measured ~0.85-0.94s - all three
improvements compounding on top of Part 1's original ~2.94s best case.
