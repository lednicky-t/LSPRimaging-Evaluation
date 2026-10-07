"""Canvas click / move / drag routing for the Image panel (split out of
`panel.py` on 2026-10-07; the methods are moved as they were).

Which tool owns a mouse or key event, and what a right-click menu offers while a
tool is active, is decided here; the tools themselves (`RotateLineTool`,
`CropTool`, `MeasureLineTool`, `AreaSelectionTool`) own their gestures, and the
panel owns the image and the overlays. With no tool active a left click selects
the ROI under the cursor (Ctrl/Shift toggles) - a click is only ever a command
to `SelectionModule` / `RoiToolbox`, never a mutation.

Everything the router needs from the panel is passed in: the pyqtgraph plot and
view, the modules, the four tools, and three small callbacks (`image_shape`,
`roi_at`, `apply_crop`). It holds no state of its own.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from ...image_tools import ActiveToolModule, ImageTool
from ...roi import RoiToolbox
from ...selection import AreaSelectionModule, SelectionModule
from .area_selection_tool import AreaSelectionTool
from .context_menu import show_tool_context_menu
from .crop_tool import CropTool
from .measure_line_tool import MeasureLineTool
from .rotate_line_tool import RotateLineTool

_CURSOR_FOR_CROP_HANDLE: dict[str | None, Qt.CursorShape] = {
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}
"""Cursor feedback for `CropTool.hover_handle`'s result - the tool draws no
separate handle graphics (see crop_tool.py's docstring), so the cursor
shape is the only hint that an edge/corner/interior is grabbable."""


class CanvasInteraction:
    def __init__(
        self,
        *,
        plot: object,
        view: object,
        menu_parent: QWidget,
        active_tool: ActiveToolModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        area_selection: AreaSelectionModule,
        rotate_tool: RotateLineTool,
        crop_tool: CropTool,
        measure_tool: MeasureLineTool,
        area_tool: AreaSelectionTool,
        image_shape: Callable[[], tuple[int, int] | None],
        roi_at: Callable[[float, float], int | None],
        apply_crop: Callable[[], None],
    ) -> None:
        self._plot = plot
        self._view = view
        self._menu_parent = menu_parent
        self._active_tool = active_tool
        self._roi_toolbox = roi_toolbox
        self._selection = selection
        self._area_selection = area_selection
        self._rotate_tool = rotate_tool
        self._crop_tool = crop_tool
        self._measure_tool = measure_tool
        self._area_tool = area_tool
        self._image_shape = image_shape
        self._roi_at = roi_at
        self._apply_crop = apply_crop

    def handle_key(self, key: int, modifiers: object) -> bool:
        """Arrow keys / Esc for the active tool; `True` when a tool used the key."""
        return (
            self._rotate_tool.handle_key(key, modifiers)
            or self._crop_tool.handle_key(key)
            or self._measure_tool.handle_key(key)
        )

    def _in_view(self, scene_pos: object) -> bool:
        return bool(self._plot.vb.sceneBoundingRect().contains(scene_pos))

    def on_scene_moved(self, scene_pos: object) -> None:
        # Cheap early-out: this fires on every mouse move over the scene.
        if self._active_tool.active() is ImageTool.CROP and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            handle = self._crop_tool.hover_handle(float(point.x()), float(point.y()))
            cursor = _CURSOR_FOR_CROP_HANDLE.get(handle)
            if cursor is None:
                self._view.viewport().unsetCursor()
            else:
                self._view.viewport().setCursor(cursor)
        if self._active_tool.active() is ImageTool.MEASURE and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            handle = self._measure_tool.hover_handle(float(point.x()), float(point.y()))
            if handle is None:
                self._view.viewport().unsetCursor()
            else:
                # Same "move" cursor as dragging Crop's interior - both mean
                # "drag this to reposition it".
                self._view.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        if self._rotate_tool.first_point() is not None and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            self._rotate_tool.on_mouse_moved(float(point.x()), float(point.y()))
        if self._measure_tool.first_point() is not None and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            self._measure_tool.on_mouse_moved(float(point.x()), float(point.y()))

    def on_scene_clicked(self, event: object) -> None:
        """Route a click: to the active tool, or - with no tool active -
        select the ROI under the cursor (clear the selection when the click
        lands on empty image). Ctrl/Shift toggles instead of replacing,
        matching the platform convention for multi-select lists. Only the
        left button selects; the middle button is for panning."""
        try:
            scene_pos = event.scenePos()
        except AttributeError:  # pragma: no cover - defensive against pyqtgraph versions
            return
        button = getattr(event, "button", lambda: Qt.MouseButton.LeftButton)()
        handlers = {
            ImageTool.ROTATE: self._click_rotate,
            ImageTool.MEASURE: self._click_measure,
            ImageTool.CROP: self._click_crop,
            ImageTool.SELECT_AREA: self._click_select_area,
            ImageTool.ADD_ROI: self._click_add_roi,
        }
        handler = handlers.get(self._active_tool.active())
        if handler is not None:
            handler(scene_pos, button)
        else:
            self._click_select(event, scene_pos, button)

    # -- one handler per tool (split from one 72-line method, 2026-10-07) ---------------

    def _click_point_tool(self, tool: object, show_menu: Callable[[object], None], scene_pos: object, button: object) -> None:
        """Rotate and Measure: a left click places a point, a right click opens the tool's menu."""
        if not self._in_view(scene_pos):
            return
        if button == Qt.MouseButton.LeftButton:
            p = self._plot.vb.mapSceneToView(scene_pos)
            tool.on_left_click(float(p.x()), float(p.y()))
        elif button == Qt.MouseButton.RightButton:
            show_menu(scene_pos)

    def _click_rotate(self, scene_pos: object, button: object) -> None:
        self._click_point_tool(self._rotate_tool, self._show_rotate_context_menu, scene_pos, button)

    def _click_measure(self, scene_pos: object, button: object) -> None:
        self._click_point_tool(self._measure_tool, self._show_measure_context_menu, scene_pos, button)

    def _click_crop(self, scene_pos: object, button: object) -> None:
        # A plain (non-drag) left-click has nothing to do - dragging is
        # handled separately, by ImageViewBox's left-drag handler
        # (`on_crop_drag`), since a real drag never reaches
        # `sigMouseClicked` at all (pyqtgraph routes it as a drag
        # event once the mouse has moved past its click threshold).
        if button == Qt.MouseButton.RightButton and self._in_view(scene_pos):
            self._show_crop_context_menu(scene_pos)

    def _click_select_area(self, scene_pos: object, button: object) -> None:
        # The drag draws (`_on_select_area_drag`); only the menu is a click.
        if button == Qt.MouseButton.RightButton and self._in_view(scene_pos):
            self._show_select_area_context_menu(scene_pos)

    def _click_add_roi(self, scene_pos: object, button: object) -> None:
        if not self._in_view(scene_pos):
            return
        if button == Qt.MouseButton.LeftButton:
            p = self._plot.vb.mapSceneToView(scene_pos)
            if self.in_selection(float(p.x()), float(p.y())):
                self._roi_toolbox.add_roi(float(p.x()), float(p.y()))
        elif button == Qt.MouseButton.RightButton:
            self._show_add_roi_context_menu(scene_pos)

    def _click_select(self, event: object, scene_pos: object, button: object) -> None:
        """No tool active: select the ROI under the cursor."""
        if button == Qt.MouseButton.RightButton and self._point_in_selection(scene_pos):
            self._context_menu([], scene_pos)  # plain Invert/Deselect menu
            return
        if button != Qt.MouseButton.LeftButton:
            return  # not a select gesture
        point = self._plot.vb.mapSceneToView(scene_pos)
        roi_id = self._roi_at(float(point.x()), float(point.y()))
        modifiers = getattr(event, "modifiers", lambda: Qt.KeyboardModifier.NoModifier)()
        additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))

        current = set(self._selection.selected_roi_ids())
        if roi_id is None:
            self._selection.set_roi_selection(current if additive else set())
            return
        if additive:
            current.symmetric_difference_update({roi_id})
            self._selection.set_roi_selection(current)
        else:
            self._selection.set_roi_selection({roi_id})

    def _show_rotate_context_menu(self, scene_pos: object) -> None:
        """Right-click while Rotate is active: a menu with a single "Cancel
        rotation" action, always enabled (2026-09-29, maintainer's spec) -
        it exits Rotate mode entirely, the same as clicking the Workflow
        panel's Rotate button again (`ActiveToolModule.set_active(ROTATE,
        False)` already drops any in-progress point 1 as a side effect of
        deactivating - `RotateLineTool.set_active`'s own `_clear_first_
        point()` call - so there is nothing extra to do first). Always
        being enabled is also what keeps the menu from ever having nothing
        clickable in it - see `context_menu.py`'s docstring for why that
        matters."""
        if self._context_menu([("Cancel rotation", True)], scene_pos) == "Cancel rotation":
            self._active_tool.set_active(ImageTool.ROTATE, False)

    def _show_measure_context_menu(self, scene_pos: object) -> None:
        """Right-click while Measure is active: a menu with a single
        "Cancel measurement" action, always enabled - same shape and
        reasoning as `_show_rotate_context_menu` (exits Measure mode
        entirely, the same as clicking the Workflow panel's Measure button
        again; always-enabled is what keeps the menu from ever having
        nothing clickable in it, see `context_menu.py`)."""
        if self._context_menu([("Cancel measurement", True)], scene_pos) == "Cancel measurement":
            self._active_tool.set_active(ImageTool.MEASURE, False)

    def _show_crop_context_menu(self, scene_pos: object) -> None:
        """Right-click while Crop is active: "Apply crop" (enabled only
        with something pending, `CropTool.has_pending_changes`) and
        "Cancel crop", always enabled - like Rotate's menu, it exits Crop
        mode entirely (`ActiveToolModule.set_active(CROP, False)`), the
        same as clicking the Workflow panel's Crop button again, dropping
        any not-yet-applied edit along the way. "Cancel" always being
        clickable is what keeps this menu from ever having nothing
        clickable in it, even with "Apply" grayed out - see
        `context_menu.py`'s docstring."""
        chosen = self._context_menu(
            [("Apply crop", self._crop_tool.has_pending_changes()), ("Cancel crop", True)], scene_pos
        )
        if chosen == "Apply crop":
            self._apply_crop()
        elif chosen == "Cancel crop":
            self._active_tool.set_active(ImageTool.CROP, False)

    def _show_add_roi_context_menu(self, scene_pos: object) -> None:
        """Right-click while Add ROI is active: a menu with a single "Exit
        tool" action, always enabled - same shape as Rotate/Measure's own
        "Cancel ..." menus (`_show_rotate_context_menu`/`_show_measure_
        context_menu`), just not labeled "Cancel" since there is no
        in-progress point to drop - each click here is already a complete,
        independent action."""
        if self._context_menu([("Exit tool", True)], scene_pos) == "Exit tool":
            self._active_tool.set_active(ImageTool.ADD_ROI, False)

    def _show_select_area_context_menu(self, scene_pos: object) -> None:
        """Right-click while a selection tool is armed: "Exit tool" (leaves the
        draw tool, keeps the selection), plus Invert/Deselect inside it."""
        if self._context_menu([("Exit tool", True)], scene_pos) == "Exit tool":
            self._active_tool.set_active(ImageTool.SELECT_AREA, False)

    def _point_in_selection(self, scene_pos: object) -> bool:
        """True when a selection exists and the scene point lies inside the
        *editable* region (inside the shape, or outside it once inverted)."""
        if not self._area_selection.has_selection() or self._image_shape() is None or not self._in_view(scene_pos):
            return False
        point = self._plot.vb.mapSceneToView(scene_pos)
        return self._area_selection.contains(float(point.x()), float(point.y()), self._image_shape())

    def _context_menu(self, actions: list[tuple[str, bool]], scene_pos: object) -> str | None:
        """`show_tool_context_menu` plus, when the click is inside the
        selection, "Invert selection" and "Deselect" (handled here). Returns
        the chosen *tool* action, or `None` if nothing or a selection entry was
        chosen. Selection entries are always enabled, so the menu never opens
        with nothing clickable (see `context_menu.py`)."""
        entries = list(actions)
        if self._point_in_selection(scene_pos):
            entries += [("Invert selection", True), ("Deselect", True)]
        if not entries:
            return None
        chosen = show_tool_context_menu(self._menu_parent, entries)
        if chosen == "Invert selection":
            self._area_selection.invert()
            return None
        if chosen == "Deselect":
            self._area_selection.clear()
            return None
        return chosen

    def on_left_drag(self, ev: object) -> bool:
        """`ImageViewBox`'s single left-drag handler slot (the panel installs `on_left_drag`) - dispatches to
        whichever tool (if any) claims the drag. Crop and Measure each
        decline (return `False`) unless *they* are the active tool, so at
        most one of them ever claims a given drag, and neither has any
        effect on plain ROI dragging."""
        return self.on_crop_drag(ev) or self.on_measure_drag(ev) or self._on_select_area_drag(ev)

    def _on_select_area_drag(self, ev: object) -> bool:
        """Draws the rectangle/lasso while a selection tool is armed."""
        if self._active_tool.active() is not ImageTool.SELECT_AREA:
            return False
        point = self._plot.vb.mapSceneToView(ev.scenePos())
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            return self._area_tool.begin_gesture(x, y)
        if ev.isFinish():
            self._area_tool.end_gesture()
            return True
        self._area_tool.update_gesture(x, y)
        return True

    def in_selection(self, x: float, y: float) -> bool:
        """Whether display point (x, y) may be edited under the current area
        selection (always True with none)."""
        if self._image_shape() is None:
            return not self._area_selection.has_selection()
        return self._area_selection.contains(x, y, self._image_shape())

    def on_crop_drag(self, ev: object) -> bool:
        if self._active_tool.active() is not ImageTool.CROP:
            return False
        scene_pos = ev.scenePos()
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            if not self._in_view(scene_pos):
                return False
            return self._crop_tool.begin_gesture(x, y)
        if ev.isFinish():
            self._crop_tool.end_gesture()
            return True
        self._crop_tool.update_gesture(x, y)
        return True

    def on_measure_drag(self, ev: object) -> bool:
        """Drags an already-placed point (maintainer's request, 2026-09-29
        - added after the click-twice-only version shipped). A drag that
        doesn't start on an existing point is left unclaimed - a *new* pair
        is placed by ordinary clicks (`on_left_click`), not by dragging
        empty space."""
        if self._active_tool.active() is not ImageTool.MEASURE:
            return False
        scene_pos = ev.scenePos()
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            if not self._in_view(scene_pos):
                return False
            return self._measure_tool.begin_gesture(x, y)
        if ev.isFinish():
            self._measure_tool.end_gesture()
            return True
        self._measure_tool.update_gesture(x, y)
        return True
