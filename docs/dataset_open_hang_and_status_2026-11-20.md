# Opening a dataset: the hour-long hang, and the status-bar steps (2026-11-20)

## What happened

The maintainer chose a dataset folder (`...LED_V2`, 71,685 TIFFs in `images/`) in File -> Open and nothing happened. No dialog,
no error, no log line; a later Open said "already loading".

## Diagnosis (how, so it can be repeated)

- The loader on the parent folder worked: 3 s (`dataset.io.load_dataset`).
- The session log had the call (`DatasetModule.load_dataset_from_folder dur_ms=0.9`, which only starts the thread) but no
  `dataset.scan` record. `timed()` writes its record even when the code raises, so the scan had **not finished**.
- `py-spy dump --pid <app> --nonblocking --locals` (never `--native`: it freezes the live app) showed the load thread in
  `io/legacy_metadata._find_case_insensitive`, at `_ == 0`: the first level, a folder that does **not** hold
  `measureing_times.csv` directly, i.e. the `images` folder itself.
- That function ran `entry.is_file()` on every entry **before** comparing the name. `is_file()` asks the disk. 26 ms per call
  measured on this drive; 71,685 entries x 2 names = about an hour. Given the parent folder, `direct.exists()` hits at once
  (0.08 s), so the slow path is only reached when the user picks the image folder.

## What changed

- `io/legacy_metadata._find_case_insensitive`: compare the name first, `is_file()` only on a match (same results). Picking the
  `images` folder now loads in 3.1 s on the real dataset. Regression test:
  `tests/unit/test_lspri_legacy_metadata.py` (counts `is_file()` calls on a folder of unrelated files).
- **Status row.** The load is now an "Open dataset" task in the status bar's `TaskIndicator`
  (`DatasetModule.task_progress` / `task_finished`, `LOAD_TASK_ID`), with Cancel (`DatasetModule.cancel_load`, wired in
  `app_rewrite.py`'s `cancel_by_task`). `io.load_dataset(folder, *, progress, cancelled)` reports each step through
  `progress.StageProgress` (stage shares 1.7 : 1.3 from the measured warm-disk times): looking for images / found N images /
  looking for saved metadata / looking for a measurement file / searching for metaData.txt and measureing_times.csv /
  reading measureing_times.csv (size) / looking for the pump-plan file / metadata read. `load_acquisition_metadata(folder, *, step)`
  carries the sub-steps. A failed load ends the task as FAILED (and still raises the dialog); a cancelled one ends as cancelled
  and raises no dialog.
- Tests: `tests/integration/test_lspri_dataset_load_status.py`.

## Things to know

- Cancel acts at the next step: one directory listing cannot be interrupted, so on a very slow drive Cancel waits for the listing
  that is running. The worker is a daemon thread, so closing the app always ends it.
- Reports are not `throttled`: about ten per load, and the last one before a slow step is the one that names it.
- During a launch that reopens the last dataset the row shows "Starting up" first and "+1 more" for this task.
- Picking the `images` folder works on purpose: the app searches up to 3 folders above a TIFF stack for the sidecar files
  (`metaData.txt`, `measureing_times.csv`), so either folder opens the same dataset.
