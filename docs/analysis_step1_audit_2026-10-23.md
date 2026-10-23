# Analysis step 1 audit (2026-10-23)

Static read of code and docs only; no tests or benchmarks were run. Speed numbers are the ones recorded in
`analysis_redesign_plan_2026-10-19.md` (sections 11-14), not re-measured today.

## Verdict

The compute kernel of step 1 (pixels -> mean, STD, n_valid, n_nominal per ROI / cube / wavelength / side) is built, fast and
tested. Nothing around it exists: no job builder from a session, no output file, no staleness record, no logging, no status-bar
row, no UI, no command line. Only `tools/frame_reduce_lab.py` and two test files call `scan_cubes`.

## Stage split (confirmed against the plan, sections 11-12)

1. Scan: pixels -> reduced numbers. The only step that reads images. Stored ("backbone"). First value = mean + STD (median,
   trimmed mean, plane fit stay in the code as optional layers).
2. Math: formula (absorbance default) on stored numbers -> spectrum per ROI per cube.
3. Fit / metric: spectrum -> peak position etc. -> Sensorgram.

## Built

| Piece | Where | State |
|---|---|---|
| Pixel finding for all ROIs of a frame in one pass | `analysis/frame_reduce.py` `locate_pixels`, `RoiSet` | done, equals the old rasterisers (22 unit tests) |
| Reduction (mean/STD float64 via bincount; median, trimmed mean, plane fit via the old `reduce_*`) | `frame_reduce.reduce_frame`, `group_mean_std` | done |
| Caches: pixel layout per (ROI layout, affine); background weights per valid mask; ROI exclusion mask per ROI layout | `PixelLayout`/`LayoutCache`, `BackgroundWeightsCache`, `scan._Worker.exclusion` | done |
| Divide by background only at ROI pixels | `apply_preprocessing_at` | done, bit-identical |
| Worker processes, 4 cubes per task, progress + cancel between tasks, worker count from `lspr_core.perf` | `analysis/scan.py` `scan_cubes` | done, 5 integration tests (1 / 2 / auto workers equal; cancel) |
| Lazy Qt imports so workers start without Qt | package `__init__` files | done |

Measured on the dev laptop (i7-10510U, 314 cubes x 26 wl x 160 ROIs): 170 s with 4 processes, 149 s with 8 (plan section 14).

## Not built

- Job builder: session + dataset -> `ScanSettings` + `CubeJob`s (ROI timeline per cube, chromatic affine per frame, ignore mask
  warped per frame, which frames are spectral, dark frame). Exists only inside the lab tool.
- Output file. The lab writes `.npz` (`--save`). The planned HDF5 layout of plan section 5 is superseded by section 11, and
  section 11's "lean backbone" is a proposal only.
- Staleness / provenance (recipe JSON, `frame_digest[cube, wl]`, `roi_digest[roi, cube]`) and an algorithm-version constant (P7).
- Logging (see below), `TaskIndicator` wiring, Workflow > Analysis controls (placeholders), second background toggle, CLI.
- Steps 2 and 3 on the new arrays (`analysis/query.py` exists but takes the old pair format), Spectra and Sensorgram panels
  (`NotImplementedError`).

## Findings

1. Logging not done. `scan.py` has no logger. `CubeResult.seconds` (read / prepare / reduce busy time) is collected and then
   dropped. The only worker-count record is a `logger.debug("perf run ...")` line in `lspr_core/perf/runtime.py`, not a structured
   `log_event`. Worker processes have no logging configuration, so per-cube timings must travel back inside `CubeResult`.
2. Status row not wired. No `ThreadedTask` subclass for the scan, no `task_progress`/`task_finished`, no connect in `app_rewrite.py`.
   Progress is per finished task (4 cubes), so cancel latency is up to one task.
3. No output format decided or written (see above). The old `analysis/store.py` (one HDF5 group per ROI x cube, schema 1.x) is
   still in the tree and `apps/LSPRi/eva/CLAUDE.md` "Analysis rules" still describe it ("one ongoing HDF5 file ... each cell has its
   own provenance record", "don't switch the analysis store back to per-settings-version files"). They contradict the redesign.
4. The old backend (`AnalysisEngine`, `tasks.compute_cell`, `provenance.py`) is still constructed in `app_rewrite.py`. No UI
   triggers it, but it carries the P8 showstopper (22 s per provenance lookup at 8,164 files) and ROI-major loop.
5. Unbounded cache: `scan._Worker._exclusion` (one full-frame boolean mask per ROI layout per worker, ~1.2 MB each) has no
   eviction. Fine for 4 layouts; a session whose ROIs move in every cube would hold hundreds per worker. `LayoutCache` is bounded (LRU).
6. Correctness evidence is partly outside the test suite: identity to the old code on real data was measured with the lab
   (`--sections verify`), the unit/integration tests are synthetic. Disclosed limit: a pixel exactly on a ROI boundary could flip
   by floating-point rounding (estimated ~2 % chance for one pixel in the whole dataset, effect ~1e-3 on one ROI mean).
7. The job builder is where the next wrong-number risk sits (wrong affine for a frame, ROI layout of the wrong cube, ignore mask
   not warped). Needs a golden test on real data against `compute_cell` before anything is stored.
8. Per-worker memory (`_SCAN_WORKER_BYTES` = 600 MB) is a guess from one machine (stated in the code comment).

## Suggested order

1. Agree the output file layout (data format: needs maintainer approval; `docs/schemas/hdf_standard.md`, `.claude/rules/hdf5.md`).
2. Job builder as a pure function, golden test on real data.
3. Scan task + status row + structured logging (small, mechanical).
4. Store writer + staleness digests + algorithm version.
5. Workflow > Analysis controls (Run / Stop / scope / background toggle), then CLI.
6. Step 2 adapter + Spectra panel; step 3 + Sensorgram.
