from __future__ import annotations

from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup
from lspr_imaging_app.gui.roi_color_palettes import sequential_gradient_color

# "This ROI's absorbance is already calculated" indicator color, shared by the
# image-overlay ROI label text and the ROI table row text. Not-yet-calculated
# ROIs keep the default/white text - see the "cached ROIs only" button help
# text for the full explanation of when this updates.
CACHED_ROI_INDICATOR_COLOR = "#3b82f6"


def resolved_roi_color(roi: AreaRoi, group: AreaRoiGroup | None, fallback: QColor) -> QColor:
    if roi.sample_color_hex:
        color = QColor(roi.sample_color_hex)
        if color.isValid():
            return color
    if group is not None:
        color = QColor(group.sample_color_hex)
        if color.isValid():
            return color
    return QColor(fallback)


def resolved_reference_color(roi: AreaRoi, group: AreaRoiGroup | None, fallback: QColor) -> QColor:
    if roi.reference_color_hex:
        color = QColor(roi.reference_color_hex)
        if color.isValid():
            return color
    if group is not None:
        color = QColor(group.reference_color_hex)
        if color.isValid():
            return color
    return QColor(fallback)


def shade_group_member_color(
    base: QColor,
    member_index: int,
    member_count: int,
    *,
    spread: int = 90,
    min_value: int = 90,
    max_value: int = 255,
) -> QColor:
    """Varies `base` (a group's own assigned color) by brightness (HSV
    value) according to one member's position within the group, so members
    of the same group read as a family - same hue/saturation - while still
    being told apart on a plot. Brightness is used rather than a hue/
    saturation shift so it still reads as "the same color, lighter/darker"
    rather than a different color outright.

    `member_index`/`member_count` should come from the group's own stable
    member order (`group.area_roi_ids`), not the current selection, so a
    member's shade doesn't change depending on what else happens to be
    selected alongside it."""
    if member_count <= 1:
        return QColor(base)
    hue, saturation, base_value, alpha = base.getHsv()
    low = max(min_value, base_value - spread // 2)
    high = min(max_value, base_value + spread // 2)
    if high <= low:
        return QColor(base)
    fraction = member_index / (member_count - 1)
    shaded_value = int(round(low + fraction * (high - low)))
    shaded = QColor()
    shaded.setHsv(hue, saturation, shaded_value, alpha)
    return shaded


def resolved_roi_plot_color(
    roi: AreaRoi,
    group: AreaRoiGroup | None,
    *,
    roi_fraction: float = 0.5,
    gradient_palette: str = "viridis",
) -> QColor:
    """Color for one ROI's series in the Spectra/Sensogram plots. Same
    precedence as `resolved_roi_color` (explicit ROI color, then group
    color) but replaces the flat shared fallback with a distinguishable
    one, since a plot - unlike the image overlay - relies on color alone
    to tell multiple simultaneously-drawn ROIs apart:
    - no color, no group: a position along the named sequential gradient
      palette (`roi_fraction` - see roi_color_palettes.roi_index_fraction -
      and `gradient_palette`), so ROIs read as an ordered progression by
      index rather than a scatter of unrelated hues.
    - no color, in a group: the group's own color (itself assigned from a
      categorical palette when the group was created - see
      roi_color_palettes.categorical_palette_color), shaded by this ROI's
      position among the group's members (`shade_group_member_color`).
    """
    if roi.sample_color_hex:
        color = QColor(roi.sample_color_hex)
        if color.isValid():
            return color
    if group is not None:
        color = QColor(group.sample_color_hex)
        if color.isValid():
            try:
                member_index = group.area_roi_ids.index(int(roi.area_roi_id))
            except ValueError:
                member_index = 0
            return shade_group_member_color(color, member_index, len(group.area_roi_ids))
    return sequential_gradient_color(gradient_palette, roi_fraction)
