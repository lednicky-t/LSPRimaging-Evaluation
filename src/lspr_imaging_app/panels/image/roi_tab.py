"""The Image panel's "ROIs" ribbon tab (split out of `panel.py` 2026-10-10).

Screen purpose: choose how ROIs look on the image, where ROI edits land, what
shape a new ROI has, and create / arrange ROIs in arrays and groups.

Layout (one ribbon row, captioned groups, thin separators between them):

    [display menu] | [scope toggle] | [shape picker] | [array controls] | [group controls]
       Display          Scope            Shape             Array              Groups

Display only (CLAUDE.md "panels/"): the colours and visibility are reported
through `overlay_changed` / `labels_toggled` and the panel applies them to the
overlay; ROI edits go through `ArrayActions` / `GroupActions`, which call the
`RoiToolbox` command API. The View tab carries copies of the Display controls
(`view_tab.py`); it reads them from the public properties here.
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
from ..ribbon_group import group_label_style, labeled_icon_group, vertical_separator
from ..ui_state import UiStateStore
from .array_actions import ArrayActions
from .array_controls import ArrayControls
from .general_group import ICON_SIZE as _RIBBON_ICON_SIZE
from .general_group import style_general_icon_button
from .group_actions import GroupActions
from .group_controls import GroupControls
from .roi_display_menu import RoiDisplayMenu
from .roi_overlay import RoiOverlay
from .roi_overlay_controls import RoiOverlayControls
from .roi_scope_toggle import RoiScopeToggle
from .roi_shape_picker import RoiShapePicker


class RoiTab(QWidget):
    # A Sample / Reference display control changed: (kind "sample"|"reference", field "visible"|"color"|"alpha", value).
    overlay_changed = pyqtSignal(str, str, object)
    labels_toggled = pyqtSignal(bool)
    # The labels button's icon was redrawn (toggle, theme change, restore); the View tab's copy follows it.
    labels_icon_refreshed = pyqtSignal()
    group_labels_changed = pyqtSignal()
    status = pyqtSignal(str)  # a one-line result from the Array / Groups actions

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
        analysis_running: Callable[[], bool],
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
        # One pick-up menu holds the Sample / Reference / Labels rows (roi_display_menu.py).
        self._display_menu = RoiDisplayMenu(self._sample_controls, self._reference_controls, self._labels_button, self)
        display_group, self._display_caption = labeled_icon_group(self, self._display_menu, "Display")
        self._scope_toggle = RoiScopeToggle(roi_scope, self)
        scope_group, self._scope_caption = labeled_icon_group(self, self._scope_toggle, "Scope")
        self._shape_picker = RoiShapePicker(self)
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
            resolve_reference=resolve_reference,
            analysis_running=analysis_running,
            dialog_parent=self,
            edit_target=edit_target,
            parent=self,
        )
        self._array_actions.status.connect(self.status)
        self._group_controls = GroupControls(self)
        groups_group, self._groups_caption = labeled_icon_group(self, self._group_controls, "Groups")
        self._group_actions = GroupActions(
            self._group_controls,
            toolbox=roi_toolbox,
            selection=selection,
            analysis_running=analysis_running,
            dialog_parent=self,
            parent=self,
        )
        self._group_actions.status.connect(self.status)
        self._group_controls.label_menu.changed.connect(self.group_labels_changed)

        # Sections left to right, a thin separator between neighbours (restyled on a theme switch).
        sections = (display_group, scope_group, shape_group, array_group, groups_group)
        self._separators = [vertical_separator(self) for _ in sections[1:]]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(sections[0])
        for separator, section in zip(self._separators, sections[1:], strict=True):
            layout.addWidget(separator)
            layout.addWidget(section)
        layout.addStretch(1)

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
        self._refresh_labels_icon()

    def bind_ui_state(self, store: UiStateStore) -> None:
        """Remember the Array and Groups-label settings (see `panels/ui_state.py`)."""
        self._array_controls.bind_ui_state(store)
        self._group_controls.label_menu.bind_ui_state(store)

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
        ):
            caption.setStyleSheet(group_label_style())
