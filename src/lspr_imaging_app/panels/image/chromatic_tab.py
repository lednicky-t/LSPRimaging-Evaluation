"""The Image panel's "Chromatic" ribbon tab (full name "Chromatic Corrections",
shown in the tab's tooltip; 2026-10-04).

Screen purpose: find the chromatic-aberration correction automatically, see
whether it worked, and switch it on or off.

Layout (one ribbon row, captioned icon groups like the Mask tab):

    [Run] [gear]  |  [eye] [stack]  |  [wand] [trash]  |  status text
     Detect            View                Correction            progress bar (only while running)

- **Run** finds landmarks on the reference frame and follows them through
  the reference cube's wavelengths (`ChromaticAutoDetect`, off the GUI
  thread). While running it turns into **Cancel**.
- **Gear** opens the settings popover: a Default / Detailed switch on top
  (Default = 15 landmarks, every 3rd wavelength; Detailed = 32 landmarks,
  every wavelength), then the landmark count, the wavelength spread, the
  advanced values (feature size, step limit, border) and a short note on why
  one static correction is used. Editing a value by hand deselects both
  presets. The values are saved (app settings) when a run succeeds, i.e.
  when they were actually applied.
- **View group:** show/hide the landmark overlay (eye), and choose whether
  it shows the current wavelength only or every wavelength at once (stack).
  Each wavelength has its own colour (`wavelength_color.py`). With the
  correction switched on the landmarks are shown corrected, so each one's
  wavelengths converge to a single point (the check that the correction
  works); switched off they show the raw chromatic shift.
- **Correction group (always visible, not in the popover):** switch the
  correction on/off (wand), clear landmarks and correction (trash). While the
  correction is on, the panel turns this ribbon tab green
  (`ImageToolRibbon.set_tab_applied`).
- **Status** states the last result in one line ("15/15 landmarks, fit
  error 0.14 px, 10 of 26 wavelengths"), or what went wrong. Failures are
  always shown, never swallowed.

Display only (CLAUDE.md "panels/"): the numbers come from
`ChromaticAutoDetect`, the model state from `ChromaticModule`; nothing is
computed here. There is no search rectangle - landmarks are searched inside
the image minus a border (5 % by default, set in the popover).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from ...dataset import DatasetModule
from ...dataset.model import is_dark_frame_wavelength
from ...image_tools import ChromaticModule
from ...image_tools.chromatic.auto_landmarks import grid_for_count, sample_count_for_stride, snap_landmark_count
from ...image_tools.chromatic.auto_task import TASK_ID, AutoDetectSummary, ChromaticAutoDetect
from ...image_tools.geometry import GeometryModule
from ..ribbon_group import group_label_style, labeled_icon_group, vertical_separator
from .general_group import ICON_SIZE, style_general_icon_button

DEFAULT_PRESET = (15, 3)  # (landmark count, track every Nth wavelength)
DETAILED_PRESET = (32, 1)
_STATUS_MIN_WIDTH_PX = 60  # the status text shrinks (elided) so this tab never widens the Image panel
_STATUS_FONT_PX = 11
_RENDER_SIZE = ICON_SIZE * 2
_STROKE_WIDTH = 2.1

WHY_STATIC_TEXT = (
    "Why one correction for the whole run? Chromatic aberration comes from the "
    "optics, not the sample, so it should not change during a measurement. "
    "On a 314-cube bulk-sensitivity test, 4 of 5 evenly spaced cubes agreed "
    "with the first to about 0.04-0.05 px rms (tracking noise alone is about "
    "0.07 px). During one liquid exchange the correction differed by up to "
    "0.7 px at the worst point (0.2 px rms), about 3 % of the correction. "
    "If the optics or focus change, run again on a cube after the change."
)


@dataclass(frozen=True)
class ChromaticUiValues:
    """The popover's values; saved to the app settings when a run succeeds."""

    landmark_count: int = DEFAULT_PRESET[0]
    stride: int = DEFAULT_PRESET[1]
    border_percent: float = 5.0
    max_step_px: float = 5.0
    feature_diameter_px: float | None = None  # None = measure from the image


class _SegmentedSwitch(QWidget):
    """Horizontal two-way switch (not a drop-down). `chosen(label)` fires on
    a user pick; `set_choice(None)` shows neither as selected."""

    chosen = pyqtSignal(str)

    def __init__(self, labels: tuple[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for index, label in enumerate(labels):
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("segment", "left" if index == 0 else "right")
            button.clicked.connect(lambda _checked=False, text=label: self.chosen.emit(text))
            self._group.addButton(button)
            layout.addWidget(button)
            self._buttons[label] = button
        self.refresh_theme(get_active_theme())

    def set_choice(self, label: str | None) -> None:
        # An exclusive group cannot be left with nothing checked while it has
        # an active button, so lift exclusivity for the "neither" state.
        self._group.setExclusive(False)
        for text, button in self._buttons.items():
            button.setChecked(text == label)
        self._group.setExclusive(True)

    def choice(self) -> str | None:
        return next((text for text, button in self._buttons.items() if button.isChecked()), None)

    def button(self, label: str) -> QPushButton:
        return self._buttons[label]

    def refresh_theme(self, theme: GuiTheme) -> None:
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {theme.control_bg}; color: {theme.text_muted};
                border: 1px solid {theme.control_border}; padding: 3px 14px;
            }}
            QPushButton[segment="left"] {{ border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}
            QPushButton[segment="right"] {{ border-top-right-radius: 4px; border-bottom-right-radius: 4px; border-left: none; }}
            QPushButton:hover {{ background: {theme.control_bg_hover}; }}
            QPushButton:checked {{ background: {theme.primary_action_bg}; color: {theme.text_primary}; border-color: {theme.accent_blue}; }}
            """
        )


class _StatusLabel(QLabel):
    """One-line label that elides to whatever width it is given (full text in
    the tooltip). Its minimum size does not depend on its text, so a long
    message cannot force the whole Image panel wider."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(_STATUS_MIN_WIDTH_PX)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, max(self.width(), 1)))


class _SettingsPopover(QWidget):
    """Contents of the gear menu. Holds widget state only; `ChromaticCorrectionTab`
    reads it through the accessors and owns every action."""

    changed = pyqtSignal()

    def __init__(self, aspect_provider: Callable[[], float], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aspect_provider = aspect_provider
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self._switch = _SegmentedSwitch(("Default", "Detailed"), self)
        self._switch.chosen.connect(self._on_preset_chosen)
        layout.addWidget(self._switch, 0, Qt.AlignmentFlag.AlignLeft)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        self._count = QSpinBox(self)
        self._count.setRange(6, 200)
        self._count.setToolTip("How many landmarks to track. Snaps to a count that forms an even grid (15 = 5 x 3, 32 = 8 x 4).")
        self._count.editingFinished.connect(self._snap_count)
        self._count.valueChanged.connect(self._on_manual_edit)
        self._grid_hint = QLabel(self)
        count_row = QHBoxLayout()
        count_row.addWidget(self._count)
        count_row.addWidget(self._grid_hint, 1)
        form.addRow("Landmarks", count_row)

        self._stride = QSpinBox(self)
        self._stride.setRange(1, 10)
        self._stride.setPrefix("every ")
        self._stride.setToolTip("1 = follow the landmarks on every wavelength; N = every Nth, the rest are interpolated.")
        self._stride.valueChanged.connect(self._on_manual_edit)
        self._spread_hint = QLabel(self)
        stride_row = QHBoxLayout()
        stride_row.addWidget(self._stride)
        stride_row.addWidget(self._spread_hint, 1)
        form.addRow("Wavelengths", stride_row)
        layout.addLayout(form)

        rule = QFrame(self)
        rule.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(rule)
        advanced = QLabel("Advanced", self)
        advanced.setStyleSheet("font-weight: 600;")
        layout.addWidget(advanced)
        advanced_form = QFormLayout()
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setHorizontalSpacing(10)
        advanced_form.setVerticalSpacing(4)
        self._auto_size = QCheckBox("measure from the image", self)
        self._auto_size.setChecked(True)
        self._diameter = QDoubleSpinBox(self)
        self._diameter.setRange(3.0, 400.0)
        self._diameter.setDecimals(0)
        self._diameter.setSuffix(" px")
        self._diameter.setValue(40.0)
        self._diameter.setEnabled(False)
        self._auto_size.toggled.connect(lambda auto: self._diameter.setEnabled(not auto))
        size_row = QHBoxLayout()
        size_row.addWidget(self._auto_size)
        size_row.addWidget(self._diameter)
        advanced_form.addRow("Feature size", size_row)
        self._max_step = QDoubleSpinBox(self)
        self._max_step.setRange(1.0, 30.0)
        self._max_step.setDecimals(1)
        self._max_step.setSuffix(" px")
        self._max_step.setValue(5.0)
        self._max_step.setToolTip("Largest shift expected between neighbouring wavelengths. A smaller value searches less and is safer.")
        advanced_form.addRow("Max step", self._max_step)
        self._border = QDoubleSpinBox(self)
        self._border.setRange(0.0, 20.0)
        self._border.setDecimals(0)
        self._border.setSuffix(" %")
        self._border.setValue(5.0)
        self._border.setToolTip("Landmarks are searched inside the image minus this border on every side.")
        advanced_form.addRow("Border", self._border)
        layout.addLayout(advanced_form)

        self._why = QLabel(WHY_STATIC_TEXT, self)
        self._why.setWordWrap(True)
        self._why.setMaximumWidth(340)
        layout.addWidget(self._why)

        for spin in (self._diameter, self._max_step, self._border):
            spin.valueChanged.connect(lambda _v: self.changed.emit())
        self._auto_size.toggled.connect(lambda _v: self.changed.emit())

        self.apply_preset(*DEFAULT_PRESET)
        self.refresh_theme(get_active_theme())

    # -- presets and snapping ----------------------------------------------

    def apply_preset(self, count: int, stride: int) -> None:
        for spin, value in ((self._count, count), (self._stride, stride)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._switch.set_choice("Default" if (count, stride) == DEFAULT_PRESET else "Detailed" if (count, stride) == DETAILED_PRESET else None)
        self._update_hints()
        self.changed.emit()

    def _on_preset_chosen(self, label: str) -> None:
        self.apply_preset(*(DEFAULT_PRESET if label == "Default" else DETAILED_PRESET))

    def _on_manual_edit(self, _value: int) -> None:
        pair = (self._count.value(), self._stride.value())
        self._switch.set_choice("Default" if pair == DEFAULT_PRESET else "Detailed" if pair == DETAILED_PRESET else None)
        self._update_hints()
        self.changed.emit()

    def _snap_count(self) -> None:
        snapped = snap_landmark_count(self._count.value(), self._aspect_provider())
        if snapped != self._count.value():
            self._count.setValue(snapped)  # triggers _on_manual_edit

    def _update_hints(self) -> None:
        try:
            nx, ny = grid_for_count(self._count.value(), self._aspect_provider())
            self._grid_hint.setText(f"{nx} x {ny} grid")
        except ValueError:
            self._grid_hint.setText("no even grid")

    def set_spread_hint(self, text: str) -> None:
        self._spread_hint.setText(text)

    # -- accessors -------------------------------------------------------

    def landmark_count(self) -> int:
        return int(self._count.value())

    def stride(self) -> int:
        return int(self._stride.value())

    def border_fraction(self) -> float:
        return float(self._border.value()) / 100.0

    def max_step_px(self) -> float:
        return float(self._max_step.value())

    def feature_diameter_px(self) -> float | None:
        return None if self._auto_size.isChecked() else float(self._diameter.value())

    def values(self) -> ChromaticUiValues:
        return ChromaticUiValues(
            landmark_count=self.landmark_count(),
            stride=self.stride(),
            border_percent=float(self._border.value()),
            max_step_px=self.max_step_px(),
            feature_diameter_px=self.feature_diameter_px(),
        )

    def set_values(self, values: ChromaticUiValues) -> None:
        """Programmatic restore (no `changed` storm): sets every field, then
        refreshes the preset switch and hints once."""
        for spin, value in (
            (self._count, values.landmark_count),
            (self._stride, values.stride),
            (self._border, values.border_percent),
            (self._max_step, values.max_step_px),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._auto_size.setChecked(values.feature_diameter_px is None)
        if values.feature_diameter_px is not None:
            self._diameter.setValue(values.feature_diameter_px)
        pair = (self._count.value(), self._stride.value())
        self._switch.set_choice("Default" if pair == DEFAULT_PRESET else "Detailed" if pair == DETAILED_PRESET else None)
        self._update_hints()

    def switch(self) -> _SegmentedSwitch:
        return self._switch

    def refresh_theme(self, theme: GuiTheme) -> None:
        self._switch.refresh_theme(theme)
        muted = f"color: {theme.text_dim}; font-size: {_STATUS_FONT_PX - 1}px;"
        self._grid_hint.setStyleSheet(muted)
        self._spread_hint.setStyleSheet(muted)
        self._why.setStyleSheet(muted)


class ChromaticCorrectionTab(QWidget):
    """Content of the "Chromatic Corrections" ribbon tab."""

    show_landmarks_changed = pyqtSignal(bool)
    landmark_scope_changed = pyqtSignal(bool)  # True = all wavelengths, False = current wavelength only
    settings_applied = pyqtSignal(object)  # ChromaticUiValues, after a successful run

    def __init__(
        self,
        chromatic: ChromaticModule,
        auto: ChromaticAutoDetect,
        dataset: DatasetModule,
        geometry: GeometryModule,
        resolve_reference: Callable[[], tuple[int, float] | None],
        aspect_provider: Callable[[], float],
        parent: QWidget | None = None,
        initial_values: ChromaticUiValues | None = None,
        initial_show_landmarks: bool = True,
        initial_all_wavelengths: bool = False,
    ) -> None:
        super().__init__(parent)
        self._chromatic = chromatic
        self._auto = auto
        self._dataset = dataset
        self._geometry = geometry
        self._resolve_reference = resolve_reference
        self._last_summary: AutoDetectSummary | None = None
        self._error_text: str | None = None
        self._busy_text: str | None = None
        self._values_in_flight: ChromaticUiValues | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self._run_button = QToolButton(self)
        style_general_icon_button(self._run_button)
        self._run_button.clicked.connect(self._on_run_clicked)

        self._gear_button = QToolButton(self)
        style_general_icon_button(self._gear_button)
        self._gear_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._gear_button.setToolTip("Chromatic correction settings")
        self._popover = _SettingsPopover(aspect_provider, self)
        if initial_values is not None:
            self._popover.set_values(initial_values)
        menu = QMenu(self._gear_button)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self._popover)
        menu.addAction(action)
        self._gear_button.setMenu(menu)
        detect_row = QWidget(self)
        detect_layout = QHBoxLayout(detect_row)
        detect_layout.setContentsMargins(0, 0, 0, 0)
        detect_layout.setSpacing(2)
        detect_layout.addWidget(self._run_button)
        detect_layout.addWidget(self._gear_button)
        detect_group, self._detect_caption = labeled_icon_group(self, detect_row, "Detect")
        layout.addWidget(detect_group, 0, Qt.AlignmentFlag.AlignVCenter)
        self._group_separators = [vertical_separator(self)]
        layout.addWidget(self._group_separators[0])

        # View group: what the overlay shows.
        self._show_button = QToolButton(self)
        style_general_icon_button(self._show_button)
        self._show_button.setCheckable(True)
        self._show_button.setChecked(bool(initial_show_landmarks))
        self._show_button.toggled.connect(self._on_show_toggled)
        self._scope_button = QToolButton(self)
        style_general_icon_button(self._scope_button)
        self._scope_button.setCheckable(True)  # checked = every wavelength, unchecked = the current one
        self._scope_button.setChecked(bool(initial_all_wavelengths))
        self._scope_button.toggled.connect(self._on_scope_toggled)
        view_row = QWidget(self)
        view_layout = QHBoxLayout(view_row)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(2)
        view_layout.addWidget(self._show_button)
        view_layout.addWidget(self._scope_button)
        view_group, self._view_caption = labeled_icon_group(self, view_row, "View")
        layout.addWidget(view_group, 0, Qt.AlignmentFlag.AlignVCenter)
        self._group_separators.append(vertical_separator(self))
        layout.addWidget(self._group_separators[-1])

        # Correction group: always visible (not in the popover).
        self._apply_button = QToolButton(self)
        style_general_icon_button(self._apply_button)
        self._apply_button.setCheckable(True)
        self._apply_button.toggled.connect(self._on_apply_toggled)
        self._clear_button = QToolButton(self)
        style_general_icon_button(self._clear_button)
        self._clear_button.clicked.connect(self._on_clear)
        correction_row = QWidget(self)
        correction_layout = QHBoxLayout(correction_row)
        correction_layout.setContentsMargins(0, 0, 0, 0)
        correction_layout.setSpacing(2)
        for button in (self._apply_button, self._clear_button):
            correction_layout.addWidget(button)
        correction_group, self._correction_caption = labeled_icon_group(self, correction_row, "Correction")
        layout.addWidget(correction_group, 0, Qt.AlignmentFlag.AlignVCenter)

        self._separator = QFrame(self)
        self._separator.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(self._separator)

        status_column = QVBoxLayout()
        status_column.setContentsMargins(0, 0, 0, 0)
        status_column.setSpacing(3)
        self._status = _StatusLabel(self)
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setMinimumWidth(_STATUS_MIN_WIDTH_PX)
        self._progress.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._progress.setVisible(False)
        status_column.addStretch(1)
        status_column.addWidget(self._status)
        status_column.addWidget(self._progress)
        status_column.addStretch(1)
        layout.addLayout(status_column, 1)

        self._popover.changed.connect(self._refresh_spread_hint)

        auto.task_progress.connect(self._on_progress)
        auto.running_changed.connect(self._on_running_changed)
        auto.completed.connect(self._on_completed)
        auto.failed.connect(self._on_failed)
        auto.cancelled.connect(self._on_cancelled)
        chromatic.chromatic_model_changed.connect(self._refresh)
        dataset.dataset_loaded.connect(self._refresh)
        dataset.dataset_cleared.connect(self._refresh)

        self.refresh_theme(get_active_theme())
        self._refresh()

    # -- accessors for tests / the panel -------------------------------------

    def popover(self) -> _SettingsPopover:
        return self._popover

    def run_button(self) -> QToolButton:
        return self._run_button

    def status_text(self) -> str:
        return self._status.full_text()

    def show_landmarks(self) -> bool:
        return self._show_button.isChecked()

    def show_button(self) -> QToolButton:
        return self._show_button

    def scope_button(self) -> QToolButton:
        return self._scope_button

    def all_wavelengths(self) -> bool:
        return self._scope_button.isChecked()

    def apply_button(self) -> QToolButton:
        return self._apply_button

    def clear_button(self) -> QToolButton:
        return self._clear_button

    def values(self) -> ChromaticUiValues:
        return self._popover.values()

    # -- actions --------------------------------------------------------

    def _on_run_clicked(self) -> None:
        if self._auto.is_running():
            self._auto.cancel()
            self._busy_text = "Cancelling..."
            self._refresh()
            return
        reference = self._resolve_reference()
        if reference is None:
            self._fail("Choose a reference frame first (Workflow panel, Reference frame).")
            return
        cube, wavelength = int(reference[0]), float(reference[1])
        if is_dark_frame_wavelength(wavelength):
            self._fail("The reference wavelength is the dark frame (0 nm). Choose a spectral wavelength as reference.")
            return
        image_keys = [
            (int(c), float(w))
            for c in self._dataset.spectral_cubes()
            for w in self._dataset.wavelengths_for_cube(c)
            if not is_dark_frame_wavelength(w)
        ]
        in_cube = sum(1 for c, _w in image_keys if c == cube)
        if in_cube < 2:
            self._fail("The reference cube has fewer than two spectral wavelengths.")
            return
        self._error_text = None
        self._last_summary = None
        self._values_in_flight = self._popover.values()
        self._auto.start(
            cube_index=cube,
            reference_wavelength_nm=wavelength,
            image_keys=image_keys,
            landmark_count=self._popover.landmark_count(),
            sample_image_count=sample_count_for_stride(in_cube, self._popover.stride()),
            border_fraction=self._popover.border_fraction(),
            max_step_px=self._popover.max_step_px(),
            feature_diameter_px=self._popover.feature_diameter_px(),
            geometry=self._geometry.settings(),
            load_plane=self._dataset.load_plane,
        )

    def _on_show_toggled(self, visible: bool) -> None:
        self._refresh_correction_buttons()
        self.show_landmarks_changed.emit(visible)

    def _on_scope_toggled(self, all_wavelengths: bool) -> None:
        self._refresh_correction_buttons()
        self.landmark_scope_changed.emit(all_wavelengths)

    def _on_apply_toggled(self, checked: bool) -> None:
        self._chromatic.set_correction_enabled(checked)

    def _refresh_correction_buttons(self) -> None:
        theme = get_active_theme()
        has_model = bool(self._chromatic.models())
        enabled = self._chromatic.settings().chromatic_correction_enabled
        self._apply_button.blockSignals(True)
        self._apply_button.setChecked(enabled)
        self._apply_button.blockSignals(False)
        self._apply_button.setEnabled(has_model)
        self._clear_button.setEnabled(has_model or bool(self._chromatic.landmarks()))
        show = self._show_button.isChecked()
        self._show_button.setIcon(
            load_tabler_icon(
                "eye" if show else "eye-off",
                color=theme.accent_blue if show else theme.text_muted,
                size=_RENDER_SIZE,
                stroke_width=_STROKE_WIDTH,
            )
        )
        self._show_button.setToolTip(
            "Landmarks shown on the image (click to hide). With the correction on they are shown corrected: "
            "each landmark's wavelengths should fall on one point"
            if show
            else "Landmarks hidden (click to show)"
        )
        every = self._scope_button.isChecked()
        self._scope_button.setEnabled(show)
        self._scope_button.setIcon(
            load_tabler_icon(
                "stack-3" if every else "stack-middle",
                color=theme.accent_blue if every and show else theme.text_muted,
                size=_RENDER_SIZE,
                stroke_width=_STROKE_WIDTH,
            )
        )
        self._scope_button.setToolTip(
            "Showing the landmarks of every wavelength at once, each in its own colour (click for the current wavelength only)"
            if every
            else "Showing the landmarks of the current wavelength only, in that wavelength's colour (click to show every wavelength)"
        )
        self._apply_button.setIcon(
            load_tabler_icon(
                "wand" if enabled else "wand-off",
                color=theme.accent_green if enabled else theme.text_muted,
                size=_RENDER_SIZE,
                stroke_width=_STROKE_WIDTH,
            )
        )
        self._apply_button.setToolTip(
            "Chromatic correction is ON (click to switch off)"
            if enabled
            else "Chromatic correction is OFF (click to switch on)"
            if has_model
            else "No correction yet - press play to compute one"
        )
        self._clear_button.setIcon(
            load_tabler_icon("trash", color=theme.text_muted, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        )
        self._clear_button.setToolTip("Clear the landmarks and the correction")

    def _on_clear(self) -> None:
        self._chromatic.clear_landmarks()
        self._chromatic.set_correction_enabled(False)
        self._last_summary = None
        self._error_text = None
        self._refresh()

    # -- auto-detect signals ---------------------------------------------

    def _on_progress(self, task_id: str, _label: str, fraction: float, message: str) -> None:
        if task_id != TASK_ID:
            return
        self._progress.setValue(int(fraction * 1000))
        self._busy_text = message
        self._set_status(message)

    def _on_running_changed(self, running: bool) -> None:
        self._progress.setVisible(running)
        self._progress.setValue(0)
        if not running:
            self._busy_text = None
        self._refresh()

    def _on_completed(self, summary: object) -> None:
        self._last_summary = summary  # type: ignore[assignment]
        self._error_text = None
        if self._values_in_flight is not None:
            self.settings_applied.emit(self._values_in_flight)  # applied: remember these values
        self._refresh()

    def _on_failed(self, message: str) -> None:
        self._fail(message)

    def _on_cancelled(self) -> None:
        self._error_text = None
        self._last_summary = None
        self._set_status("Cancelled.")

    def _fail(self, message: str) -> None:
        self._error_text = message
        self._refresh()

    # -- state -------------------------------------------------------------

    def _refresh_spread_hint(self) -> None:
        reference = self._resolve_reference()
        if reference is None:
            self._popover.set_spread_hint("")
            return
        wavelengths = [w for w in self._dataset.wavelengths_for_cube(int(reference[0])) if not is_dark_frame_wavelength(w)]
        if not wavelengths:
            self._popover.set_spread_hint("")
            return
        used = sample_count_for_stride(len(wavelengths), self._popover.stride())
        self._popover.set_spread_hint(f"{used} of {len(wavelengths)} wavelengths")

    def _refresh(self, *_args: object) -> None:
        running = self._auto.is_running()
        has_dataset = bool(self._dataset.wavelengths())
        has_model = bool(self._chromatic.models())
        enabled = self._chromatic.settings().chromatic_correction_enabled
        self._refresh_correction_buttons()
        self._refresh_spread_hint()
        self._run_button.setEnabled(has_dataset)
        name, tip = ("player-stop", "Cancel") if running else ("player-play", "Find landmarks and fit the chromatic correction")
        theme = get_active_theme()
        self._run_button.setIcon(
            load_tabler_icon(name, color=theme.accent_red if running else theme.accent_green, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH)
        )
        self._run_button.setToolTip(tip)
        if running:
            self._set_status(self._busy_text or "Starting...")
        elif not has_dataset:
            self._set_status("Load a dataset first.")
        elif self._error_text:
            self._set_status(self._error_text, error=True)
        elif self._last_summary is not None:
            self._set_status(self._summary_text(self._last_summary))
        elif has_model:
            landmarks = {mark.landmark_id for mark in self._chromatic.landmarks()}
            state = "on" if enabled else "off"
            self._set_status(f"Correction fitted from {len(landmarks)} landmarks ({state}).")
        else:
            self._set_status("Not computed. Press play to find landmarks and fit the correction.")

    @staticmethod
    def _summary_text(summary: AutoDetectSummary) -> str:
        text = (
            f"{summary.kept_count}/{summary.requested_count} landmarks, fit error {summary.loo_mean_px:.2f} px "
            f"(worst {summary.loo_max_px:.2f}), {summary.tracked_wavelength_count} of {summary.total_wavelength_count} wavelengths, "
            f"spread {summary.spread_fraction[0]:.0%} x {summary.spread_fraction[1]:.0%} of the image"
        )
        if summary.dropped:
            text += f". Dropped: {'; '.join(summary.dropped)}"
        return text

    def _set_status(self, text: str, *, error: bool = False) -> None:
        theme = get_active_theme()
        color = theme.accent_red if error else theme.text_muted
        self._status.setStyleSheet(f"color: {color}; font-size: {_STATUS_FONT_PX}px;")
        self._status.set_full_text(text)

    def refresh_theme(self, theme: GuiTheme) -> None:
        self._separator.setStyleSheet(f"color: {theme.control_border};")
        for line in self._group_separators:
            line.setStyleSheet(f"color: {theme.control_border};")
        self._detect_caption.setStyleSheet(group_label_style())
        self._correction_caption.setStyleSheet(group_label_style())
        self._view_caption.setStyleSheet(group_label_style())
        self._gear_button.setIcon(load_tabler_icon("settings", color=theme.text_primary, size=_RENDER_SIZE, stroke_width=_STROKE_WIDTH))
        self._popover.refresh_theme(theme)
        self._refresh()
