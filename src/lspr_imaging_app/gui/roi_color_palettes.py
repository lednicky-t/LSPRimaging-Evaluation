"""Named color palettes for ROI plot coloring - see roi_overlay_helpers.py's
resolved_roi_plot_color, the single place both kinds get consumed.

Two different jobs, two different kinds of palette (this split follows
standard data-visualization practice - see e.g. matplotlib's own
sequential-vs-qualitative colormap categories):

- SEQUENTIAL_PALETTES: for an *ordered* quantity - here, a ROI's position
  among all of the dataset's ROIs. One smooth gradient from one end of the
  palette to the other, so nearby ROI indices read as nearby colors.
- CATEGORICAL_PALETTES: for *identity* with no inherent order - which group
  a ROI belongs to. Colors are chosen to be maximally distinguishable from
  each other, not to form a gradient.

Every hex value below was read directly from matplotlib (`matplotlib.
colormaps[name]`), not hand-picked, so they match the real, recognizable
colormap/palette of that name.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor

# 9 evenly-spaced samples (t = 0, 1/8, ..., 1) of each matplotlib
# perceptually-uniform sequential colormap - enough stops that linear
# interpolation between them (see sequential_gradient_color) looks smooth,
# without carrying each colormap's full 256-entry lookup table.
SEQUENTIAL_PALETTES: dict[str, list[str]] = {
    "viridis": [
        "#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c",
        "#28ae80", "#5ec962", "#addc30", "#fde725",
    ],
    "plasma": [
        "#0d0887", "#4c02a1", "#7e03a8", "#aa2395", "#cc4778",
        "#e66c5c", "#f89540", "#fdc527", "#f0f921",
    ],
    "cividis": [
        "#00224e", "#1a386f", "#434e6c", "#61656f", "#7d7c78",
        "#9b9476", "#bcae6c", "#dec958", "#fee838",
    ],
    "turbo": [
        "#30123b", "#466be3", "#28bceb", "#32f298", "#a4fc3c",
        "#eecf3a", "#fb7e21", "#d02f05", "#7a0403",
    ],
}
DEFAULT_SEQUENTIAL_PALETTE = "viridis"

# Standard qualitative palettes, each read verbatim from matplotlib.
# Tab20 (10 hue pairs, each a dark/light shade) is the standard answer to
# "how many groups can one palette distinguish": it's built for exactly
# the 10-20-category range.
CATEGORICAL_PALETTES: dict[str, list[str]] = {
    "tab10": [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    ],
    "tab20": [
        "#1f77b4", "#aec7e8", "#ff7f0e", "#ffbb78", "#2ca02c",
        "#98df8a", "#d62728", "#ff9896", "#9467bd", "#c5b0d5",
        "#8c564b", "#c49c94", "#e377c2", "#f7b6d2", "#7f7f7f",
        "#c7c7c7", "#bcbd22", "#dbdb8d", "#17becf", "#9edae5",
    ],
    "set2": [
        "#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3",
        "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3",
    ],
    "dark2": [
        "#1b9e77", "#d95f02", "#7570b3", "#e7298a",
        "#66a61e", "#e6ab02", "#a6761d", "#666666",
    ],
}
DEFAULT_CATEGORICAL_PALETTE = "tab10"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    color = QColor(hex_color)
    return color.red(), color.green(), color.blue()


def sequential_gradient_color(palette_name: str, fraction: float) -> QColor:
    """A color at `fraction` (0..1, clamped) along the named sequential
    palette, linearly interpolated between its two nearest stops."""
    stops = SEQUENTIAL_PALETTES.get(palette_name) or SEQUENTIAL_PALETTES[DEFAULT_SEQUENTIAL_PALETTE]
    fraction = min(max(float(fraction), 0.0), 1.0)
    if len(stops) == 1:
        return QColor(stops[0])
    scaled = fraction * (len(stops) - 1)
    lower_index = int(scaled)
    upper_index = min(lower_index + 1, len(stops) - 1)
    local_t = scaled - lower_index
    lower_rgb = _hex_to_rgb(stops[lower_index])
    upper_rgb = _hex_to_rgb(stops[upper_index])
    blended = tuple(
        round(lower_rgb[channel] + (upper_rgb[channel] - lower_rgb[channel]) * local_t) for channel in range(3)
    )
    return QColor(*blended)


def roi_index_fraction(roi_id: int, all_roi_ids: list[int]) -> float:
    """This ROI's position among `all_roi_ids` (sorted, 0..1) - the input
    to `sequential_gradient_color` for an ungrouped ROI's plot color.
    Stable per ROI id regardless of what's currently selected, so a given
    ROI keeps the same color across different selections; it only shifts
    if ROIs are added to or removed from the dataset."""
    sorted_ids = sorted(int(value) for value in all_roi_ids)
    if len(sorted_ids) <= 1:
        return 0.5
    try:
        position = sorted_ids.index(int(roi_id))
    except ValueError:
        return 0.5
    return position / (len(sorted_ids) - 1)


def categorical_palette_color(palette_name: str, index: int) -> QColor:
    """A group's own base color, assigned in creation order (`index`,
    0-based) from the named qualitative palette. Once `index` exceeds the
    palette's length, cycles back through the same hues but shifted
    lighter/darker on each extra pass, so e.g. a 15th group with an
    8-color palette still reads as visually different from the 7th rather
    than an exact repeat."""
    palette = CATEGORICAL_PALETTES.get(palette_name) or CATEGORICAL_PALETTES[DEFAULT_CATEGORICAL_PALETTE]
    index = max(int(index), 0)
    base_color = QColor(palette[index % len(palette)])
    cycle = index // len(palette)
    if cycle == 0:
        return base_color
    hue, saturation, value, alpha = base_color.getHsv()
    direction = 1 if cycle % 2 == 1 else -1
    magnitude = min((cycle + 1) // 2, 3) * 35
    new_value = max(70, min(255, value + direction * magnitude))
    shifted = QColor()
    shifted.setHsv(hue, saturation, new_value, alpha)
    return shifted
