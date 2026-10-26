"""The pure model behind the Sensorgram's step statistics table (2026-10-26): what is written where, no Qt.

Screen purpose: next to the bar of experiment steps, write for every step the mean +/- SD of every plotted line in that step, in the colour of the line, so the steps
can be compared at a glance. A row of the table is a line of the plot (and the entry of the legend that names it), a column is a step; a cell sits in the middle of its
step. The numbers are computed by ``analysis/step_stats.py`` (`LineStepValues`, one per line); this file decides how they are written.

- **One number format for the whole table**: the number of decimals follows the typical SD (two significant digits of it), so every cell reads alike (`choose_decimals`).
- **A cell has to fit its step**: `cell_variants` lists what could be written, longest first (``595.773 ± 0.087``, one decimal less, the mean alone); `fit_variant`
  takes the first that fits the width the step has on screen, and none if even the shortest does not. A shortened variant never rounds a real SD to zero (``2.0 ± 0.0``):
  it is left out and the mean alone is written. The tooltip (`cell_tooltip`) always has the full numbers.
- **What the numbers are**: for every line, ROI or average alike, the mean and SD of the line **as drawn** over the cubes of the step (maintainer's choice, 2026-10-26), so
  they follow smoothing, baseline and spike removal like the curve. (The spread *between* the ROIs of a group, which smoothing cannot change, is available in
  `step_stats.group_statistics` and `LineStepValues.between_rois`; the table does not use it now.)
- **Narrow steps**: the text is made smaller, one size for the whole table down to the smallest the size box allows, before the SD is dropped (`StepStatsItem`).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from .bar_model import POSITION_TOP, PlanBarStep

PLUS_MINUS = "±"
MIN_CELL_WIDTH_PX = 24.0  # a step narrower than this on screen gets no text (its cells still answer the tooltip)
DECIMALS_RANGE = (1, 4)


@dataclass(frozen=True)
class StatsTableSettings:
    visible: bool = True
    position: str = POSITION_TOP  # which edge of the plot the table sits on
    text_pt: int = 9


@dataclass(frozen=True)
class StatsLine:
    """One row: a plotted line and its numbers in every step (tuples indexed by the step's position in time order, the `PlanBarStep.index`)."""

    label: str
    color: str
    mean: tuple[float, ...]
    sd: tuple[float, ...]
    n: tuple[int, ...]
    between_rois: bool


@dataclass(frozen=True)
class StatsColumn:
    index: int  # the step's position in time order
    x_start: float  # in the plot's x
    x_end: float
    title: str


@dataclass(frozen=True)
class StatsTable:
    columns: tuple[StatsColumn, ...]
    lines: tuple[StatsLine, ...]
    decimals: int
    unit: str

    def value(self, line: int, column: StatsColumn) -> tuple[float, float, int]:
        """(mean, sd, n) of a line in a step; NaN, NaN, 0 where the line has no number for it."""
        row = self.lines[line]
        i = column.index
        if i >= len(row.mean):
            return float("nan"), float("nan"), 0
        return float(row.mean[i]), float(row.sd[i]), int(row.n[i])


def choose_decimals(sds: Iterable[float]) -> int:
    """Decimals for the whole table: two significant digits of the median SD (0.087 -> 3, 0.5 -> 2, 5 -> 1), kept between 1 and 4. 2 when there is no SD at all."""
    finite = [float(s) for s in sds if math.isfinite(s) and s > 0.0]
    if not finite:
        return 2
    median = float(np.median(finite))
    return min(max(1 - math.floor(math.log10(median)), DECIMALS_RANGE[0]), DECIMALS_RANGE[1])


def cell_variants(mean: float, sd: float, decimals: int) -> tuple[str, ...]:
    """What a cell could say, longest first: ``mean ± SD``, the same with one decimal less, the mean alone. Empty without a mean. Without an SD (one value, or a
    single ROI in a group) only the mean is written."""
    if not math.isfinite(mean):
        return ()
    variants: list[str] = []
    if math.isfinite(sd):
        for d in (decimals, max(decimals - 1, DECIMALS_RANGE[0])):
            if sd != 0.0 and round(sd, d) == 0.0:
                continue  # a shortened SD must not read "± 0.0" when there is a spread: better the mean alone
            variants.append(f"{mean:.{d}f} {PLUS_MINUS} {sd:.{d}f}")
    variants.append(f"{mean:.{max(decimals - 1, DECIMALS_RANGE[0])}f}")
    return tuple(dict.fromkeys(variants))


def fit_variant(variants: Sequence[str], available_px: float, measure: Callable[[str], float]) -> str:
    """The first (longest) variant whose width is at most `available_px`, else an empty string."""
    return next((v for v in variants if measure(v) <= available_px), "")


def build_stats_table(bars: Sequence[PlanBarStep], lines: Sequence[StatsLine], unit: str = "nm") -> StatsTable | None:
    """The table for these steps (as the plan bar shows them, in the plot's x) and these lines. None when there is nothing to write: no step, no line, or no number."""
    if not bars or not lines:
        return None
    columns = tuple(StatsColumn(index=b.index, x_start=b.x_start, x_end=b.x_end, title=b.text or f"Step {b.index + 1}") for b in bars)
    sds = [sd for line in lines for sd in line.sd]
    means = [m for line in lines for m in line.mean]
    if not any(math.isfinite(m) for m in means):
        return None
    return StatsTable(columns=columns, lines=tuple(lines), decimals=choose_decimals(sds), unit=unit)


def cell_tooltip(table: StatsTable, line: int, column: StatsColumn) -> str:
    """The full numbers of one cell, and what the SD means for this line."""
    row = table.lines[line]
    mean, sd, n = table.value(line, column)
    d = (choose_decimals([sd]) if math.isfinite(sd) else table.decimals) + 1  # one digit more than the cell, by the cell's own SD (not the table's typical one)
    head = f"{row.label} - {column.title}"
    if not math.isfinite(mean):
        return f"{head}\nno value in this step"
    unit = f" {table.unit}" if table.unit else ""
    lines = [head, f"mean {mean:.{d}f}{unit}"]
    if row.between_rois:
        lines.append(f"SD between the {n} ROIs of this line {sd:.{d}f}{unit}" if math.isfinite(sd) else f"{n} ROI: no spread between ROIs")
        lines.append("(each ROI reduced to its own mean in the step first)")
    else:
        lines.append(f"SD of the {n} cubes in the step {sd:.{d}f}{unit}" if math.isfinite(sd) else f"{n} cube: no SD")
    return "\n".join(lines)
