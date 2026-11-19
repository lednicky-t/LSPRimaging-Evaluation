"""Copying and pasting cells of the ROI / Groups table, as plain data. Pure: no Qt (the view, the panel and the clipboard are in `view.py`, `panel.py`).

A copied **block** is some rows x some number columns, with the numbers as the table stores them (pixels, degrees), so switching px / µm between the copy and the paste is safe.

The paste rules (maintainer, 2026-11-19):

* **Only matching columns are filled** (a copied inner diameter goes to inner diameters, a width to widths): column types are never mixed. If the target has no column the block
  has, nothing is pasted and the answer says why.
* **One source row fills every target row** with that value. **Several source rows repeat in order** down the target rows ("a pattern") until the target rows run out.
* **One target row and a block of several rows:** the block runs on down from that row, as Excel does (`PastePlan.rows` says which rows it reached; the table's end cuts it).
* A cell the source has no value for, or a target row has no such cell (a circle has no width), is skipped and counted, never filled with a guess.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field

from .rows import (
    COLUMN_ANGLE,
    COLUMN_HEIGHT,
    COLUMN_RING_IN,
    COLUMN_RING_OUT,
    COLUMN_SAMPLE,
    COLUMN_TITLES,
    COLUMN_WIDTH,
    COLUMN_X,
    COLUMN_Y,
    NUMERIC_COLUMNS,
    LengthUnit,
    format_angle,
    format_length,
)

MIME_TYPE = "application/x-lspr-roi-cells"
"""The clipboard format of a copied block (JSON); the same copy also puts plain tab-separated text on the clipboard, for other programs."""

FIELD_OF_COLUMN = {
    COLUMN_X: "x",
    COLUMN_Y: "y",
    COLUMN_SAMPLE: "sample_diameter_px",
    COLUMN_RING_IN: "reference_inner_diameter_px",
    COLUMN_RING_OUT: "reference_outer_diameter_px",
    COLUMN_WIDTH: "width_px",
    COLUMN_HEIGHT: "height_px",
    COLUMN_ANGLE: "angle_deg",
}
"""The `RoiToolbox.set_cell_values` field each number column sets."""


@dataclass(frozen=True)
class CellBlock:
    """What a copy holds: ``columns`` (column numbers, left to right as the table shows them) and one tuple of values per source row (``None`` = that row has no such cell)."""

    columns: tuple[int, ...]
    rows: tuple[tuple[float | None, ...], ...]
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    """Names this copy: the marks stay while the clipboard still holds it, and go when something else is copied."""


def column_name(column: int) -> str:
    """A column's title for a sentence (``D_s``, ``W``)."""
    return COLUMN_TITLES[column]


def names_of(columns: Collection[int]) -> str:
    return ", ".join(column_name(c) for c in sorted(columns))


def block_to_text(block: CellBlock, unit: LengthUnit) -> str:
    """The block as tab-separated text (a row a line), numbers as the table shows them, for pasting into another program."""
    lines = []
    for row in block.rows:
        cells = []
        for column, value in zip(block.columns, row):
            if value is None:
                cells.append("")
            else:
                cells.append(format_angle(value) if column == COLUMN_ANGLE else format_length(value, unit))
        lines.append("\t".join(cells))
    return "\n".join(lines)


def block_to_payload(block: CellBlock) -> bytes:
    return json.dumps({"columns": list(block.columns), "rows": [list(row) for row in block.rows], "token": block.token}).encode("utf-8")


def block_from_payload(raw: bytes | str) -> CellBlock | None:
    """The block a payload holds, or ``None`` for anything that is not one (another program's data, a damaged payload)."""
    try:
        data = json.loads(raw if isinstance(raw, str) else bytes(raw).decode("utf-8"))
        columns = tuple(int(c) for c in data["columns"])
        rows = tuple(tuple(None if v is None else float(v) for v in row) for row in data["rows"])
        token = str(data.get("token", ""))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not columns or not rows or any(c not in NUMERIC_COLUMNS for c in columns) or any(len(row) != len(columns) for row in rows):
        return None
    return CellBlock(columns, rows, token or uuid.uuid4().hex)


@dataclass(frozen=True)
class PastePlan:
    """What a paste would do. ``values`` is what to hand `RoiToolbox.set_cell_values` (empty when there is nothing to paste: then ``refusal`` says why)."""

    values: dict[int, dict[str, float]] = field(default_factory=dict)
    rows: tuple[int, ...] = ()  # the ROIs the paste reached, in table order
    columns: tuple[int, ...] = ()  # the columns that got numbers
    refusal: str = ""
    note: str = ""  # something worth saying about a paste that did happen ("2 rows have no width", the table ended)

    @property
    def cell_count(self) -> int:
        return sum(len(fields) for fields in self.values.values())


def plan_paste(
    block: CellBlock,
    target_ids: Sequence[int],
    table_order: Sequence[int],
    target_columns: Collection[int] | None,
    has_cell: Callable[[int, int], bool],
) -> PastePlan:
    """Work out a paste. ``target_ids``: the ROIs to fill, in table order. ``table_order``: every ROI in table order (a block that runs on down needs the rows below).
    ``target_columns``: the columns selected in the target, or ``None`` for "all that match" (rows chosen, no cells). ``has_cell(roi_id, column)``: does that ROI have such a
    cell (a circle has no width, a rectangle no diameter; a position every ROI has)."""
    if not target_ids:
        return PastePlan(refusal="Nothing to paste into: select a ROI first.")
    fill = [c for c in block.columns if target_columns is None or c in target_columns]
    if not fill:
        return PastePlan(refusal=f"No matching column: copied {names_of(block.columns)}, selected {names_of(target_columns or ())}.")
    source_count = len(block.rows)
    note = ""
    if len(target_ids) == 1 and source_count > 1:  # Excel: the block runs on down from the one row
        position = list(table_order).index(target_ids[0]) if target_ids[0] in table_order else -1
        rows = list(table_order[position:position + source_count]) if position >= 0 else [target_ids[0]]
        if len(rows) < source_count:
            note = f"the table ended after {len(rows)} of {source_count} copied rows"
    else:  # one value for every row, or the rows over and over until the target runs out
        rows = list(target_ids)
    values: dict[int, dict[str, float]] = {}
    missing_cell: dict[int, int] = {}
    no_value = 0
    for position, roi_id in enumerate(rows):
        source = block.rows[position % source_count]
        for column in fill:
            value = source[block.columns.index(column)]
            if value is None:
                no_value += 1
            elif not has_cell(roi_id, column):
                missing_cell[column] = missing_cell.get(column, 0) + 1
            else:
                values.setdefault(roi_id, {})[FIELD_OF_COLUMN[column]] = value
    if not values:
        reasons = [f"{count} {'row has' if count == 1 else 'rows have'} no {column_name(c)}" for c, count in sorted(missing_cell.items())]
        if no_value:
            reasons.append("the copied cells have no value there")
        return PastePlan(refusal="Nothing to paste: " + ("; ".join(reasons) or "no matching cells") + ".")
    parts = [f"{count} {'row has' if count == 1 else 'rows have'} no {column_name(c)}" for c, count in sorted(missing_cell.items())]
    if note:
        parts.append(note)
    pasted_columns = tuple(c for c in fill if any(FIELD_OF_COLUMN[c] in fields for fields in values.values()))
    return PastePlan(values, tuple(r for r in rows if r in values), pasted_columns, note="; ".join(parts))
