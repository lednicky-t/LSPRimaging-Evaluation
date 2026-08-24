# Imaging Measurement Export/Backup Format (proposal)

**Status: proposed, not implemented.** This is a design note to align on before any code is
written. It extends the shared `lspr_measurement` HDF5 format (`docs/schemas/hdf_measurement_format.md`,
`docs/schemas/hdf_standard.md` in the umbrella repo) rather than inventing a parallel one.

## Goal

LSPRi eva needs a way to export/backup its per-ROI spectra and sensorgram traces (no images -
those stay TIFF/OME-Zarr, unaffected by this doc). The natural home is the same HDF5 format
`sLSPR` acquisition already uses for this kind of numeric time-series data, generalized to
support more than one measurement region per file.

## Why HDF5, not JSON, for this part

ROI *geometry* (`roi_table.json`, `AreaRoi`) correctly stays JSON - full fidelity, easy to
version, handles freeform masks. That decision in `format_versioning.md` is unaffected by this
doc.

Spectra and sensorgram data are different: they're numeric arrays that grow one row at a time
as analysis/acquisition proceeds. JSON has no way to extend an array in a file without rewriting
the whole file; HDF5 datasets can be created resizable and extended in place. That's what already
lets sLSPR acq append data live without a full rewrite on every sample, and it's what makes an
HDF5 file usable as an incremental, crash-safe backup during a long analysis run (partial file
stays valid if the app dies mid-run) - JSON can't give us that property.

## Decision: unify, don't fork

Rather than a sibling schema for imaging, extend `lspr_measurement` itself:

- `schema_name` stays `lspr_measurement` (unchanged - both apps' readers already key off this)
- `schema_major` bumps `6 -> 7` to signal the breaking layout change described below
- the new major version adds one grouping concept - a **`roi_id`** - that both apps use:
  - sLSPR acq: exactly one fixed `roi_id` (e.g. `"probe"`) - single fiber-probe measurement,
    unchanged data underneath, just one more level of nesting
  - LSPRi eva: one `roi_id` per sample/reference pair, using the same `pair_id` already used in
    `roi_table.json`, so the two files join without any translation table

This keeps one format, one set of reader/writer helpers in `packages/lspr_io`, one schema doc -
not two formats that drift apart over time.

`roi_id` naming call: chosen to match the vocabulary already established in
`roi_implementation_direction.md`/`roi_system_roadmap.md` (sROI/rROI/pair) rather than inventing
a new term like `channel_id`. Easy to rename before anything is implemented if it reads oddly for
the single-channel acq case - flag it if so.

## Proposed layout (schema major 7)

```
/manifest                             - same identity attrs as today (schema_name,
                                         schema_version, app_name, app_version,
                                         created_at_utc, started_at_utc, created_by...)
/axes/wavelengths_nm                  - one shared array for the whole file

/rois/<roi_id>/meta                   - name, color, geometry_type, compact numeric params
                                         (radius/offsets etc.) - a thin mirror for
                                         self-description only. Not the source of truth:
                                         LSPRi eva's roi_table.json (incl. any freeform mask)
                                         stays canonical; sLSPR acq has no separate geometry
                                         source, so this is just descriptive there.
/rois/<roi_id>/spectra/sample         - resizable 2-D (time x wavelength), raw
/rois/<roi_id>/spectra/reference      - resizable 2-D (time x wavelength), raw
/rois/<roi_id>/spectra/dark           - resizable 2-D (time x wavelength), raw
                                         (sLSPR acq only today; optional for LSPRi eva)
/rois/<roi_id>/processed/metrics      - resizable 1-D-per-column table: acquired_at_unix_ms,
                                         metric_name, metric_value, ... (the sensorgram)
```

This mirrors sLSPR acq's existing raw-vs-processed split (`/data/spectra` vs
`/processed/metrics` today) - just nested one level deeper under `/rois/<roi_id>/`.

## Write cadence

Datasets are created resizable/chunked and extended by one row per completed
frame/time-point, per `hdf_standard.md`'s existing "append raw series instead of rewriting
files" rule. For LSPRi eva this means: during a multi-ROI analysis run, each ROI's spectra and
metric datasets grow as each frame is processed, so the file is a live, crash-safe backup rather
than something only written at the end.

## Compatibility / migration

- readers accept `schema_major == 7` (new indexed layout) directly
- readers accept `schema_major == 6` (old flat `/data/spectra`, `/processed/metrics` layout,
  no `roi_id` concept) via a compatibility shim in `packages/lspr_io` that presents the old
  layout as a single implicit `roi_id` - this is required so existing lab data and any
  not-yet-migrated sLSPR acq files keep loading
- readers reject `schema_major > 7` (future incompatible), per the existing compatibility policy

## Phasing (deliberately split)

**Phase A (this proposal's actual scope): LSPRi eva only.**
LSPRi eva starts writing/reading schema major 7 natively. No existing files to migrate - this
is a new capability for this app, not a change to a live data path anyone depends on today.

**Phase B (separate, later, explicitly scoped task - not bundled into Phase A):**
Migrate sLSPR acq's writer and sLSPR eva's reader to major 7. This is a bigger, higher-stakes
change because:

- it touches the *live acquisition* app's file-writing path, not just an evaluation/export path
- it needs a real migration plan for the existing body of major-6 lab measurement files (per
  `CLAUDE.md`'s "raw data is sacred" rule - old files must remain readable, not just importable
  via a lossy converter)
- it spans two more submodules (`apps/sLSPR/acq`, `apps/sLSPR/eva`) with their own release/version
  history

Phase B should get its own sign-off and its own plan when it's actually taken on, rather than
being assumed as part of shipping Phase A.

## Open questions to confirm before implementation

- **`metric_name`/`metric_value` in `/rois/<roi_id>/processed/metrics`**: fixed enum (e.g.
  `peak_position`, `centroid`, `fwhm`, `absorbance_at_wavelength`) or open-ended string key? Fixed
  is safer for downstream readers; open-ended is more flexible if new metrics get added often.
- **Dark/reference correction for LSPRi eva's sample/reference spectra**: does each ROI's
  sample/reference spectrum already have dark subtracted before it's written, or should raw and
  dark-corrected both be stored (as sLSPR acq effectively allows via separate dark spectra +
  `dark_index`)?
- **`roi_id` naming**, as above - confirm `roi_id` reads sensibly for sLSPR acq's single-channel
  case, or whether a more neutral term is preferred.

## Where this document lives

This file is inside the `apps/LSPRi/eva` submodule (its own git repository, not the umbrella
`LSPR-Suite` repo - see `CLAUDE.md`'s Submodule Workflow section). Committing it requires a
commit inside `apps/LSPRi/eva` first, then a submodule-pointer bump commit in the umbrella repo,
same as any other change in this submodule.
