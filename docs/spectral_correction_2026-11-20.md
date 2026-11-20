# Spectral correction: what the LCTF passband of each image really was (2026-11-20)

Maintainer's idea (2026-11-20): the tunable filter (LCTF) lights each image with a *band*, not one wavelength, and the band's centre is not exactly the commanded wavelength.
A spectrometer on the bench measured every band (`spikes/lspri_acq_phase0/illumination_probe.py`, tab 4, 2026-08-07). Use those spectra to correct how the app places and fits
the points of a spectrum, in three steps the user can switch between: **None** (as before), **Centroid** (each point at the centre of gravity of its band), **Full profile**
(the fit also models that each point is an average over its band).

**Engineering priority served: 1 (correctness and scientific validity), then 2 (data integrity).** The scan, its stored numbers and the images are never touched: the correction
acts on spectrum -> fit -> metric only, which the app recomputes live. A new session starts at None, so no existing number changes until the user picks a mode.

## Decisions (maintainer, 2026-11-20)

- Store the measured spectra as a **session copy of the raw rows** (HDF5 file in the session folder; centroids and widths are derived on load, never stored).
- Loaded through **File -> Import -> Spectral Correction...** (the probe's `spectral_sweep_*.csv` as it is, optionally with its `dark_spectrum_*.csv`), like the metadata and the refractive indices.
- **Polynomial first** for Full profile; Gaussian and Voigt later. Template shift, Response shape and "No fit" read as Centroid (the "i" text says so).
- **The pick-up** sits in the Spectra ribbon before the Formula; an **"i" icon** at the end of the ribbon carries the explanation and the live state, like the Image panel's.
- **Temperature is not handled yet**: first a temperature sweep has to be measured (see "Temperature: to do"). Name: "spectral correction" (not "chromatic correction": that already means the per-wavelength image alignment).
- The spectrometer is taken as calibrated and flat; so is the camera (a slot for its response curve is reserved).

## The maths

Point `k` is the sample's spectrum `f` averaged over the weight `w_k(lambda)` of the light that reached the camera (lamp x filter band x optics x camera), area 1:

    y_k = integral w_k(lambda) f(lambda) d lambda            (the measured band is w_k)

- **Centroid**: x = `lambda_bar_k = integral lambda w_k`. Around it `y_k = f(lambda_bar_k) + (sigma_k^2 / 2) f''(lambda_bar_k) + ...` (`sigma_k` the band's standard deviation, 1.6 to 7 nm):
  the first-order term vanishes exactly there; the curvature term is left.
- **Full profile** (polynomial): the polynomial `P(t)` of the window's scaled wavelength `t = (lambda - middle) / half` is fitted so that its band averages match the points,
  `y_k = sum_m c_m B_km`, `B_km = integral w_k t^m`. The same least-squares problem as before with the Vandermonde matrix replaced by `B`; `B` comes from the band's central
  moments `mu_i = integral (lambda - lambda_bar)^i w` by the binomial theorem (`spectral_passband.passband_vander`; checked against brute-force integration to 1e-20 in the
  tests). The drawn curve and its maximum / centroid are of `P`: the spectrum with the broadening taken out. Same cost as the ordinary fit (one extra matrix, built once per window).
- A scan wavelength with no matching band (not in the table within 0.25 nm, or a weak row) keeps its nominal wavelength with a band of zero width. **Never interpolated** between
  set wavelengths: the point-to-point scatter of the centres is real (0.51 nm, against 0.03 nm replicate noise).
- Absorbance is averaged linearly (the exact average is of the transmittance): up to 0.25 % (A = 0.1), 0.76 % (A = 0.3), 2.8 % (A = 1) of the peak height, for a 30 nm wide peak
  at 700 nm; about a third of that for 40-60 nm peaks in the green; zero for the ratio formula (exactly linear). It is even about the peak: it does not move it. A second pass
  (add the term from the first fit's slope, fit again) would remove it; not built (it would double the cost of the Sensorgram's fits).

## What was measured

- **The filter** (August sweep, 61 settings 420-720 nm, 5 nm step, 30.8 C): band FWHM 4.0 nm at 420, 6.1 at 500, 10.5 at 600, 15 at 700 (the earlier note quoted only the blue end);
  centre minus setting: mean -0.23 nm, scatter 0.43 nm (half-max centroid in the older note: -0.31 / 0.51; the difference is the definition: the app uses the full first moment,
  the camera integrates all the light). Replicates agree to 0.03 nm, so the scatter is a property of the filter, not noise. Out-of-band light (outside +-3 FWHM, whole spectrometer
  range) 0.2-0.7 % of the total: a 1-2 % error in A at A = 1 (depends on the camera, order of magnitude only).
- **Simulation** (cubic absorbance through the real bands, `test_lspri_spectral_passband.py`): the worst absolute peak-position error over polynomial orders 3-5 is 0.51 nm with the
  nominal axis (it depends on the order: a higher order follows the scatter of the centres), 0.17 nm with the centroid axis (steady, but the `sigma^2/2 f''` term is left; sigma^2 grows
  twenty-fold from blue to red), 0.03 nm with the full profile.
- **Real scan** (04_Bulk_sensitivity, 24 ROIs x 314 cubes, 10 nm step, all 26 wavelengths on the table's 5 nm grid; *illustrative*: the table was measured another day, possibly with
  another lamp): default metric (polynomial 3rd order, centroid of the top 20 %): baseline position -0.24 nm (Centroid) / -0.20 nm (Full profile); the event shift (7.7 nm) changes by
  -0.04 nm (-0.55 %) / +0.02 nm (+0.21 %); noise per ROI unchanged (0.054 nm), SNR unchanged. Other settings: shift changes of -0.5 to -1.4 %, position -0.23 to -0.29 nm.
  **Reading it:** the correction matters for *absolute* position (a few tenths of a nm) and for the order-dependence of the nominal axis; for a measured *shift* it is a fraction of a percent
  to about 1 %.

## Where things are

| Part | File |
|---|---|
| Bands, centroids, moments, the passband design matrix, correction axis (pure) | `analysis/spectral_passband.py` |
| Reading the probe's sweep, the session's HDF5 file (pure) | `analysis/passband_store.py` |
| `StoredScan.spectral`, `abscissa_nm()`, `passband_moments()` | `analysis/scan_store.py` |
| The display's scan carries the correction (`set_spectral_correction`, `stored()`) | `analysis/scan_results.py` |
| One-spectrum fit (`fit_polynomial(moments=)`, `FormulaSpectrum.passband`) | `analysis/query.py` |
| Batch fit (`TraceInput.passband`, `_polynomial_group`), order check (`loo_rms_all_orders(moments=)`) | `analysis/trace_fit.py`, `analysis/order_quality.py` |
| Spectra and Sensorgram entry points | `analysis/scan_view.py`, `analysis/scan_trace.py` (`trace_input`, `trace_key`) |
| Owner: table, mode, import thread, session block | `selection/spectral_correction_module.py` |
| Session block, schema 1.13 | `storage/session.py` |
| File -> Import -> Spectral Correction... | `panels/file_menu.py` |
| The pick-up, the "i" text | `panels/spectra/correction_group.py`, `panels/spectra/pick_menu.py` (`correction_picture`) |
| Tests | `tests/unit/test_lspri_spectral_passband.py`, `tests/integration/test_lspri_spectral_correction.py` |

**Why it rides on the display's scan object.** Every consumer already takes `stored` (the display's `StoredScan`) and the caches key on its identity: a corrected copy (arrays shared,
nothing copied) is a different object, so every cache of fitted traces misses by itself and `results_changed` makes every panel redraw. `wavelength_nm` stays the nominal axis (cell hashes,
plan matching); only the spectrum builders read `abscissa_nm()`. Nothing is added to `MetricSettings`, so no old key can go stale.

## Formats

**Session block** (schema 1.13, additive; written only when a table was imported; a 1.12 file loads with no correction):
`"spectral_correction": {"mode", "file": "spectral_correction/spectral_correction.h5", "sha256", "source_name", "illumination", "imported_at"}`.

**`<session>/spectral_correction/spectral_correction.h5`** (schema `lspri_spectral_correction` 1.0; every HDF5 rule attribute: `schema_name`, `schema_version`, `app_name`, `app_version`,
`created_at_utc`, plus `source_name`, `sha256`, `illumination`, `imported_at`):
`/axes/pixel_nm [N]`, `/axes/set_nm [K]`, `/passbands/counts [K, N]` (as imported, gzip), `/passbands/confirmed_nm`, `settle_ms`, `temperature_c` `[K]`, `/passbands/dark [N]` (only with a
dark spectrum). Reserved, not written: `/passbands/detector_response [N]`. A replaced table is kept as `spectral_correction.previous.h5` (one backup slot). The imported CSV is never changed.

**Exports** (Spectra "Save data", Sensorgram "Save data", the ROI table's step columns) record the correction in their `spectral_correction` entry: mode, the table's name, checksum,
light source and filter temperature, how many points had a band, the algorithm version (`StoredScan.spectral_info()`); `{"mode": "none"}` without one. So an exported x value can always be traced.

**Failure behaviour** (said, never hidden): a missing / damaged table, a checksum that is not the one the session recorded, or a block pointing outside the session folder gives a
status-bar sentence and the "i" text says why; no correction is applied meanwhile and the session keeps its block, so the table can be found again.

## Not built yet / to decide

### Temperature: to do (maintainer, 2026-11-20: measure first)

Two sweeps of 2026-08-07 hint at a temperature effect: 13:41 at 24.6 C (no DARK row, other settings) and 15:4x at 30.8 C: mean centre-minus-setting +0.32 nm against -0.32 nm, about
**-0.1 nm per degree C**, and the pattern only partly the same (the difference scatters by 0.29 nm). Two runs of different set-ups prove nothing; it could dwarf the correction (a 6 C
difference is 0.6 nm). The datasets do not record the filter's temperature, so the user would have to say it.

What the temperature sweep needs: the same set of wavelengths and the same lamp at 4 or more filter temperatures (the probe logs `temperature_c` per row), a dark spectrum per run, and a Hg-Ar
line check of the spectrometer's own axis (its calibration cannot be separated from the filter's offset at the 0.3 nm level). What would then be built (additive format change): one table per temperature
(a temperature axis `[T, K, N]`), a way to say which temperature to use (pick one, or enter one), and an interpolation rule, to be tested on the series: the centre linear in T, the band's shape from the
nearest temperature moved to the interpolated centre (blending two shifted bands would broaden them).

### Other open points

- **Camera response** (looked into 2026-11-20). A slope `s` of lamp x camera response moves the true centre by `sigma^2 s`. The slot is reserved (`detector_response`); the import is not built.
  *Which camera:* the old acquisition software (`LSPR-LCTF_v3.00`) drives a monochrome **IDS uEye** through `pyueye` (12-bit mode) and its default image size of 960 x 600 is exactly a 1920 x 1200
  sensor binned 2 x 2, which fits the **IDS UI-3160CP-M-GL Rev. 2.1** of the acquisition notes (onsemi NOIP1SN2000A / PYTHON 2000, 1920 x 1200, 4.8 um, global shutter). **Not confirmed by the
  maintainer**; the camera is not recorded in the datasets (`metaData.txt` has binning and size only; `devices.ini` has an empty Camera section).
  *The curve:* `camera_response/ids_ui-3160cp-m-gl_rev2.1_qe_digitized.csv`, digitized by script from IDS's plot of the sensor's monochrome QE (peak 54.7 % at 550 nm, 51 % at 650 nm, 46 % at 700 nm,
  28 % at 800 nm; about +-1.5 % absolute; sensor only: no lens, no cover glass, **no IR-cut filter**: IDS's manual says an IR-cut filter at 650 nm is fitted "if used in the camera model", and
  whether this camera has one was not found; it would make the red end much steeper). Slope of ln QE: +0.0025 /nm at 450 nm, +0.0010 at 600 nm, -0.0032 at 700 nm.
  *Effect on the August bands* (weighting each band by this curve): the centres move by -0.08 nm at 670 nm and -0.18 nm at 720 nm, and by at most 0.03 nm below 650 nm (mean over the sweep -0.03 nm). So
  it matters only at the red end; the microscope optics (objective, dichroics) have their own slope, which is not in any table.
  **The table is only right for the lamp it was measured with**: the import asks for a free-text light source and keeps it. Maintainer's plan (2026-11-20): measure every lamp once and assume it does not
  change over time (so a lamp's table could later be kept once and offered to every dataset made with that lamp; today it is imported into each session). Whether the August sweep used the LED of the
  LSPRi runs: not known (maintainer, 2026-11-20).
- **Gaussian and Voigt** read Centroid under Full profile. Wanted next (maintainer, 2026-11-20: polynomial, Gaussian and Voigt are the fits that matter; template, response and no fit may stay Centroid).
  The Voigt fit works in photon energy (eV), so the band must be taken to energy first; the solvers also need a numerical band average (a speed check).
- **Absorbance averaging** (second pass, above); **step diagnostics** and **ROI position tuning** still read the nominal axis (`step_diagnostics.py`, `roi_tuning_run.py`).
- **Sweep step.** The table has 5 nm spacing; a scan at other wavelengths has those points uncovered (said in the "i" text). A finer sweep is needed for such scans.
- **Session carry** (the OME-Zarr export with its Session button) does not carry the table: the exported dataset starts without one.
- **Auto-import** from the dataset folder (as `RIs.txt`) is not built; the File menu is the only way in. Several sweeps per import (a temperature series) are refused with a sentence.
- Unseen by eye: the pick-up pictures, the "i" tooltip layout and the File-menu dialogs were checked by tests and by reading, not by looking.
