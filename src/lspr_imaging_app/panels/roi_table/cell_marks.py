"""Which cells of the ROI / Groups table are marked, and how their border is drawn. Pure: no Qt (`view.py` owns the mouse, `delegate.py` the painting).

Two marks, both made of **ROI ids x column numbers** (so they survive the table being rebuilt, sorted or re-grouped):

* the **range**: the cells the user picked by clicking a selected row's number again and dragging (always inside the selected rows). It is what Copy copies and what Paste fills.
* the **copied** block: the cells that were just copied, drawn with a moving dashed border (the "marching ants") until Esc, or until something else is copied.

A block's border is drawn only where the block ends: `edges` says, for one cell, which of its four sides lie on the border (a cell in the middle of the block has none).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable

Edges = tuple[bool, bool, bool, bool]
"""(top, right, bottom, left): which sides of a cell lie on the border of its block."""

ANTS_PERIOD = 6
"""Steps of the dash pattern before it repeats (the dash pattern is 3 on, 3 off)."""


class CellMarks:
    def __init__(self) -> None:
        self.range_rows: tuple[int, ...] = ()
        self.range_columns: frozenset[int] = frozenset()
        self.copied_rows: frozenset[int] = frozenset()
        self.copied_columns: frozenset[int] = frozenset()
        self.copied_token = ""
        self.ants_phase = 0

    # -- the range -----------------------------------------------------------------------------

    def set_range(self, rows: Iterable[int], columns: Iterable[int]) -> None:
        self.range_rows = tuple(rows)
        self.range_columns = frozenset(columns)
        if not self.range_rows or not self.range_columns:
            self.clear_range()

    def clear_range(self) -> None:
        self.range_rows = ()
        self.range_columns = frozenset()

    def restrict_range(self, selected_ids: Collection[int], shown_columns: Collection[int] | None = None) -> bool:
        """Keep only the range's rows that are still selected and its columns that are still shown; an empty range is dropped. True if the range changed."""
        keep = set(selected_ids)
        rows = tuple(r for r in self.range_rows if r in keep)
        columns = self.range_columns if shown_columns is None else frozenset(c for c in self.range_columns if c in set(shown_columns))
        if rows == self.range_rows and columns == self.range_columns:
            return False
        self.set_range(rows, columns)
        return True

    # -- the copied block ----------------------------------------------------------------------

    def set_copied(self, rows: Iterable[int], columns: Iterable[int], token: str) -> None:
        self.copied_rows = frozenset(rows)
        self.copied_columns = frozenset(columns)
        self.copied_token = token
        self.ants_phase = 0
        if not self.copied_rows or not self.copied_columns:
            self.clear_copied()

    def clear_copied(self) -> None:
        self.copied_rows = frozenset()
        self.copied_columns = frozenset()
        self.copied_token = ""

    def step_ants(self) -> None:
        self.ants_phase = (self.ants_phase + 1) % ANTS_PERIOD

    # -- both ----------------------------------------------------------------------------------

    def has_range(self) -> bool:
        return bool(self.range_rows)

    def has_copied(self) -> bool:
        return bool(self.copied_rows)

    def has_any(self) -> bool:
        return self.has_range() or self.has_copied()

    def clear(self) -> None:
        self.clear_range()
        self.clear_copied()


def edges(
    roi_id: int, column: int, rows: Collection[int], columns: Collection[int],
    above: int | None, below: int | None, left: int | None, right: int | None,
) -> Edges | None:
    """The sides of the cell (`roi_id`, `column`) that lie on the border of the block ``rows x columns``, or ``None`` if the cell is not in the block. ``above`` / ``below``:
    the ROI shown in the row above / below (``None``: none, or a group header); ``left`` / ``right``: the column shown to the left / right (``None``: none)."""
    if roi_id not in rows or column not in columns:
        return None
    return (above not in rows, right not in columns, below not in rows, left not in columns)
