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
from ...domain.roi_shapes import Border, Circle, MaskShape, Rectangle, Shape, effective_border, ring_diameters
from ...roi.palette import DEFAULT_ROI_COLOR_HEX

COLUMN_ID, COLUMN_NAME, COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT = range(7)
COLUMN_SHAPE = 7
COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE = 8, 9, 10
"""A rectangle's width, height and angle (2026-11-19). Appended after the first eight, so the number every saved column key and frozen column holds still names the same column."""
COLUMN_COUNT = 11
"""The fixed columns. The step columns (one per experiment step, ``step_columns.py``) follow them in the model: column ``COLUMN_COUNT + i`` is the i-th step."""
COLUMN_TITLES = ("#/ROIs", "Label", "x", "y", "D_s", "d_r", "D_r", "Shape", "W", "H", "θ")
"""Header titles; ``X_y`` is drawn as X with a subscript y (`view.SubscriptHeader`)."""
EDITABLE_COLUMNS = (COLUMN_X, COLUMN_Y, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT, COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)
DIAMETER_COLUMNS = (COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT)
RECTANGLE_COLUMNS = (COLUMN_WIDTH, COLUMN_HEIGHT, COLUMN_ANGLE)
"""The columns only a rectangle has. The view shows them only while the table holds a rectangle (`RoiTreeView.refresh_column_visibility`)."""
LENGTH_COLUMNS = (COLUMN_X, COLUMN_Y, *DIAMETER_COLUMNS, COLUMN_WIDTH, COLUMN_HEIGHT)
"""Columns in the display unit (px or µm). The angle is in degrees whatever the unit is."""
SIZE_COLUMNS = (*DIAMETER_COLUMNS, *RECTANGLE_COLUMNS)
"""The columns whose cell exists only for some shapes (`SHAPE_FIELDS`)."""
NUMERIC_COLUMNS = (*LENGTH_COLUMNS, COLUMN_ANGLE)
"""Every column that holds a number a ROI row can change: what a paste, a fill down, Up / Down and the mouse wheel work on."""
VIEW_SIMPLE = "simple"
VIEW_GEOMETRY = "geometry"
VIEW_MODES = (VIEW_SIMPLE, VIEW_GEOMETRY)
VIEW_MODE_COLUMNS = {
    VIEW_SIMPLE: (COLUMN_ID, COLUMN_NAME),
    VIEW_GEOMETRY: (COLUMN_ID, COLUMN_X, COLUMN_Y, COLUMN_SHAPE, COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT, *RECTANGLE_COLUMNS),
}
"""Which columns each view mode shows, left to right on screen is the model order; the rest are hidden."""

FIELD_NAMES = {
    COLUMN_X: "x", COLUMN_Y: "y", COLUMN_SAMPLE: "D_s", COLUMN_RING_IN: "d_r", COLUMN_RING_OUT: "D_r",
    COLUMN_WIDTH: "W", COLUMN_HEIGHT: "H", COLUMN_ANGLE: "θ",
}
"""D = outer / d = inner diameter; s = sample, r = reference. W / H = a rectangle's width / height along its own axes, θ = its angle (degrees, clockwise on the screen)."""

# -- shapes ---------------------------------------------------------------------

SHAPE_RING, SHAPE_DISK, SHAPE_MASK, SHAPE_RECTANGLE, SHAPE_OTHER = "ring", "disk", "mask", "rectangle", "other"
SHAPE_LABELS = {
    SHAPE_RING: "Circle with reference ring",
    SHAPE_DISK: "Circle, no reference",
    SHAPE_MASK: "Pixel mask",
    SHAPE_RECTANGLE: "Rectangle",
    SHAPE_OTHER: "Other shape",
}
SHAPE_FIELDS = {
    SHAPE_RING: (COLUMN_SAMPLE, COLUMN_RING_IN, COLUMN_RING_OUT),
    SHAPE_DISK: (COLUMN_SAMPLE,),
    SHAPE_MASK: (),
    SHAPE_RECTANGLE: RECTANGLE_COLUMNS,  # a rectangle has a width, a height and an angle, not diameters (the Shape cell's tooltip says the size too)
    SHAPE_OTHER: (),
}
"""The size columns each shape has, in order (a ring: D_s, d_r, D_r; a rectangle: W, H, θ); the others stay blank.
Offsets of the ring centre from the sample centre (``Δx, Δy``) are not stored yet (always 0);
when they are, they get columns of their own, shown only when some ROI has a non-zero one.

TODO(shapes): rectangle with frame, polygon and freehand are not implemented in the
app. `shape_of` calls any other sample/reference pairing "other"; give each new shape its
own key, label, columns and pictogram (`delegate._paint_shape`) here when it arrives."""


def has_cell(row: RoiRow, column: int) -> bool:
    """Does this ROI have a cell in this number column? A position every ROI has; a size or angle only the shapes `SHAPE_FIELDS` names."""
    return column in (COLUMN_X, COLUMN_Y) or column in SHAPE_FIELDS.get(row.shape, ())


def cell_number(row: RoiRow, column: int) -> float | None:
    """The number in a row's cell, as the table stores it (pixels, degrees); ``None`` where the ROI has no such cell (a circle has no width) or the column holds no number."""
    if not has_cell(row, column):
        return None
    return {
        COLUMN_X: row.x, COLUMN_Y: row.y, COLUMN_SAMPLE: row.sample, COLUMN_RING_IN: row.inner, COLUMN_RING_OUT: row.outer,
        COLUMN_WIDTH: row.width, COLUMN_HEIGHT: row.height, COLUMN_ANGLE: row.angle,
    }.get(column)


def shape_of(shape: Shape, border: Border | None) -> str:
    """The table's shape key of a ROI's shape and border (``border`` ``None`` = the shared default border, which is one)."""
    if isinstance(shape, MaskShape):
        return SHAPE_MASK
    if isinstance(shape, Circle):
        return SHAPE_RING if border is None or border.enabled else SHAPE_DISK
    if isinstance(shape, Rectangle):
        return SHAPE_RECTANGLE
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
    return from_display(_number_of(text), unit)


def _number_of(text: str) -> float:
    """What the user typed as a plain number (a decimal comma is accepted). Raises ``ValueError`` with a message fit to show the user."""
    cleaned = text.strip().replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError(f"'{text.strip()}' is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"'{text.strip()}' is not a finite number")
    return value


def parse_angle(text: str) -> float:
    """What the user typed -> degrees, clockwise on the screen. Any finite number is an angle (a turn of 370 is a turn of 10): it is stored as typed."""
    return _number_of(text)


def format_angle(degrees: float) -> str:
    """What a cell shows: one decimal, as a length in px does."""
    return f"{degrees:.1f}"


def angle_edit_text(degrees: float) -> str:
    """What the cell editor starts with: the precision the cell shows, trailing zeros dropped."""
    text = f"{degrees:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


ANGLE_STEP_DEG = 1.0
ANGLE_STEP_DEG_LARGE = 5.0
"""How far one Up / Down (or Ctrl+wheel) step turns a rectangle; Shift takes the large one."""


def angle_step_text(text: str, direction: int, large: bool) -> str | None:
    """``text`` (degrees) moved one step up (+1) or down (-1), as editor text; ``None`` if ``text`` is not a number. An angle has no lower limit."""
    try:
        degrees = parse_angle(text)
    except ValueError:
        return None
    return angle_edit_text(degrees + direction * (ANGLE_STEP_DEG_LARGE if large else ANGLE_STEP_DEG))


STEP_PX = 0.5
STEP_PX_LARGE = 5.0
STEP_UM = 1.0
STEP_UM_LARGE = 10.0
"""How far one Up/Down (or Ctrl+wheel) step moves a diameter; Shift takes the large one.
In µm (no decimals shown) the step is a whole µm instead."""


def step_amount_px(large: bool, unit: LengthUnit) -> float:
    """How far one step of a length is, in pixels: 0.5 px (large 5 px), or in µm a whole µm (large 10 µm) converted, since µm shows no decimals."""
    if unit.um_per_px is None:
        return STEP_PX_LARGE if large else STEP_PX
    return (STEP_UM_LARGE if large else STEP_UM) / unit.um_per_px


def step_text(text: str, direction: int, large: bool, unit: LengthUnit) -> str | None:
    """``text`` (a length in ``unit``) moved one step up (+1) or down (-1), as
    editor text, never below zero; ``None`` if ``text`` is not a number."""
    try:
        px = parse_length(text, unit)
    except ValueError:
        return None
    return edit_text(max(0.0, px + direction * step_amount_px(large, unit)), unit)


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
    role: str = "sample"  # "reference" shows as "R7" in the first column
    link_note: str = ""  # what the ROI is linked to (or used by), for the first column's tooltip
    shape_note: str = ""  # the size of a rectangle or mask, for the Shape cell's tooltip
    width: float = 0.0  # a rectangle's width and height along its own axes (px) and its angle (degrees, clockwise); 0 for any other shape
    height: float = 0.0
    angle: float = 0.0


@dataclass(frozen=True)
class GroupInfo:
    """One group header. ``group_id is None`` is the synthetic "Ungrouped"
    header: no model object behind it, so it cannot be renamed, recoloured,
    deleted or moved."""

    group_id: str | None
    name: str
    color_hex: str
    synthetic: bool = False  # a header of the Linked view: read-only, no step numbers of its own (the "Ungrouped" numbers must not be shown on it)
    note: str = ""  # its tooltip, when it has one
    collapse_key: str | None = None  # what the remembered "collapsed" set holds for it (a Linked header has no group id of its own)


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
    default_border = defaults.default_border()
    users: dict[int, list[int]] = {}  # reference id -> the samples linked to it
    for roi in rois:
        for reference_id in roi.reference_roi_ids:
            users.setdefault(reference_id, []).append(roi.area_roi_id)
    reference_ids = {roi.area_roi_id for roi in rois if roi.role == "reference"}
    for roi in rois:
        # the diameter columns describe a circle (its ring as diameters); any other shape has none to show yet
        inner, outer = ring_diameters(roi.shape, effective_border(roi.border, default_border)) if isinstance(roi.shape, Circle) else (0.0, 0.0)
        rectangle = roi.shape if isinstance(roi.shape, Rectangle) else None
        rows.append(
            RoiRow(
                roi_id=roi.area_roi_id,
                label=roi.label or "",
                group_id=group_of.get(roi.area_roi_id),
                color_hex=roi.sample_color_hex or default_color_hex,
                has_own_color=roi.sample_color_hex is not None,
                x=roi.center_x,
                y=roi.center_y,
                sample=roi.shape.diameter_px if isinstance(roi.shape, Circle) else 0.0,
                inner=inner,
                outer=outer,
                inner_inherited=roi.border is None,
                outer_inherited=roi.border is None,
                is_mask=isinstance(roi.shape, MaskShape),
                nudge_count=len(roi.per_wavelength or {}),
                shape=shape_of(roi.shape, roi.border),
                role=roi.role,
                link_note=link_note(roi, effective_border(roi.border, default_border).enabled, users.get(roi.area_roi_id, ()), reference_ids),
                shape_note=shape_note(roi.shape),
                width=rectangle.width_px if rectangle else 0.0,
                height=rectangle.height_px if rectangle else 0.0,
                angle=rectangle.angle_deg if rectangle else 0.0,
            )
        )
    return rows


def link_note(roi: AreaRoi, has_border: bool, used_by: Sequence[int], reference_ids: Collection[int]) -> str:
    """One sentence for the first column's tooltip: a reference ROI says which samples use it, a sample says what it is measured against
    (its linked references, else its own border, else nothing)."""
    if roi.role == "reference":
        if not used_by:
            return "Reference ROI: not linked to any sample yet."
        return "Reference ROI, used by sample" + ("" if len(used_by) == 1 else "s") + " " + ", ".join(str(i) for i in sorted(used_by)) + "."
    linked = [i for i in roi.reference_roi_ids if i in reference_ids]
    if linked:
        return "Measured against reference" + ("" if len(linked) == 1 else "s (pooled by pixel)") + " " + ", ".join(f"R{i}" for i in linked) + "."
    return "Measured against its own border." if has_border else "No reference: its results are missing."


def shape_note(shape: Shape) -> str:
    """The size of a rectangle or a pixel mask, for the Shape cell's tooltip (a circle's diameters are columns of their own)."""
    if isinstance(shape, Rectangle):
        return f"{shape.width_px:g} x {shape.height_px:g} px, turned {shape.angle_deg:g} degrees clockwise."
    if isinstance(shape, MaskShape):
        return f"{int(shape.mask.mask.sum()):,} pixels."
    return ""


# -- the Linked view ----------------------------------------------------------

LINKED_NOT_LINKED_KEY = "linked:none"


def build_linked_sections(rows: Sequence[RoiRow], rois: Sequence[AreaRoi], not_linked_color_hex: str = DEFAULT_ROI_COLOR_HEX) -> list[Section]:
    """The Linked arrangement (plan phase 3): one section for **each sample that has linked references**, headed by what it is measured against, holding the
    sample's own row first and then the rows of its references (a reference shared by several samples appears in each of their sections); then one
    section, "Not linked", with the samples that have no linked reference and the references no sample uses. Read-only: a view of the links, not of
    the groups (which are not shown while it is on). Every ROI is in at least one section; a link to a ROI that is not a reference is not shown."""
    row_of = {row.roi_id: row for row in rows}
    references = {roi.area_roi_id for roi in rois if roi.role == "reference"}
    used: set[int] = set()
    sections: list[Section] = []
    unlinked: list[RoiRow] = []
    for roi in sorted(rois, key=lambda r: r.area_roi_id):
        if roi.role == "reference":
            continue
        linked = [i for i in roi.reference_roi_ids if i in references and i in row_of]
        sample_row = row_of.get(roi.area_roi_id)
        if sample_row is None:
            continue
        if not linked:
            unlinked.append(sample_row)
            continue
        used.update(linked)
        names = ", ".join(f"R{i}" for i in linked)
        header = GroupInfo(
            None, f"{roi.area_roi_id} measured against {names}", sample_row.color_hex,
            synthetic=True, note=f"Sample {roi.area_roi_id} and the reference ROIs it is linked to ({names}).", collapse_key=f"linked:{roi.area_roi_id}",
        )
        sections.append(Section(header, (sample_row, *(row_of[i] for i in linked))))
    unlinked += [row_of[i] for i in sorted(references) if i in row_of and i not in used]
    if unlinked:
        sections.append(
            Section(
                GroupInfo(
                    None, "Not linked", not_linked_color_hex, synthetic=True, collapse_key=LINKED_NOT_LINKED_KEY,
                    note="Samples measured against their own border (or against nothing) and reference ROIs no sample uses.",
                ),
                tuple(sorted(unlinked, key=lambda r: r.roi_id)),
            )
        )
    return sections


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
    COLUMN_WIDTH: lambda row: (row.width, row.roi_id),
    COLUMN_HEIGHT: lambda row: (row.height, row.roi_id),
    COLUMN_ANGLE: lambda row: (row.angle, row.roi_id),
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
