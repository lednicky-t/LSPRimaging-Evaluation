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
then corrected it to "mean" the same day; mean is the default throughout. Worker processes are fine. float64 is approved
for the mean, **not yet applied**: the mean is still `np.mean` on float32 pixels (float32 pairwise accumulation), so it
equals the old code bit for bit; float64 accumulation would differ by about 1e-7 relative (roughly 0.004 intensity units at
40,000) and, being a sum, could be vectorised for all ROIs without a Python loop. Per `.claude/rules/numerics.md` it
waits for a real-data before/after comparison, which needs the machine free. Commit of the plan and the two tools approved (commit `c6dfca6` in LSPRi/eva, not pushed, no umbrella pointer bump).

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
