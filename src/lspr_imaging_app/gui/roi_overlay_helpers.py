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
