# OME-Zarr re-export bug + `Experimental_data/` metadata copy (2026-09-27)

Two related changes to the rewrite's OME-Zarr export path
(`src/lspr_imaging_app/dataset/io.py`, `src/lspr_imaging_app/io/_zarr_export_worker.py`).
Saved here because both required tracing a bug across a process boundary and
a genuine two-copies-of-the-app trap that's easy to fall into again.

## Trap: two parallel `dataset io` modules

`src/lspr_imaging_app/io/dataset.py` (the **stable** app) and
`src/lspr_imaging_app/dataset/io.py` (the **rewrite**) are near-duplicate
implementations of the same OME-Zarr export/load logic - the rewrite was
forked from the stable module and both are still live during the rewrite
transition (see `rewrite_build_log_2026-09.md`). Same function names
(`_ome_zarr_root`, `_ome_zarr_plane_path`, `field_lines`, ...), same
`OME_ZARR_ARRAY_DIRNAME = "0"` constant, same synthetic-path grammar - but
they are **not the same module**, and a fix applied to one does not reach
the other. Before editing anything in this area, check which one the code
path you're chasing actually imports (`grep` the call site, don't assume
from the function name alone).

Both share exactly one file: `src/lspr_imaging_app/io/_zarr_export_worker.py`
(`ProcessPoolExecutor` shard-writer workers, used by both `export_ome_zarr_dataset`
implementations). This doc's export worker fix lives there, so it fixes the
bug for both apps at once - deliberately, not by accident, but it's the only
piece that's actually shared.

## Bug: re-exporting (re-chunking) an already-OME-Zarr dataset failed

**Symptom**: opening an existing OME-Zarr dataset and exporting it again
(e.g. to change chunk size/shard mode) raised an error partway through.

**Root cause**: an OME-Zarr dataset's `ImageRecord.path` is a synthetic,
non-existent path (`{cube_pos}.{wl_pos}.0.0` under the array dir - see
`dataset/io.py`'s `_ome_zarr_plane_path`/`_read_ome_zarr_plane_by_path`), not
a real file. The main process already knows to detect this and route reads
through zarr instead of a file open (`_load_image_array_uncached`). But
`export_ome_zarr_dataset` writes shards via `ProcessPoolExecutor` -
**separate child processes** - and `_zarr_export_worker.py`'s `_load_image()`
only ever tried `tifffile`/`PIL.Image.open()` on the path string, which
fails immediately on a synthetic path.

This is a distinct mechanism from the documented `qthreadpool_zarr_crash_investigation.md`
invariant ("never let a `QThreadPool` worker touch an OME-Zarr read" - a
native heap-corruption crash from sharing memory with the main process's
zarr handles). `ProcessPoolExecutor` workers are separate OS processes with
their own memory - reading zarr from inside one is safe; it just needed the
same detection logic the main process already has.

**Fix** (`io/_zarr_export_worker.py::_load_image`): detect a synthetic
OME-Zarr record path the same way the main process does, and read it via
`dataset.io`'s `_ome_zarr_root`/`_ome_zarr_array_dir`/`_read_ome_zarr_plane_by_path`
(lazy import - matches the module's existing "minimal imports at module
scope" convention) before falling back to tifffile/PIL. Verified end-to-end
with a real round trip: TIFF stack → OME-Zarr (chunk 32) → reload → re-export
at chunk 16 with a different shard mode → pixel-exact match against the
original arrays.

The stable app (`io/dataset.py`) already had the *reading* half of this
fix as `_load_image_array_native` (dtype-preserving, OME-Zarr-aware) - but
that function is only ever called once, to probe shape/dtype before export
starts, not from inside the actual per-shard worker. So the stable app has
the same underlying bug; it just wasn't hit yet. Since the fix lives in the
shared worker file, both apps are fixed by the one change.

## Feature: `Experimental_data/` + auto-written metadata sidecar

Maintainer request: exporting a dataset to OME-Zarr should carry the raw
acquisition metadata files along, in a folder a person (or another tool) can
just look in, with the export still auto-loading all its metadata with no
manual re-import step.

**Design** (`dataset/io.py`):

- `EXPERIMENTAL_DATA_DIRNAME = "Experimental_data"`, `experimental_data_dir(folder)`.
- `_export_experimental_data(dataset, destination)`, called at the end of
  `_export_ome_zarr_dataset_to_path` (so it's part of the same
  write-to-temp-then-atomic-rename unit `export_ome_zarr_dataset` already
  uses for collision safety - metadata and pixels land together or not at
  all):
  1. If the *source* dataset already has `Experimental_data/` (a prior
     export being re-chunked, or a TIFF-stack folder someone populated by
     hand) - `shutil.copytree` it forward verbatim. Never re-derived, so it
     survives any number of re-exports without regenerating or losing
     anything.
  2. Else, whichever real file(s) back the source's metadata - reuses the
     *existing* discovery functions unchanged (`find_native_imaging_measurement_file`,
     `find_legacy_metadata_files`) - copied in (`shutil.copy2`, originals
     untouched).
  3. Always, if `dataset.acquisition_metadata is not None`: (re)write
     `analysis/acquisition_metadata.json` via the already-existing
     `save_acquisition_metadata_sidecar`. This is the file
     `load_acquisition_metadata` already checked *first*, before this
     change existed - so auto-load on the next open needed zero new
     loader logic for the common case.
  4. Any `OSError` here is logged and swallowed, not raised - the pixel
     data is what matters and has already been written successfully by
     the time this runs.

- `load_acquisition_metadata`'s existing priority chain gained one new step,
  inserted *after* the sidecar check (an explicit prior edit/import should
  never be silently overridden by re-scanning raw files) and *before* the
  nearby-native-HDF5/legacy-CSV fallbacks: if `Experimental_data/` exists
  and has files, classify-then-import them exactly like the manual "Import
  metadata..." action does (`io/metadata_import.py`'s `import_metadata_files`/
  `classify_metadata_file` - content-sniffed, not filename-based, reused
  as-is). This means a file dropped into `Experimental_data/` *after* the
  export was made is still picked up on the next load, not just whatever
  existed at export time.

Three persistence layers now coexist, each for a different consumer, not
redundant in practice:

| Layer | Where | Consumer |
|---|---|---|
| `lspr_acquisition_metadata` zarr attr | inside the zarr group | `load_ome_zarr_dataset` (existing, unchanged) |
| `analysis/acquisition_metadata.json` | plain file next to the export | `load_acquisition_metadata` step 1 (existing check, now always populated by export) |
| `Experimental_data/*` | plain files next to the export | human/archival - the literal original acquisition files, plus `load_acquisition_metadata` step 2 (new) as a fallback/refresh path |

Verified with a standalone script (not yet a committed test - see
`test_lspri_ome_zarr_acquisition_metadata.py` for the pattern to extend, and
note it currently exercises the **stable** `io/dataset.py`, not this
module): no-raw-file export writes only the sidecar; a source with
`Experimental_data/` carries it through a fresh export and a second
re-chunk re-export unchanged; deleting the sidecar and leaving only a
content-classifiable file in `Experimental_data/` still round-trips
`load_dataset(...).acquisition_metadata` correctly. Full LSPRi suite (819
tests) passes with no regressions.

**Not yet done**: a permanent regression test for the rewrite's `dataset/io.py`
export path specifically (mirroring `test_lspri_ome_zarr_acquisition_metadata.py`
but importing from `lspr_imaging_app.dataset.io`, not `lspr_imaging_app.io.dataset`) -
flagged rather than silently skipped, since the existing test file's name
would suggest it already covers this and it does not.
