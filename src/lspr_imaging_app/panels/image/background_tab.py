"""The Image panel's "Background" ribbon tab (2026-10-06, maintainer request:
move the Workflow panel's "Background removal" form here, and add a
show-background toggle).

Screen purpose: choose how the illumination background is estimated and
removed, and look at the estimate itself.

Layout (one ribbon row, captioned groups like the Mask/Chromatic tabs):

    [bg]  |  Sigma  Bin  |  [ROI] [mask]  Excl. dilatation  |  [wand]
     View      Estimate                       Exclude                    Removal

- **View:** show the estimated background instead of the image (the stable
  app's "Profile" view). Display only: `ImagePanel` renders it off the GUI
  thread (`render.py`, `show_background`); nothing is stored.
- **Estimate:** Gaussian sigma (px), and binning (kept to `bin <= sigma/6`) - see
  `image_tools/background/estimate.py`.
- **Exclude:** keep the sample-ROI areas (red filled circle) / the ignore mask
  (mask icon, red) out of the estimate, and the exclusion dilatation (px: how far the excluded zone is grown).
- **Removal (last):** switch background removal on/off in the processing
  pipeline. The image is divided by the estimate (`apply.apply_background`).
  While on, the panel turns this tab green (`ImageToolRibbon.set_tab_applied`).
- **Info:** the panel's shared "i" icon shows `BACKGROUND_INFO_HTML` (how it works,
  strengths, drawbacks - time) while this tab is open.

Every edit is pushed straight to `BackgroundModule.set_flatten_background_
settings` (one command carries all seven fields), same as the Workflow
section this replaces. Display/UI only - no estimation happens here.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QSpinBox,
    QToolButton,
    QWidget,
)

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from ...image_tools import BackgroundModule
from ...image_tools.background.model import BackgroundComputationalChange, BackgroundSettings, max_binning_for_sigma
from ..ribbon_group import labeled_icon_group, vertical_separator
from .general_group import ICON_SIZE, style_general_icon_button
from .roi_icons import spot_icon

_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1
_ACTIVE_COLOR = "#38bdf8"  # the ribbon's tool blue
# Content height of the number fields and the drop-down. Set through a widget
# stylesheet, not `setFixedHeight`: the app theme's own `min-height: 18px` rule
# (lspr_ui `startup_app_stylesheet`) wins over a fixed height, which left the
# drop-down 2 px shorter than the spin boxes.
_FIELD_CONTENT_HEIGHT_PX = 20
_SIGMA_WIDTH_PX = 64  # fits "2000 px"
_BIN_POPUP_WIDTH_PX = 72
_DILATION_WIDTH_PX = 48  # two digits + " px" (a 3-digit entry would be clipped)
_FLASH_MS = 1400  # the binning hint's fade
_EXCLUDE_COLOR = "#ef4444"  # red: this is being excluded from the estimate
_ROI_COLOR = _EXCLUDE_COLOR  # red, like the mask: excluded from the estimate
_ON_COLOR = "#22c55e"  # "excluded"/"applied" green, as the stable app's exclusion icons


BACKGROUND_INFO_HTML = (
    "<b>How background removal works</b><br>"
    "The lamp does not light the field evenly: it acts as a gain that multiplies every pixel. "
    "For each frame the app estimates the <i>white level</i> at every position: a Gaussian-weighted "
    "average (width = Sigma) of the nearby substrate pixels, skipping the ROI areas, the ignore mask "
    "and anything you grow around them. The frame is then divided by that estimate and rescaled to its "
    "typical level, so a sample and a reference at different positions see the same illumination. "
    "It is done separately for every wavelength and every cube (the illumination drifts during a run).<br><br>"
    "<b>Strengths</b><br>"
    "&bull; Fixes sample/reference ratios when the reference is not centred around the sample: with a reference "
    "240&nbsp;px away, the error fell from 7.9&nbsp;% to 1.6&nbsp;% on a real dataset.<br>"
    "&bull; Follows illumination changes in time and with wavelength.<br>"
    "&bull; Ignored ROIs and masked pixels cannot pull the estimate.<br><br>"
    "<b>Drawbacks</b><br>"
    "&bull; <b>Time:</b> about 22&nbsp;ms per frame at 8x8 binning (30&nbsp;ms at 4x4, 50&nbsp;ms at 2x2, 270&nbsp;ms at 1x1) "
    "for a 1269&times;742 image. Analysis repeats it for every wavelength and cube (and currently also for every ROI), "
    "so large datasets add minutes.<br>"
    "&bull; Only smooth illumination (slower than Sigma) is corrected; a small Sigma follows finer structure "
    "but can start absorbing real signal.<br>"
    "&bull; Needs enough free substrate around the ROIs.<br>"
    "&bull; With a reference ring centred on the sample the gain already cancels, so the benefit is small."
)


def _match_field_height(widget: QWidget, class_name: str) -> None:
    widget.setStyleSheet(
        f"{class_name} {{ min-height: {_FIELD_CONTENT_HEIGHT_PX}px; max-height: {_FIELD_CONTENT_HEIGHT_PX}px; }}"
    )


def _roi_icon(excluded: bool, dim: str) -> QIcon:
    """Sample-ROI exclusion icon: a filled circle, red when the ROI areas are
    excluded; grey with a slash (top right to bottom left) when they are not.
    The drawing is shared with the ROI tab's visibility toggle (`roi_icons.py`)."""
    return spot_icon(excluded, dim, _ROI_COLOR)


class BackgroundTab(QWidget):
    """See the module docstring. `show_background_changed(bool)` fires on a
    user click only (`set_show_background` is silent), so restoring the saved
    state at startup is not mistaken for a user change."""

    show_background_changed = pyqtSignal(bool)
    view_buttons_refreshed = pyqtSignal()  # the View button changed icon/state (mirrored on the View tab)

    def __init__(
        self,
        background: BackgroundModule,
        *,
        show_background: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._background = background
        # Stops the push handlers firing while the controls are being
        # updated from the module (session restore, undo) - otherwise syncing
        # seven widgets would push seven half-updated forms back at it.
        self._syncing = False

        self._apply_button = self._icon_button("Apply or skip background removal in the processing pipeline.")
        self._apply_button.toggled.connect(self._on_apply_toggled)

        self._sigma_spin = QSpinBox(self)
        self._sigma_spin.setRange(3, 2000)
        self._sigma_spin.setSuffix(" px")
        self._sigma_spin.setKeyboardTracking(False)
        self._sigma_spin.setFixedWidth(_SIGMA_WIDTH_PX)
        _match_field_height(self._sigma_spin, "QSpinBox")
        self._sigma_spin.setToolTip(
            "Width of the Gaussian blur that estimates the smooth background.\n"
            "About the size of an ROI's excluded zone works best (roughly 25-50 px): much smaller cannot\n"
            "bridge the ROI, much larger cannot follow the illumination."
        )
        self._sigma_spin.valueChanged.connect(self._push_settings)

        self._binning_combo = QComboBox(self)
        self._binning_combo.addItem("1x1", 1)
        self._binning_combo.addItem("2x2", 2)
        self._binning_combo.addItem("4x4", 4)
        self._binning_combo.addItem("8x8", 8)
        self._binning_combo.setToolTip(
            "Average this many pixels per side before blurring: faster, and just as accurate while\n"
            "Sigma stays at least 6 binned cells wide. Lowered automatically when Sigma gets too small for it."
        )
        _match_field_height(self._binning_combo, "QComboBox")
        self._binning_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._binning_combo.view().setMinimumWidth(_BIN_POPUP_WIDTH_PX)  # the drop-down list was narrower than its items
        self._binning_combo.currentIndexChanged.connect(self._push_settings)

        self._dilation_spin = QSpinBox(self)
        self._dilation_spin.setRange(0, 100)
        self._dilation_spin.setSuffix(" px")
        self._dilation_spin.setKeyboardTracking(False)
        self._dilation_spin.setFixedWidth(_DILATION_WIDTH_PX)
        _match_field_height(self._dilation_spin, "QSpinBox")
        self._dilation_spin.setToolTip("Exclusion dilatation: grow the excluded zone by this many pixels before estimating.")
        self._dilation_spin.valueChanged.connect(self._push_settings)

        self._ignore_roi_button = self._icon_button("Ignore the ROI areas while estimating the background.")
        self._ignore_roi_button.toggled.connect(self._push_settings)
        self._ignore_mask_button = self._icon_button("Ignore masked pixels while estimating the background.")
        self._ignore_mask_button.toggled.connect(self._push_settings)

        self._show_button = self._icon_button("Show the estimated background instead of the image.")
        self._show_button.setChecked(bool(show_background))
        self._show_button.toggled.connect(self._on_show_toggled)

        removal_group, self._removal_label = labeled_icon_group(self, self._apply_button, "Removal")
        sigma_group, self._sigma_label = labeled_icon_group(self, self._sigma_spin, "Sigma")
        bin_group, self._bin_label = labeled_icon_group(self, self._binning_combo, "Bin")
        dilation_group, self._dilation_label = labeled_icon_group(self, self._dilation_spin, "Excl. dilatation")
        exclude_row = QWidget(self)
        exclude_layout = QHBoxLayout(exclude_row)
        exclude_layout.setContentsMargins(0, 0, 0, 0)
        exclude_layout.setSpacing(2)
        exclude_layout.addWidget(self._ignore_roi_button)
        exclude_layout.addWidget(self._ignore_mask_button)
        exclude_group, self._exclude_label = labeled_icon_group(self, exclude_row, "Exclude")
        view_group, self._view_label = labeled_icon_group(self, self._show_button, "View")

        self._separators: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, group in enumerate((view_group, (sigma_group, bin_group), (exclude_group, dilation_group), removal_group)):
            if index:
                separator = vertical_separator(self)
                self._separators.append(separator)
                layout.addWidget(separator)
            for widget in group if isinstance(group, tuple) else (group,):
                layout.addWidget(widget)
        layout.addStretch(1)

        self._sync_from_settings(background.settings())
        self._refresh_icons()
        self._background.background_model_changed.connect(self._on_model_changed)

    # -- public ---------------------------------------------------------------

    def set_show_background(self, shown: bool) -> None:
        """Set the view toggle without emitting `show_background_changed`."""
        blocked = self._show_button.blockSignals(True)
        try:
            self._show_button.setChecked(bool(shown))
        finally:
            self._show_button.blockSignals(blocked)
        self._refresh_icons()

    def view_button(self) -> QToolButton:
        return self._show_button

    def is_background_applied(self) -> bool:
        return self._apply_button.isChecked()

    def refresh_theme(self, _theme: GuiTheme) -> None:
        for button in self._buttons():
            style_general_icon_button(button)
        self._refresh_icons()
        for separator in self._separators:
            separator.setStyleSheet(f"color: {get_active_theme().control_border};")

    # -- internals ------------------------------------------------------------

    def _icon_button(self, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(True)
        button.setToolTip(tooltip)
        style_general_icon_button(button)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        return button

    def _buttons(self) -> tuple[QToolButton, ...]:
        return (self._apply_button, self._ignore_roi_button, self._ignore_mask_button, self._show_button)

    def _refresh_icons(self) -> None:
        dim = get_active_theme().text_dim
        self._ignore_roi_button.setIcon(_roi_icon(self._ignore_roi_button.isChecked(), dim))
        for button, on_name, off_name, on_color in (
            (self._apply_button, "wand", "wand-off", _ON_COLOR),
            (self._ignore_mask_button, "mask", "mask-off", _EXCLUDE_COLOR),  # same icons as the mask tint toggle
            (self._show_button, "background", "background", _ACTIVE_COLOR),
        ):
            checked = button.isChecked()
            button.setIcon(
                load_tabler_icon(on_name if checked else off_name, color=on_color if checked else dim,
                                 size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
            )
        self.view_buttons_refreshed.emit()

    def _sync_from_settings(self, settings: BackgroundSettings) -> None:
        self._syncing = True
        try:
            self._apply_button.setChecked(settings.flatten_background_enabled)
            self._sigma_spin.setValue(int(round(settings.flatten_background_sigma_px)))
            index = self._binning_combo.findData(settings.flatten_background_binning)
            self._binning_combo.setCurrentIndex(index if index >= 0 else 1)
            self._ignore_roi_button.setChecked(settings.flatten_background_exclude_area_rois)
            self._ignore_mask_button.setChecked(settings.flatten_background_exclude_mask)
            self._dilation_spin.setValue(settings.flatten_background_exclusion_dilation_px)
        finally:
            self._syncing = False
        self._refresh_icons()

    def _on_model_changed(self, _change: BackgroundComputationalChange) -> None:
        self._sync_from_settings(self._background.settings())

    def _on_apply_toggled(self, _checked: bool) -> None:
        self._refresh_icons()
        self._push_settings()

    def _on_show_toggled(self, checked: bool) -> None:
        self._refresh_icons()
        self.show_background_changed.emit(bool(checked))

    def _enforce_binning_limit(self) -> None:
        """Lower the binning when Sigma is too small for it (`bin <= sigma/6`),
        and fade the control's border so the change is noticed, not silent."""
        limit = max_binning_for_sigma(float(self._sigma_spin.value()))
        if int(self._binning_combo.currentData()) <= limit:
            return
        blocked = self._binning_combo.blockSignals(True)
        try:
            self._binning_combo.setCurrentIndex(max(self._binning_combo.findData(limit), 0))
        finally:
            self._binning_combo.blockSignals(blocked)
        self._flash(self._binning_combo)

    def _flash(self, widget: QWidget) -> None:
        """A short fade-in/fade-out of an accent border on `widget`."""
        animation = QVariantAnimation(widget)
        animation.setDuration(_FLASH_MS)
        animation.setKeyValueAt(0.0, 0.0)
        animation.setKeyValueAt(0.25, 1.0)
        animation.setKeyValueAt(1.0, 0.0)
        accent = QColor(_ACTIVE_COLOR)
        class_name = type(widget).__name__

        def paint(alpha: float) -> None:
            widget.setStyleSheet(
                f"{class_name} {{ border: 1px solid rgba({accent.red()}, {accent.green()}, {accent.blue()}, {int(255 * float(alpha))}); border-radius: 3px; }}"
            )

        animation.valueChanged.connect(paint)
        animation.finished.connect(lambda: widget.setStyleSheet(""))
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _push_settings(self, *_args: object) -> None:
        if self._syncing:
            return
        self._enforce_binning_limit()
        self._refresh_icons()
        self._background.set_flatten_background_settings(
            enabled=self._apply_button.isChecked(),
            sigma_px=float(self._sigma_spin.value()),
            binning=int(self._binning_combo.currentData()),
            exclude_area_rois=self._ignore_roi_button.isChecked(),
            exclude_mask=self._ignore_mask_button.isChecked(),
            exclusion_dilation_px=self._dilation_spin.value(),
        )
