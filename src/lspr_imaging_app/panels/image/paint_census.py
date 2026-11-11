"""Which widgets repaint while the slideshow plays, and how much (diagnostic, 2026-11-11).

After the Play fixes of 2026-11-11 the GUI thread still spent ~65 ms per frame inside Qt itself (py-spy: no Python frame),
outside the Image view. That is widgets repainting: Qt paints a widget's whole dirty region, with its style sheet, and a label
whose text changes can make a layout repaint a whole dock. Python profilers can't see which widgets, so this counts them.

While Play runs, an application-wide event filter counts, per widget, the paint events (and the pixels in their regions)
and the layout requests. Once per playback window the panel logs the biggest as one `playback.paints` DEBUG record.
It only counts and returns False (the event goes on unchanged), and it is installed only during Play.
"""

from __future__ import annotations

from collections import Counter

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QPainterPath, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

_PAINT = QEvent.Type.Paint
_LAYOUT = QEvent.Type.LayoutRequest
_TOP = 12  # widgets reported per window
_UPDATE_REQUEST = QEvent.Type.UpdateRequest


def _region_area(region: QRegion) -> int:
    """Pixels in `region` (the sum of its rectangles; PyQt6 cannot list them directly, a painter path splits it into them)."""
    if region.rectCount() <= 1:
        rect = region.boundingRect()
        return rect.width() * rect.height()
    path = QPainterPath()
    path.addRegion(region)
    return int(sum(polygon.boundingRect().width() * polygon.boundingRect().height() for polygon in path.toFillPolygons()))


def _describe(widget: QWidget) -> str:
    """`Class#name < Parent < Grandparent`: enough to find the widget in the code."""
    parts = []
    item: QObject | None = widget
    for _ in range(4):
        if item is None:
            break
        name = type(item).__name__
        if item.objectName():
            name += f"#{item.objectName()}"
        parts.append(name)
        item = item.parent()
    return " < ".join(parts)


class PaintCensus(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._passes: Counter[str] = Counter()  # which widgets were painted together in one pass, and how often
        self._pass: set[str] = set()
        self._pass_open = False
        self._paints: Counter[str] = Counter()
        self._pixels: Counter[str] = Counter()
        self._layouts: Counter[str] = Counter()
        self._names: dict[int, str] = {}
        self._installed = False

    def start(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._installed:
            app.installEventFilter(self)
            self._installed = True
        self._reset()

    def stop(self) -> None:
        app = QApplication.instance()
        if app is not None and self._installed:
            app.removeEventFilter(self)
        self._installed = False
        self._reset()

    def _close_pass(self) -> None:
        if self._pass_open:
            self._passes[" + ".join(sorted(self._pass)) or "(nothing painted)"] += 1
        self._pass = set()
        self._pass_open = False

    def _reset(self) -> None:
        self._pass = set()
        self._pass_open = False
        self._passes.clear()
        self._paints.clear()
        self._pixels.clear()
        self._layouts.clear()
        self._names.clear()  # ids can be reused by new widgets: forget them every window

    def _name(self, widget: QWidget) -> str:
        key = id(widget)
        name = self._names.get(key)
        if name is None:
            name = self._names[key] = _describe(widget)
        return name

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == _UPDATE_REQUEST and isinstance(watched, QWidget) and watched.isWindow():
            self._close_pass()  # Qt repaints and hands a window to the screen once per UpdateRequest: a new pass
            self._pass_open = True
        elif kind == _PAINT and isinstance(watched, QWidget):
            name = self._name(watched)
            self._pass.add(name.split(" < ")[0])
            # The region's own rectangles, not its bounding box: a title bar at the top and a slider bar at the bottom repainted together
            # have a bounding box as big as the whole dock (first census readings, 2026-11-11, were inflated that way).
            self._paints[name] += 1
            self._pixels[name] += _region_area(event.region())
        elif kind == _LAYOUT and isinstance(watched, QWidget):
            self._layouts[self._name(watched)] += 1
        return False

    def window_fields(self, frames: int) -> dict[str, object]:
        """The window's biggest repaints (per shown frame) as log fields, then start a new window."""
        n = max(frames, 1)
        self._close_pass()
        passes = [f"{count / n:.1f}/frame: {widgets}" for widgets, count in self._passes.most_common(4)]
        total_px = sum(self._pixels.values())
        top = [
            f"{name} | paints/frame {self._paints[name] / n:.1f} | kpx/frame {self._pixels[name] / n / 1000:.0f}"
            for name, _px in self._pixels.most_common(_TOP)
        ]
        layouts = [f"{name} | {count / n:.1f}/frame" for name, count in self._layouts.most_common(6)]
        fields: dict[str, object] = {
            "frames": frames,
            "paints_per_frame": round(sum(self._paints.values()) / n, 1),
            "kpx_per_frame": round(total_px / n / 1000),
            "layouts_per_frame": round(sum(self._layouts.values()) / n, 1),
            "passes_per_frame": round(sum(self._passes.values()) / n, 1),
            "passes": " || ".join(passes),
            "top": " || ".join(top),
            "layouts": " || ".join(layouts),
        }
        self._reset()
        return fields
