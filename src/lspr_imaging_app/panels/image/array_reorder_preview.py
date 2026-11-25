"""Live preview of the Reorder button (2026-11-25): while its pick-up is open, the image shows the numbers the ROIs would get.

The ROIs in scope (the selected ones, all if none is selected) are labelled with their **new** numbers in the accent colour, in the place of their normal labels (those are
muted for as long as the preview is up), and also when the ROI labels are switched off. Every click on the numbering picker redraws it; closing the pick-up, by the tick
or by clicking away, clears it. Nothing is renumbered here: the numbers come from the same plan the tick carries out (`array_reorder.plan_reorder`), so what the preview
shows is what the tick makes.

A second `RoiLabelItem`, a child of the plot's view box like the real one (it follows pan and zoom by itself); it takes the real labels' place and size. Cost: one pass
over the ROIs in scope per picker click (a few ms for thousands), so it runs on the GUI thread; no fit or heavy numpy.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QColor

from lspr_ui import get_active_theme

from ...domain.roi_links import is_reference
from ...domain.roi_shapes import reach_radius
from ...roi import RoiToolbox
from ...roi.ordering import renumbering
from ...selection import SelectionModule
from .array_reorder import ArrayReorderButton, plan_reorder
from .roi_label_overlay import RoiLabelItem, label_text
from .roi_overlay import RoiOverlay

PREVIEW_Z = 901
"""Just above the real labels (900), which are muted where this draws."""


class ArrayReorderPreview(QObject):
    def __init__(
        self,
        *,
        plot: pg.PlotItem,
        overlay: RoiOverlay,
        toolbox: RoiToolbox,
        selection: SelectionModule,
        view_centers: Callable[[], np.ndarray],
        button: ArrayReorderButton,
        parent: QObject | None = None,
    ) -> None:
        """``view_centers()``: where every ROI's centre is on the canvas now, in ``toolbox.rois()`` order (`RoiQueries.view_centers` for the frame on screen)."""
        super().__init__(parent)
        self._overlay, self._toolbox, self._selection, self._view_centers, self._button = overlay, toolbox, selection, view_centers, button
        self._item = RoiLabelItem(plot.vb)
        self._item.setZValue(PREVIEW_Z)
        plot.vb.sigRangeChanged.connect(lambda *_args: self._item.update())
        plot.vb.sigResized.connect(lambda *_args: self._item.update())
        self._open = False
        button.popup_opened.connect(self._on_opened)
        button.popup_closed.connect(self.clear)
        button.choice_changed.connect(lambda *_args: self.refresh())
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self._item.set_text_color(QColor(get_active_theme().accent_blue))

    def is_showing(self) -> bool:
        return bool(self._item.labels())

    def labels(self) -> list[tuple[float, float, float, str]]:
        """The preview's labels ``(x, y, radius, text)`` (tests)."""
        return self._item.labels()

    def _on_opened(self) -> None:
        self._open = True
        self.refresh()

    def clear(self) -> None:
        """Remove the preview and bring the real labels back."""
        self._open = False
        self._item.set_labels([])
        if self._overlay.label_item is not None:
            self._overlay.label_item.set_muted(frozenset())

    def refresh(self) -> None:
        """Draw the numbers the picked numbering would give (nothing while the pick-up is closed or fewer than two ROIs are in scope)."""
        if not self._open:
            return
        self.refresh_theme()  # (cheap; keeps the colour right after a theme switch without a hook of its own)
        order = plan_reorder(self._toolbox, self._selection, *self._button.choice())
        if order is None:
            self.clear()
            self._open = True  # (the pick-up is still open: a later pick may have something to show)
            return
        new_id = renumbering(order, sorted(order))
        rois = self._toolbox.rois_at(int(self._selection.current_cube()))
        row_of = {roi.area_roi_id: row for row, roi in enumerate(self._toolbox.rois())}  # `view_centers` has one row per ROI, in this order
        centers = self._view_centers()
        entries: list[tuple[float, float, float, str]] = []
        muted: set[int] = set()
        for index, roi in enumerate(rois):
            number = new_id.get(roi.area_roi_id)
            if number is None:
                continue
            cx, cy = centers[row_of[roi.area_roi_id]]
            entries.append((float(cx), float(cy), reach_radius(roi.shape, roi.center_x, roi.center_y), label_text(number, roi.label, reference=is_reference(roi))))
            muted.add(index)
        self._item.set_placement(self._overlay.label_placement)
        self._item.set_point_size(self._overlay.label_point_size)
        self._item.set_labels(entries)
        if self._overlay.label_item is not None:
            self._overlay.label_item.set_muted(frozenset(muted))
