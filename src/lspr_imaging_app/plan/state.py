"""The saved shape of the user's edits to the experiment steps (``ExperimentPlanModule``): plain dataclasses and their JSON payload, no Qt.

The measured steps belong to the dataset and are never written. An edit is an overlay on one of them and lives in the session file (``storage/session.py``,
schema 1.5, key ``"plan"``). Every edit carries a fingerprint of the measured step it was made on (its number, measured start and measured note). If the
dataset's steps change later (notes re-imported, another plan file), an edit whose fingerprint no longer matches is **not applied** and is reported; it is
never moved onto a different step (``edits.apply_edits``).

Only what the user changed is stored: a field equal to the measured value is not an edit, so dragging a boundary back to where it was measured leaves nothing
behind.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalise_color(text: object) -> str | None:
    """``"#rgb"`` / ``"#rrggbb"`` in any case -> ``"#rrggbb"`` in lower case; anything else (a name, ``None``, junk) -> None."""
    if not isinstance(text, str) or not _COLOR.match(text.strip()):
        return None
    digits = text.strip()[1:].lower()
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return "#" + digits


@dataclass(frozen=True)
class PlanEdit:
    """What the user changed on one step. ``None`` = not changed (the measured value is used)."""

    step: int
    measured_start_unix_ms: int
    measured_label: str
    start_unix_ms: int | None = None
    end_unix_ms: int | None = None
    label: str | None = None
    color: str | None = None

    @property
    def key(self) -> tuple[int, int, str]:
        """The fingerprint of the measured step this edit belongs to."""
        return (self.step, self.measured_start_unix_ms, self.measured_label)

    def is_empty(self) -> bool:
        return self.start_unix_ms is None and self.end_unix_ms is None and self.label is None and self.color is None

    def to_payload(self) -> dict:
        payload: dict = {
            "step": self.step,
            "measured_start_unix_ms": self.measured_start_unix_ms,
            "measured_label": self.measured_label,
        }
        for name in ("start_unix_ms", "end_unix_ms", "label", "color"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> "PlanEdit":
        """Raises ``ValueError`` / ``TypeError`` / ``KeyError`` for a record that cannot be read (the caller skips it)."""
        if not isinstance(raw, dict):
            raise TypeError("a plan edit must be an object")

        def optional_int(name: str) -> int | None:
            value = raw.get(name)
            return None if value is None else int(value)

        label = raw.get("label")
        return cls(
            step=int(raw["step"]),
            measured_start_unix_ms=int(raw["measured_start_unix_ms"]),
            measured_label=str(raw["measured_label"]),
            start_unix_ms=optional_int("start_unix_ms"),
            end_unix_ms=optional_int("end_unix_ms"),
            label=None if label is None else str(label),
            color=normalise_color(raw.get("color")),
        )


@dataclass(frozen=True)
class PlanEditsState:
    """All edits of a session, in a fixed order (by step, then by measured start) so two equal states compare equal."""

    edits: tuple[PlanEdit, ...] = ()

    def edit_for(self, key: tuple[int, int, str]) -> PlanEdit | None:
        return next((edit for edit in self.edits if edit.key == key), None)

    def to_payload(self) -> dict:
        return {"edits": [edit.to_payload() for edit in self.edits]}

    @classmethod
    def from_payload(cls, raw: object) -> "PlanEditsState":
        """Rebuild from a session file's ``"plan"`` block. A missing or unreadable block gives no edits (the right reading of a session written before the
        plan bar existed); a record that cannot be read is skipped with a warning and the others are kept."""
        if not isinstance(raw, dict):
            return cls()
        records = raw.get("edits")
        if not isinstance(records, list):
            return cls()
        edits: dict[tuple[int, int, str], PlanEdit] = {}
        for record in records:
            try:
                edit = PlanEdit.from_payload(record)
            except (KeyError, TypeError, ValueError):
                logger.warning("A step edit in the session file could not be read and was skipped: %r", record)
                continue
            if not edit.is_empty():
                edits[edit.key] = edit  # a duplicate of the same step: the last one wins
        return cls(edits=tuple(sorted(edits.values(), key=lambda e: (e.step, e.measured_start_unix_ms))))
