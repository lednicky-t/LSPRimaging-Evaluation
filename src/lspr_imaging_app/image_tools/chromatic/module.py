"""``ChromaticModule`` (sketch §7 "Chromatic", §10).

Owns landmarks + fitted model. Exposes ``affine_for()``/``warp_mask()``/
``affine_between()``/``warp_mask_between()`` as its **only** public surface
- ROI/Mask code must never reach into this module's internals (AGENTS.md,
"Module boundaries"; sketch §7). Emits ``chromatic_model_changed``
(:class:`~.model.ChromaticModelChange`).

The math this module calls lives in four sibling files, split out of a
single former ``fitting.py`` (2026-09-21, maintainer's request - see the
rewrite build log for the full reasoning): ``affine.py`` (point-based
fit/apply, the actually-simple core), ``warp.py`` (apply a matrix to
pixels instead of points), ``landmark_autotrack.py`` (the automatic
landmark detection/tracking feature, kept deliberately independent of
this module - see that file's own docstring), and
``wavelength_interpolation.py`` (fit at a few sampled wavelengths,
interpolate the rest - extracted from the old app's ``gui/analysis_
tasks.py`` as part of building `refit()`, not speculatively beforehand).

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

**Landmark commands built 2026-09-21** - `add_landmark()`/
`remove_landmark()`/`clear_landmarks()` replace the `add_landmark` scaffold
stub, ported from `gui/chromatic_controller.py`'s `upsert_current_landmark`/
`clear_landmark`/`clear_landmarks`. Landmarks are keyed by
`(landmark_id, spectral_cube_index, wavelength_nm)` (a dict, not the old
app's list+linear-scan - the same dict-over-list upgrade this session's
other modules already made, e.g. `RoiToolbox`): placing the same
`landmark_id` again at the same `(cube, wavelength)` moves it, never
duplicates, exactly matching `upsert_current_landmark`'s behavior. Every
one of the three commands clears every fitted model and disables
`chromatic_correction_enabled` - confirmed by reading the old app's
`finalize_landmark_edit`, called unconditionally from every landmark-edit
path there: a landmark's position changing invalidates the *whole* fit it
fed into, not just one wavelength's, since `refit()`'s wavelength-
interpolation step composes every sampled wavelength's fit together. All
three undo-tracked (`"Chromatic landmarks"`, matching the old app's own
label for all of them).

**`refit()` built 2026-09-21** - `wavelength_interpolation.py` (fit at
sampled wavelengths, interpolate/re-anchor the rest) plus a
`sample_wavelengths_for_cube()` query method (so a caller knows which
wavelengths to prompt for landmark-marking before attempting a fit).
Matches the old app's exact cube-broadcasting behavior: one fit per
unique *wavelength*, applied identically to every cube - chromatic models
don't vary by cube today (see `refit()`'s own docstring). Does not enable
`chromatic_correction_enabled`, matching the old app exactly. Every
Chromatic scaffold stub named in the sketch is now built.

**`start_workflow()` built 2026-09-21** - the settings-bundling command
flagged as open above, closing the last named gap in this module. Takes
every value pre-resolved (sample count, feature count, reference cube/
wavelength) rather than reading a dataset or UI widget, and deliberately
does not call `auto_detect_landmarks()` the way the old app's own
`start_workflow` immediately did - that's async panel-layer dispatch, not
this module's job (see the method's own docstring, and `mask/module.py`'s
docstring for the identical reasoning applied to mask-candidate
computation). What's left in Chromatic now is genuinely UI/orchestration -
the panel to call this and then drive `auto_detect_landmarks`/`add_
landmark` off-thread - not a missing module command.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from ...undo import FunctionCommand, undo_manager
from . import affine, warp, wavelength_interpolation
from .model import (
    ChromaticLandmarkObservation,
    ChromaticModelChange,
    ChromaticSettings,
    ChromaticTransformModel,
    GridBoundsDefinition,
)


class ChromaticModule(QObject):
    """Owns chromatic-correction landmarks and one fitted
    :class:`ChromaticTransformModel` per (spectral_cube_index, wavelength_nm)
    - ported from the current app's ``window._state.chromatic_models`` list
    (``gui/chromatic_controller.py``), which is genuinely one model per
    image, not one global model."""

    chromatic_model_changed = pyqtSignal(ChromaticModelChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = ChromaticSettings()
        self._models: dict[tuple[int, float], ChromaticTransformModel] = {}
        # Keyed by (landmark_id, spectral_cube_index, wavelength_nm) - see
        # add_landmark()'s docstring for why this is a dict, not the old
        # app's list+linear-scan.
        self._landmarks: dict[tuple[int, int, float], ChromaticLandmarkObservation] = {}

    # -- query interface ------------------------------------------------

    def landmarks(self) -> tuple[ChromaticLandmarkObservation, ...]:
        return tuple(self._landmarks.values())

    def landmarks_for_image(self, image_key: tuple[int, float]) -> tuple[ChromaticLandmarkObservation, ...]:
        cube_index, wavelength_nm = int(image_key[0]), float(image_key[1])
        return tuple(
            mark
            for mark in self._landmarks.values()
            if mark.spectral_cube_index == cube_index and mark.wavelength_nm == wavelength_nm
        )

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

    def _model_snapshot(self) -> tuple[dict[tuple[int, float], ChromaticTransformModel], bool]:
        """A read-only snapshot of `self._models`/`chromatic_correction_
        enabled` - never mutates. Every landmark-editing command below
        takes this snapshot *before* checking for a no-op, then only
        mutates from inside its own `apply()`/`revert()` closures (the
        same convention every other command in this codebase follows -
        see `RoiToolbox.delete_rois()`)."""
        return dict(self._models), self._settings.chromatic_correction_enabled

    def _clear_models(self) -> None:
        self._models.clear()
        self._settings.chromatic_correction_enabled = False

    def _restore_models(self, old_models: dict[tuple[int, float], ChromaticTransformModel], old_correction_enabled: bool) -> None:
        self._models = dict(old_models)
        self._settings.chromatic_correction_enabled = old_correction_enabled

    @instrumented("ChromaticModule.add_landmark")
    def add_landmark(self, observation: ChromaticLandmarkObservation) -> None:
        """Add or move one landmark - upserts by `(landmark_id,
        spectral_cube_index, wavelength_nm)`, matching the old app's
        `upsert_current_landmark` exactly: placing the same `landmark_id`
        again at the same `(cube, wavelength)` moves it, never duplicates.

        Clears every fitted model and disables `chromatic_correction_
        enabled` - any existing fit was made from the landmark set this
        call just changed, so it's stale the instant this runs (matches
        the old app's `finalize_landmark_edit`, called unconditionally
        from every landmark-edit path). A no-op (no undo entry) if an
        identical observation already exists at this exact key. Undo-
        tracked (old app: `"Chromatic landmarks"`)."""
        key = (int(observation.landmark_id), int(observation.spectral_cube_index), float(observation.wavelength_nm))
        new_observation = replace(observation, landmark_id=key[0], spectral_cube_index=key[1], wavelength_nm=key[2])
        old_observation = self._landmarks.get(key)
        if (
            old_observation is not None
            and old_observation.x_px == new_observation.x_px
            and old_observation.y_px == new_observation.y_px
        ):
            return
        old_models, old_correction_enabled = self._model_snapshot()

        def apply() -> None:
            self._landmarks[key] = new_observation
            self._clear_models()
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="landmarks_changed"))

        def revert() -> None:
            if old_observation is None:
                self._landmarks.pop(key, None)
            else:
                self._landmarks[key] = old_observation
            self._restore_models(old_models, old_correction_enabled)
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="landmarks_changed"))

        apply()
        undo_manager.push(FunctionCommand("Chromatic landmarks", undo_fn=revert, redo_fn=apply))

    @instrumented("ChromaticModule.remove_landmark")
    def remove_landmark(self, landmark_id: int, image_key: tuple[int, float]) -> None:
        """Remove one landmark from one image only - old app's
        `clear_landmark` ("Reset a single reference point on the current
        image only, unlike clear_landmarks() which wipes every point on
        every image"). Same model-invalidation and undo-tracking as
        `add_landmark`. A no-op if no landmark exists at this exact
        `(landmark_id, image_key)`."""
        key = (int(landmark_id), int(image_key[0]), float(image_key[1]))
        if key not in self._landmarks:
            return
        old_observation = self._landmarks[key]
        old_models, old_correction_enabled = self._model_snapshot()

        def apply() -> None:
            self._landmarks.pop(key, None)
            self._clear_models()
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="landmarks_changed"))

        def revert() -> None:
            self._landmarks[key] = old_observation
            self._restore_models(old_models, old_correction_enabled)
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="landmarks_changed"))

        apply()
        undo_manager.push(FunctionCommand("Chromatic landmarks", undo_fn=revert, redo_fn=apply))

    @instrumented("ChromaticModule.clear_landmarks")
    def clear_landmarks(self) -> None:
        """Wipe every landmark on every image - old app's
        `clear_landmarks`. Same model-invalidation and undo-tracking as
        `add_landmark`. A no-op if there are no landmarks to clear."""
        if not self._landmarks:
            return
        old_landmarks = dict(self._landmarks)
        old_models, old_correction_enabled = self._model_snapshot()

        def apply() -> None:
            self._landmarks.clear()
            self._clear_models()
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="landmarks_changed"))

        def revert() -> None:
            self._landmarks = dict(old_landmarks)
            self._restore_models(old_models, old_correction_enabled)
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="landmarks_changed"))

        apply()
        undo_manager.push(FunctionCommand("Chromatic landmarks", undo_fn=revert, redo_fn=apply))

    def sample_wavelengths_for_cube(self, wavelengths_nm: list[float]) -> list[float]:
        """Which of `wavelengths_nm` (the reference cube's own available
        wavelengths) should be landmark-marked before calling `refit()` -
        matches `refit()`'s own internal sampling exactly
        (`wavelength_interpolation.sampled_wavelengths`), so a caller can
        prompt for the right set before attempting a fit. 0 nm
        (broadband/no-filter) is dropped, matching the old app's
        `candidate_chromatic_wavelengths` - the UI never lets the user mark
        landmarks on it."""
        candidates = sorted({float(w) for w in wavelengths_nm if float(w) != 0.0})
        return wavelength_interpolation.sampled_wavelengths(candidates, self._settings.chromatic_sample_image_count)

    @instrumented("ChromaticModule.refit")
    def refit(self, image_keys: list[tuple[int, float]], reference_key: tuple[int, float]) -> None:
        """Refit every `(cube, wavelength)` model in `image_keys` from the
        current landmark set, anchored on `reference_key`. Uses this
        module's own `chromatic_sample_image_count`/`chromatic_feature_
        count`/`chromatic_landmark_model` settings - the wavelength-
        interpolation math itself lives in `wavelength_interpolation.py`
        (extracted from the old app's `_estimate_chromatic_models_task`'s
        `landmark_radial` branch as part of building this method, not
        speculatively beforehand).

        Matches the old app's exact cube-broadcasting behavior: a
        transform is fit once per unique *wavelength* (from landmarks
        marked on `reference_key`'s cube only), then applied identically
        to every cube in `image_keys` at that wavelength - chromatic
        models don't vary by cube today, only by wavelength (per the
        mask/ROI design conversation's own conclusion: per-cube variation
        for chromatic transforms is a later, lower-priority extension, not
        this pass).

        Does **not** enable `chromatic_correction_enabled` - matches the
        old app's `_on_models_ready`, which explicitly turns the toggle
        off after every (re)fit, requiring the user to separately turn
        correction on. Raises `ValueError` (propagated from
        `wavelength_interpolation.fit_wavelength_transforms`) if the
        current landmarks aren't complete enough to fit - the caller's job
        to catch and display, same convention as `GeometryModule.
        apply_measurement_calibration`'s guards. Undo-tracked (old app:
        `"Chromatic correction"`, pushed before its worker dispatch)."""
        reference_cube, reference_wavelength = int(reference_key[0]), float(reference_key[1])
        landmarks_by_wavelength: dict[float, dict[int, tuple[float, float]]] = {}
        for mark in self._landmarks.values():
            if mark.spectral_cube_index != reference_cube:
                continue
            landmarks_by_wavelength.setdefault(mark.wavelength_nm, {})[mark.landmark_id] = (mark.x_px, mark.y_px)

        feature_count = max(int(self._settings.chromatic_feature_count), 1)
        expected_feature_ids = list(range(1, feature_count + 1))
        reference_cube_wavelengths = [float(w) for cube_index, w in image_keys if int(cube_index) == reference_cube]
        sample_wls = self.sample_wavelengths_for_cube(reference_cube_wavelengths)
        target_wavelengths = sorted({float(wavelength_nm) for _cube_index, wavelength_nm in image_keys})
        use_similarity = self._settings.chromatic_landmark_model == "similarity"

        fitted = wavelength_interpolation.fit_wavelength_transforms(
            landmarks_by_wavelength,
            sample_wls,
            expected_feature_ids,
            reference_wavelength,
            target_wavelengths,
            use_similarity=use_similarity,
        )
        model_kind = "landmark_similarity" if use_similarity else "landmark_affine"

        new_models: dict[tuple[int, float], ChromaticTransformModel] = {}
        for cube_index, wavelength_nm in image_keys:
            matrix, rmse, feature_count_for_wavelength = fitted[float(wavelength_nm)]
            new_models[(int(cube_index), float(wavelength_nm))] = ChromaticTransformModel(
                spectral_cube_index=int(cube_index),
                wavelength_nm=float(wavelength_nm),
                model_kind=model_kind,
                affine_matrix=[[float(value) for value in row] for row in matrix.tolist()],
                global_shift_x_px=float(matrix[0, 2]),
                global_shift_y_px=float(matrix[1, 2]),
                rmse_px=rmse,
                mean_score=1.0,
                min_score=1.0,
                tile_count=feature_count_for_wavelength,
                inlier_count=feature_count_for_wavelength,
            )

        old_models, old_correction_enabled = self._model_snapshot()

        def apply() -> None:
            self._models = dict(new_models)
            self._settings.chromatic_correction_enabled = False
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="refit"))

        def revert() -> None:
            self._restore_models(old_models, old_correction_enabled)
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="refit"))

        apply()
        undo_manager.push(FunctionCommand("Chromatic correction", undo_fn=revert, redo_fn=apply))

    @instrumented("ChromaticModule.start_workflow")
    def start_workflow(
        self,
        *,
        sample_image_count: int,
        feature_count: int,
        reference_spectral_cube_index: int,
        reference_wavelength_nm: float,
    ) -> None:
        """Reset the landmark-based registration workflow for a fresh pass -
        the settings-bundling command flagged as open in this module's own
        docstring (`update_settings`/`start_workflow` on `gui/chromatic_
        controller.py`, `develop`/`main`). Sets `chromatic_registration_
        mode="landmark_radial"`, `reference_mode="manual"`, the four given
        values, and forces `chromatic_correction_enabled=False` - matching
        the old app's `start_workflow` exactly - then wipes every landmark
        and fitted model, since starting a fresh workflow with a possibly
        different reference/sample count invalidates whatever was fit
        before (the same reasoning `add_landmark`/`refit` already apply per-
        edit, just at workflow-reset granularity here).

        Takes every value already resolved rather than reading a dataset or
        a UI widget - matches this codebase's "commands take already-
        resolved values" convention (`RoiToolbox.detect_rois`,
        `MaskModule.apply_candidate`). A caller (the future Workflow panel)
        is responsible for: confirming a dataset is loaded, picking the
        current spectral cube, and choosing `reference_wavelength_nm` -
        typically the middle entry of `self.sample_wavelengths_for_cube(...)`
        on that cube's own available wavelengths, the same "sample first,
        anchor on the middle sample" logic the old app's `start_workflow`
        used, now already available as this module's own query method
        rather than needing to be re-derived. `sample_image_count` is
        stored as given (clamped only to a sane minimum) - the actual
        odd-count normalization already happens downstream, inside
        `sampled_wavelengths()`/`refit()` themselves, against whatever
        candidate wavelength list is current at call time, so duplicating
        that normalization here would just be a second, possibly stale copy
        of the same logic.

        Deliberately does **not** call `auto_detect_landmarks()` the way the
        old app's `start_workflow` immediately did - that dispatches an
        async background-thread computation, which by this rewrite's
        established convention (see `mask/module.py`'s docstring for the
        identical reasoning around mask-candidate computation) is the
        panel's job, not this module's: the panel runs detection off the
        GUI thread and then calls `add_landmark()` per result, same as
        `RoiToolbox.detect_rois()` taking already-detected ROIs. Also does
        not touch current cube/wavelength selection (`SelectionModule`'s
        job) or any UI widget state.

        No-op-skipped only when every given value already matches current
        settings *and* there are no landmarks/models to wipe - unlike a
        plain setter, "start a workflow" is a real action even when the
        target settings happen to already match, as long as it actually
        clears something. Undo-tracked as one combined entry ("Chromatic
        workflow", the old app's own label for this action) covering the
        settings change and the landmark/model wipe together, since a user
        undoing this expects both to come back at once."""
        new_settings = (
            "landmark_radial",
            max(int(sample_image_count), 1),
            max(int(feature_count), 1),
            "manual",
            max(int(reference_spectral_cube_index), 0),
            float(reference_wavelength_nm),
            False,
        )
        old_settings = (
            self._settings.chromatic_registration_mode,
            self._settings.chromatic_sample_image_count,
            self._settings.chromatic_feature_count,
            self._settings.reference_mode,
            self._settings.reference_spectral_cube_index,
            self._settings.reference_wavelength_nm,
            self._settings.chromatic_correction_enabled,
        )
        if new_settings == old_settings and not self._landmarks and not self._models:
            return

        old_landmarks = dict(self._landmarks)
        old_models = dict(self._models)

        def apply() -> None:
            (
                self._settings.chromatic_registration_mode,
                self._settings.chromatic_sample_image_count,
                self._settings.chromatic_feature_count,
                self._settings.reference_mode,
                self._settings.reference_spectral_cube_index,
                self._settings.reference_wavelength_nm,
                self._settings.chromatic_correction_enabled,
            ) = new_settings
            self._landmarks.clear()
            self._models.clear()
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="workflow_started"))

        def revert() -> None:
            (
                self._settings.chromatic_registration_mode,
                self._settings.chromatic_sample_image_count,
                self._settings.chromatic_feature_count,
                self._settings.reference_mode,
                self._settings.reference_spectral_cube_index,
                self._settings.reference_wavelength_nm,
                self._settings.chromatic_correction_enabled,
            ) = old_settings
            self._landmarks = dict(old_landmarks)
            self._models = dict(old_models)
            self.chromatic_model_changed.emit(ChromaticModelChange(reason="workflow_started"))

        apply()
        undo_manager.push(FunctionCommand("Chromatic workflow", undo_fn=revert, redo_fn=apply))
