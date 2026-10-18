# Plan: "Scan for events" and "Auto mask" in the Mask tab (2026-10-16)

Status: **plan, nothing built.** Follows `auto_transient_mask_exploration_2026-10-16.md` (method, measurements). The maintainer's answers to its six open decisions are in section 1. Engineering priority served: 1 (a false positive must never silently remove good pixels: the scan only points, the masking is a separate, visible, undoable act) and 2 (every pointer records the parameters that produced it).

## 1. Decisions (maintainer, 2026-10-16)

1. The threshold is defined by math (a difference between frames), the contour of the particle comes out of that difference, and the user adds an extra N px of dilation.
2. Global events (soft shadows, big changes) are only **marked**, never masked automatically. The function is **split in two**: *Scan* marks suspicious frames with a predefined filter and shows them as pointers that behave like marks on the Mask slider (own colour). *Auto mask* reads that list and creates the individual masks.
3. Apply is its own function (the Auto mask part), separate from the scan.
4. A compact internal mask format that holds only what changed; the already existing PNG export (named per frame) stays.
5. Merging with the persistent mask is the default in the Auto mask step.
6. The Auto mask step may be wired to undo.

## 2. The math (what "defined by math" means here)

For a checked frame `I` (cube c, wavelength w), reference `R` = the same wavelength in the previous cube (screening) or the brighter of c-1 and c+1 per pixel (confirmation; survives a particle in one reference).

- Ratio `r = I / R`, divided by its median over the valid area. `r` is the transmittance of whatever is new in this frame.
- Depth `d = 1 - r`. Noise `sigma` = 1.4826 x median absolute deviation of `d` over the valid area, **per frame** (0.34-0.71 % on 300 clean frames at 4x4 binning).
- **Detection**: a connected region whose deepest pixel is at least `k * sigma` deep and has at least `A_min` pixels. Measured with one previous-cube reference: the deepest pixel of a clean frame reached 9 sigma (median), 30 (99th percentile), 44 (maximum of 300); the real passages (hair, small particle, cube 160) are 102-204 sigma in every frame. So `k` around 60 separates them with room on both sides; that is a dip of roughly 28 % at a typical sigma, i.e. about the old fixed "25 % darker", but it now scales with each frame's own noise and is one number with a meaning.
- **Contour**: not a global threshold. Each detected object's contour is the level halfway down its own dip, `r < 1 - 0.5 * (1 - r_min)` (full width at half depth: where a blurred edge really is), computed on the full-resolution frame. The ratio of areas that results is measured in phase 0.
- **Dilation**: `N` px (user, default 3), applied by the Auto mask step, so changing it never needs a new scan.
- Classes: `particle` (compact, area <= a maximum, default 3000 px) and `global` (larger, e.g. the cube 229-231 shadow). A frame with an object of each class carries both. Optional link of events across consecutive frames in acquisition order (also across a cube boundary) gives a passage id and a speed (px per frame) for the tooltip; it is not needed for detection.

## 3. Architecture

| Piece | Where | Notes |
|---|---|---|
| Pure detection: valid area, ratio, sigma, detection, half-depth contour, components, linking | `image_tools/mask/transient_detection.py` | No Qt, no dataset. `progress` and `cancelled` callbacks per CLAUDE.md. |
| Event model and `TransientEventsModule` (the list, dismiss / restore, "which parameters made this") | `image_tools/mask/transient_events.py` | A QObject with typed signals. Its changes are **cosmetic**: nothing recomputes. |
| Scan task (`threading.Thread`, never `QThreadPool`; progress and Cancel through `TaskIndicator`) | `image_tools/mask_scan_task.py` | Copies the shape of `chromatic_auto_task.py` (`ChromaticAutoDetect`). Stage 1: every checked frame, 4x4 binned, previous-cube reference. Stage 2: the hits (and their neighbouring wavelengths) at full resolution with the confirmation reference, which gives the contour. |
| Auto mask task | `image_tools/mask_auto_task.py` | Reads the event list; for each frame of every accepted `particle` event: contour, dilate by N, merge onto the base in effect (`resolve_mask_edit_base`: persistent mask warped into that frame), then one batch commit. |
| Batch commit on `MaskModule` | `image_tools/mask/module.py` | `set_mask_changes(batch, label)`: one `mask_changed` emission for the whole batch (a signal per frame would start 300 recompute plans) and one undo entry guarded by the existing version token. |
| Compact in-memory masks | `image_tools/mask/model.py` | `MaskChange.mask` keeps zlib-compressed packed bits and decodes on access (a 1.2 MB plane becomes about 5-20 KB, so 300 masks go from ~350 MB to a few MB). The session's PNG-per-mask files are unchanged: **no migration**. |
| Pointers on the rows | `panels/image/timeline_strip.py`, `mask_rows.py` | Marks of a second colour (suggestion: cyan, orange is the masks) as a short tick in the upper half of the row so they never hide a mask mark; Cube row = cubes with events, wavelength row = frames of the shown cube. Right-click: Go to, Dismiss (masking then skips it), Restore. |
| Ribbon | `panels/image/mask_tab.py` + new `mask_scan_panels.py` | Two captioned groups after "Manual edit": **Scan** and **Auto mask** (section 4). |
| Session | `storage/session.py` | New optional key `transient_events` (small JSON, contours as packed bits), schema 1.3 -> 1.4 (additive: an older reader ignores it). |
| Read without cache | `dataset/module.py` | `load_image_array` is an `lru_cache(160)` of float32 planes (~750 MB). A scan of 8000 frames through it would push out the frames the user is looking at, so the scan needs an uncached reader (uint16 is enough). |

## 4. Screen (new groups in the Mask ribbon, one row, same captioned style)

```
... | [tool picker][its controls] |  [scan] [k] [stride] [range]  |  [apply] [N px] [particles v] [global -]  | [PNG]
         Manual edit                       Scan                          Auto mask                              PNG
```

- Purpose: Scan finds suspicious frames and only marks them. Auto mask turns accepted events into masks.
- Scan: a play button (runs through `TaskIndicator`, Cancel there), sensitivity `k` (sigma), stride (every Nth wavelength, default 3), range (whole dataset, or cubes a-b), a clear-events button. Result: pointers on the rows.
- Auto mask: apply button, dilation N px, the classes to mask (particles on, global off and greyed: pointers only).
- States: scan running (controls greyed), no events (Auto mask greyed with the reason in its tooltip), events from an older dataset or other parameters (shown, with the parameters in the tooltip).
- Errors: a frame that cannot be read is skipped and counted in the result message, never silent.
- Remembered: every control (the `ui_state.bind` rule).

## 5. Cost (measured, TIFF dataset, 8164 frames)

Compute at 4x4 binning is 3.4 ms per frame; decoding is the floor (TIFF 18 ms/frame, 150 px OME-Zarr 13 ms). Whole dataset about 3 min, stride 3 about 1 min for stage 1, plus stage 2 for ~700-800 hit frames (3 reads each, about 40 s single-threaded). Stride 2-3 caught 35 of 35 passages, stride 4-5 34-35 of 35.

## 6. Phases (each one is testable and shippable on its own)

0. **Detection + CLI, no UI, no app change.** `transient_detection.py`, unit tests on synthetic frames (blob over a disk array, a ghost disk, a smooth shadow, a particle in the reference, an exit at the image edge), `tools/scan_transients.py` that prints the event list for any dataset. Acceptance: hair c61 620-680, small particle c128 490-550, c160 630-680, no clean-frame hit in the 300-frame sample; the half-depth contour compared with the fixed 0.75 contour.
1. **Pointers.** Events module, scan task, session key, marks on both Mask rows, Scan group, stepping through events.
2. **Compact masks + batch + undo.** Compressed `MaskChange`, `set_mask_changes`, undo, then the Auto mask group and task.
3. **Speed and polish.** Stride / threads, "Export all masks" if the session's PNG files are not enough (the session already writes one named PNG per mask; I found no separate "export all" command).

## 7. Needs a yes before building (CLAUDE.md "check in first")

- New files under `image_tools/mask/` and two task files next to `chromatic_auto_task.py` (module boundaries).
- `MaskModule`: batch commit + one more `mask_changed` reason; `MaskChange` internals compressed.
- `DatasetModule`: an uncached plane read.
- Session schema 1.3 -> 1.4 (additive).
- Wiring the Auto mask batch to undo (answered yes in decision 6; recorded here).

## 8. Open and decided on silence

- Pointer colour cyan and the upper-half tick (easy to change).
- Pointers get their own stepping mode ("Events") next to "Mask states", because they are not mask changes.
- An individual mask made by Auto mask is a normal individual mask: a later edit of the persistent mask does **not** propagate into those frames (existing behaviour of individual masks, visible on the rows). A layered "overlay" mask would propagate but changes every consumer of `resolve_mask_source`; not proposed now.

## 9. Phase 0 result (built 2026-10-16, uncommitted)

Built: `image_tools/mask/transient_detection.py` (one frame: valid area, ratio, per-frame sigma, screening, half-depth contour, linking), `image_tools/mask/transient_scan.py` (the three stages, progress, Cancel, unreadable frames listed), `tools/scan_transients.py` (the command line), `DatasetModule.load_plane_uncached` / `io.load_image_array_uncached`, `tests/unit/test_lspri_transient_detection.py` (28 tests on synthetic frames, including the scan on a small dataset; the brightness and the brighter-neighbour tests were checked to fail when the maths is broken). Nothing in the app or the session format is touched yet.

Acceptance on the real dataset (8164 frames, TIFF):

- Hair, cube 61: found at 620-680 nm, 7 frames, 190-232 px per frame. Small particle, cube 128: 490-550 nm, 7 frames. Cube 160: 630-680 nm, 6 frames. Contour area of the hair 109-669 px at half depth (the fixed 0.75 contour of the exploration gave up to 884 px; both are before the user's dilation).
- Stride 1: 102.6 s, 579 frames with events, 846 events: 455 particle, 374 artefact, 17 global. Stride 3: **56.8 s** (screen 28 s, expand 8.7 s, confirm 20 s), 565 frames, 832 events. Stride 3 misses 14 event frames (5 of 380 particle frames) and one whole passage: a faint particle in cube 267 that reaches 60 sigma in only 3 of its 5 frames (an idea for later: fill the gap along the predicted track with a lower sigma factor).
- The artefact rule works: 75 events in the corners and edges of cube 0, and the only other artefacts are at the top edge (y ~ 0, x 1000-1160) of cubes 175-185. No real passage was labelled an artefact.
- Dense streams (cubes 175-200) come out as clean 6-8 frame passages with steady steps once frames are paired one to one.
- **Known weakness**: the soft shadow of cubes 229-231 (17 `global` events, mostly larger than 3000 px) also leaves ~54 small objects labelled `particle`. Their frames are noisier (frame sigma median 0.78 %, up to 1.4 %, against 0.46-0.55 % elsewhere) and show no steady motion. Every event carries `frame_sigma`, so a filter can drop or flag them; the threshold is not decided (the two ranges overlap).
- Reading: 12.5 ms per frame wall with the native TIFF decoder (`use_fast_tiff_decoder`) on 4 threads, against 20 ms with tifffile.

## 10. Phase 1 result (built 2026-10-17, uncommitted)

Maintainer's answers: the shadow cubes 229-231 are left to review (no frame-noise filter); gap filling is implemented and tested.

**Detection, added to phase 0**
- *Gap filling* (`transient_gaps.py`): where a passage has a gap, or could go on past its end, the frame is searched again at 20 sigma, only within ~40 px + 25 % of the step of the predicted spot and only for an object of a similar size. Found events carry `inferred=True`. Bridges up to 2 missing frames between two pieces that move like one particle (speed 60-450 px per frame, direction within ~45 degrees, similar size), then follows a passage up to 2 frames beyond each end while the predicted spot is inside the image.
- *Weak hits*: a frame with a region at 30 sigma (but no 60 sigma object) is not an event, but its neighbouring frames are looked at too. With this and gap filling **stride 3 and stride 1 give the same 593 event frames** on the test dataset (before: stride 3 lost 14), the cube 267 faint passage is found whole (frames at 54 and 58 sigma filled in), 25 events are `inferred`, and a stride-3 scan of the 8164 frames takes 56.7 s (screen 24, expand 12, confirm 18, fill 1.7).

**Pointers in the app** (nothing is masked; the scan is cosmetic):
- `image_tools/mask/transient_events.py` `TransientEventsModule` (list, queries, dismiss / restore, dismissals survive a new scan at the same place, a cube-range scan replaces only its range) and `transient_events_state.py` (the saved shape; only particles keep their contour, as packed bits).
- `image_tools/mask_scan_task.py` `TransientScanTask` (plain thread, progress and Cancel through the task indicator, copied from `ChromaticAutoDetect`), wired in `app_rewrite.py` (task indicator, cancel, session capture / apply / autosave).
- Session schema **1.4**: optional `transient_events` block; a 1.3 file opens with no events.
- Mask rows: pink (`#f472b6`, not one of the colours taken) ticks in the **upper half** of both rows (`TimelineStrip.set_event_marks`, separate from the mask marks so they never change "what holds here"), tooltips with the passage and speed, right-click in the upper half: Go to / Dismiss / Restore (the lower half keeps the mask menu). Dismissed pointers are drawn faintly.
- Slideshow: fifth "Show" choice **Events** (cube + warning icon): Previous / Next / Play hop between cubes with a pointer that is not dismissed.
- Mask tab: new **Scan** group between Manual edit and PNG (`mask_scan_panels.py`): scan (search icon), sigma (default 60), 1/n stride (default 3), link toggle (gap filling, on), trash (clear). All remembered (`image/mask_scan/*`).
- Tests: `test_lspri_transient_detection.py` (40), `test_lspri_transient_events.py` (14), `test_lspri_transient_scan_task.py` (7), `test_lspri_rewrite_mask_scan_ui.py` (14); the existing image-panel (112), mask rows (35), ROI strip (38), slider ribbon (44), session (19) and slideshow (44) files still pass. `test_lspri_rewrite_ui_state.py` has the two known failures from before (reference frame; see the known-failing note).

**Decided on silence (easy to change):** pink for the pointers; the link icon for gap filling; "1/n" as the stride display; whole-dataset scans only in the UI (the cube range exists in `ScanParams` and in the CLI, not yet as a control); the Auto mask group (phase 2) is not built, so a pointer cannot be turned into a mask yet. *(Superseded by section 11: the cube range and Auto mask were built the next day.)*

## 11. Phases 2 and 3 result (built 2026-10-17, uncommitted)

**Phase 2: pointers become masks.**
- *Compressed masks* (`MaskChange`, `image_tools/mask/model.py`): the pixels are kept as zlib-compressed packed bits and decoded on access (`.mask` is the same full boolean array as before, **read-only**, shared through a 32-entry cache). A 900 x 1300 mask is 1.2 MB as an array and ~0.3-3 KB stored (a big hand-drawn region ~20 KB), so 300 masks are a few hundred KB instead of ~350 MB. The session's PNG-per-mask files are unchanged: **no migration**. `set_mask_change` now compares the compressed form (also notices a different authoring frame).
- *Batch commit* (`MaskModule.set_mask_changes`): many masks as ONE undoable step with ONE `mask_changed` (`reason="mask_batch"`, `frames=` the list; `frame` and `scope` stay `None` for a subscriber that does not know `frames`). Same version-token guard as the Mask rows' commands (its undo is skipped, with a message, if the mask changed after it). A mask may be passed as a ready `MaskChange`.
- *Which events are masked* (`transient_masks.py`): particles, not dismissed, with a contour (`global` and `artefact` never). Per frame: the contour of each event grown by N px **on its small box** (`ContourMask.grown`, clipped at the border), OR-ed onto the mask in effect there (`resolve_mask_edit_base`: the persistent mask warped into the frame, or the frame's own individual mask), stored as an individual mask.
- *Task* (`image_tools/mask_auto_task.py`): progress and Cancel in the status bar, nothing is written until the end (a cancelled or failed run changes nothing). **Superseded 2026-10-17 (audit B1):** it first ran on the GUI thread in ~30 ms slices because "each frame needs `MaskModule` / `ChromaticModule` reads"; only the *lookup* does (`MaskModule.resolve_mask_change`, a dict lookup, and `ChromaticModule.affine_between`, a 2x3 matrix, ~40 microseconds a frame together, done in one quick loop in `start()`), the pixel work (decode, warp 3.2 ms, OR the contour 0.6 ms, compress 0.24 ms = 3.9 ms a frame) is pure and now runs on a plain `threading.Thread`. The commit (`set_mask_changes`) is on the GUI thread and refuses to write if the mask timeline changed while the run was going (audit A2) or the dataset was loaded / cleared (A1).
- *UI*: Mask tab group **Auto mask** `[wand][3 px]` (remembered; the tooltip says how many pointers / frames it would mask or why it cannot); the right-click menu of a pointer has "Mask the particle events of this cube / frame". Ctrl+Z undoes the whole batch.

**Phase 3.**
- *Export all masks* (`mask/export.py`, `mask_export_task.py`, third button of the PNG group): one PNG per mask named per frame (`mask_cube061_wl620_individual.png`, `..._persistent.png`) plus `masks_index.csv` (file, cube, wavelength, scope, masked pixels, what it applies to), in a folder you choose, on a worker thread with progress and Cancel; a folder that already holds files of those names asks before replacing them.
- *Scan range*: "Only the cubes in view" (scan the stretch the Cube slider is zoomed to; a scan of a stretch replaces only the pointers inside it).
- *Settings popup*: the scan's settings (sensitivity, 1/n, follow passages, only the cubes in view) moved into a popup of a settings button, because the Mask page of the ribbon would otherwise be 814 px wide. Now **697 px** (the next widest page is 633, the Mask page was ~490 before this work); the settings button's tooltip always shows what a scan would use.
- *Speed*: screening now runs in a second pool of plain threads (it was 3.4 ms a frame on the scan's own thread, as much as the reading). Full dataset, stride 3: **48 s with 4 workers, 42 s with 6, 39 s with 8** (was 57 s); the event list is identical for every worker count. The default is all cores but two, at most 6.

**Tests added (phases 2-3):** `test_lspri_mask_batch.py` (13), `test_lspri_transient_masks.py` (10), `test_lspri_mask_export.py` (7), `test_lspri_mask_auto_task.py` (11), `test_lspri_mask_export.py` UI (6), and the Auto mask / range / settings cases in `test_lspri_rewrite_mask_scan_ui.py` (now 27 tests).

**Not done, and why:** re-running Auto mask with a *smaller* px cannot shrink masks already made (it ORs onto what is there): undo the batch and run it again. An individual mask made by Auto mask does not follow a later edit of the persistent mask (existing behaviour of individual masks; the "overlay" layer that would is not proposed). The expansion stage (~11 s of the scan) reads two frames per neighbour; a read cache would help only in dense streams.

## 12. A particle that arrives and rests is a particle (2026-10-18)

Maintainer: a particle can arrive in the frame and stay at the same spot for a whole cube's time; it must be labelled and masked like any other.

**What was wrong.** `link_passages` re-labelled "stayed at one place for 3 or more frames" as `artefact`, and Auto mask never masks artefacts. A resting particle is exactly that, so it was shown but skipped. On the 8164-frame dataset that rule had called 374 events artefacts: 356 are on the image border (corners and edges of cube 0, the top edge of cubes 175-185: real artefacts) and **18 are not** (image 900 x 1300): a 60 px object resting in cube 0 at 550-620 nm (92-124 sigma, 5 frames found plus 3 more by gap filling) and 13 stationary pieces in the soft shadow of cubes 229-230.

**The rule now** (`transient_detection.link_passages`): a run of 3 or more consecutive frames at one place (moved less than `stationary_px` = 20 px, similar size) is
- an `artefact` if any member is within `border_px` (12 px) of the image border (the whole run), as before, or
- a **particle at rest**: it stays `KIND_PARTICLE` (so Auto mask masks every one of its frames, contour grown by N px like any other), gets a `passage_id`, and `step_px` = the largest shift between two frames (a few px). `is_resting(event)` tells it from a flowing one (flowing links are at least `min_step_px` = 60 px). No new stored field: the session format is unchanged, and an older list opens as it was.
- A lone object on the border that is not part of a moving passage is an artefact, as before. Without the image size nothing is called an artefact because of the border.
- Gap filling already follows a passage one frame past each end at the predicted spot; for a resting one that spot is where it sits, so its faint first / last frames are found (cube 0: 3 more frames).
- Tooltips: "particle at rest 550-620 nm (8 frames)" on the Cube row, "at rest (passage N)" on the wavelength row; the command line prints "AT REST at (x, y)".

**Real dataset, before / after:** 871 -> 876 events; artefacts 374 -> 356 (all on the border now); particles 480 -> 503 (+18 relabelled, +5 found by gap filling). The 13 pieces in the soft shadow of cubes 229-230 are now particles at rest like the other ~54 shadow objects before them: **dismiss them before running Auto mask, or they get masked** (some are 1500-2800 px).

**Known limit (pinned by `test_known_limit_...`).** Every frame is compared with the same wavelength in the *previous* cube. A particle that arrives and rests is "new" only until that previous-cube frame contains it too, so it is found for **at most one cube's worth of frames after it arrived** (across the cube boundary: a particle arriving at 510 nm of cube 3 and resting until 500 nm of cube 4 is found in all 7 frames as one passage; a particle resting for several cubes is found in the first only). The way round is a reference from *before* it arrived (the same wavelength in the last cube without it) for as long as the object is still at its spot; not built, ask if the particles rest longer than a cube.

**Tests.** `test_lspri_transient_detection.py` (linking cases, gap targets at rest, scan of a resting particle, across a cube boundary, the known limit), `test_lspri_rewrite_mask_scan_ui.py` (labels and Auto mask on a resting particle).
