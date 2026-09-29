"""Floating "[min, max]" readout/editor for the Histogram plot's Highlight
region - positioned near the x-axis, centered under the region (maintainer's
spec, 2026-09-29). A real `QWidget` (an editable field needs real keyboard
input, unlike a painted pyqtgraph overlay item - CLAUDE.md's GUI-testability
rule), parented and positioned by `HistogramPlot` (tracking the region
through pan/zoom and through a drag is a `ViewBox`-coordinate concern this
widget has no part in).

**Kept live during a drag** (maintainer's spec: "when highlight range is
moving make these fields live showing change, now they are stale") -
`HistogramPlot` calls `set_range` on every `sigRegionChanged`, not just when
a drag finishes, the same "live preview, commit on release" split
`CropTool`/`MeasureLineTool` already use for their own floating fields.

**Design, per the maintainer's own explicit spec** (2026-09-30 - see
CLAUDE.md's "Common Pitfalls" entry for the sizing rule this arrived at,
and why `.text()`/`.width()` checked in isolation don't prove a field isn't
clipping):
- Two plain `QLineEdit`s (not spin boxes - no button reservations, no
  range-based `sizeHint()` to fight), each a **fixed width sized once for
  the five-digit worst case** (`QFontMetrics.boundingRect("99999")` plus a
  small explicit margin), never resized afterward. A short value (e.g.
  "177") is centered in that same-sized box rather than shrinking the box
  to fit it - the maintainer's own call ("smaller numbers can be
  centered... why it is so complicated?") - which also means there is no
  per-value measurement to get wrong: the one static width is checked once
  and stays checked.
- The brackets and comma are plain, non-editable `QLabel`s, not
  prefix/suffix text baked into the editable field - so the editable area
  is exactly the number, nothing more.
- Zero spacing throughout (`setSpacing(0)`, no margins between pieces) -
  "put those boxes next to each other... without any spacing".
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QIntValidator
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from lspr_ui import GuiTheme

_MAX_DIGITS = 5  # a 16-bit sensor's max value, 65535, is 5 digits


class HighlightRangeReadout(QWidget):
    """"[min, max]" - editable. A committed edit (Enter or losing focus,
    not every keystroke - matching `CropSizeControls`' own reasoning for
    "a half-typed number never drives the rectangle") reports the *edited*
    field's raw value via `min_edited`/`max_edited`; it does not resolve
    crossed min/max itself - `HistogramPanel` does that against
    `HighlightRangeModule`'s current state, the same "commands take
    already-resolved values" convention `ReferenceFrameModule` established.
    """

    min_edited = pyqtSignal(float)
    max_edited = pyqtSignal(float)

    def __init__(
        self, parent: QWidget, theme: GuiTheme, *, value_min: float, value_max: float
    ) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setObjectName("highlightRangeReadout")
        self.setCursor(Qt.CursorShape.ArrowCursor)

        # `boundingRect`, not `horizontalAdvance`: Qt documents boundingRect
        # as covering the actual pixels the text touches, where
        # horizontalAdvance is only the cursor-advance distance - a weaker
        # guarantee for "will this clip". +6px on top regardless: a zero-
        # margin fit measures as correct but leaves no room for any
        # difference between measured and rendered glyph width (subpixel
        # rounding, hinting, DPI scaling) - confirmed this matters, not
        # assumed, by a razor-thin fit that shipped and clipped (see the
        # module docstring on why `.text()` alone never caught it).
        field_width = QFontMetrics(self.font()).boundingRect("9" * _MAX_DIGITS).width() + 6
        validator = QIntValidator(int(value_min), int(value_max), self)

        self._min_edit = self._build_field(field_width, validator)
        self._min_edit.editingFinished.connect(self._on_min_edited)

        self._max_edit = self._build_field(field_width, validator)
        self._max_edit.editingFinished.connect(self._on_max_edited)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        layout.addWidget(QLabel("[", self))
        layout.addWidget(self._min_edit)
        layout.addWidget(QLabel(",", self))
        layout.addWidget(self._max_edit)
        layout.addWidget(QLabel("]", self))

        self.refresh_theme(theme)
        self.adjustSize()

    @staticmethod
    def _build_field(field_width: int, validator: QIntValidator) -> QLineEdit:
        field = QLineEdit()
        field.setValidator(validator)
        field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        field.setFrame(False)
        field.setFixedWidth(field_width)
        return field

    def refresh_theme(self, theme: GuiTheme) -> None:
        self.setStyleSheet(
            f"#highlightRangeReadout {{ background: {theme.toolbar_section_bg}; "
            f"border: 1px solid {theme.toolbar_border}; border-radius: 4px; }} "
            f"QLineEdit {{ color: {theme.text_primary}; background: transparent; "
            f"border: none; padding: 0px; margin: 0px; }} "
            f"QLabel {{ color: {theme.text_primary}; background: transparent; }}"
        )

    def set_range(self, lo: float, hi: float) -> None:
        """Programmatic update (a drag in progress, or the shared module
        confirming a change) - must not re-trigger `min_edited`/`max_edited`,
        matching `CropSizeControls.set_size`'s own reasoning."""
        for field, value in ((self._min_edit, lo), (self._max_edit, hi)):
            field.blockSignals(True)
            field.setText(str(int(round(value))))
            field.blockSignals(False)

    def _on_min_edited(self) -> None:
        """`QIntValidator` restricts what characters go *in*, but a
        `QLineEdit` can always be emptied (backspace/delete) regardless of
        it - guard rather than let an empty commit raise out of a Qt slot."""
        text = self._min_edit.text()
        if text:
            self.min_edited.emit(float(text))

    def _on_max_edited(self) -> None:
        text = self._max_edit.text()
        if text:
            self.max_edited.emit(float(text))
