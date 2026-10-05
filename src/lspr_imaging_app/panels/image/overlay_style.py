"""How one tint overlay on the Image panel looks: shown or hidden, color, opacity.

Used for the Mask overlay and the Histogram highlight overlay. A plain value
object (no Qt) so it can be saved in the app settings and handed back to
`ImagePanel` at the next launch."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayStyle:
    visible: bool
    color: str  # "#rrggbb"
    alpha: float  # 0.0 - 1.0
