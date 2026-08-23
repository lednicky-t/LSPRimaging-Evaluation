from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from lspr_ui import GuiTheme, apply_base_app_theme, get_active_theme

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
    # horizontal ones between stacked panels) in the plain window background
    # color unless styled - on this app's dark theme that made every panel
    # border invisible. theme.toolbar_border is the same subtle divider color
    # already used for QGroupBox/QMenu/QTableWidget borders elsewhere.
    theme = theme or get_active_theme()
    return f"""
    QMainWindow::separator {{
        background: {theme.toolbar_border};
    }}
    QMainWindow::separator:hover {{
        background: {theme.control_border_hover};
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
    app.setStyleSheet(app.styleSheet() + combo_box_no_arrow_stylesheet() + dock_separator_stylesheet(theme))
