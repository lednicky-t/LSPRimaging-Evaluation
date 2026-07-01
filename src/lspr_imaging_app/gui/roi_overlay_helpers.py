from __future__ import annotations

from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.models import AreaRoi, AreaRoiGroup


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
