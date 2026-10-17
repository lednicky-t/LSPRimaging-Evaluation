"""Find dark, sharp objects that are *new in one frame*: a particle, a hair or a bubble crossing the field while one wavelength
is being captured (2026-10-16, maintainer request: an automatic mode for the Mask tab). Pure numpy/OpenCV, no Qt, no dataset:
the caller hands in frames. Design and measurements: `docs/auto_transient_mask_exploration_2026-10-16.md`, plan and decisions:
`docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

**The idea.** A particle is in a different place at every wavelength (it moves ~170-260 px per frame, further than its own
length), so nothing about a frame itself says "this is a particle": the disk array is dark too. What does say it is the *same
wavelength in another cube* (about 10 s earlier or later): the array, the illumination and the chromatic shift are the same,
only the transient is new. So for a frame `I` and a reference `R` (that wavelength in a neighbouring cube):

    r = I / R, divided by its own median over the valid area      (the transmittance of whatever is new; the median cancels
                                                                   lamp / exposure drift)
    d = 1 - r                                                      (depth: 0 = nothing new, 0.9 = 90 % of the light gone)
    sigma = 1.4826 * MAD(d) over the valid area                    (the frame's own noise, robust to the object itself)

**Stage 1, screening** (4x4-binned frames, `screen_frame`): a region is an object when its deepest point is at least
`sigma_factor * sigma` deep (default 60). Measured on 300 clean frames, the deepest pixel reached 9 sigma (median), 30 (99th
percentile) and 44 (maximum); the real passages are 102-204 sigma in every frame. Binning is the smoothing: it costs 3 ms a
frame where the full-resolution version cost 53-78, and finds the same passages (only the 3-20 px specks go).

**Stage 2, contour** (full-resolution frames of the hits only, `contour_objects`): the contour of an object is the level halfway
down *its own* dip (full width at half depth: where a blurred edge really is), not a global threshold, so a faint particle and a
black hair each get their own edge. The user's extra dilation is applied later, by whoever makes the mask.

A reference that holds a particle at the same place makes the ratio *bright* there, so it can only hide an object, never invent
one. Stage 1 uses one reference (cheap); stage 2 uses the brighter of the two neighbours per pixel (`confirmation_reference`),
which survives a particle in either one.

Everything is in the frame's own raw pixel space (masks are authored there, CLAUDE.md "Masks are authored and stored in raw
pixel space").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import cv2
import numpy as np

KIND_PARTICLE = "particle"  # compact, area <= `max_particle_area_px`: the kind the Auto mask step masks
KIND_GLOBAL = "global"  # larger (a liquid front, a bubble edge, a soft shadow): a pointer only
KIND_ARTEFACT = "artefact"  # did not move between frames, or sits alone on the image border: not a passage, never masked


@dataclass(frozen=True)
class DetectionParams:
    sigma_factor: float = 60.0  # an object's deepest point must be this many sigma deep
    weak_sigma_factor: float = 30.0  # screening only: a frame with a region this deep is worth a look at its neighbours (a faint particle sits just under 60 in some frames); it makes no event by itself
    min_area_px: int = 16  # smaller objects (at full resolution) are noise specks
    max_particle_area_px: int = 3000  # larger objects are `KIND_GLOBAL` (largest real particle seen: ~900 px)
    bin_factor: int = 4  # screening resolution; 1 = full resolution
    valid_blur_px: float = 32.0  # the valid area is judged on the reference blurred over this much: wider than a dark disk
    valid_level: float = 0.45  # ... and is where that blurred reference is above this fraction of its 90th percentile
    contour_depth_fraction: float = 0.5  # the contour level, as a fraction of the object's own depth
    contour_smooth_px: float = 1.0  # Gaussian sigma applied to the full-resolution ratio before the contour is cut
    window_margin_px: int = 16  # how far around a screened object the contour is searched
    max_candidates: int = 50  # per frame; a frame with more is in trouble anyway, the biggest are kept
    fill_sigma_factor: float = 20.0  # gap filling: a weaker object (this many sigma) is accepted, but only where a passage predicts it
    fill_search_base_px: float = 40.0  # ... within this + `fill_search_fraction` x the passage's step of the predicted spot
    fill_search_fraction: float = 0.25


@dataclass(frozen=True)
class Candidate:
    """An object found by the screening, in full-resolution pixel units."""

    area_px: int
    centroid_xy: tuple[float, float]
    bbox_xyxy: tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)
    depth: float
    z: float  # depth / sigma of the frame


@dataclass(frozen=True)
class FrameScreen:
    sigma: float  # the frame's own noise (of the ratio, at screening resolution)
    gain: float  # the median ratio that was divided out
    valid_fraction: float
    candidates: tuple[Candidate, ...]
    weak_hit: bool = False  # something at `weak_sigma_factor` (or the candidates): worth looking at the neighbouring frames

@dataclass(frozen=True)
class ContourMask:
    """A contour as a small boolean array and where it sits (compact: a hair is ~70 x 40 px, not 900 x 1300)."""

    x0: int
    y0: int
    mask: np.ndarray  # bool, bbox-sized

    @property
    def area_px(self) -> int:
        return int(self.mask.sum())

    def grown(self, dilate_px: int, shape: tuple[int, int]) -> ContourMask:
        """This contour grown by `dilate_px` pixels (a disk), clipped to the image `shape` (height, width). Works on the small
        box, not the whole plane, so growing hundreds of contours stays cheap."""
        pad = max(int(dilate_px), 0)
        height, width = self.mask.shape
        if pad == 0:
            return self
        padded = np.zeros((height + 2 * pad, width + 2 * pad), dtype=np.uint8)
        padded[pad : pad + height, pad : pad + width] = self.mask
        size = 2 * pad + 1
        bigger = cv2.dilate(padded, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))) > 0
        x0, y0 = self.x0 - pad, self.y0 - pad
        cx0, cy0 = max(x0, 0), max(y0, 0)
        cx1, cy1 = min(x0 + bigger.shape[1], shape[1]), min(y0 + bigger.shape[0], shape[0])
        if cx1 <= cx0 or cy1 <= cy0:
            return ContourMask(x0=cx0, y0=cy0, mask=np.zeros((0, 0), dtype=bool))
        return ContourMask(x0=cx0, y0=cy0, mask=bigger[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0].copy())

    def paste_or(self, plane: np.ndarray) -> None:
        """OR this contour into `plane` (a full-plane boolean array, changed in place), clipped to the plane."""
        height, width = self.mask.shape
        y1, x1 = min(self.y0 + height, plane.shape[0]), min(self.x0 + width, plane.shape[1])
        if y1 > self.y0 and x1 > self.x0 and self.x0 >= 0 and self.y0 >= 0:
            plane[self.y0 : y1, self.x0 : x1] |= self.mask[: y1 - self.y0, : x1 - self.x0]

    def to_array(self, shape: tuple[int, int], dilate_px: int = 0) -> np.ndarray:
        """The contour as a full-plane boolean mask of `shape` (height, width), optionally grown by `dilate_px` pixels."""
        full = np.zeros(shape, dtype=bool)
        self.grown(dilate_px, shape).paste_or(full)
        return full


@dataclass(frozen=True)
class TransientEvent:
    frame: tuple[int, float]  # (cube index, wavelength nm)
    kind: str
    area_px: int
    centroid_xy: tuple[float, float]
    bbox_xyxy: tuple[int, int, int, int]
    depth: float
    z: float
    contour: ContourMask | None = None  # particles and artefacts; None for a global event
    frame_sigma: float = 0.0  # the frame's own noise; a high value (>~1 %, typical 0.5 %) marks a disturbed frame, treat its objects with care
    passage_id: int | None = None  # events of consecutive frames that moved like one particle share this
    step_px: float | None = None  # how far it moved from the previous / to the next frame of its passage
    inferred: bool = False  # found by gap filling: too faint for the normal threshold, accepted because a passage predicted it


# -- small building blocks ------------------------------------------------------------------------------------------


def bin_frame(frame: np.ndarray, factor: int) -> np.ndarray:
    """`frame` as float32, averaged over `factor` x `factor` pixels (area average; the last partial block is dropped)."""
    plane = np.asarray(frame, dtype=np.float32)
    if factor <= 1:
        return plane
    height, width = plane.shape
    return cv2.resize(plane, (width // factor, height // factor), interpolation=cv2.INTER_AREA)


def valid_area(reference: np.ndarray, params: DetectionParams, factor: int) -> np.ndarray:
    """Where the sample is: `reference` (already binned by `factor`) blurred over `valid_blur_px`, above a fraction of its
    90th percentile, then eroded a little. Judged on the *blurred* reference on purpose: pixel by pixel, a disk that absorbs
    most of the light at some wavelength would count as "outside the sample" (the first attempt lost 66 % of the field at
    600 nm). The border of the image is not eroded, so an object leaving the field is still seen."""
    blurred = cv2.GaussianBlur(reference, (0, 0), max(params.valid_blur_px / max(factor, 1), 1.0))
    inside = (blurred > params.valid_level * float(np.percentile(blurred, 90))).astype(np.uint8)
    return cv2.erode(inside, np.ones((5, 5), np.uint8)).astype(bool)


def ratio_image(frame: np.ndarray, reference: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, float]:
    """`frame / reference` divided by its median over `valid`; returns the ratio image and the gain that was divided out."""
    ratio = frame / np.maximum(reference, 1.0)
    sample = ratio[::2, ::2][valid[::2, ::2]]
    gain = float(np.median(sample)) if sample.size >= 64 else 1.0
    if not np.isfinite(gain) or gain <= 0.0:
        gain = 1.0
    return ratio / gain, gain


def robust_sigma(depth_in_valid: np.ndarray) -> float:
    """1.4826 x the median absolute deviation: the noise of a distribution with a few outliers (the object) in it."""
    if depth_in_valid.size == 0:
        return 1.0
    centre = float(np.median(depth_in_valid))
    return max(1.4826 * float(np.median(np.abs(depth_in_valid - centre))), 1e-4)


def confirmation_reference(before: np.ndarray | None, after: np.ndarray | None) -> np.ndarray:
    """The brighter of the two neighbouring cubes' frames, per pixel: a particle in one of them is dark there, so the other
    wins. With only one neighbour (the first or last cube) that one is used."""
    if before is None and after is None:
        raise ValueError("a reference needs at least one neighbouring frame")
    if before is None:
        return np.asarray(after, dtype=np.float32)
    if after is None:
        return np.asarray(before, dtype=np.float32)
    return np.maximum(np.asarray(before, dtype=np.float32), np.asarray(after, dtype=np.float32))


# -- stage 1 --------------------------------------------------------------------------------------------------------


def screen_frame(
    frame: np.ndarray, reference: np.ndarray, params: DetectionParams, scale_xy: tuple[float, float]
) -> FrameScreen:
    """Stage 1. `frame` and `reference` are already binned (`bin_frame`) the same way; `scale_xy` is the full-resolution
    pixels per binned pixel (so areas, centroids and boxes come out in full-resolution units)."""
    valid = valid_area(reference, params, params.bin_factor)
    valid_fraction = float(valid.mean())
    if valid_fraction <= 0.0:
        return FrameScreen(sigma=1.0, gain=1.0, valid_fraction=0.0, candidates=())
    ratio, gain = ratio_image(frame, reference, valid)
    depth = 1.0 - ratio
    sigma = robust_sigma(depth[valid])
    deep = ((depth >= params.sigma_factor * sigma) & valid).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(deep, connectivity=8)
    sx, sy = scale_xy
    weak_hit = False
    if params.weak_sigma_factor < params.sigma_factor:
        weak = ((depth >= params.weak_sigma_factor * sigma) & valid).astype(np.uint8)
        weak_count, _weak_labels, weak_stats, _ = cv2.connectedComponentsWithStats(weak, connectivity=8)
        weak_hit = any(weak_stats[i, cv2.CC_STAT_AREA] * sx * sy >= params.min_area_px for i in range(1, weak_count))
    found: list[Candidate] = []
    for index in range(1, count):
        x, y, w, h, bins = (int(v) for v in stats[index])
        area = int(round(bins * sx * sy))
        if area < params.min_area_px:
            continue
        peak = float(depth[y : y + h, x : x + w][labels[y : y + h, x : x + w] == index].max())
        found.append(
            Candidate(
                area_px=area,
                centroid_xy=(float(centroids[index][0]) * sx + (sx - 1.0) / 2.0, float(centroids[index][1]) * sy + (sy - 1.0) / 2.0),
                bbox_xyxy=(int(x * sx), int(y * sy), int(np.ceil((x + w) * sx)), int(np.ceil((y + h) * sy))),
                depth=peak,
                z=peak / sigma,
            )
        )
    found.sort(key=lambda c: c.area_px, reverse=True)
    return FrameScreen(
        sigma=sigma, gain=gain, valid_fraction=valid_fraction, candidates=tuple(found[: params.max_candidates]),
        weak_hit=weak_hit or bool(found),
    )


def classify(candidate: Candidate, params: DetectionParams) -> str:
    return KIND_PARTICLE if candidate.area_px <= params.max_particle_area_px else KIND_GLOBAL


# -- stage 2 --------------------------------------------------------------------------------------------------------


def contour_objects(
    frame: np.ndarray,
    reference: np.ndarray,
    screen: FrameScreen,
    params: DetectionParams,
    frame_id: tuple[int, float],
) -> list[TransientEvent]:
    """Stage 2 for one hit frame: the half-depth contour of every particle-sized candidate of `screen`, on the
    full-resolution `frame` against the full-resolution `reference` (normally `confirmation_reference`). Returns one
    `KIND_PARTICLE` event per object found; an object that is not there at full resolution simply does not come back."""
    candidates = [c for c in screen.candidates if classify(c, params) == KIND_PARTICLE]
    if not candidates:
        return []
    height, width = frame.shape
    factor = max(params.bin_factor, 1)
    reference_binned = bin_frame(reference, factor)
    valid_binned = valid_area(reference_binned, params, factor)
    valid = cv2.resize(valid_binned.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
    ratio, _gain = ratio_image(frame, reference, valid)
    if params.contour_smooth_px > 0:
        ratio = cv2.GaussianBlur(ratio, (0, 0), params.contour_smooth_px)
    depth = 1.0 - ratio
    core_threshold = max(params.sigma_factor * screen.sigma, 0.05)

    claimed = np.zeros((height, width), dtype=bool)
    events: list[TransientEvent] = []
    for candidate in candidates:
        bx0, by0, bx1, by1 = candidate.bbox_xyxy
        margin = params.window_margin_px
        x0, y0 = max(bx0 - margin, 0), max(by0 - margin, 0)
        x1, y1 = min(bx1 + margin, width), min(by1 + margin, height)
        window = depth[y0:y1, x0:x1]
        window_valid = valid[y0:y1, x0:x1]
        core = ((window >= core_threshold) & window_valid).astype(np.uint8)
        count, core_labels, core_stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
        peaks = [(float(window[core_labels == k].max()), k) for k in range(1, count)]
        for peak, k in sorted(peaks, reverse=True):
            seed = core_labels == k
            if claimed[y0:y1, x0:x1][seed].any():
                continue  # already part of an object found from an overlapping window
            level = params.contour_depth_fraction * peak
            region = ((window >= level) & window_valid).astype(np.uint8)
            _n, region_labels = cv2.connectedComponents(region, connectivity=8)
            wanted = np.unique(region_labels[seed])
            wanted = wanted[wanted > 0]
            if wanted.size == 0:
                continue
            mask = np.isin(region_labels, wanted)
            area = int(mask.sum())
            if area < params.min_area_px:
                continue
            ys, xs = np.nonzero(mask)
            ax0, ax1, ay0, ay1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
            cropped = mask[ay0:ay1, ax0:ax1]
            claimed[y0:y1, x0:x1] |= mask
            events.append(
                TransientEvent(
                    frame=frame_id,
                    kind=KIND_PARTICLE,
                    area_px=area,
                    centroid_xy=(float(xs.mean()) + x0, float(ys.mean()) + y0),
                    bbox_xyxy=(x0 + ax0, y0 + ay0, x0 + ax1, y0 + ay1),
                    depth=peak,
                    z=peak / screen.sigma,
                    frame_sigma=screen.sigma,
                    contour=ContourMask(x0=x0 + ax0, y0=y0 + ay0, mask=cropped.copy()),
                )
            )
    return events


def global_events(screen: FrameScreen, params: DetectionParams, frame_id: tuple[int, float]) -> list[TransientEvent]:
    """The screening's large candidates as pointer-only events (no contour: nothing is going to mask them)."""
    return [
        TransientEvent(
            frame=frame_id,
            kind=KIND_GLOBAL,
            area_px=c.area_px,
            centroid_xy=c.centroid_xy,
            bbox_xyxy=c.bbox_xyxy,
            depth=c.depth,
            z=c.z,
            frame_sigma=screen.sigma,
        )
        for c in screen.candidates
        if classify(c, params) == KIND_GLOBAL
    ]


# -- linking ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkParams:
    stationary_px: float = 20.0  # moved less than this between consecutive frames (with a similar size): did not move
    min_step_px: float = 60.0  # the slowest passage seen moved ~100 px per frame
    max_step_px: float = 450.0  # the fastest ~260
    min_stationary_run: int = 3  # frames in a row at one place before it counts as an artefact (a lone coincidence in a dense stream does not)
    border_px: float = 12.0  # an unlinked object this close to the image border is an artefact (cube 0 has some in every corner)
    max_gap: int = 2  # gap filling bridges at most this many missing frames between two pieces of one passage
    gap_direction_cos: float = 0.7  # a bridged piece must move within about 45 degrees of the passage it joins


def link_passages(
    events: Sequence[TransientEvent],
    frame_order: Sequence[tuple[int, float]],
    link: LinkParams | None = None,
    image_shape: tuple[int, int] | None = None,
) -> list[TransientEvent]:
    """Give events of consecutive frames (in acquisition order, which runs across a cube boundary) a shared `passage_id` and
    a `step_px` when they moved like one flowing particle, and re-label as `KIND_ARTEFACT` what stayed at one place for
    `min_stationary_run` frames, or sits alone on the image border (`image_shape` = height, width). Objects of two
    consecutive frames are paired one to one, nearest first. Only for the tooltip and for telling artefacts from particles:
    nothing in the detection needs it. Other kinds pass through unchanged."""
    link = link or LinkParams()
    position = {frame: i for i, frame in enumerate(frame_order)}
    particles = [e for e in events if e.kind == KIND_PARTICLE and e.frame in position]
    by_position: dict[int, list[int]] = {}
    for index, event in enumerate(particles):
        by_position.setdefault(position[event.frame], []).append(index)

    moving = _UnionFind(len(particles))
    still = _UnionFind(len(particles))
    step: dict[int, float] = {}
    linked: set[int] = set()
    for pos, here in by_position.items():
        after = by_position.get(pos + 1, [])
        pairs = sorted(
            (float(np.hypot(particles[i].centroid_xy[0] - particles[j].centroid_xy[0], particles[i].centroid_xy[1] - particles[j].centroid_xy[1])), i, j)
            for i in here
            for j in after
        )
        used_here: set[int] = set()
        used_after: set[int] = set()
        for distance, i, j in pairs:
            if i in used_here or j in used_after:
                continue
            similar = min(particles[i].area_px, particles[j].area_px) >= 0.5 * max(particles[i].area_px, particles[j].area_px)
            if distance < link.stationary_px and similar:
                still.join(i, j)
            elif link.min_step_px <= distance <= link.max_step_px:
                moving.join(i, j)
                linked.update((i, j))
                step[i] = max(step.get(i, 0.0), distance)
                step[j] = max(step.get(j, 0.0), distance)
            else:
                continue
            used_here.add(i)
            used_after.add(j)

    artefact: set[int] = set()
    still_sizes = still.sizes()
    for i in range(len(particles)):
        if still_sizes[still.find(i)] >= link.min_stationary_run:
            artefact.add(i)
        elif image_shape is not None and i not in linked and _touches_border(particles[i], image_shape, link.border_px):
            artefact.add(i)

    moving_sizes = moving.sizes()
    passage_numbers: dict[int, int] = {}
    relabelled: dict[int, TransientEvent] = {}
    for i, event in enumerate(particles):
        if i in artefact:
            relabelled[i] = replace(event, kind=KIND_ARTEFACT)
        elif moving_sizes[moving.find(i)] > 1:
            number = passage_numbers.setdefault(moving.find(i), len(passage_numbers) + 1)
            relabelled[i] = replace(event, passage_id=number, step_px=step.get(i))
    result: list[TransientEvent] = []
    seen = 0
    for event in events:
        if event.kind == KIND_PARTICLE and event.frame in position:
            result.append(relabelled.get(seen, event))
            seen += 1
        else:
            result.append(event)
    return result


def _touches_border(event: TransientEvent, image_shape: tuple[int, int], margin: float) -> bool:
    height, width = image_shape
    x0, y0, x1, y1 = event.bbox_xyxy
    return x0 <= margin or y0 <= margin or x1 >= width - margin or y1 >= height - margin


class _UnionFind:
    def __init__(self, count: int) -> None:
        self._parent = list(range(count))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def join(self, i: int, j: int) -> None:
        self._parent[self.find(j)] = self.find(i)

    def sizes(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for i in range(len(self._parent)):
            counts[self.find(i)] = counts.get(self.find(i), 0) + 1
        return counts
