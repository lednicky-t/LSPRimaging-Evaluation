"""Standalone present-cost microbenchmark (my own window, no app, no dataset).

usage (from apps/LSPRi/eva): python tools/present_bench.py MODE WxH(logical) SCREEN [frames]
MODE: gl        a QOpenGLWidget "picture" (60 % of the window) + plain widgets (like the app today)
      plain     the same, picture is a plain widget (no OpenGL anywhere)
      glnative  as gl, the QOpenGLWidget has WA_NativeWindow (own HWND)
      glview    the app's arrangement: the QOpenGLWidget is the viewport of a QGraphicsView
      glviewnative  as glview, with WA_NativeWindow on that viewport
      glcontainer  picture = QOpenGLWindow inside createWindowContainer (own HWND, top window stays plain)
      img       no OpenGL; picture = a 1275x799 image scaled at paint time (smooth), like a CPU-drawn image view
      imgpre    no OpenGL; the same image already scaled to the widget's real pixels (paint = plain copy)
Prints ms per iteration for: strip (only a thin cursor strip updated), frame (strip + whole picture updated), full (everything).
"""

from __future__ import annotations

import os
import sys
import time


def main() -> None:
    mode, size, screen_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    frames = int(sys.argv[4]) if len(sys.argv) > 4 else 200
    from PyQt6.QtCore import QEvent, QRect, Qt
    from PyQt6.QtGui import QColor, QPainter, QSurfaceFormat
    from PyQt6.QtWidgets import QApplication, QHBoxLayout, QWidget

    fmt = QSurfaceFormat.defaultFormat()
    fmt.setSwapInterval(0)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])

    class Strip(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.x = 10

        def paintEvent(self, ev) -> None:
            p = QPainter(self)
            p.fillRect(ev.rect(), QColor("#101820"))
            p.fillRect(QRect(self.x, 0, 3, self.height()), QColor("#38bdf8"))

    paints = {"pic": 0}

    class PlainPic(QWidget):
        n = 0

        def paintEvent(self, ev) -> None:
            paints["pic"] += 1
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(40 + self.n % 50, 80, 120))

    window = QWidget()
    lay = QHBoxLayout(window)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    strip = Strip()
    if mode in ("gl", "glnative"):
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget

        class GLPic(QOpenGLWidget):
            n = 0

            def paintGL(self) -> None:
                paints["pic"] += 1
                p = QPainter(self)
                p.fillRect(self.rect(), QColor(40 + self.n % 50, 80, 120))
                p.end()

        pic = GLPic()
        pic.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        if mode == "glnative":
            pic.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    elif mode in ("glview", "glviewnative"):  # the app's arrangement: the OpenGL widget is the VIEWPORT of a QGraphicsView
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget
        from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

        class GLViewport(QOpenGLWidget):
            n = 0

            def paintGL(self) -> None:
                paints["pic"] += 1
                p = QPainter(self)
                p.fillRect(self.rect(), QColor(40 + self.n % 50, 80, 120))
                p.end()

        gl_viewport = GLViewport()
        gl_viewport.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        if mode == "glviewnative":
            gl_viewport.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        pic = QGraphicsView(QGraphicsScene())
        pic.setViewport(gl_viewport)
        pic.gl = gl_viewport  # the picture to bump
    elif mode == "glcontainer":

        from PyQt6.QtOpenGL import QOpenGLWindow

        class GLWin(QOpenGLWindow):
            n = 0

            def paintGL(self) -> None:
                paints["pic"] += 1
                p = QPainter(self)
                p.fillRect(0, 0, self.width(), self.height(), QColor(40 + self.n % 50, 80, 120))
                p.end()

        glwin = GLWin()
        pic = QWidget.createWindowContainer(glwin)
    elif mode in ("img", "imgpre"):
        import numpy as np
        from PyQt6.QtGui import QImage

        rng = np.random.default_rng(0)
        frames_rgb = [
            np.ascontiguousarray(np.dstack([rng.integers(0, 255, (799, 1275), dtype=np.uint8)] * 3 + [np.full((799, 1275), 255, np.uint8)]))
            for _ in range(4)
        ]

        class ImgPic(QWidget):
            n = 0
            cache: dict = {}

            def paintEvent(self, ev) -> None:
                paints["pic"] += 1
                arr = frames_rgb[self.n % len(frames_rgb)]
                img = QImage(arr.data, 1275, 799, 1275 * 4, QImage.Format.Format_RGB32)
                p = QPainter(self)
                if mode == "img":
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    p.drawImage(self.rect(), img)
                else:
                    # stands in for scaling on a worker thread: done outside the timed paint, keyed per frame
                    dpr = self.devicePixelRatioF()
                    key = (self.n % len(frames_rgb), self.width(), self.height())
                    pre = self.cache.get(key)
                    if pre is None:
                        pre = img.scaled(int(self.width() * dpr), int(self.height() * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                        pre.setDevicePixelRatio(dpr)
                        self.cache[key] = pre
                    p.drawImage(0, 0, pre)
                p.end()

        pic = ImgPic()
        pic.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
    else:
        pic = PlainPic()
    lay.addWidget(pic, 6)
    lay.addWidget(strip, 4)

    screens = app.screens()
    screen = screens[int(screen_arg)] if screen_arg.isdigit() and int(screen_arg) < len(screens) else screens[0]
    width, height = (int(v) for v in size.lower().split("x"))
    window.setGeometry(screen.availableGeometry().x() + 20, screen.availableGeometry().y() + 40, width, height)
    window.show()
    window.windowHandle().setScreen(screen)
    window.setGeometry(screen.availableGeometry().x() + 20, screen.availableGeometry().y() + 40, width, height)
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        app.processEvents()
    info = (
        f"{mode:11s} {window.width()}x{window.height()} dpr={window.devicePixelRatioF()} real {int(window.width() * window.devicePixelRatioF())}x"
        f"{int(window.height() * window.devicePixelRatioF())} screen={screen.name()}"
    )

    def bump_picture() -> None:
        if mode == "glcontainer":
            glwin.n += 1
            glwin.update()
        elif mode in ("glview", "glviewnative"):
            pic.gl.n += 1
            pic.gl.update()
        else:
            pic.n += 1
            pic.update()

    def run(kind: str) -> float:
        # warm-up then timed
        for rounds in (20, frames):
            paints["pic"] = 0
            started = time.perf_counter()
            for i in range(rounds):
                old = strip.x
                strip.x = 10 + (i * 7) % (strip.width() - 20)
                strip.update(QRect(min(old, strip.x) - 2, 0, abs(old - strip.x) + 8, strip.height()))
                if kind in ("frame", "full"):
                    bump_picture()
                if kind == "full":
                    window.update()
                app.processEvents()
                # let the window's own repaint request run (update requests are posted)
                app.sendPostedEvents(None, int(QEvent.Type.UpdateRequest))
                app.processEvents()
                if kind in ("frame", "full") and mode == "glcontainer":
                    # a separate QWindow repaints on a short platform timer: wait until it really painted (fair count)
                    wait_until = time.perf_counter() + 0.1
                    while paints["pic"] < i + 1 and time.perf_counter() < wait_until:
                        app.processEvents()
            elapsed = time.perf_counter() - started
        counts[kind] = paints["pic"] / frames
        return 1000.0 * elapsed / frames

    counts: dict = {}
    results = {k: run(k) for k in ("strip", "frame", "full")}
    print(
        f"== {info}\n   ms/iteration  strip-only {results['strip']:.1f}   picture+strip {results['frame']:.1f} (picture painted {counts['frame']:.2f}x per iteration)"
        f"   whole window {results['full']:.1f}"
    )
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
