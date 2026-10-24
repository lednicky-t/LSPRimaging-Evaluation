"""Mask stage settings (sketch §7 "Image Tools" > Mask, §10).

``MaskSettings`` ported from the old app's ``domain/models.py``, extended
with the histogram-highlight range (``histogram_highlight_min_value``/
``histogram_highlight_max_value``) - moved out of the old
``PreprocessingSettings`` grab-bag (see
``image_tools/geometry/model.py``'s docstring) because it's the persisted
state of the histogram-mask-candidate selection (see
``gui/mask_controller.py``'s ``current_histogram_highlight_mask_raw`` on
``develop``/``main``) - a Mask concern, not a Geometry one.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass
from functools import lru_cache

import numpy as np


@dataclass(frozen=True)
class MaskComputationalChange:
    """Mask's own computational-change payload (change_events.py's
    two-type pattern, §3) - whole-image scope like
    `GeometryComputationalChange`/`BackgroundComputationalChange`, no
    `roi_ids` field. Only a committed mask change is computational - it's
    what `ignored_pixel_mask`/`flatten_background` actually read. Tool-
    tuning settings and the histogram-highlight selection are
    `MaskCosmeticChange` instead (see that type's docstring).

    `frame`/`scope` added 2026-09-21 for the timeline-based mask storage
    (see `MaskChange`'s docstring and the rewrite build log's matching
    entry) - without them a subscriber has no way to know *which* frame(s)
    a change affects: a `"persistent"` change at cube 3 means "cube 3
    onward may be stale", an `"individual"` one means "just this one
    frame". No real subscriber exists yet (`analysis/tasks.py` isn't
    built), but this is free information available at emit time, the same
    reasoning that justified `RoiToolbox.roi_ids_renumbered` before
    anything subscribed to it."""

    reason: str  # "mask_change" | "session_restored" | "cleared" | "mask_batch"
    frame: tuple[int, float] | None
    scope: str | None  # "individual" | "persistent"
    frames: tuple[tuple[int, float], ...] | None = None
    """`"mask_batch"` only (2026-10-17, `MaskModule.set_mask_changes`: the Auto mask step writes hundreds of masks as one
    step): the frames whose masks changed. `frame` and `scope` are then `None` ("every frame may have changed") for a subscriber
    that does not know `frames`; one that does may narrow its work to them."""
    """`None` on both means *every* frame may have changed - emitted by
    `MaskModule.restore_state` (replaces the whole timeline at once, added
    2026-09-23 for session loading) and `MaskModule.clear_all_masks`
    (wipes the whole timeline, added 2026-10-02). A subscriber that narrows
    its work by frame must treat `None` as "no narrowing possible, redo
    everything", which is correct: after either event nothing it computed
    against the previous timeline is still trustworthy."""


@dataclass(frozen=True)
class MaskCosmeticChange:
    """Mask's cosmetic-change payload. `relative`/`local_contrast`/
    `morphology`/`histogram` tool-tuning settings and the histogram-
    highlight drag selection don't themselves invalidate any stored result
    - unlike Geometry's crop/rotate/flip (which apply continuously, every
    render), a Mask tool's settings only affect anything once an explicit
    "apply" action merges a computed candidate into a mask change. Until
    then, changing a threshold slider is exactly like dragging Geometry's
    measurement ruler - a preview input, not a result-affecting one."""

    reason: str  # "tool_settings" | "histogram_highlight"


def _decode_plane(packed: bytes, height: int, width: int) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(zlib.decompress(packed), dtype=np.uint8), count=height * width)
    return bits.astype(bool).reshape((height, width))


@lru_cache(maxsize=32)
def _decode_mask(packed: bytes, height: int, width: int) -> np.ndarray:
    plane = _decode_plane(packed, height, width)
    plane.setflags(write=False)  # shared between every caller that asks for the same mask: nobody modifies it in place
    return plane


class MaskChange:
    """One committed ignore-mask edit, timeline-tagged (2026-09-21 mask/ROI
    design conversation - see the rewrite build log's matching entry for
    the full reasoning `MaskModule` implements this against).

    `frame` is the `(cube_index, wavelength_nm)` this mask was *authored*
    at - not normalized back to the reference frame. Scientifically, what
    matters is where the edit happened; chromatic correction (`ChromaticModule.
    warp_mask_between`) can always re-express it in any other frame's
    geometry on demand, so there's no need to force every edit through the
    reference frame just to store it.

    `scope` is `"individual"` (applies to this exact `frame` only) or
    `"persistent"` (applies to `frame`'s whole cube, and every cube after it,
    until a later persistent change supersedes it - cube granularity only,
    no per-wavelength splitting of persistence). Persistent changes are
    stored as full replacements, not diffs, so each one can be verified
    independently and a lost/corrupted change can't silently corrupt every
    mask downstream of it.

    **The pixels are kept compressed** (2026-10-17): a full-plane boolean mask is 1.2 MB at 900 x 1300, and the Auto mask step
    makes hundreds of them (the test dataset has 35 particle passages of ~7 frames), so the mask is stored as zlib-compressed
    packed bits (a few KB) and decoded on access. `.mask` is the full boolean array as before - **read-only**, shared through a
    small cache; copy it before changing it. Constructing a `MaskChange` copies the pixels, so the caller's array is never
    shared. Equal pixels at the same frame and scope compare equal, which is what `set_mask_change`'s no-op check uses.
    """

    __slots__ = ("frame", "scope", "shape", "_packed")

    def __init__(self, frame: tuple[int, float], scope: str, mask: np.ndarray) -> None:
        plane = np.asarray(mask, dtype=bool)
        if plane.ndim != 2:
            raise ValueError(f"a mask must be 2-D, got shape {plane.shape}")
        self.frame = (int(frame[0]), float(frame[1]))
        self.scope = scope
        self.shape = (int(plane.shape[0]), int(plane.shape[1]))
        self._packed = zlib.compress(np.packbits(plane.ravel(order="C")).tobytes(), 1)  # level 1: ~1 ms, within a few % of level 9

    @property
    def mask(self) -> np.ndarray:
        return _decode_mask(self._packed, self.shape[0], self.shape[1])

    def plane_uncached(self) -> np.ndarray:
        """The same pixels as `.mask`, decoded afresh: a writable array that is the caller's own and does not go through the
        shared 32-entry cache. For callers that decode hundreds of masks in one go (Auto mask, the session writer, the PNG
        export): through the cache they would push out the masks the Image panel is showing (1.75 ms each to decode again)."""
        return _decode_plane(self._packed, self.shape[0], self.shape[1])

    def content_digest(self) -> bytes:
        """A 16-byte digest of the pixels (and the shape), computed from the stored bits *without decoding them to a plane* (~0.3 ms, a decode is
        ~2 ms). Equal pixels give an equal digest wherever the mask came from: it hashes the packed bits themselves (``np.packbits`` of the
        plane in C order), not the compressed bytes, which a different zlib could write differently. The scan's staleness fingerprint uses it
        (`analysis/scan_fingerprint.py`, `authored_mask_digest` gives the same value for a plane)."""
        return hashlib.blake2b(zlib.decompress(self._packed) + str(self.shape).encode(), digest_size=16).digest()

    @property
    def stored_bytes(self) -> int:
        """What this change holds in memory (the compressed pixels)."""
        return len(self._packed)

    def moved(self, frame: tuple[int, float], scope: str) -> MaskChange:
        """The same pixels at another frame / scope, without decoding them (a persistent mask made individual and back)."""
        other = object.__new__(MaskChange)
        other.frame = (int(frame[0]), float(frame[1]))
        other.scope = scope
        other.shape = self.shape
        other._packed = self._packed
        return other

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, MaskChange)
            and self.frame == other.frame
            and self.scope == other.scope
            and self.shape == other.shape
            and self._packed == other._packed
        )

    def __hash__(self) -> int:
        return hash((self.frame, self.scope, self.shape, self._packed))

    def __repr__(self) -> str:
        return f"MaskChange(frame={self.frame}, scope={self.scope!r}, shape={self.shape}, stored_bytes={len(self._packed)})"


@dataclass(slots=True)
class MaskSettings:
    # Histogram-based mask (intensity ranges)
    histogram_min_value: float | None = None
    histogram_max_value: float | None = None

    # Figure-based mask (spatial tools)
    relative_threshold_fraction: float = 0.18
    relative_profile_sigma_px: float = 48.0
    local_contrast_sigma_px: float = 8.0
    local_contrast_z_threshold: float = 3.0

    # Morphology settings
    morphology_radius_px: int = 2

    # Drawing settings
    brush_size_px: int = 12  # brush diameter (passed to paint_brush as `diameter_px`)

    # New mask system state
    histogram_enabled: bool = False
    histogram_mask: np.ndarray | None = None
    figure_enabled: bool = False
    figure_mask: np.ndarray | None = None

    # Persisted histogram-highlight selection (moved from PreprocessingSettings)
    histogram_highlight_min_value: float | None = None
    histogram_highlight_max_value: float | None = None
