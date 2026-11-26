"""Which columns of the ROI / Groups table are selected, as a spreadsheet does it (2026-11-26). Pure: no Qt (`view.py` owns the mouse, `delegate.py` the painting).

A selected column is named by its **key** (`column_keys.py`), so the selection survives the step columns being renumbered, rebuilt or sorted. The step columns among them are
the same selection as the steps selected on the Sensorgram's plan bar (the app wires the two together, `app_rewrite._follow_plan_bar`).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from ...selection.step_table_module import COMPARE
from .column_keys import ColumnKey


def is_step_key(key: ColumnKey) -> bool:
    """True for the key of a step column (a plan step's reference); False for a fixed column (a number) and a comparison column (``("cmp", pair, operation)``)."""
    return not isinstance(key, int) and key[0] != COMPARE


def click_columns(
    current: Collection[ColumnKey], anchor: ColumnKey | None, clicked: ColumnKey, shown: Sequence[ColumnKey], *, ctrl: bool = False, shift: bool = False
) -> tuple[frozenset[ColumnKey], ColumnKey | None]:
    """The selected columns after a left click on the header of column `clicked`, and the new anchor (the column a Shift-click measures its range from). `shown`: the keys of the
    columns on screen, left to right (a hidden column is no part of a range).

    A plain click selects that column only. Ctrl adds it, or takes it out when it is selected already. Shift selects every column between the anchor and it and adds them to what
    was selected (the anchor stays where it was); with no anchor to measure from it is a plain click. Ctrl with Shift is Shift. The same rules as a click on the plan bar's steps
    (`plan.bar_model.click_selection`), so the two selections feel alike."""
    if clicked not in shown:
        return frozenset(k for k in current if k in shown), anchor
    chosen = {k for k in current if k in shown}
    if shift and anchor is not None and anchor in shown:
        low, high = sorted((shown.index(anchor), shown.index(clicked)))
        return frozenset(chosen | set(shown[low : high + 1])), anchor
    if ctrl:
        chosen.symmetric_difference_update({clicked})
        return frozenset(chosen), clicked
    return frozenset({clicked}), clicked
