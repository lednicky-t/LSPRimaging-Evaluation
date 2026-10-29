"""The saved shape of the user's edits to the experiment steps (``ExperimentPlanModule``): plain dataclasses and their JSON payload, no Qt.

The measured steps belong to the dataset and are never written. What the user does to them lives in the session file (``storage/session.py``, key ``"plan"``; the first
four kinds of edit since schema 1.5, the rest since 1.6):

- an **edit** of a measured step (moved boundary, own note, own colour). Every edit carries a fingerprint of the measured step it was made on (its number, measured start and
  measured note). If the dataset's steps change later (notes re-imported, another plan file), an edit whose fingerprint no longer matches is **not applied** and is reported; it is
  never moved onto a different step (``edits.apply_edits``). Only what the user changed is stored: a field equal to the measured value is not an edit.
- a measured step the user **removed** (its fingerprint). Its edit, if any, is kept, so restoring it brings it back as it was. A step the user **unified** with its neighbour is the first
  step's end moved plus the others removed; there is no record of its own.
- a step the user **added** (a ``uid`` given once and never reused, its start and end, note and colour). It has no measured counterpart.
- the **evaluation pairs**: which step is the baseline and which is the signal of pair 1, 2, ... (shown as ``B1`` / ``S1`` on the bar). A step is named by its *reference*:
  ``("m", step, measured_start, measured_note)`` for a measured step, ``("a", uid)`` for an added one. Other panels keep a reference, never an index or a number: the number a user
  sees is the step's place in time, and that changes when a step is added or removed.

Everything beyond ``"edits"`` is written only when there is something to write, so a session without structure edits is saved byte for byte as in schema 1.5.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

StepRef = tuple  # ("m", step, measured_start_unix_ms, measured_label) | ("a", uid)


def normalise_color(text: object) -> str | None:
    """``"#rgb"`` / ``"#rrggbb"`` in any case -> ``"#rrggbb"`` in lower case; anything else (a name, ``None``, junk) -> None."""
    if not isinstance(text, str) or not _COLOR.match(text.strip()):
        return None
    digits = text.strip()[1:].lower()
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return "#" + digits


def measured_ref(step: int, measured_start_unix_ms: int, measured_label: str) -> StepRef:
    return ("m", int(step), int(measured_start_unix_ms), str(measured_label))


def added_ref(uid: int) -> StepRef:
    return ("a", int(uid))


def ref_to_payload(ref: StepRef) -> dict:
    if ref[0] == "a":
        return {"added": int(ref[1])}
    return {"step": int(ref[1]), "measured_start_unix_ms": int(ref[2]), "measured_label": str(ref[3])}


def ref_from_payload(raw: object) -> StepRef:
    """Raises ``ValueError`` / ``TypeError`` / ``KeyError`` for a record that cannot be read (the caller skips it)."""
    if not isinstance(raw, dict):
        raise TypeError("a step reference must be an object")
    if "added" in raw:
        return added_ref(int(raw["added"]))
    return measured_ref(int(raw["step"]), int(raw["measured_start_unix_ms"]), str(raw["measured_label"]))


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
class AddedStep:
    """A step the user made. No measured counterpart: its times, note and colour are stored as they are."""

    uid: int
    start_unix_ms: int
    end_unix_ms: int
    label: str = ""
    color: str | None = None

    def to_payload(self) -> dict:
        payload: dict = {"uid": self.uid, "start_unix_ms": self.start_unix_ms, "end_unix_ms": self.end_unix_ms, "label": self.label}
        if self.color is not None:
            payload["color"] = self.color
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> "AddedStep":
        """Raises ``ValueError`` / ``TypeError`` / ``KeyError`` for a record that cannot be read (the caller skips it)."""
        if not isinstance(raw, dict):
            raise TypeError("an added step must be an object")
        step = cls(
            uid=int(raw["uid"]),
            start_unix_ms=int(raw["start_unix_ms"]),
            end_unix_ms=int(raw["end_unix_ms"]),
            label=" ".join(str(raw.get("label") or "").split()),
            color=normalise_color(raw.get("color")),
        )
        if step.uid < 1 or step.end_unix_ms <= step.start_unix_ms:
            raise ValueError(f"added step {step.uid} is not a step (uid from 1, end after start)")
        return step


@dataclass(frozen=True)
class EvalPair:
    """Pair `number` (1, 2, ...): the step that is the baseline and the step that is the signal of an evaluation. Either may be unset."""

    number: int
    baseline: StepRef | None = None
    signal: StepRef | None = None

    def to_payload(self) -> dict:
        payload: dict = {"number": self.number}
        if self.baseline is not None:
            payload["baseline"] = ref_to_payload(self.baseline)
        if self.signal is not None:
            payload["signal"] = ref_to_payload(self.signal)
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> "EvalPair":
        if not isinstance(raw, dict):
            raise TypeError("an evaluation pair must be an object")

        def optional_ref(name: str) -> StepRef | None:
            value = raw.get(name)
            if value is None:
                return None
            try:
                return ref_from_payload(value)
            except (KeyError, TypeError, ValueError):
                logger.warning("A %s step of evaluation pair %r in the session file could not be read and was dropped: %r", name, raw.get("number"), value)
                return None

        pair = cls(number=int(raw["number"]), baseline=optional_ref("baseline"), signal=optional_ref("signal"))
        if pair.number < 1:
            raise ValueError("an evaluation pair starts at 1")
        return pair


@dataclass(frozen=True)
class PlanEditsState:
    """All the user's changes to the plan of a session, in a fixed order so two equal states compare equal."""

    edits: tuple[PlanEdit, ...] = ()  # ordered by step, then by measured start
    removed: tuple[StepRef, ...] = ()  # measured steps taken away, ordered by fingerprint
    added: tuple[AddedStep, ...] = ()  # in the order they were made
    next_uid: int = 1  # the uid of the next added step: never reused, even after a restart
    pairs: tuple[EvalPair, ...] = ()  # ordered by number; a pair with nothing set is dropped

    def edit_for(self, key: tuple[int, int, str]) -> PlanEdit | None:
        return next((edit for edit in self.edits if edit.key == key), None)

    def pair(self, number: int) -> EvalPair | None:
        return next((p for p in self.pairs if p.number == number), None)

    def to_payload(self) -> dict:
        payload: dict = {"edits": [edit.to_payload() for edit in self.edits]}
        if self.removed:
            payload["removed"] = [ref_to_payload(ref) for ref in self.removed]
        if self.added or self.next_uid > 1:
            payload["added"] = [step.to_payload() for step in self.added]
            payload["next_uid"] = self.next_uid
        if self.pairs:
            payload["pairs"] = [pair.to_payload() for pair in self.pairs]
        return payload

    @classmethod
    def from_payload(cls, raw: object) -> "PlanEditsState":
        """Rebuild from a session file's ``"plan"`` block. A missing or unreadable block gives no changes (the right reading of a session written before the
        plan bar existed, or before steps could be added); a record that cannot be read is skipped with a warning and the others are kept. ``next_uid`` is never
        lower than one past the highest uid read, so a damaged counter cannot hand out a uid that is already taken."""
        if not isinstance(raw, dict):
            return cls()

        edits: dict[tuple[int, int, str], PlanEdit] = {}
        for record in raw.get("edits") if isinstance(raw.get("edits"), list) else []:
            try:
                edit = PlanEdit.from_payload(record)
            except (KeyError, TypeError, ValueError):
                logger.warning("A step edit in the session file could not be read and was skipped: %r", record)
                continue
            if not edit.is_empty():
                edits[edit.key] = edit  # a duplicate of the same step: the last one wins

        removed: dict[StepRef, None] = {}
        for record in raw.get("removed") if isinstance(raw.get("removed"), list) else []:
            try:
                ref = ref_from_payload(record)
            except (KeyError, TypeError, ValueError):
                logger.warning("A removed step in the session file could not be read and was skipped: %r", record)
                continue
            if ref[0] == "m":
                removed[ref] = None

        added: dict[int, AddedStep] = {}
        for record in raw.get("added") if isinstance(raw.get("added"), list) else []:
            try:
                step = AddedStep.from_payload(record)
            except (KeyError, TypeError, ValueError):
                logger.warning("An added step in the session file could not be read and was skipped: %r", record)
                continue
            added[step.uid] = step
        try:
            saved_next = int(raw.get("next_uid", 1))
        except (TypeError, ValueError):
            saved_next = 1

        pairs: dict[int, EvalPair] = {}
        for record in raw.get("pairs") if isinstance(raw.get("pairs"), list) else []:
            try:
                pair = EvalPair.from_payload(record)
            except (KeyError, TypeError, ValueError):
                logger.warning("An evaluation pair in the session file could not be read and was skipped: %r", record)
                continue
            if pair.baseline is not None or pair.signal is not None:
                pairs[pair.number] = pair

        return cls(
            edits=tuple(sorted(edits.values(), key=lambda e: (e.step, e.measured_start_unix_ms))),
            removed=tuple(sorted(removed, key=lambda r: r[1:])),
            added=tuple(added.values()),
            next_uid=max(saved_next, max(added, default=0) + 1, 1),
            pairs=tuple(sorted(pairs.values(), key=lambda p: p.number)),
        )
