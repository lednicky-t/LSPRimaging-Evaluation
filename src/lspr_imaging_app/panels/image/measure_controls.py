"""A small floating widget - a 3x3 grid (header row + read-only px row +
editable um row, columns dx/dy/d) plus an apply checkmark - shown next to
the Measure tool's placed ruler line (2026-09-29, maintainer's spec).

Structurally a twin of `crop_size_controls.py`: a real `QWidget` of real
input widgets (`QDoubleSpinBox`/`QToolButton`, CLAUDE.md's GUI-testability
rule), not a pyqtgraph overlay item, positioned in screen pixels by
`panel.py` and parented at construction (CLAUDE.md's phantom-top-level-
window pitfall). The read-only px cells are plain `QLabel`s rather than
disabled spin boxes - the testability rule is about *clickable* widgets,
and a label showing a number the user cannot edit needs no input widget at
all.

**Layout**: a `QGridLayout` shaped like a 3x3 table (maintainer's spec,
2026-09-29) - a header row ("dx"/"dy"/"d" column labels, replacing the
per-cell "Δx "/"Δy " prefixes the two-column version used, since the
header now carries that meaning once for the whole column) above a
read-only px row and an editable um row, with Apply in a fourth column
aligned with the um row (not spanning rows - see the 2026-09-29 revision
two entries back). `panel.py` positions the whole widget so Apply's
column sits directly under the ruler's second point
(`apply_button_center_x()`), fields to its left.

**"d" is the straight-line distance** (hypotenuse, `sqrt(dx^2+dy^2)`) -
added alongside dx/dy, maintainer's spec, 2026-09-29: "often you know the
total distance between two features but not its x/y components". Unlike
the read-only px row, **d_um is editable**, and ties into the *same*
single-scale model dx_um/dy_um already used: typing into *any one* of the
three um fields is mathematically equivalent to specifying one isotropic
um/px scale (`value / that field's own px reference`), and the other two
get recomputed from it via their own px components - `_apply_scale` is the
one function all three edit handlers call, parameterized only by which
field/px-reference is the source. This is not a separate feature bolted
onto the existing dx/dy sync; it is the same square-pixel assumption this
module has used since it was first added, now with a third equivalent way
to express it. `apply_requested` still only ever carries (dx_um, dy_um) -
`d_um` is a derived, always-consistent convenience, not a fourth
calibration parameter `GeometryModule.apply_measurement_calibration` needs
to know about.

**No unit toggle here** - removed 2026-09-29, same day it was added. Two
maintainer decisions, not one: (a) this floating widget was the wrong place
for it - "I was misunderstood that switch is next to apply button" - and
(b) more fundamentally, the toggle itself is out of scope for now: px<->um
only, no mm, until there is an actual second consumer of a "which unit am I
showing" choice (a scale bar, a ROI table column) to justify a switch at
all. `GeometryModule.set_display_units` was reverted to accept only
`("px", "um")` alongside this - see its own docstring.

**Each number gets its own small dark chip background**, not one shared
container background behind the whole widget - the maintainer's screenshot
showed the numbers unreadable against a bright dataset image with the
earlier single-container approach. Per-widget chips
(`background: rgba(0, 0, 0, 140)` - the same wash `crop_tool.py`'s
`_OVERLAY_COLOR` uses outside the crop rectangle) are what the stable app
already does for exactly this problem (`gui/measurement_calibration_
mixin.py`'s scale-bar label: "a small solid chip, same convention as the
ROI/landmark tags"): guaranteed visible regardless of what is under it.
Text is a fixed light color, not `theme.text_*`, for the same
contrast-independent-of-theme reason as the chip color itself. The header
row gets the same chip treatment (it floats over the image too) with a
dimmer text color, to read as subordinate to the data rows.

**Guarded against a ~zero source pixel span** (nothing to derive a scale
from - left alone rather than dividing by ~zero) and **never shows a
negative number** - `set_deltas` stores magnitudes (`abs`), not the signed
vector `MeasureLineTool` computes internally (which it still needs, for
`GeometryModule.set_measurement_anchors`).

**This widget owns no `GeometryModule` reference** - like `CropSizeControls`,
it only emits `apply_requested`; `panel.py` makes the actual calibration
call and reports the result back via `set_deltas`/`set_um_values`. That
keeps the same one-way flow the rest of this panel uses: a gesture becomes
a signal, a module change comes back as a redraw/refresh, never a direct
mutation from inside a widget.

**What the um fields show is `panel.py`'s call, not this widget's** - see
`_on_measure_tool_measured`'s docstring: zero for a fresh placement with no
calibration yet, the live calibrated distance whenever one already exists.
This widget just displays whatever it's told (`set_um_values`) until the
maintainer types over it.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QDoubleSpinBox, QGridLayout, QLabel, QSizePolicy, QToolButton, QWidget

from lspr_ui import GuiTheme, load_tabler_icon

_APPLY_COLOR = "#22c55e"

# Same dark wash as crop_tool.py's _OVERLAY_COLOR - deliberately duplicated
# rather than imported (that constant is that module's own private detail);
# see the module docstring for why a fixed, theme-independent, per-widget
# chip background is used instead of one shared container background.
_CHIP_BG = "rgba(0, 0, 0, 140)"
_TEXT_COLOR = "#f8fafc"  # fixed light text - readable on the fixed dark chip in either app theme
_TEXT_DIM_COLOR = "#e2e8f0"
_HEADER_TEXT_COLOR = "#94a3b8"  # dimmer than the data rows - reads as a subordinate label row
_ZERO_PX_EPSILON = 1e-6  # below this, a pixel span is "no delta to derive a scale from"


class MeasureCalibrationControls(QWidget):
    # (dx_um, dy_um) at the moment Apply was clicked. d_um is never sent -
    # it is always kept consistent with dx_um/dy_um, see module docstring.
    apply_requested = pyqtSignal(float, float)

    def __init__(self, parent: QWidget, theme: GuiTheme) -> None:
        super().__init__(parent)
        self.setObjectName("measureCalibrationControls")
        self.setCursor(Qt.CursorShape.ArrowCursor)  # see crop_size_controls.py's identical note
        self._dx_px = 0.0
        self._dy_px = 0.0
        self._d_px = 0.0

        self._header_dx = QLabel("dx", self)
        self._header_dy = QLabel("dy", self)
        self._header_d = QLabel("d", self)
        for label in (self._header_dx, self._header_dy, self._header_d):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("measureHeader")

        self._dx_px_label = QLabel(self)
        self._dy_px_label = QLabel(self)
        self._d_px_label = QLabel(self)
        for label in (self._dx_px_label, self._dy_px_label, self._d_px_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("measureChip")

        self._dx_um_spin = QDoubleSpinBox(self)
        self._dy_um_spin = QDoubleSpinBox(self)
        self._d_um_spin = QDoubleSpinBox(self)
        for spin in (self._dx_um_spin, self._dy_um_spin, self._d_um_spin):
            spin.setSuffix(" µm")
        self._dx_um_spin.setToolTip(
            "Real x distance, in micrometers. Assumes square pixels: typing into any one of dx/dy/d "
            "fills in the other two, scaled by the same um/px ratio."
        )
        self._dy_um_spin.setToolTip(self._dx_um_spin.toolTip().replace("x distance", "y distance"))
        self._d_um_spin.setToolTip(
            "Real straight-line distance between the two points, in micrometers - the hypotenuse of "
            "dx/dy. Assumes square pixels, same as dx/dy: typing here fills in dx and dy too, split "
            "according to the measured line's own x:y ratio."
        )
        # Fixed width, sized for the widest value the fields will show (up
        # to 6 digits + suffix) - the same reasoning crop_size_controls.py
        # uses, so all three rows line up into clean columns instead of
        # each auto-sizing to its own content.
        metrics = QFontMetrics(self._dx_um_spin.font())
        field_width = metrics.horizontalAdvance("9" * 6 + " µm") + 12
        for spin in (self._dx_um_spin, self._dy_um_spin, self._d_um_spin):
            spin.setRange(0.0, 1_000_000.0)
            spin.setDecimals(0)
            spin.setSingleStep(1.0)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setFixedWidth(field_width)
            spin.setObjectName("measureChip")
        for label in (self._dx_px_label, self._dy_px_label, self._d_px_label):
            label.setFixedWidth(field_width)
        for header in (self._header_dx, self._header_dy, self._header_d):
            header.setFixedWidth(field_width)
        self._dx_um_spin.editingFinished.connect(self._on_dx_um_edited)
        self._dy_um_spin.editingFinished.connect(self._on_dy_um_edited)
        self._d_um_spin.editingFinished.connect(self._on_d_um_edited)

        self._apply_button = QToolButton(self)
        self._apply_button.setAutoRaise(True)
        self._apply_button.setObjectName("measureChip")
        # Icon sized close to the button itself (2px margin each side) with
        # explicit zero QSS padding below - the default Qt button padding
        # plus a small icon on top of it made the checkmark read as "too
        # small" inside its chip.
        self._apply_button.setIcon(load_tabler_icon("checkbox", color=_APPLY_COLOR, size=32))
        self._apply_button.setIconSize(QSize(22, 22))
        self._apply_button.setFixedSize(26, 26)
        self._apply_button.setToolTip("Apply the entered distance to calibrate microns-per-pixel.")
        self._apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_button.clicked.connect(self._on_apply_clicked)

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(2)
        grid.addWidget(self._header_dx, 0, 0)
        grid.addWidget(self._header_dy, 0, 1)
        grid.addWidget(self._header_d, 0, 2)
        grid.addWidget(self._dx_px_label, 1, 0)
        grid.addWidget(self._dy_px_label, 1, 1)
        grid.addWidget(self._d_px_label, 1, 2)
        grid.addWidget(self._dx_um_spin, 2, 0)
        grid.addWidget(self._dy_um_spin, 2, 1)
        grid.addWidget(self._d_um_spin, 2, 2)
        # Same row as the um fields, not spanning all three - see module docstring.
        grid.addWidget(self._apply_button, 2, 3, Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.refresh_theme(theme)
        self.set_deltas(0.0, 0.0)
        self.adjustSize()

    def refresh_theme(self, _theme: GuiTheme) -> None:
        """Called at construction and again on a live theme switch, same as
        every other floating control in this panel - but this widget's own
        colors are deliberately fixed (see module docstring), so there is
        nothing theme-dependent to recompute here. Kept as a method (rather
        than removed) purely so `panel.py` can call it identically to
        `CropSizeControls.refresh_theme` without a special case."""
        self.setStyleSheet(
            f"QLabel#measureHeader {{ background: {_CHIP_BG}; color: {_HEADER_TEXT_COLOR}; "
            f"border-radius: 3px; padding: 1px 4px; font-size: 10px; }} "
            f"QLabel#measureChip {{ background: {_CHIP_BG}; color: {_TEXT_DIM_COLOR}; "
            f"border-radius: 3px; padding: 1px 4px; }} "
            f"QDoubleSpinBox#measureChip {{ background: {_CHIP_BG}; color: {_TEXT_COLOR}; "
            f"border: none; border-radius: 3px; font-weight: 600; }} "
            f"QToolButton#measureChip {{ background: {_CHIP_BG}; border: none; border-radius: 3px; padding: 0px; }} "
            f"QToolButton#measureChip:hover {{ background: #22c55e33; }}"
        )

    # -- state pushed in from outside ----------------------------------------

    def set_deltas(self, dx_px: float, dy_px: float) -> None:
        """A freshly placed (or hovered/dragged) ruler - update the
        read-only px row (including the derived "d") and the stored
        deltas the um-field auto-calc reads. Stores magnitudes, not the
        signed vector - see module docstring."""
        self._dx_px, self._dy_px = abs(dx_px), abs(dy_px)
        self._d_px = (self._dx_px**2 + self._dy_px**2) ** 0.5
        self._dx_px_label.setText(f"{self._dx_px:.1f} px")
        self._dy_px_label.setText(f"{self._dy_px:.1f} px")
        self._d_px_label.setText(f"{self._d_px:.1f} px")
        self.adjustSize()

    def set_um_values(self, dx_um: float, dy_um: float) -> None:
        """Programmatic update (a reset to 0, or a live calibrated
        readout - see `panel.py`'s `_on_measure_tool_measured`) - blocks
        signals so it never re-triggers the um-field auto-calc, the same
        reasoning `CropSizeControls.set_size` documents for its own
        programmatic updates. `d_um` is derived from the two, not a
        separate input to this method."""
        dx_um, dy_um = abs(dx_um), abs(dy_um)
        self._set_um_fields_blocked(dx_um, dy_um, (dx_um**2 + dy_um**2) ** 0.5)

    def reset_um_fields(self) -> None:
        self.set_um_values(0.0, 0.0)

    def apply_button_center_x(self) -> int:
        """The Apply button's horizontal center, in this widget's own
        coordinates - `panel.py` uses it to position the whole widget so
        Apply (not the widget's top-left corner) lands under the ruler's
        second point, per the maintainer's spec."""
        return self._apply_button.geometry().center().x()

    # -- internals ------------------------------------------------------------

    def _on_apply_clicked(self) -> None:
        self.apply_requested.emit(self._dx_um_spin.value(), self._dy_um_spin.value())

    def _on_dx_um_edited(self) -> None:
        self._apply_scale(source_value=self._dx_um_spin.value(), source_px=self._dx_px)

    def _on_dy_um_edited(self) -> None:
        self._apply_scale(source_value=self._dy_um_spin.value(), source_px=self._dy_px)

    def _on_d_um_edited(self) -> None:
        self._apply_scale(source_value=self._d_um_spin.value(), source_px=self._d_px)

    def _apply_scale(self, *, source_value: float, source_px: float) -> None:
        """Square-pixel assumption: whichever field the user just typed
        into implies one isotropic um/px scale (`source_value / source_px`)
        - applied to all three px references (dx/dy/d) to recompute all
        three um fields, the field just edited included (a harmless
        no-op re-set, since it is already consistent with its own scale).
        A ~zero source pixel span has no scale to derive (division by
        ~zero) - left alone rather than guessing."""
        if abs(source_px) < _ZERO_PX_EPSILON:
            return
        scale = abs(source_value / source_px)
        self._set_um_fields_blocked(scale * self._dx_px, scale * self._dy_px, scale * self._d_px)

    def _set_um_fields_blocked(self, dx_um: float, dy_um: float, d_um: float) -> None:
        for spin, value in ((self._dx_um_spin, dx_um), (self._dy_um_spin, dy_um), (self._d_um_spin, d_um)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
