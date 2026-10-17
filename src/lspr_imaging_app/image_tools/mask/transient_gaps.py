"""Gap filling for the transient scan (2026-10-17, maintainer request): find the frames of a passage that the normal threshold
missed. Pure numpy, no Qt. Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

A faint particle can be below the threshold in some frames of its passage (cube 267 of the test dataset: 3 of 5 frames reached 60
sigma, and stride 3 lost the whole passage). The passage still says where the particle must be: it moves ~100-260 px per frame in
a straight line. So where the pieces of a passage have a gap, or a passage could go on past its end, that frame is searched again
at a lower threshold (`DetectionParams.fill_sigma_factor`), but only near the predicted spot, and only for an object of a similar
size. The prediction is what makes the weaker threshold safe: the same threshold over the whole frame would find ghosts of the
array. What it finds is marked `TransientEvent.inferred`.

`find_gap_targets` decides *where to look* from the linked events; `fill_event` does the looking in one frame. The scan
(`transient_scan.py`) runs them in rounds, because a successful extension makes a new end to extend from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from .transient_detection import (
    KIND_PARTICLE,
    DetectionParams,
    LinkParams,
    TransientEvent,
    bin_frame,
    classify,
    contour_objects,
    screen_frame,
)

Frame = tuple[int, float]


@dataclass(frozen=True)
class GapTarget:
    frame: Frame
    predicted_xy: tuple[float, float]
    search_px: float
    source_area_px: int  # the area of the event the prediction comes from: what is found must be of a similar size

    @property
    def key(self) -> tuple[Frame, int, int]:
        return self.frame, int(round(self.predicted_xy[0])), int(round(self.predicted_xy[1]))


def search_radius(step_px: float, params: DetectionParams) -> float:
    return params.fill_search_base_px + params.fill_search_fraction * step_px


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def find_gap_targets(
    events: Sequence[TransientEvent],
    frame_order: Sequence[Frame],
    params: DetectionParams,
    link: LinkParams | None = None,
    image_shape: tuple[int, int] | None = None,
) -> list[GapTarget]:
    """Frames to search again, from the *linked* events (`link_passages` output): the missing frames between two pieces that
    move like one particle, and the frame just beyond each end of a passage with a known velocity (when that spot is inside the
    image, `image_shape` = height, width). A frame that already holds an event near the predicted spot is left out."""
    link = link or LinkParams()
    position = {frame: i for i, frame in enumerate(frame_order)}
    particles = [e for e in events if e.kind == KIND_PARTICLE and e.frame in position]
    if not particles:
        return []

    # Tracks: the events of one passage, or a lone event on its own.
    grouped: dict[object, list[TransientEvent]] = {}
    for index, event in enumerate(particles):
        grouped.setdefault(event.passage_id if event.passage_id is not None else ("alone", index), []).append(event)
    tracks: list[dict] = []
    for members in grouped.values():
        members.sort(key=lambda e: position[e.frame])
        first, last = members[0], members[-1]
        span = position[last.frame] - position[first.frame]
        velocity = None
        if len(members) >= 2 and span > 0:
            velocity = (
                (last.centroid_xy[0] - first.centroid_xy[0]) / span,
                (last.centroid_xy[1] - first.centroid_xy[1]) / span,
            )
        tracks.append({"first": first, "last": last, "velocity": velocity, "bridged_head": False, "bridged_tail": False})

    occupied: dict[Frame, list[TransientEvent]] = {}
    for event in events:
        occupied.setdefault(event.frame, []).append(event)

    targets: list[GapTarget] = []
    chosen: set = set()

    def add(target: GapTarget) -> None:
        taken = any(_distance(e.centroid_xy, target.predicted_xy) <= target.search_px for e in occupied.get(target.frame, []))
        if target.key not in chosen and not taken:
            chosen.add(target.key)
            targets.append(target)

    # Bridge: the end of one track and the start of another, a few missing frames apart, moving like one particle.
    pairs: list[tuple[float, int, int, tuple[float, float]]] = []
    for i, tail_track in enumerate(tracks):
        a = tail_track["last"]
        for j, head_track in enumerate(tracks):
            b = head_track["first"]
            gap = position[b.frame] - position[a.frame] - 1
            if i == j or not 1 <= gap <= link.max_gap:
                continue
            step = ((b.centroid_xy[0] - a.centroid_xy[0]) / (gap + 1), (b.centroid_xy[1] - a.centroid_xy[1]) / (gap + 1))
            speed = float(np.hypot(*step))
            if not link.min_step_px <= speed <= link.max_step_px:
                continue
            if min(a.area_px, b.area_px) < 0.3 * max(a.area_px, b.area_px):
                continue
            known = [v for v in (tail_track["velocity"], head_track["velocity"]) if v is not None]
            if any(step[0] * v[0] + step[1] * v[1] < link.gap_direction_cos * speed * float(np.hypot(*v)) for v in known):
                continue
            pairs.append((sum(_distance(step, v) for v in known), i, j, step))
    used_tail: set[int] = set()
    used_head: set[int] = set()
    for _deviation, i, j, step in sorted(pairs):
        if i in used_tail or j in used_head:
            continue
        used_tail.add(i)
        used_head.add(j)
        a, b = tracks[i]["last"], tracks[j]["first"]
        tracks[i]["bridged_tail"] = True
        tracks[j]["bridged_head"] = True
        radius = search_radius(float(np.hypot(*step)), params)
        for k in range(1, position[b.frame] - position[a.frame]):
            xy = (a.centroid_xy[0] + step[0] * k, a.centroid_xy[1] + step[1] * k)
            add(GapTarget(frame_order[position[a.frame] + k], xy, radius, min(a.area_px, b.area_px)))

    # Extend: one frame beyond the end of a passage whose velocity is known, if that spot is inside the image.
    for track in tracks:
        velocity = track["velocity"]
        if velocity is None:
            continue
        radius = search_radius(float(np.hypot(*velocity)), params)
        for edge, sign, bridged in (("last", 1, track["bridged_tail"]), ("first", -1, track["bridged_head"])):
            anchor = track[edge]
            index = position[anchor.frame] + sign
            if bridged or not 0 <= index < len(frame_order):
                continue
            xy = (anchor.centroid_xy[0] + sign * velocity[0], anchor.centroid_xy[1] + sign * velocity[1])
            if image_shape is not None and not (0 <= xy[0] < image_shape[1] and 0 <= xy[1] < image_shape[0]):
                continue
            add(GapTarget(frame_order[index], xy, radius, anchor.area_px))
    return targets


def fill_event(frame: np.ndarray, reference: np.ndarray, target: GapTarget, params: DetectionParams) -> TransientEvent | None:
    """Search `frame` (full resolution, `reference` normally `confirmation_reference`) for the particle `target` predicts, at the
    lower `fill_sigma_factor`: a screening at that threshold, the candidates within `search_px` of the predicted spot and of a
    size like the particle's, then the half-depth contour of the one nearest to it. `None` when there is nothing."""
    weak = replace(params, sigma_factor=params.fill_sigma_factor)
    factor = max(params.bin_factor, 1)
    own_binned = bin_frame(frame, factor)
    height, width = frame.shape
    screen = screen_frame(own_binned, bin_frame(reference, factor), weak, (width / own_binned.shape[1], height / own_binned.shape[0]))
    near = [
        c
        for c in screen.candidates
        if classify(c, weak) == KIND_PARTICLE
        and _distance(c.centroid_xy, target.predicted_xy) <= target.search_px
        and 0.2 * target.source_area_px <= c.area_px <= 5.0 * max(target.source_area_px, 1)
    ]
    if not near:
        return None
    found = [
        e
        for e in contour_objects(frame, reference, replace(screen, candidates=tuple(near)), weak, target.frame)
        if _distance(e.centroid_xy, target.predicted_xy) <= target.search_px
    ]
    if not found:
        return None
    return replace(min(found, key=lambda e: _distance(e.centroid_xy, target.predicted_xy)), inferred=True)
