"""``RoiTreeModel``: the ROI/Group table's rows, as a Qt tree model.

Grouped view: one top-level node per group (plus "Ungrouped"), ROIs below it.
Flat view: every ROI top-level. It presents what `RoiToolbox` holds and owns no
ROI state of its own: ``set_content`` replaces everything, and an edit is not
applied here - ``setData`` reports it through ``cell_edited`` and returns
``False``, the panel asks the toolbox, and the change comes back through
``set_content``. (A "command model": the view never shows a value the toolbox
did not accept.)
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QAbstractItemModel, QMimeData, QModelIndex, Qt, pyqtSignal

from ...roi.model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup
from ...roi.palette import DEFAULT_ROI_COLOR_HEX
from .rows import (
    COLUMN_COUNT,
    COLUMN_ID,
    COLUMN_NAME,
    COLUMN_RING_IN,
    COLUMN_RING_OUT,
    COLUMN_SAMPLE,
    COLUMN_TITLES,
    COLUMN_X,
    COLUMN_Y,
    DIAMETER_COLUMNS,
    EDITABLE_COLUMNS,
    PIXELS,
    GroupInfo,
    LengthUnit,
    RoiRow,
    Section,
    build_roi_rows,
    build_sections,
    edit_text,
    format_length,
    sort_rows,
)

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

_NUMERIC_COLUMNS = (COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT)
_HEADER_TIPS = {
    COLUMN_ID: "Position in the list. Reordering changes these numbers.",
    COLUMN_NAME: "Optional name for the ROI.",
    COLUMN_X: "Centre x on the reference image.",
    COLUMN_Y: "Centre y on the reference image.",
    COLUMN_SAMPLE: "Sample diameter.",
    COLUMN_RING_IN: "Reference ring, inner diameter. Grey italic: taken from the shared default.",
    COLUMN_RING_OUT: "Reference ring, outer diameter. Grey italic: taken from the shared default.",
}


class _Node:
    __slots__ = ("kind", "parent", "children", "position", "section", "row")

    def __init__(self, kind: str, parent: "_Node | None", *, section: Section | None = None, row: RoiRow | None = None) -> None:
        self.kind = kind
        self.parent = parent
        self.children: list[_Node] = []
        self.position = 0
        self.section = section
        self.row = row


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
        self._unit: LengthUnit = PIXELS
        self._sort_column = COLUMN_ID
        self._descending = False
        self._flat = False
        self._rows: list[RoiRow] = []
        self._rois: tuple[AreaRoi, ...] = ()
        self._groups: tuple[AreaRoiGroup, ...] = ()
        self._defaults = AreaRoiDetectionSettings()
        self._default_color_hex = DEFAULT_ROI_COLOR_HEX
        self._selected: frozenset[int] = frozenset()
        self._grouped = False

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
        if (column, descending) != (self._sort_column, self._descending):
            self._sort_column, self._descending = column, descending
            self._rebuild()

    def set_flat(self, flat: bool) -> None:
        if flat != self._flat:
            self._flat = flat
            self._rebuild()

    def sort_state(self) -> tuple[int, bool]:
        return self._sort_column, self._descending

    def is_grouped_view(self) -> bool:
        """False in the flat view, and whenever there are no groups (a lone
        "Ungrouped" header over every ROI would say nothing)."""
        return self._grouped

    def unit(self) -> LengthUnit:
        return self._unit

    def rows(self) -> list[RoiRow]:
        """Every ROI's row, unsorted (the toolbox's order)."""
        return list(self._rows)

    def _rebuild(self) -> None:
        self._rows = build_roi_rows(self._rois, self._groups, self._defaults, self._default_color_hex)
        self._grouped = bool(self._groups) and not self._flat
        root = _Node("root", None)
        if self._grouped:
            colors = {group.group_id: group.sample_color_hex for group in self._groups}
            for section in build_sections(self._rows, self._groups, colors, sort_column=self._sort_column, descending=self._descending):
                header = _Node("group", root, section=section)
                header.position = len(root.children)
                root.children.append(header)
                for row in section.rows:
                    child = _Node("roi", header, row=row)
                    child.position = len(header.children)
                    header.children.append(child)
        else:
            for row in sort_rows(self._rows, self._sort_column, self._descending):
                child = _Node("roi", root, row=row)
                child.position = len(root.children)
                root.children.append(child)
        self.beginResetModel()
        # The old tree is kept alive until the next rebuild: Qt may still hold
        # indexes into it (they point at these Python objects) until the reset
        # has been fully processed by every view and selection model.
        self._previous_root, self._root = self._root, root
        self.endResetModel()

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
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = parent.internalPointer() if parent.isValid() else self._root
        return self.createIndex(row, column, parent_node.children[row])

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
        return COLUMN_COUNT

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < COLUMN_COUNT:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMN_TITLES[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = _HEADER_TIPS[section]
            return f"{tip} ({self._unit.label})" if section in _NUMERIC_COLUMNS else tip
        if role == Qt.ItemDataRole.TextAlignmentRole:
            right = section in _NUMERIC_COLUMNS
            return int((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if node.kind == "group":
            flags |= Qt.ItemFlag.ItemIsDropEnabled  # drop ROIs on a header to put them in that group
            if index.column() == 0 and node.section.group.group_id is not None:
                flags |= Qt.ItemFlag.ItemIsEditable
            return flags
        flags |= Qt.ItemFlag.ItemIsDragEnabled  # ROI rows are not drop targets: a drop lands between rows
        column = index.column()
        if column in EDITABLE_COLUMNS and not (node.row.is_mask and column in DIAMETER_COLUMNS):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

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
            return self._group_data(node, column, role)
        return self._roi_data(node.row, column, role)

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
        if column != 0:
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return group.name
        if role == Qt.ItemDataRole.ToolTipRole:
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
        if role == INHERITED_ROLE:
            return (column == COLUMN_RING_IN and row.inner_inherited) or (column == COLUMN_RING_OUT and row.outer_inherited)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            right = column in _NUMERIC_COLUMNS
            return int((Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        value = self._cell_value(row, column)
        if role == Qt.ItemDataRole.EditRole:
            return value[1]
        if role == Qt.ItemDataRole.DisplayRole:
            return value[0]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, column)
        return None

    def _cell_value(self, row: RoiRow, column: int) -> tuple[str, str]:
        """``(shown, text the editor starts with)``."""
        unit = self._unit
        if column == COLUMN_ID:
            return str(row.roi_id), str(row.roi_id)
        if column == COLUMN_NAME:
            return row.label, row.label
        if column in (COLUMN_X, COLUMN_Y):
            value = row.x if column == COLUMN_X else row.y
            return format_length(value, unit), edit_text(value, unit)
        if row.is_mask:
            return "mask", ""
        value = {COLUMN_SAMPLE: row.sample, COLUMN_RING_IN: row.inner, COLUMN_RING_OUT: row.outer}[column]
        return format_length(value, unit), edit_text(value, unit)

    def _tooltip(self, row: RoiRow, column: int) -> str | None:
        unit = self._unit.label
        if column in (COLUMN_X, COLUMN_Y) and row.nudge_count:
            plural = "" if row.nudge_count == 1 else "s"
            return f"Position on the reference image ({unit}). {row.nudge_count} manual per-wavelength nudge{plural}."
        if column in (COLUMN_RING_IN, COLUMN_RING_OUT):
            inherited = row.inner_inherited if column == COLUMN_RING_IN else row.outer_inherited
            return f"Taken from the shared default ({unit}). Edit to set this ROI's own." if inherited else f"This ROI's own value ({unit})."
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
