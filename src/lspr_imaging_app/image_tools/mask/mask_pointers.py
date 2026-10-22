"""``MaskPointersModule`` - the list of mask pointers a transient scan found (2026-10-17; renamed from "transient events" on
2026-10-22, so the code says what the Mask rows show). Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

A mask pointer (`MaskPointer`; it wraps the `TransientEvent` the scan detected) is a **pointer**, not a mask: the scan marks the frames where something dark and sharp crossed the field (a particle, a
hair, a bubble) and this module keeps the list; it never touches `MaskModule`. A later step ("Auto mask") reads the list and makes
the masks. So everything here is **cosmetic** (CLAUDE.md "Communication"): `pointers_changed` means "redraw the marks", never
"a value may be wrong", and nothing recomputes.

The user can **dismiss** an event (a false alarm): it stays in the list, drawn faintly, and the masking step skips it. A new scan
keeps the dismissals of events that come back at the same place, and a scan of a cube range only replaces the events inside the
range. The list is part of the session (`mask_pointers_state.py` is its saved shape).

Not undoable: the list is derived data that can be made again; dismissing is one click away from being restored.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from .transient_detection import TransientEvent
from .mask_pointers_state import MaskPointer, ScanInfo, MaskPointersState
from .transient_scan import ScanResult

logger = logging.getLogger(__name__)

_SAME_PLACE_PX = 15.0  # a new event this close to a dismissed one in the same frame is the same object


@dataclass(frozen=True)
class MaskPointersChange:
    reason: str  # "scan" | "cleared" | "dismissed" | "restored" | "session_restored"


class MaskPointersModule(QObject):
    pointers_changed = pyqtSignal(MaskPointersChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._info: ScanInfo | None = None
        self._records: tuple[MaskPointer, ...] = ()
        self._by_cube: dict[int, tuple[MaskPointer, ...]] | None = None  # built on demand, dropped on every change

    # -- queries -------------------------------------------------------------------------------------------------

    def state(self) -> MaskPointersState:
        return MaskPointersState(info=self._info, records=self._records)

    def info(self) -> ScanInfo | None:
        return self._info

    def has_pointers(self) -> bool:
        return bool(self._records)

    def records(self) -> tuple[MaskPointer, ...]:
        return self._records

    def record(self, event_id: int) -> MaskPointer | None:
        return next((r for r in self._records if r.id == event_id), None)

    def counts(self) -> tuple[int, int]:
        """(events still standing, events dismissed)."""
        dismissed = sum(1 for r in self._records if r.dismissed)
        return len(self._records) - dismissed, dismissed

    def _cube_index(self) -> dict[int, tuple[MaskPointer, ...]]:
        if self._by_cube is None:
            grouped: dict[int, list[MaskPointer]] = {}
            for record in self._records:
                grouped.setdefault(record.event.frame[0], []).append(record)
            self._by_cube = {cube: tuple(records) for cube, records in grouped.items()}
        return self._by_cube

    def records_in_cube(self, cube: int) -> tuple[MaskPointer, ...]:
        """The events of one cube, in the order of the list (acquisition order). O(1) after a change."""
        return self._cube_index().get(int(cube), ())

    def records_in_frame(self, frame: tuple[int, float]) -> tuple[MaskPointer, ...]:
        return tuple(r for r in self.records_in_cube(frame[0]) if r.event.frame[1] == float(frame[1]))

    def pointer_cubes(self) -> dict[int, tuple[int, int]]:
        """Every cube that has an event -> (events standing, events dismissed)."""
        return {
            cube: (sum(1 for r in records if not r.dismissed), sum(1 for r in records if r.dismissed))
            for cube, records in self._cube_index().items()
        }

    def active_pointer_cubes(self) -> tuple[int, ...]:
        """The cubes with at least one event that is not dismissed, ascending: what the slideshow's "Events" mode steps through."""
        return tuple(sorted(cube for cube, (active, _dismissed) in self.pointer_cubes().items() if active))

    # -- commands ------------------------------------------------------------------------------------------------

    def _set(self, records: tuple[MaskPointer, ...], info: ScanInfo | None, reason: str) -> None:
        self._records = records
        self._info = info
        self._by_cube = None
        self.pointers_changed.emit(MaskPointersChange(reason))

    @instrumented("MaskPointersModule.set_scan_result")
    def set_scan_result(self, result: ScanResult) -> None:
        """Install what a scan found. A scan of the whole dataset replaces the list; a scan of a cube range replaces only the
        events inside that range. An event that comes back where a dismissed one was stays dismissed."""
        span = result.params.cube_range
        kept = [r for r in self._records if span is not None and not (span[0] <= r.event.frame[0] <= span[1])]
        dismissed_before = [r.event for r in self._records if r.dismissed]

        def was_dismissed(event: TransientEvent) -> bool:
            return any(
                old.frame == event.frame
                and abs(old.centroid_xy[0] - event.centroid_xy[0]) <= _SAME_PLACE_PX
                and abs(old.centroid_xy[1] - event.centroid_xy[1]) <= _SAME_PLACE_PX
                for old in dismissed_before
            )

        combined = [(r.event, r.dismissed) for r in kept] + [(e, was_dismissed(e)) for e in result.events]
        combined.sort(key=lambda pair: (pair[0].frame[0], pair[0].frame[1], -pair[0].area_px))
        records = tuple(MaskPointer(id=index + 1, event=event, dismissed=flag) for index, (event, flag) in enumerate(combined))
        det = result.params.detection
        settings = {
            **asdict(det),
            "stride": result.params.stride,
            "stride_offset": result.params.stride_offset,
            "cube_range": None if span is None else [int(span[0]), int(span[1])],
            "fill_gaps": result.params.fill_gaps,
            "fill_extend_frames": result.params.fill_extend_frames,
        }
        info = ScanInfo(
            created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            settings=settings,
            frames_in_range=result.frames_in_range,
            frames_screened=result.frames_screened,
            seconds=float(result.seconds),
            unreadable_frames=len(result.failed_frames),
        )
        self._set(records, info, "scan")

    @instrumented("MaskPointersModule.clear")
    def clear(self) -> None:
        if not self._records and self._info is None:
            return
        self._set((), None, "cleared")

    def _flag(self, ids: Iterable[int], dismissed: bool, reason: str) -> int:
        wanted = {int(i) for i in ids}
        changed = 0
        updated = []
        for record in self._records:
            if record.id in wanted and record.dismissed != dismissed:
                record = MaskPointer(id=record.id, event=record.event, dismissed=dismissed)
                changed += 1
            updated.append(record)
        if changed:
            self._set(tuple(updated), self._info, reason)
        return changed

    @instrumented("MaskPointersModule.dismiss")
    def dismiss(self, ids: Iterable[int]) -> int:
        """Mark events as false alarms; returns how many changed."""
        return self._flag(ids, True, "dismissed")

    @instrumented("MaskPointersModule.restore")
    def restore(self, ids: Iterable[int]) -> int:
        """Take the dismissal back; returns how many changed."""
        return self._flag(ids, False, "restored")

    def restore_state(self, state: MaskPointersState) -> None:
        """Replace the whole list (a session was opened). Not an undoable command, like every `restore_*`."""
        self._set(tuple(state.records), state.info, "session_restored")
