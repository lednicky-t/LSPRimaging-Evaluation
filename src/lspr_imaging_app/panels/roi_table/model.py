"""``RoiTreeModel``: the ROI/Group table's rows, as a Qt tree model.

Grouped view: one top-level node per group (plus "Ungrouped"), ROIs below it;
with no groups, every ROI is top-level. It presents what `RoiToolbox` holds and owns no
ROI state of its own: ``set_content`` replaces everything, and an edit is not
applied here - ``setData`` reports it through ``cell_edited`` and returns
``False``, the panel asks the toolbox, and the change comes back through
``set_content``. (A "command model": the view never shows a value the toolbox
did not accept.)
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from PyQt6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from ...applog import StageTimer
from ...selection.step_table_module import StepColumn
from ...domain.roi_model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup
from ...roi.palette import DEFAULT_ROI_COLOR_HEX
from .rows import (
    COLUMN_ANGLE,
    COLUMN_AREA,
    COLUMN_COUNT,
    COLUMN_HEIGHT,
    COLUMN_ID,
    COLUMN_NAME,
    COLUMN_RIM_OFFSET,
    COLUMN_RIM_THICKNESS,
    COLUMN_SAMPLE,
    COLUMN_SHAPE,
    COLUMN_TITLES,
    COLUMN_WIDTH,
    COLUMN_X,
    COLUMN_Y,
    EDITABLE_COLUMNS,
    LENGTH_COLUMNS,
    NUMERIC_COLUMNS,
    PIXELS,
    RECTANGLE_COLUMNS,
    RIM_COLUMNS,
    SHAPE_FIELDS,
    SHAPE_RECTANGLE,
    SIZE_COLUMNS,
    TYPE_LABELS,
    GroupInfo,
    LengthUnit,
    RoiRow,
    Section,
    StepSort,
    ANGLE_DECIMALS,
    angle_edit_text,
    area_decimals,
    area_tooltip,
    build_linked_sections,
    build_roi_rows,
    build_sections,
    edit_text,
    format_angle,
    format_area,
    has_cell,
    format_length,
    length_decimals,
    sort_rows,
    to_display,
    to_display_area,
)
from .column_keys import ColumnKey, column_key
from .number_format import DEFAULT_DISPLAY, NORMAL, ColumnDisplay
from .number_format import shown_decimals as _digits_shown
from .step_columns import DEFAULT_DECIMALS, DISPLAY_BOTH, DISPLAY_MEAN, cell_tooltip, format_cell, is_excess, sort_value, table_decimals

KIND_ROLE = Qt.ItemDataRole.UserRole + 1
"""``"group"`` or ``"roi"``."""
ROI_ID_ROLE = Qt.ItemDataRole.UserRole + 2
GROUP_ID_ROLE = Qt.ItemDataRole.UserRole + 3
"""A group node's id (``None`` for "Ungrouped"), or the group a ROI is in."""
COLOR_ROLE = Qt.ItemDataRole.UserRole + 4
INHERITED_ROLE = Qt.ItemDataRole.UserRole + 5
"""True for a ring diameter the ROI takes from the shared default."""
COUNT_ROLE = Qt.ItemDataRole.UserRole + 6
ALL_SELECTED_ROLE = Qt.ItemDataRole.UserRole + 7
"""A group header: every member is currently selected."""
DEPTH_ROLE = Qt.ItemDataRole.UserRole + 8
MASK_ROLE = Qt.ItemDataRole.UserRole + 9
SHAPE_ROLE = Qt.ItemDataRole.UserRole + 11
"""A ROI row's shape key (``rows.SHAPE_RING`` ...): what decides which size columns it has."""
TYPE_ROLE = Qt.ItemDataRole.UserRole + 15
"""A ROI row's Type pictogram and the kind of pair it is in: ``(rows.ICON_*, rows.KIND_*)``; the delegate paints it (`type_icons`)."""
VALUES_ONLY_ROLE = Qt.ItemDataRole.UserRole + 16
"""Not data: the role list of a ``dataChanged`` that says "the cells' numbers and looks changed, no row came or went and none changed size" (`RoiTreeModel._adopt`). `RoiTreeView.dataChanged` then
only repaints: Qt's own handler asks the delegate for the size of every cell of the range, 40 ms for 160 ROIs."""
PAINT_ROLE = Qt.ItemDataRole.UserRole + 17
"""A ROI row's cell, everything the delegate needs to paint it in one answer: ``(text, dim, stale, alignment, tint)`` (``dim``: the value is the shared default's or a mask's;
``stale``: a step number that is out of date; ``alignment``: an int; ``tint``: a step column's background or ``None``). Asking role by role was six to seven Python calls a cell, a quarter of
a repaint (`docs/roi_table_performance_2026-11-19.md`). ``None`` for a group header."""
STEP_STALE_ROLE = Qt.ItemDataRole.UserRole + 12
"""A step-column cell with a number that is out of date (the delegate dims it)."""
COLUMN_KIND_ROLE = Qt.ItemDataRole.UserRole + 13
"""Header data of a section after the fixed columns: ``"step"`` or ``"compare"`` (a comparison column: its header carries the arrow of the operation menu)."""
STEP_TINT_ROLE = Qt.ItemDataRole.UserRole + 14
"""A step column's background, as a ``QColor`` with the plan bar's opacity in its alpha: the same fill the Sensorgram's bar paints that step with. Cells and header; ``None``
for every other column (and when the bar's opacity is 0)."""
DEFAULT_TINT_ALPHA = 0.55  # the plan bar's own default (`plan.bar_model.PlanBarSettings.alpha`)

MIME_TYPE = "application/x-lspr-roi-ids"
"""Dragged ROIs: a JSON list of ROI ids."""


@dataclass(frozen=True)
class DropTarget:
    """Where dragged ROIs were dropped.

    ``"header"``: on a group header (``group_id`` is the group; ``None`` is
    the "Ungrouped" header). ``"section"``: between the rows of a group's list
    (``position`` is the row they were dropped before). ``"flat"``: between
    rows of the flat list (``position`` as for a section)."""

    kind: str
    group_id: str | None = None
    position: int = 0

_NUMERIC_COLUMNS = NUMERIC_COLUMNS
_ITEM = Qt.ItemFlag
_BASE_FLAGS = _ITEM.ItemIsEnabled | _ITEM.ItemIsSelectable
_ROI_FLAGS = {  # (linked view, editable): the flags of a ROI row's cell. Made once: `flags` is asked for every cell of every selected row by `QItemSelectionModel.selectedRows`, for every cell Qt paints, ...
    (linked, editable): _BASE_FLAGS | (_ITEM.NoItemFlags if linked else _ITEM.ItemIsDragEnabled) | (_ITEM.ItemIsEditable if editable else _ITEM.NoItemFlags)  # ROI rows are not drop targets: a drop lands between rows (nothing is dragged in the Linked view)
    for linked in (False, True)
    for editable in (False, True)
}
_GROUP_FLAGS = {  # (linked view, editable): a group header cell; a drop on a header puts the ROIs in that group
    (linked, editable): _BASE_FLAGS | (_ITEM.NoItemFlags if linked else _ITEM.ItemIsDropEnabled) | (_ITEM.ItemIsEditable if editable else _ITEM.NoItemFlags)
    for linked in (False, True)
    for editable in (False, True)
}
"""A flag combination built with `|` runs PyQt's enum code in Python (about 10 µs); a table of 160 ROIs asked for 27,000 of them to select everything (2026-11-19, `docs/roi_table_performance_2026-11-19.md`)."""
_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_RIGHT_ALIGNED_COLUMNS = (*NUMERIC_COLUMNS, COLUMN_AREA)  # the Area is a number too, but nothing can be typed, pasted or stepped in it (`NUMERIC_COLUMNS` is that set)
_HEADER_TIPS = {
    COLUMN_ID: "Position in the list. Reordering changes these numbers.",
    COLUMN_NAME: "Label of a group (its name, also drawn on the image). Empty for ROIs.",
    COLUMN_X: "Centre x on the reference image.",
    COLUMN_Y: "Centre y on the reference image.",
    COLUMN_SHAPE: (
        "Type of the ROI: its shape (circle, rectangle, pixel map), with a ring round it while it is measured against its own rim. "
        "Green: a sample with a rim or with links. Amber: a sample with neither (no reference). Blue: a reference some sample uses. Gray: a reference nobody uses."
    ),
    COLUMN_SAMPLE: "Sample diameter.",
    COLUMN_RIM_OFFSET: (
        "d = rim offset: how far the rim starts from the shape's edge (negative: it starts inside the shape). The same for a circle, a rectangle and a pixel map. "
        "Grey italic: taken from the shared default. Blank: a reference has no rim."
    ),
    COLUMN_RIM_THICKNESS: (
        "w = rim thickness: the width of the rim band, measured outwards from the offset. The same for a circle, a rectangle and a pixel map. "
        "Grey italic: taken from the shared default. Blank: a reference has no rim."
    ),
    COLUMN_WIDTH: "Rectangle width, along the rectangle's own x axis. Blank for other shapes.",
    COLUMN_HEIGHT: "Rectangle height, along the rectangle's own y axis. Blank for other shapes.",
    COLUMN_ANGLE: "Rectangle angle in degrees, turned clockwise on the screen about its centre. Blank for other shapes.",
    COLUMN_AREA: "Area of the ROI's own region: a count of pixels with unit px, µm² with unit µm. Circle and rectangle: the shape's area; pixel mask: its pixel count. Read only.",
}


class _Node:
    __slots__ = ("kind", "parent", "children", "position", "section", "row", "members")

    def __init__(self, kind: str, parent: "_Node | None", *, section: Section | None = None, row: RoiRow | None = None) -> None:
        self.kind = kind
        self.parent = parent
        self.children: list[_Node] = []
        self.position = 0
        self.section = section
        self.row = row
        self.members: tuple[int, ...] = tuple(sorted(r.roi_id for r in section.rows)) if section is not None else ()  # a group's ROI ids, canonical order


class StepCells:
    """What the model asks of whoever holds the step numbers (``StepTableModule``): the number of a ROI or a group in a step, or None."""

    def roi_cell(self, roi_id: int, ref: object): ...  # pragma: no cover - interface
    def group_cell(self, key: str | None, members: Sequence[int], ref: object): ...  # pragma: no cover - interface


class RoiTreeModel(QAbstractItemModel):
    cell_edited = pyqtSignal(str, object, int, str)
    """``(kind, key, column, text)``: ``kind`` is ``"roi"`` (``key`` the ROI
    id) or ``"group"`` (``key`` the group id)."""
    roi_dropped = pyqtSignal(object, object)
    """``(roi ids, DropTarget)``. Like an edit, a drop is only reported: the
    model returns ``False`` from `dropMimeData` so Qt never removes the source
    rows itself, and the new arrangement comes back through `set_content`."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root = _Node("root", None)
        self._previous_root: _Node | None = None  # kept alive one generation: see set_content
        self._stages = StageTimer()  # the stages of the last rebuild (`last_rebuild_ms`)
        self._layout: tuple | None = None  # what the tree lists (`_layout_of`): a rebuild that lists the same rows in the same groups updates the cells in place (`_adopt`)
        self._reset_happened = False  # see `take_reset_flag`
        self._unit: LengthUnit = PIXELS
        self._sort_column = COLUMN_ID
        self._descending = False
        self._rows: list[RoiRow] = []
        self._rois: tuple[AreaRoi, ...] = ()
        self._groups: tuple[AreaRoiGroup, ...] = ()
        self._defaults = AreaRoiDetectionSettings()
        self._default_color_hex = DEFAULT_ROI_COLOR_HEX
        self._selected: frozenset[int] = frozenset()
        self._grouped = False
        self._linked = False  # the Linked view (`rows.build_linked_sections`): the tree has headers but they are not groups
        self._step_columns: tuple[StepColumn, ...] = ()
        self._cells: StepCells | None = None
        self._display = DISPLAY_BOTH
        self._decimals = DEFAULT_DECIMALS
        self._decimals_key: tuple | None = None  # what `_decimals` was worked out from (`_refresh_decimals`): the same again, the same answer
        self._numbers_version = 0  # up by one whenever the step numbers may have changed (`mark_numbers_changed`)
        self._column_display: dict[ColumnKey, ColumnDisplay] = {}  # the user's per-column number format, by column key (`column_keys.py`)
        self._display_of: dict[int, ColumnDisplay] = {}  # the same by column number, for the cells (`_make_display_map`)
        self._layout_column: int | None = None  # inside `layout_pass()`: the one step column `index` answers for (the fixed columns always answer)
        self._sort_ref: object | None = None  # the step a step column sort is on, by reference: the columns are renumbered when steps change
        self._tint_alpha = DEFAULT_TINT_ALPHA  # the plan bar's opacity: how strongly a step column is coloured
        self._tints: tuple[QColor | None, ...] = ()  # one per step column (`_make_tints`), so `data()` does not build a colour per cell

    # -- content --------------------------------------------------------------

    def set_content(
        self,
        rois: Sequence[AreaRoi],
        groups: Sequence[AreaRoiGroup],
        defaults: AreaRoiDetectionSettings,
        unit: LengthUnit,
        default_color_hex: str | None = None,
    ) -> None:
        self._rois, self._groups, self._defaults, self._unit = tuple(rois), tuple(groups), defaults, unit
        if default_color_hex:
            self._default_color_hex = default_color_hex
        self._rebuild()

    def set_sort(self, column: int, descending: bool) -> None:
        if column >= COLUMN_COUNT and column - COLUMN_COUNT >= len(self._step_columns):
            column, descending = COLUMN_ID, False  # a step column that is not there (any more)
        if (column, descending) != (self._sort_column, self._descending):
            self._sort_column, self._descending = column, descending
            self._sort_ref = self._step_columns[column - COLUMN_COUNT].ref if column >= COLUMN_COUNT else None
            self._rebuild()

    def sort_state(self) -> tuple[int, bool]:
        return self._sort_column, self._descending

    # -- the step columns ----------------------------------------------------------

    def set_step_columns(self, columns: Sequence[StepColumn], cells: StepCells | None) -> None:
        """Show these steps as columns after the fixed ones (none: no step columns), with `cells` as the source of the numbers. A sort on a step column follows the
        step (by reference) to its new column, or falls back to the ``#`` order when the step is gone."""
        self._step_columns = tuple(columns) if cells is not None else ()
        self._cells = cells
        self._numbers_version += 1
        self._tints = self._make_tints()
        self._display_of = self._make_display_map()
        if self._sort_ref is not None:
            refs = [c.ref for c in self._step_columns]
            if self._sort_ref in refs:
                self._sort_column = COLUMN_COUNT + refs.index(self._sort_ref)
            else:
                self._sort_column, self._descending, self._sort_ref = COLUMN_ID, False, None
        self._rebuild()

    def step_columns(self) -> tuple[StepColumn, ...]:
        return self._step_columns

    def step_texts(self, position: int) -> list[str]:
        """What the cells of step column number `position` (0 = the first step column) show now, for every ROI row and every group header (rows with no number show nothing and
        are left out): the content the column has to be wide enough for. Collapsed groups count too, so opening one never changes a width."""
        if self._cells is None or not 0 <= position < len(self._step_columns):
            return []
        column = self._step_columns[position]
        mode = self._mode_of(column)
        decimals = self._decimals if column.decimals is None else column.decimals
        display = self._display_of.get(COLUMN_COUNT + position)
        texts = [format_cell(self._cells.roi_cell(row.roi_id, column.ref), mode, decimals, display) for row in self._rows]
        if self._grouped and not self._linked:
            texts += [format_cell(self._cells.group_cell(key, sorted(members), column.ref), mode, decimals, display) for key, members in self._members_by_group().items()]
        return [text for text in texts if text]

    def set_step_tint_alpha(self, alpha: float) -> None:
        """How strongly the step columns are coloured: the opacity of the Sensorgram plan bar's fill (0 - 1; 0 = not coloured). Changes no data: the caller repaints."""
        alpha = min(max(float(alpha), 0.0), 1.0)
        if alpha != self._tint_alpha:
            self._tint_alpha = alpha
            self._tints = self._make_tints()

    def step_tint_alpha(self) -> float:
        return self._tint_alpha

    def _make_tints(self) -> tuple[QColor | None, ...]:
        """Each step column's background: its step's colour at the bar's opacity. None where there is no colour (a comparison column) or no opacity."""
        tints: list[QColor | None] = []
        for column in self._step_columns:
            color = QColor(column.color) if column.color and self._tint_alpha > 0 else QColor()
            if color.isValid():
                color.setAlphaF(self._tint_alpha)
                tints.append(color)
            else:
                tints.append(None)
        return tuple(tints)

    def _tint_of(self, column: int) -> QColor | None:
        """The tint of a model column (None for a fixed one)."""
        position = column - COLUMN_COUNT
        return self._tints[position] if 0 <= position < len(self._tints) else None

    def set_step_display(self, mode: str) -> None:
        """What the step cells show: one of ``step_columns.DISPLAYS`` (mean, SD, SEM, mean ± SD, mean ± SEM)."""
        if mode != self._display:
            self._display = mode
            self.refresh_numbers()

    def step_display(self) -> str:
        return self._display

    # -- the user's number format per column (`number_format.py`) ---------------------------------

    def _make_display_map(self) -> dict[int, ColumnDisplay]:
        """The user's settings by column number: a fixed column by its number, a step column by where its step is now (the settings are kept by column key, which survives renumbering)."""
        mapping = {key: display for key, display in self._column_display.items() if isinstance(key, int)}
        for position, column in enumerate(self._step_columns):
            display = self._column_display.get(column_key(column))
            if display is not None:
                mapping[COLUMN_COUNT + position] = display
        return mapping

    def column_display(self, column: int) -> ColumnDisplay:
        return self._display_of.get(column, DEFAULT_DISPLAY)

    def column_displays(self) -> dict[ColumnKey, ColumnDisplay]:
        """Every setting the user made, by column key (a setting for a step this experiment does not have is kept: it is the user's, for the experiment it belongs to)."""
        return dict(self._column_display)

    def set_column_display(self, key: ColumnKey, display: ColumnDisplay) -> None:
        """Write one column's numbers another way (the default display removes the setting)."""
        self.set_column_displays({**self._column_display, key: display})

    def set_column_displays(self, displays: Mapping[ColumnKey, ColumnDisplay]) -> None:
        """Replace every setting (restoring the remembered ones). Only the cells whose column's setting changed are drawn again."""
        new = {key: display for key, display in displays.items() if not display.is_default}
        if new == self._column_display:
            return
        before = self._display_of
        self._column_display = new
        self._display_of = self._make_display_map()
        for column in sorted(set(before) | set(self._display_of)):
            if before.get(column) != self._display_of.get(column):
                self._repaint_column(column)

    def _repaint_column(self, column: int) -> None:
        if column >= self.columnCount():
            return
        top = self._root.children
        for node in top:
            for item in (node, *node.children):
                index = self.createIndex(item.position, column, item)
                self.dataChanged.emit(index, index, [VALUES_ONLY_ROLE])  # (only what the cell says: nothing to lay out again)

    def plain_decimals(self, column: int) -> int | None:
        """The decimals a column shows without a setting of the user's, or None for a column that holds no number to format."""
        if column >= COLUMN_COUNT:
            position = column - COLUMN_COUNT
            if not 0 <= position < len(self._step_columns):
                return None
            return self._decimals if self._step_columns[position].decimals is None else self._step_columns[position].decimals
        if column == COLUMN_AREA:
            return area_decimals(self._unit)
        if column == COLUMN_ANGLE:
            return ANGLE_DECIMALS
        return length_decimals(self._unit) if column in LENGTH_COLUMNS else None

    def shown_decimals(self, column: int) -> int | None:
        """The digits after the point the column writes now: what a "More / Fewer decimals" step starts from. Normal notation and an explicit setting need no number; automatic scientific
        notation writes the digits the number needs, so the first number the column holds decides (None if it holds none)."""
        plain = self.plain_decimals(column)
        if plain is None:
            return None
        display = self.column_display(column)
        if display.notation == NORMAL or display.decimals is not None:
            return _digits_shown(1.0, plain, display)
        number = self._first_number(column)
        return None if number is None else _digits_shown(number, plain, display)

    def _first_number(self, column: int) -> float | None:
        """The number in the first row that has one in this column (as the cell shows it: in the display unit)."""
        for row in self._rows:
            number = self._number_of(row, column)
            if number is not None and math.isfinite(number):
                return number
        return None

    def _number_of(self, row: RoiRow, column: int) -> float | None:
        unit = self._unit
        if column >= COLUMN_COUNT:
            position = column - COLUMN_COUNT
            if self._cells is None or not 0 <= position < len(self._step_columns):
                return None
            step = self._step_columns[position]
            return sort_value(self._cells.roi_cell(row.roi_id, step.ref), self._mode_of(step))
        if column in (COLUMN_X, COLUMN_Y):
            return to_display(row.x if column == COLUMN_X else row.y, unit)
        if column == COLUMN_AREA:
            return to_display_area(row.area_px, unit)
        if column in RECTANGLE_COLUMNS:
            if column not in SHAPE_FIELDS[row.shape]:
                return None
            return row.angle if column == COLUMN_ANGLE else to_display(row.width if column == COLUMN_WIDTH else row.height, unit)
        if column in RIM_COLUMNS:
            return to_display(row.rim_offset if column == COLUMN_RIM_OFFSET else row.rim_thickness, unit) if row.has_rim else None
        if column == COLUMN_SAMPLE and not row.is_mask and column in SHAPE_FIELDS[row.shape]:
            return to_display(row.sample, unit)
        return None

    def sorts_by_step(self) -> bool:
        return self._sort_column >= COLUMN_COUNT

    def refresh_numbers(self) -> None:
        """The numbers behind the step cells changed (a calculation finished, a row went out of date): format them again, and sort again when a step column sorts."""
        self._numbers_version += 1
        if self.sorts_by_step():
            self._rebuild()  # (which also formats again)
        else:
            self._refresh_decimals()
            self._repaint_steps()

    def mark_numbers_changed(self) -> None:
        """The step table says its numbers changed, whether or not the table is showing them now (a hidden panel does no work, `StepColumnControls._on_numbers_changed`): the common number of
        decimals is worked out again at the next rebuild instead of being taken from before."""
        self._numbers_version += 1

    def _repaint_steps(self) -> None:
        if not self._step_columns:
            return
        last = self.columnCount() - 1
        for node in self._root.children:
            self.dataChanged.emit(self.createIndex(node.position, COLUMN_COUNT, node), self.createIndex(node.position, last, node))
            for child in node.children:
                self.dataChanged.emit(self.createIndex(child.position, COLUMN_COUNT, child), self.createIndex(child.position, last, child))

    def _members_by_group(self) -> dict[str | None, list[int]]:
        members_of: dict[str | None, list[int]] = {}
        for row in self._rows:
            members_of.setdefault(row.group_id, []).append(row.roi_id)
        return members_of

    def _all_cells(self):
        if self._cells is None:
            return
        members_of = self._members_by_group() if self._grouped and not self._linked else {}
        for column in self._step_columns:
            for row in self._rows:
                yield self._cells.roi_cell(row.roi_id, column.ref)
            for key, members in members_of.items():
                yield self._cells.group_cell(key, sorted(members), column.ref)

    def _refresh_decimals(self) -> None:
        """The one number of decimals of the step columns: the median spread of every step cell (`table_decimals`), about 42 000 cell lookups for 160 ROIs and 241 step columns. Worked out
        again only when something it depends on may have changed (2026-11-26): the numbers (`_numbers_version`: the step table's own signal, also while the panel is hidden, or a new set of
        step columns), what is shown (the display mode), or which cells there are (rows, groups, roles, the step columns). A ROI that only moved changes none of these."""
        key = (
            self._numbers_version, self._display, self._grouped, self._linked,
            tuple(column.ref for column in self._step_columns), tuple((row.roi_id, row.group_id, row.role) for row in self._rows),
        )
        if key != self._decimals_key:
            self._decimals = table_decimals(self._all_cells(), self._display)
            self._decimals_key = key

    def _mode_of(self, column: StepColumn) -> str:
        """What a column shows: the chosen display, except a column with no spread (signal to noise) which has only its value, whatever is chosen."""
        return self._display if column.has_sd or is_excess(self._display) else DISPLAY_MEAN  # (in an excess mode a ratio column is blank: nothing predicts it)

    def _step_sort(self) -> StepSort | None:
        """The values the active step column sorts by, or None when a fixed column sorts."""
        if self._sort_column < COLUMN_COUNT or self._cells is None:
            return None
        column = self._step_columns[self._sort_column - COLUMN_COUNT]
        ref, mode = column.ref, self._mode_of(column)
        roi ={row.roi_id: v for row in self._rows if (v := sort_value(self._cells.roi_cell(row.roi_id, ref), mode)) is not None}
        group = {
            key: v for key, members in self._members_by_group().items()
            if (v := sort_value(self._cells.group_cell(key, sorted(members), ref), mode)) is not None
        }
        return StepSort(roi, group)

    def is_grouped_view(self) -> bool:
        """False whenever there are no groups (a lone "Ungrouped" header over
        every ROI would say nothing), and in the Linked view (its headers are not groups)."""
        return self._grouped and not self._linked

    def is_linked_view(self) -> bool:
        return self._linked

    def set_linked_view(self, linked: bool) -> None:
        """Arrange the table by links (each sample with its references, `rows.build_linked_sections`) or as before (groups or a flat list)."""
        if bool(linked) != self._linked:
            self._linked = bool(linked)
            self._rebuild()

    def collapse_key(self, index: QModelIndex) -> str | None:
        """What the remembered collapsed set holds for a group header: its group id (``"Ungrouped"``'s key for the synthetic one), or the key of a Linked header."""
        node = index.internalPointer() if index.isValid() else None
        if node is None or node.kind != "group":
            return None
        return node.section.group.collapse_key
    def unit(self) -> LengthUnit:
        return self._unit

    def rows(self) -> list[RoiRow]:
        """Every ROI's row, unsorted (the toolbox's order)."""
        return list(self._rows)

    def ordered_roi_ids(self) -> list[int]:
        """Every ROI in the order the table lists them now (sorted, grouped; rows of a collapsed group included), each once (the Linked view may list a shared reference
        under several samples). What a copy and a paste count rows by."""
        seen: set[int] = set()
        ordered: list[int] = []
        for node in self._iter_roi_nodes():
            if node.row.roi_id not in seen:
                seen.add(node.row.roi_id)
                ordered.append(node.row.roi_id)
        return ordered

    def edit_text_of(self, row: RoiRow, column: int) -> str:
        """The text the cell editor would start with for `row` in `column` (the unit as the table shows it now). Asked of a row made from the toolbox's live ROI: the
        table itself shows a change after a short pause, a step of the mouse wheel must start from the value that is stored."""
        return self._cell_value(row, column)[1]

    def has_rectangles(self) -> bool:
        """True if some ROI is a rectangle: the only time the width, height and angle columns have anything to show (`RoiTreeView.refresh_column_visibility`)."""
        return any(row.shape == SHAPE_RECTANGLE for row in self._rows)

    def display_order(self) -> list[tuple[GroupInfo | None, tuple[RoiRow, ...]]]:
        """The table as it is listed now: ``(group header, its ROI rows)`` in order; without groups one entry ``(None, every row)``. Collapsed groups are included."""
        if not self._grouped:
            return [(None, tuple(node.row for node in self._root.children))]
        return [(node.section.group, node.section.rows) for node in self._root.children]

    def _rebuild(self) -> None:
        stages = self._stages = StageTimer()
        self._rows = build_roi_rows(self._rois, self._groups, self._defaults, self._default_color_hex)
        stages.mark("rows")
        self._grouped = bool(self._groups) or self._linked
        if self._step_columns:
            self._refresh_decimals()  # one number of decimals for the rows as they are now
        stages.mark("decimals")
        root = _Node("root", None)
        if self._grouped:
            colors = {group.group_id: group.sample_color_hex for group in self._groups}
            sections = (
                build_linked_sections(self._rows, self._rois, self._default_color_hex)
                if self._linked
                else build_sections(
                    self._rows, self._groups, colors, sort_column=self._sort_column, descending=self._descending,
                    ungrouped_color_hex=self._default_color_hex, step_sort=self._step_sort(),
                )
            )
            for section in sections:
                header = _Node("group", root, section=section)
                header.position = len(root.children)
                root.children.append(header)
                for row in section.rows:
                    child = _Node("roi", header, row=row)
                    child.position = len(header.children)
                    header.children.append(child)
        else:
            for row in sort_rows(self._rows, self._sort_column, self._descending, self._step_sort()):
                child = _Node("roi", root, row=row)
                child.position = len(root.children)
                root.children.append(child)
        stages.mark("tree")  # (the sections, sorting and nodes)
        layout = self._layout_of(root)
        if layout == self._layout:
            self._adopt(root)  # the same rows in the same groups: only their numbers can have changed
            stages.mark("apply")
            return
        self._layout = layout
        self._reset_happened = True
        self.beginResetModel()
        # The old tree is kept alive until the next rebuild: Qt may still hold
        # indexes into it (they point at these Python objects) until the reset
        # has been fully processed by every view and selection model.
        self._previous_root, self._root = self._root, root
        self.endResetModel()
        stages.mark("apply")

    def last_rebuild_ms(self) -> dict[str, float]:
        """How long the stages of the last rebuild took, in ms: ``rows`` (the rows from the ROIs), ``decimals`` (the step numbers' common number of decimals; 0 without step columns),
        ``tree`` (sections, sort, nodes), ``apply`` (the reset, or the in-place update). For the panel's ``roi_table.rebuild`` log line."""
        return self._stages.ms()

    def _layout_of(self, root: _Node) -> tuple:
        """What the tree lists, without any value: the number of columns, the arrangement, and for each group header its identity and the ROI ids under it (a flat table: the ROI ids in order).
        Two rebuilds with the same layout differ only in what the cells say."""
        parts: list = [self.columnCount(), self._grouped, self._linked]
        for node in root.children:
            if node.kind == "group":
                group = node.section.group
                parts.append((group.group_id, group.collapse_key, group.synthetic, tuple(child.row.roi_id for child in node.children)))
            else:
                parts.append(node.row.roi_id)
        return tuple(parts)

    def _adopt(self, root: _Node) -> None:
        """``root`` lists the same rows in the same groups as the tree on show: put its rows and headers into the old nodes and say that the cells changed. Every index, the view's
        selection, which groups are open, the scroll position and an open cell editor all stay, and the view does not lay out its rows again (a reset made it ask the delegate for the
        height of every cell: 20 - 30 ms for 160 ROIs, `docs/roi_table_performance_2026-11-19.md`)."""
        for old, new in zip(self._root.children, root.children):
            old.row, old.section, old.members = new.row, new.section, new.members
            for old_child, new_child in zip(old.children, new.children):
                old_child.row = new_child.row
        top = self._root.children
        if not top:
            return
        last_column = self.columnCount() - 1
        self.dataChanged.emit(self.createIndex(0, 0, top[0]), self.createIndex(len(top) - 1, last_column, top[-1]), [VALUES_ONLY_ROLE])
        for node in top:
            if node.kind == "group" and node.children:
                self.dataChanged.emit(
                    self.createIndex(0, 0, node.children[0]), self.createIndex(len(node.children) - 1, last_column, node.children[-1]), [VALUES_ONLY_ROLE]
                )

    def take_reset_flag(self) -> bool:
        """True if the model was reset (its rows or groups changed, or the columns, the sort, the arrangement) since this was last asked; False if every rebuild since only updated the
        cells in place. The view puts back what a reset throws away (open groups, selection, scroll) only after a reset."""
        flag, self._reset_happened = self._reset_happened, False
        return flag

    # -- selection highlight for group headers ------------------------------------

    def set_selected_ids(self, roi_ids: Collection[int]) -> None:
        new = frozenset(roi_ids)
        if new == self._selected:
            return
        self._selected = new
        for node in self._root.children:
            if node.kind == "group":
                index = self.createIndex(node.position, 0, node)
                self.dataChanged.emit(index, index, [ALL_SELECTED_ROLE])

    # -- lookups ---------------------------------------------------------------------

    def kind(self, index: QModelIndex) -> str | None:
        return index.internalPointer().kind if index.isValid() else None

    def is_group(self, index: QModelIndex) -> bool:
        return self.kind(index) == "group"

    def roi_id(self, index: QModelIndex) -> int | None:
        node = index.internalPointer() if index.isValid() else None
        return node.row.roi_id if node is not None and node.kind == "roi" else None

    def group_id(self, index: QModelIndex) -> str | None:
        """A group header's id; for a ROI row, the group it is in. ``None`` for
        "Ungrouped" and for a ROI in no group."""
        node = index.internalPointer() if index.isValid() else None
        if node is None:
            return None
        if node.kind == "group":
            return node.section.group.group_id
        return node.row.group_id

    def member_ids(self, index: QModelIndex) -> tuple[int, ...]:
        """The ROIs under a group header."""
        node = index.internalPointer() if index.isValid() else None
        if node is None or node.kind != "group":
            return ()
        return tuple(row.roi_id for row in node.section.rows)

    def roi_index(self, roi_id: int, column: int = 0) -> QModelIndex:
        for node in self._iter_roi_nodes():
            if node.row.roi_id == roi_id:
                return self.createIndex(node.position, column, node)
        return QModelIndex()

    def roi_indexes(self, roi_ids: Collection[int]) -> list[QModelIndex]:
        """Column-0 indexes of the ROIs in ``roi_ids`` that are shown, in one
        pass over the tree (asking `roi_index` once per ROI would be quadratic)."""
        wanted = set(roi_ids)
        return [self.createIndex(node.position, 0, node) for node in self._iter_roi_nodes() if node.row.roi_id in wanted]

    def roi_index_runs(self, roi_ids: Collection[int]) -> list[tuple[QModelIndex, QModelIndex]]:
        """The ROIs in ``roi_ids`` that are shown, as runs of rows that follow each other under one parent: ``(first, last)`` column-0 indexes of each run, in table order. Selecting
        a run is one range for Qt; selecting the ROIs one by one made 160 ranges to merge for 160 ROIs (50 ms; 16 runs of a grouped table: 5 ms)."""
        wanted = set(roi_ids)
        runs: list[tuple[QModelIndex, QModelIndex]] = []
        first = last = None
        for node in self._iter_roi_nodes():
            if node.row.roi_id not in wanted:
                continue
            if last is not None and node.parent is last.parent and node.position == last.position + 1:
                last = node
                continue
            if first is not None:
                runs.append((self.createIndex(first.position, 0, first), self.createIndex(last.position, 0, last)))
            first = last = node
        if first is not None:
            runs.append((self.createIndex(first.position, 0, first), self.createIndex(last.position, 0, last)))
        return runs

    def group_index(self, group_id: str | None) -> QModelIndex:
        for node in self._root.children:
            if node.kind == "group" and node.section.group.group_id == group_id:
                return self.createIndex(node.position, 0, node)
        return QModelIndex()

    def group_headers(self) -> list[QModelIndex]:
        return [self.createIndex(node.position, 0, node) for node in self._root.children if node.kind == "group"]

    def row_for(self, roi_id: int) -> RoiRow | None:
        return next((row for row in self._rows if row.roi_id == roi_id), None)

    def _iter_roi_nodes(self):
        for node in self._root.children:
            if node.kind == "roi":
                yield node
            else:
                yield from node.children

    # -- QAbstractItemModel ------------------------------------------------------------

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        # The same answers as `hasIndex` + `createIndex`, without `hasIndex` (it calls `rowCount` and `columnCount` back in Python): Qt asks for every cell of every row while it
        # lays the tree out, 44 000 times for 160 ROIs and 241 step columns (2026-11-26, `tools/roi_table_timing.py`).
        if row < 0 or column < 0 or column >= COLUMN_COUNT + len(self._step_columns) or parent.column() > 0:
            return QModelIndex()
        if self._layout_column is not None and COLUMN_COUNT <= column != self._layout_column:
            return QModelIndex()  # (inside `layout_pass`)
        parent_node = parent.internalPointer() if parent.isValid() else self._root
        children = parent_node.children
        if row >= len(children):
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    @contextmanager
    def layout_pass(self, first_shown_column: int):
        """While Qt lays the tree out (`expandAll`), answer for the fixed columns and for `first_shown_column` only (a logical column: the first one the view shows at its left edge).
        Qt takes a row's height as the largest size hint over the columns **from the first one on screen to the last one** (a shown tree; a tree not shown asks them all): at the
        left edge of 241 step columns that is 44 000 `sizeHint` calls, ~1 s. Every column gives the same height (`RoiTableDelegate.sizeHint`), so one valid column is enough, and it
        must be one Qt asks: the first shown one always is (2026-11-27; the 2026-11-26 version dropped every step column, and a table scrolled to them got height 0 for every row).
        Used by `RoiTablePanel._rebuild` only, around `expandAll`; nothing is drawn or edited while it is on."""
        self._layout_column = first_shown_column
        try:
            yield
        finally:
            self._layout_column = None

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        parent_node = index.internalPointer().parent
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        return self.createIndex(parent_node.position, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        node = parent.internalPointer() if parent.isValid() else self._root
        return len(node.children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return COLUMN_COUNT + len(self._step_columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and COLUMN_COUNT <= section < self.columnCount():
            step = self._step_columns[section - COLUMN_COUNT]
            if role == Qt.ItemDataRole.DisplayRole:
                return step.title
            if role == Qt.ItemDataRole.ToolTipRole:
                return step.tooltip
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if role == COLUMN_KIND_ROLE:
                return step.kind
            if role == STEP_TINT_ROLE:
                return self._tint_of(section)
            return None
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < COLUMN_COUNT:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMN_TITLES[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = _HEADER_TIPS[section]
            return f"{tip} ({self._unit.label})" if section in LENGTH_COLUMNS else tip  # (the angle is in degrees whatever the unit is)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            right = section in _RIGHT_ALIGNED_COLUMNS
            return int((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        if node.kind == "group":
            return _GROUP_FLAGS[self._linked, index.column() == 0 and node.section.group.group_id is not None]  # only a real group's name can be edited
        column = index.column()
        editable = column in EDITABLE_COLUMNS and not (column in SIZE_COLUMNS and not has_cell(node.row, column))  # a mask has no diameter, a reference no rim, only a rectangle a width, a height and an angle
        return _ROI_FLAGS[self._linked, editable]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        column = index.column()
        if role == KIND_ROLE:
            return node.kind
        if role == DEPTH_ROLE:
            return 0 if node.parent is self._root else 1
        if node.kind == "group":
            return None if role == PAINT_ROLE else self._group_data(node, column, role)
        if role == PAINT_ROLE:
            return self._paint_data(node.row, column)
        return self._roi_data(node.row, column, role)

    def _paint_data(self, row: RoiRow, column: int) -> tuple[str, bool, bool, int, QColor | None]:
        """`PAINT_ROLE`: what the delegate paints in a ROI row's cell, the same answers `DisplayRole`, `INHERITED_ROLE`, `MASK_ROLE`, `STEP_STALE_ROLE`, `TextAlignmentRole` and
        `STEP_TINT_ROLE` give one by one (a step cell's number is looked up once instead of twice)."""
        if column >= COLUMN_COUNT:
            if self._cells is None or not 0 <= column - COLUMN_COUNT < len(self._step_columns):
                return "", False, False, _ALIGN_RIGHT, None
            step = self._step_columns[column - COLUMN_COUNT]
            value = self._cells.roi_cell(row.roi_id, step.ref)
            text = format_cell(value, self._mode_of(step), self._decimals if step.decimals is None else step.decimals, self._display_of.get(column))
            return text or "", False, value is not None and not value.current, _ALIGN_RIGHT, self._tint_of(column)
        text = self._cell_value(row, column)[0]
        dim = (column in RIM_COLUMNS and row.rim_inherited and row.has_rim) or (row.is_mask and text == "mask")
        return text, dim, False, _ALIGN_RIGHT if column in _RIGHT_ALIGNED_COLUMNS else _ALIGN_LEFT, None

    def _group_data(self, node: _Node, column: int, role: int):
        group: GroupInfo = node.section.group
        if role == GROUP_ID_ROLE:
            return group.group_id
        if role == COLOR_ROLE:
            return group.color_hex
        if role == COUNT_ROLE:
            return len(node.section.rows)
        if role == ALL_SELECTED_ROLE:
            members = {row.roi_id for row in node.section.rows}
            return bool(members) and members <= self._selected
        if column >= COLUMN_COUNT:
            return self._step_cell(node, column, role)
        if column != 0:
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return group.name
        if role == Qt.ItemDataRole.ToolTipRole:
            if group.note:
                return group.note
            count = len(node.section.rows)
            noun = "ROI" if count == 1 else "ROIs"
            return f"{group.name}: {count} {noun}. Double-click to rename." if group.group_id else f"{count} {noun} in no group"
        return None

    def _roi_data(self, row: RoiRow, column: int, role: int):
        if role == ROI_ID_ROLE:
            return row.roi_id
        if role == GROUP_ID_ROLE:
            return row.group_id
        if role == COLOR_ROLE:
            return row.color_hex
        if role == MASK_ROLE:
            return row.is_mask
        if role == SHAPE_ROLE:
            return row.shape
        if role == TYPE_ROLE:
            return (row.type_icon, row.kind)
        if role == INHERITED_ROLE:
            return column in RIM_COLUMNS and row.rim_inherited and row.has_rim
        if column >= COLUMN_COUNT:
            return self._step_cell(None, column, role, row=row)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            right = column in _RIGHT_ALIGNED_COLUMNS
            return int((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        value = self._cell_value(row, column)
        if role == Qt.ItemDataRole.EditRole:
            return value[1]
        if role == Qt.ItemDataRole.DisplayRole:
            return value[0]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, column)
        return None

    def _step_cell(self, node: _Node | None, column: int, role: int, row: RoiRow | None = None):
        """A step column's cell of a group header (`node`) or of a ROI row (`row`)."""
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if self._cells is None or not 0 <= column - COLUMN_COUNT < len(self._step_columns):
            return None
        if role == STEP_TINT_ROLE:
            return self._tint_of(column)  # asked for on every paint: before the cell's numbers are looked up
        step = self._step_columns[column - COLUMN_COUNT]
        if node is not None:
            group = node.section.group
            if group.synthetic:
                return None  # a Linked header is only a label: the numbers are the rows'
            value = self._cells.group_cell(group.group_id, node.members, step.ref)
            row_text, is_group = group.name, True
        else:
            value = self._cells.roi_cell(row.roi_id, step.ref)
            row_text, is_group = f"ROI {row.roi_id}", False
        if role == Qt.ItemDataRole.DisplayRole:
            return format_cell(value, self._mode_of(step), self._decimals if step.decimals is None else step.decimals, self._display_of.get(column))
        if role == Qt.ItemDataRole.ToolTipRole:
            return cell_tooltip(row_text, step, value, is_group=is_group)
        if role == STEP_STALE_ROLE:
            return value is not None and not value.current
        return None

    def _cell_value(self, row: RoiRow, column: int) -> tuple[str, str]:
        """``(shown, text the editor starts with)``."""
        unit = self._unit
        if column == COLUMN_ID:
            return (f"R{row.roi_id}" if row.role == "reference" else str(row.roi_id)), str(row.roi_id)  # a reference ROI is "R7"; editing starts from its number
        if column == COLUMN_NAME:
            return "", ""  # labels belong to groups; a ROI has its id
        if column in (COLUMN_X, COLUMN_Y):
            value = row.x if column == COLUMN_X else row.y
            return format_length(value, unit, self._display_of.get(column)), edit_text(value, unit)
        if column == COLUMN_SHAPE:
            return TYPE_LABELS[row.type_icon], ""
        if column == COLUMN_AREA:
            return format_area(row.area_px, unit, self._display_of.get(column)), ""  # derived from the shape: nothing to edit
        if column in RECTANGLE_COLUMNS:
            if column not in SHAPE_FIELDS[row.shape]:
                return "", ""  # only a rectangle has a width, a height and an angle
            if column == COLUMN_ANGLE:
                return format_angle(row.angle, self._display_of.get(column)), angle_edit_text(row.angle)
            value = row.width if column == COLUMN_WIDTH else row.height
            return format_length(value, unit, self._display_of.get(column)), edit_text(value, unit)
        if column in RIM_COLUMNS:
            if not row.has_rim:
                return "", ""  # a reference has no rim, nor has a ROI whose rim is switched off
            value = row.rim_offset if column == COLUMN_RIM_OFFSET else row.rim_thickness
            return format_length(value, unit, self._display_of.get(column)), edit_text(value, unit)
        if row.is_mask:
            return "mask", ""
        if column not in SHAPE_FIELDS[row.shape]:
            return "", ""  # this shape has no such number
        return format_length(row.sample, unit, self._display_of.get(column)), edit_text(row.sample, unit)

    def _tooltip(self, row: RoiRow, column: int) -> str | None:
        unit = self._unit.label
        if column == COLUMN_ID:
            return row.link_note or None
        if column == COLUMN_SHAPE:
            return " ".join(part for part in (f"{TYPE_LABELS[row.type_icon]}.", row.link_note, row.shape_note) if part)  # "Circle with rim. Measured against its own rim."
        if column == COLUMN_AREA:
            return area_tooltip(row, self._unit)
        if column in (COLUMN_X, COLUMN_Y) and row.nudge_count:
            plural = "" if row.nudge_count == 1 else "s"
            return f"Position on the reference image ({unit}). {row.nudge_count} manual per-wavelength nudge{plural}."
        if column in RIM_COLUMNS and row.has_rim:
            return f"Taken from the shared default ({unit}). Edit to set this ROI's own." if row.rim_inherited else f"This ROI's own value ({unit})."
        if column == COLUMN_SAMPLE and row.is_mask:
            return "This ROI is drawn as a mask, so it has no diameter."
        return None

    # -- drag and drop ------------------------------------------------------------------

    def supportedDragActions(self) -> Qt.DropAction:  # type: ignore[override]
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:  # type: ignore[override]
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:  # type: ignore[override]
        return [MIME_TYPE]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:  # type: ignore[override]
        ids = sorted({roi_id for index in indexes if (roi_id := self.roi_id(index)) is not None})
        data = QMimeData()
        data.setData(MIME_TYPE, json.dumps(ids).encode("utf-8"))
        return data

    def drop_target(self, row: int, parent: QModelIndex) -> DropTarget | None:
        """What a drop at (``row``, ``parent``) means, or ``None`` if nothing
        can be dropped there. Qt reports a drop *on* an item as ``row == -1``
        with the item as ``parent``, and a drop *between* rows as the row index
        under the parent those rows share."""
        if self._linked:
            return None  # the Linked view is read-only: dropping would put ROIs in a group or reorder them behind the arrangement's back
        if parent.isValid():
            node = parent.internalPointer()
            if node.kind != "group":
                return None
            group_id = node.section.group.group_id
            if row < 0:
                return DropTarget("header", group_id)
            return DropTarget("section", group_id, min(row, len(node.children)))
        if self._grouped:
            return None  # between two group headers: groups are not reordered by dragging ROIs
        count = len(self._root.children)
        return DropTarget("flat", None, count if row < 0 else min(row, count))

    def canDropMimeData(self, data, action, row: int, column: int, parent: QModelIndex) -> bool:  # type: ignore[override]
        return data.hasFormat(MIME_TYPE) and action == Qt.DropAction.MoveAction and self.drop_target(row, parent) is not None

    def dropMimeData(self, data, action, row: int, column: int, parent: QModelIndex) -> bool:  # type: ignore[override]
        if not self.canDropMimeData(data, action, row, column, parent):
            return False
        try:
            ids = [int(roi_id) for roi_id in json.loads(bytes(data.data(MIME_TYPE)).decode("utf-8"))]
        except (ValueError, TypeError):
            return False
        target = self.drop_target(row, parent)
        if ids and target is not None:
            self.roi_dropped.emit(ids, target)
        return False  # see `roi_dropped`

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:  # type: ignore[override]
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        node = index.internalPointer()
        text = str(value)
        if node.kind == "group":
            if node.section.group.group_id is not None and text.strip() != node.section.group.name:
                self.cell_edited.emit("group", node.section.group.group_id, 0, text)
            return False
        if text.strip() != self._cell_value(node.row, index.column())[1]:
            self.cell_edited.emit("roi", node.row.roi_id, index.column(), text)
        return False  # the toolbox decides; the new value comes back through set_content
