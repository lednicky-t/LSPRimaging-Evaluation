# Wavelength 0.0 nm ("dark frame") policy

One-page reference for anyone touching wavelength data in the rewrite -
written so this doesn't have to be re-derived each time (root `CLAUDE.md`'s
"write a short reference doc after non-trivial exploration" convention).

## The domain fact

`0.0` nm, when present in a dataset, is the dark/background frame: the LED
was off, no illumination, and the frame exists to estimate the sensor's own
dark-current offset. It is:

- **Real, ordinarily-acquired data** - gets a genuine `ImageRecord`, is a
  real, selectable index everywhere a per-cube wavelength list feeds a UI
  control.
- **Not a spectral sample point** - it must never be treated as "one more
  wavelength" by anything that fits, averages, or plots a spectrum.

Confirmed, not assumed: the stable app's `gui/analysis_worker_mixin.py` has
a real, working consumer of exactly this convention
(`dark_frame_pixel_impact()`, `dark_mask = wavelengths == 0.0`), and
`docs/row_banding_artifact_analysis_2026-09.md` independently calls the same
thing "`WL0`, LED off".

## The rule

**Any code that treats "all wavelengths for a cube" as spectral data (a fit,
a spectrum plot, a metric, an average across wavelength) must exclude
`0.0` explicitly first**, via `dataset.model.is_dark_frame_wavelength()` (or
the `DARK_FRAME_WAVELENGTH_NM` constant next to it). Nothing in
`DatasetModule.wavelengths_for_cube()` - the canonical per-cube wavelength
query - filters it out automatically, and it should not: that method's job
is "every real frame this cube has," dark frame included.

## Where this is enforced today

`AnalysisEngine` (`analysis/engine.py`) is the one real consumer of
per-cube wavelength lists for spectral compute, and all three of its
wavelength-iterating methods exclude the dark frame:

- `_gather_wavelength_inputs` - builds `compute_cell`'s per-wavelength
  inputs. Excluding it here is what keeps a dark frame out of a stored
  `CellResult`, and therefore out of `formula_spectrum`/`fit_spectrum`/
  `metric_from_spectrum` (`query.py`) - none of which have their own filter,
  because they trust their input never contains one.
- `_gather_current_inputs` - builds the planning-time settings-snapshot
  fingerprint. **Must exclude identically to `_gather_wavelength_inputs`**,
  or the live (planning) and stored (compute) fingerprints permanently
  disagree and every cell looks stale forever - the same failure shape as
  the placeholder-mask-version bug fixed 2026-09-23 (see the build log).
- `_naming` - the per-dataset filename precision scheme for mask/chromatic
  snapshots. Excluded for consistency: the dark frame never gets a snapshot
  written through this pipeline once the two methods above exclude it, so
  it shouldn't influence the precision derived here either.

`ChromaticModule.sample_wavelengths_for_cube` already followed this rule
independently (2026-09-2x) - the `AnalysisEngine` fix (2026-09-30) brings
the one remaining gap in line with it, not a new pattern.

## Where the dark frame stays *included*, on purpose

- **`DatasetModule.wavelengths_for_cube()`/`wavelengths()`** - the raw
  per-cube/global query. Filtering here would make it impossible for any UI
  to ever show or select the dark frame at all.
- **The Image panel's wavelength slider** (`panels/image/panel.py`,
  `DataAxisSlider`) - the dark frame is a normal, selectable tick, with a
  visual scale-break glyph marking the (usually large) gap between it and
  the first real spectral wavelength. This is deliberate: the maintainer
  can preview the dark frame like any other frame, it is simply excluded
  from *automatic* spectral routines, not hidden from view.
- **Dataset export** (`panels/workflow/dataset_export.py`) - raw data is
  sacred (root `CLAUDE.md`); the dark frame exports like any other frame.

## What this does *not* yet cover

There is no real dark-current **compensation** (subtraction) step anywhere
in this codebase yet, stable or rewrite. The stable app's
`dark_frame_pixel_impact()` is a one-off diagnostic button in the
preferences dialog ("test what subtracting the dark frame would do"), never
wired into the real compute path. Building a real "use the dark frame to
correct sample/reference values before the formula" mode is future work
with its own open design questions (where the toggle lives, whether it
becomes part of a cell's provenance fingerprint) - the same shape of
decision as the already-flagged fractional-pixel-weighting toggle in
`rewrite_status_and_plan_2026-09-30.md` §6. Needs its own design pass once
the Analysis stage UI exists to host it; not scoped into the exclusion fix
this doc describes.
