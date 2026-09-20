"""``ChromaticModule`` (sketch §7 "Chromatic", §10).

Owns landmarks + fitted model. Exposes ``affine_for()``/``warp_mask()`` as
its **only** public surface - ROI/Mask code must never reach into this
module's internals (AGENTS.md, "Module boundaries"; sketch §7). Emits
``chromatic_model_changed`` (computational).
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from . import fitting
from .model import ChromaticModel, LandmarkObservation


class ChromaticModule(QObject):
    """Owns chromatic-correction landmarks and the fitted model."""

    chromatic_model_changed = pyqtSignal()  # computational - TODO: payload shape

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model: ChromaticModel | None = None

    # -- the only public surface other modules may call ---------------------

    def affine_for(self, image_key: object) -> np.ndarray:
        """Not yet implemented - scaffolding only."""
        if self._model is None:
            raise RuntimeError("No chromatic model fitted yet")
        return fitting.affine_for(self._model, image_key)

    def warp_mask(self, mask: np.ndarray, image_key: object) -> np.ndarray:
        """Not yet implemented - scaffolding only."""
        if self._model is None:
            raise RuntimeError("No chromatic model fitted yet")
        return fitting.warp_mask(mask, self._model, image_key)

    # -- landmark-editing commands ------------------------------------------

    @instrumented("ChromaticModule.add_landmark")
    def add_landmark(self, observation: LandmarkObservation) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("ChromaticModule.refit")
    def refit(self) -> None:
        """Refit the model from current landmarks and emit
        :attr:`chromatic_model_changed`. Not yet implemented - scaffolding
        only."""
        raise NotImplementedError
