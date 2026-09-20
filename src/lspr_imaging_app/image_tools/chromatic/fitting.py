"""Pure chromatic-correction math (sketch §10: "ports processing/chromatic.py's
fitting/warping/tracking largely as-is").

No Qt import allowed in this file (AGENTS.md testing rule: every pure-
computation file must be importable/testable with zero Qt/GUI dependency).
Not yet ported. Current implementation lives in
``lspr_imaging_app/processing/chromatic.py`` on the ``develop``/``main``
branches.
"""

from __future__ import annotations

import numpy as np

from .model import ChromaticModel, LandmarkObservation


def fit_model(landmarks: list[LandmarkObservation]) -> ChromaticModel:
    """Fit a :class:`ChromaticModel` from observed landmarks. Not yet
    implemented - scaffolding only."""
    raise NotImplementedError


def affine_for(model: ChromaticModel, image_key: object) -> np.ndarray:
    """Return the affine transform for ``image_key`` under ``model``. Not
    yet implemented - scaffolding only."""
    raise NotImplementedError


def warp_mask(mask: np.ndarray, model: ChromaticModel, image_key: object) -> np.ndarray:
    """Forward-transform ``mask`` (raw pixel space) into ``image_key``'s
    processed/wavelength space. Not yet implemented - scaffolding only."""
    raise NotImplementedError
