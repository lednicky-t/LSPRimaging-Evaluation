# Measurement backup: crash recovery, the "gets slower over time" investigation, and batched writes

2026-09-02. Started from a report that sensorgram analysis ("Start
analysis") felt slow, got progressively slower over the course of a run, and
wasn't using CPU/SSD/RAM anywhere near their limits. That symptom had
already been reported and partly fixed twice before (see
`sensorgram_reentrancy_and_cube_slider_cache_indicator.md`'s "Bug B"), so
this doc only covers what wasn't already explained by that fix. Written up
because the investigation crossed a native crash, an HDF5 file corruption
recovery, two more distinct performance bugs, and a storage-format change
that got scoped down to a smaller mitigation - a lot of ground not worth
re-deriving next time this area needs touching.

## How this was actually diagnosed - and how not to

Guessing from reading code alone repeatedly produced *plausible-sounding but
wrong* theories (see "Dead ends" below). What actually worked, in order:

1. **Debug-only stage timing**, logged into the existing `lspr_imaging_*.log`
   files (DEBUG level, see `apps/LSPRi/eva/logs/`) rather than printed or
   inferred - `SG cube compute timing` (per-cube compute wall time) and `SG
   backup timing` (per-cube HDF5 backup-write wall time), both added
   temporarily to `analysis_tasks.py`/`analysis_worker_mixin.py` and later
   made permanent in trimmed form. This is the same pattern already
   documented for the wavelength-switch investigation - reuse it before
   guessing.
2. **cProfile, but only after fixing it for this codebase's threading.**
   `_formula_spectrum_fast_task` fans work out to a `ThreadPoolExecutor`
   internally; wrapping the caller in a plain `cProfile.Profile()` sees
   almost nothing (profile hooks are per-thread). The classic fix -
   `threading.setprofile()` installing a fresh `Profile` per new thread -
   **raises `ValueError: Another profiling tool is already active` on this
   Python 3.14 install** (cProfile now appears to register through the
   process-wide `sys.monitoring` slot rather than per-thread). Working
   substitute: temporarily monkeypatch the module-global `ThreadPoolExecutor`
   symbol with a synchronous drop-in (`submit()` just calls the function
   immediately and wraps the result in an already-resolved `Future`) for the
   handful of cubes being profiled, so all the real work happens on the one,
   already-profiled calling thread. Changes wall time for those specific
   cubes (not representative), but the *relative* hot-function breakdown is
   what answered the question, and that's unaffected by losing the
   parallelism for a few cubes.
3. **Controlled experiments, one variable at a time**, each requiring an app
   relaunch (code changes don't hot-reload): disable cyclic GC for a run
   (ruled out - growth persisted identically with GC off), exclude the
   dataset folder from Windows Defender/Search indexing (ruled out - user
   confirmed still growing after excluding both), compare a *fresh* run's
   starting cost against an *established* session's (the single most useful
   experiment - see Bug C below).
4. **Direct correlation against a concrete number**, not another timestamp -
   logging the backup file's own on-disk size (`os.path.getsize`) alongside
   each write's timing directly falsified the "file size causes slowness"
   theory (see Bug D) instead of leaving it as an unresolved guess.

## Dead ends worth remembering (so they're not re-tried blindly)

- **HDF5 file growing in *byte size* makes writes slower.** Plausible-sounding,
  and the *initial* version of this theory (before controlled testing) fit
  the shape of the symptom. Directly falsified: logged file size stayed
  essentially flat (42.2 -> 42.5 MB) over 93 cubes while write time roughly
  doubled - because most calls in that run were dedup no-ops (the exact
  cube/ROI combination was already backed up from an earlier session), so
  net bytes written stayed small even as individual write *calls* got
  slower. The real mechanism (Bug D) is about resize-*operation count* over
  the file's lifetime, not size at any point in time - a file can stay small
  and still slow down.
- **Cyclic garbage collection pressure from accumulating caches.** A clean,
  testable hypothesis (both the compute thread and the GUI-thread backup
  writes climbing *together* suggested a shared, process-wide cause) - but
  disabling GC for the whole run (temporary diagnostic, later removed)
  didn't flatten the curve at all. Ruled out cleanly rather than left
  ambiguous.
- **Windows Defender / Search indexer scanning the growing backup file.**
  Real-time AV scanning is invisible to the app's own CPU/RAM numbers and
  is a well-documented cause of exactly this class of symptom on Windows -
  worth checking, and the user did (excluded the dataset folder from both).
  Made no difference; ruled out.
- **A single dominant cause.** There wasn't one. Three unrelated mechanisms
  were each contributing a real, independently-fixable slice: Bug B (already
  fixed 2026-08-31, see the other doc), Bug C below, and Bug D below. Don't
  stop investigating just because *a* fix reduced the symptom somewhat -
  check whether the *trend* (not just the absolute numbers) actually
  flattened before declaring victory. Bug C's throttle-only first attempt is
  a good example: it reduced cost but the growth trend was still clearly
  present in the next test, which is what motivated finding Bug C's *real*
  scaling problem instead of stopping at "somewhat better."

## An unrelated but real crash, found while investigating

Partway through, the app started crashing on every launch with `0xC0000374`
(`STATUS_HEAP_CORRUPTION`, from `ntdll.dll` - a native memory-corruption
crash, not a Python exception). Windows Event Viewer's Application log
(`Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000}`) showed
the actual chain: a `sip.cp314-win_amd64.pyd` access violation (`0xC0000005`)
on a prior close - the same *already-known, previously unresolved* PyQt6-sip
crash-on-close bug - immediately followed by two heap-corruption crashes on
the next two launch attempts. The close-time crash happened while
`measurement_backup.h5` was still open for writing; every subsequent launch
reopens that same file early in startup (`dataset_controller.py`'s
`_open_measurement_export_writer_for_dataset`), and the corrupted file's
structure triggered heap corruption in the HDF5 C library instead of a
catchable Python exception (the call site *is* wrapped in try/except, which
is exactly why a clean Python-level error would have been survivable, but
native heap corruption bypasses that entirely).

**Confirmed corrupted, read-only, no risk**: `h5py.File(path, 'r')` +
`f.visititems(...)` raised `RuntimeError: Object visitation failed (address
of object past end of allocation)`. Narrower inspection showed
`processed/absorbance_spectra`'s own group directory was corrupted (can't
even list its children), while `processed/sensorgram`, `processed/
roi_definitions`, `manifest`, and `rois` were all still fully readable - the
actual sensorgram trace data survived; only the deeper per-wavelength
spectra backup for that session's cubes didn't.

**Recovery**: renamed the corrupted file aside (never deleted -
`measurement_backup.h5.corrupted_<timestamp>`, still on disk if anyone wants
to attempt a lower-level salvage later) so the writer's existing "create
fresh if the file doesn't exist" path took over on next launch. Unblocked
immediately; no code changes needed for the crash itself, only for what
follows.

## Second incident, 2026-09-07: same bug class, different trigger (ROI table, not sensorgram/absorbance)

A separate investigation that day put this same file through dozens of
"Start analysis" runs (some taking minutes per cube before being fixed/
stopped) and hit `STATUS_HEAP_CORRUPTION` again on every subsequent launch -
same exit code, same "reopen this file early in startup" mechanism as the
first incident above, but a **different specific trigger**: not the
sensorgram/absorbance append path (Bug D), but `write_roi_definitions`'s
`upsert_table` call (`packages/lspr_io/src/lspr_io/hdf5.py`), which writes
the ROI-definitions table - called unconditionally, every launch, before
any analysis even runs.

**A better diagnostic this time - worth reusing**: `faulthandler.enable()`
at the top of a small standalone script that reproduces the exact startup
call sequence (`load_roi_table` -> `ImagingMeasurementExportWriter(path)` ->
`write_roi_definitions(...)`) turns this normally-uncatchable native crash
into a real Python-level stack trace instead of a silent process death -
pinpointed the exact line (`hdf5.py`'s `dataset[...] = table`) without
needing a native debugger. Reproduced against a **copy** of the real backup
file (never the original, in case the repro script itself made things
worse) - confirmed the crash, then confirmed the fix by running the exact
same real ROI data (`load_roi_table` on the dataset's own `roi_table.json`)
against a **fresh** file, which completed cleanly - proving the ROI data
itself was never the problem, only the old file's accumulated fragility.

**Mechanism, precisely**: `upsert_table` recreates the dataset (`del
group[name]` + `create_dataset`) whenever the row count changes from the
last write (`needs_recreate` when `dataset.shape != table.shape`) - which
it does on essentially every launch, since the ROI table's row count moves
around across a long testing session. Repeated delete+recreate of a
variable-length-string (VLEN) dataset is a different flavor of the same
"resize/structural-churn fragility" Bug D already documented for
appended/grown datasets, hitting a file that had accumulated a very long
history of exactly that churn (many dozens of runs in one day).

**Recovery**: identical to the first incident - `measurement_backup.h5`
renamed aside (`.corrupted_<timestamp>`, never deleted), letting the
writer's normal "create fresh if missing" path take over.

**Takeaway for future incidents of this class**: this bug isn't confined to
Bug D's specific append path - *any* HDF5 write against a file with a long
enough resize/delete/recreate history is a candidate once it's fragile
enough, including the ROI-table upsert this incident found. The "Compact
backup file" button (below) resets *all* of a file's datasets in one pass,
including this one - using it periodically during a long, write-heavy
testing session (not only once sensorgram-write slowness is noticed) would
likely have prevented this specific incident from accumulating enough
history to crash at all.

**A second, more direct mitigation added the same day**: the Analysis
section's title row got a `[disk]`/`[RAM]` toggle
(`_analysis_ram_only_backup`, `layout_builder.py`'s
`_make_ram_only_backup_toggle`) that skips the *periodic* mid-run backup
flush entirely - results are held in RAM for the whole run and written once,
when it finishes or is stopped (`on_sensorgram_ready`/`on_sensorgram_failed`
already did that unconditional final flush regardless; the toggle only
suppresses the earlier, periodic one in `on_sensorgram_partial_result` - see
`_measurement_backup_periodic_flush_due`). For a heavy iterative testing
session specifically - exactly the kind that caused both incidents above -
this avoids the write/resize churn at its source rather than relying on
periodically remembering to compact. Deliberately not persisted across app
launches (unlike the [λ,t]/[λ] toggle next to it): a crash *during* a run
with this on loses the whole run's results so far, not just one batch, so
it resets to the safe default every session rather than potentially
carrying over unnoticed from a different testing session's needs.

## Bug C: slider-cache-refresh cost scales with the whole session, not one run

Covered in `sensorgram_reentrancy_and_cube_slider_cache_indicator.md`'s
"Follow-up done 2026-09-02" section - short version: `schedule_cube_slider_
cache_refresh`'s 150ms debounce was shorter than a single cube's own compute
time, so it fired on nearly every cube; the *smoking-gun* experiment was
comparing a brand-new "Start analysis" run's first few cubes (3.6-3.8s each)
against an *earlier* run's first few cubes in the same session (~1.0s each) -
proving the cost was tied to cumulative work across the whole app session,
not "how far into this particular run." Root cause: once most of a
selection's ROIs already have cache hits somewhere (RAM or permanently on
disk), the "is this cube fully cached" scan can't break out early anymore
and has to walk every selected ROI for every already-covered cube - a cost
that only grows as more of a dataset gets analyzed over a session. Fixed by
suppressing the refresh entirely while a run is active (not just throttling
how often it fires), at the single choke point every caller goes through.

## Bug D: HDF5 write cost grows with resize-operation count, not file size

Once Bug C was fixed, backup-write time (`SG backup timing`'s
`formula_series`, later split into `sig`/`write` components - see `SG
backup breakdown`) was *still* climbing within a single run, even with GC
disabled and the file's own byte size staying essentially flat. Splitting
the timing (`_backup_formula_spectrum_series`) into "building the dedup
signature/hash" vs. "the actual `writer.append_formula_spectrum` write
call" isolated it: signature computation stayed flat; only the write-only
component (paid exclusively for genuinely-new rows, not dedup-skipped ones)
climbed.

**Mechanism**: every dataset in `measurement_backup.h5` is created
resizable (`maxshape=(None, ...)`, `chunks=True`) and grown one row at a
time via `dataset.resize()`. Resizable = chunked storage internally, with a
per-dataset chunk index (a B-tree-like structure) that has to be searched
and updated on every resize, plus the file's free-space manager has to find
space for each new chunk. That bookkeeping cost grows with how many times a
dataset has *ever* been resized over the file's lifetime - not with the
file's current size at any given moment. A single ~30-ROI selection touches
~14 datasets per ROI (absorbance/sample_mean/reference_mean/cube_index/
timestamp/signature_hash, times 4 reduction methods for the `reduced_
values/` subgroups), so one cube's backup write is 400+ individual resize
calls; a long test session accumulates a very long resize history across
many runs, even if the file's actual row count for any given run stays
small (most cubes were dedup-skipped, not freshly written).

### Fix applied: buffer-and-batch writes (implemented, tested)

`ImagingMeasurementExportWriter` gained `append_formula_spectrum_batch`/
`append_sensorgram_point_batch` (`storage/measurement_export.py`) - one bulk
resize + one bulk slice-write per dataset for N rows, instead of N separate
resize+write calls. `append_formula_spectrum`/`append_sensorgram_point` are
now thin single-row wrappers around the batch versions, so existing
single-row callers (the interactive per-cube view) are provably unaffected
(covered by `BatchWriteEquivalenceTests` in `tests/unit/
test_lspri_measurement_export.py` - batched output is byte-identical to the
equivalent sequence of single-row calls).

`AnalysisWorkerMixin._backup_formula_spectrum_series`/`_backup_sensorgram_
point` now buffer rows in RAM (`window._formula_spectrum_backup_buffer`/
`_sensorgram_backup_buffer`, keyed by roi_id) whenever `_sensorgram_running`
is true, flushing every `measurement_backup_batch_size` cubes
(`_flush_measurement_backup_buffers`, called from `on_sensorgram_partial_
result`). The interactive single-cube path is unaffected (writes
immediately, same as before - there's no stream of cubes to batch there).

**The crash-safety trade-off, made explicit and user-controlled**: this
file's entire design purpose (per its own module docstring) is staying
valid if the app crashes mid-run, specifically *because* it writes one row
at a time. Batching genuinely weakens that - up to `measurement_backup_
batch_size` cubes' worth of results are only in RAM, not on disk, until the
next flush. Mitigated three ways:
- Every *graceful* exit path flushes first: `on_sensorgram_ready`/
  `on_sensorgram_failed` (run completes, is stopped, or fails - even a
  stale/superseded result flushes before its early return, since the
  buffered data is real regardless of which logical run produced it),
  `_close_measurement_export_writer` (dataset switch/close), and
  `MainWindow.closeEvent` (app close). Only an actual crash mid-batch loses
  anything.
- Default batch size is 5 (not larger), keeping the worst case small.
- User-configurable via **Preferences -> Analysis: measurement backup ->
  Write batch size** (`MainWindow._measurement_backup_batch_size`/`_set_
  measurement_backup_batch_size`, QSettings-backed like the existing OME-Zarr
  adaptive-batch-size preference) - 1 restores the original always-immediate
  behavior exactly.

### Mitigation applied: manual repack ("Compact backup file")

Batching amortizes Bug D's cost; it doesn't remove it - the underlying
resize-history bookkeeping still accumulates, just `batch_size`-times
slower. `ImagingMeasurementExportWriter.compact()` resets it directly:
copies every group/dataset (via `h5py.Group.copy`, the same technique
`export_snapshot` already used for its unrelated feature) into a brand-new
file, then swaps it in place of the original. Every dataset in the copy is
written fresh, in one pass, with zero resize history - equivalent to what
the external `h5repack` CLI tool does, without adding that tool as a
dependency. The original file and handle are untouched until the copy has
fully succeeded (an exception partway through never destroys the working
backup); on success the old file is removed and the compacted one takes its
place, and the writer's handle/group caches are refreshed to point at it.

Exposed as a manual action - a "Compact backup file" icon button next to
"Export Results..." (`AnalysisWorkerMixin.compact_measurement_backup`,
Results/Export panel) - not run automatically. Flushes any RAM-buffered rows
first, logs before/after size and elapsed time to the workflow log. Tested
in isolation (`CompactTests` in the same test file: data survives
byte-for-byte, and the writer stays fully appendable afterward - a row
written post-compact lands correctly, proving the reopened handle/group
caches aren't stale references into the closed file).

## Recommended real fix, not yet implemented: pre-sized, cube-index-addressed datasets

Batching and repacking both work *around* Bug D's mechanism; this would
remove it. For LSPRi eva specifically (unlike a live-acquisition writer),
the total spectral-cube count and wavelength count are both known the
moment a dataset loads, before any analysis has run - `window._spectral_
cube_values`/`_wavelength_values`. A dataset created at its **final, fixed
shape** up front (no `maxshape`, ordinary contiguous storage instead of
chunked) needs no chunk index and no free-space search at all: writing to
row N is a direct, O(1) seek-and-write into already-allocated space,
regardless of how many other rows have been written before or since. Cost
per write would stay flat for the file's entire lifetime, not just reset
periodically.

This would also let the row position *be* the cube index directly (`dataset
[cube_index] = value`), instead of the current "rows appended in whatever
order results arrive, with a separate `cube_index` column to map back" - the
`backed_up` dedup set could become "is this slot still the fill value?"
instead. Simpler code, not just faster.

**Why this wasn't done today**: it's a genuine schema change (new file
version, no backward migration path defined yet for existing `.h5` backup
files - they'd stay on the old append-and-grow layout unless a converter is
written), and the file's disk footprint would jump to roughly its full final
size as soon as each ROI's datasets are created rather than growing
gradually (likely still just tens of MB at this dataset's scale, but a real
change in behavior worth deciding on deliberately). Flagged for a future
session once the current mitigations (batching + manual compact) have been
tested in real use and there's a clear signal they're insufficient.
