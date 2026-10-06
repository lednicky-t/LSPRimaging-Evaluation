"""Group colours and the per-ROI tints derived from them. Pure: no Qt.

A group has one **base colour**. Each ROI that joins the group is given a
**tint** of it, so the members are told apart while still reading as one
group. The tint is picked once, when the ROI joins, and stored on the ROI
(`AreaRoi.sample_color_hex`); it does not change when other ROIs come or go.

`tint_color(base, 0)` is the base colour itself, so the first member matches
the group's swatch. The next tints are the same hue at other lightness
levels, each chosen as far as possible from the ones already given out, so
ROIs numbered next to each other contrast strongly (seven are easy to tell
apart). Past seven the hue is nudged for each further round, so a large group
(a whole array) keeps getting new colours instead of repeating.
"""

from __future__ import annotations

import colorsys
import re
from collections.abc import Iterable

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

GROUP_BASE_COLORS: tuple[str, ...] = (
    # matplotlib "tab10": the same qualitative palette the stable app uses
    # for group colours (gui/roi_color_palettes.py), chosen for being easy to
    # tell apart from each other.
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
)

DEFAULT_ROI_COLOR_HEX = "#f59e0b"
"""What a ROI with no stored colour (not in a group, none set by hand) is
shown in. The same amber the Image panel draws sample circles in."""

_LEVELS = (0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85)
"""The lightness levels of one round of tints. Even spacing keeps every tint
of a round distinguishable; the ends stay clear of near-black and near-white."""
_HUE_STEP_PER_ROUND = 0.06


def normalize_hex(color: str) -> str:
    """`"#1F77B4"` -> `"#1f77b4"`. Raises `ValueError` if not `#rrggbb`."""
    if not isinstance(color, str) or not HEX_COLOR.match(color):
        raise ValueError(f"not a #rrggbb colour: {color!r}")
    return color.lower()


def next_group_color(existing_base_colors: Iterable[str]) -> str:
    """The first base colour no group uses yet; once all are used, cycles by
    how many groups there are."""
    used = {color.lower() for color in existing_base_colors}
    for color in GROUP_BASE_COLORS:
        if color not in used:
            return color
    return GROUP_BASE_COLORS[len(used) % len(GROUP_BASE_COLORS)]


def _tint_levels(base_lightness: float) -> list[float]:
    """The six lightness levels a round uses besides the base colour itself,
    in the order they are handed out: the level the base already sits at is
    skipped, and each next one is the level farthest from all those already
    taken (ties go to the lighter), so early tints contrast most."""
    occupied = min(_LEVELS, key=lambda level: (abs(level - base_lightness), -level))
    remaining = [level for level in _LEVELS if level != occupied]
    taken = [base_lightness]
    order: list[float] = []
    while remaining:
        best = max(remaining, key=lambda level: (min(abs(level - t) for t in taken), level))
        order.append(best)
        taken.append(best)
        remaining.remove(best)
    return order


def tint_color(base_hex: str, index: int) -> str:
    """Tint number ``index`` (0, 1, 2, ...) of ``base_hex``, as ``#rrggbb``.
    Index 0 is the base colour itself."""
    base = normalize_hex(base_hex)
    if index <= 0:
        return base
    red, green, blue = (int(base[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    round_number, position = divmod(index, len(_LEVELS))
    if position == 0:
        level = lightness  # the base's own lightness again, with the hue moved on
    else:
        level = _tint_levels(lightness)[position - 1]
    hue = (hue + round_number * _HUE_STEP_PER_ROUND) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, level, saturation)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def first_free_tint_index(base_hex: str, used_colors: Iterable[str]) -> int:
    """The smallest tint index of ``base_hex`` that none of ``used_colors``
    already shows - what the next ROI to join the group should get."""
    used = {color.lower() for color in used_colors if color}
    index = 0
    while tint_color(base_hex, index) in used:
        index += 1
    return index
