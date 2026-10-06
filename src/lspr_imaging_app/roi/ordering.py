"""ROI order helpers. Pure: no Qt, no ROI objects, only ids.

A ROI's id is its place in the list (1..N). Reordering means choosing which
ROI sits at which place, so every reorder is a *permutation of the ids*.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence


def move_block(order: Sequence[int], moved: Collection[int], target_index: int) -> list[int]:
    """Move the ids in ``moved`` (which keep their order relative to each
    other) so that the block starts at ``target_index``.

    ``target_index`` counts in the list *with the moved ids taken out*, so 0
    puts the block first and ``len(order) - len(moved)`` puts it last; a value
    outside that range is clamped. Ids in ``moved`` that are not in ``order``
    are ignored.

    Moving a single id up one place is ``target_index = its index - 1``; down
    one place is ``its index + 1``."""
    moving = set(moved)
    block = [item for item in order if item in moving]
    rest = [item for item in order if item not in moving]
    index = max(0, min(int(target_index), len(rest)))
    return rest[:index] + block + rest[index:]


def renumbering(new_order: Sequence[int], slots: Sequence[int]) -> dict[int, int]:
    """``{old_id: new_id}`` for putting ROIs in ``new_order`` into the id
    ``slots`` (ascending), position by position. The ROI at position j of
    ``new_order`` takes ``slots[j]``."""
    if len(new_order) != len(slots):
        raise ValueError("new_order and slots must be the same length")
    return {old_id: new_id for old_id, new_id in zip(new_order, slots)}
