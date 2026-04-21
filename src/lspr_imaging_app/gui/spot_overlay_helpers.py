from __future__ import annotations

from PyQt6.QtGui import QColor

from lspr_imaging_app.domain.models import DetectedSpot, SpotGroup


def resolved_spot_color(spot: DetectedSpot, group: SpotGroup | None, fallback: QColor) -> QColor:
    if spot.spot_color_hex:
        color = QColor(spot.spot_color_hex)
        if color.isValid():
            return color
    if group is not None:
        color = QColor(group.spot_color_hex)
        if color.isValid():
            return color
    return QColor(fallback)


def resolved_ring_color(spot: DetectedSpot, group: SpotGroup | None, fallback: QColor) -> QColor:
    if spot.ring_color_hex:
        color = QColor(spot.ring_color_hex)
        if color.isValid():
            return color
    if group is not None:
        color = QColor(group.ring_color_hex)
        if color.isValid():
            return color
    return QColor(fallback)
