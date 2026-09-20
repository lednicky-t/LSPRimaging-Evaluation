"""``ChromaticModule`` (sketch §7 "Chromatic", §10).

Owns landmarks + fitted model. Exposes ``affine_for()``/``warp_mask()`` as
its **only** public surface - ROI/Mask code must never reach into this
module's internals (AGENTS.md, "Module boundaries"; sketch §7). Emits
``chromatic_model_changed`` (computational).

The math this module will eventually call lives in three sibling files,
split out of a single former ``fitting.py`` (2026-09-21, maintainer's
request - see the rewrite build log for the full reasoning): ``affine.py``
(point-based fit/apply, the actually-simple core), ``warp.py`` (apply a
matrix to pixels instead of points), and ``landmark_autotrack.py`` (the
automatic landmark detection/tracking feature, kept deliberately
independent of this module - see that file's own docstring). A fourth
piece, wavelength interpolation (fit at a few sampled wavelengths,
interpolate the rest), still lives inline in the old app's
``gui/analysis_tasks.py`` (``_estimate_chromatic_models_task``) and hasn't
been extracted yet - that's `refit()`'s job once it's actually built, not
done speculatively here.
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from . import affine, warp
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
            return affine.identity_affine_matrix()
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def warp_mask(self, mask: np.ndarray, image_key: tuple[int, float]) -> np.ndarray:
        return warp.warp_boolean_mask_affine(mask, self.affine_for(image_key))

    # -- landmark-editing commands ------------------------------------------

    @instrumented("ChromaticModule.add_landmark")
    def add_landmark(self, observation: ChromaticLandmarkObservation) -> None:
        """Not yet implemented - scaffolding only."""
        raise NotImplementedError

    @instrumented("ChromaticModule.refit")
    def refit(self) -> None:
        """Refit every (cube, wavelength) model from current landmarks and
        emit :attr:`chromatic_model_changed`. Not yet implemented -
        scaffolding only.

        The real logic to port lives in the old app's
        ``gui/analysis_tasks.py`` (``_estimate_chromatic_models_task``'s
        ``mode == "landmark_radial"`` branch, its only live branch - see
        the rewrite build log's chromatic-fitting file-split entry): fit a
        transform (``affine.fit_similarity_matrix``/``fit_affine_matrix``)
        only at the few *sampled* wavelengths that have landmarks marked,
        then interpolate each fitted matrix's coefficients
        (``affine.compose_affine_matrices`` for re-anchoring onto the true
        reference wavelength) across every other wavelength - never
        re-fitting per wavelength, which is the whole point of sampling
        only a few. That interpolation step isn't extracted into its own
        file yet; do that as part of building this method, not
        speculatively beforehand."""
        raise NotImplementedError
