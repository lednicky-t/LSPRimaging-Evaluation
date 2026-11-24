# Ignore mask and noise, plan step 1 (2026-11-24)

Question (maintainer): does the ignore mask change the noise-to-signal ratio? Test on the current session, with and without the mask, over step 1.

**Data.** LbL dataset `05_LbL_pumpplan_..._2655x27_c80IZ_u16.ome.zarr`, session `2026-11-20_174644` (160 sample ROIs, each measured against its own rim; one persistent ignore mask, v18). Step 1 as edited in the session (merged with steps 2-6): cubes 1-144, no solution change, so any cube-to-cube variation is noise (plus slow drift). Fit: the session's own (polynomial order 3, centroid of the top 20 %, absorbance).

**Method.** In memory only, through the app's scan code (`scan_jobs.build_scan_plan` + `scan_cubes`), twice: as the session stands, and with `resolve_mask` returning no mask. Noise of a series = std of successive differences / sqrt(2) (removes slow drift). Scripts and full output: `tools/mask_noise_study/` (`mask_scan.py`, `mask_analysis.py`, `result_step1_2026-11-24.txt`). Nothing was written into the session (file times checked).

**Result.**
- The mask touches the sample disc of 156 of 160 ROIs: median 2.5 % of the disc removed (0.3 to 71 %); rim: median 2.2 % of the rim. The 4 untouched ROIs give bit-identical numbers with and without.
- **Noise does not change:** peak-position noise, median ratio with / without = **0.996** (95 % interval of the median 0.995-0.998); median noise 0.216 nm without, 0.215 nm with. Absorbance noise / signal at the peak: 0.643 % without, 0.647 % with. 132 of 156 ROIs within +-3 %, 19 slightly better, 5 slightly worse.
- Only ROIs losing 20-50 % of the disc gain a little (median ratio 0.972, interval 0.944-0.993, n = 18); 50 %+ (n = 2) is inconclusive.
- **The mask moves the absolute peak position**, always to shorter wavelength: median -0.11 nm, 5-20 % masked: -0.37 nm, 20-50 %: -1.0 nm (down to -4.5 nm). In units of each ROI's own noise the median shift is 0.5. A shift against the ROI's own baseline cancels this when the mask is the same in all steps; absolute positions and comparisons between ROIs do not.

**Not covered.** A mask that differs between steps (a particle masked in some cubes only); steps with a real signal; step 1 only.

**The stored `scan.h5` is out of date against the current ROI table** (found while validating): 157 of 160 stored ROI layouts differ from `session.json` (ROI 2 is about 110 px away from where the file has it; ids were renumbered or re-placed after the 2026-11-22 scan), and the mask is newer than the file. The Spectra / Sensorgram show it as out of date until a new Scan. The in-memory pipeline itself reproduces a stored scan exactly when given the stored layout (`tools/rim_size_study/validate_pipeline.py`: 21 ROIs, 4 cubes, worst difference 0).
