"""Sensorgram plot widget - the pyqtgraph-facing half of the Sensorgram panel (sketch §10 "sensorgram/panel.py + plot.py").

Draws what it is handed: a line per series (the metric against time or cube number), a band for averaged series, hollow markers where the
scan is out of date, mask marks (a triangle: a persistent mask touched the point's ROIs, a circle: an individual one did), a vertical line at the current cube and an optional draggable baseline selector (a band along the bottom tenth of the plot, with its own colour and fill opacity). It computes nothing and reads no module.
A double-click on the y axis label emits ``y_label_double_clicked`` (the panel turns it into "absolute position / shift from the baseline"); one on the x axis label emits ``x_label_double_clicked`` (time <-> cube number, app-wide).
A left click inside the plot emits ``x_clicked`` with the x value (the panel turns it into "go to that cube"); dragging the circle on the x axis
(the Cube slider's handle, in the same colour) emits it too, for every pointer position.
The experiment steps (``plan_bar.PlanBarItem``) sit behind everything; `set_plan` hands them over and makes room for the bar by widening the y range (``plan/bar_model.py``).
The bar's pointer gestures (drag a boundary, double-click a step, right-click a step) come out as the ``plan_*`` signals; ``plan_text_editor.StepTextEditor`` is the field that opens
over a step (`open_plan_text_editor`). The plot decides nothing about them: ``plan_actions.PlanBarActions`` turns them into commands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetricsF
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.scan_trace import Series
from ...plan.bar_model import ALIGN_DATA, POSITION_TOP, PlanBarSettings, PlanBarStep, label_headroom_px, reserved_range
from ..axis_cursor import AxisCursor
from ..image.mark_colors import MASK_COLOR, cube_color
from .baseline_selector_menu import DEFAULT_ALPHA, DEFAULT_COLOR, BaselineRangeReadout
from .plan_bar import PlanBarItem
from .plan_text_editor import StepTextEditor

MAX_LEGEND_SERIES = 6
_BAND_ALPHA = 55
BASELINE_BAND_SPAN = (0.0, 0.1)  # the baseline selector fills the bottom tenth of the plot, not its full height
_WINDOW_EDGE_ALPHA = 160
_HOVER_LIFT = 0.08  # how much more opaque the fill gets under the pointer (pyqtgraph's own jump was far larger than the resting fill)
_INVISIBLE_PEN = pg.mkPen(0, 0, 0, 0)  # a curve with no pen at all is left out of the auto-range; one with a fully transparent pen is not
_LABEL_HIT_MARGIN = 4.0  # pixels around the y axis label that still count as a double-click on it
_READOUT_GAP = 0  # pixels between the bottom edge of the "Baseline [start, end]" box and the x axis line (the plot area's bottom edge); it was 2


def snap_to_grid(grid: np.ndarray, value: float) -> float:
    """The point of ``grid`` (sorted, finite, may be empty) nearest to ``value``; halfway between two points the higher one. An empty grid leaves the value alone."""
    if grid.size == 0:
        return float(value)
    above = int(np.searchsorted(grid, value))
    if above == 0:
        return float(grid[0])
    if above >= grid.size:
        return float(grid[-1])
    below_gap, above_gap = value - grid[above - 1], grid[above] - value
    return float(grid[above] if above_gap <= below_gap else grid[above - 1])


def snap_window(grid: np.ndarray, low: float, high: float) -> tuple[float, float]:
    """Both edges of a window on the grid, in order. Two edges that land on one point are pulled one point apart (a baseline of a single point is no window)."""
    first, second = sorted((snap_to_grid(grid, low), snap_to_grid(grid, high)))
    if first == second and grid.size >= 2:
        index = int(np.searchsorted(grid, first))
        if index + 1 < grid.size:
            second = float(grid[index + 1])
        else:
            first = float(grid[index - 1])
    return first, second


def _finite_runs(*arrays: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) of the stretches where every array is finite: a band is drawn per stretch (a gap stays a gap)."""
    ok = np.ones(len(arrays[0]), dtype=bool)
    for a in arrays:
        ok &= np.isfinite(a)
    runs, start = [], None
    for i, flag in enumerate(ok):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(ok)))
    return [(a, b) for a, b in runs if b - a >= 2]


class _MiddlePanViewBox(pg.ViewBox):
    """pyqtgraph pans with the left button by default; here only the middle button pans (the left one is left free for clicking a cube)."""

    def mouseDragEvent(self, ev, axis=None):  # noqa: N802 - pyqtgraph naming
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis)

    claim_right_click = None  # set by the plot: a function (scene position) -> bool, True where the plan bar wants the right-click

    def mouseClickEvent(self, ev):  # noqa: N802 - pyqtgraph naming
        """pyqtgraph hands a right-click to the ViewBox before any item and the ViewBox accepts it (its own menu). Over a step of the plan bar the click is declined
        here, so that the bar (and its menu) gets it."""
        if ev.button() == Qt.MouseButton.RightButton and self.claim_right_click is not None and self.claim_right_click(ev.scenePos()):
            ev.ignore()
            return
        super().mouseClickEvent(ev)


class SensorgramPlot(QWidget):
    x_clicked = pyqtSignal(float)
    baseline_window_changed = pyqtSignal(float, float)
    baseline_range_edited = pyqtSignal(float, float)  # start / end typed into the fields above the selector (sorted)
    y_label_double_clicked = pyqtSignal()
    x_label_double_clicked = pyqtSignal()
    plan_edge_drag_started = pyqtSignal(int, str)  # the plan bar's gestures, passed on: (step index, "start" | "end")
    plan_edge_dragged = pyqtSignal(int, str, float, bool)  # ... with the data x under the pointer, and whether Shift is held
    plan_edge_drag_finished = pyqtSignal()
    plan_text_edit_requested = pyqtSignal(int)
    plan_menu_requested = pyqtSignal(int, QPoint)
    plan_text_committed = pyqtSignal(int, str)  # (step index, new text) from the in-place editor

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._widget = pg.PlotWidget(parent=self, viewBox=_MiddlePanViewBox())
        self._widget.setMinimumHeight(100)
        self._item = self._widget.getPlotItem()
        self._item.showGrid(x=True, y=True, alpha=0.15)
        self._legend: pg.LegendItem | None = None
        self._items: list[pg.GraphicsObject] = []
        self._axis_cursor = AxisCursor(self._item, cube_color(), self)
        self._axis_cursor.dragged.connect(self.x_clicked)
        self._cursor = self._axis_cursor.line
        self._plan_bar = PlanBarItem()
        self._item.addItem(self._plan_bar, ignoreBounds=True)  # behind the traces; not part of the auto-range
        self._item.getViewBox().claim_right_click = self._plan_bar.claims_scene_pos
        self._plan_bar.edge_drag_started.connect(self.plan_edge_drag_started)
        self._plan_bar.edge_dragged.connect(self.plan_edge_dragged)
        self._plan_bar.edge_drag_finished.connect(self.plan_edge_drag_finished)
        self._plan_bar.text_edit_requested.connect(self.plan_text_edit_requested)
        self._plan_bar.menu_requested.connect(self.plan_menu_requested)
        self._text_editor = StepTextEditor(self._widget.viewport())  # floats over the plot, like the baseline range box
        self._text_editor.committed.connect(self.plan_text_committed)
        self._plan_room_applied = (0.0, 0.0)  # (top, bottom) fractions of the plot height the y range was widened by
        self._window = pg.LinearRegionItem(values=(0.0, 1.0), movable=True, span=BASELINE_BAND_SPAN)
        self._window.setZValue(-5)
        self.set_baseline_style(QColor(DEFAULT_COLOR), DEFAULT_ALPHA)
        self._window.sigRegionChangeFinished.connect(self._on_window_finished)
        self._window.sigRegionChanged.connect(self._on_window_moving)
        # The start / end fields float over the plot just above the selector, as the Histogram's range readout does over its region.
        self._range_readout = BaselineRangeReadout(self._widget.viewport())
        self._range_readout.setVisible(False)
        self._range_readout.range_edited.connect(self.baseline_range_edited)
        self._decimals = 0
        self._grid = np.empty(0)  # the x of the data points: the baseline selector's edges snap to them
        self._item.getViewBox().sigRangeChanged.connect(lambda *_args: self._reposition_range_readout())
        self._item.getViewBox().sigResized.connect(lambda *_args: self._reposition_range_readout())
        # The band's rectangle is computed from the visible y range, so it moves whenever the view re-ranges; ask for the repaint of its new place
        # ourselves (an incremental repaint after an auto-range left it unpainted until the pointer touched it).
        self._item.getViewBox().sigRangeChanged.connect(lambda *_args: self._window.update())
        self._window_in_scene = False
        self._setting_window = False
        self._item.scene().sigMouseClicked.connect(self._on_scene_clicked)
        self._item.scene().sigMouseMoved.connect(self._on_scene_moved)
        self._item.getAxis("left").setToolTip("Double-click the label to switch between the absolute position and the shift from the baseline.")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        self.set_axis_labels("Cube (#)", "Peak position (nm)")
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """pyqtgraph draws its own canvas and ignores Qt stylesheets: called at construction and on every live theme switch."""
        theme = get_active_theme()
        self._widget.setBackground(theme.toolbar_bg)
        self._axis_cursor.set_color(cube_color())
        self._range_readout.refresh_theme(theme)
        self._plan_bar.set_theme(theme.text_primary, theme.toolbar_bg, theme.accent_blue)
        self._text_editor.refresh_theme(theme)
        for name in ("bottom", "left"):
            axis = self._item.getAxis(name)
            axis.setPen(pg.mkPen(theme.text_muted))
            axis.setTextPen(pg.mkPen(theme.text_muted))

    def set_axis_labels(self, x_label: str, y_label: str) -> None:
        self._item.setLabel("bottom", x_label)
        self._item.setLabel("left", y_label)

    # -- drawing -----------------------------------------------------------------------------------------------------------------

    def clear(self) -> None:
        for item in self._items:
            self._item.removeItem(item)
        self._items.clear()
        if self._legend is not None:
            self._legend.clear()

    def show_series(
        self, series: Sequence[Series], stale: np.ndarray | None = None, mask_marks: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None
    ) -> None:
        """``stale``: bool per point (same length as the series' x); those points get a hollow marker on top: out-of-date results are still drawn.
        ``mask_marks``: series key -> (persistent, individual), bool per point; a persistent mask gets a hollow triangle (like a play button),
        an individual one a hollow circle, both in the mask colour (None draws none)."""
        self.clear()
        if len(series) <= MAX_LEGEND_SERIES:
            if self._legend is None:
                self._legend = self._item.addLegend(offset=(8, 8))
        elif self._legend is not None:
            self._legend.clear()
        for s in series:
            color = QColor(s.color)
            line = pg.PlotDataItem(
                s.x, s.y, pen=pg.mkPen(color, width=1.5), symbol="o", symbolSize=4, symbolBrush=pg.mkBrush(color), symbolPen=None,
                connect="finite", name=s.label if len(series) <= MAX_LEGEND_SERIES else None,
            )
            self._add(line)
            if s.band_low is not None and s.band_high is not None:
                brush = QColor(color)
                brush.setAlpha(_BAND_ALPHA)
                for a, b in _finite_runs(s.x, s.band_low, s.band_high):
                    low = pg.PlotDataItem(s.x[a:b], s.band_low[a:b], pen=_INVISIBLE_PEN)
                    high = pg.PlotDataItem(s.x[a:b], s.band_high[a:b], pen=_INVISIBLE_PEN)
                    fill = pg.FillBetweenItem(low, high, brush=pg.mkBrush(brush))
                    fill.setZValue(-10)
                    # The fill's own bounding rectangle reaches far outside the band (the y range of an average ran from -32 to 641 nm for peaks near 595 nm),
                    # so it stays out of the auto-range; the two invisible edge curves, which are only data, give the band's real extent.
                    self._add(low)
                    self._add(high)
                    self._add(fill, ignore_bounds=True)
            if stale is not None and stale.any():
                mask = np.asarray(stale, dtype=bool) & np.isfinite(s.y) & np.isfinite(s.x)
                if mask.any():
                    hollow = pg.ScatterPlotItem(s.x[mask], s.y[mask], symbol="o", size=9, brush=None, pen=pg.mkPen(color, width=1.5))
                    self._add(hollow)
            marks = None if mask_marks is None else mask_marks.get(s.key)
            if marks is not None:
                finite = np.isfinite(s.y) & np.isfinite(s.x)
                for flags, symbol, size in ((marks[0], "t2", 11), (marks[1], "o", 13)):
                    on = np.asarray(flags, dtype=bool) & finite
                    if on.any():
                        self._add(pg.ScatterPlotItem(s.x[on], s.y[on], symbol=symbol, size=size, brush=None, pen=pg.mkPen(MASK_COLOR, width=1.8)))
        self._item.getViewBox().autoRange()
        self._apply_plan_room()

    def _add(self, item: pg.GraphicsObject, ignore_bounds: bool = False) -> None:
        self._item.addItem(item, ignoreBounds=ignore_bounds)
        self._items.append(item)

    # -- the experiment steps ------------------------------------------------------------------------------------------------

    def plan_bar(self) -> PlanBarItem:
        return self._plan_bar

    def set_plan(self, steps: Sequence[PlanBarStep], settings: PlanBarSettings) -> None:
        """Show these steps (an empty sequence hides the bar). When the room the bar needs changed (its height, its side, the text mode), the view is ranged again."""
        self._plan_bar.set_plan(tuple(steps), settings)
        if self._plan_room() != self._plan_room_applied:
            self._item.getViewBox().autoRange()
            self._apply_plan_room()

    def _plan_room(self) -> tuple[float, float]:
        """(top, bottom): the fractions of the plot height the bar, and in the Data text mode the labels, need beyond the traces."""
        bar = self._plan_bar
        if not bar.is_shown():
            return 0.0, 0.0
        settings = bar.settings()
        top = bottom = 0.0
        if not settings.is_full:  # a full-height bar sits behind the traces and needs no room
            if settings.position == POSITION_TOP:
                top += settings.height_fraction
            else:
                bottom += settings.height_fraction
        view_height = float(self._item.getViewBox().height())
        if settings.text_align == ALIGN_DATA and view_height > 0.0:
            above, below = label_headroom_px(settings, QFontMetricsF(bar.font()).height())
            top += above / view_height
            bottom += below / view_height
        return top, bottom

    def _apply_plan_room(self) -> None:
        """Widen the y range the auto-range just set, so the bar (and the Data-mode labels) take free space instead of hiding traces."""
        self._plan_room_applied = self._plan_room()
        top, bottom = self._plan_room_applied
        if top <= 0.0 and bottom <= 0.0:
            return
        view_box = self._item.getViewBox()
        low, high = view_box.viewRange()[1]
        view_box.setYRange(*reserved_range(low, high, top, bottom), padding=0.0)

    def _on_scene_moved(self, scene_pos) -> None:
        """The bar's tooltip is the one of the step under the pointer, and the cursor shows a resize arrow over a boundary; set on every move, so they are right when Qt
        asks for them. Not while a button is down (a drag in progress keeps what it has)."""
        bar = self._plan_bar
        if not bar.is_shown() or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        bar.hover_at(bar.mapFromScene(scene_pos))

    def open_plan_text_editor(self, index: int, text: str) -> None:
        """Open the one-line editor over step `index`, showing `text` (the whole note). It reports through `plan_text_committed`."""
        viewport = self._widget.viewport()
        rect = self._plan_bar.device_rect_of(index)
        self._text_editor.open_over(index, text, rect, self._plan_bar.font(), QRectF(viewport.rect()))

    def close_plan_text_editor(self) -> None:
        self._text_editor.close_without_saving()

    # -- the cursor and the baseline window -------------------------------------------------------------------------------------------

    def set_current_x(self, x: float | None) -> None:
        """The dashed line and the slider-style circle at the current cube (None hides them)."""
        self._axis_cursor.set_x(x)

    def set_baseline_window(self, window: tuple[float, float] | None, decimals: int = 0) -> None:
        """Show the baseline window (low, high) in x units, or hide it (None). Setting it here never emits ``baseline_window_changed``.
        ``decimals``: how many the start / end fields above it show (whole cubes, or tenths of a second)."""
        if window is None:
            if self._window_in_scene:
                self._item.removeItem(self._window)
                self._window_in_scene = False
            self._range_readout.setVisible(False)
            return
        self._decimals = decimals
        if not self._window_in_scene:
            self._item.addItem(self._window, ignoreBounds=True)
            self._window_in_scene = True
        self._setting_window = True
        try:
            self._window.setRegion(window)
        finally:
            self._setting_window = False
        self._window.update()
        self._range_readout.set_range(float(window[0]), float(window[1]), self._decimals)
        self._range_readout.setVisible(True)
        self._reposition_range_readout()

    def set_snap_grid(self, x: np.ndarray) -> None:
        """The x values of the data points (cube numbers, or the cubes' start times); the selector's edges are put on them while it is dragged."""
        x = np.asarray(x, dtype=np.float64)
        self._grid = np.unique(x[np.isfinite(x)])

    def set_baseline_height(self, percent: float) -> None:
        """Height of the baseline selector, in percent of the plot height (it stands on the x axis)."""
        span = (BASELINE_BAND_SPAN[0], min(max(float(percent), 1.0), 100.0) / 100.0)
        self._window.span = span
        for line in self._window.lines:
            line.setSpan(*span)
            # An InfiniteLine keeps its bounding rectangle and end points until the view transform changes (a drag or a resize of the band), so setSpan
            # alone left the edges at the old height. Drop the cache and recompute now: that also announces the geometry change Qt repaints for.
            line.viewTransformChanged()
            line.boundingRect()
            line.update()
        self._window.update()

    def set_baseline_style(self, color: QColor, alpha: float) -> None:
        """Colour of the baseline selector and the opacity (0 - 1) of its fill; the edges keep the colour at a fixed opacity so the band stays grabbable."""
        fill = QColor(color)
        fill.setAlphaF(min(max(float(alpha), 0.0), 1.0))
        hover = QColor(fill)
        hover.setAlphaF(min(fill.alphaF() + _HOVER_LIFT, 1.0))
        edge = QColor(color)
        edge.setAlpha(_WINDOW_EDGE_ALPHA)
        self._window.setBrush(pg.mkBrush(fill))
        self._window.setHoverBrush(pg.mkBrush(hover))
        # pyqtgraph's setBrush neither repaints nor keeps the hover state: without this a new transparency shows only once the pointer
        # moves over the band, and a band under the pointer falls back to the resting brush.
        self._window.currentBrush = self._window.hoverBrush if self._window.mouseHovering else self._window.brush
        self._window.update()
        for line in self._window.lines:
            line.setPen(pg.mkPen(edge, width=1.5))
            line.setHoverPen(pg.mkPen(QColor(color), width=2.5))

    def set_readout_font_size(self, points: int) -> None:
        """Text size, in points, of the "Baseline [start, end]" box floating over the plot (the box is re-sized and put back on the x axis)."""
        self._range_readout.set_font_size(points)
        self._reposition_range_readout()

    def baseline_window(self) -> tuple[float, float] | None:
        if not self._window_in_scene:
            return None
        low, high = self._window.getRegion()
        return float(low), float(high)

    def x_range(self) -> tuple[float, float]:
        low, high = self._item.getViewBox().viewRange()[0]
        return float(low), float(high)

    def _on_window_moving(self) -> None:
        """A drag in progress: the edges are put on the nearest data points, the fields above the band follow."""
        if not self._setting_window:
            low, high = self._window.getRegion()
            snapped = snap_window(self._grid, low, high)
            if snapped != (float(low), float(high)):
                self._setting_window = True
                try:
                    self._window.setRegion(snapped)
                finally:
                    self._setting_window = False
                low, high = snapped
            self._range_readout.set_range(float(low), float(high), self._decimals)
            self._reposition_range_readout()

    def _reposition_range_readout(self) -> None:
        """Centred over the band's midpoint, standing on the base of the band, just above the x axis, kept inside the plot area."""
        if not self._range_readout.isVisible():
            return
        view_box = self._item.getViewBox()
        low, high = self._window.getRegion()
        mid_scene = view_box.mapViewToScene(QPointF((low + high) / 2.0, 0.0)).x()
        mid_x = self._widget.mapFromScene(QPointF(mid_scene, 0.0)).x()
        rect = view_box.sceneBoundingRect()
        left, right = self._widget.mapFromScene(rect.topLeft()).x(), self._widget.mapFromScene(rect.topRight()).x()
        bottom = self._widget.mapFromScene(rect.bottomLeft()).y()
        x = int(min(max(mid_x - self._range_readout.width() / 2.0, left), max(right - self._range_readout.width(), left)))
        y = int(bottom - self._range_readout.height() - _READOUT_GAP)  # on the base of the band, just above the x axis
        self._range_readout.move(x, y)
        self._range_readout.raise_()

    def _on_window_finished(self) -> None:
        if self._setting_window:
            return
        low, high = self._window.getRegion()
        self.baseline_window_changed.emit(float(low), float(high))

    def _on_y_label(self, scene_pos) -> bool:
        label = self._item.getAxis("left").label  # a rotated text item: its scene rectangle is the vertical strip the text occupies
        rect = label.sceneBoundingRect().adjusted(-_LABEL_HIT_MARGIN, -_LABEL_HIT_MARGIN, _LABEL_HIT_MARGIN, _LABEL_HIT_MARGIN)
        return rect.contains(scene_pos)

    def _on_x_label(self, scene_pos) -> bool:
        label = self._item.getAxis("bottom").label
        rect = label.sceneBoundingRect().adjusted(-_LABEL_HIT_MARGIN, -_LABEL_HIT_MARGIN, _LABEL_HIT_MARGIN, _LABEL_HIT_MARGIN)
        return rect.contains(scene_pos)

    def _on_scene_clicked(self, event) -> None:
        """A plain left click on the plot area: report the x it landed on. Clicks another item handled (the baseline band) are not ours."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if event.double():
            if self._on_y_label(event.scenePos()):
                self.y_label_double_clicked.emit()
            elif self._on_x_label(event.scenePos()):
                self.x_label_double_clicked.emit()
            return
        if event.isAccepted():
            return
        view_box = self._item.getViewBox()
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        self._plan_bar.clear_selection()  # a click on the plot, not on a step: no step stays selected
        self.x_clicked.emit(float(view_box.mapSceneToView(event.scenePos()).x()))
