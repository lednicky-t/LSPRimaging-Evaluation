"""What the ROI / Groups table's toggle buttons show and what they switch off (2026-11-26), kept out of `RoiTablePanel` (its method budget: `tests/unit/test_lspri_panel_size_budget.py`).

Each function takes the widgets it sets and `restyle(button, icon_name)`, the panel's own `_style_tool_button` (the icon in the accent colour while the button is on).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QToolButton

Restyle = Callable[[QToolButton, str], None]


def show_lock(button: QToolButton, tree, locked: bool, restyle: Restyle) -> None:
    """Follow the ROI selection lock (the ROIs tab's button, the right-click menus and a loaded session flip the same flag): the button is checked and the rows show a frame, not a tint."""
    button.setChecked(bool(locked))
    tree.set_selection_locked(bool(locked))
    restyle(button, "lock")


def show_group_only(button: QToolButton, on: bool, restyle: Restyle) -> None:
    """Make the toolbar button show the Groups-only view: checked and in the accent colour = on. `setChecked` does not click it."""
    button.setChecked(on)
    button.setToolTip(
        "Groups-only view is on: every group stays collapsed. Click to open the groups as they were."
        if on else "Groups-only view: collapse every group and keep them collapsed."
    )
    restyle(button, "list")


def enable_geometry_controls(view_toggle: QToolButton, view_actions: Iterable[QAction], group_only: bool) -> None:
    """The Geometry / Simple button and menu entries only act on ROI rows: greyed while the Groups-only view hides them. (The px / µm unit toggle greys itself, by calibration: `UnitToggle`.)"""
    rows_shown = not group_only
    view_toggle.setEnabled(rows_shown)
    for action in view_actions:
        action.setEnabled(rows_shown)
