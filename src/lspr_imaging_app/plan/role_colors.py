"""Colours of the evaluation chips on the plan bar (``B1`` / ``S1`` / ... / ``Sb``), 2026-11-13. Pure: no Qt, no numbers of the analysis.

Why: every chip used to be the same two colours (the baseline neutral, the signal in the accent colour), so with two or three evaluation pairs it was not clear which baseline
goes with which signal. Now **a pair is one colour**: its signal chip (``S1``) in the pair's colour, its baseline chip (``B1``) in the same colour darker and a little
transparent, so they read as one pair. The colours come from a palette of six (the maintainer does not expect more pairs; a seventh starts the palette again).

The ``Sb`` chip (a step of the S_bulk set) is a **grey**, from dark (the lowest level among the S_bulk steps) to white (the highest), so that the order of the steps by how
far the lines moved in them can be read at a glance. The level of a step is the median of the lines inside it as drawn (`bar_model.SectionLevels.median`: the shift when the
traces are subtracted from their baseline, the position in nm otherwise: the order is the same). A step with no level (no data in it) and a set of steps that all sit at the
same level get the middle grey: no order to show.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .edits import BULK_CHIP

# six colours that stay apart from each other on a dark and a light plot (the theme's accents, and a teal): blue, gold, green, red, purple, teal
PAIR_PALETTE: tuple[str, ...] = ("#38bdf8", "#f59e0b", "#22c55e", "#ef4444", "#a855f7", "#14b8a6")
BASELINE_DARKEN = 0.55  # the baseline chip's colour is the pair's colour times this (darker)
BASELINE_ALPHA, SIGNAL_ALPHA = 0.88, 0.95  # and a little more transparent than the signal chip
GRAY_DARK, GRAY_WHITE, GRAY_MIDDLE = 0x14, 0xFF, 0x80  # the S_bulk scale: nearly black (so it still has a shape on a dark plot) to white

KIND_BASELINE, KIND_SIGNAL, KIND_BULK, KIND_OTHER = "B", "S", "Sb", ""


def parse_role(role: str) -> tuple[str, int]:
    """(kind, pair number) of a chip: ``("B", 1)`` for ``B1``, ``("S", 2)`` for ``S2``, ``("Sb", 0)`` for the S_bulk chip, ``("", 0)`` for anything else."""
    if role == BULK_CHIP:
        return KIND_BULK, 0
    kind, digits = role[:1], role[1:]
    if kind in (KIND_BASELINE, KIND_SIGNAL) and digits.isdigit():
        return kind, int(digits)
    return KIND_OTHER, 0


def pair_color(number: int) -> str:
    """The colour of evaluation pair `number` (1, 2, ...) as ``#rrggbb``; the palette starts again after the sixth."""
    return PAIR_PALETTE[(max(int(number), 1) - 1) % len(PAIR_PALETTE)]


def darken(color: str, factor: float = BASELINE_DARKEN) -> str:
    """`color` (``#rrggbb``) with each channel times `factor` (0 - 1): the same hue, darker."""
    channels = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{min(max(int(round(c * factor)), 0), 255):02x}" for c in channels)


def role_fill(role: str) -> tuple[str, float] | None:
    """(colour, opacity) of a ``B`` or ``S`` chip by its pair; None for the S_bulk chip (its grey depends on the step, `bulk_grays`) and for an unknown chip."""
    kind, number = parse_role(role)
    if kind == KIND_SIGNAL:
        return pair_color(number), SIGNAL_ALPHA
    if kind == KIND_BASELINE:
        return darken(pair_color(number)), BASELINE_ALPHA
    return None


def bulk_grays(levels: Mapping[int, float | None]) -> dict[int, str]:
    """A grey (``#rrggbb``) for every S_bulk step, by its level: the lowest is `GRAY_DARK`, the highest white, the others in proportion (linear in the level, not in the rank, so
    two steps close together get close greys). `levels`: step index -> its level (None or NaN: no data). No spread (one step, or all equal) or no level: the middle grey."""
    finite = {i: float(v) for i, v in levels.items() if v is not None and math.isfinite(float(v))}
    low, high = (min(finite.values()), max(finite.values())) if finite else (0.0, 0.0)
    out: dict[int, str] = {}
    for index in levels:
        if index in finite and high > low:
            level = int(round(GRAY_DARK + (finite[index] - low) / (high - low) * (GRAY_WHITE - GRAY_DARK)))
        else:
            level = GRAY_MIDDLE
        out[index] = f"#{level:02x}{level:02x}{level:02x}"
    return out
