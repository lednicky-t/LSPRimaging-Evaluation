"""Guides: horizontal reference lines at a y value (2026-11-27). No Qt, no numbers of the analysis.

A guide is a line across the plot at a y value (a baseline of 650 nm, a threshold, the value of a control) with an optional label, a colour and a dash style. It is drawn behind the data,
never changes the autoscale and never changes a number: it only marks a place on the axis. Guides belong to the **y quantity** an axis shows (a guide at 650 means a position in nm, not
a shift), so they are saved with the y side's `AxisOptions` (`plot_axes.py`) in the session.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

MAX_GUIDES = 8  # the Guides table has this many rows; a longer saved list is cut
MAX_LABEL_CHARS = 40
STYLES = ("dash", "solid", "dot")
DEFAULT_STYLE = STYLES[0]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class Guide:
    value: float  # the y value (axis units) the line stands at
    label: str = ""  # written at the right end of the line; empty = none
    color: str | None = None  # '#rrggbb'; None = the theme's muted text colour (so it stays legible on every theme)
    style: str = DEFAULT_STYLE  # one of `STYLES`
    show_value: bool = False  # write the guide's y value (after the label, if there is one) at the end of the line; it follows the line while it is dragged

    def sanitized(self) -> Guide | None:
        """The same guide with every field valid, or None when its value is not a finite number (such a guide cannot be drawn)."""
        try:
            value = float(self.value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        label = " ".join(str(self.label or "").split())[:MAX_LABEL_CHARS]
        color = self.color.lower() if isinstance(self.color, str) and _HEX.match(self.color) else None
        style = self.style if self.style in STYLES else DEFAULT_STYLE
        return Guide(value, label, color, style, bool(self.show_value))

    def to_payload(self) -> dict:
        out: dict = {"value": float(self.value)}
        if self.label:
            out["label"] = self.label
        if self.color is not None:
            out["color"] = self.color
        if self.style != DEFAULT_STYLE:
            out["style"] = self.style
        if self.show_value:
            out["show_value"] = True
        return out

    @staticmethod
    def from_payload(raw: object) -> Guide | None:
        """Lenient: anything that is not a dict with a usable value is dropped (None)."""
        if not isinstance(raw, dict):
            return None
        return Guide(raw.get("value"), raw.get("label", ""), raw.get("color"), raw.get("style", DEFAULT_STYLE), raw.get("show_value") is True).sanitized()  # type: ignore[arg-type]


def normalized(guides: object) -> tuple[Guide, ...]:
    """Valid guides only, in the given order, at most `MAX_GUIDES`."""
    out: list[Guide] = []
    for item in guides if isinstance(guides, (list, tuple)) else ():
        clean = item.sanitized() if isinstance(item, Guide) else None
        if clean is not None:
            out.append(clean)
    return tuple(out[:MAX_GUIDES])


def rounded_for_span(value: float, span: float) -> float:
    """`value` rounded to a thousandth of the visible `span` (a guide put or dropped with the mouse gets a number a person would type: 650.123 on a 10 nm view, not 650.12345678).
    Returned unchanged when the span is no usable number."""
    if not (math.isfinite(value) and math.isfinite(span)) or span <= 0.0:
        return value
    decimals = max(0, 3 - math.floor(math.log10(span)))
    return round(value, decimals)
