"""Sensorgram ribbon, "Baseline" group: the anchor tools that roll out beside the edit pencil (2026-11-13).

Screen purpose: the tools that go with editing anchor points, in view exactly while the edit is on. They replaced the "Anchors" drop-down (an anchor
button with a menu that had to be opened and closed again for every change).

Main user actions, left to right:
- (reading) the anchor icon and the number of anchor points; its tool tip adds what the last fit said (the anchor scatter, lines that could not be
  corrected and why). There is no text list of the points.
- Whom new points apply to: all ROIs (shared points) / the lines shown (their own points). The scope of points already placed never changes by
  itself: that is what the next button is for.
- Apply to all ROIs: hand the own points of the lines shown on to every ROI (`baseline_drift.share_points`).
- "± N": how many cubes each point averages.
- Clear every anchor point.

Layout: one row, the last field of the Baseline group, right after the edit pencil. `DriftBaselineControls` shows it while the pencil is on (and the
method is Anchor points) and hides it when the pencil is off; the ribbon drawer scrolls the row when the panel is narrow.

Important states: "apply to all ROIs" is off when the lines shown keep no points of their own (the tool tip says why); Clear is off without points.

Display only: no settings are read or written here. The values arrive through `show_state`; a click is a signal that `DriftBaselineControls` turns
into an edit of `StatisticsSettings`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QSpinBox, QToolButton, QWidget

from lspr_ui import get_active_theme, load_tabler_icon, transparent_icon_button_stylesheet

from ..ribbon_fields import style_ribbon_number_field
from ..ribbon_icon_size import BUTTON_SIZE, ICON_SIZE
from ..spectra.view_menu import IconToggle

SCOPE_ALL, SCOPE_SHOWN = "all", "shown"  # whom a new anchor point applies to
HALFWIDTH_MAX = 50
_SPACING = 4  # between the fields, as in `RibbonRow`
_SCOPES = (  # (scope, object name, Tabler icon, tool tip)
    (SCOPE_ALL, "anchor_scope_all", "stack-3", "New points apply to all ROIs (shared points)."),
    (SCOPE_SHOWN, "anchor_scope_shown", "eye", "New points apply to the lines shown only (their own points)."),
)
_COUNT_TIP = "Number of anchor points (a cube counts once, shared or not)."


def _icon_of(name: str) -> Callable[[bool, str, str], QIcon]:
    """The `IconToggle` picture of a Tabler icon: grey when off, in the accent colour when on."""
    return lambda on, off_color, on_color: load_tabler_icon(name, color=on_color if on else off_color, size=24)


class AnchorTools(QWidget):
    scope_picked = pyqtSignal(str)  # SCOPE_ALL or SCOPE_SHOWN
    share_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    halfwidth_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("anchor_tools")
        self._syncing = False
        self._notes: list[str] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_SPACING)

        self._anchor_icon = QLabel(self)
        self.count = QLabel("0", self)
        self.count.setObjectName("anchor_count")
        row.addWidget(self._anchor_icon)
        row.addWidget(self.count)

        group = QButtonGroup(self)  # exactly one scope pressed
        self._scope_buttons: dict[str, IconToggle] = {}
        for scope, name, icon, tip in _SCOPES:
            button = IconToggle(_icon_of(icon), tip, self)
            button.setObjectName(name)
            button.clicked.connect(lambda _checked=False, picked=scope: None if self._syncing else self.scope_picked.emit(picked))
            group.addButton(button)
            row.addWidget(button)
            self._scope_buttons[scope] = button

        self._action_icons: list[tuple[QToolButton, str]] = []
        self.share = self._action_button("anchor_share", "share")  # its tool tip is set by `show_state`: it says why it is off
        self.share.clicked.connect(lambda _checked=False: self.share_requested.emit())
        row.addWidget(self.share)

        self.halfwidth = QSpinBox(self)
        self.halfwidth.setObjectName("anchor_halfwidth")
        self.halfwidth.setRange(0, HALFWIDTH_MAX)
        self.halfwidth.setPrefix("± ")
        self.halfwidth.setKeyboardTracking(False)  # a value is taken when Enter is pressed or the field is left, not on every digit
        self.halfwidth.setToolTip("Each point averages this many cubes on each side of it (0 = the point's own cube only).")
        style_ribbon_number_field(self.halfwidth, f"± {HALFWIDTH_MAX}")
        self.halfwidth.valueChanged.connect(lambda value: None if self._syncing else self.halfwidth_changed.emit(int(value)))
        row.addWidget(self.halfwidth)

        self.clear = self._action_button("anchor_clear", "trash-x")
        self.clear.setToolTip("Clear every anchor point (shared and own).")
        self.clear.clicked.connect(lambda _checked=False: self.clear_requested.emit())
        row.addWidget(self.clear)
        self.refresh_theme()

    def _action_button(self, name: str, icon: str) -> QToolButton:
        """A plain icon button (an action, not a switch) in the ribbon's size; `refresh_theme` colours it."""
        button = QToolButton(self)
        button.setObjectName(name)
        button.setAutoRaise(True)
        button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._action_icons.append((button, icon))
        return button

    # -- what the controls tell it ------------------------------------------------------------------------------------------

    def show_state(self, *, count: int, scope: str, halfwidth: int, can_share: bool, share_tip: str, can_clear: bool) -> None:
        """Show the settings (no signal is sent back): the number of points, whom new points apply to, the cubes each averages, and which actions
        have something to act on."""
        self._syncing = True
        try:
            self.count.setText(str(count))
            self._scope_buttons.get(scope, self._scope_buttons[SCOPE_ALL]).setChecked(True)
            self.halfwidth.setValue(int(halfwidth))
            self.share.setEnabled(bool(can_share))
            self.share.setToolTip(share_tip)
            self.clear.setEnabled(bool(can_clear))
        finally:
            self._syncing = False

    def set_notes(self, lines: Sequence[str]) -> None:
        """What the last fit said (the anchor scatter, lines left uncorrected and why): the tool tip of the number, so no text takes room in the row."""
        self._notes = [str(line) for line in lines]
        tip = _COUNT_TIP + ("\n\n" + "\n".join(self._notes) if self._notes else "")
        self._anchor_icon.setToolTip(tip)
        self.count.setToolTip(tip)

    def refresh_theme(self) -> None:
        theme = get_active_theme()
        self._anchor_icon.setPixmap(load_tabler_icon("anchor", color=theme.text_primary, size=ICON_SIZE).pixmap(ICON_SIZE, ICON_SIZE))
        self.count.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600; padding-right: 4px;")
        for button in self._scope_buttons.values():
            button.refresh_theme()
        for button, icon in self._action_icons:
            button.setStyleSheet(transparent_icon_button_stylesheet())
            button.setIcon(load_tabler_icon(icon, color=theme.text_primary, size=24))
        self.set_notes(self._notes)
