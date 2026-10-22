"""From the scan's pointers to masks (2026-10-17): which events the "Auto mask" step masks and what mask each frame gets. Pure numpy,
no Qt. Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

The scan only *points* (`MaskPointersModule`); this is the separate step that makes masks. For every frame that has a particle
event that is still standing, the mask is

    the mask already in effect at that frame   (merged by default: the persistent mask, warped into the frame, or the frame's own
                                                individual mask)
    OR the contour of every particle event of the frame, grown by N pixels   (N is the user's, the contour itself is the
                                                half-depth edge the scan found)

and it is stored as an *individual* mask at that frame. Which events count: `particle` kind, not dismissed, with a contour.
`global` events (a liquid front, a soft shadow) and `artefact` events (something at the image border that goes nowhere) are pointers
only, never masked. A particle that arrived and **rests** is a `particle` and is masked in every frame it is seen in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from .model import MaskChange
from .transient_detection import KIND_PARTICLE
from .mask_pointers_state import MaskPointer

Frame = tuple[int, float]


def maskable(records: Iterable[MaskPointer], include_inferred: bool = True) -> list[MaskPointer]:
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


def group_by_frame(records: Iterable[MaskPointer]) -> dict[Frame, list[MaskPointer]]:
    """Records grouped by frame, in acquisition order."""
    grouped: dict[Frame, list[MaskPointer]] = {}
    for record in records:
        grouped.setdefault(record.event.frame, []).append(record)
    return dict(sorted(grouped.items()))


@dataclass(frozen=True)
class BaseMask:
    """What is already in effect at a frame, described without touching a pixel (2026-10-17, audit B1): the stored mask that applies
    there (still compressed) and the 2x3 matrix that carries it into that frame's geometry (``None``: it was authored at that very
    frame, or the matrix is the identity). Cheap to make on the GUI thread, where the mask and chromatic modules live; the
    decoding and warping (`materialize_base`) is the expensive part and runs wherever the caller likes."""

    change: MaskChange
    affine: np.ndarray | None = None


class BaseDecoder:
    """Decodes the `MaskChange`s of many `BaseMask`es, remembering the last few planes: consecutive frames mostly share one
    persistent mask, so it is decoded once, not once per frame. Not thread-safe; one per worker."""

    def __init__(self, keep: int = 4) -> None:
        self._keep = keep
        self._planes: dict[int, tuple[MaskChange, np.ndarray]] = {}  # id(change) -> (change, plane); the change keeps its id alive

    def plane(self, change: MaskChange) -> np.ndarray:
        """The pixels of `change`: shared with this decoder, so **do not modify** (`materialize_base` copies)."""
        entry = self._planes.get(id(change))
        if entry is None:
            entry = (change, change.plane_uncached())
            if len(self._planes) >= self._keep:
                self._planes.pop(next(iter(self._planes)))
            self._planes[id(change)] = entry
        return entry[1]


def materialize_base(
    base: BaseMask | None,
    warp: Callable[[np.ndarray, np.ndarray], np.ndarray],
    decoder: BaseDecoder | None = None,
) -> np.ndarray | None:
    """The mask in effect, as a full-plane boolean array that is the caller's own to modify (``None``: nothing is in effect).
    `warp(mask, matrix)` is the chromatic warp (``chromatic.warp.warp_boolean_mask_affine``): passed in so that this module never
    imports from Chromatic. Pure; safe on any thread."""
    if base is None:
        return None
    decoder = decoder or BaseDecoder()
    plane = decoder.plane(base.change)
    if base.affine is None:
        return plane.copy()
    return warp(plane, base.affine)


def frame_mask(
    records: Sequence[MaskPointer],
    shape: tuple[int, int],
    dilate_px: int,
    base: np.ndarray | None = None,
    *,
    copy_base: bool = True,
) -> np.ndarray:
    """The full-plane mask of one frame: `base` (what is in effect there; a copy is made, `base` is never changed, unless
    ``copy_base=False``: the caller hands over an array of its own and it is changed in place) OR the contour of each of `records`
    grown by `dilate_px`. A `base` of another shape than `shape` is ignored (a dataset swapped under a session)."""
    if base is not None and base.shape == tuple(shape):
        plane = np.array(base, dtype=bool, copy=True) if copy_base or base.dtype != np.bool_ else base
    else:
        plane = np.zeros(shape, dtype=bool)
    for record in records:
        contour = record.event.contour
        if contour is not None:
            contour.grown(dilate_px, shape).paste_or(plane)
    return plane


def build_masks(
    records: Iterable[MaskPointer],
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
