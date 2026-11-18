"""The Image panel's "ROIs" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: choose how ROIs look on the image, where ROI edits land, what
shape a new ROI has, create / arrange ROIs in arrays and groups, and lock the ROI selection.

Layout (one ribbon row, captioned groups, thin separators between them):

    [display menu] | [scope toggle] | [shape picker] | [array controls] | [group controls] | [lock]
       Display          Scope            Shape             Array              Groups          Selection

**Selection** (2026-11-16, maintainer request): one lock button. While it is on, clicks on the image and in the ROI table no longer change
which ROIs are selected (`SelectionModule.set_roi_selection_locked`, which says what is blocked and what is not); the same switch is the
"Lock selection" entry of both ROI context menus. A change that was refused says so in the status line (the module's `roi_selection_refused`).
The lock is saved in the session with the selection it holds (schema 1.10) and comes back with it; it is not a remembered UI setting.

Display only (CLAUDE.md "panels/"): the colours and visibility are reported
through `overlay_changed` / `labels_toggled`, where the labels sit through
`label_placement_changed`, and the panel applies them to the overlay; ROI edits
go through `ArrayActions` / `GroupActions`, which call the `RoiToolbox` command
API. The View tab carries copies of the Display controls and of the Groups
label menu (`view_tab.py`); it reads them from the public properties here.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...image_tools import BackgroundModule, ChromaticModule, GeometryModule
from ...roi import RoiToolbox
from ...roi.array_task import ArrayAction
from ...roi.scope import RoiEditTarget, RoiScopeModule
from ...selection import SelectionModule
from .. import roi_selection_lock
from ...shortcuts import display
from ..ribbon_group import group_label_style, labeled_icon_group, ribbon_separator
from ..ui_state import UiStateStore
from .array_actions import ArrayActions
from .array_controls import ArrayControls
from .general_group import ICON_SIZE as _RIBBON_ICON_SIZE
from .general_group import style_general_icon_button
from .group_actions import GroupActions
from .group_controls import GroupControls
from .group_label_menu import GroupLabelMenu
from .label_placement_picker import LabelPlacementPicker
from .roi_display_menu import RoiDisplayMenu
from .roi_label_size_box import RoiLabelSizeBox
from .roi_overlay import RoiOverlay
from .roi_overlay_controls import RoiOverlayControls
from .roi_scope_toggle import RoiScopeToggle
from .roi_shape_picker import RoiShapePicker


class RoiTab(QWidget):
    # A Sample / Reference display control changed: (kind "sample"|"reference", field "visible"|"color"|"alpha", value).
    overlay_changed = pyqtSignal(str, str, object)
    labels_toggled = pyqtSignal(bool)
    link_requested = pyqtSignal()  # the Link button (the same as Ctrl+L)
    unlink_requested = pyqtSignal()  # the Unlink button (Ctrl+K)
    links_visible_toggled = pyqtSignal(bool)  # the Links switch: draw a dashed line from each sample to its linked references
    label_placement_changed = pyqtSignal(str)  # one of `label_placement.PLACEMENTS`
    label_size_changed = pyqtSignal(int)  # the ROI labels' text size, pt
    # The labels button's icon was redrawn (toggle, theme change, restore); the View tab's copy follows it.
    labels_icon_refreshed = pyqtSignal()
    group_labels_changed = pyqtSignal()
    shape_changed = pyqtSignal(str)  # the Shape picker's choice: "circle" or "rectangle" (what the Add ROI tool places)
    status = pyqtSignal(str)  # a one-line result from the Array / Groups actions
    auto_crop_readiness_changed = pyqtSignal(object)  # re-emitted from `ArrayActions` for the Image tools tab

    def __init__(
        self,
        overlay: RoiOverlay,
        roi_scope: RoiScopeModule,
        geometry: GeometryModule,
        background: BackgroundModule,
        chromatic: ChromaticModule,
        dataset: DatasetModule,
        roi_toolbox: RoiToolbox,
        selection: SelectionModule,
        array_action: ArrayAction,
        edit_target: RoiEditTarget,
        resolve_reference: Callable[[], tuple[int, float] | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sample_controls = RoiOverlayControls(
            "sample", visible=overlay.sample.visible, color=QColor(overlay.sample.color),
            alpha=overlay.sample.alpha, parent=self,
        )
        self._reference_controls = RoiOverlayControls(
            "reference", visible=overlay.reference.visible, color=QColor(overlay.reference.color),
            alpha=overlay.reference.alpha, parent=self,
        )
        for kind, controls in (("sample", self._sample_controls), ("reference", self._reference_controls)):
            controls.visibility_changed.connect(lambda shown, k=kind: self.overlay_changed.emit(k, "visible", bool(shown)))
            controls.color_changed.connect(lambda color, k=kind: self.overlay_changed.emit(k, "color", color.name()))
            controls.alpha_changed.connect(lambda alpha, k=kind: self.overlay_changed.emit(k, "alpha", float(alpha)))
        self._labels_button = QToolButton(self)
        self._labels_button.setCheckable(True)
        self._labels_button.setChecked(overlay.labels_visible)
        self._labels_button.setToolTip("Show or hide the ROI labels: each ROI's number, and its name if it has one.")
        style_general_icon_button(self._labels_button)
        self._labels_button.toggled.connect(self._on_labels_toggled)
        self._refresh_labels_icon()
        self._label_placement_picker = LabelPlacementPicker(overlay.label_placement, self)
        self._label_placement_picker.setToolTip(
            "Where each ROI's label sits: the middle centres it on the ROI, the others put it just outside the circle."
        )
        self._label_placement_picker.placement_changed.connect(self.label_placement_changed)
        self._label_size_box = RoiLabelSizeBox(self)
        self._label_size_box.valueChanged.connect(self.label_size_changed)
        # One pick-up menu holds the Sample / Reference / Labels rows (roi_display_menu.py).
        self._display_menu = RoiDisplayMenu(
            self._sample_controls, self._reference_controls, self._labels_button, self._label_placement_picker,
            self._label_size_box, self,
        )
        display_group, self._display_caption = labeled_icon_group(self, self._display_menu, "Display")
        self._scope_toggle = RoiScopeToggle(roi_scope, self)
        scope_group, self._scope_caption = labeled_icon_group(self, self._scope_toggle, "Scope")
        self._shape_picker = RoiShapePicker(self)
        self._shape_picker.shape_changed.connect(self.shape_changed)
        shape_group, self._shape_caption = labeled_icon_group(self, self._shape_picker, "Shape")
        self._array_controls = ArrayControls(geometry, self)
        array_group, self._array_caption = labeled_icon_group(self, self._array_controls, "Array")
        self._array_actions = ArrayActions(
            self._array_controls,
            array_action,
            toolbox=roi_toolbox,
            selection=selection,
            geometry=geometry,
            background=background,
            chromatic=chromatic,
            load_plane=dataset.load_plane,
            has_dataset=lambda: bool(dataset.spectral_cubes()),
            raw_plane_shape=dataset.raw_plane_shape,
            resolve_reference=resolve_reference,
            dialog_parent=self,
            edit_target=edit_target,
            parent=self,
        )
        self._array_actions.status.connect(self.status)
        self._array_actions.auto_crop_readiness_changed.connect(self.auto_crop_readiness_changed)
        self._group_controls = GroupControls(self)
        groups_group, self._groups_caption = labeled_icon_group(self, self._group_controls, "Groups")
        self._group_actions = GroupActions(
            self._group_controls,
            toolbox=roi_toolbox,
            selection=selection,
            dialog_parent=self,
            parent=self,
        )
        self._group_actions.status.connect(self.status)
        self._group_controls.label_menu.changed.connect(self.group_labels_changed)

        # "Selection": the lock. The button and the module's flag follow each other (the context menus flip the flag too).
        self._selection = selection
        self._lock_button = QToolButton(self)
        self._lock_button.setCheckable(True)
        self._lock_button.setChecked(selection.roi_selection_locked())
        self._lock_button.setToolTip(roi_selection_lock.TIP)
        style_general_icon_button(self._lock_button)
        self._lock_button.toggled.connect(self._on_lock_toggled)
        selection.roi_selection_lock_changed.connect(self._lock_button.setChecked)
        selection.roi_selection_refused.connect(lambda: self.status.emit(roi_selection_lock.REFUSED_TEXT))
        self._refresh_lock_icon()
        # Link / Unlink act on the selected ROIs (samples and references) and say why in the status bar when they cannot, so they are always enabled.
        self._link_button = QToolButton(self)
        self._unlink_button = QToolButton(self)
        self._links_button = QToolButton(self)
        self._links_button.setCheckable(True)
        self._links_button.setChecked(True)
        self._link_button.setToolTip(f"Link the selected sample ROIs to the selected reference ROIs ({display('links.link')}).")
        self._unlink_button.setToolTip(
            f"Take apart links ({display('links.unlink')}): between the selected samples and references; with only samples selected all their links; "
            "with only references selected, those references from every sample that uses them."
        )
        self._links_button.setToolTip("Show or hide the dashed lines that join each sample ROI to the reference ROIs it is linked to.")
        for button in (self._link_button, self._unlink_button, self._links_button):
            style_general_icon_button(button)
        self._link_button.clicked.connect(lambda _checked=False: self.link_requested.emit())
        self._unlink_button.clicked.connect(lambda _checked=False: self.unlink_requested.emit())
        self._links_button.toggled.connect(self._on_links_toggled)
        self._refresh_link_icons()
        selection_box = QWidget(self)
        selection_row = QHBoxLayout(selection_box)
        selection_row.setContentsMargins(0, 0, 0, 0)
        selection_row.setSpacing(2)
        for button in (self._lock_button, self._link_button, self._unlink_button, self._links_button):
            selection_row.addWidget(button)
        selection_group, self._selection_caption = labeled_icon_group(self, selection_box, "Selection")

        # Sections left to right, a thin separator between neighbours (restyled on a theme switch).
        sections = (display_group, scope_group, shape_group, array_group, groups_group, selection_group)
        self._separators = [ribbon_separator(self) for _ in sections[1:]]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(sections[0])
        for separator, section in zip(self._separators, sections[1:], strict=True):
            layout.addWidget(separator)
            layout.addWidget(section)
        layout.addStretch(1)

    def add_tuning(self, tuning: QWidget) -> None:
        """ROI position tuning (the `TuningTab` row, whole) as this tab's last section, after Groups (2026-10-29: it was a ribbon tab of its own)."""
        separator = ribbon_separator(self)
        self._separators.append(separator)
        at = self.layout().count() - 1  # before the trailing stretch
        self.layout().insertWidget(at, separator)
        self.layout().insertWidget(at + 1, tuning)

    def auto_rotate(self) -> None:
        """Image tools tab's Auto rotate: level the image by the detected array's tilt (`ArrayActions.auto_rotate`)."""
        self._array_actions.auto_rotate()

    def auto_crop(self) -> None:
        """Image tools tab's Auto crop: crop to the ROI lattice (`ArrayActions.auto_crop`)."""
        self._array_actions.auto_crop()

    def auto_crop_readiness(self):
        """Which of Auto crop's dependencies are met right now (`ArrayActions.auto_crop_readiness`) - for the
        Image tools tab's button, which this tab only forwards for (it owns the array tools, that tab owns the
        button)."""
        return self._array_actions.auto_crop_readiness()

    # -- what the View tab copies ------------------------------------------

    @property
    def sample_controls(self) -> RoiOverlayControls:
        return self._sample_controls

    @property
    def reference_controls(self) -> RoiOverlayControls:
        return self._reference_controls

    @property
    def labels_button(self) -> QToolButton:
        return self._labels_button

    @property
    def shape_picker(self) -> RoiShapePicker:
        return self._shape_picker

    @property
    def link_button(self) -> QToolButton:
        return self._link_button

    @property
    def unlink_button(self) -> QToolButton:
        return self._unlink_button

    @property
    def links_button(self) -> QToolButton:
        """The Links switch (checked = the link lines are drawn)."""
        return self._links_button

    @property
    def lock_button(self) -> QToolButton:
        """The ROI selection lock (checked = locked)."""
        return self._lock_button

    @property
    def label_placement_picker(self) -> LabelPlacementPicker:
        return self._label_placement_picker

    @property
    def label_size_box(self) -> RoiLabelSizeBox:
        return self._label_size_box

    @property
    def group_label_menu(self) -> GroupLabelMenu:
        return self._group_controls.label_menu

    def group_label_settings(self):
        """The Groups label menu's current settings (what the overlay draws)."""
        return self._group_controls.label_menu.settings()

    # -- state -----------------------------------------------------------

    def show_style(self, overlay: RoiOverlay) -> None:
        """Show the overlay's display options on the controls (after a restore)
        without those controls reporting them back."""
        self._sample_controls.set_state(
            visible=overlay.sample.visible, color=QColor(overlay.sample.color), alpha=overlay.sample.alpha
        )
        self._reference_controls.set_state(
            visible=overlay.reference.visible, color=QColor(overlay.reference.color), alpha=overlay.reference.alpha
        )
        blocked = self._labels_button.blockSignals(True)
        self._labels_button.setChecked(overlay.labels_visible)
        self._labels_button.blockSignals(blocked)
        self._label_placement_picker.set_placement(overlay.label_placement, emit=False)
        blocked = self._label_size_box.blockSignals(True)
        self._label_size_box.setValue(round(overlay.label_point_size))
        self._label_size_box.blockSignals(blocked)
        self._refresh_labels_icon()

    def set_label_placement(self, placement: str) -> None:
        """Pick where the ROI labels sit, as a click on the grid would (reported through `label_placement_changed`)."""
        self._label_placement_picker.set_placement(placement)

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Remember the Array, ROI-label place and Groups-label settings (see `panels/ui_state.py`)."""
        self._array_controls.bind_ui_state(store)
        self._label_placement_picker.bind_ui_state(store, "image/roi_label_placement")
        store.bind("image/roi_label_point_size", self._label_size_box)
        self._group_controls.label_menu.bind_ui_state(store)
        self._shape_picker.bind_ui_state(store, "image/roi_shape")
        store.bind("image/roi_show_links", self._links_button)
        # (The lock is not bound here: it belongs to the session, next to the selection it holds - `SessionState.selection_locked`.)

    def _on_lock_toggled(self, locked: bool) -> None:
        self._selection.set_roi_selection_locked(bool(locked))
        self._refresh_lock_icon()

    def _refresh_lock_icon(self) -> None:
        """An open padlock while the selection is free, a closed one in the accent colour while it is locked (the ribbon's
        toggles are accent-coloured when on; the area selection's lock on the toolbar beside the image is amber)."""
        theme = get_active_theme()
        on = self._lock_button.isChecked()
        self._lock_button.setIcon(
            load_tabler_icon(
                "lock" if on else "lock-open",
                color=theme.accent_blue if on else theme.text_secondary,
                size=_RIBBON_ICON_SIZE * 2,
                stroke_width=2.1,
            )
        )

    def _on_links_toggled(self, shown: bool) -> None:
        self._refresh_link_icons()
        self.links_visible_toggled.emit(bool(shown))

    def _refresh_link_icons(self) -> None:
        theme = get_active_theme()
        on = self._links_button.isChecked()
        for button, name, color in (
            (self._link_button, "link", theme.text_secondary),
            (self._unlink_button, "link-off", theme.text_secondary),
            (self._links_button, "eye" if on else "eye-off", theme.accent_blue if on else theme.text_dim),
        ):
            button.setIcon(load_tabler_icon(name, color=color, size=_RIBBON_ICON_SIZE * 2, stroke_width=2.1))

    def _on_labels_toggled(self, shown: bool) -> None:
        self._refresh_labels_icon()
        self.labels_toggled.emit(bool(shown))

    def _refresh_labels_icon(self) -> None:
        theme = get_active_theme()
        on = self._labels_button.isChecked()
        self._labels_button.setIcon(
            load_tabler_icon(
                "label-important" if on else "label-off",
                color=theme.accent_blue if on else theme.text_dim,
                size=_RIBBON_ICON_SIZE * 2,
                stroke_width=2.1,
            )
        )
        self.labels_icon_refreshed.emit()

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._sample_controls.refresh_theme(theme)
        self._reference_controls.refresh_theme(theme)
        style_general_icon_button(self._labels_button)
        self._refresh_labels_icon()
        style_general_icon_button(self._lock_button)
        self._refresh_lock_icon()
        for button in (self._link_button, self._unlink_button, self._links_button):
            style_general_icon_button(button)
        self._refresh_link_icons()
        self._label_placement_picker.refresh_theme(theme)
        self._label_size_box.refresh_theme()
        self._display_menu.refresh_theme(theme)
        self._group_controls.refresh_theme()
        self._shape_picker.refresh_theme()
        self._array_controls.refresh_theme()
        self._scope_toggle.refresh_theme(theme)
        for separator in self._separators:
            separator.setStyleSheet(f"color: {theme.control_border};")
        for caption in (
            self._display_caption,
            self._scope_caption,
            self._shape_caption,
            self._array_caption,
            self._groups_caption,
            self._selection_caption,
        ):
            caption.setStyleSheet(group_label_style())
