"""Sensorgram plot widget - the pyqtgraph-facing half of the Sensorgram panel (sketch §10 "sensorgram/panel.py + plot.py").

Draws what it is handed: a line per series (the metric against time or cube number), a band for averaged series, grey hollow diamonds where the
scan is out of date, mask marks (a triangle: a persistent mask touched the point's ROIs, a circle: an individual one did), a vertical line at the current cube and an optional draggable baseline selector (a band along the bottom tenth of the plot, with its own colour and fill opacity). It computes nothing and reads no module.
A double-click on the y axis label emits ``y_label_double_clicked`` (the panel turns it into "absolute position / shift from the baseline"); one on the x axis label emits ``x_label_double_clicked`` (time <-> cube number, app-wide).
A left click inside the plot emits ``x_clicked`` with the x value (the panel turns it into "go to that cube"); dragging the circle on the x axis
(the Cube slider's handle, in the same colour) emits it too, for every pointer position.
The experiment steps (``plan_bar.PlanBarItem``) sit behind everything; `set_plan` hands them over and makes room for the bar by widening the y range (``plan/bar_model.py``).
The step statistics table (``stats_table.StepStatsItem``, `set_stats_table`) writes mean +/- SD per line and step level with the legend's rows: the plot moves the legend into the
table's edge, makes room for it (y at that edge, x on the left for the legend) and decides which lines get a legend at all (`_fit_legend`: up to 6 always, more while it takes at most half the plot height;
the View tab's Show / Hide override that).
How the lines are drawn (dots, lines, how they are joined, the band, the anchors, the legend's size, gap and place) is a `view_settings.SensorgramViewSettings` from the View tab, handed over with `set_view`.
The bar's pointer gestures (drag a boundary, double-click a step, right-click a step) come out as the ``plan_*`` signals; ``plan_text_editor.StepTextEditor`` is the field that opens
over a step (`open_plan_text_editor`). The plot decides nothing about them: ``plan_actions.PlanBarActions`` turns them into commands.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsItem, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...analysis.scan_trace import Series
from ...plan.bar_model import ALIGN_DATA, POSITION_TOP, PlanBarSettings, PlanBarStep, label_headroom_px, reserved_range
from ...plan.stats_table import StatsTable, StatsTableSettings
from ...selection.sensorgram_axis import snap_window  # (snap_to_grid and snap_window moved to the shared axis module, 2026-11-05)
from ..axis_cursor import AxisCursor
from ..image.mark_colors import MASK_COLOR, STALE_COLOR, cube_color
from ..plot_item_pool import PlotItemPool
from ..svg_layers import SvgPart
from .baseline_selector_menu import DEFAULT_ALPHA, DEFAULT_COLOR, BaselineRangeReadout
from .plan_bar import PlanBarItem
from .plan_text_editor import StepTextEditor
from .play_repaint import PlayCursorRepaint
from .stats_table import LegendRows, StepStatsItem
from .curve_smoothing import smooth_line, smooth_run
from .draw_order import DRAW_AUTO, DRAW_LEGEND, back_to_front, roughness
from .legend_corner import CORNER_STEP_TABLE_NOTE, DEFAULT_CORNER, corner_offset
from .legend_order import SORT_VALUE, VALUE_MEAN, order_rows
from .view_settings import DEFAULT_DOTS_MAX_LINES, LEGEND_HIDE, LEGEND_SHOW, LINE_SPLINE, SensorgramViewSettings, legend_text


def _finite_mean(values: np.ndarray) -> float:
    """The mean of the finite values of a line (NaN when it has none): the number the legend's Value order uses for "Mean"."""
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    return float(finite.mean()) if finite.size else math.nan


MAX_LEGEND_SERIES = 6  # up to this many lines always have a legend
MAX_LEGEND_ENTRIES = 24  # more lines carry no names at all (a legend of a hundred ROIs is a wall of text)
LEGEND_MAX_HEIGHT_FRACTION = 0.5  # between the two: the legend stays while it takes at most this much of the plot height (it is cleared otherwise); a row is ~21 px
_LEGEND_MARGIN_PX = 3.0  # the legend's own inner margin on all four sides (2026-11-13; pyqtgraph's default is 9: the first row's text sat 25 px below the plot's top edge, now 19)
_LEGEND_MARGIN_GIVEN_UP_PX = 9.0 - _LEGEND_MARGIN_PX
_STATS_GAP_PX = 4  # between the legend and the plan bar / the edge, and around the rows of the step statistics table
_LEGEND_TEXT_MARGIN_PX = 2.0  # pyqtgraph's legend label keeps 4 px of document margin above and below its text; half of that halves the gap between rows
_LEGEND_EDGE_PX = _STATS_GAP_PX + (4.0 - _LEGEND_TEXT_MARGIN_PX)  # legend to the edge / plan bar with the table on (the legend's own inner margin, `_LEGEND_MARGIN_PX`, comes on top of it)
# From the top-left corner of the plot when the step statistics table is off. Down: the same `_LEGEND_EDGE_PX` as with the table on, so hiding the stats does not move the legend
# (maintainer, 2026-11-13: "when I hide stats, the gap gets bigger"; it was 8 + the 9 px margin = 17 px, now 6 + 3 = 9). Across: 8 px as it always was plus what the smaller inner margin gave up.
_LEGEND_OFFSET_PX = (8 + _LEGEND_MARGIN_GIVEN_UP_PX, _LEGEND_EDGE_PX)
_BAND_ALPHA = 55
BASELINE_BAND_SPAN = (0.0, 0.1)  # the baseline selector fills the bottom tenth of the plot, not its full height
_WINDOW_EDGE_ALPHA = 160
_HOVER_LIFT = 0.08  # how much more opaque the fill gets under the pointer (pyqtgraph's own jump was far larger than the resting fill)
_INVISIBLE_PEN = pg.mkPen(0, 0, 0, 0)  # a curve with no pen at all is left out of the auto-range; one with a fully transparent pen is not
_LABEL_HIT_MARGIN = 4.0  # pixels around the y axis label that still count as a double-click on it
_READOUT_GAP = 0  # pixels between the bottom edge of the "Baseline [start, end]" box and the x axis line (the plot area's bottom edge); it was 2
LINE_WIDTH = 1.0  # px, antialiased. Qt paints only 1 px lines fast: 160 lines x 400 cubes took 140 ms at 1.5 px, 30 ms at 1 px antialiased (2026-11-02)
DOTS_MAX_LINES = DEFAULT_DOTS_MAX_LINES  # a dot on every point only up to this many lines until the View tab's Dot limit says otherwise (with more they merge into the lines and cost ~1 s per redraw at 160 ROIs)
_Z_BAND, _Z_LINE, _Z_DOTS, _Z_RINGS, _Z_MARKS = -10.0, 0.0, 1.0, 2.0, 3.0  # bands behind the lines, rings and mask marks over the dots
_Z_SPREAD = 0.9  # the lines' own stacking (the Draw order): the line at rank r of n adds _Z_SPREAD * r / n to the z of each kind of item, so a kind's z stays inside its unit band
_ANCHOR_DRAG_TIME_S, _ANCHOR_MOVE_PX = 60.0, 12  # anchor edit: a press is a click unless the mouse moves more than 12 px (held time does not matter)


def _line_pen(color: QColor, width: float = LINE_WIDTH) -> QPen:
    pen = pg.mkPen(color, width=width)
    pen.setCosmetic(True)
    return pen


class _Legend(pg.LegendItem):
    """pyqtgraph's legend, sized to exactly what its layout needs (2026-11-13).

    pyqtgraph makes the legend as high as its rows added up, which ignores the margins and the Row gap. With its 9 px margins the layout always needed more than that and so no row
    was ever stretched; with the tighter margins (`_LEGEND_MARGIN_PX`) a negative Row gap makes the layout need less, and the spare height was shared out between the rows, so
    the rows moved by less than the gap that was set. Here the height is the layout's own minimum, so a row gap of -3 px moves the rows by exactly -3 px, with any margin and
    any number of rows.

    It also knows which corner of the plot area it was last put in (the Legend menu's Corner row, `legend_corner.py`), so a corner is applied only when it *changes*: a legend the user
    dragged by hand stays where it was dropped through every redraw, until another corner is picked."""

    _corner: str | None = DEFAULT_CORNER  # the corner the legend was built in (the top-left); None = something else moved it (the step table)
    _filling = False  # `add_entries` is adding rows: the layout is worked out once at the end, not after every row

    def add_entries(self, entries: Sequence[tuple[object, str]]) -> None:
        """Add the rows (`(sample item, text)` each) with ONE layout pass. pyqtgraph's `addItem` works the size out after every row, so n rows cost n passes over the whole layout
        (2026-11-22: 48 of 56 ms of a Sensorgram redraw with 20 lines); the rows, their order and the final size are the same."""
        self._filling = True
        try:
            for sample, text in entries:
                self.addItem(sample, text)
        finally:
            self._filling = False
            self.updateSize()

    def place_in_corner(self, corner: str, across: float, down: float) -> None:
        """Put the legend in `corner`, `across` px from the side and `down` px from the top / bottom, unless it was put there already."""
        if corner == self._corner:
            return
        self._corner = corner
        self.setOffset(corner_offset(corner, across, down))

    def forget_corner(self) -> None:
        """Something else (the step table) moved the legend: the next `place_in_corner` applies its corner again."""
        self._corner = None

    def updateSize(self) -> None:
        if self._filling:
            return
        super().updateSize()
        if self.size is not None:  # a fixed size was asked for
            return
        self.layout.activate()
        # Ask for no height at all: Qt raises it to the widget's own minimum, which is the rows, the row gaps and the margins and nothing spare. (Asking for a computed height
        # was fragile: the layout's size hint counts the margins or not depending on the moment.)
        self.resize(self.width(), 0.0)


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


_ANCHOR_HIT_PX = 10.0  # anchor edit: a right click this close (screen px) to an anchor square removes it; farther away it opens the anchor menu
_CURSOR_SIZE, _CURSOR_HOT = 36, 10  # logical px: the anchor cursor's picture and its cross-hair centre (the click point)


def _anchor_cursor(needed: int) -> QCursor:
    """The anchor edit's pointer: a cross-hair, plus a round gold badge with the number of points the shape still needs at its lower right
    (none once there are enough). Drawn at the screen's pixel ratio so it stays sharp."""
    if needed <= 0:
        return QCursor(Qt.CursorShape.CrossCursor)
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(round(_CURSOR_SIZE * ratio), round(_CURSOR_SIZE * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = _CURSOR_HOT + 0.5
    for color, width in (("#ffffff", 3.0), ("#000000", 1.0)):  # a white halo keeps the cross visible on dark and light plots
        painter.setPen(QPen(QColor(color), width))
        painter.drawLine(QPointF(c, 0.5), QPointF(c, 2 * _CURSOR_HOT + 0.5))
        painter.drawLine(QPointF(0.5, c), QPointF(2 * _CURSOR_HOT + 0.5, c))
    badge = QRectF(_CURSOR_HOT + 6.0, _CURSOR_HOT + 6.0, 17.0, 17.0)
    painter.setPen(QPen(QColor("#000000"), 1.0))
    painter.setBrush(QColor(get_active_theme().accent_gold))
    painter.drawEllipse(badge)
    font = QFont()
    font.setPixelSize(12)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#000000"))
    painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), str(needed) if needed < 10 else "9+")
    painter.end()
    return QCursor(pixmap, _CURSOR_HOT, _CURSOR_HOT)


class _MiddlePanViewBox(pg.ViewBox):
    """pyqtgraph pans with the left button by default; here only the middle button pans (the left one is left free for clicking a cube)."""

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        super().mouseDragEvent(ev, axis)

    claim_right_click = None  # set by the plot: a function (scene position) -> bool, True where the plan bar wants the right-click
    anchor_right_click = None  # set by the plot: a function (scene position) -> bool, True when the anchor-edit mode took the right-click (no menu)

    def mouseClickEvent(self, ev):
        """pyqtgraph hands a right-click to the ViewBox before any item and the ViewBox accepts it (its own menu). Over a step of the plan bar the click is declined
        here, so that the bar (and its menu) gets it. In the anchor-edit mode a right-click elsewhere goes to the anchors (remove the square under it, or the
        anchor menu) instead of opening the view's menu."""
        if ev.button() == Qt.MouseButton.RightButton and self.claim_right_click is not None and self.claim_right_click(ev.scenePos()):
            ev.ignore()
            return
        if ev.button() == Qt.MouseButton.RightButton and self.anchor_right_click is not None and self.anchor_right_click(ev.scenePos(), ev.screenPos()):
            ev.accept()
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
    anchor_point_added = pyqtSignal(float)  # anchor-edit mode: a left click on the plot, at this data x (the cube cursor does not move)
    anchor_point_remove_requested = pyqtSignal(float)  # anchor-edit mode: a right click on an anchor square, at its data x
    legend_corner_note_changed = pyqtSignal(str)  # the step table went on or off: the Legend menu's Corner row says whether it has an effect ("" = it has)
    anchor_menu_requested = pyqtSignal(float, QPoint)  # anchor-edit mode: a right click away from every square: data x, screen position for the menu

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._widget = pg.PlotWidget(parent=self, viewBox=_MiddlePanViewBox())
        self._widget.setMinimumHeight(100)
        self._item = self._widget.getPlotItem()
        self._item.showGrid(x=True, y=True, alpha=0.15)
        self._legend: _Legend | None = None
        self._corner_note = ""  # what the Legend menu's Corner row says now (`legend_corner_note`); `legend_corner_note_changed` fires when it changes
        self._legend_items: list[tuple[pg.PlotCurveItem, str]] = []  # every line's (marker, text), in the lines' order
        self._legend_series: list[Series] = []
        # What `restack` needs from the last `show_series`: the lines it drew (the same objects), the view it drew with, and per line its items and its export parts.
        self._drawn_series: tuple[Series, ...] | None = None
        self._drawn_view: SensorgramViewSettings | None = None
        self._line_items: dict[int, list[tuple[pg.GraphicsObject, float]]] = {}
        self._line_parts: dict[int, list[SvgPart]] = {}
        self._legend_rank: list[int] = []  # the line shown by the legend's k-th row (the Legend menu's order); the lines' own order when the order is Group, ascending
        self._order_step: int | None = None  # the plan step the "At step" order reads (the one selected on the bar)
        self._manual_order: tuple[str, ...] = ()  # the Draw order's Manual list (the session's, `PlotLineOrderModule`), first = in front
        self._view = SensorgramViewSettings()  # how the lines, band, dots, anchors and legend are drawn (the View tab); `set_view`
        self._pool = PlotItemPool(self._item)
        self._axis_cursor = AxisCursor(self._item, cube_color(), self)
        self._axis_cursor.dragged.connect(self.x_clicked)
        self._cursor = self._axis_cursor.line
        # While Play runs only the cursor strip is repainted, once per frame, together with the image (`play_repaint.py`).
        self._play_repaint = PlayCursorRepaint(self._widget, self._item.scene(), (self._axis_cursor.line, self._axis_cursor.handle), self)
        self._plan_bar = PlanBarItem()
        self._item.addItem(self._plan_bar, ignoreBounds=True)  # behind the traces; not part of the auto-range
        self._item.getViewBox().claim_right_click = self._plan_bar.claims_scene_pos
        self._item.getViewBox().anchor_right_click = self._on_anchor_right_click
        self._anchor_mode = False
        self._drag_defaults: tuple[float, int] | None = None  # the scene's click-vs-drag limits, restored when the anchor edit ends
        self._anchors_needed = 0  # anchor points the shape still needs, shown beside the edit mode's cross-hair
        self._stats = StepStatsItem()  # the mean +/- SD of every line in every step, along the same edge as the legend it follows
        self._item.addItem(self._stats, ignoreBounds=True)
        self._stats.set_row_source(self._legend_rows)
        self._plan_bar.edge_drag_started.connect(self.plan_edge_drag_started)
        self._plan_bar.edge_dragged.connect(self.plan_edge_dragged)
        self._plan_bar.edge_drag_finished.connect(self.plan_edge_drag_finished)
        self._plan_bar.text_edit_requested.connect(self.plan_text_edit_requested)
        self._plan_bar.menu_requested.connect(self.plan_menu_requested)
        self._text_editor = StepTextEditor(self._widget.viewport())  # floats over the plot, like the baseline range box
        self._text_editor.committed.connect(self.plan_text_committed)
        self._room_applied = (0.0, 0.0, 0.0)  # (top, bottom) fractions of the plot height the y range was widened by, and the pixels the x range was widened by on the left
        # The user's own zoom or pan (wheel, right-drag, middle-drag, an axis) is kept over redraws (2026-11-10): a recomputed sensorgram used to jump
        # back to the full view. `reset_view` (the corner "A" button, the view menu's "View All") and a change of an axis' quantity end it.
        self._view_kept = False
        view_box = self._item.getViewBox()
        view_box.sigRangeChangedManually.connect(self._on_view_changed_by_user)
        self._item.autoBtn.clicked.disconnect()  # pyqtgraph's own handler ranges to the data alone, without the room for the plan bar and the table
        self._item.autoBtn.clicked.connect(self.reset_view)
        if view_box.menu is not None:
            view_box.menu.viewAll.triggered.connect(self.reset_view)  # after the menu's own auto-range: the same full view as the "A" button
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
        self._item.getViewBox().sigResized.connect(lambda *_args: self._place_legend())  # the plan bar is a fraction of the plot height: the legend sits below it
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
        self._axis_labels: tuple[str, str] = ("", "")
        self.set_axis_labels("Cube (#)", "Peak position (nm)")
        # The cube cursor moves at every frame of Play and Qt repaints what it crosses: the plan bar, the table and the axes were painted
        # again each time (~24 ms of 31 per move, offscreen, 2026-11-02). Qt keeps them as pictures instead and redraws a picture only
        # when the item asks (update()) or the view range / size changes.
        for item in (self._plan_bar, self._stats, self._item.getAxis("bottom"), self._item.getAxis("left")):
            item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.refresh_theme()

    def export_parts(self) -> list[SvgPart]:
        """What each drawn item is, for a layered SVG (`svg_layers.py`): the lines, points, bands and marks of the last `show_series` by line, and
        the plan bar, the step table, the baseline window and the cube cursor. Axes and legend are named there."""
        parts = list(getattr(self, "_svg_parts", []))
        parts += [
            SvgPart("Experimental plan", "Plan bar", self._plan_bar),
            SvgPart("Step statistics", "Table", self._stats),
            SvgPart("Cursor", "Cube cursor line", self._axis_cursor.line),
            SvgPart("Cursor", "Cube cursor handle", self._axis_cursor.handle),
        ]
        if self._window_in_scene:
            parts.append(SvgPart("Baseline window", "Baseline window", self._window))
        return parts

    def plot_item(self) -> pg.PlotItem:
        """The pyqtgraph plot (for the export button: figures are drawn from it)."""
        return self._item

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
        """Name the axes. A new name means another quantity (cube number <-> time, position <-> shift, another metric): the numbers of a kept zoom
        would point at nothing, so the next draw shows the full view again."""
        if (x_label, y_label) != self._axis_labels:
            self._axis_labels = (x_label, y_label)
            self._view_kept = False
        self._item.setLabel("bottom", x_label)
        self._item.setLabel("left", y_label)

    # -- the view: full, or the user's own zoom ----------------------------------------------------------------------------------

    def view_is_kept(self) -> bool:
        """True while the user's own zoom or pan is kept: a redraw then leaves the ranges alone."""
        return self._view_kept

    def _on_view_changed_by_user(self, *_args: object) -> None:
        if self._pool.drawn:  # a wheel turn over an empty plot is no zoom on anything: the first lines then get the full view
            self._view_kept = True

    def reset_view(self, *_args: object) -> None:
        """Back to the full view: all the data, plus the room the plan bar and the step statistics table need. Ends a kept zoom."""
        self._view_kept = False
        self._item.getViewBox().autoRange()
        self._apply_room()
        self._item.autoBtn.hide()  # as pyqtgraph does after its own click; it comes back on hover

    def forget_view(self) -> None:
        """The next draw shows the full view (a new dataset: the old zoom means nothing there)."""
        self._view_kept = False

    # -- drawing -----------------------------------------------------------------------------------------------------------------

    @property
    def _items(self) -> list[pg.GraphicsObject]:
        """The items drawn now (lines, dots, bands, rings, marks), in the order they were drawn."""
        return self._pool.drawn

    def clear(self) -> None:
        self._drawn_series = None  # nothing to re-stack
        self._pool.clear()
        if self._legend is not None:
            self._legend.clear()

    def set_view(self, view: SensorgramViewSettings) -> None:
        """How the next draw looks (the View tab's options). It changes nothing that is on screen: the panel redraws the last result after calling it."""
        self._view = view.sanitized()
        self._axis_cursor.set_hidden(not self._view.show_cursor)  # the View tab's Cursor toggle: the cube cursor keeps its place and is drawn or not

    def view(self) -> SensorgramViewSettings:
        return self._view

    def show_series(
        self,
        series: Sequence[Series],
        stale: np.ndarray | None = None,
        mask_marks: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None,
        baseline_curves: bool = False,
        anchor_dots: str | None = None,
    ) -> None:
        """``stale``: bool per point (same length as the series' x); those points get a hollow grey diamond on top (a shape and colour of their own, so they are never
        taken for a mask mark, and not in the line's colour): out-of-date results are still drawn. None draws none.
        ``mask_marks``: series key -> (persistent, individual), bool per point; a persistent mask gets a hollow triangle (like a play button),
        an individual one a hollow circle, both in the mask colour (None draws none).
        ``baseline_curves``: draw each line's drift baseline (`Series.baseline_curve`) as a dashed line in the line's colour; the panel asks for it only while
        the traces are absolute (Subtract off), where the curve is what Subtract would take off. Out of the auto-range (a curve extrapolated past the
        anchors must not stretch the view).
        ``anchor_dots``: mark each line's anchors (`Series.anchor_x`) with a hollow square in the line's colour: ``"absolute"`` at the trace's level there
        (Subtract off: the curve passes through or near them), ``"subtracted"`` at what is left after correction (Subtract on: on or near zero); None draws none.

        Drawing cost (2026-11-02, ``docs/spectra_sensorgram_draw_cost_2026-11-02.md``): the items are reused (`PlotItemPool`), the lines are 1 px
        (Qt's fast path; 1.5 px was 8-11x slower to paint) and the dots on the points are drawn only up to the View tab's Dot limit (`view.dots_max_lines`, 10 at first: 64,000 dots
        for 160 ROIs cost ~1 s per redraw and cannot be told apart anyway). The Points toggle shows itself off while the limit holds (`points_toggle.py`)."""
        pool = self._pool
        pool.begin()
        self._svg_parts: list[SvgPart] = []  # names for a layered SVG export (svg_layers.py): one per drawn item
        legend_slots: list[tuple[pg.PlotCurveItem, str] | None] = [None] * len(series)  # (marker, text) of every line by its own index; `_rebuild_legend` puts them in the legend in the order asked for
        legend_rank = self._legend_ranking(series)  # the legend's row order, used by the stacking and by the legend itself
        stack = self._stacking(series, legend_rank)  # the lines in the order they are drawn: the one at the back first (the Draw order)
        count = max(len(series), 1)
        view = self._view
        # The legend names up to MAX_LEGEND_ENTRIES lines; "show" (the View tab) names them all, however many: the maintainer's choice overrides the guard against walls of text.
        named = view.legend_mode == LEGEND_SHOW or len(series) <= MAX_LEGEND_ENTRIES
        if named:
            if self._legend is None:
                self._legend = _Legend(offset=_LEGEND_OFFSET_PX)
                self._legend.setParentItem(self._item.vb)
                self._item.legend = self._legend  # as `PlotItem.addLegend` does: a curve made with a name joins it
                self._legend.layout.setContentsMargins(_LEGEND_MARGIN_PX, _LEGEND_MARGIN_PX, _LEGEND_MARGIN_PX, _LEGEND_MARGIN_PX)  # tighter than pyqtgraph's 9 px: less empty space above the first row
                # The table reads the legend's rows when it paints; with its picture cached it must be told when the legend moves or resizes.
                for signal in (self._legend.geometryChanged, self._legend.xChanged, self._legend.yChanged):
                    signal.connect(self._stats.update)
            else:
                self._legend.clear()
            self._style_legend()
        elif self._legend is not None:
            self._legend.clear()
        dots = len(series) <= view.dots_max_lines and view.show_points
        spline = view.line_style == LINE_SPLINE
        self._line_items, self._line_parts, self._drawn_series = {}, {}, None  # per line (its index): its items, and the parts of the layered export
        for rank, line_index in enumerate(stack):
            s = series[line_index]
            dz = _Z_SPREAD * rank / count  # this line's place in the stack
            placed: list[tuple[pg.GraphicsObject, float]] = []  # this line's items with the z they have before the stacking is added (`restack` puts them in another order)
            parts_from = len(self._svg_parts)
            color = QColor(s.color)
            finite = np.isfinite(s.y) & np.isfinite(s.x)
            line_x, line_y = smooth_line(s.x, s.y) if spline else (s.x, s.y)  # the dots, marks and statistics stay on the measured points; only the line between them is smoothed
            if not line_x.size:
                line_x, line_y = s.x, s.y  # nothing finite to smooth
            # A hidden line keeps its (invisible) item: it still gives the view its extent, which the dots, drawn out of the auto-range, do not.
            pen = _line_pen(color, view.line_width) if view.show_lines else _INVISIBLE_PEN
            line = self._take_stacked(placed, "line", lambda: pg.PlotCurveItem(antialias=True), _Z_LINE, dz)
            if finite.all():  # no gap to keep: one polyline, and pyqtgraph need not look for NaN again
                line.setData(line_x, line_y, pen=pen, connect="all", skipFiniteCheck=True, name=s.label if named else None)
            else:  # a NaN stays a gap in the line
                line.setData(line_x, line_y, pen=pen, connect="finite", skipFiniteCheck=False, name=s.label if named else None)
            if view.show_lines:
                self._svg_parts.append(SvgPart("Lines", s.label, line))
            if named:
                # the legend's marker is the line's colour even when the line is hidden: a sample that is not in the plot and has the real pen
                sample = line if view.show_lines else pg.PlotCurveItem(pen=_line_pen(color, view.line_width))
                legend_slots[line_index] = (sample, legend_text(s.label, s.short_label, view.legend_counts))
            if baseline_curves and s.baseline_curve is not None:
                dashed = self._take_stacked(placed, "baseline_curve", lambda: pg.PlotCurveItem(antialias=True), _Z_LINE - 0.5, dz, ignore_bounds=True)
                pen = _line_pen(color, view.line_width)
                pen.setStyle(Qt.PenStyle.DashLine)
                dashed.setData(s.x, s.baseline_curve, pen=pen, connect="finite", skipFiniteCheck=False)
                self._svg_parts.append(SvgPart("Baseline curves", s.label, dashed))
            if anchor_dots is not None and s.anchor_x is not None and s.anchor_y is not None and s.anchor_x.size:
                ay = s.anchor_y if anchor_dots == "absolute" or s.anchor_base is None else s.anchor_y - s.anchor_base
                squares = self._take_stacked(placed, "anchor_dots", lambda: pg.ScatterPlotItem(), _Z_MARKS, dz, ignore_bounds=True)
                squares.setData(s.anchor_x, ay, symbol="s", size=9, brush=None, pen=pg.mkPen(color, width=1.8))
                self._svg_parts.append(SvgPart("Baseline anchors", s.label, squares))
            if dots and finite.any():
                # Out of the auto-range: the dots sit on the line's points (same bounds), and a dot item would also widen the range by its radius.
                dot_item = self._take_stacked(placed, "dots", lambda: pg.ScatterPlotItem(), _Z_DOTS, dz, ignore_bounds=True)
                dot_item.setData(s.x[finite], s.y[finite], symbol="o", size=view.point_size, brush=pg.mkBrush(color), pen=None)
                self._svg_parts.append(SvgPart("Points", s.label, dot_item))
            if view.show_band and s.band_low is not None and s.band_high is not None:
                brush = QColor(color)
                brush.setAlpha(_BAND_ALPHA)
                for a, b in _finite_runs(s.x, s.band_low, s.band_high):
                    low = pool.take("band_edge", lambda: pg.PlotDataItem(pen=_INVISIBLE_PEN))
                    high = pool.take("band_edge", lambda: pg.PlotDataItem(pen=_INVISIBLE_PEN))
                    band_x, low_y, high_y = s.x[a:b], s.band_low[a:b], s.band_high[a:b]
                    if spline:  # the band follows the smoothed line: both edges on the same denser x
                        band_x, low_y = smooth_run(band_x, low_y)
                        high_y = smooth_run(s.x[a:b], high_y)[1]
                    low.setData(band_x, low_y)
                    high.setData(band_x, high_y)
                    # The fill's own bounding rectangle reaches far outside the band (the y range of an average ran from -32 to 641 nm for peaks near 595 nm),
                    # so it stays out of the auto-range; the two invisible edge curves, which are only data, give the band's real extent.
                    fill = self._take_stacked(placed, "band_fill", lambda: pg.FillBetweenItem(low, high), _Z_BAND, dz, ignore_bounds=True)  # noqa: B023 (called at once)
                    fill.setCurves(low, high)
                    fill.setZValue(_Z_BAND + dz)  # `setCurves` puts the fill just under its curves (z = -1): the band is stacked with its own line, behind every line (`restack` does the same)
                    fill.setBrush(pg.mkBrush(brush))
                    self._svg_parts += [SvgPart("Bands", s.label, fill), SvgPart("Bands", f"{s.label} (edges)", low), SvgPart("Bands", f"{s.label} (edges)", high)]
            if stale is not None and stale.any():
                mask = np.asarray(stale, dtype=bool) & finite
                if mask.any():
                    hollow = self._take_stacked(placed, "stale", lambda: pg.ScatterPlotItem(), _Z_RINGS, dz)
                    hollow.setData(s.x[mask], s.y[mask], symbol="d", size=10, brush=None, pen=pg.mkPen(STALE_COLOR, width=1.5))
                    self._svg_parts.append(SvgPart("Out-of-date marks", s.label, hollow))
            marks = None if mask_marks is None else mask_marks.get(s.key)
            if marks is not None:
                for flags, symbol, size in ((marks[0], "t2", 11), (marks[1], "o", 13)):
                    on = np.asarray(flags, dtype=bool) & finite
                    if on.any():
                        mark = self._take_stacked(placed, f"mark:{symbol}", lambda: pg.ScatterPlotItem(), _Z_MARKS, dz)
                        mark.setData(s.x[on], s.y[on], symbol=symbol, size=size, brush=None, pen=pg.mkPen(MASK_COLOR, width=1.8))
                        kind = "persistent mask" if symbol == "t2" else "individual mask"
                        self._svg_parts.append(SvgPart("Mask marks", f"{s.label} ({kind})", mark))
            self._line_items[line_index] = placed
            self._line_parts[line_index] = self._svg_parts[parts_from:]
        pool.end()
        self._drawn_series, self._drawn_view = tuple(series), view
        self._rebuild_legend(series, [slot for slot in legend_slots if slot is not None], legend_rank)
        if not self._view_kept:  # the user's own zoom stays over a redraw (a recompute, another selection); `reset_view` brings the full view back
            self._item.getViewBox().autoRange()
            self._apply_room()

    def _take_stacked(self, placed: list, kind: str, factory, base: float, dz: float, *, ignore_bounds: bool = False):
        """An item from the pool at z = `base` + the line's place in the stack `dz`; `placed` remembers it with its `base`, so `restack` can give it another `dz`."""
        item = self._pool.take(kind, factory, ignore_bounds=ignore_bounds, z=base + dz)
        placed.append((item, base))
        return item

    def can_restack(self, series: Sequence[Series]) -> bool:
        """True when the last `show_series` drew exactly these lines (the same objects) with a view that differs from the current one only in what `restack` can change (the order of
        the legend rows, the Draw order, the legend's corner, the cursor): then a change of those needs no redraw."""
        drawn = self._drawn_series
        return (
            drawn is not None and len(drawn) == len(series) and all(a is b for a, b in zip(drawn, series))
            and self._drawn_view is not None and self._view.differs_only_in_order(self._drawn_view)
        )

    def restack(self, series: Sequence[Series]) -> None:
        """Put the lines in the order the view asks for now (the Draw order) and write the legend rows in the order it asks for, **without drawing the lines again** (`can_restack` first).
        A reorder changes nothing but each line's z, the legend's rows (and with them the step table's) and the order of the layered export's parts: the items, their data, the ranges
        and the view stay. 2026-11-22: ~130 -> ~40 ms with 160 lines; the result is pinned equal to a full redraw by `test_lspri_reorder_fast_path.py`."""
        legend_rank = self._legend_ranking(series)
        stack = self._stacking(series, legend_rank)
        count = max(len(series), 1)
        parts: list[SvgPart] = []
        for rank, line_index in enumerate(stack):
            dz = _Z_SPREAD * rank / count
            for item, base in self._line_items.get(line_index, ()):
                if item.zValue() != base + dz:
                    item.setZValue(base + dz)
            parts += self._line_parts.get(line_index, ())
        self._svg_parts = parts  # the layers of a layered export are in stacking order, back to front, as a redraw writes them
        self._drawn_view = self._view
        self._rebuild_legend(series, self._legend_items, legend_rank)

    # -- the order of the legend's rows (2026-11-22, `legend_order.py`) -----------------------------------------------------------

    def _rebuild_legend(self, series: Sequence[Series], items: list[tuple[pg.PlotCurveItem, str]], rank: list[int] | None = None) -> None:
        """Write the legend's rows in the order the Legend menu asks for, then fit and place it (and, with it, the step table: its rows are the legend's rows). Only the rows move: the
        lines, their colours and every number stay. `items` are the lines' own (marker, text) in the lines' order; `self._legend_rank[k]` is the line the legend's k-th row shows, so the
        table can find the row of line i (`_legend_rows`)."""
        self._legend_items, self._legend_series = items, list(series)
        legend = self._legend
        self._legend_rank = list(range(len(items)))
        if legend is not None:
            legend.clear()
            self._style_legend()
            if items:
                self._legend_rank = self._legend_ranking(series) if rank is None else rank
                legend.add_entries([items[position] for position in self._legend_rank])
        self._stats.update()  # new legend entries: new rows for the (cached) table
        self._tighten_legend()
        self._fit_legend(len(series))
        self._place_legend()

    def _legend_ranking(self, series: Sequence[Series]) -> list[int]:
        """The lines' indices in the order of the legend's rows (the Legend menu's Order), first row first."""
        names = [s.short_label or s.label for s in series]
        return order_rows(names, self._order_values(series), self._view.legend_order())

    def set_manual_order(self, keys: Sequence[str]) -> None:
        """The Draw order's Manual list (line keys, first = in front). It changes nothing on screen by itself: the panel redraws."""
        self._manual_order = tuple(keys)

    def _stacking(self, series: Sequence[Series], legend_rank: list[int]) -> list[int]:
        """The lines' indices in the order they are drawn, the one at the back first (`draw_order.py`: Auto = the smoothest in front, Legend = the legend's first row in front, Manual = the table's
        first row in front). Only the stacking: the lines, the legend and the table keep their own order."""
        mode = self._view.draw_mode
        keys = [s.key for s in series]
        rough = [roughness(s.y) for s in series] if mode == DRAW_AUTO else ()
        return back_to_front(keys, mode, legend_order=legend_rank, roughness_of=rough, manual=self._manual_order)

    def _order_values(self, series: Sequence[Series]) -> list[float] | None:
        """The number each line is ordered by under "Value" (None: the order does not read one, or has no step to read it at)."""
        order = self._view.legend_order()
        if order.key != SORT_VALUE:
            return None
        if order.value == VALUE_MEAN:
            return [_finite_mean(s.y) for s in series]
        step = self._order_step
        if step is None:
            return None
        return [float(s.step_values.mean[step]) if s.step_values is not None and 0 <= step < len(s.step_values.mean) else math.nan for s in series]

    def set_order_step(self, index: int | None) -> bool:
        """The step the "At step" order reads (the plan bar's one selected step, None: not exactly one); the legend rows are put in order again at once when that order is on.
        Returns True when the lines' stacking follows the legend too (Draw order: Legend) and so needs a redraw: the owner does it."""
        if index == self._order_step:
            return False
        self._order_step = index
        if not self._view.legend_order().needs_step():
            return False
        if self._legend is not None and self._legend_items:
            self._rebuild_legend(self._legend_series, self._legend_items)
        return self._view.draw_mode == DRAW_LEGEND

    def legend_order_note(self) -> str:
        """What the Legend menu should say about the order ("" = nothing): that a step has to be picked on the plan bar, or which one is used."""
        if not self._view.legend_order().needs_step():
            return ""
        if self._order_step is None:
            return "Select exactly one step on the plan bar: the order reads the value at that step."
        if not any(s.step_values is not None for s in self._legend_series):
            return "No step values yet: the plan has no steps, or the lines are not computed."
        return f"Order by the value at step {self._order_step + 1} (selected on the plan bar)."

    def _style_legend(self) -> None:
        """The View tab's legend options: the text size (new entries take it when they are added), the space between rows (the step table's rows follow the legend's
        rows, so they move with it) and whether it is drawn. A hidden legend keeps its entries: the table still lines up with its rows."""
        legend = self._legend
        if legend is None:
            return
        view = self._view
        legend.setLabelTextSize(f"{view.legend_text_pt}pt")
        legend.layout.setVerticalSpacing(float(view.legend_spacing_px))
        legend.setVisible(not self._legend_hidden())

    def _legend_hidden(self) -> bool:
        return self._view.legend_mode == LEGEND_HIDE

    def _tighten_legend(self) -> None:
        """Rows closer together: a legend row is as high as its label (text line + twice the label's document margin, 4 px each by default; the 20 px sample is the floor)."""
        legend = self._legend
        if legend is None:
            return
        for _sample, label in legend.items:
            label.item.document().setDocumentMargin(_LEGEND_TEXT_MARGIN_PX)
            label.updateMin()

    def _fit_legend(self, count: int) -> None:
        """Up to `MAX_LEGEND_SERIES` lines the legend stays; with more it stays only while it takes at most `LEGEND_MAX_HEIGHT_FRACTION` of the plot height (a taller
        panel holds more lines). Judged from the laid-out legend, so the font and the number of lines decide, not a guess."""
        legend = self._legend
        view_height = float(self._item.getViewBox().height())
        if legend is None or not legend.items or count <= MAX_LEGEND_SERIES or view_height <= 0.0 or self._view.legend_mode == LEGEND_SHOW:
            return  # "show" (the View tab) keeps the legend whatever its height
        if self._legend_size()[1] > LEGEND_MAX_HEIGHT_FRACTION * view_height:
            legend.clear()

    def legend_corner_note(self) -> str:
        """What the Legend menu should say about its Corner row ("" = nothing): that the step table, while it is on, keeps the legend at its own edge."""
        return CORNER_STEP_TABLE_NOTE if self._stats.is_shown() else ""

    def legend_entry_count(self) -> int:
        """How many lines the legend names right now (0: no legend)."""
        return 0 if self._legend is None else len(self._legend.items)

    # -- the experiment steps ------------------------------------------------------------------------------------------------

    def plan_bar(self) -> PlanBarItem:
        return self._plan_bar

    def set_plan(self, steps: Sequence[PlanBarStep], settings: PlanBarSettings) -> None:
        """Show these steps (an empty sequence hides the bar). When the room the bar needs changed (its height, its side, the text mode), the view is ranged again."""
        self._plan_bar.set_plan(tuple(steps), settings)
        self._place_legend()  # the legend sits just inside the bar when the step statistics table is on
        self._remake_room_if_changed()

    def _room(self) -> tuple[float, float, float]:
        """(top, bottom, left): the fractions of the plot height the bar, in the Data text mode its labels, and the step statistics table need beyond the traces,
        and the pixels the table's legend needs on the left (so the first step's numbers do not start under it)."""
        left = 0.0
        bar_top = bar_bottom = head_top = head_bottom = rows_top = rows_bottom = 0.0
        view_height = float(self._item.getViewBox().height())
        bar = self._plan_bar
        if bar.is_shown():
            settings = bar.settings()
            text_height = QFontMetricsF(bar.font()).height()
            if not settings.is_full:  # a full-height bar sits behind the traces and needs no room
                # the bar is never lower than one line of its text (`strip_height_px`), so the room is what it really takes
                fraction = settings.height_fraction if view_height <= 0.0 else min(settings.strip_height_px(view_height, text_height) / view_height, 0.8)
                if settings.position == POSITION_TOP:
                    bar_top = fraction
                else:
                    bar_bottom = fraction
            if settings.text_align == ALIGN_DATA and view_height > 0.0:
                above, below = label_headroom_px(settings, text_height)
                head_top, head_bottom = above / view_height, below / view_height
        if self._stats.is_shown() and view_height > 0.0:
            width, height = self._legend_size()
            if height > 0.0:  # the rows are the legend's rows
                rows = (height + 2 * _LEGEND_EDGE_PX) / view_height
                if self._stats.settings().position == POSITION_TOP:
                    rows_top = rows
                else:
                    rows_bottom = rows
                if not self._legend_hidden():  # a hidden legend takes no room on the left: the numbers may start at the edge
                    # the Stats menu's "Legend room" (percent of what holds the legend; 0 = none): the x axis is widened before its start by this much
                    left = (width + _LEGEND_OFFSET_PX[0] + 2 * _STATS_GAP_PX) * max(0, self._stats.settings().legend_room_pct) / 100.0
        if self._legend_ignores_bar():
            # The legend (and the table's rows with it) ignores the bar and sits over it at the edge, so the two share the room: the larger one, not the sum.
            return max(bar_top, rows_top) + head_top, max(bar_bottom, rows_bottom) + head_bottom, left
        return bar_top + head_top + rows_top, bar_bottom + head_bottom + rows_bottom, left

    def _legend_ignores_bar(self) -> bool:
        """The View tab's "at the edge of the plot": the legend and the stats rows ignore the bar and sit over it. Only for a bar that holds no names (they are aligned to the data):
        with the names inside the bar the rows would sit on top of them (maintainer, 2026-11-13: "stats overlap"), so then the rows stay beyond the bar whatever the tick says."""
        bar = self._plan_bar
        names_in_the_bar = bar.is_shown() and bar.settings().text_align != ALIGN_DATA
        return bool(self._view.legend_over_bar) and not names_in_the_bar

    def _remake_room_if_changed(self) -> None:
        """Range the view again, with the room made, when what the bar, its labels and the table need beyond the traces is not what the view was ranged with.
        Not while the user's own zoom is kept: `reset_view` makes the room then."""
        if not self._view_kept and self._room() != self._room_applied:
            self._item.getViewBox().autoRange()
            self._apply_room()

    def _apply_room(self) -> None:
        """Widen the ranges the auto-range just set, so the bar (and the Data-mode labels and the step statistics table) take free space instead of hiding traces:
        the y range at the top and bottom, the x range on the left for the legend that heads the table."""
        self._room_applied = self._room()
        top, bottom, left = self._room_applied
        if top <= 0.0 and bottom <= 0.0 and left <= 0.0:
            return
        view_box = self._item.getViewBox()
        if top > 0.0 or bottom > 0.0:
            low, high = view_box.viewRange()[1]
            view_box.setYRange(*reserved_range(low, high, top, bottom), padding=0.0)
        width = float(view_box.width())
        if left > 0.0 and width > 2.0 * left:
            low, high = view_box.viewRange()[0]
            table = self._stats.table()
            low = min(low, table.columns[0].x_start) if table is not None and table.columns else low  # a first step that starts before the data has room too
            view_box.setXRange(low - (high - low) * left / (width - left), high, padding=0.0)

    # -- the step statistics table and its legend ---------------------------------------------------------------------------

    def set_stats_table(self, table: StatsTable | None, settings: StatsTableSettings) -> None:
        """Show this table (None, or `settings.visible` False, hides it). It sits along the edge `settings.position`, the legend moves into it so that the cells are
        level with the legend's rows, and the plot is ranged again when the room it needs changed."""
        self._stats.set_table(table, settings)
        self._place_legend()
        self._remake_room_if_changed()
        note = self.legend_corner_note()
        if note != self._corner_note:
            self._corner_note = note
            self.legend_corner_note_changed.emit(note)

    def stats_item(self) -> StepStatsItem:
        return self._stats

    def _legend_size(self) -> tuple[float, float]:
        """(width, height) in pixels of the laid-out legend, (0, 0) without one."""
        legend = self._legend
        if legend is None or not legend.items:
            return 0.0, 0.0
        legend.layout.activate()
        legend.updateSize()
        return float(legend.width()), float(legend.height())

    def _legend_rows(self) -> LegendRows | None:
        """Where the legend's rows are, for the table to follow (None: no legend entries)."""
        legend = self._legend
        if legend is None or not legend.items:
            return None
        legend.layout.activate()
        right = None if self._legend_hidden() else float(legend.sceneBoundingRect().right())  # a hidden legend leaves the whole width to the numbers
        centers = [float(label.sceneBoundingRect().center().y()) for _sample, label in legend.items]
        if len(centers) == len(self._legend_rank):  # the rows may be in another order than the lines: the table asks for them in the lines' order
            in_line_order = [0.0] * len(centers)
            for row, line in enumerate(self._legend_rank):
                in_line_order[line] = centers[row]
            centers = in_line_order
        return LegendRows(tuple(centers), right)

    def _bar_height_px(self, position: str) -> float:
        """Pixels the plan bar takes along the edge `position` (0 when it is hidden, full-height, or on the other edge)."""
        bar = self._plan_bar
        if not bar.is_shown():
            return 0.0
        settings = bar.settings()
        if settings.is_full or settings.position != position:
            return 0.0
        return settings.strip_height_px(float(self._item.getViewBox().height()), QFontMetricsF(bar.font()).height())

    def _place_legend(self) -> None:
        """With the table on, the legend sits at its edge just inside the plan bar (or at the very edge, over a bar that holds no names, when the View tab says so:
        `_legend_ignores_bar`), so its rows and the cells line up (the table owns its place). With the table off it is in the corner the Legend menu names (the top-left unless changed) and wherever the user dragged it:
        the corner is applied only when it changed or the table had moved the legend (`_Legend.place_in_corner`). The band the rows take is handed to the bar, so the names of a Full bar stay out of it."""
        legend = self._legend
        if legend is None:
            self._plan_bar.set_keep_out(0.0, 0.0)
            return
        if not self._stats.is_shown():
            # With the table off the legend goes to the corner the Legend menu says (the top-left unless changed), measured from the plot's edges like the top-left always was.
            # It is applied only when the corner changed or the table had moved the legend, so a legend the user dragged stays where it was dropped.
            legend.place_in_corner(self._view.legend_corner, *_LEGEND_OFFSET_PX)
            self._plan_bar.set_keep_out(0.0, 0.0)
            return
        legend.forget_corner()  # the table sets the offset below: the corner is applied again when the table goes
        position = self._stats.settings().position
        # Just inside the bar, or (the Legend menu's "at the edge of the plot") ignoring a bar without names: at the very edge, over the bar.
        gap = _LEGEND_EDGE_PX if self._legend_ignores_bar() else self._bar_height_px(position) + _LEGEND_EDGE_PX
        legend.setOffset((_LEGEND_OFFSET_PX[0], gap if position == POSITION_TOP else -gap))  # a negative offset is measured from the bottom
        height = self._legend_size()[1]
        band = gap + height + _LEGEND_EDGE_PX if height > 0.0 else 0.0  # from the edge of the plot to the far side of the rows
        self._plan_bar.set_keep_out(band if position == POSITION_TOP else 0.0, band if position != POSITION_TOP else 0.0)

    def _on_scene_moved(self, scene_pos) -> None:
        """The bar's tooltip is the one of the step under the pointer, and the cursor shows a resize arrow over a boundary; set on every move, so they are right when Qt
        asks for them. Not while a button is down (a drag in progress keeps what it has)."""
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        bar = self._plan_bar
        if bar.is_shown():
            bar.hover_at(bar.mapFromScene(scene_pos))
        if self._stats.is_shown():
            self._stats.hover_at(self._stats.mapFromScene(scene_pos))  # the full numbers of the cell under the pointer

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

    def begin_play_repaints(self) -> None:
        """Play started: from now on the plot repaints the cursor strip once per frame (`repaint_play_cursor`), not on every scene change."""
        self._play_repaint.start()

    def end_play_repaints(self) -> None:
        """Play stopped: the plot repaints on scene changes again."""
        self._play_repaint.stop()

    def repaint_play_cursor(self) -> None:
        """The cursor has moved for the frame about to be shown: its old and new place are repainted with the window's next repaint."""
        self._play_repaint.frame_shown()

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
        if event.isAccepted() and not isinstance(getattr(event, "currentItem", None), pg.ScatterPlotItem):
            # Another item handled it (the plan bar, a baseline band). A scatter item (the dots at every cube, the anchor squares, the mask marks)
            # accepts every left click that lands on one of its points but does nothing with it: such a click is still a click on the plot.
            # (2026-11-09: before this, clicking on a trace - where an anchor point belongs - was mostly lost.)
            return
        view_box = self._item.getViewBox()
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        x = float(view_box.mapSceneToView(event.scenePos()).x())
        if self._anchor_mode:
            self.anchor_point_added.emit(x)
            return
        self._plan_bar.clear_selection()  # a click on the plot, not on a step: no step stays selected
        self.x_clicked.emit(x)

    # -- the drift baseline's anchor points ----------------------------------------------------------------------------------

    def set_anchor_mode(self, on: bool) -> None:
        """Anchor-edit mode: a left click adds an anchor point (`anchor_point_added`), a right click on an anchor square removes it
        (`anchor_point_remove_requested`) and a right click anywhere else asks for the anchor menu (`anchor_menu_requested`); the cube cursor, the plot's own right-click menu and every gesture of the plan bar (select, resize, retype, its menu) wait until it is off. A cross-hair
        pointer shows the mode."""
        self._anchor_mode = bool(on)
        self._plan_bar.set_locked(self._anchor_mode)  # no step edits while anchors are edited: every click is an anchor click
        if self._anchor_mode:
            self._plan_bar.clear_selection()
        self._apply_anchor_cursor()
        # pyqtgraph turns a press into a drag (and so no click) once the mouse moves 5 px, or moves at all after 0.5 s held. Nothing is dragged with
        # the left button in the edit mode, so a slow or slightly shaky click still counts; only a real move (> 12 px) is a drag (middle-button pan).
        scene = self._item.scene()
        if self._drag_defaults is None:
            self._drag_defaults = (scene.minDragTime, scene._moveDistance)
        drag_time, move_distance = (_ANCHOR_DRAG_TIME_S, _ANCHOR_MOVE_PX) if self._anchor_mode else self._drag_defaults
        scene.minDragTime = drag_time
        scene.setMoveDistance(move_distance)

    def anchor_mode(self) -> bool:
        return self._anchor_mode

    def set_anchors_needed(self, needed: int) -> None:
        """How many more anchor points the chosen shape needs (0 = enough). Shown as a number beside the edit mode's cross-hair, so the count
        goes 2 -> 1 -> gone as the points of a linear baseline are placed."""
        needed = max(0, int(needed))
        if needed != self._anchors_needed:
            self._anchors_needed = needed
            self._apply_anchor_cursor()

    def anchors_needed(self) -> int:
        return self._anchors_needed

    def _apply_anchor_cursor(self) -> None:
        viewport = self._widget.viewport()
        if self._anchor_mode:
            viewport.setCursor(_anchor_cursor(self._anchors_needed))
        else:
            viewport.unsetCursor()

    def _on_anchor_right_click(self, scene_pos, screen_pos=None) -> bool:
        """A right click in the anchor edit: on (within `_ANCHOR_HIT_PX` of) an anchor square it removes that anchor; anywhere else on the plot it
        asks for the anchor menu (add here, clear all, scope, stop). Either way the view's own menu does not open."""
        if not self._anchor_mode:
            return False
        view_box = self._item.getViewBox()
        if not view_box.sceneBoundingRect().contains(scene_pos):
            return False
        hit = self.anchor_square_at(scene_pos)
        if hit is not None:
            self.anchor_point_remove_requested.emit(hit)
        else:
            point = screen_pos.toPoint() if screen_pos is not None else QPoint()
            self.anchor_menu_requested.emit(float(view_box.mapSceneToView(scene_pos).x()), point)
        return True

    def anchor_square_at(self, scene_pos) -> float | None:
        """The data x of the anchor square drawn nearest to `scene_pos` if it is within `_ANCHOR_HIT_PX` screen pixels, else None."""
        view_box = self._item.getViewBox()
        best, best_x = _ANCHOR_HIT_PX, None
        for part in self._svg_parts:
            if part.layer != "Baseline anchors":
                continue
            xs, ys = part.item.getData()
            for x, y in zip(xs, ys):
                if not (np.isfinite(x) and np.isfinite(y)):
                    continue
                at = view_box.mapViewToScene(QPointF(float(x), float(y)))
                distance = float(np.hypot(at.x() - scene_pos.x(), at.y() - scene_pos.y()))
                if distance <= best:
                    best, best_x = distance, float(x)
        return best_x
