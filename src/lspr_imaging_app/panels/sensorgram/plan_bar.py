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

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPainterPath, QPen, QTransform
from PyQt6.QtWidgets import QApplication

from lspr_ui import load_tabler_icon

from ...plan.bar_model import (
    ALIGN_DATA,
    MIN_TEXT_WIDTH_PX,
    POSITION_BOTTOM,
    POSITION_TOP,
    ROLES_AUTO,
    SIDE_RIGHT,
    PlanBarSettings,
    PlanBarStep,
    click_selection,
    contrast_text_color,
    grab_edge,
    text_top_y,
)
from ...plan.edits import BULK_CHIP
from ...plan.role_colors import GRAY_MIDDLE, KIND_BULK, bulk_grays, parse_role, role_fill
from .device_item import DeviceSpaceItem

Z_VALUE = -20  # below the filled bands of an average (-10), the baseline selector (-5) and every trace (0)
EDGE_TOLERANCE_PX = 5.0  # how close to a boundary the pointer must be to take hold of it
_EDGE_ALPHA = 90
_TEXT_SIDE_MARGIN_PX = 4.0
_ANCHOR_MARK_PX = 12.0  # the anchor drawn in a Range baseline anchor step's lower left corner
_CHIP_GAP_PX = 3.0  # between the evaluation chips of one step
_CHIP_MARGIN_PX = 2.0  # from the step's end and from the edge the chips are measured from


@dataclass(frozen=True)
class DrawnChip:
    """One evaluation chip (``B1``, ``S1``, ``Sb``) as it is placed, in device pixels."""

    step_index: int
    role: str
    rect: QRectF
    fill: str  # '#rrggbb'
    alpha: float
    outlined: bool  # the S_bulk greys: a white or a black chip needs an edge on a plot of either colour


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


class PlanBarItem(DeviceSpaceItem):
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
        self._locked = False  # no pointer gestures (the Sensorgram's anchor-point edit mode): `set_locked`
        self._drag_together = False  # the boundary being held is shared by two selected steps: both move
        self._baseline_anchors: frozenset[int] = frozenset()  # steps marked as Range baseline anchors: a small anchor in their lower left corner
        self._anchor_icons: dict[tuple[str, int], QIcon] = {}  # (colour, size in px) -> the anchor mark, made once (a handful: one per step colour)
        self._bulk_grays: dict[int, str] = {}  # step index -> the grey of its Sb chip, made from ALL the steps of the plan (so it does not change while panning or zooming)
        self.setZValue(Z_VALUE)

    # -- what to draw ---------------------------------------------------------------------------------------------------

    def set_plan(self, steps: tuple[PlanBarStep, ...] | list[PlanBarStep], settings: PlanBarSettings) -> None:
        self.prepareGeometryChange()
        self._steps = tuple(steps)
        self._settings = settings
        # the S_bulk steps' greys, from dark (lowest level) to white (highest), judged over every step of the plan, not only those in view
        self._bulk_grays = bulk_grays({s.index: None if s.levels is None else s.levels.median for s in self._steps if BULK_CHIP in s.roles})
        present = {step.index for step in self._steps}
        if any(index not in present for index in self._selected):
            self._set_selection(tuple(i for i in self._selected if i in present), self._anchor if self._anchor in present else None)  # a step is gone (another dataset, no traces)
        self.update()

    def set_baseline_anchors(self, indices: frozenset[int] | set[int]) -> None:
        """The steps (by index) marked as anchors of the Range baseline (2026-11-09): each gets a small anchor in its lower left corner."""
        indices = frozenset(int(i) for i in indices)
        if indices != self._baseline_anchors:
            self._baseline_anchors = indices
            self.update()

    def baseline_anchors(self) -> frozenset[int]:
        return self._baseline_anchors

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

    def boundingRect(self) -> QRectF:
        return self.view_rect() if self.is_shown() else QRectF()

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

    def set_locked(self, locked: bool) -> None:
        """Locked (the Sensorgram's anchor-point edit mode, 2026-11-09): the bar is still drawn but takes no pointer gesture at all (no select, no boundary
        drag, no text edit, no menu, no resize cursor), so every click goes to the plot underneath."""
        self._locked = bool(locked)
        if self._locked:
            self.setToolTip("")
            self.unsetCursor()

    def is_locked(self) -> bool:
        return self._locked

    def claims_scene_pos(self, scene_pos: QPointF) -> bool:
        """Whether a click at this scene position is on a step (the plot asks before the ViewBox raises its own menu)."""
        return not self.is_locked() and self.is_shown() and self.step_at_local(self.mapFromScene(scene_pos)) is not None

    def device_rect_of(self, index: int) -> QRectF | None:
        """Where the bar of step `index` is in viewport pixels (None when it is not in view): the in-place editor goes there."""
        transform = self._device_transform()
        if transform is None or not self.is_shown():
            return None
        return next((d.rect for d in self.layout(transform, self.view_rect()) if d.step.index == index), None)

    def hover_at(self, local: QPointF) -> None:
        """The pointer is over the plot at this point (item coordinates): show the step's tooltip, and a resize cursor over a boundary."""
        if self.is_locked():
            return
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

    # -- pointer gestures -----------------------------------------------------------------------------------------------------

    def mouseDragEvent(self, ev) -> None:
        """Left-drag on a boundary moves it. Anything else is declined, so the plot's own drags (the baseline selector, the cube handle) are untouched."""
        if ev.button() != Qt.MouseButton.LeftButton or not self.is_shown() or (self.is_locked() and self._dragging is None):
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

    def mouseClickEvent(self, ev) -> None:
        step = self.step_at_local(ev.pos()) if self.is_shown() and not self.is_locked() else None
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

    def paint(self, painter: QPainter, option, widget=None) -> None:
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
                    painter.drawRect(QRectF(x - 1.2, item.rect.top(), 2.4, item.rect.height()))  # 2.4 px wide (was 6, 60 % thinner, maintainer 2026-10-26)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in drawn:
            if item.text_rect is not None:
                painter.setPen(QColor(item.text_color))
                painter.drawText(item.text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), item.text)
        self._paint_role_chips(painter, drawn, area)
        self._paint_baseline_anchors(painter, drawn)
        painter.restore()

    def _paint_baseline_anchors(self, painter: QPainter, drawn: list[DrawnStep]) -> None:
        """A small anchor (Tabler's) in the lower left corner of every step marked as a Range baseline anchor, in the colour that reads on the step's fill."""
        if not self._baseline_anchors:
            return
        for item in drawn:
            if item.step.index not in self._baseline_anchors:
                continue
            size = min(_ANCHOR_MARK_PX, item.rect.height() - 2.0, item.rect.width() - 2.0)
            if size < 6.0:
                continue  # no room in a very thin or narrow step
            color = QColor(contrast_text_color(item.step.color, self._settings.alpha, self._background)).name()
            spot = QRectF(item.rect.left() + 2.0, item.rect.bottom() - 2.0 - size, size, size)
            key = (color, int(round(size)))
            icon = self._anchor_icons.get(key)
            if icon is None:  # kept: the bar is repainted on every pan, zoom and hover, and making the icon parses its SVG each time
                icon = self._anchor_icons[key] = load_tabler_icon("anchor", color=color, size=key[1])
            icon.paint(painter, spot.toRect())

    def _chip_font(self) -> QFont:
        font = self.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        font.setBold(True)
        return font

    def _chip_fill(self, role: str, step_index: int) -> tuple[str, float, bool]:
        """(colour, opacity, outlined) of one chip. A pair is one colour: its signal chip (``S1``) in the pair's colour, its baseline chip (``B1``) the same colour darker and a little
        more transparent (`plan/role_colors.py`). ``Sb``: a grey from dark to white by the step's level among the S_bulk steps. Anything else: neutral, as the baseline chip once was."""
        fill = role_fill(role)
        if fill is not None:
            return fill[0], fill[1], False
        if parse_role(role)[0] == KIND_BULK:
            return self._bulk_grays.get(step_index, f"#{GRAY_MIDDLE:02x}{GRAY_MIDDLE:02x}{GRAY_MIDDLE:02x}"), 1.0, True
        return self._text_color, 0.43, False

    def _chip_top(self, rect: QRectF, area: QRectF, height: float) -> float:
        """Device y of the top of the chips of the step whose bar segment is `rect`. ``roles_edge`` auto: hanging from the bar's free edge (below a bar at the top, above a bar at the
        bottom, inside a full bar at its top), as always. Top / bottom: that edge of the plot; when the bar is on that edge the chips hang from its inner edge (inside a full bar:
        at its top / bottom)."""
        settings = self._settings
        want = settings.position if settings.roles_edge == ROLES_AUTO else settings.roles_edge
        if settings.is_full:
            return rect.bottom() - _CHIP_MARGIN_PX - height if want == POSITION_BOTTOM and settings.roles_edge != ROLES_AUTO else rect.top() + _CHIP_MARGIN_PX
        if want == POSITION_BOTTOM:
            return rect.top() - _CHIP_MARGIN_PX - height if settings.position == POSITION_BOTTOM else area.bottom() - _CHIP_MARGIN_PX - height
        return rect.bottom() + _CHIP_MARGIN_PX if settings.position == POSITION_TOP else area.top() + _CHIP_MARGIN_PX

    def role_chips(self, drawn: list[DrawnStep], area: QRectF) -> list[DrawnChip]:
        """Where every evaluation chip goes (no painting): a row of chips per step, at the left or the right end of its bar segment (``roles_side``), in the order of the step's roles
        read left to right either way, at the edge `_chip_top` says. None when the chips are switched off (``show_roles``)."""
        if not self._settings.show_roles:
            return []
        metrics = QFontMetricsF(self._chip_font())
        height = metrics.height() + 2.0
        chips: list[DrawnChip] = []
        for item in drawn:
            roles = item.step.roles
            if not roles:
                continue
            widths = [metrics.horizontalAdvance(role) + 8.0 for role in roles]
            if self._settings.roles_side == SIDE_RIGHT:
                x = min(item.rect.right(), area.right()) - _CHIP_MARGIN_PX - (sum(widths) + _CHIP_GAP_PX * (len(roles) - 1))
            else:
                x = max(item.rect.left(), area.left()) + _CHIP_MARGIN_PX
            y = self._chip_top(item.rect, area, height)
            for role, width in zip(roles, widths):
                fill, alpha, outlined = self._chip_fill(role, item.step.index)
                chips.append(DrawnChip(item.step.index, role, QRectF(x, y, width, height), fill, alpha, outlined))
                x += width + _CHIP_GAP_PX
        return chips

    def _paint_role_chips(self, painter: QPainter, drawn: list[DrawnStep], area: QRectF) -> None:
        """The evaluation roles of each step (``B1`` baseline of pair 1, ``S1`` signal ..., ``Sb`` S_bulk step) as small chips placed by `role_chips`, away from the step's text."""
        chips = self.role_chips(drawn, area)
        if not chips:
            return
        painter.setFont(self._chip_font())
        edge = QColor(self._text_color)
        edge.setAlpha(170)
        for chip in chips:
            fill = QColor(chip.fill)
            fill.setAlphaF(chip.alpha)
            painter.setPen(QPen(edge, 1.0) if chip.outlined else Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(chip.rect, 3.0, 3.0)
            painter.setPen(QColor(contrast_text_color(chip.fill, chip.alpha, self._background)))
            painter.drawText(chip.rect, int(Qt.AlignmentFlag.AlignCenter), chip.role)
        painter.setBrush(Qt.BrushStyle.NoBrush)
