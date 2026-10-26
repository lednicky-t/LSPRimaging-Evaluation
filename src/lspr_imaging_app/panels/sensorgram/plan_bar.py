"""``PlanBarItem``: the experiment steps drawn as a coloured bar along the top or bottom of the Sensorgram plot (2026-10-26).

Screen purpose: show which solution ran when, on the same x axis as the traces (time or cube number), in the way the singleLSPR Acquisition sensorgram shows its
pump-plan steps, and let the user change them with the pointer. Design: ``docs/experimental_plan_bar_design_2026-10-26.md``; what is drawn and where the text goes is computed
by ``plan/bar_model.py`` (pure); this file paints it and turns pointer gestures into signals.

One item for all steps, added to the plot's ViewBox. Its x is in data coordinates (it follows pan and zoom); its y is glued to the edge of the plot, so everything is
drawn in device pixels: ``paint`` reads the item-to-device transform, resets the painter and draws in pixels (a text drawn through the data transform would be scaled with
the axes). It is drawn **behind** the traces and behind the baseline selector (z -20): where they overlap the baseline band is on top and gets the pointer first.

**Gestures** (the item only reports them; ``plan_actions.py`` turns them into commands of ``ExperimentPlanModule``):
- left click on a step **selects** it; Ctrl+click adds it to the selection (or takes it out), Shift+click adds it and every step between it and the last one clicked. Selected steps are
  marked by a solid bar on each of their left and right edges. A click elsewhere in the plot, or `clear_selection`, deselects. A single click on a step is taken by the bar, so it does
  not move the cube slider.
- left-drag on an edge of a **selected** step (5 px either side): ``edge_drag_started`` / ``edge_dragged`` (the data x under the pointer, and whether the boundary is moved with its
  neighbour: Shift is held, or both steps that meet there are selected) / ``edge_drag_finished``. Only selected steps' edges can be grabbed, so two steps that meet at one boundary
  never compete for it. A left-drag anywhere else is not accepted, so the plot behaves as before.
- left double-click on a step (its bar, or its label in the Data mode): ``text_edit_requested``.
- right-click on a step: selects it and asks for ``menu_requested(index, screen position)``. pyqtgraph's ViewBox takes a right-click before any item, so the plot lets it ask ``claims_scene_pos`` first.
- hover: the step's tooltip, and a resize cursor over a boundary (``hover_at``, driven by the plot's mouse-move).

The hit area (`shape`) is the strip of the bar, widened by the boundary tolerance, plus the labels of the Data mode: elsewhere in the plot nothing changes.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QTransform
from PyQt6.QtWidgets import QApplication

from ...plan.bar_model import (
    ALIGN_DATA,
    MIN_TEXT_WIDTH_PX,
    POSITION_TOP,
    PlanBarSettings,
    PlanBarStep,
    click_selection,
    contrast_text_color,
    grab_edge,
    text_top_y,
)

Z_VALUE = -20  # below the filled bands of an average (-10), the baseline selector (-5) and every trace (0)
EDGE_TOLERANCE_PX = 5.0  # how close to a boundary the pointer must be to take hold of it
_EDGE_ALPHA = 90
_TEXT_SIDE_MARGIN_PX = 4.0


@dataclass(frozen=True)
class DrawnStep:
    """One step as it is painted, in device pixels."""

    step: PlanBarStep
    rect: QRectF  # the bar segment, clipped to the plot area
    left_edge: float | None  # device x of the step's start / end when inside the plot area (a boundary line is drawn there)
    right_edge: float | None
    text: str  # elided to fit; empty when no text is drawn
    text_rect: QRectF | None
    text_color: str


class PlanBarItem(pg.GraphicsObject):
    edge_drag_started = pyqtSignal(int, str)  # (step index, "start" | "end")
    edge_dragged = pyqtSignal(int, str, float, bool)  # ... the data x under the pointer, and whether to move the shared boundary with the neighbour (Shift, or both selected)
    edge_drag_finished = pyqtSignal()
    text_edit_requested = pyqtSignal(int)  # step index
    menu_requested = pyqtSignal(int, QPoint)  # step index, where on the screen
    selection_changed = pyqtSignal(object)  # the selected steps' indices, a sorted tuple (empty: none)

    def __init__(self) -> None:
        super().__init__()
        self._steps: tuple[PlanBarStep, ...] = ()
        self._settings = PlanBarSettings()
        self._text_color = "#e6edf3"  # text drawn on the plot background (the Data mode), and the colour of the boundary lines
        self._background = "#1b1f24"  # the plot background, for the text colour on a transparent fill
        self._accent = "#4e8cff"  # the bars on the edges of the selected steps
        self._selected: tuple[int, ...] = ()  # indices of the selected steps (sorted); only their edges can be dragged
        self._anchor: int | None = None  # the step a Shift-click measures its range from
        self._dragging: tuple[int, str] | None = None  # (step index, edge) while a boundary is held
        self._drag_together = False  # the boundary being held is shared by two selected steps: both move
        self.setZValue(Z_VALUE)

    # -- what to draw ---------------------------------------------------------------------------------------------------

    def set_plan(self, steps: tuple[PlanBarStep, ...] | list[PlanBarStep], settings: PlanBarSettings) -> None:
        self.prepareGeometryChange()
        self._steps = tuple(steps)
        self._settings = settings
        present = {step.index for step in self._steps}
        if any(index not in present for index in self._selected):
            self._set_selection(tuple(i for i in self._selected if i in present), self._anchor if self._anchor in present else None)  # a step is gone (another dataset, no traces)
        self.update()

    def set_theme(self, text_color: str, background: str, accent: str | None = None) -> None:
        self._text_color, self._background = text_color, background
        if accent is not None:
            self._accent = accent
        self.update()

    # -- selection ------------------------------------------------------------------------------------------------------------

    def selected_indices(self) -> tuple[int, ...]:
        return self._selected

    def is_selected(self, index: int) -> bool:
        return index in self._selected

    def select(self, index: int | None) -> None:
        """Select only the step with this index (None: none). Never a step that is not on the bar."""
        self.select_many(() if index is None else (index,), anchor=index)

    def select_many(self, indices: tuple[int, ...] | list[int], anchor: int | None = None) -> None:
        self._set_selection(tuple(indices), anchor)

    def click_select(self, index: int, *, ctrl: bool = False, shift: bool = False) -> None:
        """What a left click on step `index` does to the selection (see `bar_model.click_selection`)."""
        selection, anchor = click_selection(self._selected, self._anchor, index, [step.index for step in self._steps], ctrl=ctrl, shift=shift)
        self._set_selection(selection, anchor)

    def clear_selection(self) -> None:
        self._set_selection((), None)

    def _set_selection(self, indices: tuple[int, ...], anchor: int | None) -> None:
        present = {step.index for step in self._steps}
        indices = tuple(sorted({i for i in indices if i in present}))
        self._anchor = anchor if anchor in present and indices else None
        if indices == self._selected:
            return
        self._selected = indices
        self.update()
        self.selection_changed.emit(indices)

    def steps(self) -> tuple[PlanBarStep, ...]:
        return self._steps

    def settings(self) -> PlanBarSettings:
        return self._settings

    def is_shown(self) -> bool:
        return bool(self._steps) and self._settings.visible

    def is_dragging(self) -> bool:
        return self._dragging is not None

    # -- geometry in data coordinates ---------------------------------------------------------------------------------------

    def view_rect(self) -> QRectF:
        """The visible part of the plot in data coordinates (x range, y range); empty without a view."""
        view = self.getViewBox()
        return QRectF() if view is None else QRectF(view.viewRect())

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        return self.view_rect() if self.is_shown() else QRectF()

    def _device_transform(self) -> QTransform | None:
        """Data -> viewport pixels, or None when the item is not in a view yet."""
        widget = self.getViewWidget()
        return None if widget is None else self.deviceTransform(widget.viewportTransform())

    def _pixel_width(self) -> float:
        """Data units per pixel along x (0 without a view)."""
        transform = self._device_transform()
        if transform is None:
            return 0.0
        inverse, ok = transform.inverted()
        return abs(inverse.map(QPointF(1.0, 0.0)).x() - inverse.map(QPointF(0.0, 0.0)).x()) if ok else 0.0

    def _strip_data(self, view: QRectF) -> tuple[float, float]:
        """(y0, y1) of the bar in data coordinates, y0 < y1. The data y axis grows upwards, so the top of the plot is the view rect's `bottom()`."""
        settings = self._settings
        if settings.is_full:
            return view.top(), view.bottom()
        height = settings.height_fraction * view.height()
        if settings.position == POSITION_TOP:
            return view.bottom() - height, view.bottom()
        return view.top(), view.top() + height

    def _text_rects_data(self) -> list[tuple[PlanBarStep, QRectF]]:
        """The labels that sit outside the bar (the Data mode), in data coordinates, so that they answer the pointer too."""
        transform = self._device_transform()
        if transform is None or self._settings.text_align != ALIGN_DATA:
            return []
        inverse, ok = transform.inverted()
        if not ok:
            return []
        return [(d.step, inverse.mapRect(d.text_rect)) for d in self.layout(transform, self.view_rect()) if d.text_rect is not None]

    def shape(self) -> QPainterPath:
        """Only the bar's strip (a few pixels wider at each boundary) and the labels answer hover and clicks, not the whole plot that the bounding
        rectangle covers, so a drag elsewhere still pans."""
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)  # the default odd-even rule makes the overlap of two neighbouring rectangles (exactly the boundary between two steps) a hole
        view = self.view_rect()
        if not self.is_shown() or view.isEmpty():
            return path
        y0, y1 = self._strip_data(view)
        tolerance = EDGE_TOLERANCE_PX * self._pixel_width()
        for step in self._steps:
            path.addRect(QRectF(step.x_start - tolerance, y0, step.x_end - step.x_start + 2.0 * tolerance, y1 - y0).normalized())
        for _step, rect in self._text_rects_data():
            path.addRect(rect)
        return path

    def step_at_x(self, x: float) -> PlanBarStep | None:
        return next((step for step in self._steps if step.x_start <= x < step.x_end), None)

    def tooltip_at(self, x: float) -> str:
        step = self.step_at_x(x)
        return "" if step is None else step.tooltip

    def step_at_local(self, local: QPointF) -> PlanBarStep | None:
        """The step under a point (item coordinates): the one whose bar is there, else the one whose label is."""
        view = self.view_rect()
        if self.is_shown() and not view.isEmpty():
            y0, y1 = self._strip_data(view)
            if y0 <= local.y() <= y1:
                step = self.step_at_x(local.x())
                if step is not None:
                    return step
        for step, rect in self._text_rects_data():
            if rect.contains(local):
                return step
        return None

    def edge_at_local(self, local: QPointF) -> tuple[int, str, bool] | None:
        """(step index, edge, together) of the selected step's edge under a point on the bar, or None. With nothing selected no edge can be taken; a label outside the bar has no
        edges. `together`: the edge is a boundary two selected steps share, so both move (`bar_model.grab_edge`)."""
        view = self.view_rect()
        if not self._selected or not self.is_shown() or view.isEmpty():
            return None
        y0, y1 = self._strip_data(view)
        if not y0 <= local.y() <= y1:
            return None
        return grab_edge(self._steps, self._selected, local.x(), EDGE_TOLERANCE_PX * self._pixel_width())

    def claims_scene_pos(self, scene_pos: QPointF) -> bool:
        """Whether a click at this scene position is on a step (the plot asks before the ViewBox raises its own menu)."""
        return self.is_shown() and self.step_at_local(self.mapFromScene(scene_pos)) is not None

    def device_rect_of(self, index: int) -> QRectF | None:
        """Where the bar of step `index` is in viewport pixels (None when it is not in view): the in-place editor goes there."""
        transform = self._device_transform()
        if transform is None or not self.is_shown():
            return None
        return next((d.rect for d in self.layout(transform, self.view_rect()) if d.step.index == index), None)

    def hover_at(self, local: QPointF) -> None:
        """The pointer is over the plot at this point (item coordinates): show the step's tooltip, and a resize cursor over a boundary."""
        step = self.step_at_local(local)
        if step is None:
            self.setToolTip("")
        else:
            hint = (
                "Drag its edges to resize it. Shift moves a shared boundary with the neighbouring step (so does selecting both). Ctrl+click adds a step, Shift+click a range."
                if step.index in self._selected else "Click to select it (Ctrl+click adds, Shift+click adds a range), then drag its edges."
            )
            self.setToolTip(f"{step.tooltip}\n{hint}")
        if self.edge_at_local(local) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()

    def viewTransformChanged(self) -> None:  # noqa: N802 - pyqtgraph naming
        self.prepareGeometryChange()  # the bounding rectangle is the visible region
        self.update()

    viewRangeChanged = viewTransformChanged  # noqa: N815 - both hooks mean the same here

    # -- pointer gestures -----------------------------------------------------------------------------------------------------

    def mouseDragEvent(self, ev) -> None:  # noqa: N802 - pyqtgraph naming
        """Left-drag on a boundary moves it. Anything else is declined, so the plot's own drags (the baseline selector, the cube handle) are untouched."""
        if ev.button() != Qt.MouseButton.LeftButton or not self.is_shown():
            ev.ignore()
            return
        if ev.isStart():
            hit = self.edge_at_local(ev.buttonDownPos())
            if hit is None:
                ev.ignore()
                return
            self._dragging, self._drag_together = (hit[0], hit[1]), hit[2]
            self.edge_drag_started.emit(hit[0], hit[1])
        if self._dragging is None:
            ev.ignore()
            return
        ev.accept()
        index, edge = self._dragging
        self.edge_dragged.emit(index, edge, float(ev.pos().x()), self._drag_together or bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        if ev.isFinish():
            self._dragging = None
            self._drag_together = False
            self.edge_drag_finished.emit()

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 - pyqtgraph naming
        step = self.step_at_local(ev.pos()) if self.is_shown() else None
        if step is None:
            ev.ignore()
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()  # taken: a click on the bar must not move the cube slider
            if ev.double():
                if step.index not in self._selected:
                    self.select(step.index)  # the click before it already did, unless this one came alone
                self.text_edit_requested.emit(step.index)
            else:
                modifiers = ev.modifiers()
                self.click_select(
                    step.index, ctrl=bool(modifiers & Qt.KeyboardModifier.ControlModifier), shift=bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
                )
        elif ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            if step.index not in self._selected:
                self.select(step.index)  # the menu acts on the selection; on a step outside it, on that step alone
            screen = ev.screenPos()
            self.menu_requested.emit(step.index, QPoint(int(screen.x()), int(screen.y())))
        else:
            ev.ignore()

    # -- layout and painting (device pixels) ---------------------------------------------------------------------------------

    def font(self) -> QFont:
        font = QFont(QApplication.font())
        font.setPointSizeF(float(self._settings.text_pt))
        return font

    def layout(self, to_device: QTransform, view: QRectF) -> list[DrawnStep]:
        """Where each step and its text go, given the data -> device transform and the visible data rectangle. No painting."""
        settings = self._settings
        area = to_device.mapRect(view)
        if area.isEmpty():
            return []
        if settings.is_full:
            bar_top, bar_bottom = area.top(), area.bottom()
        elif settings.position == POSITION_TOP:
            bar_top, bar_bottom = area.top(), area.top() + settings.height_fraction * area.height()
        else:
            bar_top, bar_bottom = area.bottom() - settings.height_fraction * area.height(), area.bottom()
        metrics = QFontMetricsF(self.font())
        text_height = metrics.height()
        drawn: list[DrawnStep] = []
        for step in self._steps:
            x0 = to_device.map(QPointF(step.x_start, 0.0)).x()
            x1 = to_device.map(QPointF(step.x_end, 0.0)).x()
            left, right = max(x0, area.left()), min(x1, area.right())
            if right <= left:
                continue
            rect = QRectF(left, bar_top, right - left, bar_bottom - bar_top)
            text, text_rect, text_color = "", None, self._text_color
            available = rect.width() - 2.0 * _TEXT_SIDE_MARGIN_PX
            if step.text and available >= MIN_TEXT_WIDTH_PX:
                levels_y = None
                if step.levels is not None and settings.text_align == ALIGN_DATA:
                    levels_y = tuple(to_device.map(QPointF(0.0, v)).y() for v in (step.levels.high, step.levels.median, step.levels.low))
                top = text_top_y(
                    settings, text_height=text_height, area_top=area.top(), area_bottom=area.bottom(), bar_top=bar_top, bar_bottom=bar_bottom, levels_y=levels_y
                )
                if top is not None:
                    text = metrics.elidedText(step.text, Qt.TextElideMode.ElideRight, int(available))
                    text_rect = QRectF(rect.left() + _TEXT_SIDE_MARGIN_PX, top, available, text_height)
                    on_fill = settings.text_align != ALIGN_DATA or settings.is_full or levels_y is None
                    if on_fill:
                        text_color = contrast_text_color(step.color, settings.alpha, self._background)
            drawn.append(
                DrawnStep(
                    step=step, rect=rect, left_edge=x0 if area.left() <= x0 <= area.right() else None,
                    right_edge=x1 if area.left() <= x1 <= area.right() else None, text=text, text_rect=text_rect, text_color=text_color,
                )
            )
        return drawn

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802 - Qt naming
        if not self.is_shown():
            return
        view = self.view_rect()
        if view.isEmpty():
            return
        to_device = painter.worldTransform()
        drawn = self.layout(to_device, view)
        if not drawn:
            return
        area = to_device.mapRect(view)
        painter.save()
        painter.resetTransform()  # from here on x and y are device pixels
        painter.setClipRect(area)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())
        edge = QColor(self._text_color)
        edge.setAlpha(_EDGE_ALPHA)
        for item in drawn:
            fill = QColor(item.step.color)
            fill.setAlphaF(min(max(self._settings.alpha, 0.0), 1.0))
            painter.fillRect(item.rect, fill)
            painter.setPen(QPen(edge, 1.0))
            for x in (item.left_edge, item.right_edge):
                if x is not None:
                    painter.drawLine(QPointF(x, item.rect.top()), QPointF(x, item.rect.bottom()))
        accent = QColor(self._accent)
        for item in drawn:
            if item.step.index not in self._selected:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            for x in (item.left_edge, item.right_edge):  # solid bars on the left and right edge of every selected step; no frame along the top and bottom
                if x is not None:
                    painter.drawRect(QRectF(x - 3.0, item.rect.top(), 6.0, item.rect.height()))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in drawn:
            if item.text_rect is not None:
                painter.setPen(QColor(item.text_color))
                painter.drawText(item.text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), item.text)
        painter.restore()
