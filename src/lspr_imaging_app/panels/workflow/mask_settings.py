"""Image Tools stage - "Mask" section: the mask tool-tuning settings form.

Ported from the stable app's mask tool spinboxes
(`gui/main_window.py`: `mask_relative_profile_sigma_spin`/
`mask_relative_threshold_spin`/`mask_local_contrast_sigma_spin`/
`mask_local_contrast_z_spin`/`mask_morphology_radius_spin`/
`mask_brush_size_spin`, all live-pushed on every change), with one real
architectural correction along the way, found by reading the source rather
than assumed: **in the source these same spinboxes double-feed both ROI
detection (`_update_roi_detection_settings`) and the mask preview
(`_refresh_mask_previews`) - exactly the kind of cross-concern
entanglement this rewrite exists to undo.** `MaskModule.set_tool_settings`
is Mask's own, single-purpose settings command; nothing here touches
`RoiToolbox`'s separate `detection_settings()` (see `analysis/engine.py`'s
`_build_analysis_engine`, which already treats them as distinct reads). If
ROI detection ever needs the same numbers, that is a deliberate design
choice for whoever wires it, not an accident of a shared spinbox.

**No apply toggle** on this section's `CollapsibleSection`, unlike
Background removal - checked `MaskSettings` before assuming one: there is
no `mask_enabled`-shaped boolean field to back it. Whether masking has any
effect is governed by whether any `MaskChange` has been committed to the
timeline (`MaskModule`'s own docstring), not a settings toggle - these
tunables are inputs to a not-yet-built "apply" action, not something that
is itself "on" or "off".

**Deliberately not built this pass** (`MaskModule`'s own docstring already
flags this precisely, quoted rather than re-derived): the actual
Apply/Reset/Show actions for each tool (relative threshold, local
contrast, morphology, drawing/brush) - each computes a real mask candidate
from the currently-viewed image, which needs a background worker (two of
the four tool kinds are genuinely slow - raw image reload + scipy
filtering) and `ChromaticModule` coordination to resolve the right base
mask for the current frame. This section is the "set the numbers" half
only; "compute and commit a candidate" is separate, larger work most
likely landing alongside the Image panel once it has a real canvas to
preview against.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QDoubleSpinBox, QSpinBox, QVBoxLayout, QWidget

from ...image_tools import MaskModule
from ...image_tools.mask.model import MaskComputationalChange, MaskCosmeticChange, MaskSettings
from .form_rows import stacked_field

logger = logging.getLogger(__name__)


class MaskSettingsSection(QWidget):
    """Relative-threshold/local-contrast/morphology/brush tuning form,
    live-pushed to `MaskModule` on every change."""

    def __init__(self, mask: MaskModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mask_module = mask
        # Same reason as BackgroundRemovalSection's `_syncing` guard - stops
        # a module-driven form sync from immediately pushing itself right
        # back at the module as if it were a fresh user edit.
        self._syncing = False

        self._relative_threshold_spin = QDoubleSpinBox(self)
        self._relative_threshold_spin.setRange(0.1, 500.0)
        self._relative_threshold_spin.setDecimals(1)
        self._relative_threshold_spin.setSingleStep(1.0)
        self._relative_threshold_spin.setSuffix(" %")
        self._relative_threshold_spin.valueChanged.connect(self._push_settings)

        self._relative_sigma_spin = QSpinBox(self)
        self._relative_sigma_spin.setRange(3, 2000)
        self._relative_sigma_spin.setSuffix(" px")
        self._relative_sigma_spin.setKeyboardTracking(False)
        self._relative_sigma_spin.valueChanged.connect(self._push_settings)

        self._local_contrast_sigma_spin = QSpinBox(self)
        self._local_contrast_sigma_spin.setRange(1, 2000)
        self._local_contrast_sigma_spin.setSuffix(" px")
        self._local_contrast_sigma_spin.setKeyboardTracking(False)
        self._local_contrast_sigma_spin.valueChanged.connect(self._push_settings)

        self._local_contrast_z_spin = QDoubleSpinBox(self)
        self._local_contrast_z_spin.setRange(0.1, 20.0)
        self._local_contrast_z_spin.setDecimals(1)
        self._local_contrast_z_spin.setSingleStep(0.1)
        self._local_contrast_z_spin.valueChanged.connect(self._push_settings)

        self._morphology_radius_spin = QSpinBox(self)
        self._morphology_radius_spin.setRange(1, 100)
        self._morphology_radius_spin.setSuffix(" px")
        self._morphology_radius_spin.valueChanged.connect(self._push_settings)

        self._brush_size_spin = QSpinBox(self)
        self._brush_size_spin.setRange(1, 200)
        self._brush_size_spin.setSuffix(" px")
        self._brush_size_spin.valueChanged.connect(self._push_settings)

        # Label-above-field, not QFormLayout (2026-09-25) - measured for
        # real at the Workflow panel's fixed 340px width: a QFormLayout row
        # with these label lengths pushed this form to ~414px, well past
        # budget. See form_rows.py's docstring.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(stacked_field("Relative threshold", self._relative_threshold_spin, self))
        layout.addWidget(stacked_field("Relative profile sigma", self._relative_sigma_spin, self))
        layout.addWidget(stacked_field("Local contrast sigma", self._local_contrast_sigma_spin, self))
        layout.addWidget(stacked_field("Local contrast z", self._local_contrast_z_spin, self))
        layout.addWidget(stacked_field("Morphology radius", self._morphology_radius_spin, self))
        layout.addWidget(stacked_field("Brush size", self._brush_size_spin, self))

        self._sync_from_settings(mask.settings())
        # Both signals re-sync: set_tool_settings only emits cosmetic_changed,
        # but restore_state (session load) replaces _settings too while only
        # emitting mask_changed - listening to just one would miss the other.
        self._mask_module.cosmetic_changed.connect(self._on_model_changed)
        self._mask_module.mask_changed.connect(self._on_model_changed)

    def _sync_from_settings(self, settings: MaskSettings) -> None:
        self._syncing = True
        try:
            self._relative_threshold_spin.setValue(settings.relative_threshold_fraction * 100.0)
            self._relative_sigma_spin.setValue(int(round(settings.relative_profile_sigma_px)))
            self._local_contrast_sigma_spin.setValue(int(round(settings.local_contrast_sigma_px)))
            self._local_contrast_z_spin.setValue(settings.local_contrast_z_threshold)
            self._morphology_radius_spin.setValue(settings.morphology_radius_px)
            self._brush_size_spin.setValue(settings.brush_size_px)
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: MaskCosmeticChange | MaskComputationalChange) -> None:
        self._sync_from_settings(self._mask_module.settings())

    def _push_settings(self, *_args: object) -> None:
        if self._syncing:
            return
        # histogram_min_value/max_value aren't this form's concern (they're
        # the histogram-highlight selection, set via
        # set_histogram_highlight_range - no Histogram panel content yet
        # either) - passed through unchanged so this push can't clobber them.
        current = self._mask_module.settings()
        self._mask_module.set_tool_settings(
            histogram_min_value=current.histogram_min_value,
            histogram_max_value=current.histogram_max_value,
            relative_threshold_fraction=self._relative_threshold_spin.value() / 100.0,
            relative_profile_sigma_px=float(self._relative_sigma_spin.value()),
            local_contrast_sigma_px=float(self._local_contrast_sigma_spin.value()),
            local_contrast_z_threshold=self._local_contrast_z_spin.value(),
            morphology_radius_px=self._morphology_radius_spin.value(),
            brush_size_px=self._brush_size_spin.value(),
        )
