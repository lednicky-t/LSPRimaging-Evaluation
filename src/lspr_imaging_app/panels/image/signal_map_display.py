"""The Image panel's side of the signal map (2026-11-10; numbers and colours: ``selection/signal_map_module.py``, ``analysis/signal_map.py``).

A drawing and nothing else, kept out of ``panel.py`` (its size budget). It is handed a ready-made `SignalMapView` and:

* gives the ROI overlay one colour per ROI instead of the ROIs' own (`colors`; the panel passes them to `RoiOverlay.draw` and puts `revision` in its redraw key,
  so a new map redraws the circles once and an unchanged one costs nothing);
* draws the legend, a colour bar with its range and title in the top-right corner of the image. It is a child of the plot's ViewBox (like the scale bar), so it is
  in a saved or copied figure of the Image plot;
* shows the value of the ROI under the cursor as a tooltip (`hover`, called from the panel's mouse-move handler).

The legend and the hover text each have an on / off switch on the ribbon (`set_legend_visible`, `set_hover_enabled`); neither changes the colours.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QToolTip, QWidget

from ...analysis.signal_map import SignalMapView, fade, scheme

_MARGIN = 12.0
_BAR_W, _BAR_H = 150.0, 10.0
_PAD = 6.0


def _number(value: float, decimals: int) -> str:
    return "–" if not math.isfinite(value) else f"{value:.{decimals}f}"


class SignalMapLegendItem(pg.GraphicsObject):
    """The colour bar: title, gradient, and the values at its ends and middle, on a translucent chip in the top-right corner. Screen pixels (a child of the
    ViewBox): the same size at every zoom.

    It is also a shortcut to the map's settings (2026-11-10). It only says where it was clicked (screen coordinates); the ribbon group owns the menus:

    * left click on the title or the colour bar: `scheme_clicked` (pick a colour scheme);
    * left click on the low or high number: `bound_clicked("low" | "high", ...)` (type a value: the range becomes Fixed);
    * right click anywhere on it: `context_requested` (auto scale, range, colours, what is shown, ...).
    """

    scheme_clicked = pyqtSignal(QPoint)
    bound_clicked = pyqtSignal(str, QPoint)
    context_requested = pyqtSignal(QPoint)

    def __init__(self, view_box: pg.ViewBox) -> None:
        super().__init__()
        self._vb = view_box
        self.setParentItem(view_box)
        self.setZValue(1000)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click: colour scheme. Click a number: type a fixed range. Right-click: more.")
        self._view: SignalMapView | None = None
        self._font = QFont()
        self._font.setPointSizeF(8.5)
        self._title_font = QFont(self._font)
        self._title_font.setBold(True)
        view_box.sigResized.connect(self._on_resized)  # a bound method, not a lambda: the connection ends with the item
        self.setVisible(False)

    def _on_resized(self, *_args: object) -> None:
        self.prepareGeometryChange()  # the bounding rectangle is the ViewBox's
        self.update()

    def set_view(self, view: SignalMapView | None, visible: bool) -> None:
        self.prepareGeometryChange()
        self._view = view
        self.setVisible(bool(visible and view is not None and math.isfinite(view.low)))
        self.update()

    def labels(self) -> tuple[str, str, str, str]:
        """(title, low, middle, high) as drawn (for tests)."""
        view = self._view
        if view is None:
            return "", "", "", ""
        d = view.decimals()
        unit = f" {view.unit}" if view.unit else ""
        title = view.title + (" (out of date)" if view.stale else "")
        return title, _number(view.low, d), _number((view.low + view.high) / 2.0, d), _number(view.high, d) + unit

    def _layout(self) -> dict[str, QRectF]:
        """Where everything is, in the item's (= the ViewBox's) coordinates: chip, title, bar, and the low / high numbers as drawn (for clicks). Empty without a
        map."""
        if self._view is None:
            return {}
        title, low, _mid, high = self.labels()
        metrics, title_metrics = QFontMetricsF(self._font), QFontMetricsF(self._title_font)
        width = max(_BAR_W, title_metrics.horizontalAdvance(title)) + 2 * _PAD
        height = title_metrics.height() + _BAR_H + metrics.height() + 4 * _PAD
        rect = self._vb.boundingRect()
        chip = QRectF(rect.right() - _MARGIN - width, rect.top() + _MARGIN, width, height)
        title_rect = QRectF(chip.left() + _PAD, chip.top() + _PAD, width - 2 * _PAD, title_metrics.height())
        bar = QRectF(chip.left() + (width - _BAR_W) / 2.0, title_rect.bottom() + _PAD, _BAR_W, _BAR_H)
        text_top, text_h = bar.bottom() + _PAD / 2.0, metrics.height()
        low_w, high_w = metrics.horizontalAdvance(low), metrics.horizontalAdvance(high)
        return {
            "chip": chip, "title": title_rect, "bar": bar, "text": QRectF(bar.left(), text_top, _BAR_W, text_h),
            "low": QRectF(bar.left() - _PAD / 2.0, text_top, low_w + _PAD, text_h + _PAD / 2.0),
            "high": QRectF(bar.right() - high_w - _PAD / 2.0, text_top, high_w + _PAD, text_h + _PAD / 2.0),
        }

    def part_at(self, pos: QPointF) -> str | None:
        """What a point (item coordinates) is over: "low", "high", "scheme" (anywhere else on the chip) or None."""
        layout = self._layout()
        if not layout or not layout["chip"].contains(pos):
            return None
        for part in ("low", "high"):
            if layout[part].contains(pos):
                return part
        return "scheme"

    def boundingRect(self) -> QRectF:
        layout = self._layout()
        return QRectF(layout["chip"]) if layout else QRectF()

    def hoverEvent(self, ev: object) -> None:
        if not ev.isExit():  # type: ignore[attr-defined]
            ev.acceptClicks(Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]  # the clicks are ours, not the image's (ROI select)
            ev.acceptClicks(Qt.MouseButton.RightButton)  # type: ignore[attr-defined]

    def mouseClickEvent(self, ev: object) -> None:
        part = self.part_at(ev.pos())  # type: ignore[attr-defined]
        if part is None:
            return
        ev.accept()  # type: ignore[attr-defined]
        where = ev.screenPos().toPoint()  # type: ignore[attr-defined]
        if ev.button() == Qt.MouseButton.RightButton:  # type: ignore[attr-defined]
            self.context_requested.emit(where)
        elif part == "scheme":
            self.scheme_clicked.emit(where)
        else:
            self.bound_clicked.emit(part, where)

    def paint(self, painter: QPainter, *_args: object) -> None:
        view = self._view
        layout = self._layout()
        if view is None or not layout:
            return
        title, low, mid, high = self.labels()
        chip, title_rect, bar, text_rect = layout["chip"], layout["title"], layout["bar"], layout["text"]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(chip, 4.0, 4.0)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.setFont(self._title_font)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        gradient = QLinearGradient(QPointF(bar.left(), 0.0), QPointF(bar.right(), 0.0))
        for position, rgb in scheme(view.scheme).stops:
            r, g, b = (int(c) for c in (fade([rgb])[0] if view.stale else rgb))
            gradient.setColorAt(position, QColor(r, g, b))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.0))
        painter.drawRect(bar)
        painter.setFont(self._font)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft, low)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter, mid)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight, high)


class SignalMapDisplay:
    """See the module docstring. ``redraw()``: the panel's forced ROI-overlay redraw. ``roi_at(x, y)``: the ROI whose sample aperture contains a display point.
    ``in_view(scene_pos)``: the point is over the image (not the axes)."""

    def __init__(
        self, plot: pg.PlotItem, widget: QWidget, redraw: Callable[[], None], roi_at: Callable[[float, float], int | None],
    ) -> None:
        self._plot = plot
        self._widget = widget
        self._redraw = redraw
        self._roi_at = roi_at
        self._view: SignalMapView | None = None
        self._legend_visible = True
        self._hover_enabled = True
        self._hovered: int | None = None
        self.legend = SignalMapLegendItem(plot.vb)

    # -- what the panel reads -------------------------------------------------------------------------------------------------

    def view(self) -> SignalMapView | None:
        return self._view

    def plot(self) -> pg.PlotItem:
        """The Image plot (what the map's figure export draws: the image, the coloured ROIs and the legend)."""
        return self._plot

    def colors(self) -> Mapping[int, str] | None:
        """The colour of every ROI while the map is shown, else None (the ROIs' own colours)."""
        return self._view.colors if self._view is not None else None

    def revision(self) -> int | None:
        return self._view.revision if self._view is not None else None

    def owns_click(self, event: object) -> bool:
        """The click was on the legend (it handled it): the image must not also select or clear ROIs, nor open its own menu."""
        return getattr(event, "currentItem", None) is self.legend and bool(getattr(event, "isAccepted", lambda: False)())

    # -- driven by the owner --------------------------------------------------------------------------------------------------

    def set_view(self, view: SignalMapView | None) -> None:
        if view is self._view:
            return
        self._view = view
        self.legend.set_view(view, self._legend_visible)
        self._hovered = None
        self._redraw()

    def set_legend_visible(self, on: bool) -> None:
        self._legend_visible = bool(on)
        self.legend.set_view(self._view, self._legend_visible)

    def set_hover_enabled(self, on: bool) -> None:
        self._hover_enabled = bool(on)
        if not on and self._hovered is not None:
            self._hovered = None
            QToolTip.hideText()

    # -- the hover text ---------------------------------------------------------------------------------------------------------

    def hover(self, scene_pos: object) -> None:
        """The cursor moved over the plot (scene coordinates): show the value of the ROI under it, once per ROI entered."""
        if self._view is None or not self._hover_enabled:
            return
        vb = self._plot.vb
        roi_id = None
        over_legend = self.legend.isVisible() and self.legend.sceneBoundingRect().contains(scene_pos)  # type: ignore[arg-type]
        if not over_legend and vb.sceneBoundingRect().contains(scene_pos):  # type: ignore[arg-type]
            point = vb.mapSceneToView(scene_pos)
            roi_id = self._roi_at(float(point.x()), float(point.y()))
        if roi_id == self._hovered:
            return
        self._hovered = roi_id
        text = self._view.describe(roi_id) if roi_id is not None else ""
        if text:
            QToolTip.showText(QCursor.pos(), text, self._widget)
        else:
            QToolTip.hideText()
