# Play: fast grey picture (2026-10-21)

**Report:** Play still 11-15 fps (target 29.6) after the prep cache (`play_prep_cost_2026-10-21.md`).
Log `logs/lspri-20261021-212944-45368.jsonl`: `prep_ms` 53 -> 6, but `pic_ms` (`prepare_frame_display`) 9 -> 42 ms;
render thread still ~53 ms/frame.

## Cause
Not slower code (the function is unchanged since 10-14; ~9 ms standalone). It makes ~15 short native calls
(OpenCV/numpy); each hands the GIL back and waits to take it again, up to one switch interval (5 ms) while the GUI
thread runs Python (slider `paintEvent` 15 % of GUI samples, no-data overlay 5 %). Standalone with a GUI-like busy
thread at 40/60/90 % duty: 9 -> 14 / 21 / 43 ms; a 0.2 ms switch interval removes it (but 0.5 ms was switched off
on 10-14 after a Qt crash, see `gil_tuning.py`). My first standalone numbers (34-65 ms) came from a loaded machine.

## Change ("fast while moving, exact at rest", like the bilinear rotation)
* `prepare_frame_display(..., fast=True)`: grey in one saturating `cv2.addWeighted(dtype=8U)` pass that rounds
  (offset -0.5 to imitate truncation); 3 native calls instead of 7. Range, levels, auto_levels, alpha identical;
  grey at most 1 level off (`MAX_FAST_GREY_DIFFERENCE`), ~0.002 % of pixels. Default stays exact (pyqtgraph-identical).
* `RenderRequest.fast_rotation` (kept name) now also selects it; set only while Play runs.
* `panel._on_playback_changed`: Play stop always re-renders the stopped frame exactly (was: only with a rotation).

## Measured (real function, 800x1276, NaN borders, GUI-like thread at 90 % duty)
| | idle | busy GUI |
|---|---|---|
| exact | 11.7 ms | 45 ms |
| fast | 5.7 ms | 29 ms |
Auto levels: 12.3 -> 4.9 idle, 51 -> 29 busy. The in-app fps is **not confirmed** (GUI rule): please check a Play.

## Left (each needs a decision)
* 10 native calls remain in fast mode: `_automatic_levels` (4) feeds only the Display-range readout; skipping it
  during Play would save ~10 ms under load but blank the readout while playing.
* Cheaper GUI paint: `data_axis_slider.paintEvent`, `no_data_overlay_rgba`. Lower GUI load helps every stage.
* GIL switch interval 0.5 ms + a guard against the Qt crash (architecture question).

## Same day: status bar target, stats bug, 20x
* "of N fps" was set when Play started and ignored speed changes: `PlaybackStats.set_target` (time-weighted, called from
  `panel._on_slideshow_speed_for_stats`); log gets `target_fps`.
* Stats bug: the picture time (`pic_ms`) was not subtracted from `queue_ms`, so it showed up as queue. Now its own stage;
  logs from before 2026-10-21 have an inflated `queue_ms`.
* Speed 20x added (`SPEEDS`); `MIN_INTERVAL_S` 0.02 -> 0.01 so 20x of a ~0.34 s dataset (17 ms) is not silently clamped to 50 fps.
