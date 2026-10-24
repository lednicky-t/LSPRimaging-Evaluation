"""The Image panel's "View" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: one place for every display option. Most of its controls are
*copies* of controls that live on other tabs (Mask, Chromatic, Background,
ROIs), so a change on either copy shows on the other.

Layout (one ribbon row, captioned groups, thin separators between them):

    [cursor, display range] | [show all, hide all] | [masks menu] | [landmarks] | [background] | [ROI display menu][group labels menu] | [scale bar] | [chunk grid]
     Image                   All                    Masks         Chromatic     BG             ROIs             Groups                Scale bar     Chunks

The Masks menu holds the histogram-highlight and mask tints (`masks_menu.py`, 2026-10-23).

Display only (CLAUDE.md "panels/"): the copies report through this tab's
signals and the panel applies the change, exactly as for the original control.
The Chromatic and Background buttons are mirrors (`mirror_icon_button`) of
those tabs' own buttons; the Mask, ROI and Group-label controls are second
instances kept equal with `sync_from`.

**All** (`set_all_overlays`): Show all / Hide all switch every overlay toggle on this tab (histogram highlight,
mask, chromatic landmarks, ROI sample / reference / labels, group labels, scale bar) through the toggles' own
buttons, so each one reports and redraws its icon exactly as for a click. The Background view is left out: it
swaps the whole image for the background estimate rather than drawing over it. The chromatic "all wavelengths"
button is a mode, not a visibility, and is left alone too.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...image_tools import GeometryModule
from ..ribbon_group import group_label_style, labeled_icon_group, ribbon_separator
from .background_tab import BackgroundTab
from .chromatic_tab import ChromaticCorrectionTab
from .chunk_grid_controls import ChunkGridControls
from .display_range_controls import DisplayRangeControls
from .general_group import mirror_icon_button
from .group_label_menu import GroupLabelMenu
from .histogram_highlight_overlay_controls import HistogramHighlightOverlayControls
from .label_placement_picker import LabelPlacementPicker
from .mask_overlay_controls import MaskOverlayControls
from .masks_menu import MasksMenu
from .overlay_all_controls import OverlayAllControls
from .overlay_tint import OverlayTint
from .roi_display_menu import RoiDisplayMenu
from .roi_label_size_box import RoiLabelSizeBox
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
    display_range_changed = pyqtSignal(bool, float, float)  # (fixed, black point, white point), a change made by the user

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
        dataset: DatasetModule,
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

        # Display range: automatic per frame, or one fixed black / white point for every frame.
        self._display_range_controls = DisplayRangeControls(self)
        self._display_range_controls.changed.connect(self.display_range_changed)
        display_range_group, self._display_range_label = labeled_icon_group(
            self, self._display_range_controls, "Image"
        )

        # All: Show all / Hide all, wired below once every overlay control exists (`set_all_overlays`).
        self._all_controls = OverlayAllControls(self)
        self._all_controls.show_all_requested.connect(lambda: self.set_all_overlays(True))
        self._all_controls.hide_all_requested.connect(lambda: self.set_all_overlays(False))
        all_group, self._all_label = labeled_icon_group(self, self._all_controls, "All")

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
        # Histogram highlight and Mask tints share one pick-up menu (masks_menu.py).
        self._masks_menu = MasksMenu(self._highlight_controls, self._mask_controls, self)
        masks_group, self._masks_label = labeled_icon_group(self, self._masks_menu, "Masks")

        # Chromatic and Background: mirrors of those tabs' own View buttons.
        self._chromatic_tab = chromatic_tab
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
        background_group, self._background_label = labeled_icon_group(self, background_mirror, "BG")

        # ROIs: copies of the ROIs tab's Sample / Reference / Labels controls, in the same pick-up menu.
        self._roi_tab = roi_tab
        self._roi_sample_controls = self._copy_roi_controls("sample", roi_tab.sample_controls)
        self._roi_reference_controls = self._copy_roi_controls("reference", roi_tab.reference_controls)
        labels_mirror = mirror_icon_button(roi_tab.labels_button, self)
        roi_tab.labels_icon_refreshed.connect(labels_mirror.sync)  # follows the icon (theme change, restore)
        # The label-place grid: a click here picks it on the ROIs tab's grid (which reports and saves it), and that grid's changes show here.
        original_picker = roi_tab.label_placement_picker
        self._roi_placement_copy = LabelPlacementPicker(original_picker.placement(), self)
        self._roi_placement_copy.setToolTip(original_picker.toolTip())
        self._roi_placement_copy.placement_changed.connect(roi_tab.set_label_placement)
        roi_tab.label_placement_changed.connect(lambda _placement: self._roi_placement_copy.sync_from(original_picker))
        # The text size: a second box; each one sets the other, and a change here reaches the panel through the original's signal.
        original_size = roi_tab.label_size_box
        self._roi_size_copy = RoiLabelSizeBox(self)
        self._roi_size_copy.setValue(original_size.value())
        self._roi_size_copy.valueChanged.connect(original_size.setValue)
        original_size.valueChanged.connect(self._roi_size_copy.setValue)
        self._roi_display_menu = RoiDisplayMenu(
            self._roi_sample_controls, self._roi_reference_controls, labels_mirror, self._roi_placement_copy,
            self._roi_size_copy, self,
        )
        roi_group, self._roi_label = labeled_icon_group(self, self._roi_display_menu, "ROIs")

        # Group labels: a second copy of the ROIs tab's Groups label menu, equal to it both ways. A change made
        # here is handed to the original as if the user had made it there (so the panel redraws and it is saved).
        original_menu = roi_tab.group_label_menu
        self._group_label_menu = GroupLabelMenu(self)
        self._group_label_menu.sync_from(original_menu)
        self._group_label_menu.changed.connect(lambda: original_menu.sync_from(self._group_label_menu, silent=False))
        original_menu.changed.connect(lambda: self._group_label_menu.sync_from(original_menu))
        group_labels_group, self._group_labels_label = labeled_icon_group(self, self._group_label_menu, "Groups")

        self._scale_bar_controls = ScaleBarControls(geometry, scale_bar_color, self)
        self._scale_bar_controls.color_changed.connect(self.scale_bar_color_changed)
        scale_bar_group, self._scale_bar_label = labeled_icon_group(self, self._scale_bar_controls, "Scale bar")

        # The loaded OME-Zarr's own chunk grid (disabled for a TIFF stack); not an overlay of the All buttons.
        self._chunk_grid_controls = ChunkGridControls(dataset, self)
        chunk_grid_group, self._chunk_grid_label = labeled_icon_group(self, self._chunk_grid_controls, "Chunks")

        # ROIs and Groups read as one section: side by side, no separator between them.
        roi_groups_group = QWidget(self)
        roi_groups_layout = QHBoxLayout(roi_groups_group)
        roi_groups_layout.setContentsMargins(0, 0, 0, 0)
        roi_groups_layout.setSpacing(6)
        roi_groups_layout.addWidget(roi_group)
        roi_groups_layout.addWidget(group_labels_group)

        groups = (
            display_range_group, all_group, masks_group, chromatic_group, background_group,
            roi_groups_group, scale_bar_group, chunk_grid_group,
        )
        self._separators = [ribbon_separator(self) for _ in groups[1:]]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(groups[0])
        for separator, group in zip(self._separators, groups[1:], strict=True):
            layout.addWidget(separator)
            layout.addWidget(group)
        layout.addStretch(1)

        # Last group and its separator: hidden while no OME-Zarr is loaded (a TIFF stack has no chunks).
        def show_chunk_grid_group(available: bool) -> None:
            chunk_grid_group.setVisible(available)
            self._separators[-1].setVisible(available)

        self._chunk_grid_controls.availability_changed.connect(show_chunk_grid_group)
        show_chunk_grid_group(self._chunk_grid_controls.source_chunk_available())

    def add_image_icon(self, button: QWidget, index: int = 0) -> None:
        """Put ``button`` in the "Image" group at ``index``, before the display-range icons (the cursor readout
        toggle first, then the area-selection picker)."""
        self._display_range_controls.insert_leading(button, index)

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

    @property
    def chunk_grid_controls(self) -> ChunkGridControls:
        return self._chunk_grid_controls

    @property
    def display_range_controls(self) -> DisplayRangeControls:
        return self._display_range_controls

    def set_all_overlays(self, shown: bool) -> None:
        """Show all / Hide all: switch every overlay toggle on or off, each through its own button, so it reports
        and redraws its icon exactly as a click does. A toggle already in the wanted state does nothing."""
        shown = bool(shown)
        self._highlight_controls.set_visible(shown)
        self._mask_controls.set_visible(shown)
        self._chromatic_tab.show_button().setChecked(shown)  # the landmarks (not the "all wavelengths" mode)
        self._roi_sample_controls.set_visible(shown)
        self._roi_reference_controls.set_visible(shown)
        self._roi_tab.labels_button.setChecked(shown)
        self._group_label_menu.show_button.setChecked(shown)
        self._scale_bar_controls.set_visible(shown)

    def sync_roi_copies(self) -> None:
        """Copy the ROIs tab's controls onto this tab's copies (after the ROIs tab was set without signals)."""
        self._roi_sample_controls.sync_from(self._roi_tab.sample_controls)
        self._roi_reference_controls.sync_from(self._roi_tab.reference_controls)
        self._roi_placement_copy.sync_from(self._roi_tab.label_placement_picker)
        self._roi_size_copy.sync_from(self._roi_tab.label_size_box)
        self._group_label_menu.sync_from(self._roi_tab.group_label_menu)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._highlight_controls.refresh_theme(theme)
        self._display_range_controls.refresh_theme(theme)
        self._all_controls.refresh_theme(theme)
        self._mask_controls.refresh_theme(theme)
        self._masks_menu.refresh_theme(theme)
        self._roi_sample_controls.refresh_theme(theme)
        self._roi_reference_controls.refresh_theme(theme)
        self._roi_placement_copy.refresh_theme(theme)
        self._roi_size_copy.refresh_theme()
        self._roi_display_menu.refresh_theme(theme)
        self._group_label_menu.refresh_theme()
        self._chunk_grid_controls.refresh_theme()
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        for label in (
            self._display_range_label,
            self._all_label,
            self._masks_label,
            self._chromatic_label,
            self._background_label,
            self._roi_label,
            self._group_labels_label,
            self._scale_bar_label,
            self._chunk_grid_label,
        ):
            label.setStyleSheet(group_label_style())
