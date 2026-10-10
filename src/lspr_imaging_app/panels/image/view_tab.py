"""The Image panel's "View" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: one place for every display option. Most of its controls are
*copies* of controls that live on other tabs (Mask, Chromatic, Background,
ROIs), so a change on either copy shows on the other.

Layout (one ribbon row, captioned groups, thin separators between them):

    [histogram overlay] | [mask overlay] | [landmarks] | [background] | [ROI display menu] | [scale bar]
        Histogram            Mask           Chromatic     Background         ROIs             Scale bar

Display only (CLAUDE.md "panels/"): the copies report through this tab's
signals and the panel applies the change, exactly as for the original control.
The Chromatic and Background buttons are mirrors (`mirror_icon_button`) of
those tabs' own buttons; the Mask and ROI controls are second instances kept
equal with `sync_from`.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...image_tools import GeometryModule
from ..ribbon_group import group_label_style, labeled_icon_group, vertical_separator
from .background_tab import BackgroundTab
from .chromatic_tab import ChromaticCorrectionTab
from .general_group import mirror_icon_button
from .histogram_highlight_overlay_controls import HistogramHighlightOverlayControls
from .mask_overlay_controls import MaskOverlayControls
from .overlay_tint import OverlayTint
from .roi_display_menu import RoiDisplayMenu
from .roi_overlay_controls import RoiOverlayControls
from .roi_tab import RoiTab
from .scale_bar_controls import ScaleBarControls


class ViewTab(QWidget):
    highlight_visibility_changed = pyqtSignal(bool)
    highlight_color_changed = pyqtSignal(QColor)
    highlight_alpha_changed = pyqtSignal(float)
    # This tab's copy of the Mask overlay controls changed (the Mask tab reports its own).
    mask_visibility_changed = pyqtSignal(bool)
    mask_color_changed = pyqtSignal(QColor)
    mask_alpha_changed = pyqtSignal(float)
    # This tab's copy of the ROI display controls changed: (kind, field, value), as on the ROIs tab.
    roi_overlay_changed = pyqtSignal(str, str, object)
    scale_bar_color_changed = pyqtSignal(QColor)

    def __init__(
        self,
        geometry: GeometryModule,
        highlight_tint: OverlayTint,
        mask_tint: OverlayTint,
        mask_controls: MaskOverlayControls,
        chromatic_tab: ChromaticCorrectionTab,
        background_tab: BackgroundTab,
        roi_tab: RoiTab,
        scale_bar_color: QColor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # Histogram highlight-overlay show/hide + colour + transparency.
        self._highlight_controls = HistogramHighlightOverlayControls(
            visible=highlight_tint.visible, color=highlight_tint.color, alpha=highlight_tint.alpha, parent=self
        )
        self._highlight_controls.visibility_changed.connect(self.highlight_visibility_changed)
        self._highlight_controls.color_changed.connect(self.highlight_color_changed)
        self._highlight_controls.alpha_changed.connect(self.highlight_alpha_changed)
        highlight_group, self._highlight_label = labeled_icon_group(self, self._highlight_controls, "Histogram")

        # Mask: a second copy of the Mask tab's Visibility icons; each copy mirrors the other.
        self._mask_controls = MaskOverlayControls(
            visible=mask_tint.visible, color=mask_tint.color, alpha=mask_tint.alpha, parent=self
        )
        self._mask_controls.visibility_changed.connect(self.mask_visibility_changed)
        self._mask_controls.color_changed.connect(self.mask_color_changed)
        self._mask_controls.alpha_changed.connect(self.mask_alpha_changed)
        for source, target in ((mask_controls, self._mask_controls), (self._mask_controls, mask_controls)):
            for signal in (source.visibility_changed, source.color_changed, source.alpha_changed):
                signal.connect(lambda _value, s=source, t=target: t.sync_from(s))
        mask_group, self._mask_label = labeled_icon_group(self, self._mask_controls, "Mask")

        # Chromatic and Background: mirrors of those tabs' own View buttons.
        chromatic_row = QWidget(self)
        chromatic_layout = QHBoxLayout(chromatic_row)
        chromatic_layout.setContentsMargins(0, 0, 0, 0)
        chromatic_layout.setSpacing(2)
        chromatic_mirrors = [
            mirror_icon_button(button, chromatic_row) for button in (chromatic_tab.show_button(), chromatic_tab.scope_button())
        ]
        for mirror in chromatic_mirrors:
            chromatic_layout.addWidget(mirror)
        background_mirror = mirror_icon_button(background_tab.view_button(), self)
        self._mirrors = [*chromatic_mirrors, background_mirror]
        chromatic_tab.view_buttons_refreshed.connect(lambda: [m.sync() for m in chromatic_mirrors])
        background_tab.view_buttons_refreshed.connect(background_mirror.sync)
        chromatic_group, self._chromatic_label = labeled_icon_group(self, chromatic_row, "Chromatic")
        background_group, self._background_label = labeled_icon_group(self, background_mirror, "Background")

        # ROIs: copies of the ROIs tab's Sample / Reference / Labels controls, in the same pick-up menu.
        self._roi_tab = roi_tab
        self._roi_sample_controls = self._copy_roi_controls("sample", roi_tab.sample_controls)
        self._roi_reference_controls = self._copy_roi_controls("reference", roi_tab.reference_controls)
        labels_mirror = mirror_icon_button(roi_tab.labels_button, self)
        roi_tab.labels_icon_refreshed.connect(labels_mirror.sync)  # follows the icon (theme change, restore)
        self._roi_display_menu = RoiDisplayMenu(
            self._roi_sample_controls, self._roi_reference_controls, labels_mirror, self
        )
        roi_group, self._roi_label = labeled_icon_group(self, self._roi_display_menu, "ROIs")

        self._scale_bar_controls = ScaleBarControls(geometry, scale_bar_color, self)
        self._scale_bar_controls.color_changed.connect(self.scale_bar_color_changed)
        scale_bar_group, self._scale_bar_label = labeled_icon_group(self, self._scale_bar_controls, "Scale bar")

        groups = (highlight_group, mask_group, chromatic_group, background_group, roi_group, scale_bar_group)
        self._separators = [vertical_separator(self) for _ in groups[1:]]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(groups[0])
        for separator, group in zip(self._separators, groups[1:], strict=True):
            layout.addWidget(separator)
            layout.addWidget(group)
        layout.addStretch(1)

    def _copy_roi_controls(self, kind: str, source: RoiOverlayControls) -> RoiOverlayControls:
        """A second instance of a ROIs-tab control, kept equal to it both ways."""
        copy = RoiOverlayControls(kind, visible=True, color=QColor(), alpha=1.0, parent=self)
        copy.sync_from(source)  # starts equal to the source (the placeholders above are overwritten)
        copy.visibility_changed.connect(lambda shown: self.roi_overlay_changed.emit(kind, "visible", bool(shown)))
        copy.color_changed.connect(lambda color: self.roi_overlay_changed.emit(kind, "color", color.name()))
        copy.alpha_changed.connect(lambda alpha: self.roi_overlay_changed.emit(kind, "alpha", float(alpha)))
        for a, b in ((source, copy), (copy, source)):
            for signal in (a.visibility_changed, a.color_changed, a.alpha_changed):
                signal.connect(lambda _value, s=a, t=b: t.sync_from(s))
        return copy

    @property
    def highlight_controls(self) -> HistogramHighlightOverlayControls:
        return self._highlight_controls

    @property
    def scale_bar_controls(self) -> ScaleBarControls:
        return self._scale_bar_controls

    def sync_roi_copies(self) -> None:
        """Copy the ROIs tab's controls onto this tab's copies (after the ROIs tab was set without signals)."""
        self._roi_sample_controls.sync_from(self._roi_tab.sample_controls)
        self._roi_reference_controls.sync_from(self._roi_tab.reference_controls)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._highlight_controls.refresh_theme(theme)
        self._mask_controls.refresh_theme(theme)
        self._roi_sample_controls.refresh_theme(theme)
        self._roi_reference_controls.refresh_theme(theme)
        self._roi_display_menu.refresh_theme(theme)
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        for label in (
            self._highlight_label,
            self._mask_label,
            self._chromatic_label,
            self._background_label,
            self._roi_label,
            self._scale_bar_label,
        ):
            label.setStyleSheet(group_label_style())
