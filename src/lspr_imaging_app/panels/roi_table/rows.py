"""What the ROI/Group table shows, as plain data. Pure: no Qt.

Everything the table needs to *decide* lives here so it can be tested without
a window: how a length is shown and read back in px or µm, which diameters a
ROI inherits, how ROIs are sorted and sectioned by group, and which ROIs a
"move up/down" may reorder. The model and the painting only present it.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from ...roi.model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup
from ...roi.palette import DEFAULT_ROI_COLOR_HEX

COLUMN_ID, COLUMN_NAME, COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT = range(7)
COLUMN_COUNT = 7
COLUMN_TITLES = ("#", "Name", "x", "y", "D_s", "d_r", "D_r")
"""Header titles; ``X_y`` is drawn as X with a subscript y (`view.SubscriptHeader`)."""
EDITABLE_COLUMNS = (COLUMN_NAME, COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT)
DIAMETER_COLUMNS = (COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT)


# -- lengths: px or µm -------------------------------------------------------


@dataclass(frozen=True)
class LengthUnit:
    """How lengths are shown. ``um_per_px`` is ``None`` for pixels."""

    label: str
    um_per_px: float | None = None


PIXELS = LengthUnit("px")


def micrometers(um_per_px: float) -> LengthUnit:
    if not (math.isfinite(um_per_px) and um_per_px > 0.0):
        raise ValueError(f"um_per_px must be a positive number, got {um_per_px!r}")
    return LengthUnit("µm", float(um_per_px))


def to_display(px: float, unit: LengthUnit) -> float:
    return px if unit.um_per_px is None else px * unit.um_per_px


def from_display(value: float, unit: LengthUnit) -> float:
    return value if unit.um_per_px is None else value / unit.um_per_px


def format_length(px: float, unit: LengthUnit) -> str:
    """What a cell shows: one decimal for px, none for µm (the stored value is always px)."""
    return f"{to_display(px, unit):.1f}" if unit.um_per_px is None else f"{to_display(px, unit):.0f}"


def edit_text(px: float, unit: LengthUnit) -> str:
    """What the cell editor starts with: the same precision the cell shows
    (one decimal for px, none for µm), trailing zeros dropped."""
    digits = 1 if unit.um_per_px is None else 0
    text = f"{to_display(px, unit):.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def parse_length(text: str, unit: LengthUnit) -> float:
    """What the user typed -> pixels. A decimal comma is accepted. Raises
    ``ValueError`` with a message fit to show the user."""
    cleaned = text.strip().replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"'{text.strip()}' is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"'{text.strip()}' is not a finite number")
    return from_display(value, unit)


STEP_PX = 0.5
STEP_PX_LARGE = 5.0
STEP_UM = 1.0
STEP_UM_LARGE = 10.0
"""How far one Up/Down (or Ctrl+wheel) step moves a diameter; Shift takes the large one.
In µm (no decimals shown) the step is a whole µm instead."""


def step_text(text: str, direction: int, large: bool, unit: LengthUnit) -> str | None:
    """``text`` (a length in ``unit``) moved one step up (+1) or down (-1), as
    editor text, never below zero; ``None`` if ``text`` is not a number."""
    try:
        px = parse_length(text, unit)
    except ValueError:
        return None
    if unit.um_per_px is None:
        step_px = STEP_PX_LARGE if large else STEP_PX
    else:  # µm shows no decimals, so a step must be a whole µm to be visible
        step_px = (STEP_UM_LARGE if large else STEP_UM) / unit.um_per_px
    return edit_text(max(0.0, px + direction * step_px), unit)


# -- rows --------------------------------------------------------------------


@dataclass(frozen=True)
class RoiRow:
    roi_id: int
    label: str
    group_id: str | None
    color_hex: str
    has_own_color: bool
    x: float
    y: float
    sample: float
    inner: float
    outer: float
    inner_inherited: bool
    outer_inherited: bool
    is_mask: bool
    nudge_count: int


@dataclass(frozen=True)
class GroupInfo:
    """One group header. ``group_id is None`` is the synthetic "Ungrouped"
    header: no model object behind it, so it cannot be renamed, recoloured,
    deleted or moved."""

    group_id: str | None
    name: str
    color_hex: str


@dataclass(frozen=True)
class Section:
    group: GroupInfo
    rows: tuple[RoiRow, ...]


def build_roi_rows(
    rois: Sequence[AreaRoi],
    groups: Sequence[AreaRoiGroup],
    defaults: AreaRoiDetectionSettings,
    default_color_hex: str = DEFAULT_ROI_COLOR_HEX,
) -> list[RoiRow]:
    """``default_color_hex``: the colour of a ROI with none of its own, as the Image panel draws it (the Sample swatch)."""
    group_of = {roi_id: group.group_id for group in groups for roi_id in group.area_roi_ids}
    rows = []
    for roi in rois:
        rows.append(
            RoiRow(
                roi_id=roi.area_roi_id,
                label=roi.label or "",
                group_id=group_of.get(roi.area_roi_id),
                color_hex=roi.sample_color_hex or default_color_hex,
                has_own_color=roi.sample_color_hex is not None,
                x=roi.center_x,
                y=roi.center_y,
                sample=roi.sample_diameter_px,
                inner=defaults.reference_inner_diameter_px if roi.reference_inner_diameter_px is None else roi.reference_inner_diameter_px,
                outer=defaults.reference_outer_diameter_px if roi.reference_outer_diameter_px is None else roi.reference_outer_diameter_px,
                inner_inherited=roi.reference_inner_diameter_px is None,
                outer_inherited=roi.reference_outer_diameter_px is None,
                is_mask=roi.sample_geometry_type == "mask",
                nudge_count=len(roi.per_wavelength or {}),
            )
        )
    return rows


# -- sorting and sections ----------------------------------------------------

_SORT_KEYS = {
    COLUMN_ID: lambda row: (row.roi_id,),
    COLUMN_NAME: lambda row: (row.label == "", row.label.casefold(), row.roi_id),
    COLUMN_X: lambda row: (row.x, row.roi_id),
    COLUMN_Y: lambda row: (row.y, row.roi_id),
    COLUMN_SAMPLE: lambda row: (row.sample, row.roi_id),
    COLUMN_RING_IN: lambda row: (row.inner, row.roi_id),
    COLUMN_RING_OUT: lambda row: (row.outer, row.roi_id),
}


def sort_rows(rows: Sequence[RoiRow], column: int, descending: bool) -> list[RoiRow]:
    return sorted(rows, key=_SORT_KEYS.get(column, _SORT_KEYS[COLUMN_ID]), reverse=descending)


def build_sections(
    rows: Sequence[RoiRow],
    groups: Sequence[AreaRoiGroup],
    group_colors: dict[str, str],
    *,
    sort_column: int,
    descending: bool,
) -> list[Section]:
    """The grouped view: one section per group in the toolbox's group order
    (that order is the user's, never sorted by a column), then "Ungrouped"
    last if any ROI is in no group. Rows are sorted inside each section."""
    by_group: dict[str | None, list[RoiRow]] = {group.group_id: [] for group in groups}
    by_group[None] = []
    for row in rows:
        by_group.setdefault(row.group_id, by_group[None]).append(row)
    sections = [
        Section(
            GroupInfo(group.group_id, group.name, group_colors[group.group_id]),
            tuple(sort_rows(by_group[group.group_id], sort_column, descending)),
        )
        for group in groups
    ]
    if by_group[None]:
        sections.append(Section(GroupInfo(None, "Ungrouped", DEFAULT_ROI_COLOR_HEX), tuple(sort_rows(by_group[None], sort_column, descending))))
    return sections


# -- reordering ----------------------------------------------------------------


def movement_scope(selected: Collection[int], rows: Sequence[RoiRow], *, grouped: bool) -> tuple[int, ...] | None:
    """The list the selected ROIs may be reordered within, as ids in
    ascending order, or ``None`` if they cannot be moved together.

    Flat view: all ROIs. Grouped view: the one group (or "Ungrouped") that all
    the selected ROIs belong to - reordering inside a group only shuffles the
    numbers that group already holds. Selected ROIs from different groups have
    no single list to move within."""
    selected_ids = set(selected)
    if not selected_ids:
        return None
    if not grouped:
        return tuple(sorted(row.roi_id for row in rows))
    groups_of_selected = {row.group_id for row in rows if row.roi_id in selected_ids}
    if len(groups_of_selected) != 1:
        return None
    (group_id,) = groups_of_selected
    return tuple(sorted(row.roi_id for row in rows if row.group_id == group_id))


def step_target_index(scope_ids: Sequence[int], moved: Collection[int], direction: int) -> int:
    """The ``target_index`` for `RoiToolbox.move_in_order` that moves the
    ``moved`` ROIs one place ``direction`` (-1 up, +1 down) within ``scope_ids``
    (ascending). The moved ROIs end up next to each other."""
    moving = set(moved)
    before = 0
    for roi_id in scope_ids:
        if roi_id in moving:
            break
        before += 1
    return before + direction
