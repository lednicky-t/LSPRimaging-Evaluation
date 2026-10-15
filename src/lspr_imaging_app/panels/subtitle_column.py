"""``SubtitleColumn`` - one field of a dock title bar's status row (2026-10-16).

The Image dock shows "what frame is this" as several fields (frame role, cube,
wavelength, time, comment). Each field has its own fixed place, left-aligned, so
a field that appears or disappears (a "Reference" word, a comment) never shifts
the others. A panel describes the fields; `PanelContainer.set_subtitle_columns`
(`dock_container.py`) draws them. Qt-free: it is only data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubtitleColumn:
    text: str = ""
    # The widest text this field can ever show ("Cube 314", "1234 nm"). The dock gives the field
    # exactly that width, whatever `text` is now, so the fields after it stay in place. Digits
    # are measured with the widest digit of the font. "" = the field takes all the room that is
    # left, left-aligned, and is cut with "..." when the text is longer.
    widest: str = ""
    color: str | None = None  # a CSS colour; None = the title bar's muted text colour
    tooltip: str = ""
