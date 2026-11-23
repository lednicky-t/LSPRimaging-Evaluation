"""The small label in the image's top-left corner that says what the area selection amounts to (2026-11-23): the share of the image it covers,
the number of blobs and their mean diameter (`selection_stats.py` has the maths and the wording).

Two parts, like the scale bar (`scale_bar_overlay.py`):

- `SelectionStatsItem`: one painted item, a child of the plot's `ViewBox`, so it is drawn in screen pixels and keeps its size while zooming.
- `SelectionStatsOverlay`: follows the selection, calculates the numbers **off the GUI thread** (a plain `threading.Thread`; the work is the
  connected-component pass of OpenCV on a 1-12 MP map; the newest request wins, an older answer is dropped) and keeps the old label until the new
  numbers are ready. The unit follows the display unit of the geometry (px / µm), as the scale bar does: switching it re-words the label, nothing
  is recalculated.

Display only: nothing here is stored, so there is nothing to remember and nothing that can make a number stale.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen

from ...image_tools import GeometryModule
from ...selection import AreaSelectionModule
from .selection_stats import SelectionStats, format_stats, selection_stats

logger = logging.getLogger(__name__)


class SelectionStatsItem(pg.GraphicsObject):
    """The two-line label. `set_lines(None)` hides it."""

    _MARGIN = 12.0  # px from the view's top-left corner (moved from the top-right 2026-11-23, maintainer)
    _PAD_X = 8.0
    _PAD_Y = 4.0

    def __init__(self, view_box: pg.ViewBox) -> None:
        super().__init__()
        self._vb = view_box
        self.setParentItem(view_box)
        self.setZValue(1000)
        self._lines: tuple[str, ...] = ()
        self._font = QFont()
        self._font.setPointSizeF(9.0)
        self._font.setBold(True)
        self.setVisible(False)
        view_box.sigResized.connect(self._on_resized)

    def lines(self) -> tuple[str, ...]:
        return self._lines

    def set_lines(self, lines: tuple[str, ...] | None) -> None:
        new = tuple(lines) if lines else ()
        if new == self._lines and self.isVisible() == bool(new):
            return
        self.prepareGeometryChange()
        self._lines = new
        self.setVisible(bool(new))
        self.update()

    def _on_resized(self, *_args: object) -> None:
        if self._lines:
            self.prepareGeometryChange()
            self.update()

    def _chip(self) -> QRectF:
        metrics = QFontMetricsF(self._font)
        width = max((metrics.horizontalAdvance(line) for line in self._lines), default=0.0) + 2 * self._PAD_X
        height = metrics.height() * len(self._lines) + 2 * self._PAD_Y
        rect = self._vb.boundingRect()
        return QRectF(rect.left() + self._MARGIN, rect.top() + self._MARGIN, width, height)

    def boundingRect(self) -> QRectF:
        return self._chip() if self._lines else QRectF()

    def paint(self, painter: QPainter, *_args: object) -> None:
        if not self._lines:
            return
        chip = self._chip()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))  # dark, see-through: readable on a bright or a dark frame alike
        painter.drawRoundedRect(chip, 4.0, 4.0)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.setFont(self._font)
        painter.drawText(
            chip.adjusted(self._PAD_X, self._PAD_Y, -self._PAD_X, -self._PAD_Y),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            "\n".join(self._lines),
        )


class SelectionStatsOverlay(QObject):
    _ready = pyqtSignal(int, object)  # request number, SelectionStats (from the worker thread; delivered on the GUI thread)

    def __init__(
        self,
        view_box: pg.ViewBox,
        selection: AreaSelectionModule,
        geometry: GeometryModule,
        image_shape: Callable[[], tuple[int, int] | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = SelectionStatsItem(view_box)
        self._selection = selection
        self._geometry = geometry
        self._image_shape = image_shape
        self._stats: SelectionStats | None = None  # what the label shows now
        self._request = 0  # the newest request; an answer with another number is dropped
        self._running = False
        self._waiting = None  # the mask of a request made while a thread was busy (only the newest is kept)
        self._ready.connect(self._on_ready)
        selection.selection_changed.connect(self.refresh)
        geometry.cosmetic_changed.connect(lambda _change: self._show())  # px <-> µm: re-word only

    def item(self) -> SelectionStatsItem:
        return self._item

    def refresh(self, *_args: object) -> None:
        """The selection changed (or the image did): ask for new numbers. With nothing selected the label goes at once."""
        shape = self._image_shape()
        mask = self._selection.selected_mask(shape) if shape is not None and self._selection.has_selection() else None
        self._request += 1
        if mask is None:
            self._waiting = None
            self._stats = None
            self._show()
            return
        if self._running:
            self._waiting = (self._request, mask)  # started when the running thread is done; the old label stays meanwhile
            return
        self._start(self._request, mask)

    def _start(self, request: int, mask) -> None:
        self._running = True

        def work() -> None:
            try:
                result: SelectionStats | None = selection_stats(mask)
            except Exception:  # nothing may escape a thread silently: it is logged and the label stays as it was
                logger.exception("selection stats failed")
                result = None
            self._ready.emit(request, result)

        threading.Thread(target=work, name="selection-stats", daemon=True).start()

    def _on_ready(self, request: int, stats: object) -> None:
        self._running = False
        waiting, self._waiting = self._waiting, None
        if waiting is not None:
            self._start(*waiting)  # a newer request was made meanwhile: this answer is already out of date
            return
        if request == self._request and isinstance(stats, SelectionStats):
            self._stats = stats
            self._show()

    def _show(self) -> None:
        if self._stats is None:
            self._item.set_lines(None)
            return
        settings = self._geometry.settings()
        um_per_px = (
            self._geometry.microns_per_pixel_scalar()
            if settings.display_units == "um" and self._geometry.can_display_micrometers()
            else None
        )
        self._item.set_lines(format_stats(self._stats, um_per_px))
