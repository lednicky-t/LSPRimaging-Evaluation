"""How a number of the ROI / Groups table is written, per column (2026-11-26). Pure: no Qt. Display only: nothing stored, copied or edited is changed by it.

**Notation** (the right-click menu of a column's header, "Display"):

- ``normal``: ``595.773``
- ``scientific``: one digit before the point, any exponent: ``5.95773e+02``

**Decimals**: the digits after the point. ``None`` (automatic) is what the column showed before: the column's own (``rows.length_decimals`` ...) or, for a step column, the one number of
decimals the table chose from the spread of its numbers (`step_columns.table_decimals`). In ``normal`` notation a number is written with exactly that many. In scientific notation an
*explicit* number is the digits after the point of the mantissa (``3`` -> ``5.958e+02``), as a scientist reads it; *automatic* keeps the resolution the plain number had (``0.087`` at 3
decimals -> ``8.7e-02``; ``595.773`` -> ``5.95773e+02``), so switching the notation alone never loses a digit the table showed.

A ``mean ± spread`` cell writes each of its two numbers with its own exponent. (An engineering notation, exponents in steps of 3, was built and taken out the same day: too like scientific.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NORMAL = "normal"
SCIENTIFIC = "scientific"
NOTATIONS = (NORMAL, SCIENTIFIC)
NOTATION_LABELS = {NORMAL: "Normal", SCIENTIFIC: "Scientific"}

MIN_DECIMALS = 0
MAX_DECIMALS = 9  # a double has ~16 significant digits; more than this on a nm or px value is noise


@dataclass(frozen=True)
class ColumnDisplay:
    """What one column's numbers look like. The default (automatic decimals, normal notation) is "no setting": the table's own look."""

    decimals: int | None = None  # None: automatic
    notation: str = NORMAL

    @property
    def is_default(self) -> bool:
        return self.decimals is None and self.notation == NORMAL


DEFAULT_DISPLAY = ColumnDisplay()


def clean_decimals(value: object) -> int | None:
    """A saved number of decimals as an int in range, or None for automatic / anything unreadable."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return min(max(value, MIN_DECIMALS), MAX_DECIMALS)


def clean_display(decimals: object, notation: object) -> ColumnDisplay:
    return ColumnDisplay(clean_decimals(decimals), notation if notation in NOTATIONS else NORMAL)


def _exponent(value: float) -> int:
    """The power of ten scientific notation writes `value` with (0 for zero)."""
    return 0 if value == 0.0 else math.floor(math.log10(abs(value)))


def _mantissa_text(value: float, exponent: int, decimals: int, signed: bool) -> tuple[str, int]:
    """``(mantissa text, exponent)``: rounding can carry into the next power (9.9996 at 3 decimals is 10.000), which then moves the exponent."""
    mantissa = value / 10.0 ** exponent
    text = f"{mantissa:+.{decimals}f}" if signed else f"{mantissa:.{decimals}f}"
    return text, exponent


def format_number(value: float, decimals: int, notation: str = NORMAL, *, signed: bool = False) -> str:
    """`value` with `decimals` digits after the point (of the mantissa for scientific notation). `signed` always writes the sign. Not for NaN / infinity: the callers leave those
    cells empty."""
    if notation == NORMAL or not math.isfinite(value):
        return f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    exponent = _exponent(value)
    text, exponent = _mantissa_text(value, exponent, decimals, signed)
    if abs(float(text)) >= 10.0:  # rounded up into the next power of ten (9.9996 at 3 decimals is 10.000)
        text, exponent = _mantissa_text(value, exponent + 1, decimals, signed)
    return f"{text}e{exponent:+03d}"


def automatic_mantissa_decimals(value: float, plain_decimals: int) -> int:
    """The digits after the point of the mantissa that keep the resolution of the plain number written with `plain_decimals` (never fewer than none)."""
    if value == 0.0 or not math.isfinite(value):
        return 0
    return max(0, plain_decimals + _exponent(value))


def format_value(value: float, plain_decimals: int, display: ColumnDisplay | None = None, *, signed: bool = False) -> str:
    """The text of one number: `plain_decimals` is what the column shows without a setting; `display` (None = no setting) changes the decimals and the notation."""
    if display is None or display.is_default:
        return format_number(value, plain_decimals, NORMAL, signed=signed)
    if display.notation == NORMAL:
        return format_number(value, plain_decimals if display.decimals is None else display.decimals, NORMAL, signed=signed)
    decimals = automatic_mantissa_decimals(value, plain_decimals) if display.decimals is None else display.decimals
    return format_number(value, decimals, display.notation, signed=signed)


def shown_decimals(value: float, plain_decimals: int, display: ColumnDisplay | None) -> int:
    """The digits after the point `format_value` writes for `value`: what a "More / Fewer decimals" step starts from."""
    if display is None or display.is_default:
        return plain_decimals
    if display.decimals is not None:
        return display.decimals
    if display.notation == NORMAL:
        return plain_decimals
    return automatic_mantissa_decimals(value, plain_decimals)


def stepped(display: ColumnDisplay | None, shown: int, direction: int) -> ColumnDisplay:
    """The display after a "More decimals" (+1) or "Fewer decimals" (-1) from the `shown` digits: an explicit number, kept in range."""
    base = display or DEFAULT_DISPLAY
    return ColumnDisplay(min(max(shown + direction, MIN_DECIMALS), MAX_DECIMALS), base.notation)
