# Play at 4-5 fps: Qt pixmap cache too small (2026-11-11)

**Report:** slideshow (Play) ran at 4-6 fps; it used to reach ~25.

**Log** (`logs/lspri-20261110-235458-34260.jsonl`, 149 `playback.window` records, 160 ROIs, speed 10):
render thread is fast (`thread_ms` 1, `pic_ms` 13), but the GUI thread is 83 % busy, `cycle_ms` 229, `deliver_ms` 200.
Only ~50 ms per frame is in the timed stages (`d_paint_view_ms` 43, `gui_ms` 5).

**py-spy** (30 s of Play, attached `--nonblocking` to the running app), GUI thread:
- 25.8 % `pyqtgraph AxisItem.paint` line 999 = `self.picture.play(p)` (replaying the axis picture, grid included)
- 20.0 % Image view paint (`timed_canvas.py`; sample circles ~10 %, ROI + group labels ~4 %)
- 33 % native Qt with no Python frame (backing-store flush, pixmap blits, widget paints)
- Plan bar and step table paints show up every frame too, although they are cached

**Cause:** the Sensorgram axes, plan bar and step table use `DeviceCoordinateCache` (2026-11-02, tested offscreen at a small
size). Those pictures live in Qt's one shared `QPixmapCache`, default 10 MB. With `showGrid` an axis' bounding rect covers the
whole plot, so each picture is plot-sized: ~9 MB at 2400 x 1000 px. On the 2560 x 1440 screen they evict each other at every
cursor move and are all repainted.

**Measured** (`SensorgramPlot` offscreen, 30 cursor moves; script was `cache_test.py` in the session scratchpad):

| Plot size | Cache | Axis paints / move | ms / move |
|---|---|---|---|
| 1400 x 600 | 10 MB | 0 | 4.0 |
| 2400 x 1000 | 10 MB | 4 | 91.5 |
| 2400 x 1000 | 200 MB | 0 | 10.4 |

**Fix:** `app_rewrite.main` raises the limit to 256 MB (`_PIXMAP_CACHE_KB`). It is a ceiling, not an allocation.

**Result:** 4.4 -> 12.4 fps (`cycle_ms` 229 -> 80; image view paint 43 -> 20 ms).

## Step 2: the ROI overlay cache never hit in Play through cubes

Second profile (py-spy `--native`, which pauses the app at each sample, so it can't be combined with `--nonblocking`):
~25 % of GUI time painted the 160 circles + labels at every frame; `c_ov_hits=0`, and `c_ov_misses` (cumulative) rose only
once per Play start. Cause: at one wavelength the overlay key never changes, so `_draw_roi_overlay` returned early and
`OverlayLayers.begin` ran only once, before the epoch had held for a frame (`admit=False`). The picture was never kept.
Fix: `OverlayLayers.repeat(epoch, state)`, called on that early return, admits the picture from the second frame on
(tests in `test_lspri_rewrite_overlay_layer_cache.py`). The cached picture has the accepted group-label difference
described in `overlay_cache.py`.

Also seen: about 40 % of GUI samples sit in the graphics driver (OpenGL). The Image view's `QOpenGLWidget` makes Qt compose the
**whole window** through OpenGL, so any repaint (Sensorgram cursor, sliders) goes through it. Turning off Preferences ->
"Draw the image with the graphics card" is the A/B test; not changed in code.

Result: 12.4 -> 14-15 fps; Image view paint 20 -> 4 ms. GPU A/B (maintainer): on ~12.5 fps, off ~10.5 fps: keep on.

## Step 3: the title-bar fields re-laid out the main window at every frame

`paint_census.py` (installed only during Play, logs `playback.paints` per window) showed 5 layout requests per frame,
from the Image dock's title-bar fields up to `QMainWindow`, and `QMainWindow` repainted 2 x 2.5 Mpx per frame.
`QLabel.setText` calls `updateGeometry`; Qt skips the layout request only when width AND height are fixed
(`QWidgetPrivate::updateGeometry_helper`). The cube / wavelength / time fields had only a fixed width. Fix: a fixed height
too, centred in the row (`dock_container._apply_subtitle_columns`). Offscreen: 4.7 -> 0 layout requests per frame; test
`test_a_new_cube_or_time_text_lays_nothing_out_again`. The comment field (Ignored width) still re-lays out, but only when
the comment changes.

## Step 4: opaque OpenGL viewport

`gpu_view.apply_gpu_drawing` sets `WA_OpaquePaintEvent` on the `QOpenGLWidget`, because Qt's plain viewport is opaque and the GL one was
not. ImagePanel and the view stopped repainting behind it (1.33 Mpx -> 0.22 Mpx). Clean run (no other load): **15.8 fps** steady,
`cycle_ms` 63.5, GUI CPU 48 ms per frame, busy 75 %.

**Census caveat:** until this step the census summed bounding boxes. A title-bar strip plus a slider strip repainted together
counted as the whole dock, so the "dock / main window 1.4-1.6 Mpx" readings were inflated. It now sums the region's own rectangles.

## Step 5: autonomous bench, repaint passes (maintainer away; ran the app themselves)

**Bench** (scratchpad `play_bench.py`, not shipped): the real main window with the maintainer's saved layout, on a COPY of the
dataset (raw `0/` linked by junction, sessions/analysis copied), settings never written, `if __name__ == "__main__"` guard (without it
the Sensorgram's helper processes re-ran the script and opened many windows). 25-40 s of Play after 60 s startup; prints
`playback.window` medians and the paint census. Bench read 14.4 fps where the maintainer saw 15.8.

The census now also groups paints by **pass** (one `UpdateRequest` to the window = one hand-over to the screen). With the OpenGL
view, every pass composes the whole window on the GPU, so passes per frame matter more than pixels.

| Change | fps | GUI CPU ms/frame | passes/frame |
|---|---|---|---|
| bench start | 15.3 | 32-56 | 3 |
| OpenGL swap interval 0 (`opengl_surface.py`, before QApplication) | 24.1 | 38.8 | 3 |
| hold a frame arriving <= 20 ms before the next step, show it with the step (`SlideshowController.hold_frame_s`, `ImagePanel._hold_for_next_step`) | 25.3 | 35.5 | 2 |
| Sensorgram cursor follows the frame on screen during Play, moved first (`play_frame_showing` -> `SensorgramPanel.show_play_frame`) | 25.9 | 31.8 | 2 |
| scale bar: no update() when its layout is unchanged | 26.3 | 31.8 | 2 |
| crop outline + chunk grids: no clear / viewport update when unchanged (`DecorationOverlays._show_grid`) | **26.6** | **26.8** | 2 |

Findings:
- Swap interval: each buffer swap waited for the 60 Hz refresh, and there were 2-3 swaps per frame. Windows' compositor (DWM)
  composes windowed apps, so swap interval 0 does not tear.
- Every step came from the timer (587/587), so the "pipelined" step inside `frame_displayed` never ran: frames arrived a few ms
  BEFORE the tick, were shown alone, and the step repainted separately.
- Qt handles posted events in order: a QGraphicsScene's queued dirty processing that is posted after the window's UpdateRequest
  gets a pass of its own. The Sensorgram cursor moved in the step, after the first widget update, hence its own pass. Running
  the scene's queued call by hand (`sendPostedEvents(scene, MetaCall)`) made it worse: a pass ran inside the step and the image
  painted twice.
- Per-frame no-ops that were not no-ops: `ScaleBarItem.update_view` (always update()), `draw_crop_outline` (always clear()),
  `update_chunk_grid` / `update_source_grid` (always clear() + `viewport().update()`, grids off or not).
- Not solved: the Image scene still reports a whole-image change right AFTER the main pass and gets a second GL pass (1/frame).
  It is not the view box, the overlay layer or any Python-level item update (all counted). Forcing `viewport().update()` in the
  step made it worse (image painted twice). The GUI thread is now ~28 % idle at 26.6 fps against a 29.6 target.

**Still open after the fix:** the Image view paint (~43 ms per frame with 160 ROIs, labels and group labels) caps Play at ~20 fps
by itself. Re-profile in the real app before working on it.

## Step 6: the "extra" image repaint is useful (2026-11-11, later)

The image scene's queued change (from `setImage`) repaints the OpenGL canvas in a window pass of its own, right after the main pass:
processing that queued call starts a full, synchronous window update. Forcing it into the main pass (canvas set to `NoViewportUpdate`
during Play, one `viewport().update()` per frame after the step) gave **1 pass per frame but 20.3 fps instead of 26.6**: the single
pass had to wait for the GPU to draw the image. Two passes let the GPU draw the image while the CPU does the rest. Reverted; not worth
removing. Running the scene's queued call by hand (`sendPostedEvents`) at the end of the tick: 13 fps (worse).

Bench readings after the PC restarted were ~14 fps with unchanged code: per frame the GUI thread waited ~40 ms more (kernel time 18 vs 7.5
ms), the GPU was not busy (bench 19 %, DWM 13 %), the window swap interval was 0 as set (the 1 read from the canvas is its own FBO
context, which never swaps). Timer resolution was 15.6 ms (1 ms helped a little: 15.2 fps). Cause not found; check in the
maintainer's own app before chasing it.
