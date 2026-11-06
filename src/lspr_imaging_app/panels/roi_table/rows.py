"""What the ROI/Group table shows, as plain data. Pure: no Qt.

Everything the table needs to *decide* lives here so it can be tested without
a window: how a length is shown and read back in px or µm, which diameters a
ROI inherits, how ROIs are sorted and sectioned by group, and which ROIs a
"move up/down" may reorder. The model and the painting only present it.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from ...domain.roi_model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup
from ...roi.palette import DEFAULT_ROI_COLOR_HEX

COLUMN_ID, COLUMN_NAME, COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT = range(7)
COLUMN_SHAPE = 7
COLUMN_COUNT = 8
"""The fixed columns. The step columns (one per experiment step, ``step_columns.py``) follow them in the model: column ``COLUMN_COUNT + i`` is the i-th step."""
COLUMN_TITLES = ("#/ROIs", "Label", "x", "y", "D_s", "d_r", "D_r", "Shape")
"""Header titles; ``X_y`` is drawn as X with a subscript y (`view.SubscriptHeader`)."""
EDITABLE_COLUMNS = (COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT)
DIAMETER_COLUMNS = (COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT)
VIEW_SIMPLE = "simple"
VIEW_GEOMETRY = "geometry"
VIEW_MODES = (VIEW_SIMPLE, VIEW_GEOMETRY)
VIEW_MODE_COLUMNS = {
    VIEW_SIMPLE: (COLUMN_ID, COLUMN_NAME),
    VIEW_GEOMETRY: (COLUMN_ID, COLUMN_X, COLUMN_Y, COLUMN_SHAPE, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT),
}
"""Which columns each view mode shows, left to right on screen is the model order; the rest are hidden."""

FIELD_NAMES = {COLUMN_X: "x", COLUMN_Y: "y", COLUMN_SAMPLE: "D_s", COLUMN_RING_IN: "d_r", COLUMN_RING_OUT: "D_r"}
"""D = outer / d = inner diameter; s = sample, r = reference."""

# -- shapes ---------------------------------------------------------------------

SHAPE_RING, SHAPE_DISK, SHAPE_MASK, SHAPE_OTHER = "ring", "disk", "mask", "other"
SHAPE_LABELS = {
    SHAPE_RING: "Circle with reference ring",
    SHAPE_DISK: "Circle, no reference",
    SHAPE_MASK: "Pixel mask",
    SHAPE_OTHER: "Other shape",
}
SHAPE_FIELDS = {
    SHAPE_RING: (COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT),
    SHAPE_DISK: (COLUMN_SAMPLE,),
    SHAPE_MASK: (),
    SHAPE_OTHER: (),
}
"""The diameter columns each shape has, in order (a ring: D_s, d_r, D_r); the others stay blank.
Offsets of the ring centre from the sample centre (``Δx, Δy``) are not stored yet (always 0);
when they are, they get columns of their own, shown only when some ROI has a non-zero one.

TODO(shapes): rectangle with frame, polygon and freehand are not implemented in the
app. `shape_of` calls any other sample/reference pairing "other"; give each new shape its
own key, label, columns and pictogram (`delegate._paint_shape`) here when it arrives."""


def shape_of(sample_geometry_type: str, reference_geometry_type: str) -> str:
    """The shape a ROI's two stored geometry types make together."""
    if sample_geometry_type == "mask":
        return SHAPE_MASK
    if sample_geometry_type == "circle" and reference_geometry_type == "annulus":
        return SHAPE_RING
    if sample_geometry_type == "circle" and reference_geometry_type == "none":
        return SHAPE_DISK
    return SHAPE_OTHER


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
    shape: str = SHAPE_RING


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
                shape=shape_of(roi.sample_geometry_type, roi.reference_geometry_type),
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
    COLUMN_SHAPE: lambda row: (row.shape, row.roi_id),
}


@dataclass(frozen=True)
class StepSort:
    """The values a step column sorts by (``roi``: per ROI id, ``group``: per group id, ``None`` for "Ungrouped"). A missing or NaN value has no place in the order."""

    roi: Mapping[int, float]
    group: Mapping[str | None, float]


def _has_value(value: float | None) -> bool:
    return value is not None and not math.isnan(value)


def _by_value(items: Sequence, value_of, tie_key, descending: bool) -> list:
    """`items` ordered by `value_of(item)`, ties by `tie_key(item)` (always ascending); items without a value come last, in `tie_key` order, in either direction."""
    with_value = [item for item in items if _has_value(value_of(item))]
    without = [item for item in items if not _has_value(value_of(item))]
    ordered = sorted(sorted(with_value, key=tie_key), key=value_of, reverse=descending)  # two stable passes: the tie order does not flip with `descending`
    return ordered + sorted(without, key=tie_key)


def sort_rows(rows: Sequence[RoiRow], column: int, descending: bool, step_sort: StepSort | None = None) -> list[RoiRow]:
    """`rows` sorted by a fixed column, or (`step_sort` given) by their value in a step column."""
    if step_sort is not None:
        return _by_value(rows, lambda row: step_sort.roi.get(row.roi_id), lambda row: row.roi_id, descending)
    return sorted(rows, key=_SORT_KEYS.get(column, _SORT_KEYS[COLUMN_ID]), reverse=descending)


def build_sections(
    rows: Sequence[RoiRow],
    groups: Sequence[AreaRoiGroup],
    group_colors: dict[str, str],
    *,
    sort_column: int,
    descending: bool,
    ungrouped_color_hex: str = DEFAULT_ROI_COLOR_HEX,
    step_sort: StepSort | None = None,
) -> list[Section]:
    """The grouped view: one section per group in the toolbox's group order
    (that order is the user's, never sorted by a column - except the Label
    column, which sorts the groups by name, and a step column, which sorts the
    groups by their value in that step; both display only), then "Ungrouped"
    last if any ROI is in no group. Rows are sorted inside each section (by their
    own value, when a step column sorts)."""
    by_group: dict[str | None, list[RoiRow]] = {group.group_id: [] for group in groups}
    by_group[None] = []
    for row in rows:
        by_group.setdefault(row.group_id, by_group[None]).append(row)
    by_label = step_sort is None and sort_column == COLUMN_NAME  # ROIs have no label: this orders the groups (by name), the ROIs stay by #
    row_column, row_descending = (COLUMN_ID, False) if by_label else (sort_column, descending)
    if step_sort is not None:
        ordered_groups = _by_value(list(groups), lambda group: step_sort.group.get(group.group_id), lambda group: 0, descending)  # equal values keep the user's order
    elif by_label:
        ordered_groups = sorted(groups, key=lambda group: group.name.casefold(), reverse=descending)
    else:
        ordered_groups = groups
    sections = [
        Section(
            GroupInfo(group.group_id, group.name, group_colors[group.group_id]),
            tuple(sort_rows(by_group[group.group_id], row_column, row_descending, step_sort)),
        )
        for group in ordered_groups
    ]
    if by_group[None]:
        sections.append(Section(GroupInfo(None, "Ungrouped", ungrouped_color_hex), tuple(sort_rows(by_group[None], row_column, row_descending, step_sort))))
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
