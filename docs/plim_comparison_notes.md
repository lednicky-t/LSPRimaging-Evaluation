# plim comparison notes for LSPR imaging evaluation

Repository compared:

- `ondrejstranik/plim.git`
- remote HEAD at `0e3074fbc8e2900c999e20132458e036893c3bec`

Purpose of this note:

- preserve the source-level comparison between the legacy `plim` repo and the current
  LSPR imaging evaluation app
- identify concrete functions and classes that are useful to reuse, reimplement, or
  ignore
- give future maintainers a short map of where similar ideas already exist in this repo

## Executive summary

`plim` is a legacy plasmon-imaging toolkit with a broad but mostly monolithic design. It
contains useful scientific routines for:

- spot detection
- spectral mask extraction
- peak fitting
- kinetic fitting
- SPR calibration and transport modeling

The current imaging evaluation app in `apps/LSPRi/eva` is more modular and already has
stronger infrastructure for:

- ROI handling
- preprocessing
- chromatic registration
- dataset loading/export
- analysis state management

The best outcome is to reuse ideas from `plim`, not its GUI code.

## What `plim` contains

Main source areas in the remote repo:

- `plim/algorithm/spotIdentification.py`
- `plim/algorithm/spotSpectra.py`
- `plim/algorithm/plasmonPeakFit.py`
- `plim/algorithm/kineticFit.py`
- `plim/algorithm/rateFit.py`
- `plim/algorithm/sprSystem.py`
- `plim/utility/_bindingModeFitter.py`
- several GUI wrappers under `plim/gui`

The GUI layer is strongly tied to external packages such as `viscope` and
`spectralCamera`, so it is not a good direct dependency target for this repo.

## Interesting functions and methods

### Spot identification

File:

- `plim/algorithm/spotIdentification.py`

Useful methods:

- `SpotIdentification.getPosition()`
- `SpotIdentification.getRadius()`

What it does:

- builds a contrast image from a spectral cube
- smooths the image
- applies local Otsu thresholding
- uses connected components and region properties
- filters by size and eccentricity

Why it is interesting:

- this is a compact legacy spot detector
- it is easy to understand and could serve as a fallback or reference implementation

Current local analogue:

- `apps/LSPRi/eva/src/lspr_imaging_app/processing/spot_detection.py`
- especially `detect_spots()`, `_refine_detected_spot()`, `_fit_grid_array()`

Recommendation:

- reimplement only the idea, not the class structure
- keep the newer detector as the primary path

### Spectrum extraction

File:

- `plim/algorithm/spotSpectra.py`

Useful methods:

- `SpotSpectra.setMask()`
- `SpotSpectra.calculateSpectra()`
- `SpotSpectra.getA()`

What it does:

- constructs circular, square, or off-centre signal/background masks
- averages the spectral cube over spot and background pixels
- applies spectral smoothing
- subtracts dark count
- returns normalized spectra and absorbance

Why it is interesting:

- this is the clearest reusable scientific block in the repo
- the extraction logic maps directly to ROI-based imaging workflows

Current local analogue:

- `apps/LSPRi/eva/src/lspr_imaging_app/processing/roi.py`
- `apps/LSPRi/eva/src/lspr_imaging_app/processing/analysis.py`
- `apps/LSPRi/eva/src/lspr_imaging_app/gui/plot_manager.py`

Recommendation:

- extract the core idea into a pure helper if the app needs alternate extraction modes
- keep GUI and file handling separate from the math

### Peak fitting and peak metrics

File:

- `plim/algorithm/plasmonPeakFit.py`

Useful functions:

- `gaussian()`
- `multigaussian()`
- `fit_polynom()`
- `fit_polynom_ext()`
- `fit_polynom_der()`
- `fit_gaussian()`
- `get_peakmax()`
- `get_peakstart()`
- `get_peakcenter()`
- `get_statistics()`
- `TDataGen()`

What it does:

- offers several polynomial and Gaussian fitting strategies
- derives peak position by maximum, threshold start, or centroid-like integration
- computes statistics over repeated signals
- includes a synthetic transmission-data generator for testing

Why it is interesting:

- the alternate peak definitions are useful as robust fallback metrics
- the statistical helper is a compact pattern for comparing fit methods

Current local analogue:

- `apps/LSPRi/eva/src/lspr_imaging_app/processing/analysis.py`
- especially `fit_absorbance_curve()` and `metric_value_from_fit()`

Recommendation:

- add one or two fallback peak estimators if the current app needs them
- keep a small synthetic-data test helper if regression tests are added for peak
  selection

### Kinetic fitting

File:

- `plim/algorithm/kineticFit.py`

Useful functions and classes:

- `functionPFO()`
- `functionEDecay()`
- `functionZO()`
- `functionP1()`
- `functionBinding()`
- `functionLinearBinding()`
- `functionDesorption()`
- `functionDoubleBinding()`
- `FitType`
- `KineticFit`

What it does:

- models adsorption, desorption, linear, and double-binding kinetics
- stores the fitted parameters and fit residuals
- supports background-subtracted signal retrieval
- serializes fits to pickle and CSV-style metadata
- exposes quartile statistics through `getParamStats()`

Why it is interesting:

- it is a practical pattern for packaging fitted time-series data
- it separates model choice from the fit results

Current local analogue:

- the imaging app does not appear to have a direct equivalent for this kinetic-series
  abstraction

Recommendation:

- reimplement only if the imaging workflow needs concentration-series or time-series
  binding fits
- use a typed dataclass or domain model instead of a pickle-centric class if added here

### Concentration-series analysis

File:

- `plim/algorithm/rateFit.py`

Useful methods:

- `RateFit.loadData()`
- `RateFit.fitKinetics()`
- `RateFit.fitEquilibrium()`
- `RateFit.plotSignals()`
- `RateFit.plotKinetics()`
- `RateFit.plotEquilibrium()`

What it does:

- loads multiple kinetic fits
- summarizes tau and amplitude using median and quartiles
- fits `1/tau` versus concentration with a censored linear model
- fits amplitude versus concentration with a Langmuir model
- plots the kinetic and equilibrium relationships

Why it is interesting:

- this is the most complete end-to-end analysis pipeline in the legacy repo
- it is scientifically useful if the imaging app ever compares multiple analyte
  concentrations

Recommendation:

- reimplement only for workflows that actually need concentration-response analysis
- if implemented, keep the model layer separate from plotting and file loading

### SPR physical model and transport

File:

- `plim/algorithm/sprSystem.py`

Useful methods and classes:

- `SPRSystem.calibrate_bulk_sensitivity()`
- `SPRSystem.calibrate_from_BSA()`
- `SPRSystem.signal_to_coverage()`
- `SPRSystem.coverage_to_signal()`
- `SPRSystem.signal_to_surface_density()`
- `SPRSystem.plotSurfaceSensitivity()`
- `SPRSystem.summary()`
- `SPRChamber.diffusion_coefficient()`
- `SPRChamber.damkohler()`
- `SPRChamber.summary()`

What it does:

- turns signal into surface mass density
- estimates surface sensitivity from BSA
- computes evanescent length and detection limits
- models chamber transport and mass transfer
- estimates diffusion coefficient and Damkohler number

Why it is interesting:

- this is the most scientifically transferable part of the repo
- it adds physical interpretation to raw sensor signals
- it would be valuable in documentation or calibration workflows for LSPR imaging

Current local analogue:

- no direct equivalent was found in `apps/LSPRi/eva/src/lspr_imaging_app`

Recommendation:

- strongly consider reimplementing the model layer here
- keep it independent from the GUI and use explicit units in the API

### Advanced mechanistic fitting

File:

- `plim/utility/_bindingModeFitter.py`

Useful functions:

- `compute_derived()`
- `pack()`
- `unpack()`
- `ode()`
- `simulate()`
- `cost()`
- `simulate_states()`

What it does:

- solves a two-state ODE system for binding
- performs constrained optimization with `scipy.optimize.minimize`
- supports explicit state simulation and residual minimization

Why it is interesting:

- it is useful if the app ever needs a mechanistic reaction model instead of a
  simple curve fit

Recommendation:

- low priority unless future scientific requirements need it
- do not port the script form directly

## What the current imaging app already does better

The current app already has richer infrastructure in these areas:

- ROI lifecycle and geometry:
  - `apps/LSPRi/eva/src/lspr_imaging_app/processing/roi.py`
  - `apps/LSPRi/eva/src/lspr_imaging_app/domain/roi_editor_tools.py`
- spot detection with scoring and grid support:
  - `apps/LSPRi/eva/src/lspr_imaging_app/processing/spot_detection.py`
- chromatic correction and mask warping:
  - `apps/LSPRi/eva/src/lspr_imaging_app/processing/chromatic.py`
- dataset loading and OME-Zarr support:
  - `apps/LSPRi/eva/src/lspr_imaging_app/io/dataset.py`
- GUI state, undo, overlays, and export orchestration:
  - `apps/LSPRi/eva/src/lspr_imaging_app/gui/*`

That means `plim` should be treated as a source of analysis ideas, not as a base to copy
over.

## Reuse / reimplement / ignore

### Reuse or reimplement

- `SpotSpectra.calculateSpectra()` style extraction logic
- `get_peakstart()` and `get_peakcenter()` style fallback metrics
- `KineticFit.getParamStats()` style summary statistics
- `SPRSystem` and `SPRChamber` physical calibration helpers
- `RateFit` concepts for multi-condition analysis

### Probably reimplement, not copy

- `KineticFit` persistence and model handling
- transport and mechanistic ODE fitting
- any fit classes that rely on old pickle state or hidden globals

### Ignore or avoid

- GUI bootstrap code in `plim/main.py`
- `viscope` and `spectralCamera`-specific wiring
- old utility scripts that mix plotting, file I/O, and analysis in one file

## Suggested next steps if this becomes work

1. Extract a pure spectrum-extraction helper that can be used from ROI-based imaging
   code.
2. Add one or two peak-location fallback metrics that mirror `get_peakstart()` /
   `get_peakcenter()`.
3. Add a small `spr_physics.py` or similar module for signal-to-coverage and transport
   calculations.
4. If needed, add concentration-series analysis as a separate module instead of folding
   it into the GUI.
