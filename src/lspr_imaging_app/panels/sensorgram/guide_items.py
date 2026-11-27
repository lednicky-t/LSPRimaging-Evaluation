"""``GuideLines``: draws the guides of the Plot group (horizontal reference lines, 2026-11-27) on a pyqtgraph plot, and lets the mouse move them. Display only.

One helper per plot (Sensorgram, LbL, S_bulk). The owner calls `set_guides` with the guides of its y quantity whenever they change (and after a window has cleared its plot: `PlotItem.clear()`
takes every item out), `refresh_theme` after a theme switch, and gives `parts()` to the figure export.

- Each guide is a `GuideLine` (a `pg.InfiniteLine`, angle 0) behind the traces (z = -8: above the bands, below every line), with its label written above the line at the right end. With
  "show value" the label ends with the guide's y value, which follows the line while it is dragged.
- **Moving**: a guide can be dragged with the left button. The pointer over it is an open hand, a closed one while it is held. The new value is reported once, on release (`moved(index, value)`,
  rounded to a thousandth of the view's height, queued so that the owner may redraw the guides in its slot); nothing is saved here.
- **Where is a guide**: `index_at(scene_pos)` answers which guide (if any) lies within a few pixels of a scene position; the plot uses it to give a right-click on a guide to the guide's menu
  (pyqtgraph hands right-clicks to the view first).
- It never takes part in the autoscale (`ignoreBounds`), so a guide far from the data does not stretch the view.
- The default colour is the theme's muted text colour, so it stays legible on every theme; a chosen colour is kept as saved.
- Export: every guide is a part of the "Guides" layer (`svg_layers.SvgPart`), named by its value and label, so it is its own object in Inkscape. A line does not look different when it is
  hovered in a figure (the hover look belongs to the screen).
"""

from __future__ import annotations

from collections.abc import Callable

import pyqtgraph as pg
from PyQt6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor

from lspr_ui import get_active_theme

from ...domain.guide_lines import Guide, rounded_for_span
from ..menu_icons import give_persistent_icon
from ..svg_layers import SvgPart
from .guide_controls import FULL_NOTE, GuideControls

GUIDE_Z = -8.0  # above the bands (-10), below the baseline window (-5) and every trace (0)
HIT_PX = 5.0  # a pointer this close (screen pixels) to a guide is on it
_PEN_STYLE = {"dash": Qt.PenStyle.DashLine, "solid": Qt.PenStyle.SolidLine, "dot": Qt.PenStyle.DotLine}
_LABEL_POSITION = 0.99  # along the visible line: at the right end
_HOVER_WIDTH = 3.0  # the line under the pointer is drawn this wide; it also makes the line easier to catch


def guide_name(guide: Guide) -> str:
    return f"{guide.value:g}" + (f" {guide.label}" if guide.label else "")


def label_format(guide: Guide) -> str | None:
    """The text of a guide's label as pyqtgraph formats it (`{value}` is the line's current y), or None when the guide has no label and shows no value. The user's own braces are
    written doubled so that they stay braces."""
    text = guide.label.replace("{", "{{").replace("}", "}}")
    if guide.show_value:
        text = f"{text} {{value:.6g}}" if text else "{value:.6g}"
    return text or None


class GuideLine(pg.InfiniteLine):
    """A guide's line: open hand under the pointer, closed hand while it is dragged."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDragEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton and self.movable and ev.isStart():
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mouseDragEvent(ev)
        if ev.isFinish():
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseClickEvent(self, ev) -> None:
        super().mouseClickEvent(ev)
        if not ev.isAccepted():
            ev.ignore()  # a plain click on the line is a click on the plot behind it (the cube cursor goes there)


class GuideViewBox(pg.ViewBox):
    """A ViewBox that lets a right-click on a guide open the guide's own menu, and remembers where the click that opened its menu was ("Add guide here" needs the y of that click).
    pyqtgraph hands a right-click to the ViewBox before any item, so this is the only place that can give it to a guide."""

    guide_right_click = None  # set by the owner: a function (scene position, screen position) -> bool, True when the right-click was on a guide (its menu opens)
    last_menu_scene_pos = None  # where the right-click that opened the view's menu was

    def raiseContextMenu(self, ev):
        self.last_menu_scene_pos = ev.scenePos()
        super().raiseContextMenu(ev)

    def takes_guide_click(self, ev) -> bool:
        """True (and the click accepted) when `ev` is a right-click on a guide, whose menu has been asked for."""
        if ev.button() == Qt.MouseButton.RightButton and self.guide_right_click is not None and self.guide_right_click(ev.scenePos(), ev.screenPos()):
            ev.accept()
            return True
        return False

    def mouseClickEvent(self, ev):
        if not self.takes_guide_click(ev):
            super().mouseClickEvent(ev)


class GuideGestures(QObject):
    """The guide gestures of the LbL and S_bulk windows (the Sensorgram has its own, with its edit modes): the plot's right-click menu gets "Add guide here" (a guide at the y of the click,
    no label, showing its value), and a right-click on a guide opens the guide's menu. Only reports to the window's `GuideControls`, the one place that changes guides. Needs the plot to be
    made with a `GuideViewBox`."""

    def __init__(self, item: pg.PlotItem, lines: GuideLines, controls: GuideControls, report: Callable[[str], None]) -> None:
        """`report(text)`: where a refusal is said (the window's status line); the pop-up's own note says it too."""
        super().__init__(item)
        self._item, self._lines, self._controls, self._report = item, lines, controls, report
        view_box = item.getViewBox()
        view_box.guide_right_click = self._on_right_click
        self._add_action = None
        if view_box.menu is not None:
            self._add_action = QAction("Add guide here", view_box.menu)
            give_persistent_icon(self._add_action, "guide_add")
            self._add_action.triggered.connect(self._on_add_here)
            first = view_box.menu.actions()[0] if view_box.menu.actions() else None
            view_box.menu.insertAction(first, self._add_action)
            view_box.menu.insertSeparator(first)

    def add_action(self) -> QAction | None:
        return self._add_action

    def _on_right_click(self, scene_pos, screen_pos) -> bool:
        index = self._lines.index_at(scene_pos)
        if index is None:
            return False
        self._controls.show_menu(index, QPoint(int(screen_pos.x()), int(screen_pos.y())))
        return True

    def _on_add_here(self) -> None:
        view_box = self._item.getViewBox()
        pos = view_box.last_menu_scene_pos
        if pos is None:
            return
        y = rounded_for_span(float(view_box.mapSceneToView(pos).y()), self._lines.view_span())
        if not self._controls.add_guide(Guide(y, "", None, "dash", True)):
            self._report(FULL_NOTE)


class GuideLines(QObject):
    moved = pyqtSignal(int, float)  # (guide index, new y value) after a drag; the owner saves it and sets the guides back

    def __init__(self, item: pg.PlotItem) -> None:
        super().__init__()
        self._item = item
        self._guides: tuple[Guide, ...] = ()
        self._lines: list[pg.InfiniteLine] = []

    def guides(self) -> tuple[Guide, ...]:
        return self._guides

    def lines(self) -> list[pg.InfiniteLine]:
        return list(self._lines)

    def set_guides(self, guides: tuple[Guide, ...]) -> None:
        """Draw exactly these guides (the old lines are taken out, also when the plot cleared them already)."""
        self._guides = tuple(guides)
        self._rebuild()

    def refresh_theme(self) -> None:
        self._rebuild()

    def parts(self) -> list[SvgPart]:
        return [SvgPart("Guides", guide_name(g), line) for g, line in zip(self._guides, self._lines)]

    def index_at(self, scene_pos: QPointF) -> int | None:
        """The guide within `HIT_PX` pixels of the scene position (the nearest one), or None. The position must be over the plot area."""
        view = self._item.getViewBox()
        if not view.sceneBoundingRect().contains(scene_pos):
            return None
        best: tuple[float, int] | None = None
        for index, guide in enumerate(self._guides):
            distance = abs(view.mapViewToScene(QPointF(0.0, guide.value)).y() - scene_pos.y())
            if distance <= HIT_PX and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

    def view_span(self) -> float:
        """The height of the view in y units (what a dropped guide is rounded to)."""
        low, high = self._item.getViewBox().viewRange()[1]
        return float(high - low)

    # -- drawing ---------------------------------------------------------------------------------------------------------------------

    def _remove(self) -> None:
        for line in self._lines:
            self._item.removeItem(line)  # does nothing for a line a `clear()` took out already
        self._lines = []

    def _rebuild(self) -> None:
        self._remove()
        theme = get_active_theme()
        for index, guide in enumerate(self._guides):
            color = QColor(guide.color or theme.text_muted)
            pen = pg.mkPen(color, width=1.0, style=_PEN_STYLE[guide.style])
            pen.setCosmetic(True)
            hover = pg.mkPen(color, width=_HOVER_WIDTH, style=_PEN_STYLE[guide.style])
            hover.setCosmetic(True)
            line = GuideLine(
                pos=guide.value, angle=0, pen=pen, hoverPen=hover, movable=True, label=label_format(guide),
                labelOpts={"position": _LABEL_POSITION, "color": color, "anchors": [(1, 1), (1, 1)], "movable": False},
            )
            line.setZValue(GUIDE_Z)
            line.sigPositionChangeFinished.connect(lambda finished, which=index: self._finished(which, finished))
            self._item.addItem(line, ignoreBounds=True)
            self._lines.append(line)

    def _finished(self, index: int, line: pg.InfiniteLine) -> None:
        value = rounded_for_span(float(line.value()), self.view_span())
        QTimer.singleShot(0, lambda: self.moved.emit(index, value))  # queued: the owner redraws the lines, which must not happen inside this line's own event
