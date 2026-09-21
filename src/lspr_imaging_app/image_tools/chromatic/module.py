"""``ChromaticModule`` (sketch §7 "Chromatic", §10).

Owns landmarks + fitted model. Exposes ``affine_for()``/``warp_mask()``/
``affine_between()``/``warp_mask_between()`` as its **only** public surface
- ROI/Mask code must never reach into this module's internals (AGENTS.md,
"Module boundaries"; sketch §7). Emits ``chromatic_model_changed``
(computational).

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

**``affine_between()``/``warp_mask_between()`` added 2026-09-21**, from a
mask/ROI design conversation about time-varying ignore masks (see the build
log's matching entry): a mask can now be authored at any frame, not only
the reference wavelength, so warping needs to work frame-to-frame in
general, not just reference->X. No new math - both are built entirely from
``affine_for()`` (already reference->X) plus ``affine.py``'s existing
``invert_affine_matrix``/``compose_affine_matrices``, the same "pivot
through the reference" composition the old app's wavelength-interpolation
code already uses to re-express a transform relative to a different anchor
wavelength.

**Settings ownership + grid bounds built 2026-09-21** - this module now
holds a real ``ChromaticSettings`` instance (``model.py``), fixing a real
gap: it never held one before, unlike every other Image Tools sub-module.
Scoped narrowly to what's genuinely independent of the not-yet-built
landmark workflow: ``set_grid_bounds()``/``clear_grid_bounds()`` (the
search-area rectangle, undo-tracked - confirmed by reading ``gui/
chromatic_controller.py``'s ``grid_roi_changed``/``reset_grid_bounds``,
which do push undo points, unlike Mask's commands). Every *other*
``ChromaticSettings`` field the old app writes
(``chromatic_correction_enabled``, ``chromatic_sample_image_count``,
``chromatic_feature_count``, ``reference_mode``/``reference_wavelength_nm``/
``reference_spectral_cube_index``) turns out to be set as part of bigger
workflow actions there (``update_settings``, ``start_workflow`` - which
also clears landmarks/models and computes which wavelengths to sample),
not a standalone settings-apply form the way Background's fields are - so
building a generic settings setter for them now would guess at a shape
that `add_landmark`/`refit`/a `start_workflow` equivalent should actually
define. Left for when those get built. ``chromatic_registration_mode``/
``chromatic_tile_size_px``/``chromatic_search_radius_px`` are vestigial -
only the now-removed dense tile-matching mode
(``docs/rewrite_build_log_2026-09.md``'s file-split entry) ever read them
- kept on the dataclass, not exposed by any command, matching the "carry
over dead fields, don't invent meaning" discipline this module's own
``model.py`` docstring already applies elsewhere in this app.

**``affine_for()``'s docstring corrected** - it previously claimed the
chromatic-correction-enabled toggle "lives in GeometryModule's settings,
not built yet", which was wrong even when it was written (it's
``ChromaticSettings.chromatic_correction_enabled``, a real field since
2026-09-20). `affine_for()` still doesn't gate on it - deliberately left
open rather than silently resolved: the old app has two different
functions for this (`affine_for_image_key`, gated; `affine_for_image_key_
any`, not), used by different callers for different reasons, and this
module was explicitly built matching the *ungated* one. Whether
`affine_for()`/`warp_mask()` need a gated variant too is a real open
question for whoever wires up a caller that needs "no correction, ever,
while the toggle is off" - not decided here.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from ...undo import FunctionCommand, undo_manager
from . import affine, warp
from .model import ChromaticLandmarkObservation, ChromaticSettings, ChromaticTransformModel, GridBoundsDefinition


class ChromaticModule(QObject):
    """Owns chromatic-correction landmarks and one fitted
    :class:`ChromaticTransformModel` per (spectral_cube_index, wavelength_nm)
    - ported from the current app's ``window._state.chromatic_models`` list
    (``gui/chromatic_controller.py``), which is genuinely one model per
    image, not one global model."""

    chromatic_model_changed = pyqtSignal()  # computational - TODO: payload shape

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = ChromaticSettings()
        self._models: dict[tuple[int, float], ChromaticTransformModel] = {}
        self._landmarks: list[ChromaticLandmarkObservation] = []

    # -- query interface ------------------------------------------------

    def settings(self) -> ChromaticSettings:
        """A defensive copy - the caller's own; mutating it has no effect
        on this module's state, since command methods are the only way to
        change it (same guarantee `GeometryModule.settings()` makes,
        including the nested-dataclass copy `chromatic_grid_bounds` needs,
        the same reason `GeometrySettings.crop` needs one)."""
        return replace(self._settings, chromatic_grid_bounds=replace(self._settings.chromatic_grid_bounds))

    # -- the only public surface other modules may call ---------------------

    def affine_for(self, image_key: tuple[int, float]) -> np.ndarray:
        """Return ``image_key``'s affine matrix, or the identity matrix if
        no model has been fitted for it yet - e.g. the reference wavelength,
        which by definition needs no correction. Doesn't gate on
        ``settings().chromatic_correction_enabled`` - see module docstring
        for why that's a deliberate, still-open decision, not an
        oversight."""
        cube_index, wavelength_nm = int(image_key[0]), float(image_key[1])
        model = self._models.get((cube_index, wavelength_nm))
        if model is None:
            return affine.identity_affine_matrix()
        return np.asarray(model.affine_matrix, dtype=np.float64)

    def warp_mask(self, mask: np.ndarray, image_key: tuple[int, float]) -> np.ndarray:
        return warp.warp_boolean_mask_affine(mask, self.affine_for(image_key))

    def affine_between(self, from_key: tuple[int, float], to_key: tuple[int, float]) -> np.ndarray:
        """The matrix mapping something authored in `from_key`'s geometry
        into `to_key`'s geometry - the general case `affine_for()` is a
        special case of (`affine_for(K)` == `affine_between(reference_key,
        K)`). Undoes `from_key`'s mapping back to reference space, then
        applies `to_key`'s mapping forward - the same pivot-through-the-
        reference composition the old app's wavelength-interpolation code
        already uses to re-express a transform relative to a different
        anchor wavelength (`compose_affine_matrices(anchor_to_target,
        reference_to_anchor)`, see `refit()`'s docstring)."""
        if from_key == to_key:
            # Avoid M @ invert(M) float noise for a very common case -
            # warping something to the exact frame it was authored at.
            return affine.identity_affine_matrix()
        return affine.compose_affine_matrices(
            self.affine_for(to_key),
            affine.invert_affine_matrix(self.affine_for(from_key)),
        )

    def warp_mask_between(self, mask: np.ndarray, from_key: tuple[int, float], to_key: tuple[int, float]) -> np.ndarray:
        """Like `warp_mask()`, but for a mask authored at an arbitrary
        `from_key` rather than always the reference frame."""
        return warp.warp_boolean_mask_affine(mask, self.affine_between(from_key, to_key))

    # -- settings commands ----------------------------------------------

    @instrumented("ChromaticModule.set_grid_bounds")
    def set_grid_bounds(self, x: int, y: int, width: int, height: int) -> None:
        """Set and enable the reference-point search-area rectangle -
        matching the old app's `grid_roi_changed` (`gui/chromatic_
        controller.py`): moving/resizing the search box always implies
        `enabled=True`, same convention as `GeometryModule.set_crop`.
        Undo-tracked (confirmed by reading the old app: `grid_roi_changed`
        pushes `"Chromatic search area"`), unlike Mask's commands - each
        module's undo-tracking is its own confirmed fact, not assumed from
        another module's precedent."""
        new = GridBoundsDefinition(x=int(x), y=int(y), width=max(int(width), 1), height=max(int(height), 1), enabled=True)
        old = replace(self._settings.chromatic_grid_bounds)
        if old == new:
            return

        def apply() -> None:
            self._settings.chromatic_grid_bounds = replace(new)

        def revert() -> None:
            self._settings.chromatic_grid_bounds = replace(old)

        apply()
        undo_manager.push(FunctionCommand("Chromatic search area", undo_fn=revert, redo_fn=apply))

    @instrumented("ChromaticModule.clear_grid_bounds")
    def clear_grid_bounds(self) -> None:
        """Reset to the automatic full-image search area - old app's
        `reset_grid_bounds`. A no-op (no undo entry) if already cleared,
        matching every other no-op-skip command in this codebase."""
        new = GridBoundsDefinition()
        old = replace(self._settings.chromatic_grid_bounds)
        if old == new:
            return

        def apply() -> None:
            self._settings.chromatic_grid_bounds = GridBoundsDefinition()

        def revert() -> None:
            self._settings.chromatic_grid_bounds = replace(old)

        apply()
        undo_manager.push(FunctionCommand("Reset chromatic search area", undo_fn=revert, redo_fn=apply))

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
