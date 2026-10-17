# Session autosave freezes the GUI with hundreds of masks (analysis, 2026-10-17)

Status: **fixed 2026-10-17 (A + B + C, maintainer's decision: "no reason for saving to be in the GUI thread").** The analysis below
is kept as the evidence; the result is in "What was done" at the end.

## Symptom

After *Auto mask* made 405 masks (testing log 2026-10-17, 13:34), the window froze for about 3 s at a time, repeatedly, with no
work visibly running.

## Evidence from the log

Both freezes end exactly at a `Session autosaved` line, and the user's queued clicks are processed in the same instant:

| Trigger | Debounce fires | `Session autosaved` | Frozen | Queued input handled right after |
|---|---|---|---|---|
| Auto mask finished 13:34:05.87 | ~13:34:08.4 | 13:34:11.501 | ~3.1 s | `set_cube` at 11.515 and 11.634 |
| `set_cube` at 11.515 / 11.634 (no mask touched) | ~13:34:14.1 | 13:34:17.816 | ~3.7 s | `set_cube` at 17.820 and 17.821 |

The second row is the important one: **scrolling cubes alone caused a full 3.7 s freeze.** Also at 17.819 the GC summary covers
5.9 s instead of 5 s (its timer was held up). The save before any masks existed (13:33:54.534) shows no such gap.

## Cause

1. `SessionAutosave._save_now` runs `save_session` **on the GUI thread** (`storage/session_autosave.py`; its docstring says this
   was deliberate "until measured"; this is the measurement).
2. `selection.cube_changed`, `wavelength_changed` and `roi_selection_changed` all call `autosave.schedule`
   (`app_rewrite.py`, `_build_session_autosave`), because the selected frame is part of the session. So every pause in
   navigation triggers a full save, 2.5 s later.
3. A full save re-processes **every** mask change (`session.py` `_encode_mask_changes`), even when none changed: decode the
   compressed mask (the 32-entry decode cache is smaller than 405, so it thrashes), convert to 8-bit, scan the masks folder
   for that frame's PNGs (`glob`), read the existing PNG back from disk, compare, and only then skip the write
   (`analysis/provenance.py` `persist_mask_snapshot` -> `resolve_mask_snapshot_ref`).

## Measurement (scratchpad `bench_session_save.py`, real `save_session`, synthetic blob masks 769 x 1239)

| Masks | First save (writes all PNGs) | Re-save, nothing changed |
|---|---|---|
| 100 | 0.5 s | 0.6 - 1.1 s |
| 200 | 1.1 s | 1.0 - 1.7 s |
| 405 | 2.1 s | 2.2 - 3.7 s |

About 5.4 ms per mask, linear in the number of masks. Of the 405-mask re-save: reading the PNGs back ~1.1 - 1.6 s, decoding
+ 8-bit conversion ~0.5 s, directory scans ~0.4 s. Real masks (merged with the mask in effect) may be larger than the
synthetic ones; the log's 3.1 - 3.7 s is consistent with this.

## Fix options (not yet applied)

- **A. Skip masks that were already saved** (small; on-disk format unchanged): remember which `MaskChange` (immutable,
  hashable) was written to which file, check the file still exists with ONE directory listing, and re-encode only new or
  changed masks. A scroll-triggered save drops from seconds to ~10 ms. Does not help the one-time write of 405 new PNGs (~2 s).
- **B. Write off the GUI thread** (larger): capture state on the GUI thread (cheap, masks are immutable), write on one
  background `threading.Thread` ("latest request wins"), `flush()` on quit / dataset switch waits for it. Removes the
  one-time freeze too. Needs care for two saves overlapping and for the quit path.
- **C. Log it:** add `timed(logger, "session.autosave", masks=..., written=...)` so the next log shows the duration
  directly (autosave logs no time today).
- Recommendation: A + C first; B if the one-time ~2 s write after Auto mask still bothers.

## What was done (all three, plus one extra)

- **B, the write is off the GUI thread.** `SessionAutosave._save_now` (GUI thread) reads the modules and calls
  `prepare_session` (`storage/session.py`), which builds a plain, **copied** dict. It has to be the GUI thread: `rois()`,
  `groups()`, `models()` and `landmarks()` return the *live* mutable objects (the `capture_session` docstring claiming every
  query returns a copy was not true for those), so handing them to a thread would let it read a half-edited ROI. The file
  work (`SessionWriter.write`) runs on one plain `threading.Thread` ("session-autosave", a daemon; never a `QThreadPool`).
  Requests are kept per root, newest wins; the dataset-switch flush still lands in the old folder. A failed write returns to
  the GUI thread through a queued signal (`save_failed`, dirty flag kept) exactly as before.
- **A, nothing is re-verified.** A `SessionWriter` remembers which `MaskChange` it put on disk as which file version; a
  repeat save costs one directory listing plus the JSON. A deleted PNG is noticed (not in the listing) and written again; a
  changed mask gets a new version; the files are byte-identical to what `save_session` made before. A freshly started
  writer still verifies every PNG once (in the background).
- **C, logged.** `session.capture` (GUI thread, `masks=`) and `session.write` (worker; `masks`, `reused`, `verified`,
  `written`, `json_kb`) at DEBUG; the old "Session autosaved" line is gone.
- **Extra: `_frame_naming` is cached per loaded dataset** (`app_rewrite._cached_frame_naming`). It walked all 314 cubes on
  the GUI thread at every save (16-18 ms).
- **Semantics that changed:** `flush()` still returns only when the data is on disk (quit, tests). `set_root()` no longer
  waits; code that is about to *read* a session back calls `wait_until_written(root)` first
  (`_wire_session_coordinator.restore`; without it, re-opening the session just edited restored the previous save: pinned by
  `test_reopening_the_session_just_edited_waits_for_its_background_write`, checked to fail without the wait).

Measured afterwards (scratchpad `bench_autosave_e2e.py`, 405 masks + 300 ROIs + 871 scan events, real autosave and writer):

| Save | GUI thread held (before: 2.2 - 3.7 s) | On disk after | Longest gap in a GUI loop meanwhile |
|---|---|---|---|
| first save after Auto mask (405 new PNGs) | 8 ms | 2.7 s | 9 ms |
| cube scroll / selection change | 6 - 11 ms | 20 - 36 ms | 7 - 16 ms |
| first save of a new app run (files already there, verified once) | 8 ms | 3.9 s | 32 ms |

The 6 ms is `prepare_session`; the rest of the GUI-thread time is the capture itself. Left as is: the one-time write of
hundreds of new PNGs still takes ~3 s, now in the background; a restart verifies the PNGs once, also in the background
(seeding the writer from the loaded session would remove that, not needed now).
