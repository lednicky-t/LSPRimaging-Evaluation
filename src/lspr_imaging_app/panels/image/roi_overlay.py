"""The ROI circles, reference rings, selection highlight and ROI labels drawn on
the Image plot (split out of `panel.py`, 2026-10-07).

`RoiOverlay` owns the pyqtgraph items and the display style (shown/hidden,
colour, transparency of the sample circles and of the reference rings; labels
on/off). It draws what it is handed - the ROIs, their display centres, the
selection, the chromatic affine, the default ring diameters - and reads no
module. The panel decides *when* (a redraw, a style change, a preview tool that
hides the overlay) and wires the ribbon controls and their persistence.

Three curve items for the whole overlay rather than per-ROI items: with NaN
separators between ROIs, one `PlotDataItem` draws any number of disjoint
circles, so adding an ROI costs an array append, not a new `QGraphicsItem`.
That matters because the overlay is redrawn on every selection change. The
geometry itself is `roi/overlay_geometry.py` (vectorized over the ROIs).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ...roi.model import AreaRoi
from ...roi.overlay_geometry import circle_outlines
from ...roi.rasterize import effective_reference_diameters
from .roi_label_overlay import RoiLabelItem, label_text

CIRCLE_POINTS = 48
"""Vertices per drawn circle. 48 is smooth at any zoom a screen can show
while keeping the whole overlay to a few thousand points for a few hundred
ROIs - the overlay is redrawn on every selection change, so its cost is
paid far more often than the image's."""

DEFAULT_SAMPLE_COLOR = "#f59e0b"
DEFAULT_REFERENCE_COLOR = "#38bdf8"
SAMPLE_WIDTH = 1.5
REFERENCE_WIDTH = 1.0
SELECTED_COLOR = "#f8fafc"
MAX_IDLE_SAMPLE_CURVES = 24
"""How many emptied per-colour sample curves are kept for reuse."""


@dataclass
class RoiCircleStyle:
    visible: bool
    color: str  # "#rrggbb"; for the sample circles, the colour of ROIs with none of their own
    alpha: float  # 0..1


def add_curve(plot: pg.PlotItem, color_hex: str, *, width: float, dashed: bool = False) -> pg.PlotDataItem:
    pen = pg.mkPen(QColor(color_hex), width=width)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
    curve = pg.PlotDataItem(pen=pen, connect="finite")
    plot.addItem(curve)
    return curve


def roi_overlay_color(color_hex: str | None, default: str) -> str:
    """The colour a ROI's sample circle is drawn in: its own stored colour, or
    ``default`` for a ROI with none (or one that is not a valid colour)."""
    if color_hex and QColor(color_hex).isValid():
        return color_hex.lower()
    return default


class RoiOverlay:
    def __init__(self, plot: pg.PlotItem) -> None:
        self._plot = plot
        self.sample = RoiCircleStyle(True, DEFAULT_SAMPLE_COLOR, 1.0)
        self.reference = RoiCircleStyle(True, DEFAULT_REFERENCE_COLOR, 1.0)
        self.labels_visible = False
        self.sample_curve = add_curve(plot, DEFAULT_SAMPLE_COLOR, width=SAMPLE_WIDTH)
        # One curve per distinct ROI colour (a group's members each have their
        # own tint); `sample_curve` stays the one for ROIs with no colour of
        # their own. See `_set_sample_curves`.
        self.sample_curves: dict[str, pg.PlotDataItem] = {DEFAULT_SAMPLE_COLOR: self.sample_curve}
        self.reference_curve = add_curve(plot, DEFAULT_REFERENCE_COLOR, width=REFERENCE_WIDTH)
        self.selection_curve = add_curve(plot, SELECTED_COLOR, width=2.5)
        self.label_item: RoiLabelItem | None = None

    def attach_labels(self) -> None:
        """Create the label item. A separate step, called where the panel used to
        create it, so its stacking order among the other overlay items is unchanged."""
        self.label_item = RoiLabelItem(self._plot.vb)
        self._plot.vb.sigRangeChanged.connect(lambda *_args: self.label_item.update())
        self._plot.vb.sigResized.connect(lambda *_args: self.label_item.update())

    # -- style ---------------------------------------------------------------------

    def style(self, kind: str) -> RoiCircleStyle:
        """The style of ``"sample"`` or ``"reference"``."""
        return self.sample if kind == "sample" else self.reference

    def apply_style(self) -> None:
        """Put the style on the curves: colour with its transparency, and shown
        or hidden. A hidden sample overlay hides the selection highlight too
        (they are the same circles). Does not redraw."""
        for color, curve in self.sample_curves.items():
            self._style_curve(curve, color, self.sample.alpha, SAMPLE_WIDTH)
            curve.setVisible(self.sample.visible)
        self.selection_curve.setVisible(self.sample.visible)
        self._style_curve(self.reference_curve, self.reference.color, self.reference.alpha, REFERENCE_WIDTH)
        self.reference_curve.setVisible(self.reference.visible)

    @staticmethod
    def _style_curve(curve: pg.PlotDataItem, color_hex: str, alpha: float, width: float) -> None:
        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        curve.setPen(pg.mkPen(color, width=width))

    # -- drawing -------------------------------------------------------------------

    def clear(self) -> None:
        self._set_sample_curves({})
        self.reference_curve.clear()
        self.selection_curve.clear()
        if self.label_item is not None:
            self.label_item.set_labels([])

    def draw(
        self,
        rois: Sequence[AreaRoi],
        display_centers: np.ndarray,
        selected_ids: Collection[int],
        affine_matrix: np.ndarray,
        default_inner_diameter_px: float,
        default_outer_diameter_px: float,
    ) -> None:
        """Draw every ROI's sample circle, reference rings and (if on) label.

        Everything is vectorized over the ROIs (`roi/overlay_geometry.py`): the
        old per-ROI loop cost ~0.27 ms per ROI per redraw. The circles are
        drawn around each ROI's *display* centre (`display_centers`, N x 2),
        bent by the affine's linear part only - the translation is already in
        the centre."""
        centers = display_centers
        sample_diameters = np.fromiter((roi.sample_diameter_px for roi in rois), dtype=np.float64, count=len(rois))
        is_selected = np.fromiter((roi.area_roi_id in selected_ids for roi in rois), dtype=bool, count=len(rois))

        # One curve per colour. Checking a colour string builds a QColor, so
        # each distinct string is resolved once, not once per ROI.
        resolved: dict[str | None, str] = {}
        indices_by_color: dict[str, list[int]] = {}
        for index, roi in enumerate(rois):
            if is_selected[index]:
                continue  # drawn in the highlight colour below, whatever its own
            key = roi.sample_color_hex
            if key not in resolved:
                resolved[key] = roi_overlay_color(key, self.sample.color)
            indices_by_color.setdefault(resolved[key], []).append(index)
        by_color = {
            color: circle_outlines(centers[indices], sample_diameters[indices], affine_matrix, n_points=CIRCLE_POINTS)
            for color, indices in indices_by_color.items()
        }

        ring_diameters = np.asarray(
            [
                effective_reference_diameters(roi, default_inner_diameter_px, default_outer_diameter_px)
                for roi in rois
            ],
            dtype=np.float64,
        )  # (N, 2): inner, outer
        reference_x, reference_y = circle_outlines(
            np.vstack((centers, centers)),
            np.concatenate((ring_diameters[:, 0], ring_diameters[:, 1])),
            affine_matrix,
            n_points=CIRCLE_POINTS,
        )
        # Selected ROIs are all drawn in the highlight colour, whatever their own, so the selection reads at a glance.
        selection_x, selection_y = circle_outlines(
            centers[is_selected], sample_diameters[is_selected], affine_matrix, n_points=CIRCLE_POINTS
        )
        labels: list[tuple[float, float, float, str]] = []
        if self.labels_visible:
            labels = [
                (float(cx), float(cy), float(roi.sample_diameter_px) / 2.0, label_text(roi.area_roi_id, roi.label))
                for roi, (cx, cy) in zip(rois, centers, strict=True)
            ]

        self._set_sample_curves(by_color)
        self.reference_curve.setData(reference_x, reference_y)
        self.selection_curve.setData(selection_x, selection_y)
        if self.label_item is not None:
            self.label_item.set_labels(labels)

    def _set_sample_curves(self, by_color: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
        """Draw each colour's circles on that colour's curve (one
        `PlotDataItem` per colour, circles joined by NaN separators, the same
        trick the single curve used). A curve is created the first time a
        colour appears and reused after, because making and removing graphics
        items is the expensive part of a redraw. Curves whose colour is no
        longer in use are emptied, and the oldest idle ones removed beyond
        `MAX_IDLE_SAMPLE_CURVES`, so recolouring a large group over and over
        cannot pile up items. The default-colour curve is never removed."""
        for color, (xs, ys) in by_color.items():
            self._sample_curve_for(color).setData(xs, ys)
        idle = [color for color in self.sample_curves if color not in by_color]
        for color in idle:
            self.sample_curves[color].clear()
        removable = [color for color in idle if color != DEFAULT_SAMPLE_COLOR]  # oldest first
        for color in removable[: max(0, len(removable) - MAX_IDLE_SAMPLE_CURVES)]:
            self._plot.removeItem(self.sample_curves.pop(color))

    def _sample_curve_for(self, color_hex: str) -> pg.PlotDataItem:
        curve = self.sample_curves.get(color_hex)
        if curve is None:
            curve = add_curve(self._plot, color_hex, width=SAMPLE_WIDTH)
            self._style_curve(curve, color_hex, self.sample.alpha, SAMPLE_WIDTH)
            curve.setVisible(self.sample.visible)
            # Drawn where the single sample curve used to be: under the
            # reference rings and the selection highlight, whenever it was made.
            curve.stackBefore(self.reference_curve)
            self.sample_curves[color_hex] = curve
        return curve
