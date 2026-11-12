"""What the ROI / Groups table's step columns write in a cell and sort by. Pure: no Qt.

A step column holds, for every ROI and group, the mean and the SD of its line in one experiment step (``analysis/step_table.py``). The user picks which numbers the cells
show: the mean (`DISPLAY_MEAN`), the SD (`DISPLAY_STD`), the SEM (`DISPLAY_SEM`, SD / sqrt(n): how well the mean is known), or the mean with one of the two
(`DISPLAY_BOTH` = mean ± SD, `DISPLAY_MEAN_SEM` = mean ± SEM). The number format is the Sensorgram step table's (``plan/stats_table.py``): one number of decimals for the whole
table, two significant digits of the typical spread that is shown, so every cell reads alike. **Sorting**: a column sorts by the mean where the mean is shown (`DISPLAY_MEAN`,
`DISPLAY_BOTH`, `DISPLAY_MEAN_SEM`) and by the spread where only that is (`DISPLAY_STD`, `DISPLAY_SEM`); a row with no number for the step always comes last, in either direction.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Callable, Iterable

from ...selection.step_table_module import CellValue, StepColumn
from ...plan.stats_table import PLUS_MINUS, choose_decimals

DISPLAY_MEAN, DISPLAY_STD, DISPLAY_BOTH, DISPLAY_SEM, DISPLAY_MEAN_SEM = "mean", "std", "both", "sem", "mean_sem"
DISPLAY_EXCESS, DISPLAY_EXCESS_U, DISPLAY_EXCESS_Z = "excess", "excess_u", "excess_z"  # measured minus the bulk prediction (2026-11-12)
DISPLAYS = (
    DISPLAY_MEAN, DISPLAY_STD, DISPLAY_BOTH, DISPLAY_SEM, DISPLAY_MEAN_SEM, DISPLAY_EXCESS, DISPLAY_EXCESS_U, DISPLAY_EXCESS_Z,
)  # ("both" keeps its old name: it is what earlier sessions saved)
EXCESS_DISPLAYS = (DISPLAY_EXCESS, DISPLAY_EXCESS_U, DISPLAY_EXCESS_Z)
NA_TEXT = "NA"
DISPLAY_TIPS = {
    DISPLAY_MEAN: "Show the mean of each ROI or group in each step. A step column sorts by it.",
    DISPLAY_STD: "Show the standard deviation of each ROI or group in each step. A step column sorts by it.",
    DISPLAY_BOTH: f"Show the mean {PLUS_MINUS} the standard deviation, as the Sensorgram's step table does. A step column sorts by the mean.",
    DISPLAY_SEM: (
        "Show the standard error of the mean (SD / sqrt(number of cubes in the step)): how well the mean is known. It shrinks with a longer step, the SD does not. "
        "A step column sorts by it."
    ),
    DISPLAY_MEAN_SEM: f"Show the mean {PLUS_MINUS} its standard error (SD / sqrt(number of cubes)). A step column sorts by the mean.",
    DISPLAY_EXCESS: (
        "Show measured minus predicted: the step's mean minus what the row's own bulk sensitivity predicts for the step's refractive index (μ* = S_bulk × RI + the line's "
        "offset; for a difference column S_bulk × (RI_S − RI_B)). Positive = more shift than the bulk RI explains (binding, a higher-RI layer). NA = no RI for the step's "
        "solution, or no S_bulk. A column sorts by it."
    ),
    DISPLAY_EXCESS_U: f"Show measured minus predicted {PLUS_MINUS} its uncertainty (the steps' SEMs and the line's own uncertainty). A column sorts by the value.",
    DISPLAY_EXCESS_Z: (
        "Show measured minus predicted divided by its uncertainty: how many uncertainties a step is away from the bulk line. About ±1 is noise; ±3 or more "
        "is a real difference. A column sorts by it."
    ),
}
UNIT = "nm"
DEFAULT_DECIMALS = 2

STEP_WIDTH_DEFAULT = 118  # px: a step column that has no numbers yet to fit itself to ("595.773 ± 0.087" with room)
MIN_STEP_WIDTH = 56  # px: no fitted step column is narrower, however short its content
MAX_STEP_WIDTH = 360  # px: nor wider; a longer title is cut with "..." (its tool tip has the whole note)
_MEASURED_TEXTS = 64

_SEM_MODES = (DISPLAY_SEM, DISPLAY_MEAN_SEM)
_SPREAD_ONLY_MODES = (DISPLAY_STD, DISPLAY_SEM)  # the mean is not in the cell


def is_excess(mode: str) -> bool:
    return mode in EXCESS_DISPLAYS


def shows_mean(mode: str) -> bool:
    return mode not in _SPREAD_ONLY_MODES


def shows_spread(mode: str) -> bool:
    return mode != DISPLAY_MEAN


def spread_word(mode: str) -> str:
    """Which spread a mode shows: ``"sd"`` or ``"sem"`` (``"sd"`` also for the mean-only mode, which has no spread but is rounded by the SD)."""
    return "sem" if mode in _SEM_MODES else "sd"


def spread_of(value: CellValue, mode: str) -> float:
    if is_excess(mode):
        return value.excess_u
    return value.sem if mode in _SEM_MODES else value.sd


OPERATION_LABELS = {"difference": "Shift", "snr": "Signal to noise"}
"""The name written after the formula in a row of a comparison column's operation menu (the formula is a picture with subscripts, `title_html`; the tip says the rest)."""

_SUBSCRIPT = re.compile(r"_([A-Za-z0-9]+)")


def subscript_runs(title: str) -> list[tuple[str, bool]]:
    """A title split into ``(text, is_subscript)`` runs: ``"μ_S1 − μ_B1"`` -> ``[("μ", False), ("S1", True), (" − μ", False), ("B1", True)]``. An underscore and the letters / digits after it
    are a subscript; a title with none is one plain run. Only the titles the app writes itself (the comparison columns) are read this way: a step's title is the user's note, in which
    an underscore is just an underscore."""
    runs: list[tuple[str, bool]] = []
    position = 0
    for match in _SUBSCRIPT.finditer(title):
        if match.start() > position:
            runs.append((title[position:match.start()], False))
        runs.append((match.group(1), True))
        position = match.end()
    if position < len(title) or not runs:
        runs.append((title[position:], False))
    return runs


def title_html(title: str) -> str:
    """A comparison column's title as HTML with real subscripts (``<sub>``), for a picture of it (`equation_picture`): ``"μ_S1 − μ_B1"`` -> ``"μ<sub>S1</sub> − μ<sub>B1</sub>"``."""
    return "".join(f"<sub>{html.escape(text)}</sub>" if sub else html.escape(text) for text, sub in subscript_runs(title))


def plain_title(title: str) -> str:
    """A comparison column's title for a place that cannot draw subscripts (a menu entry): ``"μ_S1 − μ_B1"`` -> ``"μS1 − μB1"``. Never for a step's title (the user's note)."""
    return title.replace("_", "")


ELLIPSIS = "…"


def elide_runs(runs: list[tuple[str, bool]], advance: Callable[[str, bool], int], width: int) -> list[tuple[str, bool]]:
    """`runs` (`subscript_runs`) cut at the end, with "…", to fit `width` px; `advance` is (text, is_subscript) -> px. What fits is returned whole; a title that does not fit keeps
    its subscripts as far as it goes (it is never turned into text with underscores); nothing at all when not even the "…" fits."""
    if sum(advance(text, sub) for text, sub in runs) <= width:
        return list(runs)
    room = width - advance(ELLIPSIS, False)
    if room <= 0:
        return []
    kept: list[tuple[str, bool]] = []
    for text, sub in runs:
        used = advance(text, sub)
        if used <= room:
            kept.append((text, sub))
            room -= used
            continue
        cut = ""
        for char in text:
            if advance(cut + char, sub) > room:
                break
            cut += char
        if cut:
            kept.append((cut, sub))
        break
    kept.append((ELLIPSIS, False))
    return kept
OPERATION_TIPS = {
    "difference": (
        "Mean of the signal step minus mean of the baseline step, in nm. The SD is the two steps' SDs added in quadrature; "
        "the SEM is the two steps' SEMs added in quadrature."
    ),
    "snr": "That difference divided by σ of the baseline step, its noise (its SD about a straight line, so a steady drift is not noise). A pure number; it has no SD or SEM.",
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
    if is_excess(mode):
        return _excess_text(value, mode, decimals)
    spread = spread_of(value, mode)
    if not shows_mean(mode):
        return f"{spread:.{decimals}f}" if math.isfinite(spread) else ""
    if not math.isfinite(value.mean):
        return ""
    if shows_spread(mode) and math.isfinite(spread):
        return f"{value.mean:.{decimals}f} {PLUS_MINUS} {spread:.{decimals}f}"
    return f"{value.mean:.{decimals}f}"


def _excess_text(value: CellValue, mode: str, decimals: int) -> str:
    """A cell in an excess mode: signed, so a positive excess reads as one; NA where the column could have one but does not (no RI, no S_bulk); empty where it never
    has one (the S_bulk and S/N columns)."""
    e, u = value.excess, value.excess_u
    if not math.isfinite(e):
        return NA_TEXT if value.excess_note else ""
    if mode == DISPLAY_EXCESS_Z:
        return f"{e / u:+.1f}" if math.isfinite(u) and u > 0 else NA_TEXT
    if mode == DISPLAY_EXCESS_U and math.isfinite(u):
        return f"{e:+.{decimals}f} {PLUS_MINUS} {u:.{decimals}f}"
    return f"{e:+.{decimals}f}"


def excess_number(value: CellValue | None, mode: str) -> float | None:
    """What an excess mode shows as a number: the excess, or the excess over its uncertainty; None where there is none."""
    if value is None or not math.isfinite(value.excess):
        return None
    if mode == DISPLAY_EXCESS_Z:
        return value.excess / value.excess_u if math.isfinite(value.excess_u) and value.excess_u > 0 else None
    return value.excess


def sort_value(value: CellValue | None, mode: str) -> float | None:
    """What a step column sorts a row by: the spread where only the spread is shown (`DISPLAY_STD`, `DISPLAY_SEM`), the mean otherwise; None for a row without a number."""
    if is_excess(mode):
        return excess_number(value, mode)
    if value is None:
        return None
    number = value.mean if shows_mean(mode) else spread_of(value, mode)
    return number if math.isfinite(number) else None


def _excess_lines(value: CellValue, unit: str) -> list[str]:
    """The comparison with the bulk prediction, in a step's or a difference's tool tip (any display mode)."""
    if math.isfinite(value.excess):
        text = f"measured − predicted {value.excess:+.3f} {unit}"
        if math.isfinite(value.excess_u):
            text += f" {PLUS_MINUS} {value.excess_u:.3f} {unit}"
            if value.excess_u > 0:
                text += f" ({value.excess / value.excess_u:+.1f} u)"
        return [text, f"predicted from this row's S_bulk: {value.predicted:.3f} {unit}"]
    return [value.excess_note] if value.excess_note else []


def _bulk_tooltip(head: str, column: StepColumn, value: CellValue | None) -> str:
    """An S_bulk cell: the slope, how well it is known (from the steps' SEMs and SDs), whether the steps lie on the line (reduced chi2), and how many steps it used."""
    if value is None:
        return f"{head}\nNo value: the row is not calculated yet (refresh button), or no step of the S_bulk set has a refractive index."
    fit = value.bulk.fit_sem if value.bulk is not None else None  # type: ignore[attr-defined]
    if not math.isfinite(value.mean):
        reason = fit.reason if fit is not None and fit.reason else "too few steps with a number"
        return f"{head}\nNo value: {reason}."
    unit = column.unit
    lines = [head, f"S_bulk {value.mean:.1f} {unit}"]
    if math.isfinite(value.sem):
        lines.append(f"± {value.sem:.1f} {unit} from the steps' SEMs (how well the slope is known)")
    if math.isfinite(value.sd):
        lines.append(f"± {value.sd:.1f} {unit} from the steps' SDs (what single-cube noise would give)")
    if fit is not None:
        if math.isfinite(fit.chi2_red):
            verdict = "the steps lie on the line within their error bars" if fit.chi2_red <= 2.0 else "the steps scatter more than their error bars: the uncertainty is raised by its square root"
            lines.append(f"reduced χ² {fit.chi2_red:.2f}: {verdict}")
        else:
            lines.append("two steps: no check of how well they lie on a line")
        lines.append(f"{fit.n_points} steps, {fit.n_distinct_ri} refractive indices; line: {fit.slope:.1f} × RI {fit.intercept:+.1f}")
    if not value.current:
        lines.append("Out of date: something changed since the steps were calculated. Use the refresh button.")
    return "\n".join(lines)


def cell_tooltip(row_text: str, column: StepColumn, value: CellValue | None, *, is_group: bool) -> str:
    """The full numbers of one cell and what they mean."""
    compare = column.kind == "compare"
    head = f"{row_text} - {plain_title(column.title) if compare else column.title}"  # (a tool tip cannot draw the title's subscripts)
    if compare and column.op == "bulk":
        return _bulk_tooltip(head, column, value)
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
        lines.extend(_excess_lines(value, unit.strip()))
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
    lines.extend(_excess_lines(value, UNIT))
    if not value.current:
        lines.append("Out of date: something changed since this was calculated. Use the recalculate button.")
    return "\n".join(lines)
