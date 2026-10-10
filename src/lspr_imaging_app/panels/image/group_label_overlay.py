"""Group labels on the image: each group's name, drawn beside its first or last ROI.

Like the ROI labels (`roi_label_overlay.py`) one item paints every label in screen pixels, a child of the plot's
`ViewBox`. Differences: a label is placed *outside* its anchor ROI on a chosen side, the text may run vertically,
and it is kept from running into the next group's labels by a **box**: the longest a label may be, set as a
percent of the distance between neighbouring ROIs of its group (default 90 %, so a label about one pitch long
just fits before the next ROI's). A name that does not fit on one line is first shrunk, down to the smallest
size the user finds readable (`min_point_size`), and only then broken into several lines (at spaces, or inside a long word), each no longer than the box;
only past `_MAX_LINES` lines is the end cut with "…".
The box follows the image, so the guarantee holds at any zoom.

Pure helpers (no Qt widgets, easy to test): `group_label_entries`, `label_rect`. Display only: nothing here
changes any ROI or group.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter

from ...roi.model import AreaRoi, AreaRoiGroup

SIDES = ("top", "left", "bottom", "right")
POSITIONS = ("first", "last")
MIN_BOX_PERCENT = 10
MAX_BOX_PERCENT = 300
DEFAULT_BOX_PERCENT = 90

_GAP_PX = 3.0  # between the anchor circle's edge and the label
_POINT_SIZE = 9.0  # the normal size; shrunk towards `GroupLabelSettings.min_point_size` for long names
MIN_POINT_SIZE_RANGE = (4, 14)
DEFAULT_MIN_POINT_SIZE = 6
_MAX_LINES = 4
_MIN_BOX_PX = 12.0  # a box smaller than this on screen is not worth a label
_HALO_COLOR = QColor(0, 0, 0, 210)
_HALO_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))


@dataclass
class GroupLabelSettings:
    visible: bool = False
    vertical: bool = False  # text direction: vertical text reads bottom to top
    position: str = "first"  # the group's "first" or "last" ROI (by ROI number) carries the label
    side: str = "top"  # of that ROI: "top" | "left" | "bottom" | "right"
    box_percent: float = float(DEFAULT_BOX_PERCENT)
    min_point_size: float = float(DEFAULT_MIN_POINT_SIZE)  # the smallest text the shrinking may reach (points)


@dataclass(frozen=True)
class GroupLabelEntry:
    """One label, in image (data) coordinates."""

    text: str
    x: float  # centre of the anchor ROI
    y: float
    radius: float  # of the anchor ROI's sample circle
    spacing: float  # distance between neighbouring ROIs of the group (the box is a percent of it)
    color: str  # ``#rrggbb``


def group_label_entries(
    groups: Sequence[AreaRoiGroup],
    rois: Sequence[AreaRoi],
    centers: Sequence[Sequence[float]],
    *,
    position: str,
    default_color: str,
) -> list[GroupLabelEntry]:
    """The labels to draw: one per group that has a ROI among ``rois`` (``centers[i]`` is the display centre of
    ``rois[i]``). The anchor is the group's first or last ROI by number. The colour is the middle member's own
    colour (a group's members run through a gradient, so the middle one represents it), else the group's base
    colour. The spacing is the median distance between consecutive members (by number); a group of one ROI uses
    twice its diameter."""
    index_of = {roi.area_roi_id: i for i, roi in enumerate(rois)}
    entries: list[GroupLabelEntry] = []
    for group in groups:
        members = sorted(roi_id for roi_id in group.area_roi_ids if roi_id in index_of)
        if not members:
            continue
        points = [(float(centers[index_of[m]][0]), float(centers[index_of[m]][1])) for m in members]
        anchor = members[0] if position != "last" else members[-1]
        anchor_roi = rois[index_of[anchor]]
        steps = sorted(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))
        if steps:
            middle = len(steps) // 2
            spacing = steps[middle] if len(steps) % 2 else 0.5 * (steps[middle - 1] + steps[middle])
        else:
            spacing = 2.0 * float(anchor_roi.sample_diameter_px)
        middle_roi = rois[index_of[members[len(members) // 2]]]
        color = middle_roi.sample_color_hex or group.sample_color_hex or default_color
        entries.append(
            GroupLabelEntry(
                text=group.name,
                x=float(centers[index_of[anchor]][0]),
                y=float(centers[index_of[anchor]][1]),
                radius=float(anchor_roi.sample_diameter_px) / 2.0,
                spacing=max(spacing, 1e-6),
                color=color,
            )
        )
    return entries


def label_rect(anchor: QPointF, radius: float, side: str, width: float, height: float, gap: float = _GAP_PX) -> QRectF:
    """Where a ``width`` x ``height`` label goes (screen pixels, y down) on ``side`` of a circle of ``radius``
    around ``anchor``: just outside the circle, centred on it across the other axis."""
    if side == "top":
        return QRectF(anchor.x() - width / 2.0, anchor.y() - radius - gap - height, width, height)
    if side == "bottom":
        return QRectF(anchor.x() - width / 2.0, anchor.y() + radius + gap, width, height)
    if side == "left":
        return QRectF(anchor.x() - radius - gap - width, anchor.y() - height / 2.0, width, height)
    if side == "right":
        return QRectF(anchor.x() + radius + gap, anchor.y() - height / 2.0, width, height)
    raise ValueError(f"side must be one of {SIDES}, got {side!r}")


def wrap_text(text: str, width_of: Callable[[str], float], box_px: float, max_lines: int = _MAX_LINES) -> list[str]:
    """``text`` broken into lines no wider than ``box_px`` (``width_of`` measures a string): at spaces where
    possible, inside a word that is wider than the box on its own. Past ``max_lines`` the last line ends in "…"."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if width_of(candidate) <= box_px:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        while width_of(word) > box_px and len(word) > 1:  # a word wider than the box: break it
            cut = len(word) - 1
            while cut > 1 and width_of(word[:cut]) > box_px:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines or [text]


class GroupLabelItem(pg.GraphicsObject):
    def __init__(self, view_box: pg.ViewBox) -> None:
        super().__init__()
        self._vb = view_box
        self.setParentItem(view_box)
        self.setZValue(901)
        self._entries: list[GroupLabelEntry] = []
        self._settings = GroupLabelSettings()
        self.setVisible(False)

    def set_entries(self, entries: Collection[GroupLabelEntry]) -> None:
        self._entries = list(entries)
        self._refresh_visibility()
        self.update()

    def entries(self) -> list[GroupLabelEntry]:
        return list(self._entries)

    def set_settings(self, settings: GroupLabelSettings) -> None:
        self._settings = settings
        self._refresh_visibility()
        self.update()

    def _refresh_visibility(self) -> None:
        self.setVisible(bool(self._entries) and self._settings.visible)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt naming
        return QRectF(self._vb.boundingRect())

    @staticmethod
    def _fit(text: str, box_px: float, min_point_size: float) -> tuple[QFont, list[str]]:
        """The font and lines for ``text`` in a box ``box_px`` long: the normal size if it fits on one line, else
        shrunk (in half-point steps) until it does or ``min_point_size`` is reached, where it is broken into lines."""
        font = QFont()
        font.setBold(True)
        low = min(float(min_point_size), _POINT_SIZE)
        size = _POINT_SIZE
        while True:
            font.setPointSizeF(size)
            metrics = QFontMetricsF(font)
            if metrics.horizontalAdvance(text) <= box_px or size <= low:
                break
            size = max(low, size - 0.5)
        return font, wrap_text(text, QFontMetricsF(font).horizontalAdvance, box_px)

    def paint(self, painter: QPainter, *_args: object) -> None:
        if not self._entries or not self._settings.visible:
            return
        settings = self._settings
        view = self._vb.boundingRect()
        visible = view.adjusted(-60.0, -60.0, 60.0, 60.0)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for entry in self._entries:
            anchor = self._vb.mapFromView(QPointF(entry.x, entry.y))
            if not visible.contains(anchor):
                continue
            unit = self._vb.mapFromView(QPointF(entry.x + 1.0, entry.y))
            pixels_per_unit = math.hypot(unit.x() - anchor.x(), unit.y() - anchor.y())
            box_px = entry.spacing * pixels_per_unit * settings.box_percent / 100.0
            if box_px < _MIN_BOX_PX or not entry.text:
                continue
            font, lines = self._fit(entry.text, box_px, settings.min_point_size)
            metrics = QFontMetricsF(font)
            widths = [metrics.horizontalAdvance(line) for line in lines]
            width, line_height = max(widths), metrics.height()
            height = line_height * len(lines)
            across_w, across_h = (height, width) if settings.vertical else (width, height)
            rect = label_rect(anchor, entry.radius * pixels_per_unit, settings.side, across_w, across_h)
            painter.save()
            painter.setFont(font)
            painter.translate(rect.center())
            if settings.vertical:
                painter.rotate(-90.0)  # reads from the bottom up
            for n, (line, line_width) in enumerate(zip(lines, widths, strict=True)):  # each line centred in the block
                origin = QPointF(-line_width / 2.0, -height / 2.0 + n * line_height + metrics.ascent())
                painter.setPen(_HALO_COLOR)
                for dx, dy in _HALO_OFFSETS:
                    painter.drawText(QPointF(origin.x() + dx, origin.y() + dy), line)
                painter.setPen(QColor(entry.color))
                painter.drawText(origin, line)
            painter.restore()
