from __future__ import annotations

import dataclasses

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from lspr_ui import BRIGHT_THEME, GRAY_DARK_THEME, GuiTheme, apply_base_app_theme, get_active_theme, hex_to_rgba

# LSPRi-local variants of the two shared themes (2026-09-27, maintainer
# request) - `dataclasses.replace` on the shared, frozen `GuiTheme`
# instances, not edits to `packages/lspr_ui` itself (that's used by every
# app in the suite; these two stay LSPRi-only, same boundary as the
# now-removed VS Code Dark theme experiment).
#
# Two asks, solved by the same one-field change: "make the app background
# the same as the top menu color, so they blend" + "panel backgrounds
# should be darker in dark mode (lighter in bright mode)". `window_bg`
# drives the QApplication-wide palette/QSS - i.e. the canvas showing
# through the gaps `dock_container.py`'s card framing leaves between
# panels - while `toolbar_bg` (already read by `PanelContainer` for each
# docked panel's own card interior, and by `ImagePanel`'s plot canvas) is
# untouched here, so it keeps each theme's *original* window_bg value.
# Both shared themes already ship `toolbar_bg == window_bg` (single flat
# shade) and a `toolbar_section_bg` (menu bar) that's a distinctly
# different tone from window_bg - lighter in dark mode (`#1b1f26` vs
# `#14161a`), darker in bright mode (`#eef3f7` vs `#ffffff`). Retargeting
# window_bg to toolbar_section_bg's value therefore does both things at
# once, with no new color introduced: the gap now matches the menu bar
# (first ask), and since toolbar_bg keeps the *original*, more extreme
# window_bg value, panel interiors end up darker-than-the-gap in dark mode
# and lighter-than-the-gap in bright mode (second ask) - "vice versa"
# falls out for free because that's already the relationship each shared
# theme's own toolbar_section_bg has to its window_bg.
LSPRI_DARK_THEME = dataclasses.replace(GRAY_DARK_THEME, window_bg=GRAY_DARK_THEME.toolbar_section_bg)
LSPRI_BRIGHT_THEME = dataclasses.replace(BRIGHT_THEME, window_bg=BRIGHT_THEME.toolbar_section_bg)

# Full application-level theme application - originally three separate calls
# made once at startup in app.py's main() (apply_base_app_theme,
# _apply_active_palette, _apply_dark_theme). Consolidated here so
# MainWindow._apply_theme_styles can call the exact same sequence on every
# live theme switch via Preferences, not just at launch. Before this, only
# MainWindow's own palette/stylesheet got refreshed on switch - the
# QApplication-level stylesheet set here (which QPushButton/QComboBox/
# QLineEdit/QMenu/QScrollBar/QTableWidget/QHeaderView etc. all resolve
# through, since a QSS rule always wins over QPalette for anything it sets)
# stayed frozen at whichever theme was active at launch, leaving those
# widget types stuck on stale colors after a switch until the app restarted.


def dock_separator_stylesheet(theme: GuiTheme | None = None) -> str:
    # Qt draws QMainWindow::separator (the draggable strip between docked
    # panels, both the vertical ones between side-by-side panels and the
    # horizontal ones between stacked panels).
    #
    # Transparent at rest again (2026-09-27, final pass of a same-day series
    # of iterations - see git history on this function for the earlier
    # 8px/6px-visible and 4px-visible-border attempts and why each one
    # didn't land). What actually changed: docked ``PanelContainer``s now
    # paint their own rounded-corner card border in ``paintEvent``
    # (``dock_container.py``'s ``_apply_frame_style``/``_CARD_*`` constants)
    # with a real gap around each panel, matching VS Code's actual docked-
    # panel look. That border is what gives each panel its visual frame
    # now, not this separator - so the separator can go back to being
    # invisible until actually needed (an accent-blue reveal on hover, for
    # resize affordance), the same role VS Code's own sash plays sitting on
    # top of its separately-drawn static border. Without the panel-level
    # border, "transparent at rest" previously read as no border at all
    # anywhere; with it, this is no longer the only thing standing between
    # two panels, so it doesn't need to carry that job by itself.
    #
    # 4px width: confirmed (2026-09-27) that widening this beyond Qt's own
    # stock ``PM_DockWidgetSeparatorExtent`` was never actually necessary -
    # the "hard to resize" report that originally prompted widening it
    # turned out to be a dock-topology bug (addDockWidget vs
    # splitDockWidget - see app_rewrite.py's ``_DOCK_LAYOUT_STATE_VERSION``),
    # not grab precision.
    # Hover softened (2026-09-27, maintainer screenshot of VS Code's own
    # sash hover) - translucent instead of a flat opaque accent fill.
    #
    # No border-radius (tried, then reverted the same day): the maintainer
    # reported a leftover colored line staying on screen after the cursor
    # moved away. QMainWindow::separator is drawn by QMainWindowLayout
    # itself, not a real QWidget - its repaint/invalidation on a `:hover`
    # state change is less reliable than an ordinary styled widget's to
    # begin with (already confirmed once this same day: it silently
    # ignores background-image entirely - see the git history on this
    # function). A rounded fill only paints part of the pseudo-element's
    # own rect; if the *next* repaint (cursor leaving) doesn't correctly
    # invalidate the corner pixels the round rect left transparent, those
    # corners can be left showing whatever was drawn there last - a sharp
    # rect always covers its own full bounds on every repaint, so there's
    # no partial region for a stale frame to survive in. A rounded pill is
    # achievable, just not as a bare `:hover` style rule on this
    # particular element - same class of limitation as the dot-grip mark,
    # would need custom painting to do safely.
    theme = theme or get_active_theme()
    hover_fill = hex_to_rgba(theme.accent_blue, 0.35)
    return f"""
    QMainWindow::separator {{
        background: transparent;
        width: 4px;
        height: 4px;
    }}
    QMainWindow::separator:hover {{
        background-color: {hover_fill};
    }}
    """


def combo_box_no_arrow_stylesheet() -> str:
    # Collapsing the drop-down subcontrol to zero width - rather than just
    # hiding the arrow image - is what actually frees up its reserved space,
    # so QComboBox's own "padding: 2px 5px" (see startup_app_stylesheet())
    # applies symmetrically instead of the text looking pushed left.
    return """
    QComboBox::drop-down {
        width: 0px;
        border: none;
    }
    QComboBox::down-arrow {
        image: none;
        width: 0px;
        height: 0px;
    }
    """


def menu_bar_no_border_stylesheet() -> str:
    # lspr_ui's shared base stylesheet (packages/lspr_ui/theme.py) draws a
    # 1px QMenuBar { border-bottom: ... } rule under the menu bar - used by
    # every app in the suite, so overriding it there would change all of
    # them, not just this one (2026-09-27, maintainer request: "remove the
    # line separating the top menu from the rest"). Appended after the base
    # stylesheet in apply_app_theme, where a later rule for the same
    # selector/property wins - LSPRi-only, nothing else changes.
    return "QMenuBar { border-bottom: none; }"


def apply_active_palette(app: QApplication, theme: GuiTheme | None = None) -> None:
    """The full QPalette this app actually needs - apply_base_app_theme
    (packages/lspr_ui) sets a starter palette but leaves out WindowText/
    Highlight/HighlightedText, so plain QLabels/QCheckBoxes (WindowText) and
    selection highlighting (Highlight/HighlightedText) fell back to
    whichever theme was active when the QApplication's default palette was
    first created and never updated on a live switch."""
    theme = theme or get_active_theme()
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.window_bg))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.window_bg))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.toolbar_section_bg))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.control_bg))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text_primary))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.primary_action_bg))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.text_primary))
    app.setPalette(palette)


def apply_app_theme(app: QApplication, theme: GuiTheme | None = None) -> None:
    """Everything app.py's main() runs once at startup to theme the
    QApplication, callable again on every live theme switch."""
    theme = theme or get_active_theme()
    apply_base_app_theme(app, theme)
    apply_active_palette(app, theme)
    app.setStyleSheet(
        app.styleSheet()
        + combo_box_no_arrow_stylesheet()
        + dock_separator_stylesheet(theme)
        + menu_bar_no_border_stylesheet()
    )
