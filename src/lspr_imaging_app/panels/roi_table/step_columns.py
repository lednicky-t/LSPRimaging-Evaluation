"""What the ROI / Groups table's step columns write in a cell and sort by. Pure: no Qt.

A step column holds, for every ROI and group, the mean and the SD of its line in one experiment step (``analysis/step_table.py``). The user picks which numbers the cells
show: the mean (`DISPLAY_MEAN`), the SD (`DISPLAY_STD`), the SEM (`DISPLAY_SEM`, SD / sqrt(n): how well the mean is known), or the mean with one of the two
(`DISPLAY_BOTH` = mean ± SD, `DISPLAY_MEAN_SEM` = mean ± SEM). The number format is the Sensorgram step table's (``plan/stats_table.py``): one number of decimals for the whole
table, two significant digits of the typical spread that is shown, so every cell reads alike. **Sorting**: a column sorts by the mean where the mean is shown (`DISPLAY_MEAN`,
`DISPLAY_BOTH`, `DISPLAY_MEAN_SEM`) and by the spread where only that is (`DISPLAY_STD`, `DISPLAY_SEM`); a row with no number for the step always comes last, in either direction.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from ...selection.step_table_module import CellValue, StepColumn
from ...plan.stats_table import PLUS_MINUS, choose_decimals

DISPLAY_MEAN, DISPLAY_STD, DISPLAY_BOTH, DISPLAY_SEM, DISPLAY_MEAN_SEM = "mean", "std", "both", "sem", "mean_sem"
DISPLAYS = (DISPLAY_MEAN, DISPLAY_STD, DISPLAY_BOTH, DISPLAY_SEM, DISPLAY_MEAN_SEM)  # ("both" keeps its old name: it is what earlier sessions saved)
DISPLAY_LABELS = {
    DISPLAY_MEAN: "Mean",
    DISPLAY_STD: "Std",
    DISPLAY_BOTH: f"Mean {PLUS_MINUS} Std",
    DISPLAY_SEM: "SEM",
    DISPLAY_MEAN_SEM: f"Mean {PLUS_MINUS} SEM",
}
DISPLAY_TIPS = {
    DISPLAY_MEAN: "Show the mean of each ROI or group in each step. A step column sorts by it.",
    DISPLAY_STD: "Show the standard deviation of each ROI or group in each step. A step column sorts by it.",
    DISPLAY_BOTH: f"Show the mean {PLUS_MINUS} the standard deviation, as the Sensorgram's step table does. A step column sorts by the mean.",
    DISPLAY_SEM: (
        "Show the standard error of the mean (SD / sqrt(number of cubes in the step)): how well the mean is known. It shrinks with a longer step, the SD does not. "
        "A step column sorts by it."
    ),
    DISPLAY_MEAN_SEM: f"Show the mean {PLUS_MINUS} its standard error (SD / sqrt(number of cubes)). A step column sorts by the mean.",
}
UNIT = "nm"
DEFAULT_DECIMALS = 2

STEP_WIDTH_DEFAULT = 118  # px: a step column that has no numbers yet to fit itself to ("595.773 ± 0.087" with room)
MIN_STEP_WIDTH = 56  # px: no fitted step column is narrower, however short its content
MAX_STEP_WIDTH = 360  # px: nor wider; a longer title is cut with "..." (its tool tip has the whole note)
_MEASURED_TEXTS = 64

_SEM_MODES = (DISPLAY_SEM, DISPLAY_MEAN_SEM)
_SPREAD_ONLY_MODES = (DISPLAY_STD, DISPLAY_SEM)  # the mean is not in the cell


def shows_mean(mode: str) -> bool:
    return mode not in _SPREAD_ONLY_MODES


def shows_spread(mode: str) -> bool:
    return mode != DISPLAY_MEAN


def spread_word(mode: str) -> str:
    """Which spread a mode shows: ``"sd"`` or ``"sem"`` (``"sd"`` also for the mean-only mode, which has no spread but is rounded by the SD)."""
    return "sem" if mode in _SEM_MODES else "sd"


def spread_of(value: CellValue, mode: str) -> float:
    return value.sem if mode in _SEM_MODES else value.sd


OPERATION_LABELS = {"difference": "{S} - {B}: shift (difference of the means)", "snr": "({S} - {B}) / noise of {B}: signal to noise"}
"""The operation menu of a comparison column; `{S}` and `{B}` stand for the pair's names (S1, B1 ...)."""
OPERATION_TIPS = {
    "difference": (
        "Mean of the signal step minus mean of the baseline step, in nm. The SD is the two steps' SDs added in quadrature; "
        "the SEM is the two steps' SEMs added in quadrature."
    ),
    "snr": "That difference divided by the noise of the baseline step (its SD about a straight line, so a steady drift is not noise). A pure number; it has no SD or SEM.",
}


def table_decimals(cells: Iterable[CellValue | None], mode: str = DISPLAY_BOTH) -> int:
    """One number of decimals for every cell of the table: two significant digits of the median spread of the numbers there are (`plan.stats_table.choose_decimals`) - the SEM
    where an SEM is shown (it is much smaller than the SD, and the mean is quoted to the precision of its uncertainty), the SD otherwise."""
    present = [c for c in cells if c is not None]
    return choose_decimals(spread_of(c, mode) for c in present) if present else DEFAULT_DECIMALS


def widest_text(texts: Iterable[str], advance: Callable[[str], int]) -> int:
    """Width in px of the widest of `texts` (0 for none), `advance` being text -> px. Only the longest few distinct texts are measured: the cells of a column all have one
    number format, so the widest is among the longest, and a text with fewer characters is narrower than every one of those (to within a glyph's width, which the room the
    caller adds around the text covers). With hundreds of ROIs that is about 60 measurements a column instead of hundreds."""
    longest = sorted(set(texts), key=len, reverse=True)[:_MEASURED_TEXTS]
    return max((advance(text) for text in longest), default=0)


def format_cell(value: CellValue | None, mode: str, decimals: int) -> str:
    """The text of a cell; empty for a row with no number in the step. ``mean ± spread`` falls back to the mean alone where there is no spread (a single cube, a single ROI)."""
    if value is None:
        return ""
    spread = spread_of(value, mode)
    if not shows_mean(mode):
        return f"{spread:.{decimals}f}" if math.isfinite(spread) else ""
    if not math.isfinite(value.mean):
        return ""
    if shows_spread(mode) and math.isfinite(spread):
        return f"{value.mean:.{decimals}f} {PLUS_MINUS} {spread:.{decimals}f}"
    return f"{value.mean:.{decimals}f}"


def sort_value(value: CellValue | None, mode: str) -> float | None:
    """What a step column sorts a row by: the spread where only the spread is shown (`DISPLAY_STD`, `DISPLAY_SEM`), the mean otherwise; None for a row without a number."""
    if value is None:
        return None
    number = value.mean if shows_mean(mode) else spread_of(value, mode)
    return number if math.isfinite(number) else None


def cell_tooltip(row_text: str, column: StepColumn, value: CellValue | None, *, is_group: bool) -> str:
    """The full numbers of one cell and what they mean."""
    head = f"{row_text} - {column.title}"
    compare = column.kind == "compare"
    if value is None:
        if compare:
            return f"{head}\nNo value: the row is not calculated yet (refresh button), or the baseline or signal step of this pair is not set."
        return f"{head}\nNot calculated yet. Use the recalculate button."
    if not math.isfinite(value.mean):
        return f"{head}\n" + ("No value: a step has too few cubes" + (", or the baseline has no noise value." if column.op == "snr" else ".") if compare else "No value in this step.")
    if compare:
        unit = f" {column.unit}" if column.unit else ""
        digits = (choose_decimals([value.sd]) if math.isfinite(value.sd) else DEFAULT_DECIMALS) + 1 if column.decimals is None else column.decimals + 1
        lines = [head, f"{column.value_word} {value.mean:.{digits}f}{unit}"]
        if column.has_sd and math.isfinite(value.sd):
            lines.append(f"SD {value.sd:.{digits}f}{unit} (the two steps' SDs added in quadrature)")
        if column.has_sd and math.isfinite(value.sem):
            lines.append(f"SEM {value.sem:.{digits + 1}f}{unit} (the two steps' SEMs added in quadrature)")
        lines.append(f"{value.n} cubes in the smaller of the two steps")
        if not value.current:
            lines.append("Out of date: something changed since the two steps were calculated. Use the refresh button.")
        return "\n".join(lines)
    digits = (choose_decimals([value.sd]) if math.isfinite(value.sd) else DEFAULT_DECIMALS) + 1
    lines = [head, f"mean {value.mean:.{digits}f} {UNIT}"]
    if math.isfinite(value.sd):
        what = "the group's averaged line" if is_group else "the line"
        lines.append(f"SD {value.sd:.{digits}f} {UNIT} ({what} over {value.n} cubes in the step)")
        if math.isfinite(value.sem):
            lines.append(f"SEM {value.sem:.{digits + 1}f} {UNIT} (SD / sqrt({value.n}): how well the mean is known)")
    else:
        lines.append(f"{value.n} cube: no SD")
    if not value.current:
        lines.append("Out of date: something changed since this was calculated. Use the recalculate button.")
    return "\n".join(lines)
