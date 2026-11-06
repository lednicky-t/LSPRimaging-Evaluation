"""Which column is which, in a form that survives the columns being renumbered and a restart (2026-11-06). Pure: no Qt.

The user can hide a column of the ROI / Groups table (`hide_columns.py`). The table's column numbers cannot name it: the step columns come after the eight fixed ones and are
renumbered whenever a step is added, removed or merged, and a comparison column has no number of its own. So a column is named by a **key**:

- a fixed column (#/ROIs ... Shape): its number, 1 - 7 (the first, #/ROIs, is never hidden: it carries the rows' dots, the groups' chevrons and names);
- a step column: the step's reference (``plan.state.StepRef``: the measured step with its start time and note, or an added step's uid), which stays the same when other steps come
  and go and means nothing in another experiment (a key that matches no column hides nothing);
- a comparison column (S1 - B1 ...): ``("cmp", pair, operation)``. Its running number changes at every start, the pair and the operation do not; choosing another operation in
  the column's header makes it another key, so it shows again with its new calculation.

``key_to_payload`` / ``key_from_payload`` turn a key into what the saved state (JSON) holds and back; anything unreadable gives ``None`` and is skipped.
"""

from __future__ import annotations

from ...analysis.step_table_module import COMPARE, StepColumn
from ...plan.state import StepRef, ref_from_payload, ref_to_payload
from .rows import COLUMN_COUNT, COLUMN_ID

ColumnKey = int | tuple  # a fixed column's number, or a step's reference, or ("cmp", pair, operation)


def column_key(column: StepColumn) -> ColumnKey:
    """The key of a step or comparison column."""
    return (COMPARE, column.pair, column.op) if column.kind == "compare" else column.ref


def is_hideable(key: object) -> bool:
    """Every column but the first can be hidden."""
    return key != COLUMN_ID


def key_to_payload(key: ColumnKey) -> object:
    if isinstance(key, int):
        return int(key)
    if key[0] == COMPARE:
        return {"compare": [int(key[1]), str(key[2])]}
    return ref_to_payload(key)  # type: ignore[arg-type]


def key_from_payload(raw: object) -> ColumnKey | None:
    """The key a saved entry stands for, or None for one that cannot be read or that names a column that cannot be hidden."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if COLUMN_ID < raw < COLUMN_COUNT else None
    if not isinstance(raw, dict):
        return None
    try:
        if "compare" in raw:
            pair, op = raw["compare"]
            if isinstance(pair, bool) or not isinstance(pair, int) or not isinstance(op, str):
                return None
            return (COMPARE, pair, op)
        ref: StepRef = ref_from_payload(raw)
        return ref
    except (KeyError, TypeError, ValueError):
        return None
