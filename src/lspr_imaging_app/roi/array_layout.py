"""Manual array layout: where the copies of the selected ROI(s) go (pure: numpy only, no Qt, no image).

The "Array" pop-out of the ROIs tab stamps a **unit** (the selected ROI, or several as one pattern) on the
nodes of a lattice. This module is the only place that knows where the nodes are, so the live preview and
Apply cannot disagree. Positions are processed-image pixel-index coordinates (x right, y down), the same as
`AreaRoi.center_x / center_y`; the 0.5 px canvas shift is the overlay's business (`lspri-roi-model.md`).

Definitions (maintainer, 2026-11-25)
- The selection is the **seed**: it stays where it is and is the node ``(seed_row, seed_col)`` of the array.
  *Start corner* says where in the array the seed sits: ``(0, 0)`` top-left (the array grows right and
  down), ``(1, 1)`` bottom-right (grows left and up), ``(0.5, 0.5)`` the middle, and so on. With an even
  count the middle is the lower-index node of the two.
- *Pitch* is the centre-to-centre distance of neighbours. *Pitch increment* is a **factor** (x and y
  separately, 1 = constant pitch) applied at every further step **away from the seed**: the gap between the
  k-th and (k+1)-th node counted from the seed is ``pitch * factor ** k``. (Changed 2026-11-25 from an
  additive length: a factor has a neutral default, 1, and needs no unit.)
- *Row offset* shifts every second row (counted from the seed's row) along x by ``row_offset * pitch_x``,
  away from the seed's side. Triangular defaults to 0.5. Positive growth direction: the shift points the
  way the array grows in x (so a seed at the right edge shifts to the left).
- *Angle* turns the whole lattice about the seed, degrees, clockwise on screen. The unit's own inner
  arrangement is **not** turned (the user placed it).
- *Regular cell* is one switch for the whole cell shape, driven by x: rectangular gives a square cell
  (pitch y = pitch x, factor y = factor x), triangular gives all-60-degree triangles (pitch y =
  pitch x * sqrt(3) / 2, row offset 0.5, factor y = factor x).
- *Numbering* (ROI ids of the array): by **rows** or by **columns**, starting at one of the four corners (left / right, top / bottom). By rows: the
  corner's row first, across from the corner's side, then the next row; by columns the same the other way round. It follows the lattice's own rows and
  columns, so it turns with the angle. The array's ROIs (the selection too) take the ids they hold together, in that order.
- A unit with several ROIs is placed by its **centroid**: the centroid goes on the node and every member
  keeps its offset from it.

New lattice types: add a `LatticeKind` to `LATTICE_KINDS` when it is a regular lattice with a per-row
shift. A lattice with several points per cell (honeycomb, kagome) needs a basis and a design discussion.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields, replace

import numpy as np

NUMBER_BY = ("rows", "columns")

__all__ = [
    "NUMBER_BY",
    "numbering_order",
    "ArrayLayout",
    "ArraySpec",
    "LATTICE_KINDS",
    "LatticeKind",
    "START_FRACTIONS",
    "array_layout",
    "lattice_kind",
]

START_FRACTIONS: tuple[float, ...] = (0.0, 0.5, 1.0)
"""The three positions along one axis the start-corner picker offers: start, middle, end."""


@dataclass(frozen=True, slots=True)
class LatticeKind:
    """One lattice type: its default row shift and the cell shape the *regular cell* switch enforces."""

    key: str
    label: str
    default_row_offset: float
    """Shift of every second row, as a fraction of pitch x."""
    regular_ratio_y: float
    """Pitch y / pitch x of the regular cell (square: 1, equilateral triangles: sqrt(3) / 2)."""


LATTICE_KINDS: tuple[LatticeKind, ...] = (
    LatticeKind("rectangular", "Rectangular", 0.0, 1.0),
    LatticeKind("triangular", "Triangular", 0.5, math.sqrt(3.0) / 2.0),
)


def lattice_kind(key: str) -> LatticeKind:
    for kind in LATTICE_KINDS:
        if kind.key == key:
            return kind
    raise ValueError(f"unknown lattice type {key!r}; known: {[k.key for k in LATTICE_KINDS]}")


@dataclass(frozen=True, slots=True)
class ArraySpec:
    """Everything the pop-out sets. Lengths are pixels (the pop-out converts from µm)."""

    kind: str = "rectangular"
    rows: int = 2
    cols: int = 2
    pitch_x_px: float = 50.0
    pitch_y_px: float = 50.0
    start_x: float = 0.0
    """Where along x the seed sits in the array: 0 left, 0.5 middle, 1 right."""
    start_y: float = 0.0
    """Where along y the seed sits in the array: 0 top, 0.5 middle, 1 bottom."""
    angle_deg: float = 0.0
    pitch_factor_x: float = 1.0
    """Pitch increment along x: every further step away from the seed has this times the previous gap (1 = constant)."""
    pitch_factor_y: float = 1.0
    row_offset: float | None = None
    """Shift of every second row as a fraction of pitch x; ``None`` = the lattice type's default."""
    regular_cell: bool = False
    number_by: str = "rows"
    """How the array's ROIs are numbered: ``"rows"`` (one row after the other) or ``"columns"``."""
    number_start_x: float = 0.0
    """Numbering starts at the left (0) or the right (1) side."""
    number_start_y: float = 0.0
    """Numbering starts at the top (0) or the bottom (1)."""

    def to_dict(self) -> dict[str, object]:
        """Plain values for the saved recipe (`RoiArrayGroup.spec`); `from_dict` is the way back."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> ArraySpec:
        """Lenient: unknown keys are ignored and missing ones take the default, so a recipe saved by an older or newer build still opens."""
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in known})  # type: ignore[arg-type]

    def resolved(self) -> ArraySpec:
        """The spec with the *regular cell* switch applied (pitch y, factor y, row offset follow x) and the
        row offset filled in. This is what `array_layout` uses; the pop-out shows it in the locked fields."""
        kind = lattice_kind(self.kind)
        offset = kind.default_row_offset if self.row_offset is None else self.row_offset
        if not self.regular_cell:
            return replace(self, row_offset=offset)
        return replace(
            self,
            pitch_y_px=self.pitch_x_px * kind.regular_ratio_y,
            pitch_factor_y=self.pitch_factor_x,
            row_offset=kind.default_row_offset,
        )

    def validate(self) -> None:
        """Raise ``ValueError`` with a sentence for the status line when the array cannot be made."""
        lattice_kind(self.kind)
        numbers = (
            self.pitch_x_px, self.pitch_y_px, self.start_x, self.start_y, self.angle_deg,
            self.pitch_factor_x, self.pitch_factor_y, 0.0 if self.row_offset is None else self.row_offset,
        )  # fmt: skip
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError("array settings must be finite numbers")
        if int(self.rows) < 1 or int(self.cols) < 1:
            raise ValueError("an array needs at least 1 row and 1 column")
        if self.start_x not in START_FRACTIONS or self.start_y not in START_FRACTIONS:
            raise ValueError("the start corner is one of the nine positions of the picker")
        if self.number_by not in NUMBER_BY or self.number_start_x not in (0.0, 1.0) or self.number_start_y not in (0.0, 1.0):
            raise ValueError("numbering is by rows or by columns, from a corner (left / right, top / bottom)")
        spec = self.resolved()
        for axis, pitch, factor in (("x", spec.pitch_x_px, spec.pitch_factor_x), ("y", spec.pitch_y_px, spec.pitch_factor_y)):
            if pitch <= 0.0:
                raise ValueError(f"pitch {axis} must be greater than 0")
            if factor <= 0.0:
                raise ValueError(f"the pitch increment {axis} must be greater than 0 (1 keeps the pitch constant)")


@dataclass(frozen=True, slots=True)
class ArrayLayout:
    """Result of `array_layout`. Nodes and members are in row-major order (row 0 first, then column)."""

    rows: int
    cols: int
    seed_row: int
    seed_col: int
    node_xy: np.ndarray
    """``(rows * cols, 2)`` node positions (the unit's centroid goes here)."""
    member_xy: np.ndarray
    """``(rows * cols, members, 2)`` centre of every unit member at every node."""

    @property
    def seed_node(self) -> int:
        """Row-major index of the node the selection itself occupies."""
        return self.seed_row * self.cols + self.seed_col

    @property
    def count(self) -> int:
        return self.rows * self.cols

    def new_member_xy(self) -> np.ndarray:
        """``(new nodes * members, 2)``: every position the selection does **not** already occupy (the seed node
        is left out: the seed stays as the first element)."""
        keep = np.arange(self.count) != self.seed_node
        return self.member_xy[keep].reshape(-1, 2)


def numbering_order(spec: ArraySpec, rows: int, cols: int) -> list[int]:
    """Row-major node indices in the order the numbering visits them (see the module docstring)."""
    row_sequence = range(rows) if spec.number_start_y == 0.0 else range(rows - 1, -1, -1)
    col_sequence = range(cols) if spec.number_start_x == 0.0 else range(cols - 1, -1, -1)
    if spec.number_by == "rows":
        return [r * cols + c for r in row_sequence for c in col_sequence]
    return [r * cols + c for c in col_sequence for r in row_sequence]


def _seed_index(count: int, fraction: float) -> int:
    """Index of the seed node along an axis of `count` nodes (the lower one of two for an even middle)."""
    return int((count - 1) * fraction)


def _axis_positions(count: int, seed: int, pitch: float, factor: float) -> np.ndarray:
    """Signed distance of every node from the seed along one axis (increasing index = positive).

    The gap between the k-th and (k+1)-th node counted from the seed is ``pitch * factor ** k``, on both sides."""
    steps = max(seed, count - 1 - seed)
    reach = np.concatenate(([0.0], np.cumsum(pitch * factor ** np.arange(steps, dtype=np.float64))))  # distance after n steps
    away = np.abs(np.arange(count) - seed)
    distance = reach[away]
    return np.where(np.arange(count) >= seed, distance, -distance)


def array_layout(spec: ArraySpec, seed_xy: Sequence[Sequence[float]]) -> ArrayLayout:
    """Where every copy of the unit goes. ``seed_xy``: centres of the selected ROIs (one or more), pixel-index.

    Raises ``ValueError`` (nothing is computed) when the spec or the seed is invalid."""
    seed = np.asarray(seed_xy, dtype=np.float64).reshape(-1, 2)
    if len(seed) == 0 or not np.isfinite(seed).all():
        raise ValueError("select at least one ROI with a position")
    spec.validate()
    spec = spec.resolved()
    rows, cols = int(spec.rows), int(spec.cols)
    seed_row, seed_col = _seed_index(rows, spec.start_y), _seed_index(cols, spec.start_x)

    dx = _axis_positions(cols, seed_col, spec.pitch_x_px, spec.pitch_factor_x)
    dy = _axis_positions(rows, seed_row, spec.pitch_y_px, spec.pitch_factor_y)
    local = np.empty((rows, cols, 2), dtype=np.float64)
    local[..., 0] = dx[None, :]
    local[..., 1] = dy[:, None]
    # Every second row from the seed's row is shifted the way the array grows in x.
    direction = -1.0 if spec.start_x == 1.0 else 1.0
    shifted = (np.abs(np.arange(rows) - seed_row) % 2) == 1
    local[shifted, :, 0] += direction * float(spec.row_offset) * spec.pitch_x_px

    theta = math.radians(spec.angle_deg)  # clockwise on screen (y down): the usual rotation matrix
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    flat = local.reshape(-1, 2)
    turned = np.empty_like(flat)
    turned[:, 0] = flat[:, 0] * cos_t - flat[:, 1] * sin_t
    turned[:, 1] = flat[:, 0] * sin_t + flat[:, 1] * cos_t

    centroid = seed.mean(axis=0)
    node_xy = turned + centroid
    member_xy = node_xy[:, None, :] + (seed - centroid)[None, :, :]
    seed_node = seed_row * cols + seed_col
    node_xy[seed_node] = centroid
    member_xy[seed_node] = seed  # the seed stays exactly where it is (no rounding noise)
    return ArrayLayout(rows=rows, cols=cols, seed_row=seed_row, seed_col=seed_col, node_xy=node_xy, member_xy=member_xy)
