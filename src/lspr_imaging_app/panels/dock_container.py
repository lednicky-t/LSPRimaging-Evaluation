"""``PanelContainer`` - a ``QDockWidget`` with a custom title bar (float/
maximize/close buttons, an optional help button, an optional subtitle).

Ported from the stable app's ``gui/widgets.py`` (2026-09-24, GUI shell
design pass - see ``docs/rewrite_gui_shell_design_2026-09.md``), which has
used this exact component for every panel in production for a while: real
undock/float/resize, a fix for Qt's own bug where a restored floating panel
can land off-screen, and a deliberate "docking disabled while floating"
safety (see ``_on_top_level_changed``) so dragging a floating panel over the
main window doesn't trigger an accidental snap-back.

**Two deliberate deviations from the source, everything else copied
verbatim** (float/maximize/close behavior, the docking-disabled-while-
floating safety, the paintEvent-drawn floating border, the subtitle label):

1. The source's help-button icon goes through ``MainWindowIcons``, which
   falls back to the ``lucide`` icon library - a dependency this app's icon
   policy deliberately avoids (see ``packages/lspr_ui/ICONS.md`` - icons
   come from ``lspr_ui``'s own vendored set, not a third-party icon
   package). ``_make_help_icon`` below renders the vendored ``info-circle``
   tabler icon instead, using the exact same inline SVG-to-QIcon pattern
   this class already uses for its close/float/maximize icons - no new icon
   or dependency added.
2. The source's ``title_options`` clickable-segment title (e.g. "ROI" /
   "Group") and its ``_make_chevron_icon``/``_make_apply_icon`` helpers are
   left out - nothing in the rewrite's six fixed panels needs a toggleable
   title yet.

**One addition the source doesn't have at all**: the optional
``collapsible`` constructor flag (design doc §4) - click-to-collapse to a
thin strip, not the source's static title bar. Used only by the Workflow
panel today (``app_rewrite.build_main_window``); every other panel passes
the default (``collapsible=False``) and is unaffected.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import (
    get_active_theme,
    hex_to_rgba,
    tabler_icon_svg,
    transparent_icon_button_stylesheet,
)


def _render_tabler_icon(name: str, color: str, *, stroke_width: float = 2.2) -> QIcon:
    """Shared 20x20 render used by every title-bar icon below - matches
    the source class's own inline pattern exactly (same size, same 1px
    inset) so ported and non-ported icons look identical."""
    svg = tabler_icon_svg(name, color=color, stroke_width=stroke_width)
    if not svg:
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(1.0, 1.0, 18.0, 18.0))
    painter.end()
    return QIcon(pixmap)


class PanelContainer(QDockWidget):
    def __init__(
        self,
        title: str,
        content: QWidget,
        parent: QWidget | None = None,
        *,
        help_text: str | None = None,
        collapsible: bool = False,
    ) -> None:
        super().__init__(title, parent)
        self._title = title
        self._content = content
        self._help_text = help_text
        self._subtitle_text = ""
        self._subtitle_tooltip = ""
        self._subtitle_label: QLabel | None = None
        self.setObjectName(f"{title.replace(' ', '')}Panel")
        self._frame_border_color: str | None = None
        # Click-to-collapse to a thin strip (design doc §4) - Workflow only
        # today. `_collapsed_width` is deliberately generous next to VS
        # Code's ~48px sidebar rail: this strip has to fit a whole button
        # with real click/hover affordance, not just an icon glyph.
        self._collapsible = collapsible
        self._collapsed = False
        self._collapsed_width = 36
        self.setWidget(content)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        # While floating, docking is disabled by default (see
        # _on_top_level_changed) so dragging the floating window around/over
        # the main app doesn't trigger Qt's dock-target hint overlay or risk
        # an accidental snap-back. The float button re-arms docking on
        # click, tracked here so it can also gate a second click.
        self._dock_armed = False
        # Maximize state, floating-only (see _build_title_bar /
        # _on_maximize_button_clicked): whether this panel currently fills
        # its screen's available geometry, and the geometry to restore when
        # un-maximized.
        self._maximized = False
        self._pre_maximize_geometry: QRect | None = None

        theme = get_active_theme()
        self._apply_frame_style(theme)
        # QDockWidget's built-in close/float buttons hard-code their icon's
        # pixel size in Qt's C++ paint code; a custom title bar with real
        # QToolButtons is the only way to make the glyphs bigger.
        self.setTitleBarWidget(self._build_title_bar(title, theme))
        self.topLevelChanged.connect(self._on_top_level_changed)

    def refresh_theme(self) -> None:
        """Rebuild the custom title bar with the currently active theme.

        The title bar's colors are plain per-widget setStyleSheet() calls
        baked in at construction time, not QSS resolved through the
        QApplication-level stylesheet/palette - so unlike most of the app,
        they don't just pick up a live theme switch on their own and need to
        be rebuilt explicitly. Call this on every ``PanelContainer`` after a
        theme switch. While collapsed, rebuilds the collapsed strip/title
        bar instead - _build_title_bar's buttons (`_float_button`, etc.)
        only exist on the expanded title bar, so blindly rebuilding that one
        while collapsed would leave `_on_top_level_changed` reaching for
        attributes a collapsed title bar was never given."""
        theme = get_active_theme()
        self._apply_frame_style(theme)
        if self._collapsed:
            self.setWidget(self._build_collapsed_strip(theme))
            self.setTitleBarWidget(self._build_collapsed_title_bar(theme))
        else:
            self.setTitleBarWidget(self._build_title_bar(self._title, theme))

    def _apply_frame_style(self, theme) -> None:
        """A floating PanelContainer is a real top-level OS window, but with
        no native title bar (setTitleBarWidget replaces it) the OS-drawn
        window border is often just a 1px, barely-visible line. Drawing a
        single stroke directly on ``self`` in paintEvent() avoids the doubled
        -line artifacts a QSS-based border produced (see source history) -
        there's exactly one rectangle, and it can't misalign with itself.
        ``setContentsMargins`` reserves the 1px ring so the title bar/content
        children don't paint over it."""
        self.setStyleSheet(f"QDockWidget {{ color: {theme.text_primary}; }}")
        if self.isFloating():
            self.setContentsMargins(1, 1, 1, 1)
            self._frame_border_color = theme.toolbar_border
        else:
            self.setContentsMargins(0, 0, 0, 0)
            self._frame_border_color = None
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self._frame_border_color is not None:
            painter = QPainter(self)
            painter.setPen(QPen(QColor(self._frame_border_color)))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _build_title_bar(self, title: str, theme) -> QWidget:
        bar = QWidget(self)
        bar.setStyleSheet(f"background: {theme.window_bg};")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = QWidget(bar)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(2)
        outer.addWidget(row)

        label = QLabel(title, row)
        label.setStyleSheet(f"color: {theme.text_primary}; font-weight: 600; background: transparent;")
        layout.addWidget(label)

        subtitle_label = QLabel(self._subtitle_text, row)
        subtitle_label.setStyleSheet(f"color: {theme.text_muted}; font-weight: 400; background: transparent;")
        subtitle_label.setToolTip(self._subtitle_tooltip)
        subtitle_label.setVisible(bool(self._subtitle_text))
        layout.addSpacing(6)
        layout.addWidget(subtitle_label)
        self._subtitle_label = subtitle_label
        layout.addStretch(1)

        if self._help_text:
            help_text = self._help_text
            help_button = QToolButton(row)
            help_button.setIcon(self._make_help_icon(theme))
            help_button.setIconSize(QSize(18, 18))
            help_button.setFixedSize(24, 24)
            help_button.setAutoRaise(True)
            help_button.setToolTip("Show panel help.")
            help_button.setStyleSheet(
                transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_blue, 0.22))
                + "QToolButton:hover { border-radius: 4px; }"
            )
            help_button.clicked.connect(lambda *_: QMessageBox.information(self, title, help_text))
            layout.addWidget(help_button)

        if self._collapsible:
            collapse_button = QToolButton(row)
            collapse_button.setIcon(_render_tabler_icon("chevron-left", theme.text_muted))
            collapse_button.setIconSize(QSize(18, 18))
            collapse_button.setFixedSize(24, 24)
            collapse_button.setAutoRaise(True)
            collapse_button.setToolTip(f"Collapse {title} to a thin strip.")
            collapse_button.setStyleSheet(
                transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_blue, 0.22))
                + "QToolButton:hover { border-radius: 4px; }"
            )
            collapse_button.clicked.connect(lambda: self._set_collapsed(True))
            layout.addWidget(collapse_button)

        float_button = QToolButton(row)
        float_button.setIcon(self._make_float_icon())
        float_button.setIconSize(QSize(18, 18))
        float_button.setFixedSize(24, 24)
        float_button.setAutoRaise(True)
        float_button.setToolTip("Undock this panel into a floating window.")
        float_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_blue, 0.22))
            + "QToolButton:hover { border-radius: 4px; }"
        )
        float_button.clicked.connect(self._on_float_button_clicked)
        layout.addWidget(float_button)
        self._float_button = float_button

        # Only meaningful once floating (there's no "screen" to fill while
        # docked into the main window) - starts hidden and is shown by
        # _on_top_level_changed.
        maximize_button = QToolButton(row)
        maximize_button.setIcon(self._make_maximize_icon())
        maximize_button.setIconSize(QSize(18, 18))
        maximize_button.setFixedSize(24, 24)
        maximize_button.setAutoRaise(True)
        maximize_button.setToolTip("Maximize this panel to fill the screen.")
        maximize_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_blue, 0.22))
            + "QToolButton:hover { border-radius: 4px; }"
        )
        maximize_button.clicked.connect(self._on_maximize_button_clicked)
        maximize_button.setVisible(self.isFloating())
        layout.addWidget(maximize_button)
        self._maximize_button = maximize_button

        close_button = QToolButton(row)
        close_button.setIcon(self._make_close_icon())
        close_button.setIconSize(QSize(18, 18))
        close_button.setFixedSize(24, 24)
        close_button.setAutoRaise(True)
        close_button.setToolTip("Close this panel.")
        close_button.setStyleSheet(
            transparent_icon_button_stylesheet(hover=hex_to_rgba(theme.accent_red, 0.22))
            + "QToolButton:hover { border-radius: 4px; }"
        )
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        # A plain widget (not a border-bottom in the stylesheet) - Qt
        # mis-renders a QSS border-bottom set on a QDockWidget custom title
        # bar as a short underline hugging the label text instead of a
        # full-width rule, so the separator is drawn explicitly.
        separator = QWidget(bar)
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {theme.toolbar_border};")
        outer.addWidget(separator)

        return bar

    def set_subtitle(self, text: str, tooltip: str = "") -> None:
        """Short status text shown after the panel title, e.g. a live ROI/
        cube count a panel wants visible without stealing space from its own
        content."""
        self._subtitle_text = text
        self._subtitle_tooltip = tooltip
        if self._subtitle_label is not None:
            self._subtitle_label.setText(text)
            self._subtitle_label.setToolTip(tooltip)
            self._subtitle_label.setVisible(bool(text))

    def _set_collapsed(self, collapsed: bool) -> None:
        """Click-to-collapse/expand (design doc §4 - the maintainer chose
        this over a hover-to-peek flyout: simpler, deterministic, no new
        overlay/mouse-tracking machinery, at the cost of the panel
        reclaiming layout space only on an explicit click rather than
        auto-retracting)."""
        if not self._collapsible or collapsed == self._collapsed:
            return
        theme = get_active_theme()
        outgoing_widget = self.widget()
        self._collapsed = collapsed
        if collapsed:
            self.setWidget(self._build_collapsed_strip(theme))
            self.setTitleBarWidget(self._build_collapsed_title_bar(theme))
            self.setFixedWidth(self._collapsed_width)
        else:
            # Undoes setFixedWidth's implicit max-width clamp - Qt has no
            # single call to "unfix" a width once fixed.
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setWidget(self._content)
            self._content.show()
            self.setTitleBarWidget(self._build_title_bar(self._title, theme))
        # Explicitly hidden before the event loop can repaint: a widget
        # QDockWidget.setWidget() just displaced becomes parentless, and a
        # visible parentless widget is a real top-level OS window for
        # however long it stays that way (see CLAUDE.md's documented
        # startup-flicker pitfall - the same trap, encountered here on
        # swap-out rather than construction). Hiding it in the same call
        # stack that displaces it closes that window before Qt ever paints
        # it.
        if outgoing_widget is not None and outgoing_widget is not self.widget():
            outgoing_widget.hide()

    def _build_collapsed_strip(self, theme) -> QWidget:
        """The entire collapsed dock is one big clickable button - deliberately
        not just an icon glued to the top, so there's no dead area to miss
        a click on in a 36px-wide strip."""
        button = QToolButton()
        button.setIcon(_render_tabler_icon("chevron-right", theme.text_muted))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(f"Expand {self._title}.")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoRaise(True)
        button.setStyleSheet(
            f"QToolButton {{ background: {theme.window_bg}; border: none; }}"
            f"QToolButton:hover {{ background: {hex_to_rgba(theme.accent_blue, 0.14)}; }}"
        )
        button.clicked.connect(lambda: self._set_collapsed(False))
        return button

    def _build_collapsed_title_bar(self, theme) -> QWidget:
        """A bare divider line, not the normal title bar - at 36px wide
        there's no room for a title label plus buttons, and the whole point
        of collapsing is to give the click-to-expand button (the dock's
        `widget()`, above) the full height to be clicked on."""
        bar = QWidget()
        bar.setFixedHeight(1)
        bar.setStyleSheet(f"background: {theme.toolbar_border};")
        return bar

    def _on_float_button_clicked(self) -> None:
        if not self.isFloating():
            self.setFloating(True)
            return
        # Already floating: the click toggles whether docking is armed
        # rather than changing floating state again (see
        # _on_top_level_changed for why docking starts disabled here).
        self._dock_armed = not self._dock_armed
        self.setAllowedAreas(
            Qt.DockWidgetArea.AllDockWidgetAreas
            if self._dock_armed
            else Qt.DockWidgetArea.NoDockWidgetArea
        )
        self._update_float_button_appearance()

    def _on_top_level_changed(self, floating: bool) -> None:
        # Disable docking the moment the panel becomes floating - regardless
        # of whether that happened via the float button or the user dragging
        # the title bar out - so moving the floating window around/over the
        # main app doesn't trigger Qt's dock-target hint overlay or
        # accidentally snap it back in. Re-docking always restores all
        # areas.
        self._dock_armed = False
        self.setAllowedAreas(
            Qt.DockWidgetArea.NoDockWidgetArea if floating else Qt.DockWidgetArea.AllDockWidgetAreas
        )
        self._apply_frame_style(get_active_theme())
        if self._collapsed:
            # The collapsed title bar (_build_collapsed_title_bar) has none
            # of the buttons the lines below reach for - Qt can still float
            # a collapsed panel via its native drag-the-title-bar gesture
            # (the collapsed title bar is a real, if 1px-tall, title bar
            # area), so this has to be reachable while collapsed rather
            # than assumed impossible.
            return
        self._update_float_button_appearance()
        # Maximizing only makes sense while floating - hide the button once
        # docked, and forget any stale "maximized"/pre-maximize geometry so
        # a later re-float starts fresh.
        self._maximize_button.setVisible(floating)
        if not floating:
            self._maximized = False
            self._pre_maximize_geometry = None
            self._update_maximize_button_appearance()

    def _on_maximize_button_clicked(self) -> None:
        if not self.isFloating():
            return
        if self._maximized:
            self._maximized = False
            if self._pre_maximize_geometry is not None:
                self.setGeometry(self._pre_maximize_geometry)
            self._pre_maximize_geometry = None
        else:
            self._pre_maximize_geometry = self.geometry()
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.availableGeometry())
            self._maximized = True
        self._update_maximize_button_appearance()

    def _update_maximize_button_appearance(self) -> None:
        self._maximize_button.setIcon(self._make_maximize_icon(self._maximized))
        self._maximize_button.setToolTip(
            "Restore this panel to its previous size." if self._maximized else "Maximize this panel to fill the screen."
        )

    def _update_float_button_appearance(self) -> None:
        if not self.isFloating():
            self._float_button.setIcon(self._make_float_icon())
            self._float_button.setToolTip("Undock this panel into a floating window.")
        elif self._dock_armed:
            self._float_button.setIcon(self._make_float_icon("#22c55e"))
            self._float_button.setToolTip(
                "Docking enabled - drag this window onto a dock area to dock it, "
                "or click again to disable docking."
            )
        else:
            self._float_button.setIcon(self._make_float_icon())
            self._float_button.setToolTip(
                "Docking disabled while floating, so this window won't snap back when "
                "moved over the main app. Click to re-enable docking."
            )

    @staticmethod
    def _make_close_icon(color: str | None = None) -> QIcon:
        color = color if color is not None else get_active_theme().text_muted
        return _render_tabler_icon("x", color)

    @staticmethod
    def _make_float_icon(color: str | None = None) -> QIcon:
        color = color if color is not None else get_active_theme().text_muted
        return _render_tabler_icon("external-link", color)

    @staticmethod
    def _make_maximize_icon(maximized: bool = False, color: str | None = None) -> QIcon:
        color = color if color is not None else get_active_theme().text_muted
        return _render_tabler_icon("minimize" if maximized else "maximize", color)

    @staticmethod
    def _make_help_icon(theme=None) -> QIcon:
        theme = theme or get_active_theme()
        return _render_tabler_icon("info-circle", theme.text_primary)

    @staticmethod
    def _make_pin_icon(pinned: bool) -> QIcon:
        """Not used by this class yet - kept available for the Workflow
        panel's planned auto-hide-to-strip collapse
        (``docs/rewrite_gui_shell_design_2026-09.md`` §4), which needs a
        pin/unpin glyph of exactly this shape."""
        theme = get_active_theme()
        color = theme.accent_green if pinned else theme.text_primary
        return _render_tabler_icon("pin-filled" if pinned else "pin", color)
