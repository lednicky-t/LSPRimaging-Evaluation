from __future__ import annotations

from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup

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


# Golden-angle hue step (in degrees): the standard trick for turning an
# arbitrary integer id into one of N well-separated hues without a fixed-
# size palette - consecutive ids land far apart on the color wheel instead
# of clustering, so however many ROIs end up plotted together, neighbours
# are never near-duplicates.
_GOLDEN_ANGLE_DEG = 137.508


def auto_roi_color(roi_id: int, *, saturation: int = 200, value: int = 225) -> QColor:
    """A distinct, deterministic color for a ROI that has no color of its
    own and isn't in a group - so several such ROIs plotted together (e.g.
    their Spectra/Sensogram traces) are visually distinguishable instead of
    all sharing the one flat default color. Keyed on the ROI's own stable
    `area_roi_id`, not its position in the current selection, so a given
    ROI keeps the same color across different selections and sessions."""
    hue = int((int(roi_id) * _GOLDEN_ANGLE_DEG) % 360)
    color = QColor()
    color.setHsv(hue, saturation, value)
    return color


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


def resolved_roi_plot_color(roi: AreaRoi, group: AreaRoiGroup | None) -> QColor:
    """Color for one ROI's series in the Spectra/Sensogram plots. Same
    precedence as `resolved_roi_color` (explicit ROI color, then group
    color) but replaces the flat shared fallback with an auto-generated
    color, since a plot - unlike the image overlay - relies on color alone
    to tell multiple simultaneously-drawn ROIs apart:
    - no color, no group: a distinct hue per ROI (`auto_roi_color`).
    - no color, in a group: the group's color, shaded by this ROI's
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
    return auto_roi_color(int(roi.area_roi_id))
