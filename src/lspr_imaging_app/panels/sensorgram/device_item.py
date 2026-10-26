"""``DeviceSpaceItem``: the base of the Sensorgram plot's items that are glued to an edge of the plot (the plan bar, the step statistics table).

Their x follows the data (pan and zoom move them) but their y is a place along the top or bottom edge, and their text must not be scaled with the axes, so they paint in
device pixels: `paint` reads the item-to-device transform, resets the painter and draws in pixels. This base holds what both need to do that: the visible data rectangle (the
item's bounding rectangle, once it is shown), the data -> viewport pixel transform, and a repaint when the view moves.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QTransform


class DeviceSpaceItem(pg.GraphicsObject):
    def view_rect(self) -> QRectF:
        """The visible part of the plot in data coordinates (x range, y range); empty without a view."""
        view = self.getViewBox()
        return QRectF() if view is None else QRectF(view.viewRect())

    def _device_transform(self) -> QTransform | None:
        """Data -> viewport pixels, or None when the item is not in a view yet."""
        widget = self.getViewWidget()
        return None if widget is None else self.deviceTransform(widget.viewportTransform())

    def viewTransformChanged(self) -> None:  # noqa: N802 - pyqtgraph naming
        self.prepareGeometryChange()  # the bounding rectangle is the visible region
        self.update()

    viewRangeChanged = viewTransformChanged  # noqa: N815 - both hooks mean the same here
