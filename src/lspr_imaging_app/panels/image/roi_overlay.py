"""The ROI circles, reference rings, selection highlight and ROI labels drawn on
the Image plot (split out of `panel.py`, 2026-10-07).

`RoiOverlay` owns the pyqtgraph items and the display style (shown/hidden,
colour, transparency of the sample circles and of the reference rings; labels
on/off). It draws what it is handed - the ROIs, their display centres, the
selection, the chromatic affine, the default ring diameters - and reads no
module. The panel decides *when* (a redraw, a style change, a preview tool that
hides the overlay) and wires the ribbon controls and their persistence.

A few items for the whole overlay rather than per-ROI items, so adding an ROI costs
an array append, not a new `QGraphicsItem`: the sample circles are ONE painted item
(`SampleCirclesItem`, one colour per circle; it replaced a curve + a fill per distinct
colour, 60-80 ms per redraw at 160 colours), the reference rings and the selection ring
one `PlotDataItem` each (NaN separators between circles). That matters because the
overlay is redrawn on every selection change and, with chromatic correction, on every
wavelength step of the slideshow. The geometry itself is `roi/overlay_geometry.py`
(vectorized over the ROIs).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QPainterPath
from PyQt6.QtWidgets import QGraphicsPathItem

from ...roi.display_style import DEFAULT_REFERENCE_COLOR, RoiCircleStyle, RoiDisplayStyle
from ...roi.model import AreaRoi, AreaRoiGroup
from ...roi.overlay_geometry import circle_outlines, circle_points
from ...roi.rasterize import effective_reference_diameters
from .group_label_overlay import GroupLabelItem, GroupLabelSettings, group_label_entries
from .label_placement import DEFAULT_ROI_PLACEMENT
from .overlay_cache import OverlayLayers
from .roi_label_overlay import DEFAULT_POINT_SIZE, RoiLabelItem, label_text
from .sample_circles_item import SampleCirclesItem

CIRCLE_POINTS = 48
"""Vertices per drawn circle. 48 is smooth at any zoom a screen can show
while keeping the whole overlay to a few thousand points for a few hundred
ROIs - the overlay is redrawn on every selection change, so its cost is
paid far more often than the image's."""

SAMPLE_WIDTH = 1.5
SAMPLE_WIDTH_FAST = 1.0
"""The sample circles' outline width while the slideshow plays with "thin ROI outlines" on."""
REFERENCE_WIDTH = 1.0
SELECTED_COLOR = "#f8fafc"
SELECTED_WIDTH = 3.5


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
    def __init__(self, plot: pg.PlotItem, display_style: RoiDisplayStyle | None = None) -> None:
        self._plot = plot
        # The two layers that hold the overlay items and keep one picture of them per overlay state
        # (`overlay_cache.py`). Created before the items, so they sit in the stack where the items were.
        self.layers = OverlayLayers(plot)
        # The style objects are the shared `RoiDisplayStyle`'s own (one place the colours live), not copies.
        shared = display_style if display_style is not None else RoiDisplayStyle()
        self.sample = shared.sample
        self.reference = shared.reference
        self.labels_visible = False
        self.label_placement = DEFAULT_ROI_PLACEMENT  # where the ROI labels sit around their circles (label_placement.py)
        self.label_point_size = DEFAULT_POINT_SIZE  # the ROI labels' text size, pt
        # Every sample circle, each in its ROI's own colour (a group tints its members), with its
        # translucent fill - one item, see `sample_circles_item.py`.
        self.sample_circles = SampleCirclesItem(SAMPLE_WIDTH)
        plot.addItem(self.sample_circles)
        self.sample_circles.set_style(self.sample.alpha, self.sample.alpha)
        self.sample_circles.setVisible(self.sample.visible)
        self.reference_curve = add_curve(plot, DEFAULT_REFERENCE_COLOR, width=REFERENCE_WIDTH)
        self.reference_fill = add_fill(plot, self.reference_curve)
        self.reference_fill.setPath(QPainterPath())
        self._style_fill(self.reference_fill, DEFAULT_REFERENCE_COLOR, self.reference.alpha)
        self.selection_curve = add_curve(plot, SELECTED_COLOR, width=SELECTED_WIDTH)
        self.label_item: RoiLabelItem | None = None
        self.group_label_item: GroupLabelItem | None = None
        self.group_labels = GroupLabelSettings()
        for item in (self.sample_circles, self.reference_fill, self.reference_curve, self.selection_curve):
            self.layers.adopt_data(item)  # in the stacking order they were created in (the fill sits under its curve)
        self._sample_drawn = False  # does the sample / reference part currently hold geometry? (a hidden part is not computed)
        self._reference_drawn = False

    def attach_labels(self) -> None:
        """Create the label item. A separate step, called where the panel used to
        create it, so its stacking order among the other overlay items is unchanged."""
        self.label_item = RoiLabelItem(self._plot.vb)
        self.label_item.set_placement(self.label_placement)
        self.label_item.set_point_size(self.label_point_size)
        self._plot.vb.sigRangeChanged.connect(lambda *_args: self.label_item.update())
        self._plot.vb.sigResized.connect(lambda *_args: self.label_item.update())
        self.group_label_item = GroupLabelItem(self._plot.vb)
        self.group_label_item.set_settings(self.group_labels)
        self.layers.adopt_screen(self.label_item)
        self.layers.adopt_screen(self.group_label_item)
        self._plot.vb.sigRangeChanged.connect(lambda *_args: self.group_label_item.update())
        self._plot.vb.sigResized.connect(lambda *_args: self.group_label_item.update())

    def set_label_placement(self, placement: str) -> None:
        """Where the ROI labels sit around their circles (one of `label_placement.PLACEMENTS`). Only the label
        item repaints; nothing is redrawn or recomputed."""
        self.layers.invalidate()
        self.label_placement = placement
        if self.label_item is not None:
            self.label_item.set_placement(placement)

    def set_label_point_size(self, points: float) -> None:
        """The ROI labels' text size in pt. Only the label item repaints."""
        self.layers.invalidate()
        self.label_point_size = float(points)
        if self.label_item is not None:
            self.label_item.set_point_size(self.label_point_size)

    def appearance_key(self) -> tuple:
        """Everything about *how* the overlay is drawn (not *what*: that is the ROIs, the selection and the affine): style,
        which parts are shown, line width, label settings. Part of the "unchanged" test in the panel and of the cache epoch."""
        group = self.group_labels
        return (
            self.sample.visible, self.sample.color, self.sample.alpha,
            self.reference.visible, self.reference.color, self.reference.alpha,
            self.labels_visible, self.label_placement, self.label_point_size, self.sample_circles.width,
            group.visible, group.angle, group.position, group.side, group.box_percent, group.min_point_size,
        )

    # -- style ---------------------------------------------------------------------

    def style(self, kind: str) -> RoiCircleStyle:
        """The style of ``"sample"`` or ``"reference"``."""
        return self.sample if kind == "sample" else self.reference

    def set_sample_width(self, width: float) -> None:
        """The sample circles' outline width (screen px). The slideshow's fast mode lowers it while playing."""
        self.layers.invalidate()
        self.sample_circles.set_width(width)

    def apply_style(self) -> None:
        """Put the style on the curves: colour with its transparency, and shown
        or hidden. A hidden sample overlay hides the selection highlight too
        (they are the same circles). Does not redraw."""
        self.layers.invalidate()
        self.sample_circles.set_style(self.sample.alpha, self.sample.alpha)
        self.sample_circles.setVisible(self.sample.visible)
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
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        item.setBrush(QBrush(color))

    # -- drawing -------------------------------------------------------------------

    def clear(self) -> None:
        self.layers.invalidate()
        self._sample_drawn = self._reference_drawn = False
        self.sample_circles.clear()
        self.reference_curve.clear()
        set_fill_outlines(self.reference_fill, np.empty(0), np.empty(0), 0)
        self.selection_curve.clear()
        if self.label_item is not None:
            self.label_item.set_labels([])
        if self.group_label_item is not None:
            self.group_label_item.set_entries([])

    def draw(
        self,
        rois: Sequence[AreaRoi],
        display_centers: np.ndarray,
        selected_ids: Collection[int],
        affine_matrix: np.ndarray,
        default_inner_diameter_px: float,
        default_outer_diameter_px: float,
        groups: Sequence[AreaRoiGroup] = (),
    ) -> None:
        """Draw every ROI's sample circle, reference rings and (if on) label.

        Everything is vectorized over the ROIs (`roi/overlay_geometry.py`): the
        old per-ROI loop cost ~0.27 ms per ROI per redraw. The circles are
        drawn around each ROI's *display* centre (`display_centers`, N x 2),
        bent by the affine's linear part only - the translation is already in
        the centre."""
        centers = display_centers
        sample_diameters = np.fromiter((roi.sample_diameter_px for roi in rois), dtype=np.float64, count=len(rois))

        # A part that is hidden is not computed (testing10, 2026-10-14: with every overlay switched off the overlay still
        # re-computed all its geometry on every wavelength step, ~7 ms). Showing it again goes through `apply_style` + a
        # forced redraw (`ImagePanel._apply_roi_overlay_style`), and the panel's "unchanged" test includes the visibility.
        if self.sample.visible:
            is_selected = np.fromiter((roi.area_roi_id in selected_ids for roi in rois), dtype=bool, count=len(rois))
            # One colour per ROI. Checking a colour string builds a QColor, so each
            # distinct string is resolved once, not once per ROI.
            resolved: dict[str | None, str] = {}
            sample_colors: list[str] = []
            for roi in rois:
                key = roi.sample_color_hex
                if key not in resolved:
                    resolved[key] = roi_overlay_color(key, self.sample.color)
                sample_colors.append(resolved[key])
            sample_x, sample_y = circle_points(centers, sample_diameters, affine_matrix, n_points=CIRCLE_POINTS)
            self.sample_circles.set_circles(sample_x, sample_y, sample_colors)
            # A selected ROI keeps its own colour and fill; the white border drawn on top of it marks the selection.
            selection_x, selection_y = circle_outlines(
                centers[is_selected], sample_diameters[is_selected], affine_matrix, n_points=CIRCLE_POINTS
            )
            self.selection_curve.setData(selection_x, selection_y)
            self._sample_drawn = True
        elif self._sample_drawn:
            self.sample_circles.clear()
            self.selection_curve.clear()
            self._sample_drawn = False

        if self.reference.visible:
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
            self.reference_curve.setData(reference_x, reference_y)
            ring_x, ring_y = annulus_outlines(reference_x, reference_y, len(rois), CIRCLE_POINTS)
            set_fill_outlines(self.reference_fill, ring_x, ring_y, 2 * CIRCLE_POINTS)
            self._reference_drawn = True
        elif self._reference_drawn:
            self.reference_curve.clear()
            set_fill_outlines(self.reference_fill, np.empty(0), np.empty(0), 0)
            self._reference_drawn = False
        labels: list[tuple[float, float, float, str]] = []
        if self.labels_visible:
            labels = [
                (float(cx), float(cy), float(roi.sample_diameter_px) / 2.0, label_text(roi.area_roi_id, roi.label))
                for roi, (cx, cy) in zip(rois, centers, strict=True)
            ]

        if self.label_item is not None:
            self.label_item.set_labels(labels)
        if self.group_label_item is not None:
            self.group_label_item.set_entries(
                group_label_entries(
                    groups, rois, centers, position=self.group_labels.position, default_color=self.sample.color
                )
                if self.group_labels.visible
                else []
            )
