"""Image Tools stage - "Background removal" section: the flatten-background
settings form, ported from the stable app's Background section
(`gui/main_window.py`/`gui/layout_builder.py`: `flatten_background_sigma_
spin`/`flatten_background_binning_combo`/`flatten_ignore_roi_area_check`/
`flatten_ignore_mask_check`/`flatten_background_exclusion_dilation_spin`/
`background_local_reference_check`, all live-pushed through
`_update_image_processing_settings` on every change).

**Real, and the first section to use `CollapsibleSection`'s apply
toggle for real** - `BackgroundModule.set_flatten_background_settings`
takes every field (including `enabled`) in one call, which is exactly what
the apply icon in the section header already models (see
`panel.py._build_image_tools_section`, which wires the header's
`apply_changed` to `set_enabled` below).

**Scoped down from the source, flagged rather than silently dropped**: no
"Profile" button (computes and plots the estimated background - needs a
live image/plot canvas this rewrite doesn't have yet) and no create/load/
save background-*image* file controls (a persisted, real-pixel background
estimate is a materially different feature from this settings form - see
`docs/rewrite_architecture_sketch_2026-09.md`'s note on splitting
background removal into a one-time real-pixel *estimation* step vs. a
cheap, position-only *formula application* step; this section is the
formula-application settings only). The source's icon-toggle buttons for
"Ignore ROI"/"Ignore mask" are plain `QCheckBox`es here instead - same
function, simpler to build without porting the vendored exclusion-icon
rendering too; a later pass can restyle without touching the wiring.
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QVBoxLayout, QWidget

from ...image_tools import BackgroundModule
from ...image_tools.background.model import BackgroundComputationalChange, BackgroundSettings
from .form_rows import stacked_field

logger = logging.getLogger(__name__)


class BackgroundRemovalSection(QWidget):
    """Sigma/binning/exclusion/local-reference form, live-pushed to
    `BackgroundModule` on every change. `enabled` is driven from outside
    (the owning `CollapsibleSection`'s apply toggle - see `set_enabled`),
    not a checkbox in this form."""

    def __init__(self, background: BackgroundModule, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._background_module = background
        self._enabled = background.settings().flatten_background_enabled
        # Guards against the settings-push handlers firing while this
        # widget is itself updating controls from an external
        # background_model_changed (e.g. session restore) - without it,
        # syncing 6 widgets would fire 6 redundant pushes right back at the
        # module, each momentarily reflecting a half-updated form.
        self._syncing = False

        self._sigma_spin = QSpinBox(self)
        self._sigma_spin.setRange(3, 2000)
        self._sigma_spin.setSuffix(" px")
        self._sigma_spin.setKeyboardTracking(False)
        self._sigma_spin.valueChanged.connect(self._push_settings)

        self._binning_combo = QComboBox(self)
        self._binning_combo.addItem("1x1", 1)
        self._binning_combo.addItem("2x2", 2)
        self._binning_combo.addItem("4x4", 4)
        self._binning_combo.currentIndexChanged.connect(self._push_settings)

        self._ignore_roi_check = QCheckBox("Ignore ROI area", self)
        self._ignore_roi_check.setToolTip("Ignore the detected ROI area while estimating the illumination background.")
        self._ignore_roi_check.toggled.connect(self._push_settings)

        self._ignore_mask_check = QCheckBox("Ignore mask", self)
        self._ignore_mask_check.setToolTip("Ignore masked pixels while estimating the illumination background.")
        self._ignore_mask_check.toggled.connect(self._push_settings)

        self._dilation_spin = QSpinBox(self)
        self._dilation_spin.setRange(0, 100)
        self._dilation_spin.setSuffix(" px")
        self._dilation_spin.setKeyboardTracking(False)
        self._dilation_spin.valueChanged.connect(self._push_settings)

        # Abbreviated label (2026-09-25) - "Local reference normalization"
        # alone measured ~372px, wider than the entire 340px panel; full
        # wording kept in the tooltip instead of the visible text.
        self._local_reference_check = QCheckBox("Local reference norm.", self)
        self._local_reference_check.setToolTip("Local reference normalization")
        self._local_reference_check.toggled.connect(self._push_settings)

        # Label-above-field, not QFormLayout (2026-09-25) - measured for
        # real at the Workflow panel's fixed 340px width: this form's
        # QFormLayout version was ~506px wide, well past budget (the
        # "Local reference normalization" checkbox text alone is wider
        # than the whole panel). See form_rows.py's docstring.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(stacked_field("Sigma", self._sigma_spin, self))
        layout.addWidget(stacked_field("Bin", self._binning_combo, self))
        layout.addWidget(stacked_field("Grow excl.", self._dilation_spin, self))
        layout.addWidget(self._ignore_roi_check)
        layout.addWidget(self._ignore_mask_check)
        layout.addWidget(self._local_reference_check)

        self._sync_from_settings(background.settings())
        self._background_module.background_model_changed.connect(self._on_model_changed)

    def set_enabled(self, enabled: bool) -> None:
        """Called by the owning `CollapsibleSection`'s apply toggle
        (`panel.py`) - this form has no enabled checkbox of its own."""
        self._enabled = bool(enabled)
        self._push_settings()

    def _sync_from_settings(self, settings: BackgroundSettings) -> None:
        self._syncing = True
        try:
            self._enabled = settings.flatten_background_enabled
            self._sigma_spin.setValue(int(round(settings.flatten_background_sigma_px)))
            index = self._binning_combo.findData(settings.flatten_background_binning)
            self._binning_combo.setCurrentIndex(index if index >= 0 else 1)
            self._ignore_roi_check.setChecked(settings.flatten_background_exclude_area_rois)
            self._ignore_mask_check.setChecked(settings.flatten_background_exclude_mask)
            self._dilation_spin.setValue(settings.flatten_background_exclusion_dilation_px)
            self._local_reference_check.setChecked(settings.local_reference_normalization_enabled)
        finally:
            self._syncing = False

    def _on_model_changed(self, _change: BackgroundComputationalChange) -> None:
        self._sync_from_settings(self._background_module.settings())

    def _push_settings(self, *_args: object) -> None:
        if self._syncing:
            return
        self._background_module.set_flatten_background_settings(
            enabled=self._enabled,
            sigma_px=float(self._sigma_spin.value()),
            binning=int(self._binning_combo.currentData()),
            exclude_area_rois=self._ignore_roi_check.isChecked(),
            exclude_mask=self._ignore_mask_check.isChecked(),
            exclusion_dilation_px=self._dilation_spin.value(),
            local_reference_normalization_enabled=self._local_reference_check.isChecked(),
        )
