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
from .model import ChromaticLandmarkObservation, ChromaticTransformModel


class ChromaticModule(QObject):
    """Owns chromatic-correction landmarks and one fitted
    :class:`ChromaticTransformModel` per (spectral_cube_index, wavelength_nm)
    - ported from the current app's ``window._state.chromatic_models`` list
    (``gui/chromatic_controller.py``), which is genuinely one model per
    image, not one global model."""

    chromatic_model_changed = pyqtSignal()  # computational - TODO: payload shape

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._models: dict[tuple[int, float], ChromaticTransformModel] = {}
        self._landmarks: list[ChromaticLandmarkObservation] = []

    # -- the only public surface other modules may call ---------------------

    def affine_for(self, image_key: tuple[int, float]) -> np.ndarray:
        """Return ``image_key``'s affine matrix, or the identity matrix if
        no model has been fitted for it yet - e.g. the reference wavelength,
        which by definition needs no correction. The current app's separate
        "is this the reference key" special case
        (``ChromaticController.affine_for_image_key``, which also gates on
        the chromatic_correction_enabled preprocessing toggle) isn't wired
        here yet - that toggle lives in ``GeometryModule``'s settings, not
        built yet - so this always returns a real-or-identity matrix
        unconditionally, matching ``affine_for_image_key_any``'s shape."""
        cube_index, wavelength_nm = int(image_key[0]), float(image_key[1])
        model = self._models.get((cube_index, wavelength_nm))
        if model is None:
            return fitting.identity_affine_matrix()
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def warp_mask(self, mask: np.ndarray, image_key: tuple[int, float]) -> np.ndarray:
        return fitting.warp_boolean_mask_affine(mask, self.affine_for(image_key))

    # -- landmark-editing commands ------------------------------------------

    @instrumented("ChromaticModule.add_landmark")
    def add_landmark(self, observation: ChromaticLandmarkObservation) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("ChromaticModule.refit")
    def refit(self) -> None:
        """Refit every (cube, wavelength) model from current landmarks
        (``fitting.estimate_affine_chromatic_transform``, one call per
        image) and emit :attr:`chromatic_model_changed`. Not yet
        implemented - scaffolding only."""
        raise NotImplementedError
