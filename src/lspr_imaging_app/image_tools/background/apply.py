"""Pure background-model application (cheap formula/lookup; the "apply" half
of the estimate/apply split discussed in sketch §7 "Background").

**Built 2026-09-21**: ``estimate.py``'s ``flatten_background`` used to do
estimate *and* apply (subtract-then-recenter-then-clip) inline, in one call,
exactly as ported from the old app. This file now holds that "apply" half as
its own pure function, and ``flatten_background`` composes
``estimate_background_profile()``/``_background_baseline()`` (the estimate
half) with ``apply_background()`` (here) instead of inlining the arithmetic
itself. Verified byte-for-byte identical output before/after against the
inline version on synthetic data (plain, region-scoped, and binned+region
cases).

**Not a "fitted model" object, deliberately** — checked
``BackgroundModule``'s own docstring (`image_tools/background/module.py`)
first: unlike Chromatic's per-(cube, wavelength) affine models,
``flatten_background`` is invoked live, inline, per rendered frame, straight
from ``BackgroundSettings`` — there is no persisted background model
anywhere in the app for this stage to cache and reapply later. Building a
``BackgroundModel`` dataclass here would be speculative machinery for a
caller that doesn't exist yet, not something this split calls for. This
function instead takes an already-computed background array (whatever the
caller just got from ``estimate_background_profile``) and applies it — the
"model" is just that array plus the scalar baseline, passed straight
through, not stored.

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import numpy as np


def apply_background(image: np.ndarray, background: np.ndarray, baseline: float) -> np.ndarray:
    """Subtract `background` from `image` and re-add the scalar `baseline`,
    clipping to the valid 16-bit intensity range. `image` and `background`
    must already be the same shape — the caller is responsible for aligning
    them (e.g. estimate.py's region-scoped callers slice both to the same
    ROI region before calling this, to avoid ever materializing a full-image
    background array they don't need). All the expensive per-pixel work
    (the Gaussian-blur-based background estimate) happens before this is
    ever called; this is deliberately just the cheap formula.
    """
    image_f32 = image.astype(np.float32, copy=False)
    background_f32 = background.astype(np.float32, copy=False)
    flattened = image_f32 - background_f32 + float(baseline)
    return np.clip(flattened, 0.0, 65535.0)
