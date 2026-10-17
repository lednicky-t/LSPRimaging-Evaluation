"""From the scan's pointers to masks (2026-10-17): which events the "Auto mask" step masks and what mask each frame gets. Pure numpy,
no Qt. Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

The scan only *points* (`TransientEventsModule`); this is the separate step that makes masks. For every frame that has a particle
event that is still standing, the mask is

    the mask already in effect at that frame   (merged by default: the persistent mask, warped into the frame, or the frame's own
                                                individual mask)
    OR the contour of every particle event of the frame, grown by N pixels   (N is the user's, the contour itself is the
                                                half-depth edge the scan found)

and it is stored as an *individual* mask at that frame. Which events count: `particle` kind, not dismissed, with a contour.
`global` events (a liquid front, a soft shadow) and `artefact` events (they did not move) are pointers only, never masked.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

import numpy as np

from .transient_detection import KIND_PARTICLE
from .transient_events_state import EventRecord

Frame = tuple[int, float]


def maskable(records: Iterable[EventRecord], include_inferred: bool = True) -> list[EventRecord]:
    """The events the Auto mask step masks: particles that are not dismissed and still have their contour (and, if
    `include_inferred` is false, were not found by gap filling)."""
    return [
        r
        for r in records
        if r.event.kind == KIND_PARTICLE
        and not r.dismissed
        and r.event.contour is not None
        and (include_inferred or not r.event.inferred)
    ]


def group_by_frame(records: Iterable[EventRecord]) -> dict[Frame, list[EventRecord]]:
    """Records grouped by frame, in acquisition order."""
    grouped: dict[Frame, list[EventRecord]] = {}
    for record in records:
        grouped.setdefault(record.event.frame, []).append(record)
    return dict(sorted(grouped.items()))


def frame_mask(
    records: Sequence[EventRecord], shape: tuple[int, int], dilate_px: int, base: np.ndarray | None = None
) -> np.ndarray:
    """The full-plane mask of one frame: `base` (what is in effect there; a copy is made, `base` is never changed) OR the contour of
    each of `records` grown by `dilate_px`. A `base` of another shape than `shape` is ignored (a dataset swapped under a session)."""
    plane = np.array(base, dtype=bool, copy=True) if base is not None and base.shape == tuple(shape) else np.zeros(shape, dtype=bool)
    for record in records:
        contour = record.event.contour
        if contour is not None:
            contour.grown(dilate_px, shape).paste_or(plane)
    return plane


def build_masks(
    records: Iterable[EventRecord],
    shape: tuple[int, int],
    dilate_px: int,
    resolve_base: Callable[[Frame], np.ndarray | None],
) -> list[tuple[Frame, str, np.ndarray]]:
    """``(frame, "individual", mask)`` for every frame that has a maskable event, in acquisition order (what
    `MaskModule.set_mask_changes` takes). `resolve_base(frame)` gives the mask in effect there, already in that frame's geometry."""
    return [
        (frame, "individual", frame_mask(group, shape, dilate_px, resolve_base(frame)))
        for frame, group in group_by_frame(maskable(records)).items()
    ]
