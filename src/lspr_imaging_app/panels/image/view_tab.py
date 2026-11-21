"""The Image panel's "View" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: one place for every display option. Most of its controls are
*copies* of controls that live on other tabs (Mask, Chromatic, Background,
ROIs), so a change on either copy shows on the other.

Layout (one ribbon row, captioned groups, thin separators between them):

    [cursor, display range] | [show all, hide all] | [histogram, mask] | [landmarks] | [background] | [sample, reference, labels][group labels] | [scale bar] | [chunk grid]
     Image                   All                      Masks              Chromatic     BG             ROIs                       Groups                Scale bar     Chunks

**An overlay's icon is its switch (2026-11-21, maintainer request).** Each of the overlays above (histogram highlight, mask, landmarks, sample ROIs,
reference rims, ROI labels, group labels, and since 2026-11-22 the scale bar) is one icon: a left click shows or hides it, and a right-click opens
a small menu with the rest of its controls (colour and transparency; the label places and size; the landmarks' "all wavelengths"; the group-label
strip; the scale bar's pixels / micrometers and colour). Before, the Masks and ROIs
icons only opened a menu whose rows held the switches. The menus are `right_click_menu.py`'s; they open below the icon in the tab and beside it
(to its left) in the side ribbon. The Display range's AR / MR switch also opens its numbers on a right-click, and its "..." on either click.

**Two looks, one set of controls (2026-11-21).** `set_vertical(True)` lays the same widgets out as a column of icon buttons with thin horizontal
lines between the sections and no captions: that is how the View controls sit in the side ribbon right of the image (`view_ribbon.py`,
Preferences -> "View controls"). Nothing is copied: every control exists once, and `view_placement.py` moves this widget between the ribbon's
View tab and the side ribbon. Each row of buttons (All, Scale bar, the Chromatic pair, ...) is a plain box layout, so a look is a change of
its direction; the dividers are the same widgets, drawn as horizontal lines (`RibbonSeparator.set_horizontal`). The tab's layout is the
same in both looks: group, divider, group, ...

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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QBoxLayout, QHBoxLayout, QMenu, QToolButton, QWidget

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
from .overlay_all_controls import OverlayAllControls
from .overlay_tint import OverlayTint
from .right_click_menu import MenuOpener, settings_menu
from .roi_label_size_box import RoiLabelSizeBox
from .roi_overlay_controls import RoiOverlayControls
from .roi_tab import RoiTab
from .scale_bar_controls import ScaleBarControls

_COLOUR_HINT = "\nRight-click: colour and transparency."


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
        self._openers: list[MenuOpener] = []  # one per button that has a right-click menu (`_attach`)
        rows: list[QWidget] = []  # the widgets that are a row of buttons in the tab and a column in the side ribbon

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
        rows.append(self._display_range_controls)
        # The AR / MR switch opens the numbers on a right-click; the "..." opens them on either click.
        self._attach(self._display_range_controls.toggle_button, self._display_range_controls.range_menu)
        self._attach(self._display_range_controls.more_button, self._display_range_controls.range_menu, left_click=True)
        display_range_group, self._display_range_label = labeled_icon_group(
            self, self._display_range_controls, "Image"
        )

        # All: Show all / Hide all, wired below once every overlay control exists (`set_all_overlays`).
        self._all_controls = OverlayAllControls(self)
        self._all_controls.show_all_requested.connect(lambda: self.set_all_overlays(True))
        self._all_controls.hide_all_requested.connect(lambda: self.set_all_overlays(False))
        rows.append(self._all_controls)
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
        # Masks: the histogram highlight and the mask tint, one icon each. The icon is the switch; the colour and the
        # transparency (the control without its switch) are in the menu of a right-click.
        highlight_toggle = self._highlight_controls.take_toggle()
        mask_toggle = self._mask_controls.take_toggle()
        self._add_colour_hint(highlight_toggle, mask_toggle)
        self._attach(highlight_toggle, settings_menu(self, [("Histogram highlight", self._highlight_controls)]))
        self._attach(mask_toggle, settings_menu(self, [("Mask", self._mask_controls)]))
        masks_row = self._button_row(highlight_toggle, mask_toggle)
        rows.append(masks_row)
        masks_group, self._masks_label = labeled_icon_group(self, masks_row, "Masks")

        # Chromatic and Background: mirrors of those tabs' own View buttons. The landmarks' icon is the switch; whether they
        # show for every wavelength or for the current one only is in the menu of its right-click.
        self._chromatic_tab = chromatic_tab
        landmarks_mirror = mirror_icon_button(
            chromatic_tab.show_button(), self, tip_suffix="\nRight-click: landmarks of every wavelength, or of the current one."
        )
        scope_mirror = mirror_icon_button(chromatic_tab.scope_button(), self)
        chromatic_mirrors = [landmarks_mirror, scope_mirror]
        self._attach(landmarks_mirror, settings_menu(self, [("Every wavelength", scope_mirror)]))
        background_mirror = mirror_icon_button(background_tab.view_button(), self)
        self._mirrors = [*chromatic_mirrors, background_mirror]
        chromatic_tab.view_buttons_refreshed.connect(lambda: [m.sync() for m in chromatic_mirrors])
        background_tab.view_buttons_refreshed.connect(background_mirror.sync)
        chromatic_group, self._chromatic_label = labeled_icon_group(self, landmarks_mirror, "Chromatic")
        background_group, self._background_label = labeled_icon_group(self, background_mirror, "BG")

        # ROIs: copies of the ROIs tab's Sample / Reference / Labels controls, one icon each (the switch); the sample and
        # reference colour and transparency, and the label place and text size, are in the menu of a right-click.
        self._roi_tab = roi_tab
        self._roi_sample_controls = self._copy_roi_controls("sample", roi_tab.sample_controls)
        self._roi_reference_controls = self._copy_roi_controls("reference", roi_tab.reference_controls)
        labels_mirror = mirror_icon_button(
            roi_tab.labels_button, self, tip_suffix="\nRight-click: where the labels sit, and their text size."
        )
        self._roi_labels_button = labels_mirror
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
        sample_toggle = self._roi_sample_controls.take_toggle()
        reference_toggle = self._roi_reference_controls.take_toggle()
        self._add_colour_hint(sample_toggle, reference_toggle)
        self._attach(sample_toggle, settings_menu(self, [("Sample ROIs", self._roi_sample_controls)]))
        self._attach(reference_toggle, settings_menu(self, [("Reference rims", self._roi_reference_controls)]))
        label_settings = self._button_row(self._roi_placement_copy, self._roi_size_copy, spacing=8)
        self._attach(labels_mirror, settings_menu(self, [("Labels", label_settings)]))
        roi_row = self._button_row(sample_toggle, reference_toggle, labels_mirror)
        rows.append(roi_row)
        roi_group, self._roi_label = labeled_icon_group(self, roi_row, "ROIs")

        # Group labels: a second copy of the ROIs tab's Groups label menu, equal to it both ways. A change made
        # here is handed to the original as if the user had made it there (so the panel redraws and it is saved).
        # Its icon is the switch (`click_toggles`); the strip opens on a right-click.
        original_menu = roi_tab.group_label_menu
        self._group_label_menu = GroupLabelMenu(self, click_toggles=True)
        self._attach(self._group_label_menu, self._group_label_menu.popup_menu)
        self._group_label_menu.sync_from(original_menu)
        self._group_label_menu.changed.connect(lambda: original_menu.sync_from(self._group_label_menu, silent=False))
        original_menu.changed.connect(lambda: self._group_label_menu.sync_from(original_menu))
        group_labels_group, self._group_labels_label = labeled_icon_group(self, self._group_label_menu, "Groups")

        self._scale_bar_controls = ScaleBarControls(geometry, scale_bar_color, self)
        self._scale_bar_controls.color_changed.connect(self.scale_bar_color_changed)
        # Scale bar: the ruler icon is the switch; pixels / micrometers and the colour (the control without its switch) are in the menu of a right-click.
        scale_bar_toggle = self._scale_bar_controls.take_toggle()
        self._attach(scale_bar_toggle, settings_menu(self, [("Scale bar", self._scale_bar_controls)]))
        scale_bar_row = self._button_row(scale_bar_toggle)
        rows.append(scale_bar_row)
        scale_bar_group, self._scale_bar_label = labeled_icon_group(self, scale_bar_row, "Scale bar")

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
        rows.append(roi_groups_group)

        groups = (
            display_range_group, all_group, masks_group, chromatic_group, background_group,
            roi_groups_group, scale_bar_group, chunk_grid_group,
        )
        self._separators = [ribbon_separator(self) for _ in groups[1:]]
        self._vertical = False
        # The widgets that are a row of buttons in the tab and a column in the side ribbon: (widget, its spacing as a row).
        self._rows = [(widget, widget.layout().spacing()) for widget in rows]
        self._captions = [
            self._display_range_label, self._all_label, self._masks_label, self._chromatic_label, self._background_label,
            self._roi_label, self._group_labels_label, self._scale_bar_label, self._chunk_grid_label,
        ]
        layout = QHBoxLayout(self)
        self._layout = layout
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

    # -- the two looks (2026-11-21) -------------------------------------------------------------------------

    def is_vertical(self) -> bool:
        """True while the controls are laid out as the side ribbon's column (`set_vertical`)."""
        return self._vertical

    def set_vertical(self, vertical: bool) -> None:
        """A column of icon buttons (the side ribbon) or the tab's row of captioned groups. The same widgets in both: only their direction,
        the captions and which kind of divider shows change."""
        vertical = bool(vertical)
        if vertical == self._vertical:
            return
        self._vertical = vertical
        direction = QBoxLayout.Direction.TopToBottom if vertical else QBoxLayout.Direction.LeftToRight
        self._layout.setDirection(direction)
        self._layout.setSpacing(3 if vertical else 6)
        for widget, row_spacing in self._rows:
            box = widget.layout()
            box.setDirection(direction)
            box.setSpacing(2 if vertical else row_spacing)
            for index in range(box.count()):
                child = box.itemAt(index).widget()
                if child is not None:  # a 14 px swatch in a 24 px column sits in the middle, not at the edge
                    box.setAlignment(child, Qt.AlignmentFlag.AlignHCenter if vertical else Qt.AlignmentFlag(0))
        for separator in self._separators:
            separator.set_horizontal(vertical)
            self._layout.setAlignment(separator, Qt.AlignmentFlag.AlignHCenter if vertical else Qt.AlignmentFlag(0))
        for caption in self._captions:
            caption.setVisible(not vertical)  # a caption is wider than the column
        for opener in self._openers:
            opener.set_beside(vertical)  # a column sits at the image's edge: its menus open to the left, over the image
        self.updateGeometry()

    # -- the right-click menus (2026-11-21) -----------------------------------------------------------------

    def _attach(self, button: QToolButton, menu: QMenu, *, left_click: bool = False) -> None:
        """Open `menu` when `button` is right-clicked (and when it is left-clicked, for a button that is only a menu opener)."""
        self._openers.append(MenuOpener(button, menu, left_click=left_click, parent=self))

    @staticmethod
    def _add_colour_hint(*buttons: QToolButton) -> None:
        for button in buttons:
            button.setToolTip(button.toolTip() + _COLOUR_HINT)

    def _button_row(self, *widgets: QWidget, spacing: int = 2) -> QWidget:
        """A row of buttons (a column in the side ribbon): a plain box layout, so a look is a change of its direction (`set_vertical`)."""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        for widget in widgets:
            layout.addWidget(widget)
        return row

    @property
    def openers(self) -> list[MenuOpener]:
        return list(self._openers)

    def menu_for(self, button: QToolButton) -> QMenu:
        """The menu a right-click on ``button`` opens."""
        return next(opener.menu for opener in self._openers if opener.button is button)

    @property
    def histogram_toggle(self) -> QToolButton:
        """The histogram highlight's icon: its show / hide switch."""
        return self._highlight_controls.toggle_button

    @property
    def mask_toggle(self) -> QToolButton:
        return self._mask_controls.toggle_button

    @property
    def roi_labels_button(self) -> QToolButton:
        return self._roi_labels_button

    @property
    def group_labels_button(self) -> GroupLabelMenu:
        return self._group_label_menu

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
    def scale_bar_toggle(self) -> QToolButton:
        return self._scale_bar_controls.toggle_button

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
        self._roi_sample_controls.refresh_theme(theme)
        self._roi_reference_controls.refresh_theme(theme)
        self._roi_placement_copy.refresh_theme(theme)
        self._roi_size_copy.refresh_theme()
        self._group_label_menu.refresh_theme()
        self._chunk_grid_controls.refresh_theme()
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        for label in self._captions:
            label.setStyleSheet(group_label_style())
