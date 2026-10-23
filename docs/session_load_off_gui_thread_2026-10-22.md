# Session load off the GUI thread, and a faster session carry (2026-10-22)

Maintainer request: "`load_session` still decodes about 400 mask PNGs on the GUI thread" (the window froze about 4 s every time a mask-heavy
dataset opened), and the session carry warped its masks one at a time. Maintainer's choice for the load: **open blank, fill in when ready,
with a status-bar entry that says what is happening** (not "lock editing").

## What changed

| Piece | Where | What |
|---|---|---|
| `ThreadedTask` | `threaded_task.py` | The thread / signals / Cancel / stale-result wrapper that the scan, Auto mask, mask export and session load share (written 2026-10-22 when the first three were de-duplicated). A task supplies the work and `_on_result`; `_after_run` is the hook for "start the next run" |
| `SessionLoadTask` | `storage/session_load_task.py` | Reads a session on one plain `threading.Thread`; signals `completed(LoadedSession)` and `ended(root, outcome)` |
| `load_session(root, progress, cancelled)` | `storage/session.py` | Reports "Reading the session file", "Loading masks: n of N", "Reading ROIs, chromatic correction and settings"; raises `Cancelled` when asked. The mask PNGs are still decoded on the small thread pool, now submitted so that a cancel stops the masks not yet started |
| `restore(root)` | `app_rewrite._wire_session_coordinator` | Modules are reset to blank at once; **autosave stays off for the new root** until the load lands; `loaded` applies the session and switches autosave on and seeds the writer; `ended` (failed / cancelled) leaves autosave off |
| `carry_masks` | `storage/session_carry.py` | Masks warped on a small thread pool, results taken in the file's order (same output at any pool size; checked on 400 full-size masks) |

## Rules the design keeps

- **A blank state is never written over a saved session.** Autosave is off from the moment the session changes until the load has been
  applied. A failed or cancelled load keeps it off for that session; the status bar says so. Before, a failed read was only a log line
  and the modules were left as the previous session had them.
- **A session left mid-load is not applied.** `restore` calls `discard()` first (stops the run, drops its result); a new `load` while one is
  running is queued and starts when the stopped one has ended; `loaded` also checks the root against the one it waits for.
- **A session that has no file yet** (fresh dataset, new session) takes the old synchronous path: nothing to read, so nothing to wait for.
- Edits made in the few seconds of a load are replaced by the loaded session (the blank state is a placeholder, not a session). Locking the
  panels was offered and not chosen.

## Measured

Opening a dataset whose session holds 411 masks of 1216 x 1936 (synthetic, written with `save_session`): the GUI thread was blocked
**5.7 s** by `load_session` before; `load_dataset` now returns in **5 ms** and the session is in place about 3 s later with the window
responsive (the second read had the files in the OS cache; the first one off a cold disk takes longer, which is the point of moving it).

Session carry, 400 masks of 1216 x 1936 with a 1.42 degree rotation and a crop: **5.5 s one at a time, 3.0 s on 8 threads**, identical
output (arrays equal, same order, progress monotone). Not 8x because part of each mask's work (the bool/uint8 conversions) still holds the GIL.

## Open

- Image loading (the dataset itself) is a separate, older path and was not touched.
- `SessionWriter._encode_mask_changes` still writes the PNGs of new masks one at a time (it runs on the autosave thread, so it costs time,
  not a freeze). Parallelising it needs the file version of each (frame, scope) group to be chosen in order first.
- `SessionCarry` itself was not moved onto `ThreadedTask` (it rolls back a half-written session and emits `carried`, not `completed`).
