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
from PyQt6.QtGui import QBrush, QColor, QPainterPath
from PyQt6.QtWidgets import QGraphicsPathItem

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
SELECTED_WIDTH = 3.5
MAX_IDLE_SAMPLE_CURVES = 24
"""How many emptied per-colour sample curves are kept for reuse."""
DEFAULT_FILL_MAX_OPACITY = 1.0
"""Opacity of a ROI fill when its Transparency slider is at 100 %. The slider
scales between 0 and this value; the maximum is an Options-menu preference."""


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


def add_fill(plot: pg.PlotItem, below: pg.PlotDataItem) -> QGraphicsPathItem:
    """An empty filled-polygon item, stacked under the outline curve ``below``.
    (A `PlotDataItem` can only fill down to a baseline, not inside a closed
    shape, so the fill is a plain path item.)"""
    item = QGraphicsPathItem()
    item.setPen(pg.mkPen(None))
    plot.addItem(item)
    item.stackBefore(below)
    return item


def set_fill_outlines(item: QGraphicsPathItem, xs: np.ndarray, ys: np.ndarray, shape_points: int) -> None:
    """Fill the shapes in ``xs``/``ys`` (the arrays the outline curve gets):
    ``shape_points`` vertices each, one NaN between shapes. One `QPolygonF` per
    shape, filled straight from the array - pyqtgraph's `arrayToQPath` splits
    the arrays in Python and took ~17 ms per call at 800 ROIs, which made a
    ROI drag choppy."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    if len(xs):
        stride = shape_points + 1
        shapes_x = np.append(xs, np.nan).reshape(-1, stride)[:, :shape_points]
        shapes_y = np.append(ys, np.nan).reshape(-1, stride)[:, :shape_points]
        for row_x, row_y in zip(shapes_x, shapes_y, strict=True):
            polygon = pg.functions.create_qpolygonf(shape_points)
            vertices = pg.functions.ndarray_from_qpolygonf(polygon)
            vertices[:, 0] = row_x
            vertices[:, 1] = row_y
            path.addPolygon(polygon)  # an open polygon fills as if closed
    item.setPath(path)


def annulus_outlines(xs: np.ndarray, ys: np.ndarray, count: int, n_points: int) -> tuple[np.ndarray, np.ndarray]:
    """Turn the reference outlines (``count`` inner circles, then ``count`` outer
    ones, NaN-separated) into one ring per ROI: the outer circle, then the inner
    circle walked backwards, as a single subpath. Filled with Qt's winding rule,
    the inner opening stays empty and neighbouring rings overlap without
    punching holes in each other."""
    if count == 0:
        return np.empty(0), np.empty(0)
    stride = n_points + 1

    def circles(values: np.ndarray) -> np.ndarray:
        return np.append(values, np.nan).reshape(2 * count, stride)[:, :n_points]

    out = []
    for values in (xs, ys):
        c = circles(values)
        inner, outer = c[:count], c[count:]
        ring = np.full((count, 2 * n_points + 1), np.nan)  # last slot: separator
        ring[:, :n_points] = outer
        ring[:, n_points : 2 * n_points] = inner[:, ::-1]
        out.append(ring.ravel()[:-1])
    return out[0], out[1]


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
        self.fill_max_opacity = DEFAULT_FILL_MAX_OPACITY
        self.sample_curve = add_curve(plot, DEFAULT_SAMPLE_COLOR, width=SAMPLE_WIDTH)
        # One curve per distinct ROI colour (a group's members each have their
        # own tint); `sample_curve` stays the one for ROIs with no colour of
        # their own. See `_set_sample_curves`.
        self.sample_curves: dict[str, pg.PlotDataItem] = {DEFAULT_SAMPLE_COLOR: self.sample_curve}
        self.reference_curve = add_curve(plot, DEFAULT_REFERENCE_COLOR, width=REFERENCE_WIDTH)
        # The translucent fill inside each sample circle, one item per colour, parallel to `sample_curves`.
        self.sample_fills: dict[str, QGraphicsPathItem] = {
            DEFAULT_SAMPLE_COLOR: add_fill(plot, self.sample_curve)
        }
        self._style_fill(self.sample_fills[DEFAULT_SAMPLE_COLOR], DEFAULT_SAMPLE_COLOR, self.sample.alpha)
        self.reference_fill = add_fill(plot, self.reference_curve)
        self.reference_fill.setPath(QPainterPath())
        self._style_fill(self.reference_fill, DEFAULT_REFERENCE_COLOR, self.reference.alpha)
        self.selection_curve = add_curve(plot, SELECTED_COLOR, width=SELECTED_WIDTH)
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
            self._style_fill(self.sample_fills[color], color, self.sample.alpha)
            self.sample_fills[color].setVisible(self.sample.visible)
        self.selection_curve.setVisible(self.sample.visible)
        self._style_curve(self.reference_curve, self.reference.color, self.reference.alpha, REFERENCE_WIDTH)
        self.reference_curve.setVisible(self.reference.visible)
        self._style_fill(self.reference_fill, self.reference.color, self.reference.alpha)
        self.reference_fill.setVisible(self.reference.visible)

    @staticmethod
    def _style_curve(curve: pg.PlotDataItem, color_hex: str, alpha: float, width: float) -> None:
        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        curve.setPen(pg.mkPen(color, width=width))

    def _style_fill(self, item: QGraphicsPathItem, color_hex: str, alpha: float) -> None:
        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, float(alpha))) * max(0.0, min(1.0, self.fill_max_opacity)))
        item.setBrush(QBrush(color))

    # -- drawing -------------------------------------------------------------------

    def clear(self) -> None:
        self._set_sample_curves({})
        self.reference_curve.clear()
        set_fill_outlines(self.reference_fill, np.empty(0), np.empty(0), 0)
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
        # A selected ROI keeps its own colour and fill; the white border drawn on top of it marks the selection.
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
        ring_x, ring_y = annulus_outlines(reference_x, reference_y, len(rois), CIRCLE_POINTS)
        set_fill_outlines(self.reference_fill, ring_x, ring_y, 2 * CIRCLE_POINTS)
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
            set_fill_outlines(self.sample_fills[color], xs, ys, CIRCLE_POINTS)
        idle = [color for color in self.sample_curves if color not in by_color]
        for color in idle:
            self.sample_curves[color].clear()
            set_fill_outlines(self.sample_fills[color], np.empty(0), np.empty(0), 0)
        removable = [color for color in idle if color != DEFAULT_SAMPLE_COLOR]  # oldest first
        for color in removable[: max(0, len(removable) - MAX_IDLE_SAMPLE_CURVES)]:
            self._plot.removeItem(self.sample_curves.pop(color))
            self._plot.removeItem(self.sample_fills.pop(color))

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
            fill = add_fill(self._plot, curve)
            self._style_fill(fill, color_hex, self.sample.alpha)
            fill.setVisible(self.sample.visible)
            self.sample_fills[color_hex] = fill
        return curve
