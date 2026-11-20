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
- **Polynomial first** for Full profile; **Gaussian and Voigt built later the same day, as the closed-form (analytic) band approximation** (maintainer's choice, 2026-11-20, after seeing the accuracy). Template shift,
  Response shape and "No fit" read as Centroid (the "i" text says so).
- **The pick-up** sits in the Spectra ribbon before the Formula; an **"i" icon** at the end of the ribbon carries the explanation and the live state, like the Image panel's.
- **Temperature is not handled yet**: first a temperature sweep has to be measured (see "Temperature: to do"). Name: "spectral correction" (not "chromatic correction": that already means the per-wavelength image alignment).
- The spectrometer is taken as calibrated and flat. The camera is flat too, unless a **camera response** is imported (File -> Import -> Camera Response..., built later the same day, see below).

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
- **Gaussian and Voigt on the real Bulk scan** (24 ROIs x 314 cubes, with the a2A3840 camera response; the bands as Gaussian in nm for the Gaussian fit and in energy for the Voigt fit): Gaussian fit, baseline position -0.25 nm
  (Centroid) / -0.29 nm (Full profile), the 7.98 nm event shift -1.3 % / -1.4 %, noise unchanged (0.062 nm), no gaps, same speed as before (0.03 ms per cube and ROI); Voigt fit, position -0.29 / -0.32 nm, event shift
  -1.1 % / -1.2 %, noise unchanged, about twice as slow with the full profile (0.04 -> 0.08 ms for the maximum, 0.11 -> 0.21 ms for the top-20 % centroid). One spectrum (ROI 1, cube 100, Gaussian fit): the underlying
  peak is 0.7 % taller (0.3409 -> 0.3432) and 0.2 nm narrower (FWHM 106.5 -> 106.3 nm): small, because a 109 nm peak is broad against 4-15 nm bands.
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
| One-spectrum fit (`fit_polynomial(moments=)`, `fit_gaussian(band=)`, `fit_voigt(band=)`, `FormulaSpectrum.passband` / `.band`) | `analysis/query.py` |
| Gaussian / Voigt solvers with the band average (`solve_gaussian(band_sigma=)`, `solve_voigt(band=)`, exact derivatives, no band = the original code path, bit-identical) | `analysis/gaussian_fit.py`, `analysis/voigt_fit.py` |
| Batch fit (`TraceInput.passband` / `.band`, `_polynomial_group`, `_gaussian_group`, `_voigt_group`), order check (`loo_rms_all_orders(moments=)`), the Spectra panel's batch solve (`scan_view._batch_solved`) | `analysis/trace_fit.py`, `analysis/order_quality.py`, `analysis/scan_view.py` |
| Spectra and Sensorgram entry points | `analysis/scan_view.py`, `analysis/scan_trace.py` (`trace_input`, `trace_key`) |
| Owner: table, mode, import thread, session block | `selection/spectral_correction_module.py` |
| Session block, schema 1.13 (+ `camera_response`: 1.14) | `storage/session.py` |
| Camera response: the curve, its weighting of the bands, the import, the report | `analysis/spectral_passband.py` (`CameraResponse`, `row_profile(response=)`, `derive(table, response)`), `analysis/passband_store.py` (`parse_camera_response`), `selection/spectral_correction_module.py` (`import_camera_response`, `remove_camera_response`, `camera_report`), `panels/file_menu.py` |
| File -> Import -> Spectral Correction... | `panels/file_menu.py` |
| The pick-up, the "i" text | `panels/spectra/correction_group.py`, `panels/spectra/pick_menu.py` (`correction_picture`) |
| Tests | `tests/unit/test_lspri_spectral_passband.py`, `tests/integration/test_lspri_spectral_correction.py` |

**Why it rides on the display's scan object.** Every consumer already takes `stored` (the display's `StoredScan`) and the caches key on its identity: a corrected copy (arrays shared,
nothing copied) is a different object, so every cache of fitted traces misses by itself and `results_changed` makes every panel redraw. `wavelength_nm` stays the nominal axis (cell hashes,
plan matching); only the spectrum builders read `abscissa_nm()`. Nothing is added to `MetricSettings`, so no old key can go stale.

## Formats

**Session block** (schema 1.13, additive; written only when a table was imported; a 1.12 file loads with no correction):
`"spectral_correction": {"mode", "file": "spectral_correction/spectral_correction.h5", "sha256", "source_name", "illumination", "imported_at"}`.
Schema 1.14 (2026-11-20, later): the block may also hold `"camera_response": {"source_name", "sha256", "imported_at", "wavelength_nm": [...], "response": [...]}` (about 200 points: small enough to
live in the JSON) and then needs no table (`"file"` absent): the camera can be imported before the lamp. A 1.13 block has none and loads with a flat camera.

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

- **Camera response** (looked into 2026-11-20; **import built the same day**: File -> Import -> Camera Response... / Remove Camera Response). A slope `s` of lamp x camera response moves the true centre by `sigma^2 s`.
  *How it works:* the curve (`CameraResponse`: wavelength and a relative response, any scale, normalised to 1 at its maximum, linear between points, the nearest end value outside) weights every measured band
  before it is normalised, `w_k ~ S_k(lambda) R(lambda)`, so the centroid, the width and the moments all follow. It must cover the sweep's set wavelengths (else the import says so). It is independent of the lamp
  table and kept in the session block; importing or removing one re-derives the bands (~50 ms, on a thread); the display's scan is a new object only when a correction is on (`ScanResultsModule` compares
  table, camera and mode); the `i` text and the import report say how far the camera moves the band centres (`Passbands.centroid_flat_nm` keeps the centres without it). Exports record its name and checksum.
  *Measured on the real Bulk scan with the a2A3840 response:* the camera moves the band centres at the scan's wavelengths by +0.03 nm (blue) to -0.18 nm (720 nm); in the default metric (polynomial 3rd order, centroid
  of the top 20 %) the baseline position changes by -0.04 nm and the 7.7 nm event shift by -0.08 %; with the maximum, -0.01 nm and -0.09 %.
  *Which camera.* The acquisition notes (`docs/architecture/general/lspri_acq_build_log.md`, `spikes/lspri_acq_phase0/docs/camera_backend_and_throughput_findings.md`) name three real cameras:
  **Basler ace 2 a2A3840-45umBAS** (Sony IMX334, 3840 x 2160, 2.0 um, rolling shutter) = the maintainer's call as the *primary* camera of LSPRi acquisition; **Basler ace acA5472-17um** (Sony IMX183, 5472 x 3648,
  2.4 um); IDS UI-3160CP-M-GL Rev. 2.1 (onsemi PYTHON 2000, 1920 x 1200, 4.8 um, global shutter; the old `LSPR-LCTF_v3.00` software drives an IDS uEye, so it is the older camera). The camera is not recorded
  in any dataset. **Confirmed by the maintainer (2026-11-20): the datasets use the Basler a2A3840-45umBAS** (the 3840 camera, 8.3 MP). That means the legacy `BinningH=4` of the 2025 dataset (1300 x 900 image) is a code of the
  old software and not a four-fold binning (four-fold binning of this sensor would give 960 x 540); the app only stores the value and never calculates with it.
  *The curves* (`camera_response/`, all three digitized or read from the manufacturers' published graphics; relative shapes, sensor only: no lens, **IR-cut filter unknown** for all): Basler a2A3840-45umBAS
  `basler_a2A3840-45umBAS_imx334_relative_response.csv` (read exactly from Basler's vector graphic, 412-997 nm, 3 nm sampling, peak 1.0 near 523 nm); Basler acA5472-17um
  `basler_acA5472-17um_imx183_relative_response.csv` (vector graphic, 401-1000 nm, peak near 530 nm); IDS `ids_ui-3160cp-m-gl_rev2.1_qe_digitized.csv` (QE in %, digitized from a raster plot, +-1.5 %).
  *Effect on the August bands* (weight each band by the curve; smoothing the curve's 3 nm ripple changes nothing): a2A3840: +0.03 nm below 520 nm, -0.02 at 520-620, -0.06 at 620-680, -0.11 to -0.13 at 700-720
  (extremes -0.18 .. +0.07); acA5472: -0.06 at 620-680, -0.26 at 700-720 (extremes -0.31); IDS: -0.19 at 700-720. So the camera matters only at the red end, by a tenth to a third of a nm; the microscope optics
  (objective, dichroics) have their own slope, which is not in any table.
  **The table is only right for the lamp it was measured with**: the import asks for a free-text light source and keeps it. Maintainer's plan (2026-11-20): measure every lamp once and assume it does not
  change over time (so a lamp's table could later be kept once and offered to every dataset made with that lamp; today it is imported into each session). Whether the August sweep used the LED of the
  LSPRi runs: not known (maintainer, 2026-11-20).
- **Gaussian and Voigt Full profile: built, as an approximation** (see "Gaussian and Voigt" in the maths above and below). Template, response and no fit read Centroid (maintainer, 2026-11-20: polynomial, Gaussian and Voigt
  are the fits that matter). *Why an approximation:* the real bands are far from Gaussian (kurtosis median 11, range 3-243; skew -1.7 to +12), so a Gaussian peak averaged over a band is not exactly the wider Gaussian of the closed form.
  Measured on the August sweep, the largest |exact - closed form| for a Gaussian peak: 0.026 % of its height at FWHM 141 nm, 0.13 % at 82 nm, 0.6 % at 47 nm, **2 % at 28 nm**; the Bulk data's peaks are 109 nm wide (about 0.07 %).
  An exact band average needs about 30 non-uniform nodes per band (equal-weight quantile nodes plateau at 0.5-0.9 % even at 32 nodes, because of the wings) or a mixture of 3-4 Gaussians per band, and would make these fits
  roughly 5-30 times slower; not built. Revisit if narrow peaks (FWHM < 50 nm) are ever measured.
- **Absorbance averaging** (second pass, above); **step diagnostics** and **ROI position tuning** still read the nominal axis (`step_diagnostics.py`, `roi_tuning_run.py`).
- **Sweep step.** The table has 5 nm spacing; a scan at other wavelengths has those points uncovered (said in the "i" text). A finer sweep is needed for such scans.
- **Session carry** (the OME-Zarr export with its Session button) does not carry the table: the exported dataset starts without one.
- **Auto-import** from the dataset folder (as `RIs.txt`) is not built; the File menu is the only way in. Several sweeps per import (a temperature series) are refused with a sentence.
- Unseen by eye: the pick-up pictures, the "i" tooltip layout and the File-menu dialogs were checked by tests and by reading, not by looking.
