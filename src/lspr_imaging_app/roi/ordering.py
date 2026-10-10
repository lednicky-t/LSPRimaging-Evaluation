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


def grid_lines(
    ids: Sequence[int], xy: Sequence[tuple[float, float]], band_px: float, *, column_major: bool = False
) -> list[list[int]]:
    """The rows of an array (or, with ``column_major``, its columns), each as a list of ids in reading order:
    rows left to right and top row first, columns top to bottom and left column first.

    ``xy[i]`` is the position of ``ids[i]`` (pixels, y pointing down). Rows (columns) are found by grouping ROIs
    whose y (x) lies within ``band_px`` of the group's mean, so a slightly uneven array still sorts into its
    rows; ``band_px`` should be less than the pitch and more than the position scatter (about 0.75 of a disk
    diameter works for a regular array)."""
    if len(ids) != len(xy):
        raise ValueError("ids and xy must be the same length")
    major, minor = (0, 1) if column_major else (1, 0)  # row-major groups by y, orders inside by x
    entries = sorted(zip(ids, xy), key=lambda e: (e[1][major], e[1][minor], e[0]))
    groups: list[list[tuple[int, tuple[float, float]]]] = []
    centres: list[float] = []
    for entry in entries:
        value = float(entry[1][major])
        best, best_distance = -1, float("inf")
        for index, centre in enumerate(centres):
            distance = abs(value - centre)
            if distance < best_distance:
                best, best_distance = index, distance
        if best >= 0 and best_distance <= band_px:
            groups[best].append(entry)
            centres[best] = sum(float(e[1][major]) for e in groups[best]) / len(groups[best])
        else:
            groups.append([entry])
            centres.append(value)
    order = sorted(range(len(groups)), key=lambda i: centres[i])
    return [[roi_id for roi_id, _ in sorted(groups[i], key=lambda e: (e[1][minor], e[0]))] for i in order]


def order_by_grid(
    ids: Sequence[int], xy: Sequence[tuple[float, float]], band_px: float, *, column_major: bool = False
) -> list[int]:
    """``ids`` in reading order of an array (see `grid_lines`): row by row, or column by column. The first id is
    always the top-left ROI."""
    return [roi_id for line in grid_lines(ids, xy, band_px, column_major=column_major) for roi_id in line]


def grid_band_px(diameters_px: Sequence[float]) -> float:
    """The ``band_px`` for `grid_lines`: 0.75 of the median disk diameter, at least 5 px."""
    sizes = sorted(float(d) for d in diameters_px if d)
    if not sizes:
        return 5.0
    middle = len(sizes) // 2
    median = sizes[middle] if len(sizes) % 2 else 0.5 * (sizes[middle - 1] + sizes[middle])
    return max(0.75 * median, 5.0)
