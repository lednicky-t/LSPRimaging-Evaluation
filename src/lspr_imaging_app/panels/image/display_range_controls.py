"""The View tab's "Display range" group (2026-10-14, maintainer request: every frame must be comparable).

Screen purpose: choose how pixel values map to grey on screen. By default each frame is scaled to its own
range (black point = about its lowest value, white point = about its highest), so a frame that is twice as
bright looks like a dim one and brightness changes between frames or wavelengths are hidden. A **fixed** range
uses the same black and white point for every frame, so equal values look equal in every frame.

Layout: two 28 px ribbon buttons, the same size as every other icon in the ribbon,

    [pin] [...]

* pin (checkable): off = automatic per frame; on = every frame uses the fixed range. Its tooltip names the
  range that is in use.
* "..." opens a small panel (a `QWidgetAction` in a `QMenu`, like the ROIs tab's display menu) with

      Black point (min)   [    0 ]
      White point (max)   [65535 ]
      Use this frame's range         [fit]
      Use full 16-bit range          [full]

  The two numbers are whole numbers in image values (0 to 99999: the same five-digit convention as the
  Histogram's highlight range, also an integer field). Typing applies on Enter or when the field loses focus.
  While the range is automatic the numbers are greyed and read out the current frame's own range. "fit" copies
  that range into the fixed one; "full" sets 0 to 65535, the whole 16-bit scale. Both act on the fixed range, so
  they are available while the pin is on.

The fixed range **starts at the full 16-bit range** (0 to 65535, maintainer's choice 2026-10-14): the same
absolute scale as the Histogram's X axis, so a frame's grey means the same number whichever frame it is. The
pin itself starts off (automatic), as before.

Why a popup and not the numbers in the ribbon row: measured 2026-10-14 with real fonts, two number fields in the
row made the Image panel's minimum width 80 px larger (the View tab is the widest ribbon tab; it only has
~88 px of room before it sets the width of the whole panel). Display only: the measured values, the Histogram, the cursor readout and every
analysis are unchanged. Nothing here validates the pair (max must be above min): `ImagePanel` reports that and
falls back to automatic until it is fixed.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QIntValidator
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QToolButton, QWidget, QWidgetAction

from lspr_ui import GuiTheme, get_active_theme, load_tabler_icon

from .general_group import ICON_SIZE, style_general_icon_button

VALUE_MAX = 99999  # five digits, like the Histogram's highlight range (a 16-bit sensor tops out at 65535)
FULL_16BIT_RANGE = (0.0, 65535.0)  # the whole 16-bit scale: where a fixed range starts, and the "full" button
_FIELD_MARGIN_PX = 6  # on top of the five-digit width, as in the Histogram's range readout


def _whole(value: float) -> float:
    """`value` as a whole number inside the fields' range (what the fields can show and what is emitted)."""
    return float(min(max(round(value), 0), VALUE_MAX))


class DisplayRangeControls(QWidget):
    changed = pyqtSignal(bool, float, float)
    """(fixed, black point, white point) after a change made by the user (never after `set_state`)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fixed = False
        self._low, self._high = FULL_16BIT_RANGE
        self._frame_levels: tuple[float, float] | None = None

        self._toggle = QToolButton(self)
        self._toggle.setCheckable(True)
        self._toggle.toggled.connect(self._on_toggled)

        # The popup panel: two captioned number fields and the fit button.
        self._panel = QWidget(self)
        self._panel.setObjectName("displayRangePanel")
        self._low_edit = self._make_edit(self._panel)
        self._high_edit = self._make_edit(self._panel)
        for edit in (self._low_edit, self._high_edit):
            edit.editingFinished.connect(self._on_edited)
        self._fit = QToolButton(self._panel)
        self._fit.clicked.connect(self._on_fit)
        self._full = QToolButton(self._panel)
        self._full.clicked.connect(self._on_full)
        self._captions = [QLabel("Black point (min)", self._panel), QLabel("White point (max)", self._panel)]
        self._fit_caption = QLabel("Use this frame's range", self._panel)
        self._full_caption = QLabel("Use full 16-bit range", self._panel)
        grid = QGridLayout(self._panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.addWidget(self._captions[0], 0, 0)
        grid.addWidget(self._low_edit, 0, 1)
        grid.addWidget(self._captions[1], 1, 0)
        grid.addWidget(self._high_edit, 1, 1)
        grid.addWidget(self._fit_caption, 2, 0)
        grid.addWidget(self._fit, 2, 1, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._full_caption, 3, 0)
        grid.addWidget(self._full, 3, 1, Qt.AlignmentFlag.AlignRight)

        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._panel)
        self._menu.addAction(action)
        self._more = QToolButton(self)
        self._more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more.setMenu(self._menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._more)
        self.refresh_theme(get_active_theme())
        self._refresh()  # greyed fields, the readout and the icons for the starting (automatic) state

    # -- state ----------------------------------------------------------------------

    def state(self) -> tuple[bool, float, float]:
        """(fixed, black point, white point); the pair is the remembered fixed range even while it is off."""
        return self._fixed, self._low, self._high

    def set_state(self, fixed: bool, low: float, high: float) -> None:
        """Put the controls in this state without emitting `changed` (a restore at startup / dataset load)."""
        self._fixed = bool(fixed)
        self._low, self._high = _whole(low), _whole(high)
        self._refresh()

    def show_frame_levels(self, levels: tuple[float, float] | None) -> None:
        """The current frame's own automatic range: shown in the greyed fields while the range is automatic, and
        the target of the fit button. Called for every frame that is displayed."""
        self._frame_levels = None if levels is None else (_whole(levels[0]), _whole(levels[1]))
        if not self._fixed:
            self._refresh_fields()
        self._fit.setEnabled(self._fixed and self._frame_levels is not None)

    # -- user input -----------------------------------------------------------------------

    def _on_toggled(self, checked: bool) -> None:
        self._fixed = bool(checked)  # the remembered range (the full 16-bit scale until changed) is what comes back
        self._refresh()
        self.changed.emit(self._fixed, self._low, self._high)

    def _on_edited(self) -> None:
        if not self._fixed:
            self._refresh_fields()  # read-only: the readout must stay the frame's own range
            return
        low, high = self._read(self._low_edit, self._low), self._read(self._high_edit, self._high)
        changed = (low, high) != (self._low, self._high)
        self._low, self._high = low, high
        self._refresh_fields()  # also puts a field left empty or half-typed back to the last valid number
        if changed:
            self._refresh_look(get_active_theme())  # the tooltip names the range in use
            self.changed.emit(True, self._low, self._high)

    @staticmethod
    def _read(edit: QLineEdit, fallback: float) -> float:
        try:
            return _whole(int(edit.text()))
        except ValueError:
            return fallback

    def _on_fit(self) -> None:
        if self._frame_levels is None:
            return
        self._low, self._high = self._frame_levels
        self._refresh()
        self.changed.emit(True, self._low, self._high)

    def _on_full(self) -> None:
        self._low, self._high = FULL_16BIT_RANGE
        self._refresh()
        self.changed.emit(True, self._low, self._high)

    # -- look --------------------------------------------------------------------------

    def _make_edit(self, parent: QWidget) -> QLineEdit:
        edit = QLineEdit(parent)
        edit.setValidator(QIntValidator(0, VALUE_MAX, edit))
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # boundingRect, not horizontalAdvance, and an explicit margin on top: see histogram/highlight_range_controls.py
        edit.setFixedWidth(QFontMetrics(edit.font()).boundingRect("9" * len(str(VALUE_MAX))).width() + _FIELD_MARGIN_PX + 8)
        return edit

    def _refresh_fields(self) -> None:
        if self._fixed or self._frame_levels is None:
            low, high = self._low, self._high
        else:
            low, high = self._frame_levels
        for edit, value in ((self._low_edit, low), (self._high_edit, high)):
            blocked = edit.blockSignals(True)
            edit.setText(str(int(value)))
            edit.blockSignals(blocked)

    def _refresh(self) -> None:
        blocked = self._toggle.blockSignals(True)
        self._toggle.setChecked(self._fixed)
        self._toggle.blockSignals(blocked)
        for edit in (self._low_edit, self._high_edit):
            edit.setReadOnly(not self._fixed)  # greyed (see the style sheet) and not editable while automatic
        self._refresh_fields()
        self._fit.setEnabled(self._fixed and self._frame_levels is not None)
        self._full.setEnabled(self._fixed)
        self._refresh_look(get_active_theme())

    def refresh_theme(self, theme: GuiTheme) -> None:
        for button in (self._toggle, self._more, self._fit, self._full):
            style_general_icon_button(button)
            button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._panel.setStyleSheet(
            f"QLineEdit {{ color: {theme.text_primary}; background: {theme.control_bg}; "
            f"border: 1px solid {theme.control_border}; border-radius: 3px; padding: 1px 2px; }} "
            f"QLineEdit:read-only {{ color: {theme.text_dim}; background: transparent; }} "
            f"QLabel {{ color: {theme.text_secondary}; background: transparent; }}"
        )
        self._refresh_look(theme)

    def _refresh_look(self, theme: GuiTheme) -> None:
        self._toggle.setIcon(
            load_tabler_icon("pin", color=theme.accent_blue if self._fixed else theme.text_muted,
                             size=ICON_SIZE * 2, stroke_width=2.1)
        )
        self._toggle.setToolTip(
            f"Fixed display range ON ({int(self._low)} to {int(self._high)}): every frame uses the same black and "
            "white point, so brightness is comparable between frames. Click to go back to scaling each frame to its "
            "own range."
            if self._fixed
            else "Display range is automatic: each frame is scaled to its own range. Click to use one fixed range "
            "for every frame (it starts at the full 16-bit range, 0 to 65535)."
        )
        self._more.setIcon(load_tabler_icon("dots", color=theme.text_secondary, size=ICON_SIZE * 2, stroke_width=2.1))
        self._more.setToolTip("Display range numbers: black point, white point, and a button to take this frame's range.")
        self._fit.setIcon(load_tabler_icon("arrows-minimize", color=theme.text_secondary, size=ICON_SIZE * 2, stroke_width=2.1))
        self._fit.setToolTip("Set min and max to this frame's own range (a starting point to adjust from).")
        self._full.setIcon(load_tabler_icon("arrows-maximize", color=theme.text_secondary, size=ICON_SIZE * 2, stroke_width=2.1))
        self._full.setToolTip("Set min and max to the whole 16-bit scale, 0 to 65535: the same scale as the Histogram's X axis.")
        self._low_edit.setToolTip("Black point: values at or below this are black. Whole numbers; applies on Enter.")
        self._high_edit.setToolTip("White point: values at or above this are white. Must be above min. Applies on Enter.")
