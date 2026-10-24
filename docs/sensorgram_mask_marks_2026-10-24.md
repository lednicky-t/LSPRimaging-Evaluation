# Sensorgram mask marks (2026-10-24)

**What the maintainer asked for.** A toggle (icon, new *View* group of the Sensorgram ribbon) that marks the points a mask influenced: a
**triangle** (like a play button) for a persistent mask, a **circle** for an individual one, only where the ignore mask really overlaps the
ROIs of the line. The mask icon is no longer a circle in a square (it looked like an ROI): it is a smiley now, app-wide.

**Not the same as the hollow rings.** Those mark cubes whose results are *out of date* (any input changed). The mask marks are orange
(`MASK_COLOR`), bigger (11 / 13 px) and say "a mask removed pixels here".

## Why the scan file got a new array

First idea: compare valid-pixel counts. Measured on the real scan (`2026-10-22_172035`, 314 cubes x 160 ROIs) it works for the sample disk
(identical count in every cube for most ROIs) but **not for the reference ring**: its count varies by 60 px (median) from cube to cube with no
mask at all, because the ring leaves out the other ROIs' disks, which move with the chromatic correction. So the link between a mask and a
ROI is made **during the scan**, where it costs nothing: `frame_reduce.reduce_frame` already counts the aperture pixels left after the ignore
mask (`n_nominal`); the pixels before it are in the layout, and the difference is `n_masked`.

* Scan file `lspri_scan` **1.0 -> 1.1** (maintainer OK 2026-10-24): `/processed/scan/n_masked` uint32 `[cube, roi, wavelength, side]`, written
  with the other arrays of a cube. No stored number changed.
* **Old files are not read** (maintainer: no need). `read_scan` says "scan again to replace it", and the next scan moves the old file to
  `scan.previous.h5` and makes a new one (the existing "file does not fit" path), so every cube is scanned again once (~4.5 min on the test
  dataset with 4 processes). Until then the Sensorgram shows the message instead of a plot.

## Rules

* A point is marked when `n_masked > 0` for any ROI of its line, at any wavelength of the cube, on the sample disk **or** the ring (a mask on
  either changes sample / reference). An average line (all / by group) is marked when any member ROI was touched (`Series.member_ids`).
* Persistent or individual: the rule of `MaskModule.resolve_mask_change`: an individual mask at exactly (cube, wavelength) wins, otherwise the
  latest persistent mask applies. One point can carry both marks (different wavelengths of one cube).
* The scope is read from the *current* mask timeline, the pixel counts from the scan. If a mask is edited after the scan the cubes read as
  out of date (hollow rings) until it is run again, as for every other input.
* Pure part: `analysis/scan_mask_marks.py`. Drawing: `SensorgramPlot.show_series(..., mask_marks)`. Button: `panels/sensorgram/mask_marks_toggle.py`
  (on = `mask` icon in orange, off = `mask-off`, muted), remembered under `sensorgram/mask_marks`, on by default.

## Tests

`tests/unit/test_lspri_analysis_scan_mask_marks.py` (7), `n_masked` in `test_lspri_rewrite_frame_reduce.py` (checked against an independent
rasterisation of disk and ring), `test_lspri_rewrite_scan_arrays.py`, `test_lspri_analysis_scan_store.py` (round trip; a 1.0 file is replaced),
`test_lspri_sensorgram_panel.py` (4 new: triangle / circle on a real scan with real masks, ring-only mask, average, button hides and is
remembered). **Not looked at on screen** (ask first): the marks' size and colour, and the new smiley icon, are for the maintainer's eye.
