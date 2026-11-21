# OME-Zarr export: 5 hours on an exFAT USB drive, and the fix (2026-11-20)

Maintainer question: exporting `...LED_V2` (71,685 planes, rotate + crop) shows an ETA of ~5 h for ~100 GB. Reasonable? Why slow?
Outcome: the ETA was right, the cause is the **file layout on that drive**, not compression and not computing. No code changed;
the fix is a setting (Export tab -> Shard -> **Cube**).

## Setup that mattered

- Source `images/` and the export destination were **both on E:** = Micron/Crucial X10 USB SSD, **exFAT, 2 MiB allocation unit**.
- Export tab: Shard = per image (one shard file per plane), compression off (an earlier run had lz4 on), 80 x 80 chunks, 7 workers
  ("Standard" on 4 cores / 8 threads). Session: rotation 1.4859 deg, crop 1279 x 802.
- The shard path is `c/<cube>/<wl>/0/0`, so **two new folders and one file per plane**.

## Measured

Real partial outputs of the two cancelled runs (file times): lz4 run **4.3 planes/s**, uncompressed run **3.7 planes/s** (~4.6 h for
71,685 planes). Then a controlled benchmark with the app's own `write_shard` in a 7-process pool on cold cubes from the end of the
set (216 planes per run; 378 for the per-cube run), steady-state planes/s:

| Run | planes/s | Where one plane's worker time goes |
|---|---|---|
| Write only (no read, no compute), per image -> E: | 4.0 | mkdir 1,455 ms, open + write 284 ms |
| same -> C: (internal NVMe, NTFS) | 470 | mkdir 2.8 ms |
| Source read only, raw bytes, 7 threads | 41.5 files/s | 168 ms per file |
| **Full export, per image, E: -> E:** (what was run) | **3.9** | read + rotate 352, **mkdir etc. 1,516 (79 %)**, write 49, encode 2 |
| Full export, per image, E: -> C: | 20.6 | read + rotate 416, write 15, mkdir 14 |
| same with lz4 | 18.8 | encode 22 ms, 1.23 MB per plane instead of 2.26 |
| **Full export, per cube, E: -> E:** | **28.0** | read + rotate 376, write 31, folders 58 |

- A new folder on this exFAT drive cost ~1.5 s each under 7 writers (the file system serialises the metadata writes; every folder
  is initialised as a whole 2 MiB cluster). This is the bottleneck: it reproduces the real rate with nothing else running.
- **Disk space:** measured on E: 50 empty folders = 100 MiB (2 MiB each), 50 files of 1.2 MB = 100 MiB (2 MiB each). Per image one
  plane costs 2 folders + a file rounded up to 2 MiB clusters: **8 MiB uncompressed (560 GiB for the set), 6 MiB with lz4 (420 GiB)**,
  against 374 GiB free: the per-image export **would not have fitted** (disk full at ~90 % with lz4, ~65 % without). Per cube:
  ~171 GiB uncompressed, ~99 GiB with lz4.
- Compression is not the slow part: ~5-10 % of the time, ~45 % less data (2.26 -> 1.23 MB per plane).

## Expected for the full set (extrapolated from 8-14 cubes; early cubes have smaller files, so likely a bit faster)

Per image on E: 5.1 h (and a full disk). **Per cube on E: ~43 min (28 planes/s).** Per image to C: ~58 min but C: had 121 GB free.

## Built the same day (maintainer: "do all of those")

1. **Layout check before an export** (`panels/image/export_preflight.py`, pure estimate in `dataset/export_disk_estimate.py`):
   after the destination folder is chosen the Export tab asks the drive for its allocation unit, file system and free space
   (Windows `GetDiskFreeSpaceW` / `GetVolumeInformationW`, elsewhere `statvfs`) and works out files, folders and **size on disk**
   for the layout as set and for Cube. A dialog appears **only** when the export would not fit (on-disk size > 98 % of the free
   space) or the layout is slow there (its folders alone take >= 50 % of the data size). Buttons: Use Cube layout (default, only when
   Cube fits), Export as set / Export anyway, Cancel (the default when no Cube offer exists). "Use Cube layout" sets the Shard
   control, so the control, the folder name (`...F...` tag) and the export agree; nothing is switched silently. A drive that cannot
   be asked logs a WARNING and never blocks. The compressed estimate assumes lz4 = 55 % of raw (0.54 measured on this data): an
   expectation, not a bound, and the dialog says so. Checked against the hand-measured numbers: 565 / 425 GiB per image,
   171 / 98.5 GiB per cube for the 71,685-image case.
2. **The hint** is the same dialog (it is where the user is already looking); the default of the Shard control is unchanged.
3. **Per-stage timing**: `ShardWriteResult.mkdir_s` (new), `ExportStats` stage sums filled as results arrive (so a cancelled run
   has them), logged as `dataset.export_stages` (outcome completed / cancelled / failed, planes, planes_per_s, shard_mode,
   compression, `read_ms`, `encode_ms`, `folders_ms`, `write_ms` per image) and as a third line of the "Export took ..." box.
   Read a log with `python -m lspr_imaging_app.applog grep <file> --event dataset.export_stages`.

Tests: `tests/unit/test_lspri_export_disk_estimate.py` (11), `tests/integration/test_lspri_export_preflight.py` (13),
the Export tab tests in `tests/integration/test_lspri_rewrite_export_chunk_tools.py` (+6). Unseen by eye (the dialog text was printed).
Sizes in the dialog are decimal GB like the rest of the export statistics; Windows Explorer shows binary units under the label "GB"
(374 where the dialog says 401 GB free).

## Does the Cube layout cost anything? (measured, 4 real cubes, both layouts, C: NVMe, warm cache, the app's own readers)

Same data, 108 planes, 80 x 80 chunks. Output size is identical (248 MB raw / 138 MB lz4); files 117 vs 13.

| Read | per image | per cube |
|---|---|---|
| Whole plane, random order (Image panel), ms | 7.5 (raw) / 10.7 (lz4) | **5.3 / 8.3** |
| Whole cubes in order, ms per plane | 4.9 / 8.0 | 5.0 / 8.0 |
| One ROI, all wavelengths of each cube (the scan), ms per read | 0.49 | **0.40** |
| ... shard index + CRC time of that whole pass, ms | 105 | **0.5** |
| One ROI, one wavelength across cubes, ms per read | 0.39 | 0.39 |

The index cache holds 8 shards: with one file per image a cube has 27 shards, so every ROI pass re-reads indexes (105 ms per pass);
with one per cube the cube's single index stays cached. Cold reads on the USB drive were not measured (fewer files can only help).

Real costs, none of them large for this dataset:
- **Cancel is slower**: the export waits for the cubes being written (`executor.shutdown(wait=True)` in the `finally`); a cube is 27
  images, about 13 s of one worker here, against about 0.5 s for one image.
- **A damaged shard loses a cube (27 images)**, not one image. An export that is cancelled or fails is never swapped in.
- **Memory per worker** holds a whole cube's encoded chunks (~61 MB raw here); `lspr_core.perf` counts it (`shard_bytes`) when it
  sets the worker limit. It grows with the wavelengths per cube.
- **A dataset with fewer cubes than workers** keeps some workers idle (the pool is capped at the number of tasks).
- **Other zarr readers** work (standard v3 sharding), but a reader that cannot do partial reads of a shard would read a whole cube
  file (~61 MB) for one image. Not tested with other tools.
- Nothing edits an export in place (it is write-once), so rewriting a whole shard to change one image never comes up.
- Benefit beyond speed: far fewer files and folders to copy, back up and delete (deleting ~3,300 folders of the cancelled runs on
  exFAT is itself slow).

## Method (repeatable)

`py-spy`-free: `dataset.export_done` is logged only on completion, so for a cancelled run read the shard files' creation / last
write times and count (`c/*/*/0/0`). Benchmark = `write_shard` (`io/_zarr_export_worker.py`) in a `ProcessPoolExecutor(7)` with a
`ShardWriteSpec` per plane or per cube, scratch output only; throw-away script (not kept). `fsutil`-free disk-cost test: free space
before / after creating 50 folders and 50 files.
