"""Play benchmark: the real main window playing a COPY of a dataset, with switches to change one thing at a time (2026-11-11).

Why it exists: "Play is slow" reports need a repeatable measurement of the real window on the real display; see
`docs/play_diagnosis_2026-11-11.md` for what it found (fps follows the window's real pixel count; one repaint pass per frame matters).

**Use a COPY of the dataset.** The app autosaves the session into the dataset's `sessions/` folder. Make a folder with the same name, copy
`sessions/`, `analysis/`, `Experimental_data/`, `plane_times_s/`, `valid_mask/` and `zarr.json`, and make `0/` (the images) a directory
junction to the original (`mklink /J copy\\0 original\\0`: no 10 GB copy). The app settings are loaded but never written
(`on_settings_changed` is a no-op).

Usage (from `apps/LSPRi/eva`):  python tools/play_bench.py DATASET_COPY LABEL [play_seconds] [startup_seconds]
Switches (environment variables):
  PLAY_BENCH_HIDE=Sensorgram,Spectra   hide these docks (object names without 'Panel'); ALLBUTIMAGE hides every other dock
  PLAY_BENCH_SCREEN=primary|external|N move the window to that screen before playing
  PLAY_BENCH_WINDOW=WxH                resize the window (logical pixels)
  PLAY_BENCH_GPU=0|cold0               0: switch "Draw the image with the graphics card" off at run time (the window stays an OpenGL
                                       window: a hybrid); cold0: off from the start (no OpenGL widget is ever created)
  QT_QPA_PLATFORM=windows:dpiawareness=0   (Qt's own switch, not the bench's) run as a 96-dpi program: Windows upscales the window
  PLAY_BENCH_TIMING=1                  also time the whole-window repaint passes and the plot views' paint, per frame
Prints one result block. Ends the process with `os._exit` (the app's helper processes import this file again: keep the `__main__` guard).
"""

from __future__ import annotations

import dataclasses
import json
import os
import statistics as st
import sys
import time
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    dataset = Path(sys.argv[1])
    label = sys.argv[2]
    play_s = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0
    startup_s = float(sys.argv[4]) if len(sys.argv) > 4 else 50.0

    from collections import Counter

    from PyQt6.QtCore import QEvent, QTimer
    from PyQt6.QtGui import QPixmapCache
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app import app_rewrite
    from lspr_imaging_app.applog import configure_logging
    from lspr_imaging_app.gil_tuning import apply_switch_interval
    from lspr_imaging_app.opengl_surface import configure_opengl_surface
    from lspr_imaging_app.panels.dock_container import PanelContainer
    from lspr_imaging_app.panels.image.panel import ImagePanel
    from lspr_imaging_app.storage.app_settings import load_app_settings
    from lspr_imaging_app.version import version_string

    system = configure_logging(app_version=version_string())
    apply_switch_interval()
    configure_opengl_surface()
    state = {"t0": None}
    timing: Counter = Counter()
    counts: Counter = Counter()
    timed = bool(os.environ.get("PLAY_BENCH_TIMING"))

    class TimedApp(QApplication):
        def notify(self, obj, ev):
            if ev.type() == QEvent.Type.UpdateRequest and obj.isWidgetType() and obj.isWindow() and state["t0"]:
                started = time.perf_counter()
                result = super().notify(obj, ev)
                timing["window_pass"] += time.perf_counter() - started
                counts["window_pass"] += 1
                return result
            return super().notify(obj, ev)

    app = TimedApp(sys.argv[:1]) if timed else QApplication(sys.argv[:1])
    if timed:
        from pyqtgraph.widgets.GraphicsView import GraphicsView

        inner_paint = GraphicsView.paintEvent

        def timed_paint(self, event):
            started = time.perf_counter()
            inner_paint(self, event)
            if state["t0"]:
                key = "paint_" + (self.parent().__class__.__name__ if self.parent() is not None else self.__class__.__name__)
                timing[key] += time.perf_counter() - started
                counts[key] += 1

        GraphicsView.paintEvent = timed_paint
    QPixmapCache.setCacheLimit(max(QPixmapCache.cacheLimit(), app_rewrite._PIXMAP_CACHE_KB))
    settings = dataclasses.replace(load_app_settings(), last_dataset_folder=str(dataset), auto_reopen_last_dataset=True)
    if os.environ.get("PLAY_BENCH_GPU") == "cold0":
        settings = dataclasses.replace(settings, ui_state={**dict(settings.ui_state), "image/gpu_drawing": False})
    window = app_rewrite.build_main_window(settings, on_settings_changed=lambda *_args: None)  # never writes the settings file
    window.show()
    panel = window.findChild(ImagePanel)
    docks = {d.objectName().removesuffix("Panel"): d for d in window.findChildren(PanelContainer)}
    log_path = Path(system.log_path)
    steps = {"n": 0}
    keep = QTimer()

    def prepare() -> None:
        screens = app.screens()
        want = os.environ.get("PLAY_BENCH_SCREEN")
        if want:
            target = screens[0] if want == "primary" else screens[1] if want == "external" and len(screens) > 1 else screens[int(want)] if want.isdigit() else screens[0]
            geometry = target.availableGeometry()
            window.showNormal()
            window.setGeometry(geometry.x() + 20, geometry.y() + 40, min(geometry.width() - 40, window.width()), min(geometry.height() - 80, window.height()))
            window.windowHandle().setScreen(target)
        if os.environ.get("PLAY_BENCH_WINDOW"):
            width, height = (int(v) for v in os.environ["PLAY_BENCH_WINDOW"].lower().split("x"))
            window.showNormal()
            window.resize(width, height)
        hide = os.environ.get("PLAY_BENCH_HIDE", "")
        if hide:
            for name in [n for n in docks if n != "Image"] if hide == "ALLBUTIMAGE" else [n.strip() for n in hide.split(",")]:
                if name in docks:
                    docks[name].setVisible(False)
        if os.environ.get("PLAY_BENCH_GPU") == "0":
            panel.set_gpu_drawing(False)

    def start_play() -> None:
        prepare()
        QTimer.singleShot(2500, begin)  # the layout settles after hiding / moving

    def begin() -> None:
        state["t0"] = time.monotonic()
        panel._slideshow.step_made.connect(lambda _seconds: steps.__setitem__("n", steps["n"] + 1))
        panel._slideshow.play()
        keep.start(500)

    def tick() -> None:
        if not panel._slideshow.is_playing():
            panel._slideshow.play()  # reached the end: start over
        if time.monotonic() - state["t0"] >= play_s:
            keep.stop()
            panel._slideshow.stop()
            QTimer.singleShot(600, finish)

    def finish() -> None:
        rows = []
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        windows = [r["f"] for r in rows if r.get("ev") == "playback.window"][2:]  # the first two are warm-up
        screen = window.screen()
        visible = [n for n, d in docks.items() if d.isVisible() and not d.visibleRegion().isEmpty()]
        print(
            f"== {label}: screen {screen.name()} {screen.geometry().width()}x{screen.geometry().height()} dpr={screen.devicePixelRatio()} "
            f"window {window.width()}x{window.height()} image view {panel._view.width()}x{panel._view.height()} gpu={panel.gpu_drawing} docks={visible}"
        )
        if windows:
            med = lambda key: st.median(w.get(key, 0) for w in windows)  # noqa: E731
            print(
                f"   fps {med('fps'):.1f} (min {min(w['fps'] for w in windows):.1f} max {max(w['fps'] for w in windows):.1f})  cycle {med('cycle_ms'):.1f}  "
                f"gui_cpu {med('gui_cpu_ms'):.1f}  busy {med('gui_busy_pct'):.0f}%  deliver {med('deliver_ms'):.1f}  pic {med('pic_ms'):.1f}"
            )
        if timed and steps["n"]:
            n = steps["n"]
            print("   per frame: " + "  ".join(f"{k} {1000 * timing[k] / n:.1f} ms x{counts[k] / n:.1f}" for k in sorted(timing)))
        sys.stdout.flush()
        window.close()
        app.quit()

    keep.timeout.connect(tick)
    QTimer.singleShot(int(startup_s * 1000), start_play)
    QTimer.singleShot(int((startup_s + play_s + 60) * 1000), app.quit)  # safety net
    app.exec()
    os._exit(0)


if __name__ == "__main__":  # the app's helper processes import this file again: they must not start a second app
    main()
