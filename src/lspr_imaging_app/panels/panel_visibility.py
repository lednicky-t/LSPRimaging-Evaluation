"""Panel show/hide recovery - a View -> Panels menu (Show all / Hide all,
one checkable, shortcut-bound toggle per dock) plus an off-screen safety net
for a floating panel restored onto a monitor that's since gone.

**Why this exists** (2026-09-27, found via the maintainer undocking the
Workflow panel and then being unable to get it back): every panel here is
a real ``QDockWidget`` (``PanelContainer``), which already provides exactly
the right recovery mechanism for free - ``toggleViewAction()``, a checkable
``QAction`` that shows/hides the dock and stays in sync with its actual
visibility. Nothing in ``app_rewrite.py`` was wiring it to anything, so a
closed or buried-behind-the-main-window panel had no way back short of
restarting the app. Ported from the stable app's proven pattern
(``gui/main_window_icons.py``'s ``Ctrl+1``-``Ctrl+5`` + "Panels" submenu,
``gui/layout_state_controller.py``'s ``ensure_floating_panels_on_screen``) -
scaled down to this app's much simpler fixed six-panel shell, not the
stable app's full per-section layout-reset machinery (no accordion-section
defaults to restore here; ``WorkflowPanel``'s own single-open-accordion
state isn't part of this).

**Not the same bug as "closed" vs. "floating off-screen"** - both read as
"I can't see it" to the maintainer, but need different fixes: a closed
(hidden) dock needs `toggleViewAction()`/Show all; a floating dock parked
on a monitor that's been unplugged/reordered since the last launch needs
its geometry corrected, since ``QMainWindow.restoreState()`` restores each
floating dock's geometry from its own opaque blob with no such fallback
(``restore_window_geometry``-equivalent handling exists for the *main*
window elsewhere, not for individual floating docks). This module covers
both.
"""

from __future__ import annotations

from PyQt6.QtGui import QGuiApplication, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMenu

from .dock_container import PanelContainer

# Order + shortcuts matter: Ctrl+1..5 here match the stable app's own
# assignment exactly (Workflow/Image/Histogram/Spectra/Sensorgram, in that
# order - gui/main_window_icons.py) so a maintainer's existing muscle memory
# carries over. ROI / Groups gets Ctrl+6, unbound in the stable app for no
# documented reason - no reason to leave the same gap here.
_PANEL_SHORTCUTS: tuple[str, ...] = ("Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6")


def wire_panel_visibility_menu(view_menu: QMenu, docks: dict[str, PanelContainer]) -> None:
    """Builds View -> Panels. *docks* must be given in the order its panels
    should appear in the menu / receive Ctrl+1.. shortcuts (six entries
    expected - one per ``_PANEL_SHORTCUTS`` slot; a shorter dict just leaves
    the remaining shortcuts unused, not an error)."""
    panels_menu = view_menu.addMenu("Panels")

    show_all_action = panels_menu.addAction("Show all panels")
    show_all_action.setToolTip("Show every workspace panel.")
    show_all_action.triggered.connect(lambda: _set_all_visible(docks, True))

    hide_all_action = panels_menu.addAction("Hide all panels")
    hide_all_action.setToolTip("Hide every workspace panel.")
    hide_all_action.triggered.connect(lambda: _set_all_visible(docks, False))

    panels_menu.addSeparator()
    for (_name, dock), shortcut in zip(docks.items(), _PANEL_SHORTCUTS):
        action = dock.toggleViewAction()
        action.setShortcut(QKeySequence(shortcut))
        panels_menu.addAction(action)


def _set_all_visible(docks: dict[str, PanelContainer], visible: bool) -> None:
    for dock in docks.values():
        dock.setVisible(visible)


def ensure_floating_panels_on_screen(docks: dict[str, PanelContainer], main_window: QMainWindow) -> None:
    """Re-home any floating panel that ``window.restoreState()`` just placed
    off every currently-connected screen (e.g. it was left floating on a
    second monitor that's since been unplugged, or the monitors were
    reordered). Call once, right after ``restoreState()``, before the window
    is shown - a panel with no on-screen match falls back to
    ``main_window``'s own (already-resolved) screen.

    Ported from the stable app's ``LayoutStateController.
    ensure_floating_panels_on_screen`` (``gui/layout_state_controller.py``),
    scaled to this module's plain dict-of-docks shape."""
    screens = QGuiApplication.screens()
    if not screens:
        return
    for dock in docks.values():
        if not dock.isFloating():
            continue
        panel_rect = dock.frameGeometry()
        best_screen = None
        best_area = -1
        for screen in screens:
            available = screen.availableGeometry()
            intersection = available.intersected(panel_rect)
            area = intersection.width() * intersection.height()
            if area > best_area:
                best_area = area
                best_screen = screen
        if best_screen is None:
            continue
        available = best_screen.availableGeometry()
        if best_area <= 0:
            # No overlap with any connected screen at all - its saved
            # monitor is gone, so the overlap tiebreak above is meaningless.
            # Re-home onto the main window's own (already-resolved) screen
            # instead of wherever that tiebreak happened to land.
            current_screen = main_window.screen()
            if current_screen is not None:
                available = current_screen.availableGeometry()
        _move_inside_available_screen(dock, available)


def _move_inside_available_screen(widget, available) -> None:
    frame = widget.frameGeometry()
    x = min(max(frame.x(), available.left()), max(available.right() - frame.width() + 1, available.left()))
    y = min(max(frame.y(), available.top()), max(available.bottom() - frame.height() + 1, available.top()))
    widget.move(x, y)
