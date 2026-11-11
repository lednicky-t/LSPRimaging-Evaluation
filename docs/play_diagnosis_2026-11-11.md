# Play on a 4K monitor: what slows the slideshow (2026-11-11)

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

## 7. Open

- 25 fps at 4K is not reached (20.0 default, 22.6 CPU-only): every whole-window pass costs ~20-40 ms on this Intel UHD at 3840 x 2080.
  Options, none built: the Sensorgram cursor every second frame (~+3 fps, visible); a CPU-only default (a Preferences decision);
  `display_picture` in Qt's native pixel order so CPU painting skips a conversion (Image paint 13 -> ?).
- On the laptop screen or any window under ~2 M real pixels Play is at its cap.
- The bench's own residue: it started the Sensorgram's helper processes; an aborted run can leave one (`parent_pid` of a dead run): safe to end.
