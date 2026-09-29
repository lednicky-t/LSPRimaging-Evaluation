"""A small floating widget - width/height fields plus an apply checkmark -
shown next to the crop tool's rectangle (2026-09-29, maintainer's spec).

A real `QWidget` (`QSpinBox`/`QToolButton`, both real `QAbstractButton`/
input widgets - CLAUDE.md's GUI-testability rule), not a pyqtgraph overlay
item: those two need actual keyboard/click input, which painted graphics
items don't take. `panel.py` owns positioning it in screen pixels (it must
track the rectangle's bottom-right corner through pan/zoom - the apply
button flush with the crop's right edge, the maintainer's spec, 2026-09-29
- which is a `QGraphicsView` concern this widget has no part in) and
parents it at construction time (CLAUDE.md's phantom-top-level-window
pitfall).
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QHBoxLayout, QSpinBox, QToolButton, QWidget

from lspr_ui import GuiTheme, load_tabler_icon, transparent_icon_button_stylesheet

_APPLY_COLOR = "#22c55e"
_MAX_DIGITS = 4  # maintainer's spec (2026-09-29): fields sized for up to 4-digit pixel counts


class CropSizeControls(QWidget):
    """"x:"/"y:" (width/height, the maintainer's own labels for them, not
    "w"/"h") spin boxes plus a checkmark apply button.

    `set_size`/`set_max_size` push values in (blocking signals, so a
    programmatic update from a drag never looks like a user edit). A
    *committed* edit - Enter or losing focus, not every keystroke, so a
    half-typed number never drives the rectangle - reports both values via
    `size_edited`, always as one full, already-range-clamped pair (Qt's own
    `QSpinBox` clamps to `set_max_size`'s bound as soon as editing ends -
    "fields don't accept higher values, they jump to the highest possible
    one" is `setMaximum`'s native behavior, not something this class
    re-implements)."""

    size_edited = pyqtSignal(int, int)  # width, height
    apply_requested = pyqtSignal()

    def __init__(self, parent: QWidget, theme: GuiTheme) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setObjectName("cropSizeControls")
        # A widget with no cursor of its own shows its *parent's* - and the
        # parent here is the image view's viewport, whose cursor panel.py
        # keeps changing to a resize/move shape as the mouse crosses the
        # crop rectangle's edges (`_on_scene_moved`). Without this, that
        # stray resize cursor bleeds onto this whole floating widget and
        # never changes back, including over the apply button (maintainer
        # noticed, 2026-09-29: hovering it gave no "this is clickable" cue
        # at all). An explicit cursor here blocks that inheritance for the
        # whole widget; the apply button below overrides it again with its
        # own, since it specifically wants a hand, not a plain arrow.
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self._width_spin = QSpinBox(self)
        self._width_spin.setPrefix("x: ")
        self._width_spin.setRange(1, 1)
        self._width_spin.editingFinished.connect(self._on_edited)

        self._height_spin = QSpinBox(self)
        self._height_spin.setPrefix("y: ")
        self._height_spin.setRange(1, 1)
        self._height_spin.editingFinished.connect(self._on_edited)

        # Fixed, tight width for both fields - sized for "x: " + 4 digits,
        # not QSpinBox's own much wider default sizeHint (which is what
        # produced the big gaps the maintainer flagged, 2026-09-29: with no
        # explicit width each field claims far more room than its content
        # needs, so the horizontal layout's spacing alone looks huge).
        metrics = QFontMetrics(self._width_spin.font())
        field_width = metrics.horizontalAdvance("x: " + "9" * _MAX_DIGITS) + 28  # +28: spin arrows + padding
        for spin in (self._width_spin, self._height_spin):
            spin.setFixedWidth(field_width)
            spin.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._apply_button = QToolButton(self)
        self._apply_button.setAutoRaise(True)
        # tabler's "checkbox" glyph (a checked box) is the maintainer's own
        # "check icon box" - already vendored, no new icon asset needed.
        self._apply_button.setIcon(load_tabler_icon("checkbox", color=_APPLY_COLOR, size=32))
        self._apply_button.setIconSize(QSize(16, 16))
        self._apply_button.setFixedSize(22, 22)
        self._apply_button.setToolTip("Apply crop")
        self._apply_button.setStyleSheet(transparent_icon_button_stylesheet(hover="#22c55e33"))
        self._apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_button.clicked.connect(self.apply_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(2)
        layout.addWidget(self._width_spin)
        layout.addWidget(self._height_spin)
        layout.addWidget(self._apply_button)
        self.refresh_theme(theme)
        self.adjustSize()

    def refresh_theme(self, theme: GuiTheme) -> None:
        """Called at construction and again on a live theme switch (see
        panel.py's `refresh_theme`) - floats over the image, so it needs an
        explicit opaque background rather than relying on inherited chrome."""
        self.setStyleSheet(
            f"#cropSizeControls {{ background: {theme.toolbar_section_bg}; "
            f"border: 1px solid {theme.toolbar_border}; border-radius: 4px; }} "
            f"QSpinBox {{ color: {theme.text_primary}; background: transparent; border: none; }}"
        )

    def set_max_size(self, max_width: int, max_height: int) -> None:
        self._width_spin.setMaximum(max(int(max_width), 1))
        self._height_spin.setMaximum(max(int(max_height), 1))

    def set_size(self, width: int, height: int) -> None:
        """Programmatic update (a drag, an apply, a cancel) - must not
        re-trigger `size_edited`, or a drag would fight its own readout."""
        for spin, value in ((self._width_spin, width), (self._height_spin, height)):
            spin.blockSignals(True)
            spin.setValue(int(value))
            spin.blockSignals(False)
        self.adjustSize()

    def set_apply_enabled(self, enabled: bool) -> None:
        self._apply_button.setEnabled(enabled)

    def _on_edited(self) -> None:
        self.size_edited.emit(self._width_spin.value(), self._height_spin.value())
