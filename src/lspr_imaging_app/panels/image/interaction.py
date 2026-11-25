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

from collections.abc import Callable, Mapping
from typing import Any, Protocol

import pyqtgraph as pg
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QGraphicsView, QInputDialog, QWidget

from ...image_tools import ActiveToolModule, ImageTool
from ...roi import RoiToolbox
from ...roi.shape_hits import ZONE_ROTATE
from ...selection import AreaSelectionMode, AreaSelectionModule, SelectionModule
from ...selection.area_selection_module import VALUE_MODES, how_for_keys
from ..menu_icons import menu_icon
from .area_selection_tool import AreaSelectionTool
from .combine_badge import CombineBadge
from .context_menu import SubmenuFiller, show_tool_context_menu
from .crop_tool import CropTool
from .mask_menu_actions import (
    CLEAR_MASK, MASK_ENTRIES, MASK_SUBMENU, REMOVE_BLOB, MaskMenuActions, mask_submenu_filler,
)
from .measure_line_tool import MeasureLineTool
from .roi_context_menu import (
    ADD_RECTANGLE,
    ADD_ROI,
    ADD_RIM,
    ADD_ROI_SUBMENU,
    ADD_SELECTION_BLOBS,
    ADD_SELECTION_ONE,
    ADD_TO_GROUP,
    COPY,
    DELETE,
    DESELECT,
    DESELECT_AREA,
    FIT_CIRCLE,
    FIT_RECTANGLE,
    GROUP,
    LINK,
    MAKE_REFERENCE,
    MAKE_SAMPLE,
    PASTE,
    REMOVE_CUBE_EDIT,
    RIM_TO_REFERENCE,
    SELECT_ARRAY,
    SELECT_GROUP,
    TOGGLE_LOCK,
    TURN_RIM_OFF,
    MATCH_RIM_AREA,
    UNGROUP,
    UNLINK,
    add_roi_submenu_filler,
    show_roi_context_menu,
)
from .roi_creation import RoiCreation
from .roi_gestures import RoiGestures, RoiHit
from .resize_cursor import direction_angle_deg, resize_cursor
from .rotate_cursor import rotate_cursor
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


SELECT_ROIS_IN_SELECTION = "Select ROIs in selection"
LOCK_TO_SELECTION = "Lock"  # 2026-11-15: the selection becomes the fence (`AreaSelectionModule.lock_selection`); the menu entry reads just "Lock" since 2026-11-16
UNLOCK = "Unlock"
INVERT_SELECTION = "Invert selection"
DESELECT_SELECTION = "Deselect"
EXIT_TOOL = "Exit tool"  # always the last entry of a tool's right-click menu (`_context_menu`)

# The icon of each entry of the tools' right-click menus (2026-11-19), as a key of `panels/menu_icons.py` (one icon per command in every menu).
_TOOL_MENU_ICONS = {
    "Cancel rotation": "cancel",
    "Cancel measurement": "cancel",
    "Apply crop": "apply",
    "Cancel crop": "cancel",
    EXIT_TOOL: "cancel",
    SELECT_ROIS_IN_SELECTION: "select_rois",
    ADD_ROI_SUBMENU: "add_roi",
    LOCK_TO_SELECTION: "lock",
    UNLOCK: "unlock",
    INVERT_SELECTION: "invert",
    DESELECT_SELECTION: "deselect",
    MASK_SUBMENU: "mask",  # 2026-11-23: Mask > Add / Subtract / Convert to mask (`mask_menu_actions.py`)
    REMOVE_BLOB: "remove_blob",
    CLEAR_MASK: "clear_mask",
}
"""The selection tools' right-click entry: the ROIs inside or partly inside the area selection become selected."""

_NO_CURSOR_YET = object()
"""`_roi_cursor` before the ROI hover has set anything: distinct from `None`
(= "default cursor"), so the first hover always sets the cursor explicitly."""


class _ClickTool(Protocol):
    def on_left_click(self, x: float, y: float) -> None: ...


class CanvasInteraction:
    def __init__(
        self,
        *,
        plot: pg.PlotItem,
        view: QGraphicsView,
        menu_parent: QWidget,
        active_tool: ActiveToolModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        area_selection: AreaSelectionModule,
        rotate_tool: RotateLineTool,
        crop_tool: CropTool,
        measure_tool: MeasureLineTool,
        area_tool: AreaSelectionTool,
        image_shape: Callable[[], tuple[int, ...] | None],
        roi_at: Callable[[float, float], int | None],
        apply_crop: Callable[[], None],
        roi_gestures: RoiGestures,
        roi_tab_active: Callable[[], bool],
        to_reference: Callable[[float, float], tuple[float, float]],
        notify: Callable[[str], None],
        wand_click: Callable[[float, float, str], object] | None = None,
        rois_touching: Callable[[Any], set[int]] | None = None,
        drag_adjusters: Mapping[AreaSelectionMode, Callable[[Any], bool]] | None = None,
        value_click: Callable[[AreaSelectionMode, str], object] | None = None,
        lock_to_selection: Callable[[], None] | None = None,
        unlock: Callable[[], None] | None = None,
        combine_badge: CombineBadge | None = None,
        roi_creation: RoiCreation,
        mask_menu: MaskMenuActions | None = None,
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
        self._roi_gestures = roi_gestures
        self._roi_tab_active = roi_tab_active
        self._to_reference = to_reference
        self._roi_creation = roi_creation  # Add ROI > ..., Make this ROI > ..., Link / Unlink: `roi_creation.py`
        self._notify = notify
        self._mask_menu = mask_menu  # the mask entries of the right-click menu (Mask > ..., and on a mask pixel: Mask to selection, Remove blob, Clear)
        self._wand_click = wand_click  # the wand's click: (x, y, "replace" | "add" | "subtract")
        self._rois_touching = rois_touching  # display-space bool mask -> ids of the ROIs overlapping it
        # armed tool -> its right-button drag handler (a drag event -> the tool's numbers: `tool_drag.py`, `wand_drag.py`)
        self._drag_adjusters = dict(drag_adjusters or {})
        self._value_click = value_click  # (armed select-by-value tool, how): a click on the image runs its method
        self._lock_to_selection = lock_to_selection  # the selection becomes the lock (the panel says so in the status bar)
        self._unlock = unlock
        self._combine_badge = combine_badge  # the + / - / x beside the pointer; follows it while a selection tool is armed
        self._armed_cursor = False  # whether the cursor on the canvas is a selection tool's (the cross, or the brush's blank)
        self._roi_cursor: Qt.CursorShape | QCursor | None | object = _NO_CURSOR_YET  # what the ROI hover last set (a QCursor is the rotate cursor: compared by identity)

    def handle_key(self, key: int, modifiers: Qt.KeyboardModifier) -> bool:
        """Arrow keys / Esc for the active tool; `True` when a tool used the key (for Esc: it dropped what it was doing; with nothing to drop the key is
        not used and `ToolEscape` leaves the tool)."""
        return (
            self._rotate_tool.handle_key(key, modifiers)
            or self._crop_tool.handle_key(key)
            or self._measure_tool.handle_key(key)
        )

    def _in_view(self, scene_pos: object) -> bool:
        return bool(self._plot.vb.sceneBoundingRect().contains(scene_pos))

    def pointer_xy(self) -> tuple[float, float] | None:
        """Where the mouse pointer is on the image (display point), read now; ``None`` when it is not over the image (Ctrl+V pastes there)."""
        viewport = self._view.viewport()
        if viewport is None:
            return None
        scene_pos = self._view.mapToScene(viewport.mapFromGlobal(QCursor.pos()))
        if not self._in_view(scene_pos):
            return None
        point = self._plot.vb.mapSceneToView(scene_pos)
        return float(point.x()), float(point.y())

    def _set_view_cursor(self, shape: Qt.CursorShape | QCursor | None) -> None:
        """Set the canvas cursor (a standard shape, or a cursor of our own such as the rotate cursor), or drop any custom one (`None`)."""
        viewport = self._view.viewport()
        if viewport is None:
            return
        if shape is None:
            viewport.unsetCursor()
        else:
            viewport.setCursor(shape)

    def on_scene_moved(self, scene_pos: object) -> None:
        if self._combine_badge is not None:
            self._combine_badge.follow(scene_pos)
        if self._roi_gestures_enabled():
            self._armed_cursor = False  # the ROI hover sets (or clears) the cursor from here on
            self._update_roi_hover_cursor(scene_pos)
            return
        if self._roi_cursor is not _NO_CURSOR_YET:  # left the ROI gestures: drop the cursor they set
            self._roi_cursor = _NO_CURSOR_YET
            self._set_view_cursor(None)
        tool = self._active_tool.active()
        if self._brush_armed():
            # The brush ring follows the mouse over the image (the stamp's size); the cross cursor is dropped, the ring is the cursor.
            if self._in_view(scene_pos):
                point = self._plot.vb.mapSceneToView(scene_pos)
                self._area_tool.show_brush_cursor(float(point.x()), float(point.y()))
                self._set_view_cursor(Qt.CursorShape.BlankCursor)
                self._armed_cursor = True
            else:
                self._area_tool.hide_brush_cursor()
            return
        self._area_tool.hide_brush_cursor()
        if self._wand_armed() or self._value_armed():
            # A cross over the image: "click a feature". Set when the mouse moves, dropped when it stops being armed.
            cross = self._in_view(scene_pos)
            if cross != self._armed_cursor:
                self._armed_cursor = cross
                self._set_view_cursor(Qt.CursorShape.CrossCursor if cross else None)
            return
        if self._armed_cursor:
            self._armed_cursor = False
            self._set_view_cursor(None)
        rotate_pending = self._rotate_tool.first_point() is not None
        measure_pending = self._measure_tool.first_point() is not None
        # Cheap early-out: this fires on every mouse move over the scene, and with no tool
        # needing the pointer there is nothing to look up.
        if not (tool in (ImageTool.CROP, ImageTool.MEASURE) or rotate_pending or measure_pending):
            return
        if not self._in_view(scene_pos):
            return
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        if tool is ImageTool.CROP:
            self._set_view_cursor(_CURSOR_FOR_CROP_HANDLE.get(self._crop_tool.hover_handle(x, y)))
        elif tool is ImageTool.MEASURE:
            # Same "move" cursor as dragging Crop's interior - both mean
            # "drag this to reposition it".
            handle = self._measure_tool.hover_handle(x, y)
            self._set_view_cursor(None if handle is None else Qt.CursorShape.SizeAllCursor)
        if rotate_pending:
            self._rotate_tool.on_mouse_moved(x, y)
        if measure_pending:
            self._measure_tool.on_mouse_moved(x, y)

    def on_scene_clicked(self, event: Any) -> None:
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
        modifiers = getattr(event, "modifiers", lambda: Qt.KeyboardModifier.NoModifier)()
        handlers = {
            ImageTool.ROTATE: self._click_rotate,
            ImageTool.MEASURE: self._click_measure,
            ImageTool.CROP: self._click_crop,
            ImageTool.SELECT_AREA: lambda pos, btn: self._click_select_area(pos, btn, modifiers),
        }
        tool = self._active_tool.active()
        handler = handlers.get(tool) if tool is not None else None
        if self._wand_armed() and button == Qt.MouseButton.LeftButton:
            self._click_wand(event, scene_pos)
        elif handler is not None:
            handler(scene_pos, button)
        else:
            self._click_select(event, scene_pos, button)

    # -- one handler per tool (split from one 72-line method, 2026-10-07) ---------------

    def _click_point_tool(
        self, tool: _ClickTool, show_menu: Callable[[object], None], scene_pos: object, button: object
    ) -> None:
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

    def _click_select_area(self, scene_pos: object, button: object, modifiers: object = Qt.KeyboardModifier.NoModifier) -> None:
        # The drag draws (`_on_select_area_drag`); only the menu is a click - and, for the brush, one stamp, for a select-by-value tool its run.
        # `modifiers` are the keys held at the click (Shift / Alt / Shift+Alt: how the result combines).
        if button == Qt.MouseButton.RightButton and self._in_view(scene_pos):
            self._show_select_area_context_menu(scene_pos)
        elif button == Qt.MouseButton.LeftButton and self._brush_armed() and self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            self._area_tool.stamp_at(float(point.x()), float(point.y()), self.how_for(modifiers))
        elif button == Qt.MouseButton.LeftButton and self._value_armed() and self._in_view(scene_pos):
            mode = self._area_selection.mode()
            assert self._value_click is not None
            self._value_click(mode, self.how_for(modifiers))

    def _value_armed(self) -> bool:
        """A select-by-value tool (Histogram range, Threshold, Local contrast) is armed: a click anywhere on the image runs it."""
        return (
            self._value_click is not None
            and self._active_tool.active() is ImageTool.SELECT_AREA
            and self._area_selection.mode() in VALUE_MODES
        )

    def _brush_armed(self) -> bool:
        return self._active_tool.active() is ImageTool.SELECT_AREA and self._area_selection.mode() is AreaSelectionMode.BRUSH

    def _wand_armed(self) -> bool:
        return (
            self._wand_click is not None
            and self._active_tool.active() is ImageTool.SELECT_AREA
            and self._area_selection.mode() is AreaSelectionMode.WAND
        )

    def how_for(self, modifiers: object) -> str:
        """How a selection gesture combines with the selection: Shift adds, Alt takes away, Shift+Alt intersects (the
        Photoshop keys; rows of `shortcuts.py`); with no key, the sticky mode of the toolbar's mode button (Replace /
        Add / Subtract / Intersect)."""
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)  # type: ignore[operator]
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)  # type: ignore[operator]
        return how_for_keys(shift, alt, self._area_selection.combine_mode())

    def _click_wand(self, event: object, scene_pos: object) -> None:
        """The wand: a left click selects the feature under it, combined as the mode button says (Shift adds, Alt takes
        away, Shift+Alt intersects: `how_for`)."""
        if not self._in_view(scene_pos):
            return
        modifiers = getattr(event, "modifiers", lambda: Qt.KeyboardModifier.NoModifier)()
        how = self.how_for(modifiers)
        point = self._plot.vb.mapSceneToView(scene_pos)
        assert self._wand_click is not None
        self._wand_click(float(point.x()), float(point.y()), how)

    def _click_select(self, event: object, scene_pos: object, button: object) -> None:
        """No tool active: select the ROI under the cursor. On the ROIs tab a
        right click opens the ROI menu instead."""
        if button == Qt.MouseButton.RightButton and self._roi_tab_active():
            if self._in_view(scene_pos):
                self._show_roi_menu(scene_pos)
            return
        if button == Qt.MouseButton.RightButton and (self._point_in_selection(scene_pos) or self._on_mask_pixel(scene_pos)):
            self._context_menu([], scene_pos)  # plain selection menu (Invert / Deselect, Mask > ..., and the mask-pixel entries)
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

    def _show_roi_menu(self, scene_pos: object) -> None:
        """Right click on the ROIs tab: add a ROI here, group / ungroup / delete
        the selection. A click on an unselected ROI selects it first, so the
        menu always acts on what the cursor is on. While the ROI selection is locked (2026-11-16) the click selects
        nothing: the menu acts on the locked ROIs (its entries say how many) and carries the "Lock selection" check."""
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        roi_id = self._roi_at(x, y)
        locked = self._selection.roi_selection_locked()
        if roi_id is not None and roi_id not in self._selection.selected_roi_ids() and not locked:
            self._selection.set_roi_selection({roi_id})
        ids = tuple(sorted(self._selection.selected_roi_ids()))
        toolbox = self._roi_toolbox
        clicked_group = toolbox.group_for_roi(roi_id) if roi_id is not None else None
        clicked_array = next((a for a in toolbox.array_groups() if roi_id in a.member_area_roi_ids), None) if roi_id is not None else None
        choice = show_roi_context_menu(
            self._menu_parent,
            clicked_group=(clicked_group.name, len(clicked_group.area_roi_ids)) if clicked_group else None,
            clicked_group_fully_selected=bool(clicked_group) and set(clicked_group.area_roi_ids) <= set(ids),
            clicked_array=len(clicked_array.member_area_roi_ids) if clicked_array else None,
            clicked_array_fully_selected=bool(clicked_array) and set(clicked_array.member_area_roi_ids) <= set(ids),
            selected_count=len(ids),
            groups=[(group.group_id, group.name) for group in toolbox.groups()],
            any_selected_grouped=any(toolbox.group_for_roi(selected) is not None for selected in ids),
            can_add=self.in_selection(x, y),
            can_add_from_selection=self._roi_creation.has_pixel_selection(),
            fit_to_blob=self._roi_creation.has_single_blob(),
            selected_roles=self._roi_creation.role_counts(ids),
            can_link=self._roi_creation.can_link(ids),
            can_unlink=self._roi_creation.can_unlink(ids),
            can_add_rim=self._roi_creation.can_add_rim(ids),
            can_turn_rim_off=self._roi_creation.can_turn_rim_off(ids),
            can_rim_to_reference=self._roi_creation.can_rim_to_reference(ids),
            can_match_rim_area=self._roi_creation.can_match_rim_area(ids),
            can_deselect_area=self._area_selection.has_selection(),
            paste_count=self._roi_creation.pasted_count(),
            can_remove_cube_edit=any(
                toolbox.roi_by_id(i).timeline and int(self._selection.current_cube()) in toolbox.roi_by_id(i).timeline.cubes() for i in ids
            ),
            locked=locked,
        )
        if choice is None:
            return
        if choice.action == TOGGLE_LOCK:
            self._selection.set_roi_selection_locked(not locked)
            return
        if choice.action == SELECT_GROUP and clicked_group is not None:
            self._selection.set_roi_selection(set(clicked_group.area_roi_ids))
        elif choice.action == SELECT_ARRAY and clicked_array is not None:
            self._selection.set_roi_selection(set(clicked_array.member_area_roi_ids))
        elif choice.action == ADD_ROI:  # Add ROI > Circle
            self._roi_creation.add_circle(x, y)
        elif choice.action == ADD_RECTANGLE:
            self._roi_creation.add_rectangle(x, y)
        elif choice.action == FIT_CIRCLE:
            self._roi_creation.add_fitted("circle")
        elif choice.action == FIT_RECTANGLE:
            self._roi_creation.add_fitted("rectangle")
        elif choice.action == COPY:
            self._roi_creation.copy_selected()
        elif choice.action == PASTE:
            self._roi_creation.paste_at(x, y)
        elif choice.action in (ADD_SELECTION_ONE, ADD_SELECTION_BLOBS):
            self._roi_creation.add_from_selection(together=choice.action == ADD_SELECTION_ONE)
        elif choice.action == MAKE_SAMPLE:
            self._roi_creation.set_role(ids, "sample")
        elif choice.action == MAKE_REFERENCE:
            self._roi_creation.set_role(ids, "reference")
        elif choice.action == ADD_RIM:
            self._roi_creation.add_rim(ids)
        elif choice.action == TURN_RIM_OFF:
            self._roi_creation.turn_rim_off(ids)
        elif choice.action == MATCH_RIM_AREA:
            self._roi_creation.match_rim_area(ids)
        elif choice.action == RIM_TO_REFERENCE:
            self._roi_creation.rim_to_reference(ids)
        elif choice.action == LINK:
            self._roi_creation.link()
        elif choice.action == UNLINK:
            self._roi_creation.unlink()
        elif choice.action == GROUP:
            default = f"Group {len(toolbox.groups()) + 1}"
            name, accepted = QInputDialog.getText(self._menu_parent, "Group ROIs", "Group name", text=default)
            if accepted and name.strip():
                self._guarded("Group ROIs", lambda: toolbox.group_rois(ids, name.strip()))
        elif choice.action == ADD_TO_GROUP and choice.group_id is not None:
            self._guarded("Add to group", lambda: toolbox.add_rois_to_group(ids, choice.group_id))
        elif choice.action == UNGROUP:
            self._guarded("Ungroup", lambda: toolbox.remove_rois_from_groups(ids))
        elif choice.action == DELETE:
            self._guarded("Delete ROIs", lambda: toolbox.delete_rois(ids))
        elif choice.action == REMOVE_CUBE_EDIT:
            self._guarded("Remove cube edit", lambda: toolbox.remove_cube_edit(ids, int(self._selection.current_cube())))
        elif choice.action == DESELECT:
            self._selection.set_roi_selection(set())
        elif choice.action == DESELECT_AREA:
            self._area_selection.clear()  # one undo step, as the armed tools' menu's Deselect

    def _guarded(self, action: str, command: Callable[[], None]) -> None:
        """Run a toolbox command; a refusal (bad value, stale id) goes to the status bar, not nowhere."""
        try:
            command()
        except (ValueError, KeyError) as exc:
            self._notify(f"{action}: {exc}")

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

    def _show_select_area_context_menu(self, scene_pos: object) -> None:
        """Right-click while a selection tool is armed: "Select ROIs in selection" (2026-11-15, maintainer request: the
        ROIs inside or partly inside it become the ROI selection), "Add ROI > Current selection > ..." (2026-11-19, maintainer
        request: the same submenu as the ROIs tab's menu; only while there is an area selection to make ROIs from), "Exit tool"
        (leaves the draw tool, keeps the selection), plus Invert/Deselect inside it."""
        can_pick = (
            self._rois_touching is not None and self._area_selection.has_selection() and not self._selection.roi_selection_locked()
        )
        point = self._plot.vb.mapSceneToView(scene_pos)
        x, y = float(point.x()), float(point.y())
        # 2026-11-23: Add ROI also offers Circle / Rectangle at the click, where the ROIs tab's menu does (the point is editable under the selection).
        can_add_shapes = self.in_selection(x, y)
        can_add_from_selection = self._roi_creation.has_pixel_selection()
        fit_to_blob = self._roi_creation.has_single_blob()  # (2026-11-24) one blob selected: Circle / Rectangle are fitted to it
        chosen = self._context_menu(
            [
                (SELECT_ROIS_IN_SELECTION, can_pick),
                (ADD_ROI_SUBMENU, can_add_shapes or can_add_from_selection),
                (LOCK_TO_SELECTION, self._area_selection.has_selection() and self._lock_to_selection is not None),
                (UNLOCK, self._area_selection.has_lock() and self._unlock is not None),
                (EXIT_TOOL, True),
            ],
            scene_pos,
            submenus={
                ADD_ROI_SUBMENU: add_roi_submenu_filler(
                    can_add_shapes=can_add_shapes, can_add_from_selection=can_add_from_selection, fit_to_blob=fit_to_blob
                )
            },
        )
        if chosen == EXIT_TOOL:
            self._active_tool.set_active(ImageTool.SELECT_AREA, False)
        elif chosen == ADD_ROI:  # Add ROI > Circle
            self._roi_creation.add_circle(x, y)
        elif chosen == ADD_RECTANGLE:
            self._roi_creation.add_rectangle(x, y)
        elif chosen == FIT_CIRCLE:
            self._roi_creation.add_fitted("circle")
        elif chosen == FIT_RECTANGLE:
            self._roi_creation.add_fitted("rectangle")
        elif chosen == SELECT_ROIS_IN_SELECTION:
            self.select_rois_in_selection()  # (Lock / Unlock are handled in `_context_menu`)
        elif chosen in (ADD_SELECTION_ONE, ADD_SELECTION_BLOBS):
            self._roi_creation.add_from_selection(together=chosen == ADD_SELECTION_ONE)  # (as the ROIs tab's menu does)

    def select_rois_in_selection(self) -> None:
        """Make the ROIs inside or partly inside the area selection the ROI selection (replacing it); the count goes to
        the status bar. Follows an inverted selection (then: the ROIs touching the outside)."""
        shape = self._image_shape()
        mask = self._area_selection.mask(shape) if shape is not None else None
        if mask is None or self._rois_touching is None:
            return
        ids = self._rois_touching(mask)
        self._selection.set_roi_selection(ids)
        self._notify(f"{len(ids)} ROI{'' if len(ids) == 1 else 's'} inside or partly inside the selection selected.")

    def _on_mask_pixel(self, scene_pos: object) -> bool:
        """True when the pointer is on a pixel of the mask drawn on screen (it adds the mask-pixel entries to the right-click menu)."""
        if self._mask_menu is None or not self._in_view(scene_pos):
            return False
        point = self._plot.vb.mapSceneToView(scene_pos)
        return self._mask_menu.on_mask_pixel(float(point.x()), float(point.y()))

    def _point_in_selection(self, scene_pos: object) -> bool:
        """True when a selection exists and the scene point lies inside the
        *editable* region (inside the shape, or outside it once inverted)."""
        shape = self._image_shape()
        if not self._area_selection.has_selection() or shape is None or not self._in_view(scene_pos):
            return False
        point = self._plot.vb.mapSceneToView(scene_pos)
        return self._area_selection.contains(float(point.x()), float(point.y()), shape)

    def _context_menu(
        self, actions: list[tuple[str, bool]], scene_pos: object, submenus: Mapping[str, SubmenuFiller] | None = None
    ) -> str | None:
        """`show_tool_context_menu` plus, when the click is inside the
        selection, "Invert selection" and "Deselect" (handled here). Returns
        the chosen *tool* action, or `None` if nothing or a selection entry was
        chosen. Selection entries are always enabled, so the menu never opens
        with nothing clickable (see `context_menu.py`). `submenus` turns a tool
        entry's label into a submenu (see `show_tool_context_menu`)."""
        # Order (2026-11-16, maintainer): the tool's own entries, Lock / Unlock, Invert + Deselect together, and "Exit tool" always last.
        entries = [entry for entry in actions if entry[0] != EXIT_TOOL]
        exit_entries = [entry for entry in actions if entry[0] == EXIT_TOOL]
        inside = self._point_in_selection(scene_pos)
        if inside and self._lock_to_selection is not None and all(label != LOCK_TO_SELECTION for label, _on in entries):
            entries.append((LOCK_TO_SELECTION, True))
        if inside:
            entries += [(INVERT_SELECTION, True), (DESELECT_SELECTION, True)]
        # 2026-11-23: the selection's Mask submenu (while something is selected), then the entries about the mask pixel under the pointer.
        if self._mask_menu is not None and self._active_tool.active() in (None, ImageTool.SELECT_AREA):  # not in Crop / Rotate / Measure's menus
            has_selection, has_mask = self._area_selection.has_selection(), self._mask_menu.has_mask()
            if has_selection or has_mask:
                entries.append((MASK_SUBMENU, True))
                submenus = {**(submenus or {}), MASK_SUBMENU: mask_submenu_filler(has_selection=has_selection, has_mask=has_mask)}
            if self._on_mask_pixel(scene_pos):
                entries += [(REMOVE_BLOB, True), (CLEAR_MASK, True)]
        entries += exit_entries
        if not entries:
            return None
        chosen = show_tool_context_menu(
            self._menu_parent,
            entries,
            icons={label: menu_icon(key) for label, key in _TOOL_MENU_ICONS.items() if any(label == entry[0] for entry in entries)},
            separate_before={INVERT_SELECTION, REMOVE_BLOB, EXIT_TOOL},
            submenus=submenus,
        )
        if chosen == LOCK_TO_SELECTION and self._lock_to_selection is not None:
            self._lock_to_selection()
            return None
        if chosen == UNLOCK and self._unlock is not None:
            self._unlock()
            return None
        if chosen == INVERT_SELECTION:
            self._area_selection.invert()
            return None
        if chosen == DESELECT_SELECTION:
            self._area_selection.clear()
            return None
        if chosen in MASK_ENTRIES and self._mask_menu is not None:
            point = self._plot.vb.mapSceneToView(scene_pos)
            self._mask_menu.run(chosen, float(point.x()), float(point.y()))
            return None
        return chosen

    def on_left_drag(self, ev: Any) -> bool:
        """`ImageViewBox`'s single left-drag handler slot (the panel installs `on_left_drag`) - dispatches to
        whichever tool (if any) claims the drag. Crop and Measure each
        decline (return `False`) unless *they* are the active tool, so at
        most one of them ever claims a given drag, and neither has any
        effect on plain ROI dragging."""
        return (
            self.on_crop_drag(ev)
            or self.on_measure_drag(ev)
            or self._on_select_area_drag(ev)
            or self._on_roi_drag(ev)
        )

    def on_right_drag(self, ev: Any) -> bool:
        """`ImageViewBox`'s right-drag handler slot: with a selection tool armed, holding the right button and moving changes that tool's numbers
        (the wand: threshold sideways, Grow up; Threshold / Local contrast: threshold sideways, sigma up; Draw: size sideways: `tool_drag.py`).
        A tool without numbers declines, so the right button stays as it was (a right click still opens the menus)."""
        if self._active_tool.active() is not ImageTool.SELECT_AREA:
            return False
        adjust = self._drag_adjusters.get(self._area_selection.mode())
        return adjust is not None and bool(adjust(ev))

    def _roi_gestures_enabled(self) -> bool:
        return self._active_tool.active() is None and self._roi_tab_active()

    def _on_roi_drag(self, ev: Any) -> bool:
        """ROIs tab, no tool armed: left-drag resizes (from a selected ROI's border),
        moves (from inside a selected ROI) or draws the selection rectangle."""
        if not self._roi_gestures_enabled():
            return False
        point = self._plot.vb.mapSceneToView(ev.scenePos())
        x, y = float(point.x()), float(point.y())
        modifiers = ev.modifiers()
        additive = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        if ev.isStart():
            # pyqtgraph only reports a drag once the mouse has moved a few pixels, so
            # `scenePos()` is already past where the button went down: hit-test and
            # anchor at the press point, then apply the movement so far.
            down = ev.buttonDownScenePos()
            if not self._in_view(down):
                return False
            down_point = self._plot.vb.mapSceneToView(down)
            if not self._roi_gestures.begin(float(down_point.x()), float(down_point.y()), additive=additive):
                return False
            if self._roi_gestures.rotating:  # turning shows the rotate cursor for the whole drag, even if the press came without a hover first
                self._roi_cursor = rotate_cursor()
                self._set_view_cursor(self._roi_cursor)
            self._roi_gestures.update(x, y)
            self._follow_held_cursor(x, y)
            return True
        if ev.isFinish():
            self._roi_gestures.end(x, y, additive=additive)
            return True
        self._roi_gestures.update(x, y)
        self._follow_held_cursor(x, y)
        return True

    def _follow_held_cursor(self, x: float, y: float) -> None:
        """While a resize drag runs, turn the arrow to lie along the direction at the pointer ``(x, y)`` (display units), as the hover does: round a circle the radius
        turns with the pointer, and a rectangle's corner arrow follows its changing proportions. Nothing for any other drag (a turn keeps the rotate cursor, a move
        its move cursor)."""
        held = self._roi_gestures.held
        if held is not None:
            self._show_roi_cursor(self._cursor_for_hit(held, x, y))

    def _show_roi_cursor(self, shape: Qt.CursorShape | QCursor | None) -> None:
        """Set the canvas cursor unless it is already this one (compared by identity: a cursor of our own is cached per step, `resize_cursor.py`)."""
        if shape is not self._roi_cursor and shape != self._roi_cursor:
            self._roi_cursor = shape
            self._set_view_cursor(shape)

    def _cursor_for_hit(self, hit: RoiHit, x: float, y: float) -> Qt.CursorShape | QCursor:
        """The cursor for holding (or pressing on) ``hit`` with the pointer at ``(x, y)``: the move cursor inside, the rotate cursor on the knob, else the resize arrow."""
        if hit.zone == "body":
            return Qt.CursorShape.SizeAllCursor
        if hit.zone == ZONE_ROTATE:
            return rotate_cursor()  # the round knob above a selected rectangle: turn it
        direction = self._roi_gestures.resize_direction(hit, x, y)  # a rectangle's edge, corner or border band: along its own axis, not the radius
        if direction is None:
            return self._resize_cursor(hit.center, x, y)
        return self._resize_cursor(hit.center, hit.center[0] + direction[0], hit.center[1] + direction[1])

    def _update_roi_hover_cursor(self, scene_pos: object) -> None:
        """Show what a press here would do: a resize arrow on a selected ROI's
        border (pointing along the radius), a move cursor inside a selected ROI."""
        if self._roi_gestures.busy:
            return  # keep the cursor the drag has; a resize drag turns it itself (`_follow_held_cursor`)
        shape: Qt.CursorShape | QCursor | None = None
        if self._in_view(scene_pos):
            point = self._plot.vb.mapSceneToView(scene_pos)
            x, y = float(point.x()), float(point.y())
            hit = self._roi_gestures.hit(x, y)
            if hit is not None:
                shape = self._cursor_for_hit(hit, x, y)
        self._show_roi_cursor(shape)

    def _resize_cursor(self, center: tuple[float, float], x: float, y: float) -> QCursor:
        """The two-headed arrow along the direction from ``center`` to ``(x, y)`` *on screen* (a circle's radius, or a rectangle's drag axis), at that exact angle
        (to 1.5 degrees: `resize_cursor.py`). The view may be flipped, so the angle is taken between scene points."""
        a = self._plot.vb.mapViewToScene(QPointF(center[0], center[1]))
        b = self._plot.vb.mapViewToScene(QPointF(x, y))
        return resize_cursor(direction_angle_deg(b.x() - a.x(), b.y() - a.y()))

    def _on_select_area_drag(self, ev: Any) -> bool:
        """Draws the rectangle/lasso while a selection tool is armed."""
        if self._active_tool.active() is not ImageTool.SELECT_AREA:
            return False
        point = self._plot.vb.mapSceneToView(ev.scenePos())
        x, y = float(point.x()), float(point.y())
        if ev.isStart():
            if self._brush_armed():
                # pyqtgraph reports a drag only after a few pixels: begin where the button went down, then go to where it is now
                down = self._plot.vb.mapSceneToView(ev.buttonDownScenePos())
                started = self._area_tool.begin_gesture(float(down.x()), float(down.y()), self.how_for(ev.modifiers()))
                if started:
                    self._area_tool.update_gesture(x, y)
                return started
            return self._area_tool.begin_gesture(x, y, self.how_for(ev.modifiers()))
        if ev.isFinish():
            self._area_tool.end_gesture()
            return True
        self._area_tool.update_gesture(x, y)
        return True

    def in_selection(self, x: float, y: float) -> bool:
        """Whether display point (x, y) may be edited under the current area
        selection (always True with none)."""
        shape = self._image_shape()
        if shape is None:
            return not self._area_selection.has_selection()
        return self._area_selection.contains(x, y, shape)

    def on_crop_drag(self, ev: Any) -> bool:
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

    def on_measure_drag(self, ev: Any) -> bool:
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
