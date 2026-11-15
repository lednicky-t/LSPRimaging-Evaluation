"""The Image panel's selection-lock layer (2026-11-15): everything outside the lock is dimmed, the lock's border is a solid line.

Display only. `AreaSelectionModule` owns the lock (a pixel map of the displayed image, one level); this draws it:

- **Dimming**: a black layer at `_DIM_ALPHA` over every pixel outside the lock, above the mask / histogram / preview tints and below the
  ROI curves, so what is out of reach looks out of reach while the ROIs stay readable. One `ImageItem`, painted once per lock change
  (`OverlayTint`, like the other tints), never per frame.
- **Border**: the outline of the lock along pixel edges (`region_outline`, 13-18 ms on 1275 x 800), a solid amber cosmetic line (the
  selection's marching ants are black and white and dashed, so the two never look alike). It is what tells a user who forgot the lock
  why the tools do not reach somewhere.

Hidden while Rotate / Crop show the uncropped canvas (the lock is in the displayed, cropped space), and when the lock's size is not the
size of the image on screen (only in the moment between a geometry change and the `reset` that drops the lock). A lock that covers the
whole image dims nothing and draws no border.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import QGraphicsPathItem

from ...selection.area_selection_module import AreaSelectionModule
from .area_selection_tool import region_outline
from .overlay_tint import OverlayTint

DIM_ALPHA = 0.55
BORDER_COLOR = "#f59e0b"  # Tailwind amber-500: not the selection's black / white ants, not the tool blue
_BORDER_WIDTH = 2.0
_BORDER_Z = 59  # just under the selection's ants (60)


class LockLayer:
    def __init__(
        self,
        plot: pg.PlotItem,
        area_selection: AreaSelectionModule,
        hidden: Callable[[], bool],
        image_shape: Callable[[], tuple[int, ...] | None],
    ) -> None:
        self._area_selection = area_selection
        self._hidden = hidden
        self._image_shape = image_shape
        self._dim = OverlayTint(QColor(0, 0, 0), DIM_ALPHA)
        self._dim.attach(plot)  # attached now: it sits under the ROI curves that are added after it
        pen = QPen(QColor(BORDER_COLOR))
        pen.setWidthF(_BORDER_WIDTH)
        pen.setCosmetic(True)  # constant screen width however far the view is zoomed
        self._border = QGraphicsPathItem()
        self._border.setPen(pen)
        self._border.setZValue(_BORDER_Z)
        self._border.setAcceptedMouseButtons(Qt.MouseButton.NoButton)  # purely visual, never eats clicks
        self._border.hide()
        plot.addItem(self._border, ignoreBounds=True)
        self._painted: np.ndarray | None = None  # the lock now on the items (compared by identity: the module's arrays are immutable)
        area_selection.lock_changed.connect(self.refresh)

    @property
    def item(self) -> pg.ImageItem:
        """The dimming image item (tests look at it)."""
        return self._dim.item

    @property
    def border_item(self) -> QGraphicsPathItem:
        return self._border

    def refresh(self) -> None:
        """Draw the lock as it is now; cheap when it is the one already drawn (called from the panel's redraw and whenever the
        active tool changes, as well as on `lock_changed`)."""
        lock = None if self._hidden() else self._area_selection.lock_region()
        shape = self._image_shape()
        if lock is None or shape is None or lock.shape != (int(shape[0]), int(shape[1])) or bool(lock.all()):
            self._dim.hide()
            self._border.hide()
            self._painted = None
            return
        if lock is not self._painted:
            self._dim.paint(~lock)
            self._border.setPath(region_outline(lock))
            self._painted = lock
        self._dim.item.show()
        self._border.show()
