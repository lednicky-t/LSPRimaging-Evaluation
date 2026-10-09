"""How ROIs are drawn: the one place the Sample / Reference display style lives.

Read by every view of the ROIs (the Image panel's overlay, the ROI table's colour dots), written only by the
ribbon controls through `set`. Display-only (cosmetic): it never changes a ROI or a result. Remembered across
restarts by the Image panel (`restore_ui_state`), which fills this object once at start.

``sample.color`` is the colour of a ROI that has **no colour of its own** (a ROI in a group, or one coloured by
hand, keeps its own `AreaRoi.sample_color_hex`).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_SAMPLE_COLOR = "#f59e0b"
DEFAULT_REFERENCE_COLOR = "#38bdf8"

_FIELDS = ("visible", "color", "alpha")


@dataclass
class RoiCircleStyle:
    visible: bool
    color: str  # "#rrggbb"
    alpha: float  # 0..1


class RoiDisplayStyle(QObject):
    changed = pyqtSignal(str)  # "sample" | "reference": that kind's style changed

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sample = RoiCircleStyle(True, DEFAULT_SAMPLE_COLOR, 1.0)
        self.reference = RoiCircleStyle(True, DEFAULT_REFERENCE_COLOR, 1.0)

    def style(self, kind: str) -> RoiCircleStyle:
        """The style of ``"sample"`` or ``"reference"``."""
        return self.sample if kind == "sample" else self.reference

    def set(self, kind: str, field: str, value: object) -> None:
        """Change one field of a kind's style and tell every view."""
        if field not in _FIELDS:
            raise ValueError(f"field must be one of {_FIELDS}, got {field!r}")
        setattr(self.style(kind), field, value)
        self.changed.emit(kind)
