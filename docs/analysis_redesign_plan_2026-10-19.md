# Analysis redesign: plan and design proposal (2026-10-19)

**Status: proposal for discussion. Nothing built.** Decisions needed are in section 9.

Goal (maintainer, 2026-10-19): start analysis "clean", focused on data integrity, feeding the Spectra and
Sensorgram panels, performance on TIFF stacks and OME-Zarr, correct use of caches, ROI selections, and
**external access**: run from a command shell (also by an AI agent), scripting, parameter sweeps over a
GUI-prepared setup (ROIs, masks, background, ...), with the outputs going to third-party tools for statistics and
optimisation. Later the same for ROI and mask steps.

## 1. What already exists (rewrite branch)

A backend built 2026-09-22/23, before Mask, Background, ROI arrays, the ROI timeline and the off-thread autosave
were redone. No UI calls it. Files: `analysis/{engine,tasks,planner,provenance,store,query,reduction,statistics}.py`.

**Keep (correct, pure, tested):**

- `reduction.py` (mean / median / trimmed mean / plane fit, weighted variants), `query.py` (formula, fit, metric:
  derived from stored pairs, never a recompute), `statistics.py`.
- The per-cell arithmetic inside `tasks.compute_cell`: mask -> processed space -> warp order, the
  reference-ring exclusion from **all** ROIs, background exclusion, NaN validity, coverage and flags, dark-frame
  exclusion.
- The rules in `analysis_provenance_store_design_2026-09.md`: per-cell fingerprints, any input change recomputes
  that cell, formula/fit/metric never stored as inputs, readable versioned files (no hashes), written lazily.

**Replace (the orchestration):**

| # | Problem | Where | Effect |
|---|---|---|---|
| P1 | Loop is ROI-major: for every (ROI, cube) cell it re-runs the full-frame `apply_preprocessing` (rotation + background estimate) for all wavelengths. (The planes themselves come from the 160-plane read cache after the first ROI of a cube, so reading is *not* the repeated cost.) | `engine.py:743-760`, `tasks.py:417-445` | 160 ROIs x 314 cubes x 26 wl = 1.31 M preprocessings instead of 8,164 (160x too many). Measured: see section 10 |
| P2 | Worker thread reads live module state (`_gather_wavelength_inputs` -> `resolve_mask_source`, `affine_for`, geometry, background) | `engine.py:750, 781-820` | thread-unsafe; an edit during a run changes inputs halfway |
| P3 | Analysis reads through the shared, cached `load_plane` | `app_rewrite.py:359`, `dataset/module.py:171` | a run evicts the Image panel's frames (`load_plane_uncached` exists for exactly this) |
| P4 | Store = one HDF5 group per (ROI, cube) with 5-6 datasets; file opened and closed per cell; `del` + rewrite on recompute | `store.py:97-129` | ~250 k HDF5 objects at 160 x 314; slow to load and to write; HDF5 never reclaims deleted space, so the file grows on every recompute; awkward to read from pandas/xarray |
| P5 | Full-image rasterisation of every ROI at every wavelength | `tasks.py:450-455` | violates the spirit of the "masks at the ROI's own bounding box" invariant; most of the per-ROI cost |
| P6 | Planning writes files (chromatic JSON, settings snapshots) and compares masks with PNGs on disk for every frame | `engine.py:614-643` | "preview: N cells will recompute" is not cheap and not side-effect free |
| P7 | No algorithm-version constant | provenance doc "Still open" | a change to the background estimator or reducer does not invalidate stored cells |
| **P8** | **Settings-snapshot bookkeeping scans the whole snapshot folder.** Each `SettingsSnapshot` names its frame's own chromatic version, so there is ~1 snapshot file per frame (8,164 here, 49 KiB each because they carry the all-ROI exclusion digests). `persist_settings_snapshot` globs and JSON-parses *every* file on each call, and is called per wavelength per cell by both `compute_cell` and `plan_recompute` | `provenance.py:539-565, 613-644`; `planner.py:129` | **Showstopper, measured (section 10): one lookup = 74 ms at 26 files, 22 s at 8,164. A full planning pass would take on the order of 160 days.** The current backend cannot plan a full run on a real dataset, before any pixel is touched |

## 2. Lessons from the stable app (`gui/`), so they are not repeated

- Several cache tiers with **incomplete signatures**: a cached value survived an input change (`analysis_caching_architecture.md`).
- The exclusion union was built from the **selected** ROIs, so a stored value depended on what else was selected.
- All four reduction methods computed for every ROI on bulk runs (`bulk_analysis_performance_investigation.md`, fix 1).
- A curve fit on the GUI thread every 80 ms ("live preview").
- Batch logic (prefetch, GC toggling, shared mask cache, worker calibration) tangled into the per-cell function, so nothing was testable alone.
- `QThreadPool` + zarr read -> native heap corruption.
- Analysis entry points scattered across main-window mixins: no single "run this with these inputs".

The common root: **the inputs of a computation were never one object.** They were read from wherever they lived,
at whatever moment. The proposal below makes them one object.

## 3. Core idea: Recipe -> Run -> Results

```
 GUI modules --capture_session--> SessionState --+
                                                  +--> recipe_from_session() --> AnalysisRecipe (frozen, plain data)
 session folder on disk --load_session---------> SessionState --+                      |
                                                                          overrides (sweep) | scope (ROIs, cubes)
                                                                                         v
                                              run_analysis(recipe, scope, reader, sink, progress, cancelled)   [pure, no Qt]
                                                                                         |
                                                       sink = ResultStore (session data.h5) | SweepStore | in-memory
```

- **`AnalysisRecipe`**: every input that decides a number, as immutable plain data: geometry, background method
  (+ estimator version), mask timeline (`MaskChange`, already compressed and immutable), chromatic models,
  ROIs (with the per-cube timeline), detection/exclusion settings, reduction method and its parameters, ring
  defaults, reference-exclusion mode, coverage thresholds, pixel weighting mode (D4), algorithm versions, and the
  dataset identity (format, frame list, plane shape). JSON-serialisable; saved next to every result.
- **One route from GUI and shell.** The GUI already turns the modules into a `SessionState` for autosave, and
  `storage/session.py` `load_session(root)` builds the same thing from disk with no Qt. Both go through one
  `recipe_from_session()`, so a script and the app **cannot** compute different numbers from the same setup.
  This also fixes P2: the recipe is captured once on the GUI thread; the worker never touches a module.
- **Scope is not part of the recipe** (ROIs to run, cube range). Changing scope never invalidates anything (sketch §5).

## 4. The runner: frame-major, two stages

Each raw plane is read **once per run**, preprocessed **once**, then every ROI that needs that frame is reduced
from it (fixes P1).

```
for cube in cubes_needing_work:            # from the plan: only cubes with >= 1 stale (ROI, cube) cell
    planes = reader.read_cube(cube)        # uncached (P3); one shard read for OME-Zarr, prefetched one cube ahead
    for wl in spectral wavelengths:
        frame = prepare_frame(planes[wl], recipe.frame_inputs(cube, wl))     # stage 1: FRAME stage
        #   geometry -> background (with ROI/mask exclusion) -> mask to processed space + warp -> validity
        for roi in rois_needing(cube):
            values[roi, wl] = reduce_roi(frame, roi, recipe.roi_inputs)      # stage 2: ROI stage
            #   bounding-box-local sample/ring raster (cached per ROI and affine), exclusion, reduce, coverage
    sink.write_cube(cube, values, coverage, provenance)                     # one write per cube, not per cell
```

- `prepare_frame` and `reduce_roi` are the two halves of today's `compute_cell`, moved, not rewritten. A
  **golden test** pins old == new to the last bit on real data before anything else changes (numerics rule).
- Parallelism (to be measured, not assumed): one reader thread prefetching the next cube (bounded queue, so RAM
  stays bounded), N compute threads per frame (numpy/OpenCV release the GIL). Plain `threading.Thread` only (zarr rule).
- Unit of progress, cancellation and writing = **one cube**. A cancelled run leaves every finished cube valid.
- Cost model: **measured in phase 0, see section 10** (the first guess here, a few minutes, was too optimistic: the ROI
  stage is ~0.66 ms per ROI per frame, not 0.05-0.2, so ~19 min single-threaded for 160 ROIs x 314 cubes).
- **Consequence for "Run selected ROIs" (measured, section 10):** the frame stage (~32-44 ms read + preprocess per frame)
  does not shrink with fewer ROIs, but the ROI stage (0.66 ms per ROI) does. 5 ROIs ~ 47 ms per frame (~6 min for the
  whole run), 160 ROIs ~ 140 ms per frame (~19 min): selecting ROIs buys about 3x, not 30x. A cube range scales linearly.
  The UI should say so.
- **OME-Zarr scoped reads: dropped (phase 0, 2026-10-19).** This bullet used 2026-09 numbers (690 ms per full plane,
  zarr 3.2.1); today a full plane is 9.8 ms (direct shard reader, zarr 3.3), TIFF 12-22 ms, and one ROI window is 1.3 ms.
  Scoped reads break even at ~7 small ROI windows and save at most ~8 ms of a ~40 ms frame stage; with background
  flattening on (the session's setting) the whole frame is needed anyway (the baseline is a whole-frame median). Not
  worth a code path. An earlier answer in this conversation ("~25x faster for a few ROIs") came from the stale numbers
  and was wrong.

## 5. Results store (`analysis/data.h5`, schema 2.0)

> **Superseded by section 11 (maintainer, 2026-10-19):** the backbone file holds only the reduced numbers (value, spread,
> pixel counts) per ROI, cube, wavelength and side. Math, fits and metrics are later steps. Read section 11 first; the
> text below is the earlier, larger proposal.

Dense arrays instead of one group per cell (fixes P4):

```
/sample      float64 [roi_row, cube, wl]      NaN = not computed or flagged
/reference   float64 [roi_row, cube, wl]
/coverage    float32 [roi_row, cube, wl, 7]   + /coverage_flag uint8 [roi_row, cube, wl] (code table in attrs)
/cell_recipe int32   [roi_row, cube]          which recipe snapshot produced the cell (0 = never computed)
/computed_at float64 [roi_row, cube]
/roi_rows    table   roi_row -> current roi_id   (ROI renumbering rewrites this small table, not the data)
/recipes/    recipe_v1.json, recipe_v2.json ... (readable, deduplicated by content, like today's settings_vN)
/wavelengths_nm, /cube_index, identity attrs (schema_name, schema_version, app_name, app_version, created_at_utc, dataset identity)
```

- Chunked by cube; open -> write one cube -> close (a hard crash, which this app has had on close, cannot leave
  the file half-written for longer than one cube).
- Readable with plain `h5py` / `xarray` / pandas: `sample[:, :, wl]` is a whole trace. The app loads it into RAM
  in a few array reads instead of 250 k object opens.
- Per-cell fingerprint stays (rules unchanged); it is resolved through `cell_recipe` + the ROI's own geometry,
  with per-frame inputs (mask version, chromatic affine) listed inside each recipe snapshot.
- **No migration needed**: no UI has ever written this store, so no real results exist in schema 1.x. Confirm.
- Masks stay readable PNGs in `analysis/masks/` (provenance doc), written lazily when used.

## 6. GUI side

- **Workflow -> Analysis section**: Run (all ROIs / selected), Stop, cube range, "N cells out of date / M missing"
  (from a planner that is pure and writes nothing, P6), last-run summary. Joins `TaskIndicator`
  (`task_progress` / `task_finished`, the reference pattern), Run refused while a run is going.
- **`AnalysisEngine` becomes thin**: capture recipe on GUI thread -> plan -> hand to the runner thread -> receive
  per-cube results -> update the in-RAM read model -> emit `results_changed(roi_ids, cube_range)` (targeted, once
  per cube, not per cell). It never computes on the GUI thread and never reacts to a `ComputationalChange` by running.
- **Read model for panels**: `spectrum(roi, cube)`, `trace(roi_ids, metric_settings)`, `cell_status(roi, cube)`
  in {missing, current, out_of_date}. Traces (fits) computed on a worker and cached by (query settings, store
  version). Out-of-date cells are **shown dimmed, not hidden**: the user sees what they have and that it is stale.
- Spectra first (one cube, a few ROIs: simplest consumer), Sensorgram second. Both follow the panel rules
  (`HiddenPanelGate`, 100 ms coalescing, no numpy on the GUI thread).

## 7. External access: shell, scripting, sweeps

**Principle: the session folder on disk is the interface.** The user prepares everything in the GUI; autosave
writes it; a script or an AI agent reads the same folder. No live connection to the app is needed for this.

Command line (`python -m lspr_imaging_app.analysis ...`, same shape as `tools/scan_transients.py`):

```
run      <dataset> [--session NAME] [--rois 1-20,35] [--cubes 0:50] [--out DIR]   # results + recipe.json
inspect  <dataset> [--session NAME]          # what is stored, what is out of date, recipe diff vs current session
sweep    <dataset> --set background.sigma_px=32,48,64 --set reduction.method=mean,median [--out DIR]
         <dataset> --grid sweep.json
export   <dataset> --formula absorbance --metric centroid --csv traces.csv           # tidy table for 3rd-party tools
```

Python API for notebooks and scripts: `open_session(root)` -> `.recipe()`, `recipe.with_(...)`, `run(...)`,
`sweep(...)`. The CLI is a thin wrapper over it.

**Parameter tiers** decide what a sweep costs and how variants share work:

| Tier | Parameters (examples) | Cost per variant | Shared across variants |
|---|---|---|---|
| A frame | geometry, background method / sigma / bin, mask, chromatic | read + preprocess every frame | the raw read |
| B ROI | reduction method, trimmed fraction, sample / ring diameters, exclusion mode, weighting, coverage thresholds | reduce every ROI | read + frame stage |
| C query | formula, fit method, fit window, metric, statistics | arrays only, seconds | everything: one stored run |

The sweep runner groups variants by tier A values, prepares each frame once per group and reduces all tier B
variants from it; tier C sweeps need no pixel access at all.

Sweep output, separate from the session's own store so a sweep can never overwrite what the GUI shows:

```
<dataset>/session/<name>/sweeps/<sweep_name>/
  sweep.h5        same layout as data.h5 plus a leading [variant] axis
  variants.csv    variant, the parameter values, summary columns
  base_recipe.json, sweep.json, log.jsonl
```

**While the app is open:**

- The script reads the autosaved session. Autosave writes after a short pause, so the script checks a
  `saved_at` / "unsaved edits" stamp the app writes, and warns if the GUI has unsaved changes.
- A script writes to `sweeps/` or `--out` by default, never into the open session's `data.h5` (another process
  writing into an HDF5 file the app has open is unsafe, and HDF5's own file locking may refuse it outright).
- A live link (the app listens on a local socket; a script asks it to run, change a parameter, or show a result)
  is possible later. Not proposed now: it adds a security surface and concurrency with GUI edits, and disk
  already covers sweeps and agent access.

**Future (not now):** ROI detection, Auto mask and the transient scan become recipe-building steps with
parameters (they are already pure functions: `scan_transients`, `detect_rois`), so a sweep could vary "detect
with threshold t" the same way it varies sigma.

## 8. Phases

0. **Measure** (done 2026-10-19, section 10; tool: `tools/analysis_stage_timing.py`). Decided: patch-scoped ROI stage,
   no per-frame snapshot files, native TIFF decoder worth proposing, zarr scoped reads dropped.
1. `AnalysisRecipe` + `recipe_from_session` + split `compute_cell` into `prepare_frame` / `reduce_roi`.
   Golden test: bit-identical to today's `compute_cell` on real data.
2. Frame-major runner, store schema 2.0, **new provenance (P8: no per-frame snapshot files, a handful of recipe
   snapshots plus per-frame input digests stored as arrays)**, pure planner, CLI `run` / `inspect`. Before/after: same
   numbers, time per cube.
3. GUI: thin engine, Workflow Analysis section, TaskIndicator, read model, `results_changed`.
4. Spectra panel. 5. Sensorgram panel. 6. Sweeps (API + CLI, tier grouping), `export`.
7. Later: fractional weighting toggle (D4) as a recipe field; parametric ROI/mask steps; optional live link.

## 9. Decisions (maintainer, 2026-10-19)

1. **Keep the pure pieces, replace the orchestration.**
2. **New store format, no backwards compatibility** (the app is in development, not used for real data).
3. **Sweep output: one `sweep.h5` with a variant axis + `variants.csv`.**
4. **External access via the session folder now**; live link later, if ever.
5. **Spectra panel first** (build order only): spectra per cube -> fit -> metric -> Sensorgram reads the metric.
6. **Fractional weighting (D4) becomes a recipe field** (off by default), so it can be swept.

Next: phase 0 measurement on one real TIFF and one real OME-Zarr dataset.

## 10. Phase 0 results (2026-10-19)

Tool: `tools/analysis_stage_timing.py` (read-only; scratch folder for everything the analysis persists; re-runnable for
the after-numbers). Machine: i7-10510U laptop (4 cores / 8 threads), 31.8 GB, Python 3.14.5, zarr 3.3.0 with `zarrs`.
Data: `04_Bulk_sensitivity_pumpplan_0-70percent_to_LbL_with_water_v1_LED_V2`, 314 cubes x 26 spectral wavelengths + the
0 nm dark frame, raw planes 900x1300 float32. The TIFF stack and `images_1300x900_314x27_c78IZ_f32.ome.zarr` are
pixel-identical on the planes compared (same ImageKeys), so one session serves both. Session `2026-09-27_143549`:
160 ROIs, 411 mask changes (all individual, on 49 cubes; 5 of 52 sampled frames had a mask), 8,164 chromatic models,
rotation 1.44 deg + crop, background flatten on (sigma 39, bin 4, ROI and mask exclusion on), reduction mean.
Sampled cubes 27, 231, 100, 298. Numbers are means of repeated calls on a laptop; expect +-10-20 %.

**Not covered:** this session has a ROI geometry timeline; the tool times the no-timeline path (`rois()`), which is the
same code per frame. Only the `mean` session setting was timed, but values were compared for all four methods.

| Stage | Measured |
|---|---|
| Read one plane | TIFF tifffile 22 ms, TIFF native decoder 12 ms, OME-Zarr 9.8 ms (cold ~ warm: SSD) |
| Zarr scoped read, one ROI window | 1.3 ms; 5 ROIs 4.6 ms; 20 ROIs 18 ms; 160 ROIs 143 ms |
| Frame stage `apply_preprocessing` | 21.7 ms = rotate/crop 1.2 + ROI exclusion mask 5.9 + background estimate 9.5 + divide 3.7 |
| Per-frame lookups (mask, chromatic affine) | microseconds (a first reading of 10-24 ms was my own unit bug, fixed) |
| Union of all sample apertures (full-frame path only) | 31 ms per frame |
| ROI stage, full-frame masks (today) | 4.4 ms per ROI per frame (the cost is full-frame boolean ops, not the rasterising) |
| ROI stage, patch-scoped (`rasterize_*_for_patch` + window slicing; prototype in the tool) | 0.66 ms per ROI per frame |
| **Values, patch vs full-frame** | **bit-identical**: 800 of 800 ROI/frame checks for each of mean, median, trimmed_mean, plane_fit (4 distinct frames, 3 with ignore masks, x 160 ROIs) |
| Today's `compute_cell`, ROI-major | 1.8 s per cell (preprocess 0.68 s, rasterise 0.12, reduce 0.08, the other ~0.9 s is provenance bookkeeping) -> 50,240 cells = **25 h**, single thread, *with an almost empty snapshot folder* (so a lower bound, see P8) |
| P8, one snapshot lookup | 26 files 74 ms; 260 files 0.64 s; 1,300 files 2.7 s; 3,900 files 12 s; 8,164 files 22 s (~2.6 ms per file) |
| P8, planning pass | 1.3 M lookups x growing folder -> ~3,800 h (~160 days), by linear extrapolation of the measured per-file cost |

**Frame-major projection, one thread, 8,164 frames x 160 ROIs:** full-frame ROI masks ~104 min; patch-scoped ROI masks
~19 min (zarr 18.7, TIFF native 19.0, TIFF tifffile 20.3). Of the 137 ms per frame (zarr), the ROI stage is 106 ms, the
read 10 ms, the preprocess 22 ms.

**Threads** (24 frames, warm file cache, `ThreadPoolExecutor`; module inputs resolved beforehand):

| Task | 1 | 2 | 4 | 8 threads |
|---|---|---|---|---|
| TIFF native decoder + preprocess (frames/s) | 27 | 52 | **61 (x2.2)** | 50 |
| TIFF tifffile read + preprocess | 21.6 | 26.8 | 26.8 | 25.4 |
| zarr read + preprocess | 31.7 | 35.7 | 35.1 | 33.2 |
| zarr read only | 99 | 114 | 97 | 88 |

OpenCV's own thread count (1, 4 or 8) changes little. The weak scaling of tifffile and zarr is most likely the reader
holding the GIL (the interpreter lock), not the preprocessing; I did not isolate that. The ROI stage is small numpy
calls in Python, so it will probably not scale with threads either; whether processes help is **not measured**.

### What this changes

1. **P8 is the first thing to fix, ahead of speed.** The recipe/store design must not have one snapshot file per frame.
   Per-frame inputs (mask version, chromatic affine) go into arrays in the store with a digest per cube; the number of
   whole-recipe snapshot files stays in the single digits.
2. **The ROI stage is the main cost once the loop is frame-major**, and patch-scoping it is worth 6.6x with identical
   values. Further ideas, **unmeasured**: slicing a once-per-frame union instead of rasterising neighbours per ROI;
   caching ROI window geometry across wavelengths when the affine is identity; multi-process ROI stage.
3. **The native TIFF decoder is worth ~2x on reads and is what makes threads pay** (verified pixel-identical against
   tifffile on the first files at runtime by `io.py`). The analysis currently deliberately keeps tifffile
   (`io.py` comment); switching is a decision for the maintainer.
4. Zarr scoped reads are dropped (see section 4).

## 11. Step 1 (pixels -> reduced numbers): maintainer's direction, measured result (2026-10-19)

### Direction

The analysis is split into three steps that can each be re-run without the one before it:

1. **Scan** (this section): image pixels -> reduced numbers. The only step that reads images. The only thing that must be
   stored from it ("the backbone").
2. **Math**: apply a formula (default absorbance) to the stored numbers -> one spectrum per ROI per cube (+ spread).
3. **Fit / metric**: from each spectrum -> peak position etc., which feeds the Sensorgram.

Backbone contents, per (ROI, cube, wavelength) and per side (sample, reference): the reduced **value**, a **spread**
(STD of the pixels; SEM = STD / sqrt(n_valid) is derived), **n_valid** and **n_nominal** (pixels inside the aperture after
exclusions, before / after dropping pixels without a value). Per reduction method; the default is `mean`, the others
(median, trimmed mean, plane fit) are optional extra layers, at most 4. Measured size for the real dataset, mean only:
**29.6 MB compressed** (1.3 M cells), i.e. the data volume is small; the problem in P8 was the number of *files* for
provenance, not the number of numbers.

Dropped from what the old backend stored: the 4-sector ring coverage fraction (can be derived from geometry later).
Open: exactly how a cell's staleness is recorded (see "Provenance, lean" below).

### What was built to measure it

`tools/frame_reduce_lab.py` (uncommitted, prototype; to be moved into `analysis/` once the design is agreed):

- `locate_pixels`: finds the sample and ring pixels of **all ROIs of a frame in one vectorised pass** (the same test as the
  existing rasteriser: map the pixel back through the frame's inverse chromatic affine, compare squared distance with the
  radii, inclusive). 160 per-ROI mask builds become ~10 blocks of array arithmetic.
- `reduce_frame`: exclusions (all ROIs' sample apertures out of every ring, ignore mask, pixels without a value), counts,
  then each ROI's pixels are handed to the **unchanged** `reduce_*` functions, so the values are the ones today's code
  produces. `segment_std` is the one new quantity (vectorised, float64, two-pass).
- `prepare_frame_fast`: `apply_preprocessing` with the ROI part of the background exclusion mask computed once per run (per
  cube with a ROI timeline) instead of once per frame.
- `section_scan`: threads or worker **processes**, native TIFF decoder, inputs resolved on the main thread; workers touch
  no module.

### Correctness (against today's code, the golden reference)

19 frames x 160 ROIs = 3,040 ROI-frames (cubes 27, 231, 100, 298; 5 frames with ignore masks), run after every
optimisation: identical pixel sets, identical `n_nominal`, **identical values for all four methods** (bit for bit),
identical processed image from the cached-mask preparation, STD equal to the old per-ROI formula to 1e-12 relative. The
full-dataset run was spot-checked on 6 random frames (960 ROI values), all identical. Limits: one dataset, one session,
circle/annulus ROIs only (a free-form mask ROI makes the lab refuse, by design); membership of a pixel exactly on a
boundary could in principle flip by floating-point rounding (the lab evaluates the affine by plain array arithmetic, the
old code by a matrix product); no flip was seen in 3,040 ROI-frames, and I estimate the chance of any single one in the
whole dataset at about 2 %, effect on a ROI mean ~1e-3 relative if it happened.

### Speed

Single-thread time per frame (ms), `mean`, 160 ROIs: read 13.6 (native decoder) + prepare 16.2 + ROI stage 26.3 = **56.6**
(7.8 min for 8,164 frames). Before the work above, the same step was projected at 137 ms per frame (19 min; section 10, computed from parts, not run); the ROI-major engine measured 25 h (+ P8).

| Run | Wall time, whole dataset |
|---|---|
| 1 thread (measured on 20 cubes, scaled) | 7.8 min |
| 4 threads (20 cubes, scaled) | 6.2 min with a 0.5 ms GIL interval, 7.0 min with the default |
| 4 worker processes, **all 314 cubes, measured** | **268 s = 4.5 min** (30.4 frames/s), output 29.6 MB |
| 6 processes (20 cubes, scaled, older code) | ~5.2 min, ~15 % better than 4; not re-run on the final code |

Why processes and not threads: with 4 threads the ROI stage's busy time per frame was 152 ms against 32 ms alone. Many
small numpy calls hold the GIL and the threads queue behind each other (the same effect `testing7_playback_analysis`
found). Processes avoid it. Even so, busy time per frame roughly doubles with 4 processes running (read 28, prepare 37,
ROI 61 ms): the laptop saturates a shared resource (probably memory bandwidth, possibly clock speed); I did not isolate
which. More workers therefore help little; less work per frame helps.

Where the remaining single-thread time goes (ms per frame): read 13.6 (LZW decode, native); prepare 16.2 (background
estimate ~9.5, divide ~3.7, rotate/crop 1.2, the rest mask bookkeeping); ROI stage 26.3 = find pixels 12.3, exclusion +
gather 5.8, STD 5.4, `mean` loop 5.2. Extra methods cost ~13 ms per frame each (measured: 4 methods 68.9 vs 28.7 ms ROI
stage), so all four would be ~97 ms single-thread, roughly 7 min with 4 processes (estimate, not run).

Tried and rejected: `np.nonzero` for window coordinates (slower: 20.7 vs 14.7 ms), chunk sizes other than 16 ROIs per
block (cache effect: 2 -> 18.9 ms, 16 -> 14.7, 160 -> 28.8), GIL interval 0.1 ms (no better than 0.5 ms).

Ideas not yet tried (unmeasured): int32 indices; cheaper `inside`/compare passes; vectorised `mean` with float64
accumulation (would differ from today's float32-pairwise `np.mean` by ~1e-7 relative: a numeric change, so not done
without your OK); shaving the background estimate and divide (the scientific core: needs bit-identical proof).

### Provenance, lean (proposal, not built)

The backbone file carries: the recipe as one JSON (all global settings, algorithm versions, the ROI table), a small
`frame_digest[cube, wl]` array (a digest of that frame's mask identity and chromatic affine) and `roi_digest[roi, cube]`.
A cell is current if the recipe digest, its ROI digest and its cube's frame digests match the current ones: a numpy
comparison over ~30 k numbers, milliseconds, no per-frame files. The recipe JSON is deduplicated by content as before, so
a handful of files per dataset.

## 12. Status after the maintainer's answers (2026-10-19, later the same day)

Decisions: spread = plain STD of the valid pixels, SEM derived (same for every method), the 4-sector ring coverage number is
dropped, `n_valid` / `n_nominal` kept; **the first stored value is the mean (with its STD), nothing else** (median,
trimmed mean, plane fit stay available in the code as optional layers later). The maintainer first wrote "median" and
then corrected it to "mean" the same day; mean is the default throughout. Worker processes are fine. **The mean is accumulated in float64: applied the same day**
(subsection "The mean in float64" below). Commit of the plan and the two tools approved (commit `c6dfca6` in LSPRi/eva, not pushed, no umbrella pointer bump).

Built, not committed:

* `src/lspr_imaging_app/analysis/frame_reduce.py`: the prototype moved into the package as pure functions (`RoiSet`,
  `build_roi_set`, `locate_pixels`, `segment_std`, `reduce_frame` -> `FrameReduction`). New compared with the lab: a ROI
  with a free-form mask falls back to the existing rasteriser instead of being refused; an unknown reduction method or
  exclusion mode raises instead of silently becoming `mean`; default method is `("mean",)`.
* `image_tools/background/estimate.py` (`roi_exclusion_mask` public, `roi_exclusion=` on `flatten_background`) and
  `image_tools/preprocess.py` (`apply_preprocessing(..., roi_exclusion=)`): the cached ROI part of the background exclusion
  mask, optional, default behaviour unchanged. This replaces the lab's duplicate `prepare_frame_fast`. A wrong-shaped mask
  raises.
* `tests/unit/test_lspri_rewrite_frame_reduce.py` (umbrella repo, 13 tests, 18 sub-tests, 3 s): pixel sets equal the
  existing rasterisers' masks (identity and chromatic affine, blocks of 1 to 64 ROIs); counts, values of all four methods
  and STD equal a plain per-ROI reference for both exclusion modes, with and without an ignore mask and NaN pixels; ROIs at
  the image corners, overlapping ROIs, no ring, empty sample, free-form mask; the cached exclusion equals passing the ROIs.
  Mutation check: a 0.4 px larger sample, the wrong exclusion mode, the wrong affine and the ignore mask applied wrongly are
  each caught. Neighbouring suites still pass (background estimate, analysis core, preprocess, image refresh speed-ups,
  mask resolve cache, analysis-engine integration: 86 tests).
* `tools/frame_reduce_lab.py` now calls the module instead of carrying its own copy.

**Not yet re-verified on real data:** the identity-to-the-old-code results in section 11 were measured on the prototype
copy in the lab. The moved module is covered by the synthetic tests above only. To close that: run the lab's `verify`
section and a scan on the real dataset (`--sections verify,scan`), which the maintainer asked to hold until their VM is off.
The timings in section 11 were taken with the mean, which is the default again, and describe the prototype; the module's own
timing has not been re-measured. A bench run made while a VM loaded the machine (briefly with `median`) came out about 3x
slower on every stage and is discarded.

Next: re-verify + re-time on real data when the machine is free; then the recipe from a session (Qt-free, one route for the
GUI and the shell), the scan runner (processes), the backbone file, the command line.

### The mean in float64 (numeric change, applied 2026-10-19)

Maintainer: "float64 is ok", then "mean has to be applied". `segment_mean_std` in `analysis/frame_reduce.py` now computes the
mean (and the STD) of all ROIs at once in float64, where the old code (`reduce_mean` = `np.mean` of float32 pixels)
accumulates in float32. This is the one place where step 1 is not bit-identical to `compute_cell`; the pixel sets, the counts
and the values of median / trimmed mean / plane fit still are. Section 11's "bit for bit" statements were measured on the
prototype, which still used the float32 mean; read them with this exception.

Before / after (`.claude/rules/numerics.md`), on 19,998 synthetic pixel sets with the real scan's sizes (154 to 944 px) and
intensity range (16,000 to 65,000), in three patterns (noise, gradient + noise, sorted ascending as the worst case for a running
sum):

| | old (float32 accumulation) | new (float64) |
|---|---|---|
| relative difference old vs new | median 3.3e-8, 99 % 1.3e-7, max 2.1e-7 | |
| error against an extended-precision sum | median 3.3e-8, max 2.1e-7 | 0 (exactly rounded) |

The float64 mean is exactly the correctly rounded mean here: the sum of about 900 float32 values fits exactly in the 53 bits of
a float64, so the result also no longer depends on the order of summation (reproducible across machines and implementations).
Size of the change: at most 0.008 intensity units at 40,000; on an absorbance log10(reference / sample) at most about 2e-7,
against a measurement noise of about 4e-4 (the real scan's relative SEM, 3e-4 reference and 8e-4 sample).

**Real data (VM closed, 2026-10-19) - confirmed.** Whole dataset, before (prototype scan, float32 mean) against after
(`analysis/frame_reduce.py`, float64 mean), all 2,612,480 mean values (160 ROIs x 314 cubes x 26 wavelengths x sample /
reference):

| | result |
|---|---|
| `n_valid`, `n_nominal`, NaN pattern | identical everywhere |
| STD | identical everywhere (relative difference 0) |
| mean, relative difference | median 3.2e-8, 99 % 1.2e-7, max 2.17e-7; **none above 1e-6**; 0.1 % of values bit-identical |
| largest intensity change | 0.011 units (at a value of 55,559) |
| absorbance log10(reference / sample) | at most 1.3e-7 (median 1.9e-8), against a noise of about 4e-4 |

This matches the synthetic prediction (max 2.1e-7). The lab's `verify` (19 frames x 160 ROIs): pixel counts identical; median,
trimmed mean and plane fit bit for bit identical; mean equal to a float64 mean of the same pixels to 1e-12 and within 1.71e-7 of the
old float32 mean; STD equal to the old per-ROI formula. The scan's spot check (6 random frames, 960 ROI values) is within 1e-6 of
today's code (largest 1.5e-7). Two full 4-process runs of the module are bit-identical to each other (mean, STD, counts), so
the result does not depend on scheduling. An earlier lab run printed "0 of 960 identical": that was the lab's spot check still
demanding exact equality with the old float32 mean; fixed to a tolerance, not a data problem.

Speed of the new mean + STD in isolation (same process, same data, interleaved): 5.8 ms per frame of 160 ROIs against 11.8 ms
for the old per-ROI loop (2.0x). In the whole scan this is about 5 ms of ~66 ms per frame and is **not visible above the
run-to-run variation**.

Timings of the module on the real dataset, machine at about 12 % background load (a quieter run than the one that gave
section 11's 56.6 ms; frame preparation and reading were ~25 % slower this time, so compare ratios, not absolutes):

| | per frame, 160 ROIs | whole dataset |
|---|---|---|
| 1 thread, 20 cubes scaled | 65.9 ms (read 17.0, prepare 20.5, ROI stage 27.6) | 9.0 min |
| 4 worker processes, **all 314 cubes, measured** | 32.9 ms wall | **268.8 s = 4.5 min** (30.4 frames/s), 37.9 MB |

The 4-process full scan took 268.8 s here and 268.2 s with the prototype (section 11): the module did not slow the scan
down and the float64 mean did not visibly speed it up. ROI stage by part (bench, same run): find pixels 13.6 ms, exclude +
gather + validity 6.3, mean + std 5.9, total 25.8.

## 13. Speed-up round 2 (2026-10-19): pixel-layout cache, bincount mean/STD

Finding: a real run has only **26 distinct chromatic affines** (one per wavelength, identical in every cube; 314 of them
identity) and **4 distinct ROI layouts** over the timeline. Pixel membership therefore has at most 104 distinct answers for
8,164 frames. `frame_reduce.PixelLayout` / `build_layout` / `LayoutCache` (key: ROI geometry digest `RoiSet.key`, affine
bytes, shape, exclusion mode; LRU, ~4.6 MB per layout) reuse it: identical inputs, so identical pixels by construction.
Mean/STD moved to `group_mean_std` (`np.bincount`, float64); the float64 sum is exact, so the mean is unchanged bit for bit.
The background's ROI exclusion mask is cached per ROI layout, not per cube (setup 4.4 s -> 1.6 s).

Checks: 19 unit tests (layout reuse bit-identical for all methods/modes/masks; cache key reacts to a 0.01 px move, the ring
default and a free-form mask; LRU bound; a layout for other inputs is refused). Whole dataset vs the previous run: mean,
`n_valid`, `n_nominal` bit-identical; STD within 3.5e-14 relative (summation order). Spot check vs old code within 1.6e-7.

| | before (section 12) | after |
|---|---|---|
| ROI stage, single thread (bench) | 25.8 ms | **9.5 ms** (find pixels 0, gather 3.6, mean+std 5.8) |
| 1 thread, per frame | 65.9 ms | 47.1 ms (read 15.1, prepare 19.4, ROI 11.8) -> 6.4 min |
| 4 processes, full dataset, measured | 268.8 s | **182.0 s = 3.0 min** |
| 8 processes, full dataset, measured | | 160.0 s = 2.7 min (hyper-threads add ~12 %) |

Remaining time is now **read (~32 %) and frame preparation (~41 %)**. Options, none applied:

1. Background estimate: the validity weights (binned and blurred) are the same for every frame without an ignore mask; cache
   them (~3 ms of 19 estimated). Touches `_background_and_baseline` (scientific core): needs a bit-identity proof.
2. Divide by the background only at the ~30 % of pixels the ROIs read, instead of the whole frame (~2 ms estimated; same
   elementwise arithmetic, so it can stay bit-identical). Couples the reducer to the background step.
3. Read: LZW decode is ~12-15 ms per plane; decoding only the strips inside the crop saves ~15 %. A faster storage (the
   OME-Zarr export reads in 9.8 ms; uncompressed would be faster still) is a data-format decision, set aside for now.
4. Fixed costs: worker start-up 7-12 s (the lab imports the whole app incl. Qt; a lean runner imports only `analysis/`),
   ~5 s saving.
5. Hardware: per-frame work roughly doubles with 4 busy cores (15 W laptop CPU); 8 processes help only 12 %.

Estimate if 1, 2 and 4 are done: ~2.3-2.5 min with 4 processes on this laptop.

## 14. Speed-up round 3, background experiment, OME-Zarr / smart-cache design (2026-10-19)

### Built (maintainer: "do 1, 2, 4")

1. **Background weights cache** (`background/estimate.py` `BackgroundWeightsCache`, `_weights_part`): the valid-mask half of
   the estimate (weights downsample + blur, support, baseline selection) is reused between frames with the same valid mask.
   Keyed by mask *content* (blake2b of the packed mask, then an equality check), never by a label. Real run: 518 hits / 2
   misses in 520 frames.
2. **Divide only at the ROI pixels** (`apply.apply_background_at`, `estimate.flatten_background_at`,
   `preprocess.apply_preprocessing_at`; `PixelLayout.needed_flat`, 30 % of the frame here). Same elementwise float32
   formula, bit-identical (tests). `reduce_frame(..., layout=, layout_values=)` takes those values directly.
4. **Lean workers**: `analysis/scan.py` (`ScanSettings`, `FrameJob`, `CubeJob`, `scan_cubes`): worker processes,
   progress + cancel between cubes (`progress.Cancelled`), 4 neighbouring cubes per task (see below). The package
   `__init__` files of `analysis`, `image_tools` (+ background, chromatic, geometry, mask), `roi` and `dataset` now load
   their Qt classes lazily (PEP 562), so a worker imports no Qt. Before, every pure import pulled in PyQt6 through
   `analysis/__init__.py` -> `engine.py`, which also broke CLAUDE.md's "pure files import without Qt" rule.

Regression found and fixed on the way: the first `build_layout` with `needed_flat` used `np.unique` + `searchsorted`
(137 ms per build), and workers got single cubes in any order, so they evicted and rebuilt layouts; that ate the whole gain
(prepare 26.9 ms per frame). Now a marker array (21 ms per build) and contiguous cube tasks. int32 index arrays were tried
to halve layout memory and dropped: numpy converts them back on every gather (+4 ms per frame).

Checks: `test_lspri_rewrite_frame_reduce.py` (22 tests) and the new integration `test_lspri_rewrite_scan.py` (3: in-process
and 2 worker processes equal the plain full-frame path exactly; cancel). After the lazy-import change the whole LSPRi unit
suite (110 files, 1,374 tests) and integration suite (84 files, 1,331 tests) pass. Whole dataset: 4- and 8-process scans
bit-identical to the previous run (mean, STD, counts).

| full dataset, real data | 4 processes | 8 processes | 1 process, per frame |
|---|---|---|---|
| section 11 (prototype) | 268 s | | 56.6 ms |
| section 13 (layout cache) | 182 s | 160 s | 47.1 ms |
| **now** | **170.2 s (2.8 min)** | **149.1 s (2.5 min)** | 40.6 ms (read 15.4, prepare 15.4, reduce 9.8) |
| background off (for comparison) | 137.4 s | | |

With 4 busy cores every stage takes about twice as long as alone (15 W laptop CPU): per-frame busy time at 4 processes is
read 30, prepare 30, reduce 20 ms. Reading (LZW decode) is now as large as the whole background step.

### Experiment: what does background removal change in the output?

Whole dataset, 160 ROIs x 314 cubes x 26 wavelengths, mean; background off, the session's sigma 39 / bin 4, and
sigma 24 / bin 4 (`tools/bg_study/scan_compare.py`; A = log10(reference / sample); centroid = sum(wl A) / sum(A), a stand-in
for the fitted peak until step 3 exists):

| metric (median over ROIs and wavelengths) | off | sigma 39 | sigma 24 |
|---|---|---|---|
| A short-term noise, cube to cube (mA) | 0.888 | 0.879 | 0.878 |
| A vs across-ROI median, std over cubes (mA) | 0.788 | 0.789 | 0.795 |
| A spread across ROIs (mA) | 6.59 | 6.80 | 6.84 |
| reference ring vs across-ROI median, std over cubes (%) | 0.154 | 0.094 | 0.070 |
| centroid short-term noise (nm) | 0.166 | 0.165 | 0.166 |
| centroid vs across-ROI median, std over cubes (nm) | 0.111 | 0.102 | 0.102 |
| centroid spread across ROIs (nm) | 1.338 | 1.375 | 1.357 |

A (sigma 39) - A (off): median +0.001 mA, robust spread 0.24 mA, largest 35 mA (a few cells; not yet looked at which).

Reading: the background flattens the rings (drift 0.154 -> 0.094 / 0.070 %), but with a ring centred on each ROI that
illumination shape cancels in reference / sample. On the absorbance, removal does not reduce noise (within 1 %), does not
reduce the ROI-specific drift of A, makes the ROIs about 3 % less uniform, and reduces the ROI-specific drift of the
centroid by about 8 %. Same picture as the 2026-10-06 study (136 ROIs, 53 cubes). Not covered: the fitted peak (step 3),
other datasets, references not centred on the sample (the 2026-10-06 displaced-reference test showed a large benefit
there: 7.9 -> 1.6 % error at 240 px), other reduction methods. **Decision for the maintainer:** whether the default stays on
for centred rings; it costs about 20 % of the scan.

### Design (not built): OME-Zarr chunk reads and a smarter preparation

**When partial reads help.** Processed pixel p needs only the raw pixels around G^-1(p) (rotation is bilinear, 2x2
neighbours; crop and flip are exact). With 160 ROIs of ~60 px rings on a ~75 px lattice, the 78 px chunks of
`images_1300x900_..._f32.ome.zarr` around them are almost the whole plane: no gain. With a few ROIs (a selection, a sweep on
a subset) the chunk set is small, e.g. 5 ROIs ~ 10-20 of ~220 chunks.

1. Per (ROI layout, affine, geometry): chunk set = chunks covering G^-1(needed_flat) dilated by 1 px; cached like layouts.
2. Read those chunks through the shard index directly (byte ranges + blosc decode), as `dataset.io._fast_read_zarr_plane`
   already does for whole planes; not through the zarr array API (measured ~1 ms per ROI window, its per-chunk dispatch).
3. Fill a raw plane (NaN elsewhere), rotate / crop, take the needed pixels. Bit-identical as long as every 2x2 source
   neighbour was read (a test would pin it).

**Background with partial reads.** The Gaussian estimate is local (cv2 kernel radius ~3-4 sigma, ~120-160 px at sigma 39),
but the baseline is the median of the whole-frame estimate. A window of (ROI reach + kernel radius), aligned to the binning
grid, gives the same estimate inside the ROI; the baseline needs the whole frame. The baseline multiplies sample and
reference alike, so A = log10(R / S) does not depend on it (except where 65,535 clipping acts); the stored S and R do.
Options: (a) background off for partial reads (fastest; per the experiment, little is lost with centred rings);
(b) windowed estimate + a fixed per-run baseline (A unchanged, stored S and R change scale: a format / semantics decision);
(c) whole frame (no read saving). A window for one ROI is ~400 px square ~ 30 chunks (14 %), so (b) pays off only for a
handful of ROIs.

**Using the labels (maintainer's point).** Already used, keyed by content rather than by label: one pixel layout per (ROI
layout, affine) - the chromatic models are per wavelength, the ROI timeline has 4 layouts here; one background-weights entry
per distinct valid mask - the masks are few; one ROI exclusion mask per ROI layout. Still per frame and unavoidable while the
background is on: decode, rotation, the background numerator (it depends on the pixels), the upsample, the divide at the ROI
pixels, the reduction. Labels could also save the setup's per-frame mask warp (1.7 s per run) and the valid-mask hashing
(0.5 ms per frame); small.

**Next steps if wanted, by expected gain:** a faster input format or partial reads (reading is ~1/3 of the time; the zarr
export already reads in 9.8 vs 12-15 ms for TIFF); background off for centred rings (-20 %); partial reads for small ROI
selections (a 5-ROI run in well under a minute, estimated, unmeasured).

### Maintainer decisions on section 14 (2026-10-19)

1. **Background removal stays the user's choice**, one state: `BackgroundSettings.flatten_background_enabled` (the Image ribbon's Background tab). The Analysis section, when built, gets a second toggle bound to the *same* state (not a copy). Its tooltip, for ROIs with a centred reference ring, says removal is usually not needed and points to a short summary of the experiment above (example dataset: absorbance noise and drift unchanged, ROI spread 3 % better without, centroid ROI-drift 8 % better with, ~20 % of the scan time). The scan already follows that state; nothing to change in `analysis/scan.py`.
2. **OME-Zarr with the background on reads whole planes, exactly as TIFF** (results 1:1 with the TIFF path). Partial chunk reads (design above) apply only when the background is off. A *regional* background for partial reads may come later as a second, user-chosen mode next to "full" - to be discussed and designed properly first (windowing, baseline, what is stored), not slipped in.
3. Commit approved.

## 15. Output file layout (maintainer decisions, 2026-10-23)

Supersedes section 5 (and the "Provenance, lean" proposal in section 11) for step 1. Not built yet.

**File:** `<dataset>/sessions/<id>/analysis/scan.h5`, schema `lspri_scan` 1.0, all `hdf_standard.md` identity fields. The old
`analysis/data.h5` (schema `lspri_rewrite_analysis_store` 1.1) is left alone until the cut-over (no UI ever wrote it, so no
migration). Code: new `analysis/scan_store.py` (h5py, no Qt).

```
/processed/scan/
  value/mean     float64 [cube, roi, wavelength, side]   side = sample | reference
  std            float64 [same]     spread of the valid pixels (SEM = std / sqrt(n_valid), derived later)
  n_valid        uint32  [same]     pixels with a value
  n_nominal      uint32  [same]     pixels in the aperture after exclusions
  (later, optional: value/median, value/trimmed_mean, value/plane_fit)
/axes/   wavelength_nm float64 [wl]; cube_index int32 [cube]; acquired_at_unix_ms int64 [cube, wl]; side and roi_id labels
         (HDF5 dimension scales, so xarray / pandas read it directly)
/metadata/ recipes/<n> (readable JSON: geometry, background + estimator version, mask settings, exclusion mode, trimmed fraction,
           ring defaults, algorithm version); roi_layouts/<n> (readable JSON ROI tables); dataset identity
/state/  cell_hash uint64 [cube, roi]   0 = never computed;  recipe_id int32 [cube, roi]  which recipe JSON produced the cell
```

- Chunk = one cube, gzip + shuffle. About 38 MB for 314 cubes x 160 ROIs x 26 wl. Written create-via-temp-and-rename, then one
  cube at a time (open, write values, flush, write `/state` last, close), so a crash leaves at most one cube "not computed".
- Absorbance, fits and metrics are never stored. The 4-sector ring coverage and the dark frame are not stored.
- **Staleness = one combined hash per cell** (maintainer's choice; recipe + the cube's ROI layout + the cube's frame mask and
  chromatic inputs, hashed together). Known cost: the hash alone cannot say *why* a cell is stale. `recipe_id` and the readable
  recipe JSON are kept anyway (the HDF5 rule: save analysis parameters with the data), so a "why" can be approximated by diffing the
  current recipe against the stored one. The expected hash is computed from the current session when needed; per-frame digests
  are not stored.
- **ROI delete / reorder (ids renumber):** every stored cell becomes stale, rescan (~3 min here). No code edits stored rows in
  place. Revisit only if it hurts.
- Sweeps later: same datasets with a leading `variant` axis in `sweep.h5`.
- **Mask identity in the hash: by content** (maintainer, 2026-10-23), not by version number.
- **Cubes with different wavelength sets: not expected** (maintainer, 2026-10-23), but a fallback so it can never go wrong silently:
  the `wavelength_nm` axis is the sorted union over all cubes (wavelengths compared after rounding to 0.01 nm, so float noise does not
  create a second column); a frame a cube does not have stays NaN with `n_valid` = `n_nominal` = 0 and `acquired_at_unix_ms` = 0; the job
  builder logs one WARNING (how many cubes differ) and the scan's result summary shows it. It never stops the scan and never drops a frame.

## 16. Job builder built (2026-10-23): `analysis/scan_jobs.py`

Session state -> `ScanPlan` (settings, `CubeJob`s, cube list, wavelength axis, per-cube column positions, ROI ids, warnings). Pure (no
Qt), takes **callables** like `AnalysisEngine` does (`ScanSource`; `source_from_modules(...)` builds one from the live modules). Rules in
the file docstring: dark / set-aside frames left out; wavelength axis = union over cubes (0.01 nm rounding) with one WARNING when cubes
differ (section 15 fallback); two frames on one rounded wavelength = `ValueError`; every cube must have the same ROI ids in the same
order; the chromatic affine is copied; ignore masks are resolved to processed space here and shared per (authored frame, scope, warp);
all ROIs are always reduced (the ring exclusion uses all of them). Not wired to anything yet.

Checks:

* `tests/unit/test_lspri_rewrite_scan_jobs.py`, 16 tests (no Qt, no files): frames and axis, union fallback, float noise, duplicates, cube
  subset, affine copy, ROI layouts (shared without a timeline, one per cube with it, id mismatch refused), ignore masks (no warp when authored
  at the frame, warped equals `resolve_external_mask`, sharing, no mix-up).
* `tools/scan_golden_check.py` (read-only): the new route (builder + `scan_cubes`) against `AnalysisEngine._gather_wavelength_inputs` +
  `compute_cell` on the real dataset. Result, with an **in-memory synthetic timeline** (`--synthetic-timeline`: all ROIs move from cube 100 on, 20
  ROIs on cube 200 alone, never saved): 6 cubes (0, 100, 104, 200, 208, 313; three ROI layouts; masks in cubes 0 and 200), 6 ROIs each, 1,872
  (ROI, cube, wavelength, side) values: **pixel counts identical 1872 of 1872; mean within 1.56e-7 relative** (float32 vs float64 mean, as in
  section 12). Without the timeline: 3 cubes, 936 values, identical counts, 1.44e-7. Not covered: free-form mask ROIs, persistent masks, a
  dataset with different wavelength sets, a mutation check of this tool (the unit tests and the earlier lab checks cover those kinds of fault).

Measured on the real dataset (all 314 cubes), calling thread: **plan setup 2.2-2.6 s**; 411 frames carry an ignore mask, **417 MB** of arrays
(1.17 MB each; individual masks are all distinct, nothing to share here). A session with a persistent mask would share arrays per wavelength.
Both matter for the next step (see below).

Findings for the next steps:

1. **Setup runs on the thread that owns the modules.** 2.4 s on the GUI thread is too long for a click handler (GUI-thread rule). The scan task
   must either resolve in slices, or take an immutable snapshot on the GUI thread and resolve off-thread (`MaskChange` is immutable and compressed).
   Decide in the task step.
2. **Ignore-mask memory is per distinct mask and held for the whole run**, and every array is pickled to a worker with its task. 417 MB here; many
   individual masks on a bigger dataset would grow. `IGNORE_MASK_WARN_BYTES` (2 GB) logs a warning. A compact hand-over (the compressed mask +
   warp, resolved in the worker) would remove both the memory and the setup time; not built, because it changes `FrameJob`.
3. **The test session `2026-09-27_143549` has no ROI timeline any more** (session.json saved 2026-10-22 19:09: 0 of 160 ROIs carry one; my notes
   from 2026-10-19 say 4 layouts). Either it was cleared on purpose or something dropped it on a save. Unconfirmed which.

## 17. Scan task, status row and logging built (2026-10-23)

`analysis/scan_task.py` `ScanTask(ThreadedTask)`: task id `analysis_scan`, label "Analysis scan". Wired in `app_rewrite.py` (`_Modules.analysis_scan`):
progress and finish into the status-bar `TaskIndicator`, Cancel in `cancel_by_task`, stopped by a dataset load / clear and a session switch
(`invalidate()`), stopped at application quit (`aboutToQuit`). `app_rewrite.analysis_scan_source(m)` builds the `ScanSource` from the live
modules. **There is still no button that starts it** (the Workflow > Analysis section is placeholders): the next step.

Decisions taken (the two open points of section 16):

* **Snapshot on the GUI thread, mask decoding off it.** `snapshot_source` (`scan_jobs.py`) reads all lookups into plain tables, copies the ROIs, and
  keeps ignore masks compressed (`MaskRef.load` decodes later). `ScanTask.start()` takes it; the task thread then builds the plan and runs the scan,
  and touches no module. Measured on the real dataset, all 314 cubes: **snapshot 80-170 ms on the GUI thread** (was 2.2-2.6 s for the old
  all-in-one setup; single readings up to 0.9 s while another process loaded the machine), plan build **1.9-2.7 s on the task thread**.
  `start()` with 12 cubes returned in 6-24 ms.
* **Compact mask hand-over: not built.** Ignore masks (417 MB here) are still decoded in the task thread and pickled to the workers. With the setup
  off the GUI thread, the remaining cost is memory only; the 2 GB warning stays. Revisit if a session with many more individual masks appears.

Progress: first 2 % = preparing the plan (measured ~2 s of ~3 min), then "Starting the worker processes", then one report per finished task of
4 cubes (throttled to 20/s). Cancel is checked as often, so it acts within one task.

Logging (logger `lspr_imaging_app.analysis.scan_task`, `applog` helpers): INFO `analysis.scan_prepared` (build time, cubes, frames, ignore-mask MB),
`analysis.scan_start`, `analysis.scan_done` (wall seconds, frames/s, busy seconds per stage read / prepare / reduce, warnings, and the
`lspr_core.perf` report as `perf_*` fields: workers, mode, rate, limits, machine), `analysis.scan_cancelled`; DEBUG `analysis.scan_cube` per cube.
Worker processes have no logging of their own; their timings travel back in `CubeResult.seconds`. `scan_cubes(on_finished=)` now hands over the
perf `RunReport`.

`analysis/scan_arrays.py`: `ScanArrays` / `new_arrays` / `place`: finished cubes placed into `[cube, roi, wavelength, side]` arrays (mean, std,
n_valid, n_nominal as uint32, `done[cube]`), independent of completion order; a frame a cube lacks stays NaN / 0. The task holds them in memory and
hands them to `on_result(plan, arrays, summary)` on the GUI thread: **the file writer plugs in there** (next after the button).

Tests: `tests/unit/test_lspri_rewrite_scan_jobs.py` (21: + snapshot), `tests/unit/test_lspri_rewrite_scan_arrays.py` (3),
`tests/integration/test_lspri_analysis_scan_task.py` (12: completes with progress and log events, per-cube DEBUG events, 2 worker processes equal 1,
automatic worker count and mode in the log, cube subset, an edit after `start()` cannot change the result, Cancel, stale run dropped, ROI ids
differing between cubes refused with a message, no ROIs refused, unknown cubes fail without a run, second start refused). Fault injection: a
snapshot without ROI copies fails 3 tests; a task that ignores Cancel fails 1. Neighbouring suites pass (scan, frame_reduce, edit menu, autosave,
preferences, analysis engine, log panel, export workers).

Real data (`2026-09-27_143549`): the task ran 12 cubes x 160 ROIs x 26 wavelengths in 19 s with 3 workers (Standard on this 4-core / 8-thread
laptop), all 99,840 means finite, log line complete. **That is not a speed measurement**: pool start-up (7-12 s) is most of a 12-cube run, and
a first attempt was spoiled by my own test script lacking the `if __name__ == "__main__"` guard (spawned workers re-ran it). A timed full-dataset run
through the task (expected ~3 min, section 14) is still to do when the machine is free.

## 18. `scan.h5` writer, cell hash, Run / Stop row (2026-10-23)

Built, following section 15 (layout) with the maintainer's decisions (one combined hash per cell; mask by content; ROI delete = everything stale).

**Files** (all pure, no Qt, except the task and the row):

* `analysis/scan_fingerprint.py`: `recipe_of(settings)` (readable dict), `recipe_json`, `cell_hashes(plan)` -> `uint64 [cube, roi]`. A cell's hash covers the
  recipe (geometry, background, detection settings, methods, exclusion mode, trimmed fraction, `SCAN_ALGORITHM_VERSION`), the cube's ROI layout key, every
  frame's wavelength column + **file name** + chromatic affine + ignore-mask **content** (blake2b of the packed bits), and the ROI's id and position. Not in
  the hash: app version, TIFF decoder, worker count, folder (a moved dataset stays current), and the detection fields that belong to other steps
  (`formula_key`, `reduction_method`, `array_*`; changing the formula must never make a scan stale). **Bump `SCAN_ALGORITHM_VERSION` whenever a change to
  `frame_reduce`, the background estimate, the rotation or the reduction can change a stored number.**
* `analysis/scan_store.py`: the file (layout in section 15; `/state/layout_id [cube]` added next to `recipe_id`, so every cube names the ROI table it was
  scanned with). `ScanStore.prepare(plan)` (create / reuse / replace, returns the stored hashes of the plan's cubes), `write_cube(...)`, `read_scan(path)`,
  `describe_file(path)`, `stale_cube_positions(stored, current)`. Axes are the whole dataset's (all spectral cubes, the wavelength union, the ROI ids), so a
  partial run and a full run share a file. Created under a temporary name and renamed. **Write order per cube: hash row to 0, values, flush, recipe / layout ids,
  real hash, flush** (a failure leaves the cube "never computed", never a stale hash on new values; tested by making the last write fail). A file that does not
  fit (other ROI ids, wavelengths, cubes or methods; unknown schema; not HDF5) is moved to `scan.previous.h5` (one backup slot) and a new one is made.
  `prepare` opens the file read-only unless a recipe or layout has to be added, so a run with nothing to do does not touch it (tested by modification time).
  Readers reject an unknown `schema_name` or a different major version; a newer minor reads.
* `analysis/scan_jobs.py`: the plan now carries `dataset_cubes` and `acquired_at_unix_ms [cube, wavelength]` (from the new `DatasetModule.frame_times_unix_ms()`), the
  wavelength axis is computed over **all** cubes, `subset_plan(plan, cubes)`.
* `analysis/scan_task.py`: writes each finished cube at once; `only_stale` (default) scans only cubes with a stale or missing cell (the unit of work is the cube: the
  frame stage dominates, so one stale ROI costs almost as much as all); "up to date" ends in ~2 s without touching the file; Cancel keeps every finished cube and a
  later run resumes; an `OSError` becomes "Could not save the scan file: ...". Log events as section 17, plus `analysis.scan_current` and `write_s`, `file_mb`.
* `panels/workflow/analysis_scan_row.py` + `app_rewrite._wire_analysis_scan`: **Workflow > Analysis, top row**: [Run scan] [Stop], a checkbox "Scan every cube again"
  (remembered via `ui_state`, key `analysis/scan_every_cube_again`) and a status line (what the last run did; on opening a session, what `scan.h5` holds).
  The file is `<dataset>/sessions/<id>/analysis/scan.h5`. Run refuses with a message when no dataset is open or the session is still loading.

**Not built / still open:** the second background toggle in the Analysis section (decision of section 14: one shared state); a cube range / ROI selection for Run;
the "N cells out of date" preview without running (the plan build costs ~2 s, so it is possible, but nothing shows it yet); the command line; reading the file
back into the Spectra panel (step 2); `AnalysisSettingsModule` / old engine removal. The recipe stores the detection settings except the five fields listed
above; `mask_*` threshold fields therefore stale a scan even when ignore-by-intensity is off (conservative on purpose: priority 1, correctness).

Tests: `tests/unit/test_lspri_analysis_scan_store.py` (17: file contents and dimension scales, exact read-back, partial runs, unknown schema / newer major / newer minor /
damaged file, crash-safe order, reuse and recipe de-duplication, replacement keeps a backup, what the hash reacts to and what it ignores),
`tests/unit/test_lspri_rewrite_scan_jobs.py` (21), `tests/integration/test_lspri_analysis_scan_task.py` (19: + saving, up to date, only changed cubes,
run-all, Cancel then resume, unwritable file, partial then full), `tests/integration/test_lspri_analysis_scan_row.py` (8: the real buttons on a real small dataset).
The Workflow panel suites (stage / subsection restore, width budget, dock resizable) pass with the new row.

**Timings through the task, whole real dataset (2026-10-23, 314 cubes x 160 ROIs x 26 wavelengths, i7-10510U 4 cores / 8 threads; machine otherwise idle but with Firefox / VS Code open, 7 GB of 31.8 GB free, i.e. not a clean benchmark):**

| run | wall | frames/s | busy ms per frame: read / prepare / reduce |
|---|---|---|---|
| first full run, saved to `scan.h5`, Standard = 7 workers, background off (the session's setting now) | 227 s | 36 | 75 / 77 / 30 |
| A: 4 processes, background off, in memory | 240 s | 34 | 44 / 43 / 25 |
| B: 4 processes, background ON (sigma 39, bin 4, as on 2026-10-19), in memory | 282 s | 29 | 40 / 67 / 26 |
| C: Standard (6 workers), background ON, in memory | 279 s | 29 | 58 / 102 / 36 |
| 2026-10-19 reference for B (section 14) | 170 s | 48 | 30 / 30 / 20 |

* Writing `scan.h5` cost 10 s in total on the consuming thread (38.4 MB, 314 cubes); reading it back 0.45 s; a run on an unchanged session 2.0 s ("up to date").
* Background removal costs 17 % (A vs B), as on 2026-10-19 (20 %). More workers than 4 gain nothing on this laptop (B vs C), matching the earlier finding.
* **B is 1.66x slower than the same configuration on 2026-10-19.** One known cause: rotation became Lanczos4 on 2026-10-20 (single thread, one frame:
  rotate / crop 12.2 ms against 1.4 ms bilinear, read 12.9 ms; `docs/rotated_zarr_export_interpolation_2026-10-20.md`). That is ~11 ms of ~40 ms per frame, so about
  a third of the busy-time difference; **the rest is not explained** (machine state during the runs is the other suspect). Not isolated yet: a bilinear run and a run on a
  freshly rebooted machine would separate the two.
* The 10-19 target (< 10 min for everything) still holds with margin: 4 to 4.7 min.

**Correction and the rotated export (2026-10-23, same machine, same session ROIs, background off).** The runs above scanned the *raw TIFF* dataset, so the session's
rotation (1.46 deg) and crop were applied to every frame during the scan; nothing was chosen on purpose, it is what the session's geometry says. On the rotated and
cropped OME-Zarr export (`images_1275x799_314x27_c80IZ_u16.ome.zarr`, carried session `2026-10-22_172035`: rotation 0, crop off, 160 ROIs, 314 cubes) the same scan took:

| dataset | wall | frames/s | busy ms per frame: read / prepare / reduce |
|---|---|---|---|
| raw TIFF + rotation / crop, 4 processes (run A) | 240 s | 34 | 44 / 43 / 25 |
| rotated zarr, 4 processes | 111 s | 75 | 26 / 3 / 22 |
| rotated zarr, Standard (6 workers) | 108 s | 77 | 35 / 4 / 30 |

All 2,612,480 means finite. So **a scan of the rotated export is 2.2x faster than a scan of the raw TIFFs**: no rotation in the scan (prepare 43 -> 3 ms) and a faster read.
This also means the "1.66x slower than 2026-10-19" finding is about raw TIFF scans only; the rotated export, the intended input for analysis, is not affected. Numbers of the
two datasets are not comparable value for value (the export rounds to uint16 and was resampled once at export time); not checked here.

## 19. Spectra panel and the scan-results module (2026-10-23)

**Screen.** Spectra dock: a strip of controls on top (formula, fit method, polynomial order, metric, "Fit window", "Error bars"), the plot, one status line. It follows
the selected ROIs and the current cube. It reads `scan.h5` (never the old `AnalysisEngine`) and starts no analysis.

New files (pure unless noted):

* `analysis/spectrum_errors.py`: standard error `STD / sqrt(n_valid)` and its first-order propagation through the four formulas (checked against numerical derivatives).
  The bars mean "how well the aperture's pixels determine the mean value", **not** reproducibility between measurements, and sample / reference are treated as independent
  (they share the illumination): the tooltip says so. Off by default (remembered).
* `analysis/scan_view.py`: `compute_spectra(stored, SpectraRequest)` -> per ROI: formula spectrum, errors, fit curve, metric (via `query.py`, unchanged). Each ROI alone; a
  wavelength without a valid pixel is left out and counted (`n_missing`); a cube never scanned, a ROI added after the scan and a failed fit are explained in `note`, never faked.
* `scan_store.stale_cube_indices(stored, plan, hashes)`: which cubes are out of date; a changed ROI list / wavelength axis / cube list makes **all** cubes stale and says why.
* `analysis/scan_results.py` `ScanResultsModule` (Qt): holds the loaded file (read on a plain thread, 0.45 s for 38 MB) and the stale set. A check = snapshot of the app's
  inputs on the GUI thread (~0.1 s), then on a thread: plan, `cell_hashes`, compare. Triggered (debounced 1.5 s) by geometry, mask, chromatic, background, ROI and frame-role
  changes, and by a loaded file; not while a scan runs (the finished scan reloads and checks again). **Formula / fit / metric changes never trigger it** (they cannot make a
  cell stale; pinned by a test that puts the formula key into the hash and watches it fail).
* `panels/spectra/{panel,plot}.py`: compute off the GUI thread (newest request wins, 100 ms coalescing, result from an older file dropped), `HiddenPanelGate` (hidden = no work,
  one catch-up when shown), theme refresh, error-bar choice via `ui_state` (`spectra/error_bars`). The formula goes to `AreaRoiDetectionSettings.formula_key` through
  `RoiToolbox.set_detection_settings`; fit, order, metric and window to `AnalysisSettingsModule` (saved with the session). The fit window is "set" iff
  `fit_wl_min/max` are set: checking the box sets the middle 80 % of the plotted wavelengths, dragging the band updates the settings, unchecking clears them.
  Results of an out-of-date cube are **drawn dimmed with "OUT OF DATE (...); run the scan again"**, not hidden.
* Wiring: `_Modules.scan_results`, `_wire_analysis_scan` (the file follows the session; reload after a scan finished, was stopped or failed), `SpectraPanel(m.scan_results, ...)`.

Measured on the real dataset (scan file of the whole-dataset run, TIFF session): file loaded and "0 of 314 cubes out of date" known after **1.8-3.1 s**; 160 ROIs of one cube:
no fit 9 ms, polynomial 124 ms (0.8 ms per ROI), Gaussian 111 ms (0.7 ms). Fine on a worker; it is why a fit must not run on the GUI thread when many ROIs are selected.

Tests: `tests/unit/test_lspri_analysis_scan_view.py` (14), `tests/integration/test_lspri_spectra_panel.py` (13: nothing-yet and no-selection states; drawn from the file on a worker thread
and equal to the formula of the stored pair; follows the cube; error bars; controls edit the modules and follow outside changes; fit window set, dragged and cleared; hidden = no work
and one catch-up; a moved ROI -> dimmed + status while a formula change leaves it current; an added ROI is explained; a new scan -> current again; a damaged file is reported). Fault
injection: without the gate, and with the formula key in the hash, the matching test fails. Neighbouring suites (scan row and task, Workflow panel suites, autosave, session, edit menu,
log panel which builds the whole main window) pass. **Not looked at on screen**: no screenshot was taken (ask first).

**Incident (2026-10-23), a lesson for the tools.** `tools/analysis_stage_timing.py` `build_modules` (used by the lab, the golden check and my timing scripts) builds the app's modules
*with their autosave*. A test script that moved ROI 1 by +1 px twice (its "move back" moved nothing, because the ROI object had already changed) had the edit **saved into the real
test session** (`...\images\sessions\2026-09-27_143549\session\session.json`: ROI 1 `center_x` 40.6 -> 42.6). Found because the staleness check reported every cube out of date; the scan
file's stored ROI table proved that this was the only difference (and that the golden check's in-memory timeline had not been saved). **Fixed in the tool**: `build_modules` now
suspends the autosave (verified in a temp folder: an edit is written without it, not with it). The session file itself still needs the one value put back (40.6): left to the
maintainer, because writing it was denied.

## 20. Sensorgram panel (2026-10-23)

**Screen.** Sensorgram dock: a strip of controls on top (display mode, centre and band for averages, smoothing method + window, "Remove spikes", "Baseline"), the plot,
one status line. For the selected ROIs it plots the **metric of the Spectra panel** (position of the fitted maximum or centroid, nm) at every scanned cube. x = time (s) or cube
number, following the app-wide Cube / Time choice. A dashed line marks the current cube; a left click on the plot moves the cube slider to the nearest cube.

New / changed (pure unless noted):

* `analysis/scan_trace.py`: `metric_trace` (one ROI, every cube; NaN where the cube was never scanned, has no usable wavelength or the fit failed), `compute_traces` (only the
  ROIs not already known; cancelled between ROIs, keeps what it finished), `build_series` (statistics **per ROI** with `statistics.apply_statistics`: spike removal, smoothing,
  baseline, in that order; then, for "Average all" / "Average by group", `aggregate_traces` of the finished traces: mean or median, band SD or SEM), `compute_sensorgram`,
  `trace_key` (what a cached trace depends on: the scan object, formula, fit method / order / window, metric). Averaging fitted values, never spectra: a test shows the mean of the
  traces differs from the trace of the pooled spectrum.
* `analysis/scan_view.py`: `fit_and_metric(spectrum, metric)` factored out, so the Spectra panel (one cube) and the Sensorgram (all cubes) use one function and cannot disagree
  (a test pins the equality with the query layer's own `metric_value`).
* `panels/sensorgram/{panel,plot}.py`: compute on a worker thread; the fitted traces are cached by `trace_key` (a statistics change, another selection of fitted ROIs, a cube move and a
  display-mode change recompute nothing; a changed formula / fit / metric does); a new request sets the running job's cancel flag, keeps its finished traces and starts the next one;
  100 ms coalescing; result from an older file dropped; `HiddenPanelGate`. Out-of-date cubes: **hollow rings** over the points and a status note, never hidden. Statistics controls
  edit `AnalysisSettingsModule` (saved with the session); the display mode / centre / band / smoothing / spike / baseline fields already existed in `StatisticsSettings`.
  Spike rejection uses the Hampel defaults (window 5, 3.5 MADs) of `StatisticsSettings`; there is no control for them yet.
* The baseline window is in axis units (seconds or cube number). Switching Cube / Time re-places it at its default (first tenth of the axis), because the old numbers would mean something else.
* Wiring: `SensorgramPanel(m.scan_results, m.roi_toolbox, m.dataset, m.selection, m.analysis_settings, m.cube_display)`, dock visibility, theme refresh. The old pump-plan-step overlay
  idea (sketch §7) is not built.

**Cost, measured on the real scan file** (TIFF session, polynomial order 3, maximum): **1.3-1.4 ms per ROI and cube**: 5 ROIs = 2.2 s, all 160 ROIs x 314 cubes = **66 s** once (status line "Fitting ROI n of N",
cancellable by selecting something else; the finished traces stay cached). The panel shows the selected ROIs only; "select all" is the 66 s case. A faster all-ROI path (worker processes through
`lspr_core.perf`, or a vectorised polynomial fit) is possible and not built. Real values: peak positions 590-605 nm, spread across ROIs ~5 nm per cube (this dataset's ROIs).

Tests: `tests/unit/test_lspri_analysis_scan_trace.py` (15: a drifting peak is followed; equality with the query layer; gaps stay NaN; cache key; only missing ROIs computed; cancel keeps partial;
statistics per ROI; mean of traces vs trace of pooled spectrum; groups, ungrouped, ROI in two groups; gaps do not blank an average), `tests/integration/test_lspri_sensorgram_panel.py` (13: drifting
band on a real small dataset, worker thread, click and cursor, time axis, average all / by group, controls and settings, cache reuse, changed fit recomputes, baseline band, cancel between ROIs,
hidden panel, ringed out-of-date cubes). Fault injection: no cancel of the running job, and no reuse of traces, each fail their tests. Neighbouring suites pass. **Not looked at on screen** (ask first).
Click handling uses pyqtgraph's scene click signal and is tested through the handler / signal, not with a real mouse.
