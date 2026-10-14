"""Canvas widgets that report how long their painting took (2026-10-14).

Qt paints the new image *after* `ImagePanel._on_rendered` returns, so the panel's own
stage timers cannot see it; during Play that hidden cost was ~200 ms of each ~450 ms
frame cycle (inferred, then measured with these). `paint_listener` is only set while a
playback run is being measured, so normal operation costs one `is None` check.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtGui import QImage


class TimedLayoutWidget(pg.GraphicsLayoutWidget):
    """The whole canvas: everything Qt paints for the view (image, overlays, axes)."""

    paint_listener: Callable[[float], None] | None = None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        started = time.perf_counter()
        super().paintEvent(event)
        elapsed = time.perf_counter() - started
        listener = self.paint_listener
        if listener is not None:
            listener(elapsed)


class TimedImageItem(pg.ImageItem):
    """The main image: levels / colour mapping and the draw of the pixmap.

    `.image` always stays the float frame (the cursor readout and the tests read the values from it).
    `show_prepared` can hand over, besides, the finished 8-bit picture of that frame, built on the render
    thread (`display_picture.py`); `render` then uses it instead of converting the frame on the GUI thread.
    Anything that would make that picture wrong (a new image, new levels or colour map, automatic
    down-sampling) drops it and pyqtgraph renders as usual. Relies on pyqtgraph 0.14 internals
    (`render`, `_renderRequired`, `_unrenderable`, `qimage`); the pin is `<0.15` and
    `test_lspri_display_picture.py` compares the result with pyqtgraph's own.
    """

    paint_listener: Callable[[float], None] | None = None
    _prepared_qimage = None

    def show_prepared(self, image, picture, levels) -> None:
        """Show `image` (float frame) using `picture` ((h, w, 4) uint8 RGBA made for exactly that frame
        and `levels`) instead of converting it at paint time."""
        pg.ImageItem.setImage(self, image, autoLevels=False, levels=levels)
        self._prepared_qimage = pg.functions.ndarray_to_qimage(picture, QImage.Format.Format_RGBA8888)

    def setImage(self, *args, **kwargs) -> None:  # noqa: N802 - Qt naming
        self._prepared_qimage = None
        super().setImage(*args, **kwargs)

    def setLevels(self, *args, **kwargs) -> None:  # noqa: N802
        self._prepared_qimage = None
        super().setLevels(*args, **kwargs)

    def setLookupTable(self, *args, **kwargs) -> None:  # noqa: N802
        self._prepared_qimage = None
        super().setLookupTable(*args, **kwargs)

    def setOpts(self, *args, **kwargs) -> None:  # noqa: N802
        self._prepared_qimage = None
        super().setOpts(*args, **kwargs)

    def setAutoDownsample(self, *args, **kwargs) -> None:  # noqa: N802
        self._prepared_qimage = None
        super().setAutoDownsample(*args, **kwargs)

    def render(self) -> None:
        prepared = self._prepared_qimage
        if prepared is not None and self.image is not None and not self.autoDownsample and self.lut is None:
            self.qimage = prepared
            self._renderRequired = False
            self._unrenderable = False
            return
        super().render()

    def paint(self, p, *args) -> None:
        listener = self.paint_listener
        if listener is None:
            super().paint(p, *args)
            return
        started = time.perf_counter()
        super().paint(p, *args)
        listener(time.perf_counter() - started)
