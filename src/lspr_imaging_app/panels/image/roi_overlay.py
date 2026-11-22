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

from collections.abc import Collection, Mapping, Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QPainterPath
from PyQt6.QtWidgets import QGraphicsPathItem

from ...roi.display_style import DEFAULT_REFERENCE_COLOR, RoiCircleStyle, RoiDisplayStyle
from ...domain.roi_links import is_reference
from ...domain.roi_model import AreaRoi, AreaRoiGroup
from ...domain.roi_shapes import Border, Circle, MaskShape, Rectangle, effective_border, reach_radius, ring_diameters
from ...roi.overlay_geometry import circle_outlines, circle_points, rectangle_points
from .group_label_overlay import GroupLabelItem, GroupLabelSettings, group_label_entries
from .label_placement import DEFAULT_ROI_PLACEMENT
from .overlay_cache import OverlayLayers
from .roi_label_overlay import DEFAULT_POINT_SIZE, RoiLabelItem, label_text
from .sample_circles_item import SampleCirclesItem
from .rectangle_handles_item import RectangleHandlesItem
from .shape_paths_item import MaskOutlines, ShapePathsItem, placed

CIRCLE_POINTS = 48
"""Vertices per drawn circle. 48 is smooth at any zoom a screen can show
while keeping the whole overlay to a few thousand points for a few hundred
ROIs - the overlay is redrawn on every selection change, so its cost is
paid far more often than the image's."""

SAMPLE_WIDTH = 1.5
SAMPLE_WIDTH_FAST = 1.0
"""The sample circles' outline width while the slideshow plays with "thin ROI outlines" on."""
REFERENCE_WIDTH = 1.0
LINK_WIDTH = 1.0
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


def _ring_to_draw(roi: AreaRoi, default_border: Border) -> tuple[float, float]:
    """The (inner, outer) diameters of the ring drawn for a ROI: its border's ring when it is a circle with a border, else (0, 0) (nothing).
    A reference ROI is one region with no border of its own (the scan gives it none), so none is drawn."""
    border = effective_border(roi.border, default_border)
    if isinstance(roi.shape, Circle) and border.enabled and not is_reference(roi):
        return ring_diameters(roi.shape, border)
    return 0.0, 0.0


def _flat_outlines(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(N, P)`` closed rows as the two flat NaN-separated arrays `circle_outlines` returns (no NaN after the last)."""
    if xs.shape[0] == 0:
        return np.empty(0), np.empty(0)
    gap = np.full((xs.shape[0], 1), np.nan)
    return np.hstack((xs, gap)).ravel()[:-1], np.hstack((ys, gap)).ravel()[:-1]


def _degenerate_rows(centers: np.ndarray, points: int) -> tuple[np.ndarray, np.ndarray]:
    """Rows that sit on the centre: what a ROI with nothing to draw in a given part leaves in the arrays (as a circle of diameter 0 does)."""
    return np.repeat(centers[:, 0:1], points, axis=1), np.repeat(centers[:, 1:2], points, axis=1)


def sample_rows(rois: Sequence[AreaRoi], centers: np.ndarray, affine_matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(N, CIRCLE_POINTS)`` closed outline rows of every ROI's own region: circles and rectangles as their outline, a mask as a row on its centre
    (a mask is drawn from its traced pixels, `ShapePathsItem`). Pure."""
    xs, ys = _degenerate_rows(centers, CIRCLE_POINTS)
    circles = [i for i, roi in enumerate(rois) if isinstance(roi.shape, Circle)]
    rectangles = [i for i, roi in enumerate(rois) if isinstance(roi.shape, Rectangle)]
    if circles:
        diameters = np.array([rois[i].shape.diameter_px for i in circles], dtype=np.float64)
        xs[circles], ys[circles] = circle_points(centers[circles], diameters, affine_matrix, n_points=CIRCLE_POINTS)
    if rectangles:
        shapes = [rois[i].shape for i in rectangles]
        xs[rectangles], ys[rectangles] = rectangle_points(
            centers[rectangles], np.array([r.width_px for r in shapes]), np.array([r.height_px for r in shapes]),
            np.array([r.angle_deg for r in shapes]), affine_matrix, n_points=CIRCLE_POINTS,
        )
    return xs, ys


def border_rows(
    rois: Sequence[AreaRoi], centers: np.ndarray, affine_matrix: np.ndarray, default_border: Border
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """``((inner_x, inner_y), (outer_x, outer_y))``, each ``(N, CIRCLE_POINTS)``: the two edges of every ROI's border band. A circle's are its ring; a
    rectangle's are the outlines at distance gap and gap + thickness from its edge (the outer one with rounded corners); a mask, a ROI without a
    border and a reference ROI leave rows on their centre (a mask's band is drawn from its traced pixels). Pure."""
    inner_x, inner_y = _degenerate_rows(centers, CIRCLE_POINTS)
    outer_x, outer_y = inner_x.copy(), inner_y.copy()
    circles, rectangles = [], []
    for i, roi in enumerate(rois):
        if is_reference(roi) or not effective_border(roi.border, default_border).enabled:
            continue
        if isinstance(roi.shape, Circle):
            circles.append(i)
        elif isinstance(roi.shape, Rectangle):
            rectangles.append(i)
    if circles:
        sizes = np.array([ring_diameters(rois[i].shape, effective_border(rois[i].border, default_border)) for i in circles], dtype=np.float64)
        inner_x[circles], inner_y[circles] = circle_points(centers[circles], sizes[:, 0], affine_matrix, n_points=CIRCLE_POINTS)
        outer_x[circles], outer_y[circles] = circle_points(centers[circles], sizes[:, 1], affine_matrix, n_points=CIRCLE_POINTS)
    if rectangles:
        shapes = [rois[i].shape for i in rectangles]
        borders = [effective_border(rois[i].border, default_border) for i in rectangles]
        width, height, angle = (np.array([getattr(r, name) for r in shapes], dtype=np.float64) for name in ("width_px", "height_px", "angle_deg"))
        gaps = np.array([bd.gap_px for bd in borders], dtype=np.float64)
        reaches = gaps + np.array([bd.thickness_px for bd in borders], dtype=np.float64)
        inner_x[rectangles], inner_y[rectangles] = rectangle_points(
            centers[rectangles], width, height, angle, affine_matrix, offset_px=gaps, n_points=CIRCLE_POINTS
        )
        outer_x[rectangles], outer_y[rectangles] = rectangle_points(
            centers[rectangles], width, height, angle, affine_matrix, offset_px=reaches, n_points=CIRCLE_POINTS
        )
    return (inner_x, inner_y), (outer_x, outer_y)


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
        self.sample_circles.setVisible(self._roi_part_visible())
        # Mask ROIs (variable-length outlines) and the border of a mask: painted paths, see `shape_paths_item.py`. Empty for circles and rectangles.
        self._mask_outlines = MaskOutlines()
        self.mask_samples = ShapePathsItem(SAMPLE_WIDTH)
        plot.addItem(self.mask_samples)
        self.mask_samples.set_style(self.sample.alpha, self.sample.alpha)
        self.mask_samples.setVisible(self._roi_part_visible())
        self.reference_curve = add_curve(plot, DEFAULT_REFERENCE_COLOR, width=REFERENCE_WIDTH)
        self.reference_fill = add_fill(plot, self.reference_curve)
        self.reference_fill.setPath(QPainterPath())
        self._style_fill(self.reference_fill, DEFAULT_REFERENCE_COLOR, self.reference.alpha)
        self.mask_borders = ShapePathsItem(REFERENCE_WIDTH)
        plot.addItem(self.mask_borders)
        self.mask_borders.set_style(self.reference.alpha, self.reference.alpha)
        self.mask_borders.setVisible(self.reference.visible)
        # A dashed line from every sample ROI to each reference ROI it is linked to (the ROIs tab's Links switch), in the reference colour.
        self.links_visible = True
        self.link_curve = add_curve(plot, DEFAULT_REFERENCE_COLOR, width=LINK_WIDTH, dashed=True)
        self._style_curve(self.link_curve, self.reference.color, self.reference.alpha, LINK_WIDTH, dashed=True)
        self.selection_curve = add_curve(plot, SELECTED_COLOR, width=SELECTED_WIDTH)
        self.mask_selection = ShapePathsItem(SELECTED_WIDTH, filled=False)
        plot.addItem(self.mask_selection)
        self.mask_selection.setVisible(self._roi_part_visible())
        # The handles of the selected rectangles (drag an edge or corner to resize, the knob to turn): `rectangle_handles_item.py`.
        self.rect_handles = RectangleHandlesItem()
        plot.addItem(self.rect_handles)
        self.rect_handles.setVisible(self._roi_part_visible())
        self.label_item: RoiLabelItem | None = None
        self.group_label_item: GroupLabelItem | None = None
        self.group_labels = GroupLabelSettings()
        for item in (
            self.sample_circles, self.mask_samples, self.reference_fill, self.reference_curve, self.mask_borders, self.link_curve,
            self.selection_curve, self.mask_selection, self.rect_handles,
        ):
            self.layers.adopt_data(item)  # in the stacking order they were created in (the fill sits under its curve)
        self._sample_drawn = False  # does the sample / reference part currently hold geometry? (a hidden part is not computed)
        self._reference_drawn = False

    def _roi_part_visible(self) -> bool:
        """The sample / reference-ROI items are shown when either switch is on; `draw` puts in them only the ROIs whose own switch is on."""
        return bool(self.sample.visible or self.reference.visible)

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
        self.sample_circles.setVisible(self._roi_part_visible())
        self.mask_samples.set_style(self.sample.alpha, self.sample.alpha)
        self.mask_samples.setVisible(self._roi_part_visible())
        self.selection_curve.setVisible(self._roi_part_visible())
        self.mask_selection.setVisible(self._roi_part_visible())
        self.rect_handles.setVisible(self._roi_part_visible())
        self._style_curve(self.reference_curve, self.reference.color, self.reference.alpha, REFERENCE_WIDTH)
        self.reference_curve.setVisible(self.reference.visible)
        self._style_fill(self.reference_fill, self.reference.color, self.reference.alpha)
        self.reference_fill.setVisible(self.reference.visible)
        self.mask_borders.set_style(self.reference.alpha, self.reference.alpha)
        self.mask_borders.setVisible(self.reference.visible)
        self._style_curve(self.link_curve, self.reference.color, self.reference.alpha, LINK_WIDTH, dashed=True)

    @staticmethod
    def _style_curve(curve: pg.PlotDataItem, color_hex: str, alpha: float, width: float, *, dashed: bool = False) -> None:
        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        pen = pg.mkPen(color, width=width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        curve.setPen(pen)

    def _style_fill(self, item: QGraphicsPathItem, color_hex: str, alpha: float) -> None:
        color = QColor(color_hex)
        color.setAlphaF(max(0.0, min(1.0, float(alpha))))
        item.setBrush(QBrush(color))

    # -- drawing -------------------------------------------------------------------

    def _draw_links(self, rois: Sequence[AreaRoi], centers: np.ndarray) -> None:
        """The link lines: from each sample ROI's display centre to the display centre of each reference ROI it is linked to, NaN between lines. Only while
        the Links switch is on and some ROI has links (a session without links draws nothing and costs one pass over the ROIs)."""
        if not self.links_visible or not any(roi.reference_roi_ids for roi in rois):
            self.link_curve.clear()
            return
        index_of = {roi.area_roi_id: i for i, roi in enumerate(rois)}
        xs: list[float] = []
        ys: list[float] = []
        for i, roi in enumerate(rois):
            for reference_id in roi.reference_roi_ids:
                j = index_of.get(reference_id)
                if j is not None:
                    xs += [float(centers[i, 0]), float(centers[j, 0]), np.nan]
                    ys += [float(centers[i, 1]), float(centers[j, 1]), np.nan]
        if xs:
            self.link_curve.setData(np.array(xs[:-1]), np.array(ys[:-1]))
        else:
            self.link_curve.clear()

    def set_links_visible(self, shown: bool) -> None:
        """The Links switch. The caller redraws (it needs the ROIs and their display centres)."""
        self.layers.invalidate()
        self.links_visible = bool(shown)

    def _draw_shapes_sample(
        self, rois: Sequence[AreaRoi], centers: np.ndarray, is_selected: np.ndarray, colors: Sequence[str], affine_matrix: np.ndarray
    ) -> None:
        """The sample part for a ROI list with a rectangle or a mask in it: circles and rectangles as rows in the circles item (a mask has none), each
        mask as its traced pixel outline (`MaskOutlines`, `placed`), the selected ones again, thicker and white."""
        rows_x, rows_y = sample_rows(rois, centers, affine_matrix)
        is_mask = np.array([isinstance(roi.shape, MaskShape) for roi in rois], dtype=bool)
        own = np.flatnonzero(~is_mask)
        self.sample_circles.set_circles(rows_x[own], rows_y[own], [colors[i] for i in own])
        selected_x, selected_y = _flat_outlines(rows_x[own][is_selected[own]], rows_y[own][is_selected[own]])
        self.selection_curve.setData(selected_x, selected_y)
        paths, path_colors, selected_paths = [], [], []
        for i in np.flatnonzero(is_mask):
            roi = rois[i]
            path = placed(self._mask_outlines.sample(roi.shape.mask), affine_matrix)
            paths.append(path)
            path_colors.append(colors[i])
            if is_selected[i]:
                selected_paths.append(path)
        self.mask_samples.set_paths(paths, path_colors)
        self.mask_selection.set_paths(selected_paths, [SELECTED_COLOR] * len(selected_paths))
        self.rect_handles.set_rectangles(
            [(roi.shape, tuple(centers[i])) for i, roi in enumerate(rois) if is_selected[i] and isinstance(roi.shape, Rectangle)],
            np.asarray(affine_matrix, dtype=np.float64)[:2, :2],
        )

    def _draw_shapes_border(
        self, rois: Sequence[AreaRoi], centers: np.ndarray, affine_matrix: np.ndarray, default_border: Border
    ) -> tuple[np.ndarray, np.ndarray]:
        """The border part for a ROI list with a rectangle or a mask in it: the two edge outlines of every circle and rectangle border (the flat arrays
        the reference curve and ring fill take: ``N`` inner rows then ``N`` outer rows), and the traced band of every mask's border."""
        (inner_x, inner_y), (outer_x, outer_y) = border_rows(rois, centers, affine_matrix, default_border)
        paths = []
        for roi in rois:
            border = effective_border(roi.border, default_border)
            if isinstance(roi.shape, MaskShape) and border.enabled and not is_reference(roi):
                path = self._mask_outlines.border(roi.shape.mask, border)
                if not path.isEmpty():
                    paths.append(placed(path, affine_matrix))
        self.mask_borders.set_paths(paths, [self.reference.color] * len(paths))
        return _flat_outlines(np.vstack((inner_x, outer_x)), np.vstack((inner_y, outer_y)))

    def clear(self) -> None:
        self.layers.invalidate()
        self._sample_drawn = self._reference_drawn = False
        self.sample_circles.clear()
        self.mask_samples.clear()
        self.reference_curve.clear()
        set_fill_outlines(self.reference_fill, np.empty(0), np.empty(0), 0)
        self.mask_borders.clear()
        self.link_curve.clear()
        self.selection_curve.clear()
        self.mask_selection.clear()
        self.rect_handles.clear()
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
        default_border: Border,
        groups: Sequence[AreaRoiGroup] = (),
        color_override: Mapping[int, str] | None = None,
    ) -> None:
        """Draw every ROI's sample circle, reference rings and (if on) label.

        `color_override` (the signal map, 2026-11-10): one ``'#rrggbb'`` per ROI id drawn instead of the ROI's own colour (a ROI missing from it keeps its own).
        Display only: the stored colours are not touched.

        Everything is vectorized over the ROIs (`roi/overlay_geometry.py`): the
        old per-ROI loop cost ~0.27 ms per ROI per redraw. The circles are
        drawn around each ROI's *display* centre (`display_centers`, N x 2),
        bent by the affine's linear part only - the translation is already in
        the centre."""
        centers = display_centers
        # Circles only (every session before shapes, and still the usual one): the arrays below, unchanged. A rectangle or a mask in the list sends
        # the sample and reference parts through `_draw_shapes` (phase 2.4, 2026-11-18).
        circles_only = all(isinstance(roi.shape, Circle) for roi in rois)

        # A part that is hidden is not computed (testing10, 2026-10-14: with every overlay switched off the overlay still
        # re-computed all its geometry on every wavelength step, ~7 ms). Showing it again goes through `apply_style` + a
        # forced redraw (`ImagePanel._apply_roi_overlay_style`), and the panel's "unchanged" test includes the visibility.
        # The Sample switch shows the sample ROIs; the Reference switch shows the reference ROIs and the rims (2026-11-22, maintainer request).
        if self.sample.visible and self.reference.visible:
            part_rois, part_centers = rois, centers
        elif self.sample.visible or self.reference.visible:
            keep = np.fromiter(
                (self.reference.visible if is_reference(roi) else self.sample.visible for roi in rois), dtype=bool, count=len(rois)
            )
            part_rois, part_centers = [roi for roi, k in zip(rois, keep, strict=True) if k], centers[keep]
        else:
            part_rois, part_centers = [], centers[:0]
        if part_rois:
            sample_diameters = (
                np.fromiter((roi.shape.diameter_px for roi in part_rois), dtype=np.float64, count=len(part_rois)) if circles_only else np.empty(0)
            )
            is_selected = np.fromiter((roi.area_roi_id in selected_ids for roi in part_rois), dtype=bool, count=len(part_rois))
            # One colour per ROI. Checking a colour string builds a QColor, so each
            # distinct string is resolved once, not once per ROI.
            resolved: dict[str | None, str] = {}
            sample_colors: list[str] = []
            for roi in part_rois:
                key = roi.sample_color_hex
                if color_override is not None:
                    key = color_override.get(roi.area_roi_id, key)
                if key is None and is_reference(roi):  # one meaning for one colour: a reference ROI wears the reference colour
                    key = self.reference.color
                if key not in resolved:
                    resolved[key] = roi_overlay_color(key, self.sample.color)
                sample_colors.append(resolved[key])
            if circles_only:
                sample_x, sample_y = circle_points(part_centers, sample_diameters, affine_matrix, n_points=CIRCLE_POINTS)
                self.sample_circles.set_circles(sample_x, sample_y, sample_colors)
                # A selected ROI keeps its own colour and fill; the white border drawn on top of it marks the selection.
                selection_x, selection_y = circle_outlines(
                    part_centers[is_selected], sample_diameters[is_selected], affine_matrix, n_points=CIRCLE_POINTS
                )
                self.selection_curve.setData(selection_x, selection_y)
                self.mask_samples.clear()
                self.mask_selection.clear()
                self.rect_handles.clear()
            else:
                self._draw_shapes_sample(part_rois, part_centers, is_selected, sample_colors, affine_matrix)
            self._sample_drawn = True
        elif self._sample_drawn:
            self.sample_circles.clear()
            self.mask_samples.clear()
            self.selection_curve.clear()
            self.mask_selection.clear()
            self.rect_handles.clear()
            self._sample_drawn = False

        if self.reference.visible:
            if circles_only:
                ring_sizes = np.asarray(
                    [_ring_to_draw(roi, default_border) for roi in rois], dtype=np.float64
                ).reshape(-1, 2)  # (N, 2): inner, outer diameters; (0, 0) for a ROI with no ring to draw
                reference_x, reference_y = circle_outlines(
                    np.vstack((centers, centers)),
                    np.concatenate((ring_sizes[:, 0], ring_sizes[:, 1])),
                    affine_matrix,
                    n_points=CIRCLE_POINTS,
                )
                self.mask_borders.clear()
            else:
                reference_x, reference_y = self._draw_shapes_border(rois, centers, affine_matrix, default_border)
            self.reference_curve.setData(reference_x, reference_y)
            ring_x, ring_y = annulus_outlines(reference_x, reference_y, len(rois), CIRCLE_POINTS)
            set_fill_outlines(self.reference_fill, ring_x, ring_y, 2 * CIRCLE_POINTS)
            self._reference_drawn = True
        elif self._reference_drawn:
            self.reference_curve.clear()
            self.mask_borders.clear()
            set_fill_outlines(self.reference_fill, np.empty(0), np.empty(0), 0)
            self._reference_drawn = False
        self._draw_links(rois, centers)
        labels: list[tuple[float, float, float, str]] = []
        if self.labels_visible:
            labels = [
                (
                    float(cx), float(cy), reach_radius(roi.shape, roi.center_x, roi.center_y),
                    label_text(roi.area_roi_id, roi.label, reference=is_reference(roi)),
                )
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
