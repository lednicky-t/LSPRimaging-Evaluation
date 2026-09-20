"""``BackgroundModule`` (sketch §7 "Background", §10).

Owns ``BackgroundSettings``. Emits ``background_model_changed``
(computational - a global-impact change per sketch §6, touches every cell).

**Real command method built 2026-09-21**, replacing the scaffold stub -
and correcting a wrong assumption the 2026-09-20 scaffold made in the
process. The original stub was:

```python
def fit_from_image(self, image: np.ndarray) -> None:
    self._model = estimate.estimate_background(image)
```

Two problems, found by actually running it (`estimate.estimate_background`
raised `AttributeError` - no such function exists in `estimate.py`; see
`git blame`/the build log for how this was caught): first, the obvious
bug - `estimate.py` only ever defines `flatten_background()` (estimate
*and* apply combined in one call, per that file's own docstring - the
real estimate/apply split is explicitly deferred future work, not done).
Second, a deeper one the docstring's "owns the fitted background model"
framing had baked in: checking `processing/preprocess.py` on `develop`
(the real caller) shows `flatten_background()` is invoked **live, inline,
per rendered frame**, from `BackgroundSettings` fields directly - there is
no persisted "fitted model" object anywhere in the current app for this
stage, unlike Chromatic's genuinely-stored per-(cube, wavelength) affine
models. `BackgroundSettings` itself confirms this: every field is a
parameter/toggle, none is a result. So this module's honest job today is
what `GeometryModule` already does - **own settings**, not fit-and-cache a
model - and `set_flatten_background_settings()` below replaces
`fit_from_image`/`model()` entirely rather than fixing them in place.

**One combined command, not six granular ones** - deliberately unlike
`GeometryModule`'s per-toggle setters. Checked the old app's actual UI
wiring (`gui/main_window.py`'s `_update_image_processing_settings`) rather
than assuming: it's a settings-panel-with-an-Apply-button (every
Background field read from its widget and written in one call, one
`_push_undo_point("Image processing")` for the whole group), not
per-field live-editing the way Geometry's crop/rotate/flip toolbar buttons
are. Matched that shape - one command, one undo entry, same label.

**`local_reference_normalization_enabled` is included but currently
inert** - grepped the whole app for any reader of this field besides the
GUI checkbox and session persistence; there isn't one. This isn't a gap
introduced by the rewrite - the old app persists and displays the toggle
but nothing in `processing/`/`gui/analysis_tasks.py` actually branches on
it today either. Owned here as real, persisted state regardless (that's
this module's job independent of whether anything reads it yet), flagged
so a future session doesn't assume it's wired to something.
"""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from ...diagnostics import instrumented
from ...undo import FunctionCommand, undo_manager
from .model import BackgroundComputationalChange, BackgroundSettings


class BackgroundModule(QObject):
    """Owns background-flatten settings for the active dataset."""

    background_model_changed = pyqtSignal(BackgroundComputationalChange)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = BackgroundSettings()

    # -- query interface ------------------------------------------------

    def settings(self) -> BackgroundSettings:
        """A defensive copy - the caller's own; mutating it has no effect
        on this module's state, since command methods are the only way to
        change it (same guarantee `GeometryModule.settings()` makes)."""
        return replace(self._settings)

    # -- commands -----------------------------------------------------------

    @instrumented("BackgroundModule.set_flatten_background_settings")
    def set_flatten_background_settings(
        self,
        *,
        enabled: bool,
        sigma_px: float,
        binning: int,
        exclude_area_rois: bool,
        exclude_mask: bool,
        exclusion_dilation_px: int,
        local_reference_normalization_enabled: bool,
    ) -> None:
        """Set every background-flatten field at once - matching the old
        app's own "Apply" grouping (see module docstring). A no-op (no
        undo entry) if every value already matches, same convention as
        every other command in this codebase."""
        new = BackgroundSettings(
            flatten_background_enabled=bool(enabled),
            flatten_background_sigma_px=max(float(sigma_px), 0.0),
            flatten_background_binning=max(int(binning), 1),
            flatten_background_exclude_area_rois=bool(exclude_area_rois),
            flatten_background_exclude_mask=bool(exclude_mask),
            flatten_background_exclusion_dilation_px=max(int(exclusion_dilation_px), 0),
            local_reference_normalization_enabled=bool(local_reference_normalization_enabled),
        )
        old = replace(self._settings)
        if old == new:
            return

        def apply() -> None:
            self._settings = replace(new)
            self.background_model_changed.emit(BackgroundComputationalChange(reason="flatten_background_settings"))

        def revert() -> None:
            self._settings = replace(old)
            self.background_model_changed.emit(BackgroundComputationalChange(reason="flatten_background_settings"))

        apply()
        undo_manager.push(FunctionCommand("Image processing", undo_fn=revert, redo_fn=apply))
