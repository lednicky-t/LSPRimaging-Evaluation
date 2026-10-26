"""Which part of a step's note the plan bar shows (pure, no Qt).

A note is the text the experimenter typed for a step, e.g. ``30perEG_-_ch2_to_50perEG_-_ch1P_ch4F`` or ``H2O_--_ch1F_ch2P``: the solution name, a
separator, then pump details. By default the whole note is shown; the optional rule shows only the text before the first separator (usually the solution).

``_--_`` does not contain ``_-_``, so one separator is not enough for both notations in use; the list is editable and remembered like any control
(maintainer, 2026-10-26).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

MODE_WHOLE = "whole"
MODE_SOLUTION = "solution"
MODES = (MODE_WHOLE, MODE_SOLUTION)

DEFAULT_SEPARATORS: tuple[str, ...] = ("_-_", "_--_")


def shown_label(note: str, mode: str = MODE_WHOLE, separators: Sequence[str] = DEFAULT_SEPARATORS) -> str:
    """The text to draw for `note`.

    *whole*: the note without surrounding spaces. *solution*: the text before the earliest separator found in the note (the longest one when two start at
    the same place), without surrounding spaces. A note with no separator, or one that starts with a separator (nothing would be left to show), is
    shown whole: a label is never blank because of the rule."""
    text = note.strip()
    if mode != MODE_SOLUTION:
        return text
    best: tuple[int, int] | None = None  # (position, -length): the earliest, then the longest
    for separator in separators:
        if not separator:
            continue
        position = text.find(separator)
        if position < 0:
            continue
        candidate = (position, -len(separator))
        if best is None or candidate < best:
            best = candidate
    if best is None or best[0] == 0:
        return text
    head = text[: best[0]].strip()
    return head or text


def parse_separators(text: str) -> tuple[str, ...]:
    """The separators typed into the field: split on spaces, commas and semicolons, empty pieces dropped, duplicates removed (order kept)."""
    seen: dict[str, None] = {}
    for piece in re.split(r"[\s,;]+", text):
        if piece:
            seen.setdefault(piece, None)
    return tuple(seen)


def format_separators(separators: Iterable[str]) -> str:
    """The inverse of `parse_separators`, as stored and shown: separated by single spaces."""
    return " ".join(separators)
