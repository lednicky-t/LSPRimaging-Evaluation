# Play on a 4K monitor: what slows the slideshow (2026-11-11)

**Start with section 11** (summary of the day, the rules learned, ideas for later). Sections 1-10 are the record, in the order it happened.

Question (maintainer): Play runs at ~14-15 fps, about 25 earlier; is it the Sensorgram or another panel? Method: controlled runs of the real
window (your saved layout, your dataset *copy*, settings never written) with `tools/play_bench.py`, one switch changed at a time, on an
otherwise idle machine. Earlier steps of the same day: `play_pixmap_cache_2026-11-11.md`.

## 1. Yesterday's 26 fps and today's 15 fps are the same code on a different screen

The bench restores your saved window: today it is maximised on the external DELL (2560 x 1387 logical, dpr 1.5 = **3840 x 2080 real
pixels**); the laptop screen is 1499 x 762 at dpr 1.25. Same code, same dataset, only the window changed:

| Window | Panels shown | fps |
|---|---|---|
| laptop screen, 1.8 M real pixels | Image + ROI table + Sensorgram | **28.4** (the speed setting asks 29.6: at the cap) |
| laptop screen | Image only | 29.3 (cap) |
| external monitor, small window (1.4 M px) | all | 28.9 (cap) |
| external, maximised (8.0 M px) | Image only | 17.7 |
| external, maximised | Image + ROI table (no Sensorgram) | 20.0 |
| external, maximised | Image + ROI table + Sensorgram (your layout) | **15.0** |

fps tracks the window's pixel count. Below ~2 M real pixels Play is at its cap whatever else is shown.

## 2. Where the frame goes at 4K

Per frame (361 frames, graphics-card drawing on, your layout, 69 ms): Image view paint 3.2 ms, Sensorgram plot paint 1.8 ms,
**whole-window repaint passes 63.9 ms in 2.0 passes**. Painting is cheap; what costs is Qt handing the window to the screen. A native
profile (py-spy `--native`, my own bench process) puts ~32 % of the GUI thread in `wglSwapLayerBuffers`, ~15 % in memory copies and
~15 % in `glTexSubImage2D`, all Intel GL driver (`ig9icd64.dll`). One pass costs about 20 ms (nothing but the cursor strip repainted)
to 44 ms (with the image) at 3840 x 2080.

## 3. The Sensorgram: cheap to draw, but it forced a second pass

| Config at 4K | passes / frame | window passes ms | fps |
|---|---|---|---|
| your layout | 2.0 | 63.9 | 14.5 |
| Sensorgram hidden | **1.0** | 44.1 | 20.0 |

The Sensorgram cursor moves at every frame. A QGraphicsView repaints for a scene change in a queued call that runs after the window's own
repaint request, so cursor + sliders went to the screen in one pass and the image (whose OpenGL repaint is immediate) in another.
**Fix (built):** during Play both views stop repainting on scene changes (`NoViewportUpdate`); the Image panel calls one repaint per frame,
last, after the Sensorgram marked its cursor strip (`ImagePanel.play_frame_shown` -> `SensorgramPanel.repaint_after_play_frame`). Anything
else the scene reports in the Sensorgram (a new sensorgram, an edited step) is still forwarded; a 100 ms guard timer repaints the Image
view when no frame came (zoom / pan / tool overlays during a stalled Play). Modules: `panels/image/play_repaint.py`,
`panels/sensorgram/play_repaint.py`; tests `tests/integration/test_lspri_play_repaint.py` (+ one in `test_lspri_rewrite_slider_ribbon.py`).
**Measured with the shipped code: 14.5 -> 20.0 fps, 1.0 pass per frame.** The other panels (ROI table, hidden Spectra / Histogram) were not
repainting per frame; with the Sensorgram hidden Play is 20.0 as well, so nothing else costs.

## 4. "Draw the image with the graphics card" off is not a CPU-only mode

Switching it off in Preferences at run time leaves the **window** an OpenGL window (Qt made it one when the OpenGL view was created; the
profile of an "off" run still shows `rhiFlush` and `wglSwapLayerBuffers`). So the earlier on/off comparisons measured a hybrid. Launched
with the setting saved as off (no OpenGL widget ever created, plain Windows presentation), at 4K, your layout:

| Mode | fps | Image paint ms | passes |
|---|---|---|---|
| OpenGL image view (default) | 14.5 | 3.2 | 2.0 |
| hybrid (window OpenGL, image painted by CPU; what a run-time switch gives) | 19.5 | 10.6 | 2.0 |
| launched with it off (CPU only) | 16.6 | 19.0 | 2.0 |
| **launched off + one repaint per frame (this change)** | **22.6** | 13.0 | 1.5 |
| OpenGL image view + one repaint per frame (this change, the default) | **20.0** | 4.4 | 1.0 |

On the laptop screen every mode is at the 29 cap, so the card gives no advantage there. At 4K the CPU-only launch is ~2.5 fps faster than the
default after this change, but that is a Preferences choice (and a restart); not changed.

## 5. Tried and not worth it

- Delivering the scenes' queued updates by hand to merge the passes (`sendPostedEvents`): 12.6 and 16.7 fps (2.7 and 2.1 passes).
- Qt's Direct3D11 presentation (`QT_WIDGETS_RHI=1`, `QT_WIDGETS_RHI_BACKEND=d3d11`, with the OpenGL view off): 10.7 fps, passes 82 ms.
- One pass in the hybrid: the same 19.3 fps (one pass just costs more: 41 ms).
- Windows timer resolution 1 ms: +0.7 fps, not kept.

## 6. Why a thin cursor strip costs ~14 ms, and what could change it (later the same day)

Standalone microbenchmark `tools/present_bench.py` (a window with a picture widget + a thin moving strip; no app, no dataset), 4K window
(3840 x 2080 real pixels), ms per iteration:

| Mode | strip only | picture + strip |
|---|---|---|
| OpenGL picture in the window (what the app does) | **14.4** | 27.5 |
| same, 1920 x 1039 window (1/4 of the pixels) | 3.6 | 6.2 |
| OpenGL picture with its own native window handle (`WA_NativeWindow`, set **before** the first show) | **0.8** | 20.2 |
| no OpenGL anywhere (plain widgets) | 0.7 | 10.8 |
| Windows-upscaled launch (96 dpi: 2560 x 1387 real pixels), OpenGL | 6.5 | 12.6 |

Reading: painting the strip is ~0.7 ms. In an OpenGL window Qt recomposes and swaps the **whole window** for any change, so the cost
follows the window's pixel count (4x the pixels -> 4x the time), not the size of the change. A native window for the picture takes the
strip out of that (0.8 ms). Why the driver needs ~1.8 ns per pixel per hand-over is inferred from the earlier native profile
(`wglSwapLayerBuffers`, copies, `glTexSubImage2D` in the Intel driver); not proven.

Real app, 4K, your layout (`tools/play_bench.py`, 25 s Play; "Windows-upscaled" = launched with `QT_QPA_PLATFORM=windows:dpiawareness=0`):

| Setup | fps | window passes per frame |
|---|---|---|
| shipped (OpenGL image view) | 20.7 | 1.0 x 40.5 ms |
| image view native, set after the window was shown | 17.6 (worse) | 1.0 x 50 ms |
| image view native **from creation** (`WA_NativeWindow` on the `QOpenGLWidget` before `replace_viewport`) | 23.0 | 2.0 x 19 ms |
| launched with the card off (CPU only) | 22.6 | 1.5 |
| Windows-upscaled launch, OpenGL | 28.2 | 1.3 |
| Windows-upscaled launch, native image view | 29.2 (cap 29.6) | 2.0 |
| Windows-upscaled launch, CPU only | 29.2 (GUI 48 % busy) | 2.1 |

Notes: (1) a window that was ever an OpenGL window keeps composing through OpenGL, so the native flag only works if set before the
first show; (2) the upscaled launch makes the **whole** window 96 dpi and Windows stretches it 1.5x: text and the picture get soft, and
it is a launch-time choice (no switch at run time); (3) the image itself is 1275 x 799 and is drawn ~1.25x enlarged at 4K, so little real
detail is lost in the picture, but text and overlays are; (4) the experiment switch in `gpu_view.py` was removed again (not kept);
(5) with the native image view the GUI thread is no longer the bottleneck (`deliver` 33 -> 0.4 ms, GUI 66 % busy, cycle still 43 ms): the
rest is the render thread's hand-off chain, not examined.

### 6b. Built as two Preferences choices (Edit -> Preferences -> Performance and memory; both applied at the next start)

- **Screen resolution: Full (default) / Standard** (`display_options.py`, key `display/resolution`). Standard sets Qt's platform option
  `windows:dpiawareness=0` before the QApplication is created (`src/main.py`, `app_rewrite.main`), so Windows draws the window at 100 % scale and
  stretches it. Checked end to end: Qt then reports dpr 1.0 on both screens (it was 1.25 / 1.5). Measured above: 28.2 fps instead of 20.7. Never
  overrides another platform (`offscreen` in the tests); a variable already asking for 96 dpi counts as Standard; the Preferences note says what
  *this run* has. Windows only (the combo is greyed elsewhere).
- **Show the image in a window of its own (experimental, off)** (`gpu_view.apply_gpu_drawing(own_window=True)`, key `image/own_window`):
  `WA_NativeWindow` on the OpenGL viewport before the first show.

**Result for the image window in the real app: no reliable gain.** Paired runs on the user's layout while the PC was no longer idle (noise about
+-2 fps): own window 20.6, 23.4, 20.1, 23.1; default 18.6, 20.2, 20.6, 22.4 (one 40 ms window pass per frame in both). The standalone test
still shows the effect for the app's arrangement (OpenGL viewport of a `QGraphicsView`, strip only: 17.7 -> 0.9 ms), so Qt does what was
expected. Why it does not carry over (inference, not proven): the one-repaint-per-frame change (section 3) had already merged the cursor strip
into the image's pass, where it costs almost nothing extra; the native window can then only save the re-composition of the window area outside
the image (~3-7 ms in the standalone test). It would matter again if the Sensorgram were repainted alone often. Kept as an opt-in because it is
cheap to carry, but not recommended; the risks Qt lists for native child windows (flicker while resizing or docking, they cover overlapping
siblings, widgets floating over the image: crop fields, measure calibration, cursor readout) have **not been checked by eye**.

## 6c. Full resolution without the whole-window cost: what the standalone bench says (2026-11-11, evening)

Question (maintainer): can 4K be fast without lowering resolution, given the Sensorgram cursor is one line? Two modes added to
`tools/present_bench.py`: `img` (no OpenGL, a 1275 x 799 picture scaled at paint time) and `imgpre` (the same picture already scaled to
the widget's real pixels); `glcontainer` (a `QOpenGLWindow` inside `QWidget.createWindowContainer`: the picture has its own swap chain and
the **top window stays a plain, non-OpenGL window**) was in the tool but never recorded, and its loop now waits until the picture really
painted (a separate window repaints on a short platform timer; without the wait it painted only 45 % of iterations). 4K window, ms per iteration:

| Mode | strip only | picture + strip |
|---|---|---|
| `gl` (QOpenGLWidget in the window, what the app does) | 12.9-15.5 | 21.8-22.5 |
| `glview` (the app's QGraphicsView arrangement) | 12.7 | 20.8 |
| **`glcontainer`** (picture in its own OpenGL window) | **0.8** | **6.5-6.8** |
| `plain` (no OpenGL, colour fill) | 0.6 | 10.0-10.6 |
| `img` (no OpenGL, picture scaled while painting) | 0.8 | 14.3 |
| `imgpre` (no OpenGL, picture pre-scaled) | 0.8 | 9.6 |

Reading: the cost is not the cursor and not the resolution of the picture; it is that **any `QOpenGLWidget` makes Qt compose and swap the
whole top window through OpenGL for every change**. Without one, a change costs what it covers (strip 0.6-0.8 ms; picture area ~10 ms
by plain Windows copy; 4 ms more if the picture is scaled while painting). A `glcontainer` picture costs ~6.6 ms at full resolution.
`glnative` (section 6, 20.2 ms) does not get this because the top window is still OpenGL-composed.

### 6d. The real app with the current code (commit b9bde6b), 4K, your layout, idle PC

`tools/play_bench.py` with `PLAY_BENCH_TIMING=1`, two runs each, in both orders (image view 1642 x 666 logical, ROI/Groups + Sensorgram shown):

| Launch | fps (2 runs) | cycle ms | Image paint ms | window pass ms | passes / frame | GUI busy |
|---|---|---|---|---|---|---|
| graphics card on (default) | 24.5, 24.7 | 41 | 3.4 | 33-34 | 1.0 | 78-84 % |
| **graphics card off from launch** (`PLAY_BENCH_GPU=cold0`) | **30.6, 30.0** (the cap) | 33 | 9-10 | 26-27 | 1.0 | 84-86 % |

So the "gap" of section 6c is gone: section 4's CPU-only 22.6 fps was measured with 1.5 passes per frame, before the later commits; with one
pass per frame the CPU-only launch is **at the cap at full 4K resolution**. The graphics card now only costs: it makes painting the image
~6 ms cheaper but every window hand-over ~7 ms dearer, and the hand-over is what is left. On the laptop screen every mode is at the cap (section 1).
Consequences: the "Standard" screen resolution (28 fps, softer) and the own-window experiment (no reliable gain) are beaten by a plain
CPU-only launch at full resolution. The Preferences tool tip ("15-20 % faster", "takes effect at once") is out of date: a run-time switch
gives the hybrid of section 4, only a restart gives the CPU-only window. Headroom in CPU mode: GUI 84 % busy; pre-scaling the picture on
the render thread (section 6c, `imgpre`) could save ~4 ms of the 9-10 ms paint if ever needed.

**Done (maintainer's go-ahead, same evening):** "Draw the image with the graphics card" is **off by default**; its tool tip says why and
that switching it off needs a restart; "Screen resolution" and "Show the image in a window of its own" were **removed** (module
`display_options.py`, keys `display/resolution` and `image/own_window`, the `own_window` argument of `gpu_view.apply_gpu_drawing`).
`tools/play_bench.py`: `PLAY_BENCH_GPU=1` now starts with the card on (the saved choice is used otherwise).

## 8. Uncapped (20x speed, cap 60 fps): where a CPU-drawn frame goes (2026-11-11, late)

The maintainer's saved settings still had the graphics card on (a saved choice is not changed by a new default). 4K, your layout, 20x:
card **off from launch 26.0-27.8 fps** (cycle 36-38 ms, GUI 82-86 % busy), card **on 19.5** (cycle 51). So Play is GUI-thread bound.

py-spy `--nonblocking` (Python level, 25 s, 200 Hz) of the bench process, card off; share of the GUI thread's busy time, ~33 ms per frame:

| What | share | ~ms / frame |
|---|---|---|
| Qt's own work with no Python frame on the stack (window composition, flush to the screen, plain Qt widgets) | 37 % | 12 |
| `overlay_cache.LayerCacheEffect.draw`: blending the screen-sized ROI overlay picture (alpha) over the image | 15 % | 5 |
| the image (`QGraphicsView.paintEvent` native + `ImageItem.paint`; RGB888 -> RGB32 conversion + scaling, seen in the native profile) | 15 % | 5 |
| `PanelContainer.paintEvent` (the dock card's antialiased border, repainted each frame) | 5 % | 1.7 |
| `no_data_overlay_rgba` (`isfinite` + `array_equal` over the whole frame, GUI thread) | 4 % | 1.4 |
| timeline strip paint | 4 % | 1.3 |
| display-range fields `_refresh_fields` | 2.6 % | 0.9 |
| everything else (slideshow step, Sensorgram cursor, sliders, status text) | ~17 % | 5-6 |

A native profile (`--native`, pauses the process at each sample, so only proportions count) also caught one autosave
(`_save_now -> prepare_session -> _flat_asdict`) on the GUI thread; not per frame, but a hitch when it happens.

Ideas, none built: (A) small: no-data mask on the render thread, dock card border only where needed, text fields at ~10 Hz during Play,
cached timeline strip (~5 ms); (B) bigger: the render thread hands over a screen-ready picture (scaled to device pixels with the ROI overlay
already blended; Play only, falls back on zoom / pan), so the GUI copies one opaque picture (~8 ms more). Floor: Qt's composition + flush (~12 ms).

## 9. Item A built: four paint savings (2026-11-11, late)

1. **The Image view's plain viewport is opaque** (`gpu_view.mark_opaque`, called by the panel at start and by the switch): the paint census
   showed the main window, the dock and the Image panel each repainting the ~1.25 M logical px behind the image every frame (6.3 -> 2.0
   M px painted per frame). The OpenGL viewport had this fix already; the plain one (now the default) did not. The biggest single gain.
2. **The "no data" checker comes from the render thread** (`FrameDisplay.no_data`, `no_data.no_data_overlay_from_valid`): built from the
   valid-pixel mask the render thread already has, one OpenCV call while the mask is unchanged. A first version called the four numpy
   steps of `no_data_overlay_rgba` there and the render thread's picture time went 6 -> 17-19 ms (each native call waits for the
   interpreter lock again while the GUI thread is busy); the one-call version is ~10-12 ms.
3. **Timeline strips draw a cached picture** of the line and marks (`TimelineStrip._static_picture`, keyed on size, view, colours, a marks
   version and the governing mark); only the cursor is drawn per frame. Same pixels as before.
4. **Dock card**: when only the inside of the card asks for paint, a plain fill instead of the antialiased rounded outline
   (`PanelContainer._away_from_border`). **Display range fields** during Play: rewritten at most every 0.1 s (`show_frame_levels(playing=)`);
   the frame Play stops on is drawn again with `playing` False, so its numbers always show.

Tests: `tests/integration/test_lspri_play_paint_savings.py`. **Measured** (interleaved A/B, last commit in a worktree vs this code, 25 s
each, 4K, your layout, 20x, processor drawing): before 26.1 / 30.4 / 32.1 fps, after 30.3 / 38.3 / 39.2 (mean 29.5 -> 35.9); GUI CPU per
frame 27-32 -> 19-22 ms. Back-to-back single runs on this laptop vary by +-5 fps (heat, other programs): always compare interleaved.

## 10. Item B: overlay tiles kept, screen-size picture tried and removed (2026-11-11, late)

**B1, kept: the cached ROI overlay is drawn as its used tiles only** (`overlay_cache._Entry`). On the first hit of a cached state the
picture is kept in device pixels (ratio 1, drawn under a 1 / ratio scale: pixel-identical to `drawPixmap(offset, picture)`) and its
32 x 32 tiles with any non-transparent pixel are found; runs of neighbouring tiles in a row become one piece of
`QPainter.drawPixmapFragments` (one call). A picture with more than 60 % of its tiles in use is drawn whole. Skipping a fully
transparent tile is exact (premultiplied alpha 0 changes nothing). Tests: `OverlayTilesTest` (sparse and dense, ratio 1.5, against Qt).

**B2, removed: the frame resampled to screen size on the render thread.** Built and made pixel-identical to Qt (Qt's nearest-pixel
draw steps through the source in 16.16 fixed point with a truncated step; plain `floor` drifted one device pixel every ~70 pixels at
some zooms). Used for 715 of 716 frames in the app, Image paint 12 -> 8 ms in the timing run. But measured interleaved, A + B1
**with** it 39.2 / 37.4 / 39.9 fps, **without** it 43.9 / 44.3 / 44.3; GUI CPU per frame 19.2 vs 16.5 ms. Qt's own scaled draw of the
1275 x 799 picture reads 4x fewer source bytes than copying the 2.5 M-pixel screen picture, and the resample added ~3 ms to the render
thread, which is now on the critical path (frame queue ~6 ms, picture ~10-13 ms). Removed completely (`screen_picture.py` deleted).

**Result of A + B1** (interleaved against the last commit, your layout, 4K, 20x, processor drawing): ~32 fps -> **~44 fps**.
What is left per frame: GUI ~16.5 ms of which ~half is Windows kernel time (handing the window to the screen), the rest the view's paint;
the GUI thread idles ~25 %, because the next frame waits for the render thread (queue + picture ~16-19 ms). Further gains would come from
the render thread (fewer native calls per frame, or preparing the next frame earlier), not from painting.

## 7. Open

- On the laptop screen or any window under ~2 M real pixels Play is at its cap.
- The bench's own residue: it started the Sensorgram's helper processes; an aborted run can leave one (`parent_pid` of a dead run): safe to end.
- The "pre-scale the picture on the render thread" idea listed here earlier was built and measured in section 10 (B2): slower, removed.
- Everything else that is still open is in section 11.

## 11. Summary of the day and ideas for later (2026-11-11)

**Where Play stands** (4K DELL, 3840 x 2080 real pixels, your layout, 20x speed so the cap is 60 fps, processor-drawn image):
14.5 fps at the start of the day -> 20 (one repaint per frame) -> ~25-27 (graphics card off) -> **~44** (sections 9-10). On the laptop
screen it was already at its cap. All numbers are interleaved A/B runs of `tools/play_bench.py`; single runs on this laptop vary by +-5 fps.

**What we learned (the rules to keep):**
1. **Count whole-window passes, not milliseconds of painting.** Painting is cheap (3-10 ms); handing the window to the screen is 20-40 ms
   at 4K and follows the window's pixel count. `playback.paints` (`passes_per_frame`, `kpx_per_frame`) is the number to watch.
2. **Any `QOpenGLWidget` makes Qt compose and send the WHOLE window through OpenGL for every change.** That is why the graphics-card
   option, once a win, became a loss after the one-repaint-per-frame fix; it is now off by default.
3. **A widget that paints every pixel must say so (`WA_OpaquePaintEvent`).** Otherwise Qt repaints everything behind it first (the dock,
   the panel, the main window: ~4 M px per frame). Check the census (`kpx_per_frame`) whenever a view's viewport is replaced.
4. **On the render thread, every native call (numpy, OpenCV) hands the interpreter lock back and waits for it again while the GUI is busy.**
   Fewer, bigger calls beat many small ones (the missing-pixel checker: 4 numpy calls +10 ms, one OpenCV call ~0).
5. **Cached pictures beat repainting, but only copy what holds something** (overlay tiles, strip picture).
6. **Less data wins over a pre-converted copy:** Qt scaling a 1275 x 799 picture is cheaper than copying a 2.5 M-pixel screen-size one
   (B2, removed).
7. **Setting measurement discipline:** compare interleaved, with the baseline in a `git worktree` of HEAD on `PYTHONPATH`; check the
   profile at the Python level with `py-spy record --nonblocking -p <child pid>` (the venv's `python.exe` is a launcher: use the child's pid).

**Ideas for later, none built (roughly by expected gain; all unmeasured guesses unless a number is given):**
- **Render thread as the limit (most promising; the only known route past ~37 fps, measured 2026-11-12: section 12).** After A + B1 the GUI thread idles ~25 % waiting for the next frame (queue ~6 ms +
  picture ~10-13 ms + load). Options: prepare frame N + 1 while N is drawn (a deeper pipeline; the slideshow's "stale frames" logic must
  be kept right); make `prepare_frame_display(fast=True)` cheaper (fewer native calls: it still does compare, countNonZero, minMaxLoc,
  addWeighted, bitwise_and, merge = 6 calls with NaN pixels); drop the automatic-levels sampling during Play when the range is fixed.
- **Windows kernel time on the GUI thread (~8 ms of ~16.5).** Presenting the window is the floor. Untested ideas: a different swap
  interval / `QT_QPA_PLATFORM` flag (D3D11 presentation was 10.7 fps in section 5, so unlikely), or a smaller repainted rectangle if the
  view ever stops using `FullViewportUpdate` during Play (only the strip + image rectangles; needs the ghost-line problem of 2026-10-03 solved).
- **Sensorgram cursor every second frame** (~+3 fps earlier in the day, visible as a lower cursor rate): now probably less than that.
- **Display-range fields and status text during Play:** the remaining per-frame Python work on the GUI thread (status text, frame
  counters) could also be throttled to 10 Hz like the range fields; ~1-2 ms.
- **Native pixel order for the picture** (`display_picture`: build the grey picture as BGRA / ARGB32 premultiplied directly, so
  `ndarray_to_qimage(..., RGBA8888)` is not converted by Qt: seen as `qt_convert_rgb888_to_rgb32_ssse3` in the native profile).
- **GPU-native drawing** (textures + shaders, e.g. VisPy): the only route past the whole-window cost with the graphics card, but a rewrite of
  the image view and every overlay item; not justified at 44 fps.
- **Make the two load-sensitive tests robust** (`test_lspri_analysis_warm_pool`, `test_lspri_rewrite_histogram_panel` fail when other test
  files run alongside and pass alone: they wait a fixed time for a child process / worker thread).

**Where the code is:** `panels/image/gpu_view.py` (`mark_opaque`), `no_data.py` + `display_picture.py` (checker on the render thread),
`timeline_strip.py` (`_static_picture`), `panels/dock_container.py` (`_away_from_border`), `display_range_controls.py`
(`show_frame_levels(playing=)`), `overlay_cache.py` (`_Entry`), tests `tests/integration/test_lspri_play_paint_savings.py`, benches
`tools/play_bench.py` and `tools/present_bench.py`.

## 12. Screen resolution restored: uncapped A/B at 4K (2026-11-12)

Question (maintainer): with the graphics card on, how far does the 96-dpi "reduced" resolution go (hope: 60 fps)? Setup: real main window, a COPY of
the 314-cube dataset (images as a junction), saved Play speed 20x (uncapped), `PLAY_BENCH_SCREEN=external` (the 4K DELL, window 2520 x 1329
logical = 3780 x 1994 real pixels at full, 2520 x 1329 at reduced), saved layout (Image + ROI/Groups + Sensorgram), 25 s per run, order
A B C D D C B A so drift hits every combination alike. Reduced = `QT_QPA_PLATFORM=windows:dpiawareness=0` (what Preferences -> Screen resolution
sets). Script: the scratchpad `run_ab.sh`; raw output was one file per run.

| Combination | runs (fps) | mean fps | cycle ms | GUI busy |
|---|---|---|---|---|
| full resolution, card off | 34.3, 27.5 | 30.9 | 29-36 | 60-67 % |
| full resolution, card on | 22.3, 16.4 | 19.4 | 45-60 | 85-90 % |
| **reduced, card off** | 37.7, 37.1 | **37.4** | 26.6-26.8 | 49 % |
| **reduced, card on** | 37.6, 35.3 | **36.5** | 26.6-28.2 | 82-86 % |

Reading:

1. **Reduced is faster at this window**: +21 % with the card off (37.4 vs 30.9; the full-resolution pair spread 27.5-34.3, so treat as +10 to +35 %) and
   +88 % with the card on (36.5 vs 19.4).
2. **At reduced resolution the card makes no difference** (36.5 vs 37.4, inside the noise), so "card on" is no longer a penalty there; at full
   resolution the card costs ~11 fps (as in section 6d).
3. **60 fps is not reached; the ceiling is ~37 fps and it is not painting any more.** With the card off at reduced the GUI thread is only 49 % busy
   and the cycle is 26.6 ms in both card modes: the limit is the render thread's hand-off chain (read, queue ~6 ms, picture ~8-10 ms), the "remaining
   limit" of section 10. 60 fps needs a 16.7 ms cycle: ~10 ms less on that path. Painting less cannot give it.
4. The full-resolution runs fell with time (34.3 -> 27.5, 22.3 -> 16.4 from the first pair to the last); the reduced ones did not. Cause unknown
   (heat? other load?). Absolute numbers are lower than section 8/10's ~44 fps at full resolution with the card off (same code, different window
   state and PC state: not explained). Compare only within this table.
5. Not judged by eye: how soft the text and picture look at reduced resolution (the price). Exports and results do not depend on it (display only);
   exported figure sizes at reduced resolution were not tested.

### 12b. Possible route toward 60 fps (written down 2026-11-12 at the maintainer's request; NOT started, not promised)

The A/B above leaves the render thread's hand-off chain as the limit at reduced resolution: cycle ~26.6 ms, GUI thread ~49 % busy (card off).
60 fps needs a 16.7 ms cycle, so roughly **10 ms must come off that chain**; painting less cannot help. Where to look, in this order (details
and the ideas around them in section 11, "Render thread as the limit"):

1. Profile the chain first with `playback.window` fields (`deliver_ms`, `pic_ms`, queue) at reduced resolution, card off, so the GUI is not in the way.
2. Prepare frame N + 1 while frame N is being drawn (deeper pipeline), keeping the slideshow's stale-frame handling right.
3. Fewer native calls in `prepare_frame_display(fast=True)` (6 with NaN pixels), and no automatic-levels sampling while Play runs with a fixed range.
4. Re-measure with the interleaved A/B recipe (section 11 rule 7); the maintainer's gate for "worth keeping" is a clear gain at 4K at reduced AND full resolution.

Everything here is unmeasured until step 1 is done; the 10 ms figure is arithmetic from the table, not a finding about a particular call.
