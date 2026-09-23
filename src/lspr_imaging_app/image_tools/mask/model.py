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

from dataclasses import dataclass

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

    reason: str  # "mask_change" | "session_restored"
    frame: tuple[int, float] | None
    scope: str | None  # "individual" | "persistent"
    """`None` on both means *every* frame may have changed - emitted only by
    `MaskModule.restore_state`, which replaces the whole timeline at once
    (added 2026-09-23 for session loading). A subscriber that narrows its
    work by frame must treat `None` as "no narrowing possible, redo
    everything", which is correct: after a session load nothing it computed
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


@dataclass(slots=True)
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
    `"persistent"` (applies to `frame`'s whole cube, and every cube after,
    until a later persistent change supersedes it - cube granularity only,
    no per-wavelength splitting of persistence). Persistent changes are
    stored as full replacements, not diffs, so each one can be verified
    independently and a lost/corrupted change can't silently corrupt every
    mask downstream of it.
    """

    frame: tuple[int, float]
    scope: str
    mask: np.ndarray


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
    brush_size_px: int = 12

    # New mask system state
    histogram_enabled: bool = False
    histogram_mask: np.ndarray | None = None
    figure_enabled: bool = False
    figure_mask: np.ndarray | None = None

    # Persisted histogram-highlight selection (moved from PreprocessingSettings)
    histogram_highlight_min_value: float | None = None
    histogram_highlight_max_value: float | None = None
