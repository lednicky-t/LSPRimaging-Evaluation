"""``BackgroundModule`` (sketch §7 "Background", §10).

Owns the fitted background model. Emits ``background_model_changed``
(computational - a global-impact change per sketch §6, touches every cell).
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from . import estimate


class BackgroundModule(QObject):
    """Owns the fitted background-correction model."""

    background_model_changed = pyqtSignal()  # computational - TODO: payload shape

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model: object | None = None

    @instrumented("BackgroundModule.fit_from_image")
    def fit_from_image(self, image: np.ndarray) -> None:
        """Estimate a new background model and emit
        :attr:`background_model_changed`. Not yet implemented - scaffolding
        only."""
        self._model = estimate.estimate_background(image)

    def model(self) -> object | None:
        """Not yet implemented - scaffolding only."""
        return self._model
