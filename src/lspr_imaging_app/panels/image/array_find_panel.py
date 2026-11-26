"""The Find* pop-out's content: the array finder's few settings, the numbers behind them in a roll-down, and Apply / Cancel.

Screen purpose: let the image fill in an array from the selected ROI(s), while the canvas shows what would be made.

Layout (a panel inside the app window; `array_find.py` owns it, the search, the preview and Apply):

    guide line (what the finder is guided by: "ROI 12", "ROIs 3 and 40 = the corners", "no guide")                  (preview opacity wedge)
    Strict [---------o---------] Tolerant                (one slider: it sets the four numbers below at once)
    [x] Same type   [x] Same size   [ ] Fill empty nodes    (what the new ROIs look like; whether an empty node gets a ROI)
    Numbering (reading path over nine dots)
    Advanced v       Similarity / Strength / Position / Size, and the less common switches
    status (what was found; what is wrong)                                                       (cross = cancel) (tick = apply)

Colours on the image: blue = a ROI that Apply makes, grey = a node of the array where no spot was found, amber = a similar spot that is not on the lattice (never made).

Display only: this widget holds settings and reports them (`settings()` -> `roi.array_finder.FinderSettings`, `numbering()` -> `ArraySpec`); it knows nothing of ROIs or images.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget

from lspr_ui import CompactWedgeSlider, get_active_theme, load_tabler_icon

from ...image_tools.geometry.module import GeometryModule
from ...roi.array_finder import FinderResult, FinderSettings
from ...roi.array_layout import ArraySpec
from .array_pictograms import FlatIconButton, pictogram_icon
from .array_picker import NumberingPicker
from .general_group import ICON_SIZE

OPACITY_DEFAULT = 0.5
NUMBER_WIDTH = 84
PANEL_WIDTH = 380
"""The pop-out's fixed width (px). The two text lines wrap inside it, and their height is set from it (`_fit_label`): the pop-out is sized from size hints, which
know nothing of a word-wrapped label's height, so without that its last lines were cut off."""
MARGIN = 10
OPACITY_WIDTH = 36
SLIDER_STEPS = 100

_TIPS = {
    "slider": (
        "Strict <-> Tolerant: one control for the four numbers in Advanced (similarity, strength, position, size). Strict keeps only spots that look like the guide and sit "
        "exactly on the lattice; tolerant takes in more. Editing a number in Advanced changes only that number."
    ),
    "similarity": "Similarity: how much a spot must look like the guide (1 = the same picture; spots of one kind score about 0.8 - 0.95).",
    "strength": "Strength: how strong a spot's contrast must be, relative to the guide's (1 = as strong). Keeps out faint things that merely look alike.",
    "position": "Position: how far from its node a spot may sit, as a share of the pitch. Spots farther off are strays (amber), not part of the array.",
    "size": "Size: how far a spot's size may differ from the array's typical size, as a share of it (used when the size is not the guide's: 'Same size' off).",
    "fix_type": "Same type: the new ROIs are copies of the selected ROI (circle, rectangle or pixel map, with its rim). Off: circles of the measured spot size.",
    "fix_size": "Same size: the new ROIs get the selected ROI's size. Off: each ROI takes the size measured on its spot (see 'Each spot its own size' in Advanced).",
    "place_missing": "Fill empty nodes: put a ROI on every node of the array where no spot was found (marked as not found). Off: those nodes are left empty.",
    "corners": "Two selected ROIs are the corners: the array is limited to the box they span. Off: they only show what to look for.",
    "bright": "Bright spots: the spots are brighter than their surroundings (default: darker, as in the instrument).",
    "use_mask": (
        "Ignore masked pixels: the pixels you have masked are filled in from their surroundings before the spots are compared, so a spot that is damaged where the mask "
        "is can still be found. Off: the picture is used as it is. (Does nothing while no mask applies to the frame.)"
    ),
    "individual": "Each spot its own size: measured sizes are kept per spot. Off: one value for the array (the smallest robust one, so no ROI reaches past a spot).",
}


def strictness_values(position: float) -> tuple[float, float, float, float]:
    """``(similarity, strength, position tolerance, size tolerance)`` for the Strict - Tolerant slider at ``position`` (0 strict .. 1 tolerant). The middle is the default
    set, and the defaults of `FinderSettings`. The position tolerance grows with the square of the slider: a real array scatters a fraction of a pixel, so the strict half
    stays tight (1 - 5 % of the pitch) and only the tolerant end takes in a spot 10 - 16 % of the pitch off its node."""
    t = min(max(float(position), 0.0), 1.0)
    return 0.8 - 0.4 * t, 0.55 - 0.4 * t, 0.0125 + 0.15 * t * t, 0.1 + 0.3 * t


class ArrayFindPanel(QWidget):
    settings_changed = pyqtSignal()
    advanced_toggled = pyqtSignal(bool)
    opacity_changed = pyqtSignal(float)
    content_resized = pyqtSignal()  # a text line grew or shrank: the owner re-fits the pop-out
    apply_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, geometry: GeometryModule | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ArrayFindPanel")
        self._ready = False
        self._geometry = geometry
        self._result: FinderResult | None = None
        self.setFixedWidth(PANEL_WIDTH)

        self.guide = QLabel("", self)  # one short line; what it means is in its tooltip
        self.opacity_slider = CompactWedgeSlider(parent=self)
        self.opacity_slider.setFixedWidth(OPACITY_WIDTH)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(round(OPACITY_DEFAULT * 100))
        self.opacity_slider.setToolTip("Preview opacity: starts in the middle. Lower: the preview fades, so you see the image under it. Higher: it gets more solid.")
        self.opacity_slider.valueChanged.connect(lambda value: self.opacity_changed.emit(float(value) / 100.0))
        top = QHBoxLayout()
        top.addWidget(self.guide, 1)
        top.addWidget(self.opacity_slider)

        self.strictness = QSlider(Qt.Orientation.Horizontal, self)
        self.strictness.setRange(0, SLIDER_STEPS)
        self.strictness.setValue(SLIDER_STEPS // 2)
        self.strictness.setToolTip(_TIPS["slider"])
        self.strictness.valueChanged.connect(self._on_slider)
        strict, tolerant = QLabel("Strict", self), QLabel("Tolerant", self)
        for label in (strict, tolerant):
            label.setToolTip(_TIPS["slider"])
        slider_row = QHBoxLayout()
        slider_row.addWidget(strict)
        slider_row.addWidget(self.strictness, 1)
        slider_row.addWidget(tolerant)

        self.fix_type = self._check("Same type", "fix_type", True)
        self.fix_size = self._check("Same size", "fix_size", True)
        self.place_missing = self._check("Fill empty nodes", "place_missing", False)
        switches = QHBoxLayout()
        for box in (self.fix_type, self.fix_size, self.place_missing):
            switches.addWidget(box)
        switches.addStretch(1)

        self.numbering = NumberingPicker(self)
        self.numbering.changed.connect(self._on_edited)
        numbering_caption = QLabel("Numbering", self)
        numbering_caption.setToolTip(self.numbering.toolTip())
        numbering_row = QHBoxLayout()
        numbering_row.setSpacing(10)
        numbering_row.addWidget(numbering_caption)
        numbering_row.addWidget(self.numbering)
        numbering_row.addStretch(1)

        # -- advanced ------------------------------------------------------------------------------------------------
        self._advanced_toggle = QToolButton(self)
        self._advanced_toggle.setText("Advanced")
        self._advanced_toggle.setCheckable(True)
        self._advanced_toggle.setAutoRaise(True)
        self._advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._advanced_toggle.setToolTip("Show or hide the numbers behind the slider and the less common switches.")
        self._advanced = QFrame(self)
        self._advanced.setStyleSheet("QFrame { background: transparent; border: none; }")
        grid = QGridLayout(self._advanced)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.similarity = self._number("similarity", 0.30, 0.99, 0.01, 2, 0.6, "")
        self.strength = self._number("strength", 0.0, 2.0, 0.05, 2, 0.35, "")
        self.position = self._number("position", 1.0, 40.0, 1.0, 0, 5.0, " %")
        self.size = self._number("size", 5.0, 80.0, 5.0, 0, 25.0, " %")
        for row, (name, key, field) in enumerate((("Similarity", "similarity", self.similarity), ("Strength", "strength", self.strength))):
            self._row(grid, row, 0, name, key, field)
        for row, (name, key, field) in enumerate((("Position", "position", self.position), ("Size ±", "size", self.size))):
            self._row(grid, row, 2, name, key, field)
        self.corners = self._check("Two ROIs are the corners", "corners", True)
        self.bright = self._check("Bright spots", "bright", False)
        self.individual = self._check("Each spot its own size", "individual", False)
        self.use_mask = self._check("Ignore masked pixels", "use_mask", True)
        grid.addWidget(self.corners, 2, 0, 1, 4)
        grid.addWidget(self.use_mask, 3, 0, 1, 4)
        grid.addWidget(self.bright, 4, 0, 1, 2)
        grid.addWidget(self.individual, 4, 2, 1, 2)
        grid.setColumnStretch(4, 1)
        self._advanced.setVisible(False)
        self._advanced_toggle.toggled.connect(self._on_advanced_toggled)

        # -- status and buttons --------------------------------------------------------------------------------------
        self._stats, self._stat_captions, self._stat_values = self._build_stats()
        self._steps = QLabel("", self)
        self._steps.setWordWrap(True)
        self._message = QLabel("", self)
        self._message.setWordWrap(True)
        self._count = QLabel("", self)
        self.cancel_button = FlatIconButton("Cancel: close without making anything.", self)
        self.apply_button = FlatIconButton("Apply: make the new ROIs (one undo step; Ctrl+Z takes them back).", self)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        self.apply_button.clicked.connect(lambda _checked=False: self.apply_requested.emit())
        bottom = QHBoxLayout()
        bottom.addWidget(self._count, 1)
        bottom.addWidget(self.cancel_button)
        bottom.addWidget(self.apply_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN, 8, MARGIN, 8)
        layout.setSpacing(6)
        layout.addLayout(top)
        layout.addLayout(slider_row)
        layout.addLayout(switches)
        layout.addLayout(numbering_row)
        layout.addWidget(self._advanced_toggle)
        layout.addWidget(self._advanced)
        layout.addWidget(self._stats)
        layout.addWidget(self._steps)
        layout.addWidget(self._message)
        layout.addLayout(bottom)
        if geometry is not None:  # a switch between px and um (anywhere in the app) rewrites the lengths shown
            geometry.cosmetic_changed.connect(self._render_stats)
            geometry.geometry_changed.connect(self._render_stats)
        self._render_stats()

        self._ready = True
        self.refresh_theme()

    # -- construction helpers -------------------------------------------------------------------------------------

    def _check(self, text: str, tip: str, checked: bool) -> QCheckBox:
        box = QCheckBox(text, self)
        box.setChecked(checked)
        box.setToolTip(_TIPS[tip])
        box.toggled.connect(self._on_edited)
        return box

    def _number(self, tip: str, low: float, high: float, step: float, decimals: int, value: float, suffix: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox(self)
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setDecimals(decimals)
        box.setValue(value)
        box.setSuffix(suffix)
        box.setToolTip(_TIPS[tip])
        box.setFixedWidth(NUMBER_WIDTH)
        box.valueChanged.connect(self._on_edited)
        return box

    def _row(self, grid: QGridLayout, row: int, column: int, text: str, tip: str, field: QWidget) -> None:
        label = QLabel(text, self)
        label.setToolTip(_TIPS[tip])
        grid.addWidget(label, row, column)
        grid.addWidget(field, row, column + 1)

    # -- state ------------------------------------------------------------------------------------------------------

    def settings(self) -> FinderSettings:
        return FinderSettings(
            similarity=self.similarity.value(), strength=self.strength.value(), position_tol=self.position.value() / 100.0, size_tol=self.size.value() / 100.0,
            fix_size=self.fix_size.isChecked(), fix_type=self.fix_type.isChecked(), size_mode="individual" if self.individual.isChecked() else "uniform",
            seeds_are_corners=self.corners.isChecked(), place_missing=self.place_missing.isChecked(), bright=self.bright.isChecked(), use_mask=self.use_mask.isChecked(),
        )

    def numbering_spec(self) -> ArraySpec:
        """Only the ``number_*`` fields mean anything (`roi.array_found_plan` reads nothing else)."""
        start = self.numbering.start()
        return ArraySpec(number_by=self.numbering.by(), number_start_x=start[0], number_start_y=start[1])

    def set_values(self, *, strictness: float, similarity: float, strength: float, position: float, size: float) -> None:
        """Show saved numbers without reporting each (the owner asks for the settings once afterwards). ``position`` and ``size`` are fractions."""
        widgets = (self.strictness, self.similarity, self.strength, self.position, self.size)
        blocked = [w.blockSignals(True) for w in widgets]
        try:
            self.strictness.setValue(round(min(max(strictness, 0.0), 1.0) * SLIDER_STEPS))
            self.similarity.setValue(similarity)
            self.strength.setValue(strength)
            self.position.setValue(position * 100.0)
            self.size.setValue(size * 100.0)
        finally:
            for widget, was in zip(widgets, blocked, strict=True):
                widget.blockSignals(was)

    def strictness_position(self) -> float:
        return self.strictness.value() / float(SLIDER_STEPS)

    def _fit_label(self, label: QLabel, text: str, width: int) -> None:
        """Show ``text`` on a word-wrapped label and give it the height that text needs at ``width`` (so a pop-out sized from size hints shows all of it)."""
        label.setText(text)
        label.setFixedWidth(width)
        label.setMinimumHeight(label.heightForWidth(width) if text else 0)
        self.content_resized.emit()

    def set_guide(self, text: str, tip: str = "") -> None:
        """The guide as one short line; ``tip`` (shown on hover) says what the finder does with it."""
        self.guide.setText(text)
        self.guide.setToolTip(tip)

    # -- the numbers of what was found, one per row, in the app's unit -------------------------------------------------------

    _STAT_ROWS = (
        ("array", "Array", "How many rows and columns the lattice has and which type it is (rectangular, triangular or oblique)."),
        ("pitch", "Pitch", "Centre-to-centre distance of neighbours along the two lattice directions."),
        ("tilt", "Tilt", "Angle of the rows from the image's x axis, clockwise on the screen."),
        ("size", "Spot size", "Diameter of the spots (smallest - largest when they differ)."),
        ("scatter", "Off lattice", "How far the spots sit from their lattice nodes, root-mean-square: a measure of how regular the array is."),
        ("spots", "Spots", "Spots that make the array, similar spots that are not on it (strays), and nodes of the array with no spot (empty)."),
    )

    def _build_stats(self) -> tuple[QFrame, dict[str, QLabel], dict[str, QLabel]]:
        frame = QFrame(self)
        frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        captions: dict[str, QLabel] = {}
        values: dict[str, QLabel] = {}
        for row, (key, text, tip) in enumerate(self._STAT_ROWS):
            captions[key], values[key] = QLabel(text, frame), QLabel("", frame)
            for label in (captions[key], values[key]):
                label.setToolTip(tip)
            grid.addWidget(captions[key], row, 0)
            grid.addWidget(values[key], row, 1)
        grid.setColumnStretch(1, 1)
        return frame, captions, values

    def _length(self, px: float) -> str:
        """A length in pixels as the app shows lengths (px or um, `LengthField`'s rule: one decimal for px, two for um)."""
        geometry = self._geometry
        if geometry is not None and geometry.settings().display_units == "um" and geometry.can_display_micrometers():
            return f"{px * float(geometry.microns_per_pixel_scalar()):.2f} µm"
        return f"{px:.1f} px"

    def set_result(self, result: FinderResult | None) -> None:
        """Show the numbers of ``result`` (``None``: nothing found, the rows are hidden)."""
        self._result = result
        self._render_stats()

    def _render_stats(self, *_args: object) -> None:
        result = self._result
        self._stats.setVisible(result is not None)
        self._steps.setVisible(result is not None)
        if result is None:
            self.content_resized.emit()
            return
        values = {key: "" for key, _t, _tip in self._STAT_ROWS}
        spots = f"{result.count}"
        if len(result.stray_xy):
            spots += f"  ·  {len(result.stray_xy)} stray"
        if len(result.missing_xy):
            spots += f"  ·  {len(result.missing_xy)} empty"
        values["spots"] = spots
        if result.ordered:
            sizes = result.member_diameter
            low, high = float(sizes.min()), float(sizes.max())
            values["array"] = f"{result.rows} × {result.cols}, {result.kind}"
            values["pitch"] = f"{self._length(result.pitch_x_px).rsplit(' ', 1)[0]} × {self._length(result.pitch_y_px)}"  # (the unit once, at the end)
            values["tilt"] = f"{result.tilt_deg:+.2f}°"
            values["size"] = self._length(low) if abs(high - low) < 0.05 else f"{self._length(low)} – {self._length(high)}"
            values["scatter"] = f"{self._length(result.residual_rms_px)} rms"
        else:
            values["array"] = "no lattice: free positions"
        for key, text in values.items():
            self._stat_values[key].setText(text)
            shown = bool(text)
            self._stat_captions[key].setVisible(shown)
            self._stat_values[key].setVisible(shown)
        steps = "  →  ".join(f"{count} {name}" for name, count in result.funnel.items())
        self._fit_label(self._steps, f"Steps: {steps}", PANEL_WIDTH - 2 * MARGIN)

    def set_message(self, text: str, *, problem: bool = False) -> None:
        self._fit_label(self._message, text, PANEL_WIDTH - 2 * MARGIN)
        self._message.setStyleSheet(f"color: {'#f59e0b' if problem else get_active_theme().text_muted};")

    def message(self) -> str:
        return self._message.text()

    def set_count(self, text: str) -> None:
        self._count.setText(text)

    def set_apply_enabled(self, enabled: bool) -> None:
        self.apply_button.setEnabled(bool(enabled))

    def is_advanced_open(self) -> bool:
        return self._advanced_toggle.isChecked()

    def set_advanced_open(self, opened: bool) -> None:
        self._advanced_toggle.setChecked(bool(opened))

    def set_opacity(self, opacity: float) -> None:
        blocked = self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(round(max(min(float(opacity), 1.0), 0.0) * 100))
        self.opacity_slider.blockSignals(blocked)

    def opacity(self) -> float:
        return self.opacity_slider.value() / 100.0

    # -- reactions --------------------------------------------------------------------------------------------------

    def _on_slider(self, _value: int) -> None:
        if not self._ready:
            return
        similarity, strength, position, size = strictness_values(self.strictness_position())
        widgets = (self.similarity, self.strength, self.position, self.size)
        blocked = [w.blockSignals(True) for w in widgets]
        try:
            self.similarity.setValue(similarity)
            self.strength.setValue(strength)
            self.position.setValue(position * 100.0)
            self.size.setValue(size * 100.0)
        finally:
            for widget, was in zip(widgets, blocked, strict=True):
                widget.blockSignals(was)
        self.settings_changed.emit()

    def _on_edited(self, *_args: object) -> None:
        if self._ready:
            self.settings_changed.emit()

    def _on_advanced_toggled(self, opened: bool) -> None:
        self._advanced.setVisible(bool(opened))
        self._refresh_advanced_icon()
        self.advanced_toggled.emit(bool(opened))

    # -- look -----------------------------------------------------------------------------------------------------------

    def _refresh_advanced_icon(self) -> None:
        theme = get_active_theme()
        name = "chevron-down" if self._advanced_toggle.isChecked() else "chevron-right"
        self._advanced_toggle.setIcon(load_tabler_icon(name, color=theme.text_secondary, size=ICON_SIZE * 2, stroke_width=2.1))
        self._advanced_toggle.setStyleSheet(
            "QToolButton, QToolButton:checked, QToolButton:pressed { border: none; background: transparent; padding: 2px 0px; "
            f"color: {theme.text_secondary}; font-weight: 600; text-align: left; }} QToolButton:hover {{ color: {theme.text_primary}; background: transparent; }}"
        )

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self.setStyleSheet("QLabel { background: transparent; } QCheckBox { background: transparent; }")
        self.guide.setStyleSheet(f"color: {theme.text_secondary}; background: transparent;")
        self._steps.setStyleSheet(f"color: {theme.text_muted}; background: transparent;")
        for label in self._stat_captions.values():
            label.setStyleSheet(f"color: {theme.text_muted}; background: transparent;")
        self.update()
        self._refresh_advanced_icon()
        self.cancel_button.setIcon(pictogram_icon("cancel", accent=theme.text_secondary))
        self.apply_button.setIcon(pictogram_icon("apply", accent=theme.accent_green))
        self.numbering.refresh_theme()
