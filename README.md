# LSPRimaging Evaluation

Desktop app for evaluating LSPR (localized surface plasmon resonance) imaging
datasets: a stack of wavelength-scan images captured across one or more
spectral cubes (time points). Load a dataset, define ROIs, and get
per-ROI absorbance spectra and sensorgrams.

## What it does

- **Dataset** — loads a TIFF image stack (files named like
  `WL500Frame0.tif`, one per wavelength × spectral cube) or an OME-Zarr
  dataset. TIFF stacks can be exported to a chunked, sharded OME-Zarr
  dataset for much faster loading of large datasets.
- **Image tools** — rotate, flip, crop, and physical pixel-size
  calibration, applied consistently in preview and in exports.
- **Chromatic correction** — corrects per-wavelength chromatic
  aberration by registering each wavelength against a reference image
  (affine or similarity/radial model).
- **Mask** — histogram- and drawn-region-based pixel masking to
  exclude areas (dust, saturated pixels, etc.) from analysis.
- **Background removal** — estimates and subtracts a smooth spatial
  background before analysis.
- **ROI editor** — sample ROIs with a local reference ring for
  background-corrected absorbance, including multi-ROI arrays and
  groups.
- **Analysis** — per-ROI absorbance spectra and sensorgrams (a metric
  tracked across spectral cubes/time), with polynomial peak/centroid
  fitting. Large OME-Zarr datasets use a chunk-aware fast path that
  reads only the pixels a selected ROI actually needs.

## Running

From the `LSPR-Suite` repo root (see the top-level `CLAUDE.md`/setup
instructions for the one-time `pip install -r requirements.txt`):

```powershell
lspri-evaluation      # this app directly
lspr-suite            # suite launcher (recommended entry point)
```

For VS Code "Run Python File", use [`run.py`](run.py) in this directory.

## History: the rewrite

LSPRi Evaluation was rebuilt from scratch on the `rewrite` branch (2026-09 to
2026-10) and replaced the original app (2026-10-29). The original is kept as the
git tag `legacy-final`. What the app
can do today, the rules for working on it, and the open decisions are in
[`CLAUDE.md`](CLAUDE.md); every design record and measurement is indexed in
[`docs/README.md`](docs/README.md). The latest whole-app audit is
[`docs/whole_app_audit_2026-10-29.md`](docs/whole_app_audit_2026-10-29.md).

## Data layout

See [`DATA.md`](DATA.md) for where the app looks for dataset folders and
how to point it at your own data.

## Tests

Unit tests for the scientific core (spectrum fitting, ROI/background
math, chromatic registration, the zarr-chunk-aware fast path) live in
the umbrella repo's `tests/unit/` folder, not here:

```powershell
python -m pytest tests/unit/test_lspri_*.py
```

The Qt and HDF5 tests are in `tests/integration/test_lspri_*.py`. Run those one
file per pytest process: all files in one process stalls.

## Further reading

- [`docs/format_versioning.md`](docs/format_versioning.md) — how file/schema versions are handled
- [`docs/image_tools_coordinate_spaces.md`](docs/image_tools_coordinate_spaces.md) — raw vs. processed coordinate spaces (read this before touching ROI/coordinate code)
- [`docs/versioning_and_repo_practices.md`](docs/versioning_and_repo_practices.md) — repo practices

`docs/roi_editor_branch_plan.md`, `roi_implementation_direction.md`, and
`roi_table_direction.md` document an earlier ROI redesign proposal;
the ROI model that actually shipped is `AreaRoi`/`AreaRoiGroup` in
`domain/models.py`, not the naming those docs propose — treat them as
historical context, not a current plan.

## Citing this software

This app is part of [LSPR Suite](https://github.com/lednicky-t/LSPR-Suite) — see that repo's `CITATION.cff` for how to cite it.
